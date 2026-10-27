# 本地环境验证报告（V1.3 真机集成轮）

Bounded structured wire continuation (2026-10-27): **832 backend tests passed**, zero failures/errors/skips; Ruff passed. The current VLM JSON schema now constrains each finding message to 240 characters and the summary to 300, matching application checks and the prompt. This applies only to the current wire; existing stored model limits and historical responses are unchanged. The decoder lock pins schema `86c02ef0acfc61ff1f7a69af0e1c43e2e48f9e56cd35f7793f1223238b646bf9` and decoder identity `17fc2ea6c9694291b05521ff30f0f4b389cbbfb9977bd7db290eaf30e32e6795`. A CPU-only test with the real tokenizer's alphabet accepted ASCII and Chinese 240-character messages and a 300-character summary, and rejected the respective over-limit cases. It loaded no model weights and did not initialize CUDA. Numeric bounds remain application-validated; the previously observed canonical tokenization limitation is not claimed fixed.

This decoder-lock change creates a new executor epoch: prior ASR qualification `1b2a7ca8-03e3-4cc3-8638-ea1157ff0d28` is operationally stale for the changed runtime even while its historical database status remains approved. It must be technically deprecated and a new real ASR sample requalified before claiming current ASR coverage. New VLM controls are **NOT RUN** and VLM remains unpublished. The previous incomplete walking response and abandoned unknown results are preserved; no H3 or review was replayed. Full-product status remains **PARTIAL**. The most recently checked hosted Windows job at `c9e782a` did not start because of account billing/spending limits. See [bounded wire evidence](evidence/local-closeout-20261025/bounded-wire-contract.json).

Actual identity-bound walking control (2026-10-27): after the 830-test software gate, source commit `4600572` activated VLM draft `0ad9d513-4c0f-4dd3-8ecc-9c2129d2e383` as STATIC_VALIDATED only. The revised walking negative `cfafd401-47c8-4b36-8f78-a8f3a0cbe318` did not return a complete response within the 300-second inference timeout; the separate 30-second release confirmation also failed. No quality decision can be inferred. The other three controls were NOT RUN, and the 15,874 MiB whole-GPU point sample is not peak proof or validation of the estimated 14,000 MiB model budget.

After the controller ended, exact owned executor PID/birth and Windows RAM counters were checked, the process was exited, and the identical runtime restarted cold with zero loaded models/CUDA allocation. Under explicit user technical delegation, a one-use reconciliation marked only the lost response **ABANDONED / outcome UNKNOWN**, released its lease and restored managed ComfyUI idle. Protected project/master/Style/Job/QC/provider/workflow tables remained unchanged during the control and reconciliation. No automatic replay, H3 submission, remote text, media upload or human quality/rights approval occurred. ASR qualification remains approved; VLM remains unpublished and full-product status **PARTIAL**. See [identity-bound actual and reconciliation evidence](evidence/local-closeout-20261025/identity-pinned-native-actual.json).

Identity-bound and concise review continuation (2026-10-27): **830 backend tests passed**, zero failures/errors/skips; Ruff passed. Structured local VLM `IDENTITY_MISMATCH` now requires a derived image reference bound by the frozen manifest to the exact target character. A scene/prop/costume reference, another character's reference or an ambiguous multi-character subject cannot satisfy this check. A structured finding message longer than 240 characters rejects the response rather than being silently shortened. Worker diagnostics identify `length` termination as an incomplete response rather than invalid JSON or an accepted result.

All film-review contexts now record a separate logical artifact/ShotSpec/Style hash for unknown-outcome reconciliation, including reviews without independent text evidence. Changed job aliases, revisions, transcripts or client versions cannot bypass an unresolved call for the same target; different media remains independent. Legacy unresolved rows without a comparable hash require reconciliation first. The current client-source fingerprint is `5e1e5a4c3ee3051e397a0e6206c1851de033ad3668c2018c2adeab97372cfbbb`; earlier VLM drafts and the bounded mask result remain immutable history and do not qualify this changed client. The executor runtime and existing ASR qualification are unchanged.

The new serial control harness pins its own local helper hashes and requires 14,800 MiB observed free before each model call; this is a preflight threshold, not model-peak measurement. Its target-specific matcher requires the planned posture, missing-character or absent-mask defect and rejects unrelated hard findings. Synthetic predicate checks passed, but the new real controls are **NOT RUN yet**. VLM remains unpublished and full-product status **PARTIAL**. See [identity-bound review evidence](evidence/local-closeout-20261025/identity-pinned-review-contract.json).

Actual source-pinned pixel controls (2026-10-27): under client `f775c486b299d6ff973c48e4427826578d5fe409731d8e2eadc0a70c2c62a1ff`, the predeclared mask negative `3327a0ba-11c8-4fb3-8421-cf32752a1eeb` met its bounded automated expectation: a hard costume violation, its finding mentions mask and face, all seven input checks were consistent, and the JSON ended naturally. This is limited automated adversarial evidence, not human visual acceptance or VLM publication. The following walking negative `494130b9-b80d-48d2-9798-f2056fdc648d` produced 2,048 completion tokens and stopped at `length`; its JSON was incomplete after seven partial finding codes and five partial input fields. It is a received **VLM_RESPONSE_INCOMPLETE** failure, never a passed result. The remaining two controls were NOT RUN; original response bytes and digests are retained locally.

Each stage's lease was released and exact owned reviewer process exited before a cold identical runtime was started; managed ComfyUI was restored idle. The whole-GPU mask-stage point snapshot showed 15,875 MiB used; it is not model-peak proof and the 14,000 MiB budget remains an estimate. Protected project/master/Style/Job/QC/provider/workflow tables were unchanged during the controls. No H3, cloud media, automatic replay or human quality/rights approval occurred. Client code is now changing to constrain finding length and require the matching character reference for identity claims; therefore the tested client fingerprint is historical and cannot qualify the new code. VLM remains unpublished; full-product status **PARTIAL**. See [source-pinned real control evidence](evidence/local-closeout-20261025/client-pinned-native-actual.json).

Source-pinned review and honest media diagnostics (2026-10-27): **819 backend tests passed**, zero failures/errors/skips; Ruff passed. Structured native VLM capabilities now pin the loaded client-source fingerprint `f775c486b299d6ff973c48e4427826578d5fe409731d8e2eadc0a70c2c62a1ff` covering review prompt and validation, input assessment, frame extraction, quality gates, Worker and admission code. File changes after load fail closed; calibration evidence and production must match the pinned client, decoder, executor and input policy. Previously unpinned structured VLM drafts remain historical and do not validate under the new contract. The actual database has no published VLM capability; the unchanged executor ASR revision remains approved.

An unknown guided review is bound to a separate artifact/ShotSpec/Style logical hash, so changing job alias, review revision, transcript or client version cannot bypass reconciliation. Different media remains independent; an old unresolved review lacking a comparable logical hash must be reconciled before a new guided review. A received contract rejection, JSON parsing error and unknown timeout now have distinct Worker diagnostics. Known dialogue-absence/mismatch claims require non-Mock ASR and cannot become deterministic hard errors from probabilistic recognition. Configured Mock reviewers and `mock`/`is_mock` transport envelopes retain Mock provenance; a fake local executor identity is rejected. None of these checks turns the prior failed compact response into a pass.

The explicit prompt now illustrates the observed case: input texts can agree on a seated person even when the video shows an empty street; the latter is a separate visual finding. This does not force the model's judgments. The new client-pinned real controls are **NOT RUN yet**, VLM remains unpublished, and full-product status is **PARTIAL**. Historical response and abandoned unknown call remain intact. See [client-pinned review evidence](evidence/local-closeout-20261025/client-pinned-review-contract.json).

Actual compact pixel negative control (2026-10-27): the local 8B reviewer returned a naturally terminated response for the predeclared empty-street video (3,792 prompt and 1,493 completion tokens). The response was stored after 167.925 seconds measured from controller start, including preflight and Comfy stop; this is not isolated inference time. Its decision was `failed` and included missing-person/blocking findings, but it also labeled the written `shot_blocking` input as conflicting, labeled `sound_performance` uncertain, and asserted missing dialogue without ASR. The required matching input-conflict findings were absent, so application validation correctly marked call `25bf1e76-551b-4658-88b9-3609b7707100` **FAILED / STYLE_INPUT_DECISION_CONTRADICTION**. This is a received contract failure, not a successful negative control. The unchanged response digest and original bytes are retained locally; no judgments were overwritten.

The next three controls were NOT RUN. The exact owned reviewer process exited, Windows RAM counters and process death were recorded, the lease was RELEASED, an identical cold runtime was verified, and managed ComfyUI was restored idle. A whole-GPU point sample showed 15,729 MiB used, not a measured model peak; the 14,000 MiB admission value remains estimated. Protected project/master/Style/Job/QC/provider/workflow tables stayed unchanged during the control. No H3, cloud media, automatic replay or human quality/rights approval occurred. The VLM remains unpublished and full-product status is **PARTIAL**. See [actual compact negative evidence](evidence/local-closeout-20261025/compact-native-actual.json).

Compact source-bound review continuation (2026-10-27): **794 backend tests passed**, zero failures/errors/skips; Ruff passed. All seven independent text judgments, source pointers, confidence and details remain bound to the validated full assessment hash. The new `text-assessment-compact-v2` protocol omits duplicated quotations already supplied in the frozen Style/ShotSpec. The actual Qwen tokenizer counted user text **2,737 → 1,771 tokens** (966 fewer); four original 768×439 frames remain, and visual tokens were not counted. This is not proof of GPU memory savings or a timeout root cause. Native workflow, calibration call and production review must pin the same evidence protocol. Previous guarded VLM draft `5e30c56b-8c9c-4eeb-a223-f32960297b7d` remains immutable history but fails current static binding validation because it lacks this new version; it was never published. The server/decoder runtime is unchanged and approved ASR `1b2a7ca8-03e3-4cc3-8638-ea1157ff0d28` remains valid.

Release confirmation now waits at most 30 seconds or the provider's shorter timeout; unconfirmed release keeps the lease `RECONCILE_REQUIRED`. A received rejection digest is recorded before cleanup so a later release exception cannot erase it. The original timed-out call remains ABANDONED with outcome UNKNOWN and is not replayed.

Independent CPU-only decoder diagnostics found a schema-valid synthetic JSON whose canonical Qwen token sequence is rejected at one punctuation-spanning token; a split-token sequence reconstructing the identical JSON is accepted to EOS. This is a tokenization-path limitation, not evidence that every encoding of that JSON fails or that it caused the GPU timeout. For 172 synthetic output tokens, the CPU callback took 0.229–0.258s across 8/3,500/4,500-token synthetic prefixes; this is not real GPU generation timing. The new real controls are **NOT RUN yet**, VLM remains unpublished and full-product status stays **PARTIAL**. See [compact review evidence](evidence/local-closeout-20261025/compact-review-contract.json).

