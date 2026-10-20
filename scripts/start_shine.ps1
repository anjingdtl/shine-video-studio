param([switch]$Install)
$ErrorActionPreference = 'Stop'
$repoRoot = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path

& (Join-Path $PSScriptRoot 'check_environment.ps1')

if ($Install -or -not (Test-Path (Join-Path $repoRoot 'backend/.venv'))) {
    & uv sync --project (Join-Path $repoRoot 'backend') --locked
    if ($LASTEXITCODE -ne 0) { throw 'Python dependency installation failed.' }
}
if ($Install -or -not (Test-Path (Join-Path $repoRoot 'frontend/node_modules'))) {
    & npm --prefix (Join-Path $repoRoot 'frontend') ci
    if ($LASTEXITCODE -ne 0) { throw 'Frontend dependency installation failed.' }
}

& uv run --project (Join-Path $repoRoot 'backend') alembic -c (Join-Path $repoRoot 'backend/alembic.ini') upgrade head
if ($LASTEXITCODE -ne 0) { throw 'Database migration failed.' }

$backendCommand = 'uv run --project backend uvicorn shine.main:app --host 127.0.0.1 --port 8000'
$workerCommand = 'uv run --project backend python -m shine.worker'
$frontendCommand = "npm --prefix `"$repoRoot/frontend`" run dev -- --host 127.0.0.1"
Start-Process powershell.exe -WorkingDirectory $repoRoot -ArgumentList @('-NoExit', '-Command', $backendCommand)
Start-Process powershell.exe -WorkingDirectory $repoRoot -ArgumentList @('-NoExit', '-Command', $workerCommand)
Start-Process powershell.exe -WorkingDirectory $repoRoot -ArgumentList @('-NoExit', '-Command', $frontendCommand)

Write-Host 'Shine API:      http://127.0.0.1:8000/health'
Write-Host 'Shine Web:      http://127.0.0.1:5173'
Write-Host 'Mock worker:    running in its own PowerShell window'
