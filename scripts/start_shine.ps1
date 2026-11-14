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
    [switch]$OpenBrowser,
    [switch]$AutoPorts,
    [ValidateRange(1,65535)][int]$SupervisorPort = 8790
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
# The API, the Worker and the Web write their diagnostics to log files; a
# block-buffered pipe would hide a stall until the process ends.
$env:PYTHONUNBUFFERED = '1'
New-Item -ItemType Directory -Force -Path $env:SHINE_DATA_DIR | Out-Null
# One data directory, one launcher at a time: the manifest check, the
# reconcile, the port decision, the dependency installation, the process
# start and the manifest write all happen inside one cross-process critical
# section, so two double-clicks can never observe "no manifest" together.
. (Join-Path $PSScriptRoot 'shine_startup_guard.ps1')

function Get-PortOwner([int]$Port) {
    $listener = Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue |
        Select-Object -First 1
    if ($listener) { return [int]$listener.OwningProcess }
    return 0
}

function Get-FreeShinePort([int]$Start, [int[]]$Exclude = @()) {
    for ($port = $Start; $port -lt ($Start + 60); $port++) {
        if ($Exclude -contains $port) { continue }
        if (-not (Get-PortOwner $port)) { return $port }
    }
    throw "No free local port between $Start and $($Start + 59)."
}

$receiptPath = Join-Path $env:SHINE_DATA_DIR 'run/receipts.json'

function Get-FileFingerprint([string]$Path) {
    if (-not (Test-Path -LiteralPath $Path)) { return '' }
    try { return (Get-FileHash -LiteralPath $Path -Algorithm SHA256).Hash.ToLower() } catch { return '' }
}

function Get-ShineReceipts {
    if (-not (Test-Path -LiteralPath $receiptPath)) { return $null }
    try { return Get-Content -LiteralPath $receiptPath -Raw -Encoding UTF8 | ConvertFrom-Json } catch { return $null }
}

function Save-ShineReceipt([string]$Name, [hashtable]$Data) {
    # A success receipt replaces "the directory exists" as the proof that a
    # step really finished.
    $receipts = Get-ShineReceipts
    if (-not $receipts) { $receipts = New-Object psobject }
    $entry = [ordered]@{ at = (Get-Date).ToString('o') }
    foreach ($key in $Data.Keys) { $entry[$key] = $Data[$key] }
    $receipts | Add-Member -NotePropertyName $Name -NotePropertyValue ([pscustomobject]$entry) -Force
    New-Item -ItemType Directory -Force -Path (Split-Path -Parent $receiptPath) | Out-Null
    $receipts | ConvertTo-Json -Depth 6 | Set-Content -LiteralPath $receiptPath -Encoding utf8
}

function Test-ShineReceipt([string]$Name, [string]$Fingerprint) {
    $receipts = Get-ShineReceipts
    if (-not $receipts) { return $false }
    $entry = $receipts.$Name
    if (-not $entry) { return $false }
    return ([string]$entry.fingerprint -eq $Fingerprint)
}

function Test-WebAlive([int]$Port) {
    if (-not $Port) { return $false }
    try {
        $response = Invoke-WebRequest "http://127.0.0.1:$Port/" -TimeoutSec 4 -UseBasicParsing
        return ($response.StatusCode -ge 200 -and $response.StatusCode -lt 500)
    } catch {
        return $false
    }
}

function Test-RecordedApiAlive($Record) {
    $port = [int]$Record.api_port
    if (-not $port) { return $false }
    try {
        $health = Invoke-RestMethod "http://127.0.0.1:$port/health" -TimeoutSec 3
        if (-not $health) { return $false }
    } catch {
        return $false
    }
    $api = @($Record.processes) | Where-Object { $_.name -eq 'api' } | Select-Object -First 1
    $expected = if ($api -and $api.listener_pid) { [int]$api.listener_pid } else { 0 }
    if (-not $expected) { return $false }
    return ((Get-PortOwner $port) -eq $expected)
}

