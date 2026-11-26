"""The director streamline ①–⑤ path: one editable outline, one storyboard
book, one per-episode shot table, one shot-driven material list, and an
episode-scoped package.

Every test here walks the real services the resident Worker uses — no API
shortcuts around the landing gates, no hand-built plan rows pretending to be
confirmed.  The mock provider stands only where a real text model would be.
"""

from __future__ import annotations

import copy

import pytest

from shine.director import streamline
from shine.director.contracts import derive_episode_plan_by_count
from shine.llm.gateway import LLMGateway
from shine.llm.secrets import SecretStore
from shine.models import AgentArtifact, AgentTaskRecord
from tests.conftest import create_project

LOCAL = {"x-shine-local-action": "1"}


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _sections(count: int, *, seconds: float = 30.0) -> list[dict]:
    return [
        {"production_section_id": f"sec-{index + 1:02d}", "order": index + 1,
         "title": f"第{index + 1}场", "estimated_duration_s": seconds,
         "start_offset": index * 200, "end_offset": index * 200 + 199}
        for index in range(count)
    ]


def _approved_outline(factory, project: str, document: dict, rows: list[dict],
                      *, questions: list[str] | None = None) -> dict:
    from shine.director.package import digest_of
    from shine.models import utcnow

    payload = {
        "production_sections": rows, "document_id": document["id"],
        "document_sha256": document["sha256"],
        "summary": "雨夜里，钟表匠老周修好一只停在十二点的怀表。",
        "story_line": ["老周修好怀表", "客人第二天消失"],
        "timeline": ["深夜修表", "次日发现人去楼空"],
        "relationships": ["老周与客人是旧识"],
        "questions": questions or [],
        "characters": [{"name": "老周", "entity_id": "老周",
                        "identity": "五十岁的钟表匠，沉默寡言"}],
        "locked_facts": ["主角叫老周"],
    }
    with factory.begin() as db:
        source = AgentTaskRecord(
            project_id=project, role_id="story_outline_run",
            request_identity=f"outline-{document['id']}-{len(rows)}",
            input_hash="0" * 64, asset_manifest_hash="0" * 64,
            rulepack_id="test", rulepack_hash="0" * 64,
            model_profile_id="test", model_id="test", status="SUCCEEDED", trace={},
        )
        db.add(source)
        db.flush()
        artifact = AgentArtifact(
            project_id=project, kind="StoryOutline", schema_version="1.0",
            payload_json=payload, sha256=digest_of(payload),
            producer_role="scriptwriter", source_task_id=source.id,
            approved_by="local-user", approved_at=utcnow(),
        )
        db.add(artifact)
        db.flush()
        return {"id": artifact.id, "sha256": artifact.sha256, "sections": rows}


def _document(client, project: str, name: str, text: str) -> dict:
    response = client.post(f"/api/v1/projects/{project}/documents",
                           files={"file": (name, text.encode("utf-8"), "text/plain")})
    assert response.status_code == 201, response.text
    return response.json()


def _prepare_project(client, *, sections: int = 4, section_seconds: float = 30.0,
                     questions: list[str] | None = None):
    """One project with rulepack, routes, a character master, a confirmed outline.

    The document's real bytes are padded so every section really occupies the
    200-character span its outline claims — the source-range check inside the
    chain (`STORY_SOURCE_RANGE_INVALID`) is exact, not approximate.
    """
    from tests.test_film_crew import prepare

    project_id, _style, _profile = prepare(
        client, freeze=False, character_entity_id="老周", character_filename="老周.png")
    parts = []
    for index in range(sections):
        body = (f"第{index + 1}场\n老周把怀表贴近耳朵，又放回丝绒垫上。"
                f"客人留下硬币，走进雨里。")
        parts.append(body + "。" * (200 - len(body) - 1) + "\n")
    text = "".join(parts)
    document = _document(client, project_id, "雨夜钟表铺.txt", text)
    outline = _approved_outline(
        client.app.state.session_factory, project_id, document,
        _sections(sections, seconds=section_seconds), questions=questions)
    return {"project": project_id, "document": document, "outline": outline}


def _storyboard_book(client, project: str, *, episode_count: int = 2,
                     target_seconds: int = 60, tolerance_seconds: int = 30) -> dict:
    response = client.post(
        f"/api/v1/projects/{project}/director/storyboard-book",
        json={"episode_count": episode_count, "target_seconds": target_seconds,
              "tolerance_seconds": tolerance_seconds},
        headers=LOCAL)
    assert response.status_code == 201, response.text
    return response.json()


def _confirm_book(client, project: str, plan_version: int, plan_sha: str) -> dict:
    response = client.post(
        f"/api/v1/projects/{project}/director/storyboard-book/confirm",
        json={"plan_version": plan_version, "expected_sha256": plan_sha,
              "style": {"profile_id": "wong-kar-wai", "film_type": "live_action",
                        "dialogue_ratio_percent": 80}},
        headers=LOCAL)
    assert response.status_code == 200, response.text
    return response.json()


