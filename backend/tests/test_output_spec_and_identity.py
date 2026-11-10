"""The director workbench's output frame, clip length and identity binding.

These tests cover the three things a person actually chooses before a real run:
what frame the film is delivered in, how long each clip is, and which reference
image carries which character.  The last one is the difference between a face
that matches the approved master and a face the model invented.
"""

from __future__ import annotations

import json
from types import SimpleNamespace

from sqlalchemy import select

from shine.h3.compiler import H3PromptCompiler, PromptCompilationError
from shine.models import Job
from shine.schemas import (
    ReferenceManifestItem,
    ReferenceUse,
    ShotBindingInput,
    ShotSpec,
    StyleBible,
)
from shine.services import build_reference_manifest

from .conftest import (
    PNG,
    approve_lock,
    create_project,
    create_shot,
    freeze_style,
    register_workflow_profile,
    style_payload,
    upload_asset,
)

STYLE = StyleBible.model_validate(style_payload())


def _asset(asset_id: str, name: str) -> SimpleNamespace:
    return SimpleNamespace(
        id=asset_id, kind="image", entity_id=name, role="character", duration_s=None,
        sha256="a" * 64, storage_path=f"{asset_id}.png", content_type="image/png",
    )


def _binding(asset_id: str, use_as: str, ordinal: int) -> ShotBindingInput:
    return ShotBindingInput(asset_id=asset_id, use_as=use_as, ordinal=ordinal)


def _manifest_item(
    label: str, asset_id: str, use_as: ReferenceUse, ordinal: int, role: str | None = None,
) -> ReferenceManifestItem:
    return ReferenceManifestItem(
        label=label, asset_id=asset_id, sha256="b" * 64, storage_key=f"{asset_id}.png",
        content_type="image/png", use_as=use_as, ordinal=ordinal, role=role,
    )


def test_manifest_keeps_pictures_after_subjects_so_picture_labels_match_the_renderer():
    """A picture bound first must not shift every face by one slot.

    The H3 node maps ``reference_subject_1`` to its first image input and
    ``reference_picture_1`` to a later one.  The manifest therefore has to use
    the same family order, otherwise `<Picture 1>` names a different file than
    the one the renderer receives.
    """
    assets = {"p": _asset("p", "客厅"), "s": _asset("s", "阿德")}
    manifest = build_reference_manifest(
        [_binding("p", "picture", 0), _binding("s", "subject", 1)], assets,
    )
    assert [item.label for item in manifest] == ["<Subject 1>", "<Picture 2>"]
    assert [item.asset_id for item in manifest] == ["s", "p"]


def test_compiler_binds_the_cast_order_to_the_reference_images():
    """Two subjects need an explicit order; "approved subject" twice is not one."""
    manifest = [
        _manifest_item("<Subject 1>", "a1", ReferenceUse.SUBJECT, 0, role="阿德"),
        _manifest_item("<Subject 2>", "a2", ReferenceUse.SUBJECT, 1, role="小米"),
    ]
    spec = ShotSpec(
        story_beat="Two dancers cross the hall and stop under the chandelier.",
        duration_target_s=5,
        style_version_id="style-1",
        characters=["阿德", "小米"],
    )
    compiled = H3PromptCompiler().compile(spec=spec, style=STYLE, manifest=manifest)
    assert "Reference cast order (mandatory)" in compiled.sections["detailed_description"]
    assert "<Subject 1> (first)" in compiled.sections["detailed_description"]
    assert "for the first listed performer" in compiled.sections["subject_definitions"]
    assert "never swap, merge or borrow a face" in compiled.sections["detailed_description"]


def test_compiler_refuses_a_character_without_a_reference():
    manifest = [_manifest_item("<Subject 1>", "a1", ReferenceUse.SUBJECT, 0, role="阿德")]
    spec = ShotSpec(
        story_beat="Two dancers cross the hall.",
        duration_target_s=5,
        style_version_id="style-1",
        characters=["阿德", "小米"],
    )
    try:
        H3PromptCompiler().compile(spec=spec, style=STYLE, manifest=manifest)
    except PromptCompilationError as exc:
        assert exc.code == "H3_CHARACTER_REFERENCE_MISSING"
        assert exc.details["character"] == "小米"
    else:  # pragma: no cover - the compiler must refuse
        raise AssertionError("an unbound character was compiled silently")


