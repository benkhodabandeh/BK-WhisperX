# Changelog

## 1.1.0 — 2026-07-13

### Fixed
- Preserve an explicitly selected language such as Persian (`fa`) when loading and running WhisperX.
- Refuse to silently fall back to auto-detection when an installed WhisperX API cannot enforce the requested language.
- Keep auto-detection truly per-file when `auto` is selected.
- Configure UTF-8 Windows console output and reshape RTL runs only for legacy Command Prompt.
- Normalize common Arabic/Persian character variants without altering normal Unicode transcript storage.
- Export TXT, SRT, and VTT with a Windows-friendly UTF-8 BOM.
- Validate CUDA availability after installation instead of assuming a GPU runtime is usable.
- Reject CPU `float16`, which is unsupported for this transcription path.
- Force the correct PyTorch wheel family when switching between CPU and CUDA profiles.
- Select CUDA 12.8, CUDA 12.6, or CPU automatically using conservative NVIDIA driver compatibility thresholds.
- Render Persian/Arabic previews with a display-only bidi pipeline in the CLI while preserving logical Unicode exports.
- Generate missing requested formats even when another output for the same input already exists.
- Write TXT/SRT/VTT/JSON and combined transcripts transactionally to avoid partial output after interruption.
- Preserve an existing combined transcript by selecting a unique filename when overwrite is disabled.
- Detect CTranslate2-style CUDA out-of-memory failures and retry with smaller batches.
- Validate the exact Torch, TorchVision, TorchAudio, CUDA, WhisperX, and FFmpeg runtime in one cached probe.
- Recover from broken or moved project virtual environments without repeatedly attempting to use them.
- Remove duplicate space-named batch aliases and keep one canonical launcher per interface.
- Consolidate duplicate runtime dependency manifests into one packaged source of truth.

### Added
- Runtime selector for automatic, CUDA 12.8, CUDA 12.6, and CPU-only PyTorch profiles.
- Persistent runtime and model cache under the user profile so app upgrades do not redownload healthy components.
- First-launch automated Python, PyTorch, WhisperX, and support-library setup using an isolated managed runtime.
- Built-in GitHub release update notifications.
- Windows Inno Setup installer, portable build, CI, and tag-driven release workflow.
- Structured worker events, progress reporting, persistent logs, settings, and runtime health checks.
- GPU out-of-memory retry with progressively smaller batch sizes.
- SHA-256 verification and safe archive extraction for the pinned runtime manager.
- Runtime setup locking and log rotation to prevent concurrent setup corruption and unbounded logs.
- Legacy `.venv`, Hugging Face, and Torch cache reuse for smoother upgrades.
- Secret-safe worker handoff so Hugging Face tokens are not written to temporary job files.
- Optional Authenticode signing and release checksums.
- Complete-segment live previews, processing phases, and model/package transfer progress.
- Safe/Balanced/Maximum presets, initial context prompt, hotwords/glossary, TXT layout, and subtitle line controls.
- CLI system diagnostics, disk/cache reporting, and confirmed cache cleanup.
- Cancellable runtime installation and separate normal repair/full reinstall actions.
- Cached update checks, bounded queues/logs, and transcript-free machine-event logging.
- Fast-start onedir installer launchers plus single-file portable launchers, DPI manifest, and Windows version metadata.
- Full-SHA pinned GitHub Actions, macOS test coverage, timeouts, concurrency controls, and package-build validation.

## 1.0.1 — 2026-07-08
- Previous guided CLI release.