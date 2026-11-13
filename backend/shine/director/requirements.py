"""Derive the material needs of the *confirmed shots*, not of the script.

The shots already carry who is in frame, what they say and which references they
promised; the material list must answer that question.  Re-reading the novel with
another agent would buy the same facts twice and let the second reading disagree
with the confirmed design.

So this module composes existing ledgers only:

* the confirmed episode plan and its landed shots (``ShotSpec``);
* the newest ``MaterialEntityCatalog`` artifact, when one exists, for canonical
  names / aliases / per-scene variants — a read, never a new model call;
* the project's approved masters and the shots' bindings, to see what is really
  available and which references a shot promised by id;
* the workflow profile the scope really binds, to decide what the renderer
  *needs* rather than what a nicer picture might want.

Families stay inside the published ``ScriptPreparation`` contract
(character / scene / prop).  Voice is deliberately not a family here: the H3
contract speaks a dialogue line natively and the material centre has no voice
slot, so inventing a "necessary voice reference" would promise something the
renderer cannot consume.
"""

from __future__ import annotations

import re
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from shine.asset_identity import (
    character_reference_key,
    resolve_character_asset,
    split_character_names,
)
from shine.h3.narrator import is_narrator_speaker
from shine.models import AgentArtifact, Asset, Project, Shot, WorkflowProfile

CATALOG_KIND = "MaterialEntityCatalog"


# ---------------------------------------------------------------------------
# Existing ledgers this module reads
# ---------------------------------------------------------------------------


def _catalog(db: Session, project_id: str) -> dict[str, Any]:
    """The newest usable entity catalogue, or an empty one (never a model call)."""
    row = db.scalars(select(AgentArtifact).where(
        AgentArtifact.project_id == project_id,
        AgentArtifact.kind == CATALOG_KIND,
        AgentArtifact.stale.is_(False),
    ).order_by(AgentArtifact.created_at.desc())).first()
    return dict(row.payload_json or {}) if row is not None else {}


def _alias_index(catalog: dict[str, Any]) -> dict[str, str]:
    index: dict[str, str] = {}
    for character in catalog.get("characters") or []:
        name = str(character.get("name") or "").strip()
        if not name:
            continue
        for alias in [name, *list(character.get("aliases") or [])]:
            key = character_reference_key(str(alias))
            if key:
                index.setdefault(key, name)
    return index


def _catalog_entry(catalog: dict[str, Any], family: str, name: str) -> dict[str, Any] | None:
    collection = {"character": "characters", "scene": "scenes", "prop": "props"}.get(family)
    if not collection:
        return None
    key = character_reference_key(name)
    for row in catalog.get(collection) or []:
        if character_reference_key(str(row.get("name") or "")) == key:
            return row
        for alias in row.get("aliases") or []:
            if character_reference_key(str(alias)) == key:
                return row
    return None


def bound_profile(db: Session, project_id: str) -> WorkflowProfile | None:
    """The profile the current scope really renders with, from existing rows.

    A confirmed package pins its profile.  A project without a package has no
    frozen binding yet, and the newest registered profile is the honest answer;
    mode names alone never decide it.
    """
    from shine.director.models import DirectorPackageVersion

    package = db.scalars(select(DirectorPackageVersion).where(
        DirectorPackageVersion.project_id == project_id,
        DirectorPackageVersion.status == "CONFIRMED",
        DirectorPackageVersion.stale.is_(False),
    ).order_by(DirectorPackageVersion.revision.desc())).first()
    if package is not None:
        profile_id = ((package.manifest or {}).get("binding") or {}).get("workflow_profile_id")
        if profile_id:
            row = db.get(WorkflowProfile, profile_id)
            if row is not None:
                return row
    return db.scalars(select(WorkflowProfile).where(
        WorkflowProfile.project_id == project_id).order_by(WorkflowProfile.created_at.desc())).first()


def reference_capacity(profile: WorkflowProfile | None) -> dict[str, Any]:
    """What the renderer can actually consume, counted from its parameter map.

    ``reference_subject_N`` is a character subject slot, ``reference_picture_N``
    a still reference, ``reference_audio_N`` an audio transfer.  A family with no
    slot is not a requirement — it is a suggestion at best.
    """
    parameter_map = dict((profile.parameter_map if profile is not None else {}) or {})
    media = dict((profile.media_transfers if profile is not None else {}) or {})
    subjects = sum(1 for key in parameter_map if re.match(r"^reference_subject_\d+$", key))
    pictures = sum(1 for key in parameter_map if re.match(r"^reference_picture_\d+$", key))
    videos = sum(1 for key in parameter_map if re.match(r"^reference_video_\d+$", key))
    audios = sum(1 for key in parameter_map if re.match(r"^reference_audio_\d+$", key))
    return {
        "profile_id": profile.id if profile is not None else None,
        "profile_name": profile.name if profile is not None else None,
        "mode": profile.mode if profile is not None else None,
        "subjects": subjects,
        "pictures": pictures,
        "videos": videos,
        "audios": audios,
        "image_transfer": "image" in media,
        "audio_transfer": "audio" in media,
    }


# ---------------------------------------------------------------------------
# Derivation
# ---------------------------------------------------------------------------


def _character_names(spec: dict[str, Any]) -> list[str]:
    names: list[str] = []
    for entry in spec.get("characters") or []:
        names.extend(split_character_names(entry))
    for line in spec.get("dialogue") or []:
        names.extend(split_character_names(line.get("speaker_id") or ""))
    # The narrator speaks without a picture reference, so it is not a character
    # material: requiring a "narrator portrait" would buy an image nobody sees.
    return list(dict.fromkeys(name for name in names
                              if name and not is_narrator_speaker(name)))


def _master_for(family: str, name: str, masters: list[Asset]) -> Asset | None:
    if family == "character":
        resolved = resolve_character_asset(name, [
            {"id": row.id, "role": row.role, "entity_id": row.entity_id,
             "filename": row.filename, "kind": row.kind} for row in masters
        ])
        if resolved is not None:
            return next((row for row in masters if row.id == resolved["id"]), None)
        # 同一人物被确认过两份主素材时，保守匹配会因"不唯一"而放弃——那会让
        # 已经有素材的人显示成"缺"。这里取用户最后一次确认并锁定的那一份，
        # 既确定又符合"以最近确认为准"的直觉；两份都不会被删除。
        key = character_reference_key(name)
        duplicates = [row for row in masters
                      if row.role == "character"
                      and character_reference_key(row.entity_id or "") == key]
        if duplicates:
            return max(duplicates, key=lambda row: row.created_at)
        return None
    key = character_reference_key(name)
    return next((row for row in masters
                 if row.role == family and character_reference_key(row.entity_id or "") == key), None)


def _note_local(local: dict[tuple[str, str], dict[str, Any]], family: str, name: str, *,
                variant: str | None = None, cited: bool = False) -> None:
    entry = local.setdefault((family, name), {"variants": [], "cited": False})
    if variant and variant not in entry["variants"]:
        entry["variants"].append(variant)
    entry["cited"] = entry["cited"] or cited


def _is_critical(family: str, capacity: dict[str, Any], *, cited_by_shot: bool) -> bool:
    """Whether the renderer refuses to work without this item.

    A character subject is what Ref2VA anchors on, so it is critical whenever
    the bound profile declares a subject slot.  A scene or prop plate is critical
    only when a shot promised that exact asset by id: the shot's own contract is
    the reason, never a general opinion about good filmmaking.
    """
    if family == "character":
        return capacity.get("subjects", 0) > 0
    return bool(cited_by_shot)


def derive(db: Session, project: Project, *, episode_ids: list[str] | None = None) -> dict[str, Any]:
    """Return the per-scene preparation list plus the evidence behind each need."""
    from fastapi import HTTPException

    from shine.api_director import _confirmed_outline
    from shine.director.contracts import EpisodePlanPayload
    from shine.director.streamline import _latest_plan
    from shine.materialprep.source_spans import segment_document
    from shine.materialprep.story import section_storyboard_shot_ids

    project_id = project.id
    outline = _confirmed_outline(db, project_id)
    if outline is None:
        raise HTTPException(409, "SCRIPT_OUTLINE_NOT_CONFIRMED")
    plan = _latest_plan(db, project_id)
    if plan is None:
        raise HTTPException(409, "EPISODE_PLAN_NOT_CONFIRMED")
    payload = EpisodePlanPayload.model_validate(plan.payload)
    episodes = [row for row in payload.episodes
                if not episode_ids or row.episode_id in episode_ids]
    outline_payload = dict(outline.payload_json or {})
    sections_by_id = {str(row["production_section_id"]): row
                      for row in outline_payload.get("production_sections") or []}
    from shine.models import ProjectDocument

    document = db.get(ProjectDocument, outline_payload.get("document_id"))
    document_scenes = segment_document(document.text or "") if document is not None else []

    catalog = _catalog(db, project_id)
    aliases = _alias_index(catalog)
    capacity = reference_capacity(bound_profile(db, project_id))
    masters = list(db.scalars(select(Asset).where(
        Asset.project_id == project_id, Asset.approved.is_(True), Asset.locked.is_(True))).all())
    master_by_id = {row.id: row for row in masters}
    identity_by_name = {
        str(row.get("name")): str(row.get("identity") or row.get("description") or "")
        for row in outline_payload.get("characters") or []
    }

    # (family, entity) -> evidence, merged across the whole scope.
    usage: dict[tuple[str, str], dict[str, Any]] = {}
    preparations: list[dict[str, Any]] = []

    def note(family: str, name: str, *, shot_id: str, episode_id: str,
             description: str = "", variant: str | None = None,
             cited_by_shot: bool = False, user_specified: bool = False) -> None:
        entry = usage.setdefault((family, name), {
            "family": family, "entity_id": name, "description": "",
            "shot_ids": [], "episode_ids": [], "variants": [], "cited_by_shot": False,
            "user_specified": False,
        })
        entry["user_specified"] = entry.get("user_specified", False) or user_specified
        if shot_id not in entry["shot_ids"]:
            entry["shot_ids"].append(shot_id)
        if episode_id not in entry["episode_ids"]:
            entry["episode_ids"].append(episode_id)
        if description and len(description) > len(entry["description"]):
            entry["description"] = description
        if variant and variant not in entry["variants"]:
            entry["variants"].append(variant)
        entry["cited_by_shot"] = entry["cited_by_shot"] or cited_by_shot

    for episode in episodes:
        for section_id in episode.section_ids:
            section = sections_by_id.get(str(section_id))
            if section is None:
                continue
            resolved = section_storyboard_shot_ids(db, project_id, str(section_id))
            shot_ids = [str(shot_id) for shot_id in resolved["shot_ids"]]
            if not shot_ids:
                continue
            start = int(section.get("start_offset") or 0)
            end = int(section.get("end_offset") or 0)
            scene = next((row for row in document_scenes
                          if row.spans[0].start_offset < end and row.spans[-1].end_offset > start), None)
            source_span_ids = [span.id for span in scene.spans][:8] if scene is not None else []
            scene_key = scene.key if scene is not None else f"section:{str(section_id)[:60]}"
            scene_name = str(section.get("title") or "").strip() or f"场次 {section.get('order') or ''}".strip()
            local: dict[tuple[str, str], dict[str, Any]] = {}
            scene_description = ""

            for shot_id in shot_ids:
                shot = db.get(Shot, shot_id)
                if shot is None or shot.project_id != project_id:
                    continue
                spec = dict(shot.spec or {})
                beat = str(spec.get("story_beat") or "")
                for reference in _character_names(spec):
                    canonical = aliases.get(character_reference_key(reference), reference)
                    _note_local(local, "character", canonical,
                                variant=(reference if canonical != reference else None),
                                cited=True)
                    note("character", canonical, shot_id=shot.id, episode_id=episode.episode_id,
                         description=identity_by_name.get(canonical)
                         or identity_by_name.get(reference) or "",
                         variant=(reference if canonical != reference else None),
                         cited_by_shot=True)
                # A material the person explicitly added to this shot.  It is a
                # real requirement even though no approved master exists yet:
                # that is exactly what the material stage is for.
                for requested in spec.get("requested_assets") or []:
                    family = str(requested.get("family") or "")
                    name = str(requested.get("name") or "").strip()
                    if family not in {"character", "scene", "prop"} or not name:
                        continue
                    canonical = aliases.get(character_reference_key(name), name)
                    _note_local(local, family, canonical, cited=True)
                    note(family, canonical, shot_id=shot.id, episode_id=episode.episode_id,
                         description=str(requested.get("description") or "")[:600],
                         cited_by_shot=True, user_specified=True)
                # A reference the shot promised by id is a real need whatever
                # the family: the shot already names it.
                for asset_id in spec.get("required_asset_ids") or []:
                    asset = master_by_id.get(str(asset_id)) or db.get(Asset, str(asset_id))
                    if asset is None:
                        continue
                    family = asset.role if asset.role in {"character", "scene", "prop"} else "prop"
                    entity = str(asset.entity_id or asset.filename)
                    _note_local(local, family, entity, cited=True)
                    note(family, entity, shot_id=shot.id, episode_id=episode.episode_id,
                         description=str((asset.provenance or {}).get("description") or "")[:300],
                         cited_by_shot=True)
                # A binding proves the need (and its variant) even before the
                # requirement list is rebuilt.
                for link in shot.bindings or []:
                    asset = master_by_id.get(link.asset_id) or db.get(Asset, link.asset_id)
                    if asset is None or asset.role not in {"character", "scene", "prop"}:
                        continue
                    entity = str(asset.entity_id or asset.filename)
                    _note_local(local, str(asset.role), entity, variant=(link.role or None), cited=True)
                    note(str(asset.role), entity, shot_id=shot.id,
                         episode_id=episode.episode_id, variant=(link.role or None),
                         cited_by_shot=True)
                # An approved prop master named in the beat is a real prop of
                # this shot; the master is the evidence, not a guess.
                if beat:
                    for asset in masters:
                        if asset.role != "prop" or not asset.entity_id:
                            continue
                        if str(asset.entity_id) in beat:
                            _note_local(local, "prop", str(asset.entity_id), cited=True)
                            note("prop", str(asset.entity_id), shot_id=shot.id,
                                 episode_id=episode.episode_id, cited_by_shot=True)

            # The scene plate: a suggestion unless a shot promised it by id.
            scene_catalog = _catalog_entry(catalog, "scene", scene_name)
            scene_text = str((scene_catalog or {}).get("description") or scene_description or
                             (section.get("title") or ""))[:300]
            if scene_text and shot_ids:
                _note_local(local, "scene", scene_name)
                for shot_id in shot_ids:
                    note("scene", scene_name, shot_id=str(shot_id),
                         episode_id=episode.episode_id, description=scene_text)
            # Core props the catalogue already established for this section.
            for prop in (catalog.get("props") or []):
                if str(section_id) not in [str(item) for item in prop.get("section_ids") or []]:
                    continue
                name = str(prop.get("name") or "").strip()
                if not name:
                    continue
                _note_local(local, "prop", name, cited=bool(prop.get("carrier")))
                for shot_id in shot_ids:
                    note("prop", name, shot_id=str(shot_id), episode_id=episode.episode_id,
                         description=str(prop.get("description") or "")[:300],
                         cited_by_shot=bool(prop.get("carrier")))

            if not local:
                continue
            preparations.append({
                "scene_key": scene_key,
                "requirements": [
                    {"family": family, "name": name,
                     "description": usage[(family, name)]["description"][:600],
                     "source_span_ids": source_span_ids}
                    for (family, name) in local
                ],
                "questions": [],
            })

    rows = []
    for (family, name), entry in sorted(usage.items(), key=lambda item: (item[0][0], item[0][1])):
        master = _master_for(family, name, masters)
        critical = entry.get("user_specified") or _is_critical(
            family, capacity, cited_by_shot=entry["cited_by_shot"])
        rows.append({
            **entry,
            "family": family,
            "entity_id": name,
            "name": name,
            "shot_count": len(entry["shot_ids"]),
            "used_episodes": entry["episode_ids"],
            "matched_master_id": master.id if master is not None else None,
            "matched_master_sha256": master.sha256 if master is not None else None,
            "critical": critical,
            "optional": not critical,
            "strategy": ("reuse_master" if master is not None
                         else ("generate" if critical else "skip")),
        })
    return {
        "preparations": preparations,
        "requirements": rows,
        "capacity": capacity,
        "catalog_present": bool(catalog),
        "profile": ({"id": capacity["profile_id"], "name": capacity["profile_name"],
                     "mode": capacity["mode"]} if capacity["profile_id"] else None),
    }
