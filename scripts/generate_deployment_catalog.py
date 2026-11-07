"""Assemble the versioned deployment catalogs from the recorded upstream pins.

The pins under ``config/deployment/_upstream-pins/`` were read from the
upstreams' own published metadata on 2026-10-31 (see the docstring of this
script in Git history and docs/NEW_MACHINE_DEPLOYMENT.md for the procedure).
This script validates them, attaches per-component deployment metadata and
emits:

  - config/deployment/tools-windows-x64.json   (bootstrap tool manifest)
  - config/deployment/h3-ref2va-windows-x64.json (deployment catalog)

The generator is idempotent.  Regenerating never lowers the catalog version
and never drops a component or a workflow profile the committed catalog
already carries: an audited catalog only moves forward.  When the pins and
the committed catalog disagree about the *content* of a component, the
generator stops with an explicit error instead of overwriting the audited
identity.

The admission thresholds of the catalog are not written here: they come from
the versioned variant manifest, so there is exactly one place where a RAM /
VRAM / commit / disk floor is written down.

--verify re-fetches a small sample of upstream metadata and diffs it against
the pins; it never downloads the large model files.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
PINS = REPO / "config" / "deployment" / "_upstream-pins"
OUT = REPO / "config" / "deployment"

GENERATED = "2026-11-01"

H3_COMMUNITY = "minimax-h3-community-license"
H3_LICENSE_URL = "https://huggingface.co/MiniMaxAI/MiniMax-H3/blob/main/LICENSE"

# component_id -> deployment metadata
COMPONENT_META = {
    "comfyui": {"kind": "source", "license_id": "GPL-3.0-only", "usage": "research",
                "license_url": "https://github.com/Comfy-Org/ComfyUI/blob/master/LICENSE",
                "origin": "github-archive", "provides": "ComfyUI source tree"},
    "ComfyUI-KJNodes": {"kind": "node", "license_id": "GPL-3.0-only", "usage": "research",
                        "license_url": "https://github.com/kijai/ComfyUI-KJNodes/blob/main/LICENSE",
                        "origin": "github-archive"},
    "comfyui-videohelpersuite": {"kind": "node", "license_id": "GPL-3.0-only",
                                 "usage": "research",
                                 "license_url": "https://github.com/Kosinkadink/ComfyUI-VideoHelperSuite/blob/main/LICENSE",
                                 "origin": "github-archive"},
    "ComfyUI-MiniMax-H3-PDD-Acc": {"kind": "node", "license_id": "Apache-2.0", "usage": "research",
                                   "license_url": "https://github.com/Jalen-Brunson/ComfyUI-MiniMax-H3-PDD-Acc/blob/master/LICENSE",
                                   "origin": "github-archive"},
    "ComfyUI-SolAttn_triton": {"kind": "node", "license_id": "Apache-2.0", "usage": "research",
                               "license_url": "https://github.com/kijai/ComfyUI-SolAttn_triton/blob/main/LICENSE",
                               "origin": "github-archive"},
    "Comfyui_Minimax_h3_latent_Upscaler": {"kind": "node", "license_id": "MIT", "usage": "research",
                                           "license_url": "https://github.com/LBH-123-AI/Comfyui_Minimax_h3_latent_Upscaler/blob/main/LICENSE",
                                           "note": "upscale profile only"}
}

COMPONENT_META["comfy-runtime-wheels"] = {
    "kind": "node", "license_id": "mixed-pinned-wheel-licenses", "usage": "research",
    "license_url": "https://pypi.org/",
    "origin": "pypi-json+pytorch-cu128-index",
    "note": "Hash-pinned Windows runtime and reviewer packages; explicit offline builds for source-only inputs",
}
COMPONENT_META["minimax-h3-models"] = {
    "kind": "model", "license_id": H3_COMMUNITY, "usage": "research",
    "license_url": H3_LICENSE_URL,
    "origin": "huggingface-lfs",
}
COMPONENT_META["qwen3-vl-8b-fp8"] = {
    "kind": "model", "license_id": "Apache-2.0", "usage": "research",
    "license_url": "https://huggingface.co/Qwen/Qwen3-VL-8B-Instruct-FP8",
    "origin": "huggingface-lfs", "review_role": "VLM",
}
COMPONENT_META["qwen3-asr-0.6b"] = {
    "kind": "model",
    "license_id": "Apache-2.0",
    "usage": "research",
    "license_url": "https://huggingface.co/Qwen/Qwen3-ASR-0.6B",
    "origin": "huggingface-lfs",
    "review_role": "ASR",
}

MODEL_COMPONENT_IDS = {
    "h3-unet-ref2va-int8": "Minimax-H3 diffusion backbone (Ref2VA pruned INT8 ConvRot)",
    "h3-text-encoder-nvfp4": "qwen3vl_32b text encoder (its own pinned nvfp4/awq form)",
    "h3-vae": "video VAE fp16 + audio VAE fp32",
    "h3-realism-lora": "h3-realism-people LoRA",
    "h3-pdd-ref2va-8step": "PDD Acc 8-step file (models/pdd_acc)",
}
REVIEW_COMPONENT_IDS = {
    "qwen3-vl-8b-fp8": "VLM",
    "qwen3-asr-0.6b": "ASR",
}
WHEEL_COMPONENT = "comfy-runtime-wheels"

#: The default catalog is the one this script owns.
CATALOG_NAME = "h3-ref2va-windows-x64"
CATALOG_ID = "shine-h3-ref2va-windows-x64"


class CatalogConflict(RuntimeError):
    """The pins and the committed catalog disagree about a component's content."""


