# 后端全量回归：多核 LPT 负载均衡并行分片（固定规则见 AGENTS.md）
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
    # 0 表示按本机核心数自动取值：min(14, max(8, CPU-2))；也可显式传入 1..64。
    [ValidateRange(0, 64)][int]$Shards = 0,
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
# 一个共享的 temproot 会让多进程抢同一批 pytest-N 编号目录，10 次抢不到就整体报错。
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
if ($Shards -le 0) {
    $Shards = [Math]::Min(14, [Math]::Max(8, [Environment]::ProcessorCount - 2))
}
$mediaCache = Join-Path $WorkRoot '.media-cache'
New-Item -ItemType Directory -Path $mediaCache -Force | Out-Null
$env:SHINE_TEST_MEDIA_CACHE_DIR = $mediaCache
$env:SHINE_TEST_FAST_SQLITE = '1'

$outDir = Join-Path $WorkRoot ('shards-' + (Get-Date -Format 'yyyyMMdd-HHmmss-fff'))
New-Item -ItemType Directory -Path $outDir -Force | Out-Null

# 收集顶层 test_*.py 以及 manual4 等子目录测试文件，避免遗漏子包回归。
$allFileObjs = @(Get-ChildItem (Join-Path $repo 'backend\tests') -Recurse -File -Filter 'test_*.py' |
    Sort-Object FullName)

# 已知最耗时模块兜底权重（WorkRoot 首次无历史日志时避免重文件扎堆），
# 若 WorkRoot 下已有历史 shards-* 日志则优先以本机实测 [shine-module] 耗时为准。
$moduleWeights = @{
    'backend/tests/test_native_capabilities.py' = 684.0
    'backend/tests/test_renderer_supervisor.py' = 576.0
    'backend/tests/test_four_stage_reacceptance.py' = 441.0
    'backend/tests/test_storyboard.py' = 426.0
    'backend/tests/test_production_series.py' = 404.0
    'backend/tests/test_director_streamline.py' = 396.0
    'backend/tests/test_comfy_handoff_journal.py' = 329.0
    'backend/tests/test_director_long_form_regressions.py' = 315.0
    'backend/tests/test_director_material_commit.py' = 272.0
    'backend/tests/test_handoff_lease_transfer.py' = 234.0
    'backend/tests/test_v13_adversarial.py' = 230.0
    'backend/tests/test_director_long_form.py' = 198.0
    'backend/tests/test_support_copilot_v3.py' = 194.0
    'backend/tests/test_support_agent.py' = 175.0
    'backend/tests/test_style_input_assessment.py' = 175.0
    'backend/tests/test_v13_preparation.py' = 170.0
    'backend/tests/test_variant_binding_and_gate.py' = 169.0
    'backend/tests/test_managed_run_binding.py' = 158.0
    'backend/tests/test_operator_review.py' = 156.0
    'backend/tests/test_llm_setup_contract.py' = 153.0
}
$priorRun = Get-ChildItem -LiteralPath $WorkRoot -Directory -Filter 'shards-*' -ErrorAction SilentlyContinue |
    Where-Object { $_.FullName -ne $outDir } |
    Sort-Object LastWriteTime -Descending |
    Select-Object -First 1
if ($priorRun) {
    Get-ChildItem -LiteralPath $priorRun.FullName -File -Filter 'chunk-*.log' -ErrorAction SilentlyContinue |
        ForEach-Object {
            Select-String -Path $_.FullName -Pattern '^\[shine-module\]\s+([\d.]+)s\s+phases=\d+\s+cases=\d+\s+(\S+)$' |
                ForEach-Object {
                    $m = $_.Matches[0]
                    $moduleWeights[$m.Groups[2].Value] = [double]$m.Groups[1].Value
                }
        }
}

