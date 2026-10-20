# ADR 0001: local state owner and explicit mock provider

- Status: accepted for M0/M1
- Date: 2026-10-20

## Decision

Use one FastAPI modular monolith and one SQLite WAL database for project, asset, style, shot, job, attempt, QC, and audit state. Run a separate Python worker against that database. Keep asset bytes in a content-addressed local directory. M1 exposes only an explicitly labelled Mock H3 provider; the real production adapter is reserved for local MiniMax-H3 through ComfyUI.

## Rationale

This gives development a real, inspectable state machine and recoverable mock workflow without a GPU, Redis, a second application database, or third-party service. A persisted submit intent and conservative review state establish the contract that a future ComfyUI adapter must preserve.

## Consequences

Mock QC validates scheduler and API behavior only; it does not produce media or prove H3 quality. Real ComfyUI queue/history reconciliation, technical video QC, LLM provider profiles, and Windows/GPU acceptance remain future work. Keep the API loopback-only until authentication and authorization are implemented.
