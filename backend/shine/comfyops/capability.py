"""Capability-scoped resource preparation.

A downloadable catalog belongs to exactly one capability, and the capability
decides which admission gate applies to an install plan.  The capability is
derived from the *server owned* catalog manifest, never from the request: a
browser can neither declare a plan "image" to skip a gate, nor declare it
"h3" to borrow another capability's gate.

Three rules this module owns:

* The H3 production gate (``HOST_NOT_QUALIFIED``) belongs to the H3
  capability alone.  A plan that prepares image or review weights is never
  refused because the video variant does not fit this card.
* A capability never loses its own safety conditions.  Downloading weights is
  storage bound; *running* them is compute bound.  The image capability
  therefore reports its own runtime requirements - honestly, with the real
  numbers - and the runtime gate stays where it belongs: at the moment the
  renderer is started.
* Base tools (uv, Node, FFmpeg, PowerShell) are repaired by their own plan
  and never inherit a GPU threshold.
"""

from __future__ import annotations

from typing import Any

from fastapi import HTTPException

CAPABILITY_BASE_TOOLS = "base_tools"
CAPABILITY_H3 = "h3"
CAPABILITY_IMAGE = "image"
CAPABILITY_REVIEW = "review"

#: Every capability a plan may carry.  A capability outside this set is
#: refused by name instead of silently falling back to a default.
CAPABILITIES = (CAPABILITY_BASE_TOOLS, CAPABILITY_H3, CAPABILITY_IMAGE, CAPABILITY_REVIEW)

#: Catalog file name -> (capability, expected catalog id).
#:
#: The mapping is explicit on purpose: a new catalog has to be classified
#: here, in a reviewed diff, instead of inheriting a gate by accident.  The
#: expected catalog id is checked too, so a catalog file that is swapped for
#: another one cannot borrow another capability's admission.
CATALOG_CAPABILITIES: dict[str, tuple[str, str]] = {
    "h3-ref2va-windows-x64": (CAPABILITY_H3, "shine-h3-ref2va-windows-x64"),
    "h3-ref2va-lowend-candidate-windows-x64": (CAPABILITY_H3, "shine-h3-ref2va-lowend-candidate-windows-x64"),
    "h3-ref2va-nvfp4-lowend-windows-x64": (CAPABILITY_H3, "shine-h3-ref2va-nvfp4-lowend-windows-x64"),
    "qwen-image-2.1-windows-x64": (CAPABILITY_IMAGE, "shine-qwen-image-21-windows-x64"),
}

#: The review models live in the H3 catalog but are their own capability: a
#: review plan must never inherit the video variant's admission gate.
REVIEW_ROLES = ("VLM", "ASR")

#: The compute a review model needs to *run*.  The managed review servers
#: use the same floors, so the card and the runtime cannot disagree.
REVIEW_RUNTIME_REQUIREMENTS: dict[str, dict[str, int]] = {
    "VLM": {"min_ram_mb": 4096, "min_vram_mb": 8000},
    "ASR": {"min_ram_mb": 2048, "min_vram_mb": 4000},
}


def catalog_capability(name: str, catalog: dict) -> str:
    """The capability of a catalog, from the server owned classification.

    The catalog file name is the key, and the catalog's own identity is
    verified against the expectation, so neither the request nor a swapped
    file can move a plan into another capability's gate.
    """
    entry = CATALOG_CAPABILITIES.get(name)
    if entry is None:
        raise HTTPException(409, {
            "code": "CATALOG_CAPABILITY_UNKNOWN",
            "message": "这份资源清单还没有被归类到任何能力，不能直接创建安装计划。"
                       "没有归类的清单不会继承任何门禁，也不会绕过任何门禁。",
            "catalog": name,
        })
    capability, expected_catalog_id = entry
    if catalog.get("catalog_id") != expected_catalog_id:
        raise HTTPException(409, {
            "code": "CATALOG_IDENTITY_MISMATCH",
            "message": "资源清单的身份和它的归类不一致，已拒绝。",
            "catalog": name,
            "expected_catalog_id": expected_catalog_id,
            "actual_catalog_id": catalog.get("catalog_id"),
        })
    return capability


