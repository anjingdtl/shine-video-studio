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
    ComfyPromptRejected,
    ComfySubmissionUncertain,
    ComfyUIClient,
    ComfyUnavailable,
    ComfyWorkflowError,
)
from shine.comfy.workflow import apply_parameters, graph_hash, validate_graph
from shine.comfyops import assets as candidate_assets
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


def claim_next_item(factory, worker_id: str) -> str | None:
    """Atomically claim the oldest claimable item.

    Items that already persisted a submit intent are deliberately not claimable:
    an external submission may be in flight, and re-claiming one would be the
    shortest path to rendering the same image twice.  Those go through
    ``reconcile_item`` instead.
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
        row = db.scalar(
            select(MaterialItem)
            .join(MaterialRun, MaterialRun.id == MaterialItem.run_id)
            .where(or_(MaterialItem.status.in_(sorted(ITEM_CLAIMABLE)), stale))
            .order_by(
                case((MaterialRun.payload["kind"].as_string() == "validation", 0), else_=1),
                MaterialItem.created_at, MaterialItem.id,
            )
            .limit(1)
        )
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
    from sqlalchemy import select

    from shine.h3.film_type import film_type_sentence
    from shine.models import StyleVersion
    from shine.schemas import FilmType

    with factory() as db:
        item = db.get(MaterialItem, item_id)
        if item is None:
            raise ImageExecutionError("MATERIAL_ITEM_NOT_FOUND", item_id=item_id)
        run = db.get(MaterialRun, item.run_id)
        requirement = db.get(AssetRequirement, item.requirement_id) if item.requirement_id else None
        plan = db.get(PreProductionPlan, run.plan_id) if run else None
        project = db.get(Project, item.project_id)
        style = db.scalar(
            select(StyleVersion)
            .where(StyleVersion.project_id == item.project_id, StyleVersion.frozen_at.is_not(None))
            .order_by(StyleVersion.version.desc())
        )
        film_style = ""
        if style is not None:
            try:
                film_style = film_type_sentence(FilmType((style.snapshot or {}).get("film_type")))
            except ValueError:
                film_style = ""
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
            "film_style": film_style,
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
    files = runtime.verify_model_files(config)
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
    return {"config": config, "base_url": base_url, "fingerprint": fingerprint, "graph_report": graph_report}


# --- submission ------------------------------------------------------------


def _build_graph(config, prompt: image_prompts.ImagePrompt, *, seed: int, width: int, height: int) -> dict[str, Any]:
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
    }
    final = apply_parameters(graph, qwen_workflow.PARAMETER_MAP, values)
    validate_graph(final, required_nodes=qwen_workflow.REQUIRED_NODES)
    return final


def _renderer_answers(base_url: str) -> bool:
    """Is the renderer still there?  Used to tell "nothing is loaded" from
    "the release could not be confirmed"."""
    try:
        httpx.get(f"{base_url}/system_stats", timeout=3.0, trust_env=False)
    except Exception:  # noqa: BLE001 - an unreachable renderer holds no model
        return False
    return True


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


def _register_candidate(factory, settings, context: dict[str, Any], attempt_id: str, content: bytes, family: str):
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
            width = int(context["item_payload"].get("width") or config.width)
            height = int(context["item_payload"].get("height") or config.height)
            graph = _build_graph(config, prompt, seed=seed, width=width, height=height)
            graph_sha = graph_hash(graph)
            client = ComfyUIClient(resolved["base_url"], max_retries=0)
            clients["client"] = client
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
    width = int(context["item_payload"].get("width") or 0)
    height = int(context["item_payload"].get("height") or 0)

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


def _open_attempt(factory, context, *, lease_id, graph_sha, prompt, seed, width, height, fingerprint,
                  renderer) -> str:
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


def run_pending_image_items(factory, settings, worker_id: str, *, limit: int = 1) -> int:
    """Claim and execute image items, one lease at a time.

    Each item acquires and releases the exclusive lease on its own, so a long
    batch cannot starve the H3 queue: after every item the Worker returns to its
    normal dispatch order and a waiting series goes first.
    """
    from shine.asset_center.execution import recover_stale_projectless_tasks
    from shine.materialprep import service
    from shine.materialprep.brief import reconcile_orphan_brief_tasks

    recover_stale_projectless_tasks(factory)
    # A brief task row left RUNNING by a lost worker still counts as a live
    # task for its provider profile, which locks the text model configuration.
    reconcile_orphan_brief_tasks(factory)
    # A render acknowledged by a renderer whose worker then died has no live
    # owner to harvest it; record the unknown outcome instead of queueing forever.
    recover_lost_image_items(factory)

    processed = 0
    for _ in range(max(1, limit)):
        item_id = claim_next_item(factory, worker_id)
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
    if not processed:
        # Projectless image and voice requests share this Worker. Preserve
        # arrival order so a sustained image backlog cannot starve audio.
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
