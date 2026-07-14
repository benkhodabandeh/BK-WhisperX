from __future__ import annotations

import contextlib
import gc
import inspect
import io
import json
import logging
import os
import re
import sys
import textwrap
import threading
import time
import traceback
import unicodedata
import uuid
import warnings
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import Any, TextIO

from .config import TranscriptionConfig
from .paths import runtime_environment

LogFn = Callable[[str], None]
EventFn = Callable[[str, dict[str, Any]], None]


class TranscriptionCancelled(RuntimeError):
    """Raised when the launcher requests a safe transcription shutdown."""


def _cancel_file() -> Path | None:
    raw = os.environ.get("BKWHISPERX_CANCEL_FILE", "").strip()
    return Path(raw) if raw else None


def _raise_if_cancelled() -> None:
    marker = _cancel_file()
    if marker is not None and marker.exists():
        raise TranscriptionCancelled("Transcription was cancelled by the user.")


def _start_parent_watchdog() -> None:
    """Stop the worker if its CLI launcher disappears unexpectedly."""
    raw_parent = os.environ.get("BKWHISPERX_PARENT_PID", "").strip()
    if not raw_parent.isdigit():
        return
    parent_pid = int(raw_parent)
    if parent_pid <= 0:
        return

    def parent_gone() -> None:
        marker = _cancel_file()
        if marker is not None:
            try:
                marker.parent.mkdir(parents=True, exist_ok=True)
                marker.write_text("parent-exited\n", encoding="utf-8")
            except OSError:
                pass
        # Give the normal progress callback a brief opportunity to unwind CUDA,
        # close combined files, and emit a cancellation event before hard exit.
        time.sleep(5.0)
        os._exit(130)

    def watch_windows() -> None:
        try:
            import ctypes
            from ctypes import wintypes

            synchronize = 0x00100000
            infinite = 0xFFFFFFFF
            kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
            open_process = kernel32.OpenProcess
            open_process.argtypes = (wintypes.DWORD, wintypes.BOOL, wintypes.DWORD)
            open_process.restype = wintypes.HANDLE
            wait = kernel32.WaitForSingleObject
            wait.argtypes = (wintypes.HANDLE, wintypes.DWORD)
            wait.restype = wintypes.DWORD
            close = kernel32.CloseHandle
            close.argtypes = (wintypes.HANDLE,)
            handle = open_process(synchronize, False, parent_pid)
            if not handle:
                parent_gone()
                return
            try:
                wait(handle, infinite)
            finally:
                close(handle)
            parent_gone()
        except Exception:
            return

    def watch_posix() -> None:
        while True:
            if os.getppid() != parent_pid:
                parent_gone()
                return
            time.sleep(1.0)

    target = watch_windows if sys.platform == "win32" else watch_posix
    threading.Thread(target=target, name="bkx-parent-watchdog", daemon=True).start()


_LIVE_TRANSCRIPT_RE = re.compile(r"^Transcript:\s*\[(?P<start>[0-9.]+)\s*-->\s*(?P<end>[0-9.]+)\]\s*(?P<text>.*)$")


class _WhisperTranscriptCapture(io.TextIOBase):
    """Capture WhisperX verbose segment lines while forwarding all other output."""

    def __init__(self, target: TextIO, on_segment: Callable[[float, float, str], None]) -> None:
        self.target = target
        self.on_segment = on_segment
        self.buffer = ""

    @property
    def encoding(self) -> str:
        return getattr(self.target, "encoding", None) or "utf-8"

    def writable(self) -> bool:
        return True

    def write(self, value: str) -> int:
        self.buffer += value
        while "\n" in self.buffer:
            line, self.buffer = self.buffer.split("\n", 1)
            self._handle_line(line.rstrip("\r"))
        return len(value)

    def _handle_line(self, line: str) -> None:
        match = _LIVE_TRANSCRIPT_RE.match(line.strip())
        if match:
            self.on_segment(
                float(match.group("start")),
                float(match.group("end")),
                match.group("text").strip(),
            )
            return
        self.target.write(line + "\n")
        self.target.flush()

    def flush(self) -> None:
        if self.buffer:
            pending = self.buffer
            self.buffer = ""
            self._handle_line(pending.rstrip("\r"))
        self.target.flush()


