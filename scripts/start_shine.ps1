param(
    [switch]$Install,
    [ValidateSet('mock', 'comfyui')][string]$Provider = 'mock',
    [ValidateRange(1,65535)][int]$ApiPort = 8000,
    [ValidateRange(1,65535)][int]$WebPort = 5173,
    [string]$DataDir = '',
    [switch]$NoWorker,
    [string]$ReviewPython = '',
    [string]$ReviewManifest = '',
    [string]$ReviewRuntimePackages = '',
    [switch]$IsolatedReviewProcess,
    [ValidateRange(1,65535)][int]$ReviewPort = 8116,
    [string]$AdditionalReviewManifest = '',
    [ValidateRange(1,65535)][int]$AdditionalReviewPort = 8117,
    [switch]$OpenBrowser
)

$ErrorActionPreference = 'Stop'
# The native tools in this script write progress to stderr.  PowerShell turns
# native stderr into a terminating error, so those calls run with the
# preference relaxed and are judged by their exit code instead.
$PSNativeCommandUseErrorActionPreference = $false
$explicitDataDir = [bool]$DataDir
if ($ApiPort -eq $WebPort) { throw 'API and Web require different ports.' }
$repoRoot = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
# Bootstrap persists only these host-local paths. Never evaluate .env as code.
$launchEnv = Join-Path $repoRoot '.env'
if (Test-Path -LiteralPath $launchEnv) {
    $ownedKeys = @('SHINE_DATA_DIR', 'SHINE_DATABASE_URL', 'SHINE_BOOTSTRAP_ROOT', 'SHINE_BACKEND_ENV', 'UV_CACHE_DIR',
        'UV_PYTHON_INSTALL_DIR', 'PIP_CACHE_DIR', 'npm_config_cache', 'SHINE_RENDER_PROVIDER', 'SHINE_ENABLE_LOCAL_EXECUTION')
    foreach ($line in (Get-Content -LiteralPath $launchEnv)) {
        if ($line -match '^([A-Za-z_][A-Za-z0-9_]*)=(.*)$' -and $Matches[1] -in $ownedKeys) {
            $key, $value = $Matches[1], $Matches[2].Trim()
            if ($value.StartsWith("'") -or $value.StartsWith('"')) {
                if ($value.Length -lt 2 -or $value[-1] -ne $value[0]) { throw "Invalid quoted local setting: $key" }
                $value = $value.Substring(1, $value.Length - 2).Replace("\'", "'").Replace('\\', '\')
            } else { $value = ($value -replace '[ \t]+#.*$', '').Trim() }
            if ($value.Contains('${')) { throw "Use a resolved local value for $key; launcher does not expand .env expressions." }
            if (-not [Environment]::GetEnvironmentVariable($key, 'Process')) {
                [Environment]::SetEnvironmentVariable($key, $value, 'Process')
            }
        }
    }
}
# The operator activates a managed configuration in the setup wizard, which
# persists the renderer choice. Honor it on every later start unless the caller
# passed -Provider explicitly, so a tester does not silently fall back to Mock
# after a reboot.
if (-not $PSBoundParameters.ContainsKey('Provider')) {
    $persistedProvider = [Environment]::GetEnvironmentVariable('SHINE_RENDER_PROVIDER', 'Process')
    if ($persistedProvider -eq 'mock' -or $persistedProvider -eq 'comfyui') { $Provider = $persistedProvider }
}
$backendEnv = if ($env:SHINE_BACKEND_ENV) { $env:SHINE_BACKEND_ENV } else { Join-Path $repoRoot 'backend/.venv' }
$venvPython = Join-Path $backendEnv 'Scripts/python.exe'
if ($env:SHINE_BACKEND_ENV) { $env:UV_PROJECT_ENVIRONMENT = $backendEnv }
if ($env:SHINE_BOOTSTRAP_ROOT) {
    if ([IO.Path]::GetPathRoot([IO.Path]::GetFullPath($env:SHINE_BOOTSTRAP_ROOT)).TrimEnd('\') -eq $env:SystemDrive) {
        throw 'Choose a non-system-drive bootstrap installation directory.'
    }
    $env:UV_CACHE_DIR = Join-Path $env:SHINE_BOOTSTRAP_ROOT 'download-cache/uv'
    $env:UV_PYTHON_INSTALL_DIR = Join-Path $env:SHINE_BOOTSTRAP_ROOT 'python'
    $env:PIP_CACHE_DIR = Join-Path $env:SHINE_BOOTSTRAP_ROOT 'download-cache/pip'
    $env:npm_config_cache = Join-Path $env:SHINE_BOOTSTRAP_ROOT 'download-cache/npm'
    $env:TEMP = Join-Path $env:SHINE_BOOTSTRAP_ROOT 'temp'
    $env:TMP = $env:TEMP
    New-Item -ItemType Directory -Force -Path $env:TEMP | Out-Null
}

function Invoke-Native([string]$Label, [scriptblock]$Command) {
    $previous = $ErrorActionPreference
    $ErrorActionPreference = 'Continue'
    try { & $Command } finally { $ErrorActionPreference = $previous }
    if ($LASTEXITCODE -ne 0) { throw "$Label failed with exit code $LASTEXITCODE." }
}
$startReview = [bool]($ReviewPython -or $ReviewManifest -or $ReviewRuntimePackages)
if ($IsolatedReviewProcess -and -not $startReview) { throw 'Isolated review requires the local review configuration.' }
if ($AdditionalReviewManifest -and -not $startReview) { throw 'Additional review requires the primary review runtime configuration.' }
if ($startReview) {
    if (-not ($ReviewPython -and $ReviewManifest -and $ReviewRuntimePackages)) {
        throw 'Local review requires ReviewPython, ReviewManifest and ReviewRuntimePackages together.'
    }
    if ($ReviewPort -in @($ApiPort, $WebPort)) { throw 'Review, API and Web require distinct ports.' }
    $ReviewPython = (Resolve-Path -LiteralPath $ReviewPython).Path
    $ReviewManifest = (Resolve-Path -LiteralPath $ReviewManifest).Path
    $ReviewRuntimePackages = (Resolve-Path -LiteralPath $ReviewRuntimePackages).Path
}
if ($AdditionalReviewManifest) {
    if ($AdditionalReviewPort -in @($ApiPort, $WebPort, $ReviewPort)) { throw 'All service ports must be distinct.' }
    $AdditionalReviewManifest = (Resolve-Path -LiteralPath $AdditionalReviewManifest).Path
}

if (-not $DataDir) {
    $DataDir = if ($env:SHINE_DATA_DIR) { $env:SHINE_DATA_DIR } else { Join-Path $repoRoot '.local-data' }
}
$resolvedDataDir = Resolve-Path -LiteralPath $DataDir -ErrorAction SilentlyContinue
$env:SHINE_DATA_DIR = if ($resolvedDataDir) { $resolvedDataDir.Path } else { [System.IO.Path]::GetFullPath($DataDir) }
New-Item -ItemType Directory -Force -Path $env:SHINE_DATA_DIR | Out-Null
if (Test-Path (Join-Path $env:SHINE_DATA_DIR 'run/processes.json')) {
    throw 'This data directory already has a process manifest. Stop or reconcile that stack before restarting.'
}
$env:SHINE_RENDER_PROVIDER = $Provider
$env:SHINE_VITE_API_TARGET = "http://127.0.0.1:$ApiPort"

if ($Install -or -not (Test-Path $backendEnv)) {
    Invoke-Native 'Python dependency installation' {
        & uv sync --project (Join-Path $repoRoot 'backend') --locked
    }
}
if ($Install -or -not (Test-Path (Join-Path $repoRoot 'frontend/node_modules'))) {
    $frontendInstallRoot = Join-Path $repoRoot 'frontend'
    if ($env:SHINE_BOOTSTRAP_ROOT) {
        $frontendInstallRoot = Join-Path $env:SHINE_BOOTSTRAP_ROOT 'app/frontend'
        if ((Get-Item -LiteralPath (Join-Path $repoRoot 'frontend')).Attributes -band [IO.FileAttributes]::ReparsePoint) {
            throw 'Frontend directory link requires reconciliation before relocating dependencies.'
        }
        New-Item -ItemType Directory -Force -Path $frontendInstallRoot | Out-Null
        Copy-Item -LiteralPath (Join-Path $repoRoot 'frontend/package.json'), (Join-Path $repoRoot 'frontend/package-lock.json') -Destination $frontendInstallRoot -Force
        $modules = [IO.Path]::GetFullPath((Join-Path $repoRoot 'frontend/node_modules'))
        $externalModules = [IO.Path]::GetFullPath((Join-Path $frontendInstallRoot 'node_modules'))
        if (Test-Path -LiteralPath $modules) {
            $item = Get-Item -LiteralPath $modules -Force
            if ($item.Attributes -band [IO.FileAttributes]::ReparsePoint) {
                if ([IO.Path]::GetFullPath($item.Target) -ne $externalModules) { throw 'Existing node_modules link requires reconciliation.' }
            } else {
                $backupModules = $modules + '.before-storage-' + [Guid]::NewGuid().ToString('N')
                $frontendRoot = [IO.Path]::GetFullPath((Join-Path $repoRoot 'frontend')) + [IO.Path]::DirectorySeparatorChar
                if (-not $modules.StartsWith($frontendRoot, [StringComparison]::OrdinalIgnoreCase) -or
                    -not $backupModules.StartsWith($frontendRoot, [StringComparison]::OrdinalIgnoreCase)) {
                    throw 'Dependency backup escaped the frontend directory.'
                }
                Move-Item -LiteralPath $modules -Destination $backupModules
                Write-Host "Preserved previous dependencies: $backupModules"
            }
        }
        if (-not (Test-Path -LiteralPath $modules)) {
            New-Item -ItemType Directory -Force -Path $externalModules | Out-Null
            New-Item -ItemType Junction -Path $modules -Target $externalModules | Out-Null
        }
    }
    Invoke-Native 'Frontend dependency installation' {
        & npm --prefix $frontendInstallRoot ci --fetch-retries=4 --fetch-retry-mintimeout=10000 --fetch-retry-maxtimeout=60000 --fetch-timeout=300000
    }
}

$runDir = Join-Path $env:SHINE_DATA_DIR 'run'
$logDir = Join-Path $runDir 'logs'
New-Item -ItemType Directory -Force -Path $logDir | Out-Null

# The database that is actually migrated and used. It is derived from the
# resolved data directory, so a copy database never points back at production
# media while still sharing the production database file.
$databaseUrl = if ($env:SHINE_DATABASE_URL -and -not $explicitDataDir) { $env:SHINE_DATABASE_URL } else { "sqlite:///$((Join-Path $env:SHINE_DATA_DIR 'shine.db').Replace('\','/'))" }
if (-not $databaseUrl.StartsWith('sqlite:///')) { throw 'The local launcher requires a SQLite database URL.' }
$env:SHINE_DATABASE_URL = $databaseUrl
& (Join-Path $PSScriptRoot 'check_environment.ps1')

Write-Host "Data directory: $env:SHINE_DATA_DIR"
Write-Host "Database:       $databaseUrl"

$migrationLog = Join-Path $env:TEMP 'shine-alembic-upgrade.log'
Invoke-Native 'Database migration' {
    & $venvPython -m alembic -c (Join-Path $repoRoot 'backend/alembic.ini') upgrade head 2> $migrationLog
}
Get-Content $migrationLog -Tail 5 -ErrorAction SilentlyContinue | ForEach-Object { Write-Host $_ }

$uvicornArgs = @(
    '-m', 'uvicorn', 'shine.main:app',
    '--host', '127.0.0.1', '--port', "$ApiPort"
)
$viteArgs = @(
    (Join-Path $repoRoot 'frontend/node_modules/vite/bin/vite.js'),
    '--host', '127.0.0.1', '--port', "$WebPort", '--strictPort'
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
$requestedPorts = @($ApiPort, $WebPort)
if ($startReview) { $requestedPorts += $ReviewPort }
if ($AdditionalReviewManifest) { $requestedPorts += $AdditionalReviewPort }
foreach ($port in $requestedPorts) {
    $owner = Get-PortOwner $port
    if ($owner) { $busy += "  port $port is already in use by pid $owner" }
}
if ($busy) {
    Write-Host 'Cannot start: the requested ports are not free.' -ForegroundColor Red
    $busy | ForEach-Object { Write-Host $_ -ForegroundColor Red }
    Write-Host 'Stop the previous stack first, for example:' -ForegroundColor Yellow
    Write-Host "  `"$venvPython`" scripts/stop_shine.py --data-dir `"$env:SHINE_DATA_DIR`"" -ForegroundColor Yellow
    exit 1
}

function Start-ShineProcess([string]$Name, [string]$FilePath, [string[]]$Arguments, [string]$WorkingDirectory) {
    $stdout = Join-Path $logDir "$Name.out.log"
    $stderr = Join-Path $logDir "$Name.err.log"
    $process = Start-Process -PassThru -WindowStyle Hidden -FilePath $FilePath `
        -ArgumentList ($Arguments | ForEach-Object { '"' + $_ + '"' }) -WorkingDirectory $WorkingDirectory `
        -RedirectStandardOutput $stdout -RedirectStandardError $stderr
    $info = Get-Process -Id $process.Id -ErrorAction SilentlyContinue
    $script:started += [ordered]@{
        name = $Name
        pid = $process.Id
        start_time = if ($info) { $info.StartTime.ToString('o') } else { $null }
        birth = if ($info) { $info.StartTime.ToUniversalTime().ToFileTimeUtc().ToString() } else { $null }
        working_dir = $WorkingDirectory
        stdout_log = $stdout
        stderr_log = $stderr
        command = "$FilePath $($Arguments -join ' ')"
    }
    Write-Host "Started $Name (pid $($process.Id))"
}

try {
Start-ShineProcess 'api' $venvPython $uvicornArgs $repoRoot
if ($startReview) {
    $reviewScript = if ($IsolatedReviewProcess) { 'scripts/local_review_isolated_server.py' } else { 'scripts/local_review_server.py' }
    Start-ShineProcess 'review' $ReviewPython @(
        (Join-Path $repoRoot $reviewScript),
        '--manifest', $ReviewManifest, '--runtime-packages', $ReviewRuntimePackages, '--port', "$ReviewPort"
    ) $repoRoot
    $reviewReady = $false
    for ($i = 0; $i -lt 120; $i++) {
        try {
            $reviewHealth = Invoke-RestMethod "http://127.0.0.1:$ReviewPort/health" -TimeoutSec 2
            if ($reviewHealth.status -eq 'ok') { $reviewReady = $true; break }
        } catch { }
        $reviewEntry = $started | Where-Object name -EQ 'review'
        if (-not (Get-Process -Id $reviewEntry.pid -ErrorAction SilentlyContinue)) { break }
        Start-Sleep -Milliseconds 500
    }
    if (-not $reviewReady) { throw "Hash-verified local reviewer did not start on port $ReviewPort" }
}
if ($AdditionalReviewManifest) {
    Start-ShineProcess 'review-additional' $ReviewPython @(
        (Join-Path $repoRoot 'scripts/local_review_isolated_server.py'),
        '--manifest', $AdditionalReviewManifest, '--runtime-packages', $ReviewRuntimePackages,
        '--port', "$AdditionalReviewPort"
    ) $repoRoot
    $additionalReady = $false
    for ($i = 0; $i -lt 120; $i++) {
        try {
            $additionalHealth = Invoke-RestMethod "http://127.0.0.1:$AdditionalReviewPort/health" -TimeoutSec 2
            if ($additionalHealth.status -eq 'ok') { $additionalReady = $true; break }
        } catch { }
        $additionalEntry = $started | Where-Object name -EQ 'review-additional'
        if (-not (Get-Process -Id $additionalEntry.pid -ErrorAction SilentlyContinue)) { break }
        Start-Sleep -Milliseconds 500
    }
    if (-not $additionalReady) { throw "Additional hash-verified local reviewer did not start on port $AdditionalReviewPort" }
}
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
    throw "API startup failed on port $ApiPort"
}
$started[0].listener_pid = $listenerPid
Write-Host "API is listening on port $ApiPort (pid $listenerPid)"

$webEntry = $started | Where-Object name -EQ 'web'
$webListener = $null
for ($i = 0; $i -lt 60; $i++) {
    $webListener = Get-PortOwner $WebPort
    if ($webListener) { break }
    if (-not (Get-Process -Id $webEntry.pid -ErrorAction SilentlyContinue)) { break }
    Start-Sleep -Milliseconds 500
}
if (-not $webListener) { throw "Web startup failed on port $WebPort" }
$webEntry.listener_pid = $webListener

# Capture verified birth identities for launchers and all descendants, including
# the worker's re-executed interpreter (which has no listening socket).
foreach ($entry in $started) {
    $identities = & $venvPython (Join-Path $PSScriptRoot 'shine_processes.py') $entry.pid $entry.birth
    if ($LASTEXITCODE -ne 0) { throw "Cannot identify $($entry.name) process tree" }
    $entry.owners = @($identities | ConvertFrom-Json)
}

$manifestPath = Join-Path $runDir 'processes.json'
[ordered]@{
    recorded_at = (Get-Date).ToString('o')
    repo_root = $repoRoot
    data_dir = $env:SHINE_DATA_DIR
    database_url = $databaseUrl
    render_provider = $Provider
    api_port = $ApiPort
    web_port = $WebPort
    review_port = if ($startReview) { $ReviewPort } else { $null }
    additional_review_port = if ($AdditionalReviewManifest) { $AdditionalReviewPort } else { $null }
    processes = $started
} | ConvertTo-Json -Depth 6 | Set-Content -Path $manifestPath -Encoding utf8
} catch {
    # Roll back only still-owned launcher trees created during this invocation.
    foreach ($entry in $started) {
        $process = Get-Process -Id $entry.pid -ErrorAction SilentlyContinue
        if ($process -and $process.StartTime.ToString('o') -eq $entry.start_time) {
            & $venvPython (Join-Path $PSScriptRoot 'shine_processes.py') $entry.pid $entry.birth --stop
        }
    }
    throw
}

Write-Host ''
Write-Host "Shine API:      http://127.0.0.1:$ApiPort/health"
Write-Host "Shine Web:      http://127.0.0.1:$WebPort"
Write-Host "Worker mode:    $Provider"
Write-Host "Logs:           $logDir"
Write-Host "Process record: $manifestPath"
Write-Host ''
Write-Host "Stop with:  `"$venvPython`" scripts/stop_shine.py --data-dir `"$env:SHINE_DATA_DIR`""
if ($Provider -eq 'comfyui') {
    Write-Warning 'ComfyUI mode is configured. A queued job is only a real video after a real ComfyUI attempt produced an artifact that passed technical QC.'
} else {
    Write-Host 'Mock mode simulates scheduling and QC only. It never creates or presents a video.'
}
Write-Host 'Open the Web workbench, approve and lock assets, freeze the Style Bible, then create shots.'
if ($OpenBrowser) {
    # The first installer launch must land in the guided setup, not on a blank
    # project page. Create one local setup project only when the workspace has
    # no project at all; never mutate an existing workspace.
    try {
        $apiBase = "http://127.0.0.1:$ApiPort/api/v1"
        # Invoke-RestMethod turns an empty JSON array into $null, and @($null)
        # still counts as one element. Normalise explicitly, otherwise a fresh
        # install never creates the guided setup project.
        $existing = $null
        try { $existing = Invoke-RestMethod "$apiBase/projects" -TimeoutSec 10 } catch { $existing = $null }
        $existingProjects = if ($null -eq $existing) { @() } else { @($existing) }
        if ($existingProjects.Count -eq 0) {
            $setupProject = @{ title = '本机安装与联调'; description = '由本地安装器创建的新机设置项目' } | ConvertTo-Json
            $setupProjectBytes = [System.Text.Encoding]::UTF8.GetBytes($setupProject)
            Invoke-RestMethod "$apiBase/projects" -Method Post -ContentType 'application/json; charset=utf-8' `
                -Body $setupProjectBytes -TimeoutSec 15 | Out-Null
            Write-Host 'Created the guided setup project: 本机安装与联调'
        }
        Start-Process "http://127.0.0.1:$WebPort/#setup"
    } catch {
        Write-Warning "The workbench started, but the guided browser entry could not be opened: $($_.Exception.Message)"
        Start-Process "http://127.0.0.1:$WebPort"
    }
}