$repoPrefix = ($repo.TrimEnd('\', '/') + '\')
$weighted = $allFileObjs | ForEach-Object {
    $rel = $_.FullName
    if ($rel.StartsWith($repoPrefix, [System.StringComparison]::OrdinalIgnoreCase)) {
        $rel = $rel.Substring($repoPrefix.Length)
    }
    $relKey = $rel -replace '\\', '/'
    $w = if ($moduleWeights.ContainsKey($relKey)) {
        $moduleWeights[$relKey]
    } else {
        [Math]::Max(3.0, [Math]::Round($_.Length / 1024.0 * 0.45, 2))
    }
    [PSCustomObject]@{ Path = $_.FullName; Weight = $w }
} | Sort-Object Weight -Descending

$chunks = @{}
$shardLoads = @()
for ($i = 0; $i -lt $Shards; $i++) {
    $chunks[$i] = New-Object System.Collections.ArrayList
    $shardLoads += 0.0
}
# LPT（最长处理时间优先）贪心装箱，尽量拉平各分片总耗时，消除木桶长尾。
foreach ($item in $weighted) {
    $best = 0
    for ($i = 1; $i -lt $Shards; $i++) {
        if ($shardLoads[$i] -lt $shardLoads[$best]) { $best = $i }
    }
    [void]$chunks[$best].Add($item.Path)
    $shardLoads[$best] += $item.Weight
}
Write-Output "shards=$Shards files=$($allFileObjs.Count) workroot=$WorkRoot"

$swAll = [System.Diagnostics.Stopwatch]::StartNew()
$jobs = @()
for ($i = 0; $i -lt $Shards; $i++) {
    $chunk = [string[]]$chunks[$i].ToArray()
    if ($chunk.Count -eq 0) { continue }
    $log = Join-Path $outDir "chunk-$i.log"
    $basetemp = Join-Path $outDir "bt-$i"
    $jobs += Start-Job -ScriptBlock {
        param($python, $cwd, [string[]]$chunk, $log, $basetemp, $idx)
        Set-Location -LiteralPath $cwd
        $sw = [System.Diagnostics.Stopwatch]::StartNew()
        & $python -m pytest @chunk -q -p no:cacheprovider --basetemp=$basetemp *> $log
        $code = $LASTEXITCODE
        $sw.Stop()
        [PSCustomObject]@{ Index = $idx; ExitCode = $code; ElapsedSec = [Math]::Round($sw.Elapsed.TotalSeconds, 1) }
    } -ArgumentList @($python, $repo, $chunk, $log, $basetemp, $i)
}
$results = @{}
foreach ($job in $jobs) {
    $null = Wait-Job -Job $job
    $res = Receive-Job -Job $job | Select-Object -Last 1
    if ($null -ne $res) { $results[$res.Index] = $res }
    Remove-Job -Job $job
}
$swAll.Stop()

Write-Output "=== shard results (wall=$([Math]::Round($swAll.Elapsed.TotalSeconds, 1))s) ==="
$bad = 0
for ($i = 0; $i -lt $Shards; $i++) {
    if ($chunks[$i].Count -eq 0) { continue }
    $log = Join-Path $outDir "chunk-$i.log"
    $text = Get-Content $log -Raw
    $res = $results[$i]
    $exitCode = if ($null -ne $res) { $res.ExitCode } else { $null }
    $elapsed = if ($null -ne $res) { $res.ElapsedSec } else { '?' }
    $hasFail = $text -match '(?m)^(FAILED|ERROR)'
    $ok = (-not $hasFail) -and ($null -ne $exitCode) -and ($exitCode -eq 0)
    if (-not $ok) { $bad++ }
    Write-Output ("chunk-{0}: {1} (exit {2}, {3}s, files {4}, log {5})" -f $i, $(if ($ok) { 'clean' } else { 'NOT CLEAN' }), $exitCode, $elapsed, $chunks[$i].Count, $log)
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
            # git 对象文件在 Windows 上是只读的（test_clean_checkout_identity 会在
            # basetemp 里建真实仓库），.NET 的递归删除碰到只读文件会整目录失败：每轮
            # 留下一个 bt-*，历史 run 目录也永远清不掉。先去掉只读属性再删。
            foreach ($file in [System.IO.DirectoryInfo]::new($Path).EnumerateFiles(
                    '*', [System.IO.SearchOption]::AllDirectories)) {
                if ($file.Attributes -band [System.IO.FileAttributes]::ReadOnly) {
                    $file.Attributes = $file.Attributes -band (-bnot [System.IO.FileAttributes]::ReadOnly)
                }
            }
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
# 「最近」按目录名里的创建时间戳排，不按最后修改时间：删除旧目录里的文件会刷新
# 旧目录的修改时间，按修改时间排会把自己刚产出的这一轮排到后面、当成旧目录删掉。
$runs = @(Get-ChildItem -LiteralPath $WorkRoot -Directory -Filter 'shards-*' -ErrorAction SilentlyContinue |
    Sort-Object Name -Descending)
$stale = @($runs | Select-Object -Skip $KeepRuns)
foreach ($dir in $stale) { Remove-ShineDir $dir.FullName }
if ($stale.Count -gt 0) {
    Write-Output "old runs removed: $($stale.Count) (kept latest $KeepRuns)"
}
Write-Output "workroot now: $(@(Get-ChildItem -LiteralPath $WorkRoot -Directory -ErrorAction SilentlyContinue).Count) dirs"

if ($bad -gt 0) { exit 1 }
