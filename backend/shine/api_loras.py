"""REST API for the unified Style LoRA registry (/api/v1/loras)."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Request, UploadFile
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from shine.api_common import DBSession
from shine.api_operator import Operator
from shine.comfyops.loras import (
    StyleLoraError,
    delete_style_lora,
    extract_comfy_lora_names,
    import_style_lora,
    list_style_loras,
    probe_live_lora_object_info,
    project_style_lora_asset,
    update_style_lora,
)

router = APIRouter(prefix="/api/v1/loras", tags=["loras"])


class LoraImportJsonBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    local_path: str = Field(min_length=1, max_length=1024)
    display_name: str | None = Field(default=None, max_length=160)
    default_strength: float = 0.65
    trigger_word: str = ""


class LoraPatchBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    display_name: str | None = Field(default=None, max_length=160)
    default_strength: float | None = None
    trigger_word: str | None = None


def _raise_lora_http(exc: StyleLoraError) -> None:
    raise HTTPException(
        exc.http_status,
        detail={"code": exc.code, "message": exc.message, "details": exc.details},
    ) from exc


@router.get("")
def get_loras(request: Request, db: DBSession) -> dict[str, Any]:
    """Discover built-in realism LoRA and project live ComfyUI visibility."""
    settings = request.app.state.settings
    result = list_style_loras(db, settings)
    db.commit()
    return result


@router.post("/import", status_code=201)
async def post_import_lora(
    request: Request,
    actor: Operator,
    db: DBSession,
) -> dict[str, Any]:
    """Import a style LoRA via multipart upload or local path through one executor."""
    settings = request.app.state.settings
    content_type = (request.headers.get("content-type") or "").lower()

    local_path: str | None = None
    upload_filename: str | None = None
    upload_file: UploadFile | None = None
    display_name: str | None = None
    default_strength: float = 0.65
    trigger_word: str = ""

    if "multipart/form-data" in content_type:
        form = await request.form()
        raw_file = form.get("file")
        if raw_file is None or not hasattr(raw_file, "file"):
            raise HTTPException(
                422,
                detail={"code": "LORA_IMPORT_INPUT_REQUIRED", "message": "请上传 .safetensors 文件。"},
            )
        upload_file = raw_file  # type: ignore[assignment]
        upload_filename = str(getattr(upload_file, "filename", None) or "style_lora.safetensors")
        if form.get("display_name") is not None:
            display_name = str(form.get("display_name"))
        if form.get("default_strength") is not None and str(form.get("default_strength")).strip():
            try:
                default_strength = float(str(form.get("default_strength")))
            except ValueError as exc:
                raise HTTPException(
                    422,
                    detail={
                        "code": "STYLE_LORA_STRENGTH_OUT_OF_RANGE",
                        "message": "默认权重必须是数字。",
                    },
                ) from exc
        if form.get("trigger_word") is not None:
            trigger_word = str(form.get("trigger_word"))
    else:
        try:
            raw_json = await request.json()
            parsed = LoraImportJsonBody.model_validate(raw_json)
        except (ValueError, ValidationError) as exc:
            raise HTTPException(
                422,
                detail={
                    "code": "LORA_IMPORT_INPUT_REQUIRED",
                    "message": "导入请求体不合法，请提供 local_path 或上传 .safetensors 文件。",
                },
            ) from exc
        local_path = parsed.local_path
        display_name = parsed.display_name
        default_strength = parsed.default_strength
        trigger_word = parsed.trigger_word

    try:
        try:
            row, deduplicated = import_style_lora(
                db,
                settings,
                local_path=local_path,
                upload_filename=upload_filename,
                upload_stream=upload_file.file if upload_file is not None else None,
                display_name=display_name,
                default_strength=default_strength,
                trigger_word=trigger_word,
                actor=actor,
            )
        finally:
            if upload_file is not None:
                await upload_file.close()
    except StyleLoraError as exc:
        _raise_lora_http(exc)

    db.commit()
    live_info, comfy_reachable = probe_live_lora_object_info(db, settings)
    available_names = extract_comfy_lora_names(live_info) if live_info is not None else None
    from shine.comfyops.loras import _known_lora_roots

    item = project_style_lora_asset(
        row,
        available_names=available_names,
        comfy_reachable=comfy_reachable,
        lora_roots=_known_lora_roots(db, settings),
    )
    return {
        "item": item,
        "deduplicated": deduplicated,
        "comfy_reachable": comfy_reachable,
    }


@router.patch("/{lora_id}")
def patch_lora(
    lora_id: str,
    body: LoraPatchBody,
    request: Request,
    actor: Operator,
    db: DBSession,
) -> dict[str, Any]:
    """Update display name, default strength, or validated ASCII trigger word."""
    settings = request.app.state.settings
    try:
        item = update_style_lora(
            db,
            settings,
            lora_id,
            display_name=body.display_name,
            default_strength=body.default_strength,
            trigger_word=body.trigger_word,
            actor=actor,
        )
    except StyleLoraError as exc:
        _raise_lora_http(exc)
    db.commit()
    return {"item": item}


@router.delete("/{lora_id}")
def remove_lora(
    lora_id: str,
    request: Request,
    actor: Operator,
    db: DBSession,
) -> dict[str, Any]:
    """Delete a registered style LoRA (if not referenced by any active run)."""
    settings = request.app.state.settings
    m4_factory = getattr(request.app.state, "m4_session_factory", None)
    try:
        result = delete_style_lora(
            db,
            settings,
            lora_id,
            m4_session_factory=m4_factory,
            actor=actor,
        )
    except StyleLoraError as exc:
        _raise_lora_http(exc)
    db.commit()
    return result
