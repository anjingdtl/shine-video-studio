"""Independent closeout regressions; synthetic media and isolated human operator only."""

import os
import subprocess
import sys
from pathlib import Path

import pytest

from shine.comfyops.assets import fake_image
from shine.comfyops.installer import LocalInstaller
from shine.comfyops.scheduler import ResourceScheduler
from tests.conftest import create_project, create_shot, freeze_style, upload_asset
from tests.test_v13_preparation import (  # noqa: F401
    HEADERS, approve, candidate, post, prep, rights, workflow,
)
from shine.voiceops.providers import fake_audio


def clone_request(client, project):
    asset_right = rights(client, project)
    audio, _ = fake_audio("声音参考", "Lin")
    ref = client.post(
        f"/api/v1/projects/{project}/candidate-assets/import",
        data={"family": "voice", "entity_id": "Lin", "rights_id": asset_right["id"]},
        files={"file": ("reference.wav", audio, "audio/wav")},
    ).json()
    consent = rights(client, project, "voice_clone", reference_candidate_id=ref["id"])
    return {
        "character": "Lin", "description": "test", "text": "声音参考",
        "rights_id": consent["id"], "asset_rights_id": asset_right["id"],
        "source": "AUTHORIZED_CLONE", "reference_id": ref["id"],
    }


def test_a01_new_bytes_legacy_approval_requires_operator(prep):
    client, _, _ = prep
    project = create_project(client)
    asset = upload_asset(client, project, fake_image("independent-new-bytes"))
    for action in ("approve", "lock"):
        response = client.post(f"/api/v1/assets/{asset['id']}/{action}")
        assert response.status_code == 403, response.text


def test_a02_design_clone_requires_execution_identity(prep):
    client, _, _ = prep
    project = create_project(client)
    body = clone_request(client, project)
    response = client.post(f"/api/v1/projects/{project}/voice-profiles/design", json=body)
    assert response.status_code == 403, response.text


def test_a04_clone_respects_h3_exclusive_lease(prep):
    client, factory, _ = prep
    project = create_project(client)
    body = clone_request(client, project)
    with factory.begin() as db:
        lease = ResourceScheduler().enqueue(db, "independent-h3", 16384, True, 100)
        identity = lease.id
    with factory() as db:
        assert ResourceScheduler().admit(db, identity)
    response = client.post(f"/api/v1/projects/{project}/voice-profiles/clone", json=body, headers=HEADERS)
    assert response.status_code == 409, response.text


def test_a03_extra_revoked_reference_cannot_use_frozen_manifest(prep):
    client, _, _ = prep
    project = create_project(client)
    from tests.test_film_crew import ENGLISH_STYLE

    client.put(f"/api/v1/projects/{project}/style/draft", json=ENGLISH_STYLE)
    style = post(client, f"/projects/{project}/style/freeze", expected=201)
    doc = client.post(f"/api/v1/projects/{project}/documents", files={"file": ("s.txt", b"A rainy street.")}).json()
    plan = post(client, f"/projects/{project}/preproduction/analyze", {"document_id": doc["id"], "h3_category": "H3_T2VA"})
    for category in ("H3_T2VA", "VLM_REVIEW", "ASR"):
        workflow(client, category)
    post(client, f"/projects/{project}/rulepack/freeze")
    approve(client, plan, "manifest_freeze", f"/projects/{project}/preproduction/freeze")
    right = rights(client, project)
    row = candidate(client, project, right, workflow(client), entity="extra")
    row = approve(client, row, "promote", f"/candidate-assets/{row['id']}/promote")
    post(client, f"/preparation/rights/{right['id']}/revoke", operator=True)
    shot = create_shot(client, project, style["id"], beat="Rain falls on an empty street.", bindings=[{"asset_id": row["formal_asset_id"], "use_as": "picture", "ordinal": 0}])
    response = client.post(f"/api/v1/projects/{project}/jobs", json={"shot_id": shot["id"]})
    assert response.status_code == 409, response.text


def test_a05_recover_dead_installer_allows_reinstall(tmp_path):
    # A real child process dies inside a Fake downloader; no network or third-party execution.
    script = '''
import os, sys
from pathlib import Path
from shine.comfyops.installer import LocalInstaller, PackageSpec
class FakeDownloader:
    def download(self, *args):
        os._exit(23)
spec = PackageSpec(name="crashed", kind="model", revision="v1", license_id="TEST", usage="research", files=[dict(url="https://example.com/f", relative_path="f", size=1, sha256="a"*64)])
LocalInstaller(Path(sys.argv[1]), FakeDownloader()).install(spec)
'''
    result = subprocess.run([sys.executable, "-c", script, str(tmp_path)], check=False, env=os.environ.copy())
    assert result.returncode == 23
    assert (tmp_path / "crashed.lock").exists()
    LocalInstaller(tmp_path, None).recover("crashed")
    assert not (tmp_path / "crashed.lock").exists()
