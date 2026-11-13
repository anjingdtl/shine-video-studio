# Shine Launcher V1.0 implementation status

This report reflects the isolated implementation branch after merging the
latest checked `origin/main` commit `1a55c41509924b898a587901b0aa5aa5ac71cc70`.
Source completion alone does not satisfy stages that require real Windows
acceptance.

See [the engineering acceptance report](SHINE_LAUNCHER_ENGINEERING_ACCEPTANCE_REPORT_20261113.md)
for exact evidence, distribution identity, and remaining gates.

| Stage | Status | Evidence / remaining gate |
| --- | --- | --- |
| L0 native executable prototype | `SOURCE_IMPLEMENTED / PENDING_GUI_ACCEPTANCE` | Rust + Slint CPU software-rendered GUI source, Chinese UI, and independent read-only host probe. Windows Server 2025 CI compiled the executable, passed 22 Windows unit tests and Clippy, and verified smoke-check, binary identity, hash, and ZIP contents. Windows 10/11 GUI startup, Chinese text, DPI, and path behavior remain unverified. |
| L1 registry and legacy discovery | `SOURCE_IMPLEMENTED / PARTIAL` | Read-only selected-path scan, protected data-path evidence, atomic per-user installation registry, and host checks are implemented. New-install flow, full ComfyUI/model inventory, multi-instance management, and Windows no-write acceptance remain outstanding. |
| L2 deployment protocol and channels | `PARTIAL / FAIL_CLOSED` | V1 schema/parser/compatibility checks, JCS source identity, scoped Ed25519 verification, GitHub release metadata scanning/cache, and immutable development-ref resolution are implemented. The trusted publisher store is empty; no asset downloader or Studio/Launcher installer/self-updater exists, so remote installation remains disabled. |
| L3 install/upgrade/recovery | `NOT STARTED` | Transaction journal, backup/rollback, dependency repair, process identity control, launch/stop, diagnostics, and recovery are not implemented. |
| L4 capability packs and distribution | `PARTIAL` | Windows x64 ZIP build, extraction and identity checks passed in workflow run [43420629491](https://github.com/anjingdtl/shine-video-studio/actions/runs/43420629491). Push-only publisher run [43422636863](https://github.com/anjingdtl/shine-video-studio/actions/runs/43422636863) placed a hash-verified Windows EXE, sidecar, and build identity in the implementation branch root. Model/node/runtime capability packs and independent Launcher releases remain unimplemented. |
| L5 final regression and acceptance | `PARTIAL / PENDING_LOCAL_ACCEPTANCE` | Current-main backend, frontend, static, migration, and launcher CI checks are recorded below. Still requires Windows 10/11 GUI, clean install, legacy data-preserving upgrade/rollback, real model/GPU inference, and complete L2/L3/L4 behavior. |

## Verification

- Backend regression: all 212 test files were run in eight file-count-balanced
  shards, 2,517 collected tests total; all eight final shard exit codes are 0.
  `test_startup_guard.py` had used Windows `where` during module collection
  despite being marked Windows-only. It now selects PowerShell with
  `shutil.which`; the affected shard was rerun and passed. Windows process and
  port behavior remains untested on Linux. Logs and isolated test state are in
  the ignored `work/final-backend-shards/` directory.
- Frontend: `npx vitest run --no-file-parallelism` passes 47 files / 278 tests.
  `npm run build` passes TypeScript and Vite production build, with the existing
  large-chunk advisory. The Vitest output includes existing React `act(...)`
  warnings.
- Python static check: `CODEBUDDY_SAFE_DELETE_ENABLED=0 work/backend-venv/bin/python -m ruff check backend` passes.
- Migrations: Alembic upgraded an isolated SQLite database to
  `f180c0030001` and `alembic check` reported no new operations. The database
  is under `work/final-migration/`; no user/project database was used.
- Launcher: prior isolated Linux run passed fmt, check, 24 Rust unit tests,
  Clippy, and Windows MSVC target checks. GitHub Actions Windows run
  [43420629491](https://github.com/anjingdtl/shine-video-studio/actions/runs/43420629491)
  passed 22 Windows Rust tests, Clippy, Release EXE compilation, host-only
  smoke-check, EXE identity/hash, ZIP creation, and extraction verification.
  The local shell currently has no Cargo toolchain, so those Linux checks were
  not rerun after the main merge; launcher source is unchanged by that merge.

At this report revision, the published root executable is 3,778,048 bytes with
SHA-256 `be07c704605d79646e4fd266649f1979b846b50d6fe6a2a0ddbace6bcb252607`.
Its build identity records source commit
`f41e60940968eea6555041db3204b760986f7051` and
`windows_gui_acceptance=PENDING_LOCAL_ACCEPTANCE`. The next branch push is
expected to make the publisher rebuild and replace that binary with metadata
for the latest implementation commit; the source hash and metadata must be
rechecked after that workflow completes.

GitHub Windows CI does not open the GUI. The Launcher currently does not
install, overwrite, migrate, or stop Studio, ComfyUI, user data, models, secrets,
or real processes. The trusted publisher store is empty and no unverified model
capability is marked READY. Keep PR #13 in Draft until the Windows GUI, legacy
upgrade/rollback, and remaining P0 acceptance evidence is available.