@dataclass(slots=True)
class RunStats:
    completed: int = 0
    skipped: int = 0
    failed: int = 0
    elapsed_seconds: float = 0.0


def _log_default(message: str) -> None:
    print(message, flush=True)


def _event_default(kind: str, payload: dict[str, Any]) -> None:
    del kind, payload


def configure_runtime_environment() -> None:
    # BK WhisperX always decodes the source file into an in-memory waveform via
    # whisperx.load_audio() and the managed FFmpeg executable before handing it
    # to WhisperX/pyannote. pyannote still imports its optional TorchCodec file
    # decoder and emits a large warning on Windows when shared FFmpeg DLLs are
    # absent, even though that decoder is not used by this application. Suppress
    # only that specific warning; real audio-loading failures remain visible.
    warnings.filterwarnings(
        "ignore",
        message=r"(?s).*torchcodec is not installed correctly.*",
        category=UserWarning,
        module=r"pyannote\.audio\.core\.io",
    )

    env = runtime_environment()
    for key, value in env.items():
        if key not in os.environ or key.startswith(("BKWHISPERX", "HF_", "HUGGINGFACE", "TORCH_", "XDG_")):
            os.environ[key] = value
    try:
        import imageio_ffmpeg

        ffmpeg_exe = Path(imageio_ffmpeg.get_ffmpeg_exe()).resolve()
        os.environ["IMAGEIO_FFMPEG_EXE"] = str(ffmpeg_exe)
        os.environ["PATH"] = str(ffmpeg_exe.parent) + os.pathsep + os.environ.get("PATH", "")
    except Exception:
        pass
    if sys.platform == "win32":
        try:
            import torch

            torch_lib = Path(torch.__file__).resolve().parent / "lib"
            if torch_lib.is_dir() and hasattr(os, "add_dll_directory"):
                os.add_dll_directory(str(torch_lib))
            os.environ["PATH"] = str(torch_lib) + os.pathsep + os.environ.get("PATH", "")
        except Exception:
            pass


def quiet_third_party_logs() -> None:
    for name in (
        "pyannote",
        "pytorch_lightning",
        "lightning",
        "speechbrain",
        "transformers",
        "faster_whisper",
        "whisperx",
        "huggingface_hub",
    ):
        logging.getLogger(name).setLevel(logging.ERROR)


def _accepts(function: Any, name: str) -> bool:
    try:
        signature = inspect.signature(function)
    except (TypeError, ValueError):
        return True
    return name in signature.parameters or any(
        p.kind is inspect.Parameter.VAR_KEYWORD for p in signature.parameters.values()
    )


def _supported_kwargs(function: Any, kwargs: dict[str, Any]) -> dict[str, Any]:
    try:
        signature = inspect.signature(function)
    except (TypeError, ValueError):
        return kwargs
    if any(p.kind is inspect.Parameter.VAR_KEYWORD for p in signature.parameters.values()):
        return kwargs
    return {key: value for key, value in kwargs.items() if key in signature.parameters}


def resolve_device_and_defaults(config: TranscriptionConfig, torch: Any) -> tuple[str, str, int]:
    if config.device == "auto":
        device = "cuda" if torch.cuda.is_available() else "cpu"
    else:
        device = config.device
    if device == "cuda" and not torch.cuda.is_available():
        raise RuntimeError(
            "CUDA was selected, but the installed PyTorch runtime cannot access an NVIDIA GPU. "
            "Open Runtime Setup and install/repair a CUDA profile."
        )
    compute_type = config.compute_type
    if compute_type == "auto":
        compute_type = "float16" if device == "cuda" else "int8"
    if device == "cpu" and compute_type == "float16":
        raise ValueError("float16 is not supported for CPU transcription. Select auto, int8, or float32.")
    batch_size = config.batch_size
    if batch_size <= 0:
        if device == "cpu":
            batch_size = 1 if config.performance_preset == "safe" else 2
        else:
            try:
                total_gb = torch.cuda.get_device_properties(0).total_memory / (1024**3)
                balanced = 4 if total_gb < 6 else 8 if total_gb < 10 else 16 if total_gb < 16 else 24
                multiplier = {"safe": 0.5, "balanced": 1.0, "maximum": 1.5}[config.performance_preset]
                batch_size = max(1, round(balanced * multiplier))
            except Exception:
                batch_size = 8
    return device, compute_type, batch_size


