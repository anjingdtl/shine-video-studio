"""V4 phase 1: the shot chain continues inside an episode and resets at its first shot.

The landing gate is one function (``apply_camera``) and the director run is one
executor (``run_pending_director_storyboard``).  These tests walk both through
the real services the resident Worker uses; the mock provider stands only where
a real text model would.
"""

from __future__ import annotations

import copy

import pytest

from shine.director import streamline
from shine.llm.gateway import LLMGateway
from shine.llm.secrets import SecretStore
from shine.models import Project, StyleVersion
from shine.schemas import DEFAULT_MAX_SHOT_SECONDS, ShotBindingInput, StyleBible
from shine.services import complete_location_binding
from tests.conftest import PNG, approve_lock, create_shot, upload_asset
from tests.test_director_streamline import (
    LOCAL,
    _chain_responder,
    _confirm_book,
    _prepare_project,
    _storyboard_book,
)
from tests.test_film_crew import (
    BRIEF,
    CAMERA,
    CONTINUITY,
    DIRECTOR,
    ENGLISH_STYLE,
    FACTS,
    SCRIPT,
)
from tests.test_film_crew import call as call_role
from tests.test_film_crew import (
    prepare as prepare_crew,
)

pytestmark = pytest.mark.integration


def _landed_shots(client, project: str, episode: str) -> list[dict]:
    response = client.get(f"/api/v1/projects/{project}/director/storyboard/episodes/{episode}/shots")
    assert response.status_code == 200, response.text
    return response.json()["shots"]


def test_the_chain_continues_inside_an_episode_and_resets_at_its_first_shot(api):
    """A unit's first shot continues the previous unit's last shot of the SAME episode.

    Up to now every unit opened on a cut, because the landing gate forgot the
    previous unit's shots.  The first shot of an episode is still a cut: no final
    frame is ever carried from one episode into the next.
    """
    client, _factory, settings = api
    prepared = _prepare_project(client, sections=4)
    project = prepared["project"]
    book = _storyboard_book(client, project, episode_count=2, target_seconds=60)
    _confirm_book(client, project, book["plan_version"], book["plan_sha256"])

    queued = client.post(f"/api/v1/projects/{project}/director/storyboard/generate", json={}, headers=LOCAL)
    assert queued.status_code == 202, queued.text
    assets = client.get(f"/api/v1/projects/{project}/assets").json()
    master = next(a["id"] for a in assets if a["role"] == "character" and a["entity_id"])
    base = _chain_responder({"asset": master})

    def director_asks_to_continue(messages, schema):
        answer = base(messages, schema)
        if schema.get("title") == "DirectorialPlan":
            for shot in answer["shots"]:
                shot["continuity"] = {"mode": "last_frame", "note": "the same action continues"}
        elif schema.get("title") == "SceneShotBoard":
            for shot in answer["shots"]:
                shot["transition_motivation"] = "continuous_action"
                shot["handoff_cue"] = "the same action continues"
        return answer

    gateway = LLMGateway(SecretStore(settings.secret_store_dir), mock_responder=director_asks_to_continue)
    factory = client.app.state.session_factory
    for _ in range(40):
        if not streamline.run_pending_director_storyboard(factory, settings, "worker-test", gateway=gateway):
            break
    state = client.get(f"/api/v1/projects/{project}/director/storyboard/state").json()
    assert state["status"] == "SUCCEEDED", state
    assert {row["status"] for row in state["episodes"]} == {"APPLIED"}

    first, second, third, fourth = _landed_shots(client, project, "ep-01")
    # Unit 1: nothing earlier in the episode exists, so the first shot is a cut.
    assert (first["continuity_mode"], first["previous_shot_id"]) == ("cut", None)
    assert (second["continuity_mode"], second["previous_shot_id"]) == ("last_frame", first["shot_id"])
    # Unit 2: its first shot continues the previous unit's last shot, and the
    # director's reason for the boundary is kept with the shot.
    assert (third["continuity_mode"], third["previous_shot_id"]) == ("last_frame", second["shot_id"])
    assert (fourth["continuity_mode"], fourth["previous_shot_id"]) == ("last_frame", third["shot_id"])
    landed = {row["id"]: row for row in client.get(f"/api/v1/projects/{project}/shots").json()}
    assert landed[third["shot_id"]]["spec"]["continuity"]["notes"] == "the same action continues"
    assert landed[first["shot_id"]]["spec"]["continuity"]["notes"] == ""

    # Episode 2 opens on its own: its first shot is a cut, however clearly the
    # director asked to continue, and it never points at episode 1's last shot.
    other_first, other_second, *_ = _landed_shots(client, project, "ep-02")
    assert (other_first["continuity_mode"], other_first["previous_shot_id"]) == ("cut", None)
    assert (other_second["continuity_mode"], other_second["previous_shot_id"]) == (
        "last_frame", other_first["shot_id"])
    assert fourth["shot_id"] not in {other_first["previous_shot_id"], other_second["previous_shot_id"]}


def test_the_predecessor_is_read_from_the_run_ledger_inside_one_episode():
    units = [
        {"episode_id": "ep-01", "production_section_id": "sec-01", "section_order": 1},
        {"episode_id": "ep-01", "production_section_id": "sec-02", "section_order": 2},
        {"episode_id": "ep-02", "production_section_id": "sec-03", "section_order": 3},
        {"episode_id": "ep-02", "production_section_id": "sec-04", "section_order": 4},
    ]
    state = {
        "units": units,
        "results": [
            {"unit": units[0], "apply": {"shot_ids": ["s1", "s2"]}},
            {"unit": units[1], "apply": {"shot_ids": ["s3", "s4"]}},
            {"unit": units[2], "apply": {"shot_ids": ["s5", "s6"]}},
        ],
    }
    assert streamline._episode_predecessor(state, units[1]) == "s2"
    # The second episode's first unit has no predecessor inside its episode.
    assert streamline._episode_predecessor(state, units[2]) is None
    assert streamline._episode_predecessor(state, units[3]) == "s6"
    assert streamline._episode_predecessor({"results": []}, units[0]) is None
    # The carry-over facts a unit is given follow the same boundary.
    assert streamline._prior_section_id(state, 1) == "sec-01"
    assert streamline._prior_section_id(state, 2) is None
    assert streamline._prior_section_id(state, 3) == "sec-03"


def _answer(client, payload):
    client.app.state.mock_llm_responder = lambda _messages, _schema: copy.deepcopy(payload)


def _single_shot_chain(client, project: str, *, first_shot: dict) -> tuple[dict, dict]:
    """A real crew chain whose director plan is one shot: returns (camera, continuity)."""
    script = call_role(client, project, "scriptwriter", SCRIPT)
    master = next(a for a in client.get(f"/api/v1/projects/{project}/assets").json() if a["entity_id"] == "Lin")
    plan = {"shots": [{**DIRECTOR["shots"][0], **first_shot, "asset_ids": [master["id"]]}]}
    director = call_role(client, project, "director", plan, inputs=[script["id"]])
    camera = call_role(client, project, "cinematographer", CAMERA, inputs=[director["id"]])
    continuity = call_role(client, project, "continuity_supervisor", CONTINUITY,
                           inputs=[director["id"], camera["id"]])
    return camera, continuity


def _apply(client, project: str, camera: dict, continuity: dict, **body):
    return client.post(f"/api/v1/projects/{project}/film-artifacts/{camera['id']}/apply-camera",
                       json={"continuity_artifact_id": continuity["id"], **body})


def _shot(client, project: str, shot_id: str) -> dict:
    return next(row for row in client.get(f"/api/v1/projects/{project}/shots").json() if row["id"] == shot_id)


def test_a_plan_may_continue_an_earlier_shot_of_its_episode_by_name(api):
    client, _factory, _settings = api
    project, style, _ = prepare_crew(client)
    earlier = create_shot(client, project, style["id"], beat="A woman sits down at the table.")
    camera, continuity = _single_shot_chain(client, project, first_shot={
        "continuity": {"mode": "last_frame", "note": "she stays in the same seat"},
    })
    refused = _apply(client, project, camera, continuity,
                     previous_shot_id="00000000-0000-0000-0000-000000000000")
    assert refused.status_code == 422, refused.text
    assert refused.json()["detail"]["code"] == "SHOT_CONTINUITY_PREDECESSOR_UNKNOWN"

    applied = _apply(client, project, camera, continuity, previous_shot_id=earlier["id"])
    assert applied.status_code == 200, applied.text
    landed = _shot(client, project, applied.json()["shot_ids"][0])
    assert landed["spec"]["continuity"] == {
        "mode": "last_frame", "previous_shot_id": earlier["id"], "previous_job_id": None,
        "reference_start_s": None, "reference_end_s": None, "notes": "she stays in the same seat",
    }


def test_without_an_earlier_shot_in_the_episode_the_first_shot_is_a_cut(api):
    client, _factory, _settings = api
    project, _style, _ = prepare_crew(client)
    camera, continuity = _single_shot_chain(client, project, first_shot={
        "continuity": {"mode": "last_frame", "note": "the director wanted a continuation"},
    })
    applied = _apply(client, project, camera, continuity)
    assert applied.status_code == 200, applied.text
    # There is no take to continue, so the shot lands as an independent shot.
    landed = _shot(client, project, applied.json()["shot_ids"][0])
    assert landed["spec"]["continuity"]["mode"] == "cut"
    assert landed["spec"]["continuity"]["previous_shot_id"] is None


def test_a_shot_shorter_than_its_line_is_refused_by_name_not_squeezed(api, monkeypatch):
    client, _factory, _settings = api
    project, _style, _ = prepare_crew(client)
    import shine.agents.film as film

    long_line = {"speaker_id": "Lin", "text": "你来了。", "start_s": 0.5, "end_s": 5.5}
    _answer(client, {**copy.deepcopy(SCRIPT), "beats": [{**SCRIPT["beats"][0], "dialogue": [long_line]}]})
    script = client.post(f"/api/v1/projects/{project}/film-agents/scriptwriter/run",
                         json={"brief": BRIEF, "locked_story_facts": FACTS}).json()
    master = next(a for a in client.get(f"/api/v1/projects/{project}/assets").json() if a["entity_id"] == "Lin")
    short = {"shots": [{**DIRECTOR["shots"][0], "duration_s": 4, "asset_ids": [master["id"]]}]}

    # The single adjudicator names the conflict before the shot exists, cut table or not.
    _answer(client, short)
    refused = client.post(f"/api/v1/projects/{project}/film-agents/director/run", json={
        "brief": BRIEF, "locked_story_facts": FACTS, "input_revision_ids": [script["id"]],
    })
    assert refused.status_code == 422, refused.text
    runs = client.get(f"/api/v1/projects/{project}/film-runs").json()
    refusal = next(row for row in runs if row["role_id"] == "director")
    assert refusal["trace"]["rejection_reason_code"] == "EDITORIAL_TIMING_CONFLICT"
    assert client.get(f"/api/v1/projects/{project}/shots").json() == []

    # A plan that predates the rule still cannot land with a squeezed line: the
    # gate refuses by name and leaves no shot behind.
    original = film.validate_output

    def lenient(role, proposal, body, inputs, context):
        try:
            return original(role, proposal, body, inputs, context)
        except ValueError as exc:
            if str(exc) != "EDITORIAL_TIMING_CONFLICT":
                raise

    monkeypatch.setattr(film, "validate_output", lenient)
    director = client.post(f"/api/v1/projects/{project}/film-agents/director/run", json={
        "brief": BRIEF, "locked_story_facts": FACTS, "input_revision_ids": [script["id"]],
    })
    assert director.status_code == 201, director.text
    _answer(client, CAMERA)
    camera = client.post(f"/api/v1/projects/{project}/film-agents/cinematographer/run", json={
        "brief": BRIEF, "locked_story_facts": FACTS, "input_revision_ids": [director.json()["id"]],
    }).json()
    _answer(client, CONTINUITY)
    continuity = client.post(f"/api/v1/projects/{project}/film-agents/continuity_supervisor/run", json={
        "brief": BRIEF, "locked_story_facts": FACTS,
        "input_revision_ids": [director.json()["id"], camera["id"]],
    }).json()
    landed = _apply(client, project, camera, continuity)
    assert landed.status_code == 409, landed.text
    assert landed.json()["detail"]["code"] == "SHOT_DIALOGUE_LONGER_THAN_SHOT"
    assert landed.json()["detail"]["lines"] == ["你来了。"]
    assert client.get(f"/api/v1/projects/{project}/shots").json() == []


