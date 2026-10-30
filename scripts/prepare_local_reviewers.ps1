# Prepare the local review routes for a real production run.
#
# The local review executor only speaks the strict JSON-schema review protocol,
# and the provider profile learns that protocol from a live probe of the
# executor.  The managed production run starts its own owned review services,
# so the probe has to happen against a temporary pair of executors first.
#
# This script therefore:
#   1. migrates the acceptance database;
#   2. starts the API and one temporary local review executor per role;
#   3. creates the provider profiles and the reviewer routes, and probes each
#      executor so the pinned response protocol and decoder hash are recorded;
#   4. stops every process it started.
#
# The seeded database is then used by scripts/start_shine.ps1 for the real run.
param(
    [Parameter(Mandatory = $true)][string]$DataDir,
    [Parameter(Mandatory = $true)][string]$Manifest,
    [Parameter(Mandatory = $true)][string]$RuntimePackages,
    [Parameter(Mandatory = $true)][string]$ReviewPython,
    [int]$ApiPort = 8131,
    [int]$VlmPort = 8116,
    [int]$AsrPort = 8117
)

$ErrorActionPreference = 'Stop'
$repoRoot = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
$venvPython = Join-Path $repoRoot 'backend/.venv/Scripts/python.exe'
$started = @()

function Stop-Owned([System.Diagnostics.Process]$Process) {
    if (-not $Process) { return }
    $info = Get-Process -Id $Process.Id -ErrorAction SilentlyContinue
    if (-not $info) { return }
    $birth = $info.StartTime.ToUniversalTime().ToFileTimeUtc().ToString()
    & $venvPython (Join-Path $PSScriptRoot 'shine_processes.py') $Process.Id $birth --stop 2>&1 | Out-Null
}

try {
    New-Item -ItemType Directory -Force -Path $DataDir | Out-Null
    $env:SHINE_DATA_DIR = (Resolve-Path -LiteralPath $DataDir).Path
    $env:SHINE_DATABASE_URL = "sqlite:///$((Join-Path $env:SHINE_DATA_DIR 'shine.db').Replace('\','/'))"
    $env:SHINE_RENDER_PROVIDER = 'comfyui'

    & uv run --project (Join-Path $repoRoot 'backend') alembic -c (Join-Path $repoRoot 'backend/alembic.ini') upgrade head
    if ($LASTEXITCODE -ne 0) { throw 'Database migration failed.' }

    $api = Start-Process -PassThru -WindowStyle Hidden -FilePath 'uv' -WorkingDirectory $repoRoot `
        -ArgumentList @('run', '--project', 'backend', 'uvicorn', 'shine.main:app', '--host', '127.0.0.1', '--port', "$ApiPort")
    $started += $api
    $base = "http://127.0.0.1:$ApiPort"
    $ready = $false
    for ($i = 0; $i -lt 60; $i++) {
        Start-Sleep -Seconds 1
        try { if ((Invoke-RestMethod "$base/health" -TimeoutSec 3).status -eq 'ok') { $ready = $true; break } } catch { }
    }
    if (-not $ready) { throw "The API did not answer on 127.0.0.1:$ApiPort" }

    $reviewScript = Join-Path $repoRoot 'scripts/local_review_isolated_server.py'
    foreach ($port in @($VlmPort, $AsrPort)) {
        $service = Start-Process -PassThru -WindowStyle Hidden -FilePath $ReviewPython -WorkingDirectory $repoRoot `
            -ArgumentList @($reviewScript, '--manifest', $Manifest, '--runtime-packages', $RuntimePackages, '--port', "$port")
        $started += $service
        $serviceReady = $false
        for ($i = 0; $i -lt 120; $i++) {
            Start-Sleep -Milliseconds 500
            try { if ((Invoke-RestMethod "http://127.0.0.1:$port/health" -TimeoutSec 2).status -eq 'ok') { $serviceReady = $true; break } } catch { }
        }
        if (-not $serviceReady) { throw "The local review executor did not start on port $port" }
        Write-Host "Local review executor ready on port $port"
    }

    & $venvPython (Join-Path $PSScriptRoot 'seed_local_review_routes.py') `
        --api $base --vlm-port $VlmPort --asr-port $AsrPort
    if ($LASTEXITCODE -ne 0) { throw 'Seeding the local reviewer routes failed.' }
} finally {
    foreach ($process in $started) { Stop-Owned $process }
    Start-Sleep -Seconds 2
    Write-Host 'Preparation processes stopped.'
}
