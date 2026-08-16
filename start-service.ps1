#Requires -Version 5.1
$ErrorActionPreference = "Stop"

$env:PATH = "$env:SystemRoot\System32\WindowsPowerShell\v1.0;$env:PATH"

$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $ScriptDir

if (-not (Test-Path ".venv")) {
    Write-Host "Creating virtual environment..."
    python -m venv .venv
}

Write-Host "Activating virtual environment..."
& ".\.venv\Scripts\Activate.ps1"

Write-Host "Installing dependencies..."
pip install -r requirements.txt -q

if (-not (Test-Path ".env")) {
    Copy-Item ".env.example" ".env"
    Write-Host ".env created from .env.example - edit it to set SECRET_KEY"
}

# Free port 8010 if already in use
$conn = Get-NetTCPConnection -LocalPort 8010 -State Listen -ErrorAction SilentlyContinue
if ($conn) {
    $ownerPid = $conn.OwningProcess | Select-Object -First 1
    Write-Host "Port 8010 in use (PID $ownerPid) - stopping it..."
    Stop-Process -Id $ownerPid -Force -ErrorAction SilentlyContinue
    Start-Sleep -Milliseconds 600
}

Write-Host ""
Write-Host "payments-service running at http://localhost:8010"
Write-Host "Admin panel at              http://localhost:8010/admin  (admin / 1234)"
Write-Host "Docs at                     http://localhost:8010/docs"
Write-Host ""

uvicorn main:app --reload --host 0.0.0.0 --port 8010
