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
    """White = edit (center), black = preserve (border); matches source pixels."""
    rows = []
    x0, x1 = width // 4, 3 * width // 4
    y0, y1 = height // 4, 3 * height // 4
    white = b"\xff\xff\xff"
    black = b"\x00\x00\x00"
    for y in range(height):
        inside_y = y0 <= y < y1
        row = bytearray(b"\0")
        for x in range(width):
            row += white if (inside_y and x0 <= x < x1) else black
        rows.append(bytes(row))
    return (
        b"\x89PNG\r\n\x1a\n"
        + _chunk(b"IHDR", struct.pack(">2I5B", width, height, 8, 2, 0, 0, 0))
        + _chunk(b"IDAT", zlib.compress(b"".join(rows)))
        + _chunk(b"IEND", b"")
    )


def queue_image_edit_validation(db, settings, *, actor: str = "worker:setup",
                                size: int = 512) -> dict[str, Any]:
    """Register an isolated test base + mask and queue I2I/INPAINT validation.

    Returns ``{"I2I": [task_id...], "INPAINT": [task_id...]}``. Idempotent per
    call identity: each invocation uses a fresh idempotency key, but the caller
    (the preparation task) records the result and never re-queues a finished
    mode.
    """
    from shine.api_asset_center import ImageJobRequest, ImageReference, create_image_jobs
    from shine.asset_center import service

    base_bytes = _solid_png(size, size, (118, 138, 158))
    mask_bytes = _mask_png(size, size)
    common = dict(
        actor=actor, source_type="generated", license_kind="test", scope="local",
        statement="基础配置出图能力验证用的隔离测试图，非用户素材。",
        subject="image-capability-validation", allowed_project_ids=["*"],
        permitted_uses=["local"], commercial_allowed=False, expires_at=None,
    )
    base_media = service.create_media(
        db, settings, content=base_bytes, filename="shine-edit-validation-base.png",
        kind="image", purpose="reference", **common)
    mask_media = service.create_media(
        db, settings, content=mask_bytes, filename="shine-edit-validation-mask.png",
        kind="image", purpose="mask", **common)
    db.flush()

    tasks: dict[str, Any] = {}
    for mode in ("I2I", "INPAINT"):
        body = ImageJobRequest(
            idempotency_key=f"setup-edit-{mode.lower()}-{uuid4().hex[:16]}",
            title=f"基础配置 {mode} 能力验证",
            mode=mode,
            prompt="a calm portrait in soft window light, plain background",
            category="other",
            references=[ImageReference(media_id=base_media.id, order=0, role="canvas")],
            mask_media_id=mask_media.id if mode == "INPAINT" else None,
            output={"width": size, "height": size},
            candidate_count=1,
            validation_only=True,
        )
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