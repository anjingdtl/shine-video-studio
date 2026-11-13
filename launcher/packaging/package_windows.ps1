param(
    [string] $ArtifactDirectory = (Join-Path $PSScriptRoot "../target/release")
)

$ErrorActionPreference = "Stop"
$launcherRoot = Split-Path -Parent $PSScriptRoot
$repositoryRoot = Split-Path -Parent $launcherRoot
$required = @(
    (Join-Path $ArtifactDirectory "shine-launcher.exe"),
    (Join-Path $ArtifactDirectory "shine-launcher.exe.sha256"),
    (Join-Path $ArtifactDirectory "launcher-build-info.json")
)
foreach ($path in $required) {
    if (-not (Test-Path -LiteralPath $path -PathType Leaf)) {
        throw "Required launcher build artifact is missing: $path"
    }
}

$buildInfo = Get-Content -LiteralPath (Join-Path $ArtifactDirectory "launcher-build-info.json") -Raw | ConvertFrom-Json
$archive = Join-Path $ArtifactDirectory "shine-launcher-windows-x64.zip"
$stageRoot = Join-Path $ArtifactDirectory ".package-stage-$PID"
$stage = Join-Path $stageRoot "shine-launcher"
$expanded = Join-Path $stageRoot "expanded"

try {
    New-Item -ItemType Directory -Path $stage -Force | Out-Null
    New-Item -ItemType Directory -Path (Join-Path $stage "licenses") -Force | Out-Null
    Copy-Item -LiteralPath $required[0] -Destination $stage
    Copy-Item -LiteralPath $required[1] -Destination $stage
    Copy-Item -LiteralPath $required[2] -Destination $stage
    Copy-Item -LiteralPath (Join-Path $repositoryRoot "LICENSE") -Destination $stage
    Copy-Item -LiteralPath (Join-Path $launcherRoot "LICENSE_NOTICES.md") -Destination $stage
    Copy-Item -LiteralPath (Join-Path $PSScriptRoot "release-readme.md") -Destination (Join-Path $stage "README.md")
    Copy-Item -LiteralPath (Join-Path $launcherRoot "licenses/LicenseRef-Slint-Royalty-free-2.0.md") -Destination (Join-Path $stage "licenses")
    Copy-Item -LiteralPath (Join-Path $launcherRoot "licenses/CDLA-Permissive-2.0.txt") -Destination (Join-Path $stage "licenses")

    if (Test-Path -LiteralPath $archive) {
        Remove-Item -LiteralPath $archive -Force
    }
    Compress-Archive -Path (Join-Path $stage "*") -DestinationPath $archive -CompressionLevel Optimal

    Add-Type -AssemblyName System.IO.Compression.FileSystem
    [System.IO.Compression.ZipFile]::ExtractToDirectory($archive, $expanded)
    & (Join-Path $PSScriptRoot "verify_windows.ps1") -ArtifactDirectory $expanded
    [pscustomobject]@{
        archive = $archive
        version = $buildInfo.version
        target = $buildInfo.target
        source_commit = $buildInfo.source_commit
        exe_sha256 = $buildInfo.exe_sha256
        archive_sha256 = (Get-FileHash -LiteralPath $archive -Algorithm SHA256).Hash.ToLowerInvariant()
    }
} finally {
    if (Test-Path -LiteralPath $stageRoot) {
        Remove-Item -LiteralPath $stageRoot -Recurse -Force
    }
}
