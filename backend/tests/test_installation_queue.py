"""The installation queue: one physical target, a serial order, no dead end.

The audit sample hit ``INSTALL_TARGET_CONFLICT`` while a review-model download
was running: a second capability was refused instead of waiting its turn.  The
rule now is:

* one directory has exactly one writer at a time;
* a different component set in the same directory is queued, not refused;
* the queue is durable (plan + attempt rows), so a browser or API restart does
  not lose it;
* a paused attempt keeps its plan identity but releases the directory;
* an unrelated directory can still proceed in parallel.
"""

from __future__ import annotations

from datetime import timedelta

import pytest

from shine.comfy.workflow import graph_hash
from shine.comfyops.installation import (
    CANCELLED,
    DOWNLOADING,
    PAUSED,
    QUEUED,
    WAITING_FOR_TARGET,
    attempt_view,
    claim_next_attempt,
    queue_attempt,
    request_cancel,
    request_pause,
    target_holder,
)
from shine.comfyops.models import InstallationAttempt, InstallationPlan
from shine.models import utcnow
from tests.conftest import create_project

pytestmark = pytest.mark.integration


def _plan(db, project_id: str, storage_root, components: list[str]) -> InstallationPlan:
    payload = {
        "kind": "LOCAL_PACKAGES",
        "project_id": project_id,
        "executor": "LOCAL_ISOLATED",
        "storage_root": str(storage_root),
        "packages": [{"id": item, "hash": item + "-hash"} for item in components],
        "download_bytes": 1024,
    }
    plan = InstallationPlan(
        project_id=project_id, payload=payload, sha256=graph_hash(payload), status="APPROVED",
    )
    db.add(plan)
    db.flush()
    return plan


def _finish(factory, attempt_id: str, stage: str = "FILES_VERIFIED_PENDING_RUNTIME") -> None:
    with factory.begin() as db:
        attempt = db.get(InstallationAttempt, attempt_id)
        attempt.stage = stage
        attempt.status = stage
        attempt.lease_owner = None
        attempt.lease_expires_at = None


def test_a_busy_target_makes_the_second_plan_wait_instead_of_failing(api, tmp_path):
    client, factory, _ = api
    project = create_project(client)
    root = tmp_path / "deployment"
    with factory.begin() as db:
        first = _plan(db, project, root, ["review-vlm"])
        second = _plan(db, project, root, ["qwen-image"])
        first_attempt, _ = queue_attempt(db, first, first.sha256, "operator")
        first_id = first_attempt.id

    assert claim_next_attempt(factory, "worker-1") == first_id

    with factory.begin() as db:
        second_attempt, reused = queue_attempt(
            db, db.get(InstallationPlan, second.id), second.sha256, "operator",
        )
        second_id = second_attempt.id
        assert reused is False
        # This used to be a 409 INSTALL_TARGET_CONFLICT.  It is now a durable
        # waiting state the user can see.
        assert second_attempt.stage == WAITING_FOR_TARGET
        assert second_attempt.payload["waiting_behind_attempt_id"] == first_id

    with factory() as db:
        view = attempt_view(db.get(InstallationAttempt, second_id))
        assert view["waiting_for_target"] is True
        assert view["stage"] == WAITING_FOR_TARGET
        # Waiting is not a terminal outcome.
        assert view["terminal"] is False


def test_the_waiting_plan_is_claimed_as_soon_as_the_target_is_free(api, tmp_path):
    client, factory, _ = api
    project = create_project(client)
    root = tmp_path / "deployment"
    with factory.begin() as db:
        first = _plan(db, project, root, ["review-vlm"])
        second = _plan(db, project, root, ["qwen-image"])
        first_id, _ = queue_attempt(db, first, first.sha256, "operator")
        first_id = first_id.id
    assert claim_next_attempt(factory, "worker-1") == first_id
    with factory.begin() as db:
        second_row = db.get(InstallationPlan, second.id)
        waiting, _ = queue_attempt(db, second_row, second_row.sha256, "operator")
        waiting_id = waiting.id

    # Nothing else runs while the target is busy.
    assert claim_next_attempt(factory, "worker-2") is None

    _finish(factory, first_id)
    assert claim_next_attempt(factory, "worker-2") == waiting_id
    with factory() as db:
        claimed = db.get(InstallationAttempt, waiting_id)
        assert claimed.stage == QUEUED
        assert claimed.lease_owner == "worker-2"


