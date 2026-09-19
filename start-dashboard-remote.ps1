# Palworld dashboard launcher (double-click the desktop shortcut, or run this).
#
# The server's REST API listens on loopback only and is NOT exposed to the
# internet - it's HTTP Basic auth over a plain socket, so publishing it would
# hand over the server. This opens an SSH tunnel, starts the dashboard, and
# opens your browser. Everything binds to 127.0.0.1, so only this PC can
# reach it.

$ErrorActionPreference = "Stop"

$VpsHost   = $env:PAL_VPS_HOST   # e.g. "ubuntu@203.0.113.10" - set this, or edit the line
$GameAddr  = $env:PAL_GAME_ADDR  # e.g. "203.0.113.10:8211"
$SshKey    = "$env:USERPROFILE\.ssh\palworld_vps"   # your private key for the VPS
$Dashboard = Join-Path $PSScriptRoot "dashboard\server.py"
$Python    = if ($env:PAL_PYTHON) { $env:PAL_PYTHON } else { (Get-Command python).Source }
$Url       = "http://127.0.0.1:8080"

if (-not $VpsHost)  { Write-Error "Set PAL_VPS_HOST (e.g. user@your.server.ip) or edit this script." }
if (-not $GameAddr) { $GameAddr = ($VpsHost -split "@")[-1] + ":8211" }
if (-not (Test-Path $SshKey)) { Write-Error "SSH key not found at $SshKey" }
if (-not (Test-Path $Python)) { Write-Error "Python not found at $Python" }

# Pull both passwords from the server's .env so nothing is duplicated here and
# they can never drift out of sync with what the container is actually using.
Write-Host "Connecting to the server..." -ForegroundColor Cyan
$envText = ssh -i $SshKey -o BatchMode=yes -o ConnectTimeout=15 $VpsHost "cat ~/palworld/.env"
if (-not $envText) { Write-Error "Could not read ~/palworld/.env from $VpsHost" }

$env:ADMIN_PASSWORD  = ($envText | Select-String '^ADMIN_PASSWORD=(.*)$').Matches.Groups[1].Value
$env:SERVER_PASSWORD = ($envText | Select-String '^SERVER_PASSWORD=(.*)$').Matches.Groups[1].Value
$env:GAME_ADDRESS    = $GameAddr
# Host-level health (load, memory, disk, service states) comes over SSH -
# the Palworld API only reports on the game, not the machine under it.
$env:VPS_HOST        = $VpsHost
$env:SSH_KEY         = $SshKey
if (-not $env:ADMIN_PASSWORD) { Write-Error "ADMIN_PASSWORD missing from the server's .env" }

# Tunnel: local 8212 -> the VPS's loopback 8212.
if (Get-NetTCPConnection -LocalPort 8212 -State Listen -ErrorAction SilentlyContinue) {
    Write-Host "Tunnel already open." -ForegroundColor Yellow
} else {
    Write-Host "Opening SSH tunnel..." -ForegroundColor Cyan
    Start-Process ssh -ArgumentList @(
        "-i", $SshKey, "-N",
        "-o", "BatchMode=yes",
        "-o", "ExitOnForwardFailure=yes",
        "-o", "ServerAliveInterval=30",
        "-L", "8212:127.0.0.1:8212",
        $VpsHost
    ) -WindowStyle Hidden
    Start-Sleep -Seconds 4
}

if (Get-NetTCPConnection -LocalPort 8080 -State Listen -ErrorAction SilentlyContinue) {
    Write-Host "Dashboard already running." -ForegroundColor Yellow
} else {
    Write-Host "Starting dashboard..." -ForegroundColor Cyan
    Start-Process -FilePath $Python -ArgumentList $Dashboard -WindowStyle Hidden
    Start-Sleep -Seconds 3
}

Write-Host "Opening $Url" -ForegroundColor Green
Start-Process $Url
