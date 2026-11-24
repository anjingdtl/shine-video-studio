"""Phase 3 end-to-end tests: SceneShotBoard + ContinuityEngine replace the crew chain on the novel path."""

from __future__ import annotations

import copy
import json

from sqlalchemy import select

from shine.agents import continuity_engine as engine
from shine.director import streamline
from shine.h3.compiler import H3PromptCompiler
from shine.literary.requests import SCHEMA_INSTRUCTION
from shine.llm.gateway import LLMGateway
from shine.llm.secrets import SecretStore
from shine.models import AgentArtifact, AgentTaskRecord, Project, Shot, StyleVersion
from shine.schemas import DEFAULT_MAX_SHOT_SECONDS, ShotSpec, StyleBible
from tests.test_director_streamline import (
    LOCAL,
    _chain_responder,
    _confirm_book,
    _prepare_project,
    _storyboard_book,
)
from tests.test_film_crew import prepare


def _master(client, project: str) -> str:
    assets = client.get(f"/api/v1/projects/{project}/assets").json()
    return next(a["id"] for a in assets if a["role"] == "character" and a["entity_id"])


def _drive(factory, settings, gateway, *, ticks: int = 80) -> None:
    for _ in range(ticks):
        if not streamline.run_pending_director_storyboard(factory, settings, "worker-v4", gateway=gateway):
            break


def test_same_episode_cross_unit_continuation_and_cross_episode_reset(api):
    client, factory, settings = api
    prepared = _prepare_project(client, sections=4)
    project = prepared["project"]
    book = _storyboard_book(client, project, episode_count=2, target_seconds=60)
    _confirm_book(client, project, book["plan_version"], book["plan_sha256"])
    client.post(f"/api/v1/projects/{project}/director/storyboard/generate", json={}, headers=LOCAL)

    base = _chain_responder({"asset": _master(client, project)})
    gateway = LLMGateway(SecretStore(settings.secret_store_dir), mock_responder=base)
    _drive(factory, settings, gateway)

    state = client.get(f"/api/v1/projects/{project}/director/storyboard/state").json()
    assert state["status"] == "SUCCEEDED", state
    ep1 = next(ep for ep in state["episodes"] if ep["episode_id"] == "ep-01")
    ep2 = next(ep for ep in state["episodes"] if ep["episode_id"] == "ep-02")

    with factory() as db:
        ep1_shots = [db.get(Shot, sid) for sid in ep1["shot_ids"]]
        ep2_shots = [db.get(Shot, sid) for sid in ep2["shot_ids"]]
        boards = list(db.scalars(select(AgentArtifact).where(
            AgentArtifact.project_id == project, AgentArtifact.kind == "SceneShotBoard",
        ).order_by(AgentArtifact.created_at)).all())

    assert len(ep1_shots) == 4 and len(ep2_shots) == 4
    # Ep 1 shot 1 opens on cut; Scene 2 shot 1 (index 2) continues Scene 1's last shot via last_frame.
    assert ep1_shots[0].spec["continuity"]["mode"] == "cut"
    assert ep1_shots[0].spec["continuity"]["previous_shot_id"] is None
    assert ep1_shots[2].spec["continuity"]["mode"] == "last_frame"
    assert ep1_shots[2].spec["continuity"]["previous_shot_id"] == ep1_shots[1].id

    # Cross-episode boundary: Ep 2 shot 1 resets (no continuity), even if the board asked to continue.
    assert ep2_shots[0].spec["continuity"]["mode"] == "cut"
    assert ep2_shots[0].spec["continuity"]["previous_shot_id"] is None
    assert len(boards) == 4
    # Let's verify cross-episode reset warning when a board asks to continue without predecessor.
    first_scene = ep2_shots[0]
    assert first_scene.spec.get("transition_motivation") in {"establishing_reset", "continuous_action"}

    # Verify board warning is stored if Ep 2's first board requested continuous_action without predecessor.
    sample_scene = {
        "location": "钟表铺",
        "beats": [{"beat_type": "action", "visible_action": "老周擦表。", "characters": ["老周"],
                   "spoken_lines": []}],
    }
    ep2_board_payload = copy.deepcopy(boards[2].payload_json)
    ep2_board_payload["shots"][0]["transition_motivation"] = "continuous_action"
    ep2_board_payload["shots"][0]["beat_ids"] = ["B1"]
    ep2_board_payload["shots"][0]["internal_cuts"] = []
    ep2_board_payload["shots"][0]["dialogue"] = []
    ep2_board_payload["shots"] = [ep2_board_payload["shots"][0]]
    ep2_board_payload["shots"][0]["blocking"]["placements"] = [
        p for p in ep2_board_payload["shots"][0]["blocking"]["placements"] if p["character"] == "老周"
    ]
    rows, verdict = engine.review_scene_board(
        ep2_board_payload, sample_scene, {"max_shot_seconds": 10.0, "continuity_predecessor": False}
    ).boundaries, engine.review_scene_board(
        ep2_board_payload, sample_scene, {"max_shot_seconds": 10.0, "continuity_predecessor": False}
    )
    assert rows[0]["mode"] == "cut"
    assert any(w["code"] == "CONTINUATION_IGNORED_WITHOUT_PREDECESSOR" for w in verdict.warnings)


