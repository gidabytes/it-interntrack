<#
.SYNOPSIS
    One-time setup for IT InternTrack on Windows.
.DESCRIPTION
    Creates the Python virtual environment, installs both dependency sets,
    generates .env with fresh secrets, creates the database and an admin
    account, and optionally loads demo data.

    Safe to re-run: it will not overwrite an existing .env, because doing so
    would replace the encryption keys and make existing data unreadable.
.EXAMPLE
    .\scripts\windows\setup.ps1
.EXAMPLE
    .\scripts\windows\setup.ps1 -AdminPassword 'MyStrongPass123!' -NoDemoData
#>
[CmdletBinding()]
param(
    # Temporary admin password. The web interface forces a change at first
    # sign-in, so the default is fine to start with.
    [string]$AdminPassword = 'admin',
    [switch]$NoDemoData,
    [switch]$SkipFrontend
)

$ErrorActionPreference = 'Stop'

# Repository root, two levels up from this script.
$Root    = Split-Path -Parent (Split-Path -Parent $PSScriptRoot)
$Backend = Join-Path $Root 'backend'
$Frontend= Join-Path $Root 'frontend'

function Write-Step  ($m) { Write-Host "`n==> $m" -ForegroundColor Cyan }
function Write-Ok    ($m) { Write-Host "    $m" -ForegroundColor Green }
function Write-Warn2 ($m) { Write-Host "    $m" -ForegroundColor Yellow }

Write-Host "IT InternTrack - Windows setup" -ForegroundColor White
Write-Host "Repository: $Root"

# --- Prerequisites ---------------------------------------------------------
Write-Step 'Checking prerequisites'

# "python" on a clean Windows is often a Microsoft Store stub that prints a
# message and exits 9009. Prefer the py launcher, which is what the real
# installer provides.
$PythonCmd = $null
foreach ($candidate in @('py', 'python')) {
    try {
        $version = & $candidate -c 'import sys; print("%d.%d" % sys.version_info[:2])' 2>$null
        if ($LASTEXITCODE -eq 0 -and $version -match '^\d+\.\d+$') {
            $major, $minor = $version.Split('.')
            if ([int]$major -gt 3 -or ([int]$major -eq 3 -and [int]$minor -ge 11)) {
                $PythonCmd = $candidate
                Write-Ok "Python $version  ($candidate)"
                break
            }
            Write-Warn2 "Found Python $version via '$candidate', but 3.11 or newer is required."
        }
    } catch { }
}
if (-not $PythonCmd) {
    Write-Host @'

Python 3.11+ was not found.

Install it from https://www.python.org/downloads/windows/
and tick "Add python.exe to PATH" in the installer.

If you have Python but this still fails, you may be hitting the Microsoft
Store stub. Turn it off under:
  Settings > Apps > Advanced app settings > App execution aliases
and switch off "python.exe" and "python3.exe".
'@ -ForegroundColor Red
    exit 1
}

if (-not $SkipFrontend) {
    try {
        $nodeVersion = (& node --version 2>$null).TrimStart('v')
        if ($LASTEXITCODE -ne 0) { throw }
        if ([int]$nodeVersion.Split('.')[0] -lt 18) {
            Write-Warn2 "Node $nodeVersion found, but 18 or newer is required."
            exit 1
        }
        Write-Ok "Node $nodeVersion"
    } catch {
        Write-Host @'

Node.js 18+ was not found. Install the LTS build from
https://nodejs.org/  then open a NEW terminal and run this again.

To set up only the backend for now, re-run with -SkipFrontend
'@ -ForegroundColor Red
        exit 1
    }
}

# --- Virtual environment ---------------------------------------------------
Write-Step 'Python virtual environment'
Push-Location $Backend
try {
    $VenvPython = Join-Path $Backend '.venv\Scripts\python.exe'
    if (Test-Path $VenvPython) {
        Write-Ok 'Already exists, reusing it'
    } else {
        & $PythonCmd -m venv .venv
        if (-not (Test-Path $VenvPython)) { throw 'Virtual environment was not created.' }
        Write-Ok 'Created .venv'
    }

    # Call the venv's python directly rather than activating. Activation needs
    # a relaxed PowerShell execution policy; this does not, which removes the
    # most common Windows blocker entirely.
    Write-Step 'Installing Python dependencies (a minute or two)'
    & $VenvPython -m pip install --upgrade pip --quiet
    & $VenvPython -m pip install -r requirements.txt --quiet
    if ($LASTEXITCODE -ne 0) { throw 'pip install failed.' }
    Write-Ok 'Installed'

    # --- Configuration -----------------------------------------------------
    Write-Step 'Configuration and secrets'
    if (Test-Path (Join-Path $Backend '.env')) {
        Write-Ok '.env already exists, leaving it alone'
        Write-Warn2 'Its encryption keys are kept: replacing them would make existing data unreadable.'
    } else {
        & $VenvPython -m app.cli init-env
        if ($LASTEXITCODE -ne 0) { throw 'Could not write .env' }
    }

    # --- Database ----------------------------------------------------------
    Write-Step 'Database'
    & $VenvPython -m alembic upgrade head
    if ($LASTEXITCODE -ne 0) { throw 'Migrations failed.' }
    Write-Ok 'Schema created'

    & $VenvPython -m app.cli bootstrap --password $AdminPassword
    if ($LASTEXITCODE -ne 0) { throw 'Bootstrap failed.' }

    if (-not $NoDemoData) {
        Write-Step 'Demo data'
        & $VenvPython -m app.cli seed-demo
        Write-Ok 'Loaded, every row marked DEMO DATA'
        Write-Warn2 'Remove it before real records: python -m app.cli purge-demo'
    }
} finally {
    Pop-Location
}

# --- Frontend --------------------------------------------------------------
if (-not $SkipFrontend) {
    Write-Step 'Installing web interface dependencies (a few minutes)'
    Push-Location $Frontend
    try {
        & npm install --no-fund --no-audit
        if ($LASTEXITCODE -ne 0) { throw 'npm install failed.' }
        Write-Ok 'Installed'
    } finally {
        Pop-Location
    }
}

# --- Done ------------------------------------------------------------------
Write-Host @"

==================================================================
 Setup complete
==================================================================

Start it with TWO terminals:

  Terminal 1   .\scripts\windows\start-api.ps1
  Terminal 2   .\scripts\windows\start-web.ps1

Then open   http://localhost:5174

Sign in as 'admin' with the password '$AdminPassword'.
You will be required to choose a new one immediately.
"@ -ForegroundColor White

if (-not $NoDemoData) {
    Write-Host @'
Demo accounts, password InternTrack#2026 :
  itmanager   IT Manager      both dashboards, all interns
  rsantos     IT Supervisor   own interns, can approve and sign
  jvaldez     IT Mentor       read-only plus exposure logging
  ana.cruz    OJT Intern      time clock and own records

'@ -ForegroundColor Gray
}

Write-Host "Back up backend\.env somewhere safe - it holds the encryption keys.`n" -ForegroundColor Yellow
