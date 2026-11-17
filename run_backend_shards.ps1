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
    [ValidateRange(1, 64)][int]$Shards = 8
)

$ErrorActionPreference = 'Stop'
$env:CODEBUDDY_SAFE_DELETE_ENABLED = '0'
# 宿主 NO_PROXY 可能含 [::1] 这类带方括号条目：httpx 会拒绝建客户端，
# 环回真实 HTTP 用例（renderer_supervisor / v13_e2e 等）整片假失败。
# 回归入口统一去掉，与 AGENTS.md 的 env -u NO_PROXY 口径一致。
Remove-Item Env:NO_PROXY -ErrorAction SilentlyContinue
Remove-Item Env:no_proxy -ErrorAction SilentlyContinue
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
if ($bad -gt 0) { exit 1 }