function Test-RecordedProcessAlive($Entry) {
    # Shared implementation (culture-safe against JSON-roundtripped dates).
    return (Test-ShineRecordedProcessAlive $Entry)
}

function Test-RecordedWorkerAlive($Record) {
    $worker = @($Record.processes) | Where-Object { $_.name -eq 'worker' } | Select-Object -First 1
    if (-not $worker) { return $true }  # the stack was started without a worker
    return (Test-RecordedProcessAlive $worker)
}

function Test-RecordedWebAlive($Record) {
    # A bound port is not enough: the recorded listener must still own it.
    $port = [int]$Record.web_port
    if (-not $port) { return $false }
    $web = @($Record.processes) | Where-Object { $_.name -eq 'web' } | Select-Object -First 1
    $expected = if ($web -and $web.listener_pid) { [int]$web.listener_pid } else { 0 }
    if (-not $expected) { return $false }
    if ((Get-PortOwner $port) -ne $expected) { return $false }
    return (Test-WebAlive $port)
}

$manifestPath = Join-Path $env:SHINE_DATA_DIR 'run/processes.json'

function Save-StackManifest([string]$Path, [object[]]$Entries) {
    # Write the manifest atomically (temp file on the same volume, then a
    # replace), so a reader never observes a half-written record even after
    # an interrupted launch. Every launcher reads it under the startup guard.
    $doc = [ordered]@{
        recorded_at = (Get-Date).ToString('o')
        repo_root = $repoRoot
        data_dir = $env:SHINE_DATA_DIR
        database_url = $databaseUrl
        render_provider = $Provider
        api_port = $ApiPort
        web_port = $WebPort
        review_port = if ($startReview) { $ReviewPort } else { $null }
        additional_review_port = if ($AdditionalReviewManifest) { $AdditionalReviewPort } else { $null }
        processes = @($Entries)
    }
    $json = $doc | ConvertTo-Json -Depth 6
    $tmp = "$Path.new-$PID"
    [System.IO.File]::WriteAllText($tmp, $json, (New-Object System.Text.UTF8Encoding($true)))
    if (Test-Path -LiteralPath $Path) {
        # PowerShell marshals a bare $null string argument to an empty string,
        # which File.Replace rejects as an invalid backup path; [NullString]
        # carries a real null so the replace stays atomic.
        [System.IO.File]::Replace($tmp, $Path, [NullString]::Value)
    } else {
        [System.IO.File]::Move($tmp, $Path)
    }
}

function Add-ProcessOwners([object]$Entry) {
    # Capture the verified birth identities of the launcher tree immediately,
    # not only at the end: a launcher that dies mid-start leaves a manifest
    # whose entries can still be reconciled process by process.
    $identities = & $venvPython (Join-Path $PSScriptRoot 'shine_processes.py') $Entry.pid $Entry.birth
    if ($LASTEXITCODE -ne 0) { throw "Cannot identify $($Entry.name) process tree" }
    # Windows PowerShell 5.1 pipes a top-level JSON array as one array object,
    # which ConvertTo-Json serializes as { value: [...], Count: n }. Parse the
    # complete JSON input directly so the manifest always contains owner rows.
    $identityJson = ($identities -join [Environment]::NewLine).Trim()
    $parsedIdentities = ConvertFrom-Json -InputObject $identityJson
    $Entry.owners = @($parsedIdentities)
}

