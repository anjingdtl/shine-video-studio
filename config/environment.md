# Local environment

Shine reads optional overrides from the repository-root `.env` file and from process
environment variables. `.env.example` contains only non-secret local paths. Provider
API keys are stored outside the database through the Windows DPAPI secret store (or a
permission-restricted `0600` file on non-Windows hosts) and are never returned by the API.

| Variable | Default | Purpose |
| --- | --- | --- |
| `SHINE_DATA_DIR` | `.local-data` | SQLite database, content-addressed user assets, artifacts, and secret files |
| `SHINE_DATABASE_URL` | SQLite file under `SHINE_DATA_DIR` | Optional SQLAlchemy URL override |
| `SHINE_RENDER_PROVIDER` | `mock` | `mock` or `comfyui`. `comfyui` is only valid after local H3 acceptance |
| `SHINE_COMFYUI_URL` | `http://127.0.0.1:8188` | Local ComfyUI API endpoint. It must point at this machine unless `SHINE_ALLOW_REMOTE_COMFYUI=1` is set |
| `SHINE_ALLOW_REMOTE_COMFYUI` | unset | Explicit opt-in for a non-loopback ComfyUI. Reference bytes are uploaded to the renderer, so a remote renderer is a cloud transmission of user media |
| `SHINE_MAX_UPLOAD_BYTES` | `26214400` (25 MiB) | Configurable upload limit for reference media |
| `SHINE_WORKER_LEASE_SECONDS` | `30` | Job lease duration |
| `SHINE_WORKER_HEARTBEAT_SECONDS` | `10` | Worker heartbeat interval |
| `SHINE_WORKER_OFFLINE_SECONDS` | `30` | Age after which a worker is reported offline |
| `SHINE_FFPROBE` | `ffprobe` | Probe executable. Missing tools are reported as unavailable, never passed |
| `SHINE_FFMPEG` | `ffmpeg` | FFmpeg executable. It is used for the silence check, the full decode check and, when FFprobe is missing, for the stream metadata |
| `SHINE_OPERATOR_ID` / `SHINE_OPERATOR_TOKEN_SHA256` | unset | Human operator identity and credential digest, provisioned in the local process environment; never store plaintext credentials in `.env` |
| `SHINE_ENABLE_LOCAL_EXECUTION` | unset | Explicit `1` enables approved Windows local install/instance execution; it does not approve a plan |
| `SHINE_VOICE_SDK_PACKAGES` | unset | JSON mapping `design`, `base`, `asr`, `align` to exact installed ModelPackage IDs |
| `SHINE_VOICE_ROLE_RUNTIMES` | unset | JSON mapping those roles to pinned direct Python/SDK/runtime directories and admission budgets; see the operator guide |

The API and Vite development server bind to `127.0.0.1`. General CRUD has no
account login; protected operations require local human operator authentication
and applicable one-use grants. Keep the API on loopback. Remote LLM/VLM
calls require an explicit provider profile, and remote semantic review additionally
requires the project-level `allow_remote_review` switch.

Provider keys must not be placed in `.env`. Use the Web settings panel or
`PUT /api/v1/provider-profiles/{id}/key`.

`config/voice-role-runtimes.json.example` is an inert role-runtime template.
Its paths, zero digests and Torch version placeholder must be replaced with
reviewed local values. Its 12000 MiB budget is an admission example, not a
measurement or model compatibility result. TTS and ASR use separate dependency
directories because their pinned Transformers versions differ. Configure any
required helper binaries with exact paths/digests as described in the operator
guide. The template is never loaded automatically and approves no package.

After reviewing and saving it as ignored `config/local.voice-role-runtimes.json`,
an administrator can set the process JSON before starting the API:

```powershell
$env:SHINE_VOICE_ROLE_RUNTIMES = Get-Content -LiteralPath './config/local.voice-role-runtimes.json' -Raw
```

`SHINE_VOICE_SDK_PACKAGES` separately selects the exact installed ModelPackage
IDs. Operator rights, per-request voice execution consent and frozen voice/master
approvals continue to apply. This example supplies no credentials or model weights.
