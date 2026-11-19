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
# One definition of the optional-enhancement component set: deployment.py owns
# it (the install/optional-plan verdicts read the same tuple from there).
RELAYVSR_COMPONENTS = deployment.RELAYVSR_COMPONENTS
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
    from shine.comfyops.existing_installation import comfy_root, local_path
    from shine.models import AppSetting, utcnow
    from shine.services import add_audit

    requested = local_path(root)
    resolved = comfy_root(requested) or requested
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


def model_file_presence(models_root: str | Path | None, catalog: dict, component_ids=None,
                        *, package_root: str | Path | None = None,
                        declared: dict | None = None) -> dict[str, Any]:
    """Cheap, read-only presence check: right path *and* right byte count.

    ``models_root`` is the ComfyUI ``models`` directory (``<root>/models`` for an
    installation, ``image-runtime.json``'s own ``model_root`` for the image
    runtime).  Review models are the exception: the review runtime reads them
    from ``isolated-packages/<component_id>`` (the same root
    :func:`deployment.advisory_review_readiness` reads), so ``package_root``
    names that store and presence follows it — reading the ComfyUI models dir
    for them made one card say 「已核验」 and 「缺少 13 个文件」 at once.
    Size is a filter, never proof - the column is called
    FILES_FOUND for that reason.  Only
    :mod:`shine.comfyops.verification_receipts` may say verified.
    """
    from shine.comfyops.existing_installation import model_sources

    present, missing, found_bytes = [], [], 0
    base = Path(models_root) if models_root else None
    store = Path(package_root) if package_root else None
    components = _catalog_components(catalog)
    for component_id, component in components.items():
        if component_ids is not None and component_id not in component_ids:
            continue
        if component.get("kind") not in {"model", "review-model"}:
            continue
        family = component.get("destination_family")
        review_store = store / component_id if (
            store is not None and component.get("kind") == "review-model") else None
        for item in component.get("files") or []:
            relative = Path(str(item.get("relative_path") or ""))
            target = None
            if review_store is not None and not relative.is_absolute():
                candidate = review_store / relative
                if candidate.is_file():
                    target = candidate
            elif base is not None and family and not relative.is_absolute():
                target = next(iter(model_sources(base, family, relative, declared or {})), None)
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
    return bool(recorded) and all(
        value and any(Path(value).resolve().is_relative_to(root) for root in wanted)
        for value in recorded
    )


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
                cache_dir=cache, read_only=True)
            if receipt.get("verified"):
                verified.add(component_id)
                if _receipt_in_place(receipt.get("receipt") or {}, served_roots or []):
                    in_place.add(component_id)
                continue
            if cache_candidates(cache, component, read_only=True).get("verified"):
                verified.add(component_id)
        except OSError:  # an unreadable file is not evidence
            continue
    return {"verified": verified, "in_place": in_place}


