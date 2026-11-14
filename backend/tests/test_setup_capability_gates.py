"""Capability-scoped resource preparation (2026-11-14, RTX 4070 P1 round).

The reported failure: a person with an RTX 4070 ticked "素材出图" and pressed
"确认并下载安装（约 16.1GiB）".  Nothing downloaded.  The answer was
``HOST_NOT_QUALIFIED`` - the H3 INT8 production admission gate - plus
``[object Object]; [object Object]``.

Two facts are locked here:

* A download is storage bound; *running* a weight is compute bound.  The H3
  production gate belongs to the H3 capability alone, so preparing the image
  weights is never refused because the video variant does not fit this card.
* A capability never loses its own safety conditions.  The H3 production gate
  still refuses a host that cannot run the scheme, and the image capability
  reports its own runtime floor honestly instead of pretending it can run.

The 4070 sample is a *fact injection*, not a real machine: there is no RTX
4070 in this environment and nothing here proves that H3 or Qwen Image runs
on one.  What these tests prove is the request chain and the admission
decisions.
"""

# ruff: noqa: F811 - the shared "prep" fixture is imported on purpose
from __future__ import annotations

import copy

import pytest

from tests.conftest import create_project
from tests.test_v13_preparation import HEADERS, prep  # noqa: F401

IMAGE_CATALOG = "qwen-image-2.1-windows-x64"
IMAGE_COMPONENTS = ["qwen-image-2.1-unet", "qwen-image-2.1-text-encoder", "qwen-image-2.1-vae"]
H3_MODEL_COMPONENTS = ["minimax-h3-diffusion", "minimax-h3-text-encoder", "minimax-h3-vae"]
LOWEND_CATALOG = "h3-ref2va-lowend-candidate-windows-x64"
LOWEND_COMPONENTS = [
    "minimax-h3-diffusion-w6a8", "minimax-h3-text-encoder-nvfp4", "minimax-h3-vae-int8",
]


def _rtx_4070_host(*args, **kwargs) -> dict:
    """The reported machine: 12 GB of VRAM, 32 GB class of RAM, ~500 GB free.

    The RAM is a 32 GB kit that the firmware reserves part of, which is the
    ``HARDWARE_RESERVED_MEMORY`` case the product already knows.  Only the
    VRAM is below the bar the user actually hit.
    """
    from tests.conftest import qualified_host_facts

    facts = copy.deepcopy(qualified_host_facts())
    facts["gpus"][0].update({
        "vram_total_mb": 12282, "vram_free_mb": 12282, "name": "NVIDIA GeForce RTX 4070",
    })
    facts["gpu"].update({
        "vram_mb": 12282, "vram_total_mb": 12282, "vram_free_mb": 12282,
        "name": "NVIDIA GeForce RTX 4070",
    })
    facts["ram"].update({"ram_mb": 31539, "ram_available_mb": 31539, "installed_mb": 32768})
    return facts


def _plan(client, body: dict):
    return client.post("/api/v1/setup/install-plan", json=body, headers=HEADERS)


# --- CASE-01: RTX 4070, H3 INT8 files present, image only ----------------


def test_the_image_weights_are_prepared_on_a_4070_without_the_h3_gate(prep, monkeypatch):
    """CASE-01: the image plan is its own plan and never inherits the H3 gate."""
    client, factory, _ = prep
    monkeypatch.setattr("shine.comfyops.host_env.probe_host_facts", _rtx_4070_host)
    project = create_project(client)

    response = _plan(client, {
        "catalog": IMAGE_CATALOG, "components": IMAGE_COMPONENTS,
        "project_id": project, "disk_free": 500 * 1024 ** 3,
    })
    assert response.status_code == 200, response.text
    body = response.json()
    # The plan carries the image capability and no video variant at all.
    assert body["capability"] == "image"
    assert body["variant_id"] is None
    assert body["qualification"] is None
    assert sorted(body["packages"]) == sorted(IMAGE_COMPONENTS)
    assert body["download_bytes"] > 0
    # The H3 production admission is not part of this plan's answer.
    assert "HOST_NOT_QUALIFIED" not in response.text
    with factory() as db:
        from shine.comfyops.models import InstallationPlan

        plan = db.get(InstallationPlan, body["plan_id"])
        assert plan.payload["variant"] is None
        assert plan.payload["configuration"] == {"h3_video": False, "image_prep": True}


def test_the_image_plan_states_its_own_runtime_floor_honestly(prep, monkeypatch):
    """The image capability reports what it needs to *run* - without blocking.

    A 4070 has 12 GB; the Qwen Image catalog asks for 15 GB.  The download is
    allowed (bytes on disk), but the machine is never told it can run it.
    """
    client, _, _ = prep
    monkeypatch.setattr("shine.comfyops.host_env.probe_host_facts", _rtx_4070_host)
    project = create_project(client)

    body = _plan(client, {
        "catalog": IMAGE_CATALOG, "components": IMAGE_COMPONENTS,
        "project_id": project, "disk_free": 500 * 1024 ** 3,
    }).json()
    assert body["runtime_requirements"]["min_vram_mb"] == 15360
    advisory = next(row for row in body["runtime_advisories"] if row["code"] == "VRAM_BELOW_CAPABILITY_FLOOR")
    assert advisory["required"] == 15360
    assert advisory["actual"] == 12282


def test_the_h3_plan_is_still_refused_on_the_same_4070(prep, monkeypatch):
    """The same host, the same moment, the H3 production plan is refused."""
    client, _, _ = prep
    monkeypatch.setattr("shine.comfyops.host_env.probe_host_facts", _rtx_4070_host)
    project = create_project(client)

    response = _plan(client, {
        "components": H3_MODEL_COMPONENTS, "project_id": project, "disk_free": 500 * 1024 ** 3,
    })
    assert response.status_code == 409, response.text
    detail = response.json()["detail"]
    assert detail["code"] == "HOST_NOT_QUALIFIED"
    assert detail["capability"] == "h3"
    blocker = detail["blockers"][0]
    assert blocker["code"] == "VRAM_INSUFFICIENT"
    assert blocker["required"] == 15360
    assert blocker["actual"] == 12282


# --- CASE-02: the production admission is not relaxed --------------------


def test_int8_production_stays_refused_on_a_4070(prep, monkeypatch):
    """CASE-02: this round does not widen the INT8 production admission."""
    client, _, _ = prep
    monkeypatch.setattr("shine.comfyops.host_env.probe_host_facts", _rtx_4070_host)
    project = create_project(client)

    response = _plan(client, {
        "catalog": "h3-ref2va-windows-x64", "components": H3_MODEL_COMPONENTS,
        "project_id": project, "disk_free": 500 * 1024 ** 3,
        "variant_id": "h3-ref2va-int8-convrot",
    })
    assert response.status_code == 409, response.text
    assert response.json()["detail"]["code"] == "HOST_NOT_QUALIFIED"


def test_an_unreadable_host_still_cannot_prepare_the_production_variant(prep, monkeypatch):
    """A host whose facts cannot be read is unknown, not qualified."""
    client, _, _ = prep
    from tests.test_install_plan_qualification import _unqualified_host

    monkeypatch.setattr("shine.comfyops.host_env.probe_host_facts", _unqualified_host)
    project = create_project(client)
    response = _plan(client, {
        "components": H3_MODEL_COMPONENTS, "project_id": project, "disk_free": 10 ** 15,
    })
    assert response.status_code == 409, response.text
    detail = response.json()["detail"]
    assert detail["code"] == "HOST_NOT_QUALIFIED"
    assert detail["status"] == "UNKNOWN"


# --- CASE-03: the low-VRAM candidate stays a candidate -------------------


def test_the_w6a8_candidate_can_be_prepared_on_a_4070(prep, monkeypatch):
    """CASE-03: explicit confirmation prepares the candidate's weights."""
    client, factory, _ = prep
    monkeypatch.setattr("shine.comfyops.host_env.probe_host_facts", _rtx_4070_host)
    project = create_project(client)

    response = _plan(client, {
        "catalog": LOWEND_CATALOG, "components": LOWEND_COMPONENTS,
        "project_id": project, "disk_free": 500 * 1024 ** 3,
        "variant_id": "ref2va-lowend-candidate",
        "candidate_download_acknowledged": True,
    })
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["capability"] == "h3"
    assert body["variant_id"] == "ref2va-lowend-candidate"
    # Downloading is not acceptance: the candidate is never production ready.
    assert body["qualification"]["status"] != "SUPPORTED"
    with factory() as db:
        from shine.comfyops.models import InstallationPlan

        plan = db.get(InstallationPlan, body["plan_id"])
        assert plan.payload["variant"]["acceptance_status"] == "CANDIDATE"
        assert plan.payload["variant"]["auto_download_allowed"] is False


def test_the_candidate_still_needs_the_explicit_confirmation(prep, monkeypatch):
    client, _, _ = prep
    monkeypatch.setattr("shine.comfyops.host_env.probe_host_facts", _rtx_4070_host)
    project = create_project(client)
    response = _plan(client, {
        "catalog": LOWEND_CATALOG, "components": LOWEND_COMPONENTS,
        "project_id": project, "disk_free": 500 * 1024 ** 3,
        "variant_id": "ref2va-lowend-candidate",
    })
    assert response.status_code == 409, response.text
    assert response.json()["detail"]["code"] == "VARIANT_NOT_DOWNLOADABLE"


# --- CASE-05: the base tools never inherit a GPU threshold ----------------


def test_the_base_tools_are_repaired_without_a_gpu_threshold(prep, monkeypatch):
    """CASE-05: FFmpeg / PowerShell are repaired on a card that fails H3."""
    client, _, _ = prep
    monkeypatch.setattr("shine.comfyops.host_env.probe_host_facts", _rtx_4070_host)

    report = client.get("/api/v1/setup/environment").json()
    tools = [row for row in report.get("findings", []) if str(row.get("code", "")).startswith("TOOL_")]
    assert tools, "a host without the pinned tools reports them"
    for row in tools:
        # A missing tool is a bootstrap fact.  It never blocks the resource
        # preparation scope and it never carries the video GPU threshold.
        assert "RESOURCE_PREPARATION" not in (row.get("scopes") or [])
        assert "VARIANT_QUALIFICATION" not in (row.get("scopes") or [])
    # The repair plan exists and is not refused by the video admission.
    plan = client.get("/api/v1/setup/tool-repair-plan").json()
    assert "plan_id" in plan


# --- CASE-07: one blocked capability never rejects the others ------------


def test_a_blocked_h3_does_not_reject_the_image_capability(prep, monkeypatch):
    """CASE-07 / CASE-09: the legal capability finishes on its own."""
    client, _, _ = prep
    monkeypatch.setattr("shine.comfyops.host_env.probe_host_facts", _rtx_4070_host)
    project = create_project(client)

    refused = _plan(client, {
        "components": H3_MODEL_COMPONENTS, "project_id": project, "disk_free": 500 * 1024 ** 3,
    })
    assert refused.status_code == 409

    accepted = _plan(client, {
        "catalog": IMAGE_CATALOG, "components": IMAGE_COMPONENTS,
        "project_id": project, "disk_free": 500 * 1024 ** 3,
    })
    assert accepted.status_code == 200, accepted.text
    assert accepted.json()["capability"] == "image"


def test_the_review_weights_are_prepared_without_the_video_variant(prep, monkeypatch):
    """The optional review models are their own capability as well."""
    client, _, _ = prep
    monkeypatch.setattr("shine.comfyops.host_env.probe_host_facts", _rtx_4070_host)
    project = create_project(client)

    response = _plan(client, {
        "catalog": "h3-ref2va-windows-x64", "components": ["qwen3-vl-8b-fp8"],
        "project_id": project, "disk_free": 500 * 1024 ** 3,
    })
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["capability"] == "review"
    assert body["variant_id"] is None
    assert body["runtime_requirements"]["VLM"]["min_vram_mb"] == 8000


# --- CASE-08: what is already verified is not downloaded again -----------


