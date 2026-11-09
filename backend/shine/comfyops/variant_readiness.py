"""The layered readiness of one variant on this machine.

Five layers, in the order they have to be reached:

1. ``WEIGHTS_PENDING``       待准备权重        - the pinned bytes are on this machine
2. ``FILES_VERIFIED``        文件已核验        - hash-verified against the pinned revision
3. ``WORKFLOW_REGISTERED``   工作流已登记      - the variant's profile belongs to *this* project
4. ``RUNTIME_VALIDATED``     本机运行环境已验证 - the graph was checked against the real renderer
5. ``REAL_RENDER_VERIFIED``  真实出片已验证    - a real (non-mock) shot was produced with it

The report states exactly **one** next step: the first layer that is not
reached yet, with a readable reason.  A layer that cannot be reached on this
machine is ``BLOCKED``; it is never silently skipped, and a layer that was
never measured is never reported as done.

The evidence is read from the product's own ledger and from the bytes on
disk.  It never claims more than it checked: a mock drill or a status flag
without the matching bytes proves nothing.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from shine.comfyops.models import ModelPackage
from shine.models import Job, WorkflowProfile

#: The layers, in order.  The order is the dependency order, not a preference.
LAYERS: tuple[tuple[str, str], ...] = (
    ("WEIGHTS_PENDING", "待准备权重"),
    ("FILES_VERIFIED", "文件已核验"),
    ("WORKFLOW_REGISTERED", "工作流已登记"),
    ("RUNTIME_VALIDATED", "本机运行环境已验证"),
    ("REAL_RENDER_VERIFIED", "真实出片已验证"),
)

LAYER_IDS = tuple(layer for layer, _label in LAYERS)
LAYER_LABELS = dict(LAYERS)

#: A package status that proves the bytes were hash-verified.  A mock drill
#: (``MOCK_AVAILABLE``) and an unverified origin (``UNVERIFIED_ORIGIN``) do
#: not prove anything about the files.
VERIFIED_PACKAGE_STATUSES = {"FILES_VERIFIED_PENDING_RUNTIME", "INSTALLED", "READY"}


def _model_components(catalog: dict[str, Any]) -> list[dict[str, Any]]:
    return [c for c in (catalog.get("components") or [])
            if c.get("kind") in {"model", "review-model"} and c.get("status") == "READY"]


#: The graph inputs that name a weight file on disk.
MODEL_INPUTS = ("unet_name", "clip_name", "vae_name", "model_name", "lora_name", "model_path")


def _file_name(value: str) -> str:
    return str(value or "").replace("\\", "/").split("/")[-1].strip().lower()


def _graph_model_files(graph: dict[str, Any]) -> set[str]:
    """The weight files a graph really loads."""
    names: set[str] = set()
    for node in (graph or {}).values():
        inputs = node.get("inputs") or {}
        for key in MODEL_INPUTS:
            value = inputs.get(key)
            if isinstance(value, str) and value:
                names.add(_file_name(value))
    return names


def _required_components(catalog: dict[str, Any], profiles: tuple[dict[str, Any], ...]) -> list[dict[str, Any]]:
    """The model components the variant's own profiles really load.

    A catalog may carry files a profile does not use - the optional turbo
    LoRA, for example.  Requiring those would block a stack that is otherwise
    complete, so the requirement follows the graph.
    """
    needed: set[str] = set()
    for profile in profiles:
        needed |= _graph_model_files(profile.get("graph") or {})
    components = _model_components(catalog)
    if not needed:
        return components
    required = []
    for component in components:
        files = component.get("files") or []
        if any(_file_name(str(entry.get("relative_path") or "")) in needed for entry in files):
            required.append(component)
    return required


def _cache_dir(settings) -> Any:
    """The verified-bytes cache of the isolated package store, when there is one."""
    if settings is None:
        return None
    try:
        from shine.comfyops.storage import require_storage

        return require_storage(settings) / "isolated-packages" / ".cache"
    except Exception:  # noqa: BLE001 - no storage configured is a fact, not a failure
        return None


def managed_model_bindings(db: Session, settings) -> dict[str, str]:
    """The model directories the *current* managed renderer consumes.

    The relation is read from the instance's own extra_model_paths.yaml, so a
    managed instance that was switched is answered from its new binding
    without reading a single weight byte again.  An instance that cannot be
    read simply has no binding: that is a fact, not a failure.
    """
    if settings is None:
        return {}
    try:
        from shine.comfyops.runtime_configuration import _current_identity

        identity = _current_identity(db, settings) or {}
    except Exception:  # noqa: BLE001 - an unreadable runtime is a fact
        return {}
    instance_id = identity.get("instance_id")
    if not instance_id:
        return {}
    try:
        from shine.comfyops.models import ComfyInstance

        instance = db.get(ComfyInstance, instance_id)
    except Exception:  # noqa: BLE001 - no instance row is a fact
        return {}
    root = (instance.payload or {}).get("root") if instance is not None else None
    if not root:
        return {}
    try:
        raw = json.loads((Path(root) / "extra_model_paths.yaml").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    bindings = raw.get("shine") if isinstance(raw, dict) else None
    if not isinstance(bindings, dict):
        return {}
    return {str(key): str(value) for key, value in bindings.items()}


def _same_directory(left: str | None, right: str | None) -> bool:
    if not left or not right:
        return False
    try:
        return Path(left).resolve() == Path(right).resolve()
    except OSError:
        return False


def weight_evidence(db: Session, settings, binding) -> list[dict[str, Any]]:
    """Per component: are the pinned bytes here, and were they really verified?

    Two sources are accepted, and both are evidence about *bytes*:

    * a durable verification receipt that binds the exact revision, every
      file length/SHA and the directory that was read, still matching on disk;
    * the verified-bytes cache, whose file is read (or hashed once and then
      reused while its size/mtime stay unchanged) - a digest-shaped file name
      is never accepted on its own.

    A registry row that says ``FILES_VERIFIED_PENDING_RUNTIME`` is a historical
    summary.  It cannot turn a layer green when the current revision, the file
    list or the bytes differ (J04).
    """
    from shine.comfyops.verification_receipts import (
        cache_candidates,
        installed_directory,
        verify_receipt,
    )

    cache = _cache_dir(settings)
    bound = managed_model_bindings(db, settings)
    registry = {row.id: row for row in db.scalars(select(ModelPackage)).all()}
    evidence: list[dict[str, Any]] = []
    for component in _required_components(binding.catalog, binding.profiles):
        component_id = str(component.get("component_id"))
        receipt = verify_receipt(
            db,
            component=component,
            variant_id=binding.variant_id,
            catalog_id=binding.catalog_id,
            catalog_version=str((binding.catalog or {}).get("catalog_version") or ""),
            cache_dir=cache,
        )
        cached = cache_candidates(cache, component)
        files = [
            {
                "relative_path": entry.get("relative_path"),
                "sha256": str(entry.get("sha256")),
                "size": entry.get("size"),
                "revision": str(component.get("revision") or ""),
                "on_disk": bool(cached["files"][index].get("present")) if index < len(cached["files"]) else False,
            }
            for index, entry in enumerate(component.get("files") or [])
        ]
        row = registry.get(component_id)
        # Three different facts, kept apart: the bytes are here, the bytes
        # were hash-checked, and the renderer that would execute them reads
        # them from that very place.
        verified_directory = (receipt.get("receipt") or {}).get("directory") if receipt["verified"] else None
        installed_path = installed_directory(settings, component)
        installed = str(installed_path) if installed_path is not None else None
        in_place = bool(receipt["verified"] and _same_directory(verified_directory, installed))
        bound_here = bool(receipt["verified"] and any(
            _same_directory(verified_directory, directory) for directory in bound.values()))
        consumable = bool(receipt["verified"] and (in_place or bound_here))
        evidence.append({
            "component_id": component_id,
            "registry_status": None if row is None else str(row.status),
            # A registry row alone is never verification any more.
            "registry_verified": False,
            "revision": str(component.get("revision") or ""),
            "files": files,
            "receipt": {
                "verified": receipt["verified"],
                "present": receipt["present"],
                "reason": receipt["reason"],
                "identity": receipt["identity"],
                "directory": (receipt.get("receipt") or {}).get("directory"),
                "verified_at": (receipt.get("receipt") or {}).get("verified_at"),
            },
            "cache": {"verified": cached["verified"], "present": cached["present"],
                      "reason": cached["reason"]},
            "bytes_present": sum(int(f["size"] or 0) for f in files
                                 if receipt["present"] or cached["present"]),
            "bytes_total": sum(int(f["size"] or 0) for f in files),
            # Present is a directory fact; verified is a byte fact.  They stay
            # separate so "the files are here" cannot impersonate "the bytes
            # were checked".
            "prepared": bool(files) and (receipt["present"] or cached["present"]),
            "verified": bool(files) and (receipt["verified"] or cached["verified"]),
            "verified_directory": verified_directory,
            "installed_directory": installed,
            "in_place": in_place,
            "bound_to_instance": bound_here,
            "consumable": consumable,
        })
    return evidence


def _profile_rows(db: Session, project_id: str, names: list[str]) -> list[dict[str, Any]]:
    rows = db.scalars(select(WorkflowProfile).where(
        WorkflowProfile.project_id == project_id,
        WorkflowProfile.name.in_(names),
    )).all()
    by_name = {row.name: row for row in rows}
    result: list[dict[str, Any]] = []
    for name in names:
        row = by_name.get(name)
        identity = (row.model_identity or {}) if row is not None else {}
        validated = (row.validated_against or {}) if row is not None else {}
        result.append({
            "name": name,
            "profile_id": row.id if row is not None else None,
            "registered": row is not None,
            "graph_sha256": row.graph_sha256 if row is not None else None,
            "checkpoint_id": row.checkpoint_id if row is not None else None,
            "parameter_map_keys": sorted((row.parameter_map or {}).keys()) if row is not None else [],
            "media_transfer_families": sorted((row.media_transfers or {}).keys()) if row is not None else [],
            "variant_id": identity.get("variant_id"),
            "variant_acceptance_status": identity.get("variant_acceptance_status"),
            "variant_verified": bool(identity.get("variant_verified")),
            "validated": bool(validated),
            "validated_against": validated or None,
        })
    return result


def _real_render(db: Session, project_id: str, profile_ids: list[str]) -> dict[str, Any] | None:
    """A real, non-mock job that really passed with one of these profiles."""
    if not profile_ids:
        return None
    row = db.scalars(
        select(Job).where(
            Job.project_id == project_id,
            Job.workflow_profile_id.in_(profile_ids),
            Job.provider == "comfyui",
            Job.status == "PASSED",
            Job.stale.is_(False),
        ).order_by(Job.created_at.desc()).limit(1)
    ).first()
    if row is None:
        return None
    return {
        "job_id": row.id,
        "shot_id": row.shot_id,
        "artifact_id": row.artifact_id,
        "quality_verification": row.quality_verification,
        "acceptance_status": row.last_qc_result,
        "completed_at": row.updated_at.isoformat() if row.updated_at else None,
    }


def _host_summary(settings) -> dict[str, Any]:
    """The machine facts the card shows: GPU, memory, commit, CUDA and Torch."""
    try:
        from shine.comfyops.host_env import probe_host_facts

        facts = probe_host_facts(settings.data_dir, deployment_dir=settings.comfyops_dir)
    except Exception as exc:  # noqa: BLE001 - an unreadable host is reported, not raised
        return {"status": "UNKNOWN", "reason": str(exc)}
    gpu = facts.get("gpu") or {}
    cuda = facts.get("cuda") or {}
    ram = facts.get("ram") or {}
    commit = facts.get("commit") or {}
    disk = facts.get("disk") or {}
    return {
        "status": "OK",
        "gpu": gpu.get("name"),
        "vram_total_mb": gpu.get("vram_total_mb"),
        "vram_free_mb": gpu.get("vram_free_mb"),
        "ram_total_mb": ram.get("ram_mb"),
        "ram_available_mb": ram.get("ram_available_mb"),
        "commit_total_mb": commit.get("commit_total_mb"),
        "commit_available_mb": commit.get("commit_available_mb"),
        "page_file_status": commit.get("page_file_status"),
        "disk_free_gb": disk.get("free_gb"),
        "cuda_status": cuda.get("status"),
        "compute_capability": cuda.get("compute_capability"),
        "torch": cuda.get("torch") or {},
        "probed_at": (facts.get("evidence") or {}).get("probed_at"),
        "sources": (facts.get("evidence") or {}).get("sources") or {},
    }


def _layer(state: str, detail: str, **extra: Any) -> dict[str, Any]:
    return {"state": state, "detail": detail, **extra}


def readiness(db: Session, settings, binding, project_id: str) -> dict[str, Any]:
    """The five layers of one variant on this machine, plus one next step."""
    weights = weight_evidence(db, settings, binding)
    profiles = _profile_rows(db, project_id, binding.profile_names)
    profile_ids = [row["profile_id"] for row in profiles if row["profile_id"]]
    render = _real_render(db, project_id, profile_ids)

    manifest = binding.manifest
    acceptance = str(manifest.get("acceptance_status") or "")
    downloadable = bool(manifest.get("auto_download_allowed"))

    missing_bytes = [w["component_id"] for w in weights if not w["prepared"]]
    unverified = [w["component_id"] for w in weights if w["prepared"] and not w["verified"]]
    missing_profiles = [row["name"] for row in profiles if not row["registered"]]
    unvalidated = [row["name"] for row in profiles if row["registered"] and not row["validated"]]

    layers: list[dict[str, Any]] = []
    # The five layers are a dependency order: a layer can only be done when
    # every layer before it is done.  "The weights are not here" therefore
    # cannot leave "the files are verified" green.
    blocked = False

    def add(state: str, detail: str, **extra: Any) -> None:
        layers.append(_layer(state, detail, **extra))

    if missing_bytes:
        add(
            "PENDING",
            "还缺这些权重：" + "、".join(missing_bytes) + "。"
            + ("官方没有公布这一组合的显存门槛，本机能不能跑通仍待真机验证。" if not downloadable else ""),
            components=missing_bytes,
        )
        blocked = True
    else:
        add("DONE", "本机已经准备好这个变体的全部权重文件。")

    if blocked:
        add("PENDING", "等权重就位后才能逐字节核验。")
    elif unverified:
        add("PENDING", "这些文件已经在本机，但还没有逐字节核验：" + "、".join(unverified) + "。",
            components=unverified)
        blocked = True
    else:
        add("DONE", "权重已按锁定的 revision、字节数与 SHA-256 核验。")

    if blocked:
        add("PENDING", "等前面的步骤完成后，才能把生成方案登记到这个作品。")
    elif missing_profiles:
        add("PENDING", "这个作品还没有登记这些生成方案：" + "、".join(missing_profiles) + "。",
            profiles=missing_profiles)
        blocked = True
    else:
        add("DONE", "生成方案已登记到当前作品。")

    if blocked:
        add("PENDING", "等方案登记后，才能在本机真实渲染器上核对节点。")
    elif unvalidated:
        add("PENDING", "这些方案还没有在本机真实渲染器上核对过节点：" + "、".join(unvalidated) + "。",
            profiles=unvalidated)
        blocked = True
    else:
        add("DONE", "方案已在受管渲染器上核对过节点与输入。")

    if blocked:
        add("PENDING", "前面的步骤完成后，才能用这个变体真实出片。")
    elif render is None:
        add("PENDING", "还没有用这个变体真实出过片；候选变体在这一步之前永远是候选。")
    else:
        add("DONE", "已用这个变体真实出过片并通过技术质检。", job=render)

    for index, (layer_id, label) in enumerate(LAYERS):
        layers[index]["id"] = layer_id
        layers[index]["label"] = label

    reached = 0
    for index, layer in enumerate(layers):
        if layer["state"] != "DONE":
            break
        reached = index + 1

    next_step = _next_step(
        layers=layers,
        manifest=manifest,
        acceptance=acceptance,
        downloadable=downloadable,
        missing_bytes=missing_bytes,
        unverified=unverified,
        missing_profiles=missing_profiles,
        unvalidated=unvalidated,
    )

    return {
        "variant_id": binding.variant_id,
        "title": manifest.get("title"),
        "label": manifest.get("label"),
        "summary": manifest.get("summary"),
        "quantization": manifest.get("quantization"),
        "acceptance_status": acceptance,
        "source": manifest.get("source"),
        "tier": manifest.get("tier"),
        "catalog_id": binding.catalog_id,
        "checkpoint_id": manifest.get("checkpoint_id"),
        "auto_download_allowed": downloadable,
        "workflow_profile_ids": binding.profile_names,
        "layers": layers,
        "reached_layer": LAYER_IDS[reached] if reached < len(LAYER_IDS) else LAYER_IDS[-1],
        "reached_label": LAYER_LABELS[LAYER_IDS[reached]] if reached < len(LAYER_IDS) else LAYER_LABELS[LAYER_IDS[-1]],
        "production_ready": reached >= len(LAYERS),
        "next_step": next_step,
        "weights": weights,
        "profiles": profiles,
        "real_render": render,
        "host": _host_summary(settings),
    }


def _next_step(*, layers: list[dict[str, Any]], manifest: dict[str, Any], acceptance: str,
               downloadable: bool, missing_bytes: list[str], unverified: list[str],
               missing_profiles: list[str], unvalidated: list[str]) -> dict[str, Any]:
    """The single next step, with a reason a person can act on."""
    pending = [layer for layer in layers if layer["state"] != "DONE"]
    if not pending:
        return {
            "code": "READY_FOR_PRODUCTION",
            "label": "可以用于生产",
            "reason": "权重、文件核验、方案登记、本机运行时与真实出片都已完成。",
            "action": None,
            "blocked": False,
        }
    layer = pending[0]
    if layer["id"] == "WEIGHTS_PENDING":
        if not downloadable:
            return {
                "code": "VARIANT_NOT_DOWNLOADABLE",
                "label": "把权重放到本机（或走已有的本地导入 / 隔离验证流程）",
                "reason": "这个变体还没有进入自动下载路径（验收状态是 " + acceptance + "）。"
                          "不能用改 SUPPORTED、改 auto_download_allowed 或跳过资格检查的方式解锁下载；"
                          "请用已有的「检测已有安装 / 导入模型」流程，"
                          "把官方权重按 revision、字节数与 SHA-256 校验后放进来。",
                "action": "EXISTING_INSTALLATION_IMPORT",
                "blocked": True,
            }
        return {
            "code": "WEIGHTS_MISSING",
            "label": "下载并安装这个变体的权重",
            "reason": "还缺：" + "、".join(missing_bytes) + "。",
            "action": "INSTALL_PLAN",
            "blocked": False,
        }
    if layer["id"] == "FILES_VERIFIED":
        return {
            "code": "WEIGHTS_NOT_HASH_VERIFIED",
            "label": "逐字节核验已经放好的权重",
            "reason": "文件在，但还没有按锁定的 revision、字节数与 SHA-256 核验：" + "、".join(unverified) + "。",
            "action": "EXISTING_INSTALLATION_VERIFY",
            "blocked": False,
        }
    if layer["id"] == "WORKFLOW_REGISTERED":
        return {
            "code": "PROFILE_NOT_REGISTERED",
            "label": "把这个变体的生成方案登记到当前作品",
            "reason": "当前作品还没有登记：" + "、".join(missing_profiles) + "。",
            "action": "REGISTER_PROFILES",
            "blocked": False,
        }
    if layer["id"] == "RUNTIME_VALIDATED":
        return {
            "code": "PROFILE_NOT_VALIDATED",
            "label": "在本机真实渲染器上核对节点",
            "reason": "还没有核对：" + "、".join(unvalidated) + "。核对会启动受管渲染器并读它的真实节点表。",
            "action": "VALIDATE_PROFILES",
            "blocked": False,
        }
    return {
        "code": "REAL_RENDER_NOT_DONE",
        "label": "用这个变体真实出一个镜头",
        "reason": "候选变体在真实出片之前永远是候选，不会进入默认生产推荐。",
        "action": "REAL_RENDER",
        "blocked": False,
    }