def _chain_responder(registry: dict, *, fail_section: str | None = None,
                     fail_drama: bool = False):
    """The chain's answer, optionally broken for one section or for the drama writer."""
    character = "老周"

    def respond(messages, schema):
        import json
        import re

        title = schema.get("title")
        blob = "".join(message.content for message in messages)
        match = re.search(r'"production_section_id"\s*:\s*"([^"]+)"', blob)
        section = match.group(1) if match else ""
        # The drama layer's two answers are derived from the context the server
        # sends: the plan the architect must address, and the slot the writer
        # must write into.  (The user message is that context, then the schema.)
        from shine.literary.requests import SCHEMA_INSTRUCTION

        context = json.loads(messages[-1].content.split(SCHEMA_INSTRUCTION)[0])
        production = context.get("production_context") or {}
        if title == "SeriesArchitecture":
            plan = production["plan"]
            place = (production.get("registered_locations") or [{"name": "钟表铺"}])[0]["name"]
            return copy.deepcopy({
                "locations": [{"name": place,
                               "description": "一间挂满钟的狭长店铺，柜台临街。",
                               "time_variants": ["night"]}],
                "episodes": [{"episode_id": episode["episode_id"], "opening_hook": "怀表停在十二点",
                              "climax_beat": "老周修好怀表", "cliffhanger": "客人走进雨里"}
                             for episode in plan["episodes"]],
                "scene_slots": [
                    {"production_section_id": section_id, "location": place, "time_of_day": "night",
                     "dramatic_goal": "老周修好怀表，客人留下硬币",
                     "transition_from_prev": "episode_open" if position == 0 else "location_switch",
                     "weight": 1.0}
                    for episode in plan["episodes"]
                    for position, section_id in enumerate(episode["section_ids"])],
            })
        if title == "DramaSceneScript":
            if fail_drama:
                # One line a shot cannot hold: the server's own verdict refuses it.
                return {"location": production["scene_slot"]["location"], "beats": [
                    {"visible_action": "老周说话。",
                     "spoken_lines": [{"speaker": character, "text": "又停在十二点。" * 6}]}]}
            return copy.deepcopy({
                "location": production["scene_slot"]["location"],
                "time_of_day": production["scene_slot"]["time_of_day"],
                "weather_atmosphere": "雨",
                "entry_bridge": "老周没有抬头。",
                "exit_state": "灯灭了。",
                "beats": [
                    {"beat_type": "action",
                     "visible_action": "老周把怀表贴近耳朵，又放回丝绒垫上。",
                     "characters": [character],
                     "spoken_lines": [{"speaker": character, "emotion": "压低声音", "text": "又停在十二点。"}]},
                    {"beat_type": "transition_out", "visible_action": "客人留下硬币，走进雨里。",
                     "characters": [], "spoken_lines": []},
                ],
            })
        if title == "SceneShotBoard":
            if fail_section and section == fail_section:
                return {"shots": [{"id": "S1", "beat_ids": ["B1"]}]}
            scene_input = next(
                (item["payload"] for item in context.get("inputs") or [] if item.get("kind") == "DramaSceneScript"),
                {},
            )
            scene_beats = scene_input.get("beats") or []
            place = str(scene_input.get("location") or "钟表铺")
            cap = float(context.get("max_shot_seconds") or 10.0)
            has_pred = bool(production.get("continuity_predecessor"))
            approved_ids = {a["id"] for a in (context.get("assets") or [])}
            default_assets = [registry["asset"]] if registry.get("asset") in approved_ids else []
            axis_label = "counter-axis"

            def _line_duration(text: str) -> float:
                chars = len(re.sub(r"\s+", "", text or ""))
                return round(max(1.2, chars / 4.2) + 0.3, 2)

            def _beat_chars(beat: dict) -> list[str]:
                names: list[str] = []
                for entry in beat.get("characters") or []:
                    if entry and entry not in names:
                        names.append(entry)
                for line in beat.get("spoken_lines") or []:
                    sp = str(line.get("speaker") or "").strip()
                    if sp and sp not in names:
                        names.append(sp)
                return names

            sides: dict[str, tuple[str, str]] = {}

            def _placements_for(names: list[str]) -> list[dict]:
                rows = []
                for name in names:
                    if name not in sides:
                        if len(sides) % 2 == 0:
                            sides[name] = ("left_fg", "facing_right")
                        else:
                            sides[name] = ("right_fg", "facing_left")
                    zone, facing = sides[name]
                    rows.append({
                        "character": name, "zone": zone, "facing": facing,
                        "posture": "standing at the counter", "props": [],
                    })
                return rows

            shots: list[dict] = []
            idx = 0
            while idx < len(scene_beats):
                b1 = scene_beats[idx]
                b1_id = f"B{idx + 1}"
                b1_lines = b1.get("spoken_lines") or []
                b2 = scene_beats[idx + 1] if idx + 1 < len(scene_beats) else None
                b2_lines = (b2.get("spoken_lines") or []) if b2 else []
                can_pair = (
                    b2 is not None
                    and len(b1_lines) == 1
                    and len(b2_lines) == 1
                    and b1_lines[0].get("speaker") != b2_lines[0].get("speaker")
                )
                if can_pair:
                    d1 = _line_duration(b1_lines[0]["text"])
                    d2 = _line_duration(b2_lines[0]["text"])
                    cut_s = round(max(2.2, 0.4 + d1 + 0.4), 2)
                    total_s = round(max(cut_s + max(2.2, 0.4 + d2 + 0.4), 8.0), 2)
                    if total_s <= cap + 1e-6:
                        b2_id = f"B{idx + 2}"
                        cast = list(dict.fromkeys(_beat_chars(b1) + _beat_chars(b2)))
                        dialogue = [
                            {"speaker_id": b1_lines[0]["speaker"], "text": b1_lines[0]["text"],
                             "start_s": 0.4, "end_s": round(0.4 + d1, 2)},
                            {"speaker_id": b2_lines[0]["speaker"], "text": b2_lines[0]["text"],
                             "start_s": round(cut_s + 0.4, 2), "end_s": round(cut_s + 0.4 + d2, 2)},
                        ]
                        mid = round(total_s / 2, 2)
                        shots.append({
                            "id": f"S{len(shots) + 1}",
                            "beat_ids": [b1_id],
                            "duration_s": total_s,
                            "visual_description": (
                                "The first speaker leans over the wooden counter under warm lamplight."
                            ),
                            "asset_ids": list(default_assets),
                            "location": place,
                            "dialogue": dialogue,
                            "camera": {
                                "composition": "Medium over-shoulder framing on the first speaker.",
                                "shot_size": "MS", "angle": "eye-level", "lens": "50mm",
                                "move": "static", "motive": "",
                                "movement": "The camera holds still.",
                                "lighting": "Warm desk lamp key against cool rainlight.",
                            },
                            "ambience": "Rain pattering steadily on the shop glass.",
                            "music": "none",
                            "blocking": {"axis": axis_label, "placements": _placements_for(cast)},
                            "environment": {
                                "depth": "Wooden counter foreground, clock shelves behind.",
                                "light": "Warm amber desk lamp key with cool window fill.",
                                "micro_motion": "Fine dust drifting in the lamp beam.",
                            },
                            "timeline": [
                                {"start_s": 0.0, "end_s": mid,
                                 "action": "First performer speaks across the counter.",
                                 "expression": "Focused eyes and steady jaw.",
                                 "camera": "The camera holds still."},
                                {"start_s": mid, "end_s": total_s,
                                 "action": "Reverse angle catches the second performer answering.",
                                 "expression": "Brows tighten before speaking.",
                                 "camera": "The camera holds still."},
                            ],
                            "entry_state": "The wooden counter lit by the warm desk lamp.",
                            "exit_state": "Both performers hold their gaze across the counter.",
                            "transition_motivation": (
                                "continuous_action" if (len(shots) > 0 or has_pred) else "establishing_reset"
                            ),
                            "handoff_cue": "steady gaze across the counter",
                            "internal_cuts": [{
                                "start_s": cut_s, "beat_ids": [b2_id],
                                "visual_description": "Reverse medium shot of the second performer across the counter.",
                                "camera": "The camera holds still.",
                            }],
                        })
                        idx += 2
                        continue
                cast = _beat_chars(b1)
                dialogue = []
                cursor_s = 0.4
                for line in b1_lines:
                    dur = _line_duration(line["text"])
                    dialogue.append({
                        "speaker_id": line["speaker"], "text": line["text"],
                        "start_s": round(cursor_s, 2), "end_s": round(cursor_s + dur, 2),
                    })
                    cursor_s += dur + 0.3
                needed = cursor_s + 0.4 if dialogue else 5.0
                base_s = 7.0 if dialogue else 6.0
                total_s = min(cap, max(base_s if needed <= base_s else round(needed, 2), 4.0))
                mid = round(total_s / 2, 2)
                shots.append({
                    "id": f"S{len(shots) + 1}",
                    "beat_ids": [b1_id],
                    "duration_s": total_s,
                    "visual_description": "A watchmaker holds a pocket watch under the warm desk lamp.",
                    "asset_ids": list(default_assets),
                    "location": place,
                    "dialogue": dialogue,
                    "camera": {
                        "composition": "Medium close-up at the lamp-lit counter.",
                        "shot_size": "MCU", "angle": "eye-level", "lens": "50mm",
                        "move": "static", "motive": "",
                        "movement": "The camera holds still.",
                        "lighting": "Warm desk lamp key from screen right.",
                    },
                    "ambience": "Rain pattering steadily on the shop glass.",
                    "music": "none",
                    "blocking": {"axis": axis_label, "placements": _placements_for(cast)},
                    "environment": {
                        "depth": "Velvet tray in foreground, hanging clocks behind.",
                        "light": "Warm amber desk lamp key with cool window fill.",
                        "micro_motion": "Fine dust drifting in the lamp beam.",
                    },
                    "timeline": [
                        {"start_s": 0.0, "end_s": mid,
                         "action": "Performer adjusts posture at the counter.",
                         "expression": "Quiet concentration, eyes lowered.",
                         "camera": "The camera holds still."},
                        {"start_s": mid, "end_s": total_s,
                         "action": "Performer settles hands onto the counter.",
                         "expression": "Breath steadies, gaze holds.",
                         "camera": "The camera holds still."},
                    ],
                    "entry_state": "The lamp-lit wooden counter and velvet tray.",
                    "exit_state": "The performer pauses still by the counter.",
                    "transition_motivation": (
                        "continuous_action" if (len(shots) > 0 or has_pred) else "establishing_reset"
                    ),
                    "handoff_cue": "continuous action at the counter",
                    "internal_cuts": [],
                })
                idx += 1
            locked_facts = list(context.get("locked_story_facts") or [])
            return copy.deepcopy({
                "shots": shots,
                "prompt_style": {
                    "photography_language": "Stable composed frames with clear sightlines.",
                    "shot_blocking": "Readable blocking between people and story objects.",
                    "color_palette": "Restrained palette with one clear emphasis.",
                    "lighting_tone": "Directional lamp light with layered shadow.",
                    "lens_texture": "Clean, stable optical texture.",
                    "editing_rhythm": "Deliberate reveals after reactions.",
                    "sound_performance": "Original dialogue with restrained ambience.",
                },
                "prompt_story_facts": [f"Locked story fact {i + 1}." for i in range(len(locked_facts))],
            })
        if fail_section and section == fail_section and title == "ScriptBreakdown":
            # A decided, answered refusal with genuinely missing content: the
            # salvage step cannot invent the required prose, so the refusal
            # is honest and the episode pauses.
            return {"beats": [{"id": "B1", "characters": [character], "dialogue": []}]}
        if title == "ScriptBreakdown":
            return copy.deepcopy({
                "locked_story_facts": ["主角叫老周"],
                "prompt_story_facts": ["The protagonist is a watchmaker called Lao Zhou."],
                # The frozen template style is Chinese; the contract demands one
                # faithful English rendering per frozen field.
                "prompt_style": {
                    "photography_language": "Stable composed frames with clear sightlines.",
                    "shot_blocking": "Readable blocking between people and story objects.",
                    "color_palette": "Restrained palette with one clear emphasis.",
                    "lighting_tone": "Directional lamp light with layered shadow.",
                    "lens_texture": "Clean, stable optical texture.",
                    "editing_rhythm": "Deliberate reveals after reactions.",
                    "sound_performance": "Original dialogue with restrained ambience.",
                },
                "beats": [
                    {"id": "B1", "description": "老周把怀表贴近耳朵，又放回丝绒垫上。",
                     "characters": [character], "dialogue": [
                         {"speaker_id": character, "text": "又停在十二点。", "start_s": 1, "end_s": 2.4}]},
                    {"id": "B2", "description": "客人留下硬币，走进雨里。",
                     "characters": [character], "dialogue": []},
                ],
            })
        if title == "DirectorialPlan":
            return copy.deepcopy({
                "shots": [
                    {"id": "I1", "beat_id": "B1",
                     "visual_description": "A watchmaker holds a pocket watch to his ear, then lays it on velvet.",
                     "duration_s": 7, "asset_ids": [registry["asset"]]},
                    {"id": "I2", "beat_id": "B2",
                     "visual_description": "A guest leaves a coin on the counter and walks into the rain.",
                     "duration_s": 6, "asset_ids": [registry["asset"]]},
                ],
            })
        if title == "CameraPlan":
            return copy.deepcopy({
                "shots": [
                    {"intent_id": "I1", "composition": "Medium close-up, subject right third",
                     "movement": "Static", "lighting": "Warm lamp key", "axis": "Preserve axis"},
                    {"intent_id": "I2", "composition": "Wide of the shop doorway",
                     "movement": "Static", "lighting": "Rain-blue exterior", "axis": "Same axis"},
                ],
            })
        if title == "ContinuityReport":
            return {"issues": [], "recommendation": "proceed_to_preflight"}
        if title == "EditIntentPlan":
            return copy.deepcopy({
                "schema_version": "1.2", "intent_id": "D1",
                "clips": [
                    {"intent_id": "I1", "order": 1, "planned_duration_s": 7.0,
                     "transition": "CUT", "continuity_note": "establishing"},
                    {"intent_id": "I2", "order": 2, "planned_duration_s": 6.0,
                     "transition": "CUT", "continuity_note": "reaction"},
                ],
                "rhythm": "steady", "dialogue_pace": "natural Mandarin pace",
            })
        raise AssertionError(f"unexpected schema {title}")

    return respond


def _run_storyboard_to_rest(client, settings, project: str, *, fail_section: str | None = None,
                            max_ticks: int = 40, responder=None) -> None:
    """Drive the resident worker's director-storyboard step until it rests."""
    factory = client.app.state.session_factory
    assets = client.get(f"/api/v1/projects/{project}/assets").json()
    master = next(a["id"] for a in assets if a["role"] == "character" and a["entity_id"])
    responder = responder or _chain_responder({"asset": master}, fail_section=fail_section)
    gateway = LLMGateway(SecretStore(settings.secret_store_dir), mock_responder=responder)

    for _ in range(max_ticks):
        advanced = streamline.run_pending_director_storyboard(
            factory, settings, "worker-test", gateway=gateway)
        if not advanced:
            break


# ---------------------------------------------------------------------------
# ① editable outline
# ---------------------------------------------------------------------------


def test_outline_markdown_round_trip_is_lossless():
    payload = {"summary": "一夜之间。", "story_line": ["a", "b"], "timeline": [],
               "relationships": ["x"], "questions": ["客人去哪了？"], "other": {"keep": 1}}
    markdown = streamline.outline_to_markdown(payload)
    parsed = streamline.markdown_to_outline(markdown, payload)
    assert parsed["summary"] == "一夜之间。"
    assert parsed["story_line"] == ["a", "b"]
    assert parsed["relationships"] == ["x"]
    assert parsed["questions"] == ["客人去哪了？"]
    assert parsed["other"] == {"keep": 1}  # untouched non-anchored content


def test_outline_character_edit_keeps_identity_and_updates_introduction():
    payload = {
        "summary": "雨夜。",
        "characters": [
            {"name": "老周", "entity_id": "entity-001", "stable_identity": "钟表匠",
             "introduction": "五十岁的钟表匠，沉默寡言。"},
            {"name": "客人", "entity_id": "entity-002",
             "introduction": "雨夜来访的陌生人。"},
        ],
        "story_line": [], "timeline": [], "relationships": [], "questions": [],
    }
    markdown = streamline.outline_to_markdown(payload)
    assert "## 登场人物" in markdown
    edited = markdown.replace("五十岁的钟表匠，沉默寡言。", "五十岁的钟表匠，雨夜修好一只怀表。")
    parsed = streamline.markdown_to_outline(edited, payload)
    zhou = next(c for c in parsed["characters"] if c["name"] == "老周")
    assert zhou["introduction"] == "五十岁的钟表匠，雨夜修好一只怀表。"
    assert zhou["entity_id"] == "entity-001"
    assert zhou["stable_identity"] == "钟表匠"
    guest = next(c for c in parsed["characters"] if c["name"] == "客人")
    assert guest["entity_id"] == "entity-002"
    assert guest["introduction"] == "雨夜来访的陌生人。"


