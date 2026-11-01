"""Production completion and final acceptance are separate public states."""

from shine.models import Job, QualityGate
from tests.conftest import create_comfy_job, create_project, create_shot, freeze_style


def test_passed_production_with_pending_final_gate_is_not_accepted(api):
    client, factory, _settings = api
    project = create_project(client)
    style = freeze_style(client, project)
    shot = create_shot(client, project, style["id"])
    job = create_comfy_job(client, project, shot["id"])

    with factory.begin() as db:
        stored = db.get(Job, job["id"])
        stored.status = "PASSED"
        stored.provider = "comfyui"
        stored.quality_verification = "real"
        stored.last_qc_result = "needs_review"
        final = QualityGate(
            job_id=stored.id,
            gate_name="final",
            status="needs_review",
            required=True,
            is_mock=False,
            error_code="ADVISORY_MODEL_REVIEW_PENDING",
        )
        db.add(final)
        db.flush()
        final_id = final.id

    state = client.get(f"/api/v1/jobs/{job['id']}").json()
    assert state["status"] == "PASSED"
    assert state["acceptance_status"] == "pending_review"
    assert state["acceptance_final_gate"] == "needs_review"

    with factory.begin() as db:
        db.get(QualityGate, final_id).status = "passed"
    state = client.get(f"/api/v1/jobs/{job['id']}").json()
    assert state["acceptance_status"] == "accepted"
    assert state["acceptance_final_gate"] == "passed"


def test_mock_passed_job_is_never_reported_as_accepted(api):
    client, factory, _settings = api
    project = create_project(client)
    style = freeze_style(client, project)
    shot = create_shot(client, project, style["id"])
    job = create_comfy_job(client, project, shot["id"])
    with factory.begin() as db:
        stored = db.get(Job, job["id"])
        stored.status = "PASSED"
        stored.provider = "mock"
        stored.quality_verification = "mock_verified"
    state = client.get(f"/api/v1/jobs/{job['id']}").json()
    assert state["acceptance_status"] == "mock"
    assert "film acceptance" in state["acceptance_note"]
