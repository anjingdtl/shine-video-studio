"""Domain 2: Project creation, documents, outlines, literary scripts, storyboards, and shot revision."""
from __future__ import annotations

import hashlib
from typing import Any

from fastapi import HTTPException
from sqlalchemy import select

from shine.copilot.params import ParamError, model_payload, routed_kwargs
from shine.models import utcnow
from shine.support.action_handlers import (
    ActionBlocked,
    ActionHandler,
    ActionInvalid,
    blocked_from_executor,
    json_sha256,
    register_handler,
)

# ---------------------------------------------------------------------------
# project_create & project_settings_update
# ---------------------------------------------------------------------------


def _project_create_target_facts(db, settings, params: dict[str, Any], project_id: str | None) -> tuple[dict[str, Any], str | None]:
    return {"title": params.get("title"), "language": params.get("language") or "Chinese"}, None


def _project_create_preflight(db, settings, params: dict[str, Any], common: dict[str, Any]) -> dict[str, Any]:
    return {
        **common,
        "summary": "创建一个空项目；不会导入原文、不会冻结风格、不会启动任何服务。",
        "missing_components": [],
        "download_bytes": 0,
        "target": {"title": params.get("title"), "language": params.get("language") or "Chinese"},
        "will_create_project": True,
        "will_start_gpu": False,
        "will_modify_user_comfyui": False,
        "authorization_summary": "确认后只新增一个空项目；可在项目页继续导入与创作。",
    }


def _project_create_execute(db, settings, session_factory, row, params, declaration, spec, actor, gateway) -> None:
    from shine.services import create_project

    try:
        project = create_project(db, actor=actor, **routed_kwargs(declaration, params, create_project))
    except HTTPException as exc:
        raise blocked_from_executor(exc) from exc
    except ValueError as exc:
        raise ActionBlocked("PROJECT_CREATE_REFUSED", str(exc)[:240]) from exc
    except ParamError as exc:
        raise ActionBlocked("PARAM_NOT_ROUTABLE", str(exc)) from exc
    row.project_id = project.id
    row.original_task_id = project.id
    row.status = "SUCCEEDED"
    row.verification = {
        "outcome": "VERIFIED",
        "executor": spec.executor,
        "authority": "GET /api/v1/projects/{project_id}",
        "created_project_id": project.id,
        "title": project.title,
        "language": project.language,
        "checked_at": utcnow().isoformat(),
    }


def _project_settings_target_facts(db, settings, params: dict[str, Any], project_id: str | None) -> tuple[dict[str, Any], str | None]:
    from shine.models import Project

    pid = project_id or params.get("project_id")
    if not pid:
        return {"requested_language": params.get("language")}, None
    project = db.get(Project, str(pid))
    if project is None:
        return {}, "PROJECT_NOT_FOUND"
    return {
        "project": {
            "id": project.id,
            "language": project.language,
            "frozen_style_version_id": project.frozen_style_version_id,
            "current_document_id": project.current_document_id,
        },
        "requested_language": params.get("language"),
    }, None


def _project_settings_preflight(db, settings, params: dict[str, Any], common: dict[str, Any]) -> dict[str, Any]:
    from shine.models import Project
    from shine.services import canonical_project_language

    project = db.get(Project, str(params.get("project_id") or ""))
    if project is None:
        raise ActionInvalid("目标项目不存在。")
    try:
        language = canonical_project_language(str(params.get("language") or ""))
    except Exception as exc:  # noqa: BLE001
        raise ActionInvalid(str(getattr(exc, "detail", exc))[:240]) from exc
    return {
        **common,
        "summary": "只修改项目的用户级设置（口播语言），不改冻结风格与生产合同。",
        "missing_components": [],
        "download_bytes": 0,
        "parameter_diff": [{"key": "language", "before": project.language, "after": language}],
        "will_start_gpu": False,
        "will_modify_user_comfyui": False,
        "authorization_summary": "确认后立即生效；如需恢复，可以再次预览改回原值。",
    }


def _project_settings_execute(db, settings, session_factory, row, params, declaration, spec, actor, gateway) -> None:
    from shine.models import Project
    from shine.services import update_project_settings

    project = db.get(Project, str(params.get("project_id") or ""))
    if project is None:
        raise ActionBlocked("PROJECT_NOT_FOUND", "目标项目不存在，请重新预览。")
    previous = project.language
    try:
        update_project_settings(db, project, model_payload(declaration, params), actor=actor)
    except HTTPException as exc:
        raise blocked_from_executor(exc) from exc
    except ParamError as exc:
        raise ActionBlocked("PARAM_NOT_ROUTABLE", str(exc)) from exc
    row.status = "SUCCEEDED"
    row.verification = {
        "outcome": "VERIFIED",
        "executor": spec.executor,
        "authority": "GET /api/v1/projects/{project_id}",
        "previous_language": previous,
        "language": project.language,
        "checked_at": utcnow().isoformat(),
    }


# ---------------------------------------------------------------------------
# document_import_text & document_switch_basis
# ---------------------------------------------------------------------------


