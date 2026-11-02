from __future__ import annotations

import json
import sqlite3
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Barrier

import pytest
from fastapi import HTTPException
from pydantic import ValidationError

from alembic import command
from shine.comfy.capabilities import HARD, SOFT, VIDEO, profile_metadata
from shine.comfy.media import ComfyPendingWorkflowMapping
from shine.comfy.provider import ComfyUIRenderService, validate_reference_slots
from shine.comfy.workflow import graph_hash, validate_against_object_info
from shine.models import AgentTaskRecord, utcnow
from shine.operator_review import submit_review
from shine.schemas import ContinuitySettings, RenderRequest
from tests.conftest import create_project
from tests.test_migrations_windows import alembic_config
from tests.test_operator_review import decision, reviewable  # noqa: F401
from tests.test_reference_transfer import make_profile, make_request


@pytest.mark.parametrize(
    "name,expected",
    [
        ("ref2va-2img.json", "6f7a5fb19c548ca945f8a2169e05c836d7c06f03466cf2b893b3be599505d112"),
        ("ref2va-2img-1aud.json", "7d86f7b6d994b0b38cc379a4fe0268f68776c4a43e80fb0f7651a3005f57dcd6"),
    ],
)
def test_real_profile_identity_preserves_entire_sampling_graph(name, expected):
    body = json.loads((Path(__file__).resolve().parents[2] / "config" / "workflows" / name).read_text(encoding="utf-8"))
    metadata = profile_metadata(body["graph"], body["mode"], body["parameter_map"], body["media_transfers"])
    assert metadata["variant"] == "Ref2VA INT8 ConvRot"
    assert body["mode"] == metadata["generation_mode"] == "Ref2VA"
    assert body["checkpoint_id"] == metadata["weight_files"][0]
    assert metadata["graph_sha256"] == expected
    assert metadata["continuity_modes"] == [SOFT]
    assert metadata["video_reference"] == "SUPPORTED_NODE_UNVERIFIED_TRANSFER"
    assert metadata["dynamic_autogrow"] is False
    assert len(metadata["reference_slots"]) == (3 if "1aud" in name else 2)


@pytest.mark.parametrize("mode,capability", [("hard_frame_anchor", HARD), ("video_reference", VIDEO)])
def test_unverified_continuity_is_blocked_before_any_media_upload(tmp_path, mode, capability):
    request = make_request([])
    request.shot_spec.continuity = ContinuitySettings(mode=mode)
    service = ComfyUIRenderService(object(), tmp_path)
    with pytest.raises(ComfyPendingWorkflowMapping) as exc:
        service.prepare(request, make_profile(), {})
    assert exc.value.details["requested_continuity"] == capability


def test_legacy_continuity_request_keeps_its_original_json():
    request = make_request([])
    old = request.model_dump(mode="json")
    old["shot_spec"]["continuity"]["mode"] = "last_frame"
    assert RenderRequest.model_validate(old).model_dump(mode="json") == old


def test_continuity_names_normalize_and_malformed_json_is_validation_error():
    assert ContinuitySettings(mode=SOFT).mode.value == "last_frame"
    assert ContinuitySettings(mode=HARD).mode.value == "hard_frame_anchor"
    assert ContinuitySettings(mode=VIDEO).mode.value == "video_reference"
    with pytest.raises(ValidationError):
        ContinuitySettings.model_validate({"mode": {"forged": "identity"}})


def test_film_runs_with_identical_windows_clock_ticks_show_newest_insertion(api):
    client, factory, _ = api
    project = create_project(client)
    timestamp = utcnow()
    with factory.begin() as db:
        for status in ("SUCCEEDED", "REJECTED"):
            db.add(
                AgentTaskRecord(
                    project_id=project,
                    role_id="cinematographer",
                    input_hash="a" * 64,
                    asset_manifest_hash="b" * 64,
                    rulepack_id="test",
                    rulepack_hash="c" * 64,
                    model_profile_id="test",
                    model_id="fake",
                    status=status,
                    created_at=timestamp,
                )
            )
            db.flush()
    rows = client.get(f"/api/v1/projects/{project}/film-runs").json()
    assert [row["status"] for row in rows] == ["REJECTED", "SUCCEEDED"]


