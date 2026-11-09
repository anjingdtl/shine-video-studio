from __future__ import annotations

import binascii
import hashlib
import io
import struct
import wave
import zlib
from contextlib import contextmanager
from datetime import timedelta

import pytest
from fastapi import HTTPException
from sqlalchemy import select

from shine.asset_center import execution as asset_execution
from shine.asset_center import service
from shine.asset_center.contracts import (
    build_qwen_edit_graph,
    composite_masked_png,
    cutout_png_by_mask,
    map_qwen_reference_slots,
    validate_aligned_mask,
    validate_image_request,
    validate_rgba_output,
)
from shine.asset_center.models import (
    LibraryAsset,
    LibraryMedia,
    LibraryProjectLink,
    LibraryRenderTask,
    LibraryRights,
    LibrarySourceReference,
    LibraryTaskAttempt,
    LibraryVersion,
    LibraryVoiceProfile,
)
from shine.comfyops.models import (
    PLAN_PURPOSE_ASSET_PREPARATION,
    AssetRequirement,
    PreProductionPlan,
    ResourceLease,
    RightsRecord,
    VoiceProfile,
)
from shine.materialprep import runtime as image_runtime
from shine.models import Asset, Project, utcnow

pytestmark = pytest.mark.integration


def _chunk(kind: bytes, body: bytes) -> bytes:
    return struct.pack(">I", len(body)) + kind + body + struct.pack(">I", binascii.crc32(kind + body) & 0xFFFFFFFF)


def _png(width: int, rows: list[list[tuple[int, int, int, int]]], *, rgba: bool = True) -> bytes:
    color_type = 6 if rgba else 2
    channels = 4 if rgba else 3
    scanlines = bytearray()
    for row in rows:
        scanlines.append(0)
        for pixel in row:
            scanlines.extend(pixel[:channels])
    header = struct.pack(">IIBBBBB", width, len(rows), 8, color_type, 0, 0, 0)
    return (
        b"\x89PNG\r\n\x1a\n"
        + _chunk(b"IHDR", header)
        + _chunk(b"IDAT", zlib.compress(bytes(scanlines)))
        + _chunk(b"IEND", b"")
    )


def _rights_upload(client, png: bytes):
    return client.post(
        "/api/v1/asset-center/uploads",
        data={
            "asset_kind": "image",
            "purpose": "source",
            "source_type": "authorized_local_upload",
            "license_kind": "USER_OWNED_FIXTURE",
            "rights_statement": "Synthetic test fixture owned by the test author.",
            "subject": "test fixture",
            "allowed_project_ids": "[]",
            "permitted_uses": '["research"]',
            "commercial_allowed": "false",
        },
        files={"file": ("source.png", png, "image/png")},
    )


def _mask_upload(client, png: bytes):
    return client.post(
        "/api/v1/asset-center/uploads",
        data={
            "asset_kind": "image",
            "purpose": "mask",
            "source_type": "authorized_local_upload",
            "license_kind": "USER_OWNED_FIXTURE",
            "rights_statement": "Synthetic test fixture owned by the test author.",
            "subject": "test fixture mask",
            "allowed_project_ids": "[]",
            "permitted_uses": '["research"]',
            "commercial_allowed": "false",
        },
        files={"file": ("mask.png", png, "image/png")},
    )


def _wav_fixture() -> bytes:
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as output:
        output.setnchannels(1)
        output.setsampwidth(2)
        output.setframerate(16000)
        output.writeframes(b"\x00\x00" * 16000)
    return buffer.getvalue()


def test_projectless_import_persists_and_asset_metadata_uses_compare_and_swap(api):
    client, factory, _settings = api
    png = _png(2, [[(20, 40, 60, 255), (60, 80, 100, 255)]])

    uploaded = _rights_upload(client, png)
    assert uploaded.status_code == 201, uploaded.text
    media_id = uploaded.json()["id"]
    created = client.post(
        "/api/v1/asset-center/assets",
        json={"title": "雨伞", "kind": "image", "category": "prop", "tags": ["道具"], "media_id": media_id},
    )
    assert created.status_code == 201, created.text
    asset = created.json()
    assert asset["current_version"] is None
    assert asset["versions"][0]["status"] == "CANDIDATE"
    assert asset["versions"][0]["is_mock"] is False

    listing = client.get("/api/v1/asset-center/assets?q=雨伞")
    assert listing.status_code == 200
    assert [row["id"] for row in listing.json()["items"]] == [asset["id"]]
    fetched = client.get(f"/api/v1/asset-center/assets/{asset['id']}")
    assert fetched.status_code == 200
    assert fetched.json()["versions"][0]["content_sha256"] == uploaded.json()["sha256"]
    with factory() as db:
        assert db.scalar(select(Project.id).limit(1)) is None

    changed = client.patch(
        f"/api/v1/asset-center/assets/{asset['id']}",
        json={"expected_state_version": asset["state_version"], "title": "雨伞·蓝色", "favorite": True},
    )
    assert changed.status_code == 200, changed.text
    assert changed.json()["title"] == "雨伞·蓝色"
    stale = client.patch(
        f"/api/v1/asset-center/assets/{asset['id']}",
        json={"expected_state_version": asset["state_version"], "title": "错误覆盖"},
    )
    assert stale.status_code == 409
    assert stale.json()["detail"]["code"] == "LIBRARY_ASSET_CHANGED"


def test_rights_approval_rejects_stale_snapshot_and_research_license_cannot_be_commercial(api):
    client, factory, _settings = api
    uploaded = _rights_upload(client, _png(1, [[(1, 2, 3, 255)]]))
    rights_id = uploaded.json()["rights_id"]
    with factory() as db:
        rights = db.get(LibraryRights, rights_id)
        snapshot = service.canonical_hash(service.rights_snapshot(rights))

    stale = client.post(
        f"/api/v1/asset-center/rights/{rights_id}/approve",
        headers={"X-Shine-Local-Action": "1"},
        json={
            "expected_snapshot": "0" * 64,
            "evidence": "verified fixture ownership",
            "allowed_project_ids": [],
            "permitted_uses": ["research"],
            "commercial_allowed": False,
        },
    )
    assert stale.status_code == 409
    assert stale.json()["detail"]["code"] == "LIBRARY_RIGHTS_CHANGED"

    approved = client.post(
        f"/api/v1/asset-center/rights/{rights_id}/approve",
        headers={"X-Shine-Local-Action": "1"},
        json={
            "expected_snapshot": snapshot,
            "evidence": "verified fixture ownership",
            "allowed_project_ids": [],
            "permitted_uses": ["research"],
            "commercial_allowed": False,
        },
    )
    assert approved.status_code == 200, approved.text

    with factory.begin() as db:
        rights = db.get(LibraryRights, rights_id)
        rights.license_kind = "QWEN_RESEARCH_NONCOMMERCIAL"
        current = service.canonical_hash(service.rights_snapshot(rights))
    refused = client.post(
        f"/api/v1/asset-center/rights/{rights_id}/approve",
        headers={"X-Shine-Local-Action": "1"},
        json={
            "expected_snapshot": current,
            "evidence": "research-only model license",
            "allowed_project_ids": ["*"],
            "permitted_uses": ["research", "commercial"],
            "commercial_allowed": True,
        },
    )
    assert refused.status_code == 409
    assert refused.json()["detail"]["code"] == "MODEL_LICENSE_NONCOMMERCIAL"


def test_revoked_global_rights_cannot_be_restored_by_scope_patch(api):
    client, _factory, _settings = api
    uploaded = _rights_upload(client, _png(1, [[(4, 5, 6, 255)]]))
    rights_id = uploaded.json()["rights_id"]
    revoked = client.post(
        f"/api/v1/asset-center/rights/{rights_id}/revoke?evidence=owner%20withdrew%20permission",
        headers={"X-Shine-Local-Action": "1"},
    )
    assert revoked.status_code == 200, revoked.text
    restored = client.patch(
        f"/api/v1/asset-center/rights/{rights_id}",
        headers={"X-Shine-Local-Action": "1"},
        json={
            "expected_snapshot": "0" * 64,
            "evidence": "new scope request after revocation",
            "allowed_project_ids": ["*"],
            "permitted_uses": ["research"],
            "commercial_allowed": False,
            "expires_at": None,
        },
    )
    assert restored.status_code == 409
    assert restored.json()["detail"]["code"] == "LIBRARY_RIGHTS_REVOKED"


