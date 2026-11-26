"""REST API for the unified Style LoRA registry (/api/v1/loras)."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Request, UploadFile
from pydantic import BaseModel, ConfigDict, Field, ValidationError
from sqlalchemy import text

from shine.api_common import DBSession
from shine.api_operator import Operator
from shine.comfyops.loras import (
    H3_LORA_BASE,
    IMAGE_LORA_BASE,
    StyleLoraBase,
    StyleLoraError,
    TriggerPosition,
    delete_style_lora,
    evaluate_style_lora_selection,
    extract_comfy_lora_names,
    freeze_image_lora_style,
    image_style_payload,
    import_style_lora,
    list_style_loras,
    probe_live_lora_object_info,
    project_style_lora_asset,
    update_style_lora,
)

router = APIRouter(prefix="/api/v1/loras", tags=["loras"])


class LoraImportFields(BaseModel):
    model_config = ConfigDict(extra="forbid")

    display_name: str | None = Field(default=None, max_length=160)
    default_strength: float = 0.65
    trigger_word: str = ""
    base_model: StyleLoraBase = H3_LORA_BASE
    negative_trigger_word: str = ""
    trigger_position: TriggerPosition = "suffix"


class LoraImportJsonBody(LoraImportFields):
    local_path: str = Field(min_length=1, max_length=1024)


class LoraPatchBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    display_name: str | None = Field(default=None, max_length=160)
    default_strength: float | None = None
    trigger_word: str | None = None
    negative_trigger_word: str | None = None
    trigger_position: TriggerPosition | None = None


def _raise_lora_http(exc: StyleLoraError) -> None:
    raise HTTPException(
        exc.http_status,
        detail={"code": exc.code, "message": exc.message, "details": exc.details},
    ) from exc


def resolve_image_style_request(db, settings, body, *, prior_payload: dict | None = None, lock: bool = True) -> dict:
    """Use the same admission and frozen fields for both image request entry points."""
    prior_style = image_style_payload(prior_payload or {})
    if not body.has_image_style and not prior_style:
        return {}
    if lock:
        db.execute(text("BEGIN IMMEDIATE"))
    try:
        if prior_style:
            evaluate_style_lora_selection(db, settings, prior_style.get("style_loras"),
                                          base_model=IMAGE_LORA_BASE, frozen_image_metadata=True)
            return prior_style
        return freeze_image_lora_style(db, settings, body.style_loras, body.style_preset)
    except StyleLoraError as exc:
        _raise_lora_http(exc)


@router.get("")
def get_loras(request: Request, db: DBSession, base_model: StyleLoraBase = H3_LORA_BASE) -> dict[str, Any]:
    """Discover built-in realism LoRA and project live ComfyUI visibility."""
    settings = request.app.state.settings
    if base_model == IMAGE_LORA_BASE:
        live_info, _ = probe_live_lora_object_info(db, settings, base_model=base_model)
        db.execute(text("BEGIN IMMEDIATE"))
        result = list_style_loras(db, settings, base_model=base_model, object_info=live_info, probe_comfy=False)
    else:
        result = list_style_loras(db, settings)
    db.commit()
    if base_model == IMAGE_LORA_BASE:
        # The generation forms read this ledger to let a person assemble a
        # style, so the card's VRAM advice and character-sheet LoRA projection
        # ride along: one single authoritative fact read for every surface.
        from shine.asset_center.character_sheet import project_character_sheet_lora_from_listed
        from shine.comfyops.capability import image_vram_advice_for_host

        result["vram_advice"] = image_vram_advice_for_host(settings)
        result["character_sheet"] = project_character_sheet_lora_from_listed(result)
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
        fields = {key: form[key] for key in LoraImportFields.model_fields if key in form}
        if not str(fields.get("default_strength", "")).strip():
            fields.pop("default_strength", None)
        try:
            parsed = LoraImportFields.model_validate(fields)
        except ValidationError as exc:
            await upload_file.close()
            if any(row["loc"] == ("default_strength",) for row in exc.errors()):
                raise HTTPException(422, detail={"code": "STYLE_LORA_STRENGTH_OUT_OF_RANGE",
                                                "message": "默认权重必须是数字。"}) from exc
            raise HTTPException(422, detail={"code": "LORA_IMPORT_INPUT_REQUIRED",
                                            "message": "LoRA 导入字段不合法。"}) from exc
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

    try:
        try:
            row, deduplicated = import_style_lora(
                db,
                settings,
                local_path=local_path,
                upload_filename=upload_filename,
                upload_stream=upload_file.file if upload_file is not None else None,
                **parsed.model_dump(exclude={"local_path"}),
                actor=actor,
            )
        finally:
            if upload_file is not None:
                await upload_file.close()
    except StyleLoraError as exc:
        _raise_lora_http(exc)

    db.commit()
    live_info, comfy_reachable = probe_live_lora_object_info(db, settings, base_model=row.base_model)
    available_names = extract_comfy_lora_names(live_info) if live_info is not None else None
    from shine.comfyops.loras import _known_lora_roots

    item = project_style_lora_asset(
        row,
        available_names=available_names,
        comfy_reachable=comfy_reachable,
        lora_roots=_known_lora_roots(db, settings, row.base_model),
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
            **body.model_dump(),
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
        db.execute(text("BEGIN IMMEDIATE"))
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
