param([string]$DataDir = '')

# Resolve the same Python and data directory as the start entry.
$ErrorActionPreference = 'Stop'
$PSNativeCommandUseErrorActionPreference = $false
$repoRoot = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
. (Join-Path $PSScriptRoot 'shine_local_config.ps1')
$stackSettings = Get-ShineStackSettings $repoRoot $DataDir
$dataPath = $stackSettings.DataDir
$venvPython = $stackSettings.Python
$env:PYTHONPATH = Join-Path $repoRoot 'backend'

Write-Host "Data directory: $dataPath" -ForegroundColor Gray
if (-not (Test-Path -LiteralPath (Join-Path $dataPath 'run/processes.json'))) {
    Write-Host 'No running stack was recorded for this installation. Nothing to stop.' -ForegroundColor Yellow
    exit 0
}
if (-not (Test-Path -LiteralPath $venvPython)) {
    Write-Host "Backend environment is missing: $venvPython" -ForegroundColor Red
    exit 2
}
& $venvPython (Join-Path $PSScriptRoot 'stop_shine.py') --data-dir $dataPath
$code = $LASTEXITCODE
if ($code -eq 0) {
    Write-Host 'Stopped. Start again with START_APP.cmd.' -ForegroundColor Green
} else {
    Write-Host "Some processes refused to stop (exit $code). See the lines above." -ForegroundColor Red
}
exit $code
