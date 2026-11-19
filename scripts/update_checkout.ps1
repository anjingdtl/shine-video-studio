param(
    [string]$RepoRoot = '',
    [switch]$Check,
    [ValidateRange(5, 120)][int]$FetchTimeoutSeconds = 15
)

# The single executor for "make this checkout current".
#
# It does exactly three things: decide whether the branch is behind, fast-forward
# it, and report the outcome. It never merges a divergent history, never resets,
# never stashes, and never touches uncommitted work: a tracked-file change makes
# it refuse the update outright, because the user's work always wins.
#
# The exit code is the contract with start_dev.ps1:
#   0   already current (or locally ahead) - nothing to restart
#  10   fast-forwarded this run - the caller must stop the old stack first
#  20   not a git checkout / detached HEAD / no upstream - nothing to do
#  30   tracked files are modified - refused, worktree preserved
#  40   update failed (divergence, locked file, rejected merge)
#  50   the remote is unreachable (offline or proxy down) - local code untouched

$ErrorActionPreference = 'Stop'
$PSNativeCommandUseErrorActionPreference = $false
try {
    [Console]::OutputEncoding = [Text.Encoding]::UTF8
    $OutputEncoding = [Text.Encoding]::UTF8
} catch { }
# A credential prompt would hang a double-click that has no window to type into.
$env:GIT_TERMINAL_PROMPT = '0'

if (-not $RepoRoot) { $RepoRoot = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path }
$RepoRoot = (Resolve-Path -LiteralPath $RepoRoot).Path

function Write-UpdateState([string]$State, [string]$Message, [string]$Color = 'Gray') {
    Write-Host "[update] $State $Message" -ForegroundColor $Color
}

function Get-Git([string[]]$Arguments) {
    $lines = & $script:GitPath -C $RepoRoot @Arguments 2>&1
    return [pscustomobject]@{
        Code  = $LASTEXITCODE
        Lines = @($lines | ForEach-Object { [string]$_ })
    }
}

function Get-GitText([string[]]$Arguments) {
    $result = Get-Git $Arguments
    if ($result.Code -ne 0) { return '' }
    return (($result.Lines | Where-Object { $_.Trim() }) -join "`n").Trim()
}

# A fetch against a black-holed proxy can hang far longer than a double-click
# tolerates. The job is stopped on timeout so the launcher always reaches the
# start step, with the local code untouched.
function Invoke-RemoteFetch {
    $job = Start-Job -ScriptBlock {
        param($GitPath, $Repo, $TimeoutSeconds)
        $output = & $GitPath -C $Repo `
            -c http.lowSpeedLimit=1000 -c http.lowSpeedTime=10 `
            fetch --prune --quiet 2>&1
        [pscustomobject]@{
            Code  = $LASTEXITCODE
            Lines = @($output | ForEach-Object { [string]$_ })
        }
    } -ArgumentList $script:GitPath, $RepoRoot, $FetchTimeoutSeconds
    if (-not (Wait-Job $job -Timeout $FetchTimeoutSeconds)) {
        Stop-Job $job -ErrorAction SilentlyContinue
        Remove-Job $job -Force -ErrorAction SilentlyContinue
        return $null
    }
    $result = Receive-Job $job -ErrorAction SilentlyContinue
    Remove-Job $job -Force -ErrorAction SilentlyContinue
    return $result
}

$gitCommand = Get-Command git -CommandType Application -ErrorAction SilentlyContinue |
    Select-Object -First 1
if (-not $gitCommand) {
    Write-UpdateState 'SKIP' '未找到 git，按本地代码启动。' 'Yellow'
    exit 20
}
$script:GitPath = $gitCommand.Source

# A packaged or zipped copy has no history to update; it must behave exactly as
# before, not fail.
if (-not (Test-Path -LiteralPath (Join-Path $RepoRoot '.git'))) {
    Write-UpdateState 'SKIP' '不是 git 检出版本（分发包或 zip），按本地代码启动。'
    exit 20
}

$branch = Get-GitText @('rev-parse', '--abbrev-ref', 'HEAD')
if (-not $branch) {
    Write-UpdateState 'FAIL' '无法读取当前分支，按本地代码启动。' 'Yellow'
    exit 40
}
if ($branch -eq 'HEAD') {
    Write-UpdateState 'SKIP' '当前处于 detached HEAD，跳过自动更新。' 'Yellow'
    exit 20
}

