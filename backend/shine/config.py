from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

from shine.security import is_loopback_url


def _env_int(name: str, default: int) -> int:
    raw = os.getenv(name)
    if raw is None or not raw.strip():
        return default
    try:
        return int(raw)
    except ValueError as exc:
        raise ValueError(f"{name} must be an integer") from exc


def _env_float(name: str, default: float) -> float:
    raw = os.getenv(name)
    if raw is None or not raw.strip():
        return default
    try:
        return float(raw)
    except ValueError as exc:
        raise ValueError(f"{name} must be a number") from exc


@dataclass(frozen=True)
class Settings:
    data_dir: Path
    database_url: str
    max_upload_bytes: int = 25 * 1024 * 1024
    worker_lease_seconds: int = 30
    worker_heartbeat_seconds: int = 10
    worker_offline_after_seconds: int = 30
    comfyui_base_url: str = "http://127.0.0.1:8188"
    render_provider: str = "mock"
    allow_remote_comfyui: bool = False
    secret_store_dir: Path | None = None
    ffprobe_path: str = "ffprobe"
    ffmpeg_path: str = "ffmpeg"
    offline_simulation: bool = False
    local_voice_url: str = ""
    operator_id: str = ""
    operator_token_sha256: str = ""

    @classmethod
    def from_env(cls) -> Settings:
        repo_root = Path(__file__).resolve().parents[2]
        load_dotenv(repo_root / ".env", override=False)
        configured_dir = Path(os.getenv("SHINE_DATA_DIR", repo_root / ".local-data"))
        if not configured_dir.is_absolute():
            configured_dir = repo_root / configured_dir
        data_dir = configured_dir.resolve()
        database_url = os.getenv("SHINE_DATABASE_URL") or f"sqlite:///{(data_dir / 'shine.db').as_posix()}"
        render_provider = os.getenv("SHINE_RENDER_PROVIDER", "mock").strip().lower()
        if render_provider not in {"mock", "comfyui"}:
            raise ValueError("SHINE_RENDER_PROVIDER must be 'mock' or 'comfyui'")
        allow_remote = os.getenv("SHINE_ALLOW_REMOTE_COMFYUI", "").strip().lower() in {"1", "true", "yes"}
        comfyui_base_url = os.getenv("SHINE_COMFYUI_URL", "http://127.0.0.1:8188").strip()
        if not allow_remote and not is_loopback_url(comfyui_base_url):
            raise ValueError(
                "SHINE_COMFYUI_URL must point at this machine. "
                "Set SHINE_ALLOW_REMOTE_COMFYUI=1 to allow a remote renderer explicitly."
            )
        return cls(
            data_dir=data_dir,
            database_url=database_url,
            max_upload_bytes=_env_int("SHINE_MAX_UPLOAD_BYTES", 25 * 1024 * 1024),
            worker_lease_seconds=_env_int("SHINE_WORKER_LEASE_SECONDS", 30),
            worker_heartbeat_seconds=_env_int("SHINE_WORKER_HEARTBEAT_SECONDS", 10),
            worker_offline_after_seconds=_env_int("SHINE_WORKER_OFFLINE_SECONDS", 30),
            comfyui_base_url=comfyui_base_url,
            render_provider=render_provider,
            allow_remote_comfyui=allow_remote,
            secret_store_dir=data_dir / "secrets",
            ffprobe_path=os.getenv("SHINE_FFPROBE", "ffprobe"),
            ffmpeg_path=os.getenv("SHINE_FFMPEG", "ffmpeg"),
            local_voice_url=os.getenv("SHINE_LOCAL_VOICE_URL", ""),
            offline_simulation=os.getenv("SHINE_OFFLINE_SIMULATION", "") == "1",
            operator_id=os.getenv("SHINE_OPERATOR_ID", ""),
            operator_token_sha256=os.getenv("SHINE_OPERATOR_TOKEN_SHA256", ""),
        )
