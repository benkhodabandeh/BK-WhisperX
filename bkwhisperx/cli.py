from __future__ import annotations

import argparse
import getpass
import importlib
import json
import os
import re
import shlex
import tempfile
import webbrowser
from collections.abc import Iterable
from pathlib import Path

from . import __version__
from .bootstrap import PROFILE_LABELS, automatic_profile, health_check, install_runtime, read_state
from .config import (
    LANGUAGES,
    MODELS,
    PERFORMANCE_PRESETS,
    SUPPORTED_EXTENSIONS,
    TranscriptionConfig,
    collect_media,
)
from .console import (
    brand,
    configure_utf8,
    contains_rtl,
    error,
    success,
    warning,
)
from .diagnostics import clear_caches, collect_diagnostics, storage_summary
from .paths import defaults_file, temp_dir
from .runner import EVENT_PREFIX, new_worker_controller, run_worker
from .updates import check_for_update_cached

ART = r"""
██████╗ ██╗  ██╗    ██╗    ██╗██╗  ██╗██╗███████╗██████╗ ███████╗██████╗ ██╗  ██╗
██╔══██╗██║ ██╔╝    ██║    ██║██║  ██║██║██╔════╝██╔══██╗██╔════╝██╔══██╗╚██╗██╔╝
██████╔╝█████╔╝     ██║ █╗ ██║███████║██║███████╗██████╔╝█████╗  ██████╔╝ ╚███╔╝
██╔══██╗██╔═██╗     ██║███╗██║██╔══██║██║╚════██║██╔═══╝ ██╔══╝  ██╔══██╗ ██╔██╗
██████╔╝██║  ██╗    ╚███╔███╔╝██║  ██║██║███████║██║     ███████╗██║  ██║██╔╝ ██╗
╚═════╝ ╚═╝  ╚═╝     ╚══╝╚══╝ ╚═╝  ╚═╝╚═╝╚══════╝╚═╝     ╚══════╝╚═╝  ╚═╝╚═╝  ╚═╝
""".strip("\n")


def print_header() -> None:
    print(brand(ART))
    print(brand(f"BK WhisperX v{__version__}  •  " + t("local transcription") + "  •  CLI"))
    print("═" * 92)


def ask(prompt: str, default: str = "") -> str:
    suffix = f" [{default}]" if default else ""
    try:
        value = input(f"{prompt}{suffix}: ").strip()
    except KeyboardInterrupt:
        print("\n" + t("Cancelled."))
        raise SystemExit(130) from None
    return value or default


def confirm(prompt: str, default: bool = True) -> bool:
    marker = "Y/n" if default else "y/N"
    while True:
        value = ask(f"{prompt} ({marker})").lower()
        if not value:
            return default
        if value in {"y", "yes", "1", "true"}:
            return True
        if value in {"n", "no", "0", "false"}:
            return False
        print(t("Please answer y or n."))


def choose(title: str, choices: list[tuple[str, str]], default_index: int = 0) -> str:
    print(f"\n{title}")
    for index, (value, label) in enumerate(choices, start=1):
        marker = " ← recommended" if index - 1 == default_index else ""
        print(f"  {index:>2}. {value:<18} {label}{marker}")
    while True:
        raw = ask(t("Choose a number"), str(default_index + 1))
        if raw.isdigit() and 1 <= int(raw) <= len(choices):
            return choices[int(raw) - 1][0]
        print(t("Choose one of the listed numbers."))


_UI_LANG = "en"


def t(key: str) -> str:
    if _UI_LANG != "fa":
        return key
    val = _FA.get(key, key)
    if "{" in val:
        return val
    return val[::-1] if contains_rtl(val) else val


def _td(key: str, **kwargs: str) -> str:
    val = _FA.get(key, key) if _UI_LANG == "fa" else key
    if kwargs:
        val = val.format(**kwargs)
    return val[::-1] if _UI_LANG == "fa" and contains_rtl(val) else val


