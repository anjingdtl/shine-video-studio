$ErrorActionPreference = 'Stop'
$repoRoot = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path

function Require-Command([string]$name, [string]$hint) {
    if (-not (Get-Command $name -ErrorAction SilentlyContinue)) {
        throw "$name is required. $hint"
    }
}

Require-Command 'uv' 'Install Astral uv and add it to PATH.'
Require-Command 'node' 'Install Node.js 20 or newer and add it to PATH.'
Require-Command 'npm' 'Install Node.js with npm.'

$managedPython = & uv python find 3.12 2>$null
if ($LASTEXITCODE -ne 0 -or -not $managedPython) {
    throw 'uv-managed Python 3.12 is required. Run: uv python install 3.12'
}
$pythonVersion = '3.12'
$nodeVersion = & node --version
$nodeMajor = [int]($nodeVersion.TrimStart('v').Split('.')[0])
if ($LASTEXITCODE -ne 0 -or $nodeMajor -lt 20) {
    throw "Node.js 20 or newer is required; found major version $nodeMajor."
}

$dataDir = if ($env:SHINE_DATA_DIR) { $env:SHINE_DATA_DIR } else { Join-Path $repoRoot '.local-data' }
$databaseUrl = if ($env:SHINE_DATABASE_URL) { $env:SHINE_DATABASE_URL } else { "sqlite:///$((Join-Path $dataDir 'shine.db').Replace('\','/'))" }
$provider = if ($env:SHINE_RENDER_PROVIDER) { $env:SHINE_RENDER_PROVIDER } else { 'mock' }

Write-Host "Python $pythonVersion (uv-managed: $managedPython), Node.js $(& node --version), npm $(& npm --version)"
Write-Host "uv $(& uv --version)"
Write-Host "Repository: $repoRoot"
Write-Host "Data directory: $dataDir"
Write-Host "Database: $databaseUrl"
Write-Host "Render provider: $provider"
if (Get-Command ffprobe -ErrorAction SilentlyContinue) {
    Write-Host "FFprobe: $(& ffprobe -version | Select-Object -First 1)"
} else {
    Write-Host 'FFprobe is not installed; technical QC will report decode/silence checks as unavailable, not passed.'
}
if (Get-Command ffmpeg -ErrorAction SilentlyContinue) {
    Write-Host "FFmpeg: $(& ffmpeg -version | Select-Object -First 1)"
} else {
    Write-Host 'FFmpeg is not installed; delivery assembly and silence detection are unavailable.'
}

if (Test-Path $dataDir) {
    $drive = (Get-Item $dataDir).PSDrive
    if ($drive -and $drive.Free) {
        Write-Host ("Free space on {0}: {1:N1} GiB" -f $drive.Name, ($drive.Free / 1GB))
    }
} else {
    Write-Host 'Data directory does not exist yet; startup will create it.'
}

foreach ($port in @(8000, 5173)) {
    $listener = Get-NetTCPConnection -LocalPort $port -State Listen -ErrorAction SilentlyContinue
    if ($listener) {
        Write-Warning "Port $port is already in use. Stop the other process before starting Shine."
    }
}

Write-Host 'Environment check passed.'