def test_cross_episode_reset_stores_warning_on_board_artifact(api):
    client, factory, settings = api
    prepared = _prepare_project(client, sections=4)
    project = prepared["project"]
    book = _storyboard_book(client, project, episode_count=2, target_seconds=60)
    _confirm_book(client, project, book["plan_version"], book["plan_sha256"])
    client.post(f"/api/v1/projects/{project}/director/storyboard/generate", json={}, headers=LOCAL)

    base = _chain_responder({"asset": _master(client, project)})

    def ask_continue_on_ep2(messages, schema):
        ans = base(messages, schema)
        if schema.get("title") == "SceneShotBoard":
            ctx = json.loads(messages[-1].content.split(SCHEMA_INSTRUCTION)[0])
            if (ctx.get("production_context") or {}).get("episode_id") == "ep-02":
                ans["shots"][0]["transition_motivation"] = "continuous_action"
        return ans

    gateway = LLMGateway(SecretStore(settings.secret_store_dir), mock_responder=ask_continue_on_ep2)
    _drive(factory, settings, gateway)

    with factory() as db:
        boards = list(db.scalars(select(AgentArtifact).where(
            AgentArtifact.project_id == project, AgentArtifact.kind == "SceneShotBoard",
        ).order_by(AgentArtifact.created_at)).all())
        ep2_first_board = next(b for b in boards if b.payload_json.get("episode_id") == "ep-02")
        ep2_first_shot_id = client.get(
            f"/api/v1/projects/{project}/director/storyboard/state"
        ).json()["episodes"][1]["shot_ids"][0]
        ep2_first_shot = db.get(Shot, ep2_first_shot_id)

    assert ep2_first_shot.spec["continuity"]["mode"] == "cut"
    assert ep2_first_shot.spec["continuity"]["previous_shot_id"] is None
    assert any(
        w["code"] == "CONTINUATION_IGNORED_WITHOUT_PREDECESSOR"
        for w in (ep2_first_board.payload_json.get("warnings") or [])
    )


