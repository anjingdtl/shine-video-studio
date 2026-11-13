# Shine Launcher V1.0 implementation status

This report is updated with evidence from the implementation branch. A stage is
not accepted by source inspection alone where the engineering plan requires a
real Windows machine.

See [the engineering acceptance report](SHINE_LAUNCHER_ENGINEERING_ACCEPTANCE_REPORT_20261113.md)
for exact checks, boundaries, and remaining merge gates.

| Stage | Status | Evidence / remaining gate |
| --- | --- | --- |
| L0 native executable prototype | SOURCE_IMPLEMENTED / PENDING_WINDOWS | Rust + Slint CPU software-rendered GUI source, Chinese UI, file picker and independent read-only host probe. Linux Rust checks and `--smoke-check` pass; a Windows artifact and real Windows 10/11 GUI start are not yet verified. |
| L1 registry and legacy discovery | SOURCE_IMPLEMENTED / PARTIAL | Read-only selected-path scan, protected data-path evidence, atomic per-user installation registry, and host checks are implemented. Rust unit tests pass. New-install directory flow, full ComfyUI/version inventory, multi-instance GUI handling, Chinese Windows paths, and Windows no-write acceptance remain outstanding. |
| L2 deployment protocol and channels | PARTIAL / FAIL_CLOSED | V1 schema/parser/compatibility checks, JCS source identity, scoped Ed25519 verification, configured Studio/Launcher release tags, explicit GitHub metadata scanner/cache, and immutable development-ref resolver are implemented. The embedded publisher store is empty; no asset downloader or Studio/Launcher installer/self-updater exists, so remote installation remains disabled. |
| L3 install/upgrade/recovery | NOT STARTED | Must reuse controlled deployment paths and durable receipts. |
| L4 capability packs and distribution | NOT STARTED | Requires verified model/runtime contracts and a built Windows EXE. |
| L5 final regression and acceptance | NOT STARTED | Requires Windows clean install, legacy upgrade/rollback, Clone/ZIP artifact verification, full regression, packaging and data-preservation evidence. |

Verification on the isolated Linux worktree:

- Launcher: `cargo fmt --all -- --check`, `cargo check --locked --all-targets`,
  `cargo test --locked --lib` (22 tests), `cargo clippy --locked --all-targets
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
the checked remote has no matching release tags. No Windows or GPU acceptance
result is claimed. A prebuilt EXE is not yet present in a fresh Git Clone.
True Windows GUI startup, Chinese rendering, Clone-provided EXE, old-install
upgrade/rollback, self-update rollback, and real model inference remain
`PENDING_LOCAL_ACCEPTANCE`.
