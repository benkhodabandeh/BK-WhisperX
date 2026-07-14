from __future__ import annotations

import os
import sys
from pathlib import Path

APP_DIR_NAME = "BK WhisperX"


def app_home() -> Path:
    override = os.environ.get("BKWHISPERX_HOME")
    if override:
        return Path(override).expanduser().resolve()
    if sys.platform == "win32":
        base = Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData" / "Local"))
        return base / APP_DIR_NAME
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support" / APP_DIR_NAME
    return Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local" / "share")) / "bk-whisperx"


def source_root() -> Path:
    if getattr(sys, "frozen", False):
        bundle = Path(getattr(sys, "_MEIPASS", Path(sys.executable).parent))
        if (bundle / "bk_whisperx.py").exists():
            return bundle
        install_dir = Path(sys.executable).resolve().parent
        if (install_dir / "bk_whisperx.py").exists():
            return install_dir
        return bundle
    return Path(__file__).resolve().parents[1]


def runtime_dir() -> Path:
    return app_home() / "runtime"


def venv_dir() -> Path:
    # Source upgrades can keep using the existing project-local .venv.
    # Packaged builds always use the app-owned runtime.
    if not getattr(sys, "frozen", False):
        legacy = source_root() / ".venv"
        if legacy.is_dir():
            return legacy
    return runtime_dir() / "venv"


def venv_python() -> Path:
    if sys.platform == "win32":
        return venv_dir() / "Scripts" / "python.exe"
    return venv_dir() / "bin" / "python"


def models_dir() -> Path:
    return app_home() / "models"


def cache_dir() -> Path:
    return app_home() / "cache"


def logs_dir() -> Path:
    return app_home() / "logs"


def state_file() -> Path:
    return runtime_dir() / "state.json"


def settings_file() -> Path:
    return app_home() / "settings.json"


def defaults_file() -> Path:
    return app_home() / "defaults.json"


def temp_dir() -> Path:
    return app_home() / "temp"


def ensure_directories() -> None:
    for path in (app_home(), runtime_dir(), models_dir(), cache_dir(), logs_dir(), temp_dir()):
        path.mkdir(parents=True, exist_ok=True)


def _preferred_cache(explicit_name: str, legacy: Path, managed: Path) -> Path:
    explicit = os.environ.get(explicit_name)
    if explicit:
        return Path(explicit).expanduser()
    if legacy.exists():
        return legacy
    return managed


def runtime_environment() -> dict[str, str]:
    ensure_directories()
    env = os.environ.copy()

    # Reuse standard caches created by older BK WhisperX/WhisperX installs when
    # they already exist. New installations use the app-owned persistent cache.
    hf_home = _preferred_cache("HF_HOME", Path.home() / ".cache" / "huggingface", models_dir() / "huggingface")
    hub_cache = Path(os.environ.get("HUGGINGFACE_HUB_CACHE", hf_home / "hub")).expanduser()
    torch_home = _preferred_cache("TORCH_HOME", Path.home() / ".cache" / "torch", models_dir() / "torch")

    env.update(
        {
            "BKWHISPERX_HOME": str(app_home()),
            "HF_HOME": str(hf_home),
            "HUGGINGFACE_HUB_CACHE": str(hub_cache),
            "TORCH_HOME": str(torch_home),
            "XDG_CACHE_HOME": str(cache_dir()),
            "UV_CACHE_DIR": str(cache_dir() / "uv"),
            "UV_PYTHON_INSTALL_DIR": str(runtime_dir() / "python"),
            "UV_LINK_MODE": "copy",
            "UV_COMPILE_BYTECODE": "1",
            "UV_HTTP_TIMEOUT": "120",
            "PYTHONUTF8": "1",
            "PYTHONIOENCODING": "utf-8",
            "TOKENIZERS_PARALLELISM": "false",
        }
    )
    return env
