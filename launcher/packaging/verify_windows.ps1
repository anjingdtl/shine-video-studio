param(
    [string] $ArtifactDirectory = (Join-Path $PSScriptRoot "../target/release")
)

$ErrorActionPreference = "Stop"
$exe = Join-Path $ArtifactDirectory "shine-launcher.exe"
$hashFile = Join-Path $ArtifactDirectory "shine-launcher.exe.sha256"
$infoFile = Join-Path $ArtifactDirectory "launcher-build-info.json"
foreach ($required in @($exe, $hashFile, $infoFile)) {
    if (-not (Test-Path -LiteralPath $required -PathType Leaf)) {
        throw "Required launcher artifact is missing: $required"
    }
}

$actualHash = (Get-FileHash -LiteralPath $exe -Algorithm SHA256).Hash.ToLowerInvariant()
$recordedHashLine = (Get-Content -LiteralPath $hashFile -Raw).Trim()
if ($recordedHashLine -notmatch '^([a-fA-F0-9]{64})\s+shine-launcher\.exe$') {
    throw "Invalid SHA-256 sidecar format: $hashFile"
}
if ($Matches[1].ToLowerInvariant() -ne $actualHash) {
    throw "Launcher executable SHA-256 does not match its sidecar."
}

$buildInfo = Get-Content -LiteralPath $infoFile -Raw | ConvertFrom-Json
if ($buildInfo.name -ne "Shine Launcher" -or
    $buildInfo.target -ne "x86_64-pc-windows-msvc" -or
    $buildInfo.exe_sha256 -ne $actualHash) {
    throw "Launcher build identity does not match the executable."
}
if ($buildInfo.windows_gui_acceptance -ne "PENDING_LOCAL_ACCEPTANCE") {
    throw "Build metadata must not claim Windows GUI acceptance from CI."
}

$smokeProcess = Start-Process -FilePath $exe -ArgumentList @("--smoke-check") -Wait -PassThru
if ($smokeProcess.ExitCode -ne 0) {
    throw "Launcher dependency-independent smoke check failed with exit code $($smokeProcess.ExitCode)"
}

[pscustomobject]@{
    version = $buildInfo.version
    target = $buildInfo.target
    source_commit = $buildInfo.source_commit
    sha256 = $actualHash
    smoke_check = "PASS"
    windows_gui_acceptance = $buildInfo.windows_gui_acceptance
}
