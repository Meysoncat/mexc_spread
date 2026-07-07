@echo off
setlocal EnableExtensions
cd /d "%~dp0"

if not exist ".venv\Scripts\python.exe" (
    echo [mexc_spread_monitor] Creating virtual environment...
    python -m venv .venv
    if errorlevel 1 (
        echo Python not found. Install Python 3.10+ and add to PATH.
        pause
        exit /b 1
    )
)

echo [mexc_spread_monitor] Installing Python dependencies...
call ".venv\Scripts\pip.exe" install -q -r requirements.txt
if errorlevel 1 (
    echo pip install failed.
    pause
    exit /b 1
)

echo [mexc_spread_monitor] frontend: npm install ^(подтянуть package.json, в т.ч. lightweight-charts^)...
pushd frontend
call npm install
if errorlevel 1 (
    echo npm install failed. Install Node.js LTS from https://nodejs.org/
    popd
    pause
    exit /b 1
)
popd

if not exist "node_modules" (
    echo [mexc_spread_monitor] npm install in project root ^(concurrently^)...
    call npm install
    if errorlevel 1 (
        echo npm install failed at repo root. Install Node.js LTS.
        pause
        exit /b 1
    )
)

echo.
echo [mexc_spread_monitor] API + Vite в одном окне. Остановка: Ctrl+C
echo     UI:  http://localhost:5173
echo     API: http://127.0.0.1:8000/api/health
echo.

call npm run dev:modern

echo.
pause
