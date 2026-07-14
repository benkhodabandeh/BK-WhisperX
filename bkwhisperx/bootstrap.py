from __future__ import annotations

import hashlib
import json
import os
import platform
import re
import shutil
import stat
import subprocess
import sys
import tarfile
import tempfile
import threading
import time
import urllib.request
import zipfile
from collections import deque
from collections.abc import Callable, Iterable
from contextlib import contextmanager
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path

from .hardware import HardwareInfo, detect_nvidia, inspect_torch
from .paths import (
    ensure_directories,
    logs_dir,
    runtime_dir,
    runtime_environment,
    state_file,
    venv_dir,
    venv_python,
)
from .process_control import ManagedProcess

LogFn = Callable[[str], None]
ProgressFn = Callable[[dict[str, object]], None]

PROFILE_LABELS = {
    "auto": "Automatic (NVIDIA CUDA when available)",
    "cuda128": "NVIDIA GPU — CUDA 12.8",
    "cuda126": "NVIDIA GPU — CUDA 12.6 compatibility",
    "cpu": "CPU only",
}
TORCH_INDEXES = {
    "cuda128": "https://download.pytorch.org/whl/cu128",
    "cuda126": "https://download.pytorch.org/whl/cu126",
    "cpu": "https://download.pytorch.org/whl/cpu",
}
TORCH_PACKAGES = ("torch==2.8.0", "torchvision==0.23.0", "torchaudio==2.8.0")
TORCH_BASE_VERSIONS = {
    "torch": "2.8.0",
    "torchvision": "0.23.0",
    "torchaudio": "2.8.0",
}
PIP_NETWORK_ATTEMPTS = 5
PIP_NETWORK_RETRIES = 10
PIP_NETWORK_TIMEOUT = 120
RUNTIME_SCHEMA = "4"
UV_VERSION = "0.11.28"
UV_SHA256 = {
    "uv-aarch64-apple-darwin.tar.gz": "33540eb7c883ab857eff79bd5ac2aa31fe27b595abecb4a9c003a2c998447232",
    "uv-aarch64-pc-windows-msvc.zip": "3248109afad3ec59baad299d324ff53de17e2d9a3b3e21580ffd26744b11e036",
    "uv-aarch64-unknown-linux-gnu.tar.gz": "03e9fe0a81b0718d0bc84625de3885df6cc3f89a8b6af6121d6b9f6113fb6533",
    "uv-x86_64-apple-darwin.tar.gz": "2ad79983127ffca7d77b77ce6a24278d7e4f7b817a1acf72fea5f8124b4aac5e",
    "uv-x86_64-pc-windows-msvc.zip": "0a23463216d09c6a72ff80ef5dc5a795f07dc1575cb84d24596c2f124a441b7b",
    "uv-x86_64-unknown-linux-gnu.tar.gz": "e490a6464492183c5d4534a5527fb4440f7f2bb2f228162ad7e4afe076dc0224",
}


@dataclass(slots=True)
class RuntimeState:
    profile: str
    requirements_hash: str
    healthy: bool
    torch_version: str = ""
    torch_cuda_version: str = ""
    cuda_available: bool = False
    app_version: str = ""


_CONSOLE_PROGRESS_LOCK = threading.Lock()
_CONSOLE_PROGRESS_ACTIVE = False
_CONSOLE_PROGRESS_WIDTH = 0
_CONSOLE_PROGRESS_BUCKET = -1
_HEALTH_CACHE: dict[str, tuple[float, tuple[bool, HardwareInfo, str]]] = {}
HEALTH_CACHE_SECONDS = 30.0


class SetupCancelled(RuntimeError):
    """Raised after a runtime setup cancellation is requested."""


def _raise_if_setup_cancelled(cancel_event: threading.Event | None) -> None:
    if cancel_event is not None and cancel_event.is_set():
        raise SetupCancelled("Runtime setup was cancelled. Completed downloads remain cached.")


def _human_bytes(value: float) -> str:
    units = ("B", "KB", "MB", "GB", "TB")
    size = max(0.0, float(value))
    for unit in units:
        if size < 1024.0 or unit == units[-1]:
            decimals = 0 if unit == "B" else 1 if size < 100 else 0
            return f"{size:.{decimals}f} {unit}"
        size /= 1024.0
    return f"{size:.1f} TB"


def _human_duration(seconds: float | None) -> str:
    if seconds is None or seconds < 0 or seconds == float("inf"):
        return "--"
    total = int(round(seconds))
    hours, remainder = divmod(total, 3600)
    minutes, secs = divmod(remainder, 60)
    if hours:
        return f"{hours:d}:{minutes:02d}:{secs:02d}"
    if minutes:
        return f"{minutes:d}:{secs:02d}"
    return f"{secs:d}s"