def test_a_cached_weight_is_not_downloaded_again(prep, monkeypatch):
    """CASE-08: the plan only asks for the bytes that are really missing.

    The cache is keyed by digest and re-verified before it is reused, so the
    test declares the cache valid instead of shipping 17 GB of weights.
    """
    from shine.comfyops import deployment
    from shine.comfyops.installer import ControlledDownloader

    client, _, settings = prep
    monkeypatch.setattr("shine.comfyops.host_env.probe_host_facts", _rtx_4070_host)
    monkeypatch.setattr(ControlledDownloader, "valid", staticmethod(lambda path, spec: True))
    project = create_project(client)

    catalog = deployment.load_deployment_catalog(IMAGE_CATALOG)
    from shine.comfyops.storage import require_storage

    cache = require_storage(settings) / "isolated-packages" / ".cache"
    cache.mkdir(parents=True, exist_ok=True)
    for component in catalog["components"]:
        for entry in component["files"]:
            (cache / entry["sha256"]).write_bytes(b"")

    body = _plan(client, {
        "catalog": IMAGE_CATALOG, "components": IMAGE_COMPONENTS,
        "project_id": project, "disk_free": 500 * 1024 ** 3,
    }).json()
    assert body["download_bytes"] == 0, body
    assert body["disk_required"] > 0


def test_the_same_plan_is_not_created_twice(prep, monkeypatch):
    """CASE-08: asking twice returns the plan that is already waiting."""
    client, _, _ = prep
    monkeypatch.setattr("shine.comfyops.host_env.probe_host_facts", _rtx_4070_host)
    project = create_project(client)
    body = {
        "catalog": IMAGE_CATALOG, "components": IMAGE_COMPONENTS,
        "project_id": project, "disk_free": 500 * 1024 ** 3,
    }
    first = _plan(client, body).json()
    second = _plan(client, body).json()
    assert first["plan_id"] == second["plan_id"]
    assert first["sha256"] == second["sha256"]


# --- CASE-04: an interrupted download resumes and re-verifies the digest -


def test_an_interrupted_image_download_resumes_and_verifies_the_hash(monkeypatch, tmp_path):
    """CASE-04: the image download continues at the confirmed offset.

    The pinned bytes are immutable, so a broken transfer keeps the partial
    file and asks the source again from the offset it already has.  The
    digest is checked again before the file is accepted.
    """
    import hashlib
    from contextlib import contextmanager

    import httpx

    from shine.comfyops.installer import ControlledDownloader, HTTPTransport, PackageFile

    content = b"x" * 4096
    spec = PackageFile(
        url="https://huggingface.co/Qwen/qwen-image-2.1-unet/resolve/main/unet.safetensors",
        relative_path="unet.safetensors",
        sha256=hashlib.sha256(content).hexdigest(),
        size=len(content),
    )
    calls: list[str] = []

    class Interrupted(httpx.SyncByteStream):
        def __iter__(self):
            yield content[:2048]
            raise httpx.ReadError("interrupted")

    @contextmanager
    def stream(method, url, **kwargs):
        calls.append(url)
        if url == spec.url:
            yield httpx.Response(200, stream=Interrupted())
        else:
            # The mirror serves the same pinned bytes from the confirmed offset.
            yield httpx.Response(206, content=content[2048:], headers={
                "content-range": f"bytes 2048-{len(content) - 1}/{len(content)}",
            })

    monkeypatch.setattr(httpx, "stream", stream)
    downloader = ControlledDownloader({"huggingface.co", "hf-mirror.com"}, HTTPTransport())
    target = downloader.download(spec, tmp_path)
    assert target.read_bytes() == content
    # The first ask was the official source; the retry asked for the rest.
    assert calls[0] == spec.url
    assert len(calls) > 1
    assert target.name == spec.sha256


