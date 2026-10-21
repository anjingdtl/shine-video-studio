# Offline Final Acceptance Report — M1/M2/M3

**Project:** Shine 视频工作台 (Shine Video Studio)
**Scope:** non-GPU final hardening of the M1/M2/M3 offline capabilities
**Result:** the software production loop is complete and reproducible offline; real MiniMax-H3 acceptance remains `PENDING_LOCAL_H3`.

> **No real MiniMax-H3 inference was executed in this round.** Every video-shaped
> result in this report comes from a Fake ComfyUI server or a synthetic FFmpeg
> test pattern. A `PASSED` job in this report means "the scheduling, gate and
> recovery logic closed the loop", never "the model produced an acceptable
> film".

---

## 1. Git baseline

| Item | Value |
| --- | --- |
| Baseline branch | `main` |
| Baseline commit (start of round) | `f22a1b0b45ba6e2cc0ac73430a913defa466715d` (docs) |
| Previous implementation commit | `9d0a09f327a21d4995e68a97d9e50f0bf85e1022` |
| Development branch | `feat/m123-final-hardening` |
| Implementation commit SHA | `6b49a0c7fd3190367ccca8ddd5fab9b6cb1217d6` |
| Working tree at start | clean, `main` up to date with `origin/main` |

The implementation commit is `6b49a0c`. One documentation-only commit follows it
on the same branch; it only records this SHA in the report.

No destructive git operation was used. No `git reset --hard`, no force push, and
no user data directory was reset or deleted.

## 2. The five core remediations

| # | Issue | Result | Evidence |
| --- | --- | --- | --- |
| P1-01 | ComfyUI reference media transport silently dropped references, uploaded `.bin` names, and shared one upload path for image/video/audio | **IMPLEMENTED / TESTED_OFFLINE** | `backend/shine/comfy/media.py`, `backend/shine/comfy/provider.py`, `backend/tests/test_reference_transfer.py` (13 tests) |
| P1-02 | An uncertain `/prompt` submission could be re-rendered, duplicating a real generation | **IMPLEMENTED / TESTED_OFFLINE** | `backend/shine/comfy/client.py`, `backend/shine/worker.py`, `backend/tests/test_submission_reconciliation.py` (6 tests) |
| P1-03 | Technical QC alone could mark a shot final-passed without any semantic or audio review | **IMPLEMENTED / TESTED_OFFLINE** | `backend/shine/quality/gates.py`, `backend/tests/test_quality_gates.py` (17 tests) |
| P1-04 | "Automatic repair" re-queued the original prompt instead of a changed request | **IMPLEMENTED / TESTED_OFFLINE** | `backend/shine/quality/revisions.py`, `backend/tests/test_repair_revisions.py` (10 tests) |
| P2 | A ComfyUI task that already finished required a human to download and re-check it | **IMPLEMENTED / TESTED_OFFLINE** | `backend/shine/worker.py`, `backend/tests/test_comfy_recovery.py` (7 tests) |

### P1-01 — reference media transport

* Every manifest entry now has a deterministic logical slot
  (`reference_subject_1`, `reference_picture_1`, `reference_video_1`,
  `reference_audio_1`, …). A slot without a `parameter_map` entry is a hard
  error (`PENDING_WORKFLOW_MAPPING`) — references are never silently dropped.
* The upload extension is derived from the asset **content type**, not from the
  content-addressed storage key (which has no extension and previously produced
  `.bin` names).
* Image, video and audio each use an explicit, configurable
  `MediaTransfer` adapter (`upload_path`, `field_name`, `subfolder`,
  `extension`, `required_node_classes`, `multipart|binary`). Image has
  ComfyUI's documented default; **video and audio have no default** and must be
  declared by the workflow profile, otherwise the render is blocked with
  `PENDING_WORKFLOW_MAPPING`.
* Declared loader nodes are checked against the workflow graph and against the
  live `/object_info` inventory.
