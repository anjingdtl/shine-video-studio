"""The single server-side fact view behind 「设置 → 制作环境」.

Everything the setup page shows is derived here from evidence that already
exists on this machine: the instance ledger, the receipt/package registry, the
pinned catalogues, the image runtime binding and the durable startup records.
Nothing in this module hashes a weight, downloads a byte, starts a process or
invents a state.

Two rules shape the code:

* **One fact, one name.** ``FILES_FOUND`` means a file with the pinned byte
  count is on disk. It never implies ``FILES_VERIFIED``, ``INSTANCE_BOUND``,
  ``WORKFLOW_VALIDATED`` or ``RENDER_VERIFIED``. The page may only claim the
  last one it can prove.
* **No second state machine.** The five phases are a projection of those
  facts. They are computed on every read and never stored.

The only write this module performs is the person's explicit, audited choice of
which ComfyUI installation to use (``record_installation_choice``).
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from sqlalchemy import select

from shine.comfyops import deployment
from shine.comfyops.instances import instance_identity, instance_root
from shine.comfyops.models import ComfyInstance

CHOICE_KEY = "setup-installation-choice"
SNAPSHOT_VERSION = 1

IMAGE_CATALOG = "qwen-image-2.1-windows-x64"
IMAGE_COMPONENTS = ("qwen-image-2.1-unet", "qwen-image-2.1-text-encoder", "qwen-image-2.1-vae")
REVIEW_COMPONENTS = {"vlm": "qwen3-vl-8b-fp8", "asr": "qwen3-asr-0.6b"}
RELAYVSR_CATALOG = "relayvsr-x2-windows-x64"
RELAYVSR_COMPONENTS = ("relayvsr-x2-diffusion", "relayvsr-x2-flash-vae")
#: capability -> catalog file stem (the name load_deployment_catalog accepts).
#: The catalog's own ``catalog_id`` (shine-…) is an identity, not a file name.
CATALOG_FILE_BY_CAPABILITY = {"image": IMAGE_CATALOG, "relayvsr": RELAYVSR_CATALOG}
H3_CATALOG = "h3-ref2va-windows-x64"

CAPABILITY_LABELS = {
    "h3": "H3 视频生成",
    "image": "素材出图",
    "vlm": "画面审片",
    "asr": "语音核对",
    "relayvsr": "RelayVSR 2X 精修",
}

CAPABILITIES = ("h3", "image", "vlm", "asr", "relayvsr")


# --- installation selection -------------------------------------------------


def _choice(db) -> dict:
    from shine.models import AppSetting

    row = db.get(AppSetting, CHOICE_KEY)
    return dict(row.value) if row is not None and isinstance(row.value, dict) else {}


def record_installation_choice(db, *, root: str, port: int | None, actor: str,
                               project_id: str | None = None) -> dict:
    """Persist the one installation a person picked; later reads reuse it.

    It answers "which ComfyUI is mine" exactly once.  Model registration is a
    different question with a different answer, and this record never pretends
    to have verified a byte.
    """
    from shine.models import AppSetting, utcnow
    from shine.services import add_audit

    resolved = Path(root).resolve()
    value = {
        "version": 1,
        "root": str(resolved),
        "port": int(port) if port else None,
        "confirmed_by": actor,
        "confirmed_at": utcnow().isoformat(),
    }
    row = db.get(AppSetting, CHOICE_KEY)
    if row is None:
        db.add(AppSetting(key=CHOICE_KEY, value=value))
    else:
        row.value = value
    add_audit(db, actor, "SETUP_INSTALLATION_CHOSEN", "deployment", "installation-choice", {
        "root": str(resolved), "port": value["port"],
    }, project_id=project_id)
    return value


def _row_facts(row: ComfyInstance) -> dict[str, Any]:
    payload = row.payload if isinstance(row.payload, dict) else {}
    return {
        "instance_id": row.id,
        "root": payload.get("root"),
        "port": payload.get("port"),
        "name": payload.get("name"),
        "managed": bool(payload.get("managed")),
        "adopted": bool(payload.get("adopted")),
        "is_mock": bool(payload.get("is_mock")),
        "status": row.status,
        "source": "adopted" if payload.get("adopted") else "managed",
    }


def _instance_rows(db) -> list[dict[str, Any]]:
    rows = []
    try:
        for row in db.scalars(select(ComfyInstance)):
            facts = _row_facts(row)
            if not facts["managed"] or facts["is_mock"]:
                continue
            rows.append(facts)
    except Exception:  # noqa: BLE001 - an unreadable ledger is not a claim
        return []
    return rows


def _catalog_components(catalog: dict) -> dict[str, dict]:
    return {row["component_id"]: row for row in catalog.get("components") or []}


def model_file_presence(models_root: str | Path | None, catalog: dict, component_ids=None) -> dict[str, Any]:
    """Cheap, read-only presence check: right path *and* right byte count.

    ``models_root`` is the ComfyUI ``models`` directory (``<root>/models`` for an
    installation, ``image-runtime.json``'s own ``model_root`` for the image
    runtime).  Size is a filter, never proof - the column is called
    FILES_FOUND for that reason.  Only
    :mod:`shine.comfyops.verification_receipts` may say verified.
    """
    present, missing, found_bytes = [], [], 0
    base = Path(models_root) if models_root else None
    components = _catalog_components(catalog)
    for component_id, component in components.items():
        if component_ids is not None and component_id not in component_ids:
            continue
        if component.get("kind") not in {"model", "review-model"}:
            continue
        family = component.get("destination_family")
        for item in component.get("files") or []:
            relative = Path(str(item.get("relative_path") or ""))
            target = None
            if base is not None and family and not relative.is_absolute():
                # ComfyUI's own aliases: text encoders also live in clip/.
                folders = (family, "clip") if family == "text_encoders" else (family,)
                for folder in folders:
                    candidate = base / folder / relative
                    if candidate.is_file():
                        target = candidate
                        break
            ok = False
            if target is not None:
                try:
                    ok = target.stat().st_size == int(item.get("size") or -1)
                except OSError:
                    ok = False
            if ok:
                present.append({"component_id": component_id, "relative_path": str(relative),
                                "size": item.get("size")})
                found_bytes += int(item.get("size") or 0)
            else:
                missing.append({"component_id": component_id, "relative_path": str(relative),
                                "size": item.get("size"), "sha256": item.get("sha256"),
                                "url": item.get("url"), "mirrors": item.get("mirrors") or [],
                                "destination_family": family})
    return {"present": present, "missing": missing, "bytes": found_bytes}


def _receipt_in_place(receipt: dict, roots: list[str]) -> bool:
    """Whether the bytes a receipt read really sit in the served location."""
    wanted = []
    for root in roots:
        if not root:
            continue
        try:
            wanted.append(Path(root).resolve())
        except OSError:
            continue
    if not wanted:
        return False
    # The receipt's ``root`` is the authorization boundary it was read under,
    # not where the bytes are: a candidate verified in the person's own folder
    # carries the installation root there while living somewhere else.  Only
    # the per-file paths (in-place verification) or the package directory say
    # where the bytes really are.
    recorded = [str(row.get("path") or "")
                for row in receipt.get("files") or [] if isinstance(row, dict) and row.get("path")]
    if not recorded:
        recorded = [str(receipt.get("directory") or "")]
    for value in recorded:
        if not value:
            continue
        try:
            path = Path(value).resolve()
        except OSError:
            continue
        if any(path == root or path.is_relative_to(root) for root in wanted):
            return True
    return False


def _verified_components(db, settings, catalog: dict, component_ids,
                        *, served_roots: list[str] | None = None) -> dict[str, set[str]]:
    """Components whose pinned bytes are provably here *now*.

    A package row is a claim about the past: a file that was moved or
    overwritten still carried ``FILES_VERIFIED_PENDING_RUNTIME`` and kept the
    page green.  Only two sources answer "are these bytes here now?" — a
    verification receipt that still matches on disk, and the verified-bytes
    cache — and both are re-read here (by stat when unchanged, by digest when
    not).  This one answer feeds the file facts and the download plan.

    ``in_place`` is the subset whose receipt still reads bytes inside the
    served location: those need no install either.  A component proved only in
    the cache is verified but still has to be placed, which is a local copy and
    never a download.
    """
    from shine.comfyops.verification_receipts import cache_candidates, verify_receipt

    components = _catalog_components(catalog)
    if db is None or settings is None:
        # 没有台账或没有本机配置时，没有任何凭据可读：如实报"未核验"，不猜。
        return {"verified": set(), "in_place": set()}
    cache = Path(settings.comfyops_dir) / "isolated-packages" / ".cache"
    verified: set[str] = set()
    in_place: set[str] = set()
    for component_id in component_ids:
        component = components.get(component_id)
        if not component:
            continue
        try:
            receipt = verify_receipt(
                db, component=component, variant_id=None,
                catalog_id=catalog.get("catalog_id"),
                catalog_version=str(catalog.get("catalog_version") or ""),
                cache_dir=cache)
            if receipt.get("verified"):
                verified.add(component_id)
                if _receipt_in_place(receipt.get("receipt") or {}, served_roots or []):
                    in_place.add(component_id)
                continue
            if cache_candidates(cache, component).get("verified"):
                verified.add(component_id)
        except OSError:  # an unreadable file is not evidence
            continue
    return {"verified": verified, "in_place": in_place}


def _runtime_instance_id(db) -> str | None:
    from shine.models import AppSetting

    row = db.get(AppSetting, "deployment-runtime-active")
    if row is None or not isinstance(row.value, dict):
        return None
    return ((row.value.get("identity") or {}).get("instance_id")) or None


def candidate_installations(db, settings, *, discovered: list[str] | None = None) -> list[dict[str, Any]]:
    """Every installation this machine can use, best first — never ``[0]``.

    Order is an *argument*, not a coin flip: the runtime that is already
    serving, the installation a person confirmed, an adopted one, a managed
    one, and finally directories found on disk.  Each row carries the reason it
    is where it is, so the page can explain the choice instead of silently
    picking the first entry of a scan list.
    """
    rows = _instance_rows(db)
    by_root = {str(Path(row["root"]).resolve()): row for row in rows if row.get("root")}
    chosen = _choice(db)
    active = _runtime_instance_id(db)
    candidates: list[dict[str, Any]] = []

    def add(root: str, *, instance: dict | None, source: str, reason: str) -> None:
        resolved = str(Path(root).resolve())
        if any(row["root"] == resolved for row in candidates):
            return
        candidates.append({
            "root": resolved,
            "instance_id": (instance or {}).get("instance_id"),
            "port": (instance or {}).get("port"),
            "managed": bool((instance or {}).get("managed")),
            "adopted": bool((instance or {}).get("adopted")),
            "status": (instance or {}).get("status"),
            "exists": Path(resolved).is_dir(),
            "source": source,
            "reason": reason,
        })

    if active:
        for row in rows:
            if row["instance_id"] == active:
                add(str(row["root"]), instance=row, source="active_runtime",
                    reason="正在为当前作品提供运行环境")
    if chosen.get("root"):
        add(str(chosen["root"]), instance=by_root.get(str(Path(chosen["root"]).resolve())),
            source="user_choice", reason="你上次确认使用的这套安装")
    for row in rows:
        if row["adopted"]:
            add(str(row["root"]), instance=row, source="adopted",
                reason="已经确认复用的本机 ComfyUI")
    for row in rows:
        if row["managed"]:
            add(str(row["root"]), instance=row, source="managed",
                reason="本工作台自己的受管环境")
    for root in discovered or []:
        add(root, instance=by_root.get(str(Path(root).resolve())), source="discovered",
            reason="在本机找到的 ComfyUI 安装")
    return candidates


def resolve_installation(db, settings, *, path: str | None = None,
                         discovered: list[str] | None = None) -> dict[str, Any]:
    """The installation this machine should use, plus whether a human must pick.

    ``choice_required`` is true only when several *usable* candidates exist and
    none of them outranks the others by evidence.  One candidate, or one that
    is already serving/confirmed, is resolved without asking a second time.
    """
    candidates = candidate_installations(db, settings, discovered=discovered)
    if path:
        resolved = str(Path(path).resolve())
        match = next((row for row in candidates if row["root"] == resolved), None)
        if match is None:
            match = {"root": resolved, "instance_id": None, "port": None, "managed": False,
                     "adopted": False, "status": None, "exists": Path(resolved).is_dir(),
                     "source": "request", "reason": "这次请求指定了这套安装"}
            candidates.insert(0, match)
        return {"selected": match, "candidates": candidates, "choice_required": False}
    if not candidates:
        return {"selected": None, "candidates": [], "choice_required": False}
    top = candidates[0]
    if top["source"] in {"active_runtime", "user_choice", "adopted", "managed"}:
        return {"selected": top, "candidates": candidates, "choice_required": False}
    usable = [row for row in candidates if row["exists"]]
    if len(usable) <= 1:
        return {"selected": usable[0] if usable else None, "candidates": candidates,
                "choice_required": False}
    return {"selected": None, "candidates": candidates, "choice_required": True}


# --- capabilities -----------------------------------------------------------


def _catalog_for(capability: str) -> dict | None:
    try:
        if capability in CATALOG_FILE_BY_CAPABILITY:
            return deployment.load_deployment_catalog(CATALOG_FILE_BY_CAPABILITY[capability])
        return deployment.load_default_catalog()
    except (OSError, ValueError, FileNotFoundError):
        return None


def _component_ids_for(capability: str, catalog: dict) -> set[str]:
    if capability == "image":
        return set(IMAGE_COMPONENTS)
    if capability == "relayvsr":
        return set(RELAYVSR_COMPONENTS)
    if capability in REVIEW_COMPONENTS:
        return {REVIEW_COMPONENTS[capability]}
    components = _catalog_components(catalog)
    return {cid for cid, row in components.items()
            if row.get("kind") == "model" and cid.startswith("minimax-h3-")}


def _image_binding_state(settings) -> dict[str, Any]:
    from shine.comfyops import image_renderer
    from shine.materialprep import runtime

    config = runtime.load_config(settings)
    recipe = image_renderer.image_recipe_state(settings)
    bound = bool(config.base_url and config.instance_id)
    if not bound:
        state = "NOT_BOUND"
    elif recipe["state"] != "READY":
        state = "PENDING_RECIPE"
    elif recipe.get("instance_id") and recipe["instance_id"] != config.instance_id:
        state = "PENDING_RECIPE"
    elif recipe.get("port") and config.port and int(recipe["port"]) != int(config.port):
        state = "PENDING_RECIPE"
    else:
        state = "READY"
    return {
        "binding_state": state,
        "instance_id": config.instance_id,
        "base_url": config.base_url,
        "model_root": config.model_root,
        "port": config.port,
        "recipe": recipe,
    }


def _effective_models_root(settings, capability: str, installation: dict | None) -> str | None:
    """Where this capability's bytes really are, as the runtime already says.

    The image runtime may point at its own ``model_root`` (a managed instance or
    an adopted ComfyUI's ``models`` directory).  Reading that first keeps the
    summary honest: a machine whose weights were prepared through the managed
    runtime must not be told they are missing just because no external
    installation was scanned.
    """
    if capability == "image":
        from shine.materialprep import runtime

        config = runtime.load_config(settings)
        if config.model_root and Path(config.model_root).is_dir():
            return config.model_root
    root = (installation or {}).get("root")
    return str(Path(root) / "models") if root else None


def capability_report(db, settings, capability: str, *, installation: dict | None,
                      project_id: str | None, variant_id: str | None = None,
                      runtime_verified: bool = False) -> dict[str, Any]:
    """One capability, as a ladder of facts that never impersonate each other.

    ``variant_id`` names the H3 stack the person actually chose.  Without it
    the answer describes the default accepted stack, exactly as before; with
    it, both the filesystem facts and the ledger facts follow that variant's
    own catalog — a machine that has the INT8 bytes must not be told the
    candidate it selected is already prepared.
    """
    binding = None
    if capability == "h3" and variant_id:
        from shine.comfyops.variant_binding import load_binding

        binding = load_binding(str(variant_id), fresh=False)
    if binding is not None:
        from shine.comfyops.variant_readiness import _required_components

        catalog = binding.catalog
        component_ids = {str(row.get("component_id"))
                         for row in _required_components(binding.catalog, binding.profiles)}
    else:
        catalog = _catalog_for(capability)
        component_ids = _component_ids_for(capability, catalog) if catalog is not None else set()
    if catalog is None:
        return {
            "key": capability, "label": CAPABILITY_LABELS[capability], "state": "UNKNOWN",
            "optional": capability in ("vlm", "asr", "relayvsr"),
            "facts": {}, "summary": "这个能力的资源清单读不出来。",
            "next_step": {"code": "CATALOG_UNAVAILABLE", "label": "检查安装清单",
                          "reason": "仓库里的固定清单缺失或损坏。", "action": "DIAGNOSE"},
            "missing_files": [], "advisories": [],
        }
    served_root = _effective_models_root(settings, capability, installation)
    presence = model_file_presence(served_root, catalog, component_ids)
    evidence = _verified_components(
        db, settings, catalog, component_ids,
        served_roots=[root for root in (served_root, (installation or {}).get("root")) if root])
    verified_now = evidence["verified"]
    # What still has to be *placed* into this installation.  A file the person
    # renamed is fine — reconcile proved those exact bytes in place, so it is not
    # missing.  A registry row alone is not: once the bytes were moved away the
    # receipt fails and the file is missing again, which is exactly the gap the
    # old shortcut hid.  Bytes that exist only in the verified cache stay in this
    # list (they still need placing) but owe no download.
    missing = [item for item in presence["missing"]
               if item["component_id"] not in evidence["in_place"]]
    cache_root = (Path(settings.comfyops_dir) / "isolated-packages" / ".cache"
                  if settings is not None else None)
    cached_keys = {(component_id, str(row.get("relative_path")))
                   for component_id in component_ids
                   for row in (_catalog_components(catalog).get(component_id) or {}).get("files") or []
                   if cache_root is not None
                   and (cache_root / str(row.get("sha256") or "")).is_file()}
    required = [row for row in (_catalog_components(catalog).get(cid) for cid in component_ids) if row]
    complete_components = {
        cid for cid in component_ids
        if all(item["component_id"] != cid for item in missing)
    }
    files_found = bool(component_ids) and len(complete_components) == len(component_ids)
    files_verified = bool(component_ids) and set(component_ids) <= verified_now
    missing_bytes = sum(int(item.get("size") or 0) for item in missing
                        if (item["component_id"], str(item["relative_path"])) not in cached_keys)

    facts: dict[str, Any] = {
        "FILES_FOUND": files_found,
        "FILES_VERIFIED": files_verified,
        "RESOURCE_REGISTERED": files_verified,
        "INSTANCE_BOUND": False,
        "WORKFLOW_VALIDATED": False,
        "RENDER_VERIFIED": False,
        "PRODUCTION_READY": False,
    }
    advisories: list[dict[str, Any]] = []
    try:
        if capability == "h3":
            from shine.comfyops.variant_binding import load_binding
            from shine.comfyops.variant_readiness import readiness as variant_readiness
            from shine.comfyops.variants import list_variant_manifests

            h3_acceptance: str | None = None
            if binding is None:
                manifest = next((row for row in list_variant_manifests()
                                 if row.get("acceptance_status") in {"SUPPORTED", "EXPERIMENTAL"}), None)
                if manifest:
                    h3_acceptance = str(manifest.get("acceptance_status"))
                    if project_id:
                        binding = load_binding(str(manifest["variant_id"]), fresh=False)
            if binding is not None and project_id:
                report = variant_readiness(db, settings, binding, project_id)
                h3_acceptance = str(report.get("acceptance_status"))
                weights = report.get("weights") or []
                # ``FILES_FOUND`` is never taken from the ledger: its "prepared"
                # bit also requires a package row, so borrowing it made the same
                # machine answer "found" on one read and "missing" on the next.
                # Everything above the filesystem fact comes from the ledger.
                facts.update({
                    "LEDGER_PREPARED": bool(report.get("facts", {}).get("FILES_FOUND")),
                    "FILES_VERIFIED": bool(weights) and all(row.get("verified") for row in weights),
                    "INSTANCE_BOUND": bool(weights) and all(row.get("consumable") for row in weights),
                    "WORKFLOW_VALIDATED": bool(report.get("workflow_validated")),
                    "RENDER_VERIFIED": report.get("real_render") is not None,
                    "PRODUCTION_READY": bool(report.get("production_ready")),
                })
                advisories.extend(report.get("blocking_reasons") or [])
            # 唯一检验链路：机器级运行合同已核对（与顶部裁决同一个 runtime_report）
            # 就是「② 运行」的权威答案，无论是否已绑定到具体作品。只对已验收基线
            # 生效——候选变体不是当前激活的运行配置，绝不能借基线核验冒充就绪。
            facts["RUNTIME_SERVICES_VERIFIED"] = bool(runtime_verified and h3_acceptance == "SUPPORTED")
        elif capability == "image":
            binding = _image_binding_state(settings)
            from shine.materialprep import runtime

            report = runtime.readiness(db, settings, object_info=None)
            facts["BINDING_STATE"] = binding["binding_state"]
            facts["RECIPE_READY"] = binding["recipe"]["state"] == "READY"
            facts["INSTANCE_BOUND"] = binding["binding_state"] == "READY"
            graph_complete = bool(report.get("graph_report") or {}) and bool(
                (report.get("graph_report") or {}).get("complete"))
            # 唯一检验链路：读整备任务落库的真实小样证据（与出图门禁同一份
            # render_verified_families，按指纹键控）。指纹不依赖活体 object_info，
            # 所以空闲渲染器下也能读到已验证事实——一次真实成功出图本身就证明
            # 运行合同在该指纹下跑通过，不必每次切页都重新活体探测一遍。
            persisted_verified = bool(runtime.render_verified_families(
                db, str(report.get("fingerprint") or "")))
            facts["WORKFLOW_VALIDATED"] = graph_complete or persisted_verified
            facts["RENDER_VERIFIED"] = report.get("layer") == "RENDER_VERIFIED" or persisted_verified
            facts["PRODUCTION_READY"] = bool(facts["FILES_VERIFIED"] and facts["INSTANCE_BOUND"]
                                             and facts["WORKFLOW_VALIDATED"])
            advisories.extend(report.get("blockers") or [])
        elif capability == "relayvsr":
            from shine.comfyops import relayvsr

            models_root = _effective_models_root(settings, capability, installation)
            report = relayvsr.relayvsr_readiness(
                model_root=Path(models_root) / "diffusion_models" if models_root else None,
                vae_root=Path(models_root) / "vae" if models_root else None)
            facts["RELAYVSR_AVAILABLE"] = report.get("available", False)
            facts["INSTANCE_BOUND"] = report.get("available", False)
            facts["WORKFLOW_VALIDATED"] = report.get("available", False)
            advisories.extend(report.get("blockers") or [])
        else:
            from shine.comfyops.deployment import advisory_review_readiness

            role = advisory_review_readiness(settings).get(capability.upper()) or {}
            facts["INSTANCE_BOUND"] = role.get("status") == "READY"
            facts["WORKFLOW_VALIDATED"] = role.get("status") == "READY"
    except Exception as exc:  # noqa: BLE001 - an unreadable layer stays unproven
        # The filesystem facts above are still true; only the ledger layers stay
        # unproven, and the page is told which part could not be read.
        advisories.append({"code": "FACT_LAYER_UNAVAILABLE", "detail": type(exc).__name__,
                           "layer": str(capability)})

    cached_files = sum(1 for item in missing
                       if (item["component_id"], str(item["relative_path"])) in cached_keys)
    state, summary, next_step = _capability_verdict(
        capability, facts, missing_bytes=missing_bytes, has_installation=bool(installation),
        missing_files=missing, cached_files=cached_files,
    )
    return {
        "key": capability,
        "label": CAPABILITY_LABELS[capability],
        "catalog": catalog.get("catalog_id"),
        "catalog_file": CATALOG_FILE_BY_CAPABILITY.get(capability, "h3-ref2va-windows-x64"),
        "state": state,
        # 审片是可选增强：没有它照样能真实出片，只是语义验收留给人工。
        # 机器级「下一步」不得把可选能力报成必做。
        "optional": capability in ("vlm", "asr", "relayvsr"),
        "facts": facts,
        "summary": summary,
        "next_step": next_step,
        "missing_files": missing,
        "missing_bytes": missing_bytes,
        "cached_files": cached_files,
        "advisories": advisories,
        "required_components": sorted(component_ids),
        "required_files": sum(len(row.get("files") or []) for row in required),
        "two_step": _two_step_projection(capability, facts, missing_bytes=missing_bytes,
                                         missing_files=missing, advisories=advisories,
                                         cached_files=cached_files),
    }


def _two_step_projection(capability: str, facts: dict, *, missing_bytes: int,
                         missing_files: list, advisories: list,
                         cached_files: int = 0) -> dict[str, Any]:
    """The user-facing model: two questions, not a dozen engineering steps.

    ① 文件齐不齐（缺就去下载）；② 跑不跑得通（不通就给出原因，整备任务自动
    调整）。内部的核验/绑定/登记/核对仍是必要的工程事实，但用户只需读这两行。
    """
    if facts.get("FILES_VERIFIED"):
        files = {"state": "READY", "label": "文件已就绪并核验"}
    elif facts.get("FILES_FOUND"):
        files = {"state": "UNVERIFIED", "label": "文件已找到，待核验"}
    elif cached_files and not missing_bytes:
        # 字节已经在本机缓存里，只差落到实例目录：这不是一次下载。
        files = {
            "state": "MISSING",
            "label": f"还差 {len(missing_files)} 个文件落位（字节已在本机，不需要下载）",
            "missing_count": len(missing_files), "missing_bytes": 0,
        }
    else:
        files = {
            "state": "MISSING",
            "label": f"缺 {len(missing_files)} 个文件（约 {missing_bytes / 1024 ** 3:.1f} GiB）",
            "missing_count": len(missing_files), "missing_bytes": missing_bytes,
        }
    reason = next((str(a.get("message") or a.get("detail")) for a in advisories
                   if a.get("message") or a.get("detail")), None)
    if facts.get("RENDER_VERIFIED") or facts.get("PRODUCTION_READY") or facts.get("RUNTIME_SERVICES_VERIFIED"):
        run = {"state": "READY", "label": "已验证可运行"}
    elif facts.get("WORKFLOW_VALIDATED"):
        run = {"state": "UNVERIFIED", "label": "运行合同已核对，待真实试镜"}
    else:
        if files["state"] == "MISSING":
            reason = "文件未就绪，先补齐文件"
        run = {"state": "BLOCKED", "label": "跑不通",
               "reason": reason or "资源尚未绑定到本机渲染器或运行合同未核对"}
    return {"files": files, "run": run}


def _capability_verdict(capability: str, facts: dict, *, missing_bytes: int,
                        has_installation: bool, missing_files: list,
                        cached_files: int = 0) -> tuple[str, str, dict | None]:
    """The one sentence a person reads, and the single action behind it."""
    if capability == "image" and facts.get("BINDING_STATE") == "PENDING_RECIPE":
        # The dominant fact is not "a file is missing": the person already
        # chose this renderer, the binding is saved and the weights may be
        # verified.  What is absent is the startable recipe, so that is the one
        # action - and a download is explicitly *not* the next step.
        return "FOUND", "已经找到出图资源，正在完善本机运行配置。", {
            "code": "PENDING_RECIPE", "label": "完善本机运行配置",
            "reason": "出图运行时已绑定，本机还缺一份可启动的运行配置。",
            "action": "PREPARE_ENVIRONMENT",
        }
    if not facts.get("FILES_FOUND"):
        if facts.get("FILES_VERIFIED"):
            # Verified bytes already live in a cache the runtime knows about;
            # only the instance path is missing, so a download is not the step.
            return "FOUND", "已经找到出图资源，正在完善本机运行配置。", {
                "code": "PENDING_RECIPE", "label": "完善本机运行配置",
                "reason": "文件已经核验，本机还缺一份可启动的运行配置。", "action": "PREPARE_ENVIRONMENT",
            }
        label = "准备环境" if not has_installation else "补齐缺失文件"
        if not missing_bytes and cached_files:
            reason = "字节已经在本机缓存里，只差落到实例目录；这次不需要下载。"
        else:
            reason = ("本机还没有这套资源（需要下载约 %.1f GiB）；也可以手动下载后放进来。"
                      % (missing_bytes / 1024 ** 3)) if missing_bytes else "本机还没有这套资源。"
        return "MISSING", "本机还没有这套资源。", {
            "code": "FILES_MISSING", "label": label, "reason": reason, "action": "PREPARE_ENVIRONMENT",
        }
    if not facts.get("FILES_VERIFIED"):
        return "FOUND", "已找到资源，正在自动核验并登记。", {
            "code": "FILES_NOT_VERIFIED", "label": "核验已有文件",
            "reason": "文件已在本机，还没有逐字节核验。", "action": "RECONCILE",
        }
    if not (facts.get("INSTANCE_BOUND") or facts.get("RUNTIME_SERVICES_VERIFIED")):
        if capability == "image":
            return "VERIFIED", "已经找到出图资源，正在完善本机运行配置。", {
                "code": "PENDING_RECIPE", "label": "完善本机运行配置",
                "reason": "文件已核验，本机还缺一份可启动的运行配置。", "action": "PREPARE_ENVIRONMENT",
            }
        return "VERIFIED", "资源已核验，还没有绑定到当前 ComfyUI 实例。", {
            "code": "INSTANCE_NOT_BOUND", "label": "绑定已核验的资源",
            "reason": "当前实例的模型目录里读不到已核验的文件。", "action": "RECONCILE",
        }
    if not (facts.get("WORKFLOW_VALIDATED") or facts.get("RUNTIME_SERVICES_VERIFIED")):
        return "BOUND", "资源与实例都已就位，还没核对运行合同。", {
            "code": "WORKFLOW_NOT_VALIDATED", "label": "核对运行合同",
            "reason": "还没有在本机真实服务上核对节点与输入。", "action": "PREPARE_ENVIRONMENT",
        }
    if not facts.get("RENDER_VERIFIED"):
        if capability == "image":
            return "READY", "出图环境已准备好，尚需生成一次验证样片。", {
                "code": "TRIAL_NOT_RUN", "label": "生成一次验证样片",
                "reason": "真实小样会占用显存和几分钟时间。", "action": "TRIAL",
            }
        return "READY", "视频环境已就绪，尚需用这套方案真实出一次片。", {
            "code": "TRIAL_NOT_RUN", "label": "真实试镜一次",
            "reason": "第一次真实出片会占用显存和较长时间。", "action": "TRIAL",
        }
    return "VERIFIED_RENDER", "已经在本机真实生成并留证。", None


# --- summary ----------------------------------------------------------------


def _progress(capabilities: dict[str, dict]) -> list[dict[str, str]]:
    """Five UI phases, projected from facts — never stored, never a gate."""
    relevant = [capabilities[key] for key in ("h3", "image") if key in capabilities]
    facts = [row.get("facts") or {} for row in relevant]
    any_found = any(row.get("FILES_FOUND") for row in facts)
    all_verified = bool(facts) and all(row.get("FILES_VERIFIED") for row in facts)
    all_bound = bool(facts) and all(row.get("INSTANCE_BOUND") for row in facts)
    all_validated = bool(facts) and all(
        row.get("WORKFLOW_VALIDATED") or row.get("RUNTIME_SERVICES_VERIFIED") for row in facts)
    any_render = any(row.get("RENDER_VERIFIED") for row in facts)
    phases = [
        ("DETECT", "检测本机", any_found),
        ("FILES", "文件整备", all_verified),
        ("REGISTER", "自动登记", all_verified),
        ("BIND", "绑定与运行核对", all_bound and all_validated),
        ("TRIAL", "真实试镜", any_render),
    ]
    result, active_set = [], False
    for phase_id, label, done in phases:
        if done:
            state = "DONE"
        elif not active_set:
            state, active_set = "ACTIVE", True
        else:
            state = "PENDING"
        result.append({"id": phase_id, "label": label, "state": state})
    return result


def summary(db, settings, *, project_id: str | None = None,
            discovered: list[str] | None = None,
            variant_id: str | None = None) -> dict[str, Any]:
    """The one read-only snapshot every entry point shares.

    ``variant_id`` is the H3 stack the person chose; the h3 capability then
    answers about that stack's own weights instead of the default one.
    """
    from shine.models import utcnow

    installation = resolve_installation(db, settings, discovered=discovered)
    selected = installation["selected"]
    # 唯一检验链路：顶部裁决、基础配置与高级设置都读同一个 runtime_report。
    # 这里算一次机器级运行核验，喂给 h3 的「② 运行」投影，避免各卡片各自推导
    # 出互相矛盾的结论（裁决说已就绪、卡片却说跑不通）。
    from shine.comfyops.runtime_configuration import runtime_report

    try:
        runtime_verified = bool(runtime_report(db, settings).get("services_verified"))
    except Exception:  # noqa: BLE001 - 读不到的运行配置保持未证明，不冒充已核验
        runtime_verified = False
    capabilities = {
        key: capability_report(db, settings, key, installation=selected, project_id=project_id,
                               variant_id=variant_id if key == "h3" else None,
                               runtime_verified=runtime_verified if key == "h3" else False)
        for key in CAPABILITIES
    }
    progress = _progress(capabilities)
    primary = capabilities["h3"] if (capabilities["h3"]["facts"] or {}).get("FILES_FOUND") else capabilities["image"]
    action_required = _next_action(installation, capabilities, project_id=project_id)
    blockers = sorted({row.get("code") for key, row in capabilities.items()
                       for row in (row.get("advisories") or [])
                       if isinstance(row, dict) and row.get("code")})
    return {
        "snapshot_version": SNAPSHOT_VERSION,
        "computed_at": utcnow().isoformat(),
        "project_id": project_id,
        "variant_id": variant_id,
        "installation": installation,
        "capabilities": capabilities,
        "progress": progress,
        "primary_capability": primary["key"],
        "action_required": action_required,
        "blockers": blockers,
        "preparation": preparation_state(db),
        "manual_download_root": str(Path(settings.comfyops_dir) / "manual-downloads"),
    }


def _next_action(installation: dict, capabilities: dict[str, dict], *,
                 project_id: str | None) -> dict[str, Any] | None:
    if installation.get("choice_required"):
        return {
            "code": "INSTALLATION_SELECTION_REQUIRED",
            "label": "选择要使用的 ComfyUI",
            "reason": "这台电脑上找到不止一套 ComfyUI；选一次之后会记住，不再反复询问。",
            "action": "CHOOSE_INSTALLATION",
            "options": installation.get("candidates") or [],
        }
    if project_id is None:
        # Machine-level preparation does not need a project; only a real shot
        # and a workflow profile do.  Say so instead of blocking the page.
        return {
            "code": "PROJECT_REQUIRED_FOR_TRIAL",
            "label": "建立作品后可以真实试镜",
            "reason": "环境可以现在就准备；真实试镜要绑定到一个作品。",
            "action": "SELECT_PROJECT",
        }
    ordered = [capabilities[key] for key in ("h3", "image", "vlm", "asr", "relayvsr")
               if key in capabilities]
    for row in ordered:
        # 可选增强（审片）各有自己的卡片与下一步，但不占机器级「唯一下一步」：
        # 没有审片模型照样能真实出片，语义验收留给人工。
        if row.get("optional"):
            continue
        step = row.get("next_step")
        if step:
            return {**step, "capability": row["key"]}
    return None


# --- durable preparation task ----------------------------------------------


def preparation_state(db) -> dict[str, Any]:
    from shine.models import AppSetting

    row = db.get(AppSetting, "setup-preparation-task")
    if row is None or not isinstance(row.value, dict):
        return {"status": "IDLE", "task_id": None, "current": None, "steps": [], "message": None}
    value = dict(row.value)
    steps = value.get("steps") or []
    done = len([row for row in steps if row.get("status") == "DONE"])
    return {
        "status": value.get("status") or "IDLE",
        "task_id": value.get("task_id"),
        "current": value.get("current"),
        "steps": steps,
        "done": done,
        "total": len(steps),
        "message": value.get("message"),
        "error": value.get("error"),
        "updated_at": value.get("updated_at"),
    }


# --- missing files ----------------------------------------------------------


def runtime_closure_gap(db, settings, installation: dict | None) -> dict[str, Any]:
    """Read-only bare-machine gap: runtime components and the instance ladder.

    Weights are reported by ``capability_report``; this adds the runtime closure
    (ComfyUI source, wheels, node packs) and a read-only preview of the instance
    ladder, mirroring ``next_instance_plan``'s predicates without creating plans.
    """
    from shine.comfyops import deployment
    from shine.comfyops.existing_installation import resolve_node_pack_dirs
    from shine.comfyops.models import ComfyInstance

    catalog = deployment.load_default_catalog()
    components = deployment._component_map(catalog)
    instance_present = bool(installation and installation.get("instance_id"))
    gap: list[str] = []
    ladder: list[str] = []
    if not instance_present:
        gap.extend(cid for cid, c in components.items()
                   if c["kind"] in {"source", "wheel", "node"})
        ladder = ["CREATE", "INSTALL_NODE", "BIND_MODELS"]
    else:
        root = Path(str(installation.get("root") or ""))
        _dirs, missing = resolve_node_pack_dirs(root, catalog)
        gap.extend(missing)
        instance = db.get(ComfyInstance, installation["instance_id"])
        payload = instance.payload if instance is not None else {}
        expected_nodes = {cid for cid, c in components.items() if c["kind"] == "node"}
        installed = payload.get("node_versions", {})
        if any(cid not in installed for cid in expected_nodes):
            ladder.append("INSTALL_NODE")
        expected_bindings = {c["destination_family"]: cid for cid, c in components.items()
                             if c["kind"] == "model" and c.get("destination_family")}
        if payload.get("model_bindings", {}) != expected_bindings:
            ladder.append("BIND_MODELS")
    return {
        "instance_present": instance_present,
        "missing_components": sorted(set(gap)),
        "instance_ladder": ladder,
    }


def missing_files(db, settings, *, capabilities: list[str] | None = None) -> dict[str, Any]:
    """Exactly the files that are absent, with the fixed source for each."""
    manual_root = Path(settings.comfyops_dir) / "manual-downloads"
    groups = []
    for capability in capabilities or ["h3", "image", "vlm", "asr", "relayvsr"]:
        catalog = _catalog_for(capability)
        if catalog is None:
            continue
        component_ids = _component_ids_for(capability, catalog)
        components = _catalog_components(catalog)
        files = []
        for component_id in sorted(component_ids):
            component = components.get(component_id) or {}
            family = component.get("destination_family") or ""
            for item in component.get("files") or []:
                files.append({
                    "component_id": component_id,
                    "capability": capability,
                    "filename": Path(str(item.get("relative_path") or "")).name,
                    "relative_path": item.get("relative_path"),
                    "size": item.get("size"),
                    "sha256": item.get("sha256"),
                    "url": item.get("url"),
                    "mirrors": item.get("mirrors") or [],
                    "save_to": str(manual_root / catalog.get("catalog_id", "catalog") / component_id
                                   / Path(str(item.get("relative_path") or "")).name),
                    "catalog": catalog.get("catalog_id"),
                    "family": family,
                })
        groups.append({
            "capability": capability,
            "label": CAPABILITY_LABELS[capability],
            "catalog": catalog.get("catalog_id"),
            "catalog_version": catalog.get("catalog_version"),
            "files": files,
            "bytes": sum(int(row.get("size") or 0) for row in files),
        })
    return {
        "manual_download_root": str(manual_root),
        "groups": groups,
        "total_bytes": sum(row["bytes"] for row in groups),
        "note": "把文件按原文件名放进对应目录即可；系统会自己发现、核验并继续安装。",
    }


def instance_root_for(settings, name: str = "shine-h3") -> Path:
    return instance_root(settings, name, "local")


def local_instance_id(port: int) -> str:
    return instance_identity("local", port)
