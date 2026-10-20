from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv


@dataclass(frozen=True)
class Settings:
    data_dir: Path
    database_url: str
    max_upload_bytes: int = 25 * 1024 * 1024

    @classmethod
    def from_env(cls) -> Settings:
        repo_root = Path(__file__).resolve().parents[2]
        load_dotenv(repo_root / ".env", override=False)
        configured_dir = Path(os.getenv("SHINE_DATA_DIR", repo_root / ".local-data"))
        if not configured_dir.is_absolute():
            configured_dir = repo_root / configured_dir
        data_dir = configured_dir.resolve()
        database_url = os.getenv("SHINE_DATABASE_URL") or f"sqlite:///{(data_dir / 'shine.db').as_posix()}"
        return cls(data_dir=data_dir, database_url=database_url)
