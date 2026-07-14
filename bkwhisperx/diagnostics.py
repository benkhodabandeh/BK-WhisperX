from __future__ import annotations

import os
import platform
import shutil
import tempfile
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path

from .bootstrap import health_check, read_state
from .paths import app_home, cache_dir, logs_dir, models_dir, runtime_dir, runtime_environment


@dataclass(slots=True)
class DiagnosticReport:
    platform: str
    profile: str
    runtime_healthy: bool
    runtime_message: str
    gpu_name: str
    driver_version: str
    torch_version: str
    torch_cuda_version: str
    cuda_available: bool
    ffmpeg_path: str
    app_home: str
    disk_free_bytes: int
    output_writable: bool | None
    output_message: str
    cache_bytes: dict[str, int]


def cache_locations() -> dict[str, Path]:
    env = runtime_environment()
    return {
        "models": models_dir(),
        "downloads": cache_dir(),
        "logs": logs_dir(),
        "runtime": runtime_dir(),
        "huggingface": Path(env["HF_HOME"]),
        "torch_models": Path(env["TORCH_HOME"]),
    }


def directory_size(path: Path) -> int:
    total = 0
    try:
        for root, _directories, files in os.walk(path):
            for filename in files:
                try:
                    total += (Path(root) / filename).stat().st_size
                except OSError:
                    continue
    except OSError:
        return total
    return total


def storage_summary(names: Iterable[str] | None = None) -> dict[str, int]:
    locations = cache_locations()
    selected = list(names) if names is not None else list(locations)
    return {name: directory_size(locations[name]) for name in selected if name in locations}


def clear_caches(names: Iterable[str]) -> dict[str, int]:
    """Clear only known cache roots and recreate managed directories safely."""
    locations = cache_locations()
    cleared: dict[str, int] = {}
    for name in dict.fromkeys(names):
        if name not in locations or name == "runtime":
            continue
        path = locations[name].expanduser().resolve()
        size = directory_size(path)
        if path == path.parent or path == Path.home().resolve():
            raise RuntimeError(f"Refusing to clear unsafe cache path: {path}")
        if path.exists():
            shutil.rmtree(path)
        path.mkdir(parents=True, exist_ok=True)
        cleared[name] = size
    return cleared


def _probe_output(output_dir: Path | None) -> tuple[bool | None, str]:
    if output_dir is None:
        return None, "No output folder selected."
    try:
        output_dir.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(prefix=".bkx-write-test-", dir=output_dir, delete=True):
            pass
        return True, "Output folder is writable."
    except OSError as exc:
        return False, f"Output folder is not writable: {exc}"


def collect_diagnostics(output_dir: Path | None = None, profile: str | None = None) -> DiagnosticReport:
    state = read_state()
    selected_profile = profile or (state.profile if state else "auto")
    healthy, info, message = health_check(selected_profile)
    writable, output_message = _probe_output(output_dir)
    app_home().mkdir(parents=True, exist_ok=True)
    try:
        disk_free = shutil.disk_usage(app_home()).free
    except OSError:
        disk_free = 0
    return DiagnosticReport(
        platform=f"{platform.system()} {platform.release()} ({platform.machine()})",
        profile=selected_profile,
        runtime_healthy=healthy,
        runtime_message=message,
        gpu_name=info.gpu_name or info.cuda_device_name,
        driver_version=info.driver_version,
        torch_version=info.torch_version,
        torch_cuda_version=info.torch_cuda_version,
        cuda_available=info.cuda_available,
        ffmpeg_path=info.ffmpeg_path,
        app_home=str(app_home()),
        disk_free_bytes=disk_free,
        output_writable=writable,
        output_message=output_message,
        cache_bytes=storage_summary(),
    )
