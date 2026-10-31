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
    [switch]$NoPathUpdate
)
$ErrorActionPreference = "Stop"
$repoRoot = Split-Path -Parent $PSScriptRoot
if (-not $ToolsManifest) { $ToolsManifest = Join-Path $repoRoot "config\deployment\tools-windows-x64.json" }
if (-not (Test-Path $ToolsManifest)) { throw "Tools manifest missing: $ToolsManifest" }
$manifest = Get-Content $ToolsManifest -Raw | ConvertFrom-Json
$binRoot = if ($InstallRoot) { $InstallRoot } else { Join-Path $env:LOCALAPPDATA "Shine\bin" }

$probes = @{
    "uv"      = ,@("uv", "--version")
    "node"    = ,@("node", "--version")
    "npm"     = ,@("npm.cmd", "--version")
    "pwsh"    = ,@("pwsh", "-NoProfile", "-Command", "`$PSVersionTable.PSVersion.ToString()")
    "ffmpeg"  = ,@("ffmpeg", "-version")
    "ffprobe" = ,@("ffprobe", "-version")
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

function Install-Tool([string]$name, $entry) {
    $url = $entry.url
    $sha256 = $entry.sha256
    $expectedSize = [long]$entry.size
    $cache = Join-Path $env:TEMP "shine-bootstrap-cache"
    New-Item -ItemType Directory -Force -Path $cache | Out-Null
    $archive = Join-Path $cache ($name + ".zip")
    Write-Host "[bootstrap] downloading $name $($entry.version)"
    & curl.exe -sS -L -o $archive $url
    if ($LASTEXITCODE -ne 0) { throw "download failed for $name (curl exit $LASTEXITCODE)" }
    $actual = (Get-FileHash $archive -Algorithm SHA256).Hash.ToLower()
    if ($actual -ne $sha256.ToLower()) { throw "sha256 mismatch for $name" }
    if ((Get-Item $archive).Length -ne $expectedSize) { throw "size mismatch for $name" }
    Write-Host "[bootstrap] sha256 verified for $name"
    $toolDir = Join-Path $env:LOCALAPPDATA ("Shine\tools\" + $name + "-" + $entry.version)
    if (Test-Path $toolDir) { Remove-Item -Recurse -Force $toolDir }
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
    if ($userPath -split ";" -contains $directory) { return }
    [Environment]::SetEnvironmentVariable("Path", $directory + ";" + $userPath, "User")
    $env:Path = $directory + ";" + $env:Path
}

$report = @{ tools = @(); installed = @(); blockers = @() }
$shimTargets = @{}
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
        } else {
            $report.blockers += @{ name = $name.name; status = "entry_missing_after_extract"; detail = $leaf }
        }
    }
}
if ($report.installed.Count -gt 0 -and -not $NoPathUpdate) { Add-UserPath $binRoot }
$report.bin_root = $binRoot
$report.reprobe = @{}
foreach ($name in @("uv", "node", "npm", "pwsh", "ffmpeg", "ffprobe")) {
    $result = Get-CommandResult $name $probes[$name]
    $report.reprobe[$name] = @{ present = $result.Present; version = $result.Version }
}
$report | ConvertTo-Json -Depth 5
if ($report.blockers.Count -gt 0) { exit 1 }
exit 0