def _document_import_target_facts(db, settings, params: dict[str, Any], project_id: str | None) -> tuple[dict[str, Any], str | None]:
    from shine.models import Project, ProjectDocument

    pid = str(project_id or params.get("project_id") or "")
    project = db.get(Project, pid)
    if project is None:
        return {}, "PROJECT_NOT_FOUND"
    documents = db.scalars(
        select(ProjectDocument).where(ProjectDocument.project_id == pid).order_by(ProjectDocument.created_at)
    ).all()
    text = str(params.get("text") or "")
    return {
        "text_sha256": hashlib.sha256(text.encode("utf-8")).hexdigest(),
        "project": {"id": project.id, "current_document_id": project.current_document_id, "language": project.language},
        "documents": sorted([{"id": item.id, "sha256": item.sha256} for item in documents], key=lambda item: item["id"]),
    }, None


def _document_import_preflight(db, settings, params: dict[str, Any], common: dict[str, Any]) -> dict[str, Any]:
    from shine.agents.script import parse_document
    from shine.models import Project

    project = db.get(Project, str(params.get("project_id") or ""))
    if project is None:
        raise ActionInvalid("目标项目不存在。")
    text = str(params.get("text") or "")
    filename = str(params.get("filename") or "导入原文.txt")
    try:
        parsed = parse_document(filename, text.encode("utf-8"))
    except Exception as exc:  # noqa: BLE001
        raise ActionInvalid(f"原文无法解析：{str(exc)[:200]}") from exc
    return {
        **common,
        "summary": "按既有解析与去重规则导入这段原文；不会自动切换正式底稿。",
        "missing_components": [],
        "download_bytes": 0,
        "document": {"filename": filename, "source_format": parsed.source_format, "text_length": len(parsed.text)},
        "will_switch_basis": bool(project.current_document_id is None),
        "will_start_gpu": False,
        "will_modify_user_comfyui": False,
        "authorization_summary": "确认后与界面导入同样落库；重复内容会复用已有文档。",
    }


def _document_import_execute(db, settings, session_factory, row, params, declaration, spec, actor, gateway) -> None:
    from shine.agents.script import ScriptParseError
    from shine.models import Project
    from shine.services import import_project_document

    project = db.get(Project, str(params.get("project_id") or ""))
    if project is None:
        raise ActionBlocked("PROJECT_NOT_FOUND", "目标项目不存在，请重新预览。")
    try:
        document, created = import_project_document(
            db, project,
            filename=str(params.get("filename") or "导入原文.txt"),
            content=str(params.get("text") or "").encode("utf-8"),
            content_type="text/plain",
            actor=actor,
        )
    except ScriptParseError as exc:
        raise ActionBlocked("SCRIPT_PARSE_FAILED", f"原文无法解析（{exc.code}）。") from exc
    except HTTPException as exc:
        raise blocked_from_executor(exc) from exc
    row.original_task_id = document.id
    row.status = "SUCCEEDED"
    row.verification = {
        "outcome": "VERIFIED",
        "executor": spec.executor,
        "authority": "GET /api/v1/projects/{project_id}/script-basis",
        "document_id": document.id,
        "sha256": document.sha256,
        "text_length": len(document.text),
        "reused": not created,
        "current_basis": project.current_document_id,
        "checked_at": utcnow().isoformat(),
    }


def _document_switch_target_facts(db, settings, params: dict[str, Any], project_id: str | None) -> tuple[dict[str, Any], str | None]:
    from shine.models import Project, ProjectDocument

    project = db.get(Project, str(params.get("project_id") or project_id or ""))
    document = db.get(ProjectDocument, str(params.get("document_id") or ""))
    if project is None or document is None or document.project_id != project.id:
        return {}, "TARGET_DOCUMENT_MISSING"
    return {
        "current_document_id": project.current_document_id,
        "document_id": document.id,
        "document_sha256": hashlib.sha256(document.text.encode("utf-8")).hexdigest(),
        "document_name": document.filename,
    }, None


def _document_switch_preflight(db, settings, params: dict[str, Any], common: dict[str, Any]) -> dict[str, Any]:
    from shine.models import Project, ProjectDocument

    project = db.get(Project, str(params.get("project_id") or ""))
    document = db.get(ProjectDocument, str(params.get("document_id") or ""))
    if project is None:
        raise ActionInvalid("目标项目不存在。")
    if document is None or document.project_id != project.id:
        raise ActionInvalid("这份文档不属于当前项目。")
    unchanged = project.current_document_id == document.id
    occupancy = common["occupancy"]
    return {
        **common,
        "summary": "把选中的已导入文档切换为正式原文底稿；不会改写或删除其它文档。",
        "document": {"id": document.id, "filename": document.filename, "words": len((document.text or "").strip())},
        "current_basis_id": project.current_document_id,
        "will_start_gpu": False,
        "will_modify_user_comfyui": False,
        "blocked": bool(occupancy["busy"] or unchanged),
        "blocked_code": occupancy["code"] if occupancy["busy"] else ("BASIS_UNCHANGED" if unchanged else None),
        "authorization_summary": "确认后只切换正式底稿；原文内容保持不变。",
    }


def _document_switch_execute(db, settings, session_factory, row, params, declaration, spec, actor, gateway) -> None:
    from shine.models import Project, ProjectDocument
    from shine.services import switch_script_basis

    project = db.get(Project, str(row.project_id or ""))
    document = db.get(ProjectDocument, str(params.get("document_id") or ""))
    if project is None:
        raise ActionBlocked("PROJECT_NOT_FOUND", "目标项目不存在，请重新预览。")
    if document is None or document.project_id != project.id:
        raise ActionBlocked("TARGET_DOCUMENT_MISSING", "这份文档不属于当前项目。")
    changed = switch_script_basis(db, project, document, actor=actor)
    row.status = "SUCCEEDED"
    row.verification = {
        "outcome": "VERIFIED",
        "executor": spec.executor,
        "authority": "GET /api/v1/projects/{project_id}/script-basis",
        "changed": changed,
        "current_basis_id": document.id,
        "checked_at": utcnow().isoformat(),
    }