_FA: dict[str, str] = {
    "Main menu": "منوی اصلی",
    "Quick transcribe (Persian / Farsi)": "رونویسی سریع (فارسی)",
    "Quick transcribe (English)": "رونویسی سریع (انگلیسی)",
    "Quick transcribe (saved config)": "رونویسی سریع (تنظیمات ذخیره شده)",
    "Full wizard — choose every setting": "راهنمای کامل — انتخاب همه تنظیمات",
    "Check GPU, runtime, FFmpeg, disk, and caches": "بررسی GPU، اجرا، FFmpeg، دیسک و حافظهٔ پنهان",
    "Install, switch, or repair the managed runtime": "نصب، تغییر یا تعمیر محیط اجرایی",
    "Inspect or clear managed download/model caches": "بررسی یا پاک کردن حافظهٔ پنهان دانلود/مدل",
    "Close BK WhisperX": "بستن BK WhisperX",
    "Change UI language": "تغییر زبان رابط کاربری",
    "Choose UI language": "زبان رابط کاربری را انتخاب کنید",
    "English": "انگلیسی",
    "Persian / Farsi": "فارسی",
    "Input mode": "حالت ورودی",
    "One audio/video file": "یک فایل صوتی/تصویری",
    "Several selected files": "چند فایل انتخاب شده",
    "All supported files in a folder": "همه فایل‌های پشتیبانی شده در یک پوشه",
    "Paste or drag an audio/video file path": "مسیر فایل صوتی/تصویری را وارد کنید",
    "Paste paths separated by semicolons": "مسیرها را با نقطه‌ویرگول جدا کنید",
    "Paste or drag the folder path": "مسیر پوشه را وارد کنید",
    "Scan subfolders too?": "زیرپوشه‌ها هم اسکن شوند؟",
    "Choose the input again?": "دوباره ورودی را انتخاب می‌کنید؟",
    "Cancelled without starting transcription.": "بدون شروع رونویسی لغو شد.",
    "Output folder": "پوشه خروجی",
    "Processing device": "دستگاه پردازش",
    "Use CUDA when available, otherwise CPU": "استفاده از CUDA در صورت وجود، در غیر این صورت CPU",
    "Require NVIDIA CUDA GPU": "GPU NVIDIA CUDA الزامی",
    "CPU / compatibility mode": "CPU / حالت سازگاری",
    "Model selection": "انتخاب مدل",
    "Fastest, lowest accuracy": "سریع‌ترین، کمترین دقت",
    "Fast and lightweight": "سریع و سبک",
    "Balanced for CPU or low VRAM": "متوازن برای CPU یا VRAM کم",
    "Good accuracy, moderate resources": "دقت خوب، منابع متوسط",
    "High accuracy, proven model": "دقت بالا، مدل اثبات شده",
    "Highest general accuracy": "بالاترین دقت عمومی",
    "Fast distilled model — English only": "مدل تقطیر شده سریع — فقط انگلیسی",
    "Language": "زبان",
    "Language code": "کد زبان",
    "Task": "وظیفه",
    "Keep speech in the original language": "گفتار را به زبان اصلی نگه دار",
    "Translate speech to English": "گفتار را به انگلیسی ترجمه کن",
    "Export formats: txt, srt, vtt, json, or all": "فرمت‌های خروجی: txt، srt، vtt، json یا all",
    "Formats separated by commas": "فرمت‌ها با کاما جدا شوند",
    "Enable accurate word alignment/timestamps?": "تنظیم دقیق کلمه/زمان‌بندی فعال شود؟",
    "Also create combined_transcription.txt?": "فایل combined_transcription.txt نیز ایجاد شود؟",
    "Overwrite existing output files?": "فایل‌های خروجی موجود بازنویسی شوند؟",
    "Show live segment transcription preview?": "پیش‌نمایش زنده رونویسی نشان داده شود؟",
    "Performance preset": "تنظیمات عملکرد",
    "Lower memory use and maximum compatibility": "مصرف حافظه کمتر و حداکثر سازگاری",
    "Recommended speed and memory balance": "سرعت و تعادل حافظه توصیه شده",
    "Larger GPU batches with automatic OOM fallback": "دسته‌های GPU بزرگتر با بازگشت خودکار OOM",
    "Optional context prompt (names, subject, spelling)": "راهنمای زمینه اختیاری (نام‌ها، موضوع، املاء)",
    "Optional hotwords / glossary terms": "کلمات کلیدی اختیاری / واژه‌نامه",
    "TXT layout": "نحوه چینش TXT",
    "Continuous readable paragraphs": "پاراگراف‌های پیوسته خوانا",
    "One decoded segment per line": "یک بخش رمزگشایی شده در هر خط",
    "Enable speaker labels?": "برچسب‌های گوینده فعال شود؟",
    "Subtitle maximum characters per line": "حداکثر کاراکتر هر خط زیرنویس",
    "Subtitle maximum lines per cue": "حداکثر خطوط هر زیرنویس",
    "Minimum speakers (blank if unknown)": "حداقل گویندگان (در صورت نامشخص خالی بگذارید)",
    "Maximum speakers (blank if unknown)": "حداکثر گویندگان (در صورت نامشخص خالی بگذارید)",
    "Save these settings as defaults for quick transcribe?": "این تنظیمات به عنوان پیش‌فرض رونویسی سریع ذخیره شود؟",
    "Start transcription with these settings?": "رونویسی با این تنظیمات شروع شود؟",
    "This wizard prepares a reliable local transcription job.": "این راهنما یک job رونویسی محلی مطمئن آماده می‌کند.",
    "Ready to run": "آماده اجرا",
    "Files:": "فایل‌ها:",
    "Files:": "فایل‌ها:",
    "Output:": "خروجی:",
    "Model:": "مدل:",
    "Language:": "زبان:",
    "Device:": "دستگاه:",
    "Exports:": "خروجی‌ها:",
    "Live text:": "متن زنده:",
    "enabled": "فعال",
    "disabled": "غیرفعال",
    "Perform a full reinstall instead of a normal repair?": "نصب کامل مجدد به جای تعمیر معمولی انجام شود؟",
    "Runtime profile": "پروفایل اجرایی",
    "Cache to clear": "حافظه پنهان برای پاک کردن",
    "Package download cache; installed runtime is kept": "حافظه پنهان دانلود بسته؛ محیط اجرایی نصب شده نگه داشته می‌شود",
    "App-managed model cache; models will download again": "حافظه پنهان مدل مدیریت شده توسط برنامه؛ مدل‌ها دوباره دانلود می‌شوند",
    "Hugging Face model cache": "حافظه پنهان مدل Hugging Face",
    "Hugging Face token (input hidden)": "توکن Hugging Face (ورودی مخفی)",
    "Torch alignment/model cache": "حافظه پنهان alignment/مدل Torch",
    "Activity and setup logs": "لاگ‌های فعالیت و راه‌اندازی",
    "Permanently clear the {name} cache?": "حافظه پنهان {name} برای همیشه پاک شود؟",
    "Choose a number": "یک عدد انتخاب کنید",
    "Please answer y or n.": "لطفاً y یا n پاسخ دهید.",
    "Found {count} supported file(s).": "{count} فایل پشتیبانی شده پیدا شد.",
    "Model: {model}  |  Device: {device}": "مدل: {model}  |  دستگاه: {device}",
    "Update available: v{latest} (current v{current})": "به‌روزرسانی موجود: v{latest} (فعلی v{current})",
    "Open the release page?": "صفحه انتشار باز شود؟",
    "Cancelled.": "لغو شد.",
    "local transcription": "رونویسی محلی",
    "Choose one of the listed numbers.": "یکی از شماره‌های فهرست شده را انتخاب کنید.",
    "Runtime setup failed:": "راه‌اندازی محیط اجرایی ناموفق:",
    "Runtime ready:": "محیط اجرایی آماده:",
    "The previous runtime was replaced; downloaded models were kept.": "محیط اجرایی قبلی جایگزین شد؛ مدل‌های دانلود شده حفظ شدند.",
    "No supported audio or video files were found.": "هیچ فایل صوتی یا تصویری پشتیبانی شده‌ای یافت نشد.",
    "No path was entered.": "هیچ مسیری وارد نشد.",
    "Path interpreted as:": "مسیر تفسیر شده:",
    "That path does not exist. Check the spelling or drag the folder into this window again.": "این مسیر وجود ندارد. املاء را بررسی کنید یا پوشه را دوباره به این پنجره بکشید.",
    "The selected path is a file, but folder mode was selected.": "مسیر انتخاب شده یک فایل است، اما حالت پوشه انتخاب شده.",
    "The selected path is a folder. Choose folder mode to scan it.": "مسیر انتخاب شده یک پوشه است. حالت پوشه را برای اسکن انتخاب کنید.",
    "The selected file has an unsupported extension:": "فایل انتخاب شده پسوند پشتیبانی نشده دارد:",
    "The folder could not be scanned completely:": "پوشه نمی‌تواند به طور کامل اسکن شود:",
    "Files inspected:": "فایل‌های بررسی شده:",
    "Extensions found:": "پسوندهای یافت شده:",
    "Supported extensions:": "پسوندهای پشتیبانی شده:",
    "Folder mode accepts one folder; only the first path will be scanned.": "حالت پوشه فقط یک پوشه را می‌پذیرد؛ فقط اولین مسیر اسکن می‌شود.",
    "The folder scan failed:": "اسکن پوشه ناموفق:",
    "Runtime:": "محیط اجرایی:",
    "Runtime setup required:": "راه‌اندازی محیط اجرایی لازم است:",
    "Invalid job configuration:": "پیکربندی job نامعتبر:",
    "Operation failed:": "عملیات ناموفق:",
    "Fatal error:": "خطای بحرانی:",
    "Transcription stopped safely.": "رونویسی با احتیاط متوقف شد.",
    "Transcription interrupted.": "رونویسی قطع شد.",
    "Stopping after the current decode chunk and releasing GPU memory…": "در حال توقف پس از قطعه رمزگشایی فعلی و آزاد کردن حافظه GPU…",
    "Worker shutdown completed cleanly.": "خاموشی کارگر به طور کامل انجام شد.",
    "CUDA was requested, but the installed NVIDIA driver is not compatible with the supported CUDA 12.6/12.8 runtimes. Update the NVIDIA driver, choose CPU, or select a compatible runtime explicitly from Runtime setup.": "CUDA درخواست شد، اما درایور NVIDIA نصب شده با محیط‌های اجرایی CUDA 12.6/12.8 پشتیبانی شده سازگار نیست. درایور NVIDIA را به‌روزرسانی کنید، CPU را انتخاب کنید، یا یک محیط اجرایی سازگار را به طور صریح از راه‌اندازی محیط اجرایی انتخاب کنید.",
    "Welcome! Choose your preferred UI language:": "خوش آمدید! زبان رابط کاربری ترجیحی خود را انتخاب کنید:",
    "Working…": "در حال کار…",
    "Checking runtime…": "بررسی محیط اجرایی…",
    "Loading model…": "بارگذاری مدل…",
    "Language changed.": "زبان تغییر کرد.",
    "Returning to main menu…": "…بازگشت به منوی اصلی",
    "Cleared": "پاک شد",
    "Clear selected managed caches?": "حافظه‌های پنهان مدیریت شده انتخاب شده پاک شوند؟",
}