def test_derived_version_cannot_expand_or_outlive_source_project_rights(api):
    _client, factory, _settings = api
    source_sha = "1" * 64
    with factory.begin() as db:
        source_rights = LibraryRights(
            source_type="authorized_local_upload",
            license_kind="PROJECT_A_RESEARCH_ONLY",
            scope="asset",
            statement="Authorized for project A research use.",
            subject="synthetic fixture",
            allowed_project_ids=["project-a"],
            permitted_uses=["research"],
            commercial_allowed=False,
            status="APPROVED",
            recorded_actor="fixture",
            approved_by="fixture",
        )
        source_asset = LibraryAsset(title="source", kind="image", category="character", created_by="fixture")
        output_asset = LibraryAsset(title="derived", kind="image", category="character", created_by="fixture")
        db.add_all([source_rights, source_asset, output_asset])
        db.flush()
        source = LibraryVersion(
            library_asset_id=source_asset.id,
            ordinal=1,
            filename="source.png",
            kind="image",
            content_type="image/png",
            content_sha256=source_sha,
            storage_path="unused/source.png",
            byte_size=10,
            source_mode="UPLOAD",
            rights_id=source_rights.id,
            status="PROMOTED",
            created_by="fixture",
        )
        output_rights = LibraryRights(
            source_type="qwen_derived_output",
            license_kind="QWEN_RESEARCH_WITH_SOURCE_RESTRICTIONS",
            scope="asset",
            statement="Research only; source scope inherited.",
            subject="synthetic derived fixture",
            allowed_project_ids=["project-a"],
            permitted_uses=["research"],
            commercial_allowed=False,
            status="APPROVED",
            recorded_actor="fixture",
            approved_by="fixture",
        )
        db.add_all([source, output_rights])
        db.flush()
        output = LibraryVersion(
            library_asset_id=output_asset.id,
            ordinal=1,
            filename="derived.png",
            kind="image",
            content_type="image/png",
            content_sha256="2" * 64,
            storage_path="unused/derived.png",
            byte_size=10,
            source_mode="I2I",
            rights_id=output_rights.id,
            status="PROMOTED",
            created_by="fixture",
        )
        db.add(output)
        db.flush()
        db.add(
            LibrarySourceReference(
                version_id=output.id,
                ordinal=0,
                slot_role="canvas",
                source_version_id=source.id,
                sha256=source_sha,
                rights_snapshot=service.rights_snapshot(source_rights),
            )
        )
        output_id = output.id
        output_rights_id = output_rights.id

    with factory() as db:
        output = db.get(LibraryVersion, output_id)
        output_rights = db.get(LibraryRights, output_rights_id)
        approval_snapshot = service.version_snapshot(output, output_rights)
        output_rights.statement = "Changed while review grant is pending."
        assert service.version_snapshot(output, output_rights) != approval_snapshot
        output_rights.statement = "Research only; source scope inherited."
        source_reference = db.scalar(
            select(LibrarySourceReference).where(LibrarySourceReference.version_id == output_id)
        )
        source = db.get(LibraryVersion, source_reference.source_version_id)
        source_rights = db.get(LibraryRights, source.rights_id)
        inherited = asset_execution._new_image_output_rights(
            db,
            {
                "references": [
                    {
                        "version_id": source.id,
                        "sha256": source.content_sha256,
                        "rights_snapshot": service.rights_snapshot(source_rights),
                    }
                ]
            },
        )
        assert inherited.status == "DECLARED"
        assert inherited.allowed_project_ids == ["project-a"]
        assert inherited.permitted_uses == ["research"]
        assert inherited.commercial_allowed is False
        service.require_version_rights(db, output, project_id="project-a", usage_scope="research")
        with pytest.raises(HTTPException) as denied:
            service.require_version_rights(db, output, project_id="project-b", usage_scope="research")
        assert denied.value.detail["code"] == "LIBRARY_PROJECT_NOT_AUTHORIZED"
        service.enforce_rights_ceiling(
            db,
            db.get(LibraryRights, output_rights_id),
            allowed_project_ids=["project-a"],
            permitted_uses=["research"],
            commercial_allowed=False,
        )
        with pytest.raises(HTTPException) as widened:
            service.enforce_rights_ceiling(
                db,
                db.get(LibraryRights, output_rights_id),
                allowed_project_ids=["*"],
                permitted_uses=["research"],
                commercial_allowed=False,
            )
        assert widened.value.detail["code"] == "RIGHTS_SCOPE_EXCEEDS_SOURCE"

    with factory.begin() as db:
        db.get(LibraryRights, source_rights.id).status = "REVOKED"
        db.get(LibraryRights, source_rights.id).revoked_at = utcnow()
    with factory() as db:
        with pytest.raises(HTTPException) as revoked:
            service.require_version_rights(db, db.get(LibraryVersion, output_id), project_id="project-a")
        assert revoked.value.status_code == 409


def test_image_contracts_preserve_all_reference_slots_and_reject_fake_inputs():
    references = [
        {"media_id": f"media-{index}", "order": index, "role": "subject" if index == 0 else "garment"}
        for index in range(10)
    ]
    slots = map_qwen_reference_slots(references)
    assert len(slots) == 10
    assert slots["images.image_1"]["media_id"] == "media-0"
    assert slots["images.image_10"]["media_id"] == "media-9"
    graph = build_qwen_edit_graph(
        prompt="keep the person's face and apply the selected clothing",
        uploaded_names=[f"input-{index}.png" for index in range(10)],
        width=1024,
        height=1024,
        seed=17,
    )
    assert graph["474"]["inputs"]["images.image_10"] == ["709", 0]
    assert graph["709"]["inputs"]["image"] == "input-9.png"
    assert not any("mask" in key.lower() for node in graph.values() for key in node.get("inputs", {}))
    assert validate_image_request(
        {"mode": "I2I", "prompt": "revise", "references": [{"version_id": "version-1", "order": 0, "role": "canvas"}]}
    ) == [{"version_id": "version-1", "order": 0, "role": "canvas"}]

    with pytest.raises(HTTPException) as err:
        validate_image_request({"mode": "T2I", "prompt": "sunset", "references": references[:1]})
    assert err.value.detail["code"] == "T2I_REFERENCES_NOT_ALLOWED"
    with pytest.raises(HTTPException) as err:
        validate_image_request({"mode": "I2I", "prompt": "repaint", "references": []})
    assert err.value.detail["code"] == "I2I_SOURCE_REQUIRED"
    with pytest.raises(HTTPException) as err:
        validate_image_request(
            {
                "mode": "I2I",
                "prompt": "repaint",
                "references": [
                    {"media_id": "first", "order": 0, "role": "canvas"},
                    {"media_id": "third", "order": 2, "role": "prop"},
                ],
            }
        )
    assert err.value.detail["code"] == "REFERENCE_ORDER_GAP"
    assert "从 0" in err.value.detail["message"]
    with pytest.raises(HTTPException) as err:
        validate_image_request(
            {
                "mode": "MULTIREF",
                "prompt": "combine",
                "references": [
                    {"media_id": "garment", "order": 0, "role": "garment"},
                    {"media_id": "subject", "order": 1, "role": "subject"},
                ],
            }
        )
    assert err.value.detail["code"] == "MULTIREF_EDIT_TARGET_REQUIRED"
    with pytest.raises(HTTPException) as err:
        validate_image_request(
            {
                "mode": "WARDROBE",
                "prompt": "dress the person",
                "references": [
                    {"media_id": "garment", "order": 0, "role": "garment"},
                    {"media_id": "subject", "order": 1, "role": "subject"},
                ],
            }
        )
    assert err.value.detail["code"] == "WARDROBE_SUBJECT_MUST_BE_FIRST"
    with pytest.raises(HTTPException) as err:
        validate_image_request(
            {
                "mode": "MULTIREF",
                "prompt": "combine",
                "references": references + [{"media_id": "extra", "order": 10, "role": "prop"}],
            }
        )
    assert err.value.detail["code"] == "REFERENCE_COUNT_OUT_OF_RANGE"
    with pytest.raises(HTTPException) as err:
        validate_image_request(
            {
                "mode": "INPAINT",
                "prompt": "edit this region",
                "references": [
                    {"media_id": f"image-{index}", "order": index, "role": "canvas" if index == 0 else "prop"}
                    for index in range(10)
                ],
                "mask_media_id": "mask-1",
            }
        )
    assert err.value.detail["code"] == "INPAINT_TOTAL_INPUT_LIMIT"


