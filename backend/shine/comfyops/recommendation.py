"""Deterministic deployment recommendation for this machine.

The rules here are testable and boring on purpose: a language model may explain
a recommendation, but it must never invent a download size, a compatibility
threshold or a "your card can run this" claim.  Nothing in this module loads a
weight, starts a renderer or downloads a byte.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from shine.comfyops import deployment

# The fixed product choices.  H3 is the only official video model; Qwen-Image
# 2.1 is the first adapted image model.  A new combination must be audited and
# registered in ``config/deployment`` before it can appear here.
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

# A machine sold as "32 GB" reports about 31.7 GB of usable memory, which is the
# convention ``host_env.RAM_MB_MIN`` already encodes (30 GiB floor).  A catalog
# that declares a round-GiB floor therefore has to be read in the units the host
# reports, otherwise the recommendation would reject a machine the installer
# accepts — exactly the "three different conclusions" the plan forbids.
DECLARED_FLOOR_SLACK_MB = 1024


def effective_floor_mb(declared_mb: int | None) -> int | None:
    """The declared RAM floor, expressed in the memory a host actually reports."""
    if not declared_mb:
        return declared_mb
    if declared_mb >= 8192 and declared_mb % 1024 == 0:
        return declared_mb - DECLARED_FLOOR_SLACK_MB
    return declared_mb


def _platform_id(platform: dict[str, Any]) -> str:
    """Normalise the host report to one ``<system>-<arch>`` identifier.

    Python reports ``AMD64`` where the catalog says ``x64``; comparing raw
    strings would block a supported machine for a spelling difference.
    """
    system = str(platform.get("system") or "").lower()
    machine = str(platform.get("machine") or "").lower()
    arch = {"amd64": "x64", "x86_64": "x64", "x64": "x64", "arm64": "arm64", "aarch64": "arm64"}.get(
        machine, machine
    )
    return f"{system}-{arch}" if system and arch else ""


def _catalog_total_bytes(catalog: dict[str, Any]) -> int:
    total = catalog.get("total_bytes")
    if isinstance(total, int) and total > 0:
        return total
    return sum(int(file.get("size") or 0) for component in catalog.get("components", [])
               for file in component.get("files", []))


def _requirements(catalog: dict[str, Any]) -> dict[str, Any]:
    platform = catalog.get("platform") or {}
    return {
        "os": platform.get("os"),
        "gpu_vendor": platform.get("gpu_vendor"),
        "min_vram_mb": platform.get("min_vram_mb"),
        "min_ram_mb": platform.get("min_ram_mb"),
        "min_disk_gb": platform.get("min_disk_gb"),
        "total_bytes": _catalog_total_bytes(catalog),
    }


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
    requirements = _requirements(catalog)
    gpu = env.get("gpu") or {}
    ram = env.get("ram") or {}
    disk = env.get("disk") or {}
    platform = env.get("platform") or {}
    blockers: list[dict[str, Any]] = []
    warnings: list[dict[str, Any]] = []

    want_os = (requirements.get("os") or "").lower()
    actual_os = _platform_id(platform)
    if want_os and want_os != actual_os:
        blockers.append({"code": "OS_UNSUPPORTED",
                         "message": f"这套组合要求 {requirements['os']}，本机是 {actual_os}"})
    if requirements.get("gpu_vendor") and gpu.get("status") != "OK":
        warnings.append({
            "code": "GPU_NOT_IDENTIFIED",
            "message": "本机显卡没有被识别出来；这不等于没有显卡，但无法据此确认显存要求。",
        })
    min_vram = requirements.get("min_vram_mb")
    if min_vram and gpu.get("status") == "OK" and int(gpu.get("vram_mb") or 0) < int(min_vram):
        blockers.append({
            "code": "VRAM_INSUFFICIENT",
            "message": f"这套组合需要至少 {min_vram} MB 显存，本机这张卡是 {gpu.get('vram_mb')} MB。"
                       "多卡显存不会自动相加，第一版只使用一张卡。",
            "required": min_vram, "actual": gpu.get("vram_mb"),
        })
    min_ram = effective_floor_mb(requirements.get("min_ram_mb"))
    if min_ram and ram.get("status") == "OK" and int(ram.get("ram_mb") or 0) < int(min_ram):
        blockers.append({"code": "RAM_INSUFFICIENT",
                         "message": f"需要至少 {min_ram} MB 内存，本机 {ram.get('ram_mb')} MB",
                         "required": min_ram, "actual": ram.get("ram_mb")})
    min_disk = requirements.get("min_disk_gb")
    if min_disk and disk.get("free_gb") is not None and float(disk.get("free_gb") or 0) < float(min_disk):
        blockers.append({"code": "DISK_INSUFFICIENT",
                         "message": f"需要至少 {min_disk} GB 可用空间，目标盘只剩 {disk.get('free_gb')} GB"})

    total = requirements["total_bytes"]
    reusable = _reusable_bytes(catalog, reuse_root)
    download = max(0, total - reusable)
    return {
        "catalog_id": catalog.get("catalog_id"),
        "catalog_version": catalog.get("catalog_version"),
        "status": "BLOCKED" if blockers else "SUPPORTED",
        "requirements": requirements,
        "total_bytes": total,
        "reuse_bytes": reusable,
        "download_bytes": download,
        "blockers": blockers,
        "warnings": warnings,
        "evidence": _evidence(validation, catalog.get("catalog_id", "")),
        "verified": bool((validation or {}).get(catalog.get("catalog_id", ""))),
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
    supported = [row for row in options if row.get("status") == "SUPPORTED"]
    blocked = [row for row in options if row.get("status") != "SUPPORTED"]
    return {
        "recommended": supported,
        "blocked": blocked,
        "options": options,
        "host": {
            "gpu": (env.get("gpu") or {}).get("name"),
            "vram_mb": (env.get("gpu") or {}).get("vram_mb"),
            "ram_mb": (env.get("ram") or {}).get("ram_mb"),
            "free_gb": (env.get("disk") or {}).get("free_gb"),
        },
        "notes": [
            "要求来自 config/deployment 中已审计的组合，不按下载大小或统一显存档位推断。",
            "下载量与复用量是给确认页用的估算；真正复用前安装器仍会核验文件哈希。",
            "本机没有实测过的组合只标 estimated，不会被写成“已验证可用”。",
        ],
    }
