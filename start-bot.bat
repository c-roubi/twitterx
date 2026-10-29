@echo off
REM TwitterX Bot - Complete Startup (Windows)
REM ngrok token is embedded in the code, no setup needed!

cls
echo.
echo ===============================================================
echo  TwitterX Bot - Auto-Startup
echo ===============================================================
echo.

REM Run the bot
python run.py

if errorlevel 1 (
    echo.
    echo [!] Bot failed!
    pause
    exit /b 1
)

pause
