# One-click installer and launcher for the Shine Windows test build.
#
# Contract: a tester double-clicks one .cmd, answers nothing, and ends up in the
# guided setup wizard at http://127.0.0.1:<port>/#setup.  Everything that can be
# decided safely is decided here:
#   * the install drive (largest non-system fixed disk with >= 180 GiB free)
#   * no operator credential for the personal desktop trust boundary
#   * free API/Web ports
#   * the data directory and every cache directory (all on the selected disk)
# It never installs drivers, never edits system-wide state, and never downloads
# model weights: those belong to the reviewed plan inside the Web wizard.
#
# Usage:
#   powershell -NoProfile -ExecutionPolicy Bypass -File scripts\install_once.ps1
#   ... -StorageRoot D:\Shine\Custom   (advanced override)
#   ... -CheckOnly                     (read-only environment report)

param(
    [string]$StorageRoot = '',
    [switch]$CheckOnly,
    [switch]$NoLaunch
)

$ErrorActionPreference = 'Stop'
$repoRoot = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
$requiredBytes = 180GB
$recommendedBytes = 200GB
$bootstrapScript = Join-Path $PSScriptRoot 'bootstrap.ps1'
$startScript = Join-Path $PSScriptRoot 'start_shine.ps1'
$defaultApiPort = 8000
$defaultWebPort = 5173
$transcript = $null

try {
    [Console]::OutputEncoding = [Text.Encoding]::UTF8
    $OutputEncoding = [Text.Encoding]::UTF8
} catch { }

function Write-Banner([string]$text) {
    Write-Host ''
    Write-Host ('=' * 66) -ForegroundColor DarkCyan
    Write-Host "  $text" -ForegroundColor Cyan
    Write-Host ('=' * 66) -ForegroundColor DarkCyan
}

function Write-Step([string]$text) { Write-Host "  -> $text" -ForegroundColor Gray }
function Write-Ok([string]$text) { Write-Host "  [OK] $text" -ForegroundColor Green }
function Write-Warn([string]$text) { Write-Host "  [注意] $text" -ForegroundColor Yellow }
function Write-Fail([string]$text) { Write-Host "  [失败] $text" -ForegroundColor Red }

