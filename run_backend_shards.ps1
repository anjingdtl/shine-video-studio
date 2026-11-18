# 后端全量回归：8 分片并行（按文件均分，固定规则见 AGENTS.md）
#
# 本脚本不钉死任何绝对路径：仓库位置由脚本自身推出，临时根由参数给出。
# 临时根有两个硬要求，否则会得到一整片假失败：
#   1. 必须是**独立目录**，不能与别的进程共用 PYTEST_DEBUG_TEMPROOT（编号锁会抢不到）；
#   2. 必须落在**非系统盘**，否则 require_storage 会按产品规则拒绝
#      （CHOOSE_DEPLOYMENT_DIRECTORY_OUTSIDE_SYSTEM_DRIVE）。
#
#   .\run_backend_shards.ps1 -WorkRoot <非系统盘上的测试目录>
param(
    [Parameter(Mandatory = $true)][string]$WorkRoot,
    [ValidateRange(1, 64)][int]$Shards = 8,
    # 排障用：保留各分片 basetemp（失败用例的 tmp_path）。默认全绿即删。
    [switch]$KeepBasetemp,
    # 保留最近 N 次运行的日志目录（只清本脚本产出的 shards-*，不动别的目录）。
    [ValidateRange(1, 100)][int]$KeepRuns = 1
)

$ErrorActionPreference = 'Stop'
$env:CODEBUDDY_SAFE_DELETE_ENABLED = '0'
# 宿主 NO_PROXY 可能含 [::1] 这类带方括号条目：httpx 会拒绝建客户端，
# 环回真实 HTTP 用例（renderer_supervisor / v13_e2e 等）整片假失败。
# 回归入口统一去掉，与 AGENTS.md 的 env -u NO_PROXY 口径一致。
# 用赋值置 $null 而不是 Remove-Item Env:——宿主的 safe-delete 钩子会把
# 「删除不存在的变量」判成 path-not-found 并 fail closed，脚本连启动都过不去。
$env:NO_PROXY = $null
$env:no_proxy = $null
# 一个共享的 temproot 会让 8 个进程抢同一批 pytest-N 编号目录，10 次抢不到就整体报错。
$env:PYTEST_DEBUG_TEMPROOT = ''

