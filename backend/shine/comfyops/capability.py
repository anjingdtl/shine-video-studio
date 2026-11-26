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

from shine.comfyops.variants import effective_floor_mb

CAPABILITY_BASE_TOOLS = "base_tools"
CAPABILITY_H3 = "h3"
CAPABILITY_IMAGE = "image"
CAPABILITY_REVIEW = "review"
CAPABILITY_RELAYVSR = "relayvsr"
CAPABILITY_IMAGE_LORAS = "image_loras"
CAPABILITY_CHARACTER_SHEET = "character_sheet"

#: Every capability a plan may carry.  A capability outside this set is
#: refused by name instead of silently falling back to a default.
CAPABILITIES = (CAPABILITY_BASE_TOOLS, CAPABILITY_H3, CAPABILITY_IMAGE, CAPABILITY_REVIEW,
                CAPABILITY_RELAYVSR, CAPABILITY_IMAGE_LORAS, CAPABILITY_CHARACTER_SHEET)

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
    "relayvsr-x2-windows-x64": (CAPABILITY_RELAYVSR, "shine-relayvsr-x2-windows-x64"),
    "qwen-image-loras-windows-x64": (CAPABILITY_IMAGE_LORAS, "shine-qwen-image-loras-windows-x64"),
    "qwen-character-sheet-windows-x64": (CAPABILITY_CHARACTER_SHEET, "shine-qwen-character-sheet-windows-x64"),
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

#: Free memory the managed image renderer (Qwen-Image-2.1) needs to start.
#: Kept separate from the H3 video variant's 12000 MiB floor so a 12 GB GPU
#: with normal desktop overhead is not blocked by video-only thresholds.
IMAGE_RUNTIME_REQUIREMENTS: dict[str, int] = {
    "min_ram_mb": 8192,
    "min_vram_mb": 8000,
}

#: What the managed image renderer occupied when it was measured (RTX 5080,
#: ComfyUI 0.39.0, 2026-11-25; docs/ACCEPTANCE_20261125_IMAGE_LORA_AND_PROMPT_V1.md).
#: The peak is the whole card, the desktop's own usage included.  Neither the
#: output size (640-1024 px) nor mounted LoRAs moved it: the resident weights
#: are the footprint.  A smaller card keeps the rest of the weights in system
#: memory (dynamic VRAM loading), so this is *usage* and never a minimum.
IMAGE_VRAM_MEASUREMENT: dict[str, Any] = {
    "peak_total_mb": 15473,
    "model_mb": 14580,
    "desktop_mb": 894,
    "measured_on": "RTX 5080 16 GB · ComfyUI 0.39.0 · 2026-11-25",
}

#: The VRAM classes a card falls into, by the total memory of the one selected
#: card, widest first.  ``min_mb`` is the lower bound of a class because cards
#: report a little under their nominal size.  The lowest class is the range the
#: image catalog declares as supported; below it the renderer still starts
#: (the 8000 MB start floor) but nobody has shown that it can generate.
IMAGE_VRAM_CLASSES: tuple[dict[str, Any], ...] = (
    {"id": "24gb", "label": "24 GB 档", "min_mb": 23040, "evidence": "estimated"},
    {"id": "16gb", "label": "16 GB 档", "min_mb": 15360, "evidence": "measured_on_similar_hardware"},
    {"id": "12gb", "label": "12 GB 档", "min_mb": 11776, "evidence": "reported_by_users"},
)

IMAGE_VRAM_UNVERIFIED_CLASS = {"id": "under_12gb", "label": "12 GB 以下", "min_mb": 8000}
IMAGE_VRAM_START_CLASS = {"id": "below_start", "label": "8 GB 以下", "min_mb": 0}

#: How well each statement about the image runtime is evidenced.  A class that
#: only users reported is never written up as measured.
IMAGE_VRAM_EVIDENCE_DETAIL: dict[str, str] = {
    "measured_on_this_machine": "本机已通过小样验证。",
    "measured_on_similar_hardware": "在 RTX 5080 16 GB 上实测过。",
    "reported_by_users": "据 RTX 4070 Super 12 GB 用户反馈可以正常出图；仓库里没有这一档的实测记录。",
    "estimated": "这一档没有实测记录，结论由 16 GB 档的实测占用推算。",
}