_SAVED_KEYS = {
    "model", "device", "task", "language", "formats", "timestamps", "combine", "overwrite",
    "live_preview", "performance_preset", "txt_layout", "subtitle_max_chars",
    "subtitle_max_lines", "initial_prompt", "hotwords",
    "min_speakers", "max_speakers", "chunk_size", "beam_size", "best_of",
    "batch_size", "vad_method", "vad_onset", "vad_offset", "compute_type", "quiet",
}


def _save_defaults(cfg: TranscriptionConfig) -> None:
    data = {key: getattr(cfg, key) for key in _SAVED_KEYS}
    path = defaults_file()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def _load_defaults() -> dict[str, Any]:
    try:
        data = json.loads(defaults_file().read_text(encoding="utf-8"))
        return {k: v for k, v in data.items() if k in _SAVED_KEYS and v is not None}
    except (OSError, json.JSONDecodeError):
        return {}


def _load_ui_lang() -> str:
    try:
        data = json.loads(defaults_file().read_text(encoding="utf-8"))
        return "fa" if data.get("ui_language") == "fa" else "en"
    except (OSError, json.JSONDecodeError):
        return "en"


def _save_ui_lang(lang: str) -> None:
    path = defaults_file()
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        data = {}
    data["ui_language"] = lang
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def _clean_path_token(value: str) -> str:
    """Normalize a path pasted or dragged from Windows Explorer/CMD."""
    value = value.strip().lstrip("\ufeff")
    while len(value) >= 2 and value[0] == value[-1] and value[0] in {'"', "'"}:
        value = value[1:-1].strip()
    return os.path.expandvars(value)


