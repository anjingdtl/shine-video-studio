param(
    [ValidateRange(1, 65535)][int]$ApiPort = 8000,
    [ValidateRange(1, 65535)][int]$WebPort = 5173,
    [ValidateRange(0, 600)][int]$CloseAfterSeconds = 12,
    [switch]$KeepOpen,
    # The desktop double-click passes -Update: fast-forward this checkout
    # before starting, and stop the running stack first when the code moved.
    [switch]$Update,
    [string]$DataDir = ''
)

# This is the desktop entry. The same launcher as START_APP owns dependency
# repair, process reconciliation, port selection, and the browser landing.
$ErrorActionPreference = 'Stop'
$PSNativeCommandUseErrorActionPreference = $false
try {
    [Console]::OutputEncoding = [Text.Encoding]::UTF8
    $OutputEncoding = [Text.Encoding]::UTF8
} catch { }

$runner = (Get-Command pwsh -ErrorAction SilentlyContinue).Source
if (-not $runner) { $runner = (Get-Process -Id $PID).Path }
$entry = Join-Path $PSScriptRoot 'start_shine.ps1'

if ($Update) {
    # update_checkout.ps1 is the only authority on "this checkout is current".
    # It never touches uncommitted work, and every failure path still returns a
    # code that lets the local code start.
    #
    # Whether the *running* stack must be replaced is a separate decision that
    # start_shine.ps1 makes from the recorded source identity — it also covers
    # a manual pull, a local commit or an uncommitted runtime edit that happened
    # before this double-click, which a fast-forward exit code alone cannot see.
    $updateScript = Join-Path $PSScriptRoot 'update_checkout.ps1'
    if (Test-Path -LiteralPath $updateScript) {
        & $runner -NoProfile -ExecutionPolicy Bypass -File $updateScript
    }
}

$startArgs = @('-NoProfile', '-ExecutionPolicy', 'Bypass', '-File', $entry,
    '-ApiPort', $ApiPort, '-WebPort', $WebPort, '-AutoPorts', '-OpenBrowser')
if ($DataDir) { $startArgs += @('-DataDir', $DataDir) }
& $runner @startArgs
$code = $LASTEXITCODE
if ($code -eq 0) {
    if (-not $KeepOpen -and $CloseAfterSeconds) { Start-Sleep -Seconds $CloseAfterSeconds }
} else {
    Write-Host '启动失败。若缺 Python 或 Node.js，请运行 scripts\bootstrap.ps1 安装本机工具。' -ForegroundColor Yellow
    if (-not $KeepOpen) { Read-Host '按回车关闭' | Out-Null }
}
exit $code
