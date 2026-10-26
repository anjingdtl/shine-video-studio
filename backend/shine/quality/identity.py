"""Checked local executor response metadata; no assets or credentials."""
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class ExecutorIdentity(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)
    schema_version: Literal['1']
    model_id: str = Field(min_length=1, max_length=200)
    files_sha256: str = Field(pattern=r'^[a-f0-9]{64}$')
    runtime_sha256: str = Field(pattern=r'^[a-f0-9]{64}$')
    file_count: int = Field(gt=0)
    bytes: int = Field(gt=0)
