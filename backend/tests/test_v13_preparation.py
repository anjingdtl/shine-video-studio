"""Software acceptance only; all human credentials here belong to an isolated test operator."""

import hashlib
import json
import subprocess
import zipfile
from contextlib import contextmanager
from dataclasses import replace
from datetime import timedelta

import httpx
import pytest
from fastapi import HTTPException
from sqlalchemy import select

from shine.comfyops.installer import (
    ComfyCLIAdapter,
    ControlledDownloader,
    LocalInstaller,
    ManagerAdapter,
    PackageFile,
    PackageSpec,
    check_url,
    extract_zip,
    safe_target,
)
from shine.comfyops.inventory import WindowsHostProbe
from shine.comfyops.models import ApprovalGrant, CandidateAsset, ResourceLease
from shine.comfyops.scheduler import ResourceScheduler
from shine.models import Asset, utcnow
from shine.voiceops.providers import FakeVoiceProvider, LocalVoiceHTTPProvider, subtitles, verify_dialogue
from tests.conftest import create_project, create_shot, freeze_style

TOKEN = "isolated-v13-test-operator-credential-12345"
HEADERS = {"Authorization": "Bearer " + TOKEN}


@pytest.fixture
def prep(api):
    client, factory, settings = api
    client.app.state.settings = replace(
        settings, operator_id="v13-test-operator", operator_token_sha256=hashlib.sha256(TOKEN.encode()).hexdigest()
    )
    return client, factory, client.app.state.settings


def post(client, path, body=None, *, operator=False, expected=200):
    r = client.post("/api/v1" + path, json=body or {}, headers=HEADERS if operator else {})
    assert r.status_code == expected, r.text
    return r.json()


def approve(client, row, action, path, **extra):
    grant = post(client, "/preparation/approval-grants", {"action": action, "target_id": row["id"]}, operator=True)
    return post(
        client,
        path,
        {
            "grant_id": grant["grant_id"],
            "snapshot": grant["snapshot"],
            "offline": True,
            "evidence": "Explicit isolated test operator decision",
            "listened": action == "voice_freeze",
            **extra,
        },
        operator=True,
    )


def rights(client, project, scope="asset", **extra):
    r = post(
        client,
        "/preparation/rights",
        {
            "project_id": project,
            "scope": scope,
            "license_id": "TEST-ONLY",
            "evidence": "synthetic fixture ownership",
            "purpose": "offline test",
            **extra,
        },
    )
    return approve(client, r, "rights_approve", f"/preparation/rights/{r['id']}/approve")


def workflow(client, category="IMAGE_GENERATION", model_ids=None, **overrides):
    body = {
        "name": "test " + category,
        "category": category,
        "graph": {"1": {"class_type": "ShineFake", "inputs": {"prompt": ""}}},
        "parameter_map": {"prompt": "1.inputs.prompt"},
        "model_ids": model_ids or [],
        "model_family": "MiniMax-H3" if category.startswith("H3_") else "",
    }
    body.update(overrides)
    row = post(client, "/workflow-capabilities/drafts", body)
    object_info = {
        n["class_type"]: {"input": {"required": {k: ["STRING"] for k in n["inputs"]}}} for n in body["graph"].values()
    }
    check = post(
        client,
        f"/workflow-capabilities/{row['id']}/validate",
        {"object_info": object_info},
    )
    assert not check["errors"]
    return post(client, f"/workflow-capabilities/{row['id']}/fake-execute")


def candidate(client, project, right, wf, family="character", entity="Lin"):
    row = post(
        client,
        f"/projects/{project}/candidate-assets/generate",
        {
            "family": family,
            "entity_id": entity,
            "rights_id": right["id"],
            "prompt": "synthetic fixture",
            "workflow_id": wf["id"],
        },
    )
    return post(client, f"/candidate-assets/{row['id']}/qc")


