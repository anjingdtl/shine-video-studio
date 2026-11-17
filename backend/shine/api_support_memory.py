"""V2.2 local long-term memory API.

Reads are bounded projections of ``copilot_memories``.  Writes are explicit,
loopback-only user data management through the same operator boundary as the
rest of the workbench; natural-language turns cannot call these endpoints.
"""
from __future__ import annotations

from typing import Annotated, Literal

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, ConfigDict, Field

from shine.api_common import DBSession, app_settings, llm_gateway
from shine.api_operator import Operator
from shine.config import Settings
from shine.support import memory as memory_service
from shine.support.memory import index as memory_index
from shine.support.memory import store as memory_store

router = APIRouter(prefix="/api/v1/support", tags=["ai-copilot-memory"])
SettingsDep = Annotated[Settings, Depends(app_settings)]
GatewayDep = Annotated[object, Depends(llm_gateway)]

MemoryType = Literal[
    "PREFERENCE",
    "PROJECT_DECISION",
    "CREATIVE_HABIT",
    "CONSTRAINT",
    "EXPERIENCE",
    "SUMMARY",
]


class MemoryCreateBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    scope: Literal["GLOBAL", "PROJECT"] = "GLOBAL"
    project_id: str | None = Field(default=None, max_length=36)
    content: str = Field(min_length=1, max_length=2000)
    memory_type: MemoryType = "SUMMARY"


class MemoryUpdateBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    content: str | None = Field(default=None, min_length=1, max_length=2000)
    memory_type: MemoryType | None = None


class MemoryClearBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    scope: Literal["PROJECT", "AUTO"]
    project_id: str | None = Field(default=None, max_length=36)
    confirm: bool = False


class MemoryExtractBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    session_id: str = Field(min_length=1, max_length=36)
    force: bool = True


def _http(code: str, message: str, status: int = 400) -> HTTPException:
    return HTTPException(status_code=status, detail={"code": code, "message": message})


def _memory_error(exc: memory_service.MemoryInvalid) -> HTTPException:
    status = 404 if exc.code in {"MEMORY_NOT_FOUND", "PROJECT_NOT_FOUND"} else 400
    return _http(exc.code, exc.message, status)


@router.get("/memories")
def support_memories(
    db: DBSession,
    scope: Literal["GLOBAL", "PROJECT"] | None = None,
    project_id: str | None = None,
    q: str = Query(default="", max_length=200),
    limit: int = Query(default=100, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
):
    return memory_store.list_memories(
        db,
        scope=scope,
        project_id=project_id,
        q=q,
        limit=limit,
        offset=offset,
    )


@router.get("/memories/status")
def support_memory_status(db: DBSession):
    try:
        return memory_store.status_payload(db)
    except Exception as exc:  # noqa: BLE001 - the page must still explain a broken index
        return {
            "state": "CORRUPT",
            "fts5": "UNAVAILABLE",
            "error": type(exc).__name__,
            "counts": {"active": 0},
            "privacy": {
                "delete_effect": "删除会立即移除检索条目；索引可在配置页重建。",
            },
            "notice": "本地记忆索引暂不可用；AI 管家聊天与视频生产不受影响。",
        }


@router.post("/memories", status_code=201)
def support_memory_create(body: MemoryCreateBody, actor: Operator, db: DBSession):
    try:
        result = memory_store.create_memory(
            db,
            scope=body.scope,
            project_id=body.project_id,
            content=body.content,
            memory_type=body.memory_type,
            source="MANUAL",
            confirmed=True,
            actor=actor,
        )
    except memory_store.MemoryInvalid as exc:
        raise _memory_error(exc) from exc
    return result


@router.post("/memories/clear")
def support_memory_clear(body: MemoryClearBody, actor: Operator, db: DBSession):
    if not body.confirm:
        raise _http("CONFIRMATION_REQUIRED", "清除记忆必须明确确认。", 400)
    try:
        result = memory_store.clear_memories(
            db,
            scope=body.scope,
            project_id=body.project_id,
            actor=actor,
        )
    except memory_store.MemoryInvalid as exc:
        raise _memory_error(exc) from exc
    return {**result, "status": memory_store.status_payload(db)}


@router.post("/memories/rebuild")
def support_memory_rebuild(actor: Operator, db: DBSession):
    result = memory_index.rebuild(db)
    from shine.services import add_audit

    add_audit(
        db,
        actor,
        "SUPPORT_MEMORY_INDEX_REBUILT",
        "copilot_memory_index",
        "memory-index",
        {"memories": result.get("memories"), "fts5": result.get("fts5"), "vectors": result.get("vectors")},
    )
    db.commit()
    return {"rebuild": result, "status": memory_store.status_payload(db)}


@router.post("/memory/extract")
def support_memory_extract(
    body: MemoryExtractBody,
    actor: Operator,
    settings: SettingsDep,
    db: DBSession,
    gateway: GatewayDep,
):
    result = memory_service.extract_session(
        db,
        settings,
        session_id=body.session_id,
        gateway=gateway,
        actor=actor,
        force=body.force,
    )
    if result.get("status") == "REMOTE_CONSENT_REQUIRED":
        raise _http(
            "REMOTE_CONSENT_REQUIRED",
            "当前模型在远端；请先在「AI 管家配置 → 隐私与记忆管理」允许远端提炼。",
            409,
        )
    return result


@router.get("/memories/{memory_id}")
def support_memory_get(memory_id: str, db: DBSession):
    try:
        return {"memory": memory_store.get_memory(db, memory_id)}
    except memory_store.MemoryInvalid as exc:
        raise _memory_error(exc) from exc


@router.patch("/memories/{memory_id}")
def support_memory_update(
    memory_id: str,
    body: MemoryUpdateBody,
    actor: Operator,
    db: DBSession,
):
    try:
        result = memory_store.update_memory(
            db,
            memory_id,
            content=body.content,
            memory_type=body.memory_type,
            actor=actor,
        )
    except memory_store.MemoryInvalid as exc:
        raise _memory_error(exc) from exc
    return result


@router.delete("/memories/{memory_id}")
def support_memory_delete(memory_id: str, actor: Operator, db: DBSession):
    try:
        result = memory_store.delete_memory(db, memory_id, actor=actor)
    except memory_store.MemoryInvalid as exc:
        raise _memory_error(exc) from exc
    return {**result, "status": memory_store.status_payload(db)}