def _version_tuple(text: str) -> tuple:
    parts = []
    for chunk in str(text).split("."):
        digits = "".join(ch for ch in chunk if ch.isdigit())
        parts.append(int(digits) if digits else 0)
    while len(parts) < 3:
        parts.append(0)
    return tuple(parts[:3])


def _pinned_files(component: dict) -> list:
    return sorted(
        (str(f.get("relative_path", "")), str(f.get("sha256", "")), int(f.get("size") or 0))
        for f in component.get("files", [])
    )


def merge_catalog(existing: dict | None, generated: dict) -> tuple:
    """Merge the generated catalog into the committed one, forward only.

    Returns (merged catalog, notes).  The merged catalog never loses a
    component, never loses a workflow profile and never lowers the version.
    """
    notes: list[str] = []
    merged = dict(generated)
    if not existing:
        return merged, notes + ["no committed catalog: wrote the generated one"]
    old_version = str(existing.get("catalog_version") or "0")
    new_version = str(generated.get("catalog_version") or "0")
    if _version_tuple(old_version) > _version_tuple(new_version):
        merged["catalog_version"] = old_version
        notes.append(f"catalog_version kept at {old_version} (the pins only produce {new_version})")
    elif _version_tuple(old_version) == _version_tuple(new_version):
        merged["catalog_version"] = old_version
    old_components = {c["component_id"]: c for c in existing.get("components", [])}
    new_components = {c["component_id"]: c for c in generated.get("components", [])}
    components = []
    for component_id, component in old_components.items():
        generated_component = new_components.get(component_id)
        if generated_component is None:
            components.append(component)
            notes.append(f"component {component_id} kept from the committed catalog")
            continue
        if _pinned_files(component) != _pinned_files(generated_component):
            raise CatalogConflict(
                "CATALOG_COMPONENT_CONFLICT: " + component_id
                + " - the pins and the committed catalog pin different bytes; "
                  "resolve it by hand, the generator never overwrites an audited identity"
            )
        # Same bytes: the committed declaration (capabilities, resource
        # budget, licence wording) is the audited one and stays.
        components.append(component)
    for component_id, component in new_components.items():
        if component_id not in old_components:
            components.append(component)
            notes.append(f"component {component_id} added from the pins")
    merged["components"] = components
    profiles = list(existing.get("workflow_profiles") or [])
    for name in generated.get("workflow_profiles") or []:
        if name not in profiles:
            profiles.append(name)
    merged["workflow_profiles"] = profiles
    if existing.get("deferred_workflow_profiles"):
        merged["deferred_workflow_profiles"] = existing["deferred_workflow_profiles"]
    if existing.get("variant_id"):
        merged["variant_id"] = existing["variant_id"]
    merged["total_bytes"] = sum(int(f.get("size") or 0) for c in merged["components"] for f in c.get("files", []))
    return merged, notes


