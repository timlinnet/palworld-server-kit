# Starts the Palworld dedicated server.
#
# -publiclobby is what publishes the server to the in-game community list.
# Without it your Xbox friends cannot find the server at all, because consoles
# have no way to type in an IP address.

$ErrorActionPreference = "Stop"
$ServerRoot = if ($env:PAL_SERVER_ROOT) { $env:PAL_SERVER_ROOT } else { "C:\PalServer" }
$Exe = Join-Path $ServerRoot "PalServer.exe"

if (-not (Test-Path $Exe)) {
    Write-Error "PalServer.exe not found at $Exe. Run the SteamCMD install first."
}

# The firewall rule needs admin, so it is not created here. Check whether it
# exists and tell the user rather than failing silently at connect time.
$rule = Get-NetFirewallRule -DisplayName "Palworld Server (UDP 8211)" -ErrorAction SilentlyContinue
if (-not $rule) {
    Write-Warning @"
No inbound firewall rule for UDP 8211 was found.
Your friends will not be able to connect until you add one.
Run this ONCE in an elevated PowerShell:

  New-NetFirewallRule -DisplayName 'Palworld Server (UDP 8211)' ``
    -Direction Inbound -Protocol UDP -LocalPort 8211 -Action Allow

"@
}

Write-Host "Starting Palworld server..." -ForegroundColor Green
Write-Host "Community list: enabled (-publiclobby)"
Write-Host "Join from this LAN at: <this-pc-ipv4>:8211  (run ipconfig to find it)"
Write-Host ""

Push-Location $ServerRoot
try {
    & $Exe -publiclobby -port=8211
} finally {
    Pop-Location
}