def test_shot_reverse_shot_internal_cut_compiles_to_v2_and_refuses_two_cuts(api):
    client, factory, settings = api
    prepared = _prepare_project(client, sections=1)
    project = prepared["project"]
    book = _storyboard_book(client, project, episode_count=1, target_seconds=60)
    _confirm_book(client, project, book["plan_version"], book["plan_sha256"])
    client.post(f"/api/v1/projects/{project}/director/storyboard/generate", json={}, headers=LOCAL)

    master = _master(client, project)
    base = _chain_responder({"asset": master})
    first_board_refused = {"done": False}
    board_prompts: list[str] = []

    def two_speaker_scene_and_refused_two_cuts(messages, schema):
        title = schema.get("title")
        if title == "DramaSceneScript":
            return {
                "location": "钟表铺",
                "time_of_day": "night",
                "weather_atmosphere": "雨",
                "entry_bridge": "",
                "exit_state": "两人对视。",
                "beats": [
                    {"beat_type": "dialogue_exchange", "visible_action": "老周抬眼。", "characters": ["老周"],
                     "spoken_lines": [{"speaker": "老周", "emotion": "冷", "text": "表停了。"}]},
                    {"beat_type": "dialogue_exchange", "visible_action": "客人按住表壳。", "characters": ["客人"],
                     "spoken_lines": [{"speaker": "客人", "emotion": "低", "text": "你修得好。"}]},
                ],
            }
        ans = base(messages, schema)
        if title == "SceneShotBoard":
            board_prompts.append(messages[0].content)
            if not first_board_refused["done"]:
                first_board_refused["done"] = True
                # Force 2 internal cuts -> rejected by schema/engine; retry carries repair note.
                s = copy.deepcopy(ans["shots"][0])
                c = copy.deepcopy(s["internal_cuts"][0])
                s["internal_cuts"] = [c, {**c, "start_s": round(c["start_s"] + 2.1, 2)}]
                return {"shots": [s], "prompt_style": ans["prompt_style"],
                        "prompt_story_facts": ans["prompt_story_facts"]}
        return ans

    gateway = LLMGateway(SecretStore(settings.secret_store_dir), mock_responder=two_speaker_scene_and_refused_two_cuts)
    _drive(factory, settings, gateway)

    state = client.get(f"/api/v1/projects/{project}/director/storyboard/state").json()
    assert state["status"] == "SUCCEEDED", state
    shot_ids = state["episodes"][0]["shot_ids"]
    assert len(shot_ids) == 1  # packed into ONE segment with 1 internal cut
    assert len(board_prompts) == 2

    with factory() as db:
        proj = db.get(Project, project)
        sv = db.get(StyleVersion, proj.frozen_style_version_id)
        shot = db.get(Shot, shot_ids[0])
        compiled = H3PromptCompiler().compile(
            spec=ShotSpec.model_validate(shot.spec),
            style=StyleBible.model_validate(sv.snapshot),
            manifest=[],
        )
    assert len(shot.spec.get("internal_cuts") or []) == 1
    text = compiled.sections["integrated_multimodal_description"]
    assert "[Shot 1]" in text and "[Shot 2]" in text
    assert text.index("表停了。") < text.index("[Shot 2]")
    assert text.index("你修得好。") > text.index("[Shot 2]")


def test_duration_ceiling_defaults_to_10_and_over_limit_retries_with_reason(api):
    client, factory, settings = api
    prepared = _prepare_project(client, sections=1)
    project = prepared["project"]
    # Confirm a book with >10 max_shot_seconds is refused with 422 MAX_SHOT_SECONDS_OUT_OF_RANGE.
    book = _storyboard_book(client, project, episode_count=1, target_seconds=60)
    refused = client.post(
        f"/api/v1/projects/{project}/director/storyboard-book/confirm",
        json={"plan_version": book["plan_version"], "expected_sha256": book["plan_sha256"],
              "style": {"profile_id": "wong-kar-wai", "film_type": "live_action", "max_shot_seconds": 12}},
        headers=LOCAL,
    )
    assert refused.status_code == 422
    assert refused.json()["detail"]["code"] == "MAX_SHOT_SECONDS_OUT_OF_RANGE"

    # Confirm without explicit max_shot_seconds -> book default is 10.0.
    assert book["preview"]["max_shot_seconds"] == DEFAULT_MAX_SHOT_SECONDS == 10.0
    _confirm_book(client, project, book["plan_version"], book["plan_sha256"])
    client.post(f"/api/v1/projects/{project}/director/storyboard/generate", json={}, headers=LOCAL)

    base = _chain_responder({"asset": _master(client, project)})
    over = {"on": True}

    def over_cap_once(messages, schema):
        ans = base(messages, schema)
        if schema.get("title") == "SceneShotBoard" and over["on"]:
            over["on"] = False
            ans["shots"][0]["duration_s"] = 12.0
        return ans

    gateway = LLMGateway(SecretStore(settings.secret_store_dir), mock_responder=over_cap_once)
    _drive(factory, settings, gateway)

    state = client.get(f"/api/v1/projects/{project}/director/storyboard/state").json()
    assert state["status"] == "SUCCEEDED", state
    with factory() as db:
        refused_rows = [
            row for row in db.scalars(select(AgentTaskRecord).where(
                AgentTaskRecord.project_id == project, AgentTaskRecord.role_id == "director",
            )).all()
            if (row.trace or {}).get("rejection_reason_code") == "SHOT_DURATION_OVER_LIMIT"
        ]
    assert len(refused_rows) == 1


