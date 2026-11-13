# Shine Launcher

Native Rust + Slint application for the Shine Video Studio lifecycle manager.
The launcher is a separate executable and does not depend on the Studio Python,
Node, API, Worker, ComfyUI, or GPU runtime.

## Current milestone

Engineering preview: the native window, read-only host probe, legacy directory
scanner/registry, Deployment Protocol V1 validator and GitHub release metadata
scanner are implemented. The scanner runs only after an explicit user click; it
does not download release assets. The trust store is empty, so installation,
Studio updates and Launcher self-updates are disabled.

Windows GUI startup, Chinese rendering, DPI behavior, clean-machine dependency
independence, Clone distribution and packaging remain
`PENDING_LOCAL_ACCEPTANCE` until checked on Windows 10/11 x64. See
[`../docs/SHINE_LAUNCHER_IMPLEMENTATION_STATUS.md`](../docs/SHINE_LAUNCHER_IMPLEMENTATION_STATUS.md)
for stage evidence and remaining gates.

## Build

The pinned Rust toolchain is in `rust-toolchain.toml`; dependencies are pinned
in `Cargo.toml` and `Cargo.lock`.

```powershell
cargo build --release
```

The intended Windows build uses the MSVC target on a Windows runner. A build on
Linux is useful for source checks only and is not Windows acceptance evidence.

## Safety behavior in L0

- Host checks are read-only and run on a background thread.
- Missing Node.js, Python, uv, FFmpeg, NVIDIA utilities, or GPU does not block
  the GUI.
- The current version does not modify Studio files, user databases, models,
  secrets, processes, or ports. A version scan reads GitHub release metadata
  and stores a bounded cache in Launcher user data.
- AI capability remains unverified until a real inference contract succeeds.

## Current implementation boundary

The implementation currently provides source code and a Windows CI build
workflow. A prebuilt `ShineLauncher.exe` is not yet present in a fresh Git clone.
There is no deployment artifact downloader, installer transaction, rollback
engine, process controller, capability-pack validator, or self-updater. Those
stages must pass Windows recovery and data-preservation acceptance before the
Launcher can be marked ready for release.
