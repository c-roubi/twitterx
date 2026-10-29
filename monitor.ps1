# TwitterX Bot - Simple Monitor (5 minute polling)
# Everything included - just run this!

Write-Host ""
Write-Host "===============================================================" -ForegroundColor Cyan
Write-Host " TwitterX Bot - Monitor Mode" -ForegroundColor Cyan
Write-Host "===============================================================" -ForegroundColor Cyan
Write-Host ""
Write-Host "[*] Starting bot..." -ForegroundColor Yellow
Write-Host "[*] Checking manager tweets every 5 minutes" -ForegroundColor Yellow
Write-Host "[*] Press Ctrl+C to stop" -ForegroundColor Yellow
Write-Host ""

python main.py monitor

if ($LASTEXITCODE -ne 0) {
    Write-Host ""
    Write-Host "[!] Bot error!" -ForegroundColor Red
    Read-Host "Press Enter to exit"
    exit 1
}

Read-Host "Press Enter to exit"
