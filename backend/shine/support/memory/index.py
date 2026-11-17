"""Rebuildable local search index: SQLite FTS5 plus a tiny vector table.

The authoritative memory text lives in ``copilot_memories``.  Both index
structures here are disposable: every read validates the memory row, its
status, its revision and its content hash, so a stale or corrupt index can only
lose recall quality, never invent a fact or resurrect a deleted memory.

FTS5 is optional at runtime (some bundled SQLite builds omit it); when it is
missing the caller falls back to a bounded LIKE query over the authority table.
"""
from __future__ import annotations

import re
import weakref
from typing import Any

from sqlalchemy import case, func, or_, select, text
from sqlalchemy.exc import SQLAlchemyError

from shine.models import utcnow
from shine.support.memory import embedding
from shine.support.models import CopilotMemory, CopilotMemoryVector

FTS_TABLE = "copilot_memory_fts"
_FTS_SHADOW_PREFIX = f"{FTS_TABLE}_"
_CJK = re.compile(r"[\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff]")
_ASCII = re.compile(r"[A-Za-z0-9_]{2,}")
_FTS_STATE: weakref.WeakKeyDictionary[Any, bool] = weakref.WeakKeyDictionary()
_MAX_QUERY_TOKENS = 24


def _probe_fts5(connection) -> bool:
    try:
        connection.exec_driver_sql("CREATE VIRTUAL TABLE temp.copilot_fts_probe USING fts5(x)")
        connection.exec_driver_sql("DROP TABLE temp.copilot_fts_probe")
        return True
    except Exception:  # noqa: BLE001 - a missing FTS5 build is a supported fallback
        return False


def fts_available(db) -> bool:
    bind = db.get_bind()
    cached = _FTS_STATE.get(bind)
    if cached is not None:
        return cached
    value = _probe_fts5(db.connection())
    _FTS_STATE[bind] = value
    return value


def ensure_index(db) -> dict[str, Any]:
    """Create the FTS table when the install predates the migration.

    This is intentionally lazy: opening Shine or the copilot must not pay for
    index work, and a broken FTS build must not stop the app.
    """
    available = fts_available(db)
    if not available:
        return {"fts5": False, "created": False, "reason": "FTS5_NOT_COMPILED"}
    try:
        db.connection().exec_driver_sql(
            f"CREATE VIRTUAL TABLE IF NOT EXISTS {FTS_TABLE} "
            "USING fts5(memory_id UNINDEXED, content, tokenize='unicode61')"
        )
        db.commit()
        return {"fts5": True, "created": True, "reason": None}
    except SQLAlchemyError as exc:
        _FTS_STATE[db.get_bind()] = False
        return {"fts5": False, "created": False, "reason": type(exc).__name__}


def index_text(content: str) -> str:
    """Add CJK bigrams so unicode61 can match Chinese phrases.

    ``unicode61`` sees a run of Han characters as one token.  Indexing the
    bigrams alongside the original sentence makes short Chinese queries match
    without pulling in a segmentation dependency.
    """
    chars = list(content or "")
    grams = [
        f"{chars[index]}{chars[index + 1]}"
        for index in range(len(chars) - 1)
        if _CJK.match(chars[index]) and _CJK.match(chars[index + 1])
    ]
    return f"{content}\n{' '.join(grams)}" if grams else (content or "")


def query_tokens(query: str) -> list[str]:
    tokens: list[str] = []
    for word in _ASCII.findall(query or ""):
        token = word.casefold()
        if token not in tokens:
            tokens.append(token)
    chars = list(query or "")
    for index, char in enumerate(chars):
        if not _CJK.match(char):
            continue
        pair = char
        if index + 1 < len(chars) and _CJK.match(chars[index + 1]):
            pair = f"{char}{chars[index + 1]}"
        if pair not in tokens:
            tokens.append(pair)
        if len(tokens) >= _MAX_QUERY_TOKENS:
            break
    return tokens[:_MAX_QUERY_TOKENS]


def fts_expression(query: str) -> str:
    tokens = query_tokens(query)
    if not tokens:
        return ""
    return " OR ".join('"{}"'.format(token.replace('"', '""')) for token in tokens)


def remove_fts(db, memory_id: str) -> None:
    if not fts_available(db):
        return
    db.connection().exec_driver_sql(
        f"DELETE FROM {FTS_TABLE} WHERE memory_id = :memory_id", {"memory_id": memory_id}
    )


def write_fts(db, memory: CopilotMemory) -> None:
    if not fts_available(db):
        return
    remove_fts(db, memory.id)
    if memory.status != "ACTIVE" or not memory.content:
        return
    db.connection().exec_driver_sql(
        f"INSERT INTO {FTS_TABLE} (memory_id, content) VALUES (:memory_id, :content)",
        {"memory_id": memory.id, "content": index_text(memory.content)},
    )


def remove_vector(db, memory_id: str) -> None:
    row = db.get(CopilotMemoryVector, memory_id)
    if row is not None:
        db.delete(row)


def write_vector(db, memory: CopilotMemory, *, force: bool = False) -> bool:
    """Reuse an identical revision; never recompute the same embedding twice."""
    if memory.status != "ACTIVE" or not memory.content:
        remove_vector(db, memory.id)
        return False
    row = db.get(CopilotMemoryVector, memory.id)
    if (
        row is not None
        and not force
        and row.content_revision == memory.content_revision
        and row.content_hash == memory.content_hash
        and row.provider == embedding.PROVIDER
        and row.dim == embedding.DIM
    ):
        return False
    vector = embedding.embed(memory.content)
    if row is None:
        row = CopilotMemoryVector(memory_id=memory.id)
        db.add(row)
    row.content_revision = memory.content_revision
    row.content_hash = memory.content_hash
    row.provider = embedding.PROVIDER
    row.dim = embedding.DIM
    row.vector = embedding.pack(vector)
    row.updated_at = utcnow()
    memory.embedding_revision = memory.content_revision
    return True


def sync_memory(db, memory: CopilotMemory, *, force_vector: bool = False) -> None:
    write_fts(db, memory)
    write_vector(db, memory, force=force_vector)


def drop_memory_index(db, memory_id: str) -> None:
    try:
        remove_fts(db, memory_id)
    except SQLAlchemyError:
        pass
    try:
        remove_vector(db, memory_id)
    except SQLAlchemyError:
        pass


def _scope_clause(project_id: str | None, include_project: bool = True):
    clauses = [CopilotMemory.status == "ACTIVE"]
    if include_project and project_id:
        clauses.append(
            (CopilotMemory.scope == "GLOBAL")
            | ((CopilotMemory.scope == "PROJECT") & (CopilotMemory.project_id == project_id))
        )
    else:
        clauses.append(CopilotMemory.scope == "GLOBAL")
    return clauses


def search_fts(db, query: str, *, project_id: str | None, limit: int = 20) -> list[tuple[str, float]]:
    expression = fts_expression(query)
    if not expression or not fts_available(db):
        return []
    try:
        ensure_index(db)
        rows = db.execute(
            text(
                f"SELECT {FTS_TABLE}.memory_id, bm25({FTS_TABLE}) AS rank "
                f"FROM {FTS_TABLE} "
                f"JOIN copilot_memories m ON m.id = {FTS_TABLE}.memory_id "
                f"WHERE {FTS_TABLE} MATCH :expression "
                "AND m.status = 'ACTIVE' "
                "AND (m.scope = 'GLOBAL' OR (m.scope = 'PROJECT' AND m.project_id = :project_id)) "
                "ORDER BY rank LIMIT :limit"
            ),
            {"expression": expression, "project_id": project_id or "", "limit": int(limit)},
        ).all()
    except SQLAlchemyError:
        return []
    return [(str(row[0]), float(row[1])) for row in rows]


def search_like(db, query: str, *, project_id: str | None, limit: int = 20) -> list[CopilotMemory]:
    """Bounded keyword fallback when FTS5 is unavailable.

    A whole sentence is rarely a substring of a memory, so the query is split
    into the same CJK bigrams / ASCII words the FTS index would use; each
    matching token contributes to a rank, and the best rows win.
    """
    tokens = query_tokens(query)[:12]
    if not tokens:
        return []
    clauses = []
    score_parts = []
    for token in tokens:
        escaped = token.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
        clause = CopilotMemory.content.ilike(f"%{escaped}%", escape="\\")
        clauses.append(clause)
        score_parts.append(case((clause, 1), else_=0))
    score = sum(score_parts[1:], score_parts[0]).label("match_score")
    statement = (
        select(CopilotMemory, score)
        .where(*_scope_clause(project_id, include_project=bool(project_id)))
        .where(or_(*clauses))
        .order_by(score.desc(), CopilotMemory.updated_at.desc())
        .limit(int(limit))
    )
    return [row[0] for row in db.execute(statement).all()]


def search_vector(
    db, query: str, *, project_id: str | None, limit: int = 20
) -> list[tuple[str, float, CopilotMemory]]:
    if not (query or "").strip():
        return []
    candidates = list(
        db.scalars(
            select(CopilotMemory)
            .where(*_scope_clause(project_id, include_project=bool(project_id)))
            .order_by(CopilotMemory.updated_at.desc())
            .limit(800)
        ).all()
    )
    if not candidates:
        return []
    ids = [row.id for row in candidates]
    vectors = {
        row.memory_id: row
        for row in db.scalars(
            select(CopilotMemoryVector).where(CopilotMemoryVector.memory_id.in_(ids))
        ).all()
    }
    query_vector = embedding.embed(query)
    scored: list[tuple[str, float, CopilotMemory]] = []
    for memory in candidates:
        row = vectors.get(memory.id)
        if row is None:
            continue
        if row.content_revision != memory.content_revision or row.content_hash != memory.content_hash:
            # A stale vector never answers for edited text.
            continue
        if row.provider != embedding.PROVIDER or row.dim != embedding.DIM:
            continue
        score = embedding.cosine(query_vector, embedding.unpack(row.vector))
        if score > 0.0:
            scored.append((memory.id, score, memory))
    scored.sort(key=lambda item: item[1], reverse=True)
    return scored[: int(limit)]


def rebuild(db, *, project_id: str | None = None) -> dict[str, Any]:
    """Recreate every index row from the authoritative active memories."""
    memories = list(
        db.scalars(
            select(CopilotMemory)
            .where(CopilotMemory.status == "ACTIVE")
            .order_by(CopilotMemory.updated_at.desc())
        ).all()
    )
    result = {
        "memories": len(memories),
        "fts5": fts_available(db),
        "vectors": 0,
        "rebuilt_at": utcnow().isoformat(),
        "project_id": project_id,
    }
    if result["fts5"]:
        try:
            ensure_index(db)
            db.connection().exec_driver_sql(f"DELETE FROM {FTS_TABLE}")
        except SQLAlchemyError:
            result["fts5"] = False
    for memory in memories:
        try:
            if result["fts5"]:
                write_fts(db, memory)
            if write_vector(db, memory, force=True):
                result["vectors"] += 1
        except SQLAlchemyError:
            # One unreadable row must not stop the whole rebuild.
            continue
    db.commit()
    return result


def index_status(db) -> dict[str, Any]:
    available = fts_available(db)
    if available:
        ensure_index(db)
    active = int(
        db.scalar(select(func.count()).select_from(CopilotMemory).where(CopilotMemory.status == "ACTIVE")) or 0
    )
    fts_rows: int | None = None
    fts_error: str | None = None
    if available:
        try:
            fts_rows = int(
                db.execute(text(f"SELECT COUNT(*) FROM {FTS_TABLE}")).scalar() or 0
            )
        except SQLAlchemyError as exc:
            fts_error = type(exc).__name__
    vector_rows = int(
        db.scalar(select(func.count()).select_from(CopilotMemoryVector)) or 0
    )
    stale = int(
        db.execute(
            text(
                "SELECT COUNT(*) FROM copilot_memory_vectors v "
                "JOIN copilot_memories m ON m.id = v.memory_id "
                "WHERE v.content_revision != m.content_revision "
                "OR v.content_hash != m.content_hash "
                "OR m.status != 'ACTIVE'"
            )
        ).scalar()
        or 0
    )
    if not available or fts_error:
        state = "DEGRADED"
    elif fts_rows != active or vector_rows != active or stale:
        state = "REBUILD_REQUIRED"
    else:
        state = "OK"
    return {
        "fts5": "AVAILABLE" if available and not fts_error else "UNAVAILABLE",
        "fts_rows": fts_rows,
        "vector": embedding.status_payload(),
        "vector_rows": vector_rows,
        "stale_vectors": stale,
        "active_memories": active,
        "state": state,
        "fts_error": fts_error,
        "degraded_fallback": "FTS5 不可用时使用权威记忆表的 LIKE 检索；向量缺失时不影响文字检索。",
    }
