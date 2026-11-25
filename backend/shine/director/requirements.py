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


def _preparation_catalog(
    db: Session, project_id: str, section_ids: list[str],
    section_keys: dict[str, list[str]],
    section_span_ids: dict[str, set[str]] | None = None,
) -> dict[str, Any]:
    """The entity source for projects without a four-stage catalogue.

    The streamline path already established the core cast / locations / props in
    per-scene ``ScriptPreparation`` artifacts (the same ledger the expert material
    page reads).  Each artifact carries its own ``scene_key`` and per-item
    ``source_span_ids``, so an entity is cited only by the sections whose
    document slice overlaps those spans — a prop from scene 2 never leaks into
    scene 1 just because both share the same document chunk key.
    """
    rows = db.scalars(select(AgentArtifact).where(
        AgentArtifact.project_id == project_id,
        AgentArtifact.kind == "ScriptPreparation",
        AgentArtifact.stale.is_(False),
    ).order_by(AgentArtifact.created_at.desc())).all()
    merged: dict[tuple[str, str], dict[str, Any]] = {}
    seen_scene_keys: set[str] = set()
    for row in rows:
        payload = dict(row.payload_json or {})
        row_key = str(payload.get("scene_key") or "")
        # Re-extracting an outline produces a newer ScriptPreparation for the same scene_key;
        # keep only the newest extraction per scene_key so old drafts never duplicate entities.
        if row_key:
            if row_key in seen_scene_keys:
                continue
            seen_scene_keys.add(row_key)
        key_matched = [sid for sid in section_ids if row_key and row_key in section_keys.get(sid, [])]
        for req in payload.get("requirements") or []:
            family = str(req.get("family") or "")
            name = str(req.get("name") or req.get("entity_id") or "").strip()
            if family not in {"character", "scene", "prop"} or not name:
                continue
            req_spans = {str(s).strip() for s in (req.get("source_span_ids") or []) if str(s).strip()}
            matched: list[str] = []
            for sid in key_matched:
                sec_spans = (section_span_ids or {}).get(sid) or set()
                if req_spans and sec_spans:
                    if req_spans & sec_spans:
                        matched.append(sid)
                else:
                    matched.append(sid)
            if not matched:
                continue
            entry = merged.setdefault((family, name), {
                "name": name,
                "description": str(req.get("description") or ""),
                "section_ids": [],
                "carrier": req.get("carrier"),
                "core_prop_reason": req.get("core_prop_reason"),
            })
            for sid in matched:
                if sid not in entry["section_ids"]:
                    entry["section_ids"].append(sid)
            if not entry["description"] and req.get("description"):
                entry["description"] = str(req.get("description"))
    characters = [{**e, "aliases": [], "identity": e["description"]}
                  for (f, _), e in merged.items() if f == "character"]
    scenes = [e for (f, _), e in merged.items() if f == "scene"]
    props = [e for (f, _), e in merged.items() if f == "prop"]
    return {"characters": characters, "scenes": scenes, "props": props}


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


def bound_profile(db: Session, project_id: str) -> WorkflowProfile | None:
    """Return an explicitly frozen legacy profile, when the package has one."""
    from shine.director.models import DirectorPackageVersion

    package = db.scalars(select(DirectorPackageVersion).where(
        DirectorPackageVersion.project_id == project_id,
        DirectorPackageVersion.status == "CONFIRMED",
        DirectorPackageVersion.stale.is_(False),
    ).order_by(DirectorPackageVersion.revision.desc())).first()
    if package is not None:
        profile_id = ((package.manifest or {}).get("binding") or {}).get("workflow_profile_id")
        if profile_id:
            return db.get(WorkflowProfile, profile_id)
    return None


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


def _critical_from_verdict(family: str, name: str, entry: dict[str, Any],
                           shot_problems: dict[str, list[str]],
                           aliases: dict[str, str]) -> bool:
    """Whether the renderer refuses to work without this item — the SAME verdict
    the journey uses (``_shot_material_problems``), never a second opinion.

    Ref2VA anchors on a named character subject and needs at least one reference
    per cut shot.  A character is critical when a shot that names it still lacks
    that subject master; a scene/prop is critical when a shot that uses it is a
    cut shot still missing its reference, or promised the asset by id.  Profile
    slot counts are display-only and must not decide criticality.
    """
    for shot_id in entry["shot_ids"]:
        problems = shot_problems.get(shot_id, [])
        if family == "character":
            for problem in problems:
                if problem.startswith("SHOT_CHARACTER_MASTER_MISSING:"):
                    raw = problem.split(":", 1)[1]
                    if aliases.get(character_reference_key(raw), raw) == name:
                        return True
        elif "SHOT_REFERENCE_MISSING" in problems:
            return True
    return family != "character" and bool(entry.get("cited_by_shot"))


def derive(db: Session, project: Project, *, episode_ids: list[str] | None = None) -> dict[str, Any]:
    """Return the per-scene preparation list plus the evidence behind each need."""
    from fastapi import HTTPException

    from shine.api_director import _confirmed_outline
    from shine.director.contracts import EpisodePlanPayload
    from shine.director.journey import _shot_material_problems
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

    scoped_sections = [str(sid) for ep in episodes for sid in ep.section_ids]
    section_keys: dict[str, list[str]] = {}
    section_span_ids: dict[str, set[str]] = {}
    for sid in scoped_sections:
        section = sections_by_id.get(sid)
        if section is None:
            continue
        start = int(section.get("start_offset") or 0)
        end = int(section.get("end_offset") or 0)
        section_keys[sid] = [row.key for row in document_scenes
                             if row.spans and row.spans[0].start_offset < end
                             and row.spans[-1].end_offset > start]
        section_span_ids[sid] = {
            span.id for row in document_scenes for span in row.spans
            if span.start_offset < end and span.end_offset > start
        }
    catalog = _catalog(db, project_id)
    if not catalog:
        catalog = _preparation_catalog(db, project_id, scoped_sections, section_keys, section_span_ids)
    aliases = _alias_index(catalog)
    capacity = reference_capacity(bound_profile(db, project_id))
    masters = list(db.scalars(select(Asset).where(
        Asset.project_id == project_id, Asset.approved.is_(True), Asset.locked.is_(True))).all())
    master_by_id = {row.id: row for row in masters}
    identity_by_name = {
        str(row.get("name")): str(
            row.get("identity") or row.get("introduction") or row.get("description") or ""
        )
        for row in outline_payload.get("characters") or []
    }
    # Load architecture location descriptions once so fallback scene plates use
    # pure environment copy rather than raw script prose full of characters.
    arch_locations: dict[str, str] = {}
    arch_row = db.scalars(select(AgentArtifact).where(
        AgentArtifact.project_id == project_id,
        AgentArtifact.kind == "SeriesArchitecture",
        AgentArtifact.stale.is_(False),
    ).order_by(AgentArtifact.created_at.desc())).first()
    if arch_row is not None:
        for loc in (arch_row.payload_json or {}).get("locations") or []:
            if isinstance(loc, dict) and str(loc.get("name") or "").strip():
                arch_locations[character_reference_key(str(loc["name"]))] = str(
                    loc.get("description") or ""
                ).strip()

    def _scene_base_key(raw: str) -> str:
        stripped = re.sub(r"[（(][^）)]*[）)]", "", str(raw or "")).strip()
        return character_reference_key(stripped or str(raw or ""))

    def _same_scene_place(left: str, right: str) -> bool:
        ka, kb = _scene_base_key(left), _scene_base_key(right)
        return bool(ka and kb and (ka == kb or (min(len(ka), len(kb)) >= 3 and (ka in kb or kb in ka))))

    def _scene_description_for(name: str, fallback: str = "") -> str:
        key = character_reference_key(name)
        if arch_locations.get(key):
            return arch_locations[key]
        for arch_name, arch_desc in arch_locations.items():
            if arch_desc and _same_scene_place(name, arch_name):
                return arch_desc
        for cat_loc in (catalog.get("scenes") or []):
            cat_name = str(cat_loc.get("name") or "").strip()
            cat_desc = str(cat_loc.get("description") or "").strip()
            if cat_name and cat_desc and _same_scene_place(name, cat_name):
                return cat_desc
        return fallback

    # (family, entity) -> evidence, merged across the whole scope.
    usage: dict[tuple[str, str], dict[str, Any]] = {}
    # 唯一裁决：每镜的素材缺口直接读 journey 的同一个判断函数（Ref2VA 每镜至少
    # 一个参考、点名人物必须有主体主素材），需求页不再按方案槽位另判一套。
    shot_problems: dict[str, list[str]] = {}
    # 哪几镜自己已经带着可绑成参考的需求（人物 / 场景）。道具不算参考：采纳执行者
    # 不会把道具绑成参考图，拿它当"这镜已经有参考"会让镜头一直停在缺参考上。
    referenced: set[str] = set()
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
        if shot_id and shot_id not in entry["shot_ids"]:
            entry["shot_ids"].append(shot_id)
        if episode_id and episode_id not in entry["episode_ids"]:
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

            for shot_id in shot_ids:
                shot = db.get(Shot, shot_id)
                if shot is None or shot.project_id != project_id:
                    continue
                spec = dict(shot.spec or {})
                shot_problems[shot.id] = _shot_material_problems(db, shot)
                beat = str(spec.get("story_beat") or "")
                for reference in _character_names(spec):
                    canonical = aliases.get(character_reference_key(reference), reference)
                    _note_local(local, "character", canonical,
                                variant=(reference if canonical != reference else None),
                                cited=True)
                    referenced.add(shot.id)
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
                    if family in {"character", "scene"}:
                        referenced.add(shot.id)
                    req_desc = str(requested.get("description") or "").strip()
                    if family == "scene" and not req_desc:
                        req_desc = _scene_description_for(canonical)
                    note(family, canonical, shot_id=shot.id, episode_id=episode.episode_id,
                         description=req_desc[:600],
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
                    if family in {"character", "scene"}:
                        referenced.add(shot.id)
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
                    if asset.role in {"character", "scene"}:
                        referenced.add(shot.id)
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

            # 场景底图列入清单供复用/选用，但不 blanket 塞进本场所有镜头：已采纳
            # 的场景经 bindings/required 反馈环保持它真正绑定的镜头；未采纳的只作
            # 建议；无参考的镜头由 lacking 环补一张本场场景。历史「全塞」绑定由
            # bind 执行者按「每镜至多一张场景」收敛清理。
            for loc in (catalog.get("scenes") or []):
                if str(section_id) not in [str(item) for item in loc.get("section_ids") or []]:
                    continue
                name = str(loc.get("name") or "").strip()
                if not name:
                    continue
                clean_scene_desc = _scene_description_for(name, str(loc.get("description") or ""))
                existing_scene = next(
                    (ename for (efam, ename) in local if efam == "scene" and _same_scene_place(name, ename)),
                    None,
                )
                target_name = existing_scene or name
                _note_local(local, "scene", target_name, cited=False)
                note("scene", target_name, shot_id="", episode_id=episode.episode_id,
                     description=clean_scene_desc[:300], cited_by_shot=False)
            # Ref2VA 每镜至少一个参考，但「这一场有没有素材」不是判据：同一场里
            # 一镜有人物、另一镜只有旁白时，整场判定会把旁白镜漏掉（这正是 23 镜
            # 项目 1d14b472 报 SHOT_REFERENCE_MISSING 却不在清单里的原因）。这里
            # 逐镜读 journey 的同一份裁决：只有真正没有可绑参考的镜头才补场景需求，
            # 名字用真实场次标题——已有场景主素材时它会直接命中复用。
            lacking = [shot_id for shot_id in (str(row) for row in shot_ids)
                       if "SHOT_REFERENCE_MISSING" in shot_problems.get(shot_id, [])
                       and shot_id not in referenced]
            if lacking:
                # Never copy raw character-action script prose into a scene
                # requirement's description: that leaked character names and
                # dialogue directly into empty-set image prompts.
                scene_desc = arch_locations.get(character_reference_key(scene_name), "")
                if not scene_desc:
                    for loc in (catalog.get("scenes") or []):
                        if str(section_id) in [str(item) for item in loc.get("section_ids") or []]:
                            scene_desc = str(loc.get("description") or "").strip()
                            if scene_desc:
                                break
                _note_local(local, "scene", scene_name)
                for shot_id in lacking:
                    note("scene", scene_name, shot_id=shot_id, episode_id=episode.episode_id,
                         description=scene_desc[:300] or scene_name)
            # Core props the catalogue already established for this section:
            # match per-shot usage from landed board/drama beats whenever
            # available instead of blindly attaching every prop to every shot.
            drama_beats_by_id: dict[str, str] = {}
            board_beats_by_shot_name: dict[str, list[str]] = {}
            board_row = db.scalars(select(AgentArtifact).where(
                AgentArtifact.project_id == project_id,
                AgentArtifact.kind == "SceneShotBoard",
                AgentArtifact.stale.is_(False),
            ).order_by(AgentArtifact.created_at.desc())).all()
            sec_board = next(
                (r for r in board_row
                 if str((r.payload_json or {}).get("production_section_id") or "") == str(section_id)),
                None,
            )
            if sec_board is not None:
                bpayload = dict(sec_board.payload_json or {})
                drama_id = bpayload.get("drama_scene_artifact_id")
                drama_art = db.get(AgentArtifact, drama_id) if drama_id else None
                if drama_art is not None:
                    for b_idx, beat_obj in enumerate((drama_art.payload_json or {}).get("beats") or [], start=1):
                        if not isinstance(beat_obj, dict):
                            continue
                        pieces = [str(beat_obj.get("visible_action") or "")]
                        pieces.extend(
                            str(ln.get("text") or "")
                            for ln in (beat_obj.get("spoken_lines") or [])
                            if isinstance(ln, dict)
                        )
                        drama_beats_by_id[f"B{b_idx}"] = " ".join(pieces)
                for s_entry in bpayload.get("shots") or []:
                    if not isinstance(s_entry, dict) or not s_entry.get("id"):
                        continue
                    b_ids = [str(b) for b in s_entry.get("beat_ids") or []]
                    for c_entry in s_entry.get("internal_cuts") or []:
                        if isinstance(c_entry, dict):
                            b_ids.extend(str(b) for b in c_entry.get("beat_ids") or [])
                    board_beats_by_shot_name[str(s_entry["id"])] = b_ids

            section_shots = [
                shot_obj for sid in shot_ids
                if (shot_obj := db.get(Shot, str(sid))) is not None and shot_obj.project_id == project_id
            ]
            def _prop_matches_shot(prop_name: str, beat_blob: str, spec_text: str) -> bool:
                if prop_name in beat_blob or prop_name in spec_text:
                    return True
                if len(prop_name) >= 4 and re.search(r"[\u4e00-\u9fff]", prop_name):
                    prefix = prop_name[:2]
                    tail = prop_name[-2:]
                    if prefix in beat_blob and tail in beat_blob:
                        return True
                    if len(prop_name) >= 5 and prefix in beat_blob:
                        return True
                    if len(prop_name) >= 6 and prop_name[2:4] in beat_blob:
                        return True
                return False

            for prop in (catalog.get("props") or []):
                if str(section_id) not in [str(item) for item in prop.get("section_ids") or []]:
                    continue
                name = str(prop.get("name") or "").strip()
                if not name:
                    continue
                core_prop = bool(prop.get("carrier") or prop.get("core_prop_reason"))
                matched_shot_ids: list[str] = []
                if drama_beats_by_id:
                    # Prefer exact per-shot beat evidence when a DramaSceneScript
                    # + SceneShotBoard exists for this section.
                    for shot_obj in section_shots:
                        spec = dict(shot_obj.spec or {})
                        shot_key = next(
                            (k for k in board_beats_by_shot_name
                             if shot_obj.name.endswith(f"-{k}") or shot_obj.name == k),
                            None,
                        )
                        beat_blob = " ".join(
                            drama_beats_by_id.get(bid, "")
                            for bid in (board_beats_by_shot_name.get(shot_key, []) if shot_key else [])
                        )
                        spec_props = " ".join(
                            str(p)
                            for pl in ((spec.get("blocking") or {}).get("placements") or [])
                            if isinstance(pl, dict)
                            for p in (pl.get("props") or [])
                        )
                        spec_text = " ".join([
                            str(spec.get("story_beat") or ""),
                            str(spec.get("entry_state") or ""),
                            str(spec.get("exit_state") or ""),
                            spec_props,
                        ])
                        if (_prop_matches_shot(name, beat_blob, spec_text)
                                or any(str(r.get("name") or "") == name
                                       for r in (spec.get("requested_assets") or []) if isinstance(r, dict))):
                            matched_shot_ids.append(shot_obj.id)
                target_shots = matched_shot_ids or [str(sid) for sid in shot_ids]
                _note_local(local, "prop", name, cited=core_prop)
                for shot_id in target_shots:
                    note("prop", name, shot_id=str(shot_id), episode_id=episode.episode_id,
                         description=str(prop.get("description") or "")[:300],
                         cited_by_shot=core_prop)

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
        critical = bool(entry.get("user_specified")) or _critical_from_verdict(
            family, name, entry, shot_problems, aliases)
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
