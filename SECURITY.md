# Security

Report security issues privately to the repository owner rather than opening a public issue containing sensitive details.

BK WhisperX processes media locally. The launcher contacts GitHub only for cached update checks and downloads the official `uv` runtime manager, Python runtime packages, PyTorch wheels, WhisperX dependencies, and model assets required for transcription. Hugging Face tokens are passed to the worker through the process environment and are not written to temporary job configuration files. Live transcript machine events are excluded from `latest.log`. The pinned `uv` archive is verified by SHA-256 and rejects path traversal, symbolic links, hard links, and special-device entries before extraction.

The runtime and downloaded models are stored under `%LOCALAPPDATA%\BK WhisperX` on Windows. Uninstalling the launcher intentionally preserves this directory so future upgrades can reuse the runtime and model cache. Delete that folder manually to remove all local runtime data, models, settings, and logs.