# ---------------------------------------------------------------------------
# story_outline_generate & story_outline_confirm (NEW)
# ---------------------------------------------------------------------------


def _outline_gen_target_facts(db, settings, params: dict[str, Any], project_id: str | None) -> tuple[dict[str, Any], str | None]:
    from shine.materialprep import story
    from shine.models import AgentRoute, Project, ProjectDocument, ProviderProfile

    pid = str(project_id or params.get("project_id") or "")
    project = db.get(Project, pid)
    if project is None:
        return {}, "PROJECT_NOT_FOUND"
    document = db.get(ProjectDocument, project.current_document_id) if project.current_document_id else None
    if document is None:
        return {}, "SCRIPT_DOCUMENT_MISSING"
    latest = db.scalar(
        select(story.AgentTaskRecord)
        .where(story.AgentTaskRecord.project_id == pid, story.AgentTaskRecord.role_id == story.OUTLINE_RUN)
        .order_by(story.AgentTaskRecord.created_at.desc())
        .limit(1)
    )
    route = db.scalar(select(AgentRoute).where(AgentRoute.role == "scriptwriter"))
    profile = db.get(ProviderProfile, route.provider_profile_id) if route is not None else None
    return {
        "document": {"id": document.id, "sha256": document.sha256, "text_length": len(document.text)},
        "latest_task": {"id": latest.id, "status": latest.status, "input_hash": latest.input_hash} if latest is not None else None,
        "llm_role": {
            "configured": bool(profile is not None and profile.enabled),
            "profile_id": profile.id if profile is not None else None,
            "model": profile.model if profile is not None else None,
        },
        "requested": {
            "document_id": params.get("document_id"),
            "locked_facts": params.get("locked_facts") or [],
            "pacing": params.get("pacing") or "balanced",
        },
    }, None


def _outline_gen_preflight(db, settings, params: dict[str, Any], common: dict[str, Any]) -> dict[str, Any]:
    facts, reason = _outline_gen_target_facts(db, settings, params, str(params.get("project_id") or ""))
    if reason:
        raise ActionInvalid({"SCRIPT_DOCUMENT_MISSING": "项目还没有可用的原文底稿。", "PROJECT_NOT_FOUND": "目标项目不存在。"}.get(reason, reason))
    route = facts.get("llm_role") or {}
    return {
        **common,
        "summary": "用一个持久大纲任务把原文整理成大纲；相同底稿与参数不会重复调用模型。",
        "missing_components": [],
        "download_bytes": 0,
        "document": facts.get("document"),
        "latest_task": facts.get("latest_task"),
        "llm_role": route,
        "blocked_by_role": not route.get("configured"),
        "blocked_code": None if route.get("configured") else "TEXT_ROLE_NOT_READY",
        "will_start_gpu": False,
        "will_modify_user_comfyui": False,
        "authorization_summary": "确认后只排队大纲任务；大纲确认仍由你明确决定。",
    }


def _outline_gen_execute(db, settings, session_factory, row, params, declaration, spec, actor, gateway) -> None:
    from shine.llm.base import LLMError
    from shine.materialprep import story
    from shine.models import Project, ProjectDocument
    from shine.services import add_audit

    if gateway is None:
        raise ActionBlocked("TEXT_ROLE_NOT_READY", "生成大纲需要「故事大纲」这一个文本角色已配置；其它角色不影响当前操作。")
    project = db.get(Project, str(row.project_id or ""))
    if project is None:
        raise ActionBlocked("PROJECT_NOT_FOUND", "目标项目不存在，请重新预览。")
    document_id = str(params.get("document_id") or project.current_document_id or "")
    document = db.get(ProjectDocument, document_id)
    if document is None or document.project_id != project.id:
        raise ActionBlocked("SCRIPT_DOCUMENT_MISSING", "项目还没有可用的原文底稿，请先导入原文。")
    if not document.text.strip():
        raise ActionBlocked("STORY_DOCUMENT_EMPTY", "原文底稿是空的，无法整理大纲。")
    try:
        task = story.queue_outline(
            db, project.id, document, gateway,
            list(params.get("locked_facts") or []),
            params.get("pacing") or "balanced",
            **routed_kwargs(declaration, params, story.queue_outline, handled={"document_id", "locked_facts", "pacing"}),
        )
    except LLMError as exc:
        raise ActionBlocked("TEXT_ROLE_NOT_READY", f"生成大纲需要「故事大纲」角色就绪（{getattr(exc, 'code', type(exc).__name__)}）。") from exc
    except HTTPException as exc:
        raise blocked_from_executor(exc) from exc
    except ParamError as exc:
        raise ActionBlocked("PARAM_NOT_ROUTABLE", str(exc)) from exc
    row.original_task_id = task.id
    row.status = "DELEGATED"
    row.verification = {
        "outcome": "PENDING",
        "executor": spec.executor,
        "task_id": task.id,
        "task_status": task.status,
        "document_id": document.id,
        "document_sha256": document.sha256,
        "bound_at": utcnow().isoformat(),
        "checked_at": utcnow().isoformat(),
    }
    add_audit(db, actor, "SUPPORT_ACTION_TASK_BOUND", "support_action", row.id,
              {"kind": row.kind, "task_id": task.id, "role": story.OUTLINE_RUN}, project_id=row.project_id)