def package(client, **extra):
    spec = {
        "name": "test-model",
        "kind": "model",
        "revision": "v1.0.0",
        "license_id": "TEST-ONLY",
        "usage": "research",
        "files": [
            {
                "url": "https://huggingface.co/owner/model/resolve/abc/model.bin",
                "relative_path": "test.bin",
                "size": 4,
                "sha256": "a" * 64,
            }
        ],
        **extra,
    }
    return post(client, "/comfyops/packages", spec)


def test_inventory_install_and_workflow_offline(prep):
    client, _, _ = prep
    project = create_project(client)
    snapshot = post(client, "/comfyops/inventory/refresh")
    assert snapshot["payload"]["is_mock"] and snapshot["status"] == "MOCK_VERIFIED"
    assert len(snapshot["payload"]["instances"]) == 2
    model = package(client)
    plan = post(
        client, "/comfyops/install-plans", {"project_id": project, "package_ids": [model["id"]], "disk_free": 100}
    )
    post(client, f"/comfyops/install-plans/{plan['id']}/execute", {"plan_hash": plan["sha256"]}, expected=403)
    approved = approve(client, plan, "install", f"/comfyops/install-plans/{plan['id']}/approve")
    post(
        client,
        f"/comfyops/install-plans/{plan['id']}/execute",
        {"plan_hash": plan["sha256"]},
        operator=True,
        expected=409,
    )
    execution = post(
        client, f"/comfyops/install-plans/{plan['id']}/execute", {"plan_hash": approved["sha256"]}, operator=True
    )
    assert execution["payload"]["system_changes"] is False
    post(
        client,
        f"/comfyops/install-plans/{plan['id']}/execute",
        {"plan_hash": approved["sha256"]},
        operator=True,
        expected=409,
    )
    wf = workflow(client, model_ids=[model["id"]])
    assert wf["status"] == "FAKE_EXECUTED"
    post(
        client,
        f"/workflow-capabilities/{wf['id']}/publish",
        {"grant_id": "bad", "snapshot": "bad", "evidence": "test evidence"},
        operator=True,
        expected=409,
    )
    matches = post(client, "/workflow-capabilities/match", {"category": "IMAGE_GENERATION"})
    assert matches["matches"][0]["id"] == wf["id"]
    assert post(client, "/workflow-capabilities/match", {"category": "IMAGE_GENERATION", "offline": False})["blockers"]


def test_candidate_human_boundary_and_replay(prep):
    client, factory, _ = prep
    project = create_project(client)
    right = rights(client, project)
    wf = workflow(client)
    row = candidate(client, project, right, wf)
    with factory() as db:
        assert not db.scalars(select(Asset).where(Asset.project_id == project)).all()
    post(
        client,
        f"/candidate-assets/{row['id']}/promote",
        {"grant_id": "agent", "snapshot": row["sha256"], "evidence": "AI approves"},
        expected=403,
    )
    grant = post(client, "/preparation/approval-grants", {"action": "promote", "target_id": row["id"]}, operator=True)
    body = {**{k: grant[k] for k in ("grant_id", "snapshot")}, "offline": True, "evidence": "Test user decision"}
    promoted = post(client, f"/candidate-assets/{row['id']}/promote", body, operator=True)
    assert promoted["formal_asset_id"]
    post(client, f"/candidate-assets/{row['id']}/promote", body, operator=True, expected=409)
    with factory() as db:
        asset = db.get(Asset, promoted["formal_asset_id"])
        assert asset.approved_by == "v13-test-operator" and asset.provenance["offline_only"]
        assert db.get(ApprovalGrant, grant["grant_id"]).used