def test_edit_adapter_uploads_every_reference_byte_and_preserves_slot_order(monkeypatch):
    class FakeClient:
        def __init__(self):
            self.uploads = []

        def upload_image(self, filename, content, **kwargs):
            self.uploads.append((filename, content, kwargs))
            return {"name": filename}

        def object_info(self):
            return {}

    monkeypatch.setattr(
        "shine.comfy.workflow.validate_against_object_info",
        lambda graph, parameter_map, object_info: {"missing_nodes": [], "missing_inputs": []},
    )
    references = [
        {"order": index, "role": "subject" if index == 0 else "accessory", "sha256": f"{index:064x}"}
        for index in range(10)
    ]
    contents = [(f"image-bytes-{index}".encode(), "image/png") for index in range(10)]
    client = FakeClient()
    graph = asset_execution._edit_graph(
        client,
        {"mode": "MULTIREF", "prompt": "combine these", "references": references},
        1024,
        1024,
        7,
        contents,
    )
    assert len(client.uploads) == 10
    assert [item[1] for item in client.uploads] == [item[0] for item in contents]
    assert all(item[2]["subfolder"] == "" for item in client.uploads)
    assert graph["474"]["inputs"]["images.image_1"] == ["700", 0]
    assert graph["474"]["inputs"]["images.image_10"] == ["709", 0]
    assert graph["474"]["inputs"]["prompt"].endswith("<image10> is the accessory reference")

    canvas = {"order": 0, "role": "canvas", "sha256": "1" * 64}
    mask = {"order": 1, "role": "mask_annotation", "sha256": "2" * 64}
    mask_contents = [(b"actual-canvas-bytes", "image/png"), (b"actual-mask-bytes", "image/png")]
    mask_client = FakeClient()
    inpaint_graph = asset_execution._edit_graph(
        mask_client,
        {
            "mode": "INPAINT",
            "prompt": "edit the white mask area",
            "references": [canvas],
            "graph_references": [canvas, mask],
            "mask": {"sha256": mask["sha256"]},
        },
        1024,
        1024,
        8,
        mask_contents,
    )
    assert [row[1] for row in mask_client.uploads] == [b"actual-canvas-bytes", b"actual-mask-bytes"]
    assert inpaint_graph["474"]["inputs"]["images.image_2"] == ["701", 0]
    assert "white pixels are selected for editing" in inpaint_graph["474"]["inputs"]["prompt"]


def test_mask_pixel_contract_and_rgba_validation_are_byte_level():
    mask = _png(2, [[(255, 255, 255, 255), (0, 0, 0, 255)]])
    report = validate_aligned_mask(2, 1, mask)
    assert report["semantics"] == "white=edit,black=preserve"
    assert report["changed_pixels"] == report["retained_pixels"] == 1
    assert report["sha256"]
    with pytest.raises(HTTPException) as err:
        validate_aligned_mask(3, 1, mask)
    assert err.value.detail["code"] == "MASK_DIMENSIONS_MISMATCH"
    with pytest.raises(HTTPException) as err:
        validate_aligned_mask(2, 1, _png(2, [[(0, 0, 0, 255), (0, 0, 0, 255)]]))
    assert err.value.detail["code"] == "MASK_EMPTY"
    with pytest.raises(HTTPException) as err:
        validate_aligned_mask(2, 1, _png(2, [[(255, 255, 255, 255), (255, 255, 255, 255)]]))
    assert err.value.detail["code"] == "MASK_FULL_FRAME"
    assert validate_rgba_output(_png(2, [[(2, 3, 4, 255), (5, 6, 7, 120)]]))["partial_alpha_pixels"] == 1
    with pytest.raises(HTTPException) as err:
        validate_rgba_output(_png(1, [[(1, 2, 3)]], rgba=False))
    assert err.value.detail["code"] == "RGBA_ALPHA_CHANNEL_MISSING"

    original = _png(2, [[(10, 20, 30, 255), (40, 50, 60, 255)]])
    generated = _png(2, [[(100, 110, 120, 255), (130, 140, 150, 255)]])
    combined = composite_masked_png(original, generated, mask)
    from shine.asset_center.contracts import _decode_png_rows

    width, height, channels, pixels = _decode_png_rows(combined)
    assert (width, height, channels) == (2, 1, 4)
    assert pixels[0] == bytes((100, 110, 120, 255, 40, 50, 60, 255))

    cutout = cutout_png_by_mask(original, mask)
    assert validate_rgba_output(cutout)["transparent_pixels"] == 1
    _, _, _, cutout_rows = _decode_png_rows(cutout)
    assert cutout_rows[0] == bytes((10, 20, 30, 255, 40, 50, 60, 0))


def test_inpaint_upload_uses_actual_mask_bytes_and_persists_pixel_contract(api):
    client, factory, _settings = api
    source_rows = [[(20, 40, 60, 255)] * 256 for _ in range(256)]
    mask_rows = [[(255, 255, 255, 255)] * 128 + [(0, 0, 0, 255)] * 128 for _ in range(256)]
    source = _rights_upload(client, _png(256, source_rows))
    mask = _mask_upload(client, _png(256, mask_rows))
    assert source.status_code == mask.status_code == 201
    with factory.begin() as db:
        for row_id in (source.json()["rights_id"], mask.json()["rights_id"]):
            rights = db.get(LibraryRights, row_id)
            rights.status = "APPROVED"
            rights.allowed_project_ids = ["*"]
            rights.permitted_uses = ["research"]

    response = client.post(
        "/api/v1/asset-center/image-jobs",
        json={
            "idempotency_key": "inpaint-mask-input-0001",
            "mode": "INPAINT",
            "prompt": "replace the selected region with a blue tile",
            "references": [{"media_id": source.json()["id"], "order": 0, "role": "canvas"}],
            "mask_media_id": mask.json()["id"],
            "output": {"width": 256, "height": 256},
            "category": "prop",
        },
    )
    assert response.status_code == 202, response.text
    with factory() as db:
        task = db.scalar(
            select(LibraryRenderTask).where(LibraryRenderTask.idempotency_key == "inpaint-mask-input-0001:1")
        )
        assert task is not None
        assert task.payload["mask"]["sha256"] == hashlib.sha256(_png(256, mask_rows)).hexdigest()
        assert task.payload["mask"]["sha256"] != source.json()["sha256"]
        assert task.payload["mask"]["changed_pixels"] == task.payload["mask"]["retained_pixels"] == 32768
        assert task.payload["graph_references"][1]["role"] == "mask_annotation"
        assert task.payload["reference_slot_map"]["images.image_2"]["sha256"] == task.payload["mask"]["sha256"]


