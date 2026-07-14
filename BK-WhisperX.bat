@echo off
setlocal EnableExtensions DisableDelayedExpansion
chcp 65001 >nul
pushd "%~dp0" >nul 2>&1 || exit /b 1

if exist "%~dp0BK-WhisperX.exe" (
    "%~dp0BK-WhisperX.exe" %*
    set "EXIT_CODE=%ERRORLEVEL%"
    popd
    exit /b %EXIT_CODE%
)

if not exist "%~dp0bk_whisperx.py" (
    echo ERROR: bk_whisperx.py was not found.
    pause
    popd
    exit /b 1
)

call :find_python
if not defined BKX_PYTHON_CMD call :install_python

if not defined BKX_PYTHON_CMD (
    echo.
    echo ERROR: Python 3.10 or newer could not be found or installed.
    echo Install Python 3.11 from python.org, then run this launcher again.
    pause
    popd
    exit /b 1
)

%BKX_PYTHON_CMD% "%~dp0bk_whisperx.py" %*
set "EXIT_CODE=%ERRORLEVEL%"
popd
exit /b %EXIT_CODE%

:find_python
set "BKX_PYTHON_CMD="

for %%V in (3.11 3.12 3.10) do (
    py -%%V -c "import sys; raise SystemExit(0 if sys.version_info >= (3,10) else 1)" >nul 2>&1
    if not errorlevel 1 (
        set "BKX_PYTHON_CMD=py -%%V"
        exit /b 0
    )
)

for %%P in (
    "%LOCALAPPDATA%\Programs\Python\Python311\python.exe"
    "%LOCALAPPDATA%\Programs\Python\Python312\python.exe"
    "%LOCALAPPDATA%\Programs\Python\Python310\python.exe"
    "%ProgramFiles%\Python311\python.exe"
    "%ProgramFiles%\Python312\python.exe"
    "%ProgramFiles%\Python310\python.exe"
) do (
    if exist "%%~fP" (
        "%%~fP" -c "import sys; raise SystemExit(0 if sys.version_info >= (3,10) else 1)" >nul 2>&1
        if not errorlevel 1 (
            set BKX_PYTHON_CMD="%%~fP"
            exit /b 0
        )
    )
)

where python >nul 2>&1
if not errorlevel 1 (
    python -c "import sys; raise SystemExit(0 if sys.version_info >= (3,10) else 1)" >nul 2>&1
    if not errorlevel 1 (
        set "BKX_PYTHON_CMD=python"
        exit /b 0
    )
)

where python3 >nul 2>&1
if not errorlevel 1 (
    python3 -c "import sys; raise SystemExit(0 if sys.version_info >= (3,10) else 1)" >nul 2>&1
    if not errorlevel 1 (
        set "BKX_PYTHON_CMD=python3"
        exit /b 0
    )
)

exit /b 1

:install_python
where winget >nul 2>&1
if errorlevel 1 exit /b 1

echo.
echo Python 3.10 or newer was not found.
choice /C YN /N /M "Install Python 3.11 for the current Windows user now? [Y/N] "
if errorlevel 2 exit /b 1
echo Installing Python 3.11 for the current Windows user...
echo.

winget install --id Python.Python.3.11 --exact --source winget --scope user --silent --accept-package-agreements --accept-source-agreements
if errorlevel 1 exit /b 1

call :find_python
if defined BKX_PYTHON_CMD exit /b 0
exit /b 1