def test_outline_edit_revision_and_one_click_confirm(api):
    client, factory, settings = api
    prepared = _prepare_project(client, sections=2, questions=["客人到底是谁？"])
    project, outline = prepared["project"], prepared["outline"]

    current = client.get(f"/api/v1/projects/{project}/director/outline").json()
    assert current["artifact_id"] == outline["id"]
    assert "客人到底是谁？" in current["markdown"]

    edited = current["markdown"].replace("一夜之间。", "雨夜，钟表匠老周修好怀表。")
    revision = client.post(
        f"/api/v1/projects/{project}/director/outline/revision",
        json={"expected_sha256": current["sha256"], "markdown": edited}, headers=LOCAL)
    assert revision.status_code == 201, revision.text
    body = revision.json()
    assert body["artifact_id"] != outline["id"]
    assert body["questions"] == ["客人到底是谁？"]

    confirmed = client.post(
        f"/api/v1/projects/{project}/director/outline/confirm",
        json={"artifact_id": body["artifact_id"], "expected_sha256": body["sha256"]},
        headers=LOCAL)
    assert confirmed.status_code == 200, confirmed.text
    journey = client.get(f"/api/v1/projects/{project}/journey").json()
    script = next(stage for stage in journey["stages"] if stage["id"] == "script")
    view = client.get(f"/api/v1/projects/{project}/director/outline").json()
    assert script["done"] is True and script["blockers"] == []
    assert view["confirmed"] is True
    assert script["facts"]["outline_artifact_id"] == view["confirmed_artifact_id"] == body["artifact_id"]
    with factory() as db:
        row = db.get(AgentArtifact, body["artifact_id"])
        assert row.approved_at is not None


def test_outline_edit_conflict_refuses_stale_sha(api):
    client, factory, settings = api
    prepared = _prepare_project(client, sections=2)
    project = prepared["project"]
    current = client.get(f"/api/v1/projects/{project}/director/outline").json()
    stale = "0" * 64
    response = client.post(
        f"/api/v1/projects/{project}/director/outline/revision",
        json={"expected_sha256": stale, "markdown": current["markdown"]}, headers=LOCAL)
    assert response.status_code == 409


def test_outline_version_chain_tracks_draft_and_confirmed(api):
    client, factory, settings = api
    prepared = _prepare_project(client, sections=2)
    project, outline = prepared["project"], prepared["outline"]

    current = client.get(f"/api/v1/projects/{project}/director/outline").json()
    assert [v["artifact_id"] for v in current["versions"]] == [outline["id"]]
    assert current["versions"][0]["confirmed"] is True

    revision = client.post(
        f"/api/v1/projects/{project}/director/outline/revision",
        json={"expected_sha256": current["sha256"],
              "markdown": current["markdown"].replace("一夜之间。", "雨夜改稿。")},
        headers=LOCAL)
    assert revision.status_code == 201, revision.text
    body = revision.json()

    view = client.get(f"/api/v1/projects/{project}/director/outline").json()
    assert view["artifact_id"] == body["artifact_id"]
    assert view["approved"] is False
    assert view["confirmed"] is False
    assert view["user_edited"] is True
    assert view["confirmed_artifact_id"] == outline["id"]
    chain = view["versions"]
    assert [v["artifact_id"] for v in chain] == [body["artifact_id"], outline["id"]]
    assert chain[0]["revision_of"] == outline["id"]
    assert chain[0]["user_edited"] is True
    assert chain[1]["confirmed"] is True

    old = client.get(
        f"/api/v1/projects/{project}/director/outline?artifact_id={outline['id']}").json()
    assert old["artifact_id"] == outline["id"]
    assert old["markdown"] == current["markdown"]

    # A pending draft must not displace the confirmed basis downstream.
    book = client.post(
        f"/api/v1/projects/{project}/director/storyboard-book",
        json={"episode_count": 1, "target_seconds": 60, "tolerance_seconds": 15},
        headers=LOCAL)
    assert book.status_code == 201, book.text

    missing = client.get(
        f"/api/v1/projects/{project}/director/outline"
        "?artifact_id=00000000-0000-0000-0000-000000000000")
    assert missing.status_code == 404

    # Confirming the draft switches the single basis to v2; v1 stays history.
    confirmed = client.post(
        f"/api/v1/projects/{project}/director/outline/confirm",
        json={"artifact_id": body["artifact_id"], "expected_sha256": body["sha256"]},
        headers=LOCAL)
    assert confirmed.status_code == 200, confirmed.text
    after = client.get(f"/api/v1/projects/{project}/director/outline").json()
    assert after["artifact_id"] == body["artifact_id"]
    assert after["approved"] is True and after["confirmed"] is True
    assert after["confirmed_artifact_id"] == body["artifact_id"]
    chain = {v["artifact_id"]: v for v in after["versions"]}
    assert chain[body["artifact_id"]]["confirmed"] is True
    assert chain[outline["id"]]["confirmed"] is False
    assert chain[outline["id"]]["approved"] is True  # history is never rewritten
    from shine.director import basis
    from shine.models import ProjectDocument
    with factory() as db:
        document = db.get(ProjectDocument, prepared["document"]["id"])
        resolved = basis.outline_for_document(db, project, document)
        assert resolved is not None and resolved.id == body["artifact_id"]

    # The expert route can select an earlier approved revision. The director
    # and journey must follow that recorded choice instead of creation order.
    restored = client.post(
        f"/api/v1/projects/{project}/story-preparation/outlines/{outline['id']}/confirm",
        json={"expected_sha256": current["sha256"]},
        headers=LOCAL,
    )
    assert restored.status_code == 200, restored.text
    assert restored.json() == {"artifact_id": outline["id"], "approved": True}
    current_view = client.get(f"/api/v1/projects/{project}/director/outline").json()
    assert current_view["confirmed_artifact_id"] == outline["id"]
    script_stage = next(
        stage for stage in client.get(f"/api/v1/projects/{project}/journey").json()["stages"]
        if stage["id"] == "script"
    )
    assert script_stage["done"] and script_stage["blockers"] == []
    assert script_stage["facts"]["outline_artifact_id"] == outline["id"]

# ---------------------------------------------------------------------------
# ② storyboard book
# ---------------------------------------------------------------------------


def test_episode_plan_by_count_target_governs_and_count_caps_scope():
    # 2026-11-19 用户裁定：每集秒数决定范围，集数是上限；超范围显式声明，
    # 不得把整剧塞进第一集再报一个偏差数字。
    from shine.director.contracts import validate_section_coverage

    payload = derive_episode_plan_by_count(
        _sections(5, seconds=30), episode_count=1, target_seconds=60, tolerance_seconds=15)
    assert len(payload.episodes) == 1
    assert payload.episodes[0].section_ids == ["sec-01", "sec-02"]
    assert payload.out_of_scope_section_ids == ["sec-03", "sec-04", "sec-05"]
    two = derive_episode_plan_by_count(
        _sections(5, seconds=30), episode_count=2, target_seconds=60, tolerance_seconds=15)
    assert [e.section_ids for e in two.episodes] == [["sec-01", "sec-02"], ["sec-03", "sec-04"]]
    assert two.out_of_scope_section_ids == ["sec-05"]
    # 覆盖门读同一份声明：declared out 不是遗漏。
    all_ids = [f"sec-{index:02d}" for index in range(1, 6)]
    assert validate_section_coverage(payload, all_ids) == []
    undeclared = payload.model_copy(update={"out_of_scope_section_ids": []})
    assert any(p.startswith("SECTIONS_NOT_COVERED") for p in validate_section_coverage(undeclared, all_ids))


def test_episode_plan_by_count_refuses_more_episodes_than_sections():
    with pytest.raises(ValueError):
        derive_episode_plan_by_count(_sections(2), episode_count=3)


def test_storyboard_book_confirm_pairs_plan_and_style(api):
    client, factory, settings = api
    prepared = _prepare_project(client, sections=4)
    project = prepared["project"]

    book = _storyboard_book(client, project, episode_count=2, target_seconds=60)
    preview = book["preview"]
    assert preview["episode_count"] == 2
    assert preview["episodes"][0]["sections"], "the book must read sections"
    assert "deviation_seconds" in preview["episodes"][0]

    result = _confirm_book(client, project, book["plan_version"], book["plan_sha256"])
    assert result["plan_version"] == book["plan_version"]
    assert result["style"]["style_version_id"]

    from sqlalchemy import select as sa_select

    from shine.director.models import DirectorEpisodePlan
    from shine.models import Project, StyleVersion
    with factory() as db:
        confirmed = db.scalars(sa_select(DirectorEpisodePlan).where(
            DirectorEpisodePlan.project_id == project,
            DirectorEpisodePlan.version == book["plan_version"])).one()
        assert confirmed.status == "CONFIRMED"
        project_row = db.get(Project, project)
        frozen = db.get(StyleVersion, project_row.frozen_style_version_id)
        assert frozen is not None
        assert (frozen.snapshot or {}).get("film_type") == "live_action"


def test_storyboard_book_refuses_without_confirmed_outline(api):
    client, factory, settings = api
    project = create_project(client, "无大纲")
    response = client.post(
        f"/api/v1/projects/{project}/director/storyboard-book",
        json={"episode_count": 2, "target_seconds": 60}, headers=LOCAL)
    assert response.status_code == 422


# ---------------------------------------------------------------------------
# ③ storyboard run, per-episode isolation and confirmation
# ---------------------------------------------------------------------------


def test_storyboard_run_lands_shots_per_episode(api):
    client, factory, settings = api
    prepared = _prepare_project(client, sections=4)
    project = prepared["project"]
    book = _storyboard_book(client, project, episode_count=2, target_seconds=60)
    _confirm_book(client, project, book["plan_version"], book["plan_sha256"])

    queued = client.post(f"/api/v1/projects/{project}/director/storyboard/generate",
                         json={}, headers=LOCAL)
    assert queued.status_code == 202, queued.text
    task_id = queued.json()["task_id"]
    # Idempotent: the same contract reuses the live run.
    again = client.post(f"/api/v1/projects/{project}/director/storyboard/generate",
                        json={}, headers=LOCAL)
    assert again.json()["task_id"] == task_id

    _run_storyboard_to_rest(client, settings, project)

    state = client.get(f"/api/v1/projects/{project}/director/storyboard/state").json()
    assert state["status"] == "SUCCEEDED", state
    episodes = {row["episode_id"]: row for row in state["episodes"]}
    assert set(episodes) == {"ep-01", "ep-02"}
    for row in episodes.values():
        assert row["status"] == "APPLIED", row
        assert row["shot_ids"], "each section lands its shots"

    # The per-episode table reads the landed shots with editorial facts.
    table = client.get(f"/api/v1/projects/{project}/director/storyboard/episodes/ep-01/shots").json()
    assert table["shot_count"] >= 2
    first = table["shots"][0]
    assert {"duration_target_s", "editorial_seconds", "h3_generation_seconds",
            "internal_cuts", "continuity_mode"} <= set(first)
    assert first["characters"]


def test_confirm_one_episode_while_the_other_is_pending(api):
    client, factory, settings = api
    prepared = _prepare_project(client, sections=4)
    project = prepared["project"]
    book = _storyboard_book(client, project, episode_count=2, target_seconds=60)
    _confirm_book(client, project, book["plan_version"], book["plan_sha256"])
    pending_section = prepared["outline"]["sections"][2]["production_section_id"]

    client.post(f"/api/v1/projects/{project}/director/storyboard/generate", json={}, headers=LOCAL)
    _run_storyboard_to_rest(client, settings, project, fail_section=pending_section)

    state = client.get(f"/api/v1/projects/{project}/director/storyboard/state").json()
    by_id = {row["episode_id"]: row for row in state["episodes"]}
    assert by_id["ep-01"]["status"] == "APPLIED"
    assert by_id["ep-02"]["status"] == "BLOCKED"
    assert state["status"] == "NEEDS_REVIEW"

    # The ready episode lands even while the other is blocked.
    confirmed = client.post(
        f"/api/v1/projects/{project}/director/storyboard/episodes/ep-01/confirm",
        json={"acknowledge_duration_deviation": True}, headers=LOCAL)
    assert confirmed.status_code == 200, confirmed.text
    shot_ids = confirmed.json()["shot_ids"]
    assert shot_ids
    # A repeated confirmation reuses the same shots, never duplicates them.
    repeat = client.post(
        f"/api/v1/projects/{project}/director/storyboard/episodes/ep-01/confirm",
        json={"acknowledge_duration_deviation": True}, headers=LOCAL)
    assert repeat.status_code == 200
    assert repeat.json()["shot_ids"] == shot_ids


