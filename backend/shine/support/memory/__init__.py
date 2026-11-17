"""V2.2 persona/local-memory facade.

The package deliberately stays inside ``shine.support``: it reuses the one
``AppSetting`` configuration store, the one ``SupportSession`` conversation
ledger, the one LLM Gateway and the one SQLite database.
"""
from __future__ import annotations

from shine.support.memory import embedding
from shine.support.memory.budget import (
    CONTEXT_PRIORITY,
    MAX_PROMPT_CHARS,
    MEMORY_NOTICE,
    fit_prompt_payload,
)
from shine.support.memory.extraction import (
    EXTRACTION_BATCH,
    auto_extract_due,
    extract_background,
    extract_session,
    provider_is_remote,
)
from shine.support.memory.index import index_status, rebuild
from shine.support.memory.profile import (
    DEFAULT_NICKNAME,
    DEFAULT_SALUTATION,
    DEFAULT_STARTUP_MEMORY,
    LIMITS,
    MAX_NICKNAME,
    MAX_SALUTATION,
    MAX_STARTUP_MEMORY,
    PROFILE_KEY,
    ProfileInvalid,
    preferences_payload,
    read_profile,
    update_preferences,
)
from shine.support.memory.store import (
    MAX_RECALL,
    MEMORY_TYPES,
    MemoryInvalid,
    clear_memories,
    create_memory,
    delete_memory,
    get_memory,
    list_memories,
    retrieve,
    status_payload,
    update_memory,
)

__all__ = [
    "CONTEXT_PRIORITY",
    "DEFAULT_NICKNAME",
    "DEFAULT_SALUTATION",
    "DEFAULT_STARTUP_MEMORY",
    "EXTRACTION_BATCH",
    "LIMITS",
    "MAX_NICKNAME",
    "MAX_PROMPT_CHARS",
    "MAX_RECALL",
    "MAX_SALUTATION",
    "MAX_STARTUP_MEMORY",
    "MEMORY_NOTICE",
    "MEMORY_TYPES",
    "MemoryInvalid",
    "PROFILE_KEY",
    "ProfileInvalid",
    "auto_extract_due",
    "clear_memories",
    "create_memory",
    "delete_memory",
    "embedding",
    "extract_background",
    "extract_session",
    "fit_prompt_payload",
    "get_memory",
    "index_status",
    "list_memories",
    "preferences_payload",
    "provider_is_remote",
    "read_profile",
    "rebuild",
    "retrieve",
    "status_payload",
    "update_memory",
    "update_preferences",
]
