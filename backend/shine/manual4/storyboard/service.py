"""Manual4 Stage 1 service: draft management, import, atomic confirm/freeze and diffing."""

from __future__ import annotations

import json
import logging
import time
from pathlib import Path
from typing import Any

from fastapi import HTTPException
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from shine.manual4.contracts.schemas import M4_MIN_SHOT_SECONDS, canonical_digest
from shine.manual4.storage.files import store_m4_bytes
from shine.manual4.storage.models import (
    M4AssetVersion,
    M4AuditEvent,
    M4EpisodePlan,
    M4ManualPromptRevision,
    M4MaterialRequirement,
    M4Project,
    M4Shot,
    M4ShotAssetBinding,
    M4SourceDocument,
    M4StoryboardRevision,
    m4_utcnow,
)
from shine.manual4.storyboard.parser import (
    M4ParseError,
    chunk_storyboard_text,
    extract_storyboard_from_text,
    parse_m4_document,
)
from shine.platform_projects import chat_completions_url


def _shot_content_sha(
    *,
    target_seconds: float,
    summary: str,
    dialogue: list[Any],
    requested_assets: list[Any],
    aspect_ratio: str,
    continuity_mode: str,
) -> str:
    return canonical_digest(
        {
            "target_seconds": round(float(target_seconds), 4),
            "summary": str(summary or ""),
            "dialogue": list(dialogue or []),
            "requested_assets": list(requested_assets or []),
            "aspect_ratio": str(aspect_ratio or "16:9"),
            "continuity_mode": str(continuity_mode or "cut"),
        }
    )


def _canonical_entity_key(name: str, variant: str) -> str:
    n = " ".join(str(name or "").split()).casefold()
    v = " ".join(str(variant or "").split()).casefold()
    return f"{n}::{v}" if v else n


def _normalize_draft_payload(payload: dict[str, Any]) -> dict[str, Any]:
    raw_episodes = payload.get("episodes") or []
    episodes: list[dict[str, Any]] = []
    for ep_idx, ep in enumerate(raw_episodes, start=1):
        ep_no = int(ep.get("episode_no") or ep_idx)
        ep_code = str(ep.get("episode_code") or f"ep{ep_no:02d}").strip()
        ep_title = str(ep.get("title") or f"第 {ep_no} 集").strip()
        target_total = ep.get("target_total_seconds")
        target_total_f = float(target_total) if target_total not in (None, "") else None
        raw_shots = ep.get("shots") or []
        shots: list[dict[str, Any]] = []
        for sh_idx, sh in enumerate(raw_shots, start=1):
            sh_no = int(sh.get("shot_no") or sh_idx)
            sh_code = str(sh.get("shot_code") or f"{ep_code}-s{sh_no:02d}").strip()
            dur_raw = sh.get("target_seconds")
            dur = float(dur_raw) if dur_raw not in (None, "") else None
            assets = []
            for item in sh.get("requested_assets") or []:
                family = str(item.get("family") or "").strip().lower()
                name = str(item.get("name") or "").strip()
                variant = str(item.get("variant") or "").strip()
                if family in {"character", "scene", "prop"} and name:
                    assets.append({"family": family, "name": name, "variant": variant})
            shots.append(
                {
                    "episode_code": ep_code,
                    "shot_no": sh_no,
                    "shot_code": sh_code,
                    "target_seconds": dur,
                    "summary": str(sh.get("summary") or "").strip(),
                    "dialogue": list(sh.get("dialogue") or []),
                    "requested_assets": assets,
                    "source_span": dict(sh.get("source_span") or {}),
                    "aspect_ratio": str(sh.get("aspect_ratio") or "16:9"),
                    "continuity_mode": str(sh.get("continuity_mode") or "cut"),
                }
            )
        episodes.append(
            {
                "episode_no": ep_no,
                "episode_code": ep_code,
                "title": ep_title,
                "target_total_seconds": target_total_f,
                "shots": shots,
            }
        )
    return {
        "episodes": episodes,
        "ambiguities": list(payload.get("ambiguities") or []),
        "extractor": str(payload.get("extractor") or "manual"),
    }


