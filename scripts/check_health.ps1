$ErrorActionPreference = 'Stop'
$api = if ($env:SHINE_API_URL) { $env:SHINE_API_URL } else { 'http://127.0.0.1:8000' }

function Get-Json([string]$path) {
    try {
        return Invoke-RestMethod -Uri "$api$path" -Method Get -TimeoutSec 10
    } catch {
        return $null
    }
}

$health = Get-Json '/health'
$status = Get-Json '/api/v1/system/status'
Write-Host "API:    $(if ($health) { 'online' } else { 'offline' })"
if (-not $status) {
    throw 'The Shine API is not reachable on loopback.'
}
Write-Host "Worker: $(if ($status.worker_online) { 'online' } else { 'offline' })"
Write-Host "Mock:   $(if ($status.mock_provider_available) { 'available' } else { 'unavailable' })"
Write-Host "H3:     $(if ($status.h3_connected) { 'connected' } else { 'not connected (pending local acceptance)' })"
Write-Host "Mode:   $($status.render_mode)"
Write-Host "Jobs:   pending=$($status.pending_jobs) running=$($status.running_jobs) blocked=$($status.blocked_jobs) failed=$($status.failed_jobs)"
if ($status.last_error) { Write-Warning "Worker last error: $($status.last_error)" }
