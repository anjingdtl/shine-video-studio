"""Manual4 Stage 1 HTTP endpoints (storyboard import, draft edit, confirm/freeze)."""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, File, Form, HTTPException, Request, UploadFile
from pydantic import BaseModel, ConfigDict, Field

from shine.manual4.api.deps import (
    M4DB,
    M4Operator,
    PlatformDB,
)
from shine.manual4.api.deps import (
    require_m4_project as _project,
)
from shine.manual4.comfy.client import is_loopback_url
from shine.manual4.storage.models import m4_utcnow
from shine.manual4.storyboard.service import (
    confirm_storyboard_plan,
    get_storyboard_state,
    import_storyboard_source,
    save_storyboard_draft,
)
from shine.platform_projects import (
    list_platform_llm_profiles,
    resolve_platform_llm_connection,
)

storyboard_router = APIRouter()


class M4ProjectSettingsBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    max_shot_seconds: float = Field(ge=4.0, le=15.0)


class M4DraftSaveBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    expected_revision: int | None = None
    payload: dict[str, Any]


class M4ConfirmStoryboardBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    expected_revision: int | None = None
    acknowledge_duration_mismatch: bool = False


@storyboard_router.get("/projects/{project_id}/llm-options")
def m4_llm_options(project_id: str, request: Request, platform_db: PlatformDB, m4_db: M4DB) -> list[dict[str, Any]]:
    _project(project_id, platform_db, m4_db)
    return list_platform_llm_profiles(platform_db, request.app.state.settings)


@storyboard_router.put("/projects/{project_id}/settings")
def m4_update_settings(
    project_id: str,
    body: M4ProjectSettingsBody,
    actor: M4Operator,
    platform_db: PlatformDB,
    m4_db: M4DB,
) -> dict[str, Any]:
    _ = actor
    project = _project(project_id, platform_db, m4_db)
    project.max_shot_seconds = float(body.max_shot_seconds)
    project.updated_at = m4_utcnow()
    m4_db.commit()
    return {"project_id": project.id, "max_shot_seconds": project.max_shot_seconds}


@storyboard_router.get("/projects/{project_id}/storyboard/draft")
def m4_get_storyboard_draft(
    project_id: str,
    platform_db: PlatformDB,
    m4_db: M4DB,
) -> dict[str, Any]:
    project = _project(project_id, platform_db, m4_db)
    return get_storyboard_state(m4_db, project)


@storyboard_router.put("/projects/{project_id}/storyboard/draft")
def m4_put_storyboard_draft(
    project_id: str,
    body: M4DraftSaveBody,
    actor: M4Operator,
    platform_db: PlatformDB,
    m4_db: M4DB,
) -> dict[str, Any]:
    project = _project(project_id, platform_db, m4_db)
    saved = save_storyboard_draft(
        m4_db,
        project,
        actor=actor,
        payload=body.payload,
        expected_revision=body.expected_revision,
    )
    m4_db.commit()
    return {"draft": saved}


@storyboard_router.post("/projects/{project_id}/storyboard/import", status_code=201)
async def m4_import_storyboard(
    project_id: str,
    request: Request,
    actor: M4Operator,
    platform_db: PlatformDB,
    m4_db: M4DB,
    file: Annotated[UploadFile | None, File()] = None,
    text: Annotated[str | None, Form()] = None,
    filename: Annotated[str | None, Form()] = None,
    use_llm: Annotated[bool, Form()] = False,
    llm_profile_id: Annotated[str | None, Form()] = None,
    remote_consent: Annotated[bool, Form()] = False,
) -> dict[str, Any]:
    project = _project(project_id, platform_db, m4_db)
    if file is not None:
        raw_name = file.filename or "storyboard.txt"
        raw_bytes = await file.read(20 * 1024 * 1024 + 1)
    elif text is not None:
        raw_name = (filename or "pasted_storyboard.txt").strip() or "pasted_storyboard.txt"
        raw_bytes = text.encode("utf-8")
    else:
        raise HTTPException(422, {"code": "M4_IMPORT_INPUT_REQUIRED", "message": "请上传分镜文件或粘贴分镜文本。"})

    llm_conn = None
    if use_llm and llm_profile_id:
        llm_conn = resolve_platform_llm_connection(platform_db, request.app.state.settings, llm_profile_id)
        base_url = str(llm_conn.get("base_url") or "")
        is_local = is_loopback_url(base_url)
        if not is_local and not remote_consent:
            raise HTTPException(
                409,
                {
                    "code": "M4_REMOTE_LLM_CONSENT_REQUIRED",
                    "message": "使用远端大模型辅助解析分镜文本需要您明确勾选授权。",
                },
            )

    result = import_storyboard_source(
        m4_db,
        project,
        request.app.state.m4_root,
        actor=actor,
        filename=raw_name,
        content=raw_bytes,
        use_llm=use_llm,
        llm_connection=llm_conn,
        mock_responder=getattr(request.app.state, "m4_llm_responder", None),
    )
    m4_db.commit()
    return result


@storyboard_router.post("/projects/{project_id}/storyboard/confirm")
def m4_confirm_storyboard(
    project_id: str,
    body: M4ConfirmStoryboardBody,
    actor: M4Operator,
    platform_db: PlatformDB,
    m4_db: M4DB,
) -> dict[str, Any]:
    project = _project(project_id, platform_db, m4_db)
    result = confirm_storyboard_plan(
        m4_db,
        project,
        actor=actor,
        expected_revision=body.expected_revision,
        acknowledge_duration_mismatch=body.acknowledge_duration_mismatch,
    )
    m4_db.commit()
    return result