def _revision_public(row: M4StoryboardRevision | None, project: M4Project) -> dict[str, Any]:
    if row is None:
        return {
            "revision_id": None,
            "revision": 0,
            "status": "EMPTY",
            "source_document_id": None,
            "source_sha256": "",
            "summary_sha256": "",
            "max_shot_seconds": project.max_shot_seconds,
            "payload": {"episodes": [], "ambiguities": [], "extractor": "manual"},
            "confirmed_at": None,
            "confirmed_by": None,
        }
    return {
        "revision_id": row.id,
        "revision": row.revision,
        "status": row.status,
        "source_document_id": row.source_document_id,
        "source_sha256": row.source_sha256,
        "summary_sha256": row.summary_sha256,
        "max_shot_seconds": project.max_shot_seconds,
        "payload": row.payload,
        "confirmed_at": row.confirmed_at.isoformat() + "Z" if row.confirmed_at else None,
        "confirmed_by": row.confirmed_by,
    }


def get_storyboard_state(db: Session, project: M4Project) -> dict[str, Any]:
    draft = db.scalar(
        select(M4StoryboardRevision)
        .where(M4StoryboardRevision.project_id == project.id, M4StoryboardRevision.status == "DRAFT")
        .order_by(M4StoryboardRevision.revision.desc())
        .limit(1)
    )
    confirmed = db.scalar(
        select(M4StoryboardRevision)
        .where(M4StoryboardRevision.project_id == project.id, M4StoryboardRevision.status == "CONFIRMED")
        .order_by(M4StoryboardRevision.revision.desc())
        .limit(1)
    )
    docs = db.scalars(
        select(M4SourceDocument)
        .where(M4SourceDocument.project_id == project.id)
        .order_by(M4SourceDocument.created_at.desc())
    ).all()
    episodes = []
    shots = []
    if confirmed is not None:
        episodes = db.scalars(
            select(M4EpisodePlan)
            .where(M4EpisodePlan.storyboard_revision_id == confirmed.id)
            .order_by(M4EpisodePlan.episode_no)
        ).all()
        shots = db.scalars(
            select(M4Shot)
            .where(M4Shot.storyboard_revision_id == confirmed.id)
            .order_by(M4Shot.episode_id, M4Shot.shot_no)
        ).all()
    return {
        "project_id": project.id,
        "max_shot_seconds": project.max_shot_seconds,
        "draft": _revision_public(draft or confirmed, project),
        "confirmed": _revision_public(confirmed, project) if confirmed else None,
        "source_documents": [
            {
                "id": d.id,
                "filename": d.filename,
                "source_format": d.source_format,
                "sha256": d.sha256,
                "byte_size": d.byte_size,
                "created_at": d.created_at.isoformat() + "Z",
            }
            for d in docs
        ],
        "confirmed_episodes": [
            {
                "id": ep.id,
                "episode_no": ep.episode_no,
                "episode_code": ep.episode_code,
                "title": ep.title,
                "target_total_seconds": ep.target_total_seconds,
                "planned_total_seconds": ep.planned_total_seconds,
            }
            for ep in episodes
        ],
        "confirmed_shots": [
            {
                "id": sh.id,
                "episode_id": sh.episode_id,
                "shot_no": sh.shot_no,
                "shot_code": sh.shot_code,
                "revision": sh.revision,
                "target_seconds": sh.target_seconds,
                "summary": sh.summary,
                "dialogue": sh.dialogue,
                "requested_assets": sh.requested_assets,
                "source_span": sh.source_span,
                "aspect_ratio": sh.aspect_ratio,
                "continuity_mode": sh.continuity_mode,
            }
            for sh in shots
        ],
    }


def _next_revision_no(db: Session, project_id: str) -> int:
    current_max = db.scalar(
        select(func.max(M4StoryboardRevision.revision)).where(M4StoryboardRevision.project_id == project_id)
    )
    return int(current_max or 0) + 1


