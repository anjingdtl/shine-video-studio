"""CRUD and hybrid recall for the copilot's local long-term memory.

Only ``copilot_memories`` is authority.  FTS5 and vectors are rebuilt from it;
a failed index degrades recall quality and never blocks the copilot or video
production.  Every recall is filtered by the real project identity supplied by
the caller, never by a project name the model guessed.
"""
from __future__ import annotations

import hashlib
import re
import unicodedata
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.exc import SQLAlchemyError

from shine.models import Project, utcnow
from shine.support.memory import embedding
from shine.support.memory import index as memory_index
from shine.support.memory.profile import MAX_MEMORY_CONTENT, read_profile
from shine.support.models import CopilotMemory, SupportMessage
from shine.support.privacy import redact_text

MEMORY_TYPES = (
    "PREFERENCE",
    "PROJECT_DECISION",
    "CREATIVE_HABIT",
    "CONSTRAINT",
    "EXPERIENCE",
    "SUMMARY",
)
SCOPES = ("GLOBAL", "PROJECT")
SOURCES = ("MANUAL", "AUTO")
STATUSES = ("ACTIVE", "SUPERSEDED", "DELETED")

MAX_RECALL = 6
RECALL_CONTENT_CHARS = 500
_NEAR_DUPLICATE = 0.93
_EXACT_DUPLICATE = 0.985
_CONTROL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")
_SCRIPT = re.compile(r"(?is)<\s*/?\s*(?:script|iframe|object|embed|svg|img|link|meta)\b")
_UPDATE_SIGNAL = re.compile(r"改为|改成|不再|现在|以后|更新|换成|不要|取消|停止|改成|修正")


class MemoryInvalid(ValueError):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code
        self.message = message


def normalise_content(value: str) -> str:
    text = unicodedata.normalize("NFKC", value or "").strip()
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text


def content_hash(value: str) -> str:
    return hashlib.sha256(normalise_content(value).casefold().encode("utf-8")).hexdigest()


def validate_content(value: Any, *, max_length: int = MAX_MEMORY_CONTENT) -> str:
    if not isinstance(value, str):
        raise MemoryInvalid("INVALID_CONTENT", "记忆内容必须是文本。")
    text = normalise_content(value)
    if len(text) < 2:
        raise MemoryInvalid("CONTENT_TOO_SHORT", "记忆内容太短，至少 2 个字符。")
    if len(text) > max_length:
        raise MemoryInvalid("CONTENT_TOO_LONG", f"单条记忆最多 {max_length} 个字符。")
    if _CONTROL.search(text):
        raise MemoryInvalid("CONTROL_CHARS", "记忆内容含有不可见控制字符。")
    if _SCRIPT.search(text):
        raise MemoryInvalid("HTML_NOT_ALLOWED", "记忆内容不能包含可执行网页标签。")
    if redact_text(text, limit=20_000) != text:
        raise MemoryInvalid("SECRET_DETECTED", "记忆内容看起来包含密钥或本机敏感路径，已拒绝保存。")
    return text


def validate_memory_type(value: Any) -> str:
    if value not in MEMORY_TYPES:
        raise MemoryInvalid("INVALID_MEMORY_TYPE", "未知的记忆分类。")
    return str(value)


def _scope_and_project(db, scope: str, project_id: str | None) -> tuple[str, str | None]:
    if scope not in SCOPES:
        raise MemoryInvalid("INVALID_SCOPE", "记忆范围只能是全局或项目。")
    if scope == "GLOBAL":
        return "GLOBAL", None
    if not project_id:
        raise MemoryInvalid("PROJECT_REQUIRED", "项目记忆必须带真实项目 ID。")
    if db.get(Project, project_id) is None:
        raise MemoryInvalid("PROJECT_NOT_FOUND", "项目不存在，不能写入项目记忆。")
    return "PROJECT", project_id