def test_rgba_manual_cutout_runs_as_local_worker_candidate_without_qwen_or_gpu(api):
    client, factory, settings = api
    source_rows = [[(20, 40, 60, 255)] * 256 for _ in range(256)]
    mask_rows = [[(255, 255, 255, 255)] * 128 + [(0, 0, 0, 255)] * 128 for _ in range(256)]
    source = _rights_upload(client, _png(256, source_rows))
    mask = _mask_upload(client, _png(256, mask_rows))
    with factory.begin() as db:
        for row_id in (source.json()["rights_id"], mask.json()["rights_id"]):
            rights = db.get(LibraryRights, row_id)
            rights.status = "APPROVED"
            rights.allowed_project_ids = ["*"]
            rights.permitted_uses = ["research"]

    queued = client.post(
        "/api/v1/asset-center/image-jobs",
        json={
            "idempotency_key": "manual-cutout-worker-0001",
            "mode": "RGBA",
            "references": [{"media_id": source.json()["id"], "order": 0, "role": "canvas"}],
            "mask_media_id": mask.json()["id"],
            "output": {"width": 256, "height": 256},
            "category": "prop",
        },
    )
    assert queued.status_code == 202, queued.text
    task_id = queued.json()["items"][0]["id"]
    assert queued.json()["capability"]["operation"] == "MASK_CUTOUT_V1"
    assert asset_execution.run_one_pending_job(factory, settings, "test-worker") == 1

    with factory() as db:
        task = db.get(LibraryRenderTask, task_id)
        assert task.status == "CANDIDATE_READY"
        assert task.external_outcome == "NOT_SENT"
        assert task.external_prompt_id is None
        version = db.get(LibraryVersion, task.result_version_id)
        assert version is not None and version.status == "READY_FOR_REVIEW"
        assert version.provenance["template_commit"] == "MASK_CUTOUT_V1"
        assert version.rights_id != source.json()["rights_id"]
        output_rights = db.get(LibraryRights, version.rights_id)
        assert output_rights.license_kind == "USER_OWNED_FIXTURE"
        assert output_rights.permitted_uses == ["research"]
        sources = db.scalars(
            select(LibrarySourceReference).where(LibrarySourceReference.version_id == version.id)
        ).all()
        assert {row.slot_role for row in sources} == {"canvas", "mask"}
        output = service.version_bytes(settings, version)
        assert validate_rgba_output(output)["transparent_pixels"] == 256 * 128


def test_projectless_image_job_is_persisted_but_never_claimed_as_ready(api):
    client, factory, _settings = api
    response = client.post(
        "/api/v1/asset-center/image-jobs",
        json={
            "idempotency_key": "projectless-t2i-0001",
            "mode": "T2I",
            "prompt": "a paper lantern",
            "category": "other",
        },
    )
    assert response.status_code == 202, response.text
    task = response.json()["items"][0]
    assert task["status"] == "BLOCKED_CAPABILITY"
    assert task["external_outcome"] == "NOT_SENT"
    with factory() as db:
        stored = db.get(LibraryRenderTask, task["id"])
        assert stored is not None and stored.submit_intent_at is None
        assert db.scalar(select(Project.id).limit(1)) is None


def test_image_and_voice_request_retries_with_same_idempotency_key_reuse_task(api):
    client, factory, _settings = api
    image_body = {
        "idempotency_key": "image-request-recover-0001",
        "mode": "T2I",
        "prompt": "a blue paper lantern",
        "category": "prop",
    }
    image_first = client.post("/api/v1/asset-center/image-jobs", json=image_body)
    image_retry = client.post("/api/v1/asset-center/image-jobs", json=image_body)
    assert image_first.status_code == image_retry.status_code == 202
    assert image_first.json()["items"][0]["id"] == image_retry.json()["items"][0]["id"]

    voice_body = {
        "idempotency_key": "voice-request-recover-0001",
        "mode": "DESIGN",
        "title": "Narrator",
        "description": "warm narrator",
        "text": "hello there",
        "character": "narrator",
    }
    voice_first = client.post("/api/v1/asset-center/voice-jobs", json=voice_body)
    voice_retry = client.post("/api/v1/asset-center/voice-jobs", json=voice_body)
    assert voice_first.status_code == voice_retry.status_code == 202
    assert voice_first.json()["item"]["id"] == voice_retry.json()["item"]["id"]
    assert "没有重复创建" in voice_retry.json()["notice"]

    voice_conflict = client.post(
        "/api/v1/asset-center/voice-jobs",
        json={**voice_body, "text": "different text"},
    )
    assert voice_conflict.status_code == 409
    assert voice_conflict.json()["detail"]["code"] == "IDEMPOTENCY_KEY_REUSED"
    with factory() as db:
        image_key = "image-request-recover-0001:1"
        voice_key = "voice-request-recover-0001:1"
        assert db.scalar(select(LibraryRenderTask).where(LibraryRenderTask.idempotency_key == image_key))
        assert db.scalar(select(LibraryRenderTask).where(LibraryRenderTask.idempotency_key == voice_key))


def test_upload_rejects_mime_signature_mismatch_before_creating_media(api):
    client, factory, _settings = api
    response = client.post(
        "/api/v1/asset-center/uploads",
        data={
            "asset_kind": "image",
            "purpose": "source",
            "source_type": "authorized_local_upload",
            "license_kind": "USER_OWNED_FIXTURE",
            "rights_statement": "Synthetic test fixture owned by the test author.",
        },
        files={"file": ("portrait.png", b"not a PNG image", "image/png")},
    )
    assert response.status_code == 415
    with factory() as db:
        assert db.scalar(select(LibraryMedia.id).limit(1)) is None


@pytest.mark.parametrize(
    ("rights_status", "expired", "error_code"),
    [
        ("REVOKED", False, "LIBRARY_RIGHTS_NOT_APPROVED"),
        ("APPROVED", True, "LIBRARY_RIGHTS_EXPIRED"),
    ],
)
def test_authorized_voice_clone_rejects_revoked_or_expired_consent(api, rights_status, expired, error_code):
    client, factory, _settings = api
    with factory.begin() as db:
        rights = LibraryRights(
            source_type="speaker_consent",
            license_kind="VOICE_SAMPLE_CONSENT",
            scope="voice_clone",
            statement="Synthetic authorized clone fixture.",
            subject="consenting test speaker",
            allowed_project_ids=["*"],
            permitted_uses=["research"],
            status=rights_status,
            revoked_at=utcnow() if rights_status == "REVOKED" else None,
            expires_at=utcnow() - timedelta(days=1) if expired else utcnow() + timedelta(days=1),
            recorded_actor="test-user",
            approved_by="test-user" if rights_status == "APPROVED" else None,
        )
        db.add(rights)
        db.flush()
        media = LibraryMedia(
            kind="audio",
            purpose="voice_sample",
            filename="speaker.wav",
            content_type="audio/wav",
            sha256="a" * 64,
            storage_path="unused-test-fixture.wav",
            byte_size=128,
            rights_id=rights.id,
            created_by="test-user",
        )
        db.add(media)
        db.flush()
        media_id, rights_id = media.id, rights.id

    response = client.post(
        "/api/v1/asset-center/voice-jobs",
        json={
            "idempotency_key": f"voice-clone-{rights_status.lower()}-{expired}",
            "mode": "AUTHORIZED_CLONE",
            "title": "授权测试音色",
            "character": "测试说话人",
            "reference_text": "你好，欢迎来到测试。",
            "text": "你好，欢迎来到测试。",
            "reference_media_id": media_id,
            "rights_id": rights_id,
        },
    )
    assert response.status_code == 409
    assert response.json()["detail"]["code"] == error_code
    with factory() as db:
        assert db.scalar(select(LibraryRenderTask.id).limit(1)) is None