def test_no_video_reference_no_dialogue_ratio_kpi_and_all_units_land(api):
    client, factory, settings = api
    prepared = _prepare_project(client, sections=4, section_seconds=5)
    project = prepared["project"]
    book = _storyboard_book(client, project, episode_count=2, target_seconds=10, tolerance_seconds=0)
    # Even if a legacy style snapshot had dialogue_ratio_percent=80, novel path ignores it.
    _confirm_book(client, project, book["plan_version"], book["plan_sha256"])
    client.post(f"/api/v1/projects/{project}/director/storyboard/generate", json={}, headers=LOCAL)

    base = _chain_responder({"asset": _master(client, project)})
    seen_systems: list[str] = []

    def spy(messages, schema):
        seen_systems.append(messages[0].content)
        return base(messages, schema)

    gateway = LLMGateway(SecretStore(settings.secret_store_dir), mock_responder=spy)
    _drive(factory, settings, gateway)

    state = client.get(f"/api/v1/projects/{project}/director/storyboard/state").json()
    assert state["status"] == "SUCCEEDED", state
    assert all(unit["state"] == "APPLIED" for ep in state["episodes"] for unit in ep["units"])
    assert not any("Aim for dialogue in at least" in s for s in seen_systems)

    with factory() as db:
        shots = list(db.scalars(select(Shot).where(Shot.project_id == project)).all())
        tasks = list(db.scalars(select(AgentTaskRecord).where(AgentTaskRecord.project_id == project)).all())
    assert len(shots) == 8
    assert all((s.spec.get("continuity") or {}).get("mode") != "video_reference" for s in shots)
    assert all("dialogue_coverage" not in (t.trace or {}) for t in tasks)


def test_imported_script_keeps_legacy_chain_and_verbatim_lock(api):
    client, factory, settings = api
    project, _style, _profile = prepare(
        client, freeze=True, character_entity_id="老周", character_filename="老周.png")
    master = _master(client, project)
    script_text = "第1场 钟表铺\n老周把怀表贴近耳朵。\n老周：又停在十二点。"

    # A broken scriptwriter response that invents a line must still be refused by DIALOGUE_NOT_IN_USER_SCRIPT.
    def invent_line(messages, schema):
        if schema.get("title") == "ScriptBreakdown":
            return {
                "locked_story_facts": [],
                "prompt_story_facts": [],
                "prompt_style": {
                    "photography_language": "Stable composed frames.",
                    "shot_blocking": "Readable blocking.",
                    "color_palette": "Restrained palette.",
                    "lighting_tone": "Directional lamp light.",
                    "lens_texture": "Clean optical texture.",
                    "editing_rhythm": "Deliberate reveals.",
                    "sound_performance": "Original dialogue.",
                },
                "beats": [{"id": "B1", "description": "老周看表。", "characters": ["老周"],
                           "dialogue": [{"speaker_id": "老周", "text": "剧本里没有的台词。",
                                         "start_s": 0.5, "end_s": 2.5}]}],
            }
        return _chain_responder({"asset": master})(messages, schema)

    # Confirm a style with dialogue_ratio_percent=0 (verbatim lock) for the imported script path.
    with factory.begin() as db:
        proj = db.get(Project, project)
        sv = db.get(StyleVersion, proj.frozen_style_version_id)
        sv.snapshot = {**sv.snapshot, "dialogue_ratio_percent": 0}

    gateway = LLMGateway(SecretStore(settings.secret_store_dir), mock_responder=invent_line)
    client.app.state.llm_gateway_override = gateway
    # Call generate_storyboard directly with dramatize=False (the imported-script path).
    from shine.api_film import StoryboardRequest, generate_storyboard

    class _DummyReq:
        def __init__(self, st):
            self.app = type("App", (), {"state": type("State", (), {"settings": st})()})()

    with factory() as db:
        res = generate_storyboard(
            project,
            StoryboardRequest(text=script_text, dramatize=False, allow_remote_text=True),
            _DummyReq(settings),
            db,
            gateway,
        )
    assert res["status"] == "REJECTED"
    assert res["failed_role"] == "scriptwriter"
    assert res["rejections"][0]["rejection_reason_code"] == "DIALOGUE_NOT_IN_USER_SCRIPT"


