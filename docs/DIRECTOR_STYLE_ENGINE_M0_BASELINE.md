# Director Style Engine V1 — M0 Baseline

Date: 2026-11-08
Base: `origin/main` at `0ff8f8d8c521dfeac1957a98af524cdd7045aacf`
Working branch: `feature/director-style-engine-v1-20261108`

## Current contracts confirmed

- The director journey remains the seven stages `script → plan → storyboard → assets → package → production → post`, derived in `backend/shine/director/journey.py`. This feature adds no stage.
- R09 remains authoritative at `apply_camera`: missing or unapproved assets generate a notice and are not bound; approved and locked references stay attached. The H3 compiler still rejects a missing required character reference where the selected workflow needs one.
- `H3PromptCompiler` in `backend/shine/h3/compiler.py` is the sole official prompt compiler. It pins `h3-prompt-writing@d21241f0a4b3`; Ref2VA keeps the six-section order `subject_definitions → summary → retention_analysis → detailed_description → overall_soundscape → non_diegetic_music`.
- `StyleBible` remains exactly seven fields. `StyleVersion` snapshots those fields and their existing SHA. The new feature must keep profile provenance in nullable, separate metadata so old versions remain valid and the seven-field digest does not change.
- `FilmAgentDispatcher` hashes the complete role context before creating its durable task record. A frozen profile snapshot can join that context without introducing another task lane or replay path.
- `DirectorPackage` pins the current frozen style version and hashes its manifest. Its style reference is the right place to include the frozen profile and resolution identity.
- `frontend/src/components/StylePanel.tsx` is the existing “作品风格” surface. `styleCatalog.ts` contains six existing general presets which remain supported; director choices must come from the service catalog.

## Actual extension points

| Concern | Existing owner | Planned extension |
| --- | --- | --- |
| Seven-dimension draft and freeze | `Project.style_draft`, `StyleVersion`, `services.freeze_style`, `main.py` style routes | Add nullable draft provenance and immutable frozen provenance; keep the existing save, approval, freeze, impact-analysis transaction. |
| Template contract/catalog | None | `backend/shine/director_styles/` plus `config/director_styles/builtin/*.json`; strict protocol validation and file discovery. |
| Preview/apply APIs | None | Read-only preview and CAS-protected draft adoption routes under `/api/v1/projects/{id}/director-style`. |
| Agent context | `FilmAgentDispatcher.run` | Include the frozen binding and typed resolved style in director, cinematographer, and editor context hashes. |
| Camera materialization | `api_film.apply_camera` | Add validated English style constraints to the existing `ShotSpec.camera`; do not alter story facts, timing, characters, assets, or continuity. |
| H3 compilation | `H3PromptCompiler` | No second compiler. Existing compiler serializes safe `camera` fields into the official prompt structure. |
| Edit intent / EDL | `EditIntentPlan`; `media/delivery_engine.py` | Carry an immutable editing direction into the pre-production edit intent and EDL audit metadata; preserve CUT-only transitions and full passed takes. |
| Director package | `director/package.py` | Include frozen profile and resolution hashes in the package style manifest/readiness digest. |
| Web UI | Existing `StylePanel` | Dynamically load profiles, preview a seven-dimension diff, adopt to draft, and reuse existing user freeze controls. |

## Baseline evidence

- Requested baseline commit was fetched from GitHub and verified as the current `origin/main` before branching. The feature branch starts at that commit.
- Alembic head: `f180b0030001`.
- Backend targeted regression command (StyleVersion, approval concurrency, film crew, storyboard, H3 compiler, long-form director, four-stage reacceptance, director entries/post): exit 0; no failures; one parameterized case skipped because the fixture requires three scenes.
- Frontend baseline: `npm run build` passed (Vite reports the existing >500 kB chunk advisory); `npm run test -- --run` passed 190 tests across 28 files.
- Dependency setup was isolated to `backend/.venv` and `frontend/node_modules`. `backend/uv.lock` was restored byte-for-byte after using PyPI as a local mirror workaround; no dependency lock change is part of this work.

## Risks and guardrails

| Risk | Guardrail |
| --- | --- |
| Template text could act like prompt injection or leak Chinese prose into H3 | Strict DSE/1 schema, bounded English-only technique instructions, reject URLs/paths/role overrides/placeholders, and only compile validated technique text through existing camera fields. |
| Editing style could mutate locked dialogue or timing | Style rules can guide rhythm; server-side edit intent keeps shot coverage/order and existing timing. Final EDL remains CUT-only and references passed media only. |
| Template file changes could rewrite frozen projects | Freeze full profile and resolution snapshots with the existing StyleVersion in one transaction; old versions have null provenance and remain legacy/custom. |
| Missing assets could become a style-specific blocker | Preserve current R09 notice/skip behavior and approved+locked binding checks unchanged. |
| Added style could bypass H3 or trigger work | Do not modify H3 workflow or rule pack; preview, adoption, and freeze are local data operations and never queue generation or retry UNKNOWN work. |

## Rebase review against latest main (2026-11-09)

- Fetched and reviewed `origin/main` at `16cb072aac5de2e0b3a210da391be2f027c61de2`, which had advanced from the requested original base. The seven-stage journey, R09 material policy, StyleBible freeze contract, and `H3PromptCompiler` as the only official prompt entry point remain in force.
- Preserved the new main changes for script basis, job-request fingerprints, and associated APIs/UI. The DSE migration now follows main's `f180b0050001` migration as `f180b0060001`; the migration graph has one head.
- The feature branch was rebased as two commits on the fetched main SHA. The rebase has no unresolved conflict markers, `git diff --check` passes, and `origin/main` is an ancestor of the feature `HEAD`.
- Post-rebase test results are recorded in [DIRECTOR_STYLE_ENGINE_TEST_REPORT.md](DIRECTOR_STYLE_ENGINE_TEST_REPORT.md) after the validation run.
