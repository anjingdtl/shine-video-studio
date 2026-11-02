# Shine new-machine bootstrap entry.
# Runs on stock Windows PowerShell 5.1: detects the pinned tools, installs the
# missing ones at user level from the pinned manifest, and reports honest
# blockers. Never touches system-wide state, drivers or an existing install.
# Usage:
#   powershell -NoProfile -ExecutionPolicy Bypass -File scripts\bootstrap.ps1 [-CheckOnly] [-ToolsManifest <path>]
param(
    [switch]$CheckOnly,
    [string]$ToolsManifest = "",
    [string]$InstallRoot = "",
    [switch]$NoPathUpdate,
    [switch]$NoLaunch,
    [string]$DataDir = "",
    [string]$StorageRoot = "",
    [string]$OperatorPasswordFile = ""
)
# Output encoding contract: everything this script writes to stdout/stderr is
# UTF-8 on both Windows PowerShell 5.1 and PowerShell 7, so callers (tests and
# the installer) can decode it deterministically regardless of system locale.
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
$OutputEncoding = [System.Text.Encoding]::UTF8
$ErrorActionPreference = "Stop"
$repoRoot = Split-Path -Parent $PSScriptRoot
$explicitDataDir = [bool]$DataDir
$defaultManifest = -not [bool]$ToolsManifest
if (-not $ToolsManifest) { $ToolsManifest = Join-Path $repoRoot "config\deployment\tools-windows-x64.json" }
if (-not (Test-Path $ToolsManifest)) { throw "Tools manifest missing: $ToolsManifest" }
$manifest = Get-Content $ToolsManifest -Raw | ConvertFrom-Json
if ($defaultManifest -and -not $CheckOnly -and -not $StorageRoot) {
    $StorageRoot = Read-Host 'Choose a non-system-drive installation directory (for example D:\ShineVideoStudio)'
    if (-not $StorageRoot) { throw 'Choose the installation directory before downloading tools or dependencies.' }
}
if ($StorageRoot -and -not $CheckOnly) {
    if (-not [IO.Path]::IsPathRooted($StorageRoot) -or $StorageRoot.StartsWith('\\')) {
        throw 'StorageRoot must be an absolute local directory.'
    }
    $StorageRoot = [IO.Path]::GetFullPath($StorageRoot)
    $storageDrive = [IO.Path]::GetPathRoot($StorageRoot).TrimEnd('\')
    $bootstrapSystemDrive = if ($env:SystemDrive) { $env:SystemDrive } else { 'C:' }
    if ($storageDrive -eq $bootstrapSystemDrive) { throw 'Choose a non-system drive for automatic bootstrap downloads.' }
    New-Item -ItemType Directory -Force -Path $StorageRoot | Out-Null
    $env:UV_CACHE_DIR = Join-Path $StorageRoot 'download-cache\uv'
    $env:UV_PYTHON_INSTALL_DIR = Join-Path $StorageRoot 'python'
    $env:PIP_CACHE_DIR = Join-Path $StorageRoot 'download-cache\pip'
    $env:npm_config_cache = Join-Path $StorageRoot 'download-cache\npm'
    $env:SHINE_BACKEND_ENV = Join-Path $StorageRoot 'app\backend-env'
    $env:SHINE_BOOTSTRAP_ROOT = $StorageRoot
    $env:TEMP = Join-Path $StorageRoot 'temp'
    $env:TMP = $env:TEMP
    New-Item -ItemType Directory -Force -Path $env:TEMP | Out-Null
}
$binRoot = if ($InstallRoot) { $InstallRoot } elseif ($StorageRoot) { Join-Path $StorageRoot 'bin' } else { Join-Path $env:LOCALAPPDATA "Shine\bin" }

$probes = @{
    "uv"      = @("uv", "--version")
    "node"    = @("node", "--version")
    "npm"     = @("npm.cmd", "--version")
    "pwsh"    = @("pwsh", "-NoProfile", "-Command", "`$PSVersionTable.PSVersion.ToString()")
    "ffmpeg"  = @("ffmpeg", "-version")
    "ffprobe" = @("ffprobe", "-version")
}
$floors = @{ "uv" = "0.9"; "node" = "20"; "npm" = "10"; "pwsh" = "7.3"; "ffmpeg" = "6"; "ffprobe" = "6" }

function Get-ToolVersion([string]$output) {
    if ($output -match "(\d+\.\d+[\w.+]*)") { return $Matches[1] }
    return ""
}

function Test-VersionAtFloor([string]$version, [string]$floor) {
    function Parse([string]$v) {
        $nums = [regex]::Matches($v, "\d+") | ForEach-Object { [int]$_.Value }
        @($nums) + @(0, 0) | Select-Object -First 3
    }
    $a = Parse $version
    $b = Parse $floor
    for ($i = 0; $i -lt 3; $i++) {
        if ($a[$i] -ne $b[$i]) { return ($a[$i] -gt $b[$i]) }
    }
    return $true
}

function Get-CommandResult([string]$tool, [string[]]$argv) {
    $cmd = Get-Command $argv[0] -ErrorAction SilentlyContinue
    if (-not $cmd) { return @{ Present = $false; Version = ""; Source = "" } }
    $out = & $argv[0] $argv[1..($argv.Count - 1)] 2>&1 | Out-String
    if ($LASTEXITCODE -ne 0) { return @{ Present = $false; Version = ""; Source = "" } }
    return @{ Present = $true; Version = (Get-ToolVersion $out); Source = $cmd.Source }
}

function Get-RemoteFile([string]$Url, [string]$Destination) {
    # A bare Windows may not ship curl.exe (older Win10 builds, Server images).
    # curl is preferred because it pins no .NET TLS default; the fallback sets
    # TLS 1.2 explicitly and stays usable without any installed tool.
    $curl = Get-Command curl.exe -ErrorAction SilentlyContinue
    if ($curl) {
        & curl.exe -sS -L --retry 3 --retry-delay 2 --connect-timeout 30 -o $Destination $Url
        if ($LASTEXITCODE -eq 0) { return }
        throw "download failed (curl exit $LASTEXITCODE)"
    }
    [Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12
    $previousProgress = $ProgressPreference
    $ProgressPreference = 'SilentlyContinue'
    try {
        Invoke-WebRequest -Uri $Url -OutFile $Destination -UseBasicParsing -TimeoutSec 900
    } catch {
        throw "download failed ($($_.Exception.Message))"
    } finally {
        $ProgressPreference = $previousProgress
    }
}

function Install-Tool([string]$name, $entry) {
    if ($name -notmatch '^[a-z][a-z0-9-]*$' -or $entry.version -notmatch '^[a-zA-Z0-9][a-zA-Z0-9._+-]*$') {
        throw 'Invalid pinned tool identity.'
    }
    $url = $entry.url
    $sha256 = $entry.sha256
    $expectedSize = [long]$entry.size
    $toolBase = if ($StorageRoot) { Join-Path $StorageRoot 'tools' } else { Join-Path $env:LOCALAPPDATA 'Shine\tools' }
    $toolsRoot = [IO.Path]::GetFullPath($toolBase)
    $toolDir = [IO.Path]::GetFullPath((Join-Path $toolsRoot ($name + '-' + $entry.version)))
    if (-not $toolDir.StartsWith($toolsRoot.TrimEnd('\') + '\', [StringComparison]::OrdinalIgnoreCase)) {
        throw 'Tool destination escaped the bootstrap tools directory.'
    }
    # A second run must not re-download, and must not die because the previous
    # install already put the pinned version here. Reuse only a complete tree:
    # every manifest entry has to be present, otherwise the operator has to
    # reconcile instead of silently running a half-extracted toolchain.
    if (Test-Path -LiteralPath $toolDir) {
        $missing = @()
        foreach ($leafEntry in $entry.entries) {
            $leaf = Split-Path -Leaf $leafEntry
            $found = Get-ChildItem $toolDir -Recurse -Filter $leaf -ErrorAction SilentlyContinue | Select-Object -First 1
            if (-not $found) { $missing += $leaf }
        }
        if ($missing.Count -gt 0) {
            throw "Pinned tool directory exists but is incomplete ($($missing -join ', ')); reconcile the previous installation."
        }
        Write-Host "[bootstrap] reusing $name $($entry.version) at $toolDir"
        return $toolDir
    }
    $cache = Join-Path $env:TEMP "shine-bootstrap-cache"
    New-Item -ItemType Directory -Force -Path $cache | Out-Null
    $archive = Join-Path $cache ($name + ".zip")
    Write-Host "[bootstrap] downloading $name $($entry.version)"
    Get-RemoteFile $url $archive
    $actual = (Get-FileHash $archive -Algorithm SHA256).Hash.ToLower()
    if ($actual -ne $sha256.ToLower()) { throw "sha256 mismatch for $name" }
    if ((Get-Item $archive).Length -ne $expectedSize) { throw "size mismatch for $name" }
    Write-Host "[bootstrap] sha256 verified for $name"
    New-Item -ItemType Directory -Force -Path $toolDir | Out-Null
    Expand-Archive -Path $archive -DestinationPath $toolDir -Force
    return $toolDir
}

function New-ToolShim([string]$binRoot, [string]$name, [string]$target) {
    New-Item -ItemType Directory -Force -Path $binRoot | Out-Null
    $shim = Join-Path $binRoot ($name + ".cmd")
    $line = '@echo off' + [Environment]::NewLine + '"' + $target + '" %*'
    [IO.File]::WriteAllText($shim, $line, (New-Object Text.UTF8Encoding($false)))
    return $shim
}

function Add-UserPath([string]$directory) {
    $userPath = [Environment]::GetEnvironmentVariable("Path", "User")
    if (-not ($userPath -split ";" -contains $directory)) {
        [Environment]::SetEnvironmentVariable("Path", $directory + ";" + $userPath, "User")
    }
    $env:Path = $directory + ";" + $env:Path
}

$report = @{ tools = @(); installed = @(); blockers = @() }
$shimTargets = @{}
$nativeDirectories = @()
foreach ($name in @("uv", "node", "npm", "pwsh", "ffmpeg", "ffprobe")) {
    $probe = $probes[$name]
    $result = Get-CommandResult $name $probe
    $atFloor = Test-VersionAtFloor $result.Version $floors[$name]
    if ($result.Present -and $atFloor) {
        $report.tools += @{ name = $name; status = "ok"; version = $result.Version; source = $result.Source }
        continue
    }
    if ($CheckOnly) {
        $state = if ($result.Present) { "stale" } else { "missing" }
        $report.blockers += @{ name = $name; status = $state }
        continue
    }
    $manifestTool = $manifest.tools.$name
    if (-not $manifestTool -and $name -in @("npm", "ffprobe")) { continue }
    if (-not $manifestTool) {
        $report.blockers += @{ name = $name; status = "no_pinned_manifest_entry" }
        continue
    }
    try {
        $toolDir = Install-Tool $name $manifestTool
        $shimTargets[$name] = $toolDir
        $report.installed += @{ name = $name; version = $manifestTool.version; dir = $toolDir }
    } catch {
        $report.blockers += @{ name = $name; status = "install_failed"; detail = $_.Exception.Message }
    }
}

# Create shims for every manifest entry of each installed tool and put them on the user PATH.
foreach ($name in $report.installed) {
    $toolDir = $shimTargets[$name.name]
    foreach ($entry in $manifest.tools.($name.name).entries) {
        $leaf = Split-Path -Leaf $entry
        $found = Get-ChildItem $toolDir -Recurse -Filter $leaf -ErrorAction SilentlyContinue | Select-Object -First 1
        if ($found) {
            $shimName = $leaf.Replace(".exe", "").Replace(".cmd", "")
            New-ToolShim $binRoot $shimName $found.FullName | Out-Null
            $nativeDirectories += $found.DirectoryName
        } else {
            $report.blockers += @{ name = $name.name; status = "entry_missing_after_extract"; detail = $leaf }
        }
    }
}
if ($report.installed.Count -gt 0 -and -not $NoPathUpdate) { Add-UserPath $binRoot }
if (-not $NoPathUpdate) {
    foreach ($directory in ($nativeDirectories | Select-Object -Unique)) { Add-UserPath $directory }
}
$report.bin_root = $binRoot
$report.reprobe = @{}
foreach ($name in @("uv", "node", "npm", "pwsh", "ffmpeg", "ffprobe")) {
    $result = Get-CommandResult $name $probes[$name]
    $report.reprobe[$name] = @{ present = $result.Present; version = $result.Version }
    if (-not $CheckOnly -and -not $NoPathUpdate -and
        (-not $result.Present -or -not (Test-VersionAtFloor $result.Version $floors[$name]))) {
        $report.blockers += @{ name = $name; status = "unavailable_after_install" }
    }
}
$report | ConvertTo-Json -Depth 5
if ($report.blockers.Count -gt 0) { exit 1 }
if ($defaultManifest -and -not $CheckOnly) {
    # Personal desktop installs need no operator password. Preserve explicit
    # legacy provisioning for scripts that already pass a credential file.
    if (-not $NoLaunch -and $OperatorPasswordFile) {
        . (Join-Path $PSScriptRoot 'bootstrap_identity.ps1')
        $operatorCredentialPath = [IO.Path]::GetFullPath($OperatorPasswordFile)
        $readPassword = { Get-ShineOperatorPasswordFile $operatorCredentialPath }
        Initialize-ShineOperator (Join-Path $repoRoot '.env') $readPassword
    }
    $env:SHINE_ENABLE_LOCAL_EXECUTION = '1'
    if (-not $DataDir) {
        if ($env:SHINE_DATA_DIR) { $DataDir = $env:SHINE_DATA_DIR }
        else {
            $existingEnvFile = Join-Path $repoRoot '.env'
            $storedEnvironment = if (Test-Path -LiteralPath $existingEnvFile) {
                Get-Content -LiteralPath $existingEnvFile -Raw -Encoding UTF8
            } else { '' }
            $storedDataDir = [regex]::Match($storedEnvironment, '(?m)^SHINE_DATA_DIR=([^\r\n]+)')
            if ($storedDataDir.Success) {
                $DataDir = $storedDataDir.Groups[1].Value.Trim()
                if ($DataDir.StartsWith("'") -or $DataDir.StartsWith('"')) {
                    if ($DataDir.Length -lt 2 -or $DataDir[-1] -ne $DataDir[0]) {
                        throw 'Pass the resolved existing data directory with -DataDir; its quoted .env value is ambiguous.'
                    }
                    $DataDir = $DataDir.Substring(1, $DataDir.Length - 2).Replace("\'", "'").Replace('\\', '\')
                } else { $DataDir = ($DataDir -replace '[ \t]+#.*$', '').Trim() }
                if ($DataDir.Contains('${')) { throw 'Pass the resolved existing data directory with -DataDir; bootstrap does not expand .env expressions.' }
                if (-not [IO.Path]::IsPathRooted($DataDir)) { $DataDir = Join-Path $repoRoot $DataDir }
            } elseif (Test-Path -LiteralPath (Join-Path $repoRoot '.local-data')) {
                $DataDir = Join-Path $repoRoot '.local-data'
            } else { $DataDir = Join-Path $StorageRoot 'data' }
        }
    }
    if (-not $explicitDataDir -and -not [IO.Path]::IsPathRooted($DataDir)) { $DataDir = Join-Path $repoRoot $DataDir }
    New-Item -ItemType Directory -Force -Path $DataDir | Out-Null
    $storagePreferences = Join-Path $DataDir 'deployment-storage.json'
    if (-not (Test-Path -LiteralPath $storagePreferences)) {
        $legacyDeployment = Join-Path $DataDir 'comfyops'
        if ((Test-Path -LiteralPath $legacyDeployment) -and (Get-ChildItem -LiteralPath $legacyDeployment -Force | Select-Object -First 1)) {
            throw 'Existing deployment files require reconciliation; bootstrap will not redirect or move them.'
        }
        $storageJson = @{ version = 1; root = (Join-Path $StorageRoot 'runtime'); allow_system_drive = $false } | ConvertTo-Json
        [IO.File]::WriteAllText($storagePreferences, $storageJson, (New-Object Text.UTF8Encoding($false)))
    }
    $envFile = Join-Path $repoRoot '.env'
    $localConfig = if (Test-Path -LiteralPath $envFile) { Get-Content -LiteralPath $envFile -Raw -Encoding UTF8 } else { '' }
    $launchSettings = @{
        SHINE_DATA_DIR = [IO.Path]::GetFullPath($DataDir)
        SHINE_BOOTSTRAP_ROOT = $StorageRoot
        SHINE_BACKEND_ENV = $env:SHINE_BACKEND_ENV
        UV_CACHE_DIR = $env:UV_CACHE_DIR
        UV_PYTHON_INSTALL_DIR = $env:UV_PYTHON_INSTALL_DIR
        PIP_CACHE_DIR = $env:PIP_CACHE_DIR
        npm_config_cache = $env:npm_config_cache
    }
    foreach ($key in $launchSettings.Keys) {
        $localConfig = [regex]::Replace($localConfig, "(?m)^$key=.*\r?\n?", '')
        $safeValue = $launchSettings[$key].Replace('\', '/').Replace("'", "\'")
        $localConfig += "`n$key='$safeValue'`n"
    }
    [IO.File]::WriteAllText($envFile, $localConfig, (New-Object Text.UTF8Encoding($false)))
    if ($NoLaunch) { exit 0 }
    $env:SHINE_DATA_DIR = [IO.Path]::GetFullPath($DataDir)
    if ($explicitDataDir) {
        & pwsh -NoProfile -ExecutionPolicy Bypass -File (Join-Path $PSScriptRoot 'start_shine.ps1') -Install -DataDir $DataDir -OpenBrowser
    } else {
        & pwsh -NoProfile -ExecutionPolicy Bypass -File (Join-Path $PSScriptRoot 'start_shine.ps1') -Install -OpenBrowser
    }
    exit $LASTEXITCODE
}
exit 0