def test_cross_unit_last_frame_receives_previous_shot_blocking_and_blocks_axis_flip(api):
    client, factory, settings = api
    prepared = _prepare_project(client, sections=2)
    project = prepared["project"]
    book = _storyboard_book(client, project, episode_count=1, target_seconds=40)
    _confirm_book(client, project, book["plan_version"], book["plan_sha256"])
    client.post(f"/api/v1/projects/{project}/director/storyboard/generate", json={}, headers=LOCAL)

    base = _chain_responder({"asset": _master(client, project)})
    seen_prev_blocking: list[dict | None] = []
    unit2_flip_once = {"on": True}

    def inspect_unit2(messages, schema):
        ans = base(messages, schema)
        if schema.get("title") == "SceneShotBoard":
            ctx = json.loads(messages[-1].content.split(SCHEMA_INSTRUCTION)[0])
            prod = ctx.get("production_context") or {}
            if prod.get("continuity_predecessor"):
                seen_prev_blocking.append(prod.get("previous_shot_blocking"))
                if unit2_flip_once["on"]:
                    unit2_flip_once["on"] = False
                    ans["shots"][0]["transition_motivation"] = "continuous_action"
                    ans["shots"][0]["blocking"] = {
                        "axis": "flipped-axis",
                        "placements": ans["shots"][0]["blocking"]["placements"],
                    }
        return ans

    gateway = LLMGateway(SecretStore(settings.secret_store_dir), mock_responder=inspect_unit2)
    _drive(factory, settings, gateway)

    state = client.get(f"/api/v1/projects/{project}/director/storyboard/state").json()
    assert state["status"] == "SUCCEEDED", state
    assert seen_prev_blocking and seen_prev_blocking[0] is not None
    with factory() as db:
        refused_rows = [
            row for row in db.scalars(select(AgentTaskRecord).where(
                AgentTaskRecord.project_id == project, AgentTaskRecord.role_id == "director",
            )).all()
            if (row.trace or {}).get("rejection_reason_code") == "AXIS_CHANGED_WITHOUT_BREAK"
        ]
    assert len(refused_rows) == 1


def test_retry_blocked_episode_restores_failed_task_after_other_episode_advances(api):
    client, factory, settings = api
    prepared = _prepare_project(client, sections=4)
    project = prepared["project"]
    book = _storyboard_book(client, project, episode_count=2, target_seconds=60)
    _confirm_book(client, project, book["plan_version"], book["plan_sha256"])
    client.post(f"/api/v1/projects/{project}/director/storyboard/generate", json={}, headers=LOCAL)

    base = _chain_responder({"asset": _master(client, project)})
    fail_ep1_unit1 = {"remaining": 3}

    def responder(messages, schema):
        ans = base(messages, schema)
        if schema.get("title") == "SceneShotBoard":
            ctx = json.loads(messages[-1].content.split(SCHEMA_INSTRUCTION)[0])
            prod = ctx.get("production_context") or {}
            if (
                prod.get("episode_id") == "ep-01"
                and prod.get("continuity_predecessor")
                and fail_ep1_unit1["remaining"] > 0
            ):
                fail_ep1_unit1["remaining"] -= 1
                ans["shots"][0]["location"] = "wrong-place"
        return ans

    gateway = LLMGateway(SecretStore(settings.secret_store_dir), mock_responder=responder)
    _drive(factory, settings, gateway)

    state = client.get(f"/api/v1/projects/{project}/director/storyboard/state").json()
    assert state["status"] == "NEEDS_REVIEW"
    assert state["episodes"][0]["status"] == "BLOCKED"
    assert state["episodes"][1]["status"] == "APPLIED"

    # Simulate a remote-provider decided refusal so guard_request requires
    # explicit replay authorization against ep-01's saved failed_task_id even
    # though ep-02 subsequently succeeded and cleared top-level failed_task_id.
    with factory.begin() as db:
        run_row = db.scalars(select(AgentTaskRecord).where(
            AgentTaskRecord.project_id == project,
            AgentTaskRecord.role_id == streamline.DIRECTOR_STORYBOARD_RUN,
        )).first()
        failed_id = run_row.trace["blocked_episodes"]["ep-01"]["failed_task_id"]
        assert failed_id
        assert run_row.trace.get("failed_task_id") is None
        failed_row = db.get(AgentTaskRecord, failed_id)
        failed_row.trace = {**failed_row.trace, "is_mock": False, "external_outcome": "RESPONSE_RECEIVED"}

    retried = client.post(
        f"/api/v1/projects/{project}/director/storyboard/episodes/ep-01/retry",
        json={"expected_state_version": state["state_version"]},
        headers=LOCAL,
    )
    assert retried.status_code == 202, retried.text
    _drive(factory, settings, gateway)

    final_state = client.get(f"/api/v1/projects/{project}/director/storyboard/state").json()
    assert final_state["status"] == "SUCCEEDED", final_state
    assert [ep["status"] for ep in final_state["episodes"]] == ["APPLIED", "APPLIED"]


