from __future__ import annotations

from pathlib import Path

from bkwhisperx.config import TranscriptionConfig, collect_media


def test_subtitle_format_enables_timestamps(tmp_path: Path) -> None:
    media = tmp_path / "sample.wav"
    media.write_bytes(b"test")
    config = TranscriptionConfig(
        audio_files=[str(media)],
        output_dir=str(tmp_path / "out"),
        language="fa",
        formats=["srt"],
    )
    config.validate()
    assert config.timestamps is True


def test_collect_media_is_sorted_and_filters_extensions(tmp_path: Path) -> None:
    (tmp_path / "b.mp3").write_bytes(b"")
    (tmp_path / "a.m4a").write_bytes(b"")
    (tmp_path / "ignore.txt").write_text("x")
    assert [path.name for path in collect_media(tmp_path)] == ["a.m4a", "b.mp3"]


def test_distil_model_rejects_persian_and_auto(tmp_path: Path) -> None:
    media = tmp_path / "sample.wav"
    media.write_bytes(b"test")
    for language in ("fa", "auto"):
        config = TranscriptionConfig(
            audio_files=[str(media)],
            output_dir=str(tmp_path / "out"),
            model="distil-large-v3",
            language=language,
        )
        try:
            config.validate()
        except ValueError as exc:
            assert "English-only" in str(exc)
        else:
            raise AssertionError("English-only model accepted a non-English/auto language")


def test_empty_formats_are_rejected(tmp_path: Path) -> None:
    media = tmp_path / "sample.wav"
    media.write_bytes(b"test")
    config = TranscriptionConfig(
        audio_files=[str(media)],
        output_dir=str(tmp_path / "out"),
        formats=[],
    )
    try:
        config.validate()
    except ValueError as exc:
        assert "at least one export format" in str(exc)
    else:
        raise AssertionError("An empty export list was accepted")


def test_job_config_can_exclude_and_restore_hf_token(tmp_path: Path, monkeypatch) -> None:
    media = tmp_path / "sample.wav"
    media.write_bytes(b"test")
    path = tmp_path / "job.json"
    config = TranscriptionConfig(
        audio_files=[str(media)],
        output_dir=str(tmp_path / "out"),
        diarize=True,
        hf_token="hf_private_value",
    )
    config.save(path, include_secrets=False)
    assert "hf_private_value" not in path.read_text(encoding="utf-8")

    monkeypatch.setenv("BKWHISPERX_HF_TOKEN", "hf_private_value")
    restored = TranscriptionConfig.load(path)
    assert restored.hf_token == "hf_private_value"


def test_unsupported_input_extension_is_rejected(tmp_path: Path) -> None:
    media = tmp_path / "notes.txt"
    media.write_text("not media", encoding="utf-8")
    config = TranscriptionConfig(audio_files=[str(media)], output_dir=str(tmp_path / "out"))
    try:
        config.validate()
    except ValueError as exc:
        assert "Unsupported input file extension" in str(exc)
    else:
        raise AssertionError("Unsupported media extension was accepted")


def test_enhanced_accuracy_and_formatting_settings_round_trip(tmp_path: Path) -> None:
    media = tmp_path / "sample.wav"
    media.write_bytes(b"test")
    path = tmp_path / "job.json"
    config = TranscriptionConfig(
        audio_files=[str(media)],
        output_dir=str(tmp_path / "out"),
        performance_preset="maximum",
        initial_prompt="نام درست: کیارستمی",
        hotwords="کیارستمی, سینما",
        txt_layout="segments",
        subtitle_max_chars=36,
        subtitle_max_lines=2,
    )
    config.save(path)
    loaded = TranscriptionConfig.load(path)
    assert loaded.performance_preset == "maximum"
    assert loaded.initial_prompt == "نام درست: کیارستمی"
    assert loaded.hotwords == "کیارستمی, سینما"
    assert loaded.txt_layout == "segments"
