# Local environment

Shine reads optional overrides from the repository-root `.env` file and from
process environment variables. The `.env.example` contains only non-secret local
paths. No model API credentials are used by the current M0/M1 Mock workflow.

| Variable | Default | Purpose |
| --- | --- | --- |
| `SHINE_DATA_DIR` | `.local-data` | SQLite database and content-addressed user asset storage |
| `SHINE_DATABASE_URL` | SQLite file under `SHINE_DATA_DIR` | Optional SQLAlchemy URL override |

The API and Vite development server bind to `127.0.0.1` by default.