def image_vram_class(total_mb: int | None) -> dict[str, Any] | None:
    """The class one card falls into; ``None`` when the card cannot be read."""
    if not isinstance(total_mb, int | float) or isinstance(total_mb, bool):
        return None
    return next((row for row in IMAGE_VRAM_CLASSES if total_mb >= row["min_mb"]), None)


def image_vram_class_table() -> list[dict[str, Any]]:
    """Every class with its one-line advice, widest first.

    The whole table travels with the advice for one card, so a person can see
    what the other classes are told and judge their own against them.
    """
    widest, measured, reported = IMAGE_VRAM_CLASSES
    return [
        {**widest, "advice": "余量充足；两套配方、1024×1024 和多图参考都可以用。"},
        {**measured, "advice": "实测可用，余量约 0.8 GB；出图时关掉其他占显存的程序。"},
        {**reported, "advice": "用户反馈可用；靠换页运行，配方未验证。"},
        {**IMAGE_VRAM_UNVERIFIED_CLASS, "evidence": "estimated",
         "advice": "未验证：渲染器可以启动，能否出图没有记录。"},
        {**IMAGE_VRAM_START_CLASS, "evidence": None, "advice": "无法启动出图渲染器。"},
    ]


def image_vram_advice(
    total_mb: int | None,
    free_mb: int | None,
    *,
    supported_floor_mb: int | None,
    runtime_verified: bool | None = None,
) -> dict[str, Any]:
    """How one card fits the image runtime.  Advice only: it never refuses.

    The person is told which class the card is in, what the renderer really
    occupied when it was measured, how much is free right now and how well each
    of those is evidenced.  Whether to generate stays their choice, so nothing
    here is a gate and every surface reads this one answer.
    """
    measurement = IMAGE_VRAM_MEASUREMENT
    start_free = IMAGE_RUNTIME_REQUIREMENTS["min_vram_mb"]
    model_mb, peak_mb = measurement["model_mb"], measurement["peak_total_mb"]
    advice: dict[str, Any] = {
        "blocks": False,
        "gpu": None if total_mb is None else {"total_mb": total_mb, "free_mb": free_mb},
        "status": "unknown",
        "class": None,
        "evidence": {"level": None, "detail": ""},
        "usage_mb": model_mb,
        "peak_total_mb": peak_mb,
        "measured_on": measurement["measured_on"],
        "start_free_mb": start_free,
        "supported_total_mb": supported_floor_mb,
        "headroom_mb": None,
        "headline": "没有识别到 NVIDIA 显卡，读不到显存，无法给出适配建议。",
        "notes": [],
        "classes": image_vram_class_table(),
    }
    if total_mb is None:
        return advice
    klass = image_vram_class(total_mb)
    notes: list[str] = advice["notes"]
    level = "estimated"
    if total_mb < start_free:
        advice["status"] = "cannot_start"
        advice["class"] = dict(IMAGE_VRAM_START_CLASS)
        advice["headline"] = (
            f"本卡显存 {total_mb} MB，低于出图渲染器的启动门槛（空闲 {start_free} MB），无法出图。"
        )
    elif klass is None:
        advice["status"] = "unverified"
        advice["class"] = dict(IMAGE_VRAM_UNVERIFIED_CLASS)
        advice["headline"] = (
            f"本卡显存 {total_mb} MB，低于已验证过的最低一档（12 GB 档）。"
            "渲染器可以启动，但能否出图还没有验证过。"
        )
    else:
        advice["status"] = "fits"
        advice["class"] = dict(klass)
        # Negative headroom is the 12 GB class's normal state (the rest of the
        # weights page out), so it is stated in a note rather than shown as a
        # number that reads like an error.
        advice["headroom_mb"] = max(0, total_mb - peak_mb) or None
        level = klass["evidence"]
        if klass["id"] == "24gb":
            advice["headline"] = (
                f"显存宽裕：出图时整卡占用约 {peak_mb} MB，本卡余量约 {advice['headroom_mb']} MB。"
            )
            notes.append("两套配方、1024×1024 和多图参考都可以用。")
        elif klass["id"] == "16gb":
            advice["headline"] = (
                f"实测可用：出图时整卡占用约 {peak_mb} MB，本卡余量约 {advice['headroom_mb']} MB。"
                if advice["headroom_mb"] is not None
                else f"实测可用：出图时整卡占用约 {peak_mb} MB，本卡接近占用上限。"
            )
        else:
            advice["headline"] = "据 RTX 4070 Super 12 GB 用户反馈，这一档可以正常出图。"
            notes.append(
                f"出图时模型占用约 {model_mb} MB，超出本卡显存；放不下的权重由 ComfyUI 换页到系统内存，"
                "需要 32 GB 级内存，速度会慢于 16 GB 及以上的显卡（推算）。"
            )
            notes.append("两套配方（LoRA）在这一档没有验证过：建议先不挂配方出一张，确认可用后再试配方。")
        if klass["id"] in ("16gb", "24gb"):
            notes.append("输出尺寸（640 到 1024 像素）和是否挂配方都不改变显存占用。")
    if runtime_verified is True:
        level = "measured_on_this_machine"
        notes.insert(0, "本机已通过小样验证。")
    elif runtime_verified is False:
        notes.append("本机还没有通过小样验证：先在基础设置运行一次小样验证，才能出图。")
    advice["evidence"] = {"level": level, "detail": IMAGE_VRAM_EVIDENCE_DETAIL[level]}
    if isinstance(free_mb, int) and not isinstance(free_mb, bool):
        if free_mb < start_free:
            notes.append(
                f"当前空闲显存 {free_mb} MB，低于渲染器启动所需的 {start_free} MB："
                "关掉其他占显存的程序后再出图。"
            )
        elif (
            advice["status"] == "fits"
            and klass is not None
            and klass["id"] in ("16gb", "24gb")
            and free_mb < model_mb
        ):
            notes.append(
                f"当前空闲显存 {free_mb} MB，低于实测占用约 {model_mb} MB："
                "出图前关掉其他占显存的程序，否则可能失败。"
            )
    return advice


