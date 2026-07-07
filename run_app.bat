@echo off
setlocal EnableExtensions
cd /d "%~dp0"

if not exist ".venv\Scripts\python.exe" (
    echo [mexc_spread_monitor] Creating virtual environment...
    python -m venv .venv
    if errorlevel 1 (
        echo Python not found. Install Python 3.10+ and add it to PATH.
        pause
        exit /b 1
    )
    echo [mexc_spread_monitor] Installing dependencies...
    call ".venv\Scripts\pip.exe" install -r requirements.txt
    if errorlevel 1 (
        echo pip install failed.
        pause
        exit /b 1
    )
)

echo [mexc_spread_monitor] Starting Streamlit...
echo Open the URL shown below in your browser (usually http://localhost:8501).
echo Close this window to stop the server.
echo.
".venv\Scripts\python.exe" -m streamlit run app.py

pause