* Before every upload the master asset is re-verified: approval, lock, project
  scope, manifest hash, on-disk SHA-256 and media-type/role compatibility.
* Slot count, file origin, media type and workflow mapping are cross-checked
  before the prompt is submitted.

### P1-02 — uncertain submissions

Submission outcomes are now classified explicitly:

| Outcome | Code | Behaviour |
| --- | --- | --- |
| Connection never established | `COMFY_UNAVAILABLE` | safe failure, nothing was queued |
| Request sent, answer lost | `COMFY_SUBMISSION_OUTCOME_UNKNOWN` | `NEEDS_REVIEW` + reconciliation record, **never** auto-resubmitted |
| 5xx from `/prompt` | `COMFY_SUBMISSION_OUTCOME_UNKNOWN` | same as above |
| 4xx from `/prompt` | `COMFY_PROMPT_REJECTED` | definitely not queued, safe to repair |
| Node validation errors | `COMFY_WORKFLOW_INVALID` | definitely not queued |
| Prompt id received | — | persisted immediately |

The submit intent (including the **prompt graph hash**) is persisted *before*
the external call, so a lost acknowledgement can be matched against
`/history` by graph hash. Recovery only auto-advances when the original task
is found; otherwise the job stays `NEEDS_REVIEW`.

### P1-03 — quality gates

Five named gates are evaluated and persisted per attempt: `technical`,
`semantic`, `audio`, `style`, `final`. Each gate records its status, whether it
was required, the reviewer model, the confidence, the coverage, the error code
and the evidence.

* A shot can only be final-passed when every **required** gate passed.
* A missing semantic review, a missing ASR run, a mock reviewer that the
  project does not accept, or an unavailable technical toolchain are all
  recorded explicitly — never as a pass.
* The project quality policy is configurable per project
  (`PUT /api/v1/projects/{id}/quality-policy`) and may relax **non-critical**
  thresholds per shot priority (A/B/C). Identity, master-asset and key-dialogue
  rules are hard rules and are never relaxed.
* The final verdict is labelled `real`, `mock_verified` or `partial`.

### P1-04 — repair that actually repairs

`QC failure → classification → authorised change → new request revision →
diff verification → new attempt → re-QC`.

* Prompt repairs recompile through the pinned `H3PromptCompiler` after an
  authorised camera/sound directive. Locked story facts, master assets and the
  frozen Style Bible are never modified.
* Reference repairs rebuild the request-local manifest from the shot's saved
  bindings.
* Workflow repairs change the generation parameters (seed) after the profile is
  re-validated.
* A revision that would not change anything is refused and the job escalates to
  a human.
* Each new attempt stores its own request snapshot, prompt hash, parameter
  hash, manifest hash, parent attempt and repair reason. Previous attempts are
  never overwritten.
* The retry budget stays at two rounds, and a repeated root cause escalates
  instead of rerolling forever.
* Repairing an upstream job invalidates the dependent jobs so their validity is
  recomputed.

## 3. Changed files

**New modules**

| File | Purpose |
| --- | --- |
| `backend/shine/comfy/media.py` | Per-family ComfyUI media transfer adapters and capability checks |
| `backend/shine/quality/gates.py` | Named quality gates and the per-project quality policy |
| `backend/shine/quality/revisions.py` | Authorised request revisions for automatic repair |
| `backend/shine/media/frames.py` | Derived evidence frames for remote review |

**Modified modules**

`backend/shine/worker.py` (submit intent, gates, repair revisions, recovery),
`backend/shine/comfy/client.py` (upload transports, error classification),
`backend/shine/comfy/provider.py` (strict reference transfer, reconciliation),
`backend/shine/models.py`, `backend/shine/schemas.py`, `backend/shine/main.py`,
`backend/shine/api_comfy.py`, `backend/shine/services.py`,
`backend/shine/security.py`, `backend/shine/config.py`,
`backend/shine/h3/compiler.py`, `backend/shine/quality/repair.py`,
`backend/shine/quality/technical.py`, `backend/shine/media/probe.py`.

