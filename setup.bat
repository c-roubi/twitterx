@echo off
REM TwitterX Bot Setup - Windows Batch Script

cls
echo.
echo ===============================================================
echo  TwitterX Bot - Setup ^& Configuration
echo ===============================================================
echo.

REM Check Python
python --version >nul 2>&1
if errorlevel 1 (
    echo [-] Python not found!
    echo [*] Install from: https://www.python.org/downloads/
    pause
    exit /b 1
)

for /f "tokens=*" %%i in ('python --version') do set PYTHON_VER=%%i
echo [+] %PYTHON_VER%

REM Install dependencies
echo.
echo [*] Installing dependencies...
python -m pip install --upgrade pip -q
python -m pip install -r requirements.txt -q

if errorlevel 1 (
    echo [-] Failed to install dependencies
    pause
    exit /b 1
)

echo [+] Dependencies installed

REM Install Playwright
echo.
echo [*] Installing Playwright browsers...
python -m playwright install chromium -q

if errorlevel 1 (
    echo [-] Failed to install Playwright
    pause
    exit /b 1
)

echo [+] Playwright ready

REM Create directories
if not exist "data" mkdir data
echo [+] Data directory initialized

REM Success
echo.
echo ===============================================================
echo  SETUP COMPLETE!
echo ===============================================================
echo.
echo Next steps:
echo   1. Add accounts:
echo      python main.py add --handle @account1 --username "Name"
echo   2. Login:
echo      python main.py login acc_001
echo   3. Start monitoring:
echo      python main.py monitor
echo.
pause
