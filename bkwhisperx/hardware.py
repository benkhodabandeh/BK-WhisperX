from __future__ import annotations

import json
import shutil
import subprocess
from dataclasses import dataclass


@dataclass(slots=True)
class HardwareInfo:
    nvidia_present: bool = False
    gpu_name: str = ""
    driver_version: str = ""
    torch_installed: bool = False
    torch_version: str = ""
    torchvision_version: str = ""
    torchaudio_version: str = ""
    torch_cuda_version: str = ""
    cuda_available: bool = False
    cuda_device_name: str = ""
    ffmpeg_path: str = ""
    dependencies_ok: bool = False
    error: str = ""


def detect_nvidia() -> tuple[bool, str, str]:
    exe = shutil.which("nvidia-smi")
    if not exe:
        return False, "", ""
    try:
        result = subprocess.run(
            [exe, "--query-gpu=name,driver_version", "--format=csv,noheader"],
            capture_output=True,
            text=True,
            timeout=8,
            encoding="utf-8",
            errors="replace",
        )
        if result.returncode != 0 or not result.stdout.strip():
            return False, "", ""
        first = result.stdout.strip().splitlines()[0]
        name, _, driver = first.partition(",")
        return True, name.strip(), driver.strip()
    except Exception:
        return False, "", ""


def inspect_torch(
    python_executable: str,
    *,
    full: bool = False,
    env: dict[str, str] | None = None,
) -> HardwareInfo:
    nvidia, gpu, driver = detect_nvidia()
    info = HardwareInfo(nvidia_present=nvidia, gpu_name=gpu, driver_version=driver)
    code = r"""
import json
try:
 import torch, torchvision, torchaudio
 payload = {
  "installed": True,
  "version": torch.__version__,
  "torchvision": torchvision.__version__,
  "torchaudio": torchaudio.__version__,
  "cuda_version": torch.version.cuda or "",
  "cuda_available": bool(torch.cuda.is_available()),
  "device_name": torch.cuda.get_device_name(0) if torch.cuda.is_available() else "",
 }
 if __import__("os").environ.get("BKX_FULL_PROBE") == "1":
  import whisperx, tqdm, imageio_ffmpeg
  from pathlib import Path
  ffmpeg = imageio_ffmpeg.get_ffmpeg_exe()
  if not Path(ffmpeg).is_file():
   raise RuntimeError("Managed FFmpeg binary is missing")
  payload["ffmpeg"] = ffmpeg
  payload["dependencies_ok"] = True
 print(json.dumps(payload))
except Exception as exc:
 print(json.dumps({"installed": False, "error": f"{type(exc).__name__}: {exc}"}))
"""
    try:
        child_env = (env or __import__("os").environ).copy()
        if full:
            child_env["BKX_FULL_PROBE"] = "1"
        result = subprocess.run(
            [python_executable, "-c", code],
            capture_output=True,
            text=True,
            timeout=90,
            encoding="utf-8",
            errors="replace",
            env=child_env,
        )
        payload = json.loads(result.stdout.strip().splitlines()[-1])
        info.torch_installed = bool(payload.get("installed"))
        info.torch_version = str(payload.get("version", ""))
        info.torchvision_version = str(payload.get("torchvision", ""))
        info.torchaudio_version = str(payload.get("torchaudio", ""))
        info.torch_cuda_version = str(payload.get("cuda_version", ""))
        info.cuda_available = bool(payload.get("cuda_available"))
        info.cuda_device_name = str(payload.get("device_name", ""))
        info.ffmpeg_path = str(payload.get("ffmpeg", ""))
        info.dependencies_ok = bool(payload.get("dependencies_ok")) if full else info.torch_installed
        info.error = str(payload.get("error", ""))
    except Exception as exc:
        info.error = f"{type(exc).__name__}: {exc}"
    return info
