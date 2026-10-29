@echo off
REM TwitterX Bot - Complete Startup (Windows)
REM Requires: NGROK_AUTHTOKEN environment variable set

cls
echo.
echo ===============================================================
echo  TwitterX Bot - Auto-Startup with ngrok
echo ===============================================================
echo.

REM Check ngrok token
if "%NGROK_AUTHTOKEN%"=="" (
    echo [!] NGROK_AUTHTOKEN not set!
    echo.
    echo [*] Set it with:
    echo     set NGROK_AUTHTOKEN=your_token_here
    echo.
    echo [*] Then run this script again
    echo.
    pause
    exit /b 1
)

echo [+] ngrok token found
echo.

REM Run the bot
python run.py --port 8000

if errorlevel 1 (
    echo.
    echo [!] Bot failed!
    pause
    exit /b 1
)

pause
