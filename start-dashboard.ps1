# Starts the Palworld management dashboard.
#
# Do NOT open dashboard/index.html directly - as a file:// page its API calls
# have nothing to talk to. This script serves it properly and keeps the admin
# password in the Python process instead of the browser.
#
#   .\start-dashboard.ps1      then open http://127.0.0.1:8080

$ErrorActionPreference = "Stop"

$Root = if ($env:PAL_SERVER_ROOT) { $env:PAL_SERVER_ROOT } else { "C:\PalServer" }
$Ini  = Join-Path $Root "Pal\Saved\Config\WindowsServer\PalWorldSettings.ini"
$Dashboard = Join-Path $PSScriptRoot "dashboard\server.py"
$Python = if ($env:PAL_PYTHON) { $env:PAL_PYTHON } else { (Get-Command python).Source }

if (-not (Test-Path $Ini))     { Write-Error "Server config not found at $Ini" }
if (-not (Test-Path $Python))  { Write-Error "Python not found at $Python" }

# Read the admin password straight from the server config so the two can never
# drift apart.
$cfg = Get-Content $Ini -Raw
$m = [regex]::Match($cfg, 'AdminPassword="([^"]*)"')
if (-not $m.Success -or -not $m.Groups[1].Value) {
    Write-Error "AdminPassword is not set in $Ini - the REST API will reject every request."
}
$env:ADMIN_PASSWORD = $m.Groups[1].Value

# Bail early with a clear message if something already holds the port.
$busy = Get-NetTCPConnection -LocalPort 8080 -State Listen -ErrorAction SilentlyContinue
if ($busy) {
    Write-Host "Dashboard already running (PID $($busy.OwningProcess))." -ForegroundColor Yellow
    Write-Host "Open http://127.0.0.1:8080"
    return
}

Write-Host "Starting dashboard on http://127.0.0.1:8080 (Ctrl+C to stop)" -ForegroundColor Green
& $Python $Dashboard
