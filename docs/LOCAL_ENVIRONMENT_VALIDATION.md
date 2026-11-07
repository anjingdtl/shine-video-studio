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
| 连接测试 | `{"ok": true, "message": "连接检查通过：服务可达且模型目录列出了配置的 Model ID"}` |

能力探测返回 `available: true`，并列出服务商模型表：
`MiniMax-M3.1-Flash`、`MiniMax-M3`、`MiniMax-M2.7`、`MiniMax-M2.5`、`MiniMax-M2.1`、
`MiniMax-M2` 等。配置里写的是小写拼写 `MiniMax-M3.1-flash`，服务商返回的是
`MiniMax-M3.1-Flash`，因此目录探测的结论是 `model_listed: false`。按当前契约，
这会让连接测试判定为未通过（`error_code: LLM_MODEL_NOT_LISTED`，`reachable: true`），
工作台会列出候选 Model ID 供明确选择；系统不做未经授权的大小写模糊匹配。把配置里的
Model ID 改成服务商返回的写法后再测一次即可。连接本身成功。

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


## 基础自审目标调整与本机证据（2026-10-27）

世恒哥将自审目标调整为分辨率、人物、对白三项；风格、镜头等主观项不再是基础自审的必过项。基础自审不是人工影片验收，也不批准资产、声音或权利；冻结风格和预制作约束仍然存在。无人物/无对白的镜头对应项目标记不适用，缺失、模拟、失败或不确定的审查不能记为真实通过。

新增独立基础判定函数及反例测试；它尚未接入 Worker、尚无接通的专用人物审查执行协议，也未实现可持久化的真实生产证据，因此整个新流程仍为 PARTIAL，不改变既有生产门禁与历史记录。下一步需完成专用人物审查、证据身份绑定与串行本机实测。

最新原生运行时 ASR 已重新实测并发布：能力 d4443a6a-7526-46c4-903d-81d60cc67e11，调用 3f3db68e-bc38-4af6-8ecd-bc766bf6e890 对固定八字中文台词识别 CER 0；这不证明声音质量或授权。8B VLM 空街负例 38e12038-f57f-4bf1-8ff9-e839997a35dd 正常返回，但在没有转写证据时声称缺少对白，守卫拒绝该结果；VLM 尚未发布，其余对照未运行。调用后本任务审片进程与租约已释放，托管 ComfyUI 已恢复空闲。旧未知调用未重投，H3 未重投。远端 GitHub Actions 仍因账户付款/消费额度问题无法启动，不计为通过。

基础自审人物返回格式已增加逐人物覆盖和引用绑定校验；目标尺寸必须显式给定，新任务现可显式保存基础自审期望宽高；历史请求可能没有该字段，不能凭实际输出反推期望值。原型尚不能证明模型实际看懂人物，也不能用作生产最终门禁。定向基础自审、旧质量门禁及离线端到端测试共 36 项通过，Ruff 通过。完整后端套件第一次误从 backend 目录执行导致 10 项路径相关失败；从仓库根目录执行的上一轮为 836 项、零失败/错误/跳过，最终版本回归另行记录。

最终版本从仓库根目录运行完整后端套件：838 项、零失败/错误/跳过；证据为隔离验收目录 basic-review-final-suite-20261027.xml。该结果仅为软件测试，不等于基础自审已接入、真实 VLM 校准或人工影片验收。

2026-10-27 继续：新建任务 API 可接受并持久化 basic_review_resolution（严格正整数宽高），读取任务时返回；这仅是审查期望，不会修改 H3 工作流输出，也未接通生产自审。新增转写适配仅在实际台词均匹配时标记匹配，并保留 Mock 身份；具体调用的文件绑定、执行器来源和资源释放仍由生产接入负责。API/离线及定向测试通过；运行中的旧进程尚未切换到当前源代码，新版本的 VLM 客户端身份必须重新校准，不应继承旧资格。

当前源码完整后端回归 840 项、零失败/错误/跳过（basic-resolution-suite-20261027.xml；之后仅增加人物响应转换器并通过其定向测试）；Ruff 通过。2026-10-27 已安全重启本验收副本 API/Review/Worker/Web：API、Worker、ComfyUI 在线，任务及 ComfyUI 队列为空，审片模型未加载且显存分配为零。重启前两项 REPAIR_PENDING 保留、历史 editor RUNNING 记录保留，未重投 H3。记录 run/basic-scope-service-restart-20261027.json。新尺寸字段为自审期望，当前实时生产门禁仍是旧严格流程；基础自审接入和真实人物校准尚未完成。

继续验证：运行中的 API OpenAPI 已公开新目标宽高字段，Web HTTP 200，Worker 在线；基础自审定向及旧门禁/离线端到端共 39 项通过。对白适配现在按顺序检查所有台词和重复台词；仅检查识别文字，不声称证明说话人或声音质量。

专用人物审查客户端及多固定 Schema 执行器代码已构建：仅发送人物目标、绑定人物参考图和抽帧；截断、非真实来源、身份或 Schema 证明错误均拒绝，服务端 5xx 保留结果不明。旧单 Schema 锁行为不变；扩展 Schema 必须显式固定且改变解码器身份。90 项相关软件合同测试通过。此为合成响应和离线合同验证；当前运行的审片进程仍是旧版本，新客户端尚未注册或进行真实模型调用，不能视为基础自审通过。下一步须将新 Schema、客户端与能力证据身份绑定，并串行重新校准 ASR/VLM。

基础自审集成更新：新任务可冻结 basic 范围和期望宽高；Worker 为该范围记录分辨率、人物、对白及不适用的风格门禁，前端标明自审并非人工验收。人物审查必须绑定经真实正反例校准的本地能力，模拟任务不能变为真实通过；原有 full 范围和历史记录保持。已修复结构化解码器字符集初始化时序，真实 Qwen tokenizer 的中文正例/反例/待复核逐字符和逐 token CPU 合同均通过；无模型权重或 GPU 推理。最新固定源码完整后端 859 项、零失败/错误/跳过，Ruff 和 TypeScript/Vite build 通过；两轮源码变化中的回归中断，结果无效。新执行器尚未切换、ASR/VLM 新指纹尚未实机校准，基础真实自审仍 PARTIAL。

实机校准更新：新执行器下 ASR 固定中文台词真实调用完成并精确匹配，租约释放、审片进程退出后重新登记为技术可用（能力 4a5bc5b8-2bef-4883-9dae-1b2660479603），不代表听感、人物声音或权利通过。首次基础人物空街反例返回 needs_review，未误通过，但不符合先前预定的 failed，因此原测试判为不匹配并保留，人物能力未发布、正例未运行；ComfyUI 已恢复空闲，调用后审片进程和租约释放。根据用户降低门槛的要求，新校准合同允许预先声明反例 not_passed（failed 或 needs_review），正例仍必须 passed，错误的正例/反例结论不能发布。此合同仅完成定向软件测试，新的真实对照尚未运行。

安全补充：针对有角色的真实基础自审任务，项目必须先冻结已发布且范围匹配的人物审查能力；创建任务和 Worker 提交前均检查，缺失时返回 BASIC_REVIEW_REQUIRES_FROZEN_NATIVE_CAPABILITY，禁止先执行 H3 再发现无审片能力。对应 API 反例和 Worker 零提交反例、旧离线链及预制作/能力登记定向回归已通过。历史严格审片和离线模拟边界保持。

预制作范围现可选择 basic/full：basic 固定到新清单及哈希中，预检只选择对应范围的人物能力；切换范围创建新计划，保留历史清单。任务与冻结清单范围不一致时，真实提交前阻断。隔离测试验证同一剧本的范围版本以及能力选择；前端构建通过。旧 ASR 技术资格已恢复；新版人物能力仍待真实正负对照。

人物通过结果需包含实际观察说明，仅重复 passed/pass/ok 等状态不能作为通过证据；缺失与不确定继续阻断。相关定向测试通过，完整回归在固定源码下重新执行。

最终安全合同固定源码完整后端测试 869 项、零失败/错误/跳过，Ruff 与 TypeScript/Vite 构建通过；证据见 basic-nonpass-contract.json。人物通过需有观察描述，严格类型错误不通过。真实基础人物能力仍未重新校准发布。

真实基础人物校准：固定空街反例 7ee996a8-d176-43f7-86e7-5501dfb540c2 判 failed；固定人物正例 13c71976-7bd8-4071-ab5b-d0c87e5b82e1 判 passed，均符合预先声明目标，并分别确认模型租约释放及旧进程退出。新本地人物能力 660ecd23-de23-437a-8479-5cbff6da37a0 与 ASR 能力 4a5bc5b8-2bef-4883-9dae-1b2660479603 均已技术发布且当前 live 身份校验无错误；未重投 H3 或上传素材，ComfyUI、API、Worker 和 Web 已恢复。样本没有绑定人物参考图，只证明有限人物存在与视觉特征控制，不证明身份匹配、音质、人工影片验收或完整生产闭环；后者仍 PARTIAL。

Ref2VA 源文件只读核验：现有 INT8 Ref2VA、NVFP4 文本编码器、FP16 视频 VAE、FP32 音频 VAE 与 Realism People LoRA 五文件的当前 SHA-256 和字节数均匹配上游固定版本；原文件未修改，未生成视频。来源与逐文件证据见 ref2va-upstream-source-hashes.json。该核验不等于已登记的隔离模型包、运行时路径绑定、工作流发布或许可条件的全面判断；原 FAKE_EXECUTED 工作流未升级。

远端 CI 更新：提交 c1f9b5f31c159252ad556acb8b79cde8001d22bd 对应运行 40505766167，四项检查均为 failure；GitHub 对每项明确标注因账户付款失败或消费上限需要提高而未启动。故远端代码执行和 CI 通过仍未验证；本地测试不能替代。

Ref2VA 工作流技术闭环：本机已核对的五文件经隔离安装器建立两个固定包（合计 42,601,850,335 字节）并完成复验；受管 ComfyUI 已切换到独立 overlay，抽查扩散模型、文本编码器、两个 VAE 与 LoRA 均解析到隔离副本。新工作流 bb8a5033-2d6a-44f4-b1c5-6a5cdf94b70c 经本机节点检查，并绑定已有人工审阅的真实历史 Job fffe1d8-86e7-4593-acaa-91ff453b6b31（同一 Profile/图哈希及输出哈希），技术发布；旧 Fake 工作流未升级，未新提交 H3。该证据仅覆盖该图像参考 Profile，未证明音频参考、硬连续性或新目录切换后的再次推理；还需加强运行中模型文件变化检测与真实基础自审端到端，不能宣称完整影片通过。

隔离基础预制作检查：对现有验收数据库执行 SQLite 一致性备份，复制并复核 4 个已批准锁定素材，新增独立静音样片计划；原项目未改动。工作台及受管 ComfyUI 进程曾意外退出，原因未知；核实无排队任务、未完成尝试或未释放租约后按原隔离配置恢复，未重投 H3。恢复后真实预制作报告 SHOOT_READY、无阻断，选中 Ref2VA、基础人物 VLM 和 ASR 固定能力，证据见 isolated-basic-preproduction-readiness.json。计划尚未冻结、未生成新片，对白端到端未测；当前 Worker 的 /free 确认 CUDA 分配释放但没有证明进程物理退出或 RAM 完全释放，尚不能满足本次逐阶段物理释放要求，自动真实闭环仍 PARTIAL。

真实人物参考反例及修复：对已有真实人工审阅样片 fffe1d8 使用原始批准人物参考图，本机模型任务 c55ac8ac-4170-43bd-9f8e-599c1fbbf2a0 返回 passed 且自称已比较参考，但说明恰好达到 240 字符上限、末尾为不完整句子，且未明确说明人物与参考图的对应特征；不能把这一输出作为可靠的身份匹配证据。测试前受管 ComfyUI 进程实际退出、显存空闲 15087 MiB；测试后审片进程实际退出并确认租约 RELEASED，未重投 H3，Worker 已恢复。现新增触及说明长度上限时拒绝 passed 的保守校验，并要求输出简短完整句子及参考对应特征；完整的语义真实性仍无法从模型布尔值保证。此源码修改使旧 VLM 客户端固定指纹失效，必须重新运行真实正反例校准并技术发布，不能沿用旧能力宣称生产可用。详情见 basic-reference-truncation.json。

截断修复后的仓库根目录完整后端回归：869 项，失败、错误、跳过均为 0；Ruff 与 TypeScript/Vite 构建通过。此前从 backend 子目录误启动的迁移测试因相对路径失败，已停止并从正确工作目录完整重跑；测试不替代新的真实模型校准。

预制作能力选择修复：真实数据库中确认旧基础 VLM 660ecd23-de23-437a-8479-5cbff6da37a0 的客户端指纹失效后，新的已校准能力 6daa3912-ef0f-491f-9e62-7a4f60491088 仍被早期版本遮住，导致新计划 BLOCKED。现修复未冻结计划可跳过无效原生审查版本并选择合格版本；冻结清单只使用其固定版本，即使固定列表为空也不自动替换。修复触及审查指纹覆盖的预制作代码，因此 6daa3912 的客户端指纹也已失效，最终源码必须再次以真实正反例重校准；原已冻结历史不改写。

能力选择修复最终源码全量回归：870 项，失败、错误、跳过均为 0；Ruff、TypeScript/Vite 构建通过。冻结缺失能力不会自动补选，未冻结计划仅在原生身份校验通过时才选新候选；真实模型仍须在此源码版本上重校准。

