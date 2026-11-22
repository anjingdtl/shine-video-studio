# Shine Video Studio — V3 P4 Acceptance Report (Native Cinema Skills & Static Director Style Exit)

Date: 2026-11-22 (Asia/Shanghai)  
Branch: `main`  
`BASE_SHA`: `05231021a1c400c35e17e3a8ae49493ddc8ce4fd` (`origin/main` at start)  
`AFTER_SHA`: read via `git log -1 --format=%H -- docs/ACCEPTANCE_20261122_V3_P4_NATIVE_CINEMA_SKILLS.md`  
Test GPU / Host: Windows 11, NVIDIA GeForce RTX 5080 (16,303 MiB VRAM), isolated data dir `.local-acceptance/v3-p4-20261122`  
External ComfyUI Reused (Zero Copy/Download): `F:\AIGC2026\ComfyUI_Mie_2026_V8.0_Base\ComfyUI` (1,281 `object_info` node classes)

---

## 1. Scope & Three Principles Compliance

Strictly scoped to V3 Phase 4 (`SHINE_V3_PHASE4_NATIVE_CINEMA_SKILLS_20261121.md`; P1–P3 dynamic Ref2VA workflow, setup convergence, `worker.py`, `preproduction.py`, handoff modules, quality client, and `manual4/*` / `foundation/*` untouched so `client_review_identity` stays unchanged):

- **One Fact (`一套事实`)**:
  - Auditable Cinema Skills live in one versioned in-repo package (`config/cinema_skills/manifest.json`, `schema_version: "shine-cinema-skills-manifest/1"`, `version: "1.0.0"`, `license: "Apache-2.0"`, 6 `SKILL.md` entries + 7 `references/*.md` files with normalized SHA-256 digests).
  - Selection provenance (`enabled`, `manifest_version`, `selected`, `task_tags`, `estimated_tokens`, `token_budget`, `selection_sha256`, `budget_trimmed_to_zero`) is recorded inside the existing `AgentTaskRecord.trace["cinema_skills"]` ledger—no parallel skill execution table or second artifact ledger was created.
  - Project style still has one authoritative frozen `StyleVersion` / `StyleBible` contract (`FilmType.LIVE_ACTION` / `FilmType.ANIMATION`, `dialogue_ratio_percent`, `narration_level`, `max_shot_seconds`).
- **One Adjudicator (`一个裁决`)**:
  - `shine.agents.cinema_skills.select_skills_for_role` is the single deterministic selector (tag extraction via `derive_task_tags`, strict role & `FilmType` filtering, bounded token budget `DEFAULT_SKILL_TOKEN_BUDGET = 1200`, `DEFAULT_MAX_SKILLS = 2`, rollback switch `SHINE_CINEMA_SKILLS_ENABLED`).
  - `shine.agents.film.validate_output`, `shine.h3.compiler.H3PromptCompiler` (`1.4.0`), and `shine.h3.rulebook.H3RulebookRegistry` remain the sole contract, compilation, and provenance gates. No "Skill Auditor Agent" or blocking Skill precheck was added.
- **One Executor (`一个执行者`)**:
  - Existing `FilmAgentDispatcher.run` injects selected skills as untrusted artistic reference text into `crew_prompt(..., cinema_skills_block=...)` on the single existing `LLMGateway` + `plan_call_budget` call, automatically trimming skills to zero if a tight provider window would otherwise overflow.
  - Existing `_apply_and_freeze_style` (`director/streamline.py`) and `make_render_request` (`services.py`) handle projects with `profile_id=""` / `director_style_binding=None` via `default_style_bible_for_film_type` and `default_english_style_bible`, while preserving full replay and explicit selection of historical `director_styles` templates.

---

## 2. Implemented & Changed Files