function Get-ShineEnvValue([string]$Path, [string]$Key) {
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

function Get-ShineDiskInventory {
    $items = @()
    try {
        $items = @(Get-CimInstance -ClassName Win32_LogicalDisk -Filter 'DriveType=3' -ErrorAction Stop |
            ForEach-Object {
                [pscustomobject]@{ Drive = $_.DeviceID; Free = [long]$_.FreeSpace; Size = [long]$_.Size }
            })
    } catch {
        $items = @(Get-PSDrive -PSProvider FileSystem -ErrorAction SilentlyContinue |
            Where-Object { $_.Free -ne $null } |
            ForEach-Object { [pscustomobject]@{ Drive = ($_.Name + ':'); Free = [long]$_.Free; Size = [long]($_.Used + $_.Free) } })
    }
    return $items
}

function Get-GibString([long]$bytes) { return ('{0:N1} GiB' -f ($bytes / 1GB)) }

function Test-ShinePortFree([int]$Port) {
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

function Get-ShineFreePort([int]$Start) {
    for ($port = $Start; $port -lt ($Start + 60); $port++) {
        if (Test-ShinePortFree $port) { return $port }
    }
    throw "No free local port between $Start and $($Start + 59)."
}

function Test-ShineApiAlive([int]$Port) {
    if (-not $Port) { return $false }
    try {
        $health = Invoke-RestMethod "http://127.0.0.1:$Port/health" -TimeoutSec 3
        return [bool]$health
    } catch {
        return $false
    }
}

function Show-ToolReport {
    $raw = ''
    $previous = $ErrorActionPreference
    $ErrorActionPreference = 'Continue'
    try { $raw = (& $bootstrapScript -CheckOnly 2>&1 | Out-String) }
    catch { $raw = '' }
    finally { $ErrorActionPreference = $previous }
    $report = $null
    try {
        $start = $raw.IndexOf('{')
        $end = $raw.LastIndexOf('}')
        if ($start -ge 0 -and $end -gt $start) {
            $report = $raw.Substring($start, $end - $start + 1) | ConvertFrom-Json
        }
    } catch { $report = $null }
    if (-not $report) {
        Write-Warn '工具探测没有返回结构化结果；正式安装时会重新探测。'
        return
    }
    foreach ($tool in @($report.tools)) {
        Write-Host ("     {0,-8} {1,-14} {2}" -f $tool.name, $tool.version, $tool.status) -ForegroundColor Gray
    }
    foreach ($blocker in @($report.blockers)) {
        Write-Host ("     {0,-8} {1}" -f $blocker.name, $blocker.status) -ForegroundColor Yellow
    }
    if (@($report.blockers).Count -eq 0) { Write-Ok '工具链已满足固定版本要求。' }
    else { Write-Warn '上面标记的项会在安装时按固定版本自动补齐。' }
}

Write-Banner 'Shine 视频工作台 · 一键安装'
Write-Host '  本程序会：安装固定版本工具链、Python/前端依赖、启动本地服务，' -ForegroundColor Gray
Write-Host '  然后自动打开浏览器里的「新机设置」向导，先检查本机已有 ComfyUI 和模型。' -ForegroundColor Gray
Write-Host '  只有明确选择新建环境并确认后，才会下载缺少的软件或模型。' -ForegroundColor Gray
Write-Host '  不会改动系统盘、不装驱动、不写系统级设置。' -ForegroundColor Gray

$envFile = Join-Path $repoRoot '.env'
$recordedRoot = Get-ShineEnvValue $envFile 'SHINE_BOOTSTRAP_ROOT'
$recordedData = Get-ShineEnvValue $envFile 'SHINE_DATA_DIR'

if (-not $StorageRoot -and $recordedRoot) { $StorageRoot = $recordedRoot }

# ---- 1. Host gates -------------------------------------------------------
Write-Host ''
Write-Step '检查本机条件'
if (-not [Environment]::Is64BitOperatingSystem) {
    Write-Fail '需要 64 位 Windows 10/11。'
    exit 10
}
Write-Ok "Windows 64 位 · PowerShell $($PSVersionTable.PSVersion)"

if ($CheckOnly) {
    Write-Host ''
    Write-Step '磁盘可用空间（只读检查，不写入任何文件）'
    $inventory = @(Get-ShineDiskInventory)
    $systemDrive = if ($env:SystemDrive) { $env:SystemDrive.TrimEnd('\') } else { 'C:' }
    foreach ($disk in ($inventory | Sort-Object Drive)) {
        $mark = if ($disk.Drive.TrimEnd('\') -eq $systemDrive) { '系统盘，跳过' }
        elseif ($disk.Free -ge $requiredBytes) { '可用' }
        else { '空间不足' }
        Write-Host ("     {0}  剩余 {1,-12} 总计 {2,-12} {3}" -f $disk.Drive, (Get-GibString $disk.Free), (Get-GibString $disk.Size), $mark) -ForegroundColor Gray
    }
    Write-Host ''
    Write-Step '固定版本工具链（缺失项会在正式安装时自动下载）'
    Show-ToolReport
    Write-Host ''
    Write-Host "  安装需要非系统盘 >= $(Get-GibString $requiredBytes) 空闲；建议 $(Get-GibString $recommendedBytes)。" -ForegroundColor Gray
    Write-Host '  这只是只读体检。要真正安装，请双击「一键安装并启动.cmd」。' -ForegroundColor Yellow
    exit 0
}

# Already-running stack: never start a second one, just reopen the workbench.
if ($recordedData) {
    $manifest = Join-Path $recordedData 'run/processes.json'
    if (Test-Path -LiteralPath $manifest) {
        $record = Get-Content -LiteralPath $manifest -Raw -Encoding UTF8 | ConvertFrom-Json
        if (Test-ShineApiAlive ([int]$record.api_port)) {
            $url = "http://127.0.0.1:$($record.web_port)/#setup"
            Write-Ok "工作台已在运行（数据目录 $recordedData）。"
            Write-Host "  正在打开：$url" -ForegroundColor Green
            Start-Process $url
            exit 0
        }
        # The launcher refuses to start while a manifest exists, so a crashed
        # run would otherwise deadlock the tester. Reconciliation only ends
        # processes whose OS identity still matches the record.
        Write-Warn '发现上次运行的进程记录，但服务已不在运行；正在安全收尾。'
        $backendEnv = Get-ShineEnvValue $envFile 'SHINE_BACKEND_ENV'
        if (-not $backendEnv) { $backendEnv = Join-Path $recordedData '..\app\backend-env' }
        $reconcilePython = Join-Path ([IO.Path]::GetFullPath($backendEnv)) 'Scripts/python.exe'
        if (-not (Test-Path -LiteralPath $reconcilePython)) {
            $reconcilePython = Join-Path $repoRoot 'backend/.venv/Scripts/python.exe'
        }
        if (Test-Path -LiteralPath $reconcilePython) {
            $previous = $ErrorActionPreference
            $ErrorActionPreference = 'Continue'
            try { & $reconcilePython (Join-Path $PSScriptRoot 'stop_shine.py') --data-dir $recordedData }
            finally { $ErrorActionPreference = $previous }
            if ($LASTEXITCODE -ne 0) {
                Write-Fail '上次运行的进程身份无法自动收尾，安装器不会盲杀进程。'
                Write-Host '  请双击 STOP_APP.cmd，按提示处理后再双击安装入口。' -ForegroundColor Yellow
                Write-Host "  进程记录：$manifest" -ForegroundColor Gray
                exit 14
            }
            Write-Ok '陈旧记录已清理，继续安装/启动。'
        } else {
            Write-Fail '本机缺少后端环境，无法安全收尾上次运行的进程。'
            Write-Host '  确认没有 Shine 进程在运行后，删除下面这个文件再重试：' -ForegroundColor Yellow
            Write-Host "    $manifest" -ForegroundColor Gray
            exit 14
        }
    }
}

# ---- 2. Choose the install root -----------------------------------------
if (-not $StorageRoot) {
    Write-Host ''
    Write-Step '选择安装盘（自动挑选空间最大的非系统盘）'
    $systemDrive = if ($env:SystemDrive) { $env:SystemDrive.TrimEnd('\') } else { 'C:' }
    $candidates = @(Get-ShineDiskInventory |
        Where-Object { $_.Drive -and $_.Drive.TrimEnd('\') -ne $systemDrive -and $_.Free -ge $requiredBytes } |
        Sort-Object Free -Descending)
    if (-not $candidates -or $candidates.Count -eq 0) {
        Write-Fail "没有找到满足条件的安装盘：需要非系统盘 >= $(Get-GibString $requiredBytes) 空闲空间。"
        Write-Host ''
        Write-Host '  当前磁盘：' -ForegroundColor Gray
        foreach ($disk in @(Get-ShineDiskInventory | Sort-Object Drive)) {
            Write-Host ("     {0}  剩余 {1}" -f $disk.Drive, (Get-GibString $disk.Free)) -ForegroundColor Gray
        }
        Write-Host ''
        Write-Host '  处理办法：清空一个非系统盘，或挂载一块新盘后重新双击安装。' -ForegroundColor Yellow
        Write-Host '  本项目需要下载约 60.8 GB 固定物料，安装峰值约 174 GB，不会写入 C 盘。' -ForegroundColor Yellow
        exit 11
    }
    $chosen = $candidates[0]
    $StorageRoot = Join-Path $chosen.Drive 'ShineVideoStudio'
    Write-Ok "安装到 $StorageRoot（$($chosen.Drive) 剩余 $(Get-GibString $chosen.Free)）"
    if ($chosen.Free -lt $recommendedBytes) {
        Write-Warn "该盘剩余空间低于建议值 $(Get-GibString $recommendedBytes)，安装后余量会很紧张。"
    }
}

if (-not [IO.Path]::IsPathRooted($StorageRoot)) {
    Write-Fail "安装目录必须是绝对路径：$StorageRoot"
    exit 12
}
$StorageRoot = [IO.Path]::GetFullPath($StorageRoot)
$storageDrive = [IO.Path]::GetPathRoot($StorageRoot).TrimEnd('\')
$systemDrive = if ($env:SystemDrive) { $env:SystemDrive.TrimEnd('\') } else { 'C:' }
if ($storageDrive -eq $systemDrive) {
    Write-Fail "安装目录不能在系统盘（$systemDrive）。请换一个非系统盘。"
    exit 12
}
New-Item -ItemType Directory -Force -Path $StorageRoot | Out-Null
if (-not $recordedData) { $recordedData = Join-Path $StorageRoot 'data' }

if ($recordedRoot) {
    $resolvedRecorded = [IO.Path]::GetFullPath($recordedRoot)
    if ($resolvedRecorded -ne $StorageRoot) {
        Write-Fail "本机已经安装在 $resolvedRecorded，不能同时指向 $StorageRoot。"
        Write-Host '  如需换盘：先双击 STOP_APP.cmd，再从仓库根目录删除 .env，然后重新安装。' -ForegroundColor Yellow
        Write-Host '  已下载的模型和已产生的数据不会自动搬迁。' -ForegroundColor Yellow
        exit 13
    }
}

$logPath = Join-Path $StorageRoot ('install-log-{0}.txt' -f (Get-Date -Format 'yyyyMMdd-HHmmss'))
try { Start-Transcript -Path $logPath -Force | Out-Null; $transcript = $logPath } catch { $transcript = $null }

$apiPort = Get-ShineFreePort $defaultApiPort
$webPort = Get-ShineFreePort $defaultWebPort
if ($webPort -eq $apiPort) { $webPort = Get-ShineFreePort ($apiPort + 1) }
Write-Ok "端口：API $apiPort · Web $webPort"

if ($apiPort -ne $defaultApiPort -or $webPort -ne $defaultWebPort) {
    Write-Warn "默认端口被占用，本次改用 $apiPort/$webPort。浏览器地址以最后打印的为准。"
}

# ---- 3. Personal desktop confirmation (no password) --------------------
Write-Ok '本机工作台无需密码，素材和下载仍由你点击确认。'

# ---- 4. Tools + dependencies + services ---------------------------------
$backendPython = Join-Path $StorageRoot 'app/backend-env/Scripts/python.exe'
$frontendModules = Join-Path $repoRoot 'frontend/node_modules'
# The pinned PowerShell may be installed while the current shell's PATH is stale
# (a new window opened right after the first run), so check the bootstrap shim too.
$pwshPath = (Get-Command pwsh -ErrorAction SilentlyContinue).Source
if (-not $pwshPath) {
    $shim = Join-Path $StorageRoot 'bin/pwsh.cmd'
    if (Test-Path -LiteralPath $shim) { $pwshPath = $shim }
}
$alreadyInstalled = $pwshPath -and
    (Test-Path -LiteralPath $backendPython) -and (Test-Path -LiteralPath $frontendModules)

if ($alreadyInstalled) {
    # A second double-click must not reinstall dependencies. start_shine only
    # reinstalls when -Install is passed or a dependency tree is missing.
    Write-Host ''
    Write-Banner '本机已安装：只启动服务并打开向导'
    Write-Step "复用现有安装：$StorageRoot"
    $startArgs = @(
        '-NoProfile', '-ExecutionPolicy', 'Bypass', '-File', $startScript,
        '-DataDir', $recordedData, '-ApiPort', "$apiPort", '-WebPort', "$webPort", '-OpenBrowser'
    )
    $previous = $ErrorActionPreference
    $ErrorActionPreference = 'Continue'
    try { & $pwshPath @startArgs; $installCode = $LASTEXITCODE }
    catch { Write-Fail $_.Exception.Message; $installCode = 1 }
    finally { $ErrorActionPreference = $previous }
    if ($installCode -ne 0) {
        Write-Host ''
        Write-Banner '启动失败'
        Write-Fail "启动器返回码：$installCode"
        Write-Host '  如果提示端口被占用，先双击 STOP_APP.cmd，再重试。' -ForegroundColor Yellow
        Write-Host "  日志目录：$(Join-Path $recordedData 'run/logs')" -ForegroundColor Gray
        if ($transcript) { try { Stop-Transcript | Out-Null } catch { } }
        exit $installCode
    }
    $webUrl = "http://127.0.0.1:$webPort/#setup"
    Write-Host ''
    Write-Host "  工作台地址： $webUrl" -ForegroundColor Green
    Write-Host "  数据目录：   $(($recordedData -replace '/', '\'))" -ForegroundColor Gray
    Write-Host '  停止服务：双击 STOP_APP.cmd' -ForegroundColor Gray
    if ($transcript) { try { Stop-Transcript | Out-Null } catch { } }
    exit 0
}

Write-Host ''
Write-Banner '开始安装（首次约 20-60 分钟，取决于网络；请勿关闭窗口）'
# Hashtable splatting binds by name. An array would bind positionally and hand
# "-StorageRoot" to -ToolsManifest, which is exactly the kind of silent misuse
# a one-click entry must not have.
$bootstrapArgs = @{
    StorageRoot = $StorageRoot
    DataDir = $recordedData
    ApiPort = $apiPort
    WebPort = $webPort
}
if ($NoLaunch) { $bootstrapArgs['NoLaunch'] = $true }

$previous = $ErrorActionPreference
$ErrorActionPreference = 'Continue'
try {
    & $bootstrapScript @bootstrapArgs
    $installCode = $LASTEXITCODE
} catch {
    Write-Fail $_.Exception.Message
    $installCode = 1
} finally {
    $ErrorActionPreference = $previous
}

if ($installCode -ne 0) {
    Write-Host ''
    Write-Banner '安装未完成'
    Write-Fail "安装器返回码：$installCode"
    Write-Host ''
    Write-Host '  请把下面两项发给开发同学：' -ForegroundColor Yellow
    Write-Host "   1. 本窗口全部文字（或日志 $(if ($transcript) { $transcript } else { '（未生成日志）' })）"
    Write-Host "   2. 安装目录：$StorageRoot"
    Write-Host ''
    Write-Host '  常见原因：' -ForegroundColor Gray
    Write-Host '   - 网络无法访问 GitHub / PyPI / PyTorch（需要能下载固定版本）' -ForegroundColor Gray
    Write-Host '   - 安全软件拦截了 PowerShell 或 curl' -ForegroundColor Gray
    Write-Host '   - 安装盘空间不足或被占用' -ForegroundColor Gray
    if ($transcript) { try { Stop-Transcript | Out-Null } catch { } }
    exit $installCode
}

# ---- 5. Hand over to the wizard -----------------------------------------
$webUrl = "http://127.0.0.1:$webPort/#setup"
Write-Host ''
Write-Banner '安装完成，已进入新机设置向导'
Write-Host "  工作台地址： $webUrl" -ForegroundColor Green
Write-Host "  API 健康页： http://127.0.0.1:$apiPort/health" -ForegroundColor Gray
Write-Host "  安装目录：   $StorageRoot" -ForegroundColor Gray
Write-Host "  数据目录：   $(($recordedData -replace '/', '\'))" -ForegroundColor Gray
Write-Host ''
Write-Host '  在向导中点击「检测本机环境」，再确认「下载缺项并自动配置」。' -ForegroundColor Gray
Write-Host '  后续安装、工作流登记和服务检测会自动完成，无需密码。' -ForegroundColor Gray
Write-Host ''
Write-Host '  以后再次启动：双击 START_APP.cmd     停止服务：双击 STOP_APP.cmd' -ForegroundColor Gray
if ($transcript) { try { Stop-Transcript | Out-Null } catch { } }
exit 0