def load_whisperx_model(
    whisperx: Any,
    config: TranscriptionConfig,
    device: str,
    compute_type: str,
    log: LogFn,
) -> tuple[Any, bool, bool]:
    """Load a model without silently discarding a requested language or task."""
    language = None if config.language == "auto" else config.language
    asr_options = {
        "beam_size": config.beam_size,
        "best_of": config.best_of,
        "temperatures": [0.0],
        "condition_on_previous_text": False,
    }
    if config.initial_prompt.strip():
        asr_options["initial_prompt"] = config.initial_prompt.strip()
    if config.hotwords.strip():
        asr_options["hotwords"] = config.hotwords.strip()
    requested: dict[str, Any] = {
        "compute_type": compute_type,
        "language": language,
        "task": config.task,
        "asr_options": asr_options,
        "vad_options": {"vad_onset": config.vad_onset, "vad_offset": config.vad_offset},
        "vad_method": config.vad_method,
    }
    requested = {key: value for key, value in requested.items() if value is not None}
    kwargs = _supported_kwargs(whisperx.load_model, requested)
    language_at_load = language is not None and "language" in kwargs
    task_at_load = "task" in kwargs

    log("Loading WhisperX model…")
    try:
        model = whisperx.load_model(config.model, device, **kwargs)
    except TypeError as exc:
        # Older releases differ mainly in optional VAD/ASR knobs. Never remove language/task.
        reduced = dict(kwargs)
        removed: list[str] = []
        for key in ("vad_method", "vad_options", "asr_options"):
            if key in reduced:
                removed.append(key)
                reduced.pop(key)
                try:
                    model = whisperx.load_model(config.model, device, **reduced)
                    log("Loaded with compatibility mode; unsupported options: " + ", ".join(removed))
                    language_at_load = language is not None and "language" in reduced
                    task_at_load = "task" in reduced
                    break
                except TypeError:
                    continue
        else:
            raise RuntimeError(f"WhisperX model loading failed: {exc}") from exc

    transcribe_fn = model.transcribe
    if language is not None and not language_at_load and not _accepts(transcribe_fn, "language"):
        raise RuntimeError(
            f"Installed WhisperX cannot enforce language='{language}'. Refusing to auto-detect silently. "
            "Repair the runtime to install the supported WhisperX version."
        )
    if not task_at_load and not _accepts(transcribe_fn, "task"):
        raise RuntimeError(f"Installed WhisperX cannot enforce task='{config.task}'. Repair the runtime and try again.")
    return model, language_at_load, task_at_load


def _transcribe_one(
    model: Any,
    audio: Any,
    config: TranscriptionConfig,
    language_at_load: bool,
    task_at_load: bool,
    batch_size: int,
    progress_callback: Callable[[float], None] | None = None,
    live_segment_callback: Callable[[float, float, str], None] | None = None,
) -> dict[str, Any]:
    kwargs: dict[str, Any] = {
        "batch_size": batch_size,
        "chunk_size": 20 if config.model == "distil-large-v3" and config.chunk_size == 30 else config.chunk_size,
        "print_progress": False,
        "progress_callback": progress_callback,
        "verbose": bool(config.live_preview and live_segment_callback is not None),
    }
    if config.language != "auto" and not language_at_load:
        kwargs["language"] = config.language
    if not task_at_load:
        kwargs["task"] = config.task
    kwargs = _supported_kwargs(model.transcribe, kwargs)
    if config.live_preview and live_segment_callback is not None and kwargs.get("verbose"):
        capture = _WhisperTranscriptCapture(sys.stdout, live_segment_callback)
        with contextlib.redirect_stdout(capture):
            result = model.transcribe(audio, **kwargs)
        capture.flush()
        return result
    return model.transcribe(audio, **kwargs)


def normalize_text(text: str, language: str) -> str:
    text = unicodedata.normalize("NFC", text)
    text = text.replace("\u200e", "").replace("\u200f", "").replace("\ufeff", "")
    if language == "fa":
        text = text.translate(str.maketrans({"ي": "ی", "ى": "ی", "ك": "ک", "ة": "ه"}))
        text = re.sub(r"\s+([،؛؟!,.])", r"\1", text)
        text = re.sub(r"([،؛؟!])(?=\S)", r"\1 ", text)
    text = re.sub(r"[ \t]+", " ", text)
    return text.strip()