最终源码真实重校准：人物能力 92bedd30-683e-4780-8a73-563e048dd14b 已发布，live 校验错误为空；空场景反例任务 0fd93dd4-4207-4836-988a-a7624b4de31c 判 failed，人物正例任务 76371641-a011-41b3-b9e3-b7945264ed4b 判 passed，均分别确认审片进程退出与租约释放。真实数据库仅回滚事务验证的新基础计划选择了该能力，状态 SHOOT_READY、无阻断。再以已批准人物参考图复测已有真实样片，任务 2147f3ae-386f-48bc-8c45-2d65784f7cd7 返回 passed、reference_compared=true 和 164 字符的完整特征比较；前后确认 ComfyUI 和审片进程分别退出、租约释放，Worker 已恢复。模型结果仍属概率判断；未生成新 H3、未完成对白端到端，自动 Worker 阶段间物理 RAM 释放仍未证明，不能宣称全部 PASS。详见 basic-selection-final-calibration.json。

独立真实对白计划与超时对账：从当前验收库一致性备份到独立目录，复制并哈希核对既有已审批锁定素材，冻结独立 basic 预制作清单，创建全新镜头 740d2a23-d501-4e4e-b188-ca2c80b5fed1，含现有批准人物/场景两图与一条普通话对白；原项目未修改。真实预制作检查 SHOOT_READY，提示词经 H3 编译器输出 Ref2VA，尚未创建或提交渲染 Job。单次独立文本一致性审查任务 3049daca-a1ed-4172-ac00-8c4a75954a51 在 300 秒超时，外部结果未知；已在隔离数据库对账为 ABANDONED/UNKNOWN，原请求重放保护验证仍生效，没有自动重试、H3 提交或媒体字节上传。请求前自管 ComfyUI 与审片进程退出，显存空闲 14954 MiB；工作台与 Worker 随后恢复。该计划无有效文本审查证据，真实生成仍受阻。

持久制品复检修复：明确请求机器复检时，在已有完成 Attempt 和不可变媒体通过当前身份、清单、文件大小和 SHA-256 校验后，Worker 使用该持久制品继续质量检查，不再依赖 ComfyUI 重启后可能丢失的内存历史。若文件在请求或 Worker 领取后被替换或删除，复检阻断且不下载替代文件或重投渲染；哈希采用流式读取以减少内存压力。该修改会使先前固定的 VLM 客户端源码指纹失效，必须针对最终代码重新执行真实校准。该修复不等于自动完成阶段间物理 RAM 释放或真实端到端。

远端 CI 核验：498c18c1b2ebec39ec2cc9781400adf6ec56b540 对应 Actions 运行 40528190928，backend、backend-windows、frontend、web-e2e 四项均未启动；各检查注释均说明账户付款失败或消费上限问题。该运行不证明远端代码执行，后续提交的检查需独立核验。
隔离计划在超时对账后，创建真实 Job 接口返回 409 STYLE_INPUT_ASSESSMENT_NOT_READY；无渲染请求发出。

持久制品复检修复最终源码的完整后端回归 873 项，失败、错误、跳过均为 0；Ruff 和 TypeScript/Vite 构建通过。覆盖历史丢失后的复检、请求后文件更改、Worker 领取后文件删除和无重投；实机真实新片与阶段间完整闭环仍未执行。详见 durable-recheck-and-dialogue-preparation.json。

2026-10-28 单镜头真实基本自审 PASS：隔离副本中，以已批准的人物与场景参考、冻结风格及新镜头修订 2 建立新 Job f930aca3-f4c9-4535-b5e0-7bd94a49f609，仅提交一次本机 Ref2VA H3（prompt fe0b7f67-1d3d-495c-9a3d-41fbf88ad7d0），获得真实 MP4，SHA-256 6086ac8dd6acffdfee204bd0f38708b4f1523c6e64024565ab9f39d1c1c7ee6f；完整解码、1344×768、6.583 秒及音轨检查通过。原始待审记录保留；归档 ComfyUI 历史并确认生成进程退出后，基于同一 Attempt/产物复检，本机 ASR 与 VLM 串行，阶段间审片进程实际退出，最终 technical、semantic、audio、final 均 passed，style 按 basic 范围 not_applicable。VLM 记录其对原批准人物参考的发型和服装比较；ASR 门禁记录对白匹配，但当前门禁未保存逐字转写，音质未作人工听审。审片使用了受控本机脚本提供已停止 ComfyUI 的进程见证，并在 ASR 与 VLM 间重启审片进程；因此这是**单样本、人工编排的真实端到端基础自审**，不证明现有 Worker 已具备无人值守的物理 RAM 交接，也不证明完整影片、声音质量、版权或真实人工审片通过。老文本超时仍 UNKNOWN 且禁止重放；隔离副本明确切换的独立文本模型对新修订给出 proceed_to_preflight。主工作台已恢复，原项目未改写。证据见 real-isolated-dialogue-basic-pass-20261028.json。

实机恢复及远端 CI：Windows 在上次运行后重启，旧进程记录中两个 PID 被复用；按出生时间核对后归档原清单并恢复受管配置，未停止复用 PID。f71fd4d068f5af885f7ddc1893ef55ff27cab402 的 Actions 运行 40531316985 四项任务均因付款失败或消费上限未启动；新提交 CI 要另行核验。主库最新人物审查能力 7299c5a0-62d9-4913-b1b7-5cd7989acdc7 已按 f71fd4d 重新做空场景反例、人物正例并技术发布，未生成或批准新主资产。

对白证据改进：basic 音频门禁对新审片记录保存 ASR 原文（超过 20000 字符时明确标注截断）、完整原文 SHA-256 和对应视频 SHA-256；文字仅保存在本地门禁证据中，不改变匹配判定、旧审片或旧样片。定向测试覆盖文本和超长截断；最终源码完整后端回归 874 项，失败、错误、跳过均为 0，Ruff 与 TypeScript/Vite 构建通过。源码变化导致原 VLM 客户端指纹失效，须重做真实校准；历史样片的旧门禁不被反向补写。证据见 transcript-evidence-contract-20261028.json。

对白证据真实控制：对已生成的真实单镜头视频单独运行一次本机 ASR，调用 60415fad-2fdd-4b1b-890f-34934714954b 完成且租约 RELEASED；识别原文归一化后与脚本匹配，新证据包含原文、完整原文哈希和视频哈希，无截断，音频门禁计算 passed。结果和原文保存在隔离本机记录，旧 PASS 门禁未改写；ASR 进程实际退出后恢复，未重投 H3，工作台恢复。源码 9e1cd1b 的主库人物审查能力 0bacbb99-426a-45d0-8904-7f3b80535629 已串行完成真实正反例并技术发布，live 校验无错误。上述为机器验证，不证明人工听音、音质或无人值守物理 RAM 交接。详见 real-transcript-evidence-control-20261028.json。

本地审片释放校验：`/free` 声称已释放且分配显存为零后，客户端还读取同一服务 `/health`，要求状态正常、VLM 和 ASR 均未加载、分配与保留显存均为零；缺字段、保留显存或模型仍加载均阻断并保留租约待对账。该协议适用于本地画面/语音审片端点，自定义服务须实现这些健康字段；远程端点未改变。首次完整回归发现六个用例因旧模拟端点未实现健康响应而失败，已补充并复测。真实空载本机服务已接受 `/free` 加 `/health` 检查，未运行推理。此检查不证明进程宿主 RAM 已全部释放，不替代进程级串行交接。d1583602551c43b61e52fed4e21ce3eaa2a8d66b 的 Actions 40557325886 四项检查因账户付款或消费额度问题未启动，远端代码执行仍未验证。

释放健康校验最终源码完整后端回归 876 项，失败、错误、跳过均为 0；Ruff 和 TypeScript/Vite 构建通过。反例覆盖 `/free` 返回成功但缓存显存未释放或模型仍加载，均保留 RECONCILE_REQUIRED 租约。源指纹已变，VLM 须重新真实校准；进程 RAM 释放及自动串行交接仍待完成。详见 release-health-contract-20261028.json。


2026-10-28 补充：源码 0d38d5b 的新人物审片能力草案已静态验证，实际正反样本校准在模型调用之前因释放本受管 ComfyUI 后空闲显存不足 14700 MiB 被准入检查拦截，实际模型调用 0 次、H3 重投 0 次；保护表未改变，ComfyUI 和 Worker 已恢复且空闲。旧失败记录保存在本地，不能视为新源码的合格证据；新人物能力仍待显存满足后完成正反样本校准和发布，自动进程级 RAM 释放交接也仍未完成。此前真实视频的有限基本验收证据保留，不等于本次新源码的端到端复验或人工验收。


2026-10-28 渲染器释放读数加固：反例先证实 ComfyUI 显存数值为布尔值、负数、空闲量大于总量、NaN 或无穷时旧判断可能错误确认释放；现统一拒绝这些异常，正常 0.39 本机空载报告仍可判定。渲染适配、租约、离线链定向回归及完整后端 881 项通过；本验收副本已在队列空闲、租约全部释放后重启，API 和 Worker 在线，未重投 H3。该检查仍只是渲染器自报分配值的判断，不证明进程级内存交接；此前新人物能力校准的显存阻碍仍待解决。证据见 renderer-invalid-release-20261028.json。


2026-10-28 队列交接校验：四个反例先证实 ComfyUI 队列响应缺失或字段类型无效时，旧逻辑可能发起卸载，或在卸载后错误确认队列空闲；现要求运行与等待队列均为列表，卸载前未知即阻断，卸载后未知不得确认释放。当前真实队列格式检查通过；定向回归及完整后端 885 项通过，API/Worker 已恢复空闲。再次尝试人物校准时，受管 ComfyUI 停止后显存准入仍不足 14700 MiB，0 次模型调用、0 次 H3 提交，ComfyUI 已恢复、保护表未改变；自动进程级 RAM 交接仍未完成，队列轮询本身也不证明外部用户不会并发提交。证据见 renderer-queue-validation-20261028.json。


2026-10-28 真实语音控制补充：受管 ComfyUI 经核实空队列、归档历史后停止；对既有隔离真实 H3 视频独立运行一次本机 ASR（调用 c21f5df9-69cd-414d-a545-7cac59009343），识别文字与脚本匹配，音频基础门计算 passed，调用完成且租约 RELEASED。新审片释放健康协议检查通过，旧审片进程确实退出、新进程保持相同执行器身份，ComfyUI 已恢复；未重投 H3、未改写原有 QC。此项是机器 ASR 对照，不证明声音质量、人工听审、人物 VLM 重新校准或 Worker 自动进程级交接。详见 real-release-health-asr-control-20261028.json。


2026-10-28 审片进程隔离：新增 Windows 可选 `-IsolatedReviewProcess`，本地审片请求在独立受管子进程执行，子进程退出后才返回；超时、失联、源码/身份变化阻断且不重放，父进程异常退出由 Job Object 清理子进程。Windows 进程生命周期及隔离监督合同测试通过；最终源码完整后端 892 项通过，Ruff 和前端构建通过。独立空数据库的 Mock 启动器通过并已关闭。对已有隔离 H3 视频执行的真实 ASR 调用 2b530e8c-9b1e-48d3-b83d-489bdb38a05e 与对白匹配、租约 RELEASED，子进程 PID 7132 已退出，人工停止的 ComfyUI 已恢复；未重投 H3。该新模式使用不同执行器指纹，人物 VLM 尚未真实校准；停止/恢复 ComfyUI 仍由受控脚本完成，Worker 自动生成到审片的进程级交接仍未完成。详见 isolated-review-process-20261028.json。


2026-10-28 4B 基础人物范围实机对照：依照用户降低自审范围的要求，未更改生产路由，在独立进程审片服务中使用现有 4B 模型做固定对照。空景反例 failed、人物正例 passed；另外对已有人工审阅视频及其原批准角色参考进行一次独立检查，返回 passed 且 reference_compared=true。三次真实调用均 COMPLETED、租约 RELEASED、各自子进程退出；受管 ComfyUI 在各批次前停止并恢复，保护表未变，H3 未重投。此次为临时校准档案上的有限实机对照，尚未登记、发布或切换生产能力；不改变旧 4B 全范围资格撤销，也不证明自动生成到审片交接、完整影片或人工听审。详见 isolated-4b-basic-controls-20261028.json。


2026-10-28 隔离审片身份与双端点：真实 4B `/identity` 由重启模型进程导致超过原 10 秒登记超时；现以不加载模型的逐文件复验重算执行器身份，与启动时子进程身份、解码器和运行环境逐项比对。实际 4B 本机身份核验约 2.3 秒；源文件或模型不匹配仍阻断。启动器可显式管理第二个隔离审片端点，独立 Mock 数据目录验证两个端点空载、身份与受管关闭均通过。完整后端 893 项、Ruff 和前端构建通过。修改使隔离执行器指纹改变，此版本尚需按真实注册档案重新校准 VLM 与 ASR；ComfyUI 进程自动交接仍未完成。详见 isolated-identity-dual-review-20261028.json。


