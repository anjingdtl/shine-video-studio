# Local development launcher: one double-click starts the workbench.
#
# This is the *repository* entry, not the tester package. It assumes this checkout
# already has backend/.venv and frontend/node_modules (the installer or
# `uv sync` + `npm ci` created them) and never downloads tools or models.
#
# Behaviour:
#   * already running  -> only reopen the browser
#   * stale run record -> reconcile, then start
#   * free ports       -> default 8000/5173, advanced automatically when busy
#   * closes itself 12 s after a successful start; keeps the window on failure
#
# Usage:
#   powershell -NoProfile -ExecutionPolicy Bypass -File scripts\start_dev.ps1
#   ... -ApiPort 8010 -WebPort 5180 -KeepOpen

param(
    [ValidateRange(1, 65535)][int]$ApiPort = 8000,
    [ValidateRange(1, 65535)][int]$WebPort = 5173,
    [ValidateRange(0, 600)][int]$CloseAfterSeconds = 12,
    [switch]$KeepOpen
)

$ErrorActionPreference = 'Stop'
$repoRoot = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
$startScript = Join-Path $PSScriptRoot 'start_shine.ps1'
$stopScript = Join-Path $PSScriptRoot 'stop_shine.py'
$dataDir = Join-Path $repoRoot '.local-data'
$envFile = Join-Path $repoRoot '.env'
$backendPython = Join-Path $repoRoot 'backend/.venv/Scripts/python.exe'
$frontendModules = Join-Path $repoRoot 'frontend/node_modules'
$pwshPath = (Get-Command pwsh -ErrorAction SilentlyContinue).Source

try {
    [Console]::OutputEncoding = [Text.Encoding]::UTF8
    $OutputEncoding = [Text.Encoding]::UTF8
} catch { }
try { $Host.UI.RawUI.WindowTitle = 'Shine 开发版 · 本地工作台' } catch { }

function Write-Step([string]$text) { Write-Host "  -> $text" -ForegroundColor Gray }
function Write-Ok([string]$text) { Write-Host "  [OK] $text" -ForegroundColor Green }
function Write-Warn([string]$text) { Write-Host "  [注意] $text" -ForegroundColor Yellow }
function Write-Fail([string]$text) { Write-Host "  [失败] $text" -ForegroundColor Red }