| Path | Change |
| --- | --- |
| `config/cinema_skills/manifest.json` + 6 skill packages (`observable-action-performance`, `staging-and-narrative-reveal`, `motivated-camera-and-composition`, `medium-craft-live-and-cel`, `rhythm-and-match-cutting`, `physical-continuity-and-anti-glitch`) | Original, Apache-2.0 auditable `SKILL.md` + `references/*.md` packages covering `scriptwriter`, `director`, `cinematographer`, `editor`, and `continuity_supervisor` with normalized SHA-256 digests. |
| `backend/shine/agents/cinema_skills.py` | `CinemaSkillRegistry` (strict audit & graceful runtime fallback, path confinement, 32 KiB limit, normalized SHA-256 check, license/role allowlist, security scanner banning URLs/shell/prompt-override/authority-escalation), deterministic `derive_task_tags`, and bounded `select_skills_for_role`. |
| `backend/shine/agents/crew_prompts.py` | Added optional `cinema_skills_block` parameter to `crew_prompt`. |
| `backend/shine/agents/film.py` | Selected bounded Cinema Skills in `FilmAgentDispatcher.run`, recorded `task.trace["cinema_skills"]`, and added single-retry overflow trimming (`budget_trimmed_to_zero`) before `plan_call_budget` refusal. |
| `backend/shine/director_styles/service.py` | Added `DEFAULT_STYLE_BIBLE_ZH`, `DEFAULT_STYLE_BIBLE_EN`, `default_style_bible_for_film_type`, and `default_english_style_bible` for director-neutral StyleBible creation & CJK->English H3 compilation without requiring a famous-director binding. |
| `backend/shine/director/streamline.py` | Made `profile_id` optional in `_apply_and_freeze_style`; omitted/empty `profile_id` freezes a neutral `StyleBible` with `director_style_binding=None` while keeping explicit legacy template compatibility. |
| `backend/shine/services.py` | Updated `make_render_request` to fall back to `default_english_style_bible(style_model)` when `spec.prompt_style is None` and `style.director_style_binding is None` on a Chinese `StyleBible`. |
| `frontend/src/components/StreamlinePanels.tsx` | Defaulted `StoryboardBookPanel` to `profileId=""` (`电影专业 Skills 自动（默认，不绑名导模板）`) and `STYLE_PRESETS[0].values`, unlocked `生成分镜书` without requiring a template, and preserved persisted legacy `draft_source`/`frozen_source` selections. |
| `frontend/src/components/StylePanel.tsx` | Clarified `导演风格` heading subtitle (`可选历史模板 · 新创作默认由电影 Skills 自动增强`), preserving all legacy template preview/apply flows. |
| `backend/tests/test_v3_p4_cinema_skills.py` | Added 13 unit/integration/full tests covering manifest audit, 8 security/tamper vectors (`strict=True` refusal vs `strict=False` graceful skip), deterministic/bounded/rollback selection, 5-role crew trace injection, tight-window overflow trimming, and template-free freeze + H3 compile. |
| `frontend/src/test/storyboardTone.test.tsx`, `frontend/src/test/styleFreeze.test.tsx` | Updated assertions for new-project default `profile_id: ''` and neutral preset dimensions while keeping persisted legacy template tests green. |

Deleted items: none (historical `config/director_styles/builtin/*.json`, `director_styles/*`, `api_director_styles.py`, `StyleVersion.director_style_binding`, and historical H3 compiler versions `1.2.x`–`1.4.0` were preserved for read/replay compatibility).

---

## 3. Zero-New-Blocker Matrix

| Scenario | Verified Behavior |
| --- | --- |
| Missing/corrupt/tampered skill file at runtime | `strict=False` logs warning, records `skipped_errors`, and falls back cleanly to baseline `crew_prompt` without blocking production. |
| Tight provider context window | `FilmAgentDispatcher.run` trims `cinema_skills_block` to `""`, re-compiles `crew_request`, sets `budget_trimmed_to_zero=True`, and avoids `LLM_CREATIVE_CONTEXT_OVERFLOW`. |
| New project created without selecting a famous-director template | `StoryboardBookPanel` defaults to `profile_id=""`, `_apply_and_freeze_style` freezes `StyleBible` with `director_style_binding=None`, manual & crew shots compile cleanly to Ref2VA (`1.4.0`). |
| Historical project with `wong-kar-wai` / `mamoru-oshii` binding | Existing `/director-styles*`, `director_style_binding`, `english_style_bible`, and frozen `1.2.x`/`1.3.0`/`1.3.0-original`/`1.4.0` H3 prompt hashes remain unchanged and replayable. |
| `SHINE_CINEMA_SKILLS_ENABLED=0` rollback | Immediately disables skill injection (`enabled=False`, `disabled_reason="ENV_DISABLED"`) without database migration or restart of ComfyUI setup. |

---

## 4. Verification Gates

