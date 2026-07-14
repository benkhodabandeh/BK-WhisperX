from __future__ import annotations

import ctypes
import os
import re
import shutil
import sys
import unicodedata
from typing import TextIO

ANSI_RE = re.compile(r"\x1b\[[0-9;]*m")
_LTR_MARK = "\u200e"


def configure_utf8() -> None:
    if os.name == "nt":
        try:
            kernel32 = ctypes.windll.kernel32
            kernel32.SetConsoleOutputCP(65001)
            kernel32.SetConsoleCP(65001)
            handle = kernel32.GetStdHandle(-11)
            mode = ctypes.c_uint32()
            if kernel32.GetConsoleMode(handle, ctypes.byref(mode)):
                if kernel32.SetConsoleMode(handle, mode.value | 0x0004):
                    os.environ["BKWHISPERX_ANSI"] = "1"
        except Exception:
            pass
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass


def supports_color(stream: TextIO = sys.stdout) -> bool:
    if not getattr(stream, "isatty", lambda: False)():
        return False
    if os.name != "nt":
        return True
    return bool(
        os.environ.get("BKWHISPERX_ANSI")
        or os.environ.get("WT_SESSION")
        or os.environ.get("ANSICON")
        or os.environ.get("TERM")
    )


def color(text: str, code: str) -> str:
    return f"\033[{code}m{text}\033[0m" if supports_color() else text


def brand(text: str) -> str:
    return color(text, "1;38;5;75")


def success(text: str) -> str:
    return color(text, "38;5;78")


def warning(text: str) -> str:
    return color(text, "38;5;220")


def error(text: str) -> str:
    return color(text, "38;5;203")


def _is_rtl_character(character: str) -> bool:
    return (
        unicodedata.bidirectional(character) in {"R", "AL"}
        or "\ufb50" <= character <= "\ufdff"
        or "\ufe70" <= character <= "\ufefc"
    )


def contains_rtl(text: str) -> bool:
    # Use the Unicode bidi property instead of a fixed list of blocks. This
    # covers Persian/Arabic presentation forms, newer Arabic extensions, and
    # Hebrew without misclassifying neutral punctuation or European digits.
    return any(_is_rtl_character(character) for character in text)


def _display_mode(mode: str | None = None) -> str:
    requested = (mode or os.environ.get("BKWHISPERX_RTL_MODE", "auto")).strip().lower()
    if os.environ.get("BKWHISPERX_LEGACY_RTL") == "1":
        requested = "visual"
    if requested not in {"auto", "visual", "logical"}:
        requested = "auto"
    if requested == "auto":
        # Only legacy Windows console hosts still need display-only shaping.
        # Modern terminals such as Windows Terminal, VS Code, ConEmu, and
        # ANSI/TERM-backed shells handle logical Unicode more reliably.
        return "visual" if _needs_visual_rtl_in_auto_mode() else "logical"
    return requested


def _needs_visual_rtl_in_auto_mode() -> bool:
    if os.name != "nt":
        return False
    modern_markers = (
        os.environ.get("WT_SESSION"),
        os.environ.get("ANSICON"),
        os.environ.get("ConEmuANSI"),
        os.environ.get("TERM"),
    )
    term_program = (os.environ.get("TERM_PROGRAM") or "").strip().lower()
    if any(marker for marker in modern_markers):
        return False
    if term_program in {"vscode", "windows_terminal"}:
        return False
    # ponytail: conhost.exe on Win10+ has DirectWrite but its bidi is
    # unreliable for Persian/Arabic. Only skip visual reshaping when we
    # positively identify a modern terminal above — cmd.exe gets the reshaped
    # text to avoid reversed display.
    return True


def _base_direction(text: str) -> str:
    """Select a stable paragraph direction using the first strong character."""
    for character in text:
        bidi = unicodedata.bidirectional(character)
        if bidi in {"R", "AL"}:
            return "R"
        if bidi == "L":
            return "L"
    return "R" if contains_rtl(text) else "L"


def rtl_for_terminal(text: str, *, mode: str | None = None, base_dir: str | None = None) -> str:
    """Prepare Unicode for display without ever mutating exported text."""
    clean = unicodedata.normalize("NFC", text.translate(_BIDI_CONTROL_TRANSLATION))
    if _display_mode(mode) != "visual" or not contains_rtl(clean):
        return clean

    try:
        import arabic_reshaper
        from bidi.algorithm import get_display
    except ImportError:
        # Source launchers may run before the lightweight project dependencies
        # are installed. Reuse their pure-Python copies from the managed runtime.
        try:
            from .paths import venv_dir

            candidates = [venv_dir() / "Lib" / "site-packages"]
            candidates.extend((venv_dir() / "lib").glob("python*/site-packages"))
            for candidate in candidates:
                if candidate.is_dir() and str(candidate) not in sys.path:
                    sys.path.insert(0, str(candidate))
            import arabic_reshaper
            from bidi.algorithm import get_display
        except Exception:
            return clean
    except Exception:
        return clean
    try:
        display = get_display(arabic_reshaper.reshape(clean), base_dir=base_dir or _base_direction(clean))
        return _stabilize_visual_order(display)
    except Exception:
        return clean


def _stabilize_visual_order(text: str) -> str:
    return text


_BIDI_CONTROL_TRANSLATION = str.maketrans(
    "",
    "",
    "\u061c\u200e\u200f\u202a\u202b\u202c\u202d\u202e\u2066\u2067\u2068\u2069",
)


def rtl_block_for_terminal(
    text: str,
    *,
    left_margin: int = 0,
    terminal_width: int | None = None,
    mode: str | None = None,
    base_dir: str | None = "R",
    right_align: bool = True,
) -> list[str]:
    """Wrap logical text, reorder each physical line, then align it for RTL."""
    clean = unicodedata.normalize("NFC", text.translate(_BIDI_CONTROL_TRANSLATION))
    if not clean.strip():
        return []

    margin_size = max(0, left_margin)
    columns = terminal_width if terminal_width is not None else shutil.get_terminal_size((100, 24)).columns
    # Writing a printable character into the final console cell can trigger an
    # automatic wrap before print() writes its newline. Keep that cell unused.
    content_width = max(1, max(2, columns) - margin_size - 1)
    margin = " " * margin_size
    result: list[str] = []

    paragraphs = re.split(r"\r\n?|\n", clean)
    for paragraph in paragraphs:
        normalized = " ".join(paragraph.split())
        if not normalized:
            if result:
                result.append("")
            continue
        for logical_line in _wrap_logical_line(normalized, content_width):
            display = rtl_for_terminal(logical_line, mode=mode, base_dir=base_dir)
            padding = 0
            if right_align and contains_rtl(logical_line):
                padding = max(0, content_width - terminal_cell_width(display))
            result.append(margin + (" " * padding) + display)
    return result


def _character_cell_width(character: str) -> int:
    if character in "\r\n" or unicodedata.combining(character):
        return 0
    if unicodedata.category(character) in {"Cf", "Mn", "Me"}:
        return 0
    return 2 if unicodedata.east_asian_width(character) in {"W", "F"} else 1


def terminal_cell_width(text: str) -> int:
    """Return terminal columns used by text, excluding ANSI styling."""
    return sum(_character_cell_width(character) for character in ANSI_RE.sub("", text))


def _clusters(text: str) -> list[str]:
    """Keep combining marks and joiner sequences attached while hard-wrapping."""
    clusters: list[str] = []
    for character in text:
        continuation = bool(clusters) and (
            unicodedata.combining(character)
            or unicodedata.category(character) in {"Mn", "Me"}
            or character in {"\u200c", "\u200d", "\ufe0e", "\ufe0f"}
            or clusters[-1].endswith("\u200d")
        )
        if continuation:
            clusters[-1] += character
        else:
            clusters.append(character)
    return clusters


def _split_long_token(token: str, width: int) -> list[str]:
    pieces: list[str] = []
    current = ""
    current_width = 0
    for cluster in _clusters(token):
        cluster_width = terminal_cell_width(cluster)
        if current and current_width + cluster_width > width:
            pieces.append(current)
            current = ""
            current_width = 0
        current += cluster
        current_width += cluster_width
    if current:
        pieces.append(current)
    return pieces or [token]


def _wrap_logical_line(text: str, width: int) -> list[str]:
    """Word-wrap in logical order and hard-wrap paths/other long tokens."""
    lines: list[str] = []
    current = ""
    for word in text.split():
        candidate = f"{current} {word}" if current else word
        if terminal_cell_width(candidate) <= width:
            current = candidate
            continue
        if current:
            lines.append(current)
            current = ""
        pieces = _split_long_token(word, width)
        lines.extend(pieces[:-1])
        current = pieces[-1]
    if current:
        lines.append(current)
    return lines