def test_third_image_slot_is_never_dropped():
    from types import SimpleNamespace

    from shine.schemas import ReferenceUse

    manifest = [SimpleNamespace(use_as=ReferenceUse.PICTURE) for _ in range(3)]
    with pytest.raises(ComfyPendingWorkflowMapping) as exc:
        validate_reference_slots({"reference_picture_1": "1.inputs.a", "reference_picture_2": "1.inputs.b"}, manifest)
    assert exc.value.details["missing_reference_slots"] == ["reference_picture_3"]


@pytest.mark.parametrize(
    "field,kind,valid",
    [
        ("ref_images.ref_image_0", "COMFY_AUTOGROW_V3", True),
        ("ref_images.ref_image_8", "COMFY_AUTOGROW_V3", True),
        ("ref_images.ref_image_9", "COMFY_AUTOGROW_V3", False),
        ("ref_images.wrong_0", "COMFY_AUTOGROW_V3", False),
        ("ref_images.ref_image_0", "IMAGE", False),
    ],
)
def test_static_autogrow_paths_obey_actual_ref2va_template(field, kind, valid):
    graph = {"1": {"class_type": "MiniMaxH3ReferenceToVideo", "inputs": {field: ["2", 0]}}}
    inventory = {"MiniMaxH3ReferenceToVideo": {"input": {"optional": {"ref_images": [kind]}}}}
    result = validate_against_object_info(graph, {}, inventory)
    assert (not result["missing_inputs"]) is valid


def test_two_concurrent_operator_decisions_only_one_commits(reviewable):  # noqa: F811
    _, factory, settings, job_id, fake, _ = reviewable
    with factory() as db:
        body = decision(db, settings, job_id)
    barrier = Barrier(2)
    original_history = fake.history

    def synchronized_history(prompt_id):
        barrier.wait(timeout=10)
        return original_history(prompt_id)

    fake.history = synchronized_history

    def run():
        with factory() as db:
            try:
                return submit_review(db, settings, job_id, "test-human", body, client=fake)["status"]
            except HTTPException as exc:
                return exc.status_code

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: run(), range(2)))
    assert sorted(map(str, results)) == ["409", "PASSED"]


def test_v12_database_copy_migration_preserves_graph_and_historical_rows(tmp_path, monkeypatch):
    path = tmp_path / "v12-copy.db"
    monkeypatch.setenv("SHINE_DATABASE_URL", f"sqlite:///{path.as_posix()}")
    monkeypatch.setenv("SHINE_DATA_DIR", str(tmp_path))
    config = alembic_config(path)
    command.upgrade(config, "e8f1a2b3c4d5")
    body = json.loads(
        (Path(__file__).resolve().parents[2] / "config" / "workflows" / "ref2va-2img.json").read_text(encoding="utf-8")
    )
    with sqlite3.connect(path) as db:
        db.execute(
            "INSERT INTO workflow_profiles (id,name,mode,graph,parameter_map,required_nodes,checkpoint_id,"
            "graph_sha256,created_at) VALUES (?,?,?,?,?,?,?,?,?)",
            (
                "legacy",
                "legacy",
                "Ref2VA",
                json.dumps(body["graph"]),
                json.dumps(body["parameter_map"]),
                "[]",
                "MiniMax-H3-Base",
                graph_hash(body["graph"]),
                "2026-10-21",
            ),
        )
    command.upgrade(config, "head")
    with sqlite3.connect(path) as db:
        graph, checkpoint, identity = db.execute(
            "SELECT graph,checkpoint_id,model_identity FROM workflow_profiles WHERE id='legacy'"
        ).fetchone()
        assert json.loads(graph) == body["graph"]
        assert checkpoint == body["checkpoint_id"]
        assert json.loads(identity)["continuity_modes"] == [SOFT]
