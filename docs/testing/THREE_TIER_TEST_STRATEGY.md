# Shine backend three-tier test strategy

## Purpose

The tiers make the default development check small while keeping every existing backend test in L3. The distinction is based on test behavior and dependencies, not on whether a test is recent or inconvenient.

Run these commands from the repository root:

```bash
# L1: Ruff, the complete fast unit set, and tests selected from the Git diff
uv run --project backend --locked python scripts/run_tests.py quick

# L2: isolated FastAPI, SQLite, Worker, and mocked-service integration tests
uv run --project backend --locked python scripts/run_tests.py integration

# L3: all backend tests on this operating system, Ruff, and a fresh Alembic upgrade
uv run --project backend --locked python scripts/run_tests.py full

# L3 profiling: all backend tests and the slowest 50 test phases
uv run --project backend --locked python scripts/run_tests.py profile
```

The runner writes a console log, JUnit XML report, Ruff log (where applicable), migration log (L3), and elapsed-time summary under `work/test-refactor/runs/`. The report lists selected and deselected tests, PASS/FAIL/SKIP, the slowest tests, failure categories, and why other-platform or real-device checks did not run. A layer with zero selected tests fails instead of reporting a false PASS.

## Test markers and collection guard

Every collected test belongs to exactly one tier marker:

| Marker | Layer | Purpose |
| --- | --- | --- |
| `unit` | L1 | Deterministic logic, model/schema rules, H3 prompt rules, LLM budgets/routing contracts, and a small API health/security contract set. |
| `integration` | L2 | Isolated FastAPI and SQLite behavior, lifecycle/state-machine flows, worker and ComfyUI mock integration, and cross-module business contracts. |
| `full` | L3 | Tests that need full engineering acceptance, such as real media encoding/decoding, a real service process, or Windows process ownership. |

Dimension markers add dependency information without replacing the tier:

| Marker | Meaning |
| --- | --- |
| `windows_only` | Requires Windows semantics. The marker is metadata; tests retain explicit `skipif` guards where Linux cannot exercise the behavior. |
| `media` | Calls the shared synthetic FFmpeg media encoder or otherwise verifies real FFmpeg/FFprobe behavior. |
| `external_service` | Requires a real ComfyUI/provider process or service outside an in-process mock. |
| `gpu_required` | Requires a physical GPU or its real device telemetry. |

The pytest collection hook automatically assigns an unmarked test to `full`, so new tests stay in L3 until someone explicitly assigns them to L1 or L2. Tests that call `make_test_video_bytes()` are marked `media` and `full` automatically; media takes precedence over an inherited module-level `integration` marker. An explicit function-level conflict fails collection. Each run prints counts by tier and dimension, including how many tests defaulted to L3. L3 runs the complete `backend/tests` collection without a marker filter, so an unmarked or newly added test cannot disappear from full acceptance.

## Layer selection

### L1 quick

L1 always runs Ruff and every `unit` test. It compares tracked, staged, and untracked changes with the merge base of `origin/main` (or `--changed-from <ref>`), then adds matching test modules. It uses module-name matching for normal source changes and runs the complete L1 unit set when a precise mapping is unavailable. Changes to shared database/service models, the app entry point, security boundaries, shared pytest collection/fixtures, or other cross-module contracts expand selection to integration tests. More than 24 candidate modules also expands to the complete integration tier instead of silently truncating the changed-file set. Tests classified as `full` or `media` stay in L3; the output states when a changed target is deferred to L3.

If Git history or the selected base ref is unavailable, L1 runs the complete unit set. `--no-diff` forces the same complete L1 set for profiling or verification. A frontend-only or documentation-only change still runs the functional unit set, so Ruff alone cannot produce an L1 PASS. The refactor diff widens to L2 because it changes `conftest.py`, `pyproject.toml`, and more than 24 test modules. The pre-refresh diff-aware run took 514.6 seconds; the no-diff L1 suite passed on the rebased branch in 19.18 seconds of pytest time. The post-rebase diff-aware run was interrupted at the user's request and is not counted as a result.

### L2 integration

L2 selects the modules or functions explicitly classified as `integration`; it does not rerun L1 unit tests. Existing tests without a deliberate L1/L2 classification stay in L3 and are reported in the collection counts. `--module <name>` can narrow the integration set to test-module names containing a module token, for example:

```bash
uv run --project backend python scripts/run_tests.py integration --module llm

# Select mapped integration modules from the Git diff; uncertainty widens to all L2.
uv run --project backend python scripts/run_tests.py integration --changed
```