def _outline_gen_readback(db, settings, row) -> None:
    if row.status != "DELEGATED":
        return
    from shine.models import AgentTaskRecord

    task = db.get(AgentTaskRecord, str(row.original_task_id or ""))
    if task is None:
        row.status = "NEEDS_REVIEW"
        row.last_error = "找不到这次大纲执行任务的记录，无法确认结果；请重新预览。"
        row.verification = {**(row.verification or {}), "outcome": "UNKNOWN", "attributed": False, "checked_at": utcnow().isoformat()}
    else:
        status = str(task.status or "UNKNOWN")
        row.verification = {
            **(row.verification or {}),
            "task_id": task.id,
            "task_status": status,
            "stage": (task.trace or {}).get("stage"),
            "cursor": (task.trace or {}).get("cursor"),
            "checked_at": utcnow().isoformat(),
        }
        if status == "SUCCEEDED":
            row.status = "SUCCEEDED"
            row.verification["outcome"] = "VERIFIED"
            row.verification["note"] = "大纲已生成；可继续确认大纲并推进分镜书。"
        elif status in {"FAILED", "CANCELLED", "SUPERSEDED"}:
            row.status = "FAILED" if status != "SUPERSEDED" else "SUPERSEDED"
            row.last_error = f"原大纲任务状态 {status}。"
            row.verification["outcome"] = "FAILED"
        elif status == "NEEDS_REVIEW":
            row.status = "NEEDS_REVIEW"
            row.last_error = "大纲任务结果不确定，需要先核对，不能自动重试。"
            row.verification["outcome"] = "UNKNOWN"
    db.commit()


def _outline_confirm_target_facts(db, settings, params: dict[str, Any], project_id: str | None) -> tuple[dict[str, Any], str | None]:
    from shine.models import AgentArtifact

    pid = str(project_id or params.get("project_id") or "")
    artifact = db.get(AgentArtifact, str(params.get("artifact_id") or ""))
    if artifact is None or artifact.project_id != pid or artifact.kind != "StoryOutline":
        return {}, "STORY_OUTLINE_NOT_FOUND"
    return {
        "artifact_id": artifact.id,
        "sha256": artifact.sha256,
        "approved_at": artifact.approved_at.isoformat() if artifact.approved_at else None,
        "expected_sha256": params.get("expected_sha256") or artifact.sha256,
    }, None


def _outline_confirm_preflight(db, settings, params: dict[str, Any], common: dict[str, Any]) -> dict[str, Any]:
    from shine.models import AgentArtifact

    pid = str(params.get("project_id") or "")
    artifact = db.get(AgentArtifact, str(params.get("artifact_id") or ""))
    if artifact is None or artifact.project_id != pid or artifact.kind != "StoryOutline":
        raise ActionInvalid("找不到对应的大纲产物。")
    return {
        **common,
        "summary": "确认并锁定当前故事大纲，作为后续分镜书与文学剧本的权威依据。",
        "artifact_id": artifact.id,
        "sha256": params.get("expected_sha256") or artifact.sha256,
        "will_start_gpu": False,
        "will_modify_user_comfyui": False,
        "authorization_summary": "确认后触发原 confirm_outline_director 执行者；确认结果可追溯审计。",
    }


def _outline_confirm_execute(db, settings, session_factory, row, params, declaration, spec, actor, gateway) -> None:
    from shine.director.streamline import confirm_outline_director
    from shine.models import AgentArtifact

    pid = str(row.project_id or "")
    aid = str(params.get("artifact_id") or "")
    artifact = db.get(AgentArtifact, aid)
    if artifact is None or artifact.project_id != pid:
        raise ActionBlocked("STORY_OUTLINE_NOT_FOUND", "大纲产物已不存在。")
    expected = str(params.get("expected_sha256") or artifact.sha256)
    try:
        res = confirm_outline_director(db, pid, aid, expected, actor=actor)
    except HTTPException as exc:
        raise blocked_from_executor(exc) from exc
    row.original_task_id = aid
    row.status = "SUCCEEDED"
    row.verification = {
        "outcome": "VERIFIED",
        "executor": spec.executor,
        "authority": "GET /api/v1/projects/{project_id}/director/outline",
        "artifact_id": aid,
        "result": res,
        "checked_at": utcnow().isoformat(),
    }


# ---------------------------------------------------------------------------
# storyboard_book_confirm (NEW)
# ---------------------------------------------------------------------------


def _book_confirm_target_facts(db, settings, params: dict[str, Any], project_id: str | None) -> tuple[dict[str, Any], str | None]:
    from shine.director.models import DirectorEpisodePlan

    pid = str(project_id or params.get("project_id") or "")
    plan = db.scalar(
        select(DirectorEpisodePlan)
        .where(DirectorEpisodePlan.project_id == pid, DirectorEpisodePlan.version == int(params.get("plan_version") or 0))
    )
    if plan is None:
        return {}, "STORYBOARD_BOOK_NOT_FOUND"
    return {
        "plan_version": plan.version,
        "sha256": plan.sha256,
        "status": plan.status,
        "expected_sha256": params.get("expected_sha256") or plan.sha256,
    }, None


