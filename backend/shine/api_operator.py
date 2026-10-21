from __future__ import annotations

from typing import Annotated
from urllib.parse import urlparse

from fastapi import APIRouter, Depends, Header, HTTPException, Request
from fastapi.responses import FileResponse

from shine.api_common import DBSession, app_settings
from shine.comfy.client import ComfyError
from shine.config import Settings
from shine.operator_review import OperatorDecision, authenticate, context, review_context, submit_review

router = APIRouter(prefix="/api/v1/operator", tags=["human-operator"])
SettingsDep = Annotated[Settings, Depends(app_settings)]


def operator(request: Request, settings: SettingsDep, authorization: Annotated[str, Header()] = ""):
    if not request.client or request.client.host not in {"127.0.0.1", "::1", "testclient"}:
        raise HTTPException(403, "Operator review is loopback-only")
    origin = request.headers.get("origin")
    if origin and urlparse(origin).netloc != request.url.netloc:
        raise HTTPException(403, "Cross-origin operator review forbidden")
    return authenticate(settings, authorization.removeprefix("Bearer "))


Operator = Annotated[str, Depends(operator)]


@router.get("/jobs/{job_id}")
def get_context(job_id: str, actor: Operator, settings: SettingsDep, db: DBSession):
    return {**review_context(db, settings, job_id), "operator_id": actor}


@router.get("/jobs/{job_id}/media")
def media(job_id: str, actor: Operator, settings: SettingsDep, db: DBSession):
    _, _, artifact, _, path, _ = context(db, settings, job_id)
    return FileResponse(path, media_type=artifact.content_type, headers={"Cache-Control": "no-store"})


@router.post("/jobs/{job_id}/review")
def review(job_id: str, body: OperatorDecision, actor: Operator, settings: SettingsDep, db: DBSession):
    try:
        return submit_review(db, settings, job_id, actor, body)
    except ComfyError as exc:
        raise HTTPException(503, "ComfyUI history unavailable; approval was not recorded") from exc
