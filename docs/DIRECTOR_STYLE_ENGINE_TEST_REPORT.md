# Shine Director Style Engine V1 — Test Report

Date: 2026-11-09
Original implementation base: `0ff8f8d8c521dfeac1957a98af524cdd7045aacf`
Synchronized base: `origin/main` at `16cb072aac5de2e0b3a210da391be2f027c61de2`
Branch: `feature/director-style-engine-v1-20261108`

## Results

| Check | Result | Evidence |
| --- | --- | --- |
| Director Style Engine tests | **PASS — 15 passed** | `backend/.venv/bin/python -m pytest -c backend/pyproject.toml backend/tests/test_director_styles.py -ra` |
| Full backend suite after sync | **NONZERO — 1,921 passed, 140 skipped, 14 failed** | `cd backend && .venv/bin/python -m pytest -c pyproject.toml -q -ra`; the 14 failing cases are listed and classified below. |
| Combined DSE, film crew, storyboard, H3 compiler, StyleVersion, Alembic, and migration regressions | **PASS — exit 0** | `backend/.venv/bin/python -m pytest -c backend/pyproject.toml backend/tests/test_director_styles.py backend/tests/test_film_crew.py backend/tests/test_storyboard.py backend/tests/test_h3_compiler.py backend/tests/test_style_versions.py backend/tests/test_alembic.py backend/tests/test_global_workflow_name_migration.py backend/tests/test_migrations_windows.py -q -ra` |
| Ruff | **PASS** | `backend/.venv/bin/ruff check backend/shine backend/tests` |
| Python bytecode compilation | **PASS** | `backend/.venv/bin/python -m compileall -q backend/shine` |
| Frontend Vitest after sync | **PASS — 30 files, 194 tests** | `cd frontend && npm run test -- --run` |
| Frontend production build after sync | **PASS** | `cd frontend && npm run build`; TypeScript and Vite completed. Vite reports the >500 kB chunk advisory; the generated JS bundle is 786.69 kB minified. |
| Isolated Alembic upgrade | **PASS** | Upgraded a temporary SQLite database to `f180b0060001`; no user data was used. |
| Diff whitespace check | **PASS** | `git diff --check` |

The DSE/API integration tests cover all six built-in profiles, seven-field preview, CAS-protected draft adoption, explicit freeze, profile-version immutability, equal-seven-dimensions source changes, legacy styles, frozen-history isolation, camera materialization, official six-section prompt compilation, EditIntentPlan and delivery metadata. The config-only extension test discovers and resolves a new profile from JSON alone. The frontend test drives catalog selection, confirms the Miyazaki and Oshii entries arrive from the dynamic catalog, previews the seven-row diff, adopts to draft, saves, and explicitly freezes.

The Fake/Mock integration path simulates project selection → template preview/adoption → style freeze → director/camera/continuity/editor artifacts → `apply-camera` → `H3PromptCompiler`. It is an offline contract test, not a real H3 or visual-quality result. Existing R09 missing-material and revoked-master regressions were included in the full backend run and did not appear in its failure list.

## Pre-sync full backend suite and environment limits (historical)

Command: `cd backend && .venv/bin/python -m pytest -c pyproject.toml -q -ra`.

The first full run exited nonzero with 18 failures. Three failures were stale assertions that still expected the previous Alembic head `f180b0030001`; those test expectations were updated to `f180b0040001`, and the Alembic, global-workflow migration, and downgrade/re-upgrade regression files passed in the combined targeted run.

The remaining full-suite failures are outside the DSE path and depend on Windows execution or host capacity. A follow-up `--lf` run reported **14 failed, 5 deselected**. The failures include install-plan tests whose pinned variant requires `windows-x64` while this runner reports `linux-x64`, code paths guarded by `SERVICE_VALIDATION_WINDOWS_ONLY` / `IMAGE_RENDERER_WINDOWS_ONLY`, local installation tests that require an enabled Windows executor, and one image-weight plan that exceeds the runner's available disk budget. These tests were not bypassed, weakened, or relabeled as passing. The full backend suite therefore cannot be reported as green on this Linux runner; run the complete backend suite on the supported Windows host before merge.

Several suite cases were skipped by their existing platform guards, including Windows bootstrap, Windows Job Objects, Windows entry points, and the opt-in real ComfyUI probe. A four-stage fixture case was skipped because its fixture has fewer than three scenes.

### 2026-11-09 template follow-up

After adding the Miyazaki and Oshii JSON profiles, reran the complete DSE test file (**15 passed**), full frontend Vitest (**29 files, 191 tests passed**), Ruff, and the frontend production build. Those counts are from before the latest-main synchronization.

### 2026-11-09 latest-main synchronization

After rebasing onto `16cb072aac5de2e0b3a210da391be2f027c61de2` and moving the DSE migration after main's `f180b0050001`, the DSE plus migration regression command exited 0. Ruff, `compileall`, `git diff --check`, `alembic heads`, and the feature-branch ancestry check passed. The frontend rerun passed **30 files / 194 tests**; `tsc -b && vite build` passed with the existing large-bundle advisory.

The full backend run collected 2,075 cases and exited 1: **1,921 passed, 140 skipped, 14 failed**. The DSE, migration, and H3 compiler regressions are not among the failures. Re-running pytest's last-failed selection reproduced the same 14 failures. They are host/environment-bound: four expect a `windows-x64` host while this runner is `linux-x64`; six service-validation cases are blocked by the intentional `SERVICE_VALIDATION_WINDOWS_ONLY` gate; two director setup cases require enabled local execution; the managed-renderer test hits the `IMAGE_RENDERER_WINDOWS_ONLY` gate; and the image-weight installation plan is blocked by insufficient host disk. No platform guard or test was changed to make this run pass. Windows-host validation remains **NOT_RUN**.

## Not run

- Real H3 generation or GPU inference: **NOT_RUN**.
- Human visual/audio aesthetic review: **NOT_RUN**.
- Real ComfyUI service validation or model installation: **NOT_RUN**.
- Delivery path: direct push to `main` is authorized; a PR is not part of this direct-main delivery.