def _book_confirm_preflight(db, settings, params: dict[str, Any], common: dict[str, Any]) -> dict[str, Any]:
    from shine.director.models import DirectorEpisodePlan

    pid = str(params.get("project_id") or "")
    plan = db.scalar(
        select(DirectorEpisodePlan)
        .where(DirectorEpisodePlan.project_id == pid, DirectorEpisodePlan.version == int(params.get("plan_version") or 0))
    )
    if plan is None:
        raise ActionInvalid("找不到指定版本的分镜书。")
    return {
        **common,
        "summary": "确认并锁定当前分镜书与风格设置，供后续分集与拍片使用。",
        "plan_version": plan.version,
        "plan_status": plan.status,
        "acknowledge_deviation": bool(params.get("acknowledge_deviation")),
        "will_start_gpu": False,
        "will_modify_user_comfyui": False,
        "authorization_summary": "调用既有 confirm_storyboard_book；风格与时长偏差均遵守原门禁。",
    }


def _book_confirm_execute(db, settings, session_factory, row, params, declaration, spec, actor, gateway) -> None:
    from shine.director.models import DirectorEpisodePlan
    from shine.director.streamline import confirm_storyboard_book

    pid = str(row.project_id or "")
    version = int(params.get("plan_version") or 0)
    plan = db.scalar(
        select(DirectorEpisodePlan).where(DirectorEpisodePlan.project_id == pid, DirectorEpisodePlan.version == version)
    )
    if plan is None:
        raise ActionBlocked("STORYBOARD_BOOK_NOT_FOUND", "分镜书版本已不存在。")
    expected = str(params.get("expected_sha256") or plan.sha256)
    try:
        res = confirm_storyboard_book(
            db, pid,
            version=version,
            expected_sha256=expected,
            style=params.get("style"),
            actor=actor,
            acknowledge_deviation=bool(params.get("acknowledge_deviation")),
        )
    except HTTPException as exc:
        raise blocked_from_executor(exc) from exc
    row.original_task_id = str(res.get("plan_id") or plan.id)
    row.status = "SUCCEEDED"
    row.verification = {
        "outcome": "VERIFIED",
        "executor": spec.executor,
        "authority": "GET /api/v1/projects/{project_id}/director/storyboard-book",
        "plan_version": version,
        "result": res,
        "checked_at": utcnow().isoformat(),
    }


# ---------------------------------------------------------------------------
# literary_scripts_generate
# ---------------------------------------------------------------------------


def _literary_target_facts(db, settings, params: dict[str, Any], project_id: str | None) -> tuple[dict[str, Any], str | None]:
    from shine.director import pipeline
    from shine.models import Project

    pid = str(params.get("project_id") or project_id or "")
    project = db.get(Project, pid)
    if project is None:
        return {}, "PROJECT_NOT_FOUND"
    plan = pipeline.current_volume_plan(db, project.id)
    if plan is None:
        return {}, "PIPELINE_VOLUME_PLAN_NOT_CONFIRMED"
    volume = next(
        (item for item in (plan.payload_json or {}).get("volumes") or [] if item.get("volume_id") == params.get("volume_id")),
        None,
    )
    if volume is None:
        return {}, "PIPELINE_VOLUME_NOT_IN_PLAN"
    return {
        "plan_sha256": plan.sha256,
        "volume_id": volume.get("volume_id"),
        "section_count": len(volume.get("sections") or []),
    }, None


def _literary_preflight(db, settings, params: dict[str, Any], common: dict[str, Any]) -> dict[str, Any]:
    from shine.director import pipeline
    from shine.models import Project

    project = db.get(Project, str(params.get("project_id") or ""))
    if project is None:
        raise ActionInvalid("目标项目不存在。")
    plan = pipeline.current_volume_plan(db, project.id)
    if plan is None:
        return {
            **common,
            "summary": "还没有已确认的分集方案，无法生成分集剧本。",
            "blocked": True,
            "blocked_code": "PIPELINE_VOLUME_PLAN_NOT_CONFIRMED",
            "will_start_gpu": False,
            "will_modify_user_comfyui": False,
            "authorization_summary": "先生成并确认分集方案，再回来生成剧本。",
        }
    volume = next(
        (item for item in (plan.payload_json or {}).get("volumes") or [] if item.get("volume_id") == params.get("volume_id")),
        None,
    )
    if volume is None:
        raise ActionInvalid("这一卷不在当前分集方案里。")
    return {
        **common,
        "summary": "为这一卷里还没有确认剧本的分节排队文学剧本任务；不会覆盖已确认剧本。",
        "volume": {"volume_id": volume.get("volume_id"), "title": volume.get("title"), "section_count": len(volume.get("sections") or [])},
        "will_start_gpu": False,
        "will_modify_user_comfyui": False,
        "paid": True,
        "authorization_summary": "确认后只排队缺失的分节剧本任务，由既有流水线执行者生成。",
    }


