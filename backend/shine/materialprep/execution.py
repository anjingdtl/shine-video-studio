"""Worker-side execution of one material image item.

This is the durable replacement for the synchronous candidate endpoint.  The API
never waits on a renderer; it writes rows and returns 202, and everything here
runs inside the one resident Worker.

The order is the safety property, so it is worth stating once:

1. claim the item with a conditional ``UPDATE`` (status + ``state_version``);
2. resolve the brief into a deterministic prompt **before** touching the GPU,
   because the LLM gateway refuses to run while a resource slot is held;
3. acquire the shared exclusive lease through ``resource_slot``, which is the same
   ledger H3 uses, so a running series is never preempted and an unreleased lease
   blocks this item instead of racing it;
4. persist the submit intent - final graph hash, seed, size, output node, runtime
   fingerprint - and commit;
5. call ``/prompt`` **outside** any SQLite writer transaction, with no automatic
   retry, because the call is not idempotent;
6. persist the returned ``prompt_id`` immediately;
7. harvest only the declared output node, verify the bytes, register the
   candidate and run technical QC;
8. release the lease and only then move the batch on.

An uncertain submission becomes ``UNKNOWN`` and stays that way.  Reconciliation
may adopt bytes that are already on disk after verifying them; it never resubmits.
"""

from __future__ import annotations

import time
from datetime import timedelta
from typing import Any

import httpx
from fastapi import HTTPException
from sqlalchemy import or_, select, update

from shine.comfy.client import (
    ComfyError,
    ComfyPromptRejected,
    ComfySubmissionUncertain,
    ComfyUIClient,
    ComfyUnavailable,
    ComfyWorkflowError,
)
from shine.comfy.workflow import apply_parameters, graph_hash, validate_graph
from shine.comfyops import assets as candidate_assets
from shine.comfyops.loras import StyleLoraError, image_style_payload, resolve_live_image_loras
from shine.comfyops.models import PLAN_PURPOSE_ASSET_PREPARATION, AssetRequirement, PreProductionPlan
from shine.comfyops.scheduler import resource_slot
from shine.materialprep import brief, image_prompts, qwen_workflow, runtime
from shine.materialprep.contracts import MaterialImageBrief, MaterialVisualDirection
from shine.materialprep.ledger import touch_item
from shine.materialprep.models import MaterialImageAttempt, MaterialItem, MaterialRun
from shine.materialprep.states import (
    ATTEMPT_CANCELLED,
    ATTEMPT_FAILED,
    ATTEMPT_OUTPUT_STORED,
    ATTEMPT_QC_PENDING,
    ATTEMPT_SUBMIT_INTENT,
    ATTEMPT_SUBMITTED,
    ATTEMPT_SUCCEEDED,
    ATTEMPT_UNKNOWN,
    EXECUTION_MODE_REAL,
    ITEM_BRIEF_RUNNING,
    ITEM_CANDIDATE_READY,
    ITEM_CLAIMABLE,
    ITEM_FAILED,
    ITEM_NEEDS_REVIEW,
    ITEM_QUEUED,
    ITEM_RUNNING,
    ITEM_SUBMIT_INTENT,
    ITEM_TERMINAL,
    ITEM_WAITING_GPU,
    OUTCOME_ACKNOWLEDGED,
    OUTCOME_FAILED,
    OUTCOME_NOT_SENT,
    OUTCOME_SUCCEEDED,
    OUTCOME_UNKNOWN,
    RECONCILE_REQUIRED,
)
from shine.models import Project, WorkerInstance, utcnow
from shine.security import require_loopback_url
from shine.services import add_audit

IMAGE_BUDGET_MB = 12000
"""Same estimate the existing candidate image slot uses; it is not a measurement."""

IMAGE_LEASE_PRIORITY = 10
"""Below H3's 100, so video production keeps priority on the shared ledger."""

POLL_INTERVAL_S = 1.0
POLL_LIMIT = 1800
CLAIM_SECONDS = 60


def _blocking_code(exc: ImageExecutionError) -> str:
    """The reason a person can act on, never just the outer "not ready" envelope.

    ``IMAGE_RUNTIME_NOT_READY`` covers a missing binding, unverified weights, a
    graph the live renderer does not declare, and a family that has never
    produced a real sample.  Only the inner code says which of those it is, and
    the panel shows this value, so the actionable one wins.
    """
    detail = exc.details.get("code_detail")
    return detail if isinstance(detail, str) and detail else exc.code


class ImageExecutionError(RuntimeError):
    def __init__(self, code: str, **details: Any) -> None:
        super().__init__(code)
        self.code = code
        self.details = details


# Renderer refusals that mean "busy right now", not "broken": the item goes
# back to the queue instead of failing (H3 owns the installation, another
# start is in progress, or a GPU lease is active).
RENDERER_BUSY_CODES = frozenset({
    "IMAGE_RENDERER_INSTALLATION_BUSY_H3",
    "IMAGE_RENDERER_INSTALLATION_UNCERTAIN",
    "IMAGE_RENDERER_START_IN_PROGRESS",
    "IMAGE_RENDERER_GPU_LEASE_ACTIVE",
})


# --- claiming --------------------------------------------------------------


STALE_ITEM_LEASE = timedelta(minutes=10)
"""How long a claimed item may go without progress before another Worker may take it.

Ten minutes is longer than one brief generation and than one image submit, so a
healthy Worker never loses its row; a dead one stops blocking the batch.
"""


def claim_next_item(factory, worker_id: str, *, run_id: str | None = None) -> str | None:
    """Atomically claim the oldest claimable item.

    Items that already persisted a submit intent are deliberately not claimable:
    an external submission may be in flight, and re-claiming one would be the
    shortest path to rendering the same image twice.  Those go through
    ``reconcile_item`` instead.  ``run_id`` narrows the claim to one material
    run, so a setup sample step never grabs an unrelated production item.
    """
    with factory() as db:
        # A capability validation must run before the production items that depend
        # on it: a production item refuses to render until its family has one
        # verified sample, so queueing them oldest-first made every production
        # item fail one round before the validation that would have unblocked it
        # was ever claimed. Validation first, then oldest-first as before.
        from sqlalchemy import and_, case, or_

        # A Worker that died mid-item left its row in BRIEF_RUNNING or
        # WAITING_GPU with a stale heartbeat and nothing to move it on: the batch
        # then reported "generating" forever, and every later retry joined the
        # same dead queue.  Those two states have no external submission yet, so
        # re-claiming one is safe; SUBMIT_INTENT and RUNNING stay excluded
        # because an answer may really be in flight for them.
        stale_after = utcnow() - STALE_ITEM_LEASE
        stale = and_(
            MaterialItem.status.in_((ITEM_BRIEF_RUNNING, ITEM_WAITING_GPU)),
            or_(MaterialItem.heartbeat_at.is_(None),
                MaterialItem.heartbeat_at < stale_after),
        )
        query = (
            select(MaterialItem)
            .join(MaterialRun, MaterialRun.id == MaterialItem.run_id)
            .where(or_(MaterialItem.status.in_(sorted(ITEM_CLAIMABLE)), stale))
            .order_by(
                case((MaterialRun.payload["kind"].as_string() == "validation", 0), else_=1),
                MaterialItem.created_at, MaterialItem.id,
            )
        )
        if run_id is not None:
            query = query.where(MaterialItem.run_id == run_id)
        row = db.scalar(query.limit(1))
        if row is None:
            return None
        item_id, expected = row.id, row.state_version
        run_cancelled = bool((db.get(MaterialRun, row.run_id) or MaterialRun(cancel_requested=True)).cancel_requested)
    if run_cancelled:
        with factory.begin() as db:
            db.execute(
                update(MaterialItem)
                .where(MaterialItem.id == item_id, MaterialItem.state_version == expected)
                .values(status="CANCELLED", state_version=expected + 1, error_code="RUN_CANCELLED")
            )
        return None

    lease_owner = f"image:{item_id}:{worker_id}"
    with factory.begin() as db:
        result = db.execute(
            update(MaterialItem)
            .where(
                MaterialItem.id == item_id,
                MaterialItem.state_version == expected,
                or_(MaterialItem.status.in_(sorted(ITEM_CLAIMABLE)), stale),
            )
            .values(
                status=ITEM_WAITING_GPU,
                state_version=expected + 1,
                worker_id=worker_id,
                lease_owner=lease_owner,
                heartbeat_at=utcnow(),
            )
        )
        if result.rowcount != 1:
            return None
    return item_id


# --- attempt bookkeeping ---------------------------------------------------


def _load_context(factory, item_id: str) -> dict[str, Any]:
    from shine.materialprep.prompt_source import project_visual_facts

    with factory() as db:
        item = db.get(MaterialItem, item_id)
        if item is None:
            raise ImageExecutionError("MATERIAL_ITEM_NOT_FOUND", item_id=item_id)
        run = db.get(MaterialRun, item.run_id)
        requirement = db.get(AssetRequirement, item.requirement_id) if item.requirement_id else None
        plan = db.get(PreProductionPlan, run.plan_id) if run else None
        project = db.get(Project, item.project_id)
        document_id = (plan.payload or {}).get("document_id") if plan else None
        facts = project_visual_facts(db, item.project_id, document_id=document_id)
        return {
            "item_id": item.id,
            "item_status": item.status,
            "item_state_version": item.state_version,
            "item_payload": dict(item.payload or {}),
            "generation_revision": item.generation_revision,
            "run_id": run.id if run else None,
            "run_payload": dict(run.payload or {}) if run else {},
            "run_mode": run.mode if run else None,
            "run_status": run.status if run else None,
            "cancel_requested": bool(run.cancel_requested) if run else True,
            "requirement_payload": dict(requirement.payload or {}) if requirement else {},
            "plan_purpose": plan.purpose if plan else None,
            "project_id": project.id if project else None,
            "project_title": project.title if project else "",
            "film_style": facts["film_style"],
            "style_snapshot": facts["snapshot"],
            "world": facts["world"],
            "world_source": facts.get("world_source") or "",
        }


# --- readiness gate --------------------------------------------------------