def _serialise(
    db,
    memory: CopilotMemory,
    *,
    include_sources: bool = True,
    score: float | None = None,
) -> dict[str, Any]:
    project_title = None
    if memory.project_id:
        project = db.get(Project, memory.project_id)
        project_title = project.title if project is not None else None
    payload = {
        "id": memory.id,
        "scope": memory.scope,
        "project_id": memory.project_id,
        "project_title": project_title,
        "content": memory.content,
        "memory_type": memory.memory_type,
        "source": memory.source,
        "source_session_id": memory.source_session_id,
        "source_message_ids": list(memory.source_message_ids or []),
        "content_revision": memory.content_revision,
        "status": memory.status,
        "confirmed": memory.confirmed,
        "supersedes_id": memory.supersedes_id,
        "created_at": memory.created_at.isoformat(),
        "updated_at": memory.updated_at.isoformat(),
        "deleted_at": memory.deleted_at.isoformat() if memory.deleted_at else None,
    }
    if score is not None:
        payload["score"] = round(float(score), 4)
    if include_sources and memory.source_message_ids:
        ids = [str(value) for value in memory.source_message_ids[:3]]
        rows = {
            row.id: row
            for row in db.scalars(select(SupportMessage).where(SupportMessage.id.in_(ids))).all()
        }
        payload["sources"] = [
            {
                "message_id": message_id,
                "role": rows[message_id].role if message_id in rows else None,
                "excerpt": (
                    redact_text(rows[message_id].content, limit=180)
                    if message_id in rows
                    else None
                ),
                "created_at": (
                    rows[message_id].created_at.isoformat() if message_id in rows else None
                ),
            }
            for message_id in ids
        ]
    return payload


def _tombstone_hash(db, hashed: str) -> bool:
    return db.scalar(
        select(CopilotMemory.id)
        .where(CopilotMemory.status == "DELETED")
        .where(CopilotMemory.content_hash == hashed)
        .limit(1)
    ) is not None


def _near_duplicate(
    db, *, scope: str, project_id: str | None, text: str, memory_type: str
) -> tuple[CopilotMemory, float] | None:
    candidates = list(
        db.scalars(
            select(CopilotMemory)
            .where(CopilotMemory.status == "ACTIVE")
            .where(CopilotMemory.scope == scope)
            .where(CopilotMemory.project_id == project_id)
            .where(CopilotMemory.memory_type == memory_type)
            .order_by(CopilotMemory.updated_at.desc())
            .limit(200)
        ).all()
    )
    best: tuple[CopilotMemory, float] | None = None
    for row in candidates:
        score = embedding.similarity(text, row.content)
        if best is None or score > best[1]:
            best = (row, score)
    if best is not None and best[1] >= _NEAR_DUPLICATE:
        return best
    return None


def _looks_like_update(new_text: str, old_text: str) -> bool:
    if _UPDATE_SIGNAL.search(new_text):
        return True
    return len(normalise_content(new_text)) >= len(normalise_content(old_text)) + 12


def _audit(db, actor: str, event: str, memory: CopilotMemory, extra: dict[str, Any] | None = None) -> None:
    """Write one audit row with a literal event code.

    The audit-code scanner reads literal strings at ``add_audit`` call sites;
    keeping one small dispatcher makes the memory events visible to that check
    instead of hiding them behind a variable and then omitting them from the
    frontend code table.
    """
    from shine.services import add_audit

    payload = {
        "scope": memory.scope,
        "project_id": memory.project_id,
        "memory_type": memory.memory_type,
        "source": memory.source,
        "content_revision": memory.content_revision,
        **(extra or {}),
    }
    if event == "SUPPORT_MEMORY_CREATED":
        add_audit(
            db, actor, "SUPPORT_MEMORY_CREATED", "copilot_memory", memory.id, payload,
            project_id=memory.project_id,
        )
    elif event == "SUPPORT_MEMORY_UPDATED":
        add_audit(
            db, actor, "SUPPORT_MEMORY_UPDATED", "copilot_memory", memory.id, payload,
            project_id=memory.project_id,
        )
    elif event == "SUPPORT_MEMORY_SUPERSEDED":
        add_audit(
            db, actor, "SUPPORT_MEMORY_SUPERSEDED", "copilot_memory", memory.id, payload,
            project_id=memory.project_id,
        )
    else:
        add_audit(
            db, actor, "SUPPORT_MEMORY_DELETED", "copilot_memory", memory.id, payload,
            project_id=memory.project_id,
        )


def create_memory(
    db,
    *,
    scope: str,
    project_id: str | None,
    content: str,
    memory_type: str,
    source: str = "MANUAL",
    source_session_id: str | None = None,
    source_message_ids: list[str] | None = None,
    replaces_memory_id: str | None = None,
    confirmed: bool = True,
    actor: str = "local-user",
    suppress_tombstones: bool = True,
) -> dict[str, Any]:
    scope, project_id = _scope_and_project(db, scope, project_id)
    text = validate_content(content)
    memory_type = validate_memory_type(memory_type)
    if source not in SOURCES:
        raise MemoryInvalid("INVALID_SOURCE", "未知的记忆来源。")
    hashed = content_hash(text)
    if source == "AUTO" and suppress_tombstones and _tombstone_hash(db, hashed):
        return {
            "memory": None,
            "deduplicated": False,
            "suppressed": True,
            "reason": "DELETED_MEMORY_TOMBSTONE",
            "notice": "这条内容此前已被用户删除；自动提炼不会自行恢复它。",
        }
    exact = db.scalar(
        select(CopilotMemory)
        .where(CopilotMemory.status == "ACTIVE")
        .where(CopilotMemory.scope == scope)
        .where(CopilotMemory.project_id == project_id)
        .where(CopilotMemory.content_hash == hashed)
        .limit(1)
    )
    if exact is not None:
        return {
            "memory": _serialise(db, exact),
            "deduplicated": True,
            "suppressed": False,
            "reason": "EXACT_DUPLICATE",
        }
    near = _near_duplicate(
        db, scope=scope, project_id=project_id, text=text, memory_type=memory_type
    )
    if near is not None:
        existing, score = near
        if score >= _EXACT_DUPLICATE or _looks_like_update(text, existing.content):
            existing.content = text
            existing.content_hash = hashed
            existing.content_revision += 1
            existing.updated_at = utcnow()
            existing.confirmed = existing.confirmed or confirmed
            if source == "MANUAL":
                existing.source = "MANUAL"
            memory_index.sync_memory(db, existing)
            _audit(
                db,
                actor,
                "SUPPORT_MEMORY_UPDATED",
                existing,
                {"reason": "SEMANTIC_UPDATE", "score": round(score, 4)},
            )
            db.commit()
            return {
                "memory": _serialise(db, existing),
                "deduplicated": True,
                "suppressed": False,
                "reason": "SEMANTIC_UPDATE",
                "score": round(score, 4),
            }
        return {
            "memory": _serialise(db, existing),
            "deduplicated": True,
            "suppressed": False,
            "reason": "SEMANTIC_DUPLICATE",
            "score": round(score, 4),
        }

    replaced: CopilotMemory | None = None
    if replaces_memory_id:
        replaced = db.get(CopilotMemory, replaces_memory_id)
        if (
            replaced is None
            or replaced.status != "ACTIVE"
            or replaced.scope != scope
            or replaced.project_id != project_id
        ):
            replaced = None

    row = CopilotMemory(
        scope=scope,
        project_id=project_id,
        content=text,
        memory_type=memory_type,
        source=source,
        source_session_id=source_session_id,
        source_message_ids=list(source_message_ids or [])[:12],
        content_hash=hashed,
        content_revision=1,
        embedding_revision=0,
        status="ACTIVE",
        confirmed=bool(confirmed),
        supersedes_id=replaced.id if replaced is not None else None,
        details={"created_by": actor[:64]},
    )
    db.add(row)
    db.flush()
    if replaced is not None:
        replaced.status = "SUPERSEDED"
        replaced.content_revision += 1
        replaced.updated_at = utcnow()
        memory_index.drop_memory_index(db, replaced.id)
        _audit(db, actor, "SUPPORT_MEMORY_SUPERSEDED", replaced, {"superseded_by": row.id})
    index_warning = None
    try:
        memory_index.ensure_index(db)
        memory_index.sync_memory(db, row)
    except SQLAlchemyError as exc:
        # Index failure is reported by the status endpoint, never fatal here:
        # the authoritative memory row still commits and text LIKE recall works.
        index_warning = type(exc).__name__
    _audit(db, actor, "SUPPORT_MEMORY_CREATED", row, {"index_warning": index_warning})
    db.commit()
    return {
        "memory": _serialise(db, row),
        "deduplicated": False,
        "suppressed": False,
        "reason": None,
    }


def update_memory(
    db,
    memory_id: str,
    *,
    content: str | None = None,
    memory_type: str | None = None,
    actor: str = "local-user",
) -> dict[str, Any]:
    row = db.get(CopilotMemory, memory_id)
    if row is None or row.status == "DELETED":
        raise MemoryInvalid("MEMORY_NOT_FOUND", "记忆不存在或已被删除。")
    changed = False
    if content is not None:
        text = validate_content(content)
        if text != row.content:
            row.content = text
            row.content_hash = content_hash(text)
            row.content_revision += 1
            changed = True
    if memory_type is not None:
        value = validate_memory_type(memory_type)
        if value != row.memory_type:
            row.memory_type = value
            changed = True
    if not changed:
        return {"memory": _serialise(db, row), "changed": False}
    row.confirmed = True
    # A person explicitly edited it, so "clear all automatic memories" must not
    # silently throw this text away in the future.
    row.source = "MANUAL"
    row.updated_at = utcnow()
    index_warning = None
    try:
        memory_index.ensure_index(db)
        if row.status == "ACTIVE":
            memory_index.sync_memory(db, row)
    except SQLAlchemyError as exc:
        index_warning = type(exc).__name__
    _audit(
        db,
        actor,
        "SUPPORT_MEMORY_UPDATED",
        row,
        {"fields": ["content", "memory_type"], "index_warning": index_warning},
    )
    db.commit()
    return {"memory": _serialise(db, row), "changed": True, "index_warning": index_warning}


def delete_memory(db, memory_id: str, *, actor: str = "local-user", reason: str = "USER_DELETED") -> dict[str, Any]:
    row = db.get(CopilotMemory, memory_id)
    if row is None:
        raise MemoryInvalid("MEMORY_NOT_FOUND", "记忆不存在。")
    if row.status == "DELETED":
        return {"memory_id": memory_id, "deleted": True, "already_deleted": True}
    row.status = "DELETED"
    row.content = ""
    row.content_revision += 1
    row.embedding_revision = row.content_revision
    row.deleted_at = utcnow()
    row.deleted_reason = reason[:48]
    row.updated_at = utcnow()
    memory_index.drop_memory_index(db, row.id)
    _audit(db, actor, "SUPPORT_MEMORY_DELETED", row, {"reason": reason})
    db.commit()
    return {"memory_id": memory_id, "deleted": True, "already_deleted": False}


def clear_memories(
    db,
    *,
    scope: str,
    project_id: str | None = None,
    actor: str = "local-user",
) -> dict[str, Any]:
    statement = select(CopilotMemory).where(CopilotMemory.status == "ACTIVE")
    if scope == "PROJECT":
        if not project_id:
            raise MemoryInvalid("PROJECT_REQUIRED", "清除项目记忆需要真实项目 ID。")
        if db.get(Project, project_id) is None:
            raise MemoryInvalid("PROJECT_NOT_FOUND", "项目不存在。")
        statement = statement.where(CopilotMemory.scope == "PROJECT").where(
            CopilotMemory.project_id == project_id
        )
    elif scope == "AUTO":
        statement = statement.where(CopilotMemory.source == "AUTO")
    else:
        raise MemoryInvalid("INVALID_SCOPE", "只能清除某个项目或全部自动记忆。")
    rows = list(db.scalars(statement).all())
    for row in rows:
        row.status = "DELETED"
        row.content = ""
        row.content_revision += 1
        row.embedding_revision = row.content_revision
        row.deleted_at = utcnow()
        row.deleted_reason = "USER_CLEARED"
        row.updated_at = utcnow()
        memory_index.drop_memory_index(db, row.id)
    if rows:
        from shine.services import add_audit

        add_audit(
            db,
            actor,
            "SUPPORT_MEMORY_CLEARED",
            "copilot_memory_scope",
            project_id or scope,
            {"scope": scope, "project_id": project_id, "count": len(rows)},
            project_id=project_id,
        )
    db.commit()
    return {"cleared": len(rows), "scope": scope, "project_id": project_id}


def list_memories(
    db,
    *,
    scope: str | None = None,
    project_id: str | None = None,
    q: str = "",
    limit: int = 100,
    offset: int = 0,
) -> dict[str, Any]:
    clauses = [CopilotMemory.status == "ACTIVE"]
    if scope in {"GLOBAL", "PROJECT"}:
        clauses.append(CopilotMemory.scope == scope)
        if scope == "PROJECT":
            if not project_id:
                return {"memories": [], "total": 0, "counts": counts_payload(db)}
            clauses.append(CopilotMemory.project_id == project_id)
    elif project_id:
        clauses.append(
            (CopilotMemory.scope == "GLOBAL")
            | ((CopilotMemory.scope == "PROJECT") & (CopilotMemory.project_id == project_id))
        )
    if (q or "").strip():
        term = q.strip().replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
        clauses.append(CopilotMemory.content.ilike(f"%{term}%", escape="\\"))
    total = int(
        db.scalar(select(func.count()).select_from(CopilotMemory).where(*clauses)) or 0
    )
    rows = list(
        db.scalars(
            select(CopilotMemory)
            .where(*clauses)
            .order_by(CopilotMemory.updated_at.desc())
            .offset(max(0, int(offset)))
            .limit(max(1, min(int(limit), 200)))
        ).all()
    )
    return {
        "memories": [_serialise(db, row) for row in rows],
        "total": total,
        "counts": counts_payload(db),
        "limit": max(1, min(int(limit), 200)),
        "offset": max(0, int(offset)),
    }


def get_memory(db, memory_id: str) -> dict[str, Any]:
    row = db.get(CopilotMemory, memory_id)
    if row is None or row.status == "DELETED":
        raise MemoryInvalid("MEMORY_NOT_FOUND", "记忆不存在或已被删除。")
    return _serialise(db, row)


def counts_payload(db) -> dict[str, Any]:
    active = select(CopilotMemory).where(CopilotMemory.status == "ACTIVE").subquery()
    total = int(db.scalar(select(func.count()).select_from(active)) or 0)
    global_count = int(
        db.scalar(
            select(func.count())
            .select_from(CopilotMemory)
            .where(CopilotMemory.status == "ACTIVE")
            .where(CopilotMemory.scope == "GLOBAL")
        )
        or 0
    )
    project_count = int(
        db.scalar(
            select(func.count())
            .select_from(CopilotMemory)
            .where(CopilotMemory.status == "ACTIVE")
            .where(CopilotMemory.scope == "PROJECT")
        )
        or 0
    )
    auto_count = int(
        db.scalar(
            select(func.count())
            .select_from(CopilotMemory)
            .where(CopilotMemory.status == "ACTIVE")
            .where(CopilotMemory.source == "AUTO")
        )
        or 0
    )
    tombstones = int(
        db.scalar(
            select(func.count())
            .select_from(CopilotMemory)
            .where(CopilotMemory.status == "DELETED")
        )
        or 0
    )
    by_project = {
        str(row[0]): int(row[1])
        for row in db.execute(
            select(CopilotMemory.project_id, func.count())
            .where(CopilotMemory.status == "ACTIVE")
            .where(CopilotMemory.scope == "PROJECT")
            .group_by(CopilotMemory.project_id)
        ).all()
        if row[0]
    }
    return {
        "active": total,
        "global": global_count,
        "project": project_count,
        "auto": auto_count,
        "manual": max(0, total - auto_count),
        "deleted_tombstones": tombstones,
        "by_project": by_project,
    }


def status_payload(db) -> dict[str, Any]:
    profile = read_profile(db)
    try:
        index_payload = memory_index.index_status(db)
    except Exception as exc:  # noqa: BLE001 - a corrupt index is a reported state
        index_payload = {
            "fts5": "UNAVAILABLE",
            "state": "CORRUPT",
            "error": type(exc).__name__,
            "vector": embedding.status_payload(),
            "vector_rows": 0,
            "stale_vectors": 0,
            "active_memories": 0,
            "degraded_fallback": "索引损坏时仍可从权威记忆表按文本检索；可在配置页重建索引。",
        }
    if not profile["memory_enabled"]:
        state = index_payload.get("state")
        index_payload = {
            **index_payload,
            "state": "DISABLED" if state == "OK" else state,
        }
    return {
        **index_payload,
        "memory_enabled": profile["memory_enabled"],
        "auto_memory_enabled": profile["auto_memory_enabled"],
        "remote_memory_processing_consent": profile["remote_memory_processing_consent"],
        "remote_context_consent": profile["remote_context_consent"],
        "config_revision": profile["config_revision"],
        "counts": counts_payload(db),
        "limits": {"memory_content": MAX_MEMORY_CONTENT},
        "privacy": {
            "storage": "本机 SQLite（与 SupportSession/SupportMessage 同一个数据库）",
            "auto_extraction_requires": "长期记忆开关 + 自动提炼开关；远端模型还需要允许远端提炼",
            "delete_effect": "删除会立即移除 FTS 与向量条目，并留下仅含内容哈希的墓碑阻止自动重建",
            "disable_effect": "关闭长期记忆只停止召回与提炼，不删除已有记忆",
        },
    }