Hosted CI refresh (2026-10-27): all four checks at `c06f33a` are failure; Windows check `121950609892` explicitly reports that the job was not started because of account payments/spending limits. This independently confirms the external CI blocker on the delivered code. The following evidence-only commit changes no tested source and claims no hosted PASS.

Actual source-bound pixel continuation (2026-10-27): **first control timed out; actual result UNKNOWN, not PASS**. Source commit `210c9dd` activated successfully on unchanged executor runtime `3232277f54da98e27620cd21132c14c83109f7d7c32cd0c7fbffcd1b8a2255b2`. New guarded VLM revision `5e30c56b-8c9c-4eeb-a223-f32960297b7d` is STATIC_VALIDATED and unpublished. The single real local call `50a61ac9-c076-4ed1-beae-48557b64a3e4` carried the exact verified text-assessment pin, but no complete response was received within the 300s request timeout (zero automatic retries). Cleanup could not confirm unload; the controller ended with NEEDS_REVIEW and a RECONCILE_REQUIRED lease. The remaining mask/empty/walking controls were NOT RUN. A whole-GPU point snapshot showed 15,639 MiB used; this is not a measured model peak and does not establish that the 14,000 MiB estimated admission budget is sufficient.

Under explicit user technical delegation, the ended controller and exact owned executor PID/birth were checked, the executor was exited with Windows RAM counters/process-death evidence, and an identical cold runtime was verified with no loaded models/CUDA allocation. Authenticated one-use reconciliation changed only the lost result to **ABANDONED / outcome still UNKNOWN**, released its lease, and restored our managed ComfyUI idle. No inference was replayed, no H3 was submitted and no media went to the cloud. Protected project/master/Style/Job/QC/provider/workflow tables stayed unchanged during controls/reconciliation; ASR qualification `1b2a7ca8-03e3-4cc3-8638-ea1157ff0d28` remains approved. Full-product status remains **PARTIAL**. Next work must investigate bounded context/resource cost and cleanup latency before a genuinely revised control; the original unknown request is not replayable by this one-use runner. See [actual guided review and reconciliation evidence](evidence/local-closeout-20261025/guided-native-actual.json).

Source-bound pixel-review software continuation (2026-10-27): **791 backend tests passed**, zero failures/errors/skips; Ruff and fresh TypeScript/Vite passed. The tracked reviewer now admits only exact assessment ID/hash pins, rebuilds the seven text judgments from authoritative sources and rechecks them before GPU admission, immediately before submission and after a received result. Injected judgments, stale/cross-project/current-Style or provider/input changes and Mock-as-real evidence block. Source evidence remains untrusted user content; the VLM retains its own seven checks and contradictory decisions still fail. No transcript is invented and independent audio coverage is unchanged.

Evidence-assisted calls pin a matching native production policy. They cannot qualify an unguarded binding; source evidence is rechecked on adoption, and missing proof or a policy mismatch blocks before H3. Legacy no-evidence replay fingerprints and candidate image-byte reviewer interfaces remain intact. Intermediate transcript-fixture and candidate-review failures were repaired without weakening their assertions; failed reports remain local. The decoder wire/server are unchanged, so current ASR qualification is retained. Real guided pixel controls are **NOT RUN yet**, VLM publication remains withheld and full-product status remains **PARTIAL**. Latest verified hosted Windows check at `ca99fb2` was not started because of account billing/spending limits. See [source-bound review software evidence](evidence/local-closeout-20261025/guided-review-contract.json).

Structured-runtime actual continuation (2026-10-27): **format layer improved; VLM still NOT QUALIFIED**. New runtime `3232277f54da98e27620cd21132c14c83109f7d7c32cd0c7fbffcd1b8a2255b2` includes the fixed decoder/dependencies. Old ASR revision `86702102-652d-4b03-ade2-6a6408617c91` was authenticated and deprecated; a new real ASR call `9878a61e-7d79-4565-b6f8-55e8a1c4143a` matched the fixed source line (CER0), physically released, and new revision `1b2a7ca8-03e3-4cc3-8638-ea1157ff0d28` was authenticated through evidence adoption/publication to **PRODUCTION_APPROVED**. This is recognition evidence, not listening/voice/rights acceptance.

The first new VLM pixel call `1ae48bef-0602-40c1-a6ef-e4746e4c535e` produced naturally terminated JSON (716 completion tokens), with captured protocol/schema/decoder and executor proof matching the new epoch. Application consistency correctly rejected it: `sound_performance` was `uncertain`, but verdict was `passed` with no matching finding. Thus syntax/type format succeeded but semantic cross-field contract failed. The call is terminal FAILED with released lease, no retry, and the remaining three pixel controls were NOT RUN. The full unfiltered response is retained locally; no judgment was overwritten or synthesized. VLM revision `88a97769-c763-4489-9901-092866d9d05b` remains STATIC_VALIDATED and unpublished.

All stages were serial with exact owned review-process exit/RAM evidence and identical cold runtime. Owned ComfyUI was restored idle (current owner in the new structured-review receipt); protected production/master/style/job/QC/provider/workflow data stayed unchanged during calibration. Only explicit capability deprecation, protocol discovery, new static drafts and ASR qualification changed technical registry state. No H3 submission, cloud media bytes or human approvals occurred. Complete backend **770 PASS** remains the software gate, not full-product acceptance. Next work must carry the verified independent text assessment into pixel review with exact source/pre/post checks, rather than forcing a consistent value or deleting the contradictory response. See [actual structured epoch evidence](evidence/local-closeout-20261025/structured-native-actual.json).

Final structured-protocol software gate (2026-10-27): **770 backend tests passed**, zero failures/errors/skips. The runtime identity now also includes Pydantic/Packaging/PyYAML versions so a dependency change cannot reuse prior qualification. An intermediate run's three missing-Pillow fixture errors were repaired with the existing isolated-image-stub approach, retaining every version-change assertion; no backend ML dependency was added. The final complete suite passed. Ruff and the unchanged frontend build passed; native activation/real qualification remain pending.

Structured native review protocol (2026-10-27): complete backend **767 tests passed**, zero failures/errors/skips; Ruff and fresh TypeScript/Vite passed. Two fixed MIT wheels added 37 verified files only to the isolated review SDK directory; no global Python/model-weight change. The exact real Qwen tokenizer accepted valid Chinese JSON and rejected invalid enums/overlong text on CPU without CUDA initialization or model loading. Numeric bounds remain an explicit library limitation and still fail authoritative application validation.

The explicit opt-in `local-review-json-schema-v1` binds request schema, decoder files and returned proof. The native executor validates the committed schema pin before loading and constrains token generation. Client and native calibration/production checks reject missing/mismatched proof, unsupported protocol and changed files; generated prefixes are kept out of error logs. Legacy unstructured endpoints remain distinct, with no silent protocol downgrade. New adapter code is not activated yet: its runtime fingerprint will change, so old ASR/VLM evidence cannot qualify that new runtime. Real ASR requalification and four pixel controls are pending; VLM publication remains withheld and product status PARTIAL. See [structured contract and CPU evidence](evidence/local-closeout-20261025/structured-review-contract.json).

Latest actual pixel attempt (2026-10-27): **FAILED CONTRACT / three controls NOT RUN**. After text target checks passed and our owned idle ComfyUI was physically stopped, the first local 8B pixel call `2afcb546-3440-4fcf-a727-038b9cda4883` received an unusable response (`VLM_RESPONSE_CONTRACT_INVALID`, digest `237f94d98655ae1862da3451b8c592e47e5c487aaf75c68e64389b8e66dab37d`). It is terminal FAILED, not an unknown outcome or a passing visual review. No inference was replayed, and the remaining mask/empty/walking pixel controls were not run. The original precise schema/JSON/coverage cause was not retained and is not reconstructed.

The terminal failed-call lease was released. The exact owned review process was exited and its Windows RAM counters recorded, followed by an identical cold runtime. Our owned ComfyUI was restored with unchanged arguments and verified idle. Protected project/master/style/job/QC/workflow/provider data is unchanged. No H3 call, asset/media cloud upload, model publication or human quality/rights/voice approval occurred; ASR qualification is unchanged. See [actual failed pixel evidence](evidence/local-closeout-20261025/pixel-target-actual.json).

Follow-up diagnostics now preserve only schema-owned paths and safe error types for future failures, including JSON decoding, missing coverage, contradictory decisions, overlong text and forbidden fields. Unknown keys, provider values and messages are redacted. No failure is reinterpreted or auto-retried. Complete backend **710 tests passed**, zero errors/failures/skips; Ruff and the current unchanged frontend build passed. The UI retains 20 Mock-flow evidence. Format-constrained decoding is only a candidate: `lm-format-enforcer`0.11.3 and `interegular`0.3.3 MIT wheel metadata/hashes were audited from primary sources, but nothing was installed and Qwen compatibility was not tested. VLM, M4/human review and hosted CI remain open; **full-product status is PARTIAL**. See [safe diagnostics](evidence/local-closeout-20261025/review-contract-diagnostics.json).

Visual/transcript evidence path (2026-10-27): **708 backend tests passed**, zero failures/errors/skips; Ruff and fresh TypeScript/Vite passed. A four-case transport counterexample reproduced that Worker-supplied ASR text was omitted from the VLM request. The request now includes typed ASR text and Mock provenance; legacy strings are explicitly unverified and missing text remains null, in the user evidence rather than system instructions. The reviewer is told that frames cannot establish audible timbre/quality or prove silence. Separate technical/audio gates are unchanged: dialogue without ASR still records missing required audio coverage and blocks final passage. UI is unchanged and retains its existing 20 Mock-flow evidence.

Four new pixel diagnostic controls are predeclared but **not run yet**: actual normal, mask mismatch, empty street and walking clips against source-pinned consistent targets. These will not rewrite source Jobs or erase earlier input-conflict failures, publish the VLM, infer audio quality from frames, or approve assets/films. Current full-product status remains PARTIAL. See [transcript path evidence](evidence/local-closeout-20261025/visual-transcript-contract.json).

Same-input boundary correction verified (2026-10-27): **strict seven-field text ablation PASS**, not media/product PASS. The unchanged posture-control ShotSpec was assessed once under the clarified contract in **3.773s**: only `shot_blocking` conflicted, all other six fields were consistent, natural stop, 466 completion tokens. Exact production preflight blocked it with `STYLE_INPUT_CONFLICT_NEEDS_REVIEW`. The earlier false editing finding and its artifact remain intact. Neither historical timed-out walking request was replayed; both guards remain blocked. This establishes a bounded text input path for the tested controls; it does not qualify the local VLM, approve the original silent video, or complete M4.