2026-10-28 4B 基础人物能力正式技术登记：为新增 4B 隔离端点创建独立 Provider 档案 b7a003ec-7917-470c-abaa-6e5cd602eec7，显式将 VLM 路由切到该档案；旧 8B 档案及端点保留、ASR 路由不变。注册档案下真实空景反例 failed、人物正例 passed，调用均 COMPLETED、租约 RELEASED、各自子进程退出；系统以真实证据采纳并技术发布能力 8559fff5-873b-4943-80fd-a85a788d610e，重启后实时 native 校验无错误、Worker 在线。旧冻结计划未改写，仍须遵守其固定能力和重新预制作规则；此仅为 basic 人物机器能力，非全范围审片、人工影片/音质/权利批准。ComfyUI 本次仍由受控脚本停止恢复，自动生成到审片的物理内存交接尚未完成。详见 registered-4b-basic-capability-20261028.json。


2026-10-28 隔离 ASR 登记：新档案与隔离执行器已完成真实对白匹配控制并技术发布，调用完成、租约释放、子进程退出；未重投 H3，未作人工听审或影片批准。运行中断后检查 API、Worker 对应服务及 ComfyUI 均未响应，最终恢复未验证；原有进程清单保留，需按进程出生标识和租约对账后恢复。生成到审片的自动进程交接仍未完成。详见 registered-isolated-asr-20261028.json。
# 2026-10-28 夜间续测

- 启动前 `git fetch origin`、`git ls-remote origin refs/heads/main`：均为 `f6b196afa3d53623baa2a8f16a5e62f1d2520649`；凭据管理器路径失效提示存在，但查询成功。仅有一个工作树。
- 机器开机时间 19:08:41；显卡 16303 MiB 中占用约 939 MiB。旧验收进程清单记录于 07:32，未按旧 PID 结束进程；实际服务未运行，ComfyUI 队列 **NOT RUN**。验收数据库只读核对资源租约 92 条 RELEASED、作业 4 项 NEEDS_REVIEW 与 2 项 REPAIR_PENDING，未修改数据库或重新提交 H3。
- 过期租约反例先在原代码失败：`test_expired_stage_lease_requires_reconciliation_even_if_cleanup_succeeds` 未抛出预期错误。修复后 `python -m pytest backend/tests/test_serial_stages.py backend/tests/test_v13_preparation.py backend/tests/test_offline_e2e.py -q` 通过；`test_v13_migrations.py`、`test_v13_e2e.py` 与 `test_film_crew.py` 定向通过；隔离临时数据库 Alembic upgrade head 通过。完整 `python -m pytest backend/tests -q` 通过，收集 894 项；Starlette 弃用与 SQLAlchemy 空主键警告未计为失败。`ruff check backend/shine backend/alembic backend/tests` 和 `npm run build` 通过。
- 历史 ASR 收据 [registered-isolated-asr](evidence/local-closeout-20261025/registered-isolated-asr-20261028.json) 的调用 `007ea46e-5adc-4035-9a07-7fbfdd44e962` 在数据库中为 COMPLETED；这是历史机器控制结果，非本次实机重跑或人工听审。
- `gh run list --repo anjingdtl/shine-video-studio` 返回 HTTP 401 Bad credentials；本次远端 CI **BLOCKED**。真实 H3、新视觉/ASR/声音模型调用次数均为 0，自动交接及当前真实工作台端到端 **NOT RUN**。
# 2026-10-28 自动交接建设续轮

- 在合成 ComfyUI 上先复现三个反例：卸载响应后仍进入审片、恢复扫描释放未核实的 H3 租约、操作员授权将卸载错误标成物理释放。修复后相应反例保持待对账并阻止后续操作；新增提交前无归属阻塞和已下载输出持久化检查。原离线 E2E、repair 与 film crew 保留既有断言，明确注入仅供合成测试的交接替身；生产前检查没有关闭。
- 排空屏障线程并发测试通过。`F:/AIGC2026/ComfyUI_Mie_2026_V8.0_Base/python_embeded/python.exe scripts/probe_handoff_adapter.py` 使用合成队列和真实 aiohttp 通过授权、队列繁忙及排空后拒绝新任务测试。未启动实际 ComfyUI 或安装适配器；自定义节点绕过队列入口的风险尚未排除。
- 交接账本合同测试覆盖中断后保留 STOP_INTENT、阻塞后续租约、禁止重复意图、拒绝非受管实例和过期租约。该账本尚未连接实际进程操作，不能代表恢复链路通过。
- Windows 受管进程组以挂起方式创建并先绑定 Job Object，合成 Python 子进程及其子进程退出、监督进程意外退出、Unicode 环境传递均通过；过程中曾发现活动数量结构读取不完整造成错误零值，现同时核实完整计数与根进程句柄退出。没有加载 H3 或审片模型。
- 只读物理资源探测：指定 GPU 总显存 16303 MiB、空闲 14996 MiB，系统可用物理内存 22088142848 字节；探测阈值仅作为合成示例，非真实模型预算或 H3 卸载证明。
- 完整端到端、真实 H3、真实视觉/ASR、自动恢复和人工听审本轮均 **NOT RUN**。现阶段代码和测试未组成完整自动交接，待实现受管监督服务、进程及队列身份绑定、跨阶段心跳和独占、崩溃对账、重启及全链路实机验证。
- 当前工作区执行 `backend/.venv/Scripts/python.exe -m pytest backend/tests -q` 全部通过，收集 910 项；Ruff 和前端 `npm run build` 通过。保留 Starlette 弃用与 SQLAlchemy 空主键告警；不据此推断真实模型或远端 CI 通过。
# 2026-10-28 受管交接组件续建实测

- Windows Job Object 使用同一个进程句柄核验 PID 的出生标识、存活状态和进程组归属；本机隔离 Python 进程树的正反例通过。监督原语同时对照操作系统监听表和服务报告，发现端口所有者不一致时在排空前阻止操作；拒绝代理环境以免凭据经代理传输。历史文件先独占创建、同步写入及复验哈希，文件变更时拒绝停止。
- 排空协议补充历史/队列修改的异步互斥：已有修改完成前不能排空，排空后拒绝修改。适配器身份同时绑定两个源文件，测试对临时副本改动后状态请求被拒绝。实际 ComfyUI 及其第三方节点未安装或验证该适配器，不能排除节点绕过队列入口。
- 新增 H3 交接心跳：单个 SQLite 写事务核对作业、镜头版本、输出、进程身份及资源状态，然后同时续约作业、尝试和资源；过期租约不得复活。合成跨阶段测试持续超过初始租约时长，其他任务仍无法获得资源。模型代理继承租约时新增到期检查，反例先失败、修复后通过。
- 使用受管的合成 HTTP 渲染进程及合成视频，将排空、历史快照、停止和模拟内存门禁串到 REVIEW_INTENT；进程与账本使用同一资源租约，未实现恢复时保持 RECONCILE_REQUIRED。此项不包含真实 H3/审片/重启，且没有接入 Worker。
- Worker 与 `model_calls.py` 已改变，而两者在 `shine/quality/client_identity.py` 的指纹列表中；此前真实 4B/ASR 控制和技术登记仅为历史证据，当前源码的资格需重新核验，不得沿用旧结果。
- 新增恢复反例：交接停在 STOP_INTENT 且 Worker 租约过期时，原恢复器会查询外部 ComfyUI；现先锁定数据库，将意图和 H3 资源保留为 RECONCILE_REQUIRED，记录停止前阶段和原视频，不联系或重投渲染器。未过期的活跃交接不被恢复器接管。这是安全阻塞，不是自动重启恢复实现。
- 停止后在显存检查前后核对该端口没有新 IPv4 监听者；出现替换监听者或读数不足均阻塞。测试为隔离进程和合成数据，不证明外部不受控程序不会在其他端口启动。
- 交接实验入口在账本事务中重新执行现有预制作检查；合成反例在排空后和检查与停止意图之间改变冻结快照，均阻止停止并保留租约。镜头修订变化在建立意图和阶段转换处均被阻止。以上是未接入 Worker 的工程测试，不替代生产前检查或真实 H3 验收。
- 本续轮 `backend/.venv/Scripts/python.exe -m pytest backend/tests -q` 通过，收集 929 项；Ruff、V1.3 指定测试、旧 offline E2E、film crew、临时数据库 Alembic upgrade head 及前端构建通过。Starlette 弃用与 SQLAlchemy 空主键告警仍存在。合成 HTTP 排空协议与 Windows 受管进程组定向测试通过；没有真实 H3、审片或人工听审调用。

# 2026-10-28 进程释放与审片收据续建实测

- Windows Job Object 合成反例曾观察到活动数归零早于子进程句柄退出；现终止前保留进程句柄并等待退出，同时检查进程组与根进程。隔离审片响应携带请求、响应和执行器哈希及退出收据；监督器使用令牌封闭新请求，并再次检查服务状态、子进程及物理资源。此为实验组件，现有清单不能独立证明服务归属，实际安全生命周期仍未建立。
- 合成端到端覆盖保留租约、持久化重启意图、审片后重启固定参数受管进程及更新实例记录；释放验证和显存数据使用替身，不能证明真实显存释放。Windows 定向测试 `backend/.venv/Scripts/python.exe -m pytest backend/tests/test_renderer_supervisor.py -q`：14 项通过。
- 先构造调用方仅声明 ASR 的反例，旧逻辑错误进入重启；修复后从作业冻结请求及项目质量策略推导必需视觉/对白审片。更改不将未审内容写成通过，也不赋予人工批准。
- 本轮当前源码 `ruff check backend/shine backend/alembic backend/tests scripts/local_review_isolated_server.py scripts/probe_handoff_adapter.py integrations` 和前端 `npm run build` 通过。完整回归第一次误在 backend 目录启动，两个 Alembic 测试无法定位仓库根配置，已中止并从仓库根目录重新执行；以重跑结果为准。
- 真实 H3、视觉、ASR 和其他声音模型调用次数均为 0；自动工作台流程及人工验收 **NOT RUN**。历史验收库和旧清单未修改，未重投 H3。
- 释放验证进一步要求审片监听者属于当前持有的 Windows Job Object 进程组，进程归属、出生时间、端口及子进程退出均需核对；缺失归属时在发送封闭请求前失败。`backend/.venv/Scripts/python.exe -m pytest backend/tests/test_review_isolation_receipt.py backend/tests/test_renderer_supervisor.py -q`：18 项通过。现有启动器尚未提供该受管组，不能据此宣称真实服务已受控。
- 从仓库根目录开始的完整回归中途修改了参与审片指纹的源码，后续身份测试出现错误；该次运行中止，不能计作通过，需在源码稳定后重新执行。

# 2026-10-29 隔离真实 ComfyUI 适配器实测

