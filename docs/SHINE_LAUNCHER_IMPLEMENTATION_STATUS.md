# Shine Launcher V1.0 implementation status

This report reflects the isolated implementation branch
`feat/shine-launcher-v1-final-closeout-20261114` after merging the latest
`origin/main`.

Source completion alone does not satisfy stages that require real Windows
acceptance. See
[the 2026-11-14 engineering acceptance report](SHINE_LAUNCHER_ENGINEERING_ACCEPTANCE_REPORT_20261114.md)
for exact evidence, distribution identity and remaining gates.

| Stage | Status | Evidence / remaining gate |
| --- | --- | --- |
| L0 native executable | `SOURCE_IMPLEMENTED / WINDOWS_CI_PASS / GUI_PENDING` | Rust + Slint CPU software-rendered GUI source, Chinese six-section interface, an independent read-only host probe, and two headless modes (`--smoke-check`, `--self-test`). The Windows build, unit tests, Clippy, Release EXE, artifact identity and ZIP verification passed on the pull request. Windows 10/11 GUI startup, Chinese text rendering, DPI and path behaviour remain **unverified**. |
| L1 stable installation identity | `IMPLEMENTED / LINUX_TESTED` | The installation id is now a random persisted token stored in `<install_root>/.shine/installation.json`, so moving the directory, changing disks or re-cloning no longer changes it. Launcher 0.1.x registries are migrated on load and their retired path-derived ids stay resolvable as aliases. Concurrent registrations no longer drop an installation. Windows read-only behaviour and the full ComfyUI/model inventory remain outstanding. |
| L2 deployment protocol and channels | `IMPLEMENTED / TRUST_ANCHOR_LIVE / RELEASE_PENDING` | V1 schema, parser, compatibility checks, JCS source identity and scoped Ed25519 verification are implemented. Two **production** publisher public keys are committed, one scoped to studio releases and one to launcher releases, so neither can sign for the other. A signed manifest is committed as a regression fixture proving the anchor really verifies and that tampering stops it. The signing tool and the release workflow are in place. Automatic installation stays closed until the private keys are provisioned into GitHub Secrets. |
| L3 install, upgrade and recovery | `IMPLEMENTED / LINUX_TESTED / WINDOWS_PENDING` | A controlled runner (fixed script allow-list, argument arrays, no shell), a versioned JSON bridge that reuses the backend's authoritative process identity, a durable append-only journal with a cross-process transaction lock, an immutable upgrade plan whose hash freezes at consent, a consistent SQLite snapshot, resumable downloads, safe archive extraction and a rollback rule that refuses to overwrite data the new version has already accepted are all implemented and tested. Real-machine installation, legacy data-preserving upgrade and recovery are still unverified. |
| L4 capability packs and distribution | `PARTIAL` | Capability packs are assembled from `config/deployment` catalogs and variants and reported in layers (files verified, environment compatible, managed runtime ready, real generation verified). Candidate and deferred capabilities are never downloaded automatically and never counted as installed. Model inference remains unverified, so no capability is reported as READY. The repository-root executable is still the 0.1.0 prototype binary and must be rebuilt on Windows. |
| L5 final regression and acceptance | `PARTIAL / PENDING_LOCAL_ACCEPTANCE` | Rust unit tests, Clippy, fmt, the Linux release build, both headless modes, the backend shards, the frontend build and the Windows CI all pass on this branch. Still required: Windows 10/11 GUI acceptance, clean install, legacy data-preserving upgrade and rollback, real GPU/model inference, and the formal release artifacts. |

## Verification summary

- Rust: `cargo fmt --all -- --check`, `cargo clippy --locked --all-targets -- -D warnings`
  (no warnings) and `cargo test --locked --lib` (107 passed, 0 failed).
- Headless: the release binary passes `--self-test` and `--verify-manifest`
  against the committed production trust anchor.
- Windows: the pull request's `shine-launcher-windows` workflow passed
  (formatting, source check, unit tests, Clippy, Release EXE, artifact identity,
  ZIP packaging and extraction).
- Backend: the eight-shard regression described in `AGENTS.md` ran with
  `CODEBUDDY_SAFE_DELETE_ENABLED=0` over 219 test files / 2627 selected cases.
  Six shards exited 0; two shards each carried one failure, and both are
  concurrent-timing-sensitive cases (a child-process startup handshake that
  times out under 8-way parallelism). Running those two modules on their own
  passes 29/29, so the branch introduces no backend regression. `ruff check
  backend` is clean and an isolated `alembic upgrade head` + `alembic check`
  reaches head `f180d0010001`.
- Frontend: `npm run build` passes. The Vitest suite has 13 failing tests in
  `styleFreeze.test.tsx`, `shotDraft.test.tsx` and `simpleAssets.test.tsx`.
  **These failures are pre-existing on `main`** — this branch contains no
  frontend change — and belong to the director and asset-center workstreams.
  The same area also fails in the `L3 browser E2E` CI job, before and after
  this branch was merged.

## Known blockers

1. The production signing **private** keys are deliberately not in the
   repository. They exist only outside it and must be placed in the GitHub
   secrets `SHINE_STUDIO_RELEASE_SIGNING_KEY` and
   `SHINE_LAUNCHER_RELEASE_SIGNING_KEY`. Until then, automatic updates fail
   closed by design.
2. The launcher CI workflows are stored as installable sources under
   `launcher/packaging/ci/`; `install_workflows.ps1` copies them into
   `.github/workflows/`. An automation identity cannot write that directory.
3. The repository-root `ShineLauncher.exe` must be rebuilt on Windows from the
   current source; the committed binary still corresponds to the 0.1.0
   prototype.

Do not mark any of these as READY before the corresponding evidence exists.
