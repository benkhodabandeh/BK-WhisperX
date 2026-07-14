from __future__ import annotations

import json
import os
import re
import uuid
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

SUPPORTED_EXTENSIONS = {
    ".mp3",
    ".wav",
    ".m4a",
    ".flac",
    ".ogg",
    ".wma",
    ".aac",
    ".opus",
    ".mp4",
    ".mkv",
    ".mov",
    ".avi",
    ".webm",
    ".m4v",
    ".mpeg",
    ".mpg",
}

MODELS = ["tiny", "base", "small", "medium", "large-v2", "large-v3", "distil-large-v3"]
PERFORMANCE_PRESETS = ("safe", "balanced", "maximum")
TXT_LAYOUTS = ("paragraph", "segments")
LANGUAGES = [
    ("auto", "Auto detect per file"),
    ("fa", "Persian / Farsi"),
    ("en", "English"),
    ("ar", "Arabic"),
    ("tr", "Turkish"),
    ("de", "German"),
    ("fr", "French"),
    ("es", "Spanish"),
    ("it", "Italian"),
    ("ru", "Russian"),
    ("zh", "Chinese"),
    ("ja", "Japanese"),
    ("ko", "Korean"),
    ("custom", "Type another language code"),
]


@dataclass(slots=True)
class TranscriptionConfig:
    audio_files: list[str]
    output_dir: str
    model: str = "large-v3"
    device: str = "auto"
    compute_type: str = "auto"
    batch_size: int = 0
    language: str = "auto"
    task: str = "transcribe"
    formats: list[str] = field(default_factory=lambda: ["txt"])
    timestamps: bool = False
    diarize: bool = False
    hf_token: str | None = None
    min_speakers: int | None = None
    max_speakers: int | None = None
    combine: bool = True
    overwrite: bool = False
    live_preview: bool = False
    chunk_size: int = 30
    beam_size: int = 5
    best_of: int = 5
    performance_preset: str = "balanced"
    initial_prompt: str = ""
    hotwords: str = ""
    txt_layout: str = "paragraph"
    subtitle_max_chars: int = 42
    subtitle_max_lines: int = 2
    vad_method: str = "silero"
    vad_onset: float = 0.500
    vad_offset: float = 0.363
    quiet: bool = True

    def validate(self) -> None:
        self.language = self.language.strip().lower()
        if not self.audio_files:
            raise ValueError("No audio or video files were selected.")
        missing = [p for p in self.audio_files if not Path(p).is_file()]
        if missing:
            raise ValueError(f"Input file does not exist: {missing[0]}")
        unsupported = [p for p in self.audio_files if Path(p).suffix.lower() not in SUPPORTED_EXTENSIONS]
        if unsupported:
            raise ValueError(f"Unsupported input file extension: {Path(unsupported[0]).suffix or '(none)'}")
        if self.model not in MODELS:
            raise ValueError(f"Unsupported model: {self.model}")
        if self.model == "distil-large-v3" and self.language != "en":
            raise ValueError(
                "distil-large-v3 is English-only and cannot reliably transcribe Persian or auto-detect "
                "multilingual audio. Select language 'en', or use large-v3/large-v2 for Persian and auto mode."
            )
        if self.device not in {"auto", "cuda", "cpu"}:
            raise ValueError(f"Unsupported device: {self.device}")
        if self.compute_type not in {"auto", "int8", "float16", "float32"}:
            raise ValueError(f"Unsupported compute type: {self.compute_type}")
        if self.batch_size < 0:
            raise ValueError("Batch size cannot be negative.")
        if self.chunk_size <= 0:
            raise ValueError("Chunk size must be greater than zero.")
        if self.beam_size <= 0 or self.best_of <= 0:
            raise ValueError("Beam size and best-of must be greater than zero.")
        if self.performance_preset not in PERFORMANCE_PRESETS:
            raise ValueError(f"Unsupported performance preset: {self.performance_preset}")
        if self.txt_layout not in TXT_LAYOUTS:
            raise ValueError(f"Unsupported TXT layout: {self.txt_layout}")
        if not 16 <= self.subtitle_max_chars <= 120:
            raise ValueError("Subtitle maximum characters must be between 16 and 120.")
        if not 1 <= self.subtitle_max_lines <= 4:
            raise ValueError("Subtitle maximum lines must be between 1 and 4.")
        if len(self.initial_prompt) > 4000 or len(self.hotwords) > 4000:
            raise ValueError("Initial prompt and hotwords must each be 4,000 characters or fewer.")
        if not 0.0 <= self.vad_onset <= 1.0 or not 0.0 <= self.vad_offset <= 1.0:
            raise ValueError("VAD onset and offset must be between 0 and 1.")
        if self.task not in {"transcribe", "translate"}:
            raise ValueError(f"Unsupported task: {self.task}")
        allowed_formats = {"txt", "srt", "vtt", "json"}
        if not self.formats:
            raise ValueError("Select at least one export format.")
        invalid = set(self.formats) - allowed_formats
        if invalid:
            raise ValueError(f"Unsupported export format: {sorted(invalid)}")
        if self.diarize and not self.hf_token:
            raise ValueError("Speaker diarization requires a Hugging Face token.")
        for value, label in ((self.min_speakers, "Minimum speakers"), (self.max_speakers, "Maximum speakers")):
            if value is not None and value < 1:
                raise ValueError(f"{label} must be at least 1.")
        if self.min_speakers is not None and self.max_speakers is not None:
            if self.min_speakers > self.max_speakers:
                raise ValueError("Minimum speakers cannot exceed maximum speakers.")
        if self.diarize:
            self.timestamps = True
        if any(fmt in {"srt", "vtt", "json"} for fmt in self.formats):
            self.timestamps = True
        if self.language != "auto" and not re.fullmatch(r"[A-Za-z]{2,3}(?:-[A-Za-z0-9]{2,8})*", self.language.strip()):
            raise ValueError("Language must be 'auto' or a valid code such as fa, en, or zh-CN.")

        output_dir = Path(self.output_dir).expanduser()
        if output_dir.exists() and not output_dir.is_dir():
            raise ValueError(f"Output path is not a folder: {output_dir}")

    def save(self, path: Path, *, include_secrets: bool = True) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        data = asdict(self)
        if not include_secrets:
            data["hf_token"] = None
        temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
        try:
            temporary.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
            os.replace(temporary, path)
        finally:
            temporary.unlink(missing_ok=True)

    @classmethod
    def load(cls, path: Path) -> TranscriptionConfig:
        data: dict[str, Any] = json.loads(path.read_text(encoding="utf-8"))
        if not data.get("hf_token"):
            data["hf_token"] = os.environ.get("BKWHISPERX_HF_TOKEN") or os.environ.get("HF_TOKEN")
        cfg = cls(**data)
        cfg.validate()
        return cfg


def collect_media(path: Path, recursive: bool = False) -> list[Path]:
    if path.is_file():
        return [path] if path.suffix.lower() in SUPPORTED_EXTENSIONS else []
    if not path.is_dir():
        return []
    iterator = path.rglob("*") if recursive else path.glob("*")
    return sorted(p for p in iterator if p.is_file() and p.suffix.lower() in SUPPORTED_EXTENSIONS)
