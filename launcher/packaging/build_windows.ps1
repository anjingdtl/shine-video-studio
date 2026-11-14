param(
    [switch] $SmokeCheck,
    [switch] $SelfTest
)

$ErrorActionPreference = "Stop"
$projectRoot = Split-Path -Parent $PSScriptRoot
Push-Location $projectRoot
try {
    cargo +1.100.0 build --locked --release
    if ($LASTEXITCODE -ne 0) {
        throw "Cargo release build failed with exit code $LASTEXITCODE"
    }

    $exe = Join-Path $projectRoot "target/release/shine-launcher.exe"
    if (-not (Test-Path -LiteralPath $exe -PathType Leaf)) {
        throw "Expected Windows executable was not produced: $exe"
    }

    if ($SmokeCheck) {
        $smokeProcess = Start-Process -FilePath $exe -ArgumentList @("--smoke-check") -Wait -PassThru
        if ($smokeProcess.ExitCode -ne 0) {
            throw "Launcher smoke check failed with exit code $($smokeProcess.ExitCode)"
        }
    }

    if ($SelfTest) {
        # The self test drives the installation registry, the upgrade state
        # machine, the capability planner and the deployment protocol against
        # the real trust anchors. It needs no display, no studio, no network
        # and no GPU, so a broken launcher fails the build instead of being
        # discovered by a user.
        $selfTestProcess = Start-Process -FilePath $exe -ArgumentList @("--self-test") -Wait -PassThru -NoNewWindow
        if ($selfTestProcess.ExitCode -ne 0) {
            throw "Launcher self test failed with exit code $($selfTestProcess.ExitCode)"
        }
    }

    $hash = (Get-FileHash -LiteralPath $exe -Algorithm SHA256).Hash.ToLowerInvariant()
    $sourceCommit = $env:LAUNCHER_SOURCE_COMMIT
    if ($sourceCommit -notmatch '^[a-fA-F0-9]{40}$') {
        $sourceCommit = (git rev-parse HEAD).Trim()
        if ($LASTEXITCODE -ne 0 -or $sourceCommit -notmatch '^[a-fA-F0-9]{40}$') {
            throw "Unable to determine the 40-character launcher source commit."
        }
    }
    [pscustomobject]@{
        name = "Shine Launcher"
        version = (Select-String -Path (Join-Path $projectRoot "Cargo.toml") -Pattern '^version = "([^"]+)"' | Select-Object -First 1).Matches.Groups[1].Value
        rust_toolchain = "1.100.0"
        target = "x86_64-pc-windows-msvc"
        source_commit = $sourceCommit.ToLowerInvariant()
        exe_sha256 = $hash
        windows_gui_acceptance = "PENDING_LOCAL_ACCEPTANCE"
        self_test = $(if ($SelfTest) { "PASSED" } else { "SKIPPED" })
    } | ConvertTo-Json | Set-Content -LiteralPath (Join-Path $projectRoot "target/release/launcher-build-info.json") -Encoding utf8

    "$hash  shine-launcher.exe" | Set-Content -LiteralPath (Join-Path $projectRoot "target/release/shine-launcher.exe.sha256") -NoNewline
} finally {
    Pop-Location
}
