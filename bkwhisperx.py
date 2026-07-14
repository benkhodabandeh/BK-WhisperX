#!/usr/bin/env python3
"""BK WhisperX – CLI‑only transcription.

Command‑line tool for fast, local, auto‑managed transcription with WhisperX.
Features:
- Managed runtime installation (CUDA/CPU)
- All WhisperX options: model, language, diarization, hotwords, export formats
- Interactive wizard, quick‑transcribe presets, saved configs
- Safe overwrite control, live‑preview, progress feedback
- Full Persian/English bilingual UI
- Runtime, diagnostics, cache management utilities

Usage:
    bkwhisperx [OPTIONS] [INPUTS...]

Exit codes:
    0  – Success or user exit
    1  – Runtime error or transcription failure
    2  – Configuration error or invalid command
    130 – KeyboardInterrupt (graceful exit)

For help:
    bkwhisperx -h

Installation:
    uv tool install bkwhisperx

GitHub: https://github.com/m-bain/whisperX (BK WhisperX maintains its own fork)

Authors:
    Created by BK WhisperX Team

License:
    MIT
"""

print("BK WhisperX CLI tool initialized")
if __name__ == "__main__":
    import sys
    sys.path.insert(0, "bkwhisperx")
    from bkwhisperx.cli import main
    sys.exit(main())