def test_architecture_sees_confirmed_scenes_and_subject_ordinals_stay_stable(api):
    from tests.conftest import PNG, approve_lock, upload_asset

    client, factory, settings = api
    prepared = _prepare_project(client, sections=2)
    project = prepared["project"]
    guest = upload_asset(client, project, PNG + b"guest", filename="guest.png", role="character", entity_id="客人")
    approve_lock(client, guest["id"])
    shop = upload_asset(client, project, PNG + b"shop", filename="shop.png", role="scene", entity_id="钟表铺")
    approve_lock(client, shop["id"])

    book = _storyboard_book(client, project, episode_count=1, target_seconds=40)
    _confirm_book(client, project, book["plan_version"], book["plan_sha256"])
    client.post(f"/api/v1/projects/{project}/director/storyboard/generate", json={}, headers=LOCAL)

    base = _chain_responder({"asset": _master(client, project)})
    seen_registered_locations: list[list[str]] = []
    seen_prev_exit: list[str | None] = []

    def two_hander(messages, schema):
        title = schema.get("title")
        ctx = json.loads(messages[-1].content.split(SCHEMA_INSTRUCTION)[0])
        prod = ctx.get("production_context") or {}
        if title == "SeriesArchitecture":
            seen_registered_locations.append(
                [row["name"] for row in (prod.get("registered_locations") or [])]
            )
            return base(messages, schema)
        if title == "DramaSceneScript":
            ans = base(messages, schema)
            if prod.get("previous_scene"):
                # Swap cast order in beat characters on Unit 1 to try flipping Subject 1/2.
                ans["beats"][0]["characters"] = ["客人", "老周"]
            else:
                ans["beats"][0]["characters"] = ["老周", "客人"]
            return ans
        if title == "SceneShotBoard":
            if prod.get("continuity_predecessor"):
                seen_prev_exit.append(prod.get("previous_shot_exit_state"))
            ans = base(messages, schema)
            ans["shots"][0]["blocking"] = {
                "axis": "counter-axis",
                "placements": [
                    {"character": "老周", "zone": "left_fg", "facing": "facing_right",
                     "posture": "standing", "props": []},
                    {"character": "客人", "zone": "right_fg", "facing": "facing_left",
                     "posture": "standing", "props": []},
                ],
            }
            return ans
        return base(messages, schema)

    gateway = LLMGateway(SecretStore(settings.secret_store_dir), mock_responder=two_hander)
    _drive(factory, settings, gateway)

    state = client.get(f"/api/v1/projects/{project}/director/storyboard/state").json()
    assert state["status"] == "SUCCEEDED", state
    assert seen_registered_locations and "钟表铺" in seen_registered_locations[0]
    assert seen_prev_exit and seen_prev_exit[0]

    shot_ids = state["episodes"][0]["shot_ids"]
    first_subs = [
        b["asset_id"] for b in client.get(f"/api/v1/shots/{shot_ids[0]}/bindings").json()
        if b["use_as"] == "subject"
    ]
    unit2_subs = [
        b["asset_id"] for b in client.get(f"/api/v1/shots/{shot_ids[2]}/bindings").json()
        if b["use_as"] == "subject"
    ]
    assert first_subs == unit2_subs == [_master(client, project), guest["id"]]