**Tests and infrastructure**

`backend/tests/conftest.py`, `backend/tests/fake_http.py`,
`backend/tests/test_alembic.py`, eight new test modules,
`backend/alembic/versions/c3d81f6a2b45_m123_final_hardening.py`,
`backend/pyproject.toml`, `backend/uv.lock`, `.github/workflows/ci.yml`,
`scripts/verify_local_stack.ps1`.

**Pre-existing defects found and fixed while reviewing**

* `PUT /api/v1/shots/{id}/bindings` was annotated as returning a dict while it
  returns a list; FastAPI rejected every call with a 500 response. Fixed.
* The technical QC reported "FFprobe opened and decoded stream metadata" as a
  *decode* result. Probing metadata is not decoding frames. The check is now
  split into `stream_metadata` (container/stream inspection) and `decode` (a
  real end-to-end FFmpeg decode), and FFmpeg is used as the metadata source
  when FFprobe is not installed.

## 4. New database migration

`c3d81f6a2b45_m123_final_hardening` (revisions `9f2c4d7a10b3` → `c3d81f6a2b45`):

* `projects.quality_policy` (JSON, nullable)
* `jobs.request_revision`, `jobs.pending_repair_context`, `jobs.quality_verification`
* `job_attempts.attempt_kind`, `.parent_attempt_id`, `.request_snapshot`,
  `.prompt_sha256`, `.parameter_sha256`, `.manifest_sha256`,
  `.prompt_graph_sha256`, `.repair_reason`, `.repair_root_cause`
* `workflow_profiles.media_transfers` (JSON, nullable)
* new table `quality_gates`

Only additive `ALTER TABLE … ADD COLUMN` statements and one new table are
used, exactly like the previous revision, because SQLite batch mode can drop a
parent table and silently cascade-delete user rows.

## 5. New tests and the exact commands

```powershell
uv sync --project backend --locked
uv run --project backend --locked pytest -c backend/pyproject.toml backend/tests
uv run --project backend --locked ruff check backend/shine backend/alembic backend/tests
npm ci --prefix frontend
npm --prefix frontend run build
```

| Test module | Tests | Focus |
| --- | --- | --- |
| `backend/tests/test_reference_transfer.py` | 13 | P1-01 media transport |
| `backend/tests/test_quality_gates.py` | 17 | P1-03 quality gates |
| `backend/tests/test_submission_reconciliation.py` | 6 | P1-02 uncertain submissions |
| `backend/tests/test_repair_revisions.py` | 10 | P1-04 request revisions |
| `backend/tests/test_comfy_recovery.py` | 7 | P2 automatic recovery |
| `backend/tests/test_offline_e2e.py` | 2 | Offline production E2E |
| `backend/tests/test_final_review.py` | 10 | §6 global review |
| `backend/tests/test_migrations_windows.py` | 3 | Migration fresh/upgrade/round trip |
| `backend/tests/test_technical_qc.py` (added) | 1 | The real full-decode branch with an actual FFmpeg binary |
| **New total** | **69** | |

## 6. Test results

| Run | Result |
| --- | --- |
| Baseline before this round (`main` @ `f22a1b0`) | 105 passed, 0 failed, 0 skipped |
| After this round | **174 passed, 0 failed, 0 skipped** |
| `ruff check backend/shine backend/alembic backend/tests` | All checks passed |
| `npm --prefix frontend run build` | `tsc -b` + `vite build` succeeded |
| `alembic upgrade head` on a fresh database | head = `c3d81f6a2b45` |
| `alembic upgrade head` from the M2 schema (`9f2c4d7a10b3`) | rows preserved, head reached |
| `alembic downgrade → upgrade` round trip | head = `c3d81f6a2b45` |

No test is skipped or xfailed. FFmpeg is provided by the `imageio-ffmpeg`
development dependency when the host has no system FFmpeg, so the synthetic
media used by the E2E is a real, decodable MP4 rather than a container stub.

