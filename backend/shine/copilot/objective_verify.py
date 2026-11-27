"""Goal-aware success verification for one task grant (V3.1 closeout).

"The model finished" and even "one sub-action succeeded" are not the objective.
This module reads the ORIGINAL authorities for each milestone the objective
actually requires, and reports them separately:

* ``plan_frozen``        - an installation plan really froze (installation_plans);
* ``files_verified``     - the capability's own file layer is verified (setup summary);
* ``nodes_loadable``     - the registered graph passed the live node contract;
* ``graph_registered``   - a user-level WorkflowProfile exists with its graph hash;
* ``production_selected``- a real production job actually carries that profile id;
* ``sample_completed``   - a non-mock attempt of such a job PASSED with an artifact;
* ``setup_verdict``      - the machine's single production verdict.

A registration-only objective is satisfied by registration + node contract; an
objective that asks for "可制作 / 真实小样 / 出片" additionally needs the Setup
verdict AND the profile→job→attempt→artifact chain.  Nothing here is inferred
from model text, and a missing authority stays NOT_RUN, never VERIFIED.
"""
from __future__ import annotations

from typing import Any

from sqlalchemy import select

from shine.models import Job, MediaArtifact, WorkflowProfile, utcnow

#: Objective keyword families. Deterministic on purpose: the requirement is a
#: server fact about what was asked, not a model judgement about what was done.
_SAMPLE_WORDS = ("小样", "试拍", "出片", "真实验收", "真实渲染", "拍摄", "sample", "render")
_PRODUCTION_WORDS = ("可制作", "可生产", "配齐", "就绪", "准备", "整备", "production", "ready")
_REGISTRATION_WORDS = ("工作流", "注册", "装配", "graph", "workflow", "profile")

_READY_MODES = frozenset({"READY", "REAL_READY", "RUNNING"})


def required_milestones(objective: str) -> list[str]:
    text = str(objective or "").casefold()
    required: list[str] = []
    if any(word in text for word in _REGISTRATION_WORDS):
        required.extend(["graph_registered", "nodes_loadable"])
    if any(word in text for word in _PRODUCTION_WORDS):
        required.extend(["setup_verdict", "files_verified"])
    if any(word in text for word in _SAMPLE_WORDS):
        required.extend(["setup_verdict", "production_selected", "sample_completed"])
    # Deduplicate, keep order.
    seen: set[str] = set()
    return [item for item in required if not (item in seen or seen.add(item))]


def _milestone(status: str, authority: str, **evidence: Any) -> dict[str, Any]:
    return {"status": status, "authority": authority, **evidence}


def _result(objective: str, required: list[str], results: dict, steps: list[dict]) -> dict:
    missing = [name for name in required if results.get(name, {}).get("status") != "VERIFIED"]
    has_receipt = any(step.get("satisfied") for step in steps)
    return {
        "objective": objective, "required": required, "results": results, "missing": missing,
        "has_original_receipt": has_receipt, "satisfied": has_receipt and not missing,
        "checked_at": utcnow().isoformat(),
    }


def _episode_delivery(db, grant: dict, project_ids: list[str]) -> dict:
    from shine.production.models import ProductionRun, ProductionRunEpisode

    authority = "production_runs + production_run_episodes + media_artifacts"
    for sub_action in reversed(grant.get("sub_actions") or []):
        run_id = sub_action.get("original_task_id")
        if sub_action.get("capability_id") != "production_start" or not run_id:
            continue
        run = db.get(ProductionRun, run_id)
        if run is None or run.project_id not in project_ids:
            continue
        episodes = list(db.scalars(select(ProductionRunEpisode).where(ProductionRunEpisode.run_id == run.id)))
        artifacts = []
        for episode in episodes:
            if episode.status != "DELIVERED" or not episode.delivered_artifact_id:
                continue
            artifact = db.get(MediaArtifact, episode.delivered_artifact_id)
            if (artifact is not None and not artifact.is_mock and artifact.project_id == run.project_id
                    and artifact.sha256 == episode.delivered_sha256):
                artifacts.append(artifact.id)
        delivered = run.status == "COMPLETED" and bool(episodes) and len(artifacts) == len(episodes)
        return _milestone("VERIFIED" if delivered else "NOT_RUN", authority,
                          run_id=run.id, run_status=run.status, artifact_ids=artifacts)
    return _milestone("NOT_RUN", authority)


def _image_setup_verification(
    db, settings, *, objective: str, project_ids: list[str], steps: list[dict[str, Any]]
) -> dict | None:
    text = objective.casefold()
    is_image_setup = any(word in text for word in ("出图", "生图", "image", "场景小样"))
    has_workflow = any(word in text for word in ("工作流", "workflow"))
    if not is_image_setup or has_workflow:
        return None
    from shine.comfyops.setup_summary import summary as setup_summary
    from shine.materialprep.runtime import readiness as image_readiness

    try:
        img = image_readiness(db, settings)
    except Exception as exc:  # noqa: BLE001 - unreadable authority stays FAILED
        img = {"binding_state": "UNKNOWN", "runnable": False, "families": {}, "error": type(exc).__name__}
    try:
        snap = setup_summary(db, settings, project_id=project_ids[0] if project_ids else None, discovered=[])
        img_cap = (snap.get("capabilities") or {}).get("image") or {}
    except Exception as exc:  # noqa: BLE001
        img_cap = {"state": "UNKNOWN", "facts": {}, "error": type(exc).__name__}
    facts = img_cap.get("facts") or {}
    files_ok = bool(facts.get("FILES_VERIFIED"))
    binding_ok = (
        str(img.get("binding_state") or "") == "READY"
        and bool(img.get("runnable"))
        and bool(facts.get("INSTANCE_BOUND"))
    )
    results = {
        "image_files_verified": _milestone(
            "VERIFIED" if files_ok else "FAILED",
            "GET /api/v1/setup/summary#capabilities.image",
            state=img_cap.get("state"),
        ),
        "image_binding_ready": _milestone(
            "VERIFIED" if binding_ok else "FAILED",
            "GET /api/v1/setup/image-readiness",
            binding_state=img.get("binding_state"),
            runnable=bool(img.get("runnable")),
            instance_bound=bool(facts.get("INSTANCE_BOUND")),
        ),
    }
    required = ["image_files_verified", "image_binding_ready"]
    if any(word in text for word in ("小样", "验证", "sample", "render")):
        families = img.get("families") or {}
        wanted = [
            fam
            for fam, words in (
                ("character", ("character", "角色")),
                ("scene", ("scene", "场景")),
                ("prop", ("prop", "道具")),
            )
            if any(word in text for word in words)
        ]
        targets = wanted or list(families.keys())
        verified_targets = [
            fam
            for fam in targets
            if bool((families.get(fam) or {}).get("can_generate"))
            and bool((families.get(fam) or {}).get("evidence_attempt_id"))
        ]
        sample_ok = (len(verified_targets) == len(wanted)) if wanted else bool(verified_targets)
        results["image_sample_verified"] = _milestone(
            "VERIFIED" if sample_ok else "NOT_RUN",
            "GET /api/v1/setup/image-readiness#families",
            wanted=wanted,
            verified_families=verified_targets,
        )
        required.append("image_sample_verified")
    return _result(objective, required, results, steps)