def test_a_corrupted_image_download_is_refused(monkeypatch, tmp_path):
    """CASE-04: bytes that do not match the pinned digest never land."""
    import hashlib
    from contextlib import contextmanager

    import httpx

    from shine.comfyops.installer import ControlledDownloader, HTTPTransport, PackageFile

    spec = PackageFile(
        url="https://huggingface.co/Qwen/qwen-image-2.1-unet/resolve/main/unet.safetensors",
        relative_path="unet.safetensors",
        sha256=hashlib.sha256(b"expected").hexdigest(),
        size=8,
    )

    @contextmanager
    def stream(method, url, **kwargs):
        yield httpx.Response(200, content=b"tampered")

    monkeypatch.setattr(httpx, "stream", stream)
    downloader = ControlledDownloader({"huggingface.co"}, HTTPTransport())
    with pytest.raises(ValueError):
        downloader.download(spec, tmp_path)
    assert not (tmp_path / spec.sha256).exists()


# --- CASE-10: the safety refusals stay refusals -------------------------

def test_a_forged_capability_is_refused(prep):
    """The capability comes from the server side catalog, not from the request."""
    client, _, _ = prep
    project = create_project(client)
    response = _plan(client, {
        "catalog": IMAGE_CATALOG, "components": IMAGE_COMPONENTS,
        "project_id": project, "disk_free": 10 ** 12,
        "capability": "h3",
    })
    assert response.status_code == 409, response.text
    detail = response.json()["detail"]
    assert detail["code"] == "CAPABILITY_CATALOG_MISMATCH"
    assert detail["actual_capability"] == "image"


def test_a_matching_capability_declaration_is_accepted(prep, monkeypatch):
    client, _, _ = prep
    monkeypatch.setattr("shine.comfyops.host_env.probe_host_facts", _rtx_4070_host)
    project = create_project(client)
    response = _plan(client, {
        "catalog": IMAGE_CATALOG, "components": IMAGE_COMPONENTS,
        "project_id": project, "disk_free": 500 * 1024 ** 3,
        "capability": "image",
    })
    assert response.status_code == 200, response.text


def test_an_unknown_component_is_refused(prep):
    client, _, _ = prep
    project = create_project(client)
    response = _plan(client, {
        "catalog": IMAGE_CATALOG, "components": ["minimax-h3-diffusion"],
        "project_id": project, "disk_free": 10 ** 12,
    })
    assert response.status_code == 422, response.text
    assert "CATALOG_COMPONENT_UNKNOWN" in response.text


def test_the_image_catalog_cannot_borrow_the_h3_variant(prep):
    """A forged variant id on the image catalog is refused by name."""
    client, _, _ = prep
    project = create_project(client)
    response = _plan(client, {
        "catalog": IMAGE_CATALOG, "components": IMAGE_COMPONENTS,
        "project_id": project, "disk_free": 10 ** 12,
        "variant_id": "h3-ref2va-int8-convrot",
    })
    assert response.status_code in (404, 409), response.text


def test_an_unknown_catalog_is_refused(prep):
    client, _, _ = prep
    project = create_project(client)
    response = _plan(client, {
        "catalog": "not-a-catalog", "components": IMAGE_COMPONENTS,
        "project_id": project, "disk_free": 10 ** 12,
    })
    assert response.status_code == 404, response.text


def test_a_forged_qualification_cannot_ride_along(prep):
    """No field may carry a qualification verdict from the browser."""
    client, _, _ = prep
    project = create_project(client)
    response = _plan(client, {
        "catalog": IMAGE_CATALOG, "components": IMAGE_COMPONENTS,
        "project_id": project, "disk_free": 10 ** 12,
        "qualification": {"status": "SUPPORTED"},
    })
    assert response.status_code == 422, response.text


def test_the_capability_table_is_server_owned():
    """A catalog nobody classified has no gate to borrow - and no escape."""
    from shine.comfyops.capability import CAPABILITIES, CATALOG_CAPABILITIES

    assert set(CATALOG_CAPABILITIES) >= {
        "h3-ref2va-windows-x64", IMAGE_CATALOG,
        "h3-ref2va-lowend-candidate-windows-x64", "h3-ref2va-nvfp4-lowend-windows-x64",
    }
    assert all(capability in CAPABILITIES for capability, _ in CATALOG_CAPABILITIES.values())