Current engineering evidence: complete backend **704 PASS**, zero errors/failures/skips; Ruff, TypeScript/Vite and **20 isolated Mock browser flows** passed. Current real text controls include matched input, multi-field mismatch and a same-input posture ablation with the boundary defect reproduced then corrected. VLM remains withheld, ASR qualification unchanged, protected data unchanged, local leases released/review unloaded and ComfyUI idle. Full-product gaps remain reliable pixel review, confirmed M4 assets/rights/voices/final review and hosted CI billing/quota. See [strict actual correction](evidence/local-closeout-20261025/continuity-boundary-text-actual.json).

Actual bounded text + boundary correction (2026-10-27): new real text positive and explicit multi-field negative each made one request and returned valid assessments in **3.199s / 23.326s**, with natural stop and 410 / 572 completion tokens. Exact preflight respectively bound the new proposal and blocked the conflict. A stricter single-variable sitting-to-walking ablation returned in 4.573s and correctly detected posture, but **also falsely treated inter-shot `continuity.mode=cut` as an internal cut**. Therefore that response is not a strict seven-field pass; its unfiltered finding and original helper status are retained. The two old walking timeouts remain UNKNOWN/ABANDONED and replay-blocked even with the new budget.

The role/source/evidence-pointer descriptions now explicitly separate preceding-shot boundaries from editing inside the current shot. Current complete backend **704 tests passed**, zero failures/errors/skips; Ruff passed; unchanged UI retains TypeScript/Vite and 20 Mock browser flows. Same-input strict model revalidation is pending; it must detect posture and keep all other six fields consistent. No finding was filtered, no frozen Style or source production data changed, and VLM remains withheld. The additive live migration preserved all pre-existing profile fields; local review remains unloaded, leases released and ComfyUI idle. No H3 call, media upload, asset/voice/rights/quality approval occurred. See [unfiltered actual outcomes](evidence/local-closeout-20261025/bounded-text-actual.json) and [boundary clarification](evidence/local-closeout-20261025/continuity-boundary-text.json).

Bounded text generation (2026-10-27): **704 backend tests passed**, zero failures/errors/skips (266.420s); Ruff, fresh TypeScript/Vite, and **20 isolated Mock browser flows** passed with no API errors. Provider profiles now persist optional `max_completion_tokens` and `reasoning_effort`; unset options preserve historical behavior. The additive migration preserves existing profile fields and defaults to `{}`. UI/API validate and display these controls. Returned truncation/filter termination cannot become a passing assessment even when its JSON parses. Changing options invalidates the input proof, and neither a changed budget nor a duplicate profile can bypass abandoned-request replay protection. The initial full run's four stale migration-head assertions remain recorded and were updated without removing data-preservation checks.

The earlier successful call used 2,468 reasoning tokens. Official MiniMax documentation supports explicit output caps and defaults M3.1 effort to max; unbounded reasoning is a hypothesis for long waits, **not a proven cause of the historical timeouts**. Bounded real controls are not yet run; historical walking UNKNOWN/ABANDONED outcomes, VLM withholding, human review/M4 and hosted CI barriers remain. See [bounded runtime evidence](evidence/local-closeout-20261025/bounded-text-generation.json).

Actual v2 continuation (2026-10-27): **PARTIAL, not full PASS**. A predeclared new spoken-text proposal returned a valid seven-field assessment in 17.657s and passed exact input binding through production preflight (read-only, no Job creation). It is not the original silent video and grants no media approval. The unchanged walking conflict made exactly one request, timed out, and remained UNKNOWN. After its invocation ended, authenticated technical reconciliation marked task `16b47934-2ce1-4610-83af-7d8e7e504657` **ABANDONED**, retaining UNKNOWN, with zero further inference. The identical v2 request is replay-blocked; its production preflight remains blocked. No repeated schema change or provider fallback was used to erase this outcome.

Only the QC route uses a clearly audited separate 300s, zero-retry profile with the existing endpoint/model/secret reference; the original 120s shared profile and all other role routes are unchanged. Production/master/style/job/QC rows and both source video hashes are preserved. All leases are released, local review weights are unloaded with allocated/reserved counters zero, ComfyUI is idle, and the updated API/Web are healthy without a resident Worker. VLM publication remains withheld and current ASR qualification is unchanged.

A concrete **local review package** now indexes three existing real Qwen audio examples, two video controls, and the unconfirmed 60s two-person scene draft; all five media hashes were rechecked. It creates no candidates/masters or frozen VoiceProfiles, and the four rights records remain PENDING. Missing second-character assets, human rights/voice/final review, full M4 execution and reliable real review remain open. The four hosted checks for code `f59e825` failed before starting; the Windows annotation again identifies account billing/spending limits, not a passing CI result. See [actual v2 outcomes](evidence/local-closeout-20261025/input-source-pointer-v2-actual.json). Earlier dated sections below are historical and do not override this state.

Independent text protocol v2 (2026-10-27): **690 backend tests passed**, zero failures/errors/skips (262.118s); Ruff and fresh TypeScript/Vite passed. The model now supplies seven typed judgments and JSON pointers; the server binds the unchanged frozen Style and exact ShotSpec strings. Invalid/non-text paths, source overrides, missing/duplicate fields and contradictory proceed recommendations fail closed. Stored assessments remain backward-readable. This changes quotation responsibility, not semantic findings or the frozen Style. The earlier rejection cause is unknown; both old failed controls and the abandoned unknown outcome are preserved. Real v2 controls are **not yet run**; VLM and full-product acceptance remain **PARTIAL**. See [source-bound protocol evidence](evidence/local-closeout-20261025/input-source-pointer-v2.json).

Real remote-response reconciliation (2026-10-27): the specific legacy text timeout `768a2df9-5090-48ed-8bcf-8374c1ca7c94` was authenticated and explicitly marked **ABANDONED**, with external outcome still **UNKNOWN**. The actor was `user-delegated-codex` under direct chat authorization, with no human presence/listening/rights/quality claim. The operation made **zero inference calls and zero new agent tasks**, and did not claim local model unload or remote cancellation. A read-only evaluation of the actual original-request guard returned `AGENT_REQUEST_REPLAY_BLOCKED`; both unusable source input requests remain blocked at preflight. Protected production/master/style/job/QC row snapshots and both calibration source video SHA-256 values remain unchanged. All local leases are released, local review counters are zero, ComfyUI is idle, API/Web are healthy without a resident Worker, and ASR qualification is unchanged. The first text rejection remains unqualified and its earlier exact reason cannot be recovered. **Real text/VLM and full-product acceptance remain PARTIAL.** See [technical reconciliation evidence](evidence/local-closeout-20261025/agent-remote-reconciliation.json).

Agent unknown-outcome hardening (2026-10-27): complete backend **678 tests passed**, zero failures/errors/skips; Ruff and fresh TypeScript/Vite passed. The isolated browser passed **19 Mock flows**, including authenticated remote-response abandonment, cancel/confirm, exact JSON/grant payloads and credential clearing. Non-Mock timeout/connection/server failures now remain `NEEDS_REVIEW` with unknown external outcome. A matching unresolved request is blocked before inference; explicit abandonment still blocks the original request, including through duplicate profile IDs. A genuinely changed schema can be considered only after reconciliation. Request claiming uses SQLite's writer lock and a durable intent.

New failures retain only a safe rejection code and digest, not rejected provider text or raw returned model labels. The newest matching failed/unknown task blocks both older positive selection and a stale pinned Worker request. The remote-only abandonment endpoint requires authentication and a one-use snapshot grant, rejects local/media-model calls and active leases (including expired unreconciled ones), makes no provider request, approves no quality, and claims no local unload. Its UI clears credentials on cancel, completion, error and project switch. The real legacy timeout still awaits this technical action; no original model call has been replayed. Product/model acceptance remains PARTIAL. See [outcome controls](evidence/local-closeout-20261025/agent-unknown-outcomes.json).

Actual independent text-role continuation (2026-10-27): **NOT QUALIFIED**. Two serial role invocations produced no usable `StyleInputAssessment`: the source positive was rejected by the role contract/policy, and the remaining walking control timed out without a captured response. Neither was automatically replayed. The timeout's external inference outcome is unknown even though the existing film ledger currently stores `REJECTED`; this classification and replay protection remain engineering work, not a successful check. No precise safe rejection reason was retained for the first response. All local resource leases are released, local VLM/ASR loaded/allocated/reserved counters are zero, and ComfyUI is idle. The protected production/master/style/job/QC tables remain byte-equivalent in their row snapshot. VLM publication is still withheld, current ASR qualification is unchanged, and full-product acceptance remains **PARTIAL**. See [actual role outcomes and remaining safety work](evidence/local-closeout-20261025/independent-text-input-actual.json).

Strict local dependency check (2026-10-27): the unscoped local default failed because its Aliyun index conflicted with the lock's official PyPI registry, rather than because dependency versions changed. `uv sync --project backend --locked --dry-run --default-index https://pypi.org/simple` and an offline `uv lock --check` with the same explicit index passed. The original lock was restored byte-identically, all versions/wheel hashes were preserved, and global index settings were not changed. Hosted CI still requires an actual run after the account block is resolved.

Independent input review continuation (2026-10-27): complete backend **663 tests passed**, zero failures/errors/skips; Ruff and fresh TypeScript/Vite passed. The isolated browser now verifies **18 Mock flows**, including exact selected-shot input submission and per-call remote text consent reset. Playwright download timed out before execution; the successful run reused the verified existing 1.62.0 cache without changing dependencies.

The explicit `qc_reviewer` film role now emits a typed `StyleInputAssessment` from frozen Style and ShotSpec text only. Frozen quotes and quoted shot evidence are validated, seven criteria are required, and conflicts/uncertainty cannot recommend proceeding. New analyzed real manifests pin `independent-text-v1` and require this evidence; Job creation selects the latest exact matching report and pins its identity/hash, while the shared Worker gate rechecks current input, result, route, provenance and staleness before submission. Mock reports cannot qualify real execution. Historical frozen plans and passed data are preserved. The page offers current-project shot selection; no media bytes go to this text role. This is not pixel/audio QC or human approval. Mock provenance is monotonic across text transport and role records, including non-Mock configured endpoints advertising a Mock result. **Actual text-model controls are still pending; VLM remains withheld and the full product remains PARTIAL.** See [independent input evidence](evidence/local-closeout-20261025/independent-text-input.json).

