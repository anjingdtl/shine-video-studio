from __future__ import annotations

from shine.providers import MockH3Provider
from shine.quality.repair import RepairEngine, categorize
from shine.schemas import FusedQCResult, HardFailure, RepairAction
from shine.worker import run_one
from tests.conftest import create_job, create_project, create_shot, freeze_style


def report(*codes: str, confidence: float = 1.0) -> FusedQCResult:
    return FusedQCResult(
        result="failed" if codes else "needs_review",
        hard_failures=[HardFailure(code=code, evidence=code) for code in codes],
        review_confidence=confidence,
        human_review_required=True,
        is_mock=True,
        coverage={},
    )


def test_error_codes_map_to_root_cause_actions():
    assert categorize("IDENTITY_MISMATCH") == "REFERENCE_WRONG"
    assert categorize("WRONG_LINE") == "DIALOGUE_WRONG"
    assert categorize("STYLE_DRIFT") == "PROMPT_WRONG"
    assert categorize("COMFY_WORKFLOW_INVALID") == "WORKFLOW_WRONG"
    assert categorize("COMFY_TIMEOUT") == "TRANSIENT"


def test_repair_actions_are_bounded_and_never_claim_a_pass():
    reference = RepairEngine().plan(report("IDENTITY_MISMATCH"), retries_left=2, auto_repair_enabled=True)
    assert reference.plan.action == RepairAction.REBUILD_REFERENCE_BINDINGS
    assert reference.auto_apply is False
    assert reference.plan.max_additional_attempts == 2

    dialogue = RepairEngine().plan(report("WRONG_LINE"), retries_left=1, auto_repair_enabled=True)
    assert dialogue.plan.action == RepairAction.REVISE_SHOT_INTENT
    assert dialogue.plan.human_review_required is True

    transient = RepairEngine().plan(report("COMFY_TIMEOUT"), retries_left=2, auto_repair_enabled=True)
    assert transient.plan.action == RepairAction.RERENDER_AFTER_TRANSIENT
    assert transient.auto_apply is True

    disabled = RepairEngine().plan(report("COMFY_TIMEOUT"), retries_left=2, auto_repair_enabled=False)
    assert disabled.auto_apply is False

    exhausted = RepairEngine().plan(report("COMFY_TIMEOUT"), retries_left=0, auto_repair_enabled=True)
    assert exhausted.plan.action == RepairAction.WAIT_FOR_HUMAN
    assert exhausted.plan.human_review_required is True

    low_confidence = RepairEngine().plan(
        report("COMFY_TIMEOUT", confidence=0.2), retries_left=2, auto_repair_enabled=True
    )
    assert low_confidence.plan.action == RepairAction.WAIT_FOR_HUMAN


def test_passed_jobs_are_protected_from_automatic_repairs(studio):
    client, _factory, _root, _settings = studio
    project_id = create_project(client)
    style = freeze_style(client, project_id)
    shot = create_shot(client, project_id, style["id"])
    job = create_job(client, project_id, shot["id"])
    from shine.models import Job

    class Passed:
        status = "PASSED"

    class Failed:
        status = "FAILED"

    assert RepairEngine.guard_passed(Passed()) is False
    assert RepairEngine.guard_passed(Failed()) is True
    _ = (job, Job)


def test_worker_failure_enters_repair_decision_not_blind_rerun(studio):
    client, factory, _root, _settings = studio
    project_id = create_project(client)
    style = freeze_style(client, project_id)
    shot = create_shot(client, project_id, style["id"])
    job = create_job(client, project_id, shot["id"], mock_scenario="fail")
    provider = MockH3Provider()
    assert run_one(factory, provider, "worker-repair") == job["id"]
    result = client.get(f"/api/v1/jobs/{job['id']}").json()
    assert result["status"] == "REPAIR_PENDING"
    assert provider.submission_count == 1
    suggestion = client.post(f"/api/v1/jobs/{job['id']}/repair/suggest")
    assert suggestion.status_code == 200, suggestion.text
    assert suggestion.json()["plan"]["action"] in {
        "wait_for_human",
        "rerender_after_transient",
        "adjust_prompt_strategy",
    }


def test_manual_repair_is_bounded_by_the_semantic_budget(studio):
    client, factory, _root, _settings = studio
    project_id = create_project(client)
    style = freeze_style(client, project_id)
    shot = create_shot(client, project_id, style["id"])
    job = create_job(client, project_id, shot["id"], mock_scenario="fail")
    plan = {
        "action": "revise_shot_intent",
        "root_cause": "simulated",
        "changes": ["adjust"],
        "max_additional_attempts": 2,
        "human_review_required": False,
    }
    run_one(factory, MockH3Provider(), "worker")
    for expected in (1, 2):
        response = client.post(f"/api/v1/jobs/{job['id']}/repair?mock_scenario=fail", json=plan)
        assert response.status_code == 200
        assert response.json()["semantic_retries"] == expected
        run_one(factory, MockH3Provider(), "worker")
    exhausted = client.post(f"/api/v1/jobs/{job['id']}/repair", json=plan)
    assert exhausted.status_code == 409
    job_data = client.get(f"/api/v1/jobs/{job['id']}").json()
    assert job_data["status"] == "REPAIR_PENDING"
    manual = [record for record in job_data["repairs"] if record["outcome"] == "manual_queued"]
    assert len(manual) == 2
