# Three-tier test refactor acceptance

## Revalidation on the latest remote main

The feature branch was rebased onto `origin/main` at `fe7d0d3`. In response to the user's instruction to skip L2 and L3, validation on this updated base is limited to collection, L1, Ruff, and frontend checks. No remote CI run was requested.

| Metric | Original baseline at `185c6a6` | Latest rebased branch |
| --- | --- | --- |
| L1 test time | No independent entry point | 162 passed, 0 skipped/failed; pytest 19.18 s; Ruff passed. |
| L2 test time | No independent entry point | Not rerun after rebase, per user instruction. The earlier 510.1 s result was measured before the remote refresh. |
| Linux L3 time | 2,748 s (45:48) pre-change baseline | Not rerun after rebase, per user instruction. The earlier 2,674.1 s result was measured before the remote refresh. |
| Windows L3 time | Historical reference about 62 min | Not run. |
| Test coverage count | 2,105 collected | 2,155 collected after rebase: 162 unit, 423 integration, 1,570 full. |
| Test failures | 14 in the original measured baseline | L1: 0. Current L2/L3 failure totals are unknown because those layers were not run. |
| CI single-run resource use | Not measured | Not measured; no Actions run was started. The earlier local estimate predates the latest main refresh. |

## Latest-branch evidence

| Check | Selection / collection | Result | Elapsed |
| --- | ---: | --- | ---: |
| Final full collection guard | 2,155 / 2,155 | 162 unit, 423 integration, 1,570 full; 1,546 default to L3; 174 modules | Collection only |
| L1 no-diff (`20261109T131619Z-quick`) | 162 / 2,155 | 162 passed, 0 skipped, 0 failed; Ruff passed; 1,993 deselected by tier | pytest 19.18 s |
| Diff-aware quick | 585 selected by policy | Interrupted at the user's request while executing; no PASS claimed | Not a completed result |
| Frontend Vitest | 32 files | 206 passed | 64.40 s |
| Frontend production build | 1 build | Passed; 1,629 modules transformed | Vite 2.39 s |
| Ruff after rebase | Backend, Alembic, tests, and Python runner/E2E scripts | Passed | Under 1 s |
| CI YAML parse | Workflow structure and 6 jobs | Passed locally | Under 1 s |

The diff-aware quick run selects the complete integration tier when `pyproject.toml` or shared `conftest.py` changes. It was stopped around 73% at the user's direction to skip L2/L3. The separate no-diff L1 run then completed successfully. The local browser E2E was not rerun after the remote refresh because it is part of L3; a pre-refresh local run had passed, but is not evidence for the rebased branch.

## Earlier pre-refresh measurements (not latest-main results)

These measurements remain useful for the initial refactor baseline, but were taken before rebasing onto `fe7d0d3`:

- At `185c6a6`, the original suite collected 2,105 items and had 1,951 passed, 14 failed, and 140 skipped in 2,748 seconds.
- On the pre-refresh refactor branch, clean L1 passed 162 tests in 22.8 seconds; L2 passed 415, skipped 8 Windows-only cases, and finished in 510.1 seconds.
- The pre-refresh Linux L3 run plus targeted marker correction covered all 2,107 then-collected items: 1,958 passed, 149 skipped, 0 failed. Full L3 took 2,674.1 seconds including Ruff and a fresh Alembic upgrade. The full run was not repeated after that marker-only correction; the affected module was rerun.
- A pre-refresh local browser E2E returned `MOCK_VERIFIED` with no API errors. It contains no real model execution.

The latest remote base adds 48 test items over the original `185c6a6` baseline; this refactor adds two collection-guard tests. The latest branch therefore collects 2,155. The original slowest-test tables in `TEST_BASELINE_AND_BOTTLENECKS.md` and the pre-refresh L3 figures are explicitly scoped to the earlier base.

## Test ownership and safety

- Every collected test is assigned exactly one tier. Unmarked tests default visibly to L3; L3 selects the full collection. Latest counts: 162 unit, 423 integration, 1,570 full; 1,546 default-to-L3; dimensions are 147 `windows_only`, 36 `media`, one `external_service`, and one `gpu_required`.
- No assertions were deleted or weakened. The test-host and disk fixture corrections preserve production OS gates. Windows operations remain explicitly marked and do not count as Windows evidence when skipped on Linux.
- `pytest-xdist` remains disabled because fixed ports, environment mutation, child-process ownership, and shared resources have not been proven worker-isolated.
- Full L3 after this rebase remains unverified. In particular, current Windows lifecycle, current browser journey, and current full backend failure totals are unknown. Do not read the pre-refresh full-suite pass as a result for `fe7d0d3`.

## CI resource and merge boundary

No GitHub Actions run or account billing measurement is available. The pre-refresh local-phase estimate of 17.16 runner-minutes for diff-aware L1 + L2 + frontend is not reaffirmed for the refreshed base. CI workflow changes are present and locally parsed, but no remote CI was considered, per user instruction.

The GitHub connector confirmed the authenticated identity is repository owner `anjingdtl` with admin/write permission, so the main-branch owner-only push rule permits the requested update. The remaining test boundary is explicit: L2/L3 and browser E2E were skipped on the latest base at the user's request.
