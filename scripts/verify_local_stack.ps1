<#
.SYNOPSIS
    Verifies the local Windows stack end to end without a GPU.

.DESCRIPTION
    Starts the local API and the local worker in mock mode, creates a project,
    freezes a Style Bible, creates a shot, runs one mock job and checks the
    audit trail. Mock mode never creates or presents a video; this script only
    proves that the scheduling, QC and recovery plumbing works locally.

    The script uses a throwaway data directory unless SHINE_DATA_DIR is set.
    It never touches an existing project database.
#>
param(
    [int]$ApiPort = 8123,
    [switch]$KeepData
)

$ErrorActionPreference = 'Stop'
$repoRoot = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
$venvPython = Join-Path $repoRoot 'backend/.venv/Scripts/python.exe'

if (-not $env:SHINE_DATA_DIR) {
    $env:SHINE_DATA_DIR = Join-Path $env:TEMP "shine-verify-$(Get-Random)"
    $createdDataDir = $true
} else {
    $createdDataDir = $false
}
New-Item -ItemType Directory -Path $env:SHINE_DATA_DIR -Force | Out-Null
$env:SHINE_RENDER_PROVIDER = 'mock'

Write-Host "Data directory: $env:SHINE_DATA_DIR"
Write-Host 'Applying migrations...'
& uv run --project (Join-Path $repoRoot 'backend') alembic -c (Join-Path $repoRoot 'backend/alembic.ini') upgrade head
if ($LASTEXITCODE -ne 0) { throw 'Database migration failed.' }

$api = $null
$worker = $null
try {
    $api = Start-Process -PassThru -WindowStyle Hidden -FilePath 'uv' -WorkingDirectory $repoRoot `
        -ArgumentList @('run', '--project', 'backend', 'uvicorn', 'shine.main:app', '--host', '127.0.0.1', '--port', "$ApiPort")
    $worker = Start-Process -PassThru -WindowStyle Hidden -FilePath 'uv' -WorkingDirectory $repoRoot `
        -ArgumentList @('run', '--project', 'backend', 'python', '-m', 'shine.worker')

    $base = "http://127.0.0.1:$ApiPort"
    $ready = $false
    for ($i = 0; $i -lt 40; $i++) {
        Start-Sleep -Seconds 1
        try {
            $health = Invoke-RestMethod -Uri "$base/health" -TimeoutSec 3
            $ready = $true
            break
        } catch { }
    }
    if (-not $ready) { throw "The local API did not answer on 127.0.0.1:$ApiPort" }
    Write-Host "API health: $($health.status) (render provider: $($health.render_provider))"

    $project = Invoke-RestMethod -Method Post -Uri "$base/api/v1/projects" `
        -ContentType 'application/json' -Body (@{ title = 'Local verification project' } | ConvertTo-Json)
    Write-Host "Project: $($project.id)"

    # The style values are plain ASCII so that Windows PowerShell 5.1 can read
    # this script without a UTF-8 byte order mark.
    $style = @{
        photography_language = 'eye level medium shot, stable push in'
        shot_blocking        = 'keep the dialogue axis'
        color_palette        = 'cool grey environment, warm skin tones'
        lighting_tone        = 'single key light, keep shadow detail'
        lens_texture         = 'natural shallow depth of field, light grain'
        editing_rhythm       = 'cut on the pause after a line'
        sound_performance    = 'natural spoken pace, restrained ambience'
    }
    Invoke-RestMethod -Method Put -Uri "$base/api/v1/projects/$($project.id)/style/draft" `
        -ContentType 'application/json' -Body ($style | ConvertTo-Json) | Out-Null
    $frozen = Invoke-RestMethod -Method Post -Uri "$base/api/v1/projects/$($project.id)/style/freeze" `
        -ContentType 'application/json' -Body '{}'
    Write-Host "Frozen style version: $($frozen.version) ($($frozen.sha256.Substring(0, 12)))"

    $shot = @{
        name = 'Verification shot'
        spec = @{
            schema_version     = '1.0'
            story_beat         = 'Two people exchange a clue on a rainy pier'
            duration_target_s  = 6
            shot_priority      = 'B'
            style_version_id   = $frozen.id
            camera             = @{ shot_size = 'medium' }
            characters         = @('Lan')
            required_asset_ids = @()
            dialogue           = @()
            sound              = @{}
            continuity         = @{}
            locked_story_facts = @()
        }
    }
    $created = Invoke-RestMethod -Method Post -Uri "$base/api/v1/projects/$($project.id)/shots" `
        -ContentType 'application/json' -Body ($shot | ConvertTo-Json -Depth 6)
    Write-Host "Shot: $($created.id)"

    $job = Invoke-RestMethod -Method Post -Uri "$base/api/v1/projects/$($project.id)/jobs" `
        -ContentType 'application/json' -Body (@{ shot_id = $created.id; provider = 'mock' } | ConvertTo-Json)
    Write-Host "Job: $($job.id)"

    $final = $null
    for ($i = 0; $i -lt 30; $i++) {
        Start-Sleep -Seconds 1
        $final = Invoke-RestMethod -Uri "$base/api/v1/jobs/$($job.id)" -TimeoutSec 5
        if ($final.status -notin @('QUEUED', 'SUBMITTING', 'RUNNING', 'QC_PENDING')) { break }
    }
    Write-Host "Job status: $($final.status) (simulated: $($final.simulated), video_created: $($final.video_created))"
    if ($final.status -ne 'PASSED') { throw "The mock job did not reach PASSED (status: $($final.status))." }

    $events = Invoke-RestMethod -Uri "$base/api/v1/projects/$($project.id)/events?limit=200" -TimeoutSec 5
    Write-Host "Audit events: $($events.total)"
    $status = Invoke-RestMethod -Uri "$base/api/v1/system/status" -TimeoutSec 5
    Write-Host "System status: render_mode=$($status.render_mode) workers=$($status.workers.Count)"

    Write-Host ''
    Write-Host 'Local stack verification passed. Mock mode simulates scheduling and QC only; it never created a video.'
} finally {
    # A launcher and the process that actually owns the socket are not the same
    # pid: a uv managed environment re-executes the base interpreter as a child.
    # Stopping only the launcher leaves the API and the Worker running, so the
    # whole owned tree is ended by verified birth identity instead.
    foreach ($entry in @(@{ name = 'worker'; process = $worker }, @{ name = 'api'; process = $api })) {
        if (-not $entry.process) { continue }
        $info = Get-Process -Id $entry.process.Id -ErrorAction SilentlyContinue
        if (-not $info) { continue }
        $birth = $info.StartTime.ToUniversalTime().ToFileTimeUtc().ToString()
        & $venvPython (Join-Path $PSScriptRoot 'shine_processes.py') $entry.process.Id $birth --stop 2>&1 |
            Out-Null
    }
    if ($createdDataDir -and -not $KeepData) {
        Remove-Item -Recurse -Force $env:SHINE_DATA_DIR -ErrorAction SilentlyContinue
    }
}