def _progress_summary(payload: dict[str, object]) -> str:
    label = str(payload.get("label") or "Download")
    current = int(payload.get("current") or 0)
    total = int(payload.get("total") or 0)
    speed = float(payload.get("speed") or 0.0)
    eta_raw = payload.get("eta")
    eta = float(eta_raw) if isinstance(eta_raw, int | float) else None
    percent_raw = payload.get("percent")
    percent = float(percent_raw) if isinstance(percent_raw, int | float) else None
    done = bool(payload.get("done"))

    if total > 0 and percent is not None:
        status = f"{percent:5.1f}%  {_human_bytes(current)} / {_human_bytes(total)}"
    else:
        status = _human_bytes(current) if current else "working"
    if speed > 0:
        status += f"  •  {_human_bytes(speed)}/s"
    if eta is not None and not done and total > 0:
        status += f"  •  ETA {_human_duration(eta)}"
    if done:
        status += "  •  complete"
    return f"{label}: {status}"


def _progress_default(payload: dict[str, object]) -> None:
    """Render one live transfer line in a real terminal; stay concise elsewhere."""
    global _CONSOLE_PROGRESS_ACTIVE, _CONSOLE_PROGRESS_WIDTH, _CONSOLE_PROGRESS_BUCKET
    text = _progress_summary(payload)
    done = bool(payload.get("done"))
    percent_raw = payload.get("percent")
    percent = float(percent_raw) if isinstance(percent_raw, int | float) else None

    with _CONSOLE_PROGRESS_LOCK:
        if sys.stdout.isatty():
            padded = text.ljust(max(_CONSOLE_PROGRESS_WIDTH, len(text)))
            sys.stdout.write("\r" + padded)
            sys.stdout.flush()
            _CONSOLE_PROGRESS_ACTIVE = not done
            _CONSOLE_PROGRESS_WIDTH = max(_CONSOLE_PROGRESS_WIDTH, len(text))
            if done:
                sys.stdout.write("\n")
                sys.stdout.flush()
                _CONSOLE_PROGRESS_WIDTH = 0
                _CONSOLE_PROGRESS_BUCKET = -1
            return

        bucket = 100 if done else int(percent // 10) if percent is not None else 0
        if done or bucket != _CONSOLE_PROGRESS_BUCKET:
            print(text, flush=True)
            _CONSOLE_PROGRESS_BUCKET = bucket


def _log_default(message: str) -> None:
    global _CONSOLE_PROGRESS_ACTIVE, _CONSOLE_PROGRESS_WIDTH
    with _CONSOLE_PROGRESS_LOCK:
        if _CONSOLE_PROGRESS_ACTIVE:
            sys.stdout.write("\n")
            sys.stdout.flush()
            _CONSOLE_PROGRESS_ACTIVE = False
            _CONSOLE_PROGRESS_WIDTH = 0
        print(message, flush=True)


class _TransferMeter:
    def __init__(self, label: str, phase: str, progress: ProgressFn) -> None:
        self.label = label
        self.phase = phase
        self.progress = progress
        self.started = time.monotonic()
        self.last_emit = 0.0
        self.current = 0
        self.total = 0
        self.done = False

    def update(self, current: int, total: int = 0, *, force: bool = False) -> None:
        if self.done:
            return
        current = max(0, int(current))
        total = max(0, int(total))
        if current < self.current or (self.total and total and total != self.total):
            self.started = time.monotonic()
        self.current = current
        self.total = total
        now = time.monotonic()
        if not force and now - self.last_emit < 0.2 and not (total and current >= total):
            return
        self.last_emit = now
        elapsed = max(0.001, now - self.started)
        speed = current / elapsed
        percent = current * 100.0 / total if total > 0 else None
        eta = (total - current) / speed if total > current and speed > 0 else 0.0 if total else None
        self.progress(
            {
                "phase": self.phase,
                "label": self.label,
                "current": current,
                "total": total,
                "percent": percent,
                "speed": speed,
                "eta": eta,
                "done": False,
                "indeterminate": total <= 0,
            }
        )

    def finish(self) -> None:
        if self.done:
            return
        self.done = True
        now = time.monotonic()
        elapsed = max(0.001, now - self.started)
        final_current = self.total if self.total > 0 else self.current
        speed = final_current / elapsed
        percent = 100.0 if self.total > 0 else None
        self.progress(
            {
                "phase": self.phase,
                "label": self.label,
                "current": final_current,
                "total": self.total,
                "percent": percent,
                "speed": speed,
                "eta": 0.0 if self.total > 0 else None,
                "done": True,
                "indeterminate": False,
            }
        )


def _driver_tuple(value: str) -> tuple[int, ...]:
    pieces: list[int] = []
    for part in value.strip().split("."):
        digits = "".join(character for character in part if character.isdigit())
        if not digits:
            break
        pieces.append(int(digits))
    return tuple(pieces) or (0,)


def automatic_profile() -> str:
    present, _gpu_name, driver = detect_nvidia()
    if not present:
        return "cpu"
    version = _driver_tuple(driver)
    if sys.platform == "win32":
        if version >= (570, 65):
            return "cuda128"
        if version >= (560, 76):
            return "cuda126"
    else:
        if version >= (570, 26):
            return "cuda128"
        if version >= (560, 28, 3):
            return "cuda126"
    return "cpu"


def resolve_profile(profile: str) -> str:
    profile = profile.lower().strip()
    if profile in {"cuda", "gpu"}:
        return "cuda128"
    if profile == "auto":
        return automatic_profile()
    if profile not in TORCH_INDEXES:
        raise ValueError(f"Unknown runtime profile: {profile}")
    return profile


def requirements_path() -> Path:
    return Path(__file__).resolve().parent / "runtime-requirements.txt"


def requirements_hash() -> str:
    req = requirements_path()
    payload = req.read_bytes() if req.exists() else b""
    payload += ("\n" + RUNTIME_SCHEMA + "\n" + UV_VERSION + "\n" + "\n".join(TORCH_PACKAGES)).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def read_state() -> RuntimeState | None:
    try:
        data = json.loads(state_file().read_text(encoding="utf-8"))
        return RuntimeState(**data)
    except Exception:
        return None


def write_state(state: RuntimeState) -> None:
    state_file().parent.mkdir(parents=True, exist_ok=True)
    temporary = state_file().with_suffix(".tmp")
    try:
        temporary.write_text(json.dumps(asdict(state), indent=2), encoding="utf-8")
        os.replace(temporary, state_file())
    finally:
        temporary.unlink(missing_ok=True)


def _uv_asset_url() -> str:
    machine = platform.machine().lower()
    is_arm = machine in {"arm64", "aarch64"}
    system = platform.system().lower()
    if system == "windows":
        target = "aarch64-pc-windows-msvc" if is_arm else "x86_64-pc-windows-msvc"
        return f"https://github.com/astral-sh/uv/releases/download/{UV_VERSION}/uv-{target}.zip"
    if system == "darwin":
        target = "aarch64-apple-darwin" if is_arm else "x86_64-apple-darwin"
        return f"https://github.com/astral-sh/uv/releases/download/{UV_VERSION}/uv-{target}.tar.gz"
    target = "aarch64-unknown-linux-gnu" if is_arm else "x86_64-unknown-linux-gnu"
    return f"https://github.com/astral-sh/uv/releases/download/{UV_VERSION}/uv-{target}.tar.gz"


def uv_executable() -> Path:
    return runtime_dir() / "tools" / ("uv.exe" if sys.platform == "win32" else "uv")


def _probe_uv(executable: Path) -> tuple[bool, str]:
    """Run uv and validate its semantic version without rejecting build metadata."""
    last_detail = ""
    for version_flag in ("-V", "--version"):
        try:
            result = subprocess.run(
                [str(executable), version_flag],
                capture_output=True,
                text=True,
                timeout=15,
                encoding="utf-8",
                errors="replace",
                creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0,
            )
        except Exception as exc:
            last_detail = f"{type(exc).__name__}: {exc}"
            continue

        output = "\n".join(part.strip() for part in (result.stdout, result.stderr) if part.strip())
        last_detail = output or f"process exited with code {result.returncode}"
        match = re.search(r"(?im)^\s*uv\s+v?(\d+\.\d+\.\d+)(?:\s|$)", output)
        if result.returncode == 0 and match and match.group(1) == UV_VERSION:
            return True, output

    return False, last_detail


def _download_file(
    request: urllib.request.Request,
    destination: Path,
    *,
    label: str,
    phase: str,
    log: LogFn,
    progress: ProgressFn,
    attempts: int = 3,
    cancel_event: threading.Event | None = None,
) -> None:
    last_error: Exception | None = None
    for attempt in range(1, attempts + 1):
        _raise_if_setup_cancelled(cancel_event)
        destination.unlink(missing_ok=True)
        meter = _TransferMeter(label, phase, progress)
        try:
            with urllib.request.urlopen(request, timeout=120) as response, destination.open("wb") as out:  # nosec B310
                try:
                    total = int(response.headers.get("Content-Length") or 0)
                except (TypeError, ValueError):
                    total = 0
                meter.update(0, total, force=True)
                current = 0
                while True:
                    _raise_if_setup_cancelled(cancel_event)
                    chunk = response.read(256 * 1024)
                    if not chunk:
                        break
                    out.write(chunk)
                    current += len(chunk)
                    meter.update(current, total)
                out.flush()
                try:
                    os.fsync(out.fileno())
                except OSError:
                    pass
                meter.update(current, total, force=True)
                meter.finish()
            return
        except SetupCancelled:
            destination.unlink(missing_ok=True)
            raise
        except Exception as exc:
            last_error = exc
            destination.unlink(missing_ok=True)
            log(f"{label} download attempt {attempt}/{attempts} failed: {exc}")
    if last_error is None:
        raise RuntimeError(f"Could not download {label}.")
    raise RuntimeError(f"Could not download {label} after {attempts} attempts: {last_error}") from last_error


def ensure_uv(
    log: LogFn = _log_default,
    progress: ProgressFn = _progress_default,
    cancel_event: threading.Event | None = None,
) -> Path:
    ensure_directories()
    target = uv_executable()
    if target.is_file():
        valid, _detail = _probe_uv(target)
        if valid:
            return target
        target.unlink(missing_ok=True)
    target.parent.mkdir(parents=True, exist_ok=True)
    url = _uv_asset_url()
    log("Downloading the runtime manager…")
    request = urllib.request.Request(url, headers={"User-Agent": "BK-WhisperX"})
    suffix = ".zip" if url.endswith(".zip") else ".tar.gz"
    with tempfile.TemporaryDirectory(prefix="bkx-uv-") as tmp:
        archive = Path(tmp) / f"uv{suffix}"
        _download_file(
            request,
            archive,
            label="Runtime manager",
            phase="runtime_manager",
            log=log,
            progress=progress,
            cancel_event=cancel_event,
        )
        filename = url.rsplit("/", 1)[-1]
        expected_hash = UV_SHA256.get(filename)
        actual_hash = hashlib.sha256(archive.read_bytes()).hexdigest()
        if not expected_hash or actual_hash != expected_hash:
            raise RuntimeError("The downloaded runtime manager failed SHA-256 verification.")

        extract_dir = Path(tmp) / "extract"
        extract_dir.mkdir()
        if suffix == ".zip":
            with zipfile.ZipFile(archive) as zf:
                destination = extract_dir.resolve()
                for member in zf.infolist():
                    member_path = (extract_dir / member.filename).resolve()
                    if destination not in member_path.parents and member_path != destination:
                        raise RuntimeError("Unsafe path in downloaded runtime archive.")
                    unix_mode = member.external_attr >> 16
                    if stat.S_ISLNK(unix_mode):
                        raise RuntimeError("Unsafe symbolic link in downloaded runtime archive.")
                    if member.is_dir():
                        member_path.mkdir(parents=True, exist_ok=True)
                        continue
                    file_type = stat.S_IFMT(unix_mode)
                    if file_type not in {0, stat.S_IFREG}:
                        raise RuntimeError("Unsafe special entry in downloaded runtime archive.")
                    member_path.parent.mkdir(parents=True, exist_ok=True)
                    with zf.open(member) as source, member_path.open("wb") as output:
                        shutil.copyfileobj(source, output)
        else:
            with tarfile.open(archive, "r:gz") as tf:
                destination = extract_dir.resolve()
                for member in tf.getmembers():
                    member_path = (extract_dir / member.name).resolve()
                    if destination not in member_path.parents and member_path != destination:
                        raise RuntimeError("Unsafe path in downloaded runtime archive.")
                    if member.issym() or member.islnk() or member.isdev():
                        raise RuntimeError("Unsafe special entry in downloaded runtime archive.")
                    if member.isdir():
                        member_path.mkdir(parents=True, exist_ok=True)
                        continue
                    if not member.isfile():
                        raise RuntimeError("Unsafe special entry in downloaded runtime archive.")
                    source = tf.extractfile(member)
                    if source is None:
                        raise RuntimeError("Could not read a file from the runtime archive.")
                    member_path.parent.mkdir(parents=True, exist_ok=True)
                    with source, member_path.open("wb") as output:
                        shutil.copyfileobj(source, output)
        candidates = list(extract_dir.rglob(target.name))
        if not candidates:
            raise RuntimeError("The downloaded runtime manager archive did not contain uv.")
        shutil.copy2(candidates[0], target)
        if sys.platform != "win32":
            target.chmod(0o755)

    valid, detail = _probe_uv(target)
    if not valid:
        target.unlink(missing_ok=True)
        raise RuntimeError(
            "The downloaded runtime manager could not be verified after extraction. "
            f"Expected uv {UV_VERSION}; received: {detail}"
        )
    log("Runtime manager is ready.")
    return target


def _watch_setup_cancellation(
    process: subprocess.Popen[object],
    guard: ManagedProcess,
    cancel_event: threading.Event | None,
) -> threading.Thread | None:
    if cancel_event is None:
        return None

    def watch() -> None:
        while process.poll() is None:
            if cancel_event.wait(0.1):
                guard.terminate_tree()
                return

    thread = threading.Thread(target=watch, name="bkx-setup-cancel", daemon=True)
    thread.start()
    return thread


def _run_streaming(
    command: Iterable[str | os.PathLike[str]],
    log: LogFn,
    *,
    env: dict[str, str] | None = None,
    progress: ProgressFn = _progress_default,
    activity_label: str | None = None,
    phase: str = "setup",
    cancel_event: threading.Event | None = None,
) -> None:
    cmd = [str(part) for part in command]
    log("$ " + subprocess.list2cmdline(cmd))
    meter = _TransferMeter(activity_label, phase, progress) if activity_label else None
    if meter:
        meter.update(0, 0, force=True)
    proc = subprocess.Popen(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        encoding="utf-8",
        errors="replace",
        env=env,
        **ManagedProcess.popen_platform_kwargs(hidden=True, new_group=True),
    )
    guard = ManagedProcess(proc)
    watcher = _watch_setup_cancellation(proc, guard, cancel_event)
    if proc.stdout is None:
        guard.close()
        raise RuntimeError("Could not capture setup process output.")
    try:
        for line in proc.stdout:
            _raise_if_setup_cancelled(cancel_event)
            line = line.rstrip()
            if line:
                log(line)
        return_code = proc.wait()
    finally:
        guard.close()
        if watcher is not None:
            watcher.join(timeout=0.2)
    _raise_if_setup_cancelled(cancel_event)
    if return_code != 0:
        raise RuntimeError(f"Command failed with exit code {return_code}: {subprocess.list2cmdline(cmd)}")
    if meter:
        meter.finish()


def _pip_download_label(line: str) -> str | None:
    match = re.search(r"(?:^|\s)Downloading\s+(.+?)(?:\s+\([^)]*\))?$", line.strip())
    if not match:
        return None
    value = match.group(1).strip()
    if "://" in value:
        value = value.split("?", 1)[0].rstrip("/").rsplit("/", 1)[-1]
    filename = Path(value).name
    if filename.endswith((".whl", ".zip", ".tar.gz", ".tar.bz2")):
        package = filename.split("-", 1)[0]
        return package.replace("_", "-") or "Package"
    return filename[:64] or "Package"


class _PipCommandError(RuntimeError):
    def __init__(self, return_code: int, command: list[str], output_tail: str) -> None:
        self.return_code = return_code
        self.command = command
        self.output_tail = output_tail
        super().__init__(f"Command failed with exit code {return_code}: {subprocess.list2cmdline(command)}")


# ponytail: only the 3 patterns that cover >95% of transient network failures
_RETRYABLE_PIP_ERRORS = ("IncompleteRead", "Connection broken", "timed out")


def _is_retryable_pip_failure(error: _PipCommandError) -> bool:
    details = error.output_tail.casefold()
    return any(marker.casefold() in details for marker in _RETRYABLE_PIP_ERRORS)


def _run_pip_with_retries(
    command: Iterable[str | os.PathLike[str]],
    log: LogFn,
    *,
    env: dict[str, str] | None = None,
    progress: ProgressFn = _progress_default,
    phase: str,
    attempts: int = PIP_NETWORK_ATTEMPTS,
    cancel_event: threading.Event | None = None,
) -> None:
    """Retry interrupted package transfers while retaining pip's completed cache."""
    cmd = [str(part) for part in command]
    for attempt in range(1, attempts + 1):
        try:
            _run_pip_streaming(
                cmd,
                log,
                env=env,
                progress=progress,
                phase=phase,
                cancel_event=cancel_event,
            )
            return
        except _PipCommandError as exc:
            if attempt >= attempts or not _is_retryable_pip_failure(exc):
                raise
            delay = min(30, 2**attempt)
            log(
                "Network interruption detected. "
                f"Retrying package setup ({attempt + 1}/{attempts}) in {delay}s; "
                "completed downloads remain cached."
            )
            if cancel_event is not None and cancel_event.wait(delay):
                raise SetupCancelled("Runtime setup was cancelled. Completed downloads remain cached.") from exc
            if cancel_event is None:
                time.sleep(delay)


def _run_pip_streaming(
    command: Iterable[str | os.PathLike[str]],
    log: LogFn,
    *,
    env: dict[str, str] | None = None,
    progress: ProgressFn = _progress_default,
    phase: str,
    cancel_event: threading.Event | None = None,
) -> None:
    """Run pip with its supported raw byte progress stream and render speed/ETA."""
    cmd = [str(part) for part in command]
    log("$ " + subprocess.list2cmdline(cmd))
    child_env = dict(env or os.environ)
    child_env["PIP_PROGRESS_BAR"] = "raw"
    child_env["PIP_DISABLE_PIP_VERSION_CHECK"] = "1"
    child_env["PIP_DEFAULT_TIMEOUT"] = str(PIP_NETWORK_TIMEOUT)
    child_env["PIP_RETRIES"] = str(PIP_NETWORK_RETRIES)
    child_env["PYTHONUNBUFFERED"] = "1"
    proc = subprocess.Popen(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        encoding="utf-8",
        errors="replace",
        env=child_env,
        **ManagedProcess.popen_platform_kwargs(hidden=True, new_group=True),
    )
    guard = ManagedProcess(proc)
    watcher = _watch_setup_cancellation(proc, guard, cancel_event)
    if proc.stdout is None:
        guard.close()
        raise RuntimeError("Could not capture package installer output.")
    label = "Python package"
    meter: _TransferMeter | None = None
    output_tail: deque[str] = deque(maxlen=120)
    progress_pattern = re.compile(r"^Progress\s+(\d+)\s+of\s+(\d+)\s*$")

    try:
        for raw_line in proc.stdout:
            _raise_if_setup_cancelled(cancel_event)
            line = raw_line.rstrip("\r\n")
            if line:
                output_tail.append(line)
            progress_match = progress_pattern.match(line.strip())
            if progress_match:
                current = int(progress_match.group(1))
                total = int(progress_match.group(2))
                if meter is None or current < meter.current or (meter.total and total and meter.total != total):
                    if meter is not None:
                        meter.finish()
                    meter = _TransferMeter(label, phase, progress)
                meter.update(current, total, force=current == 0 or (total > 0 and current >= total))
                if total > 0 and current >= total:
                    meter.finish()
                    meter = None
                continue

            new_label = _pip_download_label(line)
            if new_label:
                if meter is not None and meter.current:
                    meter.finish()
                    meter = None
                label = new_label
            if line:
                log(line)

        return_code = proc.wait()
    finally:
        guard.close()
        if watcher is not None:
            watcher.join(timeout=0.2)
    _raise_if_setup_cancelled(cancel_event)
    if return_code != 0:
        raise _PipCommandError(return_code, cmd, "\n".join(output_tail))
    if meter is not None:
        meter.finish()


def _ensure_pip(
    uv: Path,
    python: Path,
    log: LogFn,
    *,
    env: dict[str, str],
    progress: ProgressFn,
    cancel_event: threading.Event | None = None,
) -> None:
    result = subprocess.run(
        [str(python), "-m", "pip", "--version"],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        env=env,
        creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0,
    )
    if result.returncode == 0:
        return
    log("Preparing the package installer…")
    _run_streaming(
        [uv, "pip", "install", "--python", python, "--upgrade", "pip"],
        log,
        env=env,
        progress=progress,
        activity_label="Package installer",
        phase="package_installer",
        cancel_event=cancel_event,
    )


def _base_package_version(value: str) -> str:
    return value.strip().split("+", 1)[0]


def _validate_torch_info(profile: str, info: HardwareInfo) -> tuple[bool, str]:
    if not info.torch_installed:
        return False, info.error or "PyTorch import failed."
    installed_versions = {
        "torch": _base_package_version(info.torch_version),
        "torchvision": _base_package_version(info.torchvision_version),
        "torchaudio": _base_package_version(info.torchaudio_version),
    }
    for package, expected in TORCH_BASE_VERSIONS.items():
        actual = installed_versions[package]
        if actual != expected:
            return False, f"{package} {actual or 'unknown'} is installed; {expected} is required."
    resolved = resolve_profile(profile)
    if resolved == "cpu":
        if info.torch_cuda_version:
            return False, f"A CUDA {info.torch_cuda_version} PyTorch build is installed; the CPU build is required."
    else:
        expected_cuda = "12.8" if resolved == "cuda128" else "12.6"
        if not info.torch_cuda_version.startswith(expected_cuda):
            return False, (
                f"PyTorch CUDA {info.torch_cuda_version or 'none'} is installed; CUDA {expected_cuda} is required."
            )
        if not info.cuda_available:
            return False, "The CUDA PyTorch build is installed, but it cannot access the NVIDIA GPU."
    return True, "PyTorch stack is healthy."


def torch_health_check(profile: str) -> tuple[bool, HardwareInfo, str]:
    """Validate only the PyTorch layer so an interrupted WhisperX setup can resume."""
    python = venv_python()
    if not python.is_file():
        return False, HardwareInfo(nvidia_present=detect_nvidia()[0]), "PyTorch runtime is not installed."
    info = inspect_torch(str(python), env=runtime_environment())
    ok, message = _validate_torch_info(profile, info)
    return ok, info, message


def health_check(
    profile: str | None = None,
    *,
    use_cache: bool = True,
) -> tuple[bool, HardwareInfo, str]:
    python = venv_python()
    if not python.is_file():
        return False, HardwareInfo(nvidia_present=detect_nvidia()[0]), "Runtime is not installed."
    resolved = resolve_profile(profile or (read_state().profile if read_state() else "auto"))
    cached = _HEALTH_CACHE.get(resolved)
    if use_cache and cached and time.monotonic() - cached[0] < HEALTH_CACHE_SECONDS:
        return cached[1]
    info = inspect_torch(str(python), full=True, env=runtime_environment())
    torch_ok, message = _validate_torch_info(resolved, info)
    if not torch_ok:
        result = (False, info, message)
    elif not info.dependencies_ok:
        result = (False, info, info.error or "WhisperX support libraries failed to import.")
    elif not info.ffmpeg_path or not Path(info.ffmpeg_path).is_file():
        result = (False, info, "Managed FFmpeg binary is missing.")
    else:
        result = (True, info, "Runtime is healthy.")
    _HEALTH_CACHE[resolved] = (time.monotonic(), result)
    return result


def _python_executable_works(python: Path, env: dict[str, str]) -> bool:
    try:
        result = subprocess.run(
            [str(python), "-c", "import sys; raise SystemExit(0 if sys.version_info[:2] == (3, 11) else 2)"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=15,
            env=env,
            creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0,
        )
        return result.returncode == 0
    except OSError:
        return False


def _acquire_file_lock(handle: object) -> None:
    if sys.platform == "win32":
        import msvcrt

        handle.seek(0)
        msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
        return

    import fcntl

    fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)


def _release_file_lock(handle: object) -> None:
    if sys.platform == "win32":
        import msvcrt

        handle.seek(0)
        msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
        return

    import fcntl

    fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


@contextmanager
def _installation_lock() -> Iterable[None]:
    """Prevent concurrent setup while recovering automatically after crashes."""
    ensure_directories()
    lock_path = runtime_dir() / "setup.lock"
    descriptor = os.open(lock_path, os.O_RDWR | os.O_CREAT, 0o600)
    handle = os.fdopen(descriptor, "r+b")
    acquired = False

    try:
        # Windows byte-range locks require the target byte to exist. The lock file
        # intentionally remains on disk; only the OS lock indicates active setup.
        handle.seek(0, os.SEEK_END)
        if handle.tell() == 0:
            handle.write(b"\0")
            handle.flush()

        try:
            _acquire_file_lock(handle)
            acquired = True
        except OSError as exc:
            raise RuntimeError(
                "Another BK WhisperX runtime setup is already running. Close the other setup and try again."
            ) from exc

        metadata = json.dumps(
            {
                "pid": os.getpid(),
                "started_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            }
        ).encode("utf-8")
        handle.seek(0)
        handle.write(metadata)
        handle.truncate(max(1, len(metadata)))
        handle.flush()
        try:
            os.fsync(handle.fileno())
        except OSError:
            pass

        yield
    finally:
        if acquired:
            try:
                _release_file_lock(handle)
            except OSError:
                pass
        handle.close()


def install_runtime(
    profile: str = "auto",
    *,
    force: bool = False,
    log: LogFn = _log_default,
    progress: ProgressFn = _progress_default,
    app_version: str = "",
    cancel_event: threading.Event | None = None,
) -> RuntimeState:
    with _installation_lock():
        return _install_runtime_impl(
            profile,
            force=force,
            log=log,
            progress=progress,
            app_version=app_version,
            cancel_event=cancel_event,
        )


def _install_runtime_impl(
    profile: str = "auto",
    *,
    force: bool = False,
    log: LogFn = _log_default,
    progress: ProgressFn = _progress_default,
    app_version: str = "",
    cancel_event: threading.Event | None = None,
) -> RuntimeState:
    ensure_directories()
    setup_log = logs_dir() / "runtime-setup.log"
    if setup_log.exists() and setup_log.stat().st_size > 5 * 1024 * 1024:
        rotated = setup_log.with_suffix(".log.1")
        rotated.unlink(missing_ok=True)
        setup_log.replace(rotated)
    original_log = log

    def persistent_log(message: str) -> None:
        original_log(message)
        timestamp = datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")
        with setup_log.open("a", encoding="utf-8", newline="\n") as handle:
            handle.write(f"[{timestamp}] {message}\n")

    log = persistent_log
    resolved = resolve_profile(profile)
    if profile.lower().strip() == "auto":
        nvidia_present, gpu_name, driver = detect_nvidia()
        if nvidia_present and resolved == "cpu":
            log(
                f"NVIDIA GPU detected ({gpu_name}), but driver {driver or 'unknown'} is below the safe "
                "CUDA 12.6 profile threshold. Update the NVIDIA driver or select CUDA 12.6 manually."
            )
        elif nvidia_present:
            log(f"Automatic runtime selected {PROFILE_LABELS[resolved]} for driver {driver}.")
    req_hash = requirements_hash()
    previous = read_state()
    _raise_if_setup_cancelled(cancel_event)
    healthy, info, _ = health_check(resolved, use_cache=False)
    if not force and previous and previous.profile == resolved and previous.requirements_hash == req_hash and healthy:
        log(f"Existing {PROFILE_LABELS[resolved]} runtime is healthy; no downloads are needed.")
        if app_version and previous.app_version != app_version:
            previous.app_version = app_version
            write_state(previous)
        return previous

    if resolved.startswith("cuda") and not detect_nvidia()[0]:
        raise RuntimeError("No NVIDIA GPU/driver was detected. Install an NVIDIA driver or choose CPU only.")

    uv = ensure_uv(log, progress, cancel_event)
    env = runtime_environment()
    python = venv_python()
    environment = venv_dir()
    if environment.is_dir() and not _python_executable_works(python, env):
        broken = environment
        quarantine = broken.with_name(f"{broken.name}.broken-{int(time.time())}")
        log(f"The existing Python environment is not executable; moving it to {quarantine.name}.")
        broken.replace(quarantine)
        python = venv_python()
    if not python.is_file():
        log("Installing an isolated Python 3.11 runtime…")
        _run_streaming(
            [uv, "python", "install", "3.11"],
            log,
            env=env,
            progress=progress,
            activity_label="Python 3.11 runtime",
            phase="python_runtime",
            cancel_event=cancel_event,
        )
        _run_streaming(
            [uv, "venv", "--python", "3.11", str(python.parent.parent)],
            log,
            env=env,
            progress=progress,
            activity_label="Virtual environment",
            phase="virtual_environment",
            cancel_event=cancel_event,
        )

    req = requirements_path()
    if not req.is_file():
        raise FileNotFoundError(f"Missing runtime requirements: {req}")

    env["PIP_CACHE_DIR"] = str(runtime_dir().parent / "cache" / "pip")
    Path(env["PIP_CACHE_DIR"]).mkdir(parents=True, exist_ok=True)
    _ensure_pip(uv, python, log, env=env, progress=progress, cancel_event=cancel_event)

    index_url = TORCH_INDEXES[resolved]
    torch_healthy, _torch_info, torch_message = torch_health_check(resolved)
    torch_needs_install = force or not torch_healthy
    if torch_needs_install:
        if torch_message:
            log(f"PyTorch setup required: {torch_message}")
        log(f"Installing the matching PyTorch stack for {PROFILE_LABELS[resolved]}…")
        torch_command = [
            python,
            "-m",
            "pip",
            "install",
            "--disable-pip-version-check",
            "--no-input",
            "--progress-bar",
            "raw",
            "--retries",
            str(PIP_NETWORK_RETRIES),
            "--timeout",
            str(PIP_NETWORK_TIMEOUT),
            "--upgrade",
            "--force-reinstall",
            *TORCH_PACKAGES,
            "--index-url",
            index_url,
        ]
        _run_pip_with_retries(
            torch_command,
            log,
            env=env,
            progress=progress,
            phase="pytorch",
            cancel_event=cancel_event,
        )
    else:
        log(f"Keeping the healthy PyTorch stack for {PROFILE_LABELS[resolved]}; no Torch download is needed.")

    log("Installing or updating WhisperX and support libraries…")
    _run_pip_with_retries(
        [
            python,
            "-m",
            "pip",
            "install",
            "--disable-pip-version-check",
            "--no-input",
            "--progress-bar",
            "raw",
            "--retries",
            str(PIP_NETWORK_RETRIES),
            "--timeout",
            str(PIP_NETWORK_TIMEOUT),
            "--prefer-binary",
            "--upgrade",
            "-r",
            req,
        ],
        log,
        env=env,
        progress=progress,
        phase="runtime_packages",
        cancel_event=cancel_event,
    )

    _raise_if_setup_cancelled(cancel_event)
    _run_streaming(
        [python, "-m", "pip", "check"],
        log,
        env=env,
        progress=progress,
        activity_label="Dependency verification",
        phase="verification",
        cancel_event=cancel_event,
    )
    _HEALTH_CACHE.pop(resolved, None)
    healthy, info, message = health_check(resolved, use_cache=False)
    if not healthy:
        raise RuntimeError(message)

    state = RuntimeState(
        profile=resolved,
        requirements_hash=req_hash,
        healthy=True,
        torch_version=info.torch_version,
        torch_cuda_version=info.torch_cuda_version,
        cuda_available=info.cuda_available,
        app_version=app_version,
    )
    write_state(state)
    log(
        f"Runtime ready: torch {info.torch_version}"
        + (f", CUDA {info.torch_cuda_version}, {info.cuda_device_name}" if info.cuda_available else ", CPU")
    )
    return state