def test_authorized_voice_sample_can_be_reviewed_frozen_and_assigned_with_new_project_identity(api):
    client, factory, _settings = api
    local_action = {"X-Shine-Local-Action": "1"}
    project_id = client.post("/api/v1/projects", json={"title": "声音复用测试项目"}).json()["id"]
    transcript = "你好，欢迎来到这个声音测试。"
    uploaded = client.post(
        "/api/v1/asset-center/uploads",
        data={
            "asset_kind": "audio",
            "purpose": "voice_sample",
            "source_type": "speaker_consent",
            "license_kind": "VOICE_SAMPLE_CONSENT",
            "rights_statement": "合成测试录音，仅用于授权声音流程验收。",
            "subject": "合成测试说话人",
            "allowed_project_ids": '["*"]',
            "permitted_uses": '["research"]',
            "commercial_allowed": "false",
        },
        files={"file": ("speaker.wav", _wav_fixture(), "audio/wav")},
    )
    assert uploaded.status_code == 201, uploaded.text
    asset = client.post(
        "/api/v1/asset-center/assets",
        json={
            "title": "授权测试音色",
            "kind": "audio",
            "category": "voice",
            "media_id": uploaded.json()["id"],
            "source_mode": "AUTHORIZED_CLONE",
            "prompt": transcript,
        },
    )
    assert asset.status_code == 201, asset.text
    version_id = asset.json()["versions"][0]["id"]
    rights_id = uploaded.json()["rights_id"]
    with factory() as db:
        snapshot = service.canonical_hash(service.rights_snapshot(db.get(LibraryRights, rights_id)))
    approved = client.post(
        f"/api/v1/asset-center/rights/{rights_id}/approve",
        headers=local_action,
        json={
            "expected_snapshot": snapshot,
            "evidence": "合成测试录音的说话人授权有效。",
            "allowed_project_ids": ["*"],
            "permitted_uses": ["research"],
            "commercial_allowed": False,
        },
    )
    assert approved.status_code == 200, approved.text
    checked = client.post(f"/api/v1/asset-center/versions/{version_id}/qc")
    assert checked.status_code == 200 and checked.json()["status"] == "READY_FOR_REVIEW", checked.text
    grant = client.post(f"/api/v1/asset-center/versions/{version_id}/grant", headers=local_action)
    promoted = client.post(
        f"/api/v1/asset-center/versions/{version_id}/promote",
        headers=local_action,
        json={
            "grant_id": grant.json()["grant_id"],
            "snapshot": grant.json()["snapshot"],
            "evidence": "核对合成录音候选。",
        },
    )
    assert promoted.status_code == 200, promoted.text
    voice = client.get("/api/v1/asset-center/voice-profiles").json()["items"][0]
    assert voice["status"] == "PREVIEW"
    with factory() as db:
        profile = db.get(LibraryVoiceProfile, voice["id"])
        assert profile.source == "AUTHORIZED_CLONE"
        assert profile.conditioning_media_id == uploaded.json()["id"]
        assert db.get(LibraryVersion, version_id).prompt == transcript
    voice_grant = client.post(f"/api/v1/asset-center/voice-profiles/{voice['id']}/grant", headers=local_action)
    frozen = client.post(
        f"/api/v1/asset-center/voice-profiles/{voice['id']}/freeze",
        headers=local_action,
        json={
            "grant_id": voice_grant.json()["grant_id"],
            "snapshot": voice_grant.json()["snapshot"],
            "listened": True,
            "evidence": "已试听并确认测试音色。",
        },
    )
    assert frozen.status_code == 200 and frozen.json()["status"] == "FROZEN", frozen.text

    assigned = client.post(
        "/api/v1/asset-center/assignments",
        headers=local_action,
        json={
            "project_id": project_id,
            "library_version_id": version_id,
            "role": "voice",
            "entity_id": "旁白甲",
            "usage_scope": "research",
            "idempotency_key": "authorized-voice-project-use-01",
            "confirmed": True,
        },
    )
    assert assigned.status_code == 201, assigned.text
    assert assigned.json()["project_voice_profile_id"] != voice["id"]
    with factory() as db:
        local_profile = db.get(VoiceProfile, assigned.json()["project_voice_profile_id"])
        assert local_profile.project_id == project_id
        assert local_profile.payload["shine_assets_library_version_id"] == version_id
        assert db.scalar(
            select(LibraryRenderTask.id).where(
                LibraryRenderTask.idempotency_key == "frozen-global-voice-tts-01:1"
            )
        ) is None
    tts = client.post(
        "/api/v1/asset-center/voice-jobs",
        json={
            "idempotency_key": "frozen-global-voice-tts-01",
            "mode": "TTS",
            "title": "旁白测试对白",
            "character": "旁白甲",
            "text": "早上好。",
            "voice_profile_id": voice["id"],
        },
    )
    assert tts.status_code == 202, tts.text
    with factory() as db:
        task = db.get(LibraryRenderTask, tts.json()["item"]["id"])
        assert task.mode == "TTS"
        assert task.payload["voice_profile_id"] == voice["id"]


def test_runtime_fingerprint_drift_invalidates_previously_verified_t2i(monkeypatch, api):
    from types import SimpleNamespace

    client, factory, _settings = api
    old_fingerprint = "a" * 64
    graph_sha = "b" * 64
    digest = "c" * 64
    with factory.begin() as db:
        rights = LibraryRights(
            source_type="authorized_local_upload",
            license_kind="USER_OWNED_FIXTURE",
            scope="asset",
            statement="Synthetic test fixture owned by the test author.",
            allowed_project_ids=["*"],
            permitted_uses=["research"],
            status="APPROVED",
            recorded_actor="test-user",
            approved_by="test-user",
        )
        asset = LibraryAsset(title="验证图", kind="image", category="prop", created_by="test-user")
        db.add_all([rights, asset])
        db.flush()
        version = LibraryVersion(
            library_asset_id=asset.id,
            ordinal=1,
            filename="verified.png",
            kind="image",
            content_type="image/png",
            content_sha256=digest,
            storage_path="unused-test-fixture.png",
            byte_size=64,
            width=16,
            height=16,
            source_mode="T2I",
            rights_id=rights.id,
            provenance={},
            qc={"technical": "pass", "sha256": digest},
            status="READY_FOR_REVIEW",
            is_mock=False,
            created_by="test-user",
        )
        db.add(version)
        db.flush()
        task = LibraryRenderTask(
            idempotency_key="fingerprint-drift-task:1",
            request_hash="d" * 64,
            kind="image",
            mode="T2I",
            payload={"validation_only": True, "category": "prop"},
            workflow_revision_hash=graph_sha,
            runtime_fingerprint=old_fingerprint,
            status="CANDIDATE_READY",
            external_outcome="SUCCEEDED",
            result_version_id=version.id,
        )
        db.add(task)
        db.flush()
        db.add(
            LibraryTaskAttempt(
                task_id=task.id,
                attempt_no=1,
                snapshot_hash="e" * 64,
                submit_stage="OUTPUT_STORED",
                status="SUCCEEDED",
                external_outcome="SUCCEEDED",
                output_sha256=digest,
                payload={"renderer_owned": True, "graph_sha256": graph_sha},
            )
        )

    monkeypatch.setattr(
        image_runtime,
        "load_config",
        lambda _settings: SimpleNamespace(base_url="", instance_id=None),
    )
    monkeypatch.setattr(
        image_runtime,
        "readiness",
        lambda *_args, **_kwargs: {
            "layer": image_runtime.LAYER_RENDER_VERIFIED,
            "fingerprint": old_fingerprint,
            "graph_sha256": graph_sha,
            "output_node_id": "461",
            "families": {},
            "blockers": [],
        },
    )
    valid = client.get("/api/v1/asset-center/capabilities").json()
    assert valid["image_capabilities"]["QWEN_IMAGE_T2I"]["can_generate"] is True

    monkeypatch.setattr(
        image_runtime,
        "readiness",
        lambda *_args, **_kwargs: {
            "layer": image_runtime.LAYER_RENDER_VERIFIED,
            "fingerprint": "f" * 64,
            "graph_sha256": graph_sha,
            "output_node_id": "461",
            "families": {},
            "blockers": [],
        },
    )
    drifted = client.get("/api/v1/asset-center/capabilities").json()
    assert drifted["image_capabilities"]["QWEN_IMAGE_T2I"]["state"] == "PENDING_LOCAL_VALIDATION"
    assert drifted["image_capabilities"]["QWEN_IMAGE_T2I"]["can_generate"] is False


