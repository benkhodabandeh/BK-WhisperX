from __future__ import annotations

import os
import signal
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Any


class ManagedProcess:
    def __init__(self, process: subprocess.Popen[Any]) -> None:
        self.process = process
        self._lock = threading.RLock()

    @staticmethod
    def popen_platform_kwargs(*, hidden: bool = True, new_group: bool = True) -> dict[str, Any]:
        if sys.platform == "win32":
            flags = 0
            if hidden:
                flags |= subprocess.CREATE_NO_WINDOW
            if new_group:
                flags |= subprocess.CREATE_NEW_PROCESS_GROUP
            return {"creationflags": flags}
        return {"start_new_session": new_group}

    def wait(self, timeout: float | None = None) -> int | None:
        try:
            return self.process.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            return None

    def terminate_tree(self, exit_code: int = 130) -> None:
        with self._lock:
            if self.process.poll() is not None:
                return
            if sys.platform == "win32":
                try:
                    subprocess.run(
                        ["taskkill", "/PID", str(self.process.pid), "/T", "/F"],
                        stdout=subprocess.DEVNULL,
                        stderr=subprocess.DEVNULL,
                        timeout=5,
                        creationflags=subprocess.CREATE_NO_WINDOW,
                        check=False,
                    )
                    return
                except Exception:
                    pass
            else:
                try:
                    os.killpg(os.getpgid(self.process.pid), signal.SIGTERM)
                    return
                except Exception:
                    pass
            try:
                self.process.terminate()
            except Exception:
                pass

    def close(self) -> None:
        with self._lock:
            if self.process.poll() is None:
                self.terminate_tree()
                self.wait(3)


class WorkerController:
    def __init__(self, cancel_path: Path) -> None:
        self.cancel_path = cancel_path
        self._lock = threading.RLock()
        self._process: subprocess.Popen[Any] | None = None
        self._guard: ManagedProcess | None = None
        self._cancel_requested = threading.Event()

    def reset(self) -> None:
        with self._lock:
            self.cancel_path.unlink(missing_ok=True)
            self._process = None
            self._guard = None
            self._cancel_requested.clear()

    def attach(self, process: subprocess.Popen[Any]) -> None:
        with self._lock:
            self._process = process
            self._guard = ManagedProcess(process)
            if self._cancel_requested.is_set():
                self._write_cancel_marker()

    def _write_cancel_marker(self) -> None:
        try:
            self.cancel_path.parent.mkdir(parents=True, exist_ok=True)
            self.cancel_path.write_text("cancel\n", encoding="utf-8")
        except OSError:
            pass

    def request_cancel(self) -> None:
        self._cancel_requested.set()
        self._write_cancel_marker()

    @property
    def cancel_requested(self) -> bool:
        return self._cancel_requested.is_set()

    @property
    def active(self) -> bool:
        with self._lock:
            return self._process is not None and self._process.poll() is None

    def wait(self, timeout: float | None = None) -> int | None:
        with self._lock:
            process = self._process
        if process is None:
            return 0
        try:
            return process.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            return None

    def stop(self, graceful_timeout: float = 10.0, force_timeout: float = 3.0) -> int | None:
        self.request_cancel()
        result = self.wait(graceful_timeout)
        if result is not None:
            return result
        self.force_stop()
        return self.wait(force_timeout)

    def force_stop(self) -> None:
        with self._lock:
            guard = self._guard
            process = self._process
        if guard is not None:
            guard.terminate_tree()
        elif process is not None and process.poll() is None:
            try:
                process.terminate()
            except Exception:
                pass

    def finish(self) -> None:
        with self._lock:
            guard = self._guard
            self._guard = None
            self._process = None
        if guard is not None:
            guard.close()
        self.cancel_path.unlink(missing_ok=True)


def wait_with_updates(process: subprocess.Popen[Any], timeout: float, interval: float = 0.1) -> int | None:
    deadline = time.monotonic() + max(0.0, timeout)
    while time.monotonic() < deadline:
        result = process.poll()
        if result is not None:
            return result
        time.sleep(interval)
    return process.poll()
