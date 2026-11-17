param(
    [ValidateRange(1, 65535)][int]$ApiPort = 8000,
    [ValidateRange(1, 65535)][int]$WebPort = 5173,
    [ValidateRange(0, 600)][int]$CloseAfterSeconds = 12,
    [switch]$KeepOpen
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
& $runner -NoProfile -ExecutionPolicy Bypass -File $entry -ApiPort $ApiPort -WebPort $WebPort -AutoPorts -OpenBrowser
$code = $LASTEXITCODE
if ($code -eq 0) {
    if (-not $KeepOpen -and $CloseAfterSeconds) { Start-Sleep -Seconds $CloseAfterSeconds }
} else {
    Write-Host '启动失败。若缺 Python 或 Node.js，请运行 scripts\bootstrap.ps1 安装本机工具。' -ForegroundColor Yellow
    if (-not $KeepOpen) { Read-Host '按回车关闭' | Out-Null }
}
exit $code