def test_validation_only_worker_request_uses_validation_runtime_gate(monkeypatch, api):
    _client, factory, settings = api
    with factory.begin() as db:
        db.add(
            LibraryRenderTask(
                idempotency_key="validation-resolver-path-0001",
                request_hash="v" * 64,
                kind="image",
                mode="T2I",
                payload={
                    "mode": "T2I",
                    "category": "prop",
                    "output": {"width": 1024, "height": 1024},
                    "validation_only": True,
                    "capability": {"can_generate": False},
                },
                status="QUEUED",
                external_outcome="NOT_SENT",
            )
        )

    @contextmanager
    def fake_resource_slot(*_args, **_kwargs):
        yield "test-image-lease"

    observed = []
    monkeypatch.setattr(asset_execution, "resource_slot", fake_resource_slot)
    monkeypatch.setattr(
        asset_execution.image_execution,
        "_ensure_renderer",
        lambda *_args, **_kwargs: {"renderer_owned": True, "renderer_instance_id": "test"},
    )

    def stop_after_resolver(*_args, kind="production", **_kwargs):
        observed.append(kind)
        raise RuntimeError("stop before external submission")

    monkeypatch.setattr(asset_execution.image_execution, "resolve_runtime", stop_after_resolver)
    assert asset_execution.run_one_pending_job(factory, settings, "test-worker") == 1
    assert observed == ["validation"]
    with factory() as db:
        task = db.scalar(
            select(LibraryRenderTask).where(
                LibraryRenderTask.idempotency_key == "validation-resolver-path-0001"
            )
        )
        assert task.status == "FAILED"
        assert task.external_outcome == "NOT_SENT"
        assert task.submit_intent_at is None


def test_projectless_image_and_voice_jobs_share_worker_in_creation_order(monkeypatch, api):
    from shine.asset_center import voice_execution
    from shine.materialprep import execution as material_execution

    _client, factory, settings = api
    created = utcnow()
    with factory.begin() as db:
        db.add_all(
            [
                LibraryRenderTask(
                    idempotency_key="queue-image-arrives-second-0001",
                    request_hash="i" * 64,
                    kind="image",
                    mode="T2I",
                    payload={},
                    status="QUEUED",
                    external_outcome="NOT_SENT",
                    created_at=created + timedelta(seconds=2),
                ),
                LibraryRenderTask(
                    idempotency_key="queue-voice-arrives-first-0001",
                    request_hash="v" * 64,
                    kind="voice",
                    mode="DESIGN",
                    payload={},
                    status="QUEUED",
                    external_outcome="NOT_SENT",
                    created_at=created,
                ),
            ]
        )
    monkeypatch.setattr(material_execution, "claim_next_item", lambda *_args, **_kwargs: None)
    calls = []
    monkeypatch.setattr(asset_execution, "run_one_pending_job", lambda *_args: calls.append("image") or 1)
    monkeypatch.setattr(voice_execution, "run_one_pending_job", lambda *_args: calls.append("voice") or 1)

    assert material_execution.run_pending_image_items(factory, settings, "test-worker") == 1
    assert calls == ["voice"]


def test_voice_task_returns_to_queue_when_h3_holds_the_shared_resource_lease(monkeypatch, api):
    from shine.asset_center import voice_execution

    _client, factory, settings = api
    with factory.begin() as db:
        db.add(
            LibraryRenderTask(
                idempotency_key="voice-defer-behind-h3-0001",
                request_hash="v" * 64,
                kind="voice",
                mode="DESIGN",
                payload={"mode": "DESIGN", "description": "warm narrator", "text": "hello", "character": "narrator"},
                status="QUEUED",
                external_outcome="NOT_SENT",
            )
        )
        db.add(
            ResourceLease(
                owner="h3:active-production",
                budget_mb=16384,
                exclusive=True,
                priority=100,
                status="ACTIVE",
                expires_at=utcnow() + timedelta(minutes=5),
            )
        )

    monkeypatch.setattr(
        voice_execution,
        "_claim_inputs",
        lambda *_args: (
            {"mode": "DESIGN", "description": "warm narrator", "text": "hello", "character": "narrator"},
            {},
        ),
    )

    class Provider:
        def resource_budget_mb(self, _method):
            return 4096

        def release_models(self):
            raise AssertionError("provider must not unload while the H3 lease is active")

    monkeypatch.setattr(voice_execution, "_provider", lambda *_args: Provider())
    assert voice_execution.run_one_pending_job(factory, settings, "test-worker") == 1
    with factory() as db:
        task = db.scalar(
            select(LibraryRenderTask).where(LibraryRenderTask.idempotency_key == "voice-defer-behind-h3-0001")
        )
        h3_lease = db.scalar(select(ResourceLease).where(ResourceLease.owner == "h3:active-production"))
        assert task.status == "QUEUED"
        assert task.external_outcome == "NOT_SENT"
        assert task.submit_intent_at is None
        assert task.worker_id is None
        assert task.lease_owner is None
        assert task.error_code == "GPU_BUSY_RETRY_OR_QUEUE"
        assert h3_lease.status == "ACTIVE"


@pytest.mark.parametrize("fail_release_number", [None, 2])
def test_voice_tts_releases_each_gpu_model_before_invoking_the_next_stage(monkeypatch, api, fail_release_number):
    from types import SimpleNamespace

    from shine.asset_center import voice_execution

    _client, factory, settings = api
    with factory.begin() as db:
        task = LibraryRenderTask(
            idempotency_key="voice-serial-stage-contract-01",
            request_hash="s" * 64,
            kind="voice",
            mode="TTS",
            payload={"mode": "TTS"},
            status="QUEUED",
            external_outcome="NOT_SENT",
        )
        db.add(task)
        db.flush()
        task_id = task.id

    events = []
    release_count = 0

    class Provider:
        is_mock = False

        def resource_budget_mb(self, _method):
            return 4096

        def release_models(self):
            nonlocal release_count
            release_count += 1
            events.append("release")
            if release_count == fail_release_number:
                raise RuntimeError("MODEL_RELEASE_UNCONFIRMED")
            return {"released": True}

        def prepare_conditioning(self, *_args):
            events.append("prepare")

        def synthesize(self, *_args):
            events.append("synthesize")
            return {"audio": b"audio", "speaker": "narrator", "duration_s": 1.0}

        def transcribe(self, _audio):
            events.append("transcribe")
            return {"text": "你好", "speaker": "narrator", "language": "zh-CN", "confidence": 0.99}

        def align(self, _audio, _text):
            events.append("align")
            return [{"text": "你好", "start_s": 0.0, "end_s": 0.5}]

    provider = Provider()
    monkeypatch.setattr(voice_execution, "_claim_inputs", lambda *_args: (
        {
            "mode": "TTS",
            "text": "你好",
            "character": "narrator",
            "emotion": "neutral",
            "speed": 1.0,
            "pause": 0.0,
            "voice_hash": "v" * 64,
        },
        {
            "profile": SimpleNamespace(voice_hash="v" * 64),
            "conditioning": b"condition",
            "conditioning_text": "你好",
        },
    ))
    monkeypatch.setattr(voice_execution, "_provider", lambda *_args: provider)
    monkeypatch.setattr(voice_execution, "_open_attempt", lambda *_args: "attempt-serial-01")
    monkeypatch.setattr(voice_execution, "_store_candidate", lambda *_args: events.append("store"))

    class VoiceConfig:
        def routing(self):
            return {}

        def runtimes(self):
            return {}

    monkeypatch.setattr("shine.voiceops.runtime_config.load_voice_config", lambda _settings: VoiceConfig())
    assert voice_execution.run_one_pending_job(factory, settings, "serial-worker") == 1
    if fail_release_number is None:
        assert events == [
            "release",  # ResourceLease preflight.
            "prepare",
            "synthesize",
            "release",  # Confirm GPU/model release before ASR.
            "transcribe",
            "release",  # Confirm GPU/model release before alignment.
            "align",
            "store",
            "release",  # ResourceLease postflight before next worker tick.
        ]
    else:
        assert events == ["release", "prepare", "synthesize", "release", "release"]
        with factory() as db:
            task = db.get(LibraryRenderTask, task_id)
            lease = db.scalar(select(ResourceLease).order_by(ResourceLease.created_at.desc()))
            assert task.status == "UNKNOWN"
            assert task.external_outcome == "UNKNOWN"
            assert lease.status == "RELEASED"  # A later confirmation completed before lease release.


