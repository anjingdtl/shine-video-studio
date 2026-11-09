"""J09: the package promise and the dispatch share one size verdict."""

from __future__ import annotations

from tests.conftest import COMFY_GRAPH, COMFY_PARAM_MAP, register_workflow_profile
from tests.test_director_long_form import LOCAL, _studio


def _confirm_plan(client, project: str) -> None:
    plan = client.post(f"/api/v1/projects/{project}/episode-plans",
                       json={"target_seconds": 60, "tolerance_seconds": 0}, headers=LOCAL)
    assert plan.status_code == 201, plan.text
    confirmed = client.post(
        f"/api/v1/projects/{project}/episode-plans/{plan.json()['version']}/confirm",
        json={"expected_sha256": plan.json()["sha256"]}, headers=LOCAL)
    assert confirmed.status_code == 200, confirmed.text


def test_a_size_the_profile_cannot_map_is_refused_at_package_build(api):
    client, _factory, _settings = api
    studio = _studio(client, shots=1)
    project = studio["project"]
    _confirm_plan(client, project)
    # The registered graph has no width/height inputs.
    without_size = {key: value for key, value in COMFY_PARAM_MAP.items()
                    if key not in {"width", "height"}}
    profile = register_workflow_profile(client, mode="Ref2VA",
                                        graph=COMFY_GRAPH, parameter_map=without_size)

    refused = client.post(f"/api/v1/projects/{project}/director-packages", headers=LOCAL, json={
        "workflow_profile_id": profile["id"],
        "generation_mode": "Ref2VA",
        "output": {"width": 768, "height": 1344, "fps": 24},
    })
    assert refused.status_code == 422, refused.text
    detail = refused.json()["detail"]
    assert detail["code"] == "WORKFLOW_SIZE_NOT_MAPPED"
    assert set(detail["details"]["missing_parameters"]) == {"width", "height"}

    # The same request without a size still builds: the refusal is about the
    # size, not about the shot or the profile.
    plain = client.post(f"/api/v1/projects/{project}/director-packages", headers=LOCAL, json={
        "workflow_profile_id": profile["id"], "generation_mode": "Ref2VA",
    })
    assert plain.status_code == 201, plain.text


def test_a_direct_series_refuses_the_same_size_with_the_same_code(api):
    client, _factory, _settings = api
    studio = _studio(client, shots=1)
    project = studio["project"]
    without_size = {key: value for key, value in COMFY_PARAM_MAP.items()
                    if key not in {"width", "height"}}
    profile = register_workflow_profile(client, mode="Ref2VA",
                                        graph=COMFY_GRAPH, parameter_map=without_size)
    refused = client.post(f"/api/v1/projects/{project}/production-series", headers=LOCAL, json={
        "shot_ids": [studio["shots"][0]["id"]], "provider": "comfyui",
        "workflow_profile_id": profile["id"],
        "output": {"width": 768, "height": 1344, "fps": 24},
    })
    assert refused.status_code == 422, refused.text
    assert refused.json()["detail"]["code"] == "WORKFLOW_SIZE_NOT_MAPPED"
