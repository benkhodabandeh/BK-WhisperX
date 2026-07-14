from __future__ import annotations

from bkwhisperx.runner import _download_event, _event_log_line


def test_live_transcript_is_not_persisted_to_event_log() -> None:
    assert _event_log_line("live_segment", {"text": "متن خصوصی"}) is None


def test_safe_event_log_excludes_error_and_output_payloads() -> None:
    line = _event_log_line(
        "file_completed",
        {"index": 1, "name": "clip.wav", "outputs": ["secret/path.txt"], "text": "private"},
    )
    assert line is not None
    assert "clip.wav" in line
    assert "secret/path.txt" not in line
    assert "private" not in line


def test_huggingface_progress_line_becomes_structured_transfer() -> None:
    payload = _download_event("model.safetensors: 25%|██▌       | 250MB/1.0GB [00:05<00:15, 50MB/s]")
    assert payload is not None
    assert payload["percent"] == 25.0
    assert payload["current"] == 250_000_000
    assert payload["total"] == 1_000_000_000
    assert payload["speed"] == 50_000_000
