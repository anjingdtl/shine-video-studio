"""Add a person's requested material to one shot and let the crew rewrite it.

The material requirement is written first and stands on its own: even with no
text model, no network and no queue, the shot already carries the asset and the
material stage derives it.  The bounded rewrite afterwards is a quality step, not
the source of truth, so a failed or unavailable model never becomes a blocker.

The rewrite is deliberately narrow.  It may only restate this shot's visual
action so the requested asset really appears in it; the duration, the dialogue,
the style and every reference stay exactly as they were.
"""

from __future__ import annotations

import json
import logging
from datetime import timedelta
from typing import Any

from fastapi import HTTPException
from sqlalchemy import or_, select, update

from shine.director.contracts import digest_of
from shine.models import AgentTaskRecord, Project, Shot, utcnow
from shine.schemas import RequestedAsset, ShotSpec
from shine.services import add_audit

logger = logging.getLogger(__name__)

SHOT_REVISION_RUN = "director_shot_revision"
CLAIM_SECONDS = 120
"""How long a claim survives without a renewal.

The lease is renewed while the model call runs, so this only has to outlive a
lost worker.  It matches the storyboard queue's lease so neither queue has its
own recovery rule.
"""
_PROMPT_MAX_CHARS = 2000

SYSTEM_PROMPT = (
    "You are Shine's director, version 1.2. You rewrite exactly one shot so a "
    "material the user explicitly asked for is genuinely visible in it. "
    "Return one JSON object and nothing else, shaped as {\"story_beat\": string, "
    "\"camera\": object}. 'story_beat' is English visual prose: describe what is on "
    "screen, where the requested material sits in the frame, and how the camera "
    "covers it. Keep every existing character action and story fact. Never write "
    "dialogue, narration, voice-over or quoted speech in it; spoken lines live in "
    "the shot's own dialogue field and are not yours to change. Never add a new "
    "character, location, prop or event that the request did not already contain. "
    "The official H3 prompt guide asks for English visual prose, concrete nouns and "
    "no quality slogans; follow it, but never refuse the request over style."
)


class ShotRevisionError(ValueError):
    def __init__(self, code: str, message: str = "", details: dict[str, Any] | None = None):
        super().__init__(message or code)
        self.code = code
        self.details = details or {}


# ---------------------------------------------------------------------------
# User intent: write the requirement, then queue the bounded rewrite
# ---------------------------------------------------------------------------


def add_requested_asset(db, project: Project, shot: Shot, payload: dict[str, Any], *,
                        actor: str = "user") -> dict[str, Any]:
    from shine.services import revise_shot_record

    asset = RequestedAsset.model_validate(payload)
    spec = ShotSpec.model_validate(shot.spec)
    duplicate = next(
        (row for row in spec.requested_assets
         if row.family == asset.family and row.name.strip() == asset.name.strip()),
        None,
    )
    if duplicate is None:
        spec = spec.model_copy(update={"requested_assets": [*spec.requested_assets, asset]})
        revise_shot_record(db, project, shot, spec=spec, actor=actor)
    # The fence is the content this rewrite was planned against, not the
    # revision counter.  Two materials added to one shot bump the revision
    # twice, so a revision fence would make the first task fail forever.
    planned_spec = ShotSpec.model_validate(shot.spec)
    identity = digest_of({
        "tool": SHOT_REVISION_RUN, "shot": shot.id,
        "spec": planned_spec.model_dump(mode="json"),
        "family": asset.family, "name": asset.name.strip(),
    })
    task = db.scalar(select(AgentTaskRecord).where(AgentTaskRecord.request_identity == identity))
    created = False
    if task is None:
        task = AgentTaskRecord(
            project_id=shot.project_id,
            role_id=SHOT_REVISION_RUN,
            request_identity=identity,
            input_hash=identity,
            asset_manifest_hash=identity,
            style_version_id=project.frozen_style_version_id,
            rulepack_id="shot-revision/1.0",
            rulepack_hash=digest_of({"tool": SHOT_REVISION_RUN, "version": "1.0"}),
            model_profile_id="crew-role-routing",
            model_id="existing-film-crew",
            schema_version="1.0",
            status="QUEUED",
            trace={
                "shot_id": shot.id,
                "planned_spec_sha256": digest_of(planned_spec.model_dump(mode="json")),
                "episode_id": None,
                "asset": asset.model_dump(mode="json"),
            },
        )
        db.add(task)
        db.flush()
        created = True
    add_audit(
        db, actor, "SHOT_REQUESTED_ASSET_ADDED", "shot", shot.id,
        {"family": asset.family, "name": asset.name, "task_id": task.id,
         "shot_revision": shot.revision, "task_created": created},
        project_id=shot.project_id,
    )
    db.flush()
    db.refresh(shot)
    return {
        "shot_id": shot.id,
        "shot_revision": shot.revision,
        "task_id": task.id,
        "task_status": task.status,
        "task_created": created,
        "requested_assets": [
            row.model_dump(mode="json") for row in ShotSpec.model_validate(shot.spec).requested_assets
        ],
    }


