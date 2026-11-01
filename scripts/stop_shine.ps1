# Stop the stack recorded by start_shine.ps1 for this installation.
# Reads the recorded data directory from .env so a tester never has to know it.

$ErrorActionPreference = 'Stop'
$PSNativeCommandUseErrorActionPreference = $false
$repoRoot = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
$envFile = Join-Path $repoRoot '.env'
$dataDir = ''
$backendEnv = ''

if (Test-Path -LiteralPath $envFile) {
    foreach ($line in (Get-Content -LiteralPath $envFile)) {
        if ($line -match '^([A-Za-z_][A-Za-z0-9_]*)=(.*)$') {
            $key = $Matches[1]
            $value = $Matches[2].Trim()
            if ($value.StartsWith("'") -or $value.StartsWith('"')) {
                if ($value.Length -ge 2 -and $value[-1] -eq $value[0]) {
                    $value = $value.Substring(1, $value.Length - 2).Replace("\'", "'").Replace('\\', '\')
                }
            } else {
                $value = ($value -replace '[ \t]+#.*$', '').Trim()
            }
            if ($key -eq 'SHINE_DATA_DIR' -and $value) { $dataDir = $value }
            if ($key -eq 'SHINE_BACKEND_ENV' -and $value) { $backendEnv = $value }
        }
    }
}
if (-not $dataDir -and $env:SHINE_DATA_DIR) { $dataDir = $env:SHINE_DATA_DIR }
if (-not $dataDir) { $dataDir = Join-Path $repoRoot '.local-data' }
if (-not $backendEnv) { $backendEnv = Join-Path $repoRoot 'backend/.venv' }
$venvPython = Join-Path $backendEnv 'Scripts/python.exe'

Write-Host "Data directory: $dataDir" -ForegroundColor Gray
if (-not (Test-Path -LiteralPath (Join-Path $dataDir 'run/processes.json'))) {
    Write-Host 'No running stack was recorded for this installation. Nothing to stop.' -ForegroundColor Yellow
    exit 0
}
if (-not (Test-Path -LiteralPath $venvPython)) {
    Write-Host "Backend environment is missing: $venvPython" -ForegroundColor Red
    exit 2
}
& $venvPython (Join-Path $PSScriptRoot 'stop_shine.py') --data-dir $dataDir
$code = $LASTEXITCODE
if ($code -eq 0) {
    Write-Host 'Stopped. Start again with START_APP.cmd.' -ForegroundColor Green
} else {
    Write-Host "Some processes refused to stop (exit $code). See the lines above." -ForegroundColor Red
}
exit $code
