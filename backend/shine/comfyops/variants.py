"""Versioned H3 variant manifests and the single host-qualification service.

A *variant* is one concrete generation stack: a checkpoint with its own file
identity, its own workflow profiles, its own compatibility matrix and its own
acceptance evidence.  Two variants never share a conclusion: a candidate that
has never run on a real machine cannot inherit the acceptance status of the
variant that has.

This module owns exactly two things:

1. the versioned variant manifests under config/deployment/variants/;
2. the one qualification service that turns *host facts* plus *declared
   requirements* into SUPPORTED / BLOCKED / UNKNOWN.

The new-machine blocker, the deployment recommender, the install plan and the
production start all read their thresholds from here, so a RAM / VRAM / commit
/ disk floor is written down in exactly one place.

Evidence discipline:

* A fact that could not be read is written as the explicit token "unknown" and
  is listed in the manifest's unknown_fields with the reason.  Nothing here
  guesses a size, a hash, a driver or a threshold.
* acceptance_status is never raised by a file size, a page listing or a
  download estimate.  Only recorded evidence on a real machine may do that.
* License entries carry source, conditions and a jurisdiction note.  They
  never collapse into a "commercial / non-commercial" legal claim.

Nothing in this module loads a weight, starts a renderer or downloads a byte.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, model_validator

REPO_ROOT = Path(__file__).resolve().parents[3]
VARIANTS_DIR = REPO_ROOT / "config" / "deployment" / "variants"

#: The version of the manifest format itself.
MANIFEST_VERSION = "1"

#: The explicit marker for a fact that is not known.  It is never a guess.
UNKNOWN = "unknown"

ACCEPTANCE_CANDIDATE = "CANDIDATE"
ACCEPTANCE_EXPERIMENTAL = "EXPERIMENTAL"
ACCEPTANCE_SUPPORTED = "SUPPORTED"
ACCEPTANCE_BLOCKED = "BLOCKED"
ACCEPTANCE_STATUSES = (
    ACCEPTANCE_CANDIDATE,
    ACCEPTANCE_EXPERIMENTAL,
    ACCEPTANCE_SUPPORTED,
    ACCEPTANCE_BLOCKED,
)

STATUS_SUPPORTED = "SUPPORTED"
STATUS_BLOCKED = "BLOCKED"
STATUS_UNKNOWN = "UNKNOWN"
QUALIFICATION_STATUSES = (STATUS_SUPPORTED, STATUS_BLOCKED, STATUS_UNKNOWN)

#: A machine sold as "32 GB" reports about 31.7 GB of usable memory.  A
#: manifest that declares a round-GiB floor therefore has to be read in the
#: units the host reports, otherwise the recommendation would reject a
#: machine the installer accepts - exactly the "three different conclusions"
#: this service replaces.
DECLARED_FLOOR_SLACK_MB = 1024

_SHA256 = re.compile(r"^[a-f0-9]{64}$")

#: The thresholds of a variant.  A threshold may be a number or the explicit
#: "unknown" token; "unknown" means the requirement itself is still open, so
#: the host can never be qualified against it.
THRESHOLD_FIELDS = (
    "min_vram_mb",
    "min_ram_mb",
    "min_commit_mb",
    "min_disk_gb",
    "min_free_vram_mb",
    "min_free_ram_mb",
)


def effective_floor_mb(declared_mb: int | None) -> int | None:
    """The declared RAM floor, expressed in the memory a host actually reports."""
    if not declared_mb:
        return declared_mb
    if declared_mb >= 8192 and declared_mb % 1024 == 0:
        return declared_mb - DECLARED_FLOOR_SLACK_MB
    return declared_mb


def normalize_arch(machine: str | None) -> str:
    """Python reports AMD64 where the manifests say x64."""
    value = str(machine or "").strip().lower()
    return {
        "amd64": "x64",
        "x86_64": "x64",
        "x64": "x64",
        "arm64": "arm64",
        "aarch64": "arm64",
    }.get(value, value)


def platform_id(platform: dict[str, Any]) -> str:
    """Normalise a host report to one <system>-<arch> identifier."""
    system = str((platform or {}).get("system") or "").lower()
    arch = normalize_arch((platform or {}).get("machine"))
    return f"{system}-{arch}" if system and arch else ""


class LicenseEvidence(BaseModel):
    """Where a licence comes from, what it requires and who must check it."""

    model_config = ConfigDict(extra="forbid")
    id: str
    url: str
    conditions: str
    jurisdiction_note: str


class FileEvidence(BaseModel):
    """How the identity of one file was established."""

    model_config = ConfigDict(extra="forbid")
    source: str
    verified_on: str
    level: str
    note: str = ""


class VariantFile(BaseModel):
    """One pinned file of a variant.

    size, sha256 and url may carry the explicit "unknown" token while a
    candidate is still unpinned; they may never carry a guess.
    """

    model_config = ConfigDict(extra="forbid")
    role: str
    component_id: str
    repository: str
    revision: str
    relative_path: str
    size: int | str
    sha256: str
    url: str
    license: LicenseEvidence
    evidence: FileEvidence

    @model_validator(mode="after")
    def _check_identity(self):
        if isinstance(self.size, str):
            if self.size != UNKNOWN:
                raise ValueError("size must be a positive integer or the explicit 'unknown' token")
        elif self.size <= 0:
            raise ValueError("size must be a positive integer")
        if self.sha256 != UNKNOWN and not _SHA256.match(self.sha256):
            raise ValueError("sha256 must be 64 hex characters or the explicit 'unknown' token")
        if self.url != UNKNOWN and not self.url.startswith("https://"):
            raise ValueError("url must be https or the explicit 'unknown' token")
        return self


class VariantRequirements(BaseModel):
    """The admission thresholds of one variant.

    estimated_or_measured says, per threshold, whether the number is a
    declared estimate or a value measured on a real machine.  A threshold
    that was never measured is an estimate, and it says so.
    """

    model_config = ConfigDict(extra="forbid")
    os: str = UNKNOWN
    arch: str = UNKNOWN
    gpu_vendor: str = UNKNOWN
    min_vram_mb: int | float | str | None = None
    min_ram_mb: int | float | str | None = None
    min_commit_mb: int | float | str | None = None
    min_disk_gb: int | float | str | None = None
    min_free_vram_mb: int | float | str | None = None
    min_free_ram_mb: int | float | str | None = None
    min_cuda_compute_capability: str | None = None
    estimated_or_measured: dict[str, str] = Field(default_factory=dict)

    @model_validator(mode="after")
    def _check_thresholds(self):
        for name in THRESHOLD_FIELDS:
            value = getattr(self, name)
            if isinstance(value, str) and value != UNKNOWN:
                raise ValueError(name + " must be a number or the explicit 'unknown' token")
        return self


class VariantManifest(BaseModel):
    """One versioned H3 variant."""

    model_config = ConfigDict(extra="forbid")
    manifest_version: str
    variant_id: str
    label: str = ""
    model_family: str
    generation_mode: str
    title: str
    summary: str
    precision: str
    quantization: str
    acceptance_status: str
    default_recommended: bool
    auto_download_allowed: bool
    #: Where the file identity comes from: an official repository that is
    #: pinned, or a third party slot that is not pinned at all.
    source: str = "OFFICIAL_PINNED"
    #: The hardware tier this variant is meant for ("standard",
    #: "lowend_12gb") or "unassigned".
    tier: str = "unassigned"
    #: The preference inside a tier: lower wins.  It is a declared order,
    #: never a measurement.
    preference: int = 50
    #: What a human must supply before a blocked slot may become a candidate.
    required_inputs: list[dict] = Field(default_factory=list)
    catalog_id: str
    checkpoint_id: str
    required_component_ids: list[str] = Field(default_factory=list)
    install_component_ids: list[str] = Field(default_factory=list)
    files: list[VariantFile] = Field(default_factory=list)
    runtime: dict = Field(default_factory=dict)
    architecture_requirements: dict = Field(default_factory=dict)
    requirements: VariantRequirements = Field(default_factory=VariantRequirements)
    workflow_profile_ids: list[str] = Field(default_factory=list)
    graph_sha256: dict[str, str] = Field(default_factory=dict)
    compatibility: dict = Field(default_factory=dict)
    acceptance: dict = Field(default_factory=dict)
    #: Gaps the manifest states about itself (a third party's coverage, a
    #: missing measurement).  They are merged into the computed gaps.
    known_evidence_gaps: list[dict] = Field(default_factory=list)
    #: Runtime conditions the publisher or the user reported.  They are
    #: claims, never measurements of this project.
    runtime_requirements: list[dict] = Field(default_factory=list)
    #: What the publisher of a third-party weight states about it.  Each
    #: claim keeps its source and says that it is not our measurement.
    third_party_claims: list[dict] = Field(default_factory=list)
    unknown_fields: list[dict] = Field(default_factory=list)

    @model_validator(mode="after")
    def _check_status(self):
        if self.acceptance_status not in ACCEPTANCE_STATUSES:
            raise ValueError("acceptance_status must be one of " + ", ".join(ACCEPTANCE_STATUSES))
        if self.manifest_version != MANIFEST_VERSION:
            raise ValueError("unsupported manifest_version: " + str(self.manifest_version))
        return self


def manifest_path(variant_id: str) -> Path:
    return VARIANTS_DIR / f"{variant_id}.json"


def load_variant_manifest(variant_id: str) -> dict:
    """Read and validate one variant manifest; returns the raw dict."""
    path = manifest_path(variant_id)
    if not path.is_file():
        raise FileNotFoundError("VARIANT_MANIFEST_MISSING: " + variant_id)
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise ValueError("VARIANT_MANIFEST_INVALID: " + variant_id) from exc
    VariantManifest.model_validate(raw)
    return raw


def list_variant_manifests() -> list[dict]:
    """Every committed variant manifest, ordered by variant id."""
    if not VARIANTS_DIR.is_dir():
        return []
    return [load_variant_manifest(path.stem) for path in sorted(VARIANTS_DIR.glob("*.json"))]


def default_variant_id() -> str:
    """The variant the product installs by default."""
    for manifest in list_variant_manifests():
        if manifest.get("default_recommended"):
            return manifest["variant_id"]
    raise ValueError("VARIANT_MANIFEST_MISSING: no default variant")


def unknown_paths(value: Any, prefix: str = "") -> set[str]:
    """Every dotted path whose value is the explicit "unknown" token."""
    found: set[str] = set()
    if isinstance(value, dict):
        for key, item in value.items():
            found |= unknown_paths(item, f"{prefix}.{key}" if prefix else str(key))
    elif isinstance(value, list):
        for index, item in enumerate(value):
            found |= unknown_paths(item, f"{prefix}[{index}]")
    elif value == UNKNOWN:
        found.add(prefix)
    return found


def declared_unknown_paths(manifest: dict) -> set[str]:
    """The paths the manifest itself declares as unknown."""
    return {str(entry.get("field")) for entry in manifest.get("unknown_fields") or []}


def variant_requirements(manifest: dict) -> dict:
    """The admission thresholds of a manifest as a plain dict."""
    requirements = dict(manifest.get("requirements") or {})
    requirements.pop("estimated_or_measured", None)
    return requirements


def catalog_requirements(catalog: dict) -> dict:
    """The admission thresholds declared by a deployment catalog."""
    platform = dict(catalog.get("platform") or {})
    requirements = {
        "os": platform.get("os"),
        "arch": platform.get("arch"),
        "gpu_vendor": platform.get("gpu_vendor"),
        "min_vram_mb": platform.get("min_vram_mb"),
        "min_ram_mb": platform.get("min_ram_mb"),
        "min_commit_mb": platform.get("min_commit_mb"),
        "min_disk_gb": platform.get("min_disk_gb"),
        "min_free_vram_mb": platform.get("min_free_vram_mb"),
        "min_free_ram_mb": platform.get("min_free_ram_mb"),
    }
    return {key: value for key, value in requirements.items() if value is not None}


def requirements_for_catalog(catalog: dict) -> tuple[dict, str | None]:
    """Resolve the thresholds of a catalog: its variant first, then its platform block."""
    variant_id = catalog.get("variant_id")
    if variant_id:
        manifest = load_variant_manifest(variant_id)
        return variant_requirements(manifest), variant_id
    return catalog_requirements(catalog), None


def _selected_gpu(env: dict[str, Any]) -> dict | None:
    """The one card this machine will use.

    Multi-card memory is never summed: the product renders on a single GPU
    that the user selects by UUID, so only that card is compared against a
    threshold.
    """
    cards = [card for card in (env.get("gpus") or []) if isinstance(card, dict)]
    for card in cards:
        if card.get("selected"):
            return card
    if cards:
        return cards[0]
    legacy = env.get("gpu")
    if isinstance(legacy, dict) and legacy.get("status") == "OK":
        return legacy
    return None


def _card_total_vram_mb(card: dict) -> int | None:
    for key in ("vram_total_mb", "vram_mb"):
        value = card.get(key)
        if isinstance(value, int | float):
            return int(value)
    return None


def _card_free_vram_mb(card: dict) -> int | None:
    value = card.get("vram_free_mb")
    if isinstance(value, int | float):
        return int(value)
    return None


def _cc_value(text: str) -> float:
    """A CUDA compute capability such as 8.6 or 12.0 as a number."""
    try:
        return float(str(text))
    except (TypeError, ValueError):
        return 0.0


def _open_requirement(requirements: dict[str, Any], name: str, unknowns: list[dict[str, Any]]) -> bool:
    """True when the requirement itself is still open (its value is unknown)."""
    value = requirements.get(name)
    if isinstance(value, str) and value == UNKNOWN:
        unknowns.append({
            "code": "REQUIREMENT_UNKNOWN",
            "message": f"这套组合的 {name} 门槛还没有核实（unknown），无法判断本机是否满足。",
            "requirement": name,
        })
        return True
    return False


def qualify(requirements: dict[str, Any], env: dict[str, Any]) -> dict[str, Any]:
    """The one host-qualification decision.

    requirements are the declared admission thresholds of a variant (or of a
    catalog platform block); env is a host-facts report.

    The verdict is SUPPORTED only when every declared requirement is
    *measured* to be met.  A fact that could not be read yields UNKNOWN,
    never SUPPORTED; a fact that was read and is too small yields BLOCKED.
    The two are never mixed: a blocked machine is never also an unknown one.
    """
    blockers: list[dict[str, Any]] = []
    unknowns: list[dict[str, Any]] = []
    warnings: list[dict[str, Any]] = []

    want_os = str(requirements.get("os") or UNKNOWN)
    platform = env.get("platform") or {}
    actual_os = platform_id(platform)
    if want_os != UNKNOWN:
        if not actual_os:
            unknowns.append({"code": "OS_UNKNOWN", "message": "读不到本机操作系统，无法核对系统要求。"})
        elif actual_os != want_os:
            blockers.append({
                "code": "OS_UNSUPPORTED",
                "message": f"这套组合要求 {want_os}，本机是 {actual_os}",
                "required": want_os, "actual": actual_os,
            })
    want_arch = str(requirements.get("arch") or UNKNOWN)
    if want_arch != UNKNOWN:
        actual_arch = normalize_arch(platform.get("machine"))
        if not actual_arch:
            unknowns.append({"code": "ARCH_UNKNOWN", "message": "读不到本机架构，无法核对 32/64 位要求。"})
        elif actual_arch != want_arch:
            blockers.append({
                "code": "ARCH_UNSUPPORTED",
                "message": f"这套组合要求 {want_arch} 架构，本机是 {actual_arch}",
                "required": want_arch, "actual": actual_arch,
            })

    card = _selected_gpu(env)
    min_vram = requirements.get("min_vram_mb")
    if min_vram is not None and not _open_requirement(requirements, "min_vram_mb", unknowns):
        if card is None:
            unknowns.append({
                "code": "GPU_NOT_IDENTIFIED",
                "message": "本机显卡没有被识别出来；这不等于没有显卡，但无法据此确认显存要求。",
            })
        else:
            total = _card_total_vram_mb(card)
            if total is None:
                unknowns.append({
                    "code": "GPU_VRAM_UNKNOWN",
                    "message": "识别到显卡，但读不到这张卡的显存总量。",
                })
            elif total < int(min_vram):
                blockers.append({
                    "code": "VRAM_INSUFFICIENT",
                    "message": f"这套组合需要至少 {min_vram} MB 显存，本机这张卡是 {total} MB。"
                               "多卡显存不会自动相加，第一版只使用一张卡。",
                    "required": int(min_vram), "actual": total,
                })
            free = _card_free_vram_mb(card)
            min_free = requirements.get("min_free_vram_mb")
            if (free is not None and isinstance(min_free, int | float)
                    and not isinstance(min_free, bool) and free < int(min_free)):
                warnings.append({
                    "code": "FREE_VRAM_BELOW_FLOOR",
                    "message": f"这张卡当前可用显存 {free} MB，低于开始渲染时的门槛 {int(min_free)} MB；"
                               "下载不受影响，正式开拍前会重新实测。",
                    "required": int(min_free), "actual": free,
                })

    min_ram = requirements.get("min_ram_mb")
    if min_ram is not None and not _open_requirement(requirements, "min_ram_mb", unknowns):
        ram = env.get("ram") or {}
        total_ram = ram.get("ram_mb")
        if ram.get("status") != "OK" or not isinstance(total_ram, int | float):
            unknowns.append({
                "code": "RAM_UNKNOWN",
                "message": "读不到本机物理内存总量，无法核对内存要求。",
            })
        else:
            floor = effective_floor_mb(int(min_ram))
            if int(total_ram) < int(floor):
                blockers.append({
                    "code": "RAM_INSUFFICIENT",
                    "message": f"需要至少 {floor} MB 内存，本机 {int(total_ram)} MB",
                    "required": int(floor), "actual": int(total_ram),
                })

    min_commit = requirements.get("min_commit_mb")
    commit = env.get("commit") or {}
    if min_commit is not None and not _open_requirement(requirements, "min_commit_mb", unknowns):
        total_commit = commit.get("commit_total_mb")
        if commit.get("status") != "OK" or not isinstance(total_commit, int | float):
            unknowns.append({
                "code": "COMMIT_UNKNOWN",
                "message": "读不到本机提交内存（commit）上限，无法核对内存提交要求。",
            })
        elif int(total_commit) < int(min_commit):
            blockers.append({
                "code": "COMMIT_INSUFFICIENT",
                "message": f"需要至少 {int(min_commit)} MB 的提交内存上限，本机是 {int(total_commit)} MB；"
                           "页面文件过小或被关闭时提交上限会明显变小。",
                "required": int(min_commit), "actual": int(total_commit),
            })
    elif (min_commit is None and commit.get("status") == "OK"
            and commit.get("page_file_status") in {"PAGE_FILE_DISABLED", "PAGE_FILE_TOO_SMALL"}):
        warnings.append({
            "code": "PAGE_FILE_RISK",
            "message": "本机页面文件状态异常：" + str(commit.get("page_file_status")) + "；"
                       "渲染期的内存提交余量会变小。",
        })

    min_disk = requirements.get("min_disk_gb")
    if min_disk is not None and not _open_requirement(requirements, "min_disk_gb", unknowns):
        disk = env.get("disk") or {}
        free_gb = disk.get("free_gb")
        if free_gb is None:
            unknowns.append({
                "code": "DISK_UNKNOWN",
                "message": "读不到目标盘剩余空间，无法核对磁盘要求。",
            })
        elif float(free_gb) < float(min_disk):
            blockers.append({
                "code": "DISK_INSUFFICIENT",
                "message": f"需要至少 {min_disk} GB 可用空间，目标盘只剩 {free_gb} GB",
                "required": float(min_disk), "actual": float(free_gb),
            })

    cuda = env.get("cuda") or {}
    required_cc = requirements.get("min_cuda_compute_capability")
    if required_cc is not None and str(required_cc) == UNKNOWN:
        unknowns.append({
            "code": "REQUIREMENT_UNKNOWN",
            "message": "这套组合的 min_cuda_compute_capability 门槛还没有核实（unknown）。",
            "requirement": "min_cuda_compute_capability",
        })
    elif cuda.get("status") != "OK" or not cuda.get("compute_capability"):
        unknowns.append({
            "code": "CUDA_UNKNOWN",
            "message": "读不到本机 CUDA 算力，无法确认这套组合的显卡架构要求。",
        })
    elif required_cc is not None:
        actual_cc = str(cuda.get("compute_capability"))
        if _cc_value(actual_cc) < _cc_value(str(required_cc)):
            blockers.append({
                "code": "CUDA_COMPUTE_CAPABILITY_TOO_LOW",
                "message": f"这套组合要求 CUDA 算力 {required_cc}，本机是 {actual_cc}",
                "required": str(required_cc), "actual": actual_cc,
            })

    if blockers:
        status = STATUS_BLOCKED
    elif unknowns:
        status = STATUS_UNKNOWN
    else:
        status = STATUS_SUPPORTED
    return {
        "status": status,
        "blockers": blockers,
        "unknowns": unknowns,
        "warnings": warnings,
        "requirements": dict(requirements),
        "selected_gpu": {
            "uuid": card.get("uuid"),
            "name": card.get("name"),
            "vram_total_mb": _card_total_vram_mb(card),
            "vram_free_mb": _card_free_vram_mb(card),
        } if card else None,
        "gpus_considered": 1,
        "evidence": {
            "source": "host_facts",
            "probed_at": (env.get("evidence") or {}).get("probed_at"),
            "measured_or_estimated": "measured",
        },
    }