# ---------------------------------------------------------------------------
# The bounded rewrite (one shot, one call, guidance only)
# ---------------------------------------------------------------------------


def _response_schema() -> dict[str, Any]:
    return {
        "type": "object",
        "properties": {
            "story_beat": {"type": "string", "minLength": 1, "maxLength": _PROMPT_MAX_CHARS},
            "camera": {"type": "object", "additionalProperties": {"type": "string"}},
        },
        "required": ["story_beat"],
        "additionalProperties": False,
    }


def _user_message(spec: ShotSpec, asset: RequestedAsset, shot_name: str) -> str:
    return json.dumps({
        "shot_name": shot_name,
        "duration_target_s": spec.duration_target_s,
        "characters": list(spec.characters),
        "dialogue": [line.model_dump(mode="json") for line in spec.dialogue],
        "current_story_beat": spec.story_beat,
        "current_camera": dict(spec.camera),
        "requested_material": asset.model_dump(mode="json"),
        "request": (
            "Rewrite current_story_beat so this material is unmistakably present in the "
            "shot. Keep the same action, the same people and the same story facts. "
            "Write English visual prose only."
        ),
    }, ensure_ascii=False)


def _claim(factory, worker_id: str) -> dict[str, Any] | None:
    """Claim one queued rewrite, or take over one whose owner died.

    The claim follows the same shape as the story and storyboard queues: a
    ``RUNNING`` task whose lease expired is claimable again, so a crashed worker
    never strands a rewrite forever.
    """

    from sqlalchemy import text

    now = utcnow()
    with factory() as db:
        db.execute(text("BEGIN IMMEDIATE"))
        row = db.scalars(select(AgentTaskRecord).where(
            AgentTaskRecord.role_id == SHOT_REVISION_RUN,
            or_(
                AgentTaskRecord.status == "QUEUED",
                (AgentTaskRecord.status == "RUNNING") & (
                    AgentTaskRecord.lease_expires_at.is_(None)
                    | (AgentTaskRecord.lease_expires_at <= now)
                ),
            ),
        ).order_by(AgentTaskRecord.created_at).limit(1)).first()
        if row is None:
            db.rollback()
            return None
        # Read the version before the UPDATE: the statement sets the column to
        # this value plus one, and the ORM then syncs the in-memory object.  A
        # post-update read would hand the finisher a version one ahead of the
        # row, and the fenced ``UPDATE ... WHERE state_version = version`` would
        # never match, leaving the task RUNNING forever.
        previous_version = row.state_version
        changed = db.execute(update(AgentTaskRecord).where(
            AgentTaskRecord.id == row.id,
            AgentTaskRecord.state_version == previous_version,
        ).values(
            status="RUNNING", lease_owner=worker_id,
            lease_expires_at=now + timedelta(seconds=CLAIM_SECONDS),
            state_version=previous_version + 1,
        )).rowcount
        if not changed:
            db.rollback()
            return None
        payload = {"task_id": row.id, "state_version": previous_version + 1,
                   "trace": dict(row.trace or {})}
        db.commit()
        return payload


