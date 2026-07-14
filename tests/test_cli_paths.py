from __future__ import annotations

from pathlib import Path

from bkwhisperx.cli import _split_paths
from bkwhisperx.config import collect_media


def test_split_paths_removes_windows_drag_drop_quotes() -> None:
    paths = _split_paths('"C:\\Users\\benya\\Desktop\\morti new"')
    assert paths == [Path(r"C:\Users\benya\Desktop\morti new")]


def test_split_paths_keeps_unquoted_windows_spaces() -> None:
    paths = _split_paths(r"C:\Users\benya\Desktop\morti new")
    assert paths == [Path(r"C:\Users\benya\Desktop\morti new")]


def test_split_paths_accepts_semicolon_separated_paths() -> None:
    paths = _split_paths('"C:\\one file.m4a"; "C:\\two file.wav"')
    assert paths == [Path(r"C:\one file.m4a"), Path(r"C:\two file.wav")]


def test_collect_media_supports_persian_m4a_filename(tmp_path: Path) -> None:
    media = tmp_path / "سبک‌شناسی سینما. سینمای مؤلف ۱.m4a"
    media.write_bytes(b"test")
    assert collect_media(tmp_path) == [media]
