from __future__ import annotations

import sys

import bkwhisperx.console as console
from bkwhisperx.console import contains_rtl, rtl_block_for_terminal, rtl_for_terminal, terminal_cell_width


def test_logical_mode_preserves_persian_and_mixed_filename() -> None:
    value = "سینمای مؤلف ۱.m4a"
    assert rtl_for_terminal(value, mode="logical") == value


def test_visual_mode_is_display_only_and_keeps_numbers_extension() -> None:
    value = "سینمای مؤلف ۱.m4a"
    display = rtl_for_terminal(value, mode="visual")
    assert display != value
    assert "m4a" in display
    assert "۱" in display
    assert value == "سینمای مؤلف ۱.m4a"


def test_rtl_block_wraps_before_visual_shaping() -> None:
    lines = rtl_block_for_terminal(
        "این یک متن فارسی طولانی برای بررسی نمایش در پنجره خط فرمان است",
        terminal_width=24,
        mode="visual",
    )
    assert len(lines) >= 2
    assert all(terminal_cell_width(line) == 23 for line in lines)


def test_rtl_block_keeps_logical_sentence_and_line_order(monkeypatch) -> None:
    value = "این جمله باید از راست خوانده شود و ترتیب کلمات و خطوط آن هرگز برعکس نشود"
    rendered_logical_lines: list[str] = []

    def capture(text: str, **_kwargs) -> str:
        rendered_logical_lines.append(text)
        return text

    monkeypatch.setattr(console, "rtl_for_terminal", capture)
    lines = rtl_block_for_terminal(value, terminal_width=28, mode="visual")

    assert " ".join(rendered_logical_lines) == value
    assert rendered_logical_lines[0].startswith("این جمله")
    assert rendered_logical_lines[-1].endswith("برعکس نشود")
    assert all(terminal_cell_width(line) == 27 for line in lines)


def test_mixed_persian_filename_is_wrapped_and_right_aligned() -> None:
    value = "سینمای مؤلف ۱.m4a"
    lines = rtl_block_for_terminal(value, terminal_width=32, mode="visual", base_dir="R")

    assert len(lines) == 1
    assert terminal_cell_width(lines[0]) == 31
    assert "m4a" in lines[0]
    assert not lines[0].rstrip().endswith(" ")


def test_long_filename_cannot_fall_back_to_terminal_auto_wrap() -> None:
    value = "یک_نام_فایل_فارسی_بسیار_طولانی_بدون_فاصله.m4a"
    lines = rtl_block_for_terminal(value, terminal_width=18, mode="visual", base_dir="R")

    assert len(lines) >= 2
    assert all(terminal_cell_width(line) <= 17 for line in lines)


def test_multiline_preview_preserves_paragraph_boundaries() -> None:
    lines = rtl_block_for_terminal("خط اول\nخط دوم", terminal_width=20, mode="visual")
    assert len(lines) == 2
    assert all(terminal_cell_width(line) == 19 for line in lines)


def test_unicode_rtl_detection_covers_extended_and_presentation_forms() -> None:
    assert contains_rtl("سلام")
    assert contains_rtl("﷽")
    assert not contains_rtl("report 123.m4a")


def test_bidi_override_controls_are_never_emitted() -> None:
    value = "فایل\u202eexe.txt"
    display = rtl_for_terminal(value, mode="logical")
    assert "\u202e" not in display
    assert display == "فایلexe.txt"


def test_visual_output_has_no_lrm_marks_and_preserves_extension() -> None:
    display = rtl_for_terminal("سینمای مؤلف ۱.m4a", mode="visual", base_dir="R")
    assert display != "سینمای مؤلف ۱.m4a"
    assert "\u200e" not in display
    assert "m4a" in display
    assert "۱" in display


def test_auto_mode_uses_logical_text_in_windows_terminal(monkeypatch) -> None:
    monkeypatch.setattr(console.os, "name", "nt")
    monkeypatch.setenv("WT_SESSION", "1")
    monkeypatch.delenv("BKWHISPERX_LEGACY_RTL", raising=False)
    assert console._display_mode("auto") == "logical"


def test_auto_mode_keeps_visual_text_for_legacy_windows_console(monkeypatch) -> None:
    monkeypatch.setattr(console.os, "name", "nt")
    monkeypatch.setattr(sys, "getwindowsversion", lambda: type("ver", (), {"build": 9600})())
    monkeypatch.delenv("WT_SESSION", raising=False)
    monkeypatch.delenv("ANSICON", raising=False)
    monkeypatch.delenv("ConEmuANSI", raising=False)
    monkeypatch.delenv("TERM", raising=False)
    monkeypatch.delenv("TERM_PROGRAM", raising=False)
    monkeypatch.delenv("BKWHISPERX_LEGACY_RTL", raising=False)
    assert console._display_mode("auto") == "visual"