$startupGuard = Enter-ShineStartupGuard -DataDir $env:SHINE_DATA_DIR
$existingWebPort = $null
$started = @()
try {
    if (Test-Path $manifestPath) {
        $record = $null
        try { $record = Get-Content -LiteralPath $manifestPath -Raw -Encoding UTF8 | ConvertFrom-Json } catch { $record = $null }
        $apiAlive = $false
        $webAlive = $false
        $workerAlive = $false
        if ($record) {
            $apiAlive = Test-RecordedApiAlive $record
            $webAlive = Test-RecordedWebAlive $record
            $workerAlive = Test-RecordedWorkerAlive $record
        }
        if ($record -and $apiAlive -and $webAlive -and $workerAlive) {
            # Deliberately no exit here: the guard must be released in the
            # finally below before this process may terminate.
            $existingWebPort = [int]$record.web_port
        } else {
            # A half-dead stack must not brick the only start button. Stop what this
            # record really owns; if its identity cannot be proven, archive the record
            # and let the port check below decide whether a real conflict remains.
            $missing = @()
            if (-not $apiAlive) { $missing += 'API' }
            if (-not $webAlive) { $missing += 'Web' }
            if (-not $workerAlive) { $missing += 'Worker' }
            Write-Warning ("The previous run did not leave a complete stack (missing: " + ($missing -join ', ') + '); reconciling it first.')
            $previous = $ErrorActionPreference
            $ErrorActionPreference = 'Continue'
            # This stop runs inside the critical section this script already
            # holds; the marker keeps stop_shine.py from deadlocking on the
            # same named mutex (nested acquisition of one's own guard).
            $env:SHINE_STARTUP_GUARD_HELD = '1'
            try { & $venvPython (Join-Path $PSScriptRoot 'stop_shine.py') --data-dir $env:SHINE_DATA_DIR } finally {
                Remove-Item Env:\SHINE_STARTUP_GUARD_HELD -ErrorAction SilentlyContinue
                $ErrorActionPreference = $previous
            }
            if ($LASTEXITCODE -ne 0 -and (Test-Path -LiteralPath $manifestPath)) {
                $archived = Join-Path $env:SHINE_DATA_DIR ('run/processes.unresolved-{0}.json' -f (Get-Date -Format 'yyyyMMdd-HHmmss'))
                Move-Item -LiteralPath $manifestPath -Destination $archived -Force
                Write-Host "Preserved the unverified record at $archived; no unverified process was killed." -ForegroundColor Yellow
            }
            if (Test-Path -LiteralPath $manifestPath) {
                throw 'This data directory still has a process manifest. Stop or reconcile that stack before restarting.'
            }
        }
    }
    if (-not $existingWebPort) {
        $env:SHINE_RENDER_PROVIDER = $Provider

        # Installed copies and the packaged START_APP use -AutoPorts: a busy 8000 must
        # not make the only start button unusable. The chosen ports are printed and
        # recorded, so the browser and the stop command use the same ones.
        if ($AutoPorts) {
            $resolvedApiPort = if (Get-PortOwner $ApiPort) { Get-FreeShinePort $ApiPort } else { $ApiPort }
            $reserved = @($resolvedApiPort)
            if ($startReview) { $reserved += $ReviewPort }
            if ($AdditionalReviewManifest) { $reserved += $AdditionalReviewPort }
            $resolvedWebPort = if ((Get-PortOwner $WebPort) -or ($reserved -contains $WebPort)) {
                Get-FreeShinePort $WebPort $reserved
            } else { $WebPort }
            if ($resolvedApiPort -ne $ApiPort -or $resolvedWebPort -ne $WebPort) {
                Write-Host "Requested ports are busy; using API $resolvedApiPort / Web $resolvedWebPort instead." -ForegroundColor Yellow
            }
            $ApiPort = $resolvedApiPort
            $WebPort = $resolvedWebPort
        }
        $env:SHINE_VITE_API_TARGET = "http://127.0.0.1:$ApiPort"

        $backendLock = Join-Path $repoRoot 'backend/uv.lock'
        $backendFingerprint = Get-FileFingerprint $backendLock
        if ($Install -or -not (Test-Path $backendEnv) -or -not (Test-ShineReceipt 'backend_dependencies' $backendFingerprint)) {
            Invoke-Native 'Python dependency installation' {
                & uv sync --project (Join-Path $repoRoot 'backend') --locked
            }
            Save-ShineReceipt 'backend_dependencies' @{ fingerprint = $backendFingerprint; command = 'uv sync --locked' }
        }
        $frontendLock = Join-Path $repoRoot 'frontend/package-lock.json'
        $frontendFingerprint = Get-FileFingerprint $frontendLock
        if ($Install -or -not (Test-Path (Join-Path $repoRoot 'frontend/node_modules')) -or
            -not (Test-ShineReceipt 'frontend_dependencies' $frontendFingerprint)) {
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
            Save-ShineReceipt 'frontend_dependencies' @{ fingerprint = $frontendFingerprint; command = 'npm ci' }
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
        $checkPorts = @($ApiPort, $WebPort)
        if ($startReview) { $checkPorts += $ReviewPort }
        if ($AdditionalReviewManifest) { $checkPorts += $AdditionalReviewPort }
        & (Join-Path $PSScriptRoot 'check_environment.ps1') -Ports $checkPorts

        Write-Host "Data directory: $env:SHINE_DATA_DIR"
        Write-Host "Database:       $databaseUrl"

        $migrationLog = Join-Path $env:TEMP 'shine-alembic-upgrade.log'
        Invoke-Native 'Database migration' {
            & $venvPython -m alembic -c (Join-Path $repoRoot 'backend/alembic.ini') upgrade head 2> $migrationLog
        }
        Get-Content $migrationLog -Tail 5 -ErrorAction SilentlyContinue | ForEach-Object { Write-Host $_ }
        Save-ShineReceipt 'database_migration' @{
            fingerprint = Get-FileFingerprint $migrationLog
            database = $databaseUrl
            command = 'alembic upgrade head'
        }

        $uvicornArgs = @(
            '-m', 'uvicorn', 'shine.main:app',
            '--host', '127.0.0.1', '--port', "$ApiPort"
        )
        $viteArgs = @(
            (Join-Path $repoRoot 'frontend/node_modules/vite/bin/vite.js'),
            '--host', '127.0.0.1', '--port', "$WebPort", '--strictPort'
        )

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

        # ComfyUI and the local reviewers belong to this workbench. A renderer or a
        # reviewer that outlived an earlier run keeps the model in GPU memory and
        # blocks the next run, so a clean start begins by releasing what is left.
        Write-Host 'Releasing leftover backends of this installation...' -ForegroundColor Gray
        $residualScript = Join-Path $repoRoot 'scripts/release_residual_backends.py'
        if (Test-Path -LiteralPath $residualScript) {
            $residualArgs = @($residualScript, '--data-dir', $env:SHINE_DATA_DIR, '--review-port', "$ReviewPort")
            if ($AdditionalReviewManifest) { $residualArgs += @('--review-port', "$AdditionalReviewPort") }
            & $venvPython @residualArgs
            if ($LASTEXITCODE -ne 0) {
                Write-Host 'A leftover backend could not be released; see the lines above.' -ForegroundColor Yellow
            }
        }

        function Start-ShineProcess([string]$Name, [string]$FilePath, [string[]]$Arguments, [string]$WorkingDirectory) {
            $stdout = Join-Path $logDir "$Name.out.log"
            $stderr = Join-Path $logDir "$Name.err.log"
            $process = Start-Process -PassThru -WindowStyle Hidden -FilePath $FilePath `
                -ArgumentList ($Arguments | ForEach-Object { '"' + $_ + '"' }) -WorkingDirectory $WorkingDirectory `
                -RedirectStandardOutput $stdout -RedirectStandardError $stderr
            $info = Get-Process -Id $process.Id -ErrorAction SilentlyContinue
            $entry = [ordered]@{
                name = $Name
                pid = $process.Id
                start_time = if ($info) { $info.StartTime.ToString('o') } else { $null }
                birth = if ($info) { $info.StartTime.ToUniversalTime().ToFileTimeUtc().ToString() } else { $null }
                working_dir = $WorkingDirectory
                stdout_log = $stdout
                stderr_log = $stderr
                command = "$FilePath $($Arguments -join ' ')"
            }
            $script:started += $entry
            Write-Host "Started $Name (pid $($process.Id))"
            # Persist incrementally: every launcher reader holds the startup
            # guard, so a mid-start interruption leaves a reconcilable record
            # instead of four anonymous processes.
            Add-ProcessOwners $entry
            Save-StackManifest $script:manifestPath ([object[]]$script:started)
        }

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
        Save-StackManifest $manifestPath ([object[]]$started)
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

        # A bound port is not a usable page: wait until Vite answers the document so the
        # browser never opens on ERR_CONNECTION_REFUSED.
        $webReady = $false
        for ($i = 0; $i -lt 60; $i++) {
            try {
                $page = Invoke-WebRequest "http://127.0.0.1:$WebPort/" -TimeoutSec 2 -UseBasicParsing
                if ($page.StatusCode -ge 200 -and $page.StatusCode -lt 500) { $webReady = $true; break }
            } catch { }
            if (-not (Get-Process -Id $webEntry.pid -ErrorAction SilentlyContinue)) { break }
            Start-Sleep -Milliseconds 500
        }
        if (-not $webReady) {
            Get-Content $webEntry.stderr_log -Tail 15 -ErrorAction SilentlyContinue | ForEach-Object { Write-Host "  $_" }
            throw "Web did not serve a page on port $WebPort"
        }

        # Capture verified birth identities for launchers and all descendants, including
        # the worker's re-executed interpreter (which has no listening socket).
        foreach ($entry in $started) {
            Add-ProcessOwners $entry
        }
        Save-StackManifest $manifestPath ([object[]]$started)
        # The stack is only healthy when the API, the Web and the Worker are all up.
        Save-ShineReceipt 'service_health' @{
            fingerprint = (Get-FileFingerprint $manifestPath)
            api_port = $ApiPort
            web_port = $WebPort
            worker = (-not $NoWorker)
            command = 'start_shine'
        }

        # The resident supervisor lets the page restart a dead backend (the API
        # cannot restart itself). It is recorded in run/supervisor.json, NOT in
        # processes.json, so stop_shine.py never kills it mid-restart.
        $supervisorAlive = $false
        try {
            $sup = Invoke-RestMethod "http://127.0.0.1:$SupervisorPort/health" -TimeoutSec 2
            $supervisorAlive = ($sup.role -eq 'supervisor')
        } catch { }
        if (-not $supervisorAlive) {
            $supLog = Join-Path $logDir 'supervisor.out.log'
            $supErr = Join-Path $logDir 'supervisor.err.log'
            Start-Process -WindowStyle Hidden -FilePath $venvPython `
                -ArgumentList @(
                    (Join-Path $PSScriptRoot 'shine_supervisor.py'),
                    '--data-dir', $env:SHINE_DATA_DIR,
                    '--port', "$SupervisorPort",
                    '--api-port', "$ApiPort",
                    '--web-port', "$WebPort"
                ) -WorkingDirectory $repoRoot `
                -RedirectStandardOutput $supLog -RedirectStandardError $supErr | Out-Null
            Write-Host "Started supervisor on port $SupervisorPort (page can restart the stack)"
        }
    }
} catch {
    # Roll back only still-owned launcher trees created during this invocation.
    foreach ($entry in $started) {
        $process = Get-Process -Id $entry.pid -ErrorAction SilentlyContinue
        if ($process -and $process.StartTime.ToString('o') -eq $entry.start_time) {
            & $venvPython (Join-Path $PSScriptRoot 'shine_processes.py') $entry.pid $entry.birth --stop
        }
    }
    throw
} finally {
    Exit-ShineStartupGuard $startupGuard
}

if ($existingWebPort) {
    Write-Host "The workbench is already running: http://127.0.0.1:$existingWebPort/"
    if ($OpenBrowser) { Start-Process "http://127.0.0.1:$existingWebPort/" }
    exit 0
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
    $openUrl = "http://127.0.0.1:$WebPort/"
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
            # The director layer owns its own setup page; an existing user
            # keeps landing on the workbench home instead.
            $openUrl = "http://127.0.0.1:$WebPort/#d/setup"
        }
        Start-Process $openUrl
    } catch {
        Write-Warning "The workbench started, but the browser entry could not be opened: $($_.Exception.Message)"
        Start-Process $openUrl
    }
}
