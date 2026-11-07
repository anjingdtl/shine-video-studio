"""Deterministic deployment recommendation for this machine.

The rules here are testable and boring on purpose: a language model may explain
a recommendation, but it must never invent a download size, a compatibility
threshold or a "your card can run this" claim.  Nothing in this module loads a
weight, starts a renderer or downloads a byte.

Three states, never two:

* SUPPORTED - every declared requirement was measured and met.
* BLOCKED - a requirement was read and is not met.
* UNKNOWN - a requirement could not be read, or the variant is still a
  candidate without a real acceptance.  An unknown machine is never a
  supported one, and a candidate variant is never reported as supported.

The thresholds come from the versioned variant manifests and the verdict comes
from the one qualification service in shine.comfyops.variants, so the
recommender, the new-machine blocker, the install plan and the production
start cannot drift apart.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from shine.comfyops import deployment
from shine.comfyops.variants import (
    ACCEPTANCE_SUPPORTED,
    STATUS_BLOCKED,
    STATUS_SUPPORTED,
    STATUS_UNKNOWN,
    effective_floor_mb,  # noqa: F401 - re-exported: the floors live in one place
    list_variant_manifests,
    load_variant_manifest,
    platform_id,
    qualify,
    variant_requirements,
)

__all__ = [
    "PURPOSES",
    "effective_floor_mb",
    "evaluate",
    "recommend",
    "recommend_variants",
    "vram_tier",
]

# The fixed product choices.  H3 is the only official video model; Qwen-Image
# 2.1 is the first adapted image model.  A new combination must be audited and
# registered in config/deployment before it can appear here.
PURPOSES: dict[str, dict[str, str]] = {
    "h3_video": {
        "catalog": "h3-ref2va-windows-x64",
        "label": "H3 正式视频生成",
        "skippable": "false",
    },
    "image_prep": {
        "catalog": "qwen-image-2.1-windows-x64",
        "label": "生图候选准备",
        "skippable": "true",
    },
}

EVIDENCE_ESTIMATED = "estimated"
EVIDENCE_MEASURED = "measured_on_this_machine"
EVIDENCE_SIMILAR = "measured_on_similar_hardware"

MAX_WALKED_ENTRIES = 4000

# The hardware tiers a machine falls into, by the *measured* total memory of
# the one selected card.  Multi-card memory is never summed.
TIER_STANDARD = "standard"
TIER_LOWEND = "lowend_12gb"
TIER_BELOW_LOWEND = "below_lowend"
TIER_UNKNOWN = "unknown"

#: A 12 GB class card reports between about 10 GiB and just under 15 GiB.
LOWEND_VRAM_MIN_MB = 10240
#: The upper bound is the standard variant's own floor, so a card that meets
#: the standard floor is never put in the low-end tier.
LOWEND_VRAM_MAX_MB = 15360

#: A third party slot whose file identity is not pinned cannot be installed.
VARIANT_SOURCE_UNVERIFIED = "THIRD_PARTY_UNVERIFIED"


def _platform_id(platform: dict[str, Any]) -> str:
    """Normalise the host report to one <system>-<arch> identifier."""
    return platform_id(platform)


def _catalog_total_bytes(catalog: dict[str, Any]) -> int:
    total = catalog.get("total_bytes")
    if isinstance(total, int) and total > 0:
        return total
    return sum(int(file.get("size") or 0) for component in catalog.get("components", [])
               for file in component.get("files", []))


def _resolved_requirements(catalog: dict[str, Any]) -> tuple[dict[str, Any], str | None]:
    """The thresholds of a catalog: its variant manifest first, then its own block."""
    variant_id = catalog.get("variant_id")
    if variant_id:
        try:
            return variant_requirements(load_variant_manifest(variant_id)), variant_id
        except (OSError, ValueError, FileNotFoundError):
            pass
    from shine.comfyops.variants import catalog_requirements

    return catalog_requirements(catalog), None


def _requirements(catalog: dict[str, Any]) -> dict[str, Any]:
    resolved, _ = _resolved_requirements(catalog)
    platform = catalog.get("platform") or {}
    block = {
        "os": resolved.get("os") or platform.get("os"),
        "gpu_vendor": resolved.get("gpu_vendor") or platform.get("gpu_vendor"),
        "min_vram_mb": resolved.get("min_vram_mb"),
        "min_ram_mb": resolved.get("min_ram_mb"),
        "min_disk_gb": resolved.get("min_disk_gb"),
        "total_bytes": _catalog_total_bytes(catalog),
    }
    for key in ("min_commit_mb", "min_free_vram_mb", "min_free_ram_mb"):
        if resolved.get(key) is not None:
            block[key] = resolved[key]
    return block


def _reusable_bytes(catalog: dict[str, Any], root: Path | None) -> int:
    """Bytes of catalog files that already exist somewhere under the root.

    This is an *estimate for the download confirmation screen*: a file found by
    name is reported as reusable, and the installer still verifies its hash
    before trusting it.  A missing root simply means "nothing to reuse".
    """
    if root is None or not Path(root).is_dir():
        return 0
    wanted = {
        Path(file["relative_path"]).name: int(file.get("size") or 0)
        for component in catalog.get("components", [])
        for file in component.get("files", [])
    }
    if not wanted:
        return 0
    found = 0
    walked = 0
    for path in Path(root).rglob("*"):
        walked += 1
        if walked > MAX_WALKED_ENTRIES:
            break
        if not path.is_file() or path.name not in wanted:
            continue
        try:
            if path.stat().st_size == wanted[path.name]:
                found += wanted[path.name]
        except OSError:
            continue
    return found


def _evidence(validation: dict[str, Any] | None, catalog_id: str) -> dict[str, Any]:
    record = (validation or {}).get(catalog_id) or {}
    if not record:
        return {
            "level": EVIDENCE_ESTIMATED,
            "verified": False,
            "detail": "尚未在本机实测这套组合；下面的要求来自已审计的部署目录，不是这台机器的实测结论。",
        }
    return {
        "level": EVIDENCE_MEASURED if record.get("machine") == "local" else EVIDENCE_SIMILAR,
        "verified": True,
        "detail": "本机实测记录",
        "measured_at": record.get("verified_at"),
        "peak_vram_mb": record.get("peak_vram_mb"),
        "peak_ram_mb": record.get("peak_ram_mb"),
        "duration_s": record.get("duration_s"),
        "config_fingerprint": record.get("config_fingerprint"),
    }


def evaluate(
    catalog: dict[str, Any],
    env: dict[str, Any],
    *,
    validation: dict[str, Any] | None = None,
    reuse_root: Path | None = None,
) -> dict[str, Any]:
    """Judge one audited combination against one host report."""
    resolved, variant_id = _resolved_requirements(catalog)
    verdict = qualify(resolved, env)
    total = _catalog_total_bytes(catalog)
    reusable = _reusable_bytes(catalog, reuse_root)
    download = max(0, total - reusable)
    return {
        "catalog_id": catalog.get("catalog_id"),
        "catalog_version": catalog.get("catalog_version"),
        "variant_id": variant_id,
        "status": verdict["status"],
        "requirements": _requirements(catalog),
        "total_bytes": total,
        "reuse_bytes": reusable,
        "download_bytes": download,
        "blockers": verdict["blockers"],
        "unknowns": verdict["unknowns"],
        # The unreadable facts are also listed as warnings: the existing
        # setup card explains itself with the warning list, and a machine
        # whose GPU could not be read must say why.
        "warnings": verdict["warnings"] + verdict["unknowns"],
        "evidence": _evidence(validation, catalog.get("catalog_id", "")),
        "verified": bool((validation or {}).get(catalog.get("catalog_id", ""))),
        "selected_gpu": verdict.get("selected_gpu"),
        "gpus_considered": verdict.get("gpus_considered", 1),
    }


def recommend(
    env: dict[str, Any],
    *,
    purposes: list[str] | None = None,
    validation: dict[str, Any] | None = None,
    reuse_root: Path | None = None,
) -> dict[str, Any]:
    """Rank the audited combinations for this host, per purpose.

    A combination that cannot run here is reported as blocked with its exact
    reason instead of being silently replaced by a different model: choosing a
    different video model is a product decision, not a fallback.
    """
    wanted = purposes or list(PURPOSES)
    unknown = [name for name in wanted if name not in PURPOSES]
    if unknown:
        raise ValueError("UNKNOWN_DEPLOYMENT_PURPOSE:" + ",".join(unknown))
    options: list[dict[str, Any]] = []
    for purpose in wanted:
        entry = PURPOSES[purpose]
        try:
            catalog = deployment.load_deployment_catalog(entry["catalog"])
        except Exception as exc:  # a missing/blocked catalog must not crash setup
            options.append({
                "purpose": purpose, "label": entry["label"], "catalog_id": entry["catalog"],
                "status": "UNAVAILABLE", "blockers": [{"code": "CATALOG_UNAVAILABLE",
                                                       "message": str(exc)[:300]}],
                "skippable": entry["skippable"] == "true",
            })
            continue
        result = evaluate(catalog, env, validation=validation, reuse_root=reuse_root)
        options.append({
            "purpose": purpose,
            "label": entry["label"],
            "skippable": entry["skippable"] == "true",
            **result,
        })
    supported = [row for row in options if row.get("status") == STATUS_SUPPORTED]
    blocked = [row for row in options if row.get("status") != STATUS_SUPPORTED]
    return {
        "recommended": supported,
        "blocked": blocked,
        "options": options,
        "host": {
            "gpu": (env.get("gpu") or {}).get("name"),
            "vram_mb": (env.get("gpu") or {}).get("vram_total_mb") or (env.get("gpu") or {}).get("vram_mb"),
            "vram_free_mb": (env.get("gpu") or {}).get("vram_free_mb"),
            "ram_mb": (env.get("ram") or {}).get("ram_mb"),
            "commit_mb": (env.get("commit") or {}).get("commit_total_mb"),
            "free_gb": (env.get("disk") or {}).get("free_gb"),
        },
        "notes": [
            "要求来自 config/deployment 中已审计的组合，不按下载大小或统一显存档位推断。",
            "下载量与复用量是给确认页用的估算；真正复用前安装器仍会核验文件哈希。",
            "本机没有实测过的组合只标 estimated，不会被写成“已验证可用”。",
            "显卡、内存、提交内存、CUDA 或磁盘读不出来时结论是 UNKNOWN，不会当作支持。",
        ],
    }


def vram_tier(vram_mb: int | None) -> str:
    """The hardware tier of a machine, by the memory of its one selected card."""
    if not isinstance(vram_mb, int | float) or isinstance(vram_mb, bool):
        return TIER_UNKNOWN
    if vram_mb >= LOWEND_VRAM_MAX_MB:
        return TIER_STANDARD
    if vram_mb >= LOWEND_VRAM_MIN_MB:
        return TIER_LOWEND
    return TIER_BELOW_LOWEND


def _host_vram_mb(env: dict[str, Any]) -> int | None:
    gpu = env.get("gpu") or {}
    for key in ("vram_total_mb", "vram_mb"):
        value = gpu.get(key)
        if isinstance(value, int | float) and not isinstance(value, bool):
            return int(value)
    return None


def _evidence_gaps(manifest: dict[str, Any], env: dict[str, Any]) -> list[dict[str, Any]]:
    """What is still missing before this variant may be used in production."""
    gaps: list[dict[str, Any]] = []
    acceptance = manifest.get("acceptance_status")
    if acceptance != ACCEPTANCE_SUPPORTED:
        gaps.append({
            "code": "NO_REAL_ACCEPTANCE",
            "text": "还没有真机最小镜头验收；不能当作生产可用。",
        })
    if manifest.get("source") == VARIANT_SOURCE_UNVERIFIED:
        gaps.append({
            "code": "SOURCE_UNVERIFIED",
            "text": "这是第三方槽位，官方仓库里没有对应文件；文件身份、许可与门槛都没有核实。",
        })
        for entry in manifest.get("required_inputs") or []:
            gaps.append({
                "code": "REQUIRED_INPUT_MISSING",
                "requirement": entry.get("id"),
                "text": str(entry.get("requirement")) + "：" + str(entry.get("detail")),
            })
    requirements = variant_requirements(manifest)
    for name in ("min_vram_mb", "min_ram_mb", "min_commit_mb", "min_disk_gb",
                 "min_free_vram_mb", "min_free_ram_mb"):
        if isinstance(requirements.get(name), str):
            gaps.append({
                "code": "OFFICIAL_THRESHOLD_UNKNOWN",
                "requirement": name,
                "text": "官方没有公布这一项门槛（unknown），本机是否满足未知。",
            })
    for name, entry in (manifest.get("compatibility") or {}).items():
        if str((entry or {}).get("status")) == "unknown":
            gaps.append({
                "code": "COMPATIBILITY_UNVERIFIED",
                "requirement": name,
                "text": "这一项与本工作流的兼容性没有逐项验证。",
            })
    vram = _host_vram_mb(env)
    sizes = [entry.get("size") for entry in manifest.get("files") or []
             if isinstance(entry.get("size"), int) and not isinstance(entry.get("size"), bool)]
    if vram and sizes:
        largest = max(sizes) // (1024 ** 2)
        if largest > vram:
            gaps.append({
                "code": "WEIGHT_EXCEEDS_VRAM",
                "text": f"候选权重最大单文件 {largest} MiB，超过本机这张卡的 {vram} MiB 显存；"
                        "能否离页加载或分块推理没有证据（待核）。",
            })
    # The gaps the manifest states about itself - a third party's coverage, a
    # missing measurement - are part of the answer, not a footnote.
    for gap in manifest.get("known_evidence_gaps") or []:
        gaps.append({"code": str(gap.get("code")), "text": str(gap.get("text"))})
    return gaps


def _variant_option(manifest: dict[str, Any], env: dict[str, Any]) -> dict[str, Any]:
    """One variant, judged against this host, with its evidence gaps."""
    requirements = variant_requirements(manifest)
    verdict = qualify(requirements, env)
    acceptance = manifest.get("acceptance_status")
    gaps = _evidence_gaps(manifest, env)
    blockers = list(verdict["blockers"])
    unknowns = list(verdict["unknowns"])
    if manifest.get("source") == VARIANT_SOURCE_UNVERIFIED:
        blockers.insert(0, {
            "code": "VARIANT_SOURCE_UNVERIFIED",
            "message": "这个槽位指向的文件没有钉点（仓库、revision、体积、SHA-256、许可证都未核实），"
                       "因此不能下载、不能安装、不能验收。",
        })
    status = verdict["status"]
    if acceptance != ACCEPTANCE_SUPPORTED and status == STATUS_SUPPORTED:
        # A variant without a real acceptance never counts as supported.
        status = STATUS_UNKNOWN
    if manifest.get("source") == VARIANT_SOURCE_UNVERIFIED:
        status = STATUS_BLOCKED
    sizes = [entry.get("size") for entry in manifest.get("files") or []
             if isinstance(entry.get("size"), int) and not isinstance(entry.get("size"), bool)]
    return {
        "variant_id": manifest.get("variant_id"),
        "title": manifest.get("title"),
        "summary": manifest.get("summary"),
        "model_family": manifest.get("model_family"),
        "generation_mode": manifest.get("generation_mode"),
        "quantization": manifest.get("quantization"),
        "precision": manifest.get("precision"),
        "source": manifest.get("source"),
        "tier": manifest.get("tier"),
        "preference": manifest.get("preference", 50),
        "acceptance_status": acceptance,
        "status": status,
        "catalog_id": manifest.get("catalog_id"),
        "checkpoint_id": manifest.get("checkpoint_id"),
        "workflow_profile_ids": manifest.get("workflow_profile_ids") or [],
        "graph_sha256": manifest.get("graph_sha256") or {},
        "required_component_ids": manifest.get("required_component_ids") or [],
        "auto_download_allowed": bool(manifest.get("auto_download_allowed")),
        "total_bytes": sum(sizes),
        "requirements": requirements,
        "blockers": blockers,
        "unknowns": unknowns,
        "warnings": verdict["warnings"],
        "evidence_gaps": gaps,
        "third_party_claims": list(manifest.get("third_party_claims") or []),
        "runtime_requirements": list(manifest.get("runtime_requirements") or []),
        "requires_explicit_user_ack": acceptance != ACCEPTANCE_SUPPORTED,
        "usable_in_production": acceptance == ACCEPTANCE_SUPPORTED and status == STATUS_SUPPORTED,
    }


def recommend_variants(env: dict[str, Any], *, manifests: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    """Rank the H3 variants for this machine.

    A candidate is offered, never promised: its acceptance status, its
    evidence gaps and the fact that a human has to acknowledge it are all
    part of the answer.  The order inside a tier is a fixed preference, not
    a measurement: a pinned and usable slot wins over an unpinned candidate.
    """
    entries = manifests if manifests is not None else list_variant_manifests()
    vram = _host_vram_mb(env)
    tier = vram_tier(vram)
    options = [_variant_option(manifest, env) for manifest in entries]
    options.sort(key=lambda row: (row.get("preference", 50), str(row.get("variant_id"))))
    in_tier = [row for row in options if row.get("tier") == tier]
    recommended = [row for row in in_tier if row["usable_in_production"]]
    candidates = [row for row in in_tier if not row["usable_in_production"]]
    blocked = [row for row in options if row not in in_tier]
    notes = [
        "候选变体在真机验收之前只显示 CANDIDATE 或 BLOCKED，不会显示为生产可用。",
        "官方页面与发布方都没有给出可采用的显存门槛，所以 12GB 级显卡能不能跑通是待核，不是结论。",
        "第三方权重的分档表、算力与显存结论是发布方的声明，不是本项目的实测；本项目没有真机验收记录。",
        "多卡显存不相加；比较只用本机选中的那一张卡。",
    ]
    if tier == TIER_LOWEND:
        notes.append(
            "本机属于 12GB 级档位：偏好顺序是「已钉住的 NVFP4 第三方量化候选 → 官方 w6a8 低端机候选 → "
            "用户点名但官方/第三方仓库都没有的 W4 槽位（阻断）」。三者都还没有真机验收，"
            "所以这一档位目前没有可用于生产的变体；发布方的分档表也只覆盖 8–12GB 的 RTX 50/Blackwell，"
            "Ada 架构的 12GB 卡不在其覆盖范围内。"
        )
    return {
        "tier": tier,
        "host": {
            "gpu": (env.get("gpu") or {}).get("name"),
            "vram_mb": vram,
            "vram_free_mb": (env.get("gpu") or {}).get("vram_free_mb"),
            "ram_mb": (env.get("ram") or {}).get("ram_mb"),
            "commit_mb": (env.get("commit") or {}).get("commit_total_mb"),
            "free_gb": (env.get("disk") or {}).get("free_gb"),
        },
        "recommended": recommended,
        "candidates": candidates,
        "blocked": blocked,
        "options": options,
        "notes": notes,
    }