def test_blocked_episode_retry_resumes_and_finishes(api):
    client, factory, settings = api
    prepared = _prepare_project(client, sections=4)
    project = prepared["project"]
    book = _storyboard_book(client, project, episode_count=2, target_seconds=60)
    _confirm_book(client, project, book["plan_version"], book["plan_sha256"])
    pending_section = prepared["outline"]["sections"][2]["production_section_id"]
    client.post(f"/api/v1/projects/{project}/director/storyboard/generate", json={}, headers=LOCAL)
    _run_storyboard_to_rest(client, settings, project, fail_section=pending_section)

    state = client.get(f"/api/v1/projects/{project}/director/storyboard/state").json()
    blocked = next(row for row in state["episodes"] if row["episode_id"] == "ep-02")
    assert blocked["status"] == "BLOCKED"

    retry = client.post(
        f"/api/v1/projects/{project}/director/storyboard/episodes/ep-02/retry",
        json={"expected_state_version": state["state_version"]}, headers=LOCAL)
    assert retry.status_code == 202, retry.text

    # The responder no longer fails the section: the run finishes.
    _run_storyboard_to_rest(client, settings, project)
    state = client.get(f"/api/v1/projects/{project}/director/storyboard/state").json()
    assert state["status"] == "SUCCEEDED", state
    assert all(row["status"] == "APPLIED" for row in state["episodes"])


def test_a_structured_landing_refusal_is_named_not_HTTPException(api, monkeypatch):
    """A gate that refuses with ``{"code": ...}`` must not surface as "HTTPException".

    Live evidence (2026-11-15, scene 2): all five crew roles succeeded, then the
    landing gate refused with a structured detail.  The run reported
    ``error_code=HTTPException`` and the episode kept a blocker with an empty
    rejection list, so the panel showed a blocked scene with no reason and no
    named next step - the exact "状态未知" shape this work must not produce.
    """
    from fastapi import HTTPException

    client, _factory, settings = api
    prepared = _prepare_project(client, sections=4)
    project = prepared["project"]
    book = _storyboard_book(client, project, episode_count=2, target_seconds=60)
    _confirm_book(client, project, book["plan_version"], book["plan_sha256"])
    client.post(f"/api/v1/projects/{project}/director/storyboard/generate", json={}, headers=LOCAL)

    def refuse(*_args, **_kwargs):
        raise HTTPException(409, {"code": "FILM_HANDOFF_REFUSED_BY_TEST",
                                  "message": "landing gate refused"})

    monkeypatch.setattr("shine.api_film.apply_camera", refuse)
    _run_storyboard_to_rest(client, settings, project)
    state = client.get(f"/api/v1/projects/{project}/director/storyboard/state").json()
    assert state["status"] == "NEEDS_REVIEW"
    assert state["error_code"] == "FILM_HANDOFF_REFUSED_BY_TEST"
    blocked = next(row for row in state["episodes"] if row["episode_id"] == "ep-01")
    assert blocked["status"] == "BLOCKED"
    assert blocked["blocker"]["rejections"][0]["code"] == "FILM_HANDOFF_REFUSED_BY_TEST"


def test_a_landing_fault_names_itself_and_retries_without_the_chain_again(api, monkeypatch):
    """A landing gate refusal is this unit's reason, not a crew refusal to replay.

    Live evidence (2026-11-15, scene 2): all five crew roles succeeded and the
    landing gate refused.  The run kept the *previous* attempt's crew refusal as
    ``failed_task_id``; its retry then hit the replay gate
    (STORYBOARD_UNKNOWN_REQUIRES_RECONCILIATION) against a task the retry itself
    had superseded, so the unit could never advance.  The landing fault has to
    name itself and the retry has to reuse the five finished artifacts - no
    model call and no replay authorisation is involved in re-landing.
    """
    from fastapi import HTTPException

    import shine.api_film as api_film

    client, _factory, settings = api
    prepared = _prepare_project(client, sections=4)
    project = prepared["project"]
    book = _storyboard_book(client, project, episode_count=2, target_seconds=60)
    _confirm_book(client, project, book["plan_version"], book["plan_sha256"])
    client.post(f"/api/v1/projects/{project}/director/storyboard/generate", json={}, headers=LOCAL)

    def refuse(*_args, **_kwargs):
        raise HTTPException(409, {"code": "FILM_HANDOFF_REFUSED_BY_TEST",
                                  "message": "landing gate refused"})

    real_apply = api_film.apply_camera
    monkeypatch.setattr("shine.api_film.apply_camera", refuse)
    _run_storyboard_to_rest(client, settings, project)
    state = client.get(f"/api/v1/projects/{project}/director/storyboard/state").json()
    assert state["error_code"] == "FILM_HANDOFF_REFUSED_BY_TEST"
    blocked = next(row for row in state["episodes"] if row["episode_id"] == "ep-01")
    assert blocked["blocker"]["rejections"][0]["code"] == "FILM_HANDOFF_REFUSED_BY_TEST"
    with _factory() as db:
        run = db.get(AgentTaskRecord, state["task_id"])
        assert run.trace.get("failed_task_id") is None, (
            "a landing fault must not leave a crew refusal as the retry's reason")
        assert set(run.trace.get("resume_steps") or {}) >= {
            "series_architecture", "drama_scene", "scene_board"}, (
            "the finished board chain must travel with the recovery state")

    monkeypatch.setattr("shine.api_film.apply_camera", real_apply)
    retry = client.post(
        f"/api/v1/projects/{project}/director/storyboard/episodes/ep-01/retry",
        json={"expected_state_version": state["state_version"]}, headers=LOCAL)
    assert retry.status_code == 202, retry.text

    calls: list[str] = []
    base = _chain_responder({"asset": next(
        a["id"] for a in client.get(f"/api/v1/projects/{project}/assets").json()
        if a["role"] == "character" and a["entity_id"])})

    def counted(messages, schema):
        calls.append(schema.get("title"))
        return base(messages, schema)

    factory = client.app.state.session_factory
    gateway = LLMGateway(SecretStore(settings.secret_store_dir), mock_responder=counted)
    assert streamline.run_pending_director_storyboard(
        factory, settings, "worker-test", gateway=gateway)
    assert calls == [], "re-landing must reuse the five finished crew artifacts"
    state = client.get(f"/api/v1/projects/{project}/director/storyboard/state").json()
    first_unit = next(row for row in state["episodes"] if row["episode_id"] == "ep-01")["units"][0]
    assert first_unit["state"] == "APPLIED", state
    # The rest of the episode continues normally after the recovered landing.
    _run_storyboard_to_rest(client, settings, project)
    state = client.get(f"/api/v1/projects/{project}/director/storyboard/state").json()
    assert state["status"] == "SUCCEEDED", state
    assert all(row["status"] == "APPLIED" for row in state["episodes"])


def test_a_superseded_failure_pointer_does_not_gate_the_retry(api, monkeypatch):
    """A pointer to a request an earlier retry replaced is history, not a refusal.

    Live evidence (2026-11-15, scene 2): after the landing gate refused, the run
    still carried the *previous* attempt's director refusal.  That record had
    already been superseded by the retry's own new call, so the replay gate read
    a record that no longer represented an open refusal and refused every retry
    with STORYBOARD_UNKNOWN_REQUIRES_RECONCILIATION - a dead end.
    """
    from fastapi import HTTPException

    import shine.api_film as api_film

    client, factory, settings = api
    prepared = _prepare_project(client, sections=4)
    project = prepared["project"]
    book = _storyboard_book(client, project, episode_count=2, target_seconds=60)
    _confirm_book(client, project, book["plan_version"], book["plan_sha256"])
    client.post(f"/api/v1/projects/{project}/director/storyboard/generate", json={}, headers=LOCAL)

    def refuse(*_args, **_kwargs):
        raise HTTPException(409, {"code": "FILM_HANDOFF_REFUSED_BY_TEST"})

    real_apply = api_film.apply_camera
    monkeypatch.setattr("shine.api_film.apply_camera", refuse)
    _run_storyboard_to_rest(client, settings, project)
    monkeypatch.setattr("shine.api_film.apply_camera", real_apply)
    state = client.get(f"/api/v1/projects/{project}/director/storyboard/state").json()

    # Rebuild the stranded shape the old code left: the pointer names a director
    # request that a later retry already replaced, and no landing facts exist.
    with factory.begin() as db:
        run = db.get(AgentTaskRecord, state["task_id"])
        predecessor = AgentTaskRecord(
            project_id=project, role_id="director", request_identity="predecessor",
            input_hash="0" * 64, asset_manifest_hash="0" * 64, rulepack_id="test",
            rulepack_hash="0" * 64, model_profile_id="test", model_id="test",
            status="SUPERSEDED",
            trace={"rejection_reason_code": "VISUAL_TEXT_MUST_BE_PROMPT_LANGUAGE",
                   "is_mock": False},
        )
        db.add(predecessor)
        db.flush()
        run.trace = {**run.trace, "failed_task_id": predecessor.id, "failed_role": "director",
                     "failed_unit_index": 0, "pending_landing": None, "resume_steps": {}}
        run.status, run.error_code = "NEEDS_REVIEW", "HTTPException"

    state = client.get(f"/api/v1/projects/{project}/director/storyboard/state").json()
    retry = client.post(
        f"/api/v1/projects/{project}/director/storyboard/episodes/ep-01/retry",
        json={"expected_state_version": state["state_version"]}, headers=LOCAL)
    assert retry.status_code == 202, retry.text
    _run_storyboard_to_rest(client, settings, project)
    state = client.get(f"/api/v1/projects/{project}/director/storyboard/state").json()
    assert state["status"] == "SUCCEEDED", state
    assert all(row["status"] == "APPLIED" for row in state["episodes"])


