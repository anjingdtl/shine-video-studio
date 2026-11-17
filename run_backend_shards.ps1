# 后端全量回归：8 分片并行（按文件均分，固定规则见 AGENTS.md）
#
# 本脚本不钉死任何绝对路径：仓库位置由脚本自身推出，临时根由参数给出。
# 临时根有两个硬要求，否则会得到一整片假失败：
#   1. 必须是**独立目录**，不能与别的进程共用 PYTEST_DEBUG_TEMPROOT（编号锁会抢不到）；
#   2. 必须落在**非系统盘**，否则 require_storage 会按产品规则拒绝
#      （CHOOSE_DEPLOYMENT_DIRECTORY_OUTSIDE_SYSTEM_DRIVE）。
#
#   .\run_backend_shards.ps1                        # 默认临时根 D:\shine-pytest
#   .\run_backend_shards.ps1 -WorkRoot E:\tmp\shards
param(
    [string]$WorkRoot = 'D:\shine-pytest',
    [int]$Shards = 8
)

$ErrorActionPreference = 'Stop'
$env:CODEBUDDY_SAFE_DELETE_ENABLED = '0'
# 一个共享的 temproot 会让 8 个进程抢同一批 pytest-N 编号目录，10 次抢不到就整体报错。
$env:PYTEST_DEBUG_TEMPROOT = ''

$repo = Split-Path -Parent $PSScriptRoot
$python = Join-Path $repo 'backend\.venv\Scripts\python.exe'
$systemDrive = if ($env:SystemDrive) { $env:SystemDrive } else { 'C:' }
if ($WorkRoot.Length -lt 2 -or $WorkRoot.Substring(0, 2).ToUpper() -eq $systemDrive.ToUpper()) {
    throw "WorkRoot 必须是非系统盘目录（当前 $WorkRoot）。系统盘会被部署目录规则拒绝。"
}
$outDir = Join-Path $WorkRoot ('shards-' + (Get-Date -Format 'yyyyMMdd-HHmmss'))
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
    $list = $chunks[$i] -join ' '
    $log = Join-Path $outDir "chunk-$i.log"
    $basetemp = Join-Path $WorkRoot "bt-$i"
    $jobs += Start-Job -ScriptBlock {
        param($python, $cwd, $list, $log, $basetemp)
        Set-Location $cwd
        & $python -m pytest ($list -split ' ') -q -p no:cacheprovider --basetemp=$basetemp *> $log
        $LASTEXITCODE
    } -ArgumentList $python, $repo, $list, $log, $basetemp
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
    $ok = (-not $hasFail) -and (($exitCode -eq 0) -or ($null -eq $exitCode))
    if (-not $ok) { $bad++ }
    Write-Output ("chunk-{0}: {1} (exit {2}, log {3})" -f $i, $(if ($ok) { 'clean' } else { 'NOT CLEAN' }), $exitCode, $log)
}
Write-Output "shards_with_failure_markers=$bad"
Write-Output "logs: $outDir"
if ($bad -gt 0) { exit 1 }
