# Connects this Windows PC to the Agentic OS VPS over Tailscale and opens the HTTPS dashboard.
param(
    [string]$Node = "jarvis-vps"
)
$ErrorActionPreference = "Stop"

if (-not (Get-Command tailscale -ErrorAction SilentlyContinue)) {
    Write-Host "Tailscale not found. Installing via winget..." -ForegroundColor Cyan
    winget install --id Tailscale.Tailscale -e --accept-source-agreements --accept-package-agreements
    $env:Path += ";C:\Program Files\Tailscale"
}

$statusJson = tailscale status --json | ConvertFrom-Json
if ($statusJson.BackendState -ne "Running") {
    Write-Host "Logging in to Tailscale (a browser window will open)..." -ForegroundColor Yellow
    tailscale up
    $statusJson = tailscale status --json | ConvertFrom-Json
}

$dnsSuffix = $statusJson.MagicDNSSuffix
if (-not $dnsSuffix) {
    throw "MagicDNS suffix not found. Enable MagicDNS and HTTPS certificates in https://login.tailscale.com/admin/dns"
}

Write-Host "Checking direct WireGuard path to $Node..." -ForegroundColor Cyan
tailscale ping -c 3 $Node

$url = "https://$Node.$dnsSuffix"
Write-Host "Opening Agentic OS at $url" -ForegroundColor Green
Start-Process $url
