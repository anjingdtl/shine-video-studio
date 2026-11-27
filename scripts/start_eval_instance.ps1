# 启动一个用于模拟测评的隔离 Shine 实例。
#
# 目的：让测评实例复用仓库里已经装好的 backend/.venv 与 frontend/node_modules，
# 不重跑 uv sync / npm ci。原因是实测过的阻滞点：
#   * 隔离实例用自己的数据目录，没有依赖安装回执，启动脚本判定为“未安装”，
#     于是对 frontend 执行 npm ci；
#   * npm ci 会先删掉 frontend/node_modules，而本机已运行的 vite 占用
#     esbuild.exe，Windows 上直接 EPERM 失败；
#   * 即使不失败，也会打断用户正在运行的工作台（它的 node_modules 被删）。
#
# 做法：按 start_shine.ps1 的同一口径算出两个锁文件指纹，写入
# <DataDir>/run/receipts.json，再调用 start_shine.ps1。
# 只在依赖确实就绪时才写回执（venv 能 import shine、node_modules 与 vite 入口存在），
# 否则交给启动脚本走正常安装流程，不放宽任何检查。
#
# 用法（测试根由调用方给出，仓库不写死本机目录）：
#   pwsh -NoProfile -File scripts/start_eval_instance.ps1 -DataDir <测试根>\acceptance\eval-01\data -ApiPort 8010 -WebPort 5180
#   pwsh -NoProfile -File scripts/start_eval_instance.ps1 -DataDir <测试根>\acceptance\eval-01\data -Install

param(
    [Parameter(Mandatory = $true)][string]$DataDir,
    [ValidateRange(1, 65535)][int]$ApiPort = 8010,
    [ValidateRange(1, 65535)][int]$WebPort = 5180,
    [ValidateSet('mock', 'comfyui')][string]$Provider = 'mock',
    [switch]$Install
)

$ErrorActionPreference = 'Stop'
$repoRoot = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
. (Join-Path $PSScriptRoot 'shine_local_config.ps1')

function Get-FileFingerprint([string]$Path) {
    if (-not (Test-Path -LiteralPath $Path)) { return '' }
    try { return (Get-FileHash -LiteralPath $Path -Algorithm SHA256).Hash.ToLower() } catch { return '' }
}

$backendLock = Join-Path $repoRoot 'backend/uv.lock'
$frontendLock = Join-Path $repoRoot 'frontend/package-lock.json'
$backendFingerprint = Get-FileFingerprint $backendLock
$frontendFingerprint = Get-FileFingerprint $frontendLock

$backendEnv = if ($env:SHINE_BACKEND_ENV) { $env:SHINE_BACKEND_ENV } else { Join-Path $repoRoot 'backend/.venv' }
$backendReady = $false
$backendPython = Join-Path $backendEnv 'Scripts/python.exe'
if (Test-Path -LiteralPath $backendPython) {
    & $backendPython -c 'import shine' 2>$null | Out-Null
    $backendReady = ($LASTEXITCODE -eq 0)
}
$frontendReady = Test-Path -LiteralPath (Join-Path $repoRoot 'frontend/node_modules/vite/bin/vite.js')

# The one path resolver of the repository: a rooted -DataDir (the test root lies outside
# the checkout) is taken as is, a relative one is resolved against the caller.
$resolvedDataDir = Resolve-ShineLocalPath $DataDir (Get-Location).ProviderPath
New-Item -ItemType Directory -Force -Path $resolvedDataDir | Out-Null
$receiptDir = Join-Path $resolvedDataDir 'run'
$receiptPath = Join-Path $receiptDir 'receipts.json'

if (-not $Install -and $backendReady -and $frontendReady -and $backendFingerprint -and $frontendFingerprint) {
    $receipts = New-Object psobject
    $receipts | Add-Member -NotePropertyName 'backend_dependencies' -NotePropertyValue ([pscustomobject]@{ fingerprint = $backendFingerprint; command = 'uv sync --locked' }) -Force
    $receipts | Add-Member -NotePropertyName 'frontend_dependencies' -NotePropertyValue ([pscustomobject]@{ fingerprint = $frontendFingerprint; command = 'npm ci' }) -Force
    New-Item -ItemType Directory -Force -Path $receiptDir | Out-Null
    $receipts | ConvertTo-Json -Depth 6 | Set-Content -LiteralPath $receiptPath -Encoding utf8
    Write-Host '  [OK] 已写入依赖回执：复用现有 .venv 与 node_modules，不重装' -ForegroundColor Green
} else {
    Write-Host '  [注意] 依赖未就绪，交由启动脚本按正常流程安装' -ForegroundColor Yellow
}

Write-Host "  隔离实例：数据目录 $resolvedDataDir / API $ApiPort / Web $WebPort" -ForegroundColor Gray
& (Join-Path $PSScriptRoot 'start_shine.ps1') -DataDir $resolvedDataDir -ApiPort $ApiPort -WebPort $WebPort -Provider $Provider