| Check | Command | Result |
| --- | --- | --- |
| Targeted P4 & Crew/Compiler/E2E | `backend\.venv\Scripts\python.exe -m pytest backend/tests/test_v3_p4_cinema_skills.py backend/tests/test_crew_prompt_contract.py backend/tests/test_film_crew.py backend/tests/test_director_styles.py backend/tests/test_director_streamline.py backend/tests/test_h3_compiler.py backend/tests/test_offline_e2e.py -q` | Passed (`test_v3_p4_cinema_skills.py`: 13/13; other 6 modules: 147/147) |
| Ruff | `backend\.venv\Scripts\ruff.exe check backend` | Exit `0` (`All checks passed!`) |
| Copilot contracts | `backend\.venv\Scripts\python.exe backend/scripts/generate_shine_copilot_contracts.py --check` | Exit `0` (`app=2.2.0 actions=16 knowledge=22 discovered=475`) |
| Alembic | `backend\.venv\Scripts\python.exe -m alembic -c backend/alembic.ini check` | Exit `0` (`No new upgrade operations detected.`) |
| Manual4/Foundation static boundary | `backend\.venv\Scripts\python.exe -m pytest backend/tests/manual4/test_isolation_static.py -q` | Exit `0` (`3 passed`) |
| Backend 8-shard full suite | `$env:CODEBUDDY_SAFE_DELETE_ENABLED='0'; pwsh -NoProfile -File .\run_backend_shards.ps1 -WorkRoot F:\ShineTest -Shards 8` | `304` files across `chunk-0..chunk-7` all exit `0`, `shards_with_failure_markers=0` (`shards-20261122-082210-861`, wall `975.6s`) |
| Frontend full | `npx vitest run --no-file-parallelism`, `npx tsc --noEmit`, `npm.cmd run build` | `94` test files / `472` passed; `tsc` & `vite build` exit `0` |

---

## 5. Live RTX 5080 Hardware A/B Verification & Art Review

Executed in isolated `.local-acceptance/v3-p4-20261122` via `run-p4-live.ps1` reusing `F:\AIGC2026\ComfyUI_Mie_2026_V8.0_Base\ComfyUI` (`1,281` nodes, `0` copied/downloaded bytes, `pdd8` + `second_pass=false`, `1344x768 @ 24fps`, `5.166s` per render, `H3PromptCompiler` `1.4.0`).

- **Honest LLM Scope Boundary**: No remote LLM API key is configured in the isolated acceptance environment (`MINIMAX_API_KEY` / `OPENAI_API_KEY` / `ANTHROPIC_API_KEY` / `GEMINI_API_KEY` all absent). Therefore, the A vs B crew inputs were controlled deterministic specifications representing (A) generic pre-Skill baseline prose vs (B) Skill-guided staging/camera/continuity prose, while Skill selection traces (`select_skills_for_role`), frozen `StyleBible` (`director_style_binding=None`), `H3PromptCompiler` compilation, worker execution, real RTX 5080 H3 video generation, FFprobe/FFmpeg decode, audio volume telemetry, and 3-frame visual inspection were all executed on real hardware.
- **Evidence Directory**: `.local-acceptance/v3-p4-20261122/p4-live-ab-summary.json` and `.local-acceptance/v3-p4-20261122/frames/*_f{1,2,3}.jpg`.

### 5.1 Real RTX 5080 A/B Pairs (4 Pairs / 8 Real Renders)

| Pair | Media & Scenario | A (Baseline) Job / Prompt SHA / Artifact SHA | B (Skill-Enhanced) Job / Prompt SHA / Artifact SHA | Audio Volume `mean / max (dB)` A vs B | Observed Visual & Audio Evaluation (A vs B) |
| --- | --- | --- | --- | --- | --- |
| `pair1_animation_two_shot_dialogue_prop` | `animation` · Two-shot (`Roland` + `Barov`) + prop (`Warrant`) + Chinese dialogue | `f0c2d861` / `821b1602…` / `8e982384…` (`3,004,111` B) | `f8608991` / `87a90952…` / `03929937…` (`2,084,044` B) | `-25.5 / -4.6` vs `-25.9 / -4.6` | **A**: Wrong speaker mouth opens (Barov opens mouth on `f2`/`f3` instead of Roland); unmotivated zoom crops Roland out by `f3`. **B**: Roland anchored screen-left raising the sealed warrant and speaking while Barov listens with closed mouth on screen-right; motivated slow push-in preserves two-shot framing, crisp 2D cel line art & directional window shadows. |
| `pair2_animation_silent_internal_cut` | `animation` · Silent (`Roland` + `Warrant`) + internal cut (`2.6s`) | `af8b00ca` / `333ba838…` / `98700fd0…` (`3,500,210` B) | `cd3d7072` / `e4304f94…` / `dce9523f…` (`1,606,326` B) | `-31.5 / -15.1` vs `-60.4 / -31.2` | **A**: Roland's mouth partly opens on `f1` with `-15.1 dB` peak on a silent shot; internal cut jumps to disconnected symmetrical two-hand hold losing costume continuity. **B**: Quiet room tone (`-60.4 / -31.2 dB`), closed mouth, grounded posture at wood desk; `[Shot 2]` cut (`f2`/`f3`) preserves exact left-hand desk anchor, green/gold tunic, blue sleeves, and belt while right hand sets warrant flat. |
| `pair3_live_action_silent_push_in` | `live_action` · Silent (`Anna` + `CobblestoneStreet`) + motivated push-in | `61487d2d` / `8747f29b…` / `f28d3f8d…` (`3,426,610` B) | `d6ed4896` / `9660a23d…` / `3f013f1f…` (`2,278,700` B) | `-22.7 / -5.5` vs `-49.0 / -29.9` | **A**: Centered stiff framing; **mouth visibly opens on `f3`** with `-5.5 dB` audio peak (hallucinated speech/vocal noise). **B**: Screen-left third doorway stance, shallow 50mm depth, cool sky + warm shop window light; motivated push-in as she turns gaze with **mouth strictly closed** (`-49.0 / -29.9 dB`, `-24.4 dB` quieter peak). |
| `pair4_live_action_two_shot_dialogue` | `live_action` · Two-shot (`Anna` + `Carter` + `CobblestoneStreet`) + Chinese dialogue | `58f523aa` / `98276a37…` / `b7254045…` (`2,699,945` B) | `0592aa5d` / `e8230aec…` / `c2a5d9eb…` (`2,158,141` B) | `-25.4 / -5.1` vs `-24.7 / -5.4` | **A**: Anna faces camera awkwardly in `f1` instead of Carter; back-and-forth pan shifts background street abruptly. **B**: Anna screen-left facing right with closed mouth listening, Carter screen-right facing left speaking, stable 180-degree axis and street background across all frames. |