def _split_paths(raw: str) -> list[Path]:
    """Parse one or more paths without breaking ordinary Windows paths at spaces."""
    raw = raw.replace("\r", "").strip()
    if not raw:
        return []

    if ";" in raw or "\n" in raw:
        pieces = raw.replace("\n", ";").split(";")
    elif not any(quote in raw for quote in ('"', "'")):
        # A normal path may contain spaces. Multiple paths in the wizard are
        # explicitly separated with semicolons, so preserve this value whole.
        pieces = [raw]
    else:
        try:
            pieces = shlex.split(raw, posix=os.name != "nt")
        except ValueError:
            pieces = [raw]

    cleaned = [_clean_path_token(piece) for piece in pieces]
    return [Path(piece).expanduser() for piece in cleaned if piece]


def _supported_formats_text() -> str:
    return ", ".join(sorted(SUPPORTED_EXTENSIONS))


def _print_input_scan_failure(
    *,
    input_mode: str,
    candidates: list[Path],
    recursive: bool,
) -> None:
    print(error(t("No supported audio or video files were found.")))

    if not candidates:
        print(t("No path was entered."))
    else:
        target = candidates[0]
        print(t("Path interpreted as:") + f" {target}")
        if not target.exists():
            print(t("That path does not exist. Check the spelling or drag the folder into this window again."))
        elif input_mode == "folder" and not target.is_dir():
            print(t("The selected path is a file, but folder mode was selected."))
        elif input_mode != "folder" and target.is_dir():
            print(t("The selected path is a folder. Choose folder mode to scan it."))
        elif target.is_file():
            suffix = target.suffix.lower() or "(no extension)"
            print(t("The selected file has an unsupported extension:") + f" {suffix}")
        elif target.is_dir():
            iterator = target.rglob("*") if recursive else target.glob("*")
            discovered: set[str] = set()
            file_count = 0
            try:
                for item in iterator:
                    if not item.is_file():
                        continue
                    file_count += 1
                    if len(discovered) < 20:
                        discovered.add(item.suffix.lower() or "(no extension)")
            except OSError as exc:
                print(f"{t('The folder could not be scanned completely:')} {exc}")
            print(f"{t('Files inspected:')} {file_count}")
            if discovered:
                print(f"{t('Extensions found:')} {', '.join(sorted(discovered))}")

    print(t("Supported extensions:") + f" {_supported_formats_text()}")


def _select_input_files() -> list[Path]:
    """Keep the wizard alive until valid media is selected or the user cancels."""
    while True:
        input_mode = choose(
            t("Input mode"),
            [
                ("single", t("One audio/video file")),
                ("multiple", t("Several selected files")),
                ("folder", t("All supported files in a folder")),
            ],
            0,
        )

        candidates: list[Path] = []
        files: list[Path] = []
        recursive = False

        if input_mode == "single":
            candidates = _split_paths(ask(t("Paste or drag an audio/video file path")))[:1]
            files = candidates
        elif input_mode == "multiple":
            candidates = _split_paths(ask(t("Paste paths separated by semicolons")))
            files = candidates
        else:
            candidates = _split_paths(ask(t("Paste or drag the folder path")))
            if len(candidates) > 1:
                print(warning(t("Folder mode accepts one folder; only the first path will be scanned.")))
            if candidates:
                recursive = confirm(t("Scan subfolders too?"), False)
                try:
                    files = collect_media(candidates[0], recursive)
                except OSError as exc:
                    print(error(f"{t('The folder scan failed:')} {exc}"))
                    files = []

        files = list(dict.fromkeys(path.resolve() for path in files if path.is_file()))
        if files:
            return files

        _print_input_scan_failure(
            input_mode=input_mode,
            candidates=candidates,
            recursive=recursive,
        )
        if not confirm(t("Choose the input again?"), True):
            print(t("Cancelled without starting transcription."))
            raise SystemExit(1)


def setup_runtime_interactive(force: bool = False, preferred: str = "auto") -> str:
    state = read_state()
    profile = choose(
        t("Runtime profile"),
        [
            ("auto", PROFILE_LABELS["auto"]),
            ("cuda128", PROFILE_LABELS["cuda128"]),
            ("cuda126", PROFILE_LABELS["cuda126"]),
            ("cpu", PROFILE_LABELS["cpu"]),
        ],
        max(0, ["auto", "cuda128", "cuda126", "cpu"].index(preferred)) if preferred in PROFILE_LABELS else 0,
    )
    print()
    try:
        install_runtime(profile, force=force, app_version=__version__)
    except Exception as exc:
        print(error(f"{t('Runtime setup failed:')} {exc}"))
        raise SystemExit(2) from None
    resolved = read_state().profile if read_state() else profile
    print(success(f"{t('Runtime ready:')} {PROFILE_LABELS.get(resolved, resolved)}"))
    if state and state.profile != resolved:
        print(t("The previous runtime was replaced; downloaded models were kept."))
    return resolved