def _finish(factory, task_id: str, version: int, *, worker_id: str, status: str,
            error_code: str | None = None, trace_patch: dict[str, Any] | None = None) -> bool:
    """Write the outcome under the fence of the claim that produced it.

    The patch is merged into the trace the claim carried, so the shot and the
    requested material the task was about are still readable afterwards.
    """
    from shine.materialprep.story import _finish_step

    return _finish_step(factory, task_id, version, worker_id, dict(trace_patch or {}),
                        status, error_code, merge_trace=True)


def run_pending_shot_revision(factory, settings, worker_id: str, *, gateway=None) -> bool:
    """Advance one requested-material rewrite.  Returns whether work happened."""
    from shine.materialprep.story import _renew_story_claim

    claim = _claim(factory, worker_id)
    if claim is None:
        return False
    task_id, version, trace = claim["task_id"], claim["state_version"], claim["trace"]
    with _renew_story_claim(factory, task_id, version, worker_id):
        return _advance_shot_revision(
            factory, settings, worker_id, task_id, version, trace, gateway=gateway)


def _advance_shot_revision(factory, settings, worker_id, task_id, version, trace,
                            *, gateway=None) -> bool:
    from shine.llm.base import LLMNotConfigured
    from shine.llm.gateway import LLMGateway
    from shine.llm.secrets import SecretStore
    from shine.services import revise_shot_record

    try:
        with factory() as db:
            shot = db.get(Shot, trace.get("shot_id") or "")
            if shot is None:
                _finish(factory, task_id, version, worker_id=worker_id, status="FAILED", error_code="SHOT_NOT_FOUND")
                return True
            project = db.get(Project, shot.project_id)
            if project is None:
                _finish(factory, task_id, version, worker_id=worker_id, status="FAILED", error_code="PROJECT_NOT_FOUND")
                return True
            spec = ShotSpec.model_validate(shot.spec)
            asset = RequestedAsset.model_validate(trace.get("asset") or {})
            if not any(row.family == asset.family and row.name.strip() == asset.name.strip()
                       for row in spec.requested_assets):
                # The material was taken back off this shot before the rewrite
                # ran: the task is obsolete, not broken.
                _finish(factory, task_id, version, worker_id=worker_id, status="FAILED",
                        error_code="SHOT_REQUESTED_ASSET_REMOVED")
                return True
            shot_name, project_id = shot.name, project.id
            current_revision = shot.revision
            current_spec = spec
    except Exception as exc:  # noqa: BLE001 - one bad trace must not stop the queue
        logger.warning("shot_revision_load_failed error=%s", type(exc).__name__)
        _finish(factory, task_id, version, worker_id=worker_id, status="FAILED", error_code="SHOT_REVISION_LOAD_FAILED")
        return True

    gateway = gateway or LLMGateway(
        SecretStore(settings.secret_store_dir or settings.data_dir / "secrets"))
    try:
        with factory() as db:
            gateway.profile_for_role(db, "director")
            response = gateway.complete_for_role(
                db, "director",
                system=SYSTEM_PROMPT,
                user=_user_message(current_spec, asset, shot_name),
                schema=_response_schema(),
                task_category="creative",
            )
        parsed = response.parsed or {}
        story_beat = str(parsed.get("story_beat") or "").strip()
        if not story_beat:
            raise ShotRevisionError("SHOT_REVISION_EMPTY")
        if len(story_beat) > _PROMPT_MAX_CHARS:
            raise ShotRevisionError("SHOT_REVISION_TOO_LONG")
        import re

        if re.search(r"[\u3400-\u9fff]", story_beat):
            # The compiled H3 prompt is English; a Chinese beat would fail the
            # official rule pack on every later render.
            raise ShotRevisionError("SHOT_REVISION_NOT_ENGLISH")
        if asset.name not in story_beat:
            raise ShotRevisionError(
                "SHOT_REVISION_ASSET_NOT_MENTIONED",
                details={"asset": asset.name},
            )
        camera = parsed.get("camera")
        updates: dict[str, Any] = {"story_beat": story_beat}
        if isinstance(camera, dict) and camera:
            updates["camera"] = {**(current_spec.camera or {}),
                                 **{str(k): str(v) for k, v in camera.items() if str(v).strip()}}
        if asset.family == "character" and asset.name not in current_spec.characters:
            updates["characters"] = [*current_spec.characters, asset.name]
        revised_spec = current_spec.model_copy(update=updates)
        with factory() as db:
            shot = db.get(Shot, trace.get("shot_id") or "")
            project = db.get(Project, project_id)
            if shot is None or project is None:
                _finish(factory, task_id, version, worker_id=worker_id, status="FAILED",
                        error_code="SHOT_REVISION_CHANGED")
                return True
            # The fence is the content the rewrite was planned against.  A
            # second material added while this call ran changes the planned
            # content, so the rewrite is skipped rather than overwriting it.
            planned = digest_of(current_spec.model_dump(mode="json"))
            if digest_of(ShotSpec.model_validate(shot.spec).model_dump(mode="json")) != planned:
                _finish(factory, task_id, version, worker_id=worker_id, status="FAILED",
                        error_code="SHOT_SPEC_CHANGED")
                return True
            result = revise_shot_record(db, project, shot, spec=revised_spec, actor="director")
            add_audit(
                db, "director", "SHOT_REVISION_REWRITTEN", "shot", shot.id,
                {"task_id": task_id, "from_revision": current_revision,
                 "to_revision": result["revision"], "asset": asset.name},
                project_id=project.id,
            )
            db.commit()
    except ShotRevisionError as exc:
        _finish(factory, task_id, version, worker_id=worker_id, status="FAILED", error_code=exc.code,
                trace_patch={"details": exc.details})
        return True
    except LLMNotConfigured:
        # A machine without a text route is a normal state; the material
        # requirement was already written and stays.
        _finish(factory, task_id, version, worker_id=worker_id, status="FAILED",
                error_code="SKIPPED_NO_TEXT_ROUTE")
        return True
    except HTTPException as exc:
        # No routed text model is a normal state on a fresh machine: the
        # requirement already exists, so this is only a skipped quality step.
        code = "SKIPPED_NO_TEXT_ROUTE" if exc.status_code in {404, 409, 422} else "SHOT_REVISION_REFUSED"
        _finish(factory, task_id, version, worker_id=worker_id, status="FAILED", error_code=code)
        return True
    except Exception as exc:  # noqa: BLE001 - the queue continues with other shots
        logger.warning("shot_revision_failed error=%s", type(exc).__name__)
        _finish(factory, task_id, version, worker_id=worker_id, status="FAILED",
                error_code="SHOT_REVISION_FAILED",
                trace_patch={"error_type": type(exc).__name__})
        return True
    _finish(factory, task_id, version, worker_id=worker_id, status="SUCCEEDED",
            trace_patch={"finished_revision": result["revision"]})
    return True


def latest_task(db, shot_id: str) -> AgentTaskRecord | None:
    """The newest rewrite task of one shot, for the workbench to display."""
    rows = db.scalars(select(AgentTaskRecord).where(
        AgentTaskRecord.role_id == SHOT_REVISION_RUN,
    ).order_by(AgentTaskRecord.created_at.desc()).limit(40)).all()
    return next((row for row in rows if (row.trace or {}).get("shot_id") == shot_id), None)


__all__ = [
    "SHOT_REVISION_RUN",
    "add_requested_asset",
    "latest_task",
    "run_pending_shot_revision",
]