$upstream = Get-GitText @('rev-parse', '--abbrev-ref', '--symbolic-full-name', '@{u}')
if (-not $upstream) {
    if (Get-GitText @('remote') -eq '') {
        Write-UpdateState 'SKIP' '仓库没有配置远端，跳过自动更新。'
        exit 20
    }
    $upstream = "origin/$branch"
    if ((Get-GitText @('rev-parse', '--verify', '--quiet', "refs/remotes/$upstream")) -eq '') {
        Write-UpdateState 'SKIP' "分支 $branch 没有上游 $upstream，跳过自动更新。"
        exit 20
    }
}

$head = Get-GitText @('rev-parse', '--short', 'HEAD')

# Only tracked-file changes can be overwritten by a fast-forward. Untracked
# files are left to git, which refuses the merge by itself if they collide.
$dirty = Get-Git @('status', '--porcelain', '--untracked-files=no')
if ($dirty.Code -ne 0) {
    Write-UpdateState 'FAIL' '无法读取工作区状态，按本地代码启动。' 'Yellow'
    exit 40
}
$dirtyCount = @($dirty.Lines | Where-Object { $_.Trim() }).Count
if ($dirtyCount -gt 0) {
    Write-UpdateState 'DIRTY' ("检测到 $dirtyCount 个已跟踪文件有未提交改动，已跳过自动更新（你的改动不会被覆盖）。") 'Yellow'
    exit 30
}

Write-UpdateState 'CHECK' '正在检查远端更新...' 'DarkGray'
$fetch = Invoke-RemoteFetch
if ($null -eq $fetch) {
    Write-UpdateState 'OFFLINE' ("${FetchTimeoutSeconds} 秒内未完成远端检查（离线或代理不通），按本地 $head 启动。") 'Yellow'
    exit 50
}
if ($fetch.Code -ne 0) {
    Write-UpdateState 'OFFLINE' "远端检查失败，按本地 $head 启动。" 'Yellow'
    $fetch.Lines | Select-Object -First 5 | ForEach-Object { Write-Host "         $_" -ForegroundColor DarkGray }
    exit 50
}

$counts = Get-GitText @('rev-list', '--left-right', '--count', "$upstream...HEAD")
if (-not $counts) {
    Write-UpdateState 'FAIL' '无法比较本地与远端提交，按本地代码启动。' 'Yellow'
    exit 40
}
$parts = @($counts -split '\s+' | Where-Object { $_ -ne '' })
if ($parts.Count -lt 2) {
    Write-UpdateState 'FAIL' "无法解析提交差异（$counts），按本地代码启动。" 'Yellow'
    exit 40
}
$behind = [int]$parts[0]
$ahead = [int]$parts[1]

if ($behind -eq 0) {
    if ($ahead -gt 0) {
        Write-UpdateState 'CURRENT' "本地 $head 比 $upstream 多 $ahead 个未推送提交，无需更新。" 'Gray'
        exit 0
    }
    Write-UpdateState 'CURRENT' "已是 $upstream 最新（$head）。" 'Green'
    exit 0
}

if ($ahead -gt 0) {
    Write-UpdateState 'DIVERGED' "本地有 $ahead 个未推送提交、远端有 $behind 个新提交，已分叉；跳过自动更新，请手动处理。" 'Yellow'
    exit 40
}

if ($Check) {
    Write-UpdateState 'BEHIND' "落后 $upstream $behind 个提交（-Check 模式，未拉取）。" 'Yellow'
    exit 0
}

# The fetch already brought every object, so the fast-forward itself stays
# local: a network drop between fetch and merge cannot produce a half-update.
$merge = Get-Git @('merge', '--ff-only', $upstream)
if ($merge.Code -ne 0) {
    Write-UpdateState 'FAIL' "快进更新失败，已保留本地 $head：" 'Red'
    $merge.Lines | Select-Object -First 8 | ForEach-Object { Write-Host "         $_" -ForegroundColor DarkGray }
    exit 40
}

$newHead = Get-GitText @('rev-parse', '--short', 'HEAD')
Write-UpdateState 'UPDATED' "已更新 $head -> $newHead（$behind 个提交）。" 'Green'
exit 10
