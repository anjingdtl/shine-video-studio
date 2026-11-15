"""Unified I2I/INPAINT validation samples for the basic-config preparation.

The asset center's image modes share one capability ledger. T2I families are
validated by the materialprep sample batch; I2I and INPAINT additionally need an
edit target (and a pixel-aligned mask for INPAINT). This module generates an
isolated test base image and mask, registers them as library media, and queues
one real validation sample per mode through the same ``create_image_jobs`` path
the workshop uses. The basic-config preparation therefore verifies every image
mode in one place, and the asset center consumes the same evidence — nobody has
to run a separate manual validation per mode.

Nothing here promotes a candidate or opens formal generation: a validation
sample only proves the mode can render on this machine.
"""

from __future__ import annotations

import struct
import zlib
from typing import Any
from uuid import uuid4


def _chunk(kind: bytes, data: bytes) -> bytes:
    return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data))


def _solid_png(width: int, height: int, rgb: tuple[int, int, int]) -> bytes:
    row = b"\0" + bytes(rgb) * width
    return (
        b"\x89PNG\r\n\x1a\n"
        + _chunk(b"IHDR", struct.pack(">2I5B", width, height, 8, 2, 0, 0, 0))
        + _chunk(b"IDAT", zlib.compress(row * height))
        + _chunk(b"IEND", b"")
    )


def _mask_png(width: int, height: int) -> bytes:
    """RGBA mask: white = edit (center), black = preserve (border), alpha 255.

    The INPAINT contract requires an RGBA PNG of opaque pure-black/white pixels
    that matches the source dimensions exactly.
    """
    rows = []
    x0, x1 = width // 4, 3 * width // 4
    y0, y1 = height // 4, 3 * height // 4
    white = b"\xff\xff\xff\xff"
    black = b"\x00\x00\x00\xff"
    for y in range(height):
        inside_y = y0 <= y < y1
        row = bytearray(b"\0")
        for x in range(width):
            row += white if (inside_y and x0 <= x < x1) else black
        rows.append(bytes(row))
    return (
        b"\x89PNG\r\n\x1a\n"
        + _chunk(b"IHDR", struct.pack(">2I5B", width, height, 8, 6, 0, 0, 0))
        + _chunk(b"IDAT", zlib.compress(b"".join(rows)))
        + _chunk(b"IEND", b"")
    )


def queue_image_edit_validation(factory, settings, *, actor: str = "worker:setup",
                                size: int = 512) -> dict[str, Any]:
    """Register isolated test images and queue every edit-mode validation.

    Covers the asset center's full image capability set beyond T2I (which the
    materialprep family samples already prove): I2I, INPAINT, MULTIREF,
    WARDROBE and RGBA. Each mode gets one real validation sample through the
    same ``create_image_jobs`` ledger the workshop uses, so the basic config
    verifies every mode in one place and the asset center consumes the same
    evidence. Returns ``{mode: [task_id...]}``.

    ``create_image_jobs`` commits its own transaction, so this function manages
    short sessions itself rather than running inside an outer ``begin()`` block
    (which would double-commit and raise "closed transaction").
    """
    from shine.api_asset_center import ImageJobRequest, ImageReference, create_image_jobs
    from shine.asset_center import service

    base_bytes = _solid_png(size, size, (118, 138, 158))
    ref_bytes = _solid_png(size, size, (158, 128, 98))
    mask_bytes = _mask_png(size, size)
    common = dict(
        actor=actor, source_type="generated", license_kind="test", scope="local",
        statement="基础配置出图能力验证用的隔离测试图，非用户素材。",
        subject="image-capability-validation", allowed_project_ids=["*"],
        permitted_uses=["local"], commercial_allowed=False, expires_at=None,
    )
    with factory() as db:
        base_media = service.create_media(
            db, settings, content=base_bytes, filename="shine-edit-validation-base.png",
            kind="image", purpose="reference", **common)
        ref_media = service.create_media(
            db, settings, content=ref_bytes, filename="shine-edit-validation-ref.png",
            kind="image", purpose="reference", **common)
        mask_media = service.create_media(
            db, settings, content=mask_bytes, filename="shine-edit-validation-mask.png",
            kind="image", purpose="mask", **common)
        db.commit()
        base_id, ref_id, mask_id = base_media.id, ref_media.id, mask_media.id

    def ref(media_id: str, order: int, role: str) -> ImageReference:
        return ImageReference(media_id=media_id, order=order, role=role)

    # mode -> (references, mask, transparent). T2I is excluded: its three family
    # samples are validated by the materialprep batch, not the edit workflow.
    specs: dict[str, tuple[list[ImageReference], str | None, bool]] = {
        "I2I": ([ref(base_id, 0, "canvas")], None, False),
        "INPAINT": ([ref(base_id, 0, "canvas")], mask_id, False),
        "MULTIREF": ([ref(base_id, 0, "canvas"), ref(ref_id, 1, "scene")], None, False),
        "WARDROBE": ([ref(base_id, 0, "subject"), ref(ref_id, 1, "garment")], None, False),
        "RGBA": ([], None, True),
    }
    tasks: dict[str, Any] = {}
    for mode, (references, mask_id, transparent) in specs.items():
        body = ImageJobRequest(
            idempotency_key=f"setup-edit-{mode.lower()}-{uuid4().hex[:16]}",
            title=f"基础配置 {mode} 能力验证",
            mode=mode,
            prompt="a calm portrait in soft window light, plain background",
            category="other",
            references=references,
            mask_media_id=mask_id,
            output={"width": size, "height": size},
            candidate_count=1,
            validation_only=True,
            transparent=transparent,
        )
        with factory() as db:
            result = create_image_jobs(body=body, settings=settings, db=db)
            tasks[mode] = [item["id"] for item in result.get("items", [])]
    return tasks


def edit_validation_state(db, task_ids: dict[str, Any]) -> dict[str, Any]:
    """Read the real outcome of the queued edit validation tasks."""
    from shine.asset_center.models import LibraryRenderTask

    state: dict[str, Any] = {}
    for mode, ids in (task_ids or {}).items():
        statuses = []
        for tid in ids or []:
            row = db.get(LibraryRenderTask, tid)
            if row is not None:
                statuses.append(row.status)
        if not statuses:
            state[mode] = "UNKNOWN"
        elif all(s in {"CANDIDATE_READY", "PROMOTED"} for s in statuses):
            state[mode] = "READY"
        elif any(s in {"FAILED", "CANCELLED", "NEEDS_REVIEW"} for s in statuses):
            state[mode] = "FAILED"
        else:
            state[mode] = "RUNNING"
    return state