def _literary_execute(db, settings, session_factory, row, params, declaration, spec, actor, gateway) -> None:
    from shine.director import pipeline

    if gateway is None:
        raise ActionBlocked("TEXT_ROLE_NOT_READY", "生成分集剧本需要「分集剧本」文本角色已配置。")
    try:
        tasks = pipeline.queue_scene_scripts(db, str(row.project_id or ""), str(params.get("volume_id") or ""), gateway)
    except ValueError as exc:
        raise ActionBlocked(str(exc), f"原流水线拒绝：{exc}") from exc
    db.commit()
    task_ids = [task.id for task in tasks]
    row.original_task_id = task_ids[0] if task_ids else None
    row.status = "DELEGATED" if task_ids else "SUCCEEDED"
    row.verification = {
        "outcome": "PENDING" if task_ids else "VERIFIED",
        "executor": spec.executor,
        "authority": "GET /api/v1/projects/{project_id}/pipeline/volumes/{volume_id}",
        "task_ids": task_ids,
        "queued": len(task_ids),
        "checked_at": utcnow().isoformat(),
    }


def _literary_readback(db, settings, row) -> None:
    if row.status != "DELEGATED":
        return
    from shine.models import AgentTaskRecord

    task_ids = [str(item) for item in (row.verification or {}).get("task_ids") or []]
    tasks = list(db.scalars(select(AgentTaskRecord).where(AgentTaskRecord.id.in_(task_ids))).all()) if task_ids else []
    statuses = [str(task.status) for task in tasks]
    row.verification = {**(row.verification or {}), "task_ids": task_ids, "task_statuses": statuses, "checked_at": utcnow().isoformat()}
    if not task_ids or len(tasks) != len(task_ids):
        row.status = "NEEDS_REVIEW"
        row.last_error = "分集剧本任务记录缺失，无法确认本次授权的结果，不能自动重试。"
        row.verification["outcome"] = "UNKNOWN"
    elif all(status == "SUCCEEDED" for status in statuses):
        row.status = "SUCCEEDED"
        row.verification["outcome"] = "VERIFIED"
        row.verification["note"] = "分集剧本已由原流水线生成；确认剧本仍是用户在导演页的裁决。"
    elif any(status in {"FAILED", "CANCELLED", "SUPERSEDED"} for status in statuses):
        row.status = "FAILED"
        row.last_error = "至少一个分集剧本任务失败或被取消。"
        row.verification["outcome"] = "FAILED"
    elif any(status == "NEEDS_REVIEW" for status in statuses):
        row.status = "NEEDS_REVIEW"
        row.last_error = "分集剧本任务出现不确定结果，需要先核对，不能自动重提。"
        row.verification["outcome"] = "UNKNOWN"
    else:
        row.verification["outcome"] = "PENDING"
    db.commit()


# ---------------------------------------------------------------------------
# storyboard_generate, storyboard_confirm (NEW), shot_revise (NEW)
# ---------------------------------------------------------------------------


def _storyboard_gen_target_facts(db, settings, params: dict[str, Any], project_id: str | None) -> tuple[dict[str, Any], str | None]:
    from shine.director.models import DirectorEpisodePlan
    from shine.models import AgentTaskRecord, Project, WorkflowProfile

    pid = str(project_id or params.get("project_id") or "")
    project = db.get(Project, pid)
    if project is None:
        return {}, "PROJECT_NOT_FOUND"
    query = select(DirectorEpisodePlan).where(DirectorEpisodePlan.project_id == pid)
    if params.get("plan_version") is not None:
        query = query.where(DirectorEpisodePlan.version == int(params["plan_version"]))
    plan = db.scalar(query.order_by(DirectorEpisodePlan.version.desc()).limit(1))
    latest = db.scalar(
        select(AgentTaskRecord)
        .where(AgentTaskRecord.project_id == pid, AgentTaskRecord.role_id == "DIRECTOR_STORYBOARD_RUN")
        .order_by(AgentTaskRecord.created_at.desc())
        .limit(1)
    )
    return {
        "project": {"id": project.id, "frozen_style_version_id": project.frozen_style_version_id},
        "plan": (
            {"id": plan.id, "version": plan.version, "status": plan.status, "payload_sha256": json_sha256(plan.payload or {})}
            if plan is not None
            else None
        ),
        "latest_storyboard_task": {"id": latest.id, "status": latest.status} if latest is not None else None,
        "workflow_profiles": sorted(
            ({"id": item.id, "name": item.name, "graph_sha256": item.graph_sha256}
             for item in db.scalars(select(WorkflowProfile).where(WorkflowProfile.project_id == pid)).all()),
            key=lambda item: item["id"],
        ),
    }, None


def _storyboard_gen_preflight(db, settings, params: dict[str, Any], common: dict[str, Any]) -> dict[str, Any]:
    facts, reason = _storyboard_gen_target_facts(db, settings, params, str(params.get("project_id") or ""))
    if reason:
        raise ActionInvalid("目标项目不存在。" if reason == "PROJECT_NOT_FOUND" else reason)
    missing: list[str] = []
    if not (facts.get("project") or {}).get("frozen_style_version_id"):
        missing.append("STYLE_NOT_FROZEN")
    if not facts.get("plan"):
        missing.append("EPISODE_PLAN_MISSING")
    elif (facts.get("plan") or {}).get("status") != "CONFIRMED":
        missing.append("EPISODE_PLAN_NOT_CONFIRMED")
    occupancy = common["occupancy"]
    return {
        **common,
        "summary": "按已确认的分集方案排队一个持久分镜任务；缺风格/方案时不会开始。",
        "missing_components": [],
        "download_bytes": 0,
        "plan": facts.get("plan"),
        "latest_task": facts.get("latest_storyboard_task"),
        "blocked": bool(occupancy["busy"] or missing),
        "blocked_code": occupancy["code"] if occupancy["busy"] else (missing[0] if missing else None),
        "blockers": missing,
        "will_start_gpu": False,
        "will_modify_user_comfyui": False,
        "authorization_summary": "确认后只排队分镜任务；不会替你确认镜头或冻结风格。",
    }


