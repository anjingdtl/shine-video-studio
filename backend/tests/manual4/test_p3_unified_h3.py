"""P3 acceptance tests: unified H3 executor, reconciliation, cross-ep continuity & UTF-8 SHA."""

from __future__ import annotations

import hashlib
import io
import struct
import zlib
from datetime import timedelta
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select

from shine.config import Settings
from shine.db import Base, make_engine, make_session_factory
from shine.foundation.h3 import (
    ComfyH3Executor,
    ComfySubmissionUncertain,
    H3ExecutionRequest,
    H3ReferenceInput,
    compile_h3_ref2va_graph,
    graph_hash,
    reconcile_submission,
)
from shine.main import create_app
from shine.manual4.storage.models import (
    M4ExecutionSnapshot,
    M4Job,
    M4JobAttempt,
    M4ProductionRun,
    M4WorkerInstance,
    m4_utcnow,
)
from shine.manual4.worker import _execute_single_job, reconcile_stalled_m4_runs

pytestmark = pytest.mark.integration

LOCAL_ACTION_HEADERS = {"x-shine-local-action": "1"}


def _png_bytes(color: tuple[int, int, int] = (30, 140, 210), size: tuple[int, int] = (64, 64)) -> bytes:
    w, h = size
    raw = (b"\x00" + bytes(color) * w) * h

    def _chunk(tag: bytes, payload: bytes) -> bytes:
        crc = zlib.crc32(tag + payload) & 0xFFFFFFFF
        return struct.pack(">I", len(payload)) + tag + payload + struct.pack(">I", crc)

    ihdr = struct.pack(">IIBBBBB", w, h, 8, 2, 0, 0, 0)
    return b"\x89PNG\r\n\x1a\n" + _chunk(b"IHDR", ihdr) + _chunk(b"IDAT", zlib.compress(raw)) + _chunk(b"IEND", b"")


def _wav_bytes(duration_s: float = 2.0, sample_rate: int = 8000) -> bytes:
    num_samples = int(duration_s * sample_rate)
    data = b"\x00\x00" * num_samples
    buf = io.BytesIO()
    buf.write(b"RIFF")
    buf.write(struct.pack("<I", 36 + len(data)))
    buf.write(b"WAVEfmt ")
    buf.write(struct.pack("<IHHIIHH", 16, 1, 1, sample_rate, sample_rate * 2, 2, 16))
    buf.write(b"data")
    buf.write(struct.pack("<I", len(data)))
    buf.write(data)
    return buf.getvalue()


def _setup_app(tmp_path: Path):
    db_path = tmp_path / "shine.db"
    database_url = f"sqlite:///{db_path.as_posix()}"
    engine = make_engine(database_url)
    Base.metadata.create_all(engine)
    factory = make_session_factory(engine)
    settings = Settings(data_dir=tmp_path, database_url=database_url, node_pack_gate="diagnose")
    app = create_app(settings=settings, database_url=database_url, asset_root=tmp_path)
    app.state.engine.dispose()
    app.state.engine = engine
    app.state.session_factory = factory
    return app, settings


class _FakeComfyClient:
    """In-memory ComfyUI double supporting dict & real 5-element list /history payloads."""

    def __init__(self, *, uncertain_on_submit: bool = False, list_history: bool = True):
        self.uncertain_on_submit = uncertain_on_submit
        self.list_history = list_history
        self.uploads: list[tuple[str, bytes]] = []
        self.submitted_graphs: list[dict[str, Any]] = []
        self.history_map: dict[str, dict[str, Any]] = {}
        self.queue_running: list[list[Any]] = []
        self.queue_pending: list[list[Any]] = []
        self.outputs_bytes: dict[str, bytes] = {"out_0001.mp4": b"\x00\x00\x00\x18ftypmp42fake-h3-bytes"}

    def object_info(self) -> dict[str, Any]:
        cw = compile_h3_ref2va_graph(image_count=4, audio_count=2)
        info: dict[str, Any] = {}
        for node in cw.graph.values():
            ct = str(node["class_type"])
            req = info.setdefault(ct, {"input": {"required": {}, "optional": {}}})["input"]["required"]
            for k in (node.get("inputs") or {}):
                req.setdefault(k, ["STRING"])
                req.setdefault(k.split(".", 1)[0], ["STRING"])
        return info

    def upload_file(self, filename: str, content: bytes, **_kwargs) -> dict[str, Any]:
        self.uploads.append((filename, content))
        return {"name": filename, "subfolder": "shine"}

    def queue_prompt(self, prompt: dict[str, Any], *, extra_data=None) -> dict[str, Any]:
        self.submitted_graphs.append(prompt)
        pid = f"pid-{len(self.submitted_graphs)}"
        prompt_field: Any = [len(self.submitted_graphs), pid, prompt, extra_data, []] if self.list_history else prompt
        self.history_map[pid] = {
            "prompt": prompt_field,
            "extra_data": extra_data,
            "status": {"status_str": "success", "completed": True},
            "outputs": {"57": {"videos": [{"filename": "out_0001.mp4", "subfolder": "", "type": "output"}]}},
        }
        if self.uncertain_on_submit:
            raise ComfySubmissionUncertain("simulated read timeout after ComfyUI queued")
        return {"prompt_id": pid}

    def history(self, prompt_id: str | None = None) -> dict[str, Any]:
        if prompt_id is not None:
            return {prompt_id: self.history_map[prompt_id]} if prompt_id in self.history_map else {}
        return dict(self.history_map)

    def queue(self) -> dict[str, Any]:
        return {"queue_running": list(self.queue_running), "queue_pending": list(self.queue_pending)}

    def view(self, filename: str, **_kwargs) -> bytes:
        return self.outputs_bytes[filename]


def _seed_two_episode_project(client: TestClient) -> tuple[str, list[dict[str, Any]]]:
    pid = client.post(
        "/api/v1/platform/manual4-projects",
        headers=LOCAL_ACTION_HEADERS,
        json={"title": "P3双集连续性测试"},
    ).json()["id"]
    raw_text = (
        "第1集：夜巷起点（目标总时长：10s）\n"
        "第1镜（5s）：林晚站在巷口。\n"
        "人物：林晚\n"
        "第2镜（5s）：林晚转身抬头。\n"
        "人物：林晚\n"
        "第2集：晨光街头（目标总时长：5s）\n"
        "第1镜（5s）：林晚走出地铁口。\n"
        "人物：林晚\n"
    )
    imp = client.post(
        f"/api/v1/manual4/projects/{pid}/storyboard/import",
        headers=LOCAL_ACTION_HEADERS,
        data={"text": raw_text, "filename": "two_ep.txt"},
    )
    assert imp.status_code == 201, imp.text
    conf = client.post(
        f"/api/v1/manual4/projects/{pid}/storyboard/confirm",
        headers=LOCAL_ACTION_HEADERS,
        json={"expected_revision": imp.json()["draft"]["revision"]},
    )
    assert conf.status_code == 200, conf.text
    mats = client.get(f"/api/v1/manual4/projects/{pid}/materials/requirements").json()
    reqs = {(r["family"], r["entity_name"]): r for r in mats["requirements"]}
    up = client.post(
        f"/api/v1/manual4/projects/{pid}/assets/upload",
        headers=LOCAL_ACTION_HEADERS,
        data={"family": "character", "entity_name": "林晚", "requirement_id": reqs[("character", "林晚")]["id"]},
        files={"file": ("linwan.png", _png_bytes(), "image/png")},
    )
    assert up.status_code == 200
    prom = client.post(
        f"/api/v1/manual4/projects/{pid}/assets/{up.json()['asset']['id']}/promote",
        headers=LOCAL_ACTION_HEADERS,
        json={"requirement_id": reqs[("character", "林晚")]["id"]},
    )
    assert prom.status_code == 200
    mats2 = client.get(f"/api/v1/manual4/projects/{pid}/materials/requirements").json()
    shots = sorted(mats2["shots"], key=lambda s: (s["shot_code"], s["shot_no"]))
    for idx, sh in enumerate(shots):
        # Make shot 1 cut, shot 2 last_frame (ep1), shot 3 last_frame (ep2 boundary!)
        if idx > 0:
            opt = client.put(
                f"/api/v1/manual4/projects/{pid}/shots/{sh['id']}/render-options",
                headers=LOCAL_ACTION_HEADERS,
                json={"continuity_mode": "last_frame"},
            )
            assert opt.status_code == 200
        frz = client.post(
            f"/api/v1/manual4/projects/{pid}/shots/{sh['id']}/prompt/freeze",
            headers=LOCAL_ACTION_HEADERS,
            json={"prompt_text": f"Shot {idx + 1} 雨夜林晚镜头"},
        )
        assert frz.status_code == 200
    return pid, shots


def test_p3_a13_a18_cross_episode_continuity_and_run_idempotency(tmp_path: Path):
    app, _settings = _setup_app(tmp_path)
    with TestClient(app) as client:
        pid, shots = _seed_two_episode_project(client)
        assert len(shots) == 3

        idem = "p3-idem-run-001"
        r1 = client.post(
            f"/api/v1/manual4/projects/{pid}/production/runs",
            headers=LOCAL_ACTION_HEADERS,
            json={"scope_kind": "project", "provider": "mock", "idempotency_key": idem},
        )
        assert r1.status_code == 201, r1.text
        run1 = r1.json()["run"]
        assert run1["execution_generation"] == 2
        jobs = run1["jobs"]
        assert len(jobs) == 3
        assert jobs[0]["previous_job_id"] is None
        assert jobs[1]["previous_job_id"] == jobs[0]["id"]  # Same episode last_frame chains cleanly
        assert jobs[1]["episode_id"] != jobs[2]["episode_id"]
        assert jobs[2]["previous_job_id"] is None  # Cross-episode boundary MUST reset last_frame!
        assert all(j["execution_generation"] == 2 for j in jobs)

        # A18: Duplicate idempotency_key returns existing run without extra snapshots/jobs
        r2 = client.post(
            f"/api/v1/manual4/projects/{pid}/production/runs",
            headers=LOCAL_ACTION_HEADERS,
            json={"scope_kind": "project", "provider": "mock", "idempotency_key": idem},
        )
        assert r2.status_code in {200, 201}
        assert r2.json()["run"]["id"] == run1["id"]
        with app.state.m4_session_factory() as m4_db:
            assert m4_db.scalar(select(func.count(M4ProductionRun.id))) == 1
            assert m4_db.scalar(select(func.count(M4ExecutionSnapshot.id))) == 1
            assert m4_db.scalar(select(func.count(M4Job.id))) == 3

        # Cancel active run1, then retake shot 2 (last_frame) BEFORE shot 1 has passed -> refused 409
        cancel = client.post(
            f"/api/v1/manual4/projects/{pid}/production/runs/{run1['id']}/cancel",
            headers=LOCAL_ACTION_HEADERS,
        )
        assert cancel.status_code == 200
        retake = client.post(
            f"/api/v1/manual4/projects/{pid}/shots/{shots[1]['id']}/retake",
            headers=LOCAL_ACTION_HEADERS,
            json={"seed": 101},
        )
        assert retake.status_code == 409
        assert retake.json()["detail"]["code"] == "M4_CONTINUITY_SOURCE_MISSING"


def test_p3_a11_a17_utf8_prompt_pre_submit_sha_list_history_reconcile(tmp_path: Path):
    app, settings = _setup_app(tmp_path)
    img_path = tmp_path / "ref.png"
    img_bytes = _png_bytes()
    img_path.write_bytes(img_bytes)
    img_sha = hashlib.sha256(img_bytes).hexdigest()

    utf8_prompt = "subject_definitions:\n<Subject 1>: 雨夜林晚持伞伫立，霓虹灯映照青石板路。"
    utf8_prompt_sha = hashlib.sha256(utf8_prompt.encode()).hexdigest()

    client_ok = _FakeComfyClient(uncertain_on_submit=True, list_history=True)
    recorded_before: list[str] = []
    recorded_after: list[str] = []

    req = H3ExecutionRequest(
        prompt_text=utf8_prompt,
        prompt_sha256=utf8_prompt_sha,
        duration_s=5.0,
        width=1344,
        height=768,
        seed=42,
        idempotency_key="m4:render:attempt:1",
        references=[
            H3ReferenceInput(
                family="image",
                path=img_path,
                filename="ref.png",
                content_type="image/png",
                sha256=img_sha,
            )
        ],
    )
    receipt = ComfyH3Executor().execute(
        client_ok,
        req,
        on_before_submit=lambda gsha, _g: recorded_before.append(gsha),
        on_submitted=lambda pid: recorded_after.append(pid),
    )
    assert receipt.status == "completed"
    assert len(recorded_before) == 1
    assert recorded_after == [receipt.prompt_id]
    submitted = client_ok.submitted_graphs[0]
    assert submitted["31"]["inputs"]["prompt"] == utf8_prompt
    assert recorded_before[0] == graph_hash(submitted)
    assert receipt.prompt_graph_sha256 == graph_hash(submitted)
    metadata = client_ok.history_map[receipt.prompt_id]["prompt"][3]["shine"]
    assert metadata["idempotency_key"] == req.idempotency_key
    assert metadata["prompt_sha256"] == utf8_prompt_sha
    assert metadata["prompt_graph_sha256"] == receipt.prompt_graph_sha256

    # Verify list-formatted ComfyUI /history AND /queue reconcile without prompt_id!
    mid, state, rec = reconcile_submission(client_ok, prompt_id=None, prompt_graph_sha256=receipt.prompt_graph_sha256)
    assert mid == receipt.prompt_id and state == "completed" and rec is not None

    q_client = _FakeComfyClient()
    q_pid = "queued-list-pid-9"
    q_client.queue_running.append([0, "decoy", submitted, {"shine": {"idempotency_key": "different"}}, []])
    q_client.queue_running.append([1, q_pid, submitted, {"shine": metadata}, []])
    q_mid, q_state, q_rec = reconcile_submission(
        q_client, prompt_id=None, prompt_graph_sha256=graph_hash(submitted), idempotency_key=req.idempotency_key
    )
    assert q_mid == q_pid and q_state == "running" and q_rec is None
    assert reconcile_submission(
        q_client, prompt_id="acknowledged-but-missing", prompt_graph_sha256=graph_hash(submitted),
        idempotency_key=req.idempotency_key,
    ) == (None, "not_found", None)

    # A11 + A20: Test reconcile_stalled_m4_runs for BOTH gen=1 legacy & gen=2 completed /history record!
    m4_factory = app.state.m4_session_factory
    m4_root = app.state.m4_root
    with TestClient(app) as client:
        pid, shots = _seed_two_episode_project(client)
        r = client.post(
            f"/api/v1/manual4/projects/{pid}/production/runs",
            headers=LOCAL_ACTION_HEADERS,
            json={"scope_kind": "shot", "scope_target_ids": [shots[0]["id"]], "provider": "comfyui"},
        )
        assert r.status_code == 201
        run_id = r.json()["run"]["id"]
        job_id = r.json()["run"]["jobs"][0]["id"]

        with m4_factory.begin() as m4_db:
            w = M4WorkerInstance(
                worker_id="dead-w-1",
                pid=999999,
                status="OFFLINE",
                version="2.0",
                last_heartbeat_at=m4_utcnow() - timedelta(minutes=10),
            )
            m4_db.add(w)
            run_row = m4_db.get(M4ProductionRun, run_id)
            job_row = m4_db.get(M4Job, job_id)
            assert run_row is not None and job_row is not None
            run_row.status = "RUNNING"
            run_row.worker_id = "dead-w-1"
            job_row.status = "SUBMITTING"
            job_row.attempt_count = 1
            # This historical attempt predates the shared metadata; its job ID still proves lineage.
            client_ok.history_map[receipt.prompt_id]["prompt"][3] = {"shine_line": "manual4", "job_id": job_id}
            att = M4JobAttempt(
                job_id=job_id,
                attempt_no=1,
                execution_generation=2,
                status="SUBMITTING",
                prompt_id=None,  # Crashed right after on_before_submit!
                prompt_graph_sha256=receipt.prompt_graph_sha256,
                submit_intent_at=m4_utcnow(),
            )
            m4_db.add(att)
            # Legacy gen=1 stalled run without ComfyUI match
            leg_run = M4ProductionRun(
                project_id=pid,
                execution_snapshot_id=run_row.execution_snapshot_id,
                idempotency_key="legacy-gen1-run",
                scope_kind="shot",
                scope_target_ids=[shots[0]["id"]],
                provider="comfyui",
                execution_generation=1,
                status="RUNNING",
                worker_id="dead-w-1",
            )
            m4_db.add(leg_run)
            m4_db.flush()
            leg_job = M4Job(
                project_id=pid,
                run_id=leg_run.id,
                episode_id=job_row.episode_id,
                shot_id=job_row.shot_id,
                shot_revision=job_row.shot_revision,
                ordinal=0,
                idempotency_key="legacy-gen1-job",
                prompt_revision_id=job_row.prompt_revision_id,
                prompt_sha256=job_row.prompt_sha256,
                reference_manifest_sha256=job_row.reference_manifest_sha256,
                target_seconds=5.0,
                continuity_mode="cut",
                provider="comfyui",
                execution_generation=1,
                status="SUBMITTING",
                attempt_count=1,
            )
            m4_db.add(leg_job)
            m4_db.flush()
            m4_db.add(
                M4JobAttempt(
                    job_id=leg_job.id,
                    attempt_no=1,
                    execution_generation=1,
                    status="SUBMITTING",
                    prompt_graph_sha256="0" * 64,
                )
            )
            leg_run_id = leg_run.id
            leg_job_id = leg_job.id

        # Tampered snapshot prompt text is refused BEFORE touching ComfyUI
        with m4_factory.begin() as m4_db:
            snap_bad = {
                "shot_id": shots[0]["id"],
                "shot_code": shots[0]["shot_code"],
                "aspect_ratio": "16:9",
                "target_seconds": 5.0,
                "width": 1344,
                "height": 768,
                "seed": 1,
                "prompt_text": "TAMPERED_PROMPT",
                "prompt_sha256": hashlib.sha256(b"ORIGINAL").hexdigest(),
                "reference_manifest": [],
            }
            _ = snap_bad

        reconciled = reconcile_stalled_m4_runs(
            m4_factory,
            stale_seconds=60,
            comfy_client=client_ok,
            m4_root=m4_root,
            settings=settings,
        )
        assert reconciled >= 2
        with m4_factory() as m4_db:
            gen2_job = m4_db.get(M4Job, job_id)
            assert gen2_job is not None
            # Recovered output from ComfyUI /history without resubmit; since ffprobe/fake mp4 isn't decodable,
            # status is FAILED or NEEDS_REVIEW (NOT blind resubmitted or still SUBMITTING!)
            assert gen2_job.output_sha256 == receipt.output_sha256
            assert gen2_job.status in {"FAILED", "NEEDS_REVIEW", "PASSED"}
            assert len(client_ok.submitted_graphs) == 1

            g1_run = m4_db.get(M4ProductionRun, leg_run_id)
            g1_job = m4_db.get(M4Job, leg_job_id)
            assert g1_run is not None and g1_run.status == "NEEDS_REVIEW"
            assert g1_job is not None and g1_job.status == "NEEDS_REVIEW"

    # Also verify non-mock TechnicalQC unavailable tool preserves NEEDS_REVIEW (not FAILED) and checks prompt SHA!
    with TestClient(app) as client:
        pid2, shots2 = _seed_two_episode_project(client)
        # Upload valid wav audio to test expect_audio=True path
        mats = client.get(f"/api/v1/manual4/projects/{pid2}/materials/requirements").json()
        aud_up = client.post(
            f"/api/v1/manual4/projects/{pid2}/assets/upload",
            headers=LOCAL_ACTION_HEADERS,
            data={"family": "voice", "entity_name": "林晚对白"},
            files={"file": ("voice.wav", _wav_bytes(2.0), "audio/wav")},
        )
        assert aud_up.status_code == 200
        aud_asset_id = aud_up.json()["asset"]["id"]
        prom_aud = client.post(
            f"/api/v1/manual4/projects/{pid2}/assets/{aud_asset_id}/promote",
            headers=LOCAL_ACTION_HEADERS,
        )
        assert prom_aud.status_code == 200
        # Bind audio alongside image on shot 0 and re-freeze prompt
        img_asset_id = mats["shots"][0]["bindings"][0]["asset_version_id"]
        client.put(
            f"/api/v1/manual4/projects/{pid2}/shots/{shots2[0]['id']}/bindings",
            headers=LOCAL_ACTION_HEADERS,
            json={
                "bindings": [
                    {"asset_version_id": img_asset_id, "use_as": "image", "role_label": "林晚"},
                    {"asset_version_id": aud_asset_id, "use_as": "audio", "role_label": "林晚对白"},
                ]
            },
        )
        client.post(
            f"/api/v1/manual4/projects/{pid2}/shots/{shots2[0]['id']}/prompt/freeze",
            headers=LOCAL_ACTION_HEADERS,
            json={"prompt_text": "Shot 1 with voice ref 林晚开口说话"},
        )
        r_aud = client.post(
            f"/api/v1/manual4/projects/{pid2}/production/runs",
            headers=LOCAL_ACTION_HEADERS,
            json={"scope_kind": "shot", "scope_target_ids": [shots2[0]["id"]], "provider": "comfyui"},
        )
        assert r_aud.status_code == 201
        r_aud_id = r_aud.json()["run"]["id"]
        j_aud_id = r_aud.json()["run"]["jobs"][0]["id"]
        with m4_factory() as m4_db:
            run_row = m4_db.get(M4ProductionRun, r_aud_id)
            snap = m4_db.get(M4ExecutionSnapshot, run_row.execution_snapshot_id)
            snap_item = snap.items_json[0]
            run_info = {
                "id": run_row.id,
                "project_id": pid2,
                "provider": "comfyui",
                "execution_generation": 2,
                "config": snap.config_json,
            }

        # Verify tampered snap prompt_text fails precheck BEFORE calling ComfyUI
        bad_snap = dict(snap_item, prompt_text="TAMPERED")
        no_call_client = _FakeComfyClient()
        res_tamper = _execute_single_job(
            m4_factory,
            m4_root,
            run_info,
            j_aud_id,
            bad_snap,
            worker_id="w-test",
            comfy_client=no_call_client,
            platform_factory=app.state.session_factory,
            settings=Settings(
                data_dir=tmp_path,
                database_url=settings.database_url,
                ffprobe_path="nonexistent_ffprobe",
                ffmpeg_path="nonexistent_ffmpeg",
            ),
        )
        assert res_tamper == "FAILED"
        assert len(no_call_client.submitted_graphs) == 0
        with m4_factory.begin() as m4_db:
            jr = m4_db.get(M4Job, j_aud_id)
            assert jr is not None and jr.failure_code == "M4_MANUAL_PROMPT_SHA_MISMATCH"
            jr.status = "QUEUED"
            jr.failure_code = None

        # Verify non-mock run with audio ref sets expect_audio=True (catching missing audio track in fake mp4)
        comfy_aud = _FakeComfyClient()
        res_aud = _execute_single_job(
            m4_factory,
            m4_root,
            run_info,
            j_aud_id,
            snap_item,
            worker_id="w-test",
            comfy_client=comfy_aud,
            platform_factory=app.state.session_factory,
            settings=Settings(
                data_dir=tmp_path,
                database_url=settings.database_url,
                ffprobe_path="nonexistent_ffprobe",
                ffmpeg_path="nonexistent_ffmpeg",
            ),
        )
        assert res_aud == "FAILED"
        assert len(comfy_aud.uploads) == 2  # Both image + audio uploaded!
        with m4_factory() as m4_db:
            jr = m4_db.get(M4Job, j_aud_id)
            assert jr is not None and jr.qc_result is not None
            assert any(c["name"] == "audio_track" and c["status"] == "fail" for c in jr.qc_result["checks"])

        # Clear active r_aud run, then test no-audio shot preserves NEEDS_REVIEW when ffprobe/ffmpeg missing
        client.post(f"/api/v1/manual4/projects/{pid2}/production/runs/{r_aud_id}/cancel", headers=LOCAL_ACTION_HEADERS)
        r_no_aud = client.post(
            f"/api/v1/manual4/projects/{pid2}/production/runs",
            headers=LOCAL_ACTION_HEADERS,
            json={"scope_kind": "shot", "scope_target_ids": [shots2[2]["id"]], "provider": "comfyui"},
        )
        assert r_no_aud.status_code == 201
        r_na_id = r_no_aud.json()["run"]["id"]
        j_na_id = r_no_aud.json()["run"]["jobs"][0]["id"]
        with m4_factory() as m4_db:
            r_na_row = m4_db.get(M4ProductionRun, r_na_id)
            s_na = m4_db.get(M4ExecutionSnapshot, r_na_row.execution_snapshot_id)
            na_item = s_na.items_json[0]
            na_run_info = {
                "id": r_na_row.id,
                "project_id": pid2,
                "provider": "comfyui",
                "execution_generation": 2,
                "config": s_na.config_json,
            }
        res_nr = _execute_single_job(
            m4_factory,
            m4_root,
            na_run_info,
            j_na_id,
            na_item,
            worker_id="w-test",
            comfy_client=_FakeComfyClient(),
            platform_factory=app.state.session_factory,
            settings=Settings(
                data_dir=tmp_path,
                database_url=settings.database_url,
                ffprobe_path="nonexistent_ffprobe",
                ffmpeg_path="nonexistent_ffmpeg",
            ),
        )
        assert res_nr == "NEEDS_REVIEW"