def test_voice_reference_is_optional_in_the_shot_reference_policy(api):
    from shine.agents.film import SHOT_REFERENCE_OPTIONAL_ROLES, SHOT_REFERENCE_REQUIRED_ROLES, shot_reference_policy

    assert SHOT_REFERENCE_REQUIRED_ROLES == ("character",)
    assert SHOT_REFERENCE_OPTIONAL_ROLES == ("voice",)
    client, factory, settings = api
    project_id = create_project(client, "音色可选")
    with factory() as db:
        policy = shot_reference_policy(db, project_id, {"shots": []})["policy"]
    assert policy["optional_roles"] == ["voice"]
    assert "optional" in policy["shot_reference_set"]


def _profile_with_size(client, *, with_size: bool, reference_slot: str = "reference_subject_1"):
    graph = {
        "31": {
            "class_type": "MiniMaxH3ReferenceToVideo",
            "inputs": {"prompt": "", "width": 768, "height": 1344,
                       "duration": 6.0, "ref_images.ref_image_0": ""},
        },
    }
    parameter_map = {
        "positive_prompt": "31.inputs.prompt",
        "duration": "31.inputs.duration",
        reference_slot: "31.inputs.ref_images.ref_image_0",
    }
    if with_size:
        parameter_map |= {"width": "31.inputs.width", "height": "31.inputs.height"}
    return register_workflow_profile(
        client, name=f"size-{'yes' if with_size else 'no'}-{id(parameter_map)}", mode="Ref2VA",
        graph=graph, parameter_map=parameter_map, required_nodes=["MiniMaxH3ReferenceToVideo"],
    )


def _project_with_one_shot(client, name="画幅测试"):
    project_id = create_project(client, name)
    style = freeze_style(client, project_id)
    subject = upload_asset(client, project_id, content=PNG + b"-subject", filename="阿德.png",
                           role="character", entity_id="阿德")
    approve_lock(client, subject["id"])
    shot = create_shot(
        client, project_id, style["id"], title="镜头一", beat="阿德推门而入。",
        characters=["阿德"], asset_ids=[subject["id"]],
        bindings=[{"asset_id": subject["id"], "use_as": "subject", "role": "阿德", "ordinal": 0}],
    )
    return project_id, shot


def test_subject_slots_follow_the_cast_description_not_the_asset_list():
    """The director's asset_ids order must not decide who is <Subject 1>."""
    from shine.services import order_bindings_for_cast

    assets = {
        "b": _asset("b", "小米"),
        "a": _asset("a", "阿德"),
        "scene": SimpleNamespace(id="scene", entity_id="客厅", role="scene"),
    }
    bindings = [
        _binding("scene", "picture", 0),
        _binding("b", "subject", 1),
        _binding("a", "subject", 2),
    ]
    ordered = order_bindings_for_cast(bindings, ["阿德", "小米"], assets)
    assert [(row.asset_id, row.use_as.value, row.ordinal) for row in ordered] == [
        ("a", "subject", 0),
        ("b", "subject", 1),
        ("scene", "picture", 2),
    ]


def test_series_refuses_a_size_the_profile_does_not_map(api):
    client, _factory, _settings = api
    project_id, shot = _project_with_one_shot(client, "无宽高映射")
    profile = _profile_with_size(client, with_size=False)
    response = client.post(f"/api/v1/projects/{project_id}/production-series", json={
        "shot_ids": [shot["id"]], "provider": "comfyui", "workflow_profile_id": profile["id"],
        "output": {"width": 1344, "height": 768},
    })
    assert response.status_code == 422, response.text
    assert response.json()["detail"]["code"] == "WORKFLOW_SIZE_NOT_MAPPED"


