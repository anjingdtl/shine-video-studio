"""The one output directory setting and its migration action.

Changing the root is not a cosmetic preference: every produced file moves to
the readable two-level layout and the ledger is switched only after each file
has been copied and verified.  The endpoint therefore refuses while any
media-producing work is still pending.
"""

from __future__ import annotations

from fastapi import APIRouter, Request
from pydantic import Field

from shine.api_common import DBSession
from shine.api_operator import Operator
from shine.media.output_layout import (
    directory_summary,
    migrate_output_root,
    validate_output_root,
)
from shine.schemas import APIModel

router = APIRouter(prefix="/api/v1", tags=["output-directory"])


class OutputDirectoryUpdate(APIModel):
    #: An absolute local directory, or empty/null to return to the default.
    path: str | None = Field(default=None, max_length=1000)


def _default_root(request: Request):
    return getattr(request.app.state, "asset_root", None) or request.app.state.settings.data_dir


@router.get("/settings/output-directory")
def get_output_directory(request: Request, db: DBSession) -> dict:
    return directory_summary(
        db, request.app.state.settings, default_root=_default_root(request)
    )


@router.put("/settings/output-directory")
def update_output_directory(
    body: OutputDirectoryUpdate,
    actor: Operator,
    request: Request,
    db: DBSession,
) -> dict:
    settings = request.app.state.settings
    target = validate_output_root(body.path, settings)
    return migrate_output_root(
        db, settings, target=target, actor=actor,
        default_root=_default_root(request),
    )