The first Windows CI run found a **host-dependent test defect**: the media
tool lookup had a hardcoded `shutil.which("ffprobe")` fallback that ignored
the configured executable, so a test that asked for a missing tool silently got
the real one. The configured executable is now authoritative everywhere, the
affected tests name their tools explicitly, and a new test covers the
"tools present" branch with a real full decode. The suite now behaves the
same on a host with and without FFmpeg.

## 7. Offline E2E result

`backend/tests/test_offline_e2e.py::test_offline_production_end_to_end` walks
the complete loop with a real FastAPI app, a real SQLite database, the real
worker, the real H3 compiler, a real technical QC, a controllable mock semantic
reviewer, a controllable mock ASR and the Fake ComfyUI server:

1. project created; 2. script imported; 3. character/scene/voice assets
uploaded; 4. assets approved and locked by the operator; 5. Style Bible frozen;
6. the agent planned the structured shots from the imported document; 7. the
operator bound each shot's own references; 8. each shot produced an
independent reference manifest; 9. the H3 compiler emitted an official-format
prompt with a pinned rule version; 10.–12. Fake ComfyUI received the task,
produced media and the technical QC inspected it; 13. the mock semantic review
ran; 14. it reported a style drift; 15.–17. the repair engine produced a
materially different request, a new attempt was submitted and re-checked;
18.–19. the dependent shot was invalidated by the upstream re-render, re-queued
and executed; 20. the worker was restarted and the lost acknowledgement was
reconciled; 21. no duplicate external submission, no wrong asset reference and
no stale result entered the library; 22. the audit chain, the job versions and
the quality report were produced.

Measured outcome of the run: **3 external ComfyUI prompts for 3 logical
attempts** (1 initial + 1 repair + 1 reconciled), 1 artifact per attempt, the
repaired job at `request_revision = 2` with two attempts, and the final gate
`passed` with `verification = mock_verified`.

The mock markers are recorded on every gate, every QC report and the job
(`quality_verification`). **This is `MOCK_VERIFIED`, not a video-quality
result.**

## 8. Network outage and crash recovery results

| Scenario | Expected | Result |
| --- | --- | --- |
| ComfyUI accepts the prompt, the response is lost | no second submission | 1 external prompt, job `NEEDS_REVIEW` with `COMFY_SUBMISSION_OUTCOME_UNKNOWN` — **pass** |
| Worker restarts after the lost acknowledgement | the original task is found and collected | the original task was found by prompt-graph hash, the media was downloaded, QC ran, no second render — **pass** |
| ComfyUI history lost | never resubmit | `EXTERNAL_JOB_NOT_FOUND`, `NEEDS_REVIEW`, 0 new prompts — **pass** |
| ComfyUI offline during recovery | report unavailable, never resubmit | `RECONCILIATION_UNAVAILABLE` — **pass** |
| Worker crashes right after the acknowledgement | the finished task is collected | the media was downloaded, QC ran, the artifact was stored — **pass** |
| Crash while rendering | the lease is renewed, no second render | the running task keeps its lease — **pass** |
| Media already downloaded | reuse, do not download twice | one artifact row, one file — **pass** |
| QC already written, job status not updated | no duplicate QC report | the existing report is updated in place — **pass** |
| Upstream shot revision changed | discard, do not ingest | the job is stale, no artifact — **pass** |
| A second worker tries the same task | only one may take over | the compare-and-set lease lets exactly one worker through — **pass** |
| ComfyUI returns an execution error | report it as an execution error | `COMFY_EXECUTION_ERROR` — **pass** |
| ComfyUI rejects the prompt (4xx) | safe failure, nothing queued | `COMFY_PROMPT_REJECTED`, 0 queued prompts — **pass** |

## 9. Does the automatic repair really change the request?

Yes. The E2E and the dedicated repair tests assert all of the following:

* the compiled prompt text and its hash differ between attempt 1 and attempt 2;
* the request fingerprint differs, so the new attempt is a new logical request;
* the new attempt is recorded as `attempt_kind = "repair"` with its parent
  attempt id and the repair reason;
* the prompt revision is produced by the pinned H3 compiler, not by editing
  prompt text by hand;
* a repair that cannot change anything is refused and escalates to a human;
* a repeated root cause escalates instead of rerolling;
* identity, costume and speaker failures are **never** auto-repaired;
* the previous attempt and its artifact are preserved.

## 10. Quality coverage and gate combinations

| Gate | Recorded as | Notes |
| --- | --- | --- |
| `technical` | `passed` / `failed` / `needs_review` / `not_run` | container, stream metadata, full decode, duration, resolution, frame rate, audio stream, silence, SHA-256 |
| `semantic` | `passed` / `failed` / `needs_review` / `not_run` / `unavailable` | reviewer, model, confidence, finding codes, mock flag, consent |
| `audio` | `passed` / `failed` / `needs_review` / `not_run` / `unavailable` | ASR transcript coverage of the expected dialogue |
| `style` | `passed` / `failed` / `not_run` | style findings plus the frozen style version/hash |
| `final` | `passed` / `failed` / `needs_review` | the combined verdict and the verification label |

Failure modes are separated: reviewer failure, reviewer timeout, invalid
reviewer JSON, missing ASR, missing reviewer and an unaccepted mock reviewer
each produce their own error code and their own coverage value. A shot whose
technical check passed but whose required semantic review never ran is
`NEEDS_REVIEW`, never `PASSED`.

## 11. Windows platform results

| Check | Command | Result |
| --- | --- | --- |
| Environment check | `scripts/check_environment.ps1` | passes |
| Local stack (API + worker + mock production) | `scripts/verify_local_stack.ps1` | **pass** — API healthy on `127.0.0.1`, a mock job reached `PASSED` with `simulated=true` and `video_created=false`, 7 audit events written, 2 workers reported |
| Fresh migration | `alembic upgrade head` on a throwaway data directory | head = `c3d81f6a2b45` |
| Migration from the M2 schema | `alembic upgrade head` from `9f2c4d7a10b3` | project/job rows preserved |
| Backend tests on Windows | `pytest backend/tests` | 174 passed locally, and 174 passed in the Windows CI job |
| Frontend build on Windows | `npm --prefix frontend run build` | succeeded |

The verification script uses a throwaway data directory and never touches an
existing project database.

## 12. CI results

`.github/workflows/ci.yml` now has three jobs:

* `backend` (ubuntu): installs FFmpeg/FFprobe, syncs, runs the tests, runs
  ruff, migrates a fresh database.
* `backend-windows` (**new**, `windows-latest`): installs FFmpeg via
  Chocolatey, syncs, runs the whole test suite, runs ruff, migrates a fresh
  database and starts the API on `127.0.0.1:8123` to assert `/health` and
  `/api/v1/system/status` answer. This job is the Windows non-GPU baseline.
* `frontend` (ubuntu): `npm ci` and `npm run build`.

**Executed result — CI run `39541238457` on commit `d228ee5`, all three jobs
green:**

| Job | Result | Evidence |
| --- | --- | --- |
| `backend` (ubuntu) | ✅ success | `174 passed, 1 warning in 74.47s`; ruff clean; `alembic upgrade head` succeeded |
| `backend-windows` (windows-latest) | ✅ success | `174 passed, 1 warning in 138.05s`; ruff clean; fresh migration succeeded; the local workbench answered `health: {"status":"ok","service":"shine-api","render_provider":"mock-only","h3_connected":false}` and `status: mock api_online=True` |
| `frontend` (ubuntu) | ✅ success | `npm ci` + `npm run build` succeeded |

The first Windows run failed and exposed a host-dependent test defect, which
is now fixed (see §6). The green run above is the executed evidence.

## 13. Security check results