def normalize_result(result: dict[str, Any], language: str) -> dict[str, Any]:
    for segment in result.get("segments", []):
        if "text" in segment:
            segment["text"] = normalize_text(str(segment.get("text", "")), language)
        for word in segment.get("words", []) or []:
            if "word" in word:
                word["word"] = normalize_text(str(word.get("word", "")), language)
    result["language"] = language
    return result


def plain_text(segments: Iterable[dict[str, Any]], layout: str = "paragraph") -> str:
    pieces = [str(seg.get("text", "")).strip() for seg in segments]
    separator = "\n" if layout == "segments" else " "
    return separator.join(piece for piece in pieces if piece).strip()


def _wrap_subtitle(text: str, max_chars: int, max_lines: int) -> str:
    lines = textwrap.wrap(
        text,
        width=max_chars,
        break_long_words=False,
        break_on_hyphens=False,
    )
    if len(lines) <= max_lines:
        return "\n".join(lines)
    # Do not discard speech when a source segment is unusually long. Keep the
    # configured line count and let the final line carry the remaining words.
    return "\n".join(lines[: max_lines - 1] + [" ".join(lines[max_lines - 1 :])])


def _timestamp(seconds: float, comma: bool) -> str:
    milliseconds = max(0, int(round(seconds * 1000)))
    hours, milliseconds = divmod(milliseconds, 3_600_000)
    minutes, milliseconds = divmod(milliseconds, 60_000)
    secs, milliseconds = divmod(milliseconds, 1000)
    separator = "," if comma else "."
    return f"{hours:02d}:{minutes:02d}:{secs:02d}{separator}{milliseconds:03d}"


def srt_text(segments: Iterable[dict[str, Any]], max_chars: int = 42, max_lines: int = 2) -> str:
    lines: list[str] = []
    index = 1
    for segment in segments:
        text = str(segment.get("text", "")).strip()
        if not text or "start" not in segment or "end" not in segment:
            continue
        speaker = segment.get("speaker")
        lines.extend(
            [
                str(index),
                f"{_timestamp(float(segment['start']), True)} --> {_timestamp(float(segment['end']), True)}",
                _wrap_subtitle(f"[{speaker}] {text}" if speaker else text, max_chars, max_lines),
                "",
            ]
        )
        index += 1
    return "\n".join(lines)


def vtt_text(segments: Iterable[dict[str, Any]], max_chars: int = 42, max_lines: int = 2) -> str:
    lines = ["WEBVTT", ""]
    for segment in segments:
        text = str(segment.get("text", "")).strip()
        if not text or "start" not in segment or "end" not in segment:
            continue
        speaker = segment.get("speaker")
        lines.extend(
            [
                f"{_timestamp(float(segment['start']), False)} --> {_timestamp(float(segment['end']), False)}",
                _wrap_subtitle(f"<v {speaker}>{text}" if speaker else text, max_chars, max_lines),
                "",
            ]
        )
    return "\n".join(lines)


