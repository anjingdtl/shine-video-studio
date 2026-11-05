"""The Qwen-Image-2.1 weights are registered in the product, not by a side script.

The plan requires the image weights to be installable through the same
controlled path as the H3 runtime: a pinned catalog, an approval, and bytes
verified against the pinned digests.  This file guards the catalog against
drifting away from the audited workflow pins.
"""

from __future__ import annotations

from shine.comfyops import deployment
from shine.materialprep import qwen_workflow

CATALOG = "qwen-image-2.1-windows-x64"


def _catalog() -> dict:
    return deployment.load_deployment_catalog(CATALOG)


def test_the_catalog_pins_exactly_the_audited_weights(api):
    """The catalog and the workflow must agree byte for byte.

    A catalog that drifts from the audited pins would install bytes the audit
    does not describe, and the readiness evidence would claim a file nobody
    checked.
    """
    catalog = _catalog()
    files = {
        file_entry["relative_path"]: file_entry
        for component in catalog["components"]
        for file_entry in component["files"]
    }
    required = [entry for entry in qwen_workflow.MODEL_FILES if entry.get("required")]
    assert required, "the workflow pins no required weight"
    for entry in required:
        assert entry["filename"] in files, entry["filename"]
        pinned = files[entry["filename"]]
        assert pinned["sha256"] == entry["sha256"], entry["filename"]
        assert pinned["size"] == entry["bytes"], entry["filename"]
        assert f"/{entry['folder']}/{entry['filename']}" in pinned["url"], entry["filename"]
    optional = [entry for entry in qwen_workflow.MODEL_FILES if not entry.get("required")]
    for entry in optional:
        assert entry["filename"] not in files, "an optional weight must not be installed"


def test_the_catalog_is_research_only(api):
    """The licence limits non-commercial use to research or evaluation."""
    catalog = _catalog()
    assert catalog["components"], "an empty catalog installs nothing"
    for component in catalog["components"]:
        assert component["usage"] == "research", component["component_id"]
        assert "research" in component["license_id"], component["component_id"]


def test_the_product_can_plan_the_image_weight_installation(api):
    """The installation goes through the ordinary controlled path."""
    client, factory, settings = api
    project_id = client.post("/api/v1/projects", json={"title": "出图权重"}).json()["id"]
    catalog = _catalog()
    response = client.post("/api/v1/setup/install-plan", json={
        "catalog": CATALOG,
        "components": [component["component_id"] for component in catalog["components"]],
        "project_id": project_id,
        "disk_free": 200 * 1024 ** 3,
    })
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["download_bytes"] == qwen_workflow.REQUIRED_MODEL_BYTES, body
    assert len(body["packages"]) == 3, body