def _quick_transcribe(language: str | None) -> tuple[TranscriptionConfig, str]:
    files = _select_input_files()
    print(_td("Found {count} supported file(s).", count=len(files)))
    default_output = files[0].parent / "transcriptions"
    output_dir = Path(ask(t("Output folder"), str(default_output))).expanduser().resolve()

    merged = dict(_load_defaults())
    lang = language or merged.get("language", "auto")
    merged.update(audio_files=[str(path) for path in files], output_dir=str(output_dir), language=lang)
    config = TranscriptionConfig(**merged)
    config.validate()

    print("\n" + "\u2550" * 92)
    print(brand(t("Ready to run")))
    print(f"{t('Files:')}    {len(files)}")
    print(f"{t('Output:')}   {output_dir}")
    print(f"{t('Model:')}    {config.model}  |  {t('Device:')} {config.device}")
    print(f"{t('Language:')} {lang}  |  {t('Exports:')} {', '.join(config.formats)}")
    print(f"{t('Live text:')} {t('enabled') if config.live_preview else t('disabled')}  |  overwrite={config.overwrite}")
    print("\u2550" * 92)
    if not confirm(t("Start transcription with these settings?"), True):
        raise SystemExit(0)
    profile = ensure_runtime_interactive(config.device)
    return config, profile


def ensure_runtime_interactive(device: str = "auto") -> str:
    state = read_state()
    profile = state.profile if state else "auto"
    print(brand(t("Checking runtime…")))
    healthy, info, message = health_check(profile)
    if healthy:
        detail = f"torch {info.torch_version}"
        if info.cuda_available:
            detail += f" • CUDA {info.torch_cuda_version} • {info.cuda_device_name}"
        else:
            detail += " • CPU"
        print(success(f"{t('Runtime:')} {detail}"))
        return profile
    print(warning(f"{t('Runtime setup required:')} {message}"))
    preferred = automatic_profile() if device == "cuda" else "auto"
    return setup_runtime_interactive(force=False, preferred=preferred)


def wizard() -> tuple[TranscriptionConfig, str]:
    print(t("This wizard prepares a reliable local transcription job."))
    files = _select_input_files()
    print(_td("Found {count} supported file(s).", count=len(files)))

    default_output = files[0].parent / "transcriptions"
    output_dir = Path(ask(t("Output folder"), str(default_output))).expanduser().resolve()

    device = choose(
        t("Processing device"),
        [
            ("auto", t("Use CUDA when available, otherwise CPU")),
            ("cuda", t("Require NVIDIA CUDA GPU")),
            ("cpu", t("CPU / compatibility mode")),
        ],
        0,
    )
    model = choose(
        t("Model selection"),
        [
            ("tiny", t("Fastest, lowest accuracy")),
            ("base", t("Fast and lightweight")),
            ("small", t("Balanced for CPU or low VRAM")),
            ("medium", t("Good accuracy, moderate resources")),
            ("large-v2", t("High accuracy, proven model")),
            ("large-v3", t("Highest general accuracy")),
            ("distil-large-v3", t("Fast distilled model — English only")),
        ],
        5,
    )
    language = choose(t("Language"), LANGUAGES, 0)
    if language == "custom":
        language = ask(t("Language code"), "fa").lower()
    task = choose(
        t("Task"),
        [("transcribe", t("Keep speech in the original language")), ("translate", t("Translate speech to English"))],
        0,
    )

    formats: list[str] = []
    print("\n" + t("Export formats: txt, srt, vtt, json, or all"))
    raw_formats = ask(t("Formats separated by commas"), "txt").lower()
    for token in raw_formats.replace(";", ",").split(","):
        token = token.strip()
        if token == "all":
            formats = ["txt", "srt", "vtt", "json"]
            break
        if token in {"txt", "srt", "vtt", "json"} and token not in formats:
            formats.append(token)
    formats = formats or ["txt"]
    timestamps = any(fmt != "txt" for fmt in formats) or confirm(t("Enable accurate word alignment/timestamps?"), False)
    combine = confirm(t("Also create combined_transcription.txt?"), True)
    overwrite = confirm(t("Overwrite existing output files?"), False)
    live_preview = confirm(t("Show live segment transcription preview?"), False)

    performance_preset = choose(
        t("Performance preset"),
        [
            ("safe", t("Lower memory use and maximum compatibility")),
            ("balanced", t("Recommended speed and memory balance")),
            ("maximum", t("Larger GPU batches with automatic OOM fallback")),
        ],
        1,
    )
    initial_prompt = ask(t("Optional context prompt (names, subject, spelling)"))
    hotwords = ask(t("Optional hotwords / glossary terms"))
    txt_layout = choose(
        t("TXT layout"),
        [("paragraph", t("Continuous readable paragraphs")), ("segments", t("One decoded segment per line"))],
        0,
    )
    subtitle_max_chars = 42
    subtitle_max_lines = 2
    if any(fmt in {"srt", "vtt"} for fmt in formats):
        raw_chars = ask(t("Subtitle maximum characters per line"), "42")
        raw_lines = ask(t("Subtitle maximum lines per cue"), "2")
        subtitle_max_chars = int(raw_chars) if raw_chars.isdigit() else 42
        subtitle_max_lines = int(raw_lines) if raw_lines.isdigit() else 2

    diarize = confirm(t("Enable speaker labels?"), False)
    hf_token = None
    min_speakers = max_speakers = None
    if diarize:
        default_token = os.environ.get("HF_TOKEN", "")
        prompt = t("Hugging Face token (input hidden)")
        hf_token = getpass.getpass(f"{prompt}: ").strip() or default_token or None
        raw_min = ask(t("Minimum speakers (blank if unknown)"))
        raw_max = ask(t("Maximum speakers (blank if unknown)"))
        min_speakers = int(raw_min) if raw_min.isdigit() else None
        max_speakers = int(raw_max) if raw_max.isdigit() else None

    config = TranscriptionConfig(
        audio_files=[str(path) for path in files],
        output_dir=str(output_dir),
        model=model,
        device=device,
        language=language,
        task=task,
        formats=formats,
        timestamps=timestamps,
        diarize=diarize,
        hf_token=hf_token,
        min_speakers=min_speakers,
        max_speakers=max_speakers,
        combine=combine,
        overwrite=overwrite,
        live_preview=live_preview,
        performance_preset=performance_preset,
        initial_prompt=initial_prompt,
        hotwords=hotwords,
        txt_layout=txt_layout,
        subtitle_max_chars=subtitle_max_chars,
        subtitle_max_lines=subtitle_max_lines,
    )
    config.validate()

    print("\n" + "═" * 92)
    print(brand(t("Ready to run")))
    print(f"{t('Files:')}      {len(files)}")
    print(f"{t('Output:')}     {output_dir}")
    print(f"{t('Model:')}      {model}")
    print(f"{t('Device:')}     {device}")
    print(f"{t('Language:')}   {language} | task={task}")
    print(f"{t('Exports:')}    {', '.join(formats)} | timestamps={config.timestamps} | overwrite={config.overwrite}")
    print(f"{t('Live text:')}  {t('enabled') if config.live_preview else t('disabled')}")
    print("═" * 92)
    if confirm(t("Save these settings as defaults for quick transcribe?"), False):
        _save_defaults(config)
    if not confirm(t("Start transcription with these settings?"), True):
        raise SystemExit(0)
    profile = ensure_runtime_interactive(device)
    return config, profile


