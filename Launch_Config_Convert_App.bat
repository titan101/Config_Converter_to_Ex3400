@echo off
setlocal

cd /d "%~dp0"
set APP_URL=http://127.0.0.1:5050

echo ========================================
echo EX3400 Config Converter App
echo ========================================
echo.

if not exist ".venv" (
    echo Creating local virtual environment...
    py -3 -m venv .venv >nul 2>nul
    if errorlevel 1 (
        python -m venv .venv
    )
    if errorlevel 1 (
        echo Could not create .venv.
        echo Install Python 3 with venv support, then run this launcher again.
        echo.
        pause
        exit /b 1
    )
)

echo Checking if the app is already running...
powershell -NoProfile -ExecutionPolicy Bypass -Command "try { $r = Invoke-WebRequest -UseBasicParsing '%APP_URL%/health' -TimeoutSec 2; if ($r.StatusCode -eq 200) { exit 0 } else { exit 1 } } catch { exit 1 }"
if not errorlevel 1 (
    echo App is already running.
    echo Opening %APP_URL%
    start "" "%APP_URL%"
    echo.
    pause
    exit /b 0
)

echo Checking Python dependencies...
".venv\Scripts\python.exe" -m pip install --upgrade pip
if errorlevel 1 (
    echo.
    echo Pip upgrade failed.
    echo.
    pause
    exit /b 1
)
".venv\Scripts\python.exe" -m pip install -r requirements.txt
if errorlevel 1 (
    echo.
    echo Dependency install failed.
    echo.
    pause
    exit /b 1
)

echo.
echo Starting app...
echo The browser will open automatically:
echo %APP_URL%
echo.
echo Press Ctrl+C in this window to stop the app.
echo.

set CONFIG_CONVERT_OPEN_BROWSER=1
set CONFIG_CONVERT_PORT=5050
".venv\Scripts\python.exe" app.py

echo.
echo App stopped.
pause
