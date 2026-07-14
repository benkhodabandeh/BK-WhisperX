# BK WhisperX

## راهنمای فارسی (Persian Guide)

**BK WhisperX** یک ابزار خط فرمان برای رونویسی محلی است که از مدل‌های WhisperX استفاده می‌کند. این ابزار قابلیت‌های زیر را دارد:

- تبدیل صدا و ویدیو به متن
- استخراج زیرنویس (SRT/VTT) و متن تمیز (TXT)
- پشتیبانی از فارسی
- استفاده از (CUDA)
- نصب خودکار محیط اجرای Python و کتابخانه‌ها

### نصب سریع (Quick Install)

1. فایل `Source Code (zip)` را از صفحه Releases دانلود کنید
2. فایل زیپ را در محلی امن و ثابت استخراج کنید.
3. فایل BK-WhisperX.bat را اجرا کنید.
4.  در اولین اجرا، محیط اجرای Python 3.11، PyTorch، WhisperX و FFmpeg نصب می‌شوند

##

### مدل‌های پیشنهادی برای فارسی

| مدل | توضیح |
|-----|-------|
| `large-v3` | بهترین دقت برای فارسی |
| `large-v2` | گزینه قدیمی‌تر با کیفیت خوب |
| **نکته** | از `distil-large-v3` استفاده نکنید، فقط انگلیسی است |

---

# BK WhisperX (English)

**BK WhisperX** is a local WhisperX transcription CLI tool with managed runtime installation.

It transcribes audio and video, exports clean text and subtitles, supports Persian/Farsi, and can use an NVIDIA GPU. The machine-learning runtime is kept separate from the application so upgrades do not repeatedly download models and libraries.

> BK WhisperX is an independent launcher around WhisperX. It is not an official OpenAI, WhisperX, PyTorch, Hugging Face, pyannote, FFmpeg, or SYSTRAN product.

## What's new in v1.1.0

- Fixed manual language handling. Selecting `fa` now reaches both WhisperX model loading and transcription.
- Fixed misleading fallback behavior. The app refuses to silently auto-detect when the installed API cannot honor a manual language.
- Added correct per-file auto-detection for multilingual models.
- Prevented `distil-large-v3` from being used for Persian or auto-detection because that checkpoint is English-only.
- Added automatic, CUDA 12.8, CUDA 12.6, and CPU-only runtime profiles.
- Added UTF-8 console setup, legacy Command Prompt RTL shaping, and Windows-friendly UTF-8 TXT/SRT/VTT exports.
- Added a persistent managed runtime, legacy-cache reuse, verified runtime-manager download, update checks, installer, portable build, CI, and automatic GitHub release builds.
- Added atomic output writes, per-format resume, non-destructive combined transcripts, setup cancellation, exact runtime validation, and privacy-safe event logs.
- Added per-file status/language/progress/output queue, performance presets, glossary/hotwords, configurable TXT/subtitle layout, diagnostics, and cache management.

## Quick Start

### Windows Installation

1. Open the latest GitHub release.
2. Download `BK-WhisperX-Setup-<version>.exe`.
3. Run the installer and launch **BK WhisperX CLI**.
4. In the Runtime bar, choose one of:
   - **Automatic**: chooses CUDA 12.8, CUDA 12.6, or CPU from the detected NVIDIA driver;
   - **NVIDIA GPU — CUDA 12.8**: recommended for current NVIDIA GPUs;
   - **NVIDIA GPU — CUDA 12.6 compatibility**: alternate compatible PyTorch build;
   - **CPU only**: maximum compatibility.
5. Select media, language, model, output formats, and start.

Estimated download sizes for first launch:
- Python 3.11 runtime: ~25 MB
- PyTorch (CUDA): ~2.5 GB
- PyTorch (CPU): ~800 MB
- WhisperX: ~100 MB
- FFmpeg: ~50 MB
- Models: 1-4 GB depending on model size

### Run from Source

```powershell
git clone https://github.com/benkhodabandeh/BK-WhisperX.git
cd BK-WhisperX
python bk_whisperx.py
```

On macOS/Linux:

```bash
chmod +x bk-whisperx.sh
./bk-whisperx.sh
```

## Persian Transcription

For Persian audio, use:

```text
Model: large-v3 (recommended) or large-v2
Language: fa — Persian / Farsi
Task: transcribe
```

Do **not** use `distil-large-v3` for Persian. It is an English-only distilled checkpoint. BK WhisperX blocks this invalid combination.

Use `auto` only with a multilingual model such as `large-v3`, `large-v2`, `medium`, `small`, `base`, or `tiny`. Auto mode detects the language separately for each input file.

## Persian Output Display

- Windows Terminal and Command Prompt use display-only visual shaping because Windows console bidi layout remains incomplete.
- macOS/Linux terminals use logical Unicode by default. Override any platform with `BKWHISPERX_RTL_MODE=visual` or `BKWHISPERX_RTL_MODE=logical`.
- TXT, SRT, and VTT are saved as UTF-8 with BOM for reliable opening in common Windows applications.
- JSON remains standard UTF-8 without BOM.

## CLI Usage

### Quick Transcribe (Persian)

```powershell
python bk_whisperx.py -i "D:\media\lecture.m4a" -o "D:\media\out" --model large-v3 --language fa --task transcribe --device auto --format txt
```

### Folder Batch Processing

```powershell
python bk_whisperx.py --folder "D:\media" --recursive --language fa --model large-v3 --format all --overwrite
```

### Force CUDA Processing

```powershell
python bk_whisperx.py -i "D:\media\interview.mp4" --language fa --device cuda --format srt
```

### Runtime Management