def _config_from_args(args: argparse.Namespace) -> TranscriptionConfig | None:
    files: list[Path] = []
    for value in args.input or []:
        path = Path(value).expanduser()
        if path.is_file():
            files.append(path.resolve())
    if args.folder:
        files.extend(collect_media(Path(args.folder).expanduser(), args.recursive))
    unique = list(dict.fromkeys(files))
    if not unique:
        return None
    output = Path(args.output).expanduser().resolve() if args.output else unique[0].parent / "transcriptions"
    formats = args.formats or ["txt"]
    if "all" in formats:
        formats = ["txt", "srt", "vtt", "json"]
    return TranscriptionConfig(
        audio_files=[str(path) for path in unique],
        output_dir=str(output),
        model=args.model,
        device=args.device,
        compute_type=args.compute_type,
        batch_size=args.batch_size,
        language=args.language,
        task=args.task,
        formats=formats,
        timestamps=args.timestamps,
        combine=not args.no_combine,
        overwrite=args.overwrite,
        live_preview=args.live_preview,
        diarize=args.diarize,
        hf_token=args.hf_token or os.environ.get("HF_TOKEN"),
        min_speakers=args.min_speakers,
        max_speakers=args.max_speakers,
        performance_preset=args.preset,
        initial_prompt=args.initial_prompt,
        hotwords=args.hotwords,
        txt_layout=args.txt_layout,
        subtitle_max_chars=args.subtitle_max_chars,
        subtitle_max_lines=args.subtitle_max_lines,
    )


def _run_worker_mode(config_path: Path, machine_events: bool) -> int:
    # The ML engine is imported only inside the managed worker runtime. Keeping
    # this import dynamic prevents PyInstaller from bundling Torch into the launcher.
    engine_module = importlib.import_module(".".join(("bkwhisperx", "engine")))
    run_transcription = engine_module.run_transcription

    config = TranscriptionConfig.load(config_path)

    def event(kind: str, payload: dict) -> None:
        if machine_events:
            print(EVENT_PREFIX + json.dumps({"type": kind, **payload}, ensure_ascii=False), flush=True)

    cancelled_error = engine_module.TranscriptionCancelled
    try:
        run_transcription(config, log=lambda line: print(line, flush=True), event=event)
        return 0
    except cancelled_error:
        print(warning(t("Transcription stopped safely.")), flush=True)
        return 130
    except KeyboardInterrupt:
        print(warning(t("Transcription interrupted.")), flush=True)
        return 130
    except Exception as exc:
        print(error(f"{t('Fatal error:')} {type(exc).__name__}: {exc}"), flush=True)
        return 1


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="BK WhisperX local transcription")
    parser.add_argument("--version", action="version", version=__version__)
    parser.add_argument("--setup", choices=list(PROFILE_LABELS), help="Install or switch the runtime profile")
    parser.add_argument("--repair", action="store_true", help="Force runtime reinstallation/repair")
    parser.add_argument("--diagnostics", action="store_true", help="Print a system and runtime diagnostic report")
    parser.add_argument(
        "--clear-cache",
        action="append",
        choices=["downloads", "models", "huggingface", "torch_models", "logs"],
        help="Clear a selected managed cache; repeat to clear more than one",
    )
    parser.add_argument("--worker-config", type=Path, help=argparse.SUPPRESS)
    parser.add_argument("--machine-events", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("-i", "--input", action="append", help="Input media file; repeat for multiple files")
    parser.add_argument("--folder", help="Transcribe supported files in a folder")
    parser.add_argument("--recursive", action="store_true")
    parser.add_argument("-o", "--output", help="Output directory")
    parser.add_argument("--model", choices=MODELS, default="large-v3")
    parser.add_argument("--language", default="auto", help="Language code such as fa/en, or auto")
    parser.add_argument("--task", choices=["transcribe", "translate"], default="transcribe")
    parser.add_argument("--device", choices=["auto", "cuda", "cpu"], default="auto")
    parser.add_argument("--compute-type", choices=["auto", "int8", "float16", "float32"], default="auto")
    parser.add_argument("--batch-size", type=int, default=0, help="0 selects automatically")
    parser.add_argument("--preset", choices=PERFORMANCE_PRESETS, default="balanced")
    parser.add_argument("--initial-prompt", default="", help="Context prompt for names, spelling, or subject matter")
    parser.add_argument("--hotwords", default="", help="Comma-separated glossary or hotword terms")
    parser.add_argument("--format", dest="formats", action="append", choices=["txt", "srt", "vtt", "json", "all"])
    parser.add_argument("--timestamps", action="store_true")
    parser.add_argument("--overwrite", action="store_true")
    parser.add_argument(
        "--live-preview", action="store_true", help="Show decoded speech chunks as a live segment preview"
    )
    parser.add_argument("--no-combine", action="store_true")
    parser.add_argument("--diarize", action="store_true")
    parser.add_argument("--hf-token")
    parser.add_argument("--min-speakers", type=int)
    parser.add_argument("--max-speakers", type=int)
    parser.add_argument("--txt-layout", choices=["paragraph", "segments"], default="paragraph")
    parser.add_argument("--subtitle-max-chars", type=int, default=42)
    parser.add_argument("--subtitle-max-lines", type=int, default=2)
    parser.add_argument("--rtl-mode", choices=["auto", "visual", "logical"], default="auto")
    parser.add_argument("--no-update-check", action="store_true")
    return parser


def _human_bytes(value: int) -> str:
    size = float(value)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if size < 1024 or unit == "TB":
            return f"{size:.1f} {unit}" if unit != "B" else f"{size:.0f} {unit}"
        size /= 1024
    return f"{size:.1f} TB"


def print_diagnostics(output_dir: Path | None = None) -> None:
    print(brand("\nSystem diagnostics"))
    report = collect_diagnostics(output_dir)
    print(f"Platform:       {report.platform}")
    print(f"Runtime:        {report.profile} • {'healthy' if report.runtime_healthy else 'needs attention'}")
    print(f"Runtime detail: {report.runtime_message}")
    print(f"GPU:            {report.gpu_name or 'not detected'}")
    print(f"Driver:         {report.driver_version or 'not detected'}")
    print(f"PyTorch:        {report.torch_version or 'not installed'}")
    print(f"CUDA:           {report.torch_cuda_version or 'none'} • available={report.cuda_available}")
    print(f"FFmpeg:         {report.ffmpeg_path or 'not available'}")
    print(f"Data folder:    {report.app_home}")
    print(f"Disk free:      {_human_bytes(report.disk_free_bytes)}")
    print(f"Output:         {report.output_message}")
    print("Storage:")
    for name, size in report.cache_bytes.items():
        print(f"  {name:<14} {_human_bytes(size)}")


def interactive_menu() -> str:
    while True:
        action = choose(
            t("Main menu"),
            [
                ("fa", t("Quick transcribe (Persian / Farsi)")),
                ("en", t("Quick transcribe (English)")),
                ("saved", t("Quick transcribe (saved config)")),
                ("wizard", t("Full wizard — choose every setting")),
                ("diagnostics", t("Check GPU, runtime, FFmpeg, disk, and caches")),
                ("runtime", t("Install, switch, or repair the managed runtime")),
                ("storage", t("Inspect or clear managed download/model caches")),
                ("lang", t("Change UI language")),
                ("exit", t("Close BK WhisperX")),
            ],
            0,
        )
        if action in ("fa", "en", "saved", "wizard"):
            return action
        if action == "diagnostics":
            print_diagnostics()
        elif action == "runtime":
            setup_runtime_interactive(force=confirm(t("Perform a full reinstall instead of a normal repair?"), False))
        elif action == "lang":
            lang = choose(
                t("Choose UI language"),
                [("en", t("English")), ("fa", t("Persian / Farsi"))],
                0 if _UI_LANG == "en" else 1,
            )
            _save_ui_lang(lang)
            globals()["_UI_LANG"] = _load_ui_lang()
            print(success(t("Language changed.")))
        elif action == "storage":
            summary = storage_summary(["downloads", "models", "huggingface", "torch_models", "logs"])
            for name, size in summary.items():
                print(f"  {name:<14} {_human_bytes(size)}")
            if confirm(t("Clear selected managed caches?"), False):
                selected = choose(
                    t("Cache to clear"),
                    [
                        ("downloads", t("Package download cache; installed runtime is kept")),
                        ("models", t("App-managed model cache; models will download again")),
                        ("huggingface", t("Hugging Face model cache")),
                        ("torch_models", t("Torch alignment/model cache")),
                        ("logs", t("Activity and setup logs")),
                    ],
                    0,
                )
                if confirm(_td("Permanently clear the {name} cache?", name=selected), False):
                    cleared = clear_caches([selected])
                    print(success(f"{t('Cleared')} {_human_bytes(cleared.get(selected, 0))}."))
        else:
            return action


def _compatible_runtime_profile(device: str, profile: str) -> str:
    state = read_state()
    resolved = state.profile if profile == "auto" and state else automatic_profile() if profile == "auto" else profile
    if device != "cuda" or resolved != "cpu":
        return profile
    automatic = automatic_profile()
    if automatic == "cpu":
        raise RuntimeError(t(
            "CUDA was requested, but the installed NVIDIA driver is not compatible with the supported "
            "CUDA 12.6/12.8 runtimes. Update the NVIDIA driver, choose CPU, or select a compatible "
            "runtime explicitly from Runtime setup."
        ))
    return automatic


def _live_preview_text(payload: dict) -> str:
    text_value = str(payload.get("text") or "").strip()
    if text_value:
        return text_value

    parts: list[str] = []
    for item in payload.get("words") or []:
        if isinstance(item, dict):
            value = str(item.get("word") or "")
            separator = str(item.get("separator") or "")
            if value:
                parts.append(value + separator)
            continue
        parts.append(str(item))
    return "".join(parts).strip()


def _live_preview_lines(payload: dict) -> list[str]:
    text_value = _live_preview_text(payload)
    if not text_value:
        return []
    if contains_rtl(text_value):
        return [text_value[::-1]]
    return [text_value]


def main(argv: Iterable[str] | None = None) -> int:
    configure_utf8()
    args = build_parser().parse_args(list(argv) if argv is not None else None)
    os.environ["BKWHISPERX_RTL_MODE"] = args.rtl_mode
    globals()["_UI_LANG"] = _load_ui_lang()
    if args.worker_config:
        return _run_worker_mode(args.worker_config, args.machine_events)
    if not defaults_file().exists() and os.isatty(0):
        print(brand("Welcome! Choose your preferred UI language:"))
        lang = choose(
            t("Choose UI language"),
            [("en", t("English")), ("fa", t("Persian / Farsi"))],
            0,
        )
        _save_ui_lang(lang)
        globals()["_UI_LANG"] = _load_ui_lang()
    print_header()
    try:
        if args.setup:
            install_runtime(args.setup, force=args.repair, app_version=__version__)
            return 0
        if args.repair:
            state = read_state()
            profile = state.profile if state else "auto"
            install_runtime(profile, force=True, app_version=__version__)
            return 0
        if args.diagnostics:
            print_diagnostics(Path(args.output).expanduser() if args.output else None)
            return 0
        if args.clear_cache:
            cleared = clear_caches(args.clear_cache)
            for name, size in cleared.items():
                print(success(f"Cleared {name}: {_human_bytes(size)}"))
            return 0
    except Exception as exc:
        print(error(f"{t('Operation failed:')} {type(exc).__name__}: {exc}"))
        return 2

    config = _config_from_args(args)
    if config is None:
        while True:
            action = interactive_menu()
            if action == "exit":
                return 0
            try:
                if action == "saved":
                    config, profile = _quick_transcribe(None)
                elif action in ("fa", "en"):
                    config, profile = _quick_transcribe(action)
                else:
                    config, profile = wizard()
            except SystemExit:
                continue
            try:
                exit_code = _run_transcription(config, profile, args)
            except KeyboardInterrupt:
                print(brand(t("Returning to main menu…")))
                continue
            if exit_code != 0:
                print(warning(f"Transcription finished with errors (exit code {exit_code})."))
    else:
        state = read_state()
        profile = state.profile if state else "auto"
        try:
            config.validate()
        except Exception as exc:
            print(error(f"{t('Invalid job configuration:')} {exc}"))
            return 2
        exit_code = _run_transcription(config, profile, args)
        return exit_code


def _run_transcription(config: TranscriptionConfig, profile: str, args: argparse.Namespace) -> int:
    try:
        profile = _compatible_runtime_profile(config.device, profile)
    except RuntimeError as exc:
        print(error(str(exc)))
        return 2

    if not args.no_update_check:
        update = check_for_update_cached(__version__)
        if update.available:
            print(warning(_td("Update available: v{latest} (current v{current})", latest=update.latest_version, current=__version__)))
            if os.isatty(0) and confirm(t("Open the release page?"), False):
                webbrowser.open(update.release_url)

    temp_dir().mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(
        mode="w", suffix=".json", prefix="job-", dir=temp_dir(), delete=False, encoding="utf-8"
    ) as handle:
        config_path = Path(handle.name)
    config.save(config_path, include_secrets=False)
    controller = new_worker_controller()

    file_line_pattern = re.compile(r"^\[(\d+)/(\d+)\]\s+(.+)$")
    completed_line_pattern = re.compile(r"^(Completed in [^:]+:)(.+)$")

    def cli_line(line: str) -> None:
        match = file_line_pattern.match(line)
        if match and contains_rtl(match.group(3)):
            print(brand(f"[{match.group(1)}/{match.group(2)}]"), flush=True)
            print(match.group(3)[::-1], flush=True)
            return
        completed = completed_line_pattern.match(line)
        if completed and contains_rtl(completed.group(2)):
            print(success(completed.group(1)), flush=True)
            print("  " + completed.group(2).strip()[::-1], flush=True)
            return
        if contains_rtl(line):
            print(line[::-1], flush=True)
            return
        print(line, flush=True)

    def cli_event(kind: str, payload: dict) -> None:
        if kind == "live_segment":
            preview_lines = _live_preview_lines(payload)
            if not preview_lines:
                return
            print(brand("LIVE"), flush=True)
            for display_line in preview_lines:
                print(display_line, flush=True)
        elif kind == "run_cancelling":
            print(warning(t("Stopping after the current decode chunk and releasing GPU memory…")), flush=True)
        elif kind == "run_cancelled":
            print(success(t("Worker shutdown completed cleanly.")), flush=True)
        elif kind == "run_phase":
            print(brand(payload.get("label") or payload.get("phase") or t("Working…")), flush=True)
        elif kind == "file_phase":
            print(f"  {payload.get('label', payload.get('phase', t('Working…')))}", flush=True)

    print(brand(t("Loading model…")))
    try:
        result = run_worker(
            config_path,
            profile=profile,
            app_version=__version__,
            hf_token=config.hf_token,
            line_callback=cli_line,
            event_callback=cli_event,
            controller=controller,
        )
        return result
    finally:
        config_path.unlink(missing_ok=True)


if __name__ == "__main__":
    raise SystemExit(main())