Actual seven-field continuation (2026-10-26): **VLM qualification remains FAILED / withheld**. The real positive and masked-face negative passed. The empty-street call returned an unusable received response (`FAILED`, released lease, no retry); its initial cleanup hit a missing executor-identity field, after which an explicit continuation verified the exact owner and physically exited the process. The remaining walking control ran once separately and still falsely passed: its checklist calls walking toward camera consistent with the unchanged frozen sitting requirement. Re-reading both source requests confirmed the same frozen Style snapshot; this is not a substituted-style control. Coverage alone therefore does not establish correct input reasoning. No VLM capability was published, no production gate/history was rewritten, and no failed H3 task was replayed. All stages are released and ComfyUI has been restored idle. Current ASR qualification is unchanged. See [actual controls and failure evidence](evidence/local-closeout-20261025/style-input-real-calibration.json). Next work must separate text input consistency from pixel review; full-product acceptance remains **PARTIAL**.

Current protocol hardening (2026-10-26): **639 backend tests passed**, zero failures/errors/skips; Ruff and a fresh TypeScript/Vite build passed. A red wire counterexample reproduced acceptance of a positive response with no explicit frozen-style input comparison. New VLM responses must cover all seven Style fields, and any declared input conflict/uncertainty must have a corresponding style finding and non-pass verdict. Missing/duplicate/inconsistent coverage is a received failure with a response digest and no automatic retry. Native qualification and production evidence check the same contract; immutable historical records remain readable. This is software verification only: the four unchanged real-media controls still require new serial calls, and VLM production qualification remains withheld. Current ASR qualification is unchanged. See [protocol evidence](evidence/local-closeout-20261025/style-input-contract.json).

Current continuation (2026-10-26, actual completed execution): **PARTIAL**.

- Eight fixed model packages / 82 files / 46,803,197,151 bytes are now file-verified. The 8B FP8 package installation completed; it is no longer running.
- Five actual local calibration calls completed serially. Every lease was released, and the owned review process exited and was replaced between stages. No H3 job was resubmitted and production rows/gates were preserved.
- 8B positive and masked-face negative controls behaved correctly. The empty-street control fails the actual application style gate through `STYLE_*` codes; the private observer's case-sensitive category check was a false negative. The original receipt remains unchanged.
- The walking/sitting frozen-style conflict still falsely passed. One rejected sample also incorrectly treated a static camera as evidence against an uninterrupted take. These failures remain visible; **8B VLM is not published or production-qualified**.
- ASR actual fixed-line CER=0 qualified the current runtime. Revision `86702102-652d-4b03-ade2-6a6408617c91` is now `PRODUCTION_APPROVED`; this is technical recognition evidence, not voice identity or human listening acceptance.
- The owned ComfyUI was restored with its original launch arguments and unchanged overlay, verified idle (`RESTORED_IDLE_NO_RENDER_REPLAY`). API/Web remain available; no resident Worker was started.
- Software evidence remains 625 backend tests with zero failures/errors/skips; no application source changed in this continuation. Product-wide acceptance remains PARTIAL due VLM reliability, formal preproduction/human rights/voice/final viewing, and remote Actions billing/quota.

See [actual 8B calibration](evidence/local-closeout-20261025/vlm8b-actual-calibration.json). Earlier sections below are historical evidence, not the current qualification state.

10-26 最新实机续轮：完整后端 **625 项、零跳过/失败/错误**；Ruff 通过。前端沿用当前代码 TypeScript/Vite 与 17 类隔离 Mock 浏览器证据。
七个固定模型包 69 文件、36,197,624,599 字节已校验；真实原生 VLM 正反例、ASR 校准和认证发布完成。
后续更强的真实对抗性复核暴露 4B VLM 错误终审：曾漏报风格冲突，又误把匿名虚构人物解读为禁止可见五官/服装。
该 VLM 能力已正式 **DEPRECATED**；ASR 旧发布记录保留，但新适配器运行时需重新校准。真实预制作不把旧指纹当作当前合格能力。
两个独立真实 T2VA 样本均完成 20 步和逐帧完整解码（ComfyUI 墙钟 486.64/478.61 秒），但均未通过内容终审；不重放旧任务，不覆盖旧产物/判定。
第二例使用冻结风格一致的技术输入，并非已批准人物主资产或正式双人影片。所有模型已释放，活动租约为空；API/Web 可用，未启动常驻 Worker。
真实 Qwen 设计/克隆/新对白/识别/对齐已有证据；CER=0 与同句降速副本严格时间对齐不替代声音身份、真人试听或音色冻结。
用户自主授权以 user-delegated-codex 留账，不冒充人在场。**全产品仍 PARTIAL**：VLM 可靠性、正式预制作、M4 人工场景/权属/音色/最终观看试听与远端 CI 尚未关闭。
当前代码 CI 四项未启动，GitHub 注释明确为账户付款/额度原因；未修改付款设置。
见 [当前实机及失败证据](evidence/local-closeout-20261025/native-runtime-acceptance.json)、[逐项完成条件](LOCAL_ALL_PASS_REQUIREMENTS_20261026.md)。下文保留历轮结果。

前一续轮记录：完整后端 **474 passed，零 skipped/failures/errors**；隔离浏览器 **15 类
MOCK_VERIFIED，api_errors=[]**。安装恢复页面、项目过滤和跨项目迟到响应反例通过。
当前全产品仍 PARTIAL，真人恢复/模型安装、真实反例复验、M2/M4 等尚未完成；
见 [逐项需求核对](LOCAL_ALL_PASS_REQUIREMENTS_20261026.md) 和
[最新页面证据](evidence/local-closeout-20261025/installation-recovery-web.json)。下文保留历轮结果。

