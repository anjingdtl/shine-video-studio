"""Mainline V3 WP1-A: plan-scoped download admission.

The four-site report stays a diagnostic view.  Download admission is derived
from the plan's own component closure: an image capability that only
downloads from huggingface.co is never blocked because codeload, PyPI or the
torch index are down, and a blocked answer names the exact sources and
components it blocks.
"""

from __future__ import annotations

import httpx

from shine.comfyops import deployment
from shine.comfyops.connectivity import (
    preflight_for_components,
    probe_upstreams,
    required_sources,
)

IMAGE_CATALOG = "qwen-image-2.1-windows-x64"
IMAGE_COMPONENTS = ["qwen-image-2.1-unet", "qwen-image-2.1-text-encoder", "qwen-image-2.1-vae"]
REVIEW_VLM = "qwen3-vl-8b-fp8"
H3_WEIGHTS = ["minimax-h3-diffusion", "minimax-h3-text-encoder", "minimax-h3-vae"]


class FakeResponse:
    def __init__(self, status_code: int):
        self.status_code = status_code

    def __enter__(self):
        return self

    def __exit__(self, *exc_info):
        return False


class FakeClient:
    """Answers per host so the admission policy is testable offline."""

    def __init__(self, behaviour):
        self.behaviour = behaviour
        self.calls: list[str] = []

    def stream(self, method, url, headers=None, timeout=None):
        self.calls.append(url)
        result = self.behaviour(url)
        if isinstance(result, Exception):
            raise result
        return FakeResponse(result)


def _by_host(client: FakeClient, blocked: set[str], status_map: dict[str, int] | None = None):
    status_map = status_map or {}

    def behaviour(url):
        from urllib.parse import urlsplit

        host = urlsplit(url).hostname or ""
        if host in blocked:
            return httpx.ConnectError("no route")
        return status_map.get(host, 200)

    return behaviour


# ---------------------------------------------------------------- required_sources


def test_image_closure_only_needs_huggingface():
    catalog = deployment.load_deployment_catalog(IMAGE_CATALOG)
    sources = required_sources(catalog, IMAGE_COMPONENTS)
    assert [row["probe_id"] for row in sources] == ["huggingface"]
    assert sources[0]["component_ids"] == sorted(IMAGE_COMPONENTS)


def test_h3_weights_also_need_huggingface_only():
    catalog = deployment.load_deployment_catalog("h3-ref2va-windows-x64")
    sources = required_sources(catalog, H3_WEIGHTS)
    assert [row["probe_id"] for row in sources] == ["huggingface"]


def test_structure_closure_needs_all_four_transport_families():
    catalog = deployment.load_deployment_catalog("h3-ref2va-windows-x64")
    sources = required_sources(catalog, ["comfyui", "comfy-runtime-wheels", "minimax-h3-diffusion"])
    assert {row["probe_id"] for row in sources} == {"huggingface", "codeload", "pypi", "torch"}


def test_unnamed_components_contribute_no_source():
    """An already-verified component must never block another capability."""
    catalog = deployment.load_deployment_catalog("h3-ref2va-windows-x64")
    sources = required_sources(catalog, [REVIEW_VLM])
    assert [row["probe_id"] for row in sources] == ["huggingface"]
    assert sources[0]["component_ids"] == [REVIEW_VLM]


# ------------------------------------------------------------- preflight admission


def test_image_preflight_ignores_unrelated_blocked_sites():
    """C-01: only-image preparation survives codeload/pypi/torch being down."""
    catalog = deployment.load_deployment_catalog(IMAGE_CATALOG)
    client = FakeClient(_by_host(None, {
        "codeload.github.com", "files.pythonhosted.org", "download.pytorch.org", "download-r2.pytorch.org",
    }))
    report = preflight_for_components(catalog, IMAGE_COMPONENTS, client=client)
    assert report["status"] == "OK"
    assert report["blocked"] == []
    # Only the source this closure really downloads from was probed.
    assert client.calls == ["https://huggingface.co/api/models/Comfy-Org/MiniMax-H3"]


