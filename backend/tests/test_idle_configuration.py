"""The idle-configuration gate only blocks work that can still touch the GPU.

A series that a Worker can no longer claim - a blocked NEEDS_REVIEW run with no
lease - is an audit trail, not live work.  It must not freeze the whole
workbench: an unrelated project's blocked series would otherwise make every
runtime re-validation impossible.
"""

from __future__ import annotations

import pytest
from fastapi import HTTPException

from shine.comfyops.models import InstallationAttempt, InstallationPlan, ResourceLease
from shine.comfyops.runtime_configuration import require_idle_configuration
from shine.models import ProductionSeries, utcnow
from tests.conftest import create_project


def _series(factory, project_id: str, status: str) -> ProductionSeries:
    with factory() as db:
        row = ProductionSeries(project_id=project_id, status=status, shot_count=1, completed_count=0)
        db.add(row)
        db.commit()
        return row


def test_a_blocked_series_does_not_block_a_runtime_revalidation(studio):
    client, factory, _root, _settings = studio
    project_id = create_project(client, "另一个项目")
    _series(factory, project_id, "NEEDS_REVIEW")

    with factory() as db:
        require_idle_configuration(db)  # must not raise


def test_pending_install_record_without_a_lease_does_not_block_h3_checks(studio):
    client, factory, _root, _settings = studio
    project_id = create_project(client, "安装历史")
    with factory.begin() as db:
        plan = InstallationPlan(project_id=project_id, status="NEEDS_REVIEW", sha256="plan")
        db.add(plan)
        db.flush()
        db.add(InstallationAttempt(plan_id=plan.id, status="NEEDS_REVIEW", sha256="attempt"))
    with factory() as db:
        require_idle_configuration(db)
    with factory.begin() as db:
        db.add(ResourceLease(owner="h3:unresolved", budget_mb=1, exclusive=True, priority=10,
                             status="RECONCILE_REQUIRED",
                             expires_at=utcnow()))
    with factory() as db, pytest.raises(HTTPException, match="RUNTIME_CONFIGURATION_REQUIRES"):
        require_idle_configuration(db)


@pytest.mark.parametrize("status", ["QUEUED", "RUNNING", "PAUSED", "RECONCILING"])
def test_a_live_series_still_blocks_a_runtime_revalidation(studio, status):
    client, factory, _root, _settings = studio
    project_id = create_project(client, "在跑的项目")
    _series(factory, project_id, status)

    with factory() as db:
        with pytest.raises(HTTPException) as excinfo:
            require_idle_configuration(db)
    assert excinfo.value.status_code == 409
    assert excinfo.value.detail == "RUNTIME_CONFIGURATION_REQUIRES_IDLE_AND_RECONCILED_WORK"


def test_a_finished_series_does_not_block_a_runtime_revalidation(studio):
    client, factory, _root, _settings = studio
    project_id = create_project(client, "已完成的项目")
    for status in ("COMPLETED", "FAILED", "CANCELLED"):
        _series(factory, project_id, status)

    with factory() as db:
        require_idle_configuration(db)  # must not raise


def _attempt(factory, project_id: str, *, status: str, packages: list[str],
             lease_owner: str | None = None) -> str:
    with factory.begin() as db:
        plan = InstallationPlan(project_id=project_id, status="APPROVED", sha256="plan",
                                payload={"packages": [{"id": item} for item in packages]})
        db.add(plan)
        db.flush()
        attempt = InstallationAttempt(plan_id=plan.id, status=status, sha256="attempt",
                                      error_code="UNTRUSTED_DOWNLOAD_URL",
                                      lease_owner=lease_owner)
        db.add(attempt)
        db.flush()
        return attempt.id


def test_a_failed_advisory_review_download_does_not_block_the_whole_workbench(studio):
    """A failed *advisory* download must not freeze real production.

    The review models (画面审片 / 语音核对) only give suggestions.  When their
    download failed, the attempt was still counted as live work: service
    validation, runtime activation and the production badge refused forever,
    and nothing in the workbench could clear it.
    """
    client, factory, _root, _settings = studio
    project_id = create_project(client, "审片模型下载失败")
    _attempt(factory, project_id, status="FAILED_RECONCILE_REQUIRED",
             packages=["qwen3-asr-0.6b"])

    with factory() as db:
        require_idle_configuration(db)  # must not raise


def test_a_failed_production_download_still_blocks_until_it_is_reconciled(studio):
    """A failed production component is a real half-finished installation."""
    client, factory, _root, _settings = studio
    project_id = create_project(client, "生产组件下载失败")
    _attempt(factory, project_id, status="FAILED_RECONCILE_REQUIRED",
             packages=["minimax-h3-diffusion"])

    with factory() as db, pytest.raises(HTTPException) as excinfo:
        require_idle_configuration(db)
    assert excinfo.value.detail == "RUNTIME_CONFIGURATION_REQUIRES_IDLE_AND_RECONCILED_WORK"


def test_a_failed_download_that_still_owns_a_lease_blocks(studio):
    """A failure that still holds a lease can still hold the machine."""
    client, factory, _root, _settings = studio
    project_id = create_project(client, "还占着资源的失败下载")
    _attempt(factory, project_id, status="FAILED_RECONCILE_REQUIRED",
             packages=["qwen3-asr-0.6b"], lease_owner="worker-x")

    with factory() as db, pytest.raises(HTTPException) as excinfo:
        require_idle_configuration(db)
    assert excinfo.value.detail == "RUNTIME_CONFIGURATION_REQUIRES_IDLE_AND_RECONCILED_WORK"


def test_a_running_install_still_blocks(studio):
    client, factory, _root, _settings = studio
    project_id = create_project(client, "正在安装")
    _attempt(factory, project_id, status="INSTALLING", packages=["minimax-h3-diffusion"])

    with factory() as db, pytest.raises(HTTPException) as excinfo:
        require_idle_configuration(db)
    assert excinfo.value.detail == "RUNTIME_CONFIGURATION_REQUIRES_IDLE_AND_RECONCILED_WORK"