def resolve_runtime(
    factory,
    settings,
    family: str,
    *,
    kind: str,
    object_info: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Prove the runtime may really render this family, or refuse by name.

    A validation run is allowed one layer lower (``GRAPH_VERIFIED``) because
    requiring a real sample before the first real sample could never be
    satisfied.  A production batch is not: it needs a current real sample for its
    own family.
    """
    config = runtime.load_config(settings)
    if not config.base_url or not config.instance_id:
        raise ImageExecutionError("IMAGE_RUNTIME_NOT_READY", code_detail="IMAGE_RUNTIME_NOT_BOUND", family=family)
    base_url = require_loopback_url(
        config.base_url, allow_remote=bool(getattr(settings, "allow_remote_comfyui", False))
    )
    files = runtime.verify_model_files(config, settings=settings)
    runtime.persist_verification(settings, config, files)
    if not files["complete"]:
        raise ImageExecutionError(
            "IMAGE_RUNTIME_NOT_READY",
            code_detail="IMAGE_MODEL_FILES_NOT_VERIFIED",
            problems=files["problems"],
            family=family,
        )
    if object_info is None:
        client = ComfyUIClient(base_url, max_retries=0)
        object_info = client.object_info()
    graph_report = runtime.verify_graph_against_object_info(object_info)
    fingerprint = runtime.current_fingerprint(config, graph_report=graph_report)
    if not graph_report["complete"]:
        raise ImageExecutionError(
            "IMAGE_RUNTIME_NOT_READY",
            code_detail="IMAGE_GRAPH_NOT_VERIFIED",
            missing_nodes=graph_report["missing_nodes"],
            missing_inputs=graph_report["missing_inputs"],
            family=family,
        )
    if kind != "validation":
        with factory() as db:
            verified = runtime.render_verified_families(db, fingerprint)
        if family not in verified:
            raise ImageExecutionError(
                "IMAGE_RUNTIME_NOT_READY",
                code_detail="IMAGE_FAMILY_NOT_RENDER_VERIFIED",
                family=family,
                remediation="Run the three small-sample validation tasks for this family first.",
            )
    return {"config": config, "base_url": base_url, "fingerprint": fingerprint, "graph_report": graph_report,
            "object_info": object_info}


# --- submission ------------------------------------------------------------


def _build_graph(config, prompt: image_prompts.ImagePrompt, *, seed: int, width: int, height: int,
                 style_loras=(), style_sampling: dict | None = None) -> dict[str, Any]:
    graph = qwen_workflow.base_graph()
    values = {
        "prompt": prompt.prompt,
        "negative_prompt": prompt.negative_prompt,
        "width": width,
        "height": height,
        "seed": seed,
        "steps": config.steps,
        "cfg": config.cfg,
        "sampler_name": config.sampler_name,
        "scheduler": config.scheduler,
        "denoise": qwen_workflow.DEFAULT_DENOISE,
        "resolution": qwen_workflow.DEFAULT_RESOLUTION,
        "filename_prefix": qwen_workflow.FILENAME_PREFIX,
        **(style_sampling or {}),
    }
    final = apply_parameters(graph, qwen_workflow.PARAMETER_MAP, values)
    qwen_workflow.attach_qwen_style_loras(final, cache_node_id=qwen_workflow.CACHE_NODE, style_loras=style_loras)
    nodes = [*qwen_workflow.REQUIRED_NODES, "LoraLoaderModelOnly"] if style_loras else qwen_workflow.REQUIRED_NODES
    validate_graph(final, required_nodes=nodes)
    return final


def _renderer_answers(base_url: str) -> bool:
    """Is the renderer still there?  Used to tell "nothing is loaded" from
    "the release could not be confirmed"."""
    try:
        httpx.get(f"{base_url}/system_stats", timeout=3.0, trust_env=False)
    except Exception:  # noqa: BLE001 - an unreachable renderer holds no model
        return False
    return True


def _renderer_gone(factory, settings, renderer) -> bool:
    """Whether the renderer that acknowledged this prompt is provably gone.

    A renderer that crashed inside its own native stack leaves no traceback;
    the only fact is that the recorded process is not there any more.  That
    fact is what names the failure; uncertainty stays uncertainty.
    """
    instance_id = str((renderer or {}).get("renderer_instance_id") or "")
    if not instance_id:
        return False
    try:
        from shine.comfyops.image_renderer import image_renderer_state

        with factory() as db:
            state = image_renderer_state(db, settings, instance_id)
    except Exception:  # noqa: BLE001 - uncertainty is never a crash report
        return False
    return state.get("state") == "GONE"


def _ensure_renderer(factory, settings, family: str) -> dict[str, Any]:
    """The renderer that serves the image runtime is started by this workbench.

    A renderer that a script started would keep the weights resident in GPU
    memory and could not be released afterwards, so the Worker starts the
    managed one itself and proves that this installation owns it.  The start
    happens before the lease is taken: the memory admission inside the start
    refuses a renderer while another one still holds the GPU.

    Whether this installation really owns the renderer is recorded with the
    attempt.  A renderer that the workbench does not own may still produce
    bytes, but those bytes are never counted as real verification.
    """
    from shine.comfyops.image_renderer import ensure_image_renderer
    from shine.comfyops.models import ComfyInstance

    config = runtime.load_config(settings)
    if not config.instance_id:
        raise ImageExecutionError(
            "IMAGE_RUNTIME_NOT_READY", code_detail="IMAGE_RUNTIME_NOT_BOUND", family=family
        )
    with factory() as db:
        instance = db.get(ComfyInstance, config.instance_id)
    owned = bool(
        instance is not None
        and instance.payload.get("managed")
        and not instance.payload.get("is_mock")
    )
    if owned:
        try:
            ensure_image_renderer(factory, settings, instance_id=config.instance_id)
        except HTTPException as exc:
            detail = exc.detail if isinstance(exc.detail, str) else str((exc.detail or {}).get("code", ""))
            raise ImageExecutionError(
                "IMAGE_RENDERER_UNAVAILABLE", code_detail=detail or "IMAGE_RENDERER_UNAVAILABLE", family=family
            ) from exc
    return {
        "renderer_owned": owned,
        "renderer_base_url": config.base_url,
        "renderer_instance_id": config.instance_id,
    }


def _manual_prompt(context: dict[str, Any]) -> dict[str, Any] | None:
    """The prompt a person froze for this requirement, when there is one."""
    from shine.materialprep.prompt_source import frozen_prompt

    return frozen_prompt(context.get("requirement_payload") or {})


def _compose_prompt(context: dict[str, Any]) -> image_prompts.ImagePrompt:
    """The prompt this item renders with.

    The material centre shows the same text before the picture exists, so the
    preview and the render share one composer.
    """
    from shine.materialprep.prompt_source import (
        PromptSourceError,
        compose_requirement_prompt,
    )

    payload = context["item_payload"]
    brief_raw = payload.get("brief")
    requirement = context["requirement_payload"]
    try:
        return compose_requirement_prompt(
            family=str(requirement.get("family") or payload.get("family") or ""),
            brief=MaterialImageBrief.model_validate(brief_raw) if brief_raw else None,
            visual_direction=MaterialVisualDirection.model_validate(
                payload.get("visual_direction") or {}),
            requirement_name=str(requirement.get("entity_id") or ""),
            carrier=requirement.get("carrier") or payload.get("carrier"),
            film_style=str(context.get("film_style") or ""),
            description=str(requirement.get("description") or ""),
            manual=_manual_prompt(context),
            world_note=str(context.get("world") or ""),
            world_source=str(context.get("world_source") or ""),
            style_snapshot=context.get("style_snapshot"),
            image_style=image_style_payload(payload),
        )
    except PromptSourceError as exc:
        raise ImageExecutionError(
            exc.code, item_id=context["item_id"], **exc.details) from exc


def _submit_outcome(exc: Exception) -> tuple[str, str, str | None]:
    """Map a transport failure onto (attempt status, external outcome, error code).

    ``ComfyUnavailable`` is the only branch that proves the request was never
    delivered, so it is the only one that may be resumed into a new attempt.
    Everything uncertain stays unknown forever.
    """
    if isinstance(exc, ComfyUnavailable):
        return ATTEMPT_FAILED, OUTCOME_NOT_SENT, "IMAGE_SUBMISSION_NOT_SENT"
    if isinstance(exc, ComfySubmissionUncertain):
        return ATTEMPT_UNKNOWN, OUTCOME_UNKNOWN, "IMAGE_SUBMISSION_OUTCOME_UNKNOWN"
    if isinstance(exc, ComfyPromptRejected):
        return ATTEMPT_FAILED, OUTCOME_FAILED, "IMAGE_PROMPT_REJECTED"
    if isinstance(exc, ComfyWorkflowError):
        return ATTEMPT_FAILED, OUTCOME_FAILED, "IMAGE_WORKFLOW_REJECTED"
    return ATTEMPT_UNKNOWN, OUTCOME_UNKNOWN, "IMAGE_SUBMISSION_OUTCOME_UNKNOWN"


def _poll(client: ComfyUIClient, prompt_id: str) -> dict[str, Any]:
    for _ in range(POLL_LIMIT):
        history = client.history(prompt_id)
        entry = history.get(prompt_id)
        if entry:
            status = entry.get("status") or {}
            if status.get("status_str") == "error":
                raise ImageExecutionError("IMAGE_EXECUTION_FAILED", prompt_id=prompt_id)
            if entry.get("outputs"):
                return entry
            if status.get("completed"):
                return entry
        time.sleep(POLL_INTERVAL_S)
    raise ImageExecutionError("IMAGE_OUTCOME_RECONCILIATION_REQUIRED", prompt_id=prompt_id)


def _harvest(client: ComfyUIClient, history_entry: dict[str, Any]) -> tuple[bytes, dict[str, Any]]:
    """Download the ONE declared output image."""
    entry = qwen_workflow.declared_output_slot(history_entry)
    filename = str(entry["filename"])
    subfolder = str(entry.get("subfolder") or "")
    file_type = str(entry.get("type") or "output")
    if file_type != "output":
        raise ImageExecutionError(
            "IMAGE_OUTPUT_NODE_MISMATCH", reason="declared entry is not an output", filename=filename
        )
    # filename/subfolder are external data; the storage layer re-derives a safe
    # digest-addressed path, so nothing here is joined onto the local filesystem.
    content = client.view(filename, subfolder=subfolder, file_type=file_type)
    return content, {"filename": filename, "subfolder": subfolder, "type": file_type}


def _candidate_filename(db, project_id: str, family: str, entity_id: str) -> str:
    """The file name of one generated material."""
    from shine.materialprep.naming import material_filename

    return material_filename(db, project_id, family, entity_id)


def _register_candidate(
    factory,
    settings,
    context: dict[str, Any],
    attempt_id: str,
    content: bytes,
    family: str,
    *,
    extra_provenance: dict[str, Any] | None = None,
):
    """Verify the bytes through the shared candidate ledger and link the attempt."""
    from shine.comfy.workflow import graph_hash as _hash

    with factory.begin() as db:
        project = db.get(Project, context["project_id"])
        requirement = context["requirement_payload"]
        entity_id = str(requirement.get("entity_id") or context["item_payload"].get("entity_id") or "")
        rights_id = context["run_payload"].get("rights_id")
        provenance = {
            "source": "material_preparation",
            "is_mock": False,
            "renderer_owned": bool((context.get("renderer") or {}).get("renderer_owned")),
            "generator": qwen_workflow.MODEL_FAMILY,
            "generation_mode": qwen_workflow.GENERATION_MODE,
            "template_commit": qwen_workflow.TEMPLATE_COMMIT,
            "model_revision": qwen_workflow.MODEL_REVISION,
            "run_id": context["run_id"],
            "item_id": context["item_id"],
            "attempt_id": attempt_id,
            "generation_revision": context["generation_revision"],
            "usage_scope": "LOCAL_RESEARCH_AND_EVALUATION_ONLY",
            "license": "Qwen RESEARCH LICENSE AGREEMENT (2026-09-20), non-commercial",
            **image_style_payload(context["item_payload"]),
            **(extra_provenance or {}),
        }
        row = candidate_assets.create_candidate(
            db,
            settings,
            project,
            family,
            entity_id,
            content,
            _candidate_filename(db, project.id, family, entity_id),
            rights_id,
            provenance,
        )
        attempt = db.get(MaterialImageAttempt, attempt_id)
        attempt.candidate_id = row.id
        attempt.content_sha256 = row.payload["content_sha256"]
        attempt.byte_size = row.payload["byte_size"]
        attempt.status = ATTEMPT_OUTPUT_STORED
        attempt.payload = {
            **(attempt.payload or {}),
            "candidate": {"id": row.id, "sha256": row.payload["content_sha256"]},
        }
        attempt.sha256 = _hash(attempt.payload)
        item = db.get(MaterialItem, context["item_id"])
        item.payload = {**(item.payload or {}), "candidate_id": row.id}
        item.sha256 = _hash(item.payload)
        return row.id, row.payload["content_sha256"]


def _technical_qc(factory, settings, candidate_id: str) -> dict[str, Any]:
    """Re-hash the stored bytes and really decode them before anything is offered.

    ``candidate_qc`` calls ``verify_candidate``, which re-reads the file and
    compares its digest, so bytes changed after persistence are refused here
    rather than adopted.  Semantic verdicts stay ``PENDING_HUMAN_REVIEW``: a
    technical pass is never an artistic acceptance.
    """
    with factory.begin() as db:
        from shine.media.output_layout import output_root

        row = db.get(candidate_assets.CandidateAsset, candidate_id)
        qc = candidate_assets.candidate_qc(settings, row, root=output_root(db, settings))
        status = row.status
    return {"qc": qc, "candidate_status": status}


# --- the item lifecycle ----------------------------------------------------


def execute_item(factory, settings, item_id: str, worker_id: str) -> dict[str, Any]:
    """Run one claimed item to a candidate, a definite failure, or UNKNOWN.

    ``item_state_version`` is threaded through every write: a Worker whose claim
    expired and was taken over by another Worker fails its next conditional
    update and stops, instead of writing over the new owner's progress.
    """
    context = _load_context(factory, item_id)
    if context["plan_purpose"] != PLAN_PURPOSE_ASSET_PREPARATION:
        # A material item must belong to a material plan. This is the write-side
        # twin of the read-side purpose filter.
        raise ImageExecutionError(
            "PREPRODUCTION_PLAN_PURPOSE_MISMATCH", item_id=item_id, purpose=str(context["plan_purpose"])
        )
    if context["run_mode"] != EXECUTION_MODE_REAL:
        raise ImageExecutionError("IMAGE_REAL_PROVIDER_REQUIRED", run_mode=str(context["run_mode"]))

    version = context["item_state_version"]
    if context["cancel_requested"]:
        touch_item(factory, item_id, version, status="CANCELLED", error_code="RUN_CANCELLED")
        return {"item_id": item_id, "status": "CANCELLED"}

    requirement = context["requirement_payload"]
    family = str(requirement.get("family") or context["item_payload"].get("family") or "")
    kind = str(context["run_payload"].get("kind") or "production")

    # The producer's brief comes first: the appearance text is the producer's
    # authority, and the call happens before the GPU lease is taken because the
    # gateway refuses to run a text model while a resource slot is held.  A
    # frozen, hand-written prompt replaces that step entirely: spending a model
    # call to produce text nobody will read is pure cost.
    if _manual_prompt(context) is not None:
        context["manual_prompt"] = _manual_prompt(context)
    else:
        brief_result = brief.ensure_brief(factory, settings, item_id, worker_id, version=version)
        if brief_result.get("state_version") is None:
            return {"item_id": item_id, "status": "CLAIM_LOST", "code": "CLAIM_LOST"}
        version = brief_result["state_version"]
        if not brief_result.get("ready"):
            return {"item_id": item_id, "status": brief_result.get("status"),
                    "code": brief_result.get("code")}

        # The composition reads the brief the item now carries.
        context["item_payload"] = {**context["item_payload"], "brief": brief_result.get("brief")}

    # The prompt is composed before the GPU is touched: the gateway refuses to run
    # while a resource slot is held, and a prompt failure must not burn a lease.
    try:
        prompt = _compose_prompt(context)
    except ImageExecutionError as exc:
        touch_item(factory, item_id, version, status=ITEM_FAILED, error_code=_blocking_code(exc))
        _audit(factory, context, "MATERIAL_IMAGE_BLOCKED", {"code": exc.code, **exc.details})
        return {"item_id": item_id, "status": ITEM_FAILED, "code": exc.code, "details": exc.details}

    seed = int(context["item_payload"].get("seed") or 0)
    if not 0 <= seed <= qwen_workflow.MAX_SEED:
        touch_item(factory, item_id, version, status=ITEM_FAILED, error_code="IMAGE_SEED_OUT_OF_RANGE")
        return {"item_id": item_id, "status": ITEM_FAILED, "code": "IMAGE_SEED_OUT_OF_RANGE"}

    clients: dict[str, Any] = {}

    def release_models() -> None:
        """Free the renderer's models, or prove that there is nothing to free.

        A renderer that is gone holds no model in GPU memory, so its release is
        confirmed.  A renderer that still answers but cannot confirm is the
        uncertain case, and the ledger has to reconcile it.
        """
        client = clients.get("client")
        if client is None:
            return
        try:
            client.free_models()
        except Exception:
            if _renderer_answers(client.base_url):
                raise

    try:
        # The lease comes first: the renderer is started while the GPU is
        # exclusively ours, so it can never race a running video series, and a
        # busy ledger makes the item wait instead of failing.
        with resource_slot(
            factory,
            "IMAGE",
            IMAGE_BUDGET_MB,
            release_models=release_models,
            wait_seconds=0,
        ) as lease_id:
            renderer = _ensure_renderer(factory, settings, family)
            context["renderer"] = renderer
            resolved = resolve_runtime(factory, settings, family, kind=kind)
            config = resolved["config"]
            fingerprint = resolved["fingerprint"]
            payload = context["item_payload"]
            use_scene_sheet = family == "scene" and kind == "production" and bool(payload.get("scene_sheet_mode"))
            if use_scene_sheet:
                from shine.asset_center.scene_sheet import SCENE_VIEW_HEIGHT, SCENE_VIEW_WIDTH

                width = int(payload.get("width") or SCENE_VIEW_WIDTH)
                height = int(payload.get("height") or SCENE_VIEW_HEIGHT)
            else:
                width = int(payload.get("width") or config.width)
                height = int(payload.get("height") or config.height)
            client = ComfyUIClient(resolved["base_url"], max_retries=0)
            clients["client"] = client
            style_loras = resolve_live_image_loras(
                factory, settings, payload, object_info=resolved["object_info"],
            ) if payload.get("style_loras") else ()
            if style_loras:
                context["item_payload"] = {**payload, "style_loras": [row.canonical_dict() for row in style_loras]}
            graph = _build_graph(config, prompt, seed=seed, width=width, height=height,
                                 style_loras=style_loras, style_sampling=payload.get("style_sampling"))
            graph_sha = graph_hash(graph)
            if family == "character" and kind == "production" and bool(payload.get("character_sheet_mode")):
                return _render_character_sheet_inside_lease(
                    factory, settings, context, version, client, lease_id, graph, graph_sha,
                    fingerprint, family, prompt, renderer, object_info=resolved.get("object_info"),
                )
            if use_scene_sheet:
                return _render_scene_sheet_inside_lease(
                    factory, settings, context, version, client, lease_id, graph, graph_sha,
                    fingerprint, family, prompt, renderer, object_info=resolved.get("object_info"),
                )
            return _render_inside_lease(
                factory, settings, context, version, client, lease_id, graph, graph_sha, fingerprint, family, prompt,
                renderer,
            )
    except HTTPException as exc:
        code = exc.detail if isinstance(exc.detail, str) else str((exc.detail or {}).get("code", "GPU_UNAVAILABLE"))
        if code == "GPU_BUSY_RETRY_OR_QUEUE" or code in RENDERER_BUSY_CODES:
            # Waiting for the GPU is not a failure. Put the item back so a later
            # pass retries it, and never fail a batch because the ledger is busy.
            # The same holds when the unified controller refuses a renderer
            # start because H3 (or another start) owns the installation now.
            touch_item(factory, item_id, version, status=ITEM_QUEUED, lease_id=None, lease_owner=None)
            # The generic ledger wait keeps its historical "GPU" marker; the
            # renderer-busy refusals name their specific reason.
            waiting = "GPU" if code == "GPU_BUSY_RETRY_OR_QUEUE" else code
            return {"item_id": item_id, "status": ITEM_QUEUED, "waiting": waiting}
        touch_item(factory, item_id, version, status=ITEM_FAILED, error_code=code)
        return {"item_id": item_id, "status": ITEM_FAILED, "code": code}
    except StyleLoraError as exc:
        touch_item(factory, item_id, version, status=ITEM_FAILED, error_code=exc.code)
        _audit(factory, context, "MATERIAL_IMAGE_BLOCKED", {"code": exc.code, **exc.details})
        return {"item_id": item_id, "status": ITEM_FAILED, "code": exc.code, "details": exc.details}
    except ImageExecutionError as exc:
        detail_code = str((exc.details or {}).get("code_detail") or "")
        if detail_code in RENDERER_BUSY_CODES:
            # The renderer was refused for a named busy reason (H3 owns the
            # installation, a start is in progress, a GPU lease is active):
            # requeue instead of failing the item (audit R3/R4 fallout).
            touch_item(factory, item_id, version, status=ITEM_QUEUED, lease_id=None, lease_owner=None)
            return {"item_id": item_id, "status": ITEM_QUEUED, "waiting": detail_code}
        touch_item(factory, item_id, version, status=ITEM_FAILED, error_code=_blocking_code(exc))
        return {"item_id": item_id, "status": ITEM_FAILED, "code": exc.code, "details": exc.details}


def _render_inside_lease(factory, settings, context, version, client, lease_id, graph, graph_sha, fingerprint,
                         family, prompt, renderer):
    """Everything that happens while the exclusive lease is held."""
    item_id = context["item_id"]
    seed = int(context["item_payload"].get("seed") or 0)
    latent = graph[qwen_workflow.EMPTY_LATENT_NODE]["inputs"]
    width, height = int(latent["width"]), int(latent["height"])

    attempt_id = _open_attempt(
        factory,
        context,
        lease_id=lease_id,
        graph_sha=graph_sha,
        prompt=prompt,
        seed=seed,
        width=width,
        height=height,
        fingerprint=fingerprint,
        renderer=renderer,
    )
    next_version = touch_item(factory, item_id, version, status=ITEM_SUBMIT_INTENT, lease_id=lease_id,
                               current_attempt_id=attempt_id)
    if next_version is None:
        _close_attempt(factory, attempt_id, status=ATTEMPT_CANCELLED, outcome=OUTCOME_NOT_SENT,
                       error_code="CLAIM_LOST", release_intent=True)
        return {"item_id": item_id, "status": "CLAIM_LOST", "attempt_id": attempt_id}
    version = next_version

    # The submit intent is committed. The external call now runs outside any
    # SQLite writer transaction and is never retried automatically.
    try:
        response = client.queue_prompt(graph, extra_data={"shine_kind": "material_image", "shine_attempt": attempt_id})
    except Exception as exc:  # noqa: BLE001 - _submit_outcome decides recoverability
        status, outcome, code = _submit_outcome(exc)
        unknown = outcome == OUTCOME_UNKNOWN
        item_status = ITEM_NEEDS_REVIEW if unknown else ITEM_FAILED
        _close_attempt(factory, attempt_id, status=status, outcome=outcome, error_code=code,
                       details={"error_type": type(exc).__name__}, release_intent=not unknown)
        touch_item(factory, item_id, version, status=item_status, error_code=code)
        _audit(factory, context,
               "MATERIAL_IMAGE_SUBMISSION_UNCERTAIN" if unknown else "MATERIAL_IMAGE_SUBMISSION_FAILED",
               {"attempt_id": attempt_id, "code": code})
        return {"item_id": item_id, "status": item_status, "code": code, "external_outcome": outcome,
                "attempt_id": attempt_id}

    prompt_id = str(response.get("prompt_id") or "")
    # Persist the acknowledgement immediately: from here a crash is recoverable by
    # querying this prompt id, and never by submitting a second time.
    _close_attempt(factory, attempt_id, status=ATTEMPT_SUBMITTED, outcome=OUTCOME_ACKNOWLEDGED,
                   prompt_id=prompt_id, keep_intent=True)
    next_version = touch_item(factory, item_id, version, status="RUNNING")
    version = next_version if next_version is not None else version
    _audit(factory, context, "MATERIAL_IMAGE_SUBMITTED", {"attempt_id": attempt_id, "prompt_id": prompt_id})

    try:
        history_entry = _poll(client, prompt_id)
        content, source = _harvest(client, history_entry)
    except ImageExecutionError as exc:
        uncertain = exc.code == "IMAGE_OUTCOME_RECONCILIATION_REQUIRED"
        _close_attempt(factory, attempt_id,
                       status=ATTEMPT_UNKNOWN if uncertain else ATTEMPT_FAILED,
                       outcome=OUTCOME_UNKNOWN if uncertain else OUTCOME_FAILED,
                       error_code=exc.code, details=exc.details, release_intent=not uncertain)
        touch_item(factory, item_id, version,
                    status=ITEM_NEEDS_REVIEW if uncertain else ITEM_FAILED, error_code=_blocking_code(exc))
        _audit(factory, context, "MATERIAL_IMAGE_OUTCOME_UNCERTAIN" if uncertain else "MATERIAL_IMAGE_EXECUTION_FAILED",
               {"attempt_id": attempt_id, "prompt_id": prompt_id, "code": exc.code})
        return {"item_id": item_id, "status": ITEM_NEEDS_REVIEW if uncertain else ITEM_FAILED,
                "code": exc.code, "prompt_id": prompt_id, "attempt_id": attempt_id}
    except ComfyError as exc:
        # The renderer stopped answering after it had already acknowledged the
        # prompt.  The bytes may or may not exist, so the outcome stays
        # unknown and is never resubmitted - but the attempt has to reach a
        # terminal state.  A row that stays "submitted" for ever is what
        # blocks every later render of this item and what leaves the ledger
        # claiming that work is still running.
        gone = _renderer_gone(factory, settings, renderer)
        code = "IMAGE_RENDERER_GONE" if gone else "IMAGE_OUTCOME_RECONCILIATION_REQUIRED"
        _close_attempt(factory, attempt_id, status=ATTEMPT_UNKNOWN, outcome=OUTCOME_UNKNOWN,
                       error_code=code,
                       details={"error_type": type(exc).__name__, "renderer_gone": gone,
                                "comfy_code": getattr(exc, "code", None)},
                       release_intent=False)
        touch_item(factory, item_id, version, status=ITEM_NEEDS_REVIEW, error_code=code)
        _audit(factory, context, "MATERIAL_IMAGE_OUTCOME_UNCERTAIN",
               {"attempt_id": attempt_id, "prompt_id": prompt_id, "code": code,
                "renderer_gone": gone})
        return {"item_id": item_id, "status": ITEM_NEEDS_REVIEW, "code": code,
                "prompt_id": prompt_id, "attempt_id": attempt_id}

    candidate_id, content_sha = _register_candidate(factory, settings, context, attempt_id, content, family)
    _close_attempt(factory, attempt_id, status=ATTEMPT_QC_PENDING, outcome=OUTCOME_SUCCEEDED,
                   content_sha256=content_sha, keep_intent=True, harvest=source)
    next_version = touch_item(factory, item_id, version, status="OUTPUT_STORED")
    version = next_version if next_version is not None else version

    qc = _technical_qc(factory, settings, candidate_id)
    passed = qc["candidate_status"] == "READY_FOR_REVIEW"
    _close_attempt(factory, attempt_id, status=ATTEMPT_SUCCEEDED if passed else ATTEMPT_FAILED,
                   outcome=OUTCOME_SUCCEEDED if passed else OUTCOME_FAILED,
                   error_code=None if passed else "IMAGE_TECHNICAL_QC_FAILED", qc=qc["qc"], release_intent=True)
    touch_item(factory, item_id, version,
                status=ITEM_CANDIDATE_READY if passed else ITEM_FAILED,
                error_code=None if passed else "IMAGE_TECHNICAL_QC_FAILED")
    _audit(factory, context,
           "MATERIAL_IMAGE_CANDIDATE_READY" if passed else "MATERIAL_IMAGE_QC_FAILED",
           {"attempt_id": attempt_id, "candidate_id": candidate_id, "content_sha256": content_sha,
            "prompt_id": prompt_id, "graph_sha256": graph_sha, "technical": qc["qc"].get("technical"),
            "semantic": qc["qc"].get("semantic")})
    return {
        "item_id": item_id,
        "status": ITEM_CANDIDATE_READY if passed else ITEM_FAILED,
        "attempt_id": attempt_id,
        "candidate_id": candidate_id,
        "content_sha256": content_sha,
        "prompt_id": prompt_id,
        "graph_sha256": graph_sha,
        "runtime_fingerprint": fingerprint,
    }


def _set_character_sheet_progress(factory, item_id: str, version: int, progress: dict[str, Any],
                                  *, character_sheet_lora: dict[str, Any] | None = None) -> int:
    with factory.begin() as db:
        item = db.get(MaterialItem, item_id)
        if item is None or item.state_version != version:
            raise ImageExecutionError("CLAIM_LOST", item_id=item_id)
        payload = {
            **(item.payload or {}),
            "character_sheet_mode": True,
            "character_sheet_progress": progress,
            **({"character_sheet_lora": character_sheet_lora} if character_sheet_lora is not None else {}),
        }
        item.payload = payload
        item.sha256 = graph_hash(payload)
        item.state_version += 1
        item.heartbeat_at = utcnow()
        return item.state_version


def _set_scene_sheet_progress(factory, item_id: str, version: int, progress: dict[str, Any],
                              *, scene_sheet_lora: dict[str, Any] | None = None) -> int:
    with factory.begin() as db:
        item = db.get(MaterialItem, item_id)
        if item is None or item.state_version != version:
            raise ImageExecutionError("CLAIM_LOST", item_id=item_id)
        payload = {
            **(item.payload or {}),
            "scene_sheet_mode": True,
            "scene_sheet_progress": progress,
            **({"scene_sheet_lora": scene_sheet_lora} if scene_sheet_lora is not None else {}),
        }
        item.payload = payload
        item.sha256 = graph_hash(payload)
        item.state_version += 1
        item.heartbeat_at = utcnow()
        return item.state_version


def _execute_step_attempt(
    factory,
    settings,
    context: dict[str, Any],
    *,
    version: int,
    client: ComfyUIClient,
    lease_id: str,
    graph: dict[str, Any],
    graph_sha: str,
    fingerprint: str,
    family: str,
    prompt: image_prompts.ImagePrompt | None,
    prompt_text: str,
    negative_prompt_text: str,
    seed: int,
    width: int,
    height: int,
    renderer: dict[str, Any],
    stage_key: str,
    extra_attempt_payload: dict[str, Any] | None = None,
    template_version: str = "character-sheet-v2",
) -> tuple[bool, dict[str, Any]]:
    """Run one ComfyUI graph inside the active lease with durable SUBMIT_INTENT / non-replay rules."""
    item_id = context["item_id"]
    if prompt is not None:
        attempt_prompt = prompt
    else:
        attempt_prompt = image_prompts.ImagePrompt(
            prompt=prompt_text,
            negative_prompt=negative_prompt_text,
            template_version=template_version,
            sha256=graph_hash({"prompt": prompt_text, "negative_prompt": negative_prompt_text, "stage": stage_key}),
        )

    attempt_id = _open_attempt(
        factory,
        context,
        lease_id=lease_id,
        graph_sha=graph_sha,
        prompt=attempt_prompt,
        seed=seed,
        width=width,
        height=height,
        fingerprint=fingerprint,
        renderer=renderer,
        extra_payload=extra_attempt_payload,
    )
    next_version = touch_item(
        factory, item_id, version, status=ITEM_SUBMIT_INTENT, lease_id=lease_id, current_attempt_id=attempt_id
    )
    if next_version is None:
        _close_attempt(factory, attempt_id, status=ATTEMPT_CANCELLED, outcome=OUTCOME_NOT_SENT,
                       error_code="CLAIM_LOST", release_intent=True)
        return False, {"item_id": item_id, "status": "CLAIM_LOST", "attempt_id": attempt_id}
    version = next_version

    try:
        response = client.queue_prompt(
            graph,
            extra_data={"shine_kind": "material_image", "shine_attempt": attempt_id, "shine_step": stage_key},
        )
    except Exception as exc:  # noqa: BLE001 - _submit_outcome decides recoverability
        status, outcome, code = _submit_outcome(exc)
        unknown = outcome == OUTCOME_UNKNOWN
        item_status = ITEM_NEEDS_REVIEW if unknown else ITEM_FAILED
        _close_attempt(factory, attempt_id, status=status, outcome=outcome, error_code=code,
                       details={"error_type": type(exc).__name__, "stage": stage_key}, release_intent=not unknown)
        touch_item(factory, item_id, version, status=item_status, error_code=code)
        _audit(factory, context,
               "MATERIAL_IMAGE_SUBMISSION_UNCERTAIN" if unknown else "MATERIAL_IMAGE_SUBMISSION_FAILED",
               {"attempt_id": attempt_id, "code": code, "stage": stage_key})
        return False, {
            "item_id": item_id, "status": item_status, "code": code,
            "external_outcome": outcome, "attempt_id": attempt_id,
        }

    prompt_id = str(response.get("prompt_id") or "")
    _close_attempt(factory, attempt_id, status=ATTEMPT_SUBMITTED, outcome=OUTCOME_ACKNOWLEDGED,
                   prompt_id=prompt_id, keep_intent=True)
    next_version = touch_item(factory, item_id, version, status="RUNNING")
    version = next_version if next_version is not None else version
    _audit(factory, context, "MATERIAL_IMAGE_SUBMITTED",
           {"attempt_id": attempt_id, "prompt_id": prompt_id, "stage": stage_key})

    try:
        history_entry = _poll(client, prompt_id)
        content, source = _harvest(client, history_entry)
    except ImageExecutionError as exc:
        uncertain = exc.code == "IMAGE_OUTCOME_RECONCILIATION_REQUIRED"
        _close_attempt(factory, attempt_id,
                       status=ATTEMPT_UNKNOWN if uncertain else ATTEMPT_FAILED,
                       outcome=OUTCOME_UNKNOWN if uncertain else OUTCOME_FAILED,
                       error_code=exc.code, details={**exc.details, "stage": stage_key}, release_intent=not uncertain)
        touch_item(factory, item_id, version,
                   status=ITEM_NEEDS_REVIEW if uncertain else ITEM_FAILED, error_code=_blocking_code(exc))
        _audit(factory, context, "MATERIAL_IMAGE_OUTCOME_UNCERTAIN" if uncertain else "MATERIAL_IMAGE_EXECUTION_FAILED",
               {"attempt_id": attempt_id, "prompt_id": prompt_id, "code": exc.code, "stage": stage_key})
        return False, {
            "item_id": item_id,
            "status": ITEM_NEEDS_REVIEW if uncertain else ITEM_FAILED,
            "code": exc.code,
            "prompt_id": prompt_id,
            "attempt_id": attempt_id,
        }
    except ComfyError as exc:
        gone = _renderer_gone(factory, settings, renderer)
        code = "IMAGE_RENDERER_GONE" if gone else "IMAGE_OUTCOME_RECONCILIATION_REQUIRED"
        _close_attempt(factory, attempt_id, status=ATTEMPT_UNKNOWN, outcome=OUTCOME_UNKNOWN,
                       error_code=code,
                       details={"error_type": type(exc).__name__, "renderer_gone": gone,
                                "comfy_code": getattr(exc, "code", None), "stage": stage_key},
                       release_intent=False)
        touch_item(factory, item_id, version, status=ITEM_NEEDS_REVIEW, error_code=code)
        _audit(factory, context, "MATERIAL_IMAGE_OUTCOME_UNCERTAIN",
               {"attempt_id": attempt_id, "prompt_id": prompt_id, "code": code,
                "renderer_gone": gone, "stage": stage_key})
        return False, {"item_id": item_id, "status": ITEM_NEEDS_REVIEW, "code": code,
                       "prompt_id": prompt_id, "attempt_id": attempt_id}

    return True, {
        "attempt_id": attempt_id,
        "prompt_id": prompt_id,
        "content": content,
        "harvest": source,
        "version": version,
    }


def _render_character_sheet_inside_lease(
    factory,
    settings,
    context: dict[str, Any],
    version: int,
    client: ComfyUIClient,
    lease_id: str,
    t2i_graph: dict[str, Any],
    t2i_graph_sha: str,
    fingerprint: str,
    family: str,
    prompt: image_prompts.ImagePrompt,
    renderer: dict[str, Any],
    *,
    object_info: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Generate a production character as T2I seed portrait -> 5-view 16:9 Character Sheet in 1 lease."""
    import hashlib

    from shine.asset_center.character_sheet import (
        CHARACTER_SHEET_BASE_SAMPLING,
        CHARACTER_SHEET_LAYOUT_VERSION,
        CHARACTER_SHEET_LORA_SAMPLING,
        CHARACTER_SHEET_OPERATION,
        CHARACTER_SHEET_SETUP_HINT,
        STEP_SPECS,
        build_step_prompts,
        compose_character_sheet,
        extract_character_sheet_note_from_material,
        freeze_character_sheet_lora,
        step_dimensions,
    )
    from shine.asset_center.contracts import build_qwen_edit_graph
    from shine.asset_center.execution import _reference_prompt
    from shine.comfy.workflow import validate_against_object_info
    from shine.security import probe_upload, verify_upload

    item_id = context["item_id"]
    live_object_info = object_info if object_info is not None else client.object_info()

    # Fail-closed before running T2I: re-verify frozen multiple-angles LoRA (or freeze if
    # legacy item) + live ComfyUI visibility + edit graph contract first.
    cs_frozen = context["item_payload"].get("character_sheet_lora")
    if not isinstance(cs_frozen, dict) or not cs_frozen.get("style_loras"):
        with factory.begin() as db:
            cs_frozen = freeze_character_sheet_lora(db, settings)
    cs_style_payload = {
        "style_loras": cs_frozen["style_loras"],
        "style_sampling": cs_frozen.get("style_sampling") or dict(CHARACTER_SHEET_LORA_SAMPLING),
    }
    cs_loras = resolve_live_image_loras(factory, settings, cs_style_payload, object_info=live_object_info)
    if not cs_loras:
        raise StyleLoraError(
            "CHARACTER_SHEET_LORA_REQUIRED",
            f"缺少「Qwen 多视角镜头」LoRA 权重。{CHARACTER_SHEET_SETUP_HINT}",
        )
    probe_graph = build_qwen_edit_graph(
        prompt="probe",
        negative_prompt="probe",
        uploaded_names=["probe.png"],
        width=STEP_SPECS[0].target_width,
        height=STEP_SPECS[0].target_height,
        seed=1,
        style_loras=cs_loras,
        **CHARACTER_SHEET_LORA_SAMPLING,
    )
    validate_graph(probe_graph, required_nodes=[str(node["class_type"]) for node in probe_graph.values()])
    edit_report = validate_against_object_info(probe_graph, {}, live_object_info)
    if edit_report["missing_nodes"] or edit_report["missing_inputs"]:
        raise ImageExecutionError("QWEN_EDIT_WORKFLOW_CONTRACT_CHANGED", **edit_report)

    total_steps = 1 + len(STEP_SPECS)
    version = _set_character_sheet_progress(
        factory,
        item_id,
        version,
        {
            "current_step": "t2i_seed_portrait",
            "current_step_label": "文生图基准肖像",
            "completed_steps": [],
            "total_steps": total_steps,
        },
        character_sheet_lora=cs_frozen,
    )

    base_seed = int(context["item_payload"].get("seed") or 0)
    t2i_latent = t2i_graph[qwen_workflow.EMPTY_LATENT_NODE]["inputs"]
    t2i_w, t2i_h = int(t2i_latent["width"]), int(t2i_latent["height"])

    # Stage 0: T2I base character portrait (retains project style & T2I appearance brief).
    ok, res = _execute_step_attempt(
        factory,
        settings,
        context,
        version=version,
        client=client,
        lease_id=lease_id,
        graph=t2i_graph,
        graph_sha=t2i_graph_sha,
        fingerprint=fingerprint,
        family=family,
        prompt=prompt,
        prompt_text=prompt.prompt,
        negative_prompt_text=prompt.negative_prompt,
        seed=base_seed,
        width=t2i_w,
        height=t2i_h,
        renderer=renderer,
        stage_key="t2i_seed_portrait",
        extra_attempt_payload={"character_sheet_stage": "t2i_seed_portrait", "step_index": 0},
    )
    if not ok:
        return res

    version = res["version"]
    t2i_bytes = res["content"]
    t2i_type = verify_upload("image", "t2i_seed_portrait.png", t2i_bytes, settings.max_upload_bytes)
    t2i_info = probe_upload("image", t2i_bytes, t2i_type)
    t2i_sha = hashlib.sha256(t2i_bytes).hexdigest()
    t2i_record = {
        "step_key": "t2i_seed_portrait",
        "label": "文生图基准肖像",
        "prompt_id": res["prompt_id"],
        "graph_sha256": t2i_graph_sha,
        "sha256": t2i_sha,
        "seed": base_seed,
        "width": t2i_info.width,
        "height": t2i_info.height,
    }
    _close_attempt(
        factory,
        res["attempt_id"],
        status=ATTEMPT_SUCCEEDED,
        outcome=OUTCOME_SUCCEEDED,
        content_sha256=t2i_sha,
        harvest=res["harvest"],
        details={"stage": "t2i_seed_portrait", "step_record": t2i_record},
        release_intent=True,
    )

    cs_note = extract_character_sheet_note_from_material(
        context["requirement_payload"],
        context["item_payload"].get("brief"),
        str(context["item_payload"].get("brief_note") or ""),
    )
    step_outputs: dict[str, bytes] = {}
    step_records: list[dict[str, Any]] = []
    front_dims: tuple[int, int] | None = None
    final_attempt_id = res["attempt_id"]
    final_prompt_id = res["prompt_id"]
    final_graph_sha = t2i_graph_sha
    final_harvest = res["harvest"]
    completed_progress_steps: list[dict[str, Any]] = [t2i_record]

    # Stages 1..5: Fixed Character Sheet pipeline inside the same GPU lease.
    for step_index, spec in enumerate(STEP_SPECS):
        version = _set_character_sheet_progress(
            factory,
            item_id,
            version,
            {
                "current_step": spec.key,
                "current_step_label": spec.label,
                "completed_steps": list(completed_progress_steps),
                "total_steps": total_steps,
            },
        )
        step_seed = (base_seed + step_index + 1) & qwen_workflow.MAX_SEED
        step_loras = cs_loras if spec.uses_lora else ()
        step_sampling = dict(CHARACTER_SHEET_LORA_SAMPLING if spec.uses_lora else CHARACTER_SHEET_BASE_SAMPLING)

        if spec.reference_source == "input":
            if spec.key.startswith("closeup_"):
                step_w, step_h = spec.target_width, spec.target_height
            else:
                step_w, step_h = step_dimensions(
                    t2i_info.width, t2i_info.height, target_width=spec.target_width, target_height=spec.target_height
                )
            step_ref_bytes = t2i_bytes
            step_ref_sha = t2i_sha
            step_ref_type = t2i_type
        else:
            step_ref_bytes = step_outputs["fullbody_front"]
            step_w, step_h = front_dims or (spec.target_width, spec.target_height)
            step_ref_sha = hashlib.sha256(step_ref_bytes).hexdigest()
            step_ref_type = "image/png"

        step_pos, step_neg = build_step_prompts(
            spec.key,
            note=cs_note,
            style_loras=cs_frozen["style_loras"] if spec.uses_lora else (),
        )
        graph_refs = [{"order": 0, "role": "subject", "sha256": step_ref_sha}]
        uploaded = client.upload_image(
            f"mat_{step_ref_sha[:16]}.png",
            step_ref_bytes,
            subfolder="",
            content_type=step_ref_type,
        )
        uploaded_name = str(uploaded.get("name") or "")
        if not uploaded_name or "/" in uploaded_name or "\\" in uploaded_name:
            raise ImageExecutionError("COMFY_INPUT_UPLOAD_RESPONSE_INVALID", stage=spec.key)
        step_graph = build_qwen_edit_graph(
            prompt=_reference_prompt(step_pos, graph_refs),
            negative_prompt=step_neg,
            uploaded_names=[uploaded_name],
            width=step_w,
            height=step_h,
            seed=step_seed,
            style_loras=step_loras,
            **step_sampling,
        )
        validate_graph(step_graph, required_nodes=[str(node["class_type"]) for node in step_graph.values()])
        step_graph_sha = graph_hash(step_graph)

        ok, step_res = _execute_step_attempt(
            factory,
            settings,
            context,
            version=version,
            client=client,
            lease_id=lease_id,
            graph=step_graph,
            graph_sha=step_graph_sha,
            fingerprint=fingerprint,
            family=family,
            prompt=None,
            prompt_text=step_pos,
            negative_prompt_text=step_neg,
            seed=step_seed,
            width=step_w,
            height=step_h,
            renderer=renderer,
            stage_key=spec.key,
            extra_attempt_payload={
                "character_sheet_stage": spec.key,
                "step_index": step_index + 1,
                **({"style_loras": cs_frozen["style_loras"]} if spec.uses_lora else {}),
            },
        )
        if not ok:
            return step_res

        version = step_res["version"]
        step_bytes = step_res["content"]
        harvested_type = verify_upload("image", f"{spec.key}.png", step_bytes, settings.max_upload_bytes)
        harvested_info = probe_upload("image", step_bytes, harvested_type)
        if harvested_info.width != step_w or harvested_info.height != step_h:
            _close_attempt(
                factory,
                step_res["attempt_id"],
                status=ATTEMPT_FAILED,
                outcome=OUTCOME_FAILED,
                error_code="IMAGE_STEP_DIMENSIONS_MISMATCH",
                details={
                    "stage": spec.key,
                    "expected": [step_w, step_h],
                    "actual": [harvested_info.width, harvested_info.height],
                },
                release_intent=True,
            )
            touch_item(factory, item_id, version, status=ITEM_FAILED, error_code="IMAGE_STEP_DIMENSIONS_MISMATCH")
            return {
                "item_id": item_id,
                "status": ITEM_FAILED,
                "code": "IMAGE_STEP_DIMENSIONS_MISMATCH",
                "attempt_id": step_res["attempt_id"],
            }

        step_sha = hashlib.sha256(step_bytes).hexdigest()
        record = {
            "step_key": spec.key,
            "label": spec.label,
            "prompt_id": step_res["prompt_id"],
            "graph_sha256": step_graph_sha,
            "sha256": step_sha,
            "seed": step_seed,
            "width": harvested_info.width,
            "height": harvested_info.height,
            "uses_lora": spec.uses_lora,
            "sampling": step_sampling,
        }
        step_outputs[spec.key] = step_bytes
        step_records.append(record)
        completed_progress_steps.append(record)
        if spec.key == "fullbody_front":
            front_dims = (harvested_info.width, harvested_info.height)

        final_attempt_id = step_res["attempt_id"]
        final_prompt_id = step_res["prompt_id"]
        final_graph_sha = step_graph_sha
        final_harvest = step_res["harvest"]
        is_last = step_index == len(STEP_SPECS) - 1
        if not is_last:
            _close_attempt(
                factory,
                step_res["attempt_id"],
                status=ATTEMPT_SUCCEEDED,
                outcome=OUTCOME_SUCCEEDED,
                content_sha256=step_sha,
                harvest=step_res["harvest"],
                details={"stage": spec.key, "step_record": record},
                release_intent=True,
            )

    composed = compose_character_sheet(
        closeup_front_bytes=step_outputs["closeup_front"],
        closeup_3_4_bytes=step_outputs["closeup_3_4"],
        front_bytes=step_outputs["fullbody_front"],
        side_bytes=step_outputs["fullbody_side"],
        back_bytes=step_outputs["fullbody_back"],
    )
    composite_bytes = composed["composite_sheet_bytes"]
    composite_sha = hashlib.sha256(composite_bytes).hexdigest()
    composite_provenance = {
        "pipeline": "t2i_to_character_sheet_v2",
        "t2i_seed_portrait": t2i_record,
        "character_sheet": {
            "operation": CHARACTER_SHEET_OPERATION,
            "artifact_role": "composite_sheet",
            "artifact_label": "16:9 整体角色定妆照（正面/3/4 侧面肩上 + 全身三视图）",
            "layout_version": CHARACTER_SHEET_LAYOUT_VERSION,
            "layout": composed["layout"],
            "source_reference_sha256": t2i_sha,
            "note": cs_note,
            "lora": dict(cs_frozen["style_loras"][0]),
            "steps": step_records,
        },
    }
    candidate_id, content_sha = _register_candidate(
        factory,
        settings,
        context,
        final_attempt_id,
        composite_bytes,
        family,
        extra_provenance=composite_provenance,
    )
    _close_attempt(
        factory,
        final_attempt_id,
        status=ATTEMPT_QC_PENDING,
        outcome=OUTCOME_SUCCEEDED,
        content_sha256=content_sha,
        keep_intent=True,
        harvest=final_harvest,
        details={"stage": STEP_SPECS[-1].key, "composite_sha256": composite_sha,
                 "composite_dimensions": composed["composite_sheet_size"]},
    )
    next_version = touch_item(factory, item_id, version, status="OUTPUT_STORED")
    version = next_version if next_version is not None else version

    qc = _technical_qc(factory, settings, candidate_id)
    passed = qc["candidate_status"] == "READY_FOR_REVIEW"
    _close_attempt(
        factory,
        final_attempt_id,
        status=ATTEMPT_SUCCEEDED if passed else ATTEMPT_FAILED,
        outcome=OUTCOME_SUCCEEDED if passed else OUTCOME_FAILED,
        error_code=None if passed else "IMAGE_TECHNICAL_QC_FAILED",
        qc=qc["qc"],
        release_intent=True,
    )
    version = _set_character_sheet_progress(
        factory,
        item_id,
        version,
        {
            "current_step": None,
            "current_step_label": None,
            "completed_steps": list(completed_progress_steps),
            "total_steps": total_steps,
        },
    )
    touch_item(
        factory,
        item_id,
        version,
        status=ITEM_CANDIDATE_READY if passed else ITEM_FAILED,
        error_code=None if passed else "IMAGE_TECHNICAL_QC_FAILED",
    )
    _audit(
        factory,
        context,
        "MATERIAL_IMAGE_CANDIDATE_READY" if passed else "MATERIAL_IMAGE_QC_FAILED",
        {
            "attempt_id": final_attempt_id,
            "candidate_id": candidate_id,
            "content_sha256": content_sha,
            "prompt_id": final_prompt_id,
            "graph_sha256": final_graph_sha,
            "pipeline": "t2i_to_character_sheet_v2",
            "technical": qc["qc"].get("technical"),
            "semantic": qc["qc"].get("semantic"),
        },
    )
    return {
        "item_id": item_id,
        "status": ITEM_CANDIDATE_READY if passed else ITEM_FAILED,
        "attempt_id": final_attempt_id,
        "candidate_id": candidate_id,
        "content_sha256": content_sha,
        "prompt_id": final_prompt_id,
        "graph_sha256": final_graph_sha,
        "runtime_fingerprint": fingerprint,
    }


def _render_scene_sheet_inside_lease(
    factory,
    settings,
    context: dict[str, Any],
    version: int,
    client: ComfyUIClient,
    lease_id: str,
    t2i_graph: dict[str, Any],
    t2i_graph_sha: str,
    fingerprint: str,
    family: str,
    prompt: image_prompts.ImagePrompt,
    renderer: dict[str, Any],
    *,
    object_info: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Generate a production scene as 4 full-size 16:9 single-angle views + 4-in-1 master sheet in 1 lease."""
    import hashlib

    from shine.asset_center.contracts import build_qwen_edit_graph
    from shine.asset_center.execution import _reference_prompt
    from shine.asset_center.scene_sheet import (
        ALL_STEP_SPECS,
        EDIT_STEP_SPECS,
        FRONT_STEP_SPEC,
        SCENE_SHEET_LAYOUT_VERSION,
        SCENE_SHEET_LORA_SAMPLING,
        SCENE_SHEET_OPERATION,
        SCENE_SHEET_PIPELINE,
        SCENE_SHEET_SETUP_HINT,
        build_scene_step_prompts,
        compose_scene_sheet,
        extract_scene_sheet_note_from_material,
        freeze_scene_sheet_lora,
    )
    from shine.comfy.workflow import validate_against_object_info
    from shine.media.output_layout import ensure_storage_writable, store_project_asset
    from shine.security import probe_upload, verify_upload

    item_id = context["item_id"]
    live_object_info = object_info if object_info is not None else client.object_info()

    # Fail-closed before running T2I: re-verify frozen multiple-angles LoRA + live
    # ComfyUI visibility + Qwen edit graph contract. Never silently downgrade.
    ss_frozen = context["item_payload"].get("scene_sheet_lora")
    if not isinstance(ss_frozen, dict) or not ss_frozen.get("style_loras"):
        with factory.begin() as db:
            ss_frozen = freeze_scene_sheet_lora(db, settings)
    ss_style_payload = {
        "style_loras": ss_frozen["style_loras"],
        "style_sampling": ss_frozen.get("style_sampling") or dict(SCENE_SHEET_LORA_SAMPLING),
    }
    ss_loras = resolve_live_image_loras(factory, settings, ss_style_payload, object_info=live_object_info)
    if not ss_loras:
        raise StyleLoraError(
            "SCENE_SHEET_LORA_REQUIRED",
            f"缺少「Qwen 多视角镜头」LoRA 权重。{SCENE_SHEET_SETUP_HINT}",
        )
    probe_graph = build_qwen_edit_graph(
        prompt="probe",
        negative_prompt="probe",
        uploaded_names=["probe.png"],
        width=EDIT_STEP_SPECS[0].target_width,
        height=EDIT_STEP_SPECS[0].target_height,
        seed=1,
        style_loras=ss_loras,
        **SCENE_SHEET_LORA_SAMPLING,
    )
    validate_graph(probe_graph, required_nodes=[str(node["class_type"]) for node in probe_graph.values()])
    edit_report = validate_against_object_info(probe_graph, {}, live_object_info)
    if edit_report["missing_nodes"] or edit_report["missing_inputs"]:
        raise ImageExecutionError("QWEN_EDIT_WORKFLOW_CONTRACT_CHANGED", **edit_report)

    total_steps = len(ALL_STEP_SPECS)
    version = _set_scene_sheet_progress(
        factory,
        item_id,
        version,
        {
            "current_step": FRONT_STEP_SPEC.key,
            "current_step_label": FRONT_STEP_SPEC.label,
            "completed_steps": [],
            "total_steps": total_steps,
        },
        scene_sheet_lora=ss_frozen,
    )

    base_seed = int(context["item_payload"].get("seed") or 0)
    t2i_latent = t2i_graph[qwen_workflow.EMPTY_LATENT_NODE]["inputs"]
    front_w, front_h = int(t2i_latent["width"]), int(t2i_latent["height"])

    # Step 0: front_wide via T2I (1344x768)
    ok, res = _execute_step_attempt(
        factory,
        settings,
        context,
        version=version,
        client=client,
        lease_id=lease_id,
        graph=t2i_graph,
        graph_sha=t2i_graph_sha,
        fingerprint=fingerprint,
        family=family,
        prompt=prompt,
        prompt_text=prompt.prompt,
        negative_prompt_text=prompt.negative_prompt,
        seed=base_seed,
        width=front_w,
        height=front_h,
        renderer=renderer,
        stage_key=FRONT_STEP_SPEC.key,
        extra_attempt_payload={"scene_sheet_stage": FRONT_STEP_SPEC.key, "step_index": 0},
        template_version="scene-sheet-v1",
    )
    if not ok:
        return res

    version = res["version"]
    front_bytes = res["content"]
    front_type = verify_upload("image", f"{FRONT_STEP_SPEC.key}.png", front_bytes, settings.max_upload_bytes)
    front_info = probe_upload("image", front_bytes, front_type)
    front_sha = hashlib.sha256(front_bytes).hexdigest()
    front_record = {
        "step_key": FRONT_STEP_SPEC.key,
        "angle_key": FRONT_STEP_SPEC.angle_key,
        "label": FRONT_STEP_SPEC.label,
        "prompt_id": res["prompt_id"],
        "graph_sha256": t2i_graph_sha,
        "sha256": front_sha,
        "seed": base_seed,
        "width": front_info.width,
        "height": front_info.height,
        "uses_lora": False,
    }
    _close_attempt(
        factory,
        res["attempt_id"],
        status=ATTEMPT_SUCCEEDED,
        outcome=OUTCOME_SUCCEEDED,
        content_sha256=front_sha,
        harvest=res["harvest"],
        details={"stage": FRONT_STEP_SPEC.key, "step_record": front_record},
        release_intent=True,
    )

    scene_note = extract_scene_sheet_note_from_material(
        context["requirement_payload"],
        context["item_payload"].get("brief"),
        str(context["item_payload"].get("brief_note") or ""),
    )
    uploaded = client.upload_image(
        f"mat_scene_{front_sha[:16]}.png",
        front_bytes,
        subfolder="",
        content_type=front_type,
    )
    uploaded_front_name = str(uploaded.get("name") or "")
    if not uploaded_front_name or "/" in uploaded_front_name or "\\" in uploaded_front_name:
        raise ImageExecutionError("COMFY_INPUT_UPLOAD_RESPONSE_INVALID", stage=FRONT_STEP_SPEC.key)

    view_bytes: dict[str, bytes] = {FRONT_STEP_SPEC.angle_key: front_bytes}
    view_dims: dict[str, tuple[int, int]] = {FRONT_STEP_SPEC.angle_key: (front_info.width, front_info.height)}
    view_shas: dict[str, str] = {FRONT_STEP_SPEC.angle_key: front_sha}
    step_records: list[dict[str, Any]] = [front_record]
    completed_progress_steps: list[dict[str, Any]] = [front_record]
    final_attempt_id = res["attempt_id"]
    final_prompt_id = res["prompt_id"]
    final_graph_sha = t2i_graph_sha
    final_harvest = res["harvest"]

    # Steps 1..3: left_quarter, right_quarter, reverse_wide via Qwen-Image Edit + <mva>
    for step_index, spec in enumerate(EDIT_STEP_SPECS, start=1):
        version = _set_scene_sheet_progress(
            factory,
            item_id,
            version,
            {
                "current_step": spec.key,
                "current_step_label": spec.label,
                "completed_steps": list(completed_progress_steps),
                "total_steps": total_steps,
            },
        )
        step_seed = (base_seed + step_index) & qwen_workflow.MAX_SEED
        step_w, step_h = front_w, front_h
        step_sampling = dict(SCENE_SHEET_LORA_SAMPLING)
        step_pos, step_neg = build_scene_step_prompts(
            spec.key,
            note=scene_note,
            style_loras=ss_frozen["style_loras"],
        )
        graph_refs = [{"order": 0, "role": "scene", "sha256": front_sha}]
        step_graph = build_qwen_edit_graph(
            prompt=_reference_prompt(step_pos, graph_refs),
            negative_prompt=step_neg,
            uploaded_names=[uploaded_front_name],
            width=step_w,
            height=step_h,
            seed=step_seed,
            style_loras=ss_loras,
            **step_sampling,
        )
        validate_graph(step_graph, required_nodes=[str(node["class_type"]) for node in step_graph.values()])
        step_graph_sha = graph_hash(step_graph)

        ok, step_res = _execute_step_attempt(
            factory,
            settings,
            context,
            version=version,
            client=client,
            lease_id=lease_id,
            graph=step_graph,
            graph_sha=step_graph_sha,
            fingerprint=fingerprint,
            family=family,
            prompt=None,
            prompt_text=step_pos,
            negative_prompt_text=step_neg,
            seed=step_seed,
            width=step_w,
            height=step_h,
            renderer=renderer,
            stage_key=spec.key,
            extra_attempt_payload={
                "scene_sheet_stage": spec.key,
                "step_index": step_index,
                "style_loras": ss_frozen["style_loras"],
            },
            template_version="scene-sheet-v1",
        )
        if not ok:
            return step_res

        version = step_res["version"]
        step_bytes = step_res["content"]
        harvested_type = verify_upload("image", f"{spec.key}.png", step_bytes, settings.max_upload_bytes)
        harvested_info = probe_upload("image", step_bytes, harvested_type)
        if harvested_info.width != step_w or harvested_info.height != step_h:
            _close_attempt(
                factory,
                step_res["attempt_id"],
                status=ATTEMPT_FAILED,
                outcome=OUTCOME_FAILED,
                error_code="IMAGE_STEP_DIMENSIONS_MISMATCH",
                details={
                    "stage": spec.key,
                    "expected": [step_w, step_h],
                    "actual": [harvested_info.width, harvested_info.height],
                },
                release_intent=True,
            )
            touch_item(factory, item_id, version, status=ITEM_FAILED, error_code="IMAGE_STEP_DIMENSIONS_MISMATCH")
            return {
                "item_id": item_id,
                "status": ITEM_FAILED,
                "code": "IMAGE_STEP_DIMENSIONS_MISMATCH",
                "attempt_id": step_res["attempt_id"],
            }

        step_sha = hashlib.sha256(step_bytes).hexdigest()
        record = {
            "step_key": spec.key,
            "angle_key": spec.angle_key,
            "label": spec.label,
            "prompt_id": step_res["prompt_id"],
            "graph_sha256": step_graph_sha,
            "sha256": step_sha,
            "seed": step_seed,
            "width": harvested_info.width,
            "height": harvested_info.height,
            "uses_lora": True,
            "sampling": step_sampling,
        }
        view_bytes[spec.angle_key] = step_bytes
        view_dims[spec.angle_key] = (harvested_info.width, harvested_info.height)
        view_shas[spec.angle_key] = step_sha
        step_records.append(record)
        completed_progress_steps.append(record)

        final_attempt_id = step_res["attempt_id"]
        final_prompt_id = step_res["prompt_id"]
        final_graph_sha = step_graph_sha
        final_harvest = step_res["harvest"]
        is_last = step_index == len(EDIT_STEP_SPECS)
        if not is_last:
            _close_attempt(
                factory,
                step_res["attempt_id"],
                status=ATTEMPT_SUCCEEDED,
                outcome=OUTCOME_SUCCEEDED,
                content_sha256=step_sha,
                harvest=step_res["harvest"],
                details={"stage": spec.key, "step_record": record},
                release_intent=True,
            )

    # Atomically store all 4 full-size single-angle PNGs into the project asset directory
    # so they travel with the single master asset's provenance["scene_sheet"]["views"].
    stored_views: dict[str, dict[str, Any]] = {}
    with factory.begin() as db:
        project = db.get(Project, context["project_id"])
        ensure_storage_writable(db)
        requirement = context["requirement_payload"]
        entity_id = str(requirement.get("entity_id") or context["item_payload"].get("entity_id") or "scene")
        for spec in ALL_STEP_SPECS:
            raw = view_bytes[spec.angle_key]
            sha = view_shas[spec.angle_key]
            vw, vh = view_dims[spec.angle_key]
            rel_path, _stored_name = store_project_asset(
                db,
                settings,
                project,
                raw,
                sha,
                kind="image",
                base=f"{entity_id}_{spec.key}",
                extension=".png",
            )
            stored_views[spec.angle_key] = {
                "key": spec.key,
                "storage_path": rel_path,
                "sha256": sha,
                "width": vw,
                "height": vh,
            }

    composed = compose_scene_sheet(
        front_bytes=view_bytes["front"],
        left_bytes=view_bytes["left"],
        right_bytes=view_bytes["right"],
        reverse_bytes=view_bytes["reverse"],
    )
    composite_bytes = composed["composite_sheet_bytes"]
    composite_sha = hashlib.sha256(composite_bytes).hexdigest()
    composite_provenance = {
        "pipeline": SCENE_SHEET_PIPELINE,
        "scene_sheet": {
            "operation": SCENE_SHEET_OPERATION,
            "artifact_role": "composite_sheet",
            "artifact_label": "16:9 场景四视图母版（主全景 / 左 45° / 右 45° / 180° 反打）",
            "layout_version": SCENE_SHEET_LAYOUT_VERSION,
            "layout": composed["layout"],
            "source_reference_sha256": front_sha,
            "note": scene_note,
            "lora": dict(ss_frozen["style_loras"][0]),
            "views": stored_views,
            "steps": step_records,
        },
    }
    candidate_id, content_sha = _register_candidate(
        factory,
        settings,
        context,
        final_attempt_id,
        composite_bytes,
        family,
        extra_provenance=composite_provenance,
    )
    _close_attempt(
        factory,
        final_attempt_id,
        status=ATTEMPT_QC_PENDING,
        outcome=OUTCOME_SUCCEEDED,
        content_sha256=content_sha,
        keep_intent=True,
        harvest=final_harvest,
        details={
            "stage": EDIT_STEP_SPECS[-1].key,
            "composite_sha256": composite_sha,
            "composite_dimensions": composed["composite_sheet_size"],
            "scene_sheet_views": stored_views,
        },
    )
    next_version = touch_item(factory, item_id, version, status="OUTPUT_STORED")
    version = next_version if next_version is not None else version

    qc = _technical_qc(factory, settings, candidate_id)
    passed = qc["candidate_status"] == "READY_FOR_REVIEW"
    _close_attempt(
        factory,
        final_attempt_id,
        status=ATTEMPT_SUCCEEDED if passed else ATTEMPT_FAILED,
        outcome=OUTCOME_SUCCEEDED if passed else OUTCOME_FAILED,
        error_code=None if passed else "IMAGE_TECHNICAL_QC_FAILED",
        qc=qc["qc"],
        release_intent=True,
    )
    version = _set_scene_sheet_progress(
        factory,
        item_id,
        version,
        {
            "current_step": None,
            "current_step_label": None,
            "completed_steps": list(completed_progress_steps),
            "total_steps": total_steps,
        },
    )
    touch_item(
        factory,
        item_id,
        version,
        status=ITEM_CANDIDATE_READY if passed else ITEM_FAILED,
        error_code=None if passed else "IMAGE_TECHNICAL_QC_FAILED",
    )
    _audit(
        factory,
        context,
        "MATERIAL_IMAGE_CANDIDATE_READY" if passed else "MATERIAL_IMAGE_QC_FAILED",
        {
            "attempt_id": final_attempt_id,
            "candidate_id": candidate_id,
            "content_sha256": content_sha,
            "prompt_id": final_prompt_id,
            "graph_sha256": final_graph_sha,
            "pipeline": SCENE_SHEET_PIPELINE,
            "views": {k: v["sha256"] for k, v in stored_views.items()},
            "technical": qc["qc"].get("technical"),
            "semantic": qc["qc"].get("semantic"),
        },
    )
    return {
        "item_id": item_id,
        "status": ITEM_CANDIDATE_READY if passed else ITEM_FAILED,
        "attempt_id": final_attempt_id,
        "candidate_id": candidate_id,
        "content_sha256": content_sha,
        "prompt_id": final_prompt_id,
        "graph_sha256": final_graph_sha,
        "runtime_fingerprint": fingerprint,
    }


def _open_attempt(factory, context, *, lease_id, graph_sha, prompt, seed, width, height, fingerprint,
                  renderer, extra_payload: dict[str, Any] | None = None) -> str:
    """Persist the submit intent and return its attempt id.

    The unique ``active_intent_key`` makes this the atomic guard against a second
    concurrent submission for the same item: if an unfinished attempt already
    exists, the insert fails and nothing is submitted.
    """
    with factory() as db:
        existing = db.scalars(
            select(MaterialImageAttempt).where(MaterialImageAttempt.item_id == context["item_id"])
        ).all()
        attempt_no = len(existing) + 1
    payload = {
        "prompt_evidence": prompt.as_evidence(),
        "prompt": prompt.prompt,
        "negative_prompt": prompt.negative_prompt,
        **image_style_payload(context["item_payload"]),
        **(extra_payload or {}),
        "graph_sha256": graph_sha,
        "output_node_id": qwen_workflow.OUTPUT_NODE,
        "output_class_type": qwen_workflow.OUTPUT_CLASS_TYPE,
        "model_family": qwen_workflow.MODEL_FAMILY,
        "template_commit": qwen_workflow.TEMPLATE_COMMIT,
        # Whether this installation really owns the renderer that produced the
        # bytes.  A stand-in renderer may prove the chain, but its output is
        # never counted as real verification.
        "renderer_owned": bool((renderer or {}).get("renderer_owned")),
        "renderer_base_url": str((renderer or {}).get("renderer_base_url") or ""),
        "renderer_instance_id": str((renderer or {}).get("renderer_instance_id") or ""),
    }
    with factory.begin() as db:
        attempt = MaterialImageAttempt(
            item_id=context["item_id"],
            run_id=context["run_id"],
            project_id=context["project_id"],
            attempt_no=attempt_no,
            status=ATTEMPT_SUBMIT_INTENT,
            active_intent_key=f"{context['item_id']}:{attempt_no}",
            graph_sha256=graph_sha,
            prompt_template_version=prompt.template_version,
            prompt_sha256=prompt.sha256,
            seed=seed,
            width=width,
            height=height,
            output_node_id=qwen_workflow.OUTPUT_NODE,
            runtime_fingerprint=fingerprint,
            submit_intent_at=utcnow(),
            external_outcome=OUTCOME_NOT_SENT,
            lease_id=lease_id,
            payload=payload,
            sha256=graph_hash(payload),
        )
        db.add(attempt)
        db.flush()
        return attempt.id


def _close_attempt(factory, attempt_id: str, *, status: str, outcome: str | None = None,
                   error_code: str | None = None, prompt_id: str | None = None,
                   content_sha256: str | None = None, details: dict[str, Any] | None = None,
                   qc: dict[str, Any] | None = None, harvest: dict[str, Any] | None = None,
                   release_intent: bool = False, keep_intent: bool = False) -> None:
    with factory.begin() as db:
        attempt = db.get(MaterialImageAttempt, attempt_id)
        if attempt is None:
            return
        attempt.status = status
        if outcome is not None:
            attempt.external_outcome = outcome
        if error_code is not None:
            attempt.error_code = error_code
        if prompt_id is not None:
            attempt.prompt_id = prompt_id
        if content_sha256 is not None:
            attempt.content_sha256 = content_sha256
        payload = dict(attempt.payload or {})
        if details:
            payload["details"] = {**payload.get("details", {}), **details}
        if qc:
            payload["qc"] = qc
        if harvest:
            payload["harvest"] = harvest
        payload.setdefault("history", []).append({"status": status, "outcome": outcome, "at": utcnow().isoformat()})
        attempt.payload = payload
        attempt.sha256 = graph_hash(payload)
        if release_intent and not keep_intent:
            attempt.active_intent_key = None
        if outcome == OUTCOME_UNKNOWN:
            attempt.reconciliation_state = RECONCILE_REQUIRED


def recover_lost_image_items(factory) -> int:
    """Hand a render whose worker died after /prompt but before harvest to the person.

    The attempt was acknowledged by a renderer that may since have exited; the
    bytes were never harvested, so the outcome is unknown.  Record it as unknown
    (never resubmit) and move the item to NEEDS_REVIEW, where the explicit
    regenerate action is the only way to ask again.  A live owner's render is
    never touched.
    """
    recovered = 0
    with factory.begin() as db:
        stale_after = utcnow() - STALE_ITEM_LEASE
        items = db.scalars(select(MaterialItem).where(
            MaterialItem.status == ITEM_RUNNING,
            or_(MaterialItem.heartbeat_at.is_(None), MaterialItem.heartbeat_at < stale_after),
        )).all()
        for item in items:
            owner = db.get(WorkerInstance, item.worker_id) if item.worker_id else None
            if owner is not None and owner.last_heartbeat_at >= stale_after:
                continue
            attempt = db.scalars(
                select(MaterialImageAttempt)
                .where(MaterialImageAttempt.item_id == item.id)
                .order_by(MaterialImageAttempt.attempt_no.desc())
            ).first()
            if attempt is None or attempt.status != ATTEMPT_SUBMITTED or attempt.candidate_id:
                continue
            attempt.status = ATTEMPT_UNKNOWN
            attempt.external_outcome = OUTCOME_UNKNOWN
            attempt.reconciliation_state = "worker_lost_before_harvest"
            item.status = ITEM_NEEDS_REVIEW
            item.error_code = "IMAGE_WORKER_LOST"
            item.state_version += 1
            recovered += 1
            add_audit(db, "comfy-worker", "MATERIAL_IMAGE_WORKER_LOST", "material_item", item.id,
                      {"attempt_id": attempt.id, "prompt_id": attempt.prompt_id,
                       "resubmitted": False}, project_id=item.project_id)
    return recovered


def run_pending_image_items(factory, settings, worker_id: str, *, limit: int = 1,
                            run_id: str | None = None) -> int:
    """Claim and execute image items, one lease at a time.

    Each item acquires and releases the exclusive lease on its own, so a long
    batch cannot starve the H3 queue: after every item the Worker returns to its
    normal dispatch order and a waiting series goes first.  ``run_id`` limits
    the pass to one material run's items and disables the unrelated
    library-task fallback, so a caller waiting on its own samples cannot
    execute someone else's work.
    """
    from shine.asset_center.execution import recover_stale_projectless_tasks
    from shine.materialprep import service
    from shine.materialprep.brief import reconcile_orphan_brief_tasks

    if run_id is None:
        recover_stale_projectless_tasks(factory)
        # A brief task row left RUNNING by a lost worker still counts as a live
        # task for its provider profile, which locks the text model configuration.
        reconcile_orphan_brief_tasks(factory)
        # A render acknowledged by a renderer whose worker then died has no live
        # owner to harvest it; record the unknown outcome instead of queueing forever.
        recover_lost_image_items(factory)

    processed = 0
    for _ in range(max(1, limit)):
        item_id = claim_next_item(factory, worker_id, run_id=run_id)
        if item_id is None:
            break
        try:
            result = execute_item(factory, settings, item_id, worker_id)
        except ImageExecutionError as exc:
            result = {"item_id": item_id, "status": ITEM_FAILED, "code": exc.code}
            with factory() as db:
                item = db.get(MaterialItem, item_id)
                if item is not None and item.status not in ITEM_TERMINAL:
                    item.status = ITEM_FAILED
                    item.error_code = exc.code
                    item.state_version += 1
                    db.commit()
        except Exception as exc:  # noqa: BLE001 - an unexpected fault must not kill the Worker
            result = {"item_id": item_id, "status": ITEM_NEEDS_REVIEW, "code": type(exc).__name__}
            with factory.begin() as db:
                item = db.get(MaterialItem, item_id)
                if item is not None and item.status not in ITEM_TERMINAL:
                    # Not FAILED: an unexpected fault mid-render leaves the external
                    # outcome unproven, so it is reviewed rather than retried.
                    item.status = ITEM_NEEDS_REVIEW
                    item.error_code = type(exc).__name__
                    item.state_version += 1
        processed += 1
        with factory() as db:
            item = db.get(MaterialItem, item_id)
            if item is not None:
                service.refresh_run_status(db, item.run_id)
        if result.get("status") == ITEM_QUEUED:
            # Still waiting for the GPU; do not spin on it this pass.
            break
    if not processed and run_id is None:
        # Projectless image and voice requests share this Worker. Preserve
        # arrival order so a sustained image backlog cannot starve audio.
        # A run-scoped pass (a setup sample step waiting on its own batch)
        # never falls through to unrelated library work.
        from shine.asset_center.models import LibraryRenderTask

        with factory() as db:
            next_task = db.scalar(
                select(LibraryRenderTask)
                .where(
                    LibraryRenderTask.kind.in_({"image", "voice"}),
                    LibraryRenderTask.status == "QUEUED",
                    LibraryRenderTask.external_outcome == "NOT_SENT",
                )
                .order_by(LibraryRenderTask.created_at, LibraryRenderTask.id)
                .limit(1)
            )
            next_kind = next_task.kind if next_task else None
        if next_kind == "image":
            from shine.asset_center.execution import run_one_pending_job

            processed = run_one_pending_job(factory, settings, worker_id)
        elif next_kind == "voice":
            from shine.asset_center.voice_execution import run_one_pending_job as run_one_voice_job

            processed = run_one_voice_job(factory, settings, worker_id)
    return processed


def _audit(factory, context, action: str, detail: dict[str, Any]) -> None:
    try:
        with factory.begin() as db:
            add_audit(db, "worker", action, "material_item", context["item_id"], detail,
                      project_id=context["project_id"])
    except Exception:  # noqa: BLE001 - an audit write must not abort a render
        pass