def save_storyboard_draft(
    db: Session,
    project: M4Project,
    *,
    actor: str,
    payload: dict[str, Any],
    expected_revision: int | None = None,
    source_document_id: str | None = None,
    source_sha256: str = "",
) -> dict[str, Any]:
    normalized = _normalize_draft_payload(payload)
    draft = db.scalar(
        select(M4StoryboardRevision)
        .where(M4StoryboardRevision.project_id == project.id, M4StoryboardRevision.status == "DRAFT")
        .order_by(M4StoryboardRevision.revision.desc())
        .limit(1)
    )
    if expected_revision is not None:
        current_rev = draft.revision if draft is not None else 0
        if current_rev == 0:
            latest_conf = db.scalar(
                select(M4StoryboardRevision)
                .where(M4StoryboardRevision.project_id == project.id, M4StoryboardRevision.status == "CONFIRMED")
                .order_by(M4StoryboardRevision.revision.desc())
                .limit(1)
            )
            current_rev = latest_conf.revision if latest_conf is not None else 0
        if expected_revision != current_rev:
            raise HTTPException(
                409,
                {
                    "code": "M4_STORYBOARD_REVISION_CONFLICT",
                    "message": "分镜草稿已在别处更新，请刷新后再保存。",
                    "expected_revision": expected_revision,
                    "current_revision": current_rev,
                },
            )

    summary_sha = canonical_digest(normalized)
    if draft is None:
        draft = M4StoryboardRevision(
            project_id=project.id,
            revision=_next_revision_no(db, project.id),
            status="DRAFT",
            source_document_id=source_document_id,
            source_sha256=source_sha256,
            summary_sha256=summary_sha,
            payload=normalized,
        )
        db.add(draft)
        db.flush()
    else:
        draft.payload = normalized
        draft.summary_sha256 = summary_sha
        if source_document_id is not None:
            draft.source_document_id = source_document_id
        if source_sha256:
            draft.source_sha256 = source_sha256
        draft.updated_at = m4_utcnow()

    db.add(
        M4AuditEvent(
            project_id=project.id,
            actor=actor,
            stage="storyboard",
            event_type="M4_STORYBOARD_DRAFT_SAVED",
            entity_type="m4_storyboard_revision",
            entity_id=draft.id,
            payload={"revision": draft.revision, "summary_sha256": summary_sha},
        )
    )
    return _revision_public(draft, project)