def test_h3_preflight_blocked_huggingface_names_the_components():
    """C-01: the blocked answer names sources and components, never a 404-like silence."""
    catalog = deployment.load_deployment_catalog("h3-ref2va-windows-x64")
    client = FakeClient(_by_host(None, {"huggingface.co"}))
    report = preflight_for_components(catalog, H3_WEIGHTS, client=client)
    assert report["status"] == "BLOCKED"
    assert report["blocked"] == ["huggingface"]
    row = next(row for row in report["required"] if row["probe_id"] == "huggingface")
    assert row["status"] == "BLOCKED"
    assert row["retryable"] is True
    assert row["reason_code"] == "UPSTREAM_UNREACHABLE"
    assert row["component_ids"] == sorted(H3_WEIGHTS)


def test_degraded_source_is_a_named_fact_not_admission_failure():
    catalog = deployment.load_deployment_catalog(IMAGE_CATALOG)
    client = FakeClient(_by_host(None, set(), {"huggingface.co": 503}))
    report = preflight_for_components(catalog, IMAGE_COMPONENTS, client=client)
    assert report["blocked"] == []
    assert report["degraded"] == ["huggingface"]
    assert report["status"] == "PARTIAL"
    row = report["required"][0]
    assert row["reason_code"] == "UPSTREAM_DEGRADED" and row["http_status"] == 503


def test_probe_subset_keeps_diagnostic_report_untouched():
    """The four-site diagnostic and every existing caller keep their exact answer."""
    client = FakeClient(lambda url: 200)
    report = probe_upstreams(client=client)
    assert len(client.calls) == 4
    assert report["status"] == "OK"


# ------------------------------------------------------------------- API contract


def _offline_preflight(monkeypatch):
    """The endpoint owns a real probe; the API contract is tested offline."""
    import shine.comfyops.connectivity as connectivity_module

    def fake(catalog, components, *, client=None, timeout=8.0):
        return {
            "status": "OK", "checked_at": "2026-11-14T00:00:00", "blocked": [], "degraded": [],
            "required": [{"probe_id": "huggingface", "label": "Hugging Face 模型库",
                          "host": "huggingface.co", "download_hosts": ["huggingface.co"],
                          "component_ids": sorted(components), "needed_by": "、".join(sorted(components)),
                          "status": "OK", "http_status": 200, "error": None, "retryable": False,
                          "reason_code": None}],
            "guidance": ["offline fixture"],
        }

    monkeypatch.setattr(connectivity_module, "preflight_for_components", fake)


def test_api_for_plan_scopes_probing_to_the_requested_closure(api, monkeypatch):
    """POST /setup/connectivity/for-plan answers per-plan, not per-machine."""
    _offline_preflight(monkeypatch)
    client, _, _ = api
    response = client.post("/api/v1/setup/connectivity/for-plan", json={
        "catalog": IMAGE_CATALOG, "components": IMAGE_COMPONENTS,
    })
    assert response.status_code == 200
    body = response.json()
    assert body["capability"] == "image"
    assert body["catalog"] == "shine-qwen-image-21-windows-x64"
    assert [row["probe_id"] for row in body["required"]] == ["huggingface"]
    # No signed URL or query string ever leaves the server.
    assert "?" not in str(body)


def test_api_for_plan_refuses_unknown_components(api, monkeypatch):
    _offline_preflight(monkeypatch)
    client, _, _ = api
    response = client.post("/api/v1/setup/connectivity/for-plan", json={
        "catalog": IMAGE_CATALOG, "components": ["not-a-component"],
    })
    assert response.status_code == 422
    assert response.json()["detail"] == "CATALOG_COMPONENT_UNKNOWN"


def test_api_for_plan_derives_review_capability_from_h3_catalog(api, monkeypatch):
    """The capability comes from the server owned catalog, like /install-plan."""
    _offline_preflight(monkeypatch)
    client, _, _ = api
    response = client.post("/api/v1/setup/connectivity/for-plan", json={
        "catalog": "h3-ref2va-windows-x64", "components": [REVIEW_VLM],
    })
    assert response.status_code == 200
    assert response.json()["capability"] == "review"
