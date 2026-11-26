<#
.SYNOPSIS
    Start the IT InternTrack web interface on http://localhost:5174
.DESCRIPTION
    The dev server proxies /api to the backend on port 8000, so start the API
    first. Pass -ApiPort if the API is running somewhere else.
.EXAMPLE
    .\scripts\windows\start-web.ps1
.EXAMPLE
    .\scripts\windows\start-web.ps1 -ApiPort 8001
#>
[CmdletBinding()]
param([int]$ApiPort = 8000)

$ErrorActionPreference = 'Stop'
$Root     = Split-Path -Parent (Split-Path -Parent $PSScriptRoot)
$Frontend = Join-Path $Root 'frontend'

if (-not (Test-Path (Join-Path $Frontend 'node_modules'))) {
    Write-Host "Dependencies are not installed. Run .\scripts\windows\setup.ps1 first." -ForegroundColor Red
    exit 1
}

# Warn rather than fail: the user may be about to start the API in the other
# terminal, and the dev server copes with the backend appearing later.
try {
    $null = Invoke-WebRequest -Uri "http://127.0.0.1:$ApiPort/api/health" -TimeoutSec 2 -UseBasicParsing
    Write-Host "API is up on port $ApiPort." -ForegroundColor Green
} catch {
    Write-Host "The API does not seem to be running on port $ApiPort." -ForegroundColor Yellow
    Write-Host "Start it in another terminal: .\scripts\windows\start-api.ps1" -ForegroundColor Yellow
    Write-Host "Continuing anyway - sign-in will fail until it is up.`n" -ForegroundColor Gray
}

Write-Host "Web interface  http://localhost:5174" -ForegroundColor Cyan
Write-Host "Ctrl+C to stop.`n" -ForegroundColor Gray

Push-Location $Frontend
try {
    $env:VITE_API_TARGET = "http://127.0.0.1:$ApiPort"
    & npm run dev
} finally {
    Pop-Location
}
