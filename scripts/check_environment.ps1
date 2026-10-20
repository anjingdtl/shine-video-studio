$ErrorActionPreference = 'Stop'
$repoRoot = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path

function Require-Command([string]$name, [string]$hint) {
    if (-not (Get-Command $name -ErrorAction SilentlyContinue)) {
        throw "$name is required. $hint"
    }
}

Require-Command 'python' 'Install Python 3.12 and add it to PATH.'
Require-Command 'uv' 'Install Astral uv and add it to PATH.'
Require-Command 'node' 'Install Node.js 20 or newer and add it to PATH.'
Require-Command 'npm' 'Install Node.js with npm.'

$pythonVersion = & python -c 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}")'
if ($LASTEXITCODE -ne 0 -or $pythonVersion -ne '3.12') {
    throw "Python 3.12 is required; found $pythonVersion."
}
$nodeVersion = & node --version
$nodeMajor = [int]($nodeVersion.TrimStart('v').Split('.')[0])
if ($LASTEXITCODE -ne 0 -or $nodeMajor -lt 20) {
    throw "Node.js 20 or newer is required; found major version $nodeMajor."
}

Write-Host "Python $pythonVersion, Node.js $(& node --version), npm $(& npm --version)"
Write-Host "uv $(& uv --version)"
if (Get-Command ffmpeg -ErrorAction SilentlyContinue) {
    Write-Host 'FFmpeg is available (optional until M2 media validation).'
} else {
    Write-Host 'FFmpeg is not installed; it is optional for the current Mock workflow.'
}
Write-Host "Repository: $repoRoot"
