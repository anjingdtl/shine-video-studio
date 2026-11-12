"""Derive the material requirement list from the finished four-stage analysis.

The crew already read every contracted scene once.  A single project-wide
consolidation call (``materialprep.catalog``) merges aliases into one entry
per real character, keeps per-scene appearance variants on that entry, and
picks key scenes / key props.  This module maps that catalog onto the
server's material segmentation and opens the same ScriptPreparation plan
contract, so confirmation and generation stay on their existing gates.

Character descriptions are the stable identity only: per-scene clothing
changes live in the catalog variants and in each shot's visual description,
never in a second master.
"""
from __future__ import annotations

from fastapi import HTTPException
from sqlalchemy import select

from shine.asset_identity import split_character_names
from shine.materialprep.catalog import get_or_create_catalog
from shine.materialprep.source_spans import segment_document
from shine.models import AgentArtifact, ProjectDocument


def derive_preparations(db, gateway, session_factory, project_id: str):
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
            scripts[payload["section_id"]] = {**payload, "_sha256": row.sha256}
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

    catalog = get_or_create_catalog(db, gateway, session_factory, project_id,
                                    document, scripts, bible)

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

    # The name the shots actually use must be the master's entity_id, or the
    # binding cannot resolve it.  Prefer the alias that appears in the cast /
    # speakers over a catalog display name like "程岩／罗兰·温布顿".
    all_raw_names: set[str] = set()
    for sids in by_scene.values():
        for sid in sids:
            payload = scripts[sid]
            for entry in payload.get("characters") or []:
                raw = entry if isinstance(entry, str) else str(entry.get("name") or "")
                all_raw_names.update(split_character_names(raw))
            for line in payload.get("dialogue") or []:
                all_raw_names.update(split_character_names(
                    line.get("speaker") or line.get("speaker_id") or ""))

    preparations = []
    for scene in scenes:
        sids = by_scene[scene.key]
        if not sids:
            continue
        sid_set = set(sids)
        span_ids = [span.id for span in scene.spans][:8]
        raw_names: set[str] = set()
        for sid in sids:
            payload = scripts[sid]
            for entry in payload.get("characters") or []:
                raw = entry if isinstance(entry, str) else str(entry.get("name") or "")
                raw_names.update(split_character_names(raw))
            for line in payload.get("dialogue") or []:
                raw_names.update(split_character_names(
                    line.get("speaker") or line.get("speaker_id") or ""))
        requirements = []
        for character in catalog.get("characters") or []:
            names = {character.get("name"), *(character.get("aliases") or [])}
            if not (sid_set & set(character.get("section_ids") or [])) and not (names & raw_names):
                continue
            display = next((n for n in [character.get("name"), *(character.get("aliases") or [])]
                            if n in all_raw_names), character["name"])
            requirements.append({
                "family": "character", "name": display,
                "description": (character.get("identity") or "")[:600],
                "source_span_ids": span_ids, "questions": []})
        for key_scene in catalog.get("scenes") or []:
            if sid_set & set(key_scene.get("section_ids") or []):
                requirements.append({
                    "family": "scene", "name": key_scene["name"],
                    "description": (key_scene.get("description") or "")[:600],
                    "source_span_ids": span_ids, "questions": []})
        for prop in catalog.get("props") or []:
            if sid_set & set(prop.get("section_ids") or []):
                requirements.append({
                    "family": "prop", "name": prop["name"],
                    "description": (prop.get("description") or "")[:600],
                    "carrier": prop.get("carrier") or "object",
                    "core_prop_reason": (prop.get("core_prop_reason") or "")[:200],
                    "source_span_ids": span_ids, "questions": []})
        if not requirements:
            continue
        preparations.append({
            "schema_version": "1.0", "scene_key": scene.key,
            "summary": str(scripts[sids[0]].get("title") or "")[:800],
            "locked_facts": [], "requirements": requirements[:32], "questions": []})
    if not preparations:
        raise HTTPException(409, "MATERIAL_DERIVE_EMPTY")
    return document, preparations, catalog


def enrich_character_definitions(db, plan, catalog) -> None:
    """Attach the full-picture character definition to each character requirement.

    The binding key (entity_id) stays the name the shots use; the canonical
    full name, aliases, stable identity and per-scene variants travel alongside
    so the material centre shows the accurate whole character, not a fragment.
    """
    from shine.comfy.workflow import graph_hash
    from shine.comfyops.models import AssetRequirement

    by_name: dict[str, dict] = {}
    for character in catalog.get("characters") or []:
        for name in [character.get("name"), *(character.get("aliases") or [])]:
            if name:
                by_name.setdefault(name, character)
    requirements = [dict(row) for row in (plan.payload.get("requirements") or [])]
    changed = False
    for requirement in requirements:
        if requirement.get("family") != "character":
            continue
        character = by_name.get(requirement.get("entity_id"))
        if not character:
            continue
        definition = {
            "canonical_name": character.get("name"),
            "aliases": character.get("aliases") or [],
            "identity": character.get("identity") or "",
            "variants": character.get("variants") or [],
        }
        requirement["character_definition"] = definition
        changed = True
        row = db.get(AssetRequirement, requirement.get("requirement_id")) \
            if requirement.get("requirement_id") else None
        if row is not None:
            row.payload = {**(row.payload or {}), "character_definition": definition}
    if changed:
        plan.payload = {**plan.payload, "requirements": requirements}
        plan.sha256 = graph_hash(plan.payload)