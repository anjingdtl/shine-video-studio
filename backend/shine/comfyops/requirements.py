"""Deterministic extraction from existing structured scripts; no model guesses assets."""

from fastapi import HTTPException

from shine.comfy.workflow import graph_hash


def entity(value):
    if isinstance(value, str):
        return value
    if isinstance(value, dict):
        return value.get("id") or value.get("entity_id") or value.get("name")
    return None


def extract_structured(script):
    if not isinstance(script, dict):
        raise HTTPException(422, "STRUCTURED_SCRIPT_OBJECT_REQUIRED")
    requirements, records = {}, {"scenes": [], "shots": [], "dialogue": [], "characters": []}

    def add(family, identity, scene=None, shot=None, **details):
        if not isinstance(identity, str) or not identity.strip():
            raise HTTPException(422, "STRUCTURED_ENTITY_ID_REQUIRED")
        key = (family, identity)
        row = requirements.setdefault(
            key, {"family": family, "entity_id": identity, "critical": True, "scene_ids": [], "shot_ids": [], **details}
        )
        if scene and scene not in row["scene_ids"]:
            row["scene_ids"].append(scene)
        if shot and shot not in row["shot_ids"]:
            row["shot_ids"].append(shot)

    scenes = script.get("scenes")
    if scenes is None:
        scenes = [{"id": "script", "shots": script.get("shots", script.get("beats", []))}]
    if not isinstance(scenes, list) or not scenes:
        raise HTTPException(422, "STRUCTURED_SCENES_OR_BEATS_REQUIRED")
    for scene in scenes:
        sid = entity(scene)
        if not sid:
            raise HTTPException(422, "STRUCTURED_SCENE_ID_REQUIRED")
        records["scenes"].append({"id": sid, "description": scene.get("description", "")})
        if scene.get("location"):
            add("scene", entity(scene["location"]), sid)
        for field, family in (("props", "prop"), ("outfits", "costume"), ("sfx", "sfx"), ("music", "music")):
            for value in scene.get(field, []):
                add(family, entity(value), sid)
        shots = scene.get("shots", scene.get("beats", []))
        if not isinstance(shots, list):
            raise HTTPException(422, "STRUCTURED_SHOTS_REQUIRED")
        for shot in shots:
            shid = entity(shot)
            if not shid:
                raise HTTPException(422, "STRUCTURED_SHOT_ID_REQUIRED")
            records["shots"].append(
                {"id": shid, "scene_id": sid, "description": shot.get("description", shot.get("story_beat", ""))}
            )
            for character in shot.get("characters", []):
                cid = entity(character)
                add("character", cid, sid, shid)
                if cid not in records["characters"]:
                    records["characters"].append(cid)
                if isinstance(character, dict) and character.get("outfit_id"):
                    add("costume", character["outfit_id"], sid, shid, character_id=cid)
                else:
                    add("costume", cid, sid, shid)
            for field, family in (
                ("props", "prop"),
                ("outfits", "costume"),
                ("sfx", "sfx"),
                ("music", "music"),
                ("keyframes", "keyframe"),
                ("storyboards", "storyboard"),
                ("actions", "action"),
            ):
                for value in shot.get(field, []):
                    add(family, entity(value), sid, shid)
            for index, line in enumerate(shot.get("dialogue", [])):
                speaker = line.get("speaker_id") or line.get("speaker")
                if not isinstance(line.get("text"), str) or not line["text"]:
                    raise HTTPException(422, "STRUCTURED_DIALOGUE_TEXT_REQUIRED")
                add("character", speaker, sid, shid)
                add("voice", speaker, sid, shid)
                identity = f"{shid}:{index + 1}"
                locked = {"id": identity, "scene_id": sid, "shot_id": shid, **line, "speaker_id": speaker}
                records["dialogue"].append(locked)
                native = shot.get("audio_mode", "H3_NATIVE") == "H3_NATIVE"
                add(
                    "dialogue",
                    identity,
                    sid,
                    shid,
                    character_id=speaker,
                    text=line["text"],
                    line_hash=graph_hash(locked),
                    satisfaction="LOCKED_SCRIPT" if native else "APPROVED_ASSET",
                )
                if shot.get("subtitles_required", True):
                    add("subtitle", identity, sid, shid, text=line["text"], character_id=speaker)
    return list(requirements.values()), records
