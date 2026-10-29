# TwitterX Bot - Complete Startup (PowerShell)
# Requires: NGROK_AUTHTOKEN environment variable set

Write-Host ""
Write-Host "===============================================================" -ForegroundColor Cyan
Write-Host " TwitterX Bot - Auto-Startup with ngrok" -ForegroundColor Cyan
Write-Host "===============================================================" -ForegroundColor Cyan
Write-Host ""

# Check ngrok token
if (-not $env:NGROK_AUTHTOKEN) {
    Write-Host "[!] NGROK_AUTHTOKEN not set!" -ForegroundColor Red
    Write-Host ""
    Write-Host "[*] Set it with:" -ForegroundColor Yellow
    Write-Host '    $env:NGROK_AUTHTOKEN = "your_token_here"' -ForegroundColor Yellow
    Write-Host ""
    Write-Host "[*] Then run this script again" -ForegroundColor Yellow
    Write-Host ""
    Read-Host "Press Enter to exit"
    exit 1
}

Write-Host "[+] ngrok token found" -ForegroundColor Green
Write-Host ""

# Run the bot
python run.py --port 8000

if ($LASTEXITCODE -ne 0) {
    Write-Host ""
    Write-Host "[!] Bot failed!" -ForegroundColor Red
    Read-Host "Press Enter to exit"
    exit 1
}

Read-Host "Press Enter to exit"