def test_legacy_unknown_refusal_recovery_path_names_and_resumes(api):
    """A failed record that says UNKNOWN stays behind the reconciliation gate:
    the direct episode retry refuses with a named code (never a silent
    nothing), and the person's decision — reconcile + resend-unknown —
    unlocks exactly one new call so the episode finishes.

    The 2026-11-15 journey hit this shape: a streamed answer cut at
    finish_reason=length was mis-classified UNKNOWN, so the panel had to
    offer this decision path instead of a dead "go check the panel" hint.
    """
    client, factory, settings = api
    prepared = _prepare_project(client, sections=4)
    project = prepared["project"]
    book = _storyboard_book(client, project, episode_count=2, target_seconds=60)
    _confirm_book(client, project, book["plan_version"], book["plan_sha256"])
    pending_section = prepared["outline"]["sections"][2]["production_section_id"]
    client.post(f"/api/v1/projects/{project}/director/storyboard/generate", json={}, headers=LOCAL)
    _run_storyboard_to_rest(client, settings, project, fail_section=pending_section)

    state = client.get(f"/api/v1/projects/{project}/director/storyboard/state").json()
    blocked = next(row for row in state["episodes"] if row["episode_id"] == "ep-02")
    assert blocked["status"] == "BLOCKED"
    rejection = blocked["blocker"]["rejections"][0]
    failed_task_id = rejection["task_id"]
    # Rewrite the failed record into the legacy mis-classified shape: UNKNOWN,
    # no received-result digest, no reconciliation decision yet.
    with factory.begin() as db:
        task = db.get(AgentTaskRecord, failed_task_id)
        assert task is not None
        task.status = "NEEDS_REVIEW"
        task.trace = {**task.trace, "external_outcome": "UNKNOWN",
                      "diagnostic_category": "provider_format",
                      "provider_finish_reason": "length", "provider_text_chars": 3140}
        task.trace.pop("received_result_sha256", None)
        task_version = task.state_version

    # 1) A blind retry cannot silently replay the unknown call: the run stays
    #    blocked, the failed episode keeps its blocked marker, and the refusal
    #    is named on the run — never a silent nothing.
    retry = client.post(
        f"/api/v1/projects/{project}/director/storyboard/episodes/ep-02/retry",
        json={"expected_state_version": state["state_version"]}, headers=LOCAL)
    assert retry.status_code == 202, retry.text
    _run_storyboard_to_rest(client, settings, project)
    state = client.get(f"/api/v1/projects/{project}/director/storyboard/state").json()
    assert state["status"] == "NEEDS_REVIEW"
    assert state["error_code"], "the refusal must be named, never status-unknown"
    still = next(row for row in state["episodes"] if row["episode_id"] == "ep-02")
    assert still["status"] == "BLOCKED", still
    assert still["blocker"] is not None, "the episode must keep its blocked marker"

    # 2) The person checks the unknown call, then explicitly authorises one
    #    new request — the decision path the panel must expose.
    checked = client.post(f"/api/v1/projects/{project}/film-runs/{failed_task_id}/reconcile",
                          json={"expected_state_version": task_version}, headers=LOCAL)
    assert checked.status_code == 200, checked.text
    assert checked.json()["outcome"] == "UNKNOWN_REQUIRES_NEW_ATTEMPT"
    resent = client.post(f"/api/v1/projects/{project}/film-runs/{failed_task_id}/resend-unknown",
                         json={"expected_state_version": checked.json()["state_version"]}, headers=LOCAL)
    assert resent.status_code == 200, resent.text
    assert resent.json()["outcome"] == "USER_AUTHORIZED_NEW_REQUEST"

    # 3) Now the episode retry is lawful and the run finishes.
    retry = client.post(
        f"/api/v1/projects/{project}/director/storyboard/episodes/ep-02/retry",
        json={"expected_state_version": state["state_version"]}, headers=LOCAL)
    assert retry.status_code == 202, retry.text
    _run_storyboard_to_rest(client, settings, project)
    state = client.get(f"/api/v1/projects/{project}/director/storyboard/state").json()
    assert state["status"] == "SUCCEEDED", state
    assert all(row["status"] == "APPLIED" for row in state["episodes"])


def test_blocked_state_names_the_failed_scene(api):
    """A blocked episode must tell the person which scene is stuck, not only
    that the episode failed: the state names each unit's script section."""
    client, factory, settings = api
    prepared = _prepare_project(client, sections=4)
    project = prepared["project"]
    book = _storyboard_book(client, project, episode_count=2, target_seconds=60)
    _confirm_book(client, project, book["plan_version"], book["plan_sha256"])
    pending_section = prepared["outline"]["sections"][2]["production_section_id"]
    client.post(f"/api/v1/projects/{project}/director/storyboard/generate", json={}, headers=LOCAL)
    _run_storyboard_to_rest(client, settings, project, fail_section=pending_section)

    state = client.get(f"/api/v1/projects/{project}/director/storyboard/state").json()
    blocked = next(row for row in state["episodes"] if row["episode_id"] == "ep-02")
    unit = next(u for u in blocked["units"] if u["index"] == blocked["blocker"]["unit_index"])
    assert unit["state"] == "BLOCKED"
    assert unit["section_order"] == 3
    assert unit["section_title"] == "第3场"
    # The applied episode names its scenes too, so the person sees the full map.
    applied = next(row for row in state["episodes"] if row["episode_id"] == "ep-01")
    assert applied["units"][0]["section_title"] == "第1场"


def test_a_retried_scene_is_told_what_was_refused_before(api):
    """A retry must carry the last named refusal into its first call.

    The 2026-11-15 journey found the hole: scene 2 was refused twice for
    Chinese visual prose, and each retry spent its first call re-buying the
    same refusal.  Worse, when that first call was truncated by an unrelated
    rule, the earlier lesson was dropped entirely and the retry answered the
    old mistake again.  The retry names the record it replaces, so that
    record's trace is the lesson the first call must already carry.
    """
    client, factory, settings = api
    prepared = _prepare_project(client, sections=4)
    project = prepared["project"]
    book = _storyboard_book(client, project, episode_count=2, target_seconds=60)
    _confirm_book(client, project, book["plan_version"], book["plan_sha256"])
    pending_section = prepared["outline"]["sections"][2]["production_section_id"]
    assets = client.get(f"/api/v1/projects/{project}/assets").json()
    master = next(a["id"] for a in assets if a["role"] == "character" and a["entity_id"])
    base = _chain_responder({"asset": master})

    def chinese_prose(messages, schema):
        plan = base(messages, schema)
        if schema.get("title") == "SceneShotBoard" and pending_section in "".join(
                message.content for message in messages):
            plan["shots"][0]["visual_description"] = "老周把怀表贴近耳朵，又放回丝绒垫上。"
        return plan

    client.post(f"/api/v1/projects/{project}/director/storyboard/generate", json={}, headers=LOCAL)
    _run_storyboard_to_rest(client, settings, project, responder=chinese_prose)
    state = client.get(f"/api/v1/projects/{project}/director/storyboard/state").json()
    blocked = next(row for row in state["episodes"] if row["episode_id"] == "ep-02")
    assert blocked["status"] == "BLOCKED"
    assert blocked["blocker"]["rejections"][0]["rejection_reason_code"] == (
        "VISUAL_TEXT_MUST_BE_PROMPT_LANGUAGE")

    calls: list[str] = []

    def healthy(messages, schema):
        calls.append(" ".join(message.content for message in messages
                              if message.role == "system"))
        return base(messages, schema)

    retry = client.post(
        f"/api/v1/projects/{project}/director/storyboard/episodes/ep-02/retry",
        json={"expected_state_version": state["state_version"]}, headers=LOCAL)
    assert retry.status_code == 202, retry.text
    _run_storyboard_to_rest(client, settings, project, responder=healthy)
    state = client.get(f"/api/v1/projects/{project}/director/storyboard/state").json()
    assert state["status"] == "SUCCEEDED", state
    assert calls, "the retry must actually call the model"
    assert "failed the visual language check" in calls[0], (
        "the first call of a retry must already carry the last refusal's lesson: "
        + calls[0][-400:])


def test_a_truncated_retry_does_not_drop_the_lesson_it_was_told(api, monkeypatch):
    """A retry's second call must still carry the lesson its first call earned.

    The live retry on 2026-11-15 exposed this: the retry started already
    knowing the visual-language refusal, its first call was truncated by an
    unrelated budget miss (LLM_FORMAT), and the second call overwrote the
    standing lesson with the truncation's own note.  The episode was then
    refused for the exact reason the retry had been told to repair.  Lessons
    accumulate inside one request; a later, unrelated failure never erases one.
    """
    from shine.llm import mock as mock_module
    from shine.llm.base import LLMFormatError

    client, factory, settings = api
    prepared = _prepare_project(client, sections=4)
    project = prepared["project"]
    book = _storyboard_book(client, project, episode_count=2, target_seconds=60)
    _confirm_book(client, project, book["plan_version"], book["plan_sha256"])
    pending_section = prepared["outline"]["sections"][2]["production_section_id"]
    assets = client.get(f"/api/v1/projects/{project}/assets").json()
    master = next(a["id"] for a in assets if a["role"] == "character" and a["entity_id"])
    base = _chain_responder({"asset": master})

    def chinese_prose(messages, schema):
        plan = base(messages, schema)
        if schema.get("title") == "SceneShotBoard" and pending_section in "".join(
                message.content for message in messages):
            plan["shots"][0]["visual_description"] = "老周把怀表贴近耳朵，又放回丝绒垫上。"
        return plan

    client.post(f"/api/v1/projects/{project}/director/storyboard/generate", json={}, headers=LOCAL)
    _run_storyboard_to_rest(client, settings, project, responder=chinese_prose)
    state = client.get(f"/api/v1/projects/{project}/director/storyboard/state").json()
    blocked = next(row for row in state["episodes"] if row["episode_id"] == "ep-02")
    assert blocked["status"] == "BLOCKED"
    assert blocked["blocker"]["rejections"][0]["rejection_reason_code"] == (
        "VISUAL_TEXT_MUST_BE_PROMPT_LANGUAGE")

    plan_calls: list[str] = []
    original = mock_module.MockLLMProvider.complete

    def truncated_first_answer(self, messages, *, schema=None, json_mode=True):
        system = " ".join(message.content for message in messages
                          if message.role == "system")
        if (schema or {}).get("title") == "SceneShotBoard" and pending_section in "".join(
                message.content for message in messages):
            plan_calls.append(system)
            if len(plan_calls) == 1:
                # A 200 answer that ended mid-object: the same LLM_FORMAT the
                # live truncated retry produced, and still a received result.
                raise LLMFormatError("Mock answer truncated", details={"http_status": 200})
        return original(self, messages, schema=schema, json_mode=json_mode)

    monkeypatch.setattr(mock_module.MockLLMProvider, "complete", truncated_first_answer)
    retry = client.post(
        f"/api/v1/projects/{project}/director/storyboard/episodes/ep-02/retry",
        json={"expected_state_version": state["state_version"]}, headers=LOCAL)
    assert retry.status_code == 202, retry.text
    _run_storyboard_to_rest(client, settings, project, responder=chinese_prose)
    assert len(plan_calls) >= 2, f"a bounded retry must spend its second call: {plan_calls}"
    assert "failed the visual language check" in plan_calls[1], (
        "the second call of a retry must still carry the refusal the retry was told to "
        "repair, even when the first call failed for another reason: " + plan_calls[1][-400:])


def test_shot_row_edit_changes_duration_with_revision_fence(api):
    client, factory, settings = api
    prepared = _prepare_project(client, sections=2)
    project = prepared["project"]
    book = _storyboard_book(client, project, episode_count=1, target_seconds=60)
    _confirm_book(client, project, book["plan_version"], book["plan_sha256"])
    client.post(f"/api/v1/projects/{project}/director/storyboard/generate", json={}, headers=LOCAL)
    _run_storyboard_to_rest(client, settings, project)

    table = client.get(f"/api/v1/projects/{project}/director/storyboard/episodes/ep-01/shots").json()
    shot = table["shots"][0]
    patched = client.patch(
        f"/api/v1/projects/{project}/director/storyboard/shots/{shot['shot_id']}",
        json={"expected_revision": shot["revision"], "duration_target_s": 9.5}, headers=LOCAL)
    assert patched.status_code == 200, patched.text
    assert patched.json()["revision"] == shot["revision"] + 1

    stale = client.patch(
        f"/api/v1/projects/{project}/director/storyboard/shots/{shot['shot_id']}",
        json={"expected_revision": shot["revision"], "duration_target_s": 8.0}, headers=LOCAL)
    assert stale.status_code == 409

    # The continuity relation is an editorial fact on the same row.
    flipped = client.patch(
        f"/api/v1/projects/{project}/director/storyboard/shots/{shot['shot_id']}",
        json={"expected_revision": patched.json()["revision"], "continuity_mode": "last_frame"},
        headers=LOCAL)
    assert flipped.status_code == 200, flipped.text
    table2 = client.get(f"/api/v1/projects/{project}/director/storyboard/episodes/ep-01/shots").json()
    edited = next(row for row in table2["shots"] if row["shot_id"] == shot["shot_id"])
    assert edited["continuity_mode"] == "last_frame"
    assert edited["duration_target_s"] == 9.5


# ---------------------------------------------------------------------------
# ④ shot-driven material list
# ---------------------------------------------------------------------------


def test_derive_materials_matches_masters_and_marks_usage(api):
    client, factory, settings = api
    prepared = _prepare_project(client, sections=2)
    project = prepared["project"]
    book = _storyboard_book(client, project, episode_count=1, target_seconds=60)
    _confirm_book(client, project, book["plan_version"], book["plan_sha256"])
    client.post(f"/api/v1/projects/{project}/director/storyboard/generate", json={}, headers=LOCAL)
    _run_storyboard_to_rest(client, settings, project)
    client.post(f"/api/v1/projects/{project}/director/storyboard/episodes/ep-01/confirm",
                json={"acknowledge_duration_deviation": True}, headers=LOCAL)

    derived = client.post(f"/api/v1/projects/{project}/director/materials/derive",
                          json={"episode_ids": ["ep-01"]}, headers=LOCAL)
    assert derived.status_code == 200, derived.text
    body = derived.json()
    assert body["requirements"], "the confirmed shots ask for their cast"
    character = next(row for row in body["requirements"] if row["family"] == "character")
    assert character["status"] == "matched"
    assert character["matched_master"]["asset_id"]
    assert character["used_shot_ids"], "the row names the shots that use it"
    assert body["replayed"] is False
    # Deriving again reuses the plan instead of stacking revisions.
    twice = client.post(f"/api/v1/projects/{project}/director/materials/derive",
                        json={"episode_ids": ["ep-01"]}, headers=LOCAL)
    assert twice.json()["plan_revision_id"] == body["plan_revision_id"]
    assert twice.json()["replayed"] is True


# ---------------------------------------------------------------------------
# ⑤ episode-scoped package
# ---------------------------------------------------------------------------


def _confirmed_scoped_package(client, factory, project: str, profile: dict,
                              episode_ids: list[str]) -> dict:
    built = client.post(
        f"/api/v1/projects/{project}/director-packages",
        json={"workflow_profile_id": profile["id"], "generation_mode": "Ref2VA",
              "episode_ids": episode_ids},
        headers=LOCAL)
    assert built.status_code == 201, built.text
    confirmed = client.post(f"/api/v1/director-packages/{built.json()['id']}/confirm",
                            json={}, headers=LOCAL)
    assert confirmed.status_code == 200, confirmed.text
    return built.json()


def test_episode_scoped_package_freezes_only_ready_episodes(api):
    client, factory, settings = api
    from tests.conftest import register_workflow_profile

    prepared = _prepare_project(client, sections=4)
    project = prepared["project"]
    book = _storyboard_book(client, project, episode_count=2, target_seconds=60)
    _confirm_book(client, project, book["plan_version"], book["plan_sha256"])
    pending_section = prepared["outline"]["sections"][2]["production_section_id"]
    client.post(f"/api/v1/projects/{project}/director/storyboard/generate", json={}, headers=LOCAL)
    _run_storyboard_to_rest(client, settings, project, fail_section=pending_section)
    confirmed_ep = client.post(
        f"/api/v1/projects/{project}/director/storyboard/episodes/ep-01/confirm",
        json={"acknowledge_duration_deviation": True}, headers=LOCAL)
    assert confirmed_ep.status_code == 200, confirmed_ep.text

    profile = register_workflow_profile(client, mode="Ref2VA")
    package = _confirmed_scoped_package(client, factory, project, profile, ["ep-01"])
    assert package["episode_count"] == 1
    assert package["episode_plan"].get("selected_episode_ids") == ["ep-01"]

    # The whole-plan build still refuses while an episode is unmaterialised.
    whole = client.post(
        f"/api/v1/projects/{project}/director-packages",
        json={"workflow_profile_id": profile["id"], "generation_mode": "Ref2VA"},
        headers=LOCAL)
    assert whole.status_code == 409
    assert whole.json()["detail"]["code"] == "EPISODE_PLAN_NOT_MATERIALIZED"

    # An unknown episode scope is a named refusal.
    unknown = client.post(
        f"/api/v1/projects/{project}/director-packages",
        json={"workflow_profile_id": profile["id"], "episode_ids": ["ep-99"]},
        headers=LOCAL)
    assert unknown.status_code in (404, 409, 422)


def test_journey_reports_per_episode_storyboard_progress(api):
    client, factory, settings = api
    prepared = _prepare_project(client, sections=4)
    project = prepared["project"]
    book = _storyboard_book(client, project, episode_count=2, target_seconds=60)
    _confirm_book(client, project, book["plan_version"], book["plan_sha256"])
    pending_section = prepared["outline"]["sections"][2]["production_section_id"]
    client.post(f"/api/v1/projects/{project}/director/storyboard/generate", json={}, headers=LOCAL)
    _run_storyboard_to_rest(client, settings, project, fail_section=pending_section)
    client.post(f"/api/v1/projects/{project}/director/storyboard/episodes/ep-01/confirm",
                json={"acknowledge_duration_deviation": True}, headers=LOCAL)

    journey = client.get(f"/api/v1/projects/{project}/journey").json()
    stage = next(row for row in journey["stages"] if row["id"] == "storyboard")
    facts = stage["facts"]
    assert facts["episode_total"] == 2
    assert facts["episode_confirmed_count"] == 1
    assert facts["ready_episode_ids"] == ["ep-01"]
    assert "1/2 集分镜已确认" in stage["summary"]


def test_journey_follows_the_current_plan_beside_an_older_scoped_package(api):
    """A package frozen for ep-01 must not keep the journey on its old plan.

    The panel and the auto-confirm write landed shots into the newest confirmed
    plan version; the journey's storyboard gate used to re-judge the plan the
    package pinned, so a later episode that landed and auto-confirmed still left
    the stage blocked with EPISODE_PLAN_NOT_MATERIALIZED.
    """
    client, factory, settings = api
    from tests.conftest import register_workflow_profile

    prepared = _prepare_project(client, sections=4)
    project = prepared["project"]
    book = _storyboard_book(client, project, episode_count=2, target_seconds=60)
    _confirm_book(client, project, book["plan_version"], book["plan_sha256"])
    pending_section = prepared["outline"]["sections"][2]["production_section_id"]
    client.post(f"/api/v1/projects/{project}/director/storyboard/generate", json={}, headers=LOCAL)
    _run_storyboard_to_rest(client, settings, project, fail_section=pending_section)

    profile = register_workflow_profile(client, mode="Ref2VA")
    package = _confirmed_scoped_package(client, factory, project, profile, ["ep-01"])

    # The package covers ep-01. The project journey reports the remaining
    # storyboard work across the current plan while ep-01 stays ready.
    journey = client.get(f"/api/v1/projects/{project}/journey").json()
    stage = next(row for row in journey["stages"] if row["id"] == "storyboard")
    pending = next(b for b in stage["blockers"] if b["code"] == "EPISODE_PLAN_NOT_MATERIALIZED")
    assert pending["pending_section_count"] > 0
    assert stage["done"] is False
    assert stage["facts"]["ready_episode_ids"] == ["ep-01"]

    # Retry the blocked episode; landing auto-confirms it into a new plan version.
    state = client.get(f"/api/v1/projects/{project}/director/storyboard/state").json()
    retried = client.post(
        f"/api/v1/projects/{project}/director/storyboard/episodes/ep-02/retry",
        json={"expected_state_version": state["state_version"]}, headers=LOCAL)
    assert retried.status_code == 202, retried.text
    _run_storyboard_to_rest(client, settings, project)

    plans = client.get(f"/api/v1/projects/{project}/episode-plans").json()
    confirmed = next(row for row in plans if row["status"] == "CONFIRMED")
    assert confirmed["version"] > package["episode_plan"]["version"]
    assert all(ep["shot_ids"] for ep in confirmed["payload"]["episodes"])

    journey = client.get(f"/api/v1/projects/{project}/journey").json()
    stage = next(row for row in journey["stages"] if row["id"] == "storyboard")
    assert not any(b["code"] == "EPISODE_PLAN_NOT_MATERIALIZED" for b in stage["blockers"])
    assert stage["done"] is True, stage["blockers"]
    assert stage["facts"]["episode_confirmed_count"] == 2
    assert stage["facts"]["storyboard_plan_version"] == confirmed["version"]


# ---------------------------------------------------------------------------
# crew salvage and budget
# ---------------------------------------------------------------------------


def test_crew_salvage_keeps_usable_content_and_refuses_missing_content():
    from shine.agents.crew_salvage import parse_with_salvage
    from shine.agents.film_contracts import ScriptBreakdown

    noisy = {
        "beats": [{"id": "B1", "description": "雨夜。", "characters": ["老周"],
                   "source_note": "trust me",
                   "dialogue": [{"speaker": "老周", "line": "来了。", "start_s": 0.5}]}],
    }
    proposal, notes = parse_with_salvage(noisy, ScriptBreakdown)
    assert proposal.beats[0].dialogue[0].speaker_id == "老周"
    assert any("aliased_to_speaker_id" in note for note in notes)
    assert any("unknown_field_dropped" in note for note in notes)

    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        parse_with_salvage({"beats": [{"id": "B1"}]}, ScriptBreakdown)