def _storyboard_gen_execute(db, settings, session_factory, row, params, declaration, spec, actor, gateway) -> None:
    from shine.director.streamline import queue_director_storyboard
    from shine.services import add_audit

    try:
        value = queue_director_storyboard(
            db, str(row.project_id or ""), actor=actor,
            **routed_kwargs(declaration, params, queue_director_storyboard),
        )
    except HTTPException as exc:
        raise blocked_from_executor(exc) from exc
    except ParamError as exc:
        raise ActionBlocked("PARAM_NOT_ROUTABLE", str(exc)) from exc
    task_id = str(value.get("task_id") or "") or None
    row.original_task_id = task_id
    row.status = "DELEGATED"
    row.verification = {
        "outcome": "PENDING",
        "executor": spec.executor,
        "task_id": task_id,
        "task_status": value.get("status"),
        "episode_count": len(value.get("episodes") or []),
        "bound_at": utcnow().isoformat(),
        "checked_at": utcnow().isoformat(),
    }
    if task_id:
        add_audit(db, actor, "SUPPORT_ACTION_TASK_BOUND", "support_action", row.id, {"kind": row.kind, "task_id": task_id}, project_id=row.project_id)


def _storyboard_gen_readback(db, settings, row) -> None:
    if row.status != "DELEGATED":
        return
    from shine.director.streamline import director_storyboard_state

    try:
        state = director_storyboard_state(db, str(row.project_id or ""))
    except Exception as exc:  # noqa: BLE001
        state = {"task_id": None, "status": None, "error": type(exc).__name__}
    if not row.original_task_id or str(state.get("task_id") or "") != str(row.original_task_id):
        row.status = "NEEDS_REVIEW"
        row.last_error = "分镜记录已被新的任务取代或无法归属，本次授权不能借用它的结果。"
        row.verification = {**(row.verification or {}), "outcome": "UNKNOWN", "attributed": False, "task_id": row.original_task_id, "current_task_id": state.get("task_id"), "checked_at": utcnow().isoformat()}
    else:
        status = str(state.get("status") or "UNKNOWN")
        episodes = state.get("episodes") or []
        blocked = [row_ep for row_ep in episodes if row_ep.get("status") == "BLOCKED"]
        row.verification = {
            **(row.verification or {}),
            "task_id": state.get("task_id"),
            "task_status": status,
            "episode_count": len(episodes),
            "blocked_episodes": [{"episode_id": item.get("episode_id"), "blocker": item.get("blocker")} for item in blocked[:5]],
            "checked_at": utcnow().isoformat(),
        }
        if status in {"SUCCEEDED", "COMPLETED"} and not blocked:
            row.status = "SUCCEEDED"
            row.verification["outcome"] = "VERIFIED"
        elif status in {"FAILED", "CANCELLED", "SUPERSEDED"}:
            row.status = "FAILED" if status != "SUPERSEDED" else "SUPERSEDED"
            row.last_error = f"原分镜任务状态 {status}。"
            row.verification["outcome"] = "FAILED"
        elif status in {"NEEDS_REVIEW", "UNKNOWN"} or blocked:
            row.status = "NEEDS_REVIEW"
            row.last_error = "分镜任务有未完成或结果不确定的集，需要先核对。"
            row.verification["outcome"] = "UNKNOWN"
        else:
            row.verification["outcome"] = "PENDING"
    db.commit()


def _storyboard_confirm_target_facts(db, settings, params: dict[str, Any], project_id: str | None) -> tuple[dict[str, Any], str | None]:
    from shine.director.streamline import episode_shot_table

    pid = str(project_id or params.get("project_id") or "")
    ep_id = str(params.get("episode_id") or "")
    if not pid or not ep_id:
        return {}, "EPISODE_ID_REQUIRED"
    table = episode_shot_table(db, pid, ep_id)
    shots = table.get("shots") or []
    return {
        "episode_id": ep_id,
        "shot_revisions": [(item.get("id"), item.get("revision")) for item in shots if isinstance(item, dict)],
    }, None


def _storyboard_confirm_preflight(db, settings, params: dict[str, Any], common: dict[str, Any]) -> dict[str, Any]:
    return {
        **common,
        "summary": f"确认第「{params.get('episode_id')}」集分镜并生成权威生产依据。",
        "episode_id": params.get("episode_id"),
        "acknowledge_duration_deviation": bool(params.get("acknowledge_duration_deviation")),
        "will_start_gpu": False,
        "will_modify_user_comfyui": False,
        "authorization_summary": "调用既有 confirm_episode_shots，未过时长/台词核验的集将被拒绝。",
    }