def verify(
    db,
    settings,
    *,
    grant: dict[str, Any],
    steps: list[dict[str, Any]],
) -> dict[str, Any]:
    """Read every required milestone from its original authority."""
    objective = str(grant.get("objective") or "")
    required = required_milestones(objective)
    scope = grant.get("scope") or {}
    project_ids = [str(item) for item in scope.get("project_ids") or []]
    if project_ids and "production" in (scope.get("capability_groups") or []) and any(
        word in objective.casefold() for word in ("交付", "成片", "deliver")
    ):
        return _result(objective, ["episode_delivery"], {
            "episode_delivery": _episode_delivery(db, grant, project_ids),
        }, steps)
    if "production" not in (scope.get("capability_groups") or []):
        image_setup = _image_setup_verification(
            db, settings, objective=objective, project_ids=project_ids, steps=steps,
        )
        if image_setup is not None:
            return image_setup
    results: dict[str, dict[str, Any]] = {}

    # --- what the sub-actions already proved (their own verification rows) ---
    created_profile_ids: list[str] = []
    plan_frozen = False
    for step in steps:
        outcome = str(step.get("outcome") or "")
        capability = str(step.get("capability_id") or "")
        if capability == "resource_install_plan" and outcome == "PLAN_FROZEN":
            plan_frozen = True
        if capability in {"workflow_graph_register", "workflow_user_copy"} and step.get("satisfied"):
            # The action's verification carries the created profile id.
            from shine.support.models import SupportAction

            action = db.get(SupportAction, str(step.get("action_id") or ""))
            verification = (action.verification if action is not None else {}) or {}
            profile_id = verification.get("created_profile_id")
            if profile_id:
                created_profile_ids.append(str(profile_id))
    results["plan_frozen"] = _milestone(
        "VERIFIED" if plan_frozen else "NOT_RUN", "installation_plans",
    )

    # --- graph registration + live node contract, from the profile row ---
    profile: WorkflowProfile | None = None
    for profile_id in created_profile_ids:
        candidate = db.get(WorkflowProfile, profile_id)
        if candidate is not None:
            profile = candidate
            break
    if profile is None and project_ids:
        # A project may already own the profile the objective refers to.
        profile = db.scalar(
            select(WorkflowProfile)
            .where(WorkflowProfile.project_id.in_(project_ids))
            .order_by(WorkflowProfile.created_at.desc())
        )
    if profile is not None:
        validated = profile.validated_against or {}
        missing_nodes = list(validated.get("missing_nodes") or [])
        missing_inputs = list(validated.get("missing_inputs") or [])
        results["graph_registered"] = _milestone(
            "VERIFIED", "workflow_profiles", profile_id=profile.id,
            graph_sha256=profile.graph_sha256,
        )
        results["nodes_loadable"] = _milestone(
            "VERIFIED" if validated and not missing_nodes and not missing_inputs
            else ("FAILED" if validated else "NOT_RUN"),
            "workflow_profiles.validated_against",
            missing_nodes=missing_nodes, missing_input_count=len(missing_inputs),
        )
    else:
        results["graph_registered"] = _milestone("NOT_RUN", "workflow_profiles")
        results["nodes_loadable"] = _milestone("NOT_RUN", "workflow_profiles.validated_against")

    # --- the machine's single verdict + the capability file layer ---
    verdict: dict[str, Any] = {}
    capabilities: dict[str, Any] = {}
    try:
        from shine.comfyops.setup_summary import summary as setup_summary

        snapshot = setup_summary(
            db, settings,
            project_id=project_ids[0] if project_ids else None, discovered=[],
        )
        verdict = snapshot.get("verdict") or {}
        capabilities = snapshot.get("capabilities") or {}
    except Exception as exc:  # noqa: BLE001 - an unreadable authority stays unknown
        verdict = {"mode": "UNKNOWN", "error": type(exc).__name__}
    mode = str(verdict.get("mode") or "UNKNOWN")
    results["setup_verdict"] = _milestone(
        "VERIFIED" if mode in _READY_MODES else "FAILED",
        "GET /api/v1/setup/verdict", mode=mode, code=verdict.get("code"),
    )
    required_caps = [
        key for key, row in capabilities.items()
        if isinstance(row, dict) and not row.get("optional")
    ]
    files_verified = bool(required_caps) and all(
        bool((capabilities.get(key) or {}).get("facts", {}).get("FILES_VERIFIED"))
        or str((capabilities.get(key) or {}).get("state") or "") in {"READY", "VERIFIED_RENDER"}
        for key in required_caps
    )
    results["files_verified"] = _milestone(
        "VERIFIED" if files_verified else ("FAILED" if required_caps else "NOT_RUN"),
        "GET /api/v1/setup/summary#capabilities",
        capabilities=required_caps,
    )

    # --- production actually selected the profile, and a real sample passed ---
    selected_jobs: list[Job] = []
    if profile is not None and project_ids:
        selected_jobs = list(db.scalars(
            select(Job).where(
                Job.project_id.in_(project_ids),
                Job.workflow_profile_id == profile.id,
            )
        ).all())
    results["production_selected"] = _milestone(
        "VERIFIED" if selected_jobs else "NOT_RUN",
        "jobs.workflow_profile_id",
        job_ids=[job.id for job in selected_jobs[:8]],
        profile_id=profile.id if profile is not None else None,
    )
    sample_job: Job | None = None
    sample_artifact: MediaArtifact | None = None
    for job in selected_jobs:
        if job.status != "PASSED" or job.provider == "mock" or not job.artifact_id:
            continue
        artifact = db.get(MediaArtifact, job.artifact_id)
        if artifact is not None and not artifact.is_mock:
            sample_job, sample_artifact = job, artifact
            break
    results["sample_completed"] = _milestone(
        "VERIFIED" if sample_job is not None else "NOT_RUN",
        "jobs + job_attempts + media_artifacts",
        job_id=sample_job.id if sample_job is not None else None,
        artifact_id=sample_artifact.id if sample_artifact is not None else None,
        artifact_sha256=sample_artifact.sha256 if sample_artifact is not None else None,
    )

    return _result(objective, required, results, steps)