def test_unknown_image_task_cannot_be_retried_and_confirmed_not_sent_can_get_new_identity(api):
    client, factory, _settings = api
    initial = client.post(
        "/api/v1/asset-center/image-jobs",
        json={
            "idempotency_key": "safe-retry-source-01",
            "mode": "T2I",
            "prompt": "a lantern",
            "category": "other",
        },
    )
    original = initial.json()["items"][0]
    with factory.begin() as db:
        row = db.get(LibraryRenderTask, original["id"])
        row.status = "UNKNOWN"
        row.external_outcome = "UNKNOWN"
        row.external_prompt_id = "prompt-may-have-been-accepted"
    denied = client.post(
        f"/api/v1/asset-center/image-jobs/{original['id']}/new-attempt",
        json={"confirmed": True, "idempotency_key": "unsafe-retry-new-001"},
    )
    assert denied.status_code == 409
    assert denied.json()["detail"]["code"] == "NEW_ATTEMPT_NOT_SAFE"

    with factory.begin() as db:
        row = db.get(LibraryRenderTask, original["id"])
        row.status = "FAILED"
        row.external_outcome = "NOT_SENT"
        row.external_prompt_id = None
    accepted = client.post(
        f"/api/v1/asset-center/image-jobs/{original['id']}/new-attempt",
        json={"confirmed": True, "idempotency_key": "safe-retry-new-001"},
    )
    assert accepted.status_code == 202, accepted.text
    assert accepted.json()["id"] != original["id"]
    assert accepted.json()["external_outcome"] == "NOT_SENT"
    assert accepted.json()["status"] == "BLOCKED_CAPABILITY"