def test_a_declared_place_binds_its_confirmed_scene_master_or_asks_for_it(api):
    client, factory, _settings = api
    project, _style, _ = prepare_crew(client)
    # Its own bytes: an upload that repeats a locked master's bytes is that master.
    shop = upload_asset(client, project, PNG + b"shop", filename="shop.png", role="scene", entity_id="钟表铺")
    approve_lock(client, shop["id"])

    camera, continuity = _single_shot_chain(client, project, first_shot={"location": "钟表铺"})
    applied = _apply(client, project, camera, continuity)
    assert applied.status_code == 200, applied.text
    landed = _shot(client, project, applied.json()["shot_ids"][0])
    assert landed["spec"]["location_id"] == "钟表铺"
    assert "requested_assets" not in landed["spec"]
    assert shop["id"] in landed["spec"]["required_asset_ids"]
    bound = {row["asset_id"]: row["use_as"] for row in client.get(f"/api/v1/shots/{landed['id']}/bindings").json()}
    assert bound[shop["id"]] == "picture"

    # A place with no confirmed master yet: the shot still lands and asks the
    # material stage for it, exactly like a character without a master.
    camera, continuity = _single_shot_chain(client, project, first_shot={"location": "码头"})
    applied = _apply(client, project, camera, continuity)
    assert applied.status_code == 200, applied.text
    landed = _shot(client, project, applied.json()["shot_ids"][0])
    assert landed["spec"]["location_id"] == "码头"
    assert landed["spec"]["requested_assets"] == [
        {"family": "scene", "name": "码头", "description": "", "carrier": None}]
    assert shop["id"] not in landed["spec"]["required_asset_ids"]

    master = next(a["id"] for a in client.get(f"/api/v1/projects/{project}/assets").json()
                  if a["entity_id"] == "Lin")
    with factory() as db:
        nine = [ShotBindingInput(asset_id=master, use_as="subject", ordinal=index) for index in range(9)]
        # A shot that is already full keeps its cast and asks instead of pushing
        # the references past the renderer's image limit.
        kept, request = complete_location_binding(db, project, "钟表铺", nine)
        assert len(kept) == 9 and request is not None and request.name == "钟表铺"
        # One scene picture per shot: a scene already bound is kept, none is added.
        one = [ShotBindingInput(asset_id=shop["id"], use_as="picture", ordinal=0)]
        same, request = complete_location_binding(db, project, "钟表铺", one)
        assert [binding.asset_id for binding in same] == [shop["id"]] and request is None
        # A scene asset from a different place cannot satisfy this location or
        # hide its RequestedAsset gap.
        mismatched, mismatch_req = complete_location_binding(db, project, "青石街", one)
        assert mismatched == []
        assert mismatch_req is not None and mismatch_req.name == "青石街"
        assert complete_location_binding(db, project, "  ", one) == (one, None)


def test_a_new_storyboard_book_starts_from_the_default_ceiling_of_the_one_setting(api):
    client, factory, _settings = api
    prepared = _prepare_project(client, sections=2)
    project = prepared["project"]
    # Omitted: the book starts from the default of the person's own setting.
    book = client.post(f"/api/v1/projects/{project}/director/storyboard-book",
                       json={"episode_count": 1, "target_seconds": 60, "tolerance_seconds": 30},
                       headers=LOCAL).json()
    assert book["preview"]["max_shot_seconds"] == DEFAULT_MAX_SHOT_SECONDS == 10.0
    _confirm_book(client, project, book["plan_version"], book["plan_sha256"])
    with factory() as db:
        frozen = db.get(StyleVersion, db.get(Project, project).frozen_style_version_id)
        assert frozen.snapshot["max_shot_seconds"] == 10

    # The setting itself is unchanged: H3's own 4-15 window, and a style frozen
    # with a value between 10 and 15 still loads exactly as it was frozen.
    assert StyleBible.model_validate({**ENGLISH_STYLE, "max_shot_seconds": 14}).max_shot_seconds == 14
    assert "max_shot_seconds" not in StyleBible.model_validate(ENGLISH_STYLE).model_dump(mode="json")