def plan_capability(capability: str, catalog: dict, component_ids: list[str]) -> str:
    """The capability of one plan, refined by the components it carries.

    The image catalog has no video variant, so an image plan is always an
    image plan.  Inside the H3 catalog a plan that carries nothing but review
    models is a review plan: the video production gate must not reach it.
    """
    if capability != CAPABILITY_H3:
        return capability
    components = {row["component_id"]: row for row in catalog.get("components", [])}
    roles = {str((components.get(cid) or {}).get("role") or "") for cid in component_ids}
    roles.discard("")
    if roles and roles.issubset(set(REVIEW_ROLES)):
        return CAPABILITY_REVIEW
    return CAPABILITY_H3


def runtime_requirements(capability: str, catalog: dict, component_ids: list[str]) -> dict[str, Any]:
    """The compute a capability needs to *run*, from the server owned facts.

    These are runtime floors, not download floors.  A weight can be downloaded
    on a machine that cannot run it yet; what must not happen is that the
    machine is told the capability is ready.
    """
    if capability == CAPABILITY_IMAGE:
        platform = catalog.get("platform") or {}
        return {
            "min_vram_mb": platform.get("min_vram_mb"),
            "min_ram_mb": platform.get("min_ram_mb"),
            "min_disk_gb": platform.get("min_disk_gb"),
            "source": "catalog_platform",
        }
    if capability == CAPABILITY_REVIEW:
        components = {row["component_id"]: row for row in catalog.get("components", [])}
        floors: dict[str, Any] = {"source": "managed_review_runtime"}
        for cid in component_ids:
            role = str((components.get(cid) or {}).get("role") or "")
            floors[role or cid] = dict(REVIEW_RUNTIME_REQUIREMENTS.get(role, {}))
        return floors
    return {}


def runtime_advisories(requirements: dict[str, Any], env: dict[str, Any]) -> list[dict[str, Any]]:
    """What this host cannot do with the capability *right now*.

    These never block a download.  They are the honest answer to "can this
    machine run it", and they stay attached to the plan so the card can keep
    showing them after the bytes are on disk.

    A capability may carry one flat requirement set (the image weights) or one
    set per model (the review models).  Both are answered here.
    """
    notes: list[dict[str, Any]] = []
    flat = {
        key: value for key, value in requirements.items()
        if isinstance(value, int | float) and not isinstance(value, bool)
    }
    nested = {
        key: value for key, value in requirements.items() if isinstance(value, dict)
    }
    scopes: list[tuple[str, dict[str, Any]]] = [("", flat)]
    scopes.extend((name, value) for name, value in nested.items())
    for name, floors in scopes:
        notes.extend(_floor_notes(name, floors, env))
    return notes


def _floor_notes(name: str, floors: dict[str, Any], env: dict[str, Any]) -> list[dict[str, Any]]:
    """The measured gaps of one requirement set."""
    notes: list[dict[str, Any]] = []
    subject = f"（{name}）" if name else ""
    card = None
    for row in env.get("gpus") or []:
        if isinstance(row, dict) and row.get("selected"):
            card = row
            break
    if card is None:
        gpus = [row for row in env.get("gpus") or [] if isinstance(row, dict)]
        card = gpus[0] if gpus else None
    min_vram = floors.get("min_vram_mb")
    if isinstance(min_vram, int) and card is not None:
        total = card.get("vram_total_mb")
        if isinstance(total, int) and total < min_vram:
            notes.append({
                "code": "VRAM_BELOW_CAPABILITY_FLOOR",
                "message": f"这个能力{subject}运行时需要至少 {min_vram} MB 显存，本机这张卡是 {total} MB。"
                           "权重可以先下载，但这台机器暂时跑不动它。",
                "required": min_vram,
                "actual": total,
            })
    min_ram = floors.get("min_ram_mb")
    ram = env.get("ram") or {}
    if isinstance(min_ram, int) and ram.get("status") == "OK":
        total_ram = ram.get("ram_mb")
        if isinstance(total_ram, int) and total_ram < min_ram:
            notes.append({
                "code": "RAM_BELOW_CAPABILITY_FLOOR",
                "message": f"这个能力{subject}运行时需要至少 {min_ram} MB 内存，本机是 {total_ram} MB。"
                           "权重可以先下载，但这台机器暂时跑不动它。",
                "required": min_ram,
                "actual": total_ram,
            })
    return notes
