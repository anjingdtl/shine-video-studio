<#
.SYNOPSIS
    Installs the optional signed release workflow into .github/workflows.

.DESCRIPTION
    The Windows build workflow lives in .github/workflows. This script
    installs the separate signed release template and can verify its copy.

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

$name = "launcher-release.yml"
$source = Join-Path $sourceRoot $name
$target = Join-Path $targetRoot $name
if (-not (Test-Path -LiteralPath $source -PathType Leaf)) { throw "缺少发布模板：$source" }

if ($Verify) {
    if (-not (Test-Path -LiteralPath $target -PathType Leaf)) { throw "签名发布工作流尚未安装：$target" }
    $sourceHash = (Get-FileHash -LiteralPath $source -Algorithm SHA256).Hash
    $targetHash = (Get-FileHash -LiteralPath $target -Algorithm SHA256).Hash
    if ($sourceHash -ne $targetHash) { throw "已安装的发布工作流与源文件不一致：$name" }
    return
}

New-Item -ItemType Directory -Force -Path $targetRoot | Out-Null
Copy-Item -LiteralPath $source -Destination $target -Force
Write-Output "已安装签名发布工作流：$target"
