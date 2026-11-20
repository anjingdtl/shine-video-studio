"""Shared FastAPI dependencies and guards for all Manual4 endpoints."""

from __future__ import annotations

from typing import Annotated
from urllib.parse import urlparse

from fastapi import Depends, Header, HTTPException, Request
from sqlalchemy.orm import Session

from shine.manual4.storage.models import M4Project
from shine.platform_projects import require_manual4_project


def _m4_db(request: Request):
    factory = getattr(request.app.state, "m4_session_factory", None)
    if factory is None:
        raise HTTPException(503, "MANUAL4_DB_UNINITIALIZED")
    session: Session = factory()
    try:
        yield session
    finally:
        session.close()


def _platform_db(request: Request):
    session: Session = request.app.state.session_factory()
    try:
        yield session
    finally:
        session.close()


M4DB = Annotated[Session, Depends(_m4_db)]
PlatformDB = Annotated[Session, Depends(_platform_db)]


def m4_operator(request: Request, authorization: Annotated[str, Header()] = "") -> str:
    if not request.client or request.client.host not in {"127.0.0.1", "::1", "testclient"}:
        raise HTTPException(403, "Manual4 API is loopback-only")
    origin = request.headers.get("origin")
    if origin and urlparse(origin).netloc != request.url.netloc:
        raise HTTPException(403, "Cross-origin Manual4 action forbidden")
    if request.headers.get("sec-fetch-site") == "cross-site":
        raise HTTPException(403, "Cross-site Manual4 action forbidden")
    token = authorization.strip()
    if request.headers.get("x-shine-local-action") == "1" and not token:
        return "local-user"
    if token.lower().startswith("bearer ") and len(token) > 7:
        return "local-user"
    raise HTTPException(403, "Manual4 action requires x-shine-local-action header")


M4Operator = Annotated[str, Depends(m4_operator)]


def require_m4_project(project_id: str, platform_db: Session, m4_db: Session) -> M4Project:
    require_manual4_project(platform_db, project_id)
    row = m4_db.get(M4Project, project_id)
    if row is None:
        raise HTTPException(404, "M4_PROJECT_RECORD_MISSING")
    return row