def _llm_extract_chunk(
    chunk_text: str,
    *,
    llm_connection: dict[str, Any] | None,
    mock_responder: Any = None,
) -> dict[str, Any] | None:
    if mock_responder is not None:
        return mock_responder(chunk_text)
    if llm_connection is None:
        return None
    import httpx

    logger = logging.getLogger("shine.manual4.storyboard")
    base_url = str(llm_connection.get("base_url") or "").rstrip("/")
    model = str(llm_connection.get("model") or "")
    api_key = llm_connection.get("api_key") or ""
    timeout_s = float(llm_connection.get("timeout_s") or 60)
    if not base_url or not model:
        return None
    endpoint = str(llm_connection.get("chat_url") or chat_completions_url(base_url))
    system_prompt = (
        "Extract ONLY explicit episodes, shot numbers, explicit durations in seconds (null if absent), "
        "characters, scenes, props, and dialogue lines from the user text as strict JSON: "
        '{"episodes":[{"episode_no":1,"title":"...","shots":[{"shot_no":1,"target_seconds":null,'
        '"summary":"verbatim","requested_assets":[{"family":"character|scene|prop","name":"...","variant":""}],'
        '"dialogue":[{"speaker":"...","text":"..."}]}]}]}. '
        "Never invent durations, shots, or plot."
    )
    headers = {"Content-Type": "application/json"}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
    payload = {
        "model": model,
        "temperature": 0,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": chunk_text},
        ],
    }
    resp = None
    last_exc: httpx.HTTPError | None = None
    for attempt in range(2):
        try:
            resp = httpx.post(
                endpoint,
                headers=headers,
                json=payload,
                timeout=timeout_s,
            )
            if resp.status_code in {429, 502, 503} and attempt == 0:
                logger.warning(
                    "M4 storyboard LLM transient status=%s endpoint=%s; retrying once",
                    resp.status_code,
                    endpoint,
                )
                time.sleep(0.5)
                continue
            resp.raise_for_status()
            last_exc = None
            break
        except httpx.HTTPError as exc:
            last_exc = exc
            if isinstance(exc, httpx.TransportError) and attempt == 0:
                logger.warning(
                    "M4 storyboard LLM transport error=%s endpoint=%s; retrying once",
                    type(exc).__name__,
                    endpoint,
                )
                time.sleep(0.5)
                continue
            status_code = exc.response.status_code if isinstance(exc, httpx.HTTPStatusError) else None
            snippet = ""
            if isinstance(exc, httpx.HTTPStatusError):
                try:
                    snippet = (exc.response.text or "")[:200].replace("\n", " ").strip()
                except Exception:  # noqa: BLE001
                    snippet = ""
            logger.warning(
                "M4 storyboard LLM extract failed error=%s status=%s endpoint=%s body=%r",
                type(exc).__name__,
                status_code,
                endpoint,
                snippet,
            )
            detail_label = f"{type(exc).__name__} (HTTP {status_code})" if status_code else type(exc).__name__
            raise HTTPException(
                502,
                {
                    "code": "M4_LLM_EXTRACT_FAILED",
                    "message": f"辅助分镜解析模型调用失败：{detail_label}",
                    "status_code": status_code,
                },
            ) from exc
    if last_exc is not None or resp is None:  # pragma: no cover - defensive guard
        raise HTTPException(
            502,
            {"code": "M4_LLM_EXTRACT_FAILED", "message": "辅助分镜解析模型调用失败"},
        )
    content = resp.json()["choices"][0]["message"]["content"]
    match = content[content.find("{") : content.rfind("}") + 1] if "{" in content else ""
    if not match:
        return None
    parsed = json.loads(match)
    return parsed if isinstance(parsed, dict) and isinstance(parsed.get("episodes"), list) else None


def _llm_extract_unstructured(
    text: str,
    *,
    llm_connection: dict[str, Any] | None,
    mock_responder: Any = None,
    source_sha256: str = "",
    document_id: str = "",
) -> dict[str, Any] | None:
    """Chunked structured extractor for unstructured or >12k texts (never truncates silently)."""
    chunks = chunk_storyboard_text(text)
    if not chunks:
        return None
    merged_by_ep: dict[int, dict[str, Any]] = {}
    ambiguities: list[dict[str, Any]] = []
    any_hit = False
    for ch in chunks:
        res = _llm_extract_chunk(ch.text, llm_connection=llm_connection, mock_responder=mock_responder)
        if not res or not res.get("episodes"):
            continue
        any_hit = True
        for amb in res.get("ambiguities") or []:
            if isinstance(amb, dict):
                ambiguities.append({**amb, "chunk_index": ch.chunk_index})
        for raw_ep in res.get("episodes") or []:
            ep_no = int(raw_ep.get("episode_no") or (len(merged_by_ep) + 1))
            bucket = merged_by_ep.setdefault(
                ep_no,
                {
                    "episode_no": ep_no,
                    "episode_code": str(raw_ep.get("episode_code") or f"ep{ep_no:02d}"),
                    "title": str(raw_ep.get("title") or f"第{ep_no}集"),
                    "target_total_seconds": raw_ep.get("target_total_seconds"),
                    "shots": [],
                },
            )
            if not bucket.get("target_total_seconds") and raw_ep.get("target_total_seconds"):
                bucket["target_total_seconds"] = raw_ep.get("target_total_seconds")
            for raw_sh in raw_ep.get("shots") or []:
                sh_copy = dict(raw_sh)
                existing_span = sh_copy.get("source_span") if isinstance(sh_copy.get("source_span"), dict) else {}
                sh_copy["source_span"] = {
                    "document_id": document_id,
                    "source_sha256": source_sha256,
                    "chunk_index": ch.chunk_index,
                    "start_offset": ch.start_offset,
                    "end_offset": ch.end_offset,
                    **existing_span,
                }
                if sh_copy.get("target_seconds") is None:
                    ambiguities.append(
                        {
                            "code": "M4_MISSING_EXPLICIT_DURATION",
                            "episode_no": ep_no,
                            "shot_no": sh_copy.get("shot_no"),
                            "message": f"第{ep_no}集镜头缺少明确时长，请手工填写。",
                            "source_span": sh_copy["source_span"],
                        }
                    )
                bucket["shots"].append(sh_copy)
    if not any_hit:
        return None
    episodes = [merged_by_ep[k] for k in sorted(merged_by_ep)]
    return {"episodes": episodes, "ambiguities": ambiguities}