def _atomic_write_text(path: Path, content: str, *, encoding: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    try:
        with temporary.open("w", encoding=encoding, newline="\n") as handle:
            handle.write(content)
            handle.flush()
            try:
                os.fsync(handle.fileno())
            except OSError:
                pass
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def output_paths(output_dir: Path, stem: str, formats: Iterable[str]) -> dict[str, Path]:
    return {fmt: output_dir / f"{stem}.{fmt}" for fmt in formats}


def formats_to_generate(requested: dict[str, Path], overwrite: bool) -> list[str]:
    return list(requested) if overwrite else [fmt for fmt, path in requested.items() if not path.exists()]


def write_outputs(
    result: dict[str, Any],
    output_dir: Path,
    stem: str,
    formats: Iterable[str],
    *,
    txt_layout: str = "paragraph",
    subtitle_max_chars: int = 42,
    subtitle_max_lines: int = 2,
) -> dict[str, Path]:
    output_dir.mkdir(parents=True, exist_ok=True)
    segments = result.get("segments", [])
    written: dict[str, Path] = {}
    for fmt in formats:
        path = output_dir / f"{stem}.{fmt}"
        if fmt == "txt":
            content = plain_text(segments, txt_layout) + "\n"
            _atomic_write_text(path, content, encoding="utf-8-sig")
        elif fmt == "srt":
            _atomic_write_text(
                path,
                srt_text(segments, subtitle_max_chars, subtitle_max_lines),
                encoding="utf-8-sig",
            )
        elif fmt == "vtt":
            _atomic_write_text(
                path,
                vtt_text(segments, subtitle_max_chars, subtitle_max_lines),
                encoding="utf-8-sig",
            )
        elif fmt == "json":
            _atomic_write_text(
                path,
                json.dumps(result, ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
            )
        else:
            continue
        written[fmt] = path
    return written


def unique_available_path(path: Path) -> Path:
    if not path.exists():
        return path
    counter = 2
    while True:
        candidate = path.with_name(f"{path.stem}_{counter}{path.suffix}")
        if not candidate.exists():
            return candidate
        counter += 1


def _is_cuda_oom(exc: BaseException, torch: Any) -> bool:
    if isinstance(exc, torch.cuda.OutOfMemoryError):
        return True
    message = str(exc).casefold()
    return "out of memory" in message and any(token in message for token in ("cuda", "cublas", "ctranslate2"))


def unique_output_stems(audio_files: Iterable[str]) -> list[str]:
    used: set[str] = set()
    stems: list[str] = []
    for raw_path in audio_files:
        base = Path(raw_path).stem or "transcription"
        candidate = base
        suffix = 2
        while candidate.casefold() in used:
            candidate = f"{base}_{suffix}"
            suffix += 1
        used.add(candidate.casefold())
        stems.append(candidate)
    return stems


def _load_diarizer(whisperx: Any, token: str, device: str) -> Any:
    try:
        from whisperx.diarize import DiarizationPipeline
    except ImportError:
        DiarizationPipeline = whisperx.diarize.DiarizationPipeline
    kwargs = {"token": token, "device": device}
    try:
        return DiarizationPipeline(**kwargs)
    except TypeError:
        return DiarizationPipeline(use_auth_token=token, device=device)


def run_transcription(
    config: TranscriptionConfig,
    *,
    log: LogFn = _log_default,
    event: EventFn = _event_default,
) -> RunStats:
    config.validate()
    configure_runtime_environment()
    _start_parent_watchdog()
    if config.quiet:
        quiet_third_party_logs()

    import torch
    import whisperx

    device, compute_type, batch_size = resolve_device_and_defaults(config, torch)
    output_dir = Path(config.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    device_name = torch.cuda.get_device_name(0) if device == "cuda" else "CPU"
    log(f"Device: {device} ({device_name}) | compute={compute_type} | batch={batch_size}")
    log(f"Model: {config.model} | language={config.language} | task={config.task}")
    event("run_started", {"total": len(config.audio_files), "device": device, "device_name": device_name})
    _raise_if_cancelled()

    event("run_phase", {"phase": "model", "label": f"Loading {config.model} model", "indeterminate": True})
    model, language_at_load, task_at_load = load_whisperx_model(whisperx, config, device, compute_type, log)
    log("Model loaded.")
    event("run_phase", {"phase": "model", "label": "Model ready", "percent": 100.0})
    _raise_if_cancelled()
    if config.live_preview:
        log("Live transcript preview is enabled; text appears as each speech chunk is decoded.")

    diarizer = None
    if config.diarize:
        hf_token = config.hf_token or os.environ.get("BKWHISPERX_HF_TOKEN", "")
        if not hf_token:
            log("Skipping speaker labels: no Hugging Face token available. Set HF_TOKEN or use the wizard to provide one.")
        else:
            log("Loading speaker diarization model…")
            event("run_phase", {"phase": "diarization_model", "label": "Loading diarization model", "indeterminate": True})
            diarizer = _load_diarizer(whisperx, hf_token, device)
            event("run_phase", {"phase": "diarization_model", "label": "Diarization model ready", "percent": 100.0})

    align_cache: dict[str, tuple[Any, Any]] = {}
    combined_handle = None
    combined_path: Path | None = None
    combined_temporary: Path | None = None
    if config.combine:
        requested_combined = output_dir / "combined_transcription.txt"
        combined_path = requested_combined if config.overwrite else unique_available_path(requested_combined)
        combined_temporary = combined_path.with_name(f".{combined_path.name}.{uuid.uuid4().hex}.tmp")
        combined_handle = combined_temporary.open("w", encoding="utf-8-sig", newline="\n")
        event("combined_output", {"path": str(combined_path)})

    stats = RunStats()
    run_started = time.monotonic()
    output_stems = unique_output_stems(config.audio_files)
    try:
        for index, raw_path in enumerate(config.audio_files, start=1):
            _raise_if_cancelled()
            path = Path(raw_path)
            output_stem = output_stems[index - 1]
            event("file_started", {"index": index, "total": len(config.audio_files), "name": path.name})
            log(f"[{index}/{len(config.audio_files)}] {path.name}")
            requested_paths = output_paths(output_dir, output_stem, config.formats)
            formats_to_write = formats_to_generate(requested_paths, config.overwrite)
            if not formats_to_write:
                stats.skipped += 1
                log("Skipped: every requested output already exists.")
                if combined_handle:
                    primary = "txt" if "txt" in requested_paths else config.formats[0]
                    primary_path = requested_paths[primary]
                    combined_handle.write(f"=== {path.name} (skipped) ===\n")
                    combined_handle.write(primary_path.read_text(encoding="utf-8-sig", errors="replace") + "\n\n")
                    combined_handle.flush()
                event(
                    "file_skipped",
                    {
                        "index": index,
                        "total": len(config.audio_files),
                        "name": path.name,
                        "outputs": [str(value) for value in requested_paths.values()],
                    },
                )
                continue
            if len(formats_to_write) != len(config.formats):
                kept = sorted(set(config.formats) - set(formats_to_write))
                log(f"Keeping existing {', '.join(kept)} output(s); generating missing formats.")

            file_started = time.monotonic()
            try:
                event(
                    "file_phase",
                    {
                        "index": index,
                        "total": len(config.audio_files),
                        "name": path.name,
                        "phase": "decode",
                        "label": "Loading media",
                    },
                )
                audio = whisperx.load_audio(str(path))
                current_batch = batch_size
                result: dict[str, Any] | None = None
                while current_batch >= 1:
                    try:

                        def on_progress(percent: float, file_index: int = index, file_name: str = path.name) -> None:
                            _raise_if_cancelled()
                            event(
                                "file_progress",
                                {
                                    "index": file_index,
                                    "total": len(config.audio_files),
                                    "name": file_name,
                                    "percent": max(0.0, min(100.0, float(percent))),
                                },
                            )

                        def on_live_segment(
                            start: float,
                            end: float,
                            text: str,
                            file_index: int = index,
                            file_name: str = path.name,
                        ) -> None:
                            _raise_if_cancelled()
                            language_hint = config.language if config.language != "auto" else ""
                            clean_text = normalize_text(text, language_hint)
                            if not clean_text:
                                return
                            event(
                                "live_segment",
                                {
                                    "index": file_index,
                                    "total": len(config.audio_files),
                                    "name": file_name,
                                    "start": start,
                                    "end": end,
                                    "text": clean_text,
                                    "words": re.findall(r"\S+\s*", clean_text),
                                },
                            )

                        result = _transcribe_one(
                            model,
                            audio,
                            config,
                            language_at_load,
                            task_at_load,
                            current_batch,
                            progress_callback=on_progress,
                            live_segment_callback=on_live_segment if config.live_preview else None,
                        )
                        break
                    except Exception as exc:
                        if not _is_cuda_oom(exc, torch):
                            raise
                        gc.collect()
                        if torch.cuda.is_available():
                            torch.cuda.empty_cache()
                        if current_batch == 1:
                            raise exc
                        current_batch = max(1, current_batch // 2)
                        log(f"GPU memory was full; retrying with batch size {current_batch}.")
                if result is None:
                    raise RuntimeError("WhisperX returned no transcription result.")

                detected_language = str(result.get("language") or "unknown")
                effective_language = config.language if config.language != "auto" else detected_language
                if config.language != "auto" and detected_language not in {"unknown", config.language}:
                    log(
                        f"WhisperX reported '{detected_language}', but manual language '{config.language}' "
                        "is enforced for this run."
                    )
                result = normalize_result(result, effective_language)

                if config.timestamps and config.task == "transcribe":
                    try:
                        event(
                            "file_phase",
                            {
                                "index": index,
                                "total": len(config.audio_files),
                                "name": path.name,
                                "phase": "alignment",
                                "label": "Aligning timestamps",
                            },
                        )
                        if effective_language not in align_cache:
                            log(f"Loading alignment model for {effective_language}…")
                            align_cache[effective_language] = whisperx.load_align_model(
                                language_code=effective_language, device=device
                            )
                        align_model, metadata = align_cache[effective_language]
                        result = whisperx.align(
                            result["segments"],
                            align_model,
                            metadata,
                            audio,
                            device,
                            return_char_alignments=False,
                        )
                        result = normalize_result(result, effective_language)
                    except Exception as exc:
                        log(f"Alignment was unavailable; keeping segment timestamps. ({exc})")
                elif config.timestamps and config.task == "translate":
                    log("Translation uses Whisper segment timestamps; forced source-language alignment is skipped.")

                if diarizer is not None:
                    event(
                        "file_phase",
                        {
                            "index": index,
                            "total": len(config.audio_files),
                            "name": path.name,
                            "phase": "diarization",
                            "label": "Identifying speakers",
                        },
                    )
                    diarization = diarizer(
                        audio,
                        min_speakers=config.min_speakers,
                        max_speakers=config.max_speakers,
                    )
                    result = whisperx.assign_word_speakers(diarization, result)

                event(
                    "file_phase",
                    {
                        "index": index,
                        "total": len(config.audio_files),
                        "name": path.name,
                        "phase": "export",
                        "label": "Writing outputs",
                    },
                )
                write_outputs(
                    result,
                    output_dir,
                    output_stem,
                    formats_to_write,
                    txt_layout=config.txt_layout,
                    subtitle_max_chars=config.subtitle_max_chars,
                    subtitle_max_lines=config.subtitle_max_lines,
                )
                if combined_handle:
                    combined_handle.write(f"=== {path.name} ({effective_language}) ===\n")
                    combined_handle.write(plain_text(result.get("segments", []), config.txt_layout) + "\n\n")
                    combined_handle.flush()
                stats.completed += 1
                elapsed = time.monotonic() - file_started
                all_outputs = [requested_paths[fmt] for fmt in config.formats]
                log(f"Completed in {elapsed:.1f}s: " + ", ".join(str(p) for p in all_outputs))
                event(
                    "file_completed",
                    {
                        "index": index,
                        "total": len(config.audio_files),
                        "name": path.name,
                        "language": effective_language,
                        "elapsed": elapsed,
                        "outputs": [str(p) for p in all_outputs],
                    },
                )
                del audio, result
            except TranscriptionCancelled:
                log("Cancellation requested; releasing the current transcription safely…")
                event("run_cancelling", {"index": index, "name": path.name})
                raise
            except Exception as exc:
                stats.failed += 1
                log(f"Failed: {type(exc).__name__}: {exc}")
                event(
                    "file_failed",
                    {
                        "index": index,
                        "total": len(config.audio_files),
                        "name": path.name,
                        "error": f"{type(exc).__name__}: {exc}",
                    },
                )
                if os.environ.get("BKWHISPERX_DEBUG") == "1":
                    log(traceback.format_exc())
    except TranscriptionCancelled:
        stats.elapsed_seconds = time.monotonic() - run_started
        event(
            "run_cancelled",
            {
                "completed": stats.completed,
                "skipped": stats.skipped,
                "failed": stats.failed,
                "elapsed": stats.elapsed_seconds,
            },
        )
        log(
            f"Stopped safely: {stats.completed} completed, {stats.skipped} skipped, "
            f"{stats.failed} failed in {stats.elapsed_seconds:.1f}s."
        )
        raise
    finally:
        if combined_handle:
            combined_handle.flush()
            try:
                os.fsync(combined_handle.fileno())
            except OSError:
                pass
            combined_handle.close()
        if combined_temporary is not None and combined_path is not None:
            try:
                os.replace(combined_temporary, combined_path)
            finally:
                combined_temporary.unlink(missing_ok=True)

    stats.elapsed_seconds = time.monotonic() - run_started
    event("run_completed", {"completed": stats.completed, "skipped": stats.skipped, "failed": stats.failed})
    log(
        f"Finished: {stats.completed} completed, {stats.skipped} skipped, "
        f"{stats.failed} failed in {stats.elapsed_seconds:.1f}s."
    )
    if stats.failed:
        raise RuntimeError(f"{stats.failed} file(s) failed. Check the log for details.")
    return stats
