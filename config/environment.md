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
| `SHINE_COMFYUI_URL` | `http://127.0.0.1:8188` | Local ComfyUI API endpoint |
| `SHINE_MAX_UPLOAD_BYTES` | `26214400` (25 MiB) | Configurable upload limit for reference media |
| `SHINE_WORKER_LEASE_SECONDS` | `30` | Job lease duration |
| `SHINE_WORKER_HEARTBEAT_SECONDS` | `10` | Worker heartbeat interval |
| `SHINE_WORKER_OFFLINE_SECONDS` | `30` | Age after which a worker is reported offline |
| `SHINE_FFPROBE` | `ffprobe` | Probe executable. Missing tools are reported as unavailable, never passed |
| `SHINE_FFMPEG` | `ffmpeg` | FFmpeg executable for silence detection and later delivery work |

The API and Vite development server bind to `127.0.0.1`. There is no user
authentication yet; do not expose the API to a LAN or the internet. Remote LLM/VLM
calls require an explicit provider profile, and remote semantic review additionally
requires the project-level `allow_remote_review` switch.

Provider keys must not be placed in `.env`. Use the Web settings panel or
`PUT /api/v1/provider-profiles/{id}/key`.