| Item | Result |
| --- | --- |
| API keys in SQLite, logs, audit payloads or API responses | not present; keys stay in DPAPI / `0600` storage |
| Absolute local paths in audit payloads | redacted by `sanitize_payload` |
| Remote VLM/ASR without project consent | refused with `PermissionError` → `SEMANTIC_REVIEW_FAILED`; only derived evidence frames and the produced audio are sent |
| Agent calling asset approval or style freeze | impossible; the agent runtime has no database, filesystem, shell or approval tool |
| API/Vite binding | `127.0.0.1` only |
| Unrestricted arbitrary URL reads | **fixed this round**: the ComfyUI renderer URL must be loopback; a remote renderer requires the explicit `SHINE_ALLOW_REMOTE_COMFYUI=1` opt-in, because reference bytes are uploaded to it |
| Unrestricted arbitrary file reads | uploads are sanitised, size- and signature-checked, stored under content-addressed paths, and read back only through `safe_asset_path` |
| Unrestricted shell execution | none in the product code |
| Destructive migrations | only additive `ADD COLUMN` and one new table; a downgrade/upgrade round trip is tested |
| User data destruction | the verification script and the migration tests use throwaway directories |

## 14. Still to be verified with a real H3 environment

Everything below stays `PENDING_LOCAL_H3` and **must not** be inferred from
this report:

* the real MiniMax-H3 API workflow JSON for the user's ComfyUI nodes, its
  `parameter_map`, and the `media_transfers` declaration for video and audio;
* the real H3-Base checkpoint family, filename and SHA-256;
* real T2VA / I2VA / FL2VA / L2VA / Ref2VA generation and output quality;
* identity consistency, costume stability, lip-sync and dialogue accuracy;
* long-shot continuation across shots;
* the real VLM/ASR review path with an authorized provider profile and the
  user's explicit consent;
* the real FFprobe/FFmpeg QC on H3 output (the synthetic media used here only
  proves the checker works on a valid MP4);
* unattended full-drama completion.

The exact procedure and the evidence template stay in
[`local-h3-acceptance.md`](local-h3-acceptance.md).

## 15. Open issues and risk levels

| # | Issue | Risk | Mitigation / next step |
| --- | --- | --- | --- |
| 1 | The real H3 workflow mapping, the video/audio media transport and the checkpoint are unknown | **High** (blocks real production) | Register the exported API workflow during local acceptance; video/audio need an explicit `media_transfers` declaration |
| 2 | The offline E2E proves the software loop with a mock reviewer; character identity and lip-sync are unproven | **High** | Real VLM/ASR review with consent during local acceptance |
| 3 | The technical gate can be downgraded to non-required with `allow_unverified_technical` | **Medium** | The downgrade is explicit, audited and visible in the gate ledger; the final verdict is labelled `partial`. Production projects should leave it off |
| 4 | A mock reviewer can be accepted with `accept_mock_review` | **Medium** | Same: explicit, audited, and the job is labelled `mock_verified` |
| 5 | The FFmpeg metadata fallback parses `ffmpeg -i` output | **Low** | Only used when FFprobe is missing; the full decode check still uses FFmpeg and fails loudly on media errors |
| 6 | `find_submission` matches a lost submission by prompt-graph hash | **Low** | It can only match an identical prompt graph; a non-match stays `NEEDS_REVIEW` and is never resubmitted |
| 7 | The worker polls ComfyUI once per second | **Low** | Acceptable for a local renderer; a long render holds a renewed lease |

No unexplained high-priority software defect remains in the offline scope.

## 16. Delivery

* Branch: `feat/m123-final-hardening`
* Implementation commit SHA: `6b49a0c7fd3190367ccca8ddd5fab9b6cb1217d6`
* Follow-up commits: the host-independence fix (`d228ee5`) and the report
  updates. The head of the branch is what CI run `39541238457` verified.
* Pull request: <https://github.com/anjingdtl/shine-video-studio/pull/1>
* The branch is **not** merged into `main`; merging requires the user's
  authorisation.