```powershell
# Install runtime profiles
python bk_whisperx.py --setup cuda128
python bk_whisperx.py --setup cuda126
python bk_whisperx.py --setup cpu

# Repair the currently selected runtime
python bk_whisperx.py --repair

# System diagnostics
python bk_whisperx.py --diagnostics

# Clear caches
python bk_whisperx.py --clear-cache downloads
python bk_whisperx.py --clear-cache models
```

### Advanced Options

```powershell
# Context prompt and hotwords
python bk_whisperx.py -i "D:\media\lecture.m4a" --language fa `
  --initial-prompt "درس فلسفه؛ نام استاد: ..." --hotwords "هگل, پدیدارشناسی" `
  --preset balanced --txt-layout segments --format srt --subtitle-max-chars 42

# Force terminal display behavior
python bk_whisperx.py --rtl-mode visual
```

## Models

| Model | Language support | Typical use |
|---|---|---|
| `tiny` | Multilingual | Quick tests, weak CPU |
| `base` | Multilingual | Lightweight jobs |
| `small` | Multilingual | Balanced CPU or low VRAM |
| `medium` | Multilingual | Better accuracy with moderate resources |
| `large-v2` | Multilingual | High-quality established workflows |
| `large-v3` | Multilingual | Recommended accuracy, including Persian |
| `distil-large-v3` | **English only** | Faster English transcription |

Larger models improve accuracy but require more memory. On CUDA, the default compute type is `float16`; on CPU it is `int8`. Automatic runtime selection uses conservative NVIDIA driver thresholds; automatic batch size uses conservative GPU-memory tiers, and CUDA out-of-memory errors retry with smaller batches.

## Export Formats

| Format | Purpose |
|---|---|
| TXT | Clean transcript text |
| SRT | Subtitles for editors and players |
| VTT | Web captions |
| JSON | Full structured result |

Selecting SRT, VTT, or JSON automatically enables timestamps/alignment. If forced alignment is unavailable for a language, the app retains Whisper segment timestamps instead of discarding the transcription.

## Speaker Diarization

Speaker labels require:

1. a Hugging Face account and access token;
2. accepted terms for the required pyannote models;
3. timestamps enabled.

Pass the token via `HF_TOKEN` environment variable or `--hf-token` option.

## Persistent Upgrade Behavior

The application is installed under:

```text
%LOCALAPPDATA%\Programs\BK WhisperX
```

Reusable data is stored separately under:

```text
%LOCALAPPDATA%\BK WhisperX\runtime
%LOCALAPPDATA%\BK WhisperX\models
%LOCALAPPDATA%\BK WhisperX\cache
%LOCALAPPDATA%\BK WhisperX\logs
```

Installing a newer application version replaces the launcher while preserving the managed runtime and downloaded models. A dependency is downloaded again only when the selected runtime changes, the dependency specification changes, or the health check fails. Existing standard Hugging Face and Torch caches under the user profile are detected and reused, so models downloaded by v1.0.x are not needlessly fetched again.

When running from source, an existing project-local `.venv` is reused in place and repaired only when required. Packaged installer upgrades use the persistent managed runtime above. Python virtual environments from unrelated or moved folders are not copied because relocated environments are not reliable.

## Build the Windows Release Locally

Requirements:

- Windows 10/11 x64;
- Python 3.11;
- Inno Setup 6.

Then run:

```powershell
winget install JRSoftware.InnoSetup
.\scripts\build_windows.ps1 -Version 1.1.0
```

Outputs:

```text
release\BK-WhisperX-Setup-1.1.0.exe
release\BK-WhisperX-Portable-1.1.0.zip
```

Pushing a tag such as `v1.1.0` runs `.github/workflows/release.yml`, tests the project, builds both launchers, creates the installer and portable ZIP, writes SHA-256 checksums, and attaches them to a GitHub release. If `BKX_SIGN_CERT_SHA1` is configured on a local Windows build machine, the build script also Authenticode-signs both launchers and the installer.

## Troubleshooting

### The summary says CPU although an NVIDIA GPU is installed

Open the Runtime selector and install **NVIDIA GPU — CUDA 12.8**, then confirm the runtime status shows a CUDA version and GPU name. Selecting `device=cuda` intentionally fails if PyTorch cannot access the GPU.

### Installation was interrupted by an internet outage

Launch the installer again and click **Install / repair**. The package and model caches are persistent, so completed downloads are reused where supported.

### Persian was converted to English

Confirm all three settings:

```text
Model: large-v3 or large-v2
Language: fa
Task: transcribe
```

`translate` intentionally produces English. `distil-large-v3` is English-only and is rejected for Persian.

### Persian terminal preview still looks awkward

Try `--rtl-mode visual` (pre-shaped display) or `--rtl-mode logical` (terminal-native bidi). Windows defaults to visual mode. Transcript files remain correct logical Unicode regardless of the preview mode.

### CUDA out of memory

Use a smaller model, choose `int8`, or set a smaller batch size. Automatic mode retries CUDA OOM failures with progressively smaller batches.

### Logs

```text
%LOCALAPPDATA%\BK WhisperX\logs\runtime-setup.log
%LOCALAPPDATA%\BK WhisperX\logs\latest.log
```

## Privacy

Media is processed locally. Network access is used for runtime/package installation, model downloads, and the GitHub release update check. Live transcript event payloads are not written to `latest.log`. Review the licenses and terms of WhisperX, PyTorch, Hugging Face-hosted models, pyannote, FFmpeg, and other dependencies.

## License

BK WhisperX is released under the MIT License. See [LICENSE](LICENSE). Third-party packages and models retain their own licenses and terms.