def image_vram_advice_for_host(settings, *, runtime_verified: bool | None = None) -> dict[str, Any]:
    """The advice for the card this machine renders on.

    An unreadable GPU or catalog is *unknown*, never an error: advice must not
    be the reason a page or a generation request fails.
    """
    from shine.comfyops.deployment import IMAGE_CAPABILITY_CATALOG, load_deployment_catalog
    from shine.comfyops.host_env import probe_gpus, select_gpu

    card, floor = None, None
    try:
        card = select_gpu(probe_gpus())
        catalog = load_deployment_catalog(IMAGE_CAPABILITY_CATALOG)
        floor = runtime_requirements(CAPABILITY_IMAGE, catalog, [])["min_vram_mb"]
    except Exception:  # noqa: BLE001 - advice only; an unreadable host stays unknown
        pass
    return image_vram_advice(
        None if card is None else card.get("vram_total_mb"),
        None if card is None else card.get("vram_free_mb"),
        supported_floor_mb=floor,
        runtime_verified=runtime_verified,
    )


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
    if capability in (CAPABILITY_IMAGE, CAPABILITY_RELAYVSR):
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
    # Read the RAM floor exactly as ``qualify`` does: a declared whole-GiB floor
    # is compared with the memory a host really reports, which is a little
    # under the installed size.  A literal 32768 told every 32 GB machine that
    # generated images daily that it could not run them.
    declared_ram = floors.get("min_ram_mb")
    min_ram = effective_floor_mb(declared_ram)
    ram = env.get("ram") or {}
    if isinstance(min_ram, int) and ram.get("status") == "OK":
        total_ram = ram.get("ram_mb")
        installed = ram.get("installed_mb")
        # ``qualify`` lets a measured installed capacity bridge the gap that
        # firmware-reserved memory opens; this answer may not disagree with it.
        reserved = (isinstance(installed, int) and not isinstance(installed, bool)
                    and isinstance(declared_ram, int) and installed >= declared_ram)
        if isinstance(total_ram, int) and total_ram < min_ram and not reserved:
            notes.append({
                "code": "RAM_BELOW_CAPABILITY_FLOOR",
                "message": f"这个能力{subject}运行时需要至少 {min_ram} MB 内存，本机是 {total_ram} MB。"
                           "权重可以先下载，但这台机器暂时跑不动它。",
                "required": min_ram,
                "actual": total_ram,
            })
    return notes