def test_crew_salvage_promotes_misnested_shot_siblings_from_camera():
    from shine.agents.crew_salvage import parse_with_salvage
    from shine.agents.scene_director import SceneShotBoard

    shot_base = {
        "id": "S01",
        "beat_ids": ["B1"],
        "duration_s": 6.0,
        "visual_description": "Lao Zhou oils a brass pocket watch behind the counter.",
        "asset_ids": [],
        "location": "周记钟表铺",
        "dialogue": [],
        "camera": {
            "composition": "Medium shot behind the wooden counter, subject centered.",
            "shot_size": "MS",
            "angle": "eye-level",
            "lens": "50mm",
            "move": "static",
            "motive": "",
            "movement": "The camera holds still.",
            "lighting": "Warm desk lamp key.",
            "blocking": {"axis": "group_1", "placements": [
                {"character": "老周", "zone": "left_fg", "facing": "facing_right", "posture": "seated", "props": []}
            ]},
            "environment": {"depth": "Counter foreground, clock wall behind.", "light": "Warm desk lamp.",
                            "micro_motion": "Dust drifts in the lamplight."},
            "timeline": [{"start_s": 0.0, "end_s": 6.0, "action": "He oils the brass gears.",
                          "expression": "Steady focus.", "camera": "The camera holds still."}],
            "entry_state": "Lao Zhou seated at the counter.",
            "exit_state": "Lao Zhou lowers the loupe.",
            "transition_motivation": "match_on_action",
            "handoff_cue": "rain on the glass door draws his glance",
            "ambience": "steady clock ticking and rain",
            "music": "none",
        },
        "camera_note": "extra note to drop",
    }
    proposal, notes = parse_with_salvage(
        {"shots": [shot_base]},
        SceneShotBoard,
    )
    assert proposal.shots[0].blocking.axis == "group_1"
    assert proposal.shots[0].timeline[0].action == "He oils the brass gears."
    assert any("blocking:promoted_to_parent" in n for n in notes)
    assert any("camera_note:unknown_field_dropped" in n for n in notes)

    shot_with_root = {
        **shot_base,
        "prompt_style": {
            "photography_language": "Eye-level medium shots",
            "shot_blocking": "Keep counter axis",
            "color_palette": "Warm amber",
            "lighting_tone": "Desk lamp key",
            "lens_texture": "50mm film texture",
            "editing_rhythm": "Unhurried beats",
            "sound_performance": "Natural dialogue",
        },
        "prompt_story_facts": [],
    }
    proposal2, notes2 = parse_with_salvage(
        {"shots": [shot_with_root, None]},
        SceneShotBoard,
    )
    assert len(proposal2.shots) == 1
    assert proposal2.prompt_style is not None
    assert proposal2.prompt_style.color_palette == "Warm amber"
    assert any("prompt_style:promoted_to_parent" in n for n in notes2)
    assert any("empty_non_object_dropped" in n for n in notes2)

    # Lossless trim when a shot repeats internal_cuts[0].beat_ids at the tail of shot.beat_ids
    cut_overlap_shot = {
        **shot_base,
        "beat_ids": ["B1", "B2"],
        "camera": {
            "composition": "Medium shot behind the wooden counter, subject centered.",
            "shot_size": "MS", "angle": "eye-level", "lens": "50mm",
            "move": "static", "motive": "", "movement": "The camera holds still.",
            "lighting": "Warm desk lamp key.",
        },
        "blocking": shot_base["camera"]["blocking"],
        "environment": shot_base["camera"]["environment"],
        "timeline": shot_base["camera"]["timeline"],
        "entry_state": shot_base["camera"]["entry_state"],
        "exit_state": shot_base["camera"]["exit_state"],
        "transition_motivation": "match_on_action",
        "handoff_cue": "cut on hand motion",
        "ambience": "steady clock ticking and rain",
        "music": "none",
        "internal_cuts": [{"start_s": 3.0, "beat_ids": ["B2"], "visual_description": "Close-up of the watch."}],
    }
    proposal3, notes3 = parse_with_salvage({"shots": [cut_overlap_shot]}, SceneShotBoard)
    assert proposal3.shots[0].beat_ids == ["B1"]
    assert proposal3.shots[0].internal_cuts[0].beat_ids == ["B2"]
    assert any("beat_ids:deduped_internal_cut_beats" in n for n in notes3)

    # Preserve B1 duplicate if trimming would leave shot.beat_ids empty (still fails coverage gate).
    only_cut_beat = {**cut_overlap_shot, "beat_ids": ["B2"]}
    proposal4, notes4 = parse_with_salvage({"shots": [only_cut_beat]}, SceneShotBoard)
    assert proposal4.shots[0].beat_ids == ["B2"]
    assert not any("deduped_internal_cut_beats" in n for n in notes4)


def test_crew_chain_records_the_literary_budget(api):
    client, factory, settings = api
    prepared = _prepare_project(client, sections=2)
    project = prepared["project"]
    book = _storyboard_book(client, project, episode_count=1, target_seconds=60)
    _confirm_book(client, project, book["plan_version"], book["plan_sha256"])
    client.post(f"/api/v1/projects/{project}/director/storyboard/generate", json={}, headers=LOCAL)
    _run_storyboard_to_rest(client, settings, project)

    with factory() as db:
        from sqlalchemy import select as sa_select

        rows = db.scalars(sa_select(AgentTaskRecord).where(
            AgentTaskRecord.project_id == project,
            AgentTaskRecord.role_id.in_(["scriptwriter", "director", "cinematographer",
                                         "continuity_supervisor", "editor"]),
            AgentTaskRecord.status == "SUCCEEDED")).all()
        budgeted = [row for row in rows if (row.trace or {}).get("call_budget")]
        assert budgeted, "every crew role records its approved call budget"
        sample = budgeted[0].trace["call_budget"]
        assert sample["output_request_tokens"] > 0
        assert "window_source" in sample


def test_storyboard_book_preview_reports_covered_scope_and_remaining_episodes(api):
    client, _factory, _settings = api
    prepared = _prepare_project(client, sections=6, section_seconds=30.0)
    project = prepared["project"]

    response = client.post(
        f"/api/v1/projects/{project}/director/storyboard-book",
        json={"episode_count": 1, "target_seconds": 60, "tolerance_seconds": 15,
              "max_shot_seconds": 10},
        headers=LOCAL,
    )
    assert response.status_code == 201, response.text
    book = response.json()
    preview = book["preview"]
    assert preview["episode_count"] == 1
    assert preview["max_shot_seconds"] == 10.0
    covered = preview["covered_scope"]
    assert covered["from_order"] == 1 and covered["to_order"] == 2
    assert covered["covered_section_count"] == 2 and covered["total_section_count"] == 6
    assert covered["coverage_percent"] == 33
    out = preview["out_of_scope"]
    assert out["count"] == 4
    assert out["from_order"] == 3 and out["to_order"] == 6
    assert out["remaining_seconds"] == 120.0
    assert out["estimated_remaining_episodes"] == 2
    assert out["estimated_total_episodes"] == 3
    assert "第 1 场" in covered["note"] and "第 2 场" in covered["note"]
    assert "还需约 2 集" in out["note"]

    # GET returns the exact persisted latest plan preview on reload.
    fetched = client.get(f"/api/v1/projects/{project}/director/storyboard-book")
    assert fetched.status_code == 200, fetched.text
    assert fetched.json()["plan_version"] == book["plan_version"]
    assert fetched.json()["preview"]["out_of_scope"]["estimated_remaining_episodes"] == 2


def test_storyboard_book_confirm_freezes_customized_seven_dimensions(api):
    from shine.models import Project, StyleVersion

    client, factory, _settings = api
    prepared = _prepare_project(client, sections=2, section_seconds=30.0)
    project = prepared["project"]
    book = _storyboard_book(client, project, episode_count=1, target_seconds=60)

    custom_dims = {
        "photography_language": "固定远景，水平稳定推进。",
        "shot_blocking": "双人对角站位，严守轴线。",
        "color_palette": "低饱和青灰配一点暖灯。",
        "lighting_tone": "低调侧逆光，暗部有层次。",
        "lens_texture": "50mm 胶片柔焦与细腻颗粒。",
        "editing_rhythm": "克制节奏，保留停顿呼吸。",
        "sound_performance": "真实环境雨声与克制普通话对白。",
    }
    confirmed = client.post(
        f"/api/v1/projects/{project}/director/storyboard-book/confirm",
        json={
            "plan_version": book["plan_version"],
            "expected_sha256": book["plan_sha256"],
            "style": {
                "profile_id": "wong-kar-wai",
                "strength": "strong",
                "film_type": "animation",
                "dialogue_ratio_percent": 70,
                "narration_level": "low",
                "max_shot_seconds": 10,
                "dimensions": custom_dims,
            },
        },
        headers=LOCAL,
    )
    assert confirmed.status_code == 200, confirmed.text
    with factory() as db:
        proj = db.get(Project, project)
        frozen = db.get(StyleVersion, proj.frozen_style_version_id)
        assert frozen is not None
        for key, value in custom_dims.items():
            assert frozen.snapshot[key] == value
        assert frozen.snapshot["film_type"] == "animation"
        assert frozen.snapshot["max_shot_seconds"] == 10
        assert frozen.director_style_binding["strength"] == "strong"


def test_storyboard_book_exceeding_sections_returns_chinese_guidance(api):
    client, _factory, _settings = api
    prepared = _prepare_project(client, sections=2, section_seconds=30.0)
    project = prepared["project"]
    refused = client.post(
        f"/api/v1/projects/{project}/director/storyboard-book",
        json={"episode_count": 5, "target_seconds": 60},
        headers=LOCAL,
    )
    assert refused.status_code == 422
    detail = refused.json()["detail"]
    assert detail["code"] == "EPISODE_COUNT_EXCEEDS_SECTIONS"
    assert "共 2 个场次" in detail["message"]


def _mark_imported_script_units(factory, project: str) -> None:
    """Attach a confirmed scene script to the queued run's units so legacy crew tests exercise dramatize=False."""
    from sqlalchemy import select as sa_select

    from shine.h3.rulebook import digest as h3_digest
    from shine.models import AgentArtifact

    with factory.begin() as db:
        run = db.scalars(sa_select(AgentTaskRecord).where(
            AgentTaskRecord.project_id == project,
            AgentTaskRecord.role_id == streamline.DIRECTOR_STORYBOARD_RUN,
        )).first()
        script_payload = {"section_id": "sec-01", "text": "老周把怀表贴近耳朵。"}
        script_art = AgentArtifact(
            project_id=project, kind="FourStageSceneScript", payload_json=script_payload,
            sha256=h3_digest(script_payload), producer_role="scriptwriter",
            source_task_id=run.id, approved_by="local-user",
        )
        db.add(script_art)
        db.flush()
        units = [{**u, "creative_source": "confirmed_scene_script",
                  "script_artifact_id": script_art.id, "script_sha256": script_art.sha256}
                 for u in run.trace["units"]]
        run.trace = {**run.trace, "units": units}


def test_unknown_continuity_resend_preserves_failed_role_and_reuses_succeeded_lane(api):
    """Regression for live 2026-11-22: after reconcile + resend-unknown marks the
    failed continuity task SUPERSEDED, episode retry must resume at continuity_supervisor
    (never restarting from scriptwriter) and reuse the already-succeeded sub-lane."""
    import json

    from shine.llm.base import LLMConnectionError

    client, factory, settings = api
    prepared = _prepare_project(client, sections=1)
    project = prepared["project"]
    book = _storyboard_book(client, project, episode_count=1, target_seconds=60)
    _confirm_book(client, project, book["plan_version"], book["plan_sha256"])
    client.post(f"/api/v1/projects/{project}/director/storyboard/generate", json={}, headers=LOCAL)
    _mark_imported_script_units(factory, project)

    assets = client.get(f"/api/v1/projects/{project}/assets").json()
    master = next(a["id"] for a in assets if a["role"] == "character" and a["entity_id"])
    base = _chain_responder({"asset": master})

    def fail_physical(messages, schema):
        title = schema.get("title")
        if title == "ContinuityReport":
            ctx = json.JSONDecoder().raw_decode(messages[1].content)[0]
            if "continuity_policy" not in ctx:
                raise LLMConnectionError(
                    "Provider stream broke",
                    details={"external_outcome": "UNKNOWN", "streamed": True,
                             "first_event_ms": 4000, "last_event_ms": 13000},
                )
        return base(messages, schema)

    _run_storyboard_to_rest(client, settings, project, responder=fail_physical)
    state = client.get(f"/api/v1/projects/{project}/director/storyboard/state").json()
    assert state["status"] == "NEEDS_REVIEW"
    blocked = state["episodes"][0]
    assert blocked["status"] == "BLOCKED"
    failed_task_id = blocked["blocker"]["reconcile_task_id"]
    task_version = blocked["blocker"]["reconcile_state_version"]
    assert failed_task_id

    # Pretend the task came from a non-mock provider so resend-unknown runs the exact
    # production reconciliation path that sets status = SUPERSEDED.
    with factory.begin() as db:
        row = db.get(AgentTaskRecord, failed_task_id)
        row.trace = {**row.trace, "is_mock": False}

    checked = client.post(
        f"/api/v1/projects/{project}/film-runs/{failed_task_id}/reconcile",
        json={"expected_state_version": task_version}, headers=LOCAL)
    assert checked.status_code == 200, checked.text
    assert checked.json()["outcome"] == "UNKNOWN_REQUIRES_NEW_ATTEMPT"
    resent = client.post(
        f"/api/v1/projects/{project}/film-runs/{failed_task_id}/resend-unknown",
        json={"expected_state_version": checked.json()["state_version"]}, headers=LOCAL)
    assert resent.status_code == 200, resent.text
    assert resent.json()["outcome"] == "USER_AUTHORIZED_NEW_REQUEST"

    retry = client.post(
        f"/api/v1/projects/{project}/director/storyboard/episodes/ep-01/retry",
        json={"expected_state_version": state["state_version"]}, headers=LOCAL)
    assert retry.status_code == 202, retry.text

    resumed_calls: list[str] = []

    def tracking_responder(messages, schema):
        resumed_calls.append(schema.get("title") or "")
        return base(messages, schema)

    _run_storyboard_to_rest(client, settings, project, responder=tracking_responder)
    final_state = client.get(f"/api/v1/projects/{project}/director/storyboard/state").json()
    assert final_state["status"] == "SUCCEEDED", final_state
    assert final_state["episodes"][0]["status"] == "APPLIED"
    # Only the failed continuity_physical lane and downstream EditIntentPlan run;
    # ScriptBreakdown, DirectorialPlan, CameraPlan, and continuity_timing are reused.
    assert resumed_calls == ["ContinuityReport", "EditIntentPlan"]