def import_storyboard_source(
    db: Session,
    project: M4Project,
    m4_root: Path,
    *,
    actor: str,
    filename: str,
    content: bytes,
    use_llm: bool = False,
    llm_connection: dict[str, Any] | None = None,
    mock_responder: Any = None,
) -> dict[str, Any]:
    try:
        parsed = parse_m4_document(filename, content)
    except M4ParseError as exc:
        raise HTTPException(422, {"code": exc.code, "message": exc.message}) from exc

    rel_path, digest, _ = store_m4_bytes(
        m4_root, project.id, "sources", parsed.filename, parsed.text.encode("utf-8")
    )
    doc = M4SourceDocument(
        project_id=project.id,
        filename=parsed.filename,
        source_format=parsed.source_format,
        sha256=digest,
        byte_size=parsed.byte_size,
        storage_path=rel_path,
        text=parsed.text,
        parsed_meta={"title": parsed.title, "structured_keys": sorted(parsed.parsed.keys())},
    )
    db.add(doc)
    db.flush()

    extracted = extract_storyboard_from_text(parsed.text, source_sha256=digest, document_id=doc.id)
    needs_llm_assist = not extracted["episodes"] or any(not ep.get("shots") for ep in extracted["episodes"])
    if use_llm and needs_llm_assist:
        llm_data = _llm_extract_unstructured(
            parsed.text,
            llm_connection=llm_connection,
            mock_responder=mock_responder,
            source_sha256=digest,
            document_id=doc.id,
        )
        if llm_data and llm_data.get("episodes"):
            extracted = {
                "episodes": llm_data["episodes"],
                "ambiguities": llm_data.get("ambiguities") or [],
                "chunks": extracted["chunks"],
                "extractor": "llm_assisted",
            }

    draft_pub = save_storyboard_draft(
        db,
        project,
        actor=actor,
        payload=extracted,
        source_document_id=doc.id,
        source_sha256=digest,
    )
    db.add(
        M4AuditEvent(
            project_id=project.id,
            actor=actor,
            stage="storyboard",
            event_type="M4_STORYBOARD_IMPORTED",
            entity_type="m4_source_document",
            entity_id=doc.id,
            payload={
                "filename": doc.filename,
                "source_sha256": digest,
                "extractor": extracted.get("extractor"),
                "episodes": len(extracted.get("episodes") or []),
            },
        )
    )
    return {
        "document": {
            "id": doc.id,
            "filename": doc.filename,
            "source_format": doc.source_format,
            "sha256": doc.sha256,
            "byte_size": doc.byte_size,
        },
        "draft": draft_pub,
    }