def _runtime_instance_id(db, settings=None) -> str | None:
    from fastapi import HTTPException

    from shine.comfyops.runtime_configuration import ACTIVE_KEY, resolve_runtime
    from shine.models import AppSetting

    row = db.get(AppSetting, ACTIVE_KEY)
    if row is None or not isinstance(row.value, dict):
        return None
    if settings is not None:
        try:
            resolve_runtime(db, settings)
        except HTTPException:
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
    from shine.comfyops.existing_installation import comfy_root, local_path
    from shine.comfyops.resource_discovery import read_state

    rows = [row for row in _instance_rows(db) if row.get("root") and Path(row["root"]).is_dir()]
    chosen = _choice(db)
    active = _runtime_instance_id(db, settings)
    # R5 B8：同一物理 root 的历史多行（旧端口漂移遗留）按确定性偏好收敛为一条：
    # 正在服务的行 > 用户确认端口的行 > 稳定 root 身份行 > 较小 id。绝不依赖
    # 表的遍历顺序，选择不得随重启/切页漂移（INV-04）。
    from shine.comfyops.instances import adopted_instance_identity, confirmation_sort_key

    def root_priority(row: dict[str, Any]) -> tuple[int, str]:
        score = 0
        if active and row.get("instance_id") == active:
            score += 8
        if chosen.get("port") and row.get("port") == chosen.get("port"):
            score += 4
        if row.get("instance_id") == adopted_instance_identity(row["root"]):
            score += 2
        return (score, str(row.get("instance_id") or ""))

    by_root: dict[str, dict[str, Any]] = {}
    for row in rows:
        key = str(Path(row["root"]).resolve())
        current = by_root.get(key)
        if current is None:
            by_root[key] = row
            continue
        left, right = root_priority(row), root_priority(current)
        # 分数高者优先；同分取较小 id，保证与插入顺序无关。
        if left[0] > right[0] or (left[0] == right[0] and left[1] < right[1]):
            by_root[key] = row
    candidates: list[dict[str, Any]] = []

    def add(root: str, *, instance: dict | None, source: str, reason: str) -> None:
        resolved = str(Path(root).resolve())
        if any(row["root"] == resolved for row in candidates):
            return
        candidates.append({
            "root": resolved,
            "instance_id": (instance or {}).get("instance_id"),
            "name": (instance or {}).get("name"),
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
        root = str(Path(chosen["root"]).resolve())
        if Path(root).is_dir():
            add(root, instance=by_root.get(root) or {"port": chosen.get("port")},
                source="user_choice", reason="你上次确认使用的这套安装")
    # 多套已收编安装：最近一次显式确认优先（与端点查找同一排序规则），
    # 用户显式选择（user_choice）仍高于一切自动排序。
    def _confirmed_at(facts: dict[str, Any]) -> str:
        instance = db.get(ComfyInstance, facts["instance_id"]) if facts.get("instance_id") else None
        return confirmation_sort_key(db, instance)

    adopted_rows = [by_root[key] for key in sorted(by_root) if by_root[key]["adopted"]]
    for row in sorted(adopted_rows, key=_confirmed_at, reverse=True):
        add(str(row["root"]), instance=row, source="adopted",
            reason="已经确认复用的本机 ComfyUI")
    for key in sorted(by_root):
        row = by_root[key]
        if row["managed"]:
            add(str(row["root"]), instance=row, source="managed",
                reason="本工作台自己的受管环境")
    indexed = read_state(db)["resources"]["comfy_roots"]
    for path in dict.fromkeys([*(row["root"] for row in indexed), *(discovered or [])]):
        root = comfy_root(local_path(path))
        if root is not None:
            add(str(root), instance=by_root.get(str(root)), source="discovered",
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
        from shine.comfyops.existing_installation import comfy_root, local_path

        requested = local_path(path)
        resolved = str(comfy_root(requested) or requested)
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


def resolve_setup_port(db, settings, installation: dict, requested: int | None) -> int:
    """A reusable installation's directory does not confer ownership of its listener."""
    from urllib.parse import urlsplit

    from fastapi import HTTPException

    from shine.comfyops.host_env import DEFAULT_PORT, _first_free_port, _port_bindable

    if installation.get("instance_id"):
        return int(installation["port"])
    port = requested or installation.get("port") or urlsplit(settings.comfyui_base_url).port or DEFAULT_PORT
    reserved = {row["port"] for row in _instance_rows(db) if row.get("port")}
    if port not in reserved and _port_bindable(port):
        return port
    available = _first_free_port(port, owned_ports=reserved)
    if available is None:
        raise HTTPException(409, "PORT_OCCUPIED")
    return available


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
    from shine.comfyops.variant_readiness import required_h3_components

    return {row["component_id"] for row in required_h3_components(catalog)}


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
    if capability == "image" and not installation:
        from shine.materialprep import runtime

        config = runtime.load_config(settings)
        if config.model_root and Path(config.model_root).is_dir():
            return config.model_root
    root = (installation or {}).get("root")
    return str(Path(root) / "models") if root else None


_RELAYVSR_INSTALLATION_UNSET = object()


def relayvsr_machine_readiness(db, settings=None, *, installation=_RELAYVSR_INSTALLATION_UNSET) -> dict:
    """The one RelayVSR readiness verdict every surface reads.

    The capability card, the strategy option matrix and the production gates
    all hang off this single reader: a machine whose weights are verified and
    placed must not be told "not ready" by one surface and "ready" by another.
    ``settings`` only sharpens the installation ordering (active runtime first);
    the resolution itself is the same single function with or without it.
    An explicit ``installation=None`` means "no installation known" and stays
    unproven; only an omitted argument resolves the machine's installation.
    """
    from shine.comfyops import relayvsr

    if installation is _RELAYVSR_INSTALLATION_UNSET:
        installation = resolve_installation(db, settings)["selected"]
    models_root = _effective_models_root(settings, "relayvsr", installation)
    return relayvsr.relayvsr_readiness(
        model_root=Path(models_root) / "diffusion_models" if models_root else None,
        vae_root=Path(models_root) / "vae" if models_root else None)


def capability_report(db, settings, capability: str, *, installation: dict | None,
                      project_id: str | None, variant_id: str | None = None,
                      runtime_verified: bool = False, preparing: bool = False) -> dict[str, Any]:
    """One capability, as a ladder of facts that never impersonate each other.

    ``variant_id`` names the H3 stack the person actually chose.  Without it
    the answer describes the default accepted stack, exactly as before; with
    it, both the filesystem facts and the ledger facts follow that variant's
    own catalog — a machine that has the INT8 bytes must not be told the
    candidate it selected is already prepared.
    """
    binding = None
    if capability == "h3":
        from shine.comfyops.variant_binding import load_binding
        from shine.comfyops.variants import list_variant_manifests

        if variant_id:
            binding = load_binding(str(variant_id), fresh=False)
        elif project_id:
            # 缺件表与作品的基线合同读取同一组真正加载的组件。
            try:
                manifest = next((row for row in list_variant_manifests()
                                 if row.get("acceptance_status") in {"SUPPORTED", "EXPERIMENTAL"}), None)
                if manifest:
                    binding = load_binding(str(manifest["variant_id"]), fresh=False)
            except Exception:  # noqa: BLE001 - 读不到的基线回退默认清单，台账层另行具名
                binding = None
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
    # 审片模型的消费根是隔离包目录（与 advisory_review_readiness 同一根），
    # 不是 ComfyUI 的 models 目录；presence 与 in_place 都跟着它走。
    package_root = (Path(settings.comfyops_dir) / "isolated-packages"
                    if settings is not None and capability in REVIEW_COMPONENTS else None)
    from shine.comfyops.adoption import runtime_root
    from shine.comfyops.existing_installation import declared_model_family_roots

    extra = (runtime_root(settings, Path(installation["root"]), installation["instance_id"])
             / "shine-node-paths.yaml" if installation and installation.get("adopted") else None)
    declared = (declared_model_family_roots(Path(installation["root"]), extra_config=extra)
                if installation and installation.get("root") and capability in {"h3", "image"} else {})
    presence = model_file_presence(served_root, catalog, component_ids, package_root=package_root,
                                   declared=declared)
    served_roots = [root for root in (served_root,) if root]
    served_roots.extend(str(path) for paths in declared.values() for path in paths)
    if package_root is not None:
        served_roots.extend(str(package_root / cid) for cid in sorted(component_ids))
    evidence = _verified_components(
        db, settings, catalog, component_ids, served_roots=served_roots)
    verified_now = evidence["verified"]
    # One classification per file, shared across discovery, summary and prepare-plan.
    # Unverified SHA-named cache bytes without a valid sidecar receipt never count
    # as owned bytes, and unfound files under an incomplete/truncated/depth-limited
    # scan stay UNKNOWN instead of claiming MISSING.
    unknown_keys: set[tuple[str, str]] = set()
    if db is not None and capability in {"h3", "image"}:
        from shine.comfyops import resource_discovery

        discovery = resource_discovery.report(db)
        if resource_discovery.coverage_gaps_present(discovery):
            bucket = (discovery.get("capabilities") or {}).get(capability) or {}
            unknown_keys = {
                (str(row.get("component_id")), Path(str(row.get("relative_path") or "")).as_posix())
                for row in (bucket.get("unknown") or [])
            }
    unplaced = [item for item in presence["missing"]
                if item["component_id"] not in evidence["in_place"]]
    raw_missing = [item for item in unplaced
                   if item["component_id"] not in verified_now]
    missing = [item for item in raw_missing
               if (item["component_id"], Path(str(item["relative_path"])).as_posix()) not in unknown_keys]
    unknown_files = [item for item in raw_missing
                     if (item["component_id"], Path(str(item["relative_path"])).as_posix()) in unknown_keys]
    required = [row for row in (_catalog_components(catalog).get(cid) for cid in component_ids) if row]
    complete_components = {
        cid for cid in component_ids
        if all(item["component_id"] != cid for item in missing)
        and all(item["component_id"] != cid for item in unknown_files)
    }
    files_found = bool(component_ids) and len(complete_components) == len(component_ids)
    files_verified = bool(component_ids) and set(component_ids) <= verified_now
    missing_bytes = sum(int(item.get("size") or 0) for item in missing)
    unknown_bytes = sum(int(item.get("size") or 0) for item in unknown_files)

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
                    binding = load_binding(str(manifest["variant_id"]), fresh=False)
            if binding is not None:
                report = variant_readiness(db, settings, binding, project_id, include_host=False)
                h3_acceptance = str(report.get("acceptance_status"))
                weights = report.get("weights") or []
                # ``FILES_FOUND`` is never taken from the ledger: its "prepared"
                # bit also requires a package row, so borrowing it made the same
                # machine answer "found" on one read and "missing" on the next.
                # Everything above the filesystem fact comes from the ledger.
                facts.update({
                    "LEDGER_PREPARED": bool(report.get("facts", {}).get("FILES_FOUND")),
                    "INSTANCE_BOUND": bool(weights) and all(row.get("consumable") for row in weights),
                    "WORKFLOW_VALIDATED": bool(report.get("workflow_validated")),
                    "RENDER_VERIFIED": report.get("real_render") is not None,
                    "PRODUCTION_READY": bool(report.get("production_ready")),
                })
                advisories.extend(report.get("blocking_reasons") or [])
                # ② 的原因挂变体就绪自己的唯一下一步（它已算好），不在卡片里
                # 另写一句通用推断：已核验未绑定时人要读到「绑定」而不是「缺件」。
                step = report.get("next_step") or {}
                if not runtime_verified and not report.get("workflow_validated") and step.get("reason"):
                    advisories.append({"code": step["code"], "message": step["reason"]})
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
            report = relayvsr_machine_readiness(db, settings, installation=installation)
            available = bool(report.get("available", False))
            facts["RELAYVSR_AVAILABLE"] = available
            facts["INSTANCE_BOUND"] = available
            facts["WORKFLOW_VALIDATED"] = available
            facts["PRODUCTION_READY"] = bool(facts["FILES_VERIFIED"] and available)
            advisories.extend(report.get("blockers") or [])
        else:
            from shine.comfyops.deployment import advisory_review_readiness

            role = advisory_review_readiness(settings).get(capability.upper()) or {}
            role_ready = role.get("status") == "READY"
            facts["INSTANCE_BOUND"] = role_ready
            facts["WORKFLOW_VALIDATED"] = role_ready
            facts["PRODUCTION_READY"] = bool(facts["FILES_VERIFIED"] and role_ready)
            if role.get("status") != "READY" and role.get("code"):
                # ② 的原因读同一份审片就绪裁决，不在卡片里另写一套推断。
                runtime_missing = role.get("runtime_packages_missing") or []
                advisories.append({
                    "code": role["code"],
                    "message": (f"审片资源未就绪：模型文件 "
                                f"{role.get('model_files_present', 0)}/{role.get('model_files_expected', 0)}"
                                + (f"，运行时包缺 {'、'.join(runtime_missing)}" if runtime_missing else "")),
                })
    except Exception as exc:  # noqa: BLE001 - an unreadable layer stays unproven
        # The filesystem facts above are still true; only the ledger layers stay
        # unproven, and the page is told which part could not be read.
        advisories.append({"code": "FACT_LAYER_UNAVAILABLE", "detail": type(exc).__name__,
                           "layer": str(capability)})

    state, summary, next_step = _capability_verdict(
        capability, facts, missing_bytes=missing_bytes, has_installation=bool(installation),
        missing_files=missing, unknown_files=unknown_files,
    )
    return {
        "key": capability,
        "label": CAPABILITY_LABELS[capability],
        "catalog": catalog.get("catalog_id"),
        "catalog_file": (binding.manifest.get("catalog_id") if capability == "h3" and binding else None)
        or CATALOG_FILE_BY_CAPABILITY.get(capability, H3_CATALOG),
        "state": state,
        # 审片是可选增强：没有它照样能真实出片，只是语义验收留给人工。
        # 机器级「下一步」不得把可选能力报成必做。
        "optional": capability in ("vlm", "asr", "relayvsr"),
        "facts": facts,
        "summary": summary,
        "next_step": next_step,
        "missing_files": missing,
        "missing_bytes": missing_bytes,
        "unknown_files": unknown_files,
        "unknown_bytes": unknown_bytes,
        "unplaced_files": unplaced,
        "advisories": advisories,
        "required_components": sorted(component_ids),
        "required_files": sum(len(row.get("files") or []) for row in required),
        "two_step": _two_step_projection(capability, facts, missing_bytes=missing_bytes,
                                         missing_files=missing, advisories=advisories,
                                         unknown_files=unknown_files,
                                         unknown_bytes=unknown_bytes, preparing=preparing),
    }


def _two_step_projection(capability: str, facts: dict, *, missing_bytes: int,
                         missing_files: list, advisories: list,
                         unknown_files: list | None = None,
                         unknown_bytes: int = 0, preparing: bool = False) -> dict[str, Any]:
    """The user-facing model: two questions, not a dozen engineering steps.

    ① 文件齐不齐（缺就去下载）；② 跑不跑得通（不通就给出原因，整备任务自动
    调整）。内部的核验/绑定/登记/核对仍是必要的工程事实，但用户只需读这两行。
    「缺」只指本机真的没有的字节（``missing_files``）；已核验但尚未落位的字节
    属于②的绑定/整备，不在①报缺，否则同一张卡片会给出两个互相矛盾的裁决。
    """
    unknown_list = unknown_files or []
    if facts.get("FILES_VERIFIED"):
        files = {"state": "READY", "label": "文件已就绪并核验"}
    elif facts.get("FILES_FOUND"):
        files = {"state": "UNVERIFIED", "label": "文件已找到，待核验"}
    elif unknown_list and not missing_files:
        files = {
            "state": "UNKNOWN",
            "label": f"本机检索未完成（{len(unknown_list)} 个文件待确认）",
            "unknown_count": len(unknown_list),
            "unknown_bytes": unknown_bytes,
            "missing_count": 0,
            "missing_bytes": 0,
        }
    else:
        files = {
            "state": "MISSING",
            "label": f"缺 {len(missing_files)} 个文件（约 {missing_bytes / 1024 ** 3:.1f} GiB）",
            "missing_count": len(missing_files), "missing_bytes": missing_bytes,
            **({"unknown_count": len(unknown_list), "unknown_bytes": unknown_bytes} if unknown_list else {}),
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
    if preparing and run["state"] != "READY":
        # The single preparation task is actively working on this capability:
        # the honest projection is "in progress", not a dead-end "cannot run"
        # with a button that only re-queues the same task.  The fact read is
        # the same preparation ledger the progress panel renders.
        run = {"state": "PREPARING", "label": "后台整备中，完成后自动可用"}
    return {"files": files, "run": run}


def _capability_verdict(capability: str, facts: dict, *, missing_bytes: int,
                        has_installation: bool, missing_files: list,
                        unknown_files: list | None = None) -> tuple[str, str, dict | None]:
    """The one sentence a person reads, and the single action behind it."""
    if capability == "image" and facts.get("BINDING_STATE") == "PENDING_RECIPE":
        # The dominant fact is not "a file is missing": the person already
        # chose this renderer, the binding is saved and the weights may be
        # verified.  What is absent is the startable recipe, so that is the one
        # action - and a download is explicitly *not* the next step.
        return "FOUND", "已经找到出图资源，尚需完善本机运行配置。", {
            "code": "PENDING_RECIPE", "label": "完善本机运行配置",
            "reason": "出图运行时已绑定，本机还缺一份可启动的运行配置。",
            "action": "PREPARE_ENVIRONMENT",
        }
    if not facts.get("FILES_FOUND"):
        if unknown_files and not missing_files:
            return "UNKNOWN", "本机资源检索未完成，暂无法断言缺件。", {
                "code": "SCAN_INCOMPLETE", "label": "完成本机检索",
                "reason": "本机检索尚未完成或部分目录受限，请等待或完成扫描后再决定是否下载。",
                "action": "SCAN",
            }
        label = "准备环境" if not has_installation else "补齐缺失文件"
        reason = ("本机还没有这套资源（需要下载约 %.1f GiB）；也可以手动下载后放进来。"
                  % (missing_bytes / 1024 ** 3)) if missing_bytes else "本机还没有这套资源。"
        return "MISSING", "本机还没有这套资源。", {
            "code": "FILES_MISSING", "label": label, "reason": reason, "action": "PREPARE_ENVIRONMENT",
        }
    if not facts.get("FILES_VERIFIED"):
        return "FOUND", "已找到资源，尚未核验并登记。", {
            "code": "FILES_NOT_VERIFIED", "label": "核验已有文件",
            "reason": "文件已在本机，还没有逐字节核验。", "action": "RECONCILE",
        }
    if not (facts.get("INSTANCE_BOUND") or facts.get("RUNTIME_SERVICES_VERIFIED")):
        if capability == "image":
            return "VERIFIED", "已经找到出图资源，尚需完善本机运行配置。", {
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
        if capability == "relayvsr":
            return "READY", "精修权重已就位，可在作品「⑦ 后期成片」直接使用。", None
        if capability in {"vlm", "asr"}:
            return "READY", "审片资源已就绪，将在出片质检时自动提供建议。", None
        if capability == "image":
            return "READY", "出图环境已准备好，尚需生成一次验证样片。", {
                "code": "TRIAL_NOT_RUN", "label": "生成一次验证样片",
                "reason": "真实小样会占用显存和几分钟时间。", "action": "TRIAL",
            }
        return "READY", "视频制作环境已就绪，可在作品「⑥ 视频生产」正式出片。", {
            "code": "TRIAL_NOT_RUN", "label": "去作品正式出片",
            "reason": "运行合同已核对，首次真实出片在作品「⑥ 视频生产」进行。", "action": "TRIAL",
        }
    return "VERIFIED_RENDER", "已经在本机真实生成并留证。", None


# --- summary ----------------------------------------------------------------


def _progress(capabilities: dict[str, dict]) -> list[dict[str, str]]:
    """Five UI phases, projected from facts — never stored, never a gate."""
    relevant = [capabilities[key] for key in ("h3", "image") if key in capabilities]
    facts = [row.get("facts") or {} for row in relevant]
    any_found = any(row.get("FILES_FOUND") for row in facts)
    all_verified = bool(facts) and all(row.get("FILES_VERIFIED") for row in facts)
    all_validated = bool(facts) and all(
        row.get("RUNTIME_SERVICES_VERIFIED")
        or (row.get("INSTANCE_BOUND") and row.get("WORKFLOW_VALIDATED")) for row in facts)
    all_rendered = bool(facts) and all(row.get("RENDER_VERIFIED") for row in facts)
    phases = [
        ("DETECT", "检测本机", any_found),
        ("FILES", "文件整备", all_verified),
        ("REGISTER", "自动登记", all_verified),
        ("BIND", "绑定与运行核对", all_validated),
        ("TRIAL", "真实试镜", all_rendered),
    ]
    result, active_set = [], False
    for phase_id, label, done in phases:
        if done and not active_set:
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
        report = runtime_report(db, settings)
    except Exception:  # noqa: BLE001 - 读不到的运行配置保持未证明，不冒充已核验
        report = {}
    runtime_verified = bool(report.get("services_verified") and selected
                            and selected.get("instance_id") == _runtime_instance_id(db, settings))
    preparation = preparation_state(db)
    preparing_caps = (set(preparation.get("capabilities") or [])
                      if preparation.get("status") in {"QUEUED", "RUNNING"} else set())
    capabilities = {
        key: capability_report(db, settings, key, installation=selected, project_id=project_id,
                               variant_id=variant_id if key == "h3" else None,
                               runtime_verified=runtime_verified if key == "h3" else False,
                               preparing=key in preparing_caps)
        for key in CAPABILITIES
    }
    progress = _progress(capabilities)
    primary = capabilities["h3"] if (capabilities["h3"]["facts"] or {}).get("FILES_FOUND") else capabilities["image"]
    action_required = _next_action(installation, capabilities, project_id=project_id)
    blockers = sorted({row.get("code") for key, row in capabilities.items()
                       for row in (row.get("advisories") or [])
                       if isinstance(row, dict) and row.get("code")})
    from shine.production_mode import machine_verdict

    verdict = machine_verdict(db, settings, report=report or None)
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
        "preparation": preparation,
        "verdict": verdict,
        "machine_progress": machine_progress(db, progress=progress, verdict=verdict),
        "manual_download_root": str(Path(settings.comfyops_dir) / "manual-downloads"),
    }


def _scan_fraction(db) -> float:
    from shine.comfyops import resource_discovery

    scan = resource_discovery.read_state(db).get("scan") or {}
    coverage = scan.get("coverage") or {}
    total = len(coverage) + len(scan.get("queue") or [])
    if not total:
        return 0.0
    done = len([row for row in coverage.values() if row.get("status") == "DONE"])
    return min(1.0, done / total)


def _active_install_attempt(db):
    from shine.comfyops.models import InstallationAttempt

    return db.scalars(select(InstallationAttempt).where(
        InstallationAttempt.status.in_(["QUEUED", "SUBMIT_INTENT", "INSTALLING"]))).first()


def _download_fraction(db) -> float:
    attempt = _active_install_attempt(db)
    if attempt is None or not attempt.total_bytes:
        return 0.0
    return min(1.0, (attempt.confirmed_bytes or 0) / attempt.total_bytes)


def _bytes_label(n: int) -> str:
    if n >= 1024 ** 3:
        return f"{n / 1024 ** 3:.2f} GiB"
    if n >= 1024 ** 2:
        return f"{n / 1024 ** 2:.1f} MiB"
    return f"{n / 1024:.0f} KiB"


def _download_detail(db, prefix: str) -> str:
    """Live bytes/speed so a hours-long download still visibly moves."""
    attempt = _active_install_attempt(db)
    if attempt is None or not attempt.total_bytes:
        return prefix
    text = f"{prefix} · 已下载 {_bytes_label(attempt.confirmed_bytes or 0)} / {_bytes_label(attempt.total_bytes)}"
    if attempt.speed_bps:
        text += f" · {_bytes_label(attempt.speed_bps)}/s"
    return text


def machine_progress(db, *, progress: list[dict], verdict: dict) -> dict[str, Any]:
    """The top bar's single projection of "what is this machine doing now".

    Only existing ledgers are read: the preparation task, the resource scan and
    the installation attempts.  No new state is introduced and no second
    scheduler is implied.  100% is reserved for the one production verdict
    saying READY; while work moves, the percent is that work's own progress
    and the label names the step, so the bar never looks dead and never claims
    readiness it has not proven.
    """
    from shine.comfyops import resource_discovery

    phases = progress or []
    done_phases = len([row for row in phases if row.get("state") == "DONE"])
    ready_fraction = round(100 * done_phases / len(phases)) if phases else 0
    idle_percent = 100 if verdict.get("mode") in {"READY", "RUNNING"} else min(99, ready_fraction)

    prep = preparation_state(db)
    if prep.get("status") in {"QUEUED", "RUNNING"}:
        steps = prep.get("steps") or []
        total = len(steps) or 1
        done = prep.get("done") or 0
        current = prep.get("current")
        fraction = 0.0
        if current == "DOWNLOAD":
            fraction = _download_fraction(db)
        elif current == "SCAN":
            fraction = _scan_fraction(db)
        label = next((row.get("label") for row in steps if row.get("id") == current), None) or "后台整备"
        detail = prep.get("message") or "关闭页面也会继续"
        if current == "DOWNLOAD":
            detail = _download_detail(db, detail)
        return {"active": True, "percent": min(99, round(100 * (done + fraction) / total)),
                "label": label, "detail": detail,
                "source": "preparation"}
    scan = resource_discovery.read_state(db).get("scan") or {}
    if scan.get("status") in {"QUEUED", "RUNNING"}:
        return {"active": True, "percent": min(99, round(100 * _scan_fraction(db))),
                "label": "深搜本机已有资源", "detail": scan.get("message") or "检索进行中（关闭页面也会继续）",
                "source": "scan"}
    attempt = _active_install_attempt(db)
    if attempt is not None:
        return {"active": True, "percent": min(99, round(100 * _download_fraction(db))),
                "label": f"下载并安装 {attempt.current_component or '缺失文件'}",
                "detail": _download_detail(db, f"{attempt.current_file or '准备中'} · 关闭页面也会继续"),
                "source": "download"}
    return {"active": False, "percent": idle_percent,
            "label": verdict.get("label") or "制作环境",
            "detail": verdict.get("detail") or "", "source": "idle"}


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
    ordered = [capabilities[key] for key in ("h3", "image", "vlm", "asr", "relayvsr")
               if key in capabilities]
    trial = None
    for row in ordered:
        # 可选增强（审片）各有自己的卡片与下一步，但不占机器级「唯一下一步」：
        # 没有审片模型照样能真实出片，语义验收留给人工。
        if row.get("optional"):
            continue
        step = row.get("next_step")
        if step:
            if step["action"] == "TRIAL":
                # H3 真实出片属于作品「⑥ 视频生产」（强制 Ref2VA），不在基础配置页
                # 挂一个重跑整备的伪试镜按钮；只有素材出图的验证小样属于整备任务步骤。
                if row["key"] == "h3":
                    continue
                trial = trial or {**step, "capability": row["key"]}
                continue
            return {**step, "capability": row["key"]}
    if trial and not project_id:
        return {"code": "PROJECT_REQUIRED_FOR_TRIAL", "label": "建立作品后可以真实试镜",
                "reason": "运行合同已核对，建立作品后即可开始试镜。", "action": "SELECT_PROJECT"}
    return trial


# --- durable preparation task ----------------------------------------------


def preparation_state(db) -> dict[str, Any]:
    from shine.models import AppSetting

    row = db.get(AppSetting, "setup-preparation-task")
    if row is None or not isinstance(row.value, dict):
        return {"status": "IDLE", "task_id": None, "current": None, "steps": [], "message": None,
                "capabilities": []}
    value = dict(row.value)
    steps = value.get("steps") or []
    done = len([row for row in steps if row.get("status") in {"DONE", "SKIPPED"}])
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
        "capabilities": sorted((value.get("request") or {}).get("capabilities") or []),
    }


# --- missing files ----------------------------------------------------------


def runtime_closure_gap(db, settings, installation: dict | None,
                        *, capabilities: list[str] | None = None) -> dict[str, Any]:
    """Read-only bare-machine gap: runtime components and the instance ladder.

    Weights are reported by ``capability_report``; this adds the runtime closure
    (ComfyUI source, wheels, node packs when H3 is included) and a read-only
    preview of the instance ladder, mirroring ``next_instance_plan``'s
    predicates without creating plans.
    """
    from shine.comfyops import deployment
    from shine.comfyops.existing_installation import resolve_node_pack_dirs
    from shine.comfyops.models import ComfyInstance

    catalog = deployment.load_default_catalog()
    components = deployment._component_map(catalog)
    include_h3 = capabilities is None or "h3" in capabilities
    instance_present = bool(installation and installation.get("instance_id"))
    gap: list[str] = []
    ladder: list[str] = []
    if installation and installation.get("root"):
        root = Path(installation["root"])
        if include_h3:
            _dirs, missing = resolve_node_pack_dirs(root, catalog)
            gap.extend(missing)
        if not instance_present:
            ladder = ["ADOPT"]
    elif not instance_present:
        if include_h3:
            gap.extend(cid for cid, c in components.items()
                       if c["kind"] in {"source", "wheel", "node"})
            ladder = ["CREATE", "INSTALL_NODE", "BIND_MODELS"]
        else:
            gap.extend(cid for cid, c in components.items()
                       if c["kind"] in {"source", "wheel"})
            ladder = ["CREATE", "BIND_MODELS"]
    if instance_present and not installation.get("adopted"):
        instance = db.get(ComfyInstance, installation["instance_id"])
        payload = instance.payload if instance is not None else {}
        if include_h3:
            expected_nodes = {cid for cid, c in components.items() if c["kind"] == "node"}
            installed = payload.get("node_versions", {})
            if any(cid not in installed for cid in expected_nodes):
                ladder.append("INSTALL_NODE")
            expected_bindings = {c["destination_family"]: cid for cid, c in components.items()
                                 if c["kind"] == "model" and c.get("destination_family")}
        else:
            img_catalog = deployment.load_deployment_catalog(deployment.IMAGE_CAPABILITY_CATALOG)
            expected_bindings = {c["destination_family"]: c["component_id"]
                                 for c in img_catalog.get("components", [])
                                 if c.get("kind") == "model" and c.get("destination_family")}
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