def test_an_unrelated_directory_is_not_blocked_by_a_busy_one(api, tmp_path):
    client, factory, _ = api
    project = create_project(client)
    busy_root = tmp_path / "deployment"
    other_root = tmp_path / "other-disk"
    with factory.begin() as db:
        first = _plan(db, project, busy_root, ["review-vlm"])
        blocked = _plan(db, project, busy_root, ["qwen-image"])
        independent = _plan(db, project, other_root, ["asr"])
        first_id, _ = queue_attempt(db, first, first.sha256, "operator")
        first_id = first_id.id
    assert claim_next_attempt(factory, "worker-1") == first_id

    with factory.begin() as db:
        waiting, _ = queue_attempt(db, db.get(InstallationPlan, blocked.id), blocked.sha256, "operator")
        assert waiting.stage == WAITING_FOR_TARGET
        free, _ = queue_attempt(db, db.get(InstallationPlan, independent.id),
                                independent.sha256, "operator")
        assert free.stage == QUEUED
        free_id = free.id

    # Creation order would pick the waiting plan first; it is skipped because
    # its directory is busy, and the independent plan proceeds.
    assert claim_next_attempt(factory, "worker-2") == free_id


def test_a_paused_attempt_releases_the_directory(api, tmp_path):
    client, factory, _ = api
    project = create_project(client)
    root = tmp_path / "deployment"
    with factory.begin() as db:
        first = _plan(db, project, root, ["review-vlm"])
        first_attempt, _ = queue_attempt(db, first, first.sha256, "operator")
        first_id = first_attempt.id
    assert claim_next_attempt(factory, "worker-1") == first_id
    with factory.begin() as db:
        attempt = db.get(InstallationAttempt, first_id)
        attempt.stage = DOWNLOADING
        attempt.status = DOWNLOADING
        request_pause(db, attempt)
        # The worker stopped at a safe point and released its lease.
        attempt.stage = PAUSED
        attempt.status = PAUSED
        attempt.lease_owner = None
        attempt.lease_expires_at = None

    with factory() as db:
        assert target_holder(db, str(root)) is None

    with factory.begin() as db:
        second = _plan(db, project, root, ["qwen-image"])
        queued, _ = queue_attempt(db, second, second.sha256, "operator")
        assert queued.stage == QUEUED
        queued_id = queued.id
    assert claim_next_attempt(factory, "worker-2") == queued_id


def test_an_expired_lease_is_not_a_permanent_lock(api, tmp_path):
    client, factory, _ = api
    project = create_project(client)
    root = tmp_path / "deployment"
    with factory.begin() as db:
        first = _plan(db, project, root, ["review-vlm"])
        attempt, _ = queue_attempt(db, first, first.sha256, "operator")
        attempt_id = attempt.id
    assert claim_next_attempt(factory, "dead-worker") == attempt_id
    with factory.begin() as db:
        attempt = db.get(InstallationAttempt, attempt_id)
        attempt.stage = DOWNLOADING
        attempt.lease_expires_at = utcnow() - timedelta(seconds=5)
        db.commit()

    with factory() as db:
        assert target_holder(db, str(root)) is None


def test_the_same_physical_work_is_idempotent_across_plan_rows(api, tmp_path):
    client, factory, _ = api
    project = create_project(client)
    root = tmp_path / "deployment"
    with factory.begin() as db:
        first = _plan(db, project, root, ["review-vlm"])
        duplicate = _plan(db, project, root, ["review-vlm"])
        original, reused = queue_attempt(db, first, first.sha256, "operator")
        again, reused_again = queue_attempt(db, duplicate, duplicate.sha256, "operator")
        assert reused is False
        assert reused_again is True
        assert again.id == original.id


def test_cancelling_a_waiting_attempt_is_immediately_effective(api, tmp_path):
    client, factory, _ = api
    project = create_project(client)
    root = tmp_path / "deployment"
    with factory.begin() as db:
        first = _plan(db, project, root, ["review-vlm"])
        second = _plan(db, project, root, ["qwen-image"])
        first_attempt, _ = queue_attempt(db, first, first.sha256, "operator")
        first_id = first_attempt.id
    assert claim_next_attempt(factory, "worker-1") == first_id
    with factory.begin() as db:
        waiting, _ = queue_attempt(
            db, db.get(InstallationPlan, second.id), second.sha256, "operator",
        )
        assert waiting.stage == WAITING_FOR_TARGET
        request_cancel(db, waiting)
        assert waiting.stage == CANCELLED