function Get-EnvValue([string]$Path, [string]$Key) {
    if (-not (Test-Path -LiteralPath $Path)) { return '' }
    $content = Get-Content -LiteralPath $Path -Raw -Encoding UTF8
    $match = [regex]::Match($content, "(?m)^$Key=(.*)$")
    if (-not $match.Success) { return '' }
    $value = $match.Groups[1].Value.Trim()
    if ($value.StartsWith("'") -or $value.StartsWith('"')) {
        if ($value.Length -lt 2) { return '' }
        $value = $value.Substring(1, $value.Length - 2).Replace("\'", "'").Replace('\\', '\')
    } else {
        $value = ($value -replace '[ \t]+#.*$', '').Trim()
    }
    return $value
}

function Test-PortFree([int]$Port) {
    # Ask the OS first: a TcpListener may still bind a port another process
    # holds when address reuse is permitted, which made the launcher believe a
    # busy port was free and hand it to start_shine.ps1 (which then failed).
    if (Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue) {
        return $false
    }
    $listener = $null
    try {
        $listener = New-Object System.Net.Sockets.TcpListener([System.Net.IPAddress]::Loopback, $Port)
        $listener.Start()
        return $true
    } catch {
        return $false
    } finally {
        if ($listener) { try { $listener.Stop() } catch { } }
    }
}

function Get-FreePort([int]$Start) {
    for ($port = $Start; $port -lt ($Start + 40); $port++) {
        if (Test-PortFree $port) { return $port }
    }
    throw "No free local port between $Start and $($Start + 39)."
}

function Test-ApiAlive([int]$Port) {
    if (-not $Port) { return $false }
    try {
        $health = Invoke-RestMethod "http://127.0.0.1:$Port/health" -TimeoutSec 3
        return [bool]$health
    } catch {
        return $false
    }
}

function Test-WebAlive([int]$Port) {
    if (-not $Port) { return $false }
    try {
        # A listening socket is not a working page: Vite binds the port before
        # it serves the first request. Ask for the document itself.
        $response = Invoke-WebRequest "http://127.0.0.1:$Port/" -TimeoutSec 4 -UseBasicParsing
        return ($response.StatusCode -ge 200 -and $response.StatusCode -lt 500)
    } catch {
        return $false
    }
}

function Get-PortOwner([int]$Port) {
    $listener = Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue |
        Select-Object -First 1
    if ($listener) { return [int]$listener.OwningProcess }
    return 0
}

function Test-RecordedApiAlive($Record) {
    # Only the process this record started may count as "our API". A stray
    # developer server on the same port used to make a dead stack look alive
    # and sent the browser to an empty web port.
    $port = [int]$Record.api_port
    if (-not (Test-ApiAlive $port)) { return $false }
    $api = @($Record.processes) | Where-Object { $_.name -eq 'api' } | Select-Object -First 1
    $expected = if ($api -and $api.listener_pid) { [int]$api.listener_pid } else { 0 }
    if (-not $expected) { return $false }
    return ((Get-PortOwner $port) -eq $expected)
}

function Show-Tail([string]$Path, [int]$Lines = 12) {
    if (Test-Path -LiteralPath $Path) {
        Get-Content -LiteralPath $Path -Tail $Lines -ErrorAction SilentlyContinue |
            ForEach-Object { Write-Host "    $_" -ForegroundColor DarkGray }
    }
}

Write-Host ''
Write-Host ('=' * 62) -ForegroundColor DarkCyan
Write-Host '  Shine 视频工作台 · 本机开发版' -ForegroundColor Cyan
Write-Host ('=' * 62) -ForegroundColor DarkCyan

Write-Step '检查开发环境'
if (-not (Test-Path -LiteralPath $backendPython)) {
    Write-Fail "缺少后端环境：$backendPython"
    Write-Host '  先执行（任选其一）：' -ForegroundColor Yellow
    Write-Host '    pwsh -File scripts\bootstrap.ps1 -StorageRoot <非系统盘目录> -NoLaunch' -ForegroundColor Yellow
    Write-Host '    cd backend; uv sync --locked' -ForegroundColor Yellow
    if (-not $KeepOpen) { Read-Host '按回车关闭' | Out-Null }
    exit 2
}
if (-not (Test-Path -LiteralPath $frontendModules)) {
    Write-Fail "缺少前端依赖：$frontendModules"
    Write-Host '  先执行：cd frontend; npm ci' -ForegroundColor Yellow
    if (-not $KeepOpen) { Read-Host '按回车关闭' | Out-Null }
    exit 2
}
Write-Ok '后端环境与前端依赖已就位'

$storedData = Get-EnvValue $envFile 'SHINE_DATA_DIR'
if ($storedData) { $dataDir = $storedData }
if (-not (Test-Path -LiteralPath $dataDir)) { New-Item -ItemType Directory -Force -Path $dataDir | Out-Null }
$runDir = Join-Path $dataDir 'run'
$manifestPath = Join-Path $runDir 'processes.json'
$logDir = Join-Path $runDir 'logs'

# Already running: never start a second stack, just bring the browser back.
# The manifest decision runs inside the startup guard: another launcher may be
# mid-start with a partial manifest on disk, and this check must observe the
# same critical section. The guard is released before start_shine.ps1 takes
# it again, so the nested call never deadlocks on itself.
. (Join-Path $PSScriptRoot 'shine_startup_guard.ps1')
$devGuard = Enter-ShineStartupGuard -DataDir $dataDir
$alreadyRunningUrl = $null
try {
    if (Test-Path -LiteralPath $manifestPath) {
        $record = $null
        try { $record = Get-Content -LiteralPath $manifestPath -Raw -Encoding UTF8 | ConvertFrom-Json } catch { $record = $null }
        $recordedApiAlive = $false
        $recordedWebAlive = $false
        if ($record) {
            $recordedApiAlive = Test-RecordedApiAlive $record
            $recordedWebAlive = Test-WebAlive ([int]$record.web_port)
        }
        if ($record -and $recordedApiAlive -and $recordedWebAlive) {
            $alreadyRunningUrl = "http://127.0.0.1:$($record.web_port)/"
        } else {
            if ($record -and ($recordedApiAlive -or $recordedWebAlive)) {
                # One half of the stack answered while the other did not. Opening the
                # browser here would show ERR_CONNECTION_REFUSED; stop what this record
                # owns and start a complete stack again.
                Write-Warn '上次只启动了一部分服务（API 与网页没有同时在线），正在收尾并重启'
            } else {
                Write-Warn '上次运行没有正常退出，正在安全收尾（只结束身份匹配的进程）'
            }
            $previous = $ErrorActionPreference
            $ErrorActionPreference = 'Continue'
            # This stop runs inside the critical section this script already
            # holds; the marker keeps stop_shine.py from deadlocking on the
            # same named mutex (nested acquisition of one's own guard).
            $env:SHINE_STARTUP_GUARD_HELD = '1'
            try { & $backendPython $stopScript --data-dir $dataDir } finally {
                Remove-Item Env:\SHINE_STARTUP_GUARD_HELD -ErrorAction SilentlyContinue
                $ErrorActionPreference = $previous
            }
            if ($LASTEXITCODE -ne 0) {
                # 仍有进程身份无法核对。这里绝不盲杀进程，但也不能让一条无法核对的
                # 残留记录永久卡死唯一入口：上面已确认 API 不在运行，下面还会再确认
                # 端口空闲，因此把无法核对的记录归档后继续启动。
                Write-Warn '部分进程身份无法确认，已原样保留且不会盲杀它们；本次继续启动。'
                try {
                    if (Test-Path -LiteralPath $manifestPath) {
                        $archived = Join-Path $runDir ('processes.unresolved-{0}.json' -f (Get-Date -Format 'yyyyMMdd-HHmmss'))
                        Move-Item -LiteralPath $manifestPath -Destination $archived -Force
                        Write-Host "  未核对记录已归档：$archived" -ForegroundColor Gray
                        # 只保留最近 5 份归档，避免个人机器上无限堆积。
                        Get-ChildItem -LiteralPath $runDir -Filter 'processes.unresolved-*.json' -File -ErrorAction SilentlyContinue |
                            Sort-Object Name -Descending | Select-Object -Skip 5 |
                            ForEach-Object { Remove-Item -LiteralPath $_.FullName -Force -ErrorAction SilentlyContinue }
                    }
                } catch {
                    Write-Fail "未核对记录归档失败：$($_.Exception.Message)"
                    if (-not $KeepOpen) { Read-Host '按回车关闭' | Out-Null }
                    exit 3
                }
            } else {
                Write-Ok '陈旧记录已清理'
            }
        }
    }
} finally {
    Exit-ShineStartupGuard $devGuard
}
if ($alreadyRunningUrl) {
    Write-Ok "工作台已在运行：$alreadyRunningUrl"
    Write-Host "  正在打开：$alreadyRunningUrl" -ForegroundColor Green
    Start-Process $alreadyRunningUrl
    Write-Host ''
    Write-Host '  停止服务请双击桌面「Shine 停止」或执行 scripts\stop_shine.py' -ForegroundColor Gray
    if (-not $KeepOpen) { Start-Sleep -Seconds ([Math]::Max(3, $CloseAfterSeconds)) }
    exit 0
}

Write-Step '选择端口'
# PowerShell variable names are case-insensitive: assigning to $apiPort would
# overwrite the $ApiPort parameter, so keep the requested defaults first.
$defaultApiPort = $ApiPort
$defaultWebPort = $WebPort
$apiPort = Get-FreePort $defaultApiPort
$webPort = Get-FreePort $defaultWebPort
if ($webPort -eq $apiPort) { $webPort = Get-FreePort ($apiPort + 1) }
if ($apiPort -ne $defaultApiPort -or $webPort -ne $defaultWebPort) {
    Write-Warn "默认端口被占用，已改用 API $apiPort / Web $webPort"
} else {
    Write-Ok "API $apiPort / Web $webPort"
}

Write-Host ''
Write-Step '启动 API / Worker / Web（首次约 10-30 秒）'
$runner = $pwshPath
if (-not $runner) { $runner = (Get-Process -Id $PID).Path }
$startArgs = @(
    '-NoProfile', '-ExecutionPolicy', 'Bypass', '-File', $startScript,
    '-ApiPort', "$apiPort", '-WebPort', "$webPort", '-OpenBrowser'
)
$previous = $ErrorActionPreference
$ErrorActionPreference = 'Continue'
try { & $runner @startArgs; $startCode = $LASTEXITCODE }
catch { Write-Fail $_.Exception.Message; $startCode = 1 }
finally { $ErrorActionPreference = $previous }

if ($startCode -ne 0 -or -not (Test-ApiAlive $apiPort)) {
    Write-Host ''
    Write-Fail '启动失败'
    Write-Host "  API 日志：$(Join-Path $logDir 'api.err.log')" -ForegroundColor Gray
    Show-Tail (Join-Path $logDir 'api.err.log')
    Write-Host ''
    Write-Host '  常见原因：端口被占用、依赖未同步、上一套服务没停干净。' -ForegroundColor Yellow
    if (-not $KeepOpen) { Read-Host '按回车关闭' | Out-Null }
    exit 1
}

# The launcher prints its own banner; report the facts a developer needs.
$mode = 'mock'
if ($env:SHINE_RENDER_PROVIDER) { $mode = $env:SHINE_RENDER_PROVIDER }
$storedMode = Get-EnvValue $envFile 'SHINE_RENDER_PROVIDER'
if ($storedMode) { $mode = $storedMode }

Write-Host ''
Write-Host ('=' * 62) -ForegroundColor DarkGreen
Write-Host '  已启动' -ForegroundColor Green
Write-Host ('=' * 62) -ForegroundColor DarkGreen
Write-Host "  工作台：  http://127.0.0.1:$webPort/" -ForegroundColor Green
Write-Host "  API：     http://127.0.0.1:$apiPort/health" -ForegroundColor Gray
Write-Host "  数据目录：$dataDir" -ForegroundColor Gray
Write-Host "  渲染模式：$mode（改 .env 的 SHINE_RENDER_PROVIDER，或 start_shine.ps1 -Provider）" -ForegroundColor Gray
Write-Host "  日志：    $logDir" -ForegroundColor Gray
Write-Host '  前端已开启热更新；后端改动需要重启用本快捷方式。' -ForegroundColor Gray
Write-Host ''
Write-Host '  停止：桌面「Shine 停止」，或 scripts\stop_shine.py --data-dir <数据目录>' -ForegroundColor Gray
if (-not $KeepOpen) {
    Write-Host ''
    Write-Host "  窗口将在 $CloseAfterSeconds 秒后自动关闭（-KeepOpen 可保留）" -ForegroundColor DarkGray
    Start-Sleep -Seconds ([Math]::Max(3, $CloseAfterSeconds))
}
exit 0
