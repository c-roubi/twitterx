# TwitterX Bot Setup - PowerShell Script
# Run as: powershell -ExecutionPolicy Bypass -File setup.ps1

Write-Host ""
Write-Host "================================================================="
Write-Host " TwitterX Bot - Setup & Configuration" -ForegroundColor Cyan
Write-Host "=================================================================" -ForegroundColor Cyan
Write-Host ""

# Check Python
$pythonExists = $null -ne (python --version 2>&1)
if (-not $pythonExists) {
    Write-Host "[-] Python not found!" -ForegroundColor Red
    Write-Host "[*] Install from: https://www.python.org/downloads/"
    Read-Host "Press Enter to exit"
    exit 1
}

$pythonVersion = python --version 2>&1
Write-Host "[+] $pythonVersion" -ForegroundColor Green

# Install dependencies
Write-Host ""
Write-Host "[*] Installing dependencies..." -ForegroundColor Yellow
python -m pip install --upgrade pip | Out-Null
python -m pip install -r requirements.txt | Out-Null

if ($LASTEXITCODE -ne 0) {
    Write-Host "[-] Failed to install dependencies" -ForegroundColor Red
    Read-Host "Press Enter to exit"
    exit 1
}

Write-Host "[+] Dependencies installed" -ForegroundColor Green

# Install Playwright
Write-Host ""
Write-Host "[*] Installing Playwright browsers..." -ForegroundColor Yellow
python -m playwright install chromium | Out-Null

if ($LASTEXITCODE -ne 0) {
    Write-Host "[-] Failed to install Playwright" -ForegroundColor Red
    Read-Host "Press Enter to exit"
    exit 1
}

Write-Host "[+] Playwright ready" -ForegroundColor Green

# Create directories
if (-not (Test-Path "data")) {
    New-Item -ItemType Directory -Path "data" | Out-Null
}
Write-Host "[+] Data directory initialized" -ForegroundColor Green

# Success
Write-Host ""
Write-Host "================================================================="
Write-Host " SETUP COMPLETE!" -ForegroundColor Green
Write-Host "================================================================="
Write-Host ""
Write-Host "Next steps:" -ForegroundColor Cyan
Write-Host "  1. Add accounts:"
Write-Host "     python main.py add --handle @account1 --username `"Name`""
Write-Host "  2. Login:"
Write-Host "     python main.py login acc_001"
Write-Host "  3. Start monitoring:"
Write-Host "     python main.py monitor"
Write-Host ""
Read-Host "Press Enter to exit"
