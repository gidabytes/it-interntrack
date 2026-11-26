<#
.SYNOPSIS
    Start the IT InternTrack API on http://localhost:8000
.DESCRIPTION
    Calls the virtual environment's python directly, so no PowerShell
    execution-policy change is needed.
.EXAMPLE
    .\scripts\windows\start-api.ps1
.EXAMPLE
    .\scripts\windows\start-api.ps1 -Port 8001
#>
[CmdletBinding()]
param([int]$Port = 8000)

$ErrorActionPreference = 'Stop'
$Root    = Split-Path -Parent (Split-Path -Parent $PSScriptRoot)
$Backend = Join-Path $Root 'backend'
$VenvPython = Join-Path $Backend '.venv\Scripts\python.exe'

if (-not (Test-Path $VenvPython)) {
    Write-Host "Not set up yet. Run .\scripts\windows\setup.ps1 first." -ForegroundColor Red
    exit 1
}
if (-not (Test-Path (Join-Path $Backend '.env'))) {
    Write-Host "backend\.env is missing. Run .\scripts\windows\setup.ps1 first." -ForegroundColor Red
    exit 1
}

# A port already in use is the most common startup failure; name the culprit
# rather than letting the user read a bare WinError 10048.
$inUse = Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue
if ($inUse) {
    $owner = (Get-Process -Id $inUse[0].OwningProcess -ErrorAction SilentlyContinue).ProcessName
    Write-Host "Port $Port is already in use by '$owner' (PID $($inUse[0].OwningProcess))." -ForegroundColor Red
    Write-Host "Stop it, or start on another port:  .\scripts\windows\start-api.ps1 -Port 8001" -ForegroundColor Yellow
    exit 1
}

Write-Host "API      http://localhost:$Port" -ForegroundColor Cyan
Write-Host "API docs http://localhost:$Port/api/docs" -ForegroundColor Cyan
Write-Host "Ctrl+C to stop.`n" -ForegroundColor Gray

# Run from backend\ so the relative SQLite path in .env resolves.
Push-Location $Backend
try {
    & $VenvPython -m uvicorn app.main:app --reload --port $Port
} finally {
    Pop-Location
}
