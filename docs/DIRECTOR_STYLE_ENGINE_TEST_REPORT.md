# Shine Director Style Engine V1 — Test Report

Date: 2026-11-08
Base: `0ff8f8d8c521dfeac1957a98af524cdd7045aacf`
Branch: `feature/director-style-engine-v1-20261108`

## Results

| Check | Result | Evidence |
| --- | --- | --- |
| Director Style Engine tests | **PASS — 15 passed** | `backend/.venv/bin/python -m pytest -c backend/pyproject.toml backend/tests/test_director_styles.py -ra` |
| Combined DSE, film crew, storyboard, H3 compiler, StyleVersion, Alembic, and migration regressions | **PASS — exit 0** | `backend/.venv/bin/python -m pytest -c backend/pyproject.toml backend/tests/test_director_styles.py backend/tests/test_film_crew.py backend/tests/test_storyboard.py backend/tests/test_h3_compiler.py backend/tests/test_style_versions.py backend/tests/test_alembic.py backend/tests/test_global_workflow_name_migration.py backend/tests/test_migrations_windows.py -q -ra` |
| Ruff | **PASS** | `backend/.venv/bin/ruff check backend/shine backend/tests` |
| Python bytecode compilation | **PASS** | `backend/.venv/bin/python -m compileall -q backend/shine` |
| Frontend Vitest | **PASS — 29 files, 191 tests** | `cd frontend && npm run test -- --run` |
| Frontend production build | **PASS** | `cd frontend && npm run build`; TypeScript and Vite completed. Vite reports the existing >500 kB chunk advisory; the generated JS bundle is about 766 kB minified. |
| Isolated Alembic upgrade | **PASS** | Upgraded a temporary SQLite database to `f180b0060001`; no user data was used. |
| Diff whitespace check | **PASS** | `git diff --check` |

The DSE/API integration tests cover the server catalog, seven-field preview, CAS-protected draft adoption, explicit freeze, profile-version immutability, equal-seven-dimensions source changes, legacy styles, frozen-history isolation, camera materialization, official six-section prompt compilation, EditIntentPlan and delivery metadata. The fifth-template test discovers and resolves a new profile from JSON alone. The frontend test drives catalog selection, preview, seven-row diff, draft adoption, save, and explicit freeze.

The Fake/Mock integration path simulates project selection → template preview/adoption → style freeze → director/camera/continuity/editor artifacts → `apply-camera` → `H3PromptCompiler`. It is an offline contract test, not a real H3 or visual-quality result. Existing R09 missing-material and revoked-master regressions were included in the full backend run and did not appear in its failure list.

## Full backend suite and environment limits

Command: `cd backend && .venv/bin/python -m pytest -c pyproject.toml -q -ra`.

The first full run exited nonzero with 18 failures. Three failures were stale assertions that still expected the previous Alembic head `f180b0030001`; those test expectations were updated to `f180b0040001`, and the Alembic, global-workflow migration, and downgrade/re-upgrade regression files passed in the combined targeted run.

The remaining full-suite failures are outside the DSE path and depend on Windows execution or host capacity. A follow-up `--lf` run reported **14 failed, 5 deselected**. The failures include install-plan tests whose pinned variant requires `windows-x64` while this runner reports `linux-x64`, code paths guarded by `SERVICE_VALIDATION_WINDOWS_ONLY` / `IMAGE_RENDERER_WINDOWS_ONLY`, local installation tests that require an enabled Windows executor, and one image-weight plan that exceeds the runner's available disk budget. These tests were not bypassed, weakened, or relabeled as passing. The full backend suite therefore cannot be reported as green on this Linux runner; run the complete backend suite on the supported Windows host before merge.

Several suite cases were skipped by their existing platform guards, including Windows bootstrap, Windows Job Objects, Windows entry points, and the opt-in real ComfyUI probe. A four-stage fixture case was skipped because its fixture has fewer than three scenes.

## Not run

- Real H3 generation or GPU inference: **NOT_RUN**.
- Human visual/audio aesthetic review: **NOT_RUN**.
- Real ComfyUI service validation or model installation: **NOT_RUN**.
- Push, PR creation, or remote review: **NOT_PERFORMED** by explicit user direction.
