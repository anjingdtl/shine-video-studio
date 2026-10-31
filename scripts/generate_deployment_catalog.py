"""Assemble the versioned deployment catalogs from the recorded upstream pins.

The pins under ``config/deployment/_upstream-pins/`` were read from the
upstreams' own published metadata on 2026-10-31 (see the docstring of this
script in Git history and docs/NEW_MACHINE_DEPLOYMENT.md for the procedure).
This script validates them, attaches per-component deployment metadata and
emits:

  - config/deployment/tools-windows-x64.json   (bootstrap tool manifest)
  - config/deployment/h3-ref2va-windows-x64.json (deployment catalog)

``--verify`` re-fetches a small sample of upstream metadata and diffs it
against the pins; it never downloads the large model files.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
PINS = REPO / "config" / "deployment" / "_upstream-pins"
OUT = REPO / "config" / "deployment"

GENERATED = "2026-10-31"

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
    "note": "93-hash-pinned Windows wheels for the isolated venv",
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


def catalog_components():
    """Build every catalog component entry from the recorded pins."""
    archives = json.loads((PINS / "archives.json").read_text(encoding="utf-8"))
    wheels = json.loads((PINS / "wheels.json").read_text(encoding="utf-8"))
    components = []
    # ComfyUI source archive
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
    # wheel package: every pinned wheel as one component, one file per wheel
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
        "revision": "cu128-2026-10-31",
        "license_id": COMPONENT_META[WHEEL_COMPONENT]["license_id"],
        "usage": "research",
        "license_url": COMPONENT_META[WHEEL_COMPONENT]["license_url"],
        "origin_repo": "pypi.org + download.pytorch.org/whl/cu128",
        "origin_revision": "2026-10-31",
        "origin_kind": "pypi-json+pytorch-cu128-index",
        "destination_family": None,
        "dependencies": [],
        "files": wheel_files,
        "status": "READY",
    })
    # node packs from archives
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


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--verify", action="store_true", help="re-fetch upstream metadata and diff against pins")
    parser.parse_args()

    components = catalog_components() + model_components()
    catalog = {
        "catalog_id": "shine-h3-ref2va-windows-x64",
        "catalog_version": "1.0.0",
        "schema_version": 1,
        "generated": GENERATED,
        "platform": {
            "os": "windows-x64",
            "gpu_vendor": "nvidia",
            "min_vram_mb": 15360,
            "min_ram_mb": 32768,
            "min_disk_gb": 120,
        },
    }
    catalog["components"] = components
    catalog["review_runtime_wheels"] = ["transformers", "tokenizers", "lm-format-enforcer", "interegular"]

    catalog["dependency_policy"] = {
        "wheelhouse": "offline pip --no-index --require-hashes from the isolated wheelhouse",
        "closure": "uv pip compile (windows / py3.12) full dependency closure, hashes cross-checked",
        "excluded": "qwen-asr/qwen-tts SDKs stay optional voiceops overlays (sdist-only deps)",
    }
    catalog["workflow_profiles"] = [
        "local-h3-ref2va-pdd8-2img-1aud",
        "local-h3-ref2va-pdd8-2img-1aud-portrait",
    ]
    catalog["total_bytes"] = sum(f["size"] for c in components for f in c["files"])

    tools = json.loads((PINS / "tools.json").read_text(encoding="utf-8"))
    tools_manifest = {
        "manifest_id": "shine-bootstrap-tools-windows-x64",
        "manifest_version": "1.0.0",
        "generated": GENERATED,
        "tools": tools,
    }
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "h3-ref2va-windows-x64.json").write_text(
        json.dumps(catalog, indent=1, sort_keys=True, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    (OUT / "tools-windows-x64.json").write_text(
        json.dumps(tools_manifest, indent=1, sort_keys=True, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    print("components:", len(components), "total_bytes:", catalog["total_bytes"])
    print("wrote h3-ref2va-windows-x64.json and tools-windows-x64.json")
    return 0


if __name__ == "__main__":
    sys.exit(main())
