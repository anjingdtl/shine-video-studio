# Shine Launcher V1.0 implementation status

This report is updated with evidence from the implementation branch. A stage is
not accepted by source inspection alone where the engineering plan requires a
real Windows machine.

See [the engineering acceptance report](SHINE_LAUNCHER_ENGINEERING_ACCEPTANCE_REPORT_20261113.md)
for exact checks, boundaries, and remaining merge gates.

| Stage | Status | Evidence / remaining gate |
| --- | --- | --- |
| L0 native executable prototype | SOURCE_IMPLEMENTED / PENDING_GUI_ACCEPTANCE | Rust + Slint CPU software-rendered GUI source, Chinese UI, file picker and independent read-only host probe. Windows Server 2025 CI compiled the executable, passed 22 Windows unit tests and Clippy, and verified the dependency-independent smoke-check and binary hash. Actual GUI startup and Windows 10/11 Chinese text/DPI/path behavior remain unverified. |
| L1 registry and legacy discovery | SOURCE_IMPLEMENTED / PARTIAL | Read-only selected-path scan, protected data-path evidence, atomic per-user installation registry, and host checks are implemented. Rust unit tests pass. New-install directory flow, full ComfyUI/version inventory, multi-instance GUI handling, Chinese Windows paths, and Windows no-write acceptance remain outstanding. |
| L2 deployment protocol and channels | PARTIAL / FAIL_CLOSED | V1 schema/parser/compatibility checks, JCS source identity, scoped Ed25519 verification, configured Studio/Launcher release tags, explicit GitHub metadata scanner/cache, and immutable development-ref resolver are implemented. The embedded publisher store is empty; no asset downloader or Studio/Launcher installer/self-updater exists, so remote installation remains disabled. |
| L3 install/upgrade/recovery | NOT STARTED | Must reuse controlled deployment paths and durable receipts. |
| L4 capability packs and distribution | PARTIAL | Windows ZIP pack and extracted-artifact identity verification pass in workflow run [43420629491](https://github.com/anjingdtl/shine-video-studio/actions/runs/43420629491). A push-only publisher job is configured to add the verified EXE, hash, and build identity to the implementation branch root; that commit remains pending. Capability packs, full deployment, and independent Launcher release remain unimplemented. |
| L5 final regression and acceptance | PARTIAL / PENDING_LOCAL_ACCEPTANCE | Linux regression and a full Windows x64 build, test, Clippy, smoke, hash, and ZIP verification pass. Still requires Windows 10/11 GUI, clean install, legacy data-preserving upgrade/rollback, root Clone binary publication, and real GPU/model acceptance. |

Verification on the isolated Linux worktree:

- Launcher: `cargo fmt --all -- --check`, `cargo check --locked --all-targets`,
  `cargo test --locked --lib` (24 tests), `cargo clippy --locked --all-targets
  -- -D warnings`, and `cargo check --locked --target
  x86_64-pc-windows-msvc --all-targets` pass. The Linux release build and
  `--smoke-check` pass; the smoke mode verifies the host probe and does not show
  the GUI. Its output is an ELF Linux executable, not a Windows deliverable.
- Backend: all 204 test files ran in eight file-count-balanced shards (26, 26,
  26, 26, 25, 25, 25, 25). All eight exit codes are 0 and logs contain no
  `FAILED` or `ERROR` lines. Windows-only process/port tests are skipped on
  Linux; their real Windows execution remains pending. Per-shard logs and
  isolated SQLite databases are under the ignored `work/backend-shards/`.
- Frontend: `npx vitest run --no-file-parallelism` passes 42 files / 273 tests;
  `npm run build` passes TypeScript and Vite production build (with the existing
  large-chunk advisory).
- Python static and migrations: `ruff check backend` passes. Alembic upgraded an
  isolated database to `f180c0020001` and `alembic check` reported no new
  operations.

The GitHub release tag prefixes are currently a proposed repository convention;
the checked remote has no matching release tags. Windows CI did not open the
GUI. The current branch head still lacks the root Clone EXE; a push-only
publisher is configured to copy the hash-verified Windows binary into the
branch after a successful push build. The artifact's temporary download URL
was blocked by the local network proxy, so that publisher path will be
validated against GitHub Actions directly. Windows 10/11 GUI and Chinese
rendering, Clone-provided EXE after publisher completion, old-install
upgrade/rollback, self-update rollback, and real model inference remain
`PENDING_LOCAL_ACCEPTANCE`.
