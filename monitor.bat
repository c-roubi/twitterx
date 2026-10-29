@echo off
REM TwitterX Bot - Simple Monitor (5 minute polling)
REM Everything included - just run this!

cls
echo.
echo ===============================================================
echo  TwitterX Bot - Monitor Mode
echo ===============================================================
echo.
echo [*] Starting bot...
echo [*] Checking manager tweets every 5 minutes
echo [*] Press Ctrl+C to stop
echo.

python main.py monitor

if errorlevel 1 (
    echo.
    echo [!] Bot error!
    pause
    exit /b 1
)

pause
