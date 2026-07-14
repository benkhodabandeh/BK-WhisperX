from __future__ import annotations

import json
from pathlib import Path

from bkwhisperx.engine import (
    formats_to_generate,
    normalize_text,
    srt_text,
    unique_available_path,
    unique_output_stems,
    write_outputs,
)


def test_persian_normalization() -> None:
    assert normalize_text("يك تست  ،خوب", "fa") == "یک تست، خوب"


def test_text_subtitle_outputs_use_windows_friendly_utf8_bom(tmp_path: Path) -> None:
    result = {
        "language": "fa",
        "segments": [{"start": 0.0, "end": 1.2, "text": "سلام دنیا"}],
    }
    written = write_outputs(result, tmp_path, "sample", ["txt", "srt", "vtt", "json"])
    assert written["txt"].read_bytes().startswith(b"\xef\xbb\xbf")
    assert written["srt"].read_bytes().startswith(b"\xef\xbb\xbf")
    assert written["vtt"].read_bytes().startswith(b"\xef\xbb\xbf")
    assert not written["json"].read_bytes().startswith(b"\xef\xbb\xbf")
    assert json.loads(written["json"].read_text(encoding="utf-8"))["language"] == "fa"


def test_srt_generation_keeps_unicode_and_timestamps() -> None:
    value = srt_text([{"start": 1.25, "end": 2.5, "text": "متن فارسی"}])
    assert "00:00:01,250 --> 00:00:02,500" in value
    assert "متن فارسی" in value


def test_duplicate_input_stems_get_unique_output_names(tmp_path: Path) -> None:
    first = tmp_path / "one" / "clip.wav"
    second = tmp_path / "two" / "clip.mp3"
    third = tmp_path / "three" / "clip_2.m4a"
    assert unique_output_stems([str(first), str(second), str(third)]) == ["clip", "clip_2", "clip_2_2"]


def test_only_missing_output_formats_are_generated(tmp_path: Path) -> None:
    existing = tmp_path / "sample.txt"
    existing.write_text("keep", encoding="utf-8")
    requested = {"txt": existing, "srt": tmp_path / "sample.srt", "json": tmp_path / "sample.json"}
    assert formats_to_generate(requested, overwrite=False) == ["srt", "json"]
    assert formats_to_generate(requested, overwrite=True) == ["txt", "srt", "json"]


def test_unique_combined_path_does_not_overwrite(tmp_path: Path) -> None:
    first = tmp_path / "combined_transcription.txt"
    second = tmp_path / "combined_transcription_2.txt"
    first.write_text("one", encoding="utf-8")
    second.write_text("two", encoding="utf-8")
    assert unique_available_path(first).name == "combined_transcription_3.txt"


def test_txt_segment_layout_and_subtitle_wrapping(tmp_path: Path) -> None:
    result = {
        "language": "fa",
        "segments": [
            {"start": 0.0, "end": 1.0, "text": "بخش نخست متن"},
            {"start": 1.0, "end": 2.0, "text": "بخش دوم متن طولانی"},
        ],
    }
    written = write_outputs(
        result,
        tmp_path,
        "sample",
        ["txt", "srt"],
        txt_layout="segments",
        subtitle_max_chars=10,
        subtitle_max_lines=2,
    )
    assert "بخش نخست متن\nبخش دوم" in written["txt"].read_text(encoding="utf-8-sig")
    assert "\n" in written["srt"].read_text(encoding="utf-8-sig")


def test_atomic_output_leaves_no_temporary_files(tmp_path: Path) -> None:
    result = {"language": "fa", "segments": [{"text": "متن سالم"}]}
    write_outputs(result, tmp_path, "sample", ["txt"])
    assert [path.name for path in tmp_path.iterdir()] == ["sample.txt"]