def test_project_assignment_is_scoped_immutable_and_idempotency_binds_identity(api):
    client, factory, _settings = api
    local_action = {"X-Shine-Local-Action": "1"}
    project_a = client.post("/api/v1/projects", json={"title": "项目 A"}).json()["id"]
    project_b = client.post("/api/v1/projects", json={"title": "项目 B"}).json()["id"]
    with factory.begin() as db:
        plan = PreProductionPlan(
            project_id=project_a,
            purpose=PLAN_PURPOSE_ASSET_PREPARATION,
            status="CONFIRMED",
            sha256="p" * 64,
            payload={},
        )
        db.add(plan)
        db.flush()
        requirement = AssetRequirement(
            project_id=project_a,
            plan_id=plan.id,
            status="OPEN",
            sha256="r" * 64,
            payload={"family": "character", "entity_id": "林知远"},
        )
        db.add(requirement)
        db.flush()
        requirement_id = requirement.id
    uploaded = _rights_upload(client, _png(2, [[(25, 35, 45, 255), (55, 65, 75, 255)]]))
    media_id = uploaded.json()["id"]
    asset = client.post(
        "/api/v1/asset-center/assets",
        json={"title": "共享素材", "kind": "image", "category": "character", "media_id": media_id},
    ).json()
    version_id = asset["versions"][0]["id"]
    rights_id = uploaded.json()["rights_id"]
    with factory() as db:
        snapshot = service.canonical_hash(service.rights_snapshot(db.get(LibraryRights, rights_id)))
    approved_rights = client.post(
        f"/api/v1/asset-center/rights/{rights_id}/approve",
        headers=local_action,
        json={
            "expected_snapshot": snapshot,
            "evidence": "fixture owned by the test",
            "allowed_project_ids": [project_a],
            "permitted_uses": ["research"],
            "commercial_allowed": False,
        },
    )
    assert approved_rights.status_code == 200, approved_rights.text
    qc = client.post(f"/api/v1/asset-center/versions/{version_id}/qc")
    assert qc.status_code == 200 and qc.json()["status"] == "READY_FOR_REVIEW", qc.text
    unreviewed = client.post(
        "/api/v1/asset-center/assignments",
        headers=local_action,
        json={
            "project_id": project_a,
            "library_version_id": version_id,
            "role": "character",
            "entity_id": "林知远",
            "usage_scope": "research",
            "idempotency_key": "assign-before-promotion-01",
            "confirmed": True,
        },
    )
    assert unreviewed.status_code == 409
    assert unreviewed.json()["detail"]["code"] == "LIBRARY_VERSION_NOT_ASSIGNABLE"
    grant = client.post(f"/api/v1/asset-center/versions/{version_id}/grant", headers=local_action)
    assert grant.status_code == 200, grant.text
    promoted = client.post(
        f"/api/v1/asset-center/versions/{version_id}/promote",
        headers=local_action,
        json={
            "grant_id": grant.json()["grant_id"],
            "snapshot": grant.json()["snapshot"],
            "evidence": "reviewed fixture",
        },
    )
    assert promoted.status_code == 200 and promoted.json()["status"] == "PROMOTED", promoted.text

    queued = client.post(
        "/api/v1/asset-center/image-jobs",
        json={
            "idempotency_key": "asset-version-branch-0001",
            "mode": "T2I",
            "prompt": "prepare a new candidate without changing the approved source",
            "category": "character",
            "target_asset_id": asset["id"],
            "parent_version_id": version_id,
            "expected_asset_state_version": asset["state_version"] + 1,
        },
    )
    assert queued.status_code == 202, queued.text
    task_id = queued.json()["items"][0]["id"]
    with factory() as db:
        task = db.get(LibraryRenderTask, task_id)
        assert task.status == "BLOCKED_CAPABILITY"
        assert task.payload["target_asset_id"] == asset["id"]
        assert task.payload["parent_version_id"] == version_id
        assert db.get(LibraryVersion, version_id).status == "PROMOTED"

    body = {
        "project_id": project_a,
        "library_version_id": version_id,
        "role": "character",
        "entity_id": "林知远",
        "usage_scope": "research",
        "idempotency_key": "assign-project-a-0001",
        "confirmed": True,
        "requirement_context": {
            "project_id": project_a,
            "requirement_id": requirement_id,
            "requirement_hash": "r" * 64,
        },
    }
    assigned = client.post("/api/v1/asset-center/assignments", headers=local_action, json=body)
    assert assigned.status_code == 201, assigned.text
    first = assigned.json()
    assert first["project_id"] == project_a
    assert first["library_version_id"] == version_id

    with factory.begin() as db:
        db.get(AssetRequirement, requirement_id).sha256 = "s" * 64
    stale_requirement = client.post(
        "/api/v1/asset-center/assignments",
        headers=local_action,
        json={**body, "idempotency_key": "assign-stale-requirement-01"},
    )
    assert stale_requirement.status_code == 409
    assert stale_requirement.json()["detail"]["code"] == "MATERIAL_REQUIREMENT_STALE"
    with factory.begin() as db:
        db.get(AssetRequirement, requirement_id).sha256 = "r" * 64

    same = client.post(
        "/api/v1/asset-center/assignments",
        headers=local_action,
        json={**body, "idempotency_key": "assign-project-a-0002"},
    )
    assert same.status_code == 201, same.text
    assert same.json()["id"] == first["id"]
    assert same.json()["project_asset_id"] == first["project_asset_id"]

    reused = client.post(
        "/api/v1/asset-center/assignments",
        headers=local_action,
        json={**body, "role": "prop", "idempotency_key": body["idempotency_key"], "requirement_context": None},
    )
    assert reused.status_code == 409
    assert reused.json()["detail"]["code"] == "IDEMPOTENCY_KEY_REUSED"

    cross_project = client.post(
        "/api/v1/asset-center/assignments",
        headers=local_action,
        json={**body, "project_id": project_b, "idempotency_key": "assign-project-b-0001", "requirement_context": None},
    )
    assert cross_project.status_code == 403
    assert cross_project.json()["detail"]["code"] == "LIBRARY_PROJECT_NOT_AUTHORIZED"

    with factory() as db:
        current_rights = service.canonical_hash(service.rights_snapshot(db.get(LibraryRights, rights_id)))
    expanded_rights = client.post(
        f"/api/v1/asset-center/rights/{rights_id}/approve",
        headers=local_action,
        json={
            "expected_snapshot": current_rights,
            "evidence": "项目 B 的权利范围也已逐项核实",
            "allowed_project_ids": [project_a, project_b],
            "permitted_uses": ["research"],
            "commercial_allowed": False,
        },
    )
    assert expanded_rights.status_code == 200, expanded_rights.text
    assigned_b = client.post(
        "/api/v1/asset-center/assignments",
        headers=local_action,
        json={
            **body,
            "project_id": project_b,
            "entity_id": "林知远·乙项目",
            "idempotency_key": "assign-project-b-0001",
            "requirement_context": None,
        },
    )
    assert assigned_b.status_code == 201, assigned_b.text
    assert assigned_b.json()["library_version_id"] == first["library_version_id"]
    assert assigned_b.json()["project_asset_id"] != first["project_asset_id"]
    with factory() as db:
        project_asset_a = db.get(Asset, first["project_asset_id"])
        project_asset_b = db.get(Asset, assigned_b.json()["project_asset_id"])
        assert project_asset_a.project_id == project_a and project_asset_b.project_id == project_b
        assert project_asset_a.sha256 == project_asset_b.sha256 == asset["versions"][0]["content_sha256"]

    source_v2 = _rights_upload(client, _png(2, [[(110, 120, 130, 255), (140, 150, 160, 255)]]))
    with factory() as db:
        v2_rights_snapshot = service.canonical_hash(
            service.rights_snapshot(db.get(LibraryRights, source_v2.json()["rights_id"]))
        )
    v2_rights = client.post(
        f"/api/v1/asset-center/rights/{source_v2.json()['rights_id']}/approve",
        headers=local_action,
        json={
            "expected_snapshot": v2_rights_snapshot,
            "evidence": "V2 测试图拥有者授权复核",
            "allowed_project_ids": [project_a, project_b],
            "permitted_uses": ["research"],
            "commercial_allowed": False,
        },
    )
    assert v2_rights.status_code == 200, v2_rights.text
    version2 = client.post(
        f"/api/v1/asset-center/assets/{asset['id']}/versions",
        json={"media_id": source_v2.json()["id"], "source_mode": "UPLOAD", "parent_version_id": version_id},
    )
    assert version2.status_code == 201, version2.text
    assert version2.json()["parent_version_id"] == version_id
    assert client.post(f"/api/v1/asset-center/versions/{version2.json()['id']}/qc").status_code == 200
    v2_grant = client.post(f"/api/v1/asset-center/versions/{version2.json()['id']}/grant", headers=local_action).json()
    v2_promoted = client.post(
        f"/api/v1/asset-center/versions/{version2.json()['id']}/promote",
        headers=local_action,
        json={"grant_id": v2_grant["grant_id"], "snapshot": v2_grant["snapshot"], "evidence": "V2 人工审核"},
    )
    assert v2_promoted.status_code == 200, v2_promoted.text
    latest_asset = client.get(f"/api/v1/asset-center/assets/{asset['id']}").json()
    selected_v2 = client.post(
        f"/api/v1/asset-center/assets/{asset['id']}/select-version",
        params={"version_id": version2.json()["id"], "expected_state_version": latest_asset["state_version"]},
    )
    assert selected_v2.status_code == 200, selected_v2.text
    assert selected_v2.json()["current_version"]["id"] == version2.json()["id"]
    assert assigned_b.json()["library_version_id"] == first["library_version_id"]
    with factory.begin() as db:
        db.delete(db.get(Project, project_b))
    with factory() as db:
        remaining_links = db.scalars(
            select(LibraryProjectLink).where(LibraryProjectLink.project_id == project_a)
        ).all()
        assert len(remaining_links) == 1
        assert remaining_links[0].library_version_id == version_id
        assert db.scalar(select(LibraryProjectLink.id).where(LibraryProjectLink.project_id == project_b)) is None
        assert db.scalar(select(RightsRecord.id).where(RightsRecord.project_id == project_b)) is None
        assert db.get(Asset, first["project_asset_id"]) is not None
        assert db.scalar(select(RightsRecord.id).where(RightsRecord.project_id == project_a)) is not None
        frozen_v1 = db.get(LibraryVersion, version_id)
        assert frozen_v1.status == "PROMOTED"
        frozen_v1_sha = hashlib.sha256(service.version_bytes(_settings, frozen_v1)).hexdigest()
        assert frozen_v1_sha == asset["versions"][0]["content_sha256"]

    saved = client.post(
        f"/api/v1/projects/{project_a}/assets/{first['project_asset_id']}/save-to-library",
        headers=local_action,
        json={"title": "林知远·全局收藏", "category": "character", "tags": ["复用"], "confirmed_rights": True},
    )
    assert saved.status_code == 201, saved.text
    assert saved.json()["versions"][0]["content_sha256"] == asset["versions"][0]["content_sha256"]
    assert saved.json()["versions"][0]["status"] == "READY_FOR_REVIEW"
    with factory() as db:
        links = db.scalars(select(LibraryProjectLink).where(LibraryProjectLink.project_id == project_a)).all()
        assert len(links) == 1
        project_asset = db.get(Asset, first["project_asset_id"])
        project_rights_id = project_asset.provenance["rights_id"]
        from shine.comfyops.assets import require_rights as require_project_asset_rights

        require_project_asset_rights(db, project_a, project_rights_id)
    with factory.begin() as db:
        source_rights = db.get(LibraryRights, rights_id)
        source_rights.status = "REVOKED"
        source_rights.revoked_at = utcnow()
    with factory() as db:
        with pytest.raises(HTTPException):
            require_project_asset_rights(db, project_a, project_rights_id)


def test_stale_worker_recovery_keeps_uncertain_external_tasks_out_of_queue(api):
    from shine.asset_center.execution import _claim_next, recover_stale_projectless_tasks

    _client, factory, _settings = api
    old = utcnow() - timedelta(hours=1)
    with factory.begin() as db:
        not_sent = LibraryRenderTask(
            idempotency_key="stale-not-sent-0001",
            request_hash="a" * 64,
            kind="image",
            mode="T2I",
            payload={"mode": "T2I"},
            status="RUNNING",
            external_outcome="NOT_SENT",
            worker_id="worker-old",
            lease_owner="IMAGE:lease-old",
            created_at=old,
            updated_at=old,
            heartbeat_at=old,
        )
        unknown = LibraryRenderTask(
            idempotency_key="stale-unknown-0001",
            request_hash="b" * 64,
            kind="image",
            mode="T2I",
            payload={"mode": "T2I"},
            status="RUNNING",
            submit_intent_at=old,
            external_prompt_id="prompt-may-exist",
            external_outcome="UNKNOWN",
            worker_id="worker-old",
            lease_owner="IMAGE:lease-old",
            created_at=old,
            updated_at=old,
            heartbeat_at=old,
        )
        db.add_all([not_sent, unknown])
        db.flush()
        not_sent_id, unknown_id = not_sent.id, unknown.id

    assert recover_stale_projectless_tasks(factory) == 2
    with factory() as db:
        assert db.get(LibraryRenderTask, not_sent_id).status == "QUEUED"
        row = db.get(LibraryRenderTask, unknown_id)
        assert row.status == "UNKNOWN"
        assert row.external_outcome == "UNKNOWN"
        assert row.submit_intent_at is not None
        assert row.worker_id is None and row.lease_owner is None
    assert _claim_next(factory, "worker-new") == not_sent_id
    with factory() as db:
        assert db.get(LibraryRenderTask, unknown_id).status == "UNKNOWN"
