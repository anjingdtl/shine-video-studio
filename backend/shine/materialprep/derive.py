"""Derive the material requirement list from the finished four-stage analysis.

The crew already read every contracted scene once: the scene scripts name the
cast and carry the scene prose, and the global bible holds each character's
stable identity.  Running a second whole-script scriptwriter analysis would
buy the same facts again, so this path reuses the durable artifacts and makes
zero model calls.  The output is the published ScriptPreparation contract, so
the plan builder, confirmation and generation stay on their existing gates.
"""
from __future__ import annotations

from fastapi import HTTPException
from sqlalchemy import select

from shine.asset_identity import split_character_names
from shine.materialprep.source_spans import segment_document
from shine.models import AgentArtifact, ProjectDocument


def derive_preparations(db, project_id: str):
    from shine.director import pipeline

    volume_ids: list[str] = []
    for plan in db.scalars(select(AgentArtifact).where(
        AgentArtifact.project_id == project_id,
        AgentArtifact.kind == "FourStageVolumePlan",
    ).order_by(AgentArtifact.created_at.desc())):
        for volume in (plan.payload_json or {}).get("volumes") or []:
            if volume.get("volume_id") and volume["volume_id"] not in volume_ids:
                volume_ids.append(volume["volume_id"])
    contracts = [c for vid in volume_ids
                 for c in pipeline.volume_episode_contracts(db, project_id, vid)
                 if c["confirmed"] and c["current"]]
    if not contracts:
        raise HTTPException(409, "MATERIAL_NO_CONFIRMED_EPISODES")
    section_ids = list(dict.fromkeys(s for c in contracts for s in c["sections"]))

    scripts: dict[str, dict] = {}
    document_id = None
    for row in db.scalars(select(AgentArtifact).where(
        AgentArtifact.project_id == project_id,
        AgentArtifact.kind == "FourStageSceneScript",
        AgentArtifact.stale.is_(False),
    ).order_by(AgentArtifact.created_at)):
        payload = row.payload_json or {}
        if payload.get("section_id") in section_ids:
            scripts[payload["section_id"]] = payload
            document_id = (payload.get("source_refs") or {}).get("volume_document_id") or document_id
    missing = [sid for sid in section_ids if sid not in scripts]
    if missing:
        raise HTTPException(409, {"code": "MATERIAL_SCENE_SCRIPT_MISSING", "section_ids": missing})
    document = db.get(ProjectDocument, document_id)
    if document is None or document.project_id != project_id:
        raise HTTPException(409, "MATERIAL_DOCUMENT_NOT_FOUND")

    bible = db.scalars(select(AgentArtifact).where(
        AgentArtifact.project_id == project_id,
        AgentArtifact.kind == "FourStageGlobalStoryBible",
    ).order_by(AgentArtifact.created_at.desc())).first()
    identities: dict[str, str] = {}
    for character in ((bible.payload_json or {}).get("characters") or []) if bible else []:
        name = str(character.get("name") or "").split("（")[0].strip()
        if name:
            identities[name] = str(character.get("identity") or "")

    scenes = segment_document(document.text or "")
    by_scene: dict[str, list[str]] = {scene.key: [] for scene in scenes}
    for sid in section_ids:
        refs = scripts[sid].get("source_refs") or {}
        start, end = refs.get("start_offset"), refs.get("end_offset")
        if start is None or end is None:
            continue
        for scene in scenes:
            if start < scene.spans[-1].end_offset and end > scene.spans[0].start_offset:
                by_scene[scene.key].append(sid)
                break

    preparations = []
    for scene in scenes:
        sids = by_scene[scene.key]
        if not sids:
            continue
        span_ids = [span.id for span in scene.spans][:8]
        names: list[str] = []
        for sid in sids:
            payload = scripts[sid]
            for entry in payload.get("characters") or []:
                raw = entry if isinstance(entry, str) else str(entry.get("name") or "")
                for part in split_character_names(raw):
                    if part not in names:
                        names.append(part)
            for line in payload.get("dialogue") or []:
                for part in split_character_names(line.get("speaker") or line.get("speaker_id") or ""):
                    if part not in names:
                        names.append(part)
        requirements = [
            {"family": "character", "name": name,
             "description": identities.get(name, "")[:600],
             "source_span_ids": span_ids, "questions": []}
            for name in names[:24]
        ]
        first = scripts[sids[0]]
        setting = " ".join((first.get("text") or "").split())[:300]
        requirements.append({
            "family": "scene", "name": str(first.get("title") or scene.key)[:100],
            "description": setting[:600], "source_span_ids": span_ids, "questions": []})
        preparations.append({
            "schema_version": "1.0", "scene_key": scene.key,
            "summary": str(first.get("title") or "")[:800],
            "locked_facts": [], "requirements": requirements[:32], "questions": []})
    if not preparations:
        raise HTTPException(409, "MATERIAL_DERIVE_EMPTY")
    return document, preparations