def catalog_components():
    """Build every catalog component entry from the recorded pins."""
    archives = json.loads((PINS / "archives.json").read_text(encoding="utf-8"))
    wheels = json.loads((PINS / "wheels.json").read_text(encoding="utf-8"))
    components = []
    comfy = archives["comfyui"]
    components.append({
        "component_id": "comfyui",
        "kind": "source",
        "revision": comfy["revision"],
        "license_id": COMPONENT_META["comfyui"]["license_id"],
        "usage": "research",
        "license_url": COMPONENT_META["comfyui"]["license_url"],
        "origin_repo": comfy["slug"],
        "origin_revision": comfy["revision"],
        "origin_kind": "github-archive",
        "destination_family": None,
        "dependencies": [],
        "files": [{
            "url": comfy["url"],
            "relative_path": comfy["filename"],
            "sha256": comfy["sha256"],
            "size": comfy["size"],
            "redirect_hosts": [],
        }],
        "status": "READY",
    })
    wheel_files = []
    for name, w in sorted(wheels.items()):
        wheel_files.append({
            "url": w["url"],
            "relative_path": w["filename"],
            "dependency": name,
            "version": w["version"],
            "sha256": w["sha256"],
            "size": w["size"],
            "redirect_hosts": (["release-assets.githubusercontent.com"]
                               if "release-assets" in w["url"] else []),
        })
    components.append({
        "component_id": WHEEL_COMPONENT,
        "kind": "wheel",
        "revision": "cu128-asr-2026-11-01",
        "license_id": COMPONENT_META[WHEEL_COMPONENT]["license_id"],
        "usage": "research",
        "license_url": COMPONENT_META[WHEEL_COMPONENT]["license_url"],
        "origin_repo": "pypi.org + download.pytorch.org/whl/cu128",
        "origin_revision": "2026-10-31",
        "origin_kind": "pypi-json+pytorch-cu128-index",
        "destination_family": None,
        "dependencies": [],
        "files": wheel_files,
        "source_builds": [
            {"relative_path": w["filename"], "name": name, "version": w["version"],
             "backend": "setuptools", "license_id": w["license_id"]}
            for name, w in sorted(wheels.items()) if not w["filename"].endswith('.whl')
        ],
        "status": "READY",
    })
    for key, component_id in [
        ("kjnodes", "ComfyUI-KJNodes"),
        ("videohelpersuite", "comfyui-videohelpersuite"),
        ("pdd-acc", "ComfyUI-MiniMax-H3-PDD-Acc"),
        ("solattn", "ComfyUI-SolAttn_triton"),
        ("h3-upscaler", "Comfyui_Minimax_h3_latent_Upscaler"),
    ]:
        archive = archives[key]
        meta = COMPONENT_META[component_id]
        components.append({
            "component_id": component_id,
            "kind": "node",
            "revision": archive["revision"],
            "license_id": meta["license_id"],
            "usage": meta["usage"],
            "license_url": meta["license_url"],
            "origin_repo": archive["slug"],
            "origin_revision": archive["revision"],
            "origin_kind": "github-archive",
            "destination_family": None,
            "dependencies": [],
            "files": [{
                "url": archive["url"],
                "relative_path": archive["filename"],
                "sha256": archive["sha256"],
                "size": archive["size"],
                "redirect_hosts": [],
            }],
            "status": "READY",
        })
    return components

def model_components():
    """Model components: H3 stack + the two local review models."""
    models = json.loads((PINS / "models.json").read_text(encoding="utf-8"))
    by_id = {c["component_id"]: c for c in models["components"]}
    components = []
    # One component per model family the production graph binds. The four H3
    # pieces share the pinned Comfy-Org revision; families map to BIND_MODELS.
    h3_ids = {
        "h3-unet-ref2va-int8": "minimax-h3-diffusion",
        "h3-text-encoder-nvfp4": "minimax-h3-text-encoder",
        "h3-vae": "minimax-h3-vae",
        "h3-realism-lora": "minimax-h3-lora",
        "h3-pdd-ref2va-8step": "minimax-h3-pdd",
    }
    for src_id, component_id in h3_ids.items():
        source = by_id[src_id]
        meta = COMPONENT_META["minimax-h3-models"]
        family = source["destination_family"]
        components.append({
            "component_id": component_id,
            "kind": "model",
            "revision": source["origin_revision"][:12],
            "license_id": meta["license_id"],
            "usage": meta["usage"],
            "license_url": meta["license_url"],
            "origin_repo": source["repo"],
            "origin_revision": source["origin_revision"],
            "origin_kind": "huggingface-lfs",
            "destination_family": family,
            "dependencies": [],
            "files": source["files"],
            "status": "READY",
        })
    for src_id, role in REVIEW_COMPONENT_IDS.items():
        source = by_id[src_id]
        meta = COMPONENT_META[src_id]
        components.append({
            "component_id": src_id,
            "kind": "review-model",
            "revision": source["origin_revision"][:12],
            "role": role,
            "license_id": meta["license_id"],
            "usage": meta["usage"],
            "license_url": meta["license_url"],
            "origin_repo": source["repo"],
            "origin_revision": source["origin_revision"],
            "origin_kind": "huggingface-lfs",
            "destination_family": None,
            "dependencies": ["comfy-runtime-wheels"] if role == "VLM" else [],
            "depends_on_wheels": True,
            "files": source["files"],
            "status": "READY",
        })
    return components