def test_series_carries_the_chosen_frame_into_the_job_request(api):
    client, factory, _settings = api
    project_id, shot = _project_with_one_shot(client, "宽高生效")
    profile = _profile_with_size(client, with_size=True)
    response = client.post(f"/api/v1/projects/{project_id}/production-series", json={
        "shot_ids": [shot["id"]], "provider": "comfyui", "workflow_profile_id": profile["id"],
        "output": {"width": 1344, "height": 768},
    })
    assert response.status_code == 201, response.text
    series = response.json()
    assert series["output_spec"]["width"] == 1344
    assert series["output_spec"]["height"] == 768
    with factory() as db:
        job = db.scalar(select(Job).where(Job.shot_id == shot["id"]))
        request = json.loads(json.dumps(job.request))
    assert request["output_width"] == 1344
    assert request["output_height"] == 768


def test_series_with_one_ordinary_picture_keeps_the_selected_ref2va_profile(api):
    client, factory, _settings = api
    project_id = create_project(client, "单图参考切镜")
    style = freeze_style(client, project_id)
    image = upload_asset(client, project_id, role="prop", entity_id="Red box")
    approve_lock(client, image["id"])
    shot = create_shot(client, project_id, style["id"], beat="An overhead view of the red box.",
                       asset_ids=[image["id"]], bindings=[{"asset_id": image["id"], "use_as": "picture"}])
    profile = _profile_with_size(client, with_size=True, reference_slot="reference_picture_1")
    response = client.post(f"/api/v1/projects/{project_id}/production-series", json={
        "shot_ids": [shot["id"]], "provider": "comfyui", "workflow_profile_id": profile["id"],
    })
    assert response.status_code == 201, response.text
    with factory() as db:
        request = db.scalar(select(Job).where(Job.shot_id == shot["id"])).request
    assert request["generation_mode"] == "Ref2VA"
    assert request["reference_manifest"][0]["frame_role"] is None
    assert request["shot_spec"]["continuity"]["mode"] == "cut"


def test_a_clip_length_that_cannot_hold_the_dialogue_is_refused_by_name(api):
    client, _factory, _settings = api
    project_id = create_project(client, "时长装不下")
    style = freeze_style(client, project_id)
    subject = upload_asset(client, project_id, content=PNG + b"-s2", filename="小米.png",
                           role="character", entity_id="小米")
    approve_lock(client, subject["id"])
    shot = create_shot(
        client, project_id, style["id"], title="长台词", beat="小米说完整一句台词。",
        characters=["小米"], asset_ids=[subject["id"]], duration=8,
        dialogue=[{"speaker_id": "小米", "text": "这句话要说很久。", "start_s": 0.5, "end_s": 7.0}],
        bindings=[{"asset_id": subject["id"], "use_as": "subject", "role": "小米", "ordinal": 0}],
    )
    profile = _profile_with_size(client, with_size=True)
    response = client.post(f"/api/v1/projects/{project_id}/production-series", json={
        "shot_ids": [shot["id"]], "provider": "comfyui", "workflow_profile_id": profile["id"],
        "output": {"clip_seconds": 4},
    })
    assert response.status_code == 422, response.text
    assert response.json()["detail"]["code"] == "CLIP_TOO_SHORT_FOR_DIALOGUE"


def test_standard_parameters_only_map_an_input_the_graph_really_has(api):
    client, _factory, _settings = api
    project_id = create_project(client, "补齐参数")
    profile = _profile_with_size(client, with_size=False)
    response = client.post(f"/api/v1/workflow-profiles/{profile['id']}/standard-parameters")
    assert response.status_code == 200, response.text
    mapped = response.json()["parameter_map"]
    assert mapped["width"] == "31.inputs.width"
    assert mapped["height"] == "31.inputs.height"

    # A graph without a size input is refused, never guessed.
    other = register_workflow_profile(
        client, name=f"no-size-node-{project_id}", mode="Ref2VA",
        graph={"1": {"class_type": "H3BaseSampler", "inputs": {"prompt": "", "duration": 6.0}}},
        parameter_map={"positive_prompt": "1.inputs.prompt", "duration": "1.inputs.duration"},
        required_nodes=["H3BaseSampler"],
    )
    refused = client.post(f"/api/v1/workflow-profiles/{other['id']}/standard-parameters")
    assert refused.status_code == 422, refused.text
    assert refused.json()["detail"]["code"] == "WORKFLOW_SIZE_INPUT_NOT_FOUND"