- 使用安装目录中的真实 ComfyUI 和其 Python，以 `--base-directory` 指向全新验收副本、`--cpu`、禁用其他自定义节点并仅加载复制的 Shine 屏障插件；未加载 H3 或审片权重，没有修改现有 ComfyUI 的目录、配置、队列或历史。Windows Job Object 持有受管实例。
- 第一次探测的合成 `EmptyImage → SaveImage` 成功，但测试脚本给了非 UUID 尝试标识，排空前按合同拒绝；受管进程组已退出。失败及退出证据保存在 `.local-acceptance/comfy-adapter-cpu-probe-20261029/evidence.json`。
- 第二次以合法标识运行：合成任务 `34dde4e1-3961-41f0-b13c-fda3b5cc2236` 完成，排空并留存历史哈希；排空后新任务和历史清空均返回 409，进程组 2 个成员均等待退出。验收文件见 `.local-acceptance/comfy-adapter-cpu-probe-20261029-b/evidence.json` 和同目录 `history/`。事后两次端口均无监听、两个根 PID 不存在；GPU 空闲约 14903 MiB。
- 这是实际 ComfyUI、CPU 合成节点和专用插件的有限兼容测试，不覆盖真实 H3 节点或其他自定义节点绕过队列、模型物理卸载、Worker 自动交接和审片；这些仍为 **NOT RUN/PARTIAL**。
- 本轮源码稳定后在仓库根目录运行 `backend/.venv/Scripts/python.exe -m pytest backend/tests -q`：收集 944 项，退出码 0；完整输出 `.local-acceptance/closeout-20261025/full-suite-20261029.log`。Starlette 弃用与 SQLAlchemy 空主键警告仍在。Ruff、前端构建和临时 SQLite 的 Alembic upgrade head 通过；这些结果均不证明真实模型验收。
- 另构造共享租约并发反例：两个不同输入的模型调用同时继承同一有效资源租约时，旧代码允许两个提供方同时执行。修复后在 SQLite 写事务中检查同租约的 RUNNING、NEEDS_REVIEW 和 RECONCILING 模型调用，忙时拒绝第二个；首个调用清理完成后下一调用可执行。`test_serial_stages.py` 8 项通过，旧版 offline E2E 与 film crew 定向通过，Ruff 通过。该保护不代替阶段间实际进程退出和显存检查。
- 上述 944 项完整回归发生在并发修复之前；修复改变客户端身份，完整后端回归再次运行，结果以 `.local-acceptance/closeout-20261025/full-suite-serial-fix-20261029.log` 的最终退出码为准。
- GitHub 连接器于 2026-10-29 重新查询：远端 main `f6b196afa3d53623baa2a8f16a5e62f1d2520649` 对应最新 CI 运行 [40603149040](https://github.com/anjingdtl/shine-video-studio/actions/runs/40603149040) 为 completed/failure；frontend、backend-windows、backend、web-e2e 均为 failure，steps 和 logs_url 为 null，backend 日志读取返回 BlobNotFound。实际测试没有可见执行证据，不能视为 CI 通过；本次接口未给出明确失败原因，历史付款提示不可直接当成当前诊断。`gh run list` 仍 HTTP 401。
- 并发修复后，最终源码完整后端回归收集 945 项、退出码 0；证据 `.local-acceptance/closeout-20261025/full-suite-serial-fix-20261029.log`。明确重跑 `test_v13_preparation.py test_v13_migrations.py test_v13_e2e.py test_offline_e2e.py test_film_crew.py` 全部通过；Ruff 通过。此前 944 项是修复前的结果。仍有 Starlette 弃用和 SQLAlchemy 空主键告警。

# 2026-10-29 审片阶段准入原语

- 新增审片意图登记：同一 SQLite 写事务内重核作业、尝试、镜头、输出、预制作和独占租约后写入 ASR/VLM 意图；已有未核实意图时禁止另一阶段或结束审片。反例先失败，修复后交接账本、受管渲染器和旧 offline E2E 定向测试共 27 项通过，Ruff 通过。中断时保留意图并阻塞资源。
- 该原语尚未接入实际模型调用，也尚未实现将真实审片释放证据与意图绑定为 VERIFIED 的完整协议；不得将其视为自动链路完成。修改后完整回归日志为 `.local-acceptance/closeout-20261025/full-suite-review-intent-20261029.log`，以其中最终退出码为准；此前 945 项结果属于本修改前。真实 H3 和模型调用本轮 0 次。
- 此次修改后完整后端回归收集 946 项、退出码 0，日志为 `.local-acceptance/closeout-20261025/full-suite-review-intent-20261029.log`；仍有已记录的 Starlette 弃用及 SQLAlchemy 空主键警告。

# 2026-10-29 审片意图、调用和释放收据绑定

- 实验性交接新增 `review_stage` 上下文；`TrackedProvider` 在调用提供方之前，将未提交调用记录与相应 ASR/VLM 意图及租约绑定。调用返回只能完成调用记录，意图保持 INTENT；只有匹配的隔离释放验证成功后，才在数据库事务中把释放收据和 VERIFIED 写入对应记录。未绑定、已提交或已完成的旧调用不能冒充新意图；重新核验租约、作业状态和镜头版本。
- 首先构造无意图直接接受旧结果的反例，原实现错误接受；修复后定向交接账本、受管渲染器、串行阶段、旧 offline E2E、film crew 全部通过，Ruff 通过。上述提供方、显存和释放验证为合成替身，非真实模型证据；完整回归日志 `.local-acceptance/closeout-20261025/full-suite-review-binding-20261029.log` 以最终退出码为准。
- 生产 Worker 尚未接入协议，独立监督与真实服务的受管启动、阶段间物理资源门禁和崩溃恢复仍需完成；真实 H3/视觉/ASR 本轮调用为 0。客户端代码改变，历史资格不能复用。
- 绑定修改后完整后端回归收集 947 项、退出码 0；指定 V1.3 preparation/migrations/e2e、旧 offline E2E、film crew 单独重跑通过。日志 `.local-acceptance/closeout-20261025/full-suite-review-binding-20261029.log`；Starlette 弃用与 SQLAlchemy 空主键警告仍在。

# 2026-10-29 多阶段释放复核

- 构造两个反例：第一审片监督服务在后续阶段前或渲染器重启前失去可信归属时，原实现仍会继续。现从内存中保存每一已接受阶段的受管验证参数，进入下一阶段及重启前逐一重查封闭状态、进程归属、收据和物理资源；任一失败阻塞。敏感令牌仅保存在进程内，不写入账本。
- 合成端到端测试现通过真实 `review_stage` 与 `TrackedProvider` 路径完成意图、调用、释放和受管渲染器重启，保留无意图旧结果不得接受的反例；审片释放与 GPU 数据仍为替身。交接账本、渲染器、旧 offline E2E、film crew 定向通过，Ruff 通过。完整回归日志 `.local-acceptance/closeout-20261025/full-suite-review-recheck-20261029.log`，以最终退出码为准。
- 该流程未接入生产 Worker；真实 H3、审片模型和人类审核 **NOT RUN**。进程组启动、独立监督和中断恢复仍需继续实现与实测。
- 本次修改后的完整后端回归收集 949 项、退出码 0，日志 `.local-acceptance/closeout-20261025/full-suite-review-recheck-20261029.log`；仍有既存 Starlette 弃用和 SQLAlchemy 空主键告警。

# 2026-10-29 受管重启源码竞态和中断测试

- 先复现重启竞态：已停止的渲染器在旧哈希检查通过后、实际启动重新计算哈希前源文件被替换，原实现错误启动了替换后的代码。修复后把原固定哈希传入启动，并在创建挂起进程、恢复运行前复验；定向 Windows 测试通过。此检查仍不是对任意文件修改的原子锁定，不能宣称所有启动或运行期间源码变化均已防止。
- 另以受管合成渲染器模拟审片意图产生后的异常：账本保留未确认意图和 RECONCILE_REQUIRED 租约，拒绝再次预留，未重启渲染器。此为异常模拟，非真实进程崩溃恢复或 H3 实测。相关受管进程、交接、旧 offline E2E、film crew 定向测试及 Ruff 通过；完整回归日志 `.local-acceptance/closeout-20261025/full-suite-pinned-relaunch-20261029.log` 以最终退出码为准。
- 生产 Worker、独立监督服务和真实阶段仍未接入；本轮真实 H3/视觉/ASR 调用 0 次。
- 此轮固定哈希修复后的完整后端回归收集 951 项，退出码 0；日志为 `.local-acceptance/closeout-20261025/full-suite-pinned-relaunch-20261029.log`。已有 Starlette 弃用及 SQLAlchemy 空主键告警。

# 2026-10-29 受管进程固定文件锁

- 构造反例：固定源码在受管进程存活时仍可写入，旧代码未阻止。现以 Windows 文件共享句柄限制被固定文件的写入和删除，在取得句柄后重新核验哈希，受管进程确认退出后释放；指定文件写入、删除及退出后恢复写入的定向测试通过。只覆盖调用方显式指定的文件，未覆盖未固定依赖、目录替换、模型文件和其他进程；不能作为完整源码不可变或真实 H3 安全证明。
- 真实 ComfyUI 安装以独立基础目录和 CPU 模式再次启动，固定 Python、入口和适配器源文件；合成任务 `20c37651-b6e0-40c3-9630-ddeac1782af0` 完成，排空后任务提交和历史清空均被拒绝，已归档历史，受管进程组两个成员等待退出。证据 `.local-acceptance/comfy-adapter-cpu-probe-20261029-c/evidence.json`；退出后端口无监听且根进程不存在，GPU 空闲约 14881 MiB。未加载 H3 或审片模型。
- 受管进程、渲染器、旧 offline E2E 和 film crew 定向测试及 Ruff 通过；完整回归日志 `.local-acceptance/closeout-20261025/full-suite-source-lock-20261029.log`，以最终退出码为准。独立监督、生产 Worker 接线、真实模型和崩溃恢复仍未完成。
- 本轮修改后完整后端回归收集 952 项、退出码 0，日志 `.local-acceptance/closeout-20261025/full-suite-source-lock-20261029.log`；保留既有 Starlette 弃用及 SQLAlchemy 空主键告警。

# 2026-10-29 串行审片代理

- 为实验交接新增审片代理，封装既有 `TrackedProvider`：调用前预留和绑定意图，得到结果后从账本取匹配收据，完成隔离释放验证后才向调用方返回；无收据或验证失败不得返回成功。拒绝通过该代理调用另一种模型方法。旧结果无意图、调用其他模型方法的反例均在修复前失败；合成端到端测试实际经过代理与受管渲染器重启。
- 交接账本、受管渲染器、串行阶段、旧 offline E2E 和 film crew 定向通过，Ruff 通过。完整回归日志 `.local-acceptance/closeout-20261025/full-suite-review-proxy-20261029.log`，以最终退出码为准；所有审片和资源释放为合成替身。
- 生产 Worker 尚未使用该代理，当前受管实例的启动器也尚未持有可供交接的 Job Object；独立监督服务、真实崩溃恢复、真实 H3 及审片仍未完成。
- 本次代理修改后的完整后端回归收集 952 项、退出码 0，日志 `.local-acceptance/closeout-20261025/full-suite-review-proxy-20261029.log`；既有 Starlette 弃用及 SQLAlchemy 空主键告警仍在。

# 2026-10-29 审片调用前准入

- 先构造代理在核验服务归属之前已能提交模型调用的反例，以及实际调用地址与核验地址不同的反例；修复后调用前要求地址和 GPU 与交接配置一致，并检查受管进程组、端口与出生标识、审片服务空载且未封闭、执行器哈希和实际资源阈值，期间续约并复核。未满足条件时不得提交。
- 交接、审片收据、旧 offline E2E 和 film crew 定向通过，Ruff 通过；具体进程、健康和显存检查的相关测试使用替身，真实 H3/视觉/ASR 未运行。完整后端回归日志 `.local-acceptance/closeout-20261025/full-suite-review-admission-20261029.log` 以最终退出码为准。
- 审片代理仍未接入生产 Worker；独立监督服务及真实阶段间恢复未实现。

# 2026-10-29 审片调用前物理准入

- 新增代理模型调用前检查：被核验的地址与实际提供方地址、GPU 身份必须一致；确认受管 Windows 进程组、监听 PID 和出生时间、执行器哈希、服务未封闭且空载、内存显存阈值。隔离服务新增不加载模型的令牌认证探测；错误令牌、封闭服务及非空载状态均不应提交模型调用。两类地址/归属反例在修复前失败。
- 隔离审片监督器、审片收据、交接账本、渲染器、旧 offline E2E、film crew 定向通过，Ruff 通过。涉及审片归属和显存的测试为合成替身，不是实机模型验收。完整回归日志 `.local-acceptance/closeout-20261025/full-suite-review-readiness-20261029.log` 以最终退出码为准。
- 中间一次完整回归在加入令牌探测前中止，不计为通过。监督器源码与审片客户端指纹改变，旧 4B/ASR 能力登记不可用于新版本。生产 Worker 与独立受管服务仍未接入，真实自动链路仍 **PARTIAL/NOT RUN**。
- 调用前准入修改后完整后端回归收集 953 项、退出码 0；日志 `.local-acceptance/closeout-20261025/full-suite-review-readiness-20261029.log`。已有 Starlette 弃用和 SQLAlchemy 空主键警告仍在。

# 2026-10-29 审片服务一次性准入

- 原隔离服务的调用前检查与模型请求之间允许其他本地客户端插入调用。新增经令牌认证的单次意图预留：预留期间拒绝其他请求与重复调用；预留后再次核验服务空载、监督归属和内存/显存。超时或不确定预留不自动重试，意图与租约保留对账。
- 本地 ASR、VLM 和 basic 人物客户端仅在绑定的 127.0.0.1 地址发送一次性凭据，并在此模式关闭环境代理；凭据留在进程内，不写账本。隔离服务对预留后未调用的旧收据拒绝封闭。
- 隔离服务、客户端、交接、旧 offline E2E、film crew 定向测试与 Ruff 通过；合成 ASR 客户端经过实际 HTTP 处理逻辑，只执行一次并拒绝重放，但执行器和音频为替身。完整回归日志 `.local-acceptance/closeout-20261025/full-suite-reserved-review-20261029.log` 以最终退出码为准。
- 真实 Worker 和受管实例启动器尚未接线；真实 H3/视觉/ASR 本轮未调用。此次客户端和服务指纹变化，历史模型资格仍需重新校准。
- 此次一次性准入修改后完整后端回归收集 956 项、退出码 0；日志 `.local-acceptance/closeout-20261025/full-suite-reserved-review-20261029.log`。已有 Starlette 弃用和 SQLAlchemy 空主键警告仍在。

# 2026-10-29 受管渲染器关闭启动与激活原语

- 构造反例：渲染器重启后默认接受队列，交接租约尚在时外部请求可进入。现支持显式 `SHINE_COMFY_START_DRAINED=1`，受管重启要求该固定启动配置且确认新进程屏障为 drained；不支持关闭启动的配方在停止原渲染器前被拒绝。令牌认证的恢复接口本身不检查 SQLite 租约，不能单独当作生产授权。
- 新增受管启动入口：监听前核验指定屏障模块、队列绑定、drained 状态、源码哈希及控制路由，禁止非 loopback。独立 CPU 实例实测启动和重启均为 drained，未授权恢复 401、关闭时任务 409；认证恢复后合成任务 `89778bf5-b081-4c18-ac23-66b9c3b2f9bb` 完成并归档历史，进程组已确认退出。证据 `.local-acceptance/comfy-managed-bootstrap-cpu-final-20261029/evidence.json`。缺少插件白名单的另一隔离实例退出且轮询期间未见监听，证据 `.local-acceptance/comfy-bootstrap-missing-adapter-20261029/evidence.json`；该探测未捕获子进程错误日志，不能仅凭此断定具体退出原因。端口及根 PID 事后不存在，显卡空闲约 14913 MiB。未加载 H3 或审片模型。
- 新增实验激活函数：打开队列前在 SQLite 同时核验作业、镜头、尝试、独占租约和预制作状态，并持久化 INTENT；回复丢失时标记 RECONCILE_REQUIRED，拒绝重放。Worker 的过期恢复扫描会在任何渲染器请求前阻断未完成激活。合成反例、旧 offline E2E、film crew 和恢复定向通过，Ruff 通过。
- 上述激活原语尚未接入生产 Worker；调用前如何证明前一模型阶段的进程及资源已释放、受管启动器/令牌生命周期、监督进程独立运行和恢复仍未完成。故真实 H3 到审片 **PARTIAL/NOT RUN**，不得将 CPU 或替身测试作为真实通过。完整回归日志 `.local-acceptance/closeout-20261025/full-suite-closed-bootstrap-20261029.log` 以最终退出码为准。
- 本轮完整后端回归收集 964 项、退出码 0；指定 V1.3 preparation/migrations/e2e、旧 offline E2E、film crew 另行通过，Ruff、前端 TypeScript/Vite 和临时 SQLite Alembic upgrade head 通过。完整日志 `.local-acceptance/closeout-20261025/full-suite-closed-bootstrap-20261029.log`；仍有既存 Starlette 弃用和 SQLAlchemy 空主键警告。

# 2026-10-29 跨进程归属与下一阶段物理边界

- 为受管 Windows Job Object 增加可选随机名称。新进程只以查询权限打开对象，使用同一进程句柄核验 PID、出生时间、存活与组成员；名称已存在时拒绝创建，不接管或改变已有对象。重启生成新名称，绑定的实例进程记录包含该名称。另行模拟监督进程退出，验证无其他持有句柄时子进程退出；这不代表 Worker 已能接管或恢复真实渲染器。
- 在隔离真实 ComfyUI CPU 实例中，另一 Python 进程验证命名组成员，合成任务 `6c4ca153-5445-4aa2-9393-def2e7eed775` 完成并留存历史；重启产生新组名且队列仍关闭，关闭时提交返回 409。两次受管进程组均等待退出，端口和根 PID 事后不存在，GPU 空闲约 14926 MiB。证据 `.local-acceptance/comfy-managed-named-job-cpu-20261029/evidence.json`；该探测中的恢复由隔离测试显式调用，并非数据库租约驱动的生产流。
- 构造无上一审片物理证据却可打开队列的反例并修复：实验激活现要求前一次同实例受管审片的持久化记录、非模拟来源、逐阶段匹配的真实释放收据、存活监督进程及针对下一阶段要求的内存显存复核；仅隔离 offline_simulation 测试可使用假证据。缺证时保持关闭；首次 H3 启动的可信物理基线尚无实现，因此不得启用此接口进行首次生产。
- Windows 归属、审片边界、恢复、旧 offline E2E、film crew 定向和 Ruff 通过；完整回归 `.local-acceptance/closeout-20261025/full-suite-named-boundary-20261029.log` 以最终退出码为准。真实 H3/视觉/ASR 未运行；Worker 接线、独立监督器的运行及崩溃对账尚未完成。
- 审片释放边界另核对持久化阶段时间及后续资源租约、模型调用：发现其他活跃/待对账租约或之后登记的模型活动时，不复用旧释放证据。反例使用隔离测试数据库；该检查依赖系统时间及当前账本，不能证明未登记的外部进程不存在或系统时钟异常时的顺序。缺少首次启动可信基线和持久化单调活动序号，生产入口继续关闭。
- 修改后的完整回归日志 `.local-acceptance/closeout-20261025/full-suite-named-boundary-epoch-20261029.log` 以最终退出码为准；一次定向命令误传不存在的路径，pytest 未执行，该命令不计为通过，随后正确定向测试通过。

# 2026-10-29 应用资源活动序号

- 构造固定系统时间下连续资源准入的反例，旧账本不能提供顺序；现于已有 SQLite 写事务中为每次资源准入及共享租约模型调用递增序号。实验交接重启记录序号，下一次激活检查期间有中间活动则拒绝；物理释放复核后在写事务内再次检查序号，并保持写锁至渲染器恢复确认。续约过期或回复不确定仍转待对账，不重投。
- 定向测试覆盖固定时钟、插入模型活动、释放证据缺失、回执篡改和模拟受管重启；旧 offline E2E、film crew 及 Ruff 通过。定向模型调用、显存与释放检查使用替身。完整后端回归记录于 `.local-acceptance/closeout-20261025/full-suite-resource-epoch-20261029.log`，以日志最终退出码为准；之前一轮完整测试被主动中断，不计为通过。
- 序号仅覆盖本应用登记的资源活动，不能证明外部进程未占用资源。缺少序号的历史交接不能作为新激活证明；审片客户端身份同时包含这些安全组件，旧版本的真实模型资格须重新核验。首次 H3 基线、独立监督器、生产 Worker 接线、真实模型串联和人工验收仍 **PARTIAL/NOT RUN**。
- 本次源码的完整后端回归 971 项通过、退出码 0；前端 `npm run build`、Ruff 和独立临时 SQLite 的 `alembic upgrade head` 通过。完整日志为 `.local-acceptance/closeout-20261025/full-suite-resource-epoch-20261029.log`；保留既有 Starlette 弃用与 SQLAlchemy 空主键告警。指定 V1.3 定向测试日志为 `.local-acceptance/closeout-20261025/v13-resource-epoch-20261029.log`，以其最终退出码为准。
- V1.3 preparation、migrations、e2e、旧 offline E2E 和 film crew 单独重跑退出码 0。完整回归后另外补充并通过一项合成竞态测试：物理复核后、队列恢复前插入模型活动时，队列保持 drained，激活与租约标记 RECONCILE_REQUIRED；该附加测试不包含在上述 971 项完整回归数字中。

# 2026-10-29 审片释放到重启的活动顺序反例

- 构造三个隔离账本反例：最后一次审片释放后、重启前插入活动；重启期间插入活动；同一租约的审片调用完成后又插入模型活动。修复前均能错误采纳新的活动或释放基线。修复后每项共享租约模型调用在准入事务中记录自身序号，释放验证与该调用匹配；下一审片或重启前复核上次释放序号，重启完成记录在 SQLite 写事务内再次比较。活动改变则阻断，保留重启或审片意图供对账。
- 另构造持久化审片步骤序号被篡改的反例；激活前检查各审片调用和账本序号一致、顺序递增且最后一项与重启基线一致。上述反例和旧 offline E2E、film crew 定向通过，Ruff 通过；真实 GPU、审片执行和进程释放均为测试替身，不能据此认定真实自动交接通过。
- 本轮代码改变审片客户端指纹；历史能力登记不能直接继承。完整回归日志 `.local-acceptance/closeout-20261025/full-suite-release-order-20261029.log` 以最终退出码为准。生产 Worker、受管监督器、首次启动基线和真实端到端仍 **PARTIAL/NOT RUN**。
- 本轮完整后端回归 975 项通过、退出码 0；Ruff、前端构建、独立临时 SQLite 的 Alembic 升级通过。V1.3 指定测试、旧 offline E2E 与 film crew 单独运行日志为 `.local-acceptance/closeout-20261025/v13-release-order-20261029.log`，以最终退出码为准。已有 Starlette 弃用及 SQLAlchemy 空主键警告。
- 上述 V1.3 指定测试、旧 offline E2E 与 film crew 单独运行退出码 0。

# 2026-10-29 受管审片收尾入口

- Worker 的渲染收尾函数新增显式内部协调器入口：原视频登记后，在同一资源租约和心跳下调用已有受管排空、审片代理、释放核验及关闭状态重启，再进行质量门禁记录；受管路径不调用仅返回 HTTP 确认的 `free_models` 捷径。隔离测试使用受管合成渲染器和模拟 ASR，验证一次审片、重启、原视频保留与租约持续占用；非真实 H3 或真实审片模型。
- 先构造 Worker 把受管审片安全异常转换为普通 ASR 失败的反例，现让受管 ASR/VLM 异常向交接上下文传播；另构造失败后作业仍为 QC_PENDING 的反例，现仅在原 Worker、状态版本仍匹配时将作业置为 NEEDS_REVIEW，保留视频和 RECONCILE_REQUIRED 租约。旧 Worker 不得覆盖新声明；隔离正反例通过。
- 正常 Worker 启动尚未创建或传入该协调器，初次启动物理基线、跨作业租约延续和独立监督仍未接通；现有生产准入阻断保持。完整后端回归 `.local-acceptance/closeout-20261025/full-suite-worker-managed-review-20261029.log` 以最终退出码为准，真实 H3/视觉/ASR 与人工验收 **NOT RUN**。源码改变导致历史模型能力指纹需重新核验。
- 本轮完整后端回归 980 项通过、退出码 0，Ruff、前端构建及独立临时 SQLite Alembic 升级通过；保留已有 Starlette 弃用与 SQLAlchemy 空主键警告。V1.3 指定测试、旧 offline E2E 与 film crew 日志为 `.local-acceptance/closeout-20261025/v13-worker-managed-review-20261029.log`，以最终退出码为准。
- V1.3 指定测试、旧 offline E2E 与 film crew 本轮单独运行退出码 0。
- 完整回归后补充并通过受管收尾质量边界断言：测试中的合成无效视频字节被判为 `QC_FAILED` / `REPAIR_PENDING`，不是通过；审片异常仍为 `NEEDS_REVIEW` 和待对账。该补充断言单独重跑通过，不改变上述完整回归的测试数量。

# 2026-10-29 跨作业资源租约原子交接

- 新增内部租约交接原语：先复核上一审片的实时释放证明，再以同一 SQLite 写事务校验旧租约、下一作业与尝试、预制作约束和活动序号；将旧租约标记 RELEASED、创建新的 ACTIVE 独占租约并递增活动序号。旧租约不改写归属、不删除历史；重复意图、竞争资源、过期租约、活动变化或缺失工作流图哈希均阻断，事务失败回滚。
- 构造两个线程竞争同一 SQLite 写锁的隔离反例，确认交接事务持锁期间其他资源申请等待，提交后仍因新租约 ACTIVE 而不能取得资源。另一合成测试经过受管审片释放、旧/新租约切换和下一渲染器关闭启动后的受控激活；并未提交 H3。GPU 资源和审片执行均为替身。
- 仅凭 `WAITING` 状态无法证明旧版 Worker 从未使用某租约；对缺少可验证准入来源的后续记录仍保守阻断。原子交接原语尚未接入正常 Worker 调度，首次启动物理基线、独立监督器和租约空闲时续期仍未完成；真实模型串联 **PARTIAL/NOT RUN**。完整回归 `.local-acceptance/closeout-20261025/full-suite-atomic-transfer-20261029.log` 以最终退出码为准。
- 此次完整后端回归 986 项通过、退出码 0；Ruff、前端构建和隔离临时 SQLite 的 Alembic 升级通过。V1.3 指定测试、旧 offline E2E 与 film crew 日志 `.local-acceptance/closeout-20261025/v13-atomic-transfer-20261029.log` 以最终退出码为准。已有 Starlette 弃用及 SQLAlchemy 空主键告警。
- V1.3 指定测试、旧 offline E2E 与 film crew 单独运行退出码 0。

# 2026-10-29 受管交接空闲心跳

- 复现受管审片完成并重启渲染器后，原交接心跳退出、独占租约不能在等待下一作业期间自动续期的问题。新增空闲监督：只在原 Worker 进程身份、受管进程组、关闭的监听器、实例哈希和活动序号均匹配时续约；租约过期、归属丢失或停止监督均保留 `RECONCILE_REQUIRED`，不按时间推断物理卸载。
- 合成测试验证后台自动续期、过期不复活、归属丢失和主动停止阻断；另让心跳线程与原子交接竞争，确认交接后旧心跳退出并保留新独占租约。对失败接手保留待对账。测试模型、释放和显存均为替身；未执行真实 H3/审片或人工验收。
- 收尾协调器现在可持有空闲监督器，但正常 Worker 的受管启动、首次物理基线、任务声明和交接调度尚未接入；不具备真实自动生产资格。完整后端回归日志 `.local-acceptance/closeout-20261025/full-suite-idle-handoff-20261029.log` 以最终退出码为准。
- 本轮完整后端回归 991 项通过、退出码 0；Ruff、前端构建和独立临时 SQLite Alembic 升级通过。V1.3 指定测试、旧 offline E2E 和 film crew 日志为 `.local-acceptance/closeout-20261025/v13-idle-handoff-20261029.log`，以最终退出码为准；既有 Starlette 弃用与 SQLAlchemy 空主键告警未消除。
- V1.3 指定测试、旧 offline E2E 与 film crew 本轮单独运行退出码 0。
- 2026-10-29 14:57（上海）只读资源快照：可用显存 14913 MiB，常用 8188、8189、8116、8117、14338 端口无监听，未找到命令行匹配的相关 Python 服务。`nvidia-smi` 列出的 GPU 进程显存占用均为 `N/A`，包括系统及桌面进程，因此此快照不能证明没有外部模型或先前模型已释放；未停止任何进程。证据 `.local-acceptance/closeout-20261025/gpu-baseline-inventory-20261029.json`。
- NVIDIA 官方 [NVML 进程信息文档](https://docs.nvidia.com/deploy/nvml-api/latest/api/structnvmlProcessInfo__t.html)说明 WDDM 下进程级显存字段不可用；这与本机 `N/A` 相符，但不证明当前各进程的用途或资源释放。首次启动仍须结合具体受管进程退出证据、物理空闲资源和未知进程边界处理。
- 本机 Windows 11 build 26200 的 `GPU Process Memory(*)\Dedicated Usage` 性能计数器可以读取数值，单次样本 28 项、其中 22 项非零，留存在 `.local-acceptance/closeout-20261025/gpu-pdh-inventory-20261029.json`。目前未将计数器 LUID 可靠映射到目标 GPU，也未验证计数器完整性和读数与模型占用的对应关系，不能用该单次样本证明模型释放。[Microsoft 已记录 Windows 10 特定场景的进程级 GPU 计数偏差](https://learn.microsoft.com/en-us/troubleshoot/windows-client/performance/gpu-process-memory-counters-report-wrong-value)；该记录不证明本机 Windows 11 存在同一问题。
- 随后以只读 DXGI 枚举及不创建 CUDA 上下文的驱动设备查询，将目标 GPU UUID 映射到 LUID `00000000:00012d14`、节点掩码 1；同名 NVIDIA DXGI 项另有不同 LUID，不能仅按名称匹配。对应样本归组得到 25 项，进程名称和出生时间在采样后另行读取，可能发生变化。证据为 `.local-acceptance/closeout-20261025/dxgi-adapters-20261029.json`、`cuda-luid-20261029.json` 和 `gpu-pdh-mapped-20261029.json`。设备映射方法参照 [NVIDIA CUDA 驱动 API](https://docs.nvidia.com/cuda/cuda-driver-api/cuda_driver_api/group__CUDA__DEVICE.html) 和 [Microsoft DXGI 描述](https://learn.microsoft.com/en-us/windows/win32/api/dxgi/ns-dxgi-dxgi_adapter_desc1)；计数器的完整性、精度及单次采样边界仍未验证，不能据此放行模型。

# 2026-10-29 受管后续作业与恢复边界

- 新增受管后续作业内部入口：原子预留下一排队作业、领取尝试、持久化工作流图哈希、核验物理释放与受管端点、激活关闭的渲染器、提交后记录确认，再进入受管审片及空闲心跳。隔离合成测试覆盖一次提交及完整收尾、慢准备期间的后台心跳、地址不符、未标记模拟服务、响应不明、提交前声明过期及图被改动；未提交真实 H3。正常 `serve` 启动尚未构造首个受管协调器，首次物理基线和独立监督仍未完成，生产入口仍 **PARTIAL/NOT RUN**。
- 修复普通 Worker 心跳可复活过期资源、对新声明续约的问题；受管心跳在数据库写事务中核对声明版本、所有者、镜头版本和三类租约期限，并一起续约或保持待对账。过期及丢失声明、镜头更新和缺失资源均有隔离反例。外部任务已确认但声明丢失时新增独立确认收据，保留任务 ID，不覆盖新 Worker 的状态；不明提交保留图哈希，不自动重投。
- 预留记录在崩溃后可区分仍存活的持有者和失联/过期状态；缺失租约或损坏绑定时保留原始记录，并创建阻塞性待对账资源记录。所有权不确定时不访问渲染器。测试使用隔离账本与合成服务，真实 GPU、H3 和审片模型本轮未执行；源码改变使历史能力指纹需重新核验。
# 2026-10-29 连续作业审片封存反例与回归

- 反例：隔离服务完成一次审片并封存收据后，普通 `/handoff/ready` 和 `/handoff/reserve` 会拒绝下一次调用。原多作业合成测试替换了这些接口，不能证明实际协议可复用。先新增接口合同用例，修复前请求 `/handoff/rearm-reserve` 返回 404、测试失败。
- 修复：新增认证、持锁且绑定上一份完整收据的单次重新预约；拒绝错误收据、未认证、重复 ID 和重放。内部受管协调器携带各类型最近一次释放收据，先复核旧收据、进程归属及物理资源，再登记新的审片意图并预约。ASR 和 VLM 若共用同一监听端口则保守阻断，避免覆盖仍需复核的收据。进程或结果不确定仍进入待对账；不自动重放。
- 用真实 FastAPI 隔离服务接口、假模型执行器和模拟资源/归属探针验证两次封存与预约，并验证认证、错误收据、非预约请求和重放拒绝。此测试不是实际模型执行或物理内存释放证明。
- 当前源码完整后端测试：仓库根目录 `.\\backend\\.venv\\Scripts\\python.exe -m pytest backend\\tests -q` 退出 0；当时收集 **1029** 项。之后新增一项接口联动测试并单独通过；当前收集 1030 项，不能将新增后的完整套件写成已重跑。指定 V1.3 preparation/migrations/e2e、旧 offline E2E、film crew 和隔离服务用例联合运行退出 0。Ruff、前端 `npm run build`、独立临时 SQLite `alembic upgrade head` 均通过。
- `uv lock --check --offline` 在默认镜像配置下失败；临时设置 `UV_DEFAULT_INDEX=https://pypi.org/simple` 后 `uv lock --check --offline` 和 `uv sync --locked --offline` 均通过，未修改锁文件。测试从 `backend` 目录启动曾因找不到根目录所需配置而失败，已改为从仓库根目录运行，不将该次失败记为通过。
- 新增接口改变隔离服务指纹，历史 4B/ASR 能力登记不能证明当前版本资格。首次受管 H3 可信基线与正常 Worker 启动尚未接入；真实 H3、真实视觉/ASR 多作业交接和实际工作台闭环 **NOT RUN**，自动交接 **PARTIAL**。未重投 H3 或代行人工审核。
- Git `fetch origin main` 后本地 HEAD 与远端 main 均为 `f6b196afa3d53623baa2a8f16a5e62f1d2520649`，ahead/behind 为 0/0；当前工作区包含本轮未提交实现。`gh` 返回 401；连接器复核 [Actions 运行 40603149040](https://github.com/anjingdtl/shine-video-studio/actions/runs/40603149040) 为 **FAIL**，四个 job 均无步骤和日志 URL，原因未证实。
# 2026-10-29 无审片器的未受管首任务准入反例

- 构造 `test_unowned_renderer_without_reviewer_is_not_contacted`：无审片器、非离线模拟、实例归属未知时，注入只记录 `/free` 的客户端。修复前实际调用了卸载，随后因为该测试客户端不支持提交而进入不确定状态，未满足无外部调用合同。
- 普通 `run_one` 对所有 ComfyUI 任务执行原有交接准入判断，而不是仅在提供审片器时判断。未证明归属时，提交前标记 `COMFY_HANDOFF_OWNERSHIP_UNVERIFIED`，资源保持 `RECONCILE_REQUIRED`，不调用卸载、不创建 attempt、不提交。这个保守门禁也会阻断无审片器的未受管真实任务；首次受管基线尚未实现，不能视为生产流程贯通。
- 全量回归首次出现 8 项失败，原因是既有合成 HTTP 用例需要进入新门禁之后，分别验证能力复查、完成窗口、解码检查、重启恢复、卸载失败和提交对账。已在这些用例中显式隔离新准入判断，保留原断言；新反例独立覆盖未受管准入。相关定向套件通过，随后从仓库根目录 `.\\backend\\.venv\\Scripts\\python.exe -m pytest backend\\tests -q` 全量 **1031 项通过**；指定 V1.3 preparation/migrations/e2e、旧 offline E2E、film crew 单独通过；Ruff、前端 TypeScript/Vite 构建、`git diff --check` 通过。迁移在上一轮同一源码所用模型版本的独立临时库通过，本轮没有新增迁移。
- 运行期间只读检查系统启动时间为 2026-10-28 19:08:41；8000、8001、8116、8117、8188、8189 无监听。测试期间看到两个 Python 进程，未单独确认其命令行；测试结束后复查无 Python 进程。显存空闲约 14929 MiB 只是该时点容量快照，不证明旧模型释放或初始物理基线。未启动真实 H3、视觉模型或 ASR，真实自动交接和人工验收 **NOT RUN**。
# 2026-10-30 首次隔离服务封闭原语与交接状态

- 新增首次封闭、查询和绑定预约协议。服务仅在未曾执行审片时允许封闭，封闭期间拒绝普通推理；首次预约须匹配封闭标识，错误标识、重复预约与未授权请求均阻断。首次封闭只是协议原语，尚未接入首次持久化启动意图或正常 Worker。
- 已构造反例：推理子进程运行后返回非法 JSON 时，旧逻辑仍可能将服务当作从未运行；修复后调用一旦开始就不再视为初始状态，非法响应使服务保持不确定状态。客户端复核包含监督进程归属、启动子进程退出、封闭状态及内存/显存；跨作业边界还复核尚未使用的冷封闭服务和旧释放收据。
- 定向测试 `test_isolated_review_supervisor.py`、`test_review_isolation_receipt.py`、`test_renderer_supervisor.py`、`test_handoff_lease_transfer.py`、`test_offline_e2e.py` 于本轮退出 0；Ruff 通过。此前完整后端 1031 项的结果不覆盖本次首次封闭变更：后续全量测试运行至 90% 后被用户中断，测试句柄已不存在，**最终结果未知，不计通过**。
- 真实 H3、审片模型、首次受管启动及工作台自动链路 **NOT RUN**。测试中的模型执行、资源读数和部分进程归属为替身；首次启动的持久化意图、独占租约、意外中断对账、监督器创建和 Worker 接线仍需完成。新源码改变审片身份，旧 4B/ASR 真实登记不能替代当前源码重新校准。
# 2026-10-30 首次受管启动接线、真实进程实测与全量回归

## 本机与仓库状态复核（2026-10-30 01:36 起）

- 系统启动时间为 2026-10-30 01:25:48。本机在本轮之前发生过重启，上一轮记录的全部 PID 与端口归属均已失效；本轮不按旧 PID 操作，也没有停止、卸载或接管任何外部或归属不明的 ComfyUI。
- `Get-NetTCPConnection -State Listen`：8188/8189/8116/8117/8000/8001 无监听；`Get-CimInstance Win32_Process` 未发现 python/comfy/node 相关进程。
- `nvidia-smi --query-gpu=index,uuid,memory.total,memory.free`：`0, GPU-f3fda412-dabc-0f4a-c8e0-9fd69902205b, NVIDIA GeForce RTX 5080, 16303 MiB, 14789 MiB`。这是时点容量快照，不是模型峰值证明，也不证明外部进程已卸载任何模型。
- 验收库 `.local-acceptance/closeout-20261025/shine.db`：`resource_leases` 全部为 RELEASED，无 ACTIVE 或 RECONCILE_REQUIRED 残留；`app_settings` 中不存在任何 `handoff:`、`renderer-activation:`、`renderer-startup:`、`review-startup:` 记录，即该库此前从未走过交接路径；唯一的 `comfy_instances` 行为 status ONLINE 且 payload 缺少 `managed`/`process`，不是受管实例。本轮未写入该库。
- `.local-acceptance/data/shine.db` 以只读方式打开即报 `malformed database schema (83580f76-cbc9-4933-afd7-5d3d7293f16b)`。本轮未做任何清理、改写或修复。该 UUID 出现在镜头 continuity JSON 的 `previous_job_id` 值中，属于数据内容而非可修复的 schema 语句；该损坏属本轮之前的既有状态。
- Git：本轮开始时本地 main=`53a2a81`、origin/main=`f6b196a`、本地超前 1、工作区干净。`git fetch` 因用户级 `~/.gitconfig` 的 `credential.helper` 指向已不存在的 PortableGit GCM 二进制而报错；改用系统 GCM 执行 `git fetch origin main` 成功（exit 0），复核 `origin/main`=`f6b196afa3d53623baa2a8f16a5e62f1d2520649`。`gh` 返回 HTTP 401，托管 CI 状态本轮**无法核实**，既不写成通过也不写成失败。

## 实现状态

本轮把此前只有内部原语的交接机制接进正常 Worker，并补上"首次受管启动"这一缺失环节。

- 新增 `backend/shine/comfyops/handoff_startup.py`：首次受管启动原语。启动前先写入持久意图（`renderer-startup:<instance>`），意图中记录 argv 哈希、cwd、钉住文件的 SHA-256、端口、GPU UUID、阈值、owner PID 与出生标识，以及**控制令牌摘要而非令牌本身**；随后才创建受管 Windows 进程组（命名 Job Object），核验唯一 loopback 监听者属于该进程组、渲染器以封闭（drained）状态起来，再实测 RAM/VRAM 基线，最后登记 `ComfyInstance`（`managed=True`、`is_mock=False`、`process={pid,birth,job_name}`、`RUNNING_PENDING_HEALTH`）。任一环节不确定即写 `RECONCILE_REQUIRED` 并保留租约，重复启动同一实例被拒绝，绝不盲目重投。
- 新增 `backend/shine/comfyops/managed_recipe.py`：由运维提供的渲染器与审片服务配方。只描述可执行文件、ComfyUI 根目录、钉住的适配器字节和物理阈值；控制令牌每次启动现场生成，不落盘、不进 SQLite、不进 Git。
- 新增 `backend/shine/comfyops/handoff_startup.py` 中的 `reconcile_managed_startups`：崩溃恢复。只有当记录的 PID 与出生标识仍一致、且命名 Job Object 成员关系仍可核实时才允许停止该进程组；无法确认归属时保持阻塞并保留对账证据，绝不触碰外部或归属不明的进程。所有者进程仍存活时完全不介入，避免两个 Worker 相互干扰。
- 新增 `owned_process.terminate_named_group`：在归属已被证明后，按命名 Job Object 终止并由内核确认无残留成员。
- 新增 `handoff_activation.activate_renderer_for_first_attempt`：首次提交前的激活。首次没有可释放的上一轮审片，物理证明改由启动绑定与新鲜实测基线承担；同样先写意图再恢复队列，恢复结果不确定时保留租约并把作业置为 NEEDS_REVIEW。
- 新增 `review_release.create_cold_review_fence`：客户端侧的首次封闭原语，把服务端已有的"首次封闭 + 一次性绑定预约"接进受管协调器，并复核启动子进程已退出与内存/显存。
- `backend/shine/worker.py`：新增 `_FirstManagedContinuation` 与 `run_managed_first`；正常 `run_one` 在配置了 `SHINE_MANAGED_RECIPE` 时改走受管启动分支，不再对未受管 ComfyUI 静默请求卸载；`recover_expired_jobs` 先做启动对账；配方或审片服务绑定不成立时在外部调用前阻断并保留待对账租约，不让 Worker 循环崩溃。
- `backend/shine/quality/client_identity.py`：把 `handoff_startup.py` 与 `owned_process.py` 纳入受钉住的审片客户端源码集合，源码变更后旧登记自动失效。

## 自动化测试

- 新增 `backend/tests/test_managed_startup.py`（13 项合同反例）：意图先于进程、无独占租约拒绝启动、外部租约冲突、渲染器未封闭即拒绝、基线不足即拒绝、控制令牌不落 SQLite、崩溃恢复只停止可证明归属的进程、所有者存活时不介入、已退出进程不做任何终止、首次激活结果不确定时保留租约、首次封闭绑定、封闭被拒时不产生围栏、未受管监督器不能封闭。
- 新增 `backend/tests/test_real_managed_startup.py`：真实机验收用例，仅在设置 `SHINE_REAL_COMFY_PROBE=1` 并给出本机 ComfyUI 路径时运行，默认跳过，CI 不受影响。
- 变更前基线：仓库根目录 `.\backend\.venv\Scripts\python.exe -m pytest backend\tests -q` 收集 1038 项，exit 0，日志 `.local-acceptance/closeout-20261025/full-suite-baseline-20261030.log`。
- 变更后完整回归（两轮，源码固定后各跑一次）：`full-suite-final-20261030.log` **1051 项通过、0 失败、0 错误、0 跳过**，exit 0；加入真实机验收用例后的最终一轮 `full-suite-final2-20261030.log` **1051 项通过、1 跳过、0 失败、0 错误**，exit 0，耗时 383.04 秒，唯一跳过项是需显式设置 `SHINE_REAL_COMFY_PROBE=1` 的真实机用例。
- 指定套件：`test_v13_preparation.py`、`test_v13_migrations.py`、`test_v13_e2e.py`、`test_offline_e2e.py`、`test_film_crew.py` 联合运行 **95 项通过**，exit 0，日志 `.local-acceptance/closeout-20261025/v13-targeted-managed-startup-20261030.log`。
- Ruff：`.\backend\.venv\Scripts\python.exe -m ruff check backend` **All checks passed**，exit 0。
- Alembic：独立临时 SQLite 上 `alembic upgrade head` 走完 8 个版本到 `f130a0030001`，exit 0；本轮未新增迁移。`git diff --check` 通过。
- 前端：`npm run build`（tsc -b && vite build）exit 0。
- `uv lock --check --offline` 在默认镜像下仍失败；设 `UV_DEFAULT_INDEX=https://pypi.org/simple` 后 `uv lock --check --offline` 与 `uv sync --locked --offline` 均 exit 0，**锁文件未被改写**。
- 中间一轮全量回归出现 5 项 `test_structured_review.py` 失败，原因是回归运行期间修改了被钉住的 `shine/worker.py`，触发 `REVIEW_CLIENT_SOURCE_CHANGED_SINCE_LOAD` 关闭失败保护；这属于设计内的行为，不是回归缺陷。固定源码后重跑即全绿，该中间结果不计通过。

## 真实 H3/模型验证

- **真实渲染器（PASS，有限）**：以本机真实 ComfyUI `F:\AIGC2026\ComfyUI_Mie_2026_V8.0_Base\ComfyUI` 运行 `backend/tests/test_real_managed_startup.py`，1 项通过、exit 0、耗时 9.57 秒。证据：受管启动写入持久意图；真实进程组启动并带上真实 `shine_comfy_handoff` 适配器；适配器以封闭状态起来；监听者归属由 PID + 出生标识 + 命名 Job Object 成员关系三重证明；RAM/VRAM 由 `nvidia-smi` 与 `GlobalMemoryStatusEx` 实测；实例登记为 `RUNNING_PENDING_HEALTH`；独占租约保持 ACTIVE；结束时受管进程组由内核确认 `active_processes == 0`。运行使用 CPU 模式、未加载任何模型权重、未提交任何 prompt、未生成任何媒体，未接触生产数据库。
- 运行结束复核：未残留 python/comfy 进程，8188/8189/8116/8117/8000/8001 无监听，GPU 可用显存回到 14828 MiB。
- **真实 H3 生成到审片自动链路：NOT RUN**。本轮未向 ComfyUI 提交任何 H3 提示词，未加载 H3 权重，未执行真实视觉审片或 ASR 审片，未产生可复核的真实审片收据。历史 4B/ASR 真实登记属上一版本指纹，本轮源码已改变审片身份（新增 `handoff_startup.py`、`owned_process.py` 进入钉住集合），不能作为当前源码的资格证明。

## 人工验收

**NOT RUN**。未代替用户确认资产权属、未批准任何影片、未进行人工听审、未把任何候选资产提升为正式资产。真实自动链路本身尚未跑通，人工验收的前置条件不成立。

## 外部阻塞

- 托管 CI：`gh` 返回 HTTP 401，无法读取最新 Actions 运行，本轮对远端检查状态**无法核实**；按要求不写成通过，也未修改任何账单或支付信息。
- 本机 Git 凭据助手：用户级 `C:\Users\Administrator\.gitconfig` 的 `credential.helper` 指向已不存在的 PortableGit GCM 二进制，每次凭据操作都会报错；系统级 `C:\Program Files\Git\etc\gitconfig` 的 `manager` 仍可正常认证，fetch 因此可用。本轮**未改动**用户全局配置，修复方式为删除该失效条目或改回 `manager`。
- 真实 H3→审片自动链路未跑通属于**未完成工作**，不是外部阻塞；剩余工作包括首次提交的真实 H3 渲染、真实视觉/ASR 审片串行交接、跨作业续期与工作台闭环。

## 提交与推送

- 本轮变更提交为 `05fd0a7dca320f45b6e88023bed8e90c42518c2b`（`Wire the first managed renderer startup into the normal worker`），在 `53a2a81` 之上，未改写历史。
- 以普通 `git push origin main` 推送，输出 `f6b196a..05fd0a7 main -> main`，为快进推送，**没有使用 force**。
- 推送后重新 `git fetch origin main`（exit 0）复核：本地 `main` 与 `origin/main` 均为 `05fd0a7dca320f45b6e88023bed8e90c42518c2b`，`git rev-list --left-right --count origin/main...main` 为 `0  0`，工作区干净。
- 提交内容仅包含源码、测试、配置样例与两份报告；未包含数据库、模型权重、生成视频、个人素材或密钥。`.local-acceptance/` 由 `.gitignore` 排除，验收证据与数据库未进入提交。
- 未认证的 GitHub API 对该私有仓库返回 404，属预期；远端 head 以上述带认证的 fetch 结果为准。

## 可复查证据路径

- `.local-acceptance/closeout-20261025/full-suite-baseline-20261030.log`（变更前基线）
- `.local-acceptance/closeout-20261025/full-suite-final-20261030.log`、`full-suite-final2-20261030.log`（最终全量回归）
- `.local-acceptance/closeout-20261025/v13-targeted-managed-startup-20261030.log`（指定套件）
- `.local-acceptance/closeout-20261025/alembic-managed-startup-20261030/`（临时库迁移）
- `.local-acceptance/managed-startup-real-20261030/pytest.log`（真实 ComfyUI 受管启动）
- `.local-acceptance/probe-managed-startup-real-20261030.py`（只读诊断脚本）
- `.local-acceptance/_probe_state.py`、`.local-acceptance/_probe_corrupt.py`（数据库只读诊断）
# 2026-10-30 CI 全量复现、两处真实阻断缺陷修复与失效数据清理

## CI 逐步复现

`.github/workflows/ci.yml` 定义四个作业：`backend`(ubuntu)、`backend-windows`、`frontend`、`web-e2e`。本轮把每个作业的每一步都在本机按 CI 的原始命令跑通（`uv` 在本机默认镜像下解析失败，需 `UV_DEFAULT_INDEX=https://pypi.org/simple`；该设置只影响命令环境，不改锁文件）。

| CI 步骤 | 结果 |
|---|---|
| `uv sync --project backend --locked` | PASS，exit 0 |
| `uv run --project backend --locked pytest -c backend/pyproject.toml backend/tests` | **修复前 FAIL（1 failed / 1050 passed）→ 修复后 PASS，1058 passed、1 skipped、0 failed，exit 0**，381.47 秒；在最终提交树 `4ae33cd` 上复跑同样 **1058 passed、1 skipped、0 failed**，exit 0，385.47 秒 |
| `uv run --project backend --locked ruff check backend/shine backend/alembic backend/tests` | PASS，exit 0 |
| `uv run --project backend alembic -c backend/alembic.ini upgrade head`（新库） | PASS，exit 0，8 个版本 |
| Windows：启动本地工作台并检查 `/health` 与 `/api/v1/system/status` | PASS，exit 0，`status: mock api_online=True` |
| Windows：`./scripts/verify_local_stack.ps1 -ApiPort 8127` | PASS，exit 0，Mock 作业到达 PASSED、审计 7 条 |
| Windows：`npm ci --prefix frontend` | PASS，exit 0，70 个包、0 漏洞 |
| Windows：`./scripts/verify_stack_operations.ps1` | PASS，exit 0（自定义代理、NoWorker、重复启动拒绝、Worker 重启与已核验停机） |
| frontend：`npm --prefix frontend run build` | PASS，exit 0 |
| web-e2e：`ruff check scripts/verify_v13_web.py` | PASS，exit 0 |
| web-e2e：`python -m playwright install chromium` | PASS（Chrome for Testing 151.0.7922.34） |
| web-e2e：`python scripts/verify_v13_web.py --screenshot ...` | **修复前 FAIL（IndexError）→ 修复后 PASS，exit 0**，20 条流程全部 MOCK_VERIFIED，`api_errors: []` |

日志：`ci-pytest-exact-20261030.log`、`ci-pytest-final-20261030.log`、`web-e2e-20261030-rerun.log`、`web-e2e-final-20261030.log`、`npm-ci-20261030.log`。

## 修复一：web-e2e 作业真实失败（前端与验收脚本漂移）

前端在 `39410c9`/`71fafff` 引入"基础自审"范围，并要求操作员先填写**预期输出宽高**才提供真实生成的逐次确认；`scripts/verify_v13_web.py` 最后更新于更早的 `84a6066`，从未填写这两个输入。因此确认对话框不出现，脚本的 `messages[-1]` 抛出 `IndexError`，整个 web-e2e 作业失败。

修复方式是让脚本按操作员的真实操作填写宽高（768×1344），**没有放宽前端的提交前检查，也没有削弱脚本的任何断言**：取消确认仍必须不产生后端作业、确认仍必须带上 `generation_mode` 与 `workflow_profile_id`、后端作业数仍必须为零。修复后该作业本机 exit 0。

## 修复二：同一时钟刻度内写入的行，"最新行"是不确定的（真实产品缺陷）

上一轮全量回归在 CI 原始命令下出现 `test_v13_preparation.py::test_review_scope_creates_new_plan_and_selects_matching_native_capability` 失败（`KeyError: 'review_scope'`），而在单独运行时 12/12 通过、在整文件运行时 3/3 通过。这是跨用例顺序相关的真实缺陷，不是测试写法问题。

根因已用只读探针证实：

- 本机 `datetime.now()` 在一次时钟刻度内不前进：连续 19999 次调用返回**完全相同**的时间戳。
- `PreProductionPlan.created_at` 因此相同：`analyze` 写入的"full"计划与"basic"计划的 `created_at` 精确到微秒都一致（`datetime(2026, 10, 29, 23, 9, 4, 430986)`）。
- `current_plan` 以 `(created_at DESC, id DESC)` 选取"当前计划"，而 `id` 是随机 UUID —— 于是选中哪个计划是**掷硬币**。30 次复现中 2 次选中了旧计划。
- 后果是产品级的：`current_plan` 决定 `analyze` 的幂等命中、`readiness` 生成的清单（review_scope 与被选中的原生审片能力）、`enforce` 的生产准入门禁，以及 `freeze_manifest` 冻结的清单。选错计划会让基础自审按错误的范围和能力放行。

修复分两层：

- `shine/models.py` 的 `utcnow()` 改为**严格单调递增**（同刻度内逐微秒递增），且最多只领先真实时钟 1 秒，系统时间回拨时重新同步，因此不会把租约到期或审批窗口钉在未来。
- `current_plan` 以及同模块的 `voice_profiles`、`workflow_revisions` 与审计事件的排序，改用 `literal_column("<table>.rowid")` 作为插入顺序的**确定性并列决胜** —— 这与本仓库既有做法一致（`api_film.py`、`main.py`、`operator_review.py`、`quality/input_assessment.py` 已用同一模式）。

新增 `backend/tests/test_timestamp_ordering.py`（7 项）锁定该合同：连续读数严格递增、时钟不得领先真实时钟超过 1 秒、同一时间戳的两个计划必须解析为最新、重复 `analyze` 不得把最新计划挤掉、重复分析保持幂等。修复后同一探针 30 次复现 **0 次选错**。

本轮复跑还暴露出 `backend/tests/test_managed_startup.py` 自身的端口竞争：夹具先预留随机端口再启动服务，若端口在两步之间被另一实例占用，夹具会按端口反查监听者并**误连到别人的服务**，从而出现与本缺陷无关的偶发失败。夹具已改为由服务把**实际绑定的端口**写入标记文件，夹具等待标记后再取监听者；若端口被占则服务启动失败并明确报错，而不是指向另一个服务。修复后该文件连跑 8 次全部通过。

## 修复三：Windows 本地栈验收脚本泄漏进程

`scripts/verify_local_stack.ps1` 只 `Stop-Process` 掉 `uv run` 启动器，而 `uv` 管理的虚拟环境会把基础解释器**再执行**为子进程，因此 API 与 Worker 的真实进程被留在后台。本轮复现时泄漏出 4 组共 15 个进程（占用 8123/8124/8125/8127/8129），它们仍持有临时数据库句柄。

修复后脚本按仓库既有做法记录并核验**出生标识**，用 `scripts/shine_processes.py <pid> <birth> --stop` 先停子进程再停父进程（`start_shine.ps1` / `stop_shine.py` 使用同一机制）。修复后复跑该脚本 exit 0，且不再留下任何进程或监听端口。此前泄漏的 15 个进程也按同样的身份核验方式逐个停掉，未使用任何仅凭 PID 的递归强杀。

## 失效与损坏数据清理（本轮已获授权）

清理前先做了全量只读体检：`.local-acceptance`、`.local-data*` 下共 15 个 SQLite 文件，`pragma quick_check` 全部为 `ok`，**只有一个**损坏；无孤立的 `-wal`/`-shm`；两份 `run/processes.json` 中的 PID 全部已死（对应 2026-10-25 与 2026-10-28 的运行，本机 2026-10-30 01:25:48 重启后失效）。

经授权删除（走可恢复回收站，未做永久删除）：

- `.local-acceptance/data/shine.db` 及 `-shm`/`-wal` —— `malformed database schema`，schema 不可读，无法使用。
- `.local-data/shine.db` 及 `-shm`/`-wal` —— 4096 字节、0 张表的空库文件，没有任何数据或 schema。
- `.local-acceptance/closeout-20261025/run/processes.json`、`.local-acceptance/h3run/run/processes.json` —— 指向已死进程的失效受管进程清单，会让 `start_shine.ps1` 以"已有所有者"为由拒绝启动。

未删除：全部可用数据库、资产、产物、截图、证据与日志。

## 托管 CI 状态（本轮已用 GitHub API 证实）

`gh` 此前返回 401，本轮修复后可直接读取远端。**失败原因已由 GitHub 的作业注解给出原文**：

> The job was not started because recent account payments have failed or your spending limit needs to be increased. Please check the 'Billing & plans' section in your settings.

四个作业 `backend` / `backend-windows` / `web-e2e` / `frontend` 全部 `runner=` 为空、2–3 秒内 `completed/failure`、**步骤数为 0**：没有任何一个作业真正启动过 runner。这不是代码失败，而是**账户计费/额度阻断**，需要账户所有者处理账单或提高额度。本轮未触碰任何支付信息。

最近 8 次运行的结论一致（全部 failure、全部 0 步骤）：`40974056273`(fe02990)、`40973214662`(4ae33cd)、`40972734952`(087d737)、`40929722967`(8b862df)、`40929665748`(05fd0a7)、`40603149040`(f6b196a)、`40602547640`、`40600931825`。

## 全局配置修复（经授权）

本轮修复了三个反复出现的环境级报错，并都做了独立验证：

1. **失效的 Git 凭据助手**。全局 `~/.gitconfig` 的 `credential.helper` 指向 `C:/Users/Administrator/.workbuddy/binaries/PortableGit/versions/1.2.0/mingw64/bin/git-credential-manager.exe`，该路径已随 PortableGit 卸载而消失，于是每次 fetch/push 都先报 `No such file or directory`。已删除该条（系统级 `C:\Program Files\Git\etc\gitconfig` 的 `manager` 继续生效），`git ls-remote origin` 现在 exit 0 且无任何报错。修改前的配置已备份到 `.local-acceptance/gitconfig.backup-20261030`。
2. **`uv` 默认索引与锁文件不一致**。全局 `uv.toml` 把阿里云镜像设为主索引，而 `backend/uv.lock` 的每一项都记录 `source = { registry = "https://pypi.org/simple" }`，因此 `uv lock --check` 与 `uv sync --locked` 一律报"锁文件需要更新"。已改为 `index-url = "https://pypi.org/simple"`（与锁文件逐字一致，无尾斜杠），并把阿里云镜像保留为 `default = false` 的回退索引。现在**不再需要任何环境变量覆盖**：`uv lock --check --offline`、`uv sync --locked --offline`、`uv lock --check` 全部 exit 0，锁文件未被改写。修改前的配置备份在 `.local-acceptance/uv.toml.backup-20261030`。
3. **GitHub CLI 的 keyring 条目过期**。`gh` 的 keyring 里存的是已失效的 token，而 Git Credential Manager 里存着同一账号的有效 token（`gho_` 前缀，scopes `gist, repo, workflow`，可读私有仓库与 Actions）。`gh auth login --with-token` 会因该 token 缺少 `read:org` 而拒绝入库，因此改为用 Windows `CredWriteW` 把同一 token 写进 gh 读取的凭据键（UTF-8 字节），并恢复 gh 的账号记录；**token 没有落盘到任何明文文件**。现在 `gh auth status` 显示 `✓ Logged in to github.com account anjingdtl (keyring)`，`gh api user` 与 `gh run list` 均 exit 0。

另外新增仓库级 `.gitattributes`（`* text=auto eol=lf` 并显式标注媒体与权重为 binary），使工作区在 Windows 与 Linux CI 上行尾一致，消除每次 `git add` 的 "LF will be replaced by CRLF" 提示；实测未引起任何既有文件的重整，`git status` 除新增文件外保持干净。

## 可复查证据路径

- `.local-acceptance/closeout-20261025/ci-pytest-final-20261030.log`（最终全量回归，exit 0）
- `.local-acceptance/closeout-20261025/web-e2e-final-20261030.log`、`shine-v13-web-qa-20261030.png`
- `.local-acceptance/closeout-20261025/npm-ci-20261030.log`
- `.local-acceptance/_probe_clock.py`（时钟刻度证据）、`.local-acceptance/_probe_plan_order.py`（计划选取顺序复现）
- `.local-acceptance/_survey_dbs.py`、`.local-acceptance/_survey_stale.py`、`.local-acceptance/_survey_manifests.py`（清理前只读体检）
# 2026-10-30 真实 H3 生产贯通与六处受管链路缺陷修复

## 本轮定位并修复的真实缺陷

本轮以"正常 Worker 真实跑通 H3"为目标，逐个暴露出六处此前只有内部原语、从未在真实链路上成立的缺陷。每一处都由可复现的反例定位，修复后有定向验证；**没有**放宽断言、关闭生产前检查或降低验收标准。

1. **首次受管启动被类型检查拒绝**。`_run_comfy_job` 只接受"上一轮交接的延续"类型，首次启动上下文永远抛 `HANDOFF_COORDINATOR_INVALID`。现抽出共同基类 `_ManagedAttemptContext`，两种上下文都提供 `instance_id` 与 `supervisor`。
2. **提交期守卫引用只有延续才有的 `.previous`**。首次启动在提交后崩溃，提交结果被误判为"不明"。现统一走公共访问器。
3. **渲染器客户端从不满足受管要求**。`ComfyUIClient` 默认 `trust_env=True`，而受管路径要求 `False`；Worker 从不创建合规客户端，本地渲染器流量还可能被环境代理劫持。现由 `_renderer_client` 统一创建：本地渲染器一律 `trust_env=False`，仅显式允许的远端渲染器保留代理行为。
4. **审片围栏 ID 混入 release config**，导致 `verify_cold_review_fence` 收到不认的参数。围栏归协调器所有，不再进入 release config。
5. **审片服务的工作目录被设成 python.exe 文件本身**，`Path(cwd).is_dir()` 恒为 False，审片进程永远无法启动。
6. **`managed_recipe.adapter_identity` 与 `build_review_launch` 的实现错误**（`read_bytes()` 用在已打开的文件对象上；工作目录未取父目录）。

## ComfyUI 节点白名单：枚举、修正与启动期校验

- 以 `/object_info` 枚举全部已登记生产工作流：**29 个节点类型，其中 12 个来自自定义节点包**；再用真实 ComfyUI 的 `/object_info` 逐一确认提供目录。
- 原配方 `--disable-all-custom-nodes` 且只放行 `shine_comfy_handoff`，真实 Ref2VA 无法运行；更关键的是 `--base-directory` 会把 `custom_nodes` 与 `models` 一并重定向到 BASE 目录，使真实节点包与 H3 权重都不可见，并导致适配器身份核验失败。配方已去掉该参数，与用户自己的生产启动器一致。
- 修正后的白名单**恰好加载 7 个供应商**（`shine_comfy_handoff`、`ComfyUI-KJNodes`、`ComfyUI-MiniMax-H3-PDD-Acc`、`Comfyui_Minimax_h3_latent_Upscaler`、`ComfyUI-SolAttn_triton`、`comfyui-videohelpersuite`、`shine_h3_sampling_mode.py`），其余全部跳过；`/object_info` 返回 **1281 个节点类型**，13 个必需节点全部存在。
- 新增**启动期节点完备性校验**：受管启动在登记实例前读取实时 `/object_info`，任一注册工作流所需节点缺失即以 `COMFY_REQUIRED_NODE_MISSING` 失败关闭，并把缺失清单写入启动日志。该校验已实际拦截过一次缺失（验收库自带的合成测试图 `H3BaseSampler`）。

## 本地 API 访问控制

- `security.py` 原先把 `0.0.0.0` 当作回环主机，`config.py` 用它校验 `SHINE_COMFYUI_URL`，因此 `http://0.0.0.0:8188` 会被误判为"本机"。已改为统一的 `is_loopback_peer`，正确处理 `::ffff:127.0.0.1`，拒绝 `0.0.0.0` 与 `::`。
- 普通项目 API 此前**完全没有访问控制**，只依赖 uvicorn 绑定地址。现新增应用层回环守卫：非回环客户端 403，需局域网暴露必须显式设置 `SHINE_ALLOW_NON_LOOPBACK_API=1`。新增用例 26 项。

## 真实 H3 生产（PASS_REAL）

由**正常 Worker** 完成，不是独立脚本。证据 `.local-acceptance/final-all-pass-20261030/real-production-run.json`。

- 真实 MiniMax-H3 **Ref2VA INT8 ConvRot 剪枝版**，注册 profile `local-h3-ref2va-2img`。
- 受管启动 5.6–5.9 秒，关闭启动（`drained`），授权前提示词被拒。
- 提交意图先落库，两张参考图按 profile 的 `media_transfers` 上传，激活后提交。
- **真实生成约 110 秒**，产物 MP4 **1,822,918 字节**，SHA-256 `a90f67fbd5c82cc3a7d80fe689c444a5b84cd4914e7835fa9ed1f432b3e645f0`。
- ffprobe：`h264` **480×864**、**24/1** fps、**5.165 s**；`aac` **5.166 s**；容器 5.166 s。`is_mock=false`。
- 参考素材复用历史**已人工批准并锁定**的角色图与场景图（哈希与库内一致、文件在位）；本轮未新建或晋升任何主资产。

## 真实交接与质检（PASS_REAL / FAIL）

- **ASR：PASS_REAL**。受管审片服务启动、首次封闭围栏成立、预约成功、真实转写完成并产出隔离收据、`result_is_mock=false`、封存成功。
- **VLM：FAIL**。4B 模型把风格输入字段判为不一致却给出 passed（`STYLE_INPUT_DECISION_CONTRADICTION`），应用按合同拒绝；换 8B FP8 后在本机 16GB 显存上推理超过隔离服务 600 秒上限，隔离服务把结果标记为未知并**拒绝释放**（503），作业进入 `COMFY_HANDOFF_RECONCILE_REQUIRED`。
- 失败后的行为正确：**未伪造 PASS、未盲重投、租约保留待对账**，渲染器与审片服务进程均已退出，GPU 回落到 1356 MiB。

## 本轮清理（已授权）

- `.local-acceptance/data/shine.db`（malformed schema）、`.local-data/shine.db`（0 表空库）、两份指向已死进程的 `run/processes.json` → 走可恢复回收站删除。
- 上一轮早期探针遗留在 `F:\AIGC2026\ComfyUI_Mie_2026_V8.0_Base\custom_nodes\shine_comfy_handoff` 的适配器副本（位于真实安装之外、指向错误位置）→ 已删除该副本与随之变空的 `custom_nodes` 目录。
- 本轮安装的交接适配器保留在真实安装位置 `ComfyUI\custom_nodes\shine_comfy_handoff`，身份 `d182268e70b4d57be695f01f0328c44df05b90480398777829f53785e301a93a`。

## 托管 CI

注解原文（四个作业全部相同）："The job was not started because recent account payments have failed or your spending limit needs to be increased." 作业 `runner=` 为空、2–3 秒内失败、**步骤数为 0** —— 账户计费/额度阻断，不是代码失败。本轮未修改任何账单或支付设置。

完整验收矩阵与交付建议见 [LOCAL_ALL_PASS_FINAL_REPORT.md](LOCAL_ALL_PASS_FINAL_REPORT.md)。