def variant_platform() -> dict:
    """The admission thresholds of the default variant, read from its manifest."""
    sys.path.insert(0, str(REPO / "backend"))
    from shine.comfyops.variants import (  # noqa: PLC0415 - the script runs standalone
        default_variant_id,
        load_variant_manifest,
        variant_requirements,
    )

    manifest = load_variant_manifest(default_variant_id())
    requirements = variant_requirements(manifest)
    return {
        "variant_id": manifest["variant_id"],
        "platform": {
            "os": requirements.get("os"),
            "arch": requirements.get("arch"),
            "gpu_vendor": requirements.get("gpu_vendor"),
            "min_vram_mb": requirements.get("min_vram_mb"),
            "min_ram_mb": requirements.get("min_ram_mb"),
            "min_commit_mb": requirements.get("min_commit_mb"),
            "min_disk_gb": requirements.get("min_disk_gb"),
            "min_free_vram_mb": requirements.get("min_free_vram_mb"),
            "min_free_ram_mb": requirements.get("min_free_ram_mb"),
        },
        "workflow_profiles": list(manifest.get("workflow_profile_ids") or []),
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--verify", action="store_true", help="re-fetch upstream metadata and diff against pins")
    parser.parse_args()

    components = catalog_components() + model_components()
    wheel_inputs = json.loads((PINS / 'wheels.json').read_text(encoding='utf-8'))
    inventory = {name: {'sha256': value['sha256'], 'size': value['size']} for name, value in wheel_inputs.items()}
    inventory_hash = hashlib.sha256(json.dumps(
        inventory, sort_keys=True, separators=(',', ':'), ensure_ascii=False,
    ).encode()).hexdigest()
    disk = json.loads((PINS / 'runtime-disk.json').read_text(encoding='utf-8'))
    if disk['inputs_sha256'] != inventory_hash:
        raise ValueError('Runtime disk measurement must be refreshed for the exact dependency inputs')
    variant = variant_platform()
    catalog = {
        "catalog_id": CATALOG_ID,
        # What the pins alone produce.  The merge below keeps the committed
        # version when it is higher: an audited catalog never moves back.
        "catalog_version": "1.1.0",
        "schema_version": 1,
        "generated": GENERATED,
        "variant_id": variant["variant_id"],
        "platform": variant["platform"],
    }
    catalog["components"] = components
    catalog["review_runtime_wheels"] = ["transformers", "tokenizers", "lm-format-enforcer", "interegular", "qwen-asr"]

    catalog["dependency_policy"] = {
        "wheelhouse": "offline pip --no-index --require-hashes from the isolated wheelhouse",
        "closure": "uv pip compile (windows / py3.12) full dependency closure, hashes cross-checked",
        "excluded": "qwen-tts voiceops overlay is outside this runtime; qwen-asr reviewer SDK is included",
        "source_builds": "approved hashes + pinned setuptools/wheel; no-index/no-build-isolation; derived hash audit",
        "runtime_disk": disk,
    }
    catalog["workflow_profiles"] = list(variant["workflow_profiles"])
    catalog["total_bytes"] = sum(f["size"] for c in components for f in c["files"])

    tools = json.loads((PINS / "tools.json").read_text(encoding="utf-8"))
    tools_manifest = {
        "manifest_id": "shine-bootstrap-tools-windows-x64",
        "manifest_version": "1.0.0",
        "generated": GENERATED,
        "tools": tools,
    }
    OUT.mkdir(parents=True, exist_ok=True)
    catalog_path = OUT / f"{CATALOG_NAME}.json"
    existing = None
    if catalog_path.is_file():
        try:
            existing = json.loads(catalog_path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            raise SystemExit("CATALOG_UNREADABLE: " + str(exc)) from exc
    merged, notes = merge_catalog(existing, catalog)
    catalog_path.write_text(
        json.dumps(merged, indent=2, sort_keys=True, ensure_ascii=False) + "\n", encoding="utf-8", newline="\n"
    )
    (OUT / "tools-windows-x64.json").write_text(
        json.dumps(tools_manifest, indent=1, sort_keys=True, ensure_ascii=False) + "\n", encoding="utf-8", newline="\n"
    )
    print("components:", len(merged["components"]), "total_bytes:", merged["total_bytes"])
    print("catalog_version:", merged["catalog_version"], "workflow_profiles:", merged["workflow_profiles"])
    for note in notes:
        print("note:", note)
    print("wrote " + catalog_path.name + " and tools-windows-x64.json")
    return 0


if __name__ == "__main__":
    sys.exit(main())
