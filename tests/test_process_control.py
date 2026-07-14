from __future__ import annotations

import subprocess
import sys
from pathlib import Path

from bkwhisperx.process_control import ManagedProcess, WorkerController


def test_worker_controller_force_stops_process(tmp_path: Path) -> None:
    controller = WorkerController(tmp_path / "cancel.flag")
    process = subprocess.Popen(
        [sys.executable, "-c", "import time; time.sleep(60)"],
        **ManagedProcess.popen_platform_kwargs(hidden=True, new_group=True),
    )
    controller.attach(process)
    controller.request_cancel()
    assert controller.cancel_path.exists()
    controller.force_stop()
    assert controller.wait(5) is not None
    controller.finish()
    assert not controller.cancel_path.exists()
