param(
    [switch]$Install,
    [ValidateSet('mock', 'comfyui')][string]$Provider = 'mock',
    [int]$ApiPort = 8000,
    [int]$WebPort = 5173,
    [string]$DataDir = '',
    [switch]$NoWorker
)

$ErrorActionPreference = 'Stop'
$repoRoot = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path

& (Join-Path $PSScriptRoot 'check_environment.ps1')

if (-not $DataDir) {
    $DataDir = if ($env:SHINE_DATA_DIR) { $env:SHINE_DATA_DIR } else { Join-Path $repoRoot '.local-data' }
}
$env:SHINE_DATA_DIR = (Resolve-Path -LiteralPath $DataDir -ErrorAction SilentlyContinue)?.Path
if (-not $env:SHINE_DATA_DIR) { $env:SHINE_DATA_DIR = [System.IO.Path]::GetFullPath($DataDir) }
New-Item -ItemType Directory -Force -Path $env:SHINE_DATA_DIR | Out-Null
$env:SHINE_RENDER_PROVIDER = $Provider

if ($Install -or -not (Test-Path (Join-Path $repoRoot 'backend/.venv'))) {
    & uv sync --project (Join-Path $repoRoot 'backend') --locked
    if ($LASTEXITCODE -ne 0) { throw 'Python dependency installation failed.' }
}
if ($Install -or -not (Test-Path (Join-Path $repoRoot 'frontend/node_modules'))) {
    & npm --prefix (Join-Path $repoRoot 'frontend') ci
    if ($LASTEXITCODE -ne 0) { throw 'Frontend dependency installation failed.' }
}

$runDir = Join-Path $env:SHINE_DATA_DIR 'run'
$logDir = Join-Path $runDir 'logs'
New-Item -ItemType Directory -Force -Path $logDir | Out-Null

# The database that is actually migrated and used. It is derived from the
# resolved data directory, so a copy database never points back at production
# media while still sharing the production database file.
$databaseUrl = if ($env:SHINE_DATABASE_URL) { $env:SHINE_DATABASE_URL } else { "sqlite:///$((Join-Path $env:SHINE_DATA_DIR 'shine.db').Replace('\','/'))" }
$env:SHINE_DATABASE_URL = $databaseUrl

Write-Host "Data directory: $env:SHINE_DATA_DIR"
Write-Host "Database:       $databaseUrl"

& uv run --project (Join-Path $repoRoot 'backend') alembic -c (Join-Path $repoRoot 'backend/alembic.ini') upgrade head
if ($LASTEXITCODE -ne 0) { throw 'Database migration failed.' }

$venvPython = Join-Path $repoRoot 'backend/.venv/Scripts/python.exe'
$uvicornArgs = @(
    '-m', 'uvicorn', 'shine.main:app',
    '--host', '127.0.0.1', '--port', "$ApiPort"
)
$viteArgs = @(
    (Join-Path $repoRoot 'frontend/node_modules/vite/bin/vite.js'),
    '--host', '127.0.0.1', '--port', "$WebPort"
)

$started = @()

function Get-PortOwner([int]$Port) {
    $listener = Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue |
        Select-Object -First 1
    if ($listener) { return $listener.OwningProcess }
    return $null
}

# A port that is already in use makes the new API die with a bind error while the
# old server keeps serving, which looks like the new configuration being ignored.
$busy = @()
foreach ($port in @($ApiPort, $WebPort)) {
    $owner = Get-PortOwner $port
    if ($owner) { $busy += "  port $port is already in use by pid $owner" }
}
if ($busy) {
    Write-Host 'Cannot start: the requested ports are not free.' -ForegroundColor Red
    $busy | ForEach-Object { Write-Host $_ -ForegroundColor Red }
    Write-Host 'Stop the previous stack first, for example:' -ForegroundColor Yellow
    Write-Host "  uv run --project backend python scripts/stop_shine.py --data-dir `"$env:SHINE_DATA_DIR`"" -ForegroundColor Yellow
    exit 1
}

function Start-ShineProcess([string]$Name, [string]$FilePath, [string[]]$Arguments, [string]$WorkingDirectory) {
    $stdout = Join-Path $logDir "$Name.out.log"
    $stderr = Join-Path $logDir "$Name.err.log"
    $process = Start-Process -PassThru -WindowStyle Hidden -FilePath $FilePath `
        -ArgumentList $Arguments -WorkingDirectory $WorkingDirectory `
        -RedirectStandardOutput $stdout -RedirectStandardError $stderr
    $info = Get-Process -Id $process.Id -ErrorAction SilentlyContinue
    $script:started += [ordered]@{
        name = $Name
        pid = $process.Id
        start_time = if ($info) { $info.StartTime.ToString('o') } else { $null }
        working_dir = $WorkingDirectory
        stdout_log = $stdout
        stderr_log = $stderr
        command = "$FilePath $($Arguments -join ' ')"
    }
    Write-Host "Started $Name (pid $($process.Id))"
}

Start-ShineProcess 'api' $venvPython $uvicornArgs $repoRoot
if (-not $NoWorker) {
    Start-ShineProcess 'worker' $venvPython @('-m', 'shine.worker') $repoRoot
}
Start-ShineProcess 'web' (Get-Command node).Source $viteArgs (Join-Path $repoRoot 'frontend')

# The launcher and the process that actually owns the socket are not the same
# pid: a uv managed virtual environment re-executes the base interpreter as a
# child. Record the real listener so the stop command can end it.
$listenerPid = $null
for ($i = 0; $i -lt 60; $i++) {
    Start-Sleep -Milliseconds 500
    $listenerPid = Get-PortOwner $ApiPort
    if ($listenerPid) { break }
    if (-not (Get-Process -Id $started[0].pid -ErrorAction SilentlyContinue)) { break }
}
if (-not $listenerPid) {
    Write-Host "The API did not start listening on port $ApiPort." -ForegroundColor Red
    Get-Content $started[0].stderr_log -Tail 15 -ErrorAction SilentlyContinue | ForEach-Object { Write-Host "  $_" }
    exit 1
}
$started[0].listener_pid = $listenerPid
Write-Host "API is listening on port $ApiPort (pid $listenerPid)"

$webListener = Get-PortOwner $WebPort
if ($webListener) { $started[2].listener_pid = $webListener }

$manifestPath = Join-Path $runDir 'processes.json'
[ordered]@{
    recorded_at = (Get-Date).ToString('o')
    repo_root = $repoRoot
    data_dir = $env:SHINE_DATA_DIR
    database_url = $databaseUrl
    render_provider = $Provider
    api_port = $ApiPort
    web_port = $WebPort
    processes = $started
} | ConvertTo-Json -Depth 6 | Set-Content -Path $manifestPath -Encoding utf8

Write-Host ''
Write-Host "Shine API:      http://127.0.0.1:$ApiPort/health"
Write-Host "Shine Web:      http://127.0.0.1:$WebPort"
Write-Host "Worker mode:    $Provider"
Write-Host "Logs:           $logDir"
Write-Host "Process record: $manifestPath"
Write-Host ''
Write-Host "Stop with:  uv run --project backend python scripts/stop_shine.py --data-dir `"$env:SHINE_DATA_DIR`""
if ($Provider -eq 'comfyui') {
    Write-Warning 'ComfyUI mode is configured. A queued job is only a real video after a real ComfyUI attempt produced an artifact that passed technical QC.'
} else {
    Write-Host 'Mock mode simulates scheduling and QC only. It never creates or presents a video.'
}
Write-Host 'Open the Web workbench, approve and lock assets, freeze the Style Bible, then create shots.'