最新串行执行与本地审片补充见 [10-25 继续收尾第 7 节](LOCAL_FINAL_CLOSEOUT_20261025.md#7-继续推进真实本地审片与严格串行)。
历史真机轮结果保留。新 PDD8 超分样本及仅重跑机器 QC 通过；后续卸载异常已由真人完成指定调用对账，
cuBLAS 清理后的新真实 VLM 推理/卸载闭环通过，模型退出后 allocated/reserved 均为 0。
当前全产品状态仍为 PARTIAL。
本阶段最终工程复验为后端 427 项通过、Ruff/TypeScript/Vite/锁文件/Windows 启停通过；
浏览器回归仅为 MOCK_VERIFIED。命令、历次失败和最新统计见
[验证记录](evidence/local-closeout-20261025/verification.json)。
后续 SDK 隔离轮更新为后端 **437 项通过**；两个官方 Qwen SDK 在不同依赖目录导入成功，
仅验证导入和进程退出，没有加载语音模型。四个模型的固定文件安装提案已创建但未批准。
当前详细边界见 [SDK 轮记录](LOCAL_FINAL_CLOSEOUT_20261025.md#75-qwen-sdk-依赖隔离与模型进程退出)。
`68af13d` 的四项远端 CI 均为 SUCCESS。安装前又发现 Hugging Face CDN 跳转被旧下载器拒绝，
已补受包/计划审批快照约束的逐文件跳转策略；68 项定向回归和真实 142 字节元数据下载通过。
四个模型的新安装计划仍待真人批准，未下载权重、未运行语音模型；
见 [当前安装通道记录](LOCAL_FINAL_CLOSEOUT_20261025.md#77-固定模型安装的下载通道修复)。
本阶段完整后端更新为 **455 passed，0 skipped / failures / errors**，Ruff 与显式 PyPI 锁文件核验通过。
10-26 续：真人安装在首包权重连接时出现 TLS EOF，五份元数据缓存保留、完成模型包为零；
旧失败尝试保持待对账，没有自动重试。已补响应建立前有界连接重试与正式人工失败对账入口。
Web 模式不再写死 Ref2VA；隔离浏览器 13 类流程通过，T2VA/Ref2VA 请求被拦截验证，真实新增任务为零。
T2VA 官方原生 20 步 API 草稿结构核对通过，但缺失 INT8 Base 权重、未注册真实档案、未提交生成。
当前详情见 [续轮对账](LOCAL_FINAL_CLOSEOUT_20261025.md#78-安装-tls-失败后的正式对账与连接容错10-26-续)
和 [T2VA 准备](LOCAL_FINAL_CLOSEOUT_20261025.md#79-t2va-页面入口与官方草稿准备)。
随后真实 VLM 反例返回截断 JSON，被正确拒绝；释放核验通过，生产镜头/门禁保持不变。
已补输出预算、明确 finish_reason、响应哈希和确定响应失败分类；软件定向回归通过，
补丁后的真实反例尚未复验，旧截断调用需本人对账。服务离线后已核验旧进程退出并恢复 API/Web/ComfyUI，
验收 Worker 暂停启动；见 [反例实测与修复](LOCAL_FINAL_CLOSEOUT_20261025.md#710-真实-vlm-反例发现截断输出未将其算作通过)。
本续轮最终完整后端 **473 passed，0 skipped / failures / errors**；安装恢复定向 80 项、
审片响应与旧 E2E 定向 30 项通过，Ruff/前端构建/锁文件核验通过。
命令和当前真实边界见 [续轮验证记录](evidence/local-closeout-20261025/recovery-and-review.json)。
`0a35891` 的当前远端 CI 四项均因 GitHub 账户付款/额度问题未启动，已逐一读取 Check annotations
确认；不是当前代码的测试通过证据。本机 473 项与旧提交 CI 通过分别保留，
见 [账户阻断记录](evidence/local-closeout-20261025/ci-account-block.json)。

日期：2026-10-25（Asia/Shanghai）。范围：本机 Windows 11 工作站在本轮 V1.3 本地
H3 集成工作中被实际检查过的硬件、运行时、ComfyUI 实例、模型文件、工作流档案、
LLM 网关与落盘产物。本报告只写实际执行过的检查及其真实输出；没有执行的项目一律
标注为未覆盖。

关联文档：[真实 H3 验收报告](LOCAL_REAL_H3_ACCEPTANCE.md)、
[V1.3 收尾报告](LOCAL_V1.3_CLOSEOUT_REPORT.md)、[操作员指南](operator-guide.md)。

证据文件（本机本地路径，不进入 Git）：
`.local-acceptance/evidence/`（`acceptance-evidence.json`、`h3-model-hashes.json`、
`comfyui-process.json`、`comfyui-preflight.json`、`comfyui-object_info-full.json`、
`db-backup-inventory.json`、`v13-feature-verification.json/.md`）。

---

## 1. 结论摘要

- 本机是一台满足本地 H3 Ref2VA 推理条件的 Windows 11 + RTX 5080 工作站，本轮在其中
  完成了五次真实 ComfyUI 渲染（A、B 各两版，C 一版）、一次真实交付样片装配，并修复了
  三个在真机路径上才暴露的缺陷；收尾审片阶段又定位并修复了两个新缺陷（LLM 网关未下发
  JSON 契约、启动脚本记错了监听 pid），并复验了陈旧租约清扫（见
  [V1.3 收尾报告 7.2](LOCAL_V1.3_CLOSEOUT_REPORT.md#72-收尾审片阶段新发现并修复的缺陷)）。
- 工程回归、静态检查、前端构建在交付提交上全部通过。
- 唯一被本轮证伪的环境级结论是 `uv lock --check` 的失败原因：它与 `UV_DEFAULT_INDEX`
  导出与否无关，真正原因是本机全局 uv 配置指向阿里云镜像（详见 2.4）。
- 语义、风格、终审三道门与 B 的音频门在本轮**没有**模型覆盖，但由操作员
  2026-10-25 11:30（UTC）的逐项人工审阅关闭；三个真实镜头的最终状态是 `PASSED`、
  `quality_verification = manual_verified`。

## 2. 硬件与运行时

### 2.1 主机事实

| 项 | 实测值 | 取证方式 |
| --- | --- | --- |
| 操作系统 | Microsoft Windows 11 专业版 10.0.26200 64 位 | `Get-CimInstance Win32_OperatingSystem` |
| CPU | AMD Ryzen 7 9800X3D，8 核 16 线程 | `Get-CimInstance Win32_Processor` |
| 内存 | 31.66 GiB | `Get-CimInstance Win32_ComputerSystem` |
| GPU | NVIDIA GeForce RTX 5080，16303 MiB 显存，驱动 621.01 | `nvidia-smi` / `Win32_VideoController` |
| F 盘 | 总量 1907.7 GiB，剩余 534.6 GiB | `Get-Volume -DriveLetter F` |

```powershell
Get-CimInstance Win32_OperatingSystem | Select-Object Caption, Version, OSArchitecture
Get-CimInstance Win32_Processor | Select-Object Name, NumberOfCores, NumberOfLogicalProcessors
nvidia-smi --query-gpu=name,memory.total,memory.used,driver_version --format=csv,noheader
Get-Volume -DriveLetter F | Select-Object Size, SizeRemaining
```

实测输出：

```
OS: Microsoft Windows 11 专业版 10.0.26200 64 位
CPU: AMD Ryzen 7 9800X3D 8-Core Processor    cores=8 logical=16
RAM GiB: 31.66
NVIDIA GeForce RTX 5080, 16303 MiB, 1497 MiB, 621.01
F: SizeGiB=1907.7 FreeGiB=534.6
```

### 2.2 工具链

| 工具 | 版本 | 备注 |
| --- | --- | --- |
| Shine Python | CPython 3.12.13 | uv 管理的 `backend/.venv` |
| ComfyUI Python | CPython 3.13.11 | ComfyUI 自带 `python_embeded` |
| ComfyUI torch | 2.10.0+cu130 | 随 ComfyUI 内置 |
| FFmpeg / FFprobe | 8.1 full build (gyan.dev) | 在 PATH 上 |
| Node / npm | v24.14.1 / 11.14.1 | 前端构建 |
| uv | 0.11.2 | 依赖与锁文件 |

```powershell
& 'backend\.venv\Scripts\python.exe' --version      # Python 3.12.13
ffprobe -version | Select-Object -First 1             # ffprobe version 8.1-full_build-www.gyan.dev
node --version; npm --version; uv --version
```

两套 Python 环境完全分离，本轮没有互相污染：Shine 只用自己的 `backend/.venv`，
ComfyUI 只用自己的 `python_embeded`。

### 2.3 仓库与提交状态

| 项 | 值 |
| --- | --- |
| 仓库 | `anjingdtl/shine-video-studio`（private） |
| 本轮基线 | `63929f49c98e205776da5e2287cfe368cf87de75`（与 `origin/main` 同一 SHA） |
| 本轮提交 | `fc75693d9aabb44cee92ca40eb97838e1ebd674c`，分支 `feature/v13-local-h3-integration`，2026-10-25 17:49:36 +08:00 |
| 后续提交 | `2df8aff9f62209b6202bbd839fd914989499d5b2`（2026-10-25 18:01:13）：`backend/shine/llm/gateway.py` 修复 `LLM_FORMAT` 根因，并加入本轮四份文档 |
| 收尾审片轮证据快照 | `8bf956fc527c9865fdbf779409d8c1d683064f64`（`.local-acceptance/evidence/acceptance-evidence.json` 记录的 HEAD，采集于 2026-10-25 11:31:09 UTC） |
| 工作区 | 提交前后均干净 |

```powershell
git log --oneline -3
git branch -vv
git status --short
```

`2df8aff` 改的是 LLM 网关：以前只把 schema 交给服务商做校验、没下发给模型，而剧组
指令要求模型"只返回所给的 JSON 契约"——契约却从未被提供。修复把 schema 追加到请求
末尾。详见 [V1.3 收尾报告 5.1](LOCAL_V1.3_CLOSEOUT_REPORT.md#51-llm_format-的根因与修复本轮后续提交)。

本轮改动相对基线共 17 个文件、+3004/−40 行：

| 类别 | 文件 |
| --- | --- |
| 交付装配（新增） | `backend/shine/media/assembly.py`、`backend/shine/api_delivery.py` |
| 三个真机缺陷修复 | `backend/shine/worker.py`、`backend/shine/comfy/client.py` |
| 工作流档案生成器 | `scripts/build_h3_workflow_profiles.py`、`config/workflows/ref2va-pdd8-*.json`（3 个） |
| 进程管理 | `scripts/start_shine.ps1`、`scripts/stop_shine.py` |
| 回归测试 | `backend/tests/test_comfy_completion_window.py`、`test_gpu_release_confirmation.py`、`test_delivery_assembly.py`、`backend/tests/fake_http.py` |
| 收尾审片阶段 | `backend/shine/llm/gateway.py`（把 JSON 契约下发模型，提交 `2df8aff`）、`scripts/start_shine.ps1`（拒绝占用端口 + 记录 `listener_pid`）、`scripts/stop_shine.py`（结束监听者 + 端口仍被占用告警） |
| 其他 | `backend/shine/main.py`（路由挂载）、`scripts/register_ref2va_profiles.py`、`.gitignore` |

`git fetch` 在本轮因网络连接重置失败，远端状态无法刷新。写文档时用只读的
`git ls-remote` 重新核对过：远端 `refs/heads/main` 仍是
`63929f49c98e205776da5e2287cfe368cf87de75`，与本地 `origin/main` 同一 SHA，远端 main
没有移动；本轮提交 `fc75693` 与后续的 `2df8aff` 都在本地分支
`feature/v13-local-h3-integration` 上，尚未推送。因此本轮不对远端 CI 结果做任何断言。

```powershell
git ls-remote origin refs/heads/main   # 63929f49…（只读，不改本地引用）
git rev-parse origin/main               # 63929f49…
git rev-parse HEAD                      # 2df8aff…
```

### 2.4 依赖锁文件（本轮实测纠正了先前的归因）

本机全局 uv 配置把默认索引指向阿里云镜像：

```toml
# C:\Users\Administrator\AppData\Roaming\uv\uv.toml
index-url = "https://mirrors.aliyun.com/pypi/simple/"
```

而 `backend/uv.lock` 的来源是 `https://pypi.org/simple`。实测：

```powershell
# 默认（走阿里云镜像）
uv lock --check --project backend
# The lockfile at `uv.lock` needs to be updated, but `--check` was provided.
# exit=1

# 覆盖为 pypi.org 后
$env:UV_DEFAULT_INDEX = 'https://pypi.org/simple'
uv lock --check --project backend
# Resolved 42 packages in 1ms
# exit=0
```

`uv lock --check -v` 的关键行直接指出了原因：

```
DEBUG Resolving despite existing lockfile due to missing remote index: `alembic` `1.16.2` from `https://pypi.org/simple`
```

结论：失败与 `UV_DEFAULT_INDEX` **是否导出无关**，与镜像指向有关。覆盖索引后退出码为
0，说明锁文件本身有效、没有被改动。这是本机索引/镜像配置问题，不是仓库缺陷。报告中
不再沿用"导出该变量才会失败"的旧说法。

## 3. 工程回归（本轮在交付提交上亲自重跑）

```powershell
& 'backend\.venv\Scripts\python.exe' -m pytest backend/tests -q
& 'backend\.venv\Scripts\ruff.exe' check backend/shine backend/alembic backend/tests
npm --prefix frontend run build
npm --prefix frontend audit
```

| 检查 | 结果 | 类别 |
| --- | --- | --- |
| 后端全量测试 | exit 0，**389 项全部通过**（36 个测试文件，基线 375 + 本轮新增 14） | `TESTED_OFFLINE` |
| 三个新增回归文件 | 14 项全通过（2 + 6 + 6） | `TESTED_OFFLINE` |
| Ruff | `All checks passed!`，exit 0 | `TESTED_OFFLINE` |
| 前端构建 | `tsc -b && vite build`，Vite 6.4.5，1590 modules，exit 0 | `TESTED_OFFLINE` |
| `npm audit` | `found 0 vulnerabilities`，exit 0 | `TESTED_OFFLINE` |

前端构建实测输出片段：

```
vite v6.4.5 building for production...
✓ 1590 modules transformed.
dist/assets/index-DVA6zgt_.js   289.69 kB │ gzip: 89.26 kB
✓ built in 1.06s
```

## 4. ComfyUI 实例

### 4.1 实例身份

| 项 | 值 | 来源 |
| --- | --- | --- |
| 安装根目录 | `F:\AIGC2026\ComfyUI_Mie_2026_V8.0_Base` | `comfyui-preflight.json` |
| ComfyUI 版本 | 0.39.0 | 运行日志 / `/system_stats` |
| ComfyUI commit | `b0b743566f65daafc423b4fea8a2fbda94b3384a` | `comfyui-preflight.json` |
| git 工作区 | 干净（`comfyui_git_dirty: 0`） | `comfyui-preflight.json` |
| 监听地址 | `127.0.0.1:8188`（仅回环） | `comfyui-process.json` |
| 进程 PID / 启动时间 | 10184 / 2026-10-25T17:02:15+08:00 | `comfyui-process.json` |
| 启动前状态 | 监听器为空、无 ComfyUI 进程、队列与历史为空 | `comfyui-preflight.json` |

本轮**没有**升级、修补、清理或停止该安装的任何内容；只按操作员已验证的参数另起了一个
实例。

启动参数（`comfyui-process.json` 的 `argv`，Python 内嵌解释器 + ComfyUI 入口）：

```
F:\AIGC2026\ComfyUI_Mie_2026_V8.0_Base\python_embeded\python.exe
  -X utf8 -s F:\AIGC2026\ComfyUI_Mie_2026_V8.0_Base\ComfyUI\main.py
  --windows-standalone-build
  --listen 127.0.0.1 --port 8188
  --disable-async-offload --disable-pinned-memory
  --reserve-vram 1 --cache-ram 6 10 --fast fp16_accumulation
```

这是操作员既有的 INT8 ConvRot 配置（profile 标识 `int8_convrot_int8five`）。

### 4.2 运行时自述

```
[INFO] Total VRAM 16276 MB, total RAM 32418 MB
[INFO] pytorch version: 2.10.0+cu130
[INFO] Device: cuda:0 NVIDIA GeForce RTX 5080 : cudaMallocAsync
[INFO] Python version: 3.13.11 (tags/v3.13.11:6278944, Dec  5 2025, 16:26:58) [MSC v.1944 64 bit (AMD64)]
[INFO] ComfyUI version: 0.39.0
```

### 4.3 自定义节点

运行日志 `comfyui-stderr.log` 记录本轮实际加载的节点，其中与 H3 链路直接相关的是：

- 核心 `comfy_extras/nodes_minimax_h3.py`
- `ComfyUI-KJNodes`、`comfyui-videohelpersuite`、`ComfyMath`
- `comfyui-SolAttn_triton`
- `ComfyUI-MiniMax-H3-PDD-Acc`
- `comfyui-dev-utils`
- 操作员自建 `custom_nodes/shine_h3_sampling_mode.py`（只把枚举转成 PDD nfe/enabled，
  不改模型、采样或缓存）

其余为该安装中原有的节点包，本轮未改动。

### 4.4 节点类型数量

```powershell
Invoke-WebRequest 'http://127.0.0.1:8188/object_info' -UseBasicParsing
```

实测：**3091** 个节点类型，快照存于
`.local-acceptance/evidence/comfyui-object_info-full.json`（7.4 MB）。

写文档时又用 Python 直接取了一次实时接口（未改动实例）：

```python
import json, urllib.request
oi = json.load(urllib.request.urlopen('http://127.0.0.1:8188/object_info', timeout=120))
print(len(oi))                     # 3091
ss = json.load(urllib.request.urlopen('http://127.0.0.1:8188/system_stats', timeout=60))
print(ss['system']['comfyui_version'], ss['system']['python_version'], ss['system']['pytorch_version'])
```

```
object_info node classes: 3091
comfyui_version: 0.39.0
python: 3.13.11 (tags/v3.13.11:6278944, Dec  5 2025, 16:26:58) [MSC v.1944 64 bit (AMD64)]
pytorch: 2.10.0+cu130
device: cuda:0 NVIDIA GeForce RTX 5080 : cudaMallocAsync vram_total= 17066360832 vram_free= 15560602112 torch_vram_total= 67108864 torch_vram_free= 49017344
queue running: 0 pending: 0
```

### 4.5 0.39 的显存口径（与第 8.2 节的缺陷直接相关）

上面这行实时读数就是活证据：`torch_vram_total = 67,108,864`、
`torch_vram_free = 49,017,344`，**空闲部分比总量小**，差值
18,091,520 字节（17.25 MiB）。这说明 0.39 报的是"分配器已保留池"与"已保留但未分配的
部分"，两者不可能满足原先的 `total <= free` 判定。这条读数同时也是 GPU 释放确认修复
（`MAX_LIVE_TENSOR_BYTES = 512 MiB`）在本机的实测依据：差值远低于阈值，说明渲染端
当前确实没有驻留模型。

## 5. 模型文件

来源：`.local-acceptance/evidence/h3-model-hashes.json`（SHA-256 与字节数）。

| 路径（相对 ComfyUI 根） | 字节 | SHA-256 |
| --- | --- | --- |
| `models/diffusion_models/Minimax-H3/minimax_h3_ref2va_pruned_int8_convrot.safetensors` | 20,970,379,616 | `9255f52b6677845ad238f20dfaafa94727053694127ab7f255c048f0f9365779` |
| `models/clip/qwen3vl_32b_minimax_h3_nvfp4_awq.safetensors` | 15,687,142,551 | `35a88d51044231fe332301d7a62aa81e3f2cba62febeb446e2c1e3e0ef76f2c6` |
| `models/vae/MiniMax-H3(1)/minimax_h3_video_vae_fp16.safetensors` | 5,207,808,496 | `7c1f131492e7eddacaac9069a61b81bdd39de5cc96561e677c5eab1cdce5e522` |
| `models/vae/MiniMax-H3(1)/minimax_h3_audio_vae_fp32.safetensors` | 605,254,808 | `8e505d95dd1561d47abd43d4238fd40d9bb1ae9e147ed0a4cba778d76ae4db48` |
| `models/clip/qwen3vl_32b_minimax_h3_int8_convrot.safetensors` | 27,141,342,152 | `bc2ced0fbea64757fa9acddccfc0b3f4819d1dcf1da6c124d690d368be283923` |
| `models/vae/MiniMax-H3(1)/minimax_h3_video_vae_int8_convrot.safetensors` | 3,171,670,912 | `9bb2d96f218c76babd85e0611b85ca8fb330a90546c01a0005e8a58a59593410` |

实际被本轮渲染加载的是前四个（INT8 ConvRot 主干 + NVFP4 文本编码器 + fp16 视频 VAE +
fp32 音频 VAE），日志中对应：

```
[INFO] Model storage policy: ... models/vae/MiniMax-H3(1)/minimax_h3_audio_vae_fp32.safetensors
[INFO] VAE load device: cuda:0, offload device: cpu, dtype: torch.float32
[INFO] Model storage policy: ... models/vae/MiniMax-H3(1)/minimax_h3_video_vae_fp16.safetensors
[INFO] Model storage policy: ... models/clip/qwen3vl_32b_minimax_h3_nvfp4_awq.safetensors
[INFO] Model storage policy: ... models/diffusion_models/Minimax-H3/minimax_h3_ref2va_pruned_int8_convrot.safetensors
```

PDD 加速文件 `models/pdd_acc/MiniMax-H3-Ref2VA-Acc-8Step.safetensors` 由插件在运行时加载，
日志为：

```
[INFO] MiniMaxH3PDDAccApply: MiniMax-H3-Ref2VA-Acc-8Step.safetensors (original) steps=8 blocks=4,4,4,4,4,4,4,4, 258 lora modules, heads fused
```

该文件**不在** `h3-model-hashes.json` 中，因此本报告不给出它的 SHA-256，只保留运行日志
里的文件名与参数行。模型权重只留在这台机器上，没有进入 Git。

## 6. 工作流档案与实时校验

五个档案全部登记在验收库中，且每一个都用**实时** `object_info` 校验过，结果统一为
`{"missing_nodes": [], "missing_inputs": []}`：

| 档案 ID | 名称 | 图 SHA-256 | 校验时间 (UTC) |
| --- | --- | --- | --- |
| `3a307aba-15fd-4edb-9ea8-50ce08def73a` | `local-h3-ref2va-2img` | `6f7a5fb19c548ca945f8a2169e05c836d7c06f03466cf2b893b3be599505d112` | 2026-10-25T09:14:08Z |
| `ddfa53df-3af7-494b-a1de-43c74344bea1` | `local-h3-ref2va-2img-1aud` | `7d86f7b6d994b0b38cc379a4fe0268f68776c4a43e80fb0f7651a3005f57dcd6` | 2026-10-25T09:14:08Z |
| `18e2f8d3-ca1d-4f0d-83ae-0bffc1b4b5f2` | `local-h3-ref2va-pdd8-2img` | `86a7624e6eaeccd3a3c54cf5024317698cac64cbe083ed10dd158bcaa1e6eaa0` | 2026-10-25T09:14:09Z |
| `01510286-83e7-4bde-ac2b-09332aeb1435` | `local-h3-ref2va-pdd8-2img-1aud` | `ea979fba2d3ad02493ee28d7126a624ca8afde06b1895a0e81a6bd50a193ca64` | 2026-10-25T09:14:09Z |
| `e8aa2bb8-6f11-478b-ad4e-9e05a478b1fa` | `local-h3-ref2va-pdd8-upscale-2img` | `ca3f6f70611b13923f682ce965504666b948082af8df61aed93571eb97353490` | 2026-10-25T09:14:10Z |

前两个是 V1.2 的历史档案（图 hash 未变）；后三个是本轮新增，由
`scripts/build_h3_workflow_profiles.py` 可重复生成：

```powershell
uv run --project backend python scripts/build_h3_workflow_profiles.py
uv run --project backend python scripts/build_h3_workflow_profiles.py --validate http://127.0.0.1:8188
```

PDD8 档案（`config/workflows/ref2va-pdd8-2img.json`，25 个节点）的固定参数，逐节点实测：

| 节点 | 关键输入 |
| --- | --- |
| `MiniMaxH3ReferenceToVideo` | `width: 1344`、`height: 768`（模型原生） |
| `LoraLoaderModelOnly` | `h3-realism-people-t2v-i2v-r2v.safetensors`，`strength_model: 0.65` |
| `MiniMaxH3SigmaShift` | `shift_video: 12.0`、`shift_audio: 3.0` |
| `SolAttnPatch` | `tau 1.5`、`start_percent 0.2`、`end_percent 0.9` |
| `BasicScheduler` | `scheduler: simple`、`steps: 20` |
| `ShineH3SamplingMode` | `mode: "PDD 8 steps"` |
| `MiniMaxH3PDDAccApply` | `pdd_file: MiniMax-H3-Ref2VA-Acc-8Step.safetensors`、`lora_strength 1.0`、`head_strength 1.0` |
| `ComfyMathExpression` | `max(5, round(a * 24)) + (5 - (max(5, round(a * 24)) % 17)) % 17`（H3 的 17k+5 帧格点） |
| `KSamplerSelect` | `sampler_name: euler` |
| `RandomNoise` | `noise_seed: 77` |
| `CreateVideo` | `fps: 24.0`、`codec: h264` |
| `LoadImage` × 2 | `shine-subject.png`、`shine-picture.png` —— 恰好一个 subject 槽与一个 picture 槽 |

采样步数由 `ShineH3SamplingMode` 把枚举转成 PDD 的 nfe/enabled，PDD 权重是
`MiniMax-H3-Ref2VA-Acc-8Step.safetensors`；基础调度器仍出 20 步 sigma，PDD 节点据此
分组成 8 步。超分档案 `ref2va-pdd8-upscale-2img.json`（29 个节点）在其上追加
`MinimaxH3LatentUpscaler3DRefineHandoff`（`scale: 1.5`、`cfg: 1.0`）与
`MiniMaxH3PDDAccScheduler`（`nfe: "8"`、`denoise: 0.25`）。

每个档案都声明了 `parameter_map`（`positive_prompt`/`duration`/`seed`/
`reference_subject_1`/`reference_picture_1`/`reference_audio_1`）与 `media_transfers`
（image 走 `/upload/image`；audio 额外声明 `VHS_LoadAudioUpload`）。未映射的槽位会
以 `PENDING_WORKFLOW_MAPPING` 阻断渲染，不会被静默丢弃。

档案身份中还固定记录了连续性边界：

```
"continuity_modes": ["SOFT_REFERENCE_CONTINUITY"],
"hard_continuity": "PENDING_HARD_CONTINUITY_TEST",
"video_reference": "SUPPORTED_NODE_UNVERIFIED_TRANSFER"
```

## 7. LLM 网关与密钥边界

| 项 | 值 |
| --- | --- |
| 档案 ID | `ffbf39c1-1a30-459b-8e4e-cca7ba10b3cd` |
| 名称 / 类型 | `minimax-openai-compatible` / `openai_compatible` |
| 端点 | `https://api.minimax.cn/v1`（操作员授权的本地测试端点） |
| 模型 | `MiniMax-M3.1-flash` |
| 密钥存储 | Windows DPAPI 秘密存储（`secret_storage: dpapi`） |
| 连接测试 | `{"ok": true, "message": "Connection succeeded"}` |

能力探测返回 `available: true`，并列出服务商模型表：
`MiniMax-M3.1-Flash`、`MiniMax-M3`、`MiniMax-M2.7`、`MiniMax-M2.5`、`MiniMax-M2.1`、
`MiniMax-M2` 等。探测同时标注 `model_listed: false` 与
`"warning": "The configured model was not listed by the provider"` —— 配置里写的是小写
拼写 `MiniMax-M3.1-flash`，服务商返回的是 `MiniMax-M3.1-Flash`。连接本身成功。

12 个 Agent 角色全部路由到该档案：`scriptwriter`、`director`、`cinematographer`、
`editor`、`continuity_supervisor`、`sound_director`、`producer`、`comfyops`、
`script_planner`、`shot_planner`、`repair_planner`、`qc_reviewer`。

**密钥处理声明**：本轮创建的密钥只写入 Windows DPAPI 秘密存储，API 从不返回它，
它没有进入 SQLite、没有进入 Git、没有进入日志、没有进入前端响应。本文档不包含、
不引用、不复述任何密钥内容。

**JSON 契约下发**：剧组的角色指令要求模型"只返回所给的 JSON 契约"。提交 `2df8aff`
之后，`LLMGateway.complete_for_role` 会把该角色的 schema 作为请求的**最后一段**
追加给模型 —— 服务商收到的请求里因此确实带着这份契约。对授权的 MiniMax 端点实跑，
`scriptwriter` 返回 HTTP **201** 并产出有效的 `ScriptBreakdown`。修复前的失败是
`LLM_FORMAT`（HTTP 422），根因见
[V1.3 收尾报告 5.1](LOCAL_V1.3_CLOSEOUT_REPORT.md#51-llm_format-的根因与修复本轮后续提交)。

## 8. 数据目录与数据库备份

本轮使用隔离的验收数据目录 `.local-acceptance/h3run/`，原 `.local-data-v12` 只读。
数据库用 SQLite 在线备份 API（WAL 感知）复制，媒体复制进隔离目录，因此没有任何写回。

| 数据库 | 来源 | 备份 | 完整性 |
| --- | --- | --- | --- |
| V1.2 验收库 | `.local-data-v12/shine.db` | `.local-acceptance/db/v12_acceptance-backup.db`（483,328 B） | `ok` |
| V1.2.1 审计副本 | `.local-data-v121-audit/v12-upgrade-copy.db` | `.local-acceptance/db/v121_audit-backup.db`（483,328 B） | `ok` |
| 默认本地库 | `.local-data/shine.db` | `.local-acceptance/db/default_local-backup.db`（4,096 B） | `ok` |

验收库本身：`.local-acceptance/h3run/shine.db`，Alembic head `f130a0020001`。

## 9. 落盘产物校验

验收库中全部 12 个媒体产物的落盘文件 SHA-256 与登记值逐一相等（`hash_matches: true`），
字节数也相符：

| 产物 | 字节 | SHA-256 与登记值一致 |
| --- | --- | --- |
| V1.2 A（480x864） | 2,540,146 | 是 |
| V1.2 B（480x864） | 1,228,496 | 是 |
| V1.2 C（480x864） | 1,899,829 | 是 |
| 本轮 Shot A 第一版（1344x768） | 6,660,241 | 是 |
| 本轮 Shot B 第一版（1344x768） | 4,589,710 | 是 |
| 本轮 Shot C（1344x768） | 4,414,119 | 是 |
| 本轮 Shot A 当前版（1344x768） | 4,249,108 | 是 |
| 本轮 Shot B 当前版（1344x768） | 3,390,919 | 是 |
| 交付样片（错误顺序版） | 14,085,974 | 是 |
| 交付样片（A→B→C，第一版来源） | 14,085,974 | 是 |
| 交付样片（A→B→C，重渲染来源，`pending_review`） | 10,893,258 | 是 |
| 交付样片（最终，`all_passed`） | 10,893,258 | 是 |

最后两条登记的是**同一个内容寻址文件**（SHA-256 `4ce49654…`），区别只在来源 job 的
状态：第一条装配时三个来源 job 尚未被审片签署，第二条是审片之后重装的。已通过版本
的镜头产物与先前的成片都没有被覆盖。

## 10. 本轮未覆盖或受限的环境项

| 项 | 状态 | 说明 |
| --- | --- | --- |
| 语义 / 风格 / 终审门的**模型**覆盖 | `BLOCKED / PENDING` | 没有模型覆盖；这三道门由操作员的人工逐项审阅关闭；`allow_remote_review = 0`，没有任何图像/音频/视频离开本机 |
| 自动 ASR | `BLOCKED / PENDING` | 未配置；音频流存在并可解码，中文台词未逐字**机器**核验（由人工试听逐字核对） |
| 严格首尾帧锚定 | `PENDING_HARD_CONTINUITY_TEST` | 未实现为可放行的连续性 |
| 视频参考真实传输 | `SUPPORTED_NODE_UNVERIFIED_TRANSFER` | 节点存在，传输未验证 |
| 多模型 GPU 争用 | 未覆盖 | 本轮只跑了一个本地模型家族 |
| ComfyUI 远程绑定 | 未启用 | `SHINE_ALLOW_REMOTE_COMFYUI` 未设置，渲染端始终是回环地址 |
| 远端 CI | 未确认 | `git fetch` 在本轮失败；已用只读 `git ls-remote` 核对远端 main 未移动，但仍不对远端 CI 结果做断言 |

## 11. 结论分类

| 结论 | 类别 |
| --- | --- |
| 本机硬件、磁盘、工具链满足本地 H3 Ref2VA 推理条件，实测值如第 2 节所列 | `VERIFIED_LOCAL_ENV` |
| ComfyUI 0.39.0 实例在 `127.0.0.1:8188` 上以操作员既有 INT8 ConvRot 参数启动，安装未被改动 | `VERIFIED_LOCAL_ENV` |
| 实时 `object_info` 含 3091 个节点类型，快照已留档 | `VERIFIED_LOCAL_ENV` |
| 实际加载的 4 个 H3 模型文件，以及另外 2 个未使用的 INT8 变体，SHA-256 与字节数均已留档（PDD 加速文件未留档，见第 5 节） | `VERIFIED_LOCAL_ENV` |
| 五个工作流档案全部用实时 `object_info` 校验通过（无缺失节点/输入） | `VERIFIED_LOCAL_ENV` |
| `uv lock --check` 失败源于本机全局 uv 镜像配置，覆盖索引后 exit 0；锁文件本身有效 | `VERIFIED_LOCAL_ENV` |
| 后端全量测试、Ruff、前端构建、`npm audit` 在交付提交上通过 | `TESTED_OFFLINE` |
| 交付装配能力、三个真机缺陷修复、进程清单启停脚本 | `IMPLEMENTED` / `TESTED_OFFLINE` |
| 本轮三个真实镜头为真实 ComfyUI 产物 | `VERIFIED_REAL_H3` |
| 交付样片为真实 FFmpeg 装配产物，可完整解码 | `VERIFIED_REAL_H3` |
| 操作员 `shine-owner` 逐项审阅并签署三个镜头，语义 / 风格 / 终审门与 B 的音频门由**人工**关闭 | `VERIFIED_MANUAL` |
| 语义 / 风格 / 终审门与音频门**没有模型覆盖**，只由人工关闭 | `BLOCKED / PENDING` |
| 严格连续性、视频参考传输、多模型 GPU 争用 | `BLOCKED / PENDING` |


## 2026-10-26 后续实机结果与场景审片修复

- 七个固定模型包的 69 个文件、36,197,624,599 字节已实际校验。T2VA Base INT8、原生 Qwen3-VL 与 Whisper 的受控安装完成；包文件校验状态与运行能力分别留证，不手改包验证等级。
- 原生 VLM 正例通过、硬反例失败及 ASR 八字逐字一致，三个新调用均真实完成、租约释放。VLM/ASR 能力已通过认证委托发布（5c49f760… / 31db7d50…）；项目的 PreProductionPlan 尚未冻结。旧 e6d718c1… 已正式 ABANDONED，不重放旧调用。
- 新 T2VA Job `90370ed1-595c-499f-ab3c-02f2fa8b430a` 经编译器、真实 Profile、Worker 和正常审片：20 步，ComfyUI 墙钟 486.64 秒，1344×768/24fps/5.167 秒，3,904,601 字节，完整解码通过。它的空景/静态广角要求与原冻结风格冲突，且出现车辆/标识；style/final 失败、REPAIR_PENDING，旧产物和判定保留。此处不是合格影片，也不是 M4 验收。
- 实测暴露空景语义门错误地记为不适用，以及可选门已观察到的硬错误/不确定/Mock 来源未完整参与终审。六个反例先失败再修复；启用语义 QC 时所有故事场景都要求审片，实际观察到的错误不能因可选配置被隐藏。
- 审片提示明确 CUT 指镜头之间的转场，不能要求单镜头内部必须剪切；未降低置信阈值或过滤已返回的失败。新实机复核另留记录，不覆盖旧审片。
- 当前完整后端 **595 项，零失败/错误/跳过**，定向质量/离线 E2E/V1.3/评审回归 **53 passed**，Ruff 通过。前端未在本步改动，沿用当前提交的 TypeScript/Vite 与 17 类 Mock 浏览器通过证据。
- GitHub 当前 4eba47f 的四项 CI 均未启动：账户付款/额度限制；没有更改账单或将本机测试冒充远端 CI。人工场景、权属、音色冻结、最终观看试听与正式项目预制作仍待真实证据，整体 PARTIAL。
- 可复核机器摘要：[实机安装、校准、发布及 T2VA](evidence/local-closeout-20261025/native-runtime-acceptance.json)。自主授权记录为 user-delegated-codex，不冒充人工在场或内容批准。


### 单镜头审片的转场语义输入

真实 CUT 复核第一次仍误判内部剪切，说明仅补充文字说明不足；该失败完整保留，未把它记为修复通过。现在仅在发送给单镜头审片器的视图中，将机器枚举 cut 表达为“独立镜头、无前镜头帧约束”，不改原 ShotSpec、编译器、引用绑定或历史记录；last_frame 等其余约束不变。没有角色/资产绑定本身也不构成身份或服装违规，已有必需身份缺失仍需披露。最新提示变更定向 55 项及 Ruff 通过；上面的全量 595 项对应前一提交。后续真实结果单独记账。


## 真实审片对抗性复核与资格撤下（2026-10-26）

- 第二个独立 T2VA Job `41f20ac6-6d8b-4912-8f8b-60095c709410`、Attempt `ab1ea473-e2b1-4ed5-97d0-0b95aa0934b7`、Comfy prompt `2e0667f9-965f-4f4f-887d-d638911147b1`：1344×768、24fps、5.167 秒、1,554,281 字节，SHA-256 `a176267078f107bd8a39f4678a57f6445dc2ab5c12065bc0e4108d0fff48224a`。技术门/完整解码通过，真实 ASR 已运行；semantic/final 失败、style 需复核，仍 REPAIR_PENDING，未自动重投。
- 真实审片把没有指定人物身份或服装目标的匿名虚构人物判为必须没有五官/衣物，并错误解释单长镜头/转场。调整规范说明和 system/user 指令边界后，现有视频的两个独立新校准调用仍复现误判；这不是已解决的模型质量问题。旧风格冲突视频的一次复核也曾错误通过，记录保留，未写回生产五道门。
- 新代码明确单镜头与跨镜头语义、缺少绑定不等于禁止面部/服装、冻结 Style 独立检查；没有降低阈值、删失败 findings、改冻结风格、修改历史 ShotSpec 或人造人工批准。正确输入契约不能证明小模型已经服从它。
- VLM Workflow `5c49f760-5132-4cf9-8fbb-1f9e243bb4ff` 经 workflow_deprecate 单次快照授权正式撤下，不能再被真实 Factory 匹配。ASR `31db7d50…` 保持 PRODUCTION_APPROVED；历史真实调用、先前发布事实、全部旧 Job/Artifact/门禁均保留。正式预制作因缺少合格 VLM 保持阻断。
- 最终所有租约 RELEASED，审片服务 loaded/asr_loaded=false，allocated/reserved=0，ComfyUI queue_running/queue_pending 为空。操作发生于隔离验收副本；未改原 ComfyUI 模型/工作流文件，本次撤下未再安装其他模型或换远端服务绕过质量问题。
- 当前限制需要更可靠的本机审片模型或更完整、能通过这些固定反例的校准；不得用基础恐龙反例的通过覆盖这里的失败。正式人物、权利与声音批准及 M4 人工观看边界仍独立存在。

最终提示范围修订后再次运行完整后端：595 项、零 failures/errors/skipped，231.840 秒；Ruff 与 git diff --check 通过。真实媒体质量失败及 VLM 撤下资格不因软件回归通过而改变。


## 官方较大 VLM 候选与分片模型支持（2026-10-26）

- 已在已登录 GitHub 账户页面实际核对：Actions 2,000/2,000 分钟、0.5/0.5 GB，免费额度用尽，超额预算为零并显示 Usage blocked；未改变预算、支付信息或仓库可见性，也没有为此重跑无法启动的 CI。旧 Qwen 恢复脚本没有重放。
- 为继续解决审片质量问题，准备官方 Qwen3-VL-8B-Instruct-FP8 固定 9cdc6310… 候选，13 文件 10,605,572,552 字节。显存 14,000 MiB / RAM 22,000 MiB 均为 ESTIMATED，不是实测值；当前尚未开始权重安装或新模型推理。
- 本机执行器补齐严格分片索引校验：索引和每个分片全部纳入哈希身份链，不允许未声明权重、缺片、多余分片或单文件/分片混用。九个反例先失败，修复后定向 44 项通过。完整后端 **604 项、零失败/错误/跳过，232.678 秒**；Ruff 通过。未改前端，沿用已记录的构建和 17 类 Mock 页面证据。
- 上游词表 GET 超时后，以原有相同词表重现上游 JSON 编码；仅在 4,957,462 字节和固定上游 Git blob ID 完全匹配后用于精确缓存。没有修改词表含义、权重或安装范围。其余小文件同样核对固定 Git blob ID，权重由上游 LFS SHA-256 固定。
- 后续只允许串行安装和对抗性校准；更换适配器运行时也要求 ASR 重新校准/发布，不能继承旧指纹。旧 VLM 仍 DEPRECATED，项目预制作没有冻结，完整产品仍 PARTIAL。
- 机器记录：[分片与候选资格记录](evidence/local-closeout-20261025/sharded-review-qualification.json)。


## 较大原生模型预算与校准租约一致性（2026-10-26）

- 新增 7 个反例先复现失败：原生 binding 拒绝合法分片；固定小预算无法表达较大模型；较小的旧租约可能被用于较大的新能力；已收到模型结果后活动租约预算变化未被拒绝。
- 原生包和工作流预算现在严格一致，保留 VLM 8,000 / ASR 4,000 MiB 最低估算和 16,384 MiB 上限。当前角色的有效 STATIC_VALIDATED/REAL_TESTED/PRODUCTION_APPROVED 声明为实际配置的本地审片器提供保守准入预算；静态声明仅供校准准入，不意味着生产批准。
- 真实执行证据采纳要求已释放租约覆盖工作流预算，生产审片前后也检查活动租约是否仍足额。收到但因后置检查被拒绝的结果摘要保留，不触发重投。分片格式仍由执行器对索引和全部实际文件逐字节复验。
- 完整后端 **611 项，零失败/错误/跳过**；Ruff 与差异空白检查通过。定向验证通过，不改动下载器、前端、数据库结构或已有数据。新的预算代码在受控下载期间完成软件验证，后续服务重启及实机校准使用新版本，不能把旧适配器指纹继承给新运行时。
- 固定官方候选已登记并批准隔离计划 `1b2b9d23-d884-4d4d-bbdd-3450a1585ca8`，唯一安装尝试 `2663f819-f427-41b5-b7a0-6ccf470a7e2b` 仍 SUBMIT_INTENT。累计接收约 1.92 GB，仅为下载进展，未完成文件校验或推理。不得重放旧安装或未知审片。
- 本次初始服务重启已改变适配器运行时指纹。旧 ASR 发布记录保留，但不再据其旧指纹认定当前可用；下载完成后将正式弃用旧运行时版本并重新实际校准、发布。旧 4B VLM 已 DEPRECATED，新 8B 尚无生产资格，项目预制作仍阻断。
- 已准备只停本任务持有的 ComfyUI PID+OS birth 的串行释放/恢复工具；执行前必须完成安装、核验空队列/租约、保存已有两次完整 History 和校验归档。归档不冒充实时能力证据，不重放历史渲染，不改原模型/工作流文件。当前尚未执行停机。


## 校准输入设计复核（2026-10-26）

重新逐字段核对发现，旧 A (`7a526327…`) 的 Story/camera 是行走、中景、缓慢推进，而其冻结 Style 文案是坐姿、中近景、轻微手持。这个未消解的输入差异使它不适合作为“严格符合该冻结风格”的无争议正例。旧模型实际返回 passed 的事实和旧证据仍保留，但不能据此评价较大模型必须通过；此前 VLM 资格已经撤下，未恢复。

尚未执行的新模型校准设计已纠正：采用输入与冻结风格一致的匿名虚构人物 T2VA 样本作为正向候选；同一视频的内存评估规格加入完全遮挡面部的白色面具要求作为明确服装反例；空街和旧 A 行走请求分别作为冻结风格冲突观察。所有原 Job/ShotSpec/Style/门禁保持原样，未生成新人物主资产或批准新道具。正向候选若失败，要先区分真实生成缺陷和评审误判，不能为了凑正例修改冻结风格或删除 findings。最终校准和真实影片验收仍未完成。


## 本机加载闭包与重复 CI 修复（2026-10-26）

- 实际检查隔离 Transformers 加载器确认：它优先选择单文件权重，再选择分片索引；PEFT 配置还可能把加载路径改到另一基座。因此仅校验清单内文件不足以证明实际加载源固定。
- 新增根目录加载输入检查，拒绝清单外权重、词表、模板和 JSON 配置；Windows 的词表文件名按大小写不敏感识别。当前完整模型执行器不支持可跳转未验证外部基座的 PEFT 布局，即使其 adapter_config 已列入清单也明确阻断。已有本机 source-audit/configuration 附注保持非加载元数据边界，没有删除或更改任何模型文件。
- 13 个新增阻断反例先失败、修复后通过，另有 1 个良性附注兼容回归；VLM/ASR 冷加载都在调用模型前重新检查。定向 **59 项通过**，完整后端 **625 项、零失败/错误/跳过**；Ruff 通过。以上是隔离结构/Mock 合同验证，不冒充新模型推理。
- CI 原先对全部分支 push 和 PR 都运行，导致同一 feature 提交重复四项检查。现在仅 main push 与 PR 触发，并取消同一 PR 的过时运行；已实际解析 YAML、对比四个 job 全部内容和只读 permissions，均未改动，Windows 保留。额度已耗尽的账户不会因此自动恢复，仍未声称远端 CI 通过。
- 本次固定 8B 两个分片已在下载缓存完成完整校验；唯一安装尝试尚在隔离复制/最终验证，不是整体安装或运行验收通过。不得因所有字节接收完毕而跳过后续终态、进程和实机校准。
- 已准备逐次校准后的进程退出：确认调用完成/租约释放/模型卸载后，按 OS birth 停止本任务审片进程，记录 Windows 工作集与私有提交峰值，再启动相同冷服务。仅在辅助进程自身做了内存计数读取自检，尚无新 VLM/ASR 的峰值或结果，不把 Windows 进程数据当作 GPU 峰值。
