param(
    [string]$Version = "1.1.0",
    [switch]$SkipTests
)

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest
$Root = Split-Path -Parent $PSScriptRoot
Set-Location $Root

$VersionMatch = Select-String -Path "pyproject.toml" -Pattern '^version\s*=\s*"([^"]+)"$' | Select-Object -First 1
if (-not $VersionMatch) { throw "Could not read the project version from pyproject.toml" }
$ProjectVersion = $VersionMatch.Matches[0].Groups[1].Value
if ($Version -ne $ProjectVersion) {
    throw "Build version $Version does not match project version $ProjectVersion"
}

function Step([string]$Message) {
    Write-Host "`n==> $Message" -ForegroundColor Cyan
}

function Find-SignTool {
    $command = Get-Command signtool.exe -ErrorAction SilentlyContinue
    if ($command) { return $command.Source }

    $kitsRoot = Join-Path ${env:ProgramFiles(x86)} "Windows Kits\10\bin"
    if (Test-Path $kitsRoot) {
        return Get-ChildItem -Path $kitsRoot -Filter signtool.exe -Recurse -ErrorAction SilentlyContinue |
            Where-Object { $_.FullName -match '\\x64\\signtool\.exe$' } |
            Sort-Object FullName -Descending |
            Select-Object -First 1 -ExpandProperty FullName
    }
    return $null
}

function Sign-Artifact([string]$Path) {
    if (-not $env:BKX_SIGN_CERT_SHA1) { return }
    $SignTool = Find-SignTool
    if (-not $SignTool) {
        throw "BKX_SIGN_CERT_SHA1 is set, but signtool.exe was not found. Install the Windows SDK."
    }
    $TimestampUrl = if ($env:BKX_TIMESTAMP_URL) { $env:BKX_TIMESTAMP_URL } else { "http://timestamp.digicert.com" }
    & $SignTool sign /sha1 $env:BKX_SIGN_CERT_SHA1 /fd SHA256 /tr $TimestampUrl /td SHA256 $Path
    if ($LASTEXITCODE -ne 0) { throw "Code signing failed for $Path" }
}

Step "Preparing build environment"
python -m pip install --upgrade pip
python -m pip install -e ".[dev]"

if (-not $SkipTests) {
    Step "Running tests and static checks"
    python -m compileall -q bkwhisperx bk_whisperx.py
    python -m pytest
    python -m ruff check bkwhisperx tests *.py
}

Remove-Item build, dist, release -Recurse -Force -ErrorAction SilentlyContinue
New-Item -ItemType Directory -Path release | Out-Null

$CommonData = @(
    "--add-data", "bk_whisperx.py;."
    "--add-data", "bkwhisperx;bkwhisperx"
)
$LauncherIncludes = @(
    "--hidden-import", "arabic_reshaper"
    "--hidden-import", "bidi.algorithm"
    "--manifest", "assets\app.manifest"
    "--version-file", "assets\version_info.txt"
)
$LauncherExcludes = @(
    "--exclude-module", "torch"
    "--exclude-module", "torchaudio"
    "--exclude-module", "torchvision"
    "--exclude-module", "whisperx"
    "--exclude-module", "transformers"
    "--exclude-module", "faster_whisper"
    "--exclude-module", "pyannote"
    "--exclude-module", "imageio_ffmpeg"
)

Step "Building fast-start installer launchers"
python -m PyInstaller --noconfirm --clean --onedir --console `
    --distpath dist\installer `
    --name BK-WhisperX-CLI `
    @CommonData `
    @LauncherIncludes `
    @LauncherExcludes `
    cli_entry.py

Step "Building single-file portable launchers"
python -m PyInstaller --noconfirm --clean --onefile --console `
    --distpath dist\portable `
    --name BK-WhisperX-CLI `
    @CommonData `
    @LauncherIncludes `
    @LauncherExcludes `
    cli_entry.py

Step "Signing launcher executables when a certificate is configured"
Sign-Artifact "dist\portable\BK-WhisperX-CLI.exe"

Step "Smoke-testing the portable CLI"
& "dist\portable\BK-WhisperX-CLI.exe" --version
if ($LASTEXITCODE -ne 0) { throw "Portable CLI smoke test failed" }

Step "Creating portable package"
$Portable = Join-Path $Root "build\portable\BK-WhisperX-$Version"
New-Item -ItemType Directory -Path $Portable -Force | Out-Null
Copy-Item dist\portable\BK-WhisperX-CLI.exe, README.md, CHANGELOG.md, LICENSE -Destination $Portable
Compress-Archive -Path $Portable -DestinationPath "release\BK-WhisperX-Portable-$Version.zip" -CompressionLevel Optimal

Step "Building Windows installer"
$PossibleISCC = @(
    "${env:ProgramFiles(x86)}\Inno Setup 6\ISCC.exe",
    "$env:ProgramFiles\Inno Setup 6\ISCC.exe"
)
$ISCC = $PossibleISCC | Where-Object { Test-Path $_ } | Select-Object -First 1
if (-not $ISCC) {
    throw "Inno Setup 6 was not found. Install it with: winget install JRSoftware.InnoSetup"
}
& $ISCC "/DMyAppVersion=$Version" "installer\BKWhisperX.iss"
if ($LASTEXITCODE -ne 0) { throw "Inno Setup failed with exit code $LASTEXITCODE" }
Sign-Artifact "release\BK-WhisperX-Setup-$Version.exe"

Step "Writing checksums"
$Artifacts = Get-ChildItem release -File | Where-Object { $_.Extension -in ".exe", ".zip" }
$ChecksumLines = foreach ($Artifact in $Artifacts) {
    $Hash = (Get-FileHash $Artifact.FullName -Algorithm SHA256).Hash.ToLowerInvariant()
    "$Hash  $($Artifact.Name)"
}
$ChecksumLines | Set-Content -Path "release\SHA256SUMS.txt" -Encoding ascii

Step "Build complete"
Get-ChildItem release | Select-Object Name, Length, LastWriteTime