def confirm_storyboard_plan(
    db: Session,
    project: M4Project,
    *,
    actor: str,
    expected_revision: int | None = None,
    acknowledge_duration_mismatch: bool = False,
) -> dict[str, Any]:
    draft = db.scalar(
        select(M4StoryboardRevision)
        .where(M4StoryboardRevision.project_id == project.id, M4StoryboardRevision.status == "DRAFT")
        .order_by(M4StoryboardRevision.revision.desc())
        .limit(1)
    )
    if draft is None:
        raise HTTPException(
            409,
            {"code": "M4_STORYBOARD_DRAFT_MISSING", "message": "没有可确认的分镜草稿，请先导入或编辑分镜表。"},
        )
    if expected_revision is not None and draft.revision != expected_revision:
        raise HTTPException(
            409,
            {
                "code": "M4_STORYBOARD_REVISION_CONFLICT",
                "message": "分镜草稿版本号已变化，请刷新后再确认。",
                "expected_revision": expected_revision,
                "current_revision": draft.revision,
            },
        )

    normalized = _normalize_draft_payload(draft.payload)
    episodes = normalized["episodes"]
    if not episodes or all(not ep["shots"] for ep in episodes):
        raise HTTPException(
            422,
            {"code": "M4_STORYBOARD_EMPTY", "message": "分镜计划至少需要 1 集 1 个镜头才能冻结。"},
        )

    seen_ep_codes: set[str] = set()
    seen_shot_codes: set[str] = set()
    deviations: list[dict[str, Any]] = []
    for ep in episodes:
        if ep["episode_code"] in seen_ep_codes:
            raise HTTPException(
                422,
                {"code": "M4_EPISODE_CODE_DUPLICATE", "message": f"分集编号重复：{ep['episode_code']}"},
            )
        seen_ep_codes.add(ep["episode_code"])
        if not ep["shots"]:
            raise HTTPException(
                422,
                {"code": "M4_EPISODE_HAS_NO_SHOTS", "message": f"{ep['title']} 至少需要 1 个镜头。"},
            )
        ep_sum = 0.0
        for sh in ep["shots"]:
            if sh["shot_code"] in seen_shot_codes:
                raise HTTPException(422, {"code": "M4_SHOT_CODE_DUPLICATE", "message": f"镜号重复：{sh['shot_code']}"})
            seen_shot_codes.add(sh["shot_code"])
            dur = sh["target_seconds"]
            if dur is None:
                raise HTTPException(
                    422,
                    {
                        "code": "M4_SHOT_DURATION_MISSING",
                        "shot_code": sh["shot_code"],
                        "message": f"镜头 {sh['shot_code']} 缺少时长，请先填写（4–{project.max_shot_seconds} 秒）。",
                    },
                )
            if not (M4_MIN_SHOT_SECONDS <= dur <= float(project.max_shot_seconds)):
                raise HTTPException(
                    422,
                    {
                        "code": "M4_SHOT_DURATION_OUT_OF_RANGE",
                        "shot_code": sh["shot_code"],
                        "message": (
                            f"镜头 {sh['shot_code']} 时长 {dur}s "
                            f"超出允许范围（4–{project.max_shot_seconds}s）。"
                        ),
                    },
                )
            ep_sum += float(dur)
        if ep["target_total_seconds"] is not None and abs(ep_sum - float(ep["target_total_seconds"])) > 0.5:
            deviations.append(
                {
                    "episode_code": ep["episode_code"],
                    "target_total_seconds": ep["target_total_seconds"],
                    "planned_total_seconds": round(ep_sum, 2),
                    "delta_seconds": round(ep_sum - float(ep["target_total_seconds"]), 2),
                }
            )
    if deviations and not acknowledge_duration_mismatch:
        raise HTTPException(
            409,
            {
                "code": "M4_EPISODE_DURATION_DEVIATION",
                "message": (
                    "各镜目标时长之和与设定的分集目标总时长存在偏差，"
                    "请确认以实际镜头时长之和为准（系统不会自动截镜）。"
                ),
                "deviations": deviations,
            },
        )

    # Carry forward prior shot revisions & detect duration changes before superseding
    prior_confirmed = db.scalar(
        select(M4StoryboardRevision)
        .where(M4StoryboardRevision.project_id == project.id, M4StoryboardRevision.status == "CONFIRMED")
        .order_by(M4StoryboardRevision.revision.desc())
        .limit(1)
    )
    prior_shots_by_code: dict[str, M4Shot] = {}
    prior_reqs_by_key: dict[tuple[str, str], M4MaterialRequirement] = {}
    if prior_confirmed is not None:
        for old_sh in db.scalars(
            select(M4Shot).where(M4Shot.storyboard_revision_id == prior_confirmed.id)
        ).all():
            prior_shots_by_code[old_sh.shot_code] = old_sh
        for old_req in db.scalars(
            select(M4MaterialRequirement).where(M4MaterialRequirement.storyboard_revision_id == prior_confirmed.id)
        ).all():
            prior_reqs_by_key[(old_req.family, old_req.canonical_key)] = old_req
        prior_confirmed.status = "SUPERSEDED"

    now = m4_utcnow()
    draft.payload = normalized
    draft.summary_sha256 = canonical_digest(normalized)
    draft.status = "CONFIRMED"
    draft.confirmed_by = actor
    draft.confirmed_at = now
    draft.updated_at = now

    merged_reqs: dict[tuple[str, str], dict[str, Any]] = {}
    invalidated_prompts: list[str] = []

    for ep in episodes:
        planned_total = round(sum(float(s["target_seconds"]) for s in ep["shots"]), 2)
        ep_row = M4EpisodePlan(
            project_id=project.id,
            storyboard_revision_id=draft.id,
            episode_no=ep["episode_no"],
            episode_code=ep["episode_code"],
            title=ep["title"],
            target_total_seconds=ep["target_total_seconds"],
            planned_total_seconds=planned_total,
        )
        db.add(ep_row)
        db.flush()

        for sh in ep["shots"]:
            old_sh = prior_shots_by_code.get(sh["shot_code"])
            new_rev = 1
            changed = False
            invalidation_reason: str | None = None
            sh_content_sha = _shot_content_sha(
                target_seconds=float(sh["target_seconds"]),
                summary=sh["summary"],
                dialogue=sh["dialogue"],
                requested_assets=sh["requested_assets"],
                aspect_ratio=sh["aspect_ratio"],
                continuity_mode=sh["continuity_mode"],
            )
            if old_sh is not None:
                duration_changed = abs(float(old_sh.target_seconds) - float(sh["target_seconds"])) > 1e-6
                options_changed = (
                    old_sh.aspect_ratio != sh["aspect_ratio"]
                    or old_sh.continuity_mode != sh["continuity_mode"]
                )
                old_sha = getattr(old_sh, "content_sha256", None) or _shot_content_sha(
                    target_seconds=float(old_sh.target_seconds),
                    summary=old_sh.summary,
                    dialogue=old_sh.dialogue,
                    requested_assets=old_sh.requested_assets,
                    aspect_ratio=old_sh.aspect_ratio,
                    continuity_mode=old_sh.continuity_mode,
                )
                changed = old_sha != sh_content_sha
                new_rev = old_sh.revision + 1 if changed else old_sh.revision
                if changed:
                    invalidation_reason = (
                        "M4_DURATION_CHANGED"
                        if duration_changed
                        else ("M4_SHOT_OPTIONS_CHANGED" if options_changed else "M4_STORYBOARD_SHOT_CHANGED")
                    )
                    for pr in db.scalars(
                        select(M4ManualPromptRevision).where(
                            M4ManualPromptRevision.shot_id == old_sh.id,
                            M4ManualPromptRevision.status == "FROZEN",
                        )
                    ).all():
                        pr.status = "INVALIDATED"
                        pr.invalidated_reason = invalidation_reason
                        invalidated_prompts.append(sh["shot_code"])

            sh_row = M4Shot(
                project_id=project.id,
                storyboard_revision_id=draft.id,
                episode_id=ep_row.id,
                shot_no=sh["shot_no"],
                shot_code=sh["shot_code"],
                revision=new_rev,
                content_sha256=sh_content_sha,
                target_seconds=float(sh["target_seconds"]),
                summary=sh["summary"],
                dialogue=sh["dialogue"],
                requested_assets=sh["requested_assets"],
                source_span=sh["source_span"],
                aspect_ratio=sh["aspect_ratio"],
                continuity_mode=sh["continuity_mode"],
            )
            db.add(sh_row)
            db.flush()

            # Carry forward bindings, and preserve FROZEN only when shot semantics/options are unchanged;
            # otherwise preserve the prior text as INVALIDATED with explicit reason so history isn't lost.
            if old_sh is not None:
                for old_link in db.scalars(
                    select(M4ShotAssetBinding)
                    .where(M4ShotAssetBinding.shot_id == old_sh.id)
                    .order_by(M4ShotAssetBinding.ordinal)
                ).all():
                    db.add(
                        M4ShotAssetBinding(
                            project_id=project.id,
                            shot_id=sh_row.id,
                            asset_version_id=old_link.asset_version_id,
                            use_as=old_link.use_as,
                            role_label=old_link.role_label,
                            ordinal=old_link.ordinal,
                        )
                    )
                old_prompt = db.scalar(
                    select(M4ManualPromptRevision)
                    .where(
                        M4ManualPromptRevision.shot_id == old_sh.id,
                        M4ManualPromptRevision.status.in_(["FROZEN", "INVALIDATED"]),
                    )
                    .order_by(M4ManualPromptRevision.revision.desc())
                    .limit(1)
                )
                if old_prompt is not None:
                    db.add(
                        M4ManualPromptRevision(
                            project_id=project.id,
                            shot_id=sh_row.id,
                            revision=1,
                            status="FROZEN" if (not changed and old_prompt.status == "FROZEN") else "DRAFT",
                            invalidated_reason=(
                                None
                                if (not changed and old_prompt.status == "FROZEN")
                                else (invalidation_reason or old_prompt.invalidated_reason)
                            ),
                            prompt_text=old_prompt.prompt_text,
                            prompt_sha256=old_prompt.prompt_sha256,
                            shot_revision=sh_row.revision,
                            target_seconds=sh_row.target_seconds,
                            generation_mode=old_prompt.generation_mode,
                            reference_manifest=old_prompt.reference_manifest,
                            reference_manifest_sha256=old_prompt.reference_manifest_sha256,
                            warnings=old_prompt.warnings,
                            frozen_by=old_prompt.frozen_by,
                            frozen_at=old_prompt.frozen_at,
                        )
                    )

            for asset_ref in sh["requested_assets"]:
                ckey = _canonical_entity_key(asset_ref["name"], asset_ref.get("variant", ""))
                entry = merged_reqs.setdefault(
                    (asset_ref["family"], ckey),
                    {
                        "family": asset_ref["family"],
                        "entity_name": asset_ref["name"],
                        "variant_name": asset_ref.get("variant", ""),
                        "canonical_key": ckey,
                        "shot_ids": [],
                    },
                )
                if sh_row.id not in entry["shot_ids"]:
                    entry["shot_ids"].append(sh_row.id)

    for (family, ckey), req in merged_reqs.items():
        prior_req = prior_reqs_by_key.get((family, ckey))
        bound_id = None
        req_status = "declined" if (prior_req is not None and prior_req.user_declined) else "missing"
        if prior_req is not None and prior_req.bound_asset_version_id:
            av = db.get(M4AssetVersion, prior_req.bound_asset_version_id)
            if av is not None and av.status == "promoted":
                bound_id = av.id
                req_status = "bound"
        db.add(
            M4MaterialRequirement(
                project_id=project.id,
                storyboard_revision_id=draft.id,
                family=req["family"],
                entity_name=req["entity_name"],
                variant_name=req["variant_name"],
                canonical_key=req["canonical_key"],
                description=prior_req.description if prior_req else "",
                shot_ids=req["shot_ids"],
                status=req_status,
                bound_asset_version_id=bound_id,
                user_declined=prior_req.user_declined if prior_req else False,
            )
        )

    db.add(
        M4AuditEvent(
            project_id=project.id,
            actor=actor,
            stage="storyboard",
            event_type="M4_STORYBOARD_CONFIRMED",
            entity_type="m4_storyboard_revision",
            entity_id=draft.id,
            payload={
                "revision": draft.revision,
                "summary_sha256": draft.summary_sha256,
                "episodes": len(episodes),
                "requirements": len(merged_reqs),
                "invalidated_prompts": invalidated_prompts,
            },
        )
    )
    return {
        "confirmed": _revision_public(draft, project),
        "invalidated_prompts": invalidated_prompts,
        "requirements_count": len(merged_reqs),
    }
