# TwitterX Bot - Complete Startup (PowerShell)
# ngrok token is embedded in the code, no setup needed!

Write-Host ""
Write-Host "===============================================================" -ForegroundColor Cyan
Write-Host " TwitterX Bot - Auto-Startup" -ForegroundColor Cyan
Write-Host "===============================================================" -ForegroundColor Cyan
Write-Host ""

# Run the bot
python run.py

if ($LASTEXITCODE -ne 0) {
    Write-Host ""
    Write-Host "[!] Bot failed!" -ForegroundColor Red
    Read-Host "Press Enter to exit"
    exit 1
}

Read-Host "Press Enter to exit"
