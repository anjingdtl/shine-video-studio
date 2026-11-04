param(
    [string]$ShortcutPath = '',
    [string]$PwshPath = ''
)

# Rebuild the repository-root development shortcut after the repo moves, the
# pinned PowerShell changes, or start_dev.ps1's arguments change. The existing
# shortcut is updated in place, so its icon survives.
$ErrorActionPreference = 'Stop'
$repoRoot = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
if (-not $ShortcutPath) { $ShortcutPath = Join-Path $repoRoot '一键启动 Shine 视频工作台.lnk' }
if (-not $PwshPath) {
    $command = Get-Command pwsh -ErrorAction SilentlyContinue
    if (-not $command) { throw 'PowerShell 7 (pwsh) is required to rebuild the shortcut.' }
    $PwshPath = $command.Source
}
$startScript = Join-Path $repoRoot 'scripts/start_dev.ps1'
if (-not (Test-Path -LiteralPath $startScript)) { throw "Missing launcher: $startScript" }

$shell = New-Object -ComObject WScript.Shell
$link = $shell.CreateShortcut($ShortcutPath)
$link.TargetPath = $PwshPath
$link.Arguments = "-NoProfile -ExecutionPolicy Bypass -File `"$startScript`""
$link.WorkingDirectory = $repoRoot
$link.Description = 'Shine 视频工作台 · 本机开发版一键启动'
$link.Save()

# Read it back: a shortcut that silently keeps an old target is worse than none.
$check = $shell.CreateShortcut($ShortcutPath)
if ($check.TargetPath -ne $PwshPath) { throw "Shortcut target was not updated: $($check.TargetPath)" }
if ($check.Arguments -notlike '*start_dev.ps1*') { throw "Shortcut arguments were not updated: $($check.Arguments)" }
if ($check.WorkingDirectory -ne $repoRoot) { throw "Shortcut working directory was not updated: $($check.WorkingDirectory)" }

Write-Host "Shortcut:  $ShortcutPath"
Write-Host "Target:    $PwshPath"
Write-Host "Arguments: $($check.Arguments)"
Write-Host "Workdir:   $repoRoot"
