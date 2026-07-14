from __future__ import annotations

import io

import bkwhisperx.cli as cli
from bkwhisperx.config import TranscriptionConfig
from bkwhisperx.engine import _transcribe_one, _WhisperTranscriptCapture


def test_whisper_verbose_line_becomes_live_segment() -> None:
    forwarded = io.StringIO()
    captured: list[tuple[float, float, str]] = []
    stream = _WhisperTranscriptCapture(forwarded, lambda start, end, text: captured.append((start, end, text)))

    stream.write("ordinary log\n")
    stream.write("Transcript: [1.25 --> 3.50] سلام دنیا\n")
    stream.flush()

    assert forwarded.getvalue() == "ordinary log\n"
    assert captured == [(1.25, 3.5, "سلام دنیا")]


class _VerboseModel:
    def transcribe(
        self,
        _audio,
        *,
        batch_size=1,
        chunk_size=30,
        print_progress=False,
        progress_callback=None,
        verbose=False,
    ):
        del batch_size, chunk_size, print_progress
        if progress_callback:
            progress_callback(50.0)
        if verbose:
            print("Transcript: [0.00 --> 2.00] یک متن آزمایشی")
        return {"segments": [{"text": " یک متن آزمایشی", "start": 0.0, "end": 2.0}], "language": "fa"}


def test_transcribe_one_emits_live_segment_without_changing_result(tmp_path) -> None:
    media = tmp_path / "sample.wav"
    media.write_bytes(b"placeholder")
    config = TranscriptionConfig(
        audio_files=[str(media)],
        output_dir=str(tmp_path / "out"),
        language="fa",
        live_preview=True,
    )
    segments = []
    result = _transcribe_one(
        _VerboseModel(),
        object(),
        config,
        language_at_load=True,
        task_at_load=True,
        batch_size=1,
        live_segment_callback=lambda start, end, text: segments.append((start, end, text)),
    )
    assert segments == [(0.0, 2.0, "یک متن آزمایشی")]
    assert result["segments"][0]["text"].strip() == "یک متن آزمایشی"


def test_cli_live_preview_uses_text_payload_for_rtl_rendering(monkeypatch) -> None:
    captured: list[str] = []

    def fake_block(text: str, **kwargs) -> list[str]:
        captured.append(text)
        return [text]

    monkeypatch.setattr(cli, "rtl_block_for_terminal", fake_block)

    lines = cli._live_preview_lines({"text": "سلام دنیا", "words": ["ignored"]})

    assert lines == ["\u0627\u06cc\u0646\u062f \u0645\u0627\u0644\u0633"]
    assert captured == []


def test_cli_live_preview_rebuilds_word_payload_with_separators(monkeypatch) -> None:
    captured: list[str] = []

    def fake_block(text: str, **_kwargs) -> list[str]:
        captured.append(text)
        return [text]

    monkeypatch.setattr(cli, "rtl_block_for_terminal", fake_block)

    lines = cli._live_preview_lines(
        {
            "words": [
                {"word": "سلام", "separator": " "},
                {"word": "دنیا", "separator": ""},
            ]
        }
    )

    assert lines == ["\u0627\u06cc\u0646\u062f \u0645\u0627\u0644\u0633"]
    assert captured == []
