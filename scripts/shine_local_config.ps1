# Shared local paths for Windows launchers. A dotenv file is data, never code.
function Read-ShineLocalEnvironment([string]$RepoRoot) {
    $values = @{}
    $file = Join-Path $RepoRoot '.env'
    if (-not (Test-Path -LiteralPath $file -PathType Leaf)) { return $values }

    $keys = @('SHINE_DATA_DIR', 'SHINE_DATABASE_URL', 'SHINE_BOOTSTRAP_ROOT', 'SHINE_BACKEND_ENV',
        'UV_CACHE_DIR', 'UV_PYTHON_INSTALL_DIR', 'PIP_CACHE_DIR', 'npm_config_cache',
        'SHINE_RENDER_PROVIDER', 'SHINE_ENABLE_LOCAL_EXECUTION', 'SHINE_IDLE_SHUTDOWN_MINUTES',
        'SHINE_ALLOW_SYSTEM_DRIVE')
    foreach ($line in (Get-Content -LiteralPath $file -Encoding UTF8)) {
        if ($line -notmatch '^([A-Za-z_][A-Za-z0-9_]*)=(.*)$' -or $Matches[1] -notin $keys) { continue }
        $key, $value = $Matches[1], $Matches[2].Trim()
        if ($value.StartsWith("'") -or $value.StartsWith('"')) {
            if ($value.Length -lt 2 -or $value[-1] -ne $value[0]) { throw "Invalid quoted local setting: $key" }
            $value = $value.Substring(1, $value.Length - 2).Replace("\'", "'").Replace('\\', '\')
        } else {
            $value = ($value -replace '[ \t]+#.*$', '').Trim()
        }
        if ($value.Contains('$' + '{')) { throw "Use a resolved local value for $key; launcher does not expand .env expressions." }
        $values[$key] = $value
    }
    return $values
}

function Resolve-ShineLocalPath([string]$Path, [string]$RelativeTo) {
    if (-not [IO.Path]::IsPathRooted($Path)) { $Path = Join-Path $RelativeTo $Path }
    return [IO.Path]::GetFullPath($Path)
}

function Get-ShineStackSettings([string]$RepoRoot, [string]$DataDir = '') {
    $values = Read-ShineLocalEnvironment $RepoRoot
    foreach ($key in @($values.Keys) + @('SHINE_DATA_DIR', 'SHINE_BACKEND_ENV', 'SHINE_DATABASE_URL', 'SHINE_BOOTSTRAP_ROOT')) {
        $current = [Environment]::GetEnvironmentVariable($key, 'Process')
        if ($current) { $values[$key] = $current }
    }
    # A command-line path is relative to the caller. Configured paths are
    # relative to the checkout, like Settings.from_env in the backend.
    if ($values.SHINE_BOOTSTRAP_ROOT) {
        $values.SHINE_BOOTSTRAP_ROOT = Resolve-ShineLocalPath $values.SHINE_BOOTSTRAP_ROOT $RepoRoot
    }
    $dataPath = if ($DataDir) { Resolve-ShineLocalPath $DataDir (Get-Location).ProviderPath }
        elseif ($values.SHINE_DATA_DIR) { Resolve-ShineLocalPath $values.SHINE_DATA_DIR $RepoRoot }
        elseif ($values.SHINE_BOOTSTRAP_ROOT) { Join-Path $values.SHINE_BOOTSTRAP_ROOT 'data' }
        else { Join-Path $RepoRoot '.local-data' }
    $backendPath = if ($values.SHINE_BACKEND_ENV) { Resolve-ShineLocalPath $values.SHINE_BACKEND_ENV $RepoRoot }
        elseif ($values.SHINE_BOOTSTRAP_ROOT) { Join-Path $values.SHINE_BOOTSTRAP_ROOT 'app/backend-env' }
        else { Join-Path $RepoRoot 'backend/.venv' }
    # A process-level data override must not inherit a database URL from a
    # different installation's .env. Explicit -DataDir isolates both paths.
    $databaseOverride = if ($env:SHINE_DATA_DIR) { $env:SHINE_DATABASE_URL } else { $values.SHINE_DATABASE_URL }
    $database = if (-not $DataDir -and $databaseOverride) { $databaseOverride }
        else { "sqlite:///$((Join-Path $dataPath 'shine.db').Replace('\', '/'))" }
    return [pscustomobject]@{
        DataDir = $dataPath
        DatabaseUrl = $database
        BackendEnv = $backendPath
        Python = Join-Path $backendPath 'Scripts/python.exe'
        Environment = $values
    }
}

function Test-ShineCheckoutPython([string]$Python, [string]$RepoRoot) {
    if (-not (Test-Path -LiteralPath $Python -PathType Leaf)) { return $false }
    $probe = 'import sys; from pathlib import Path; import uvicorn, fastapi, alembic, sqlalchemy, shine; sys.exit(Path(shine.__file__).resolve() != Path(sys.argv[1]).resolve())'
    $previous = $ErrorActionPreference
    $ErrorActionPreference = 'Continue'
    try {
        & $Python -c $probe (Join-Path $RepoRoot 'backend/shine/__init__.py') 2>$null | Out-Null
        return $LASTEXITCODE -eq 0
    } finally {
        $ErrorActionPreference = $previous
    }
}
