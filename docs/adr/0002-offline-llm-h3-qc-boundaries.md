# ADR 0002: offline M2/M3 boundaries

- Status: accepted for this build
- Date: 2026-10-21

## Decision

1. Add one LLM gateway with provider profiles outside the business logic and role routes stored in SQLite. API keys live outside SQLite in DPAPI/`0600` storage and never appear in API responses or audit payloads.
2. Make `H3PromptCompiler` the only ShotSpec → prompt path, pinned to the official `h3-prompt-writing` rule version/hashes. Agents propose structured ShotSpecs, never final prompts.
3. Ship a generic ComfyUI HTTP adapter and strict workflow-profile validation. Do not invent H3 node mappings without the user's exported API workflow. Real H3 execution remains `PENDING_LOCAL_H3`.
4. Separate deterministic technical QC from probabilistic VLM/ASR review. Missing tools produce `unavailable`, not `pass`. Fusion escalates low-confidence/uncertain evidence and never auto-passes a hard failure.
5. Add a typed, bounded repair engine. Auto-apply only transient/prompt/workflow-class fixes when the project opts in; escalate creative, reference, and low-confidence failures.

## Rationale

This lets the project be fully exercised with Mock and Fake HTTP servers while preserving honest boundaries for real MiniMax-H3, real provider keys, and GPU validation. It also keeps a single job state machine, one asset registry, one frozen style authority, and one audit trail.

## Consequences

- Real H3 workflow mapping, GPU performance, output quality, and long-shot continuation remain pending local acceptance.
- The API remains unauthenticated and loopback-only.
- A generic workflow template is a development aid, not proof of H3 compatibility.
- Mock/fake-server success is never presented as real video quality.