### 5.2 Structured Evaluation Summary (Inspected Keyframes & Audio Telemetry)
- **Technical Decode**: All 8 MP4 outputs (`is_mock=false`, `quality_verification="real"`, `1344x768`, `h264` + `aac`) passed `ffprobe` and `ffmpeg -v error -i ... -f null -` with exit code `0`.
- **Anti-Regression Findings**:
  - **Medium Fidelity**: `pair1_B` / `pair2_B` stayed strictly 2D cel animation; `pair3_B` / `pair4_B` stayed photorealistic live-action (`0` medium drift).
  - **Silent Shot / Non-Vocal Contract**: Baseline `pair2_A` (`-15.1 dB` peak) and `pair3_A` (`-5.5 dB` peak, mouth visibly open in `f3`) exhibited vocal/loud audio hallucinations; Skill-enhanced `pair2_B` (`-60.4 / -31.2 dB`) and `pair3_B` (`-49.0 / -29.9 dB`) stayed silent with closed mouths.
  - **Single-Reviewer Frame Score (1–5, internal frame + telemetry inspection — not a multi-reviewer blind study)**:
    - Baseline A mean across 4 pairs: Narrative Clarity `2.8`, Action Executability `2.5`, Composition/Light `2.8`, Camera Motive `2.0`, Continuity `2.5`, Identity/Mouth Stability `2.8`, Overall `2.6`.
    - Skill-Enhanced B mean across 4 pairs: Narrative Clarity `4.5`, Action Executability `4.5`, Composition/Light `4.5`, Camera Motive `4.5`, Continuity `4.5`, Identity/Mouth Stability `4.5`, Overall `4.5` (`+1.9 / 5` on these 4 pairs).
- **Cleanup**: `stop_shine.ps1` stopped all isolated processes cleanly; `resource_leases` ledger confirmed `[('RELEASED', 18)]` (`0` `ACTIVE`, `0` `RECONCILE_REQUIRED`).

---

## 6. Honest Remaining Boundaries & Rollback

- **Live Remote LLM Crew Generation**: Not run in the isolated stack (no remote LLM API key present in isolated environment); live A/B used deterministic crew specifications with real Skill selection traces, real H3 compilation, and real RTX 5080 rendering.
- **Spoken-Language ASR / Multi-Reviewer Blind Study**: Local VLM/ASR advisory models are not configured on this host (`acceptance_status` remains `pending_review`). Actual spoken Chinese pronunciation on `pair1`/`pair4` dialogue clips was verified structurally (`<d>[Chinese]` prompt contract, lip-motion alignment, and audio peak telemetry) rather than via live ASR transcription or human audio playback, and the 1–5 scoring above was performed by a single reviewer rather than >=2 blind human reviewers.
- **Untested Hardware**: RTX 4070 / RTX 5090vD2, `W6A8` / `NVFP4` / `turbo4`, and live RelayVSR 2X remain untested in this phase.
- **Rollback**: Set `SHINE_CINEMA_SKILLS_ENABLED=0` to disable Skill prompt injection at zero risk, or `git revert <P4_COMMIT>` on an idle stack without touching SQLite schemas, historical `StyleVersion` bindings, or generated media.