def test_headingless_multiprose_splits_into_paragraph_sections_for_episodes(api):
    from shine.materialprep.story import source_sections
    from tests.test_film_crew import prepare

    client, factory, _settings = api
    project_id, _style, _profile = prepare(
        client, freeze=False, character_entity_id="老周", character_filename="老周.png")
    story_text = (
        "《雨夜钟表》\n"
        "（根据民间故事旧抄本原篇整理改写）\n\n"
        "第一天夜里，老周在灯下擦拭着那块停摆已久的怀表，窗外大雨倾盆而下，街角几乎没有行人。\n\n"
        "第二天清晨，一位穿深蓝风衣的年轻女子走进钟表铺，轻声询问昨天留下的那枚铜钥匙是否还在。\n\n"
        "第三天黄昏，老周沿着女子留下的地址找到城南旧巷，在斑驳木门后发现了一整墙走时精准的挂钟。\n\n"
        "（完）\n"
    )
    doc = _document(client, project_id, "雨夜钟表.txt", story_text)
    sections = source_sections(project_id, type("Doc", (), {"text": story_text})())
    assert len(sections) == 3
    assert sections[0]["start_offset"] == 0
    assert sections[-1]["end_offset"] == len(story_text)
    assert "".join(story_text[s["start_offset"]:s["end_offset"]] for s in sections) == story_text
    assert "《雨夜钟表》" in sections[0]["title"]
    assert "（根据民间故事旧抄本原篇整理改写）" in story_text[sections[0]["start_offset"]:sections[0]["end_offset"]]
    assert "（完）" in story_text[sections[-1]["start_offset"]:sections[-1]["end_offset"]]

    _approved_outline(factory, project_id, doc, sections)
    book = _storyboard_book(client, project_id, episode_count=3, target_seconds=12, tolerance_seconds=6)
    assert book["preview"]["episode_count"] == 3


def test_fault_code_and_curly_quoted_visible_text_under_h3_english():
    from fastapi import HTTPException

    from shine.director.streamline import _fault_code
    from shine.h3.spec_review import BASE_SECTION_ORDER, QUOTED_OR_DIALOGUE, review_h3_prompt

    report_exc = HTTPException(
        status_code=422,
        detail={"violations": [{"rule_id": "H3_ENGLISH", "evidence": "CJK found"}]},
    )
    assert _fault_code(report_exc) == "H3_ENGLISH"

    text = "The wooden plaque reads \u201c\u5170\u82e5\u5bfa\u201d in weathered gilt calligraphy."
    assert not any("\u4e00" <= ch <= "\u9fff" for ch in QUOTED_OR_DIALOGUE.sub("", text))
    findings = review_h3_prompt(
        text,
        {key: "Clean ambient acoustics." for key in BASE_SECTION_ORDER},
        mode="I2VA",
    )
    assert not any(item["code"] == "H3_ENGLISH" for item in findings)


def test_continuity_conflict_reports_reason_and_retries_director_with_lesson(api):
    client, factory, settings = api
    prepared = _prepare_project(client, sections=1)
    project = prepared["project"]
    book = _storyboard_book(client, project, episode_count=1, target_seconds=60)
    _confirm_book(client, project, book["plan_version"], book["plan_sha256"])
    client.post(f"/api/v1/projects/{project}/director/storyboard/generate", json={}, headers=LOCAL)
    _mark_imported_script_units(factory, project)

    assets = client.get(f"/api/v1/projects/{project}/assets").json()
    master = next(a["id"] for a in assets if a["role"] == "character" and a["entity_id"])
    base = _chain_responder({"asset": master})

    conflict_issue = "[shot-1] 人物把碗放上桌面后下一句又凭空端着碗。"
    first_round = True
    retry_director_prompts: list[str] = []
    retry_titles: list[str] = []

    def responder(messages, schema):
        title = schema.get("title") or ""
        if not first_round:
            retry_titles.append(title)
            if title == "DirectorialPlan":
                retry_director_prompts.append(messages[0].content)
        if first_round and title == "ContinuityReport":
            return {"issues": [conflict_issue], "recommendation": "needs_review"}
        if first_round and title == "ContinuityArbitration":
            return {"rulings": [{"issue_index": 0, "issue": conflict_issue,
                                 "verdict": "conflict", "rule_id": "H3_KEYFRAMES",
                                 "rationale": "Prop handoff contradiction."}],
                    "recommendation": "needs_review"}
        return base(messages, schema)

    _run_storyboard_to_rest(client, settings, project, responder=responder)
    blocked = client.get(f"/api/v1/projects/{project}/director/storyboard/state").json()
    assert blocked["status"] == "NEEDS_REVIEW"
    blocker = blocked["episodes"][0]["blocker"]
    assert blocker["rejections"][0]["code"] == "CONTINUITY_CONFLICT"
    assert "连续性矛盾需要重排" in blocker["summary"]
    assert conflict_issue in blocker["summary"]

    first_round = False
    retry = client.post(
        f"/api/v1/projects/{project}/director/storyboard/episodes/ep-01/retry",
        json={"expected_state_version": blocked["state_version"]}, headers=LOCAL)
    assert retry.status_code == 202, retry.text
    _run_storyboard_to_rest(client, settings, project, responder=responder)

    final_state = client.get(f"/api/v1/projects/{project}/director/storyboard/state").json()
    assert final_state["status"] == "SUCCEEDED"
    assert final_state["episodes"][0]["status"] == "APPLIED"
    # ScriptBreakdown is preserved; retry begins directly from director with the conflict lesson.
    assert "ScriptBreakdown" not in retry_titles
    assert retry_director_prompts and conflict_issue in retry_director_prompts[0]


def test_structured_front_matter_merges_into_first_scene_and_excludes_setup_from_duration(api):
    from shine.materialprep.source_spans import is_narrative_fragment, narrative_body
    from shine.materialprep.story import source_sections
    from tests.test_film_crew import prepare

    client, factory, _settings = api
    project_id, _style, _profile = prepare(
        client, freeze=False, character_entity_id="艾德里安", character_filename="艾德里安.png"
    )
    story_text = (
        "《灰烬圣剑：古堡守望》\n\n"
        "【人物设定】\n"
        "1. 艾德里安：三十五岁欧洲中世纪男骑士，灰蓝双眼，左颧骨旧剑疤，身着雕花钢制胸甲与深墨绿色连帽斗篷。\n\n"
        "【关键道具】\n"
        "1. 符文长剑：刻有冰蓝色魔纹的中世纪双手长剑。\n\n"
        "【核心场景】\n"
        "1. 哥特石砌古堡庭院：暴雨夜晚的灰色花岗岩尖拱庭院。\n\n"
        "---\n\n"
        "第一场 哥特石砌古堡庭院 夜 暴雨\n"
        "暴雨倾盆，艾德里安正面走向镜头，说道：“城门已破，誓约不能断。”\n\n"
        "第二场 圣殿残垣祭坛 夜 薄雾\n"
        "薄雾弥漫的祭坛前，艾德里安举起符文长剑凝望黑暗。\n"
    )
    preamble = story_text[: story_text.index("第一场")]
    assert narrative_body(preamble) == "《灰烬圣剑：古堡守望》"
    assert not is_narrative_fragment(preamble)

    doc = _document(client, project_id, "medieval.txt", story_text)
    sections = source_sections(project_id, type("Doc", (), {"text": story_text})())
    assert [sec["title"] for sec in sections] == [
        "第一场 哥特石砌古堡庭院 夜 暴雨",
        "第二场 圣殿残垣祭坛 夜 薄雾",
    ]
    assert sections[0]["start_offset"] == 0
    assert sections[-1]["end_offset"] == len(story_text)
    assert "".join(story_text[s["start_offset"]:s["end_offset"]] for s in sections) == story_text
    # Setup bio block must not inflate Scene 1's estimated narrative duration.
    assert sections[0]["estimated_duration_s"] < 15.0

    _approved_outline(factory, project_id, doc, sections)
    book = _storyboard_book(client, project_id, episode_count=1, target_seconds=48, tolerance_seconds=12)
    assert book["preview"]["episode_count"] == 1
    assert book["preview"]["out_of_scope"] is None
    assert [sec["title"] for sec in book["preview"]["episodes"][0]["sections"]] == [
        "第一场 哥特石砌古堡庭院 夜 暴雨",
        "第二场 圣殿残垣祭坛 夜 薄雾",
    ]


def test_director_storyboard_state_flags_stale_for_basis_after_new_style_freeze(api):
    client, _factory, settings = api
    prepared = _prepare_project(client, sections=2)
    project = prepared["project"]
    book = _storyboard_book(client, project, episode_count=1, target_seconds=60)
    _confirm_book(client, project, book["plan_version"], book["plan_sha256"])
    queued = client.post(f"/api/v1/projects/{project}/director/storyboard/generate", json={}, headers=LOCAL)
    assert queued.status_code == 202
    _run_storyboard_to_rest(client, settings, project)

    before = client.get(f"/api/v1/projects/{project}/director/storyboard/state").json()
    assert before["stale_for_basis"] is False
    assert before["basis_changed_reason"] is None

    # Confirm a fresh storyboard book with a new frozen style in Stage 2.
    book2 = _storyboard_book(client, project, episode_count=1, target_seconds=60)
    confirm2 = client.post(
        f"/api/v1/projects/{project}/director/storyboard-book/confirm",
        json={
            "plan_version": book2["plan_version"],
            "expected_sha256": book2["plan_sha256"],
            "style": {
                "profile_id": "",
                "film_type": "live_action",
                "max_shot_seconds": 8.0,
                "dimensions": {"color_palette": "冷色低饱和暗调"},
            },
            "acknowledge_deviation": True,
        },
        headers=LOCAL,
    )
    assert confirm2.status_code == 200, confirm2.text

    after = client.get(f"/api/v1/projects/{project}/director/storyboard/state").json()
    assert after["stale_for_basis"] is True
    assert after["basis_changed_reason"] == "STYLE_UPDATED"
