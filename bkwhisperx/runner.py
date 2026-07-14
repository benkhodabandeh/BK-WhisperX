from __future__ import annotations

import json
import os
import re
import subprocess
import uuid
from collections.abc import Callable
from pathlib import Path

from .bootstrap import ProgressFn, SetupCancelled, install_runtime, read_state
from .paths import logs_dir, runtime_environment, source_root, temp_dir, venv_python
from .process_control import ManagedProcess, WorkerController

LineFn = Callable[[str], None]
EventFn = Callable[[str, dict], None]
EVENT_PREFIX = "BKX_EVENT:"
_TRANSFER_RE = re.compile(
    r"^(?P<label>[^:\r\n]{1,100}):\s*(?P<percent>\d{1,3})%\|.*?\|\s*"
    r"(?P<current>[\d.]+)(?P<current_unit>[kMGT]?B)?/(?P<total>[\d.]+)(?P<total_unit>[kMGT]?B)?"
    r"(?:\s*\[[^\]]*,\s*(?P<speed>[\d.]+)(?P<speed_unit>[kMGT]?B)/s\])?"
)


def _size_to_bytes(value: str, unit: str | None) -> int:
    multipliers = {None: 1, "B": 1, "kB": 1000, "MB": 1000**2, "GB": 1000**3, "TB": 1000**4}
    return round(float(value) * multipliers.get(unit, 1))


def _download_event(line: str) -> dict[str, object] | None:
    match = _TRANSFER_RE.match(line.strip())
    if not match:
        return None
    current = _size_to_bytes(match.group("current"), match.group("current_unit"))
    total = _size_to_bytes(match.group("total"), match.group("total_unit"))
    speed = _size_to_bytes(match.group("speed"), match.group("speed_unit")) if match.group("speed") else 0
    return {
        "phase": "model_download",
        "label": match.group("label"),
        "percent": float(match.group("percent")),
        "current": current,
        "total": total,
        "speed": speed,
        "done": int(match.group("percent")) >= 100,
        "indeterminate": False,
    }


def _event_log_line(kind: str, payload: dict) -> str | None:
    if kind == "live_segment":
        return None
    safe_keys = ("index", "total", "name", "phase", "label", "percent", "elapsed", "language")
    safe = {key: payload[key] for key in safe_keys if key in payload}
    return f"[event:{kind}] " + json.dumps(safe, ensure_ascii=False)


def worker_command(config_path: Path, machine_events: bool = True) -> list[str]:
    script = source_root() / "bk_whisperx.py"
    if script.is_file():
        command = [str(venv_python()), str(script), "--worker-config", str(config_path)]
    else:
        command = [str(venv_python()), "-m", "bkwhisperx.cli", "--worker-config", str(config_path)]
    if machine_events:
        command.append("--machine-events")
    return command


def new_worker_controller() -> WorkerController:
    temp_dir().mkdir(parents=True, exist_ok=True)
    return WorkerController(temp_dir() / f"cancel-{uuid.uuid4().hex}.flag")


def run_worker(
    config_path: Path,
    *,
    profile: str = "auto",
    line_callback: LineFn | None = None,
    event_callback: EventFn | None = None,
    progress_callback: ProgressFn | None = None,
    app_version: str = "",
    hf_token: str | None = None,
    controller: WorkerController | None = None,
) -> int:
    line_callback = line_callback or (lambda line: print(line, flush=True))
    event_callback = event_callback or (lambda kind, payload: None)
    controller = controller or new_worker_controller()
    controller.reset()

    install_kwargs = {
        "profile": profile,
        "log": line_callback,
        "app_version": app_version,
    }
    if progress_callback is not None:
        install_kwargs["progress"] = progress_callback
    try:
        install_runtime(**install_kwargs)
    except (KeyboardInterrupt, SetupCancelled):
        line_callback("")
        line_callback("Runtime setup cancelled. Temporary child processes were terminated safely.")
        return 130

    logs_dir().mkdir(parents=True, exist_ok=True)
    log_path = logs_dir() / "latest.log"
    env = runtime_environment()
    root = str(source_root())
    env["PYTHONPATH"] = root + (os.pathsep + env["PYTHONPATH"] if env.get("PYTHONPATH") else "")
    env["BKWHISPERX_RUNTIME_PROFILE"] = read_state().profile if read_state() else profile
    env["BKWHISPERX_CANCEL_FILE"] = str(controller.cancel_path)
    env["BKWHISPERX_PARENT_PID"] = str(os.getpid())
    env["HF_HUB_DISABLE_PROGRESS_BARS"] = "0"
    if hf_token:
        env["BKWHISPERX_HF_TOKEN"] = hf_token

    proc = subprocess.Popen(
        worker_command(config_path, machine_events=True),
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        encoding="utf-8",
        errors="replace",
        env=env,
        **ManagedProcess.popen_platform_kwargs(hidden=True, new_group=True),
    )
    controller.attach(proc)
    if proc.stdout is None:
        controller.force_stop()
        controller.finish()
        raise RuntimeError("Could not capture transcription worker output.")

    try:
        with log_path.open("w", encoding="utf-8", newline="\n") as log_file:
            for raw_line in proc.stdout:
                line = raw_line.rstrip("\r\n")
                if line.startswith(EVENT_PREFIX):
                    try:
                        payload = json.loads(line[len(EVENT_PREFIX) :])
                        kind = str(payload.pop("type"))
                        event_callback(kind, payload)
                        safe_line = _event_log_line(kind, payload)
                        if safe_line:
                            log_file.write(safe_line + "\n")
                            log_file.flush()
                    except Exception:
                        line_callback(line)
                else:
                    log_file.write(line + "\n")
                    log_file.flush()
                    transfer = _download_event(line)
                    if transfer:
                        event_callback("download_progress", transfer)
                    line_callback(line)
        return proc.wait()
    except KeyboardInterrupt:
        line_callback("")
        line_callback("Stopping BK WhisperX safely…")
        result = controller.stop(graceful_timeout=10.0, force_timeout=3.0)
        if result is None:
            line_callback("The worker did not respond, so its process tree was terminated.")
        else:
            line_callback("BK WhisperX stopped cleanly.")
        return 130
    finally:
        controller.finish()