def _hybrid_candidates(
    db, *, query: str, project_id: str | None
) -> tuple[list[CopilotMemory], dict[str, Any]]:
    state: dict[str, Any] = {"fts": 0, "vector": 0, "like": 0, "degraded": False}
    scored: dict[str, float] = {}
    rows: dict[str, CopilotMemory] = {}
    try:
        fts_rows = memory_index.search_fts(db, query, project_id=project_id, limit=20)
        state["fts"] = len(fts_rows)
        for position, (memory_id, _rank) in enumerate(fts_rows):
            scored[memory_id] = max(scored.get(memory_id, 0.0), 1.0 - position / max(1, len(fts_rows)) * 0.35)
    except Exception:  # noqa: BLE001 - fall through to the authority table
        state["degraded"] = True
    try:
        vector_rows = memory_index.search_vector(db, query, project_id=project_id, limit=20)
        state["vector"] = len(vector_rows)
        for memory_id, score, memory in vector_rows:
            rows[memory_id] = memory
            scored[memory_id] = max(scored.get(memory_id, 0.0), max(0.0, score) * 0.6 + 0.4)
    except Exception:  # noqa: BLE001 - vector failure is a degraded mode
        state["degraded"] = True
    if not scored:
        try:
            like_rows = memory_index.search_like(db, query, project_id=project_id, limit=20)
            state["like"] = len(like_rows)
            for position, memory in enumerate(like_rows):
                rows[memory.id] = memory
                scored[memory.id] = 0.3 + (1.0 - position / max(1, len(like_rows))) * 0.2
        except Exception:  # noqa: BLE001 - no index and no LIKE means no recall
            state["degraded"] = True
    missing = [memory_id for memory_id in scored if memory_id not in rows]
    if missing:
        for memory in db.scalars(
            select(CopilotMemory)
            .where(CopilotMemory.id.in_(missing))
            .where(CopilotMemory.status == "ACTIVE")
        ).all():
            rows[memory.id] = memory
    ordered = sorted(scored.items(), key=lambda item: item[1], reverse=True)
    result: list[CopilotMemory] = []
    for memory_id, _score in ordered:
        memory = rows.get(memory_id)
        if memory is None or memory.status != "ACTIVE":
            continue
        if memory.scope == "GLOBAL" or (project_id and memory.project_id == project_id):
            result.append(memory)
    return result, state


def retrieve(
    db,
    *,
    project_id: str | None,
    query: str,
    limit: int = MAX_RECALL,
) -> dict[str, Any]:
    """Hybrid recall bounded to global + the caller's real project."""
    profile = read_profile(db)
    if not profile["memory_enabled"]:
        return {
            "enabled": False,
            "state": "DISABLED",
            "memories": [],
            "count": 0,
            "reason": "长期记忆已在配置中关闭。",
        }
    q = (query or "").strip()
    try:
        if q:
            rows, degraded = _hybrid_candidates(db, query=q, project_id=project_id)
            state = "DEGRADED" if degraded["degraded"] else "OK"
            detail = degraded
        else:
            clauses = [
                CopilotMemory.status == "ACTIVE",
                (CopilotMemory.scope == "GLOBAL")
                | ((CopilotMemory.scope == "PROJECT") & (CopilotMemory.project_id == project_id)),
            ]
            if not project_id:
                clauses = [CopilotMemory.status == "ACTIVE", CopilotMemory.scope == "GLOBAL"]
            rows = list(
                db.scalars(
                    select(CopilotMemory)
                    .where(*clauses)
                    .order_by(CopilotMemory.updated_at.desc())
                    .limit(max(1, int(limit)))
                ).all()
            )
            state = "OK"
            detail = {"fts": 0, "vector": 0, "like": 0, "degraded": False}
    except Exception as exc:  # noqa: BLE001 - recall never blocks the turn
        return {
            "enabled": True,
            "state": "ERROR",
            "memories": [],
            "count": 0,
            "reason": type(exc).__name__,
        }
    selected = rows[: max(1, min(int(limit), MAX_RECALL))]
    return {
        "enabled": True,
        "state": state,
        "project_id": project_id,
        "query": q[:200],
        "count": len(selected),
        "retrieval": detail,
        "memories": [
            {
                "id": row.id,
                "scope": row.scope,
                "project_id": row.project_id,
                "memory_type": row.memory_type,
                "content": redact_text(row.content, limit=RECALL_CONTENT_CHARS),
                "updated_at": row.updated_at.isoformat(),
            }
            for row in selected
        ],
    }
