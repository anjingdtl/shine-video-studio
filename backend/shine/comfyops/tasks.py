"""Media preparation uses the existing AgentTaskRecord ledger, not another queue."""

from contextlib import contextmanager

from shine.comfy.workflow import graph_hash
from shine.models import AgentTaskRecord, ProjectRuleLock
from shine.services import add_audit


@contextmanager
def preparation_task(db, project, role, payload, model):
    lock = db.get(ProjectRuleLock, project.id)
    task = AgentTaskRecord(
        project_id=project.id,
        role_id=role,
        input_hash=graph_hash(payload),
        style_version_id=project.frozen_style_version_id,
        asset_manifest_hash=graph_hash({}),
        rulepack_id=lock.rulepack_id if lock else "PREPRODUCTION_UNFROZEN",
        rulepack_hash=lock.rulepack_hash if lock else graph_hash({}),
        model_profile_id="local-media-tool",
        model_id=model,
        schema_version="1.3",
        status="RUNNING",
        trace={"tool": role, "replay": "NEVER_AUTOMATIC", "is_mock": model.startswith("fake-")},
    )
    db.add(task)
    db.commit()  # Durable intent before any provider call.
    try:
        yield task
    except Exception as exc:
        db.rollback()
        task = db.get(AgentTaskRecord, task.id)
        task.status = "FAILED"
        task.error_code = type(exc).__name__
        add_audit(
            db,
            role,
            "PREPARATION_TASK_FAILED",
            "agent_task",
            task.id,
            {"error_type": type(exc).__name__},
            project_id=project.id,
        )
        db.commit()
        raise
    else:
        task.status = "COMPLETED"
        add_audit(db, role, "PREPARATION_TASK_COMPLETED", "agent_task", task.id, project_id=project.id)