def test_an_older_project_profile_adopts_the_standard_size_mapping(api):
    """A project registered before the size mapping existed is migrated.

    The canonical file now carries width/height; the project's own copy must
    adopt exactly those two keys, and nothing else may change silently.
    """
    import json as _json

    from shine.api_setup import _register_project_profiles
    from shine.comfyops import deployment

    client, factory, _settings = api
    project_id = create_project(client, "老方案升级")
    name = deployment.load_default_catalog()["workflow_profiles"][0]
    matches = [
        path for path in deployment.PROFILE_DIR.glob("*.json")
        if _json.loads(path.read_text(encoding="utf-8")).get("name") == name
    ]
    assert len(matches) == 1
    raw = _json.loads(matches[0].read_text(encoding="utf-8"))
    legacy_map = {key: value for key, value in raw["parameter_map"].items() if key not in ("width", "height")}
    register_workflow_profile(
        client, name=name, mode=raw["mode"], graph=raw["graph"], parameter_map=legacy_map,
        media_transfers=raw.get("media_transfers"), required_nodes=raw["required_nodes"],
        checkpoint_id=raw.get("checkpoint_id"), project_id=project_id,
    )
    with factory() as db:
        result = _register_project_profiles(db, project_id)
    entry = next(row for row in result if row["name"] == name)
    assert entry["status"] == "STANDARD_PARAMETERS_UPDATED"
    profiles = client.get(f"/api/v1/workflow-profiles?project_id={project_id}").json()
    migrated = next(row for row in profiles if row["name"] == name)
    assert migrated["parameter_map"]["width"] == "31.inputs.width"
    assert migrated["parameter_map"]["height"] == "31.inputs.height"
    assert {k: v for k, v in migrated["parameter_map"].items() if k not in ("width", "height")} == legacy_map


def test_the_chosen_frame_reaches_the_h3_node_inputs():
    """The series output spec is only real when it changes the submitted graph."""
    from shine.comfy.workflow import apply_parameters, generic_h3_parameters

    graph = {
        "31": {
            "class_type": "MiniMaxH3ReferenceToVideo",
            "inputs": {"prompt": "", "width": 768, "height": 1344, "duration": 4.0,
                       "ref_images.ref_image_0": ""},
        },
    }
    parameter_map = {
        "positive_prompt": "31.inputs.prompt",
        "duration": "31.inputs.duration",
        "width": "31.inputs.width",
        "height": "31.inputs.height",
        "reference_subject_1": "31.inputs.ref_images.ref_image_0",
    }
    values = generic_h3_parameters(
        prompt="a shot", seed=None, duration_s=4.0,
        references={"reference_subject_1": "shine_a.png"}, width=1344, height=768,
    )
    submitted = apply_parameters(graph, parameter_map, {k: v for k, v in values.items() if k in parameter_map})
    assert submitted["31"]["inputs"]["width"] == 1344
    assert submitted["31"]["inputs"]["height"] == 768
    assert submitted["31"]["inputs"]["prompt"] == "a shot"


def test_every_later_shot_of_a_series_reads_the_same_output_spec(api):
    """The resident Worker builds the next job from the durable series row."""
    from shine.comfyops.production_series import _create_body
    from shine.models import ProductionSeries, Shot

    client, factory, _settings = api
    project_id = create_project(client, "后续镜头规格")
    style = freeze_style(client, project_id)
    asset = upload_asset(client, project_id, content=PNG + b"-later", filename="阿德.png",
                         role="character", entity_id="阿德")
    approve_lock(client, asset["id"])
    shot = create_shot(
        client, project_id, style["id"], title="第二镜", beat="阿德回头。", characters=["阿德"],
        asset_ids=[asset["id"]],
        bindings=[{"asset_id": asset["id"], "use_as": "subject", "role": "阿德", "ordinal": 0}],
    )
    with factory() as db:
        series = ProductionSeries(
            project_id=project_id, provider="comfyui", self_review_scope="full",
            mock_scenario="success", output_spec={"width": 1344, "height": 768, "fps": 24, "clip_seconds": 6},
        )
        body = _create_body(series, db.get(Shot, shot["id"]))
    assert body.output is not None
    assert (body.output.width, body.output.height, body.output.clip_seconds) == (1344, 768, 6)