@pytest.mark.parametrize("change", ["tamper", "cross-origin", "expired", "wrong-action", "real-mock"])
def test_approval_adversarial(prep, change):
    client, factory, settings = prep
    project = create_project(client)
    row = candidate(client, project, rights(client, project), workflow(client))
    grant = post(client, "/preparation/approval-grants", {"action": "promote", "target_id": row["id"]}, operator=True)
    body = {k: grant[k] for k in ("grant_id", "snapshot")}
    body.update(evidence="Test user decision", offline=change != "real-mock")
    if change == "tamper":
        path = settings.data_dir / row["payload"]["storage_path"]
        path.write_bytes(b"changed")
    if change == "expired":
        with factory.begin() as db:
            db.get(ApprovalGrant, grant["grant_id"]).expires_at = utcnow() - timedelta(seconds=1)
    if change == "wrong-action":
        with factory.begin() as db:
            db.get(ApprovalGrant, grant["grant_id"]).action = "install"
    headers = {**HEADERS, **({"Origin": "https://evil.example"} if change == "cross-origin" else {})}
    r = client.post(f"/api/v1/candidate-assets/{row['id']}/promote", json=body, headers=headers)
    assert r.status_code in {403, 409}
    with factory() as db:
        assert db.get(CandidateAsset, row["id"]).formal_asset_id is None


def test_cross_project_generation_and_clone_consent(prep):
    client, _, _ = prep
    p1, p2 = create_project(client), create_project(client)
    right = rights(client, p1)
    wf = workflow(client)
    post(
        client,
        f"/projects/{p2}/candidate-assets/generate",
        {
            "family": "character",
            "entity_id": "Lin",
            "rights_id": right["id"],
            "prompt": "test",
            "workflow_id": wf["id"],
        },
        expected=409,
    )
    voice_right = rights(client, p1, "voice_design")
    post(
        client,
        f"/projects/{p1}/voice-profiles/design",
        {
            "character": "Lin",
            "description": "test",
            "rights_id": voice_right["id"],
            "asset_rights_id": right["id"],
            "source": "AUTHORIZED_CLONE",
        },
        expected=409,
    )


def test_voice_design_freeze_dialogue_asr_alignment_subtitles(prep):
    client, _, _ = prep
    project = create_project(client)
    asset_right, voice_right = rights(client, project), rights(client, project, "voice_design")
    voice = post(
        client,
        f"/projects/{project}/voice-profiles/design",
        {
            "character": "Lin",
            "description": "warm original",
            "rights_id": voice_right["id"],
            "asset_rights_id": asset_right["id"],
        },
    )
    preview = post(client, f"/candidate-assets/{voice['payload']['candidate_id']}/qc")
    post(
        client,
        f"/voice-profiles/{voice['id']}/dialogue",
        {"character": "Lin", "text": "你好", "voice_hash": voice["sha256"]},
        expected=409,
    )
    approve(client, preview, "promote", f"/candidate-assets/{preview['id']}/promote")
    voice = approve(client, voice, "voice_freeze", f"/voice-profiles/{voice['id']}/approve-and-freeze")
    body = {
        "character": "Lin",
        "text": "你终于来了。",
        "voice_hash": voice["sha256"],
        "emotion": "calm",
        "speed": 1.1,
        "pause": 0.2,
    }
    render = post(client, f"/voice-profiles/{voice['id']}/dialogue", body)
    assert render["status"] == "MOCK_VERIFIED"
    assert render["payload"]["transcript"]["text"] == body["text"]
    assert "".join(w["text"] for w in render["payload"]["words"]) == body["text"]
    assert "-->" in render["payload"]["subtitles"]["srt"]
    assert "[Events]" in render["payload"]["subtitles"]["ass"]
    assert client.get(f"/api/v1/voice-renders/{render['id']}/subtitles?format=srt").status_code == 200
    correction = approve(
        client,
        render,
        "subtitle_correct",
        f"/voice-renders/{render['id']}/correct-subtitles",
        words=render["payload"]["words"],
    )
    assert correction["id"] != render["id"]
    assert correction["payload"]["is_mock"] is True
    assert correction["payload"]["parent_render_id"] == render["id"]

    post(client, f"/voice-profiles/{voice['id']}/dialogue", {**body, "character": "Other"}, expected=409)
    post(client, f"/voice-profiles/{voice['id']}/dialogue", {**body, "voice_hash": "old"}, expected=409)

    class BadVoice(FakeVoiceProvider):
        def transcribe(self, audio):
            return {**super().transcribe(audio), "text": "错误"}

    client.app.state.voice_provider = BadVoice()
    failed = post(client, f"/voice-profiles/{voice['id']}/dialogue", body)
    assert failed["status"] == "QC_FAILED"
    failed_candidate = post(client, f"/candidate-assets/{failed['payload']['candidate_id']}/qc")
    assert failed_candidate["status"] == "QC_FAILED"
    post(client, f"/preparation/rights/{voice_right['id']}/revoke", operator=True)
    post(client, f"/voice-profiles/{voice['id']}/dialogue", body, expected=409)


def test_preproduction_missing_blocks_existing_job_endpoint(prep):
    client, _, _ = prep
    project = create_project(client)
    style = freeze_style(client, project)
    shot = create_shot(client, project, style["id"])
    doc = client.post(
        f"/api/v1/projects/{project}/documents", files={"file": ("test.txt", "Lin：你好。", "text/plain")}
    ).json()
    post(client, f"/projects/{project}/preproduction/analyze", {"document_id": doc["id"]})
    report = post(client, f"/projects/{project}/preproduction/readiness")
    assert report["status"] == "BLOCKED"
    assert {r["family"] for r in report["requirements"]} >= {"character", "costume", "voice", "subtitle", "storyboard"}
    post(client, f"/projects/{project}/jobs", {"shot_id": shot["id"]}, expected=409)


@pytest.mark.parametrize(
    "path",
    [
        "../secret",
        "C:\\secret",
        "/tmp/secret",
        "x/../../evil",
        "a:stream",
        "CON.bin",
        "x/NUL",
        "a./b",
        "\\\\server\\share",
    ],
)
def test_windows_paths_rejected(tmp_path, path):
    with pytest.raises(ValueError):
        safe_target(tmp_path, path)


@pytest.mark.parametrize(
    "url",
    [
        "http://huggingface.co/file",
        "https://127.0.0.1/x",
        "https://huggingface.co@evil.com/x",
        "https://huggingface.co:8443/x",
        "https://huggingface.co/x?redirect=http://localhost",
        "file:///tmp/x",
    ],
)
def test_download_ssrf_rejected(url):
    with pytest.raises(ValueError):
        check_url(url, {"huggingface.co"})


class BytesTransport:
    def __init__(self, content=b"data", *, ignore_range=False, redirect=False, fail=False):
        self.content, self.ignore_range, self.redirect, self.fail = content, ignore_range, redirect, fail
        self.offsets = []

    @contextmanager
    def stream(self, url, offset):
        self.offsets.append(offset)
        if self.fail:
            raise OSError("simulated network failure")
        if self.redirect:
            yield httpx.Response(302, headers={"location": "http://127.0.0.1/secret"})
        else:
            partial = offset > 0 and not self.ignore_range
            response = httpx.Response(
                206 if partial else 200,
                content=self.content[offset:] if partial else self.content,
                headers={"content-range": f"bytes {offset}-{len(self.content) - 1}/{len(self.content)}"}
                if partial
                else {},
            )
            yield response


def file_spec():
    return PackageFile(
        url="https://huggingface.co/test/data",
        relative_path="中文/test.bin",
        size=4,
        sha256=hashlib.sha256(b"data").hexdigest(),
    )


def test_production_downloader_resume_checksum_cache_and_atomic_install(tmp_path):
    spec = file_spec()
    cache = tmp_path / "cache"
    cache.mkdir()
    (cache / (spec.sha256 + ".part")).write_bytes(b"da")
    http = BytesTransport()
    downloader = ControlledDownloader({"huggingface.co"}, http)
    assert downloader.download(spec, cache).read_bytes() == b"data"
    assert http.offsets == [2]
    downloader.download(spec, cache)
    assert http.offsets == [2]
    package = PackageSpec(name="model", kind="model", revision="v1", license_id="TEST", usage="research", files=[spec])
    installer = LocalInstaller(tmp_path / "隔离实例", downloader)
    assert installer.install(package)["status"] == "AVAILABLE"
    assert (tmp_path / "隔离实例/model/中文/test.bin").read_bytes() == b"data"
    assert installer.install(package)["status"] == "AVAILABLE"


@pytest.mark.parametrize("mode", ["corrupt", "oversize", "redirect", "ignore-range"])
def test_production_download_failures_and_restart(tmp_path, mode):
    spec = file_spec()
    transport = BytesTransport(
        content=b"evil" if mode == "corrupt" else b"longer" if mode == "oversize" else b"data",
        redirect=mode == "redirect",
        ignore_range=mode == "ignore-range",
    )
    downloader = ControlledDownloader({"huggingface.co"}, transport)
    (tmp_path / (spec.sha256 + ".part")).write_bytes(b"da")
    if mode == "ignore-range":
        assert downloader.download(spec, tmp_path).read_bytes() == b"data"
    else:
        with pytest.raises(ValueError):
            downloader.download(spec, tmp_path)
        assert not (tmp_path / spec.sha256).exists()


def test_install_failure_preserves_previous_and_crash_rollback(tmp_path):
    root = tmp_path / "isolated"
    (root / "model").mkdir(parents=True)
    (root / "model/old").write_text("keep")
    package = PackageSpec(
        name="model", kind="model", revision="v1", license_id="TEST", usage="research", files=[file_spec()]
    )
    installer = LocalInstaller(root, ControlledDownloader({"huggingface.co"}, BytesTransport(fail=True)))
    with pytest.raises(OSError):
        installer.install(package)
    assert (root / "model/old").read_text() == "keep"
    (root / "model").rename(root / "model.rollback")
    (root / "model").mkdir()
    (root / "model.journal").write_text("INSTALLED")
    assert installer.recover("model")["status"] == "ROLLED_BACK"
    assert (root / "model/old").read_text() == "keep"


@pytest.mark.parametrize("name", ["../evil.py", "/evil.py", "C:\\evil.py"])
def test_zip_slip(tmp_path, name):
    path = tmp_path / "input.zip"
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr(name, "bad")
    with pytest.raises(ValueError):
        extract_zip(path, tmp_path / "extract")


def test_cli_adapters_build_fixed_commands(tmp_path):
    binary = tmp_path / "cli"
    binary.write_bytes(b"fake executable")
    digest = hashlib.sha256(binary.read_bytes()).hexdigest()
    calls = []

    class Executor:
        def run(self, args, *, cwd):
            calls.append((args, cwd))

    ComfyCLIAdapter(binary, digest, Executor()).snapshot(tmp_path)
    ManagerAdapter(binary, binary, digest, Executor()).snapshot(tmp_path, tmp_path / "snapshot.json")
    assert calls[0][0][-2:] == ["node", "save-snapshot"]
    assert calls[1][0][-3:] == ["save-snapshot", "--output", str(tmp_path / "snapshot.json")]
    with pytest.raises(ValueError):
        ComfyCLIAdapter(binary, "a" * 64, Executor()).snapshot(tmp_path)


def test_windows_production_probe_fake_executor_and_http(tmp_path):
    root = tmp_path / "中文 Comfy"
    (root / "models").mkdir(parents=True)
    (root / "models/a.gguf").write_bytes(b"fixture")
    (root / "user/default/workflows").mkdir(parents=True)
    (root / "user/default/workflows/api.json").write_text('{"1":{"class_type":"Fake","inputs":{}}}')

    def run(args, **kwargs):
        if args[0] == "nvidia-smi":
            return subprocess.CompletedProcess(args, 0, stdout="Fake RTX 5080, 16384, 16000, test\n")
        return subprocess.CompletedProcess(args, 0, stdout='{"TotalVisibleMemorySize":33554432}')

    client = httpx.Client(transport=httpx.MockTransport(lambda r: httpx.Response(200, json={})))
    result = WindowsHostProbe([root], [8188, 8189], run=run, http=client).scan()
    assert len(result["instances"]) == 2 and result["instances"][0]["health"] == "ONLINE"
    assert result["roots"][0]["models"][0]["sha256"] is None
    assert result["roots"][0]["workflows"][0]["format"] == "API"
    assert result["gpu"][0][1] == "16384"


def test_gpu_exclusive_priority_budget_and_crash_reconciliation(prep):
    _, factory, _ = prep
    scheduler = ResourceScheduler()
    with factory.begin() as db:
        image = scheduler.enqueue(db, "image", 10000)
        h3 = scheduler.enqueue(db, "H3", 16000, True, 100)
        ids = image.id, h3.id
    with factory() as db:
        assert not scheduler.admit(db, ids[0])
    with factory() as db:
        assert scheduler.admit(db, ids[1])
    with factory.begin() as db:
        db.get(ResourceLease, ids[1]).expires_at = utcnow() - timedelta(seconds=1)
    with factory() as db:
        assert not scheduler.admit(db, ids[0])
    with factory.begin() as db:
        assert db.get(ResourceLease, ids[1]).status == "RECONCILE_REQUIRED"
        with pytest.raises(HTTPException):
            scheduler.release(db, ids[1], "H3")
        scheduler.release(db, ids[1], "H3", reconciled=True)
    with factory() as db:
        assert scheduler.admit(db, ids[0])
    with factory.begin() as db:
        with pytest.raises(HTTPException):
            scheduler.enqueue(db, "oversize", 17000)
        with pytest.raises(HTTPException):
            scheduler.release(db, ids[0], "other-owner")


def test_voice_http_protocol_and_bad_asr_alignment():
    fake = FakeVoiceProvider()

    def handler(request):
        body = json.loads(request.content)
        if request.url.path == "/tts":
            import base64

            result = fake.synthesize(body["text"], body["character"])
            result["audio_base64"] = base64.b64encode(result.pop("audio")).decode()
            return httpx.Response(200, json=result)
        import base64

        audio = base64.b64decode(body["audio_base64"])
        return httpx.Response(
            200,
            json=fake.transcribe(audio) if request.url.path == "/asr" else {"words": fake.align(audio, body["text"])},
        )

    provider = LocalVoiceHTTPProvider("http://127.0.0.1:1234", httpx.Client(transport=httpx.MockTransport(handler)))
    result = provider.synthesize("你好", "Lin", "hash", "calm", 1, 0)
    transcript = provider.transcribe(result["audio"])
    words = provider.align(result["audio"], "你好")
    assert not verify_dialogue(result, transcript, words, "你好", "Lin")
    transcript["text"] = "错误"
    assert "DIALOGUE_TEXT_MISMATCH" in verify_dialogue(result, transcript, words, "你好", "Lin")
    words[0]["end_s"] = 100
    assert "ALIGNMENT_TIME_INVALID" in verify_dialogue(result, transcript, words, "你好", "Lin")
    assert "{evil}" not in subtitles([{"text": "{evil}", "start_s": 0, "end_s": 1}])["ass"]


def test_local_install_business_service_runs_production_code_with_fake_http(prep):
    from shine.comfyops.local import execute_approved
    from shine.comfyops.models import InstallationPlan, ModelPackage

    client, factory, settings = prep
    project = create_project(client)
    model = package(client, files=[file_spec().model_dump()])
    plan = post(
        client,
        "/comfyops/install-plans",
        {"project_id": project, "package_ids": [model["id"]], "disk_free": 100, "executor": "local"},
    )
    plan = approve(client, plan, "install", f"/comfyops/install-plans/{plan['id']}/approve")
    # The Web API refuses any local side effects even for an approved local plan.
    post(
        client,
        f"/comfyops/install-plans/{plan['id']}/execute",
        {"executor": "local", "plan_hash": plan["sha256"]},
        operator=True,
        expected=409,
    )
    installer = LocalInstaller(
        settings.data_dir / "isolated-test", ControlledDownloader({"huggingface.co"}, BytesTransport())
    )
    identity = execute_approved(factory, settings, plan["id"], plan["sha256"], "v13-test-operator", installer)
    assert identity
    with factory() as db:
        assert db.get(InstallationPlan, plan["id"]).status == "FILES_VERIFIED_PENDING_RUNTIME"
        assert db.get(ModelPackage, model["id"]).status == "FILES_VERIFIED_PENDING_RUNTIME"
    with pytest.raises(ValueError):
        execute_approved(factory, settings, plan["id"], plan["sha256"], "v13-test-operator", installer)


def test_model_unload_requires_idle_queue_and_never_kills_process():
    from shine.comfyops.inventory import ComfyModelLifecycle

    calls = []
    busy = True

    def handler(request):
        calls.append(request)
        if request.url.path == "/queue":
            return httpx.Response(200, json={"queue_running": ["busy"] if busy else [], "queue_pending": []})
        assert json.loads(request.content) == {"unload_models": True, "free_memory": True}
        return httpx.Response(200)

    lifecycle = ComfyModelLifecycle(
        "http://127.0.0.1:8188", client=httpx.Client(transport=httpx.MockTransport(handler))
    )
    with pytest.raises(ValueError):
        lifecycle.release_models()
    assert len(calls) == 1
    busy = False
    assert lifecycle.release_models()["process_killed"] is False


def test_candidate_legacy_upload_bypass_and_corrupt_decode_rejected(prep):
    client, _, _ = prep
    project = create_project(client)
    row = candidate(client, project, rights(client, project), workflow(client))
    media = client.get(f"/api/v1/candidate-assets/{row['id']}/media").content
    response = client.post(
        f"/api/v1/projects/{project}/assets",
        data={"asset_kind": "image"},
        files={"file": ("fake.png", media, "image/png")},
    )
    assert response.status_code == 409
    right = rights(client, project)
    reimport = client.post(
        f"/api/v1/projects/{project}/candidate-assets/import",
        data={"family": "character", "entity_id": "Copy", "rights_id": right["id"]},
        files={"file": ("copy.png", media, "image/png")},
    ).json()
    assert reimport["payload"]["provenance"]["is_mock"] is True
    response = client.post(
        f"/api/v1/projects/{project}/candidate-assets/import",
        data={"family": "character", "entity_id": "Bad", "rights_id": right["id"]},
        files={"file": ("bad.png", b"\x89PNG\r\n\x1a\nbroken", "image/png")},
    )
    assert response.status_code == 200
    broken = post(client, f"/candidate-assets/{response.json()['id']}/qc")
    assert broken["status"] == "QC_FAILED"
    grant = post(
        client, "/preparation/approval-grants", {"action": "promote", "target_id": broken["id"]}, operator=True
    )
    post(
        client,
        f"/candidate-assets/{broken['id']}/promote",
        {k: grant[k] for k in ("grant_id", "snapshot")} | {"evidence": "cannot pass corrupted image"},
        operator=True,
        expected=409,
    )


@pytest.mark.parametrize("kind", ["slot", "version", "node", "cycle", "mapping"])
def test_workflow_static_negative_contracts(prep, kind):
    client, _, _ = prep
    graph = {"1": {"class_type": "Known", "inputs": {"prompt": ""}}}
    body = {
        "name": "negative test",
        "category": "IMAGE_GENERATION",
        "graph": graph,
        "parameter_map": {"prompt": "1.inputs.prompt"},
    }
    if kind == "slot":
        body["reference_slots"] = ["reference_audio_1"]
    if kind == "version":
        body["node_versions"] = {"Known": "v2"}
    if kind == "cycle":
        graph["1"]["inputs"]["prompt"] = ["1", 0]
    if kind == "mapping":
        body["parameter_map"]["prompt"] = "99.inputs.prompt"
    row = post(client, "/workflow-capabilities/drafts", body)
    response = client.post(
        f"/api/v1/workflow-capabilities/{row['id']}/validate",
        json={
            "object_info": {} if kind == "node" else {"Known": {"input": {"required": {"prompt": ["STRING"]}}}},
            "node_versions": {"Known": "v1"},
        },
    )
    assert response.status_code == 422 if kind in {"cycle", "mapping"} else response.json()["errors"]