def _storyboard_confirm_execute(db, settings, session_factory, row, params, declaration, spec, actor, gateway) -> None:
    from shine.director.streamline import confirm_episode_shots

    pid = str(row.project_id or "")
    ep_id = str(params.get("episode_id") or "")
    try:
        res = confirm_episode_shots(
            db, pid, ep_id,
            actor=actor,
            acknowledge_duration_deviation=bool(params.get("acknowledge_duration_deviation")),
        )
    except HTTPException as exc:
        raise blocked_from_executor(exc) from exc
    row.original_task_id = str(res.get("plan_sha256") or ep_id)
    row.status = "SUCCEEDED"
    row.verification = {
        "outcome": "VERIFIED",
        "executor": spec.executor,
        "authority": "GET /api/v1/projects/{project_id}/director/storyboard/state",
        "result": res,
        "checked_at": utcnow().isoformat(),
    }


def _shot_revise_target_facts(db, settings, params: dict[str, Any], project_id: str | None) -> tuple[dict[str, Any], str | None]:
    from shine.models import Shot

    pid = str(project_id or params.get("project_id") or "")
    shot = db.get(Shot, str(params.get("shot_id") or ""))
    if shot is None or shot.project_id != pid:
        return {}, "SHOT_NOT_FOUND"
    return {
        "shot_id": shot.id,
        "revision": shot.revision,
        "expected_revision": params.get("expected_revision") or shot.revision,
        "duration_target_s": params.get("duration_target_s"),
        "continuity_mode": params.get("continuity_mode"),
    }, None


def _shot_revise_preflight(db, settings, params: dict[str, Any], common: dict[str, Any]) -> dict[str, Any]:
    from shine.models import Shot

    shot = db.get(Shot, str(params.get("shot_id") or ""))
    if shot is None:
        raise ActionInvalid("找不到指定的镜头。")
    return {
        **common,
        "summary": f"修订单镜「{shot.name or shot.id}」（当前版本 r{shot.revision}），递增镜头修订号。",
        "shot_id": shot.id,
        "current_revision": shot.revision,
        "changes": {
            k: params[k]
            for k in ("duration_target_s", "continuity_mode", "story_beat")
            if params.get(k) is not None
        },
        "will_start_gpu": False,
        "will_modify_user_comfyui": False,
        "authorization_summary": "经过 check_continuation_opening 接拍连续性校验与台词时长检验；旧已过镜历史版本不会被覆盖。",
    }


def _shot_revise_execute(db, settings, session_factory, row, params, declaration, spec, actor, gateway) -> None:
    from shine.director.streamline import revise_director_shot
    from shine.models import Shot

    pid = str(row.project_id or "")
    shot_id = str(params.get("shot_id") or "")
    shot = db.get(Shot, shot_id)
    if shot is None or shot.project_id != pid:
        raise ActionBlocked("SHOT_NOT_FOUND", "镜头不存在。")
    expected = params.get("expected_revision")
    try:
        res = revise_director_shot(
            db, pid, shot_id,
            expected_revision=int(expected) if expected is not None else shot.revision,
            duration_target_s=params.get("duration_target_s"),
            continuity_mode=params.get("continuity_mode"),
            story_beat=params.get("story_beat"),
            actor=actor,
        )
    except HTTPException as exc:
        raise blocked_from_executor(exc) from exc
    row.original_task_id = shot_id
    row.status = "SUCCEEDED"
    row.verification = {
        "outcome": "VERIFIED",
        "executor": spec.executor,
        "authority": "GET /api/v1/projects/{project_id}/shots",
        "shot_id": shot_id,
        "new_revision": res.get("revision"),
        "changed": res.get("changed"),
        "invalidated_job_ids": res.get("invalidated_job_ids") or [],
        "checked_at": utcnow().isoformat(),
    }


register_handler("project_create", ActionHandler(target_facts=_project_create_target_facts, preflight=_project_create_preflight, execute=_project_create_execute))
register_handler("project_settings_update", ActionHandler(target_facts=_project_settings_target_facts, preflight=_project_settings_preflight, execute=_project_settings_execute))
register_handler("document_import_text", ActionHandler(target_facts=_document_import_target_facts, preflight=_document_import_preflight, execute=_document_import_execute))
register_handler("document_switch_basis", ActionHandler(target_facts=_document_switch_target_facts, preflight=_document_switch_preflight, execute=_document_switch_execute))
register_handler("story_outline_generate", ActionHandler(target_facts=_outline_gen_target_facts, preflight=_outline_gen_preflight, execute=_outline_gen_execute, readback=_outline_gen_readback))
register_handler("story_outline_confirm", ActionHandler(target_facts=_outline_confirm_target_facts, preflight=_outline_confirm_preflight, execute=_outline_confirm_execute))
register_handler("storyboard_book_confirm", ActionHandler(target_facts=_book_confirm_target_facts, preflight=_book_confirm_preflight, execute=_book_confirm_execute))
register_handler("literary_scripts_generate", ActionHandler(target_facts=_literary_target_facts, preflight=_literary_preflight, execute=_literary_execute, readback=_literary_readback))
register_handler("storyboard_generate", ActionHandler(target_facts=_storyboard_gen_target_facts, preflight=_storyboard_gen_preflight, execute=_storyboard_gen_execute, readback=_storyboard_gen_readback))
register_handler("storyboard_confirm", ActionHandler(target_facts=_storyboard_confirm_target_facts, preflight=_storyboard_confirm_preflight, execute=_storyboard_confirm_execute))
register_handler("shot_revise", ActionHandler(target_facts=_shot_revise_target_facts, preflight=_shot_revise_preflight, execute=_shot_revise_execute))