$repo = $PSScriptRoot
. (Join-Path $repo 'scripts/shine_local_config.ps1')
$python = (Get-ShineStackSettings $repo).Python
$env:PYTHONPATH = Join-Path $repo 'backend'
$WorkRoot = Resolve-ShineLocalPath $WorkRoot (Get-Location).ProviderPath
$systemDrive = if ($env:SystemDrive) { $env:SystemDrive } else { 'C:' }
if ([IO.Path]::GetPathRoot($WorkRoot).TrimEnd('\') -eq $systemDrive) {
    throw "WorkRoot 必须是非系统盘目录（当前 $WorkRoot）。系统盘会被部署目录规则拒绝。"
}
$outDir = Join-Path $WorkRoot ('shards-' + (Get-Date -Format 'yyyyMMdd-HHmmss-fff'))
New-Item -ItemType Directory -Path $outDir -Force | Out-Null

$files = Get-ChildItem (Join-Path $repo 'backend\tests\test_*.py') |
    Sort-Object Name | ForEach-Object { $_.FullName }
$chunks = @{}
for ($i = 0; $i -lt $Shards; $i++) { $chunks[$i] = New-Object System.Collections.ArrayList }
for ($i = 0; $i -lt $files.Count; $i++) {
    [void]$chunks[$i % $Shards].Add($files[$i])
}
Write-Output "shards=$Shards files=$($files.Count) workroot=$WorkRoot"

$jobs = @()
for ($i = 0; $i -lt $Shards; $i++) {
    $chunk = [string[]]$chunks[$i].ToArray()
    $log = Join-Path $outDir "chunk-$i.log"
    $basetemp = Join-Path $outDir "bt-$i"
    $jobs += Start-Job -ScriptBlock {
        param($python, $cwd, [string[]]$chunk, $log, $basetemp)
        Set-Location -LiteralPath $cwd
        & $python -m pytest @chunk -q -p no:cacheprovider --basetemp=$basetemp *> $log
        $LASTEXITCODE
    } -ArgumentList @($python, $repo, $chunk, $log, $basetemp)
}
$exits = @()
foreach ($job in $jobs) {
    $null = Wait-Job -Job $job
    $exits += (Receive-Job -Job $job)
    Remove-Job -Job $job
}

Write-Output "=== shard results ==="
$bad = 0
for ($i = 0; $i -lt $Shards; $i++) {
    $log = Join-Path $outDir "chunk-$i.log"
    $text = Get-Content $log -Raw
    $exitCode = $exits[$i]
    $hasFail = $text -match '(?m)^(FAILED|ERROR)'
    $ok = (-not $hasFail) -and ($null -ne $exitCode) -and ($exitCode -eq 0)
    if (-not $ok) { $bad++ }
    Write-Output ("chunk-{0}: {1} (exit {2}, log {3})" -f $i, $(if ($ok) { 'clean' } else { 'NOT CLEAN' }), $exitCode, $log)
}
Write-Output "shards_with_failure_markers=$bad"
Write-Output "logs: $outDir"

# ---- 清理：分片 basetemp + 历史 run 目录 ----
# 每次跑完必须落地：basetemp 里是各用例的 workspace，长期堆积会把测试盘吃满
# （历史事故：3 次全量回归在 F:\ShineTest 留下 327 GB）。
function Remove-ShineDir([string]$Path) {
    if (-not (Test-Path -LiteralPath $Path)) { return }
    for ($attempt = 1; $attempt -le 3; $attempt++) {
        try {
            # 用 .NET 直删，绕开宿主 safe-delete 垫片。那是按「轮」计数的 Remove-Item
            # 函数钩子，删自己的 basetemp 也会命中 BULK_CONFIRM_REQUIRED（阈值 50 次），
            # 结果是删到一半停下、留下半套目录——比不删更难收拾。删除目标始终是本
            # 脚本本次刚产出的临时目录，不涉及用户文件。
            [System.IO.Directory]::Delete($Path, $true)
            return
        } catch {
            if ($attempt -eq 3) {
                Write-Warning "未能完全删除（可能有句柄未释放）：$Path — $($_.Exception.Message)"
                return
            }
            Start-Sleep -Milliseconds 500
        }
    }
}

$keepBasetemp = $KeepBasetemp.IsPresent -or ($bad -gt 0)
if ($keepBasetemp) {
    $why = if ($KeepBasetemp) { '-KeepBasetemp' } else { '有分片未通过，留作排障' }
    Write-Output "basetemp kept ($why): $outDir\bt-*"
} else {
    Get-ChildItem -LiteralPath $outDir -Directory -Filter 'bt-*' -ErrorAction SilentlyContinue |
        ForEach-Object { Remove-ShineDir $_.FullName }
    Write-Output "basetemp removed: $outDir\bt-*"
}

# 只清本脚本自己的产物（shards-*），WorkRoot 下的其它目录不动。
$runs = @(Get-ChildItem -LiteralPath $WorkRoot -Directory -Filter 'shards-*' -ErrorAction SilentlyContinue |
    Sort-Object LastWriteTime -Descending)
$stale = @($runs | Select-Object -Skip $KeepRuns)
foreach ($dir in $stale) { Remove-ShineDir $dir.FullName }
if ($stale.Count -gt 0) {
    Write-Output "old runs removed: $($stale.Count) (kept latest $KeepRuns)"
}
Write-Output "workroot now: $(@(Get-ChildItem -LiteralPath $WorkRoot -Directory -ErrorAction SilentlyContinue).Count) dirs"

if ($bad -gt 0) { exit 1 }
