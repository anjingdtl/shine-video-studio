param(
    [switch] $SmokeCheck
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
        & $exe --smoke-check
        if ($LASTEXITCODE -ne 0) {
            throw "Launcher smoke check failed with exit code $LASTEXITCODE"
        }
    }

    $hash = (Get-FileHash -LiteralPath $exe -Algorithm SHA256).Hash.ToLowerInvariant()
    [pscustomobject]@{
        name = "Shine Launcher"
        version = (Select-String -Path (Join-Path $projectRoot "Cargo.toml") -Pattern '^version = "([^"]+)"' | Select-Object -First 1).Matches.Groups[1].Value
        rust_toolchain = "1.100.0"
        target = "x86_64-pc-windows-msvc"
        source_commit = (git rev-parse HEAD).Trim()
        exe_sha256 = $hash
        windows_gui_acceptance = "PENDING_LOCAL_ACCEPTANCE"
    } | ConvertTo-Json | Set-Content -LiteralPath (Join-Path $projectRoot "target/release/launcher-build-info.json") -Encoding utf8

    "$hash  shine-launcher.exe" | Set-Content -LiteralPath (Join-Path $projectRoot "target/release/shine-launcher.exe.sha256") -NoNewline
} finally {
    Pop-Location
}