L2 does not rerun unit tests; the PR gate requires L1 first. Full-tier media, Windows process ownership, browser E2E, and opted-in real-service tests remain visible in the collection report and run only in L3.

On the pre-refresh 2026-11-09 Linux measurement, 423 items belonged to L2. The runner selected 423, passed 415, skipped 8 Windows-only cases, and finished in 510.1 seconds. `test_storyboard.py` was the largest L2 module at 265.5 cumulative phase seconds. This met the 5–10 minute target on that host. The remote main refresh retained 423 L2 items, but L2 was not rerun after rebasing; see the acceptance report.

Current module ownership is explicit at the module or function level:

- L1 files: `test_acceptance_status`, `test_alignment_text`, `test_api_loopback_guard`, `test_creative_output_budget`, `test_film_reference_budget`, `test_h3_compiler`, `test_host_facts`, `test_llm_context_window`, `test_llm_gateway`, `test_llm_multi_model_routing`, `test_schema_identity`, `test_text_generation_options`, and `test_variant_manifest`. `test_worker_health` has one L1 status contract; `test_test_tiers` has one L1 marker contract and one L3 defaulting contract.
- L2 files: `test_agent_runtime`, `test_alembic`, `test_asset_center`, `test_comfy_adapter`, `test_comfy_completion_window`, `test_creative_script_task_lifecycle`, `test_director_long_form_regressions`, `test_director_styles`, `test_film_crew`, `test_image_managed_renderer`, `test_llm_setup_contract`, `test_material_director_flow`, `test_material_preparation`, `test_production_series`, `test_quality_gates`, `test_repair_engine`, `test_service_validation`, `test_story_preparation`, `test_story_section_run`, `test_storyboard`, `test_v13_migrations`, `test_v13_preparation`, `test_worker_health`, and `test_workflow`.
- Everything else stays in L3 until it is deliberately classified. A full run contains both explicit L3 and defaulted L3 tests.

The current tests mostly use a fresh `tmp_path` SQLite database, monkeypatched environment, `httpx.MockTransport`, or a local ComfyUI fake. The suite also contains process ownership, file-system and socket-boundary tests. Although much database state is isolated, the whole suite has not been proven safe under process-level parallelism. `pytest-xdist` is therefore not enabled; tests remain serial until fixed ports, environment mutation, temporary data and child-process ownership have explicit worker isolation and a before/after parallel run proves equivalent behavior.

### L3 full

The local `full` command executes all backend tests on the current OS, Ruff, and a migration to `head` against a fresh database under the run report directory. It does not claim to validate another OS, a GPU, a loaded model, or browser E2E. The authoritative engineering L3 is the CI workflow's Linux and Windows full jobs plus the Linux browser E2E job.

The collection guard after rebasing onto `origin/main` at `fe7d0d3` reported 2,155 items in 174 modules: 162 unit, 423 integration, and 1,570 full. Of the full items, 1,546 default to L3 because their layer has not yet been explicitly classified; they remain in every full run and are reported rather than dropped. There are 36 media, 147 Windows-only, one external-service, and one GPU-required item across those tiers. The latest main added 48 collected items relative to the original `185c6a6` baseline; the refactor adds two collection-guard tests.

Windows service, installation, drive qualification, PowerShell, Job Object, process and stack lifecycle behavior must run in a Windows job. Linux skips of those tests are counted as skips, not as Windows evidence. Real ComfyUI startup is opt-in with `SHINE_REAL_COMFY_PROBE=1` and its documented local paths; synthetic fixtures never count as GPU/model acceptance. Real H3/GPU production acceptance remains a separate local-device activity with explicit evidence.

## GitHub Actions policy

The workflow separates cost by event:

- L1 runs for pushes and PR updates.
- L2 runs for PR updates and manual integration runs, after L1.
- L3 Linux, L3 Windows, migration, Windows stack smoke, and browser E2E run on `main` pushes and manual `full` dispatches.
- `workflow_dispatch` exposes `quick`, `integration`, and `full` on a selected branch. For an important PR, dispatch `full` on the PR branch before merging; any Windows-specific change is important.
- `uv.lock` and `frontend/package-lock.json` are cached. Concurrency cancels superseded work on a branch and coalesces the equivalent push/PR event for one branch.

Recommended repository branch-protection checks for regular PRs are `L1 backend quick`, `L2 backend integration`, and `Frontend build`. GitHub branch protection is repository settings outside this checkout, so the workflow does not claim those checks are already configured. For important PRs, review the successful `L3 backend full (Linux)`, `L3 backend full (Windows)`, and `L3 browser E2E` run on the PR branch before merge. GPU/model acceptance still needs local hardware evidence.
