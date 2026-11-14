<#
.SYNOPSIS
    Installs the launcher's GitHub Actions workflows into .github/workflows.

.DESCRIPTION
    The canonical copies of the launcher workflows live in this directory so
    that the launcher owns its own continuous integration definitions. GitHub
    only runs workflows that live under .github/workflows, so this script
    copies them into place.

    The copy is deterministic: the files are byte-identical afterwards, which
    is what the repository check in the acceptance report relies on.

.EXAMPLE
    pwsh -File launcher/packaging/ci/install_workflows.ps1
#>
[CmdletBinding()]
param(
    [switch] $Verify
)

$ErrorActionPreference = "Stop"
$scriptRoot = Split-Path -Parent $PSScriptRoot
$repoRoot = (Resolve-Path (Join-Path $scriptRoot "../..")).Path
$sourceRoot = Join-Path $scriptRoot "ci"
$targetRoot = Join-Path $repoRoot ".github/workflows"

$files = @("launcher-windows.yml", "launcher-release.yml")
$problems = @()

foreach ($name in $files) {
    $source = Join-Path $sourceRoot $name
    $target = Join-Path $targetRoot $name
    if (-not (Test-Path -LiteralPath $source -PathType Leaf)) {
        $problems += "缺少工作流源文件：$source"
        continue
    }
    if ($Verify) {
        if (-not (Test-Path -LiteralPath $target -PathType Leaf)) {
            $problems += "工作流尚未安装：$target"
            continue
        }
        $sourceHash = (Get-FileHash -LiteralPath $source -Algorithm SHA256).Hash
        $targetHash = (Get-FileHash -LiteralPath $target -Algorithm SHA256).Hash
        if ($sourceHash -ne $targetHash) {
            $problems += "已安装的工作流与源文件不一致：$name"
        }
        continue
    }
    New-Item -ItemType Directory -Force -Path $targetRoot | Out-Null
    Copy-Item -LiteralPath $source -Destination $target -Force
    Write-Output "已安装工作流：$target"
}

if ($problems.Count -gt 0) {
    foreach ($problem in $problems) { Write-Error $problem }
    exit 1
}

if (-not $Verify) {
    Write-Output "启动器的 GitHub Actions 工作流已安装完成。"
}