def test_local_image_provider_contract_uses_fake_http_and_scoped_references():
    import base64

    from shine.comfyops.assets import fake_image
    from shine.comfyops.images import LocalImageHTTPProvider

    content = fake_image("test")
    seen = []

    def handler(request):
        body = json.loads(request.content)
        seen.append(body)
        assert base64.b64decode(body["references"][0]) == b"authorized fixture"
        return httpx.Response(
            200,
            json={
                "content_base64": base64.b64encode(content).decode(),
                "filename": "fixture.png",
                "is_mock": True,
                "model": "fake-image",
            },
        )

    provider = LocalImageHTTPProvider("http://127.0.0.1:1234", httpx.Client(transport=httpx.MockTransport(handler)))
    result = provider.generate("test", 7, {"category": "IMAGE_EDIT"}, [b"authorized fixture"])
    assert result["content"] == content and result["is_mock"]
    assert len(seen) == 1
    with pytest.raises(HTTPException):
        LocalImageHTTPProvider("https://external.example")


def test_failed_model_release_holds_gpu_for_reconciliation(prep):
    from shine.comfyops.scheduler import resource_slot

    _, factory, _ = prep

    def fail_release():
        raise OSError("fake release failure")

    with pytest.raises(OSError):
        with resource_slot(factory, "TTS", 4000, release_models=fail_release):
            pass
    with factory() as db:
        lease = db.scalar(select(ResourceLease).where(ResourceLease.status == "RECONCILE_REQUIRED"))
        assert lease


def test_fake_inventory_cannot_publish_real_capability(prep):
    client, _, _ = prep
    snapshot = post(client, "/comfyops/inventory/refresh")
    assert snapshot["payload"]["is_mock"]
    instances = client.get("/api/v1/comfyops/instances").json()
    row = workflow(client, "ASR")
    grant = post(
        client, "/preparation/approval-grants", {"action": "workflow_validate", "target_id": row["id"]}, operator=True
    )
    post(
        client,
        f"/workflow-capabilities/{row['id']}/local-execution-evidence",
        {k: grant[k] for k in ("grant_id", "snapshot")}
        | {
            "instance_id": instances[0]["id"],
            "prompt_id": "test-only",
            "output_sha256": "a" * 64,
            "offline": False,
            "evidence": "Fake evidence must never become real",
        },
        operator=True,
        expected=409,
    )


def test_v13_style_freeze_requires_human_grant_for_current_draft(prep):
    from tests.conftest import style_payload

    client, _, _ = prep
    project = create_project(client)
    doc = client.post(
        f"/api/v1/projects/{project}/documents", files={"file": ("script.txt", "Lin：你好。".encode(), "text/plain")}
    ).json()
    post(client, f"/projects/{project}/preproduction/analyze", {"document_id": doc["id"]})
    client.put(f"/api/v1/projects/{project}/style/draft", json=style_payload()).raise_for_status()
    post(client, f"/projects/{project}/style/freeze", expected=403)
    grant = post(
        client, "/preparation/approval-grants", {"action": "style_freeze", "target_id": project}, operator=True
    )
    changed = style_payload() | {"lens_texture": "Changed after the review snapshot"}
    client.put(f"/api/v1/projects/{project}/style/draft", json=changed).raise_for_status()
    decision = {k: grant[k] for k in ("grant_id", "snapshot")}
    post(client, f"/projects/{project}/style/freeze", decision, operator=True, expected=409)
    grant = post(
        client, "/preparation/approval-grants", {"action": "style_freeze", "target_id": project}, operator=True
    )
    decision = {k: grant[k] for k in ("grant_id", "snapshot")}
    frozen = post(client, f"/projects/{project}/style/freeze", decision, operator=True, expected=201)
    assert frozen["approved_by"] == "v13-test-operator"
    post(client, f"/projects/{project}/style/freeze", decision, operator=True, expected=409)
