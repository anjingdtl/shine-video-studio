"""The audited Qwen-Image-2.1 text-to-image API graph.

Provenance is in ``docs/upstream-audit.md`` (2026-11-05 section).  Every node
class and input name below was read from the pinned local ComfyUI ``v0.39.0``
source, and every parameter default was read from the pinned official template
``image_qwen_image_2_1_t2i.json`` at commit ``9fae1041``.  Nothing here is a
guess: the graph is re-validated against a live ``object_info`` before it may be
used, and a real small-sample render is what promotes a family to READY.

Two deliberate, documented deviations from the UI template:

1. **The prompt-expansion branch is omitted.**  The template ships
   ``switch=false``, and ``ComfySwitchNode`` declares both branches ``lazy=True``
   with a ``check_lazy_status`` that only requests ``on_false`` when the switch is
   false.  ``TextGenerate`` and its 9B PE text encoder are therefore never
   evaluated.  Shine always sends a deterministic server-composed prompt, so the
   branch is dead weight in the API graph and its 9.47 GB encoder is not
   downloaded.  ``TextGenerate``/``ComfySwitchNode``/``PreviewAny``/
   ``PrimitiveStringMultiline`` are omitted with it.
2. **``SaveImageAdvanced`` becomes core ``SaveImage``.**  The advanced node's
   ``format`` input is a ``DynamicCombo`` whose API serialization is not part of
   the audited evidence, while the template's own widget values (``png``,
   ``8-bit``, ``sRGB``) are exactly ``SaveImage``'s default output.  Same bytes,
   one unambiguous output node.

The UI template is never submitted to ``/prompt``: it is a subgraph-bearing UI
document (``version 0.4``), and only this expanded API graph is.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any

from shine.comfy.workflow import graph_hash

TEMPLATE_REPOSITORY = "Comfy-Org/workflow_templates"
TEMPLATE_PATH = "templates/image_qwen_image_2_1_t2i.json"
TEMPLATE_COMMIT = "9fae1041be7ecb53bbd7c06533c80aa038f4af93"
TEMPLATE_SHA256 = "5f2b50eeaf27e56a45456681ad5319df1ae51243d9653ac3f92c7dc05d1e1503"
TEMPLATE_BYTES = 78_494
TEMPLATE_SUBGRAPH_ID = "c291ceec-b98f-4751-9d0b-7bc288f27b30"

MODEL_REPOSITORY = "Comfy-Org/Qwen-Image-2.1"
MODEL_REVISION = "aacf31a6d48e707f352ecba714cee0680da3479b"

MODEL_FAMILY = "Qwen-Image-2.1"
GENERATION_MODE = "T2I"
H3_CATEGORY = "QWEN_IMAGE_T2I"
"""Distinct from every ``H3_*`` category: an image workflow is not an H3 workflow."""

# Node ids are kept from the template so the expanded graph stays auditable
# against the UI document it came from.
UNET_NODE = "451"
CACHE_NODE = "480"
CLIP_NODE = "453"
VAE_NODE = "454"
TEXT_ENCODE_NODE = "452"
EMPTY_LATENT_NODE = "456"
SAMPLER_NODE = "458"
VAE_DECODE_NODE = "457"
OUTPUT_NODE = "461"

OUTPUT_CLASS_TYPE = "SaveImage"
"""The single declared final output node.

Only this node's images may be harvested.  The template's inner ``PreviewAny``
node is a preview and is not in this graph at all; harvesting "the first
``type == 'output'`` image in history" would reintroduce exactly the ambiguity
the declared output node exists to remove.
"""

REQUIRED_NODES = [
    "UNETLoader",
    "QwenImage21Cache",
    "CLIPLoader",
    "VAELoader",
    "TextEncodeQwenImage21",
    "EmptyLatentImage",
    "KSampler",
    "VAEDecode",
    OUTPUT_CLASS_TYPE,
]

UNET_FILE = "qwen_image_2.1_int8_convrot.safetensors"
CLIP_FILE = "qwen3vl_8b_int8_convrot.safetensors"
VAE_FILE = "qwen_image_2.1_vae_bf16.safetensors"

MODEL_FILES: tuple[dict[str, Any], ...] = (
    {
        "role": "unet",
        "folder": "diffusion_models",
        "filename": UNET_FILE,
        "bytes": 7_256_783_064,
        "sha256": "cb74113cb03faecd79611b01fd7fd642f0aa60d6f0b95086abee214d75eaa57d",
        "url": f"https://huggingface.co/{MODEL_REPOSITORY}/resolve/{MODEL_REVISION}/diffusion_models/{UNET_FILE}",
        "required": True,
    },
    {
        "role": "clip",
        "folder": "text_encoders",
        "filename": CLIP_FILE,
        "bytes": 9_350_798_360,
        "sha256": "8bfd0f6e12abf2d2d697ecc888e5e90b0d6741d6708f05799f53afa560452e8f",
        "url": f"https://huggingface.co/{MODEL_REPOSITORY}/resolve/{MODEL_REVISION}/text_encoders/{CLIP_FILE}",
        "required": True,
    },
    {
        "role": "vae",
        "folder": "vae",
        "filename": VAE_FILE,
        "bytes": 675_509_688,
        "sha256": "bb21f7473051e1ac368515dd3f2e15cd44d7a11748ee8823e1ddca3e4876b7c9",
        "url": f"https://huggingface.co/{MODEL_REPOSITORY}/resolve/{MODEL_REVISION}/vae/{VAE_FILE}",
        "required": True,
    },
    {
        # Present in the template, unreachable in it: the PE branch is lazily
        # skipped because switch=false.  Recorded so an operator can see why it
        # is not downloaded, not so a build can silently require it.
        "role": "pe_text_encoder",
        "folder": "text_encoders",
        "filename": "qwen3.5_9b_qwen_image_2.1_pe_t2i.int8_convrot.safetensors",
        "bytes": 9_471_072_252,
        "sha256": "9182abae56fe05459840a86d22abd21f972061c92fce032630af680c8c5178d3",
        "url": (
            f"https://huggingface.co/{MODEL_REPOSITORY}/resolve/{MODEL_REVISION}/"
            "text_encoders/qwen3.5_9b_qwen_image_2.1_pe_t2i.int8_convrot.safetensors"
        ),
        "required": False,
        "not_required_because": "prompt-expansion branch is lazily skipped at switch=false",
    },
)

REQUIRED_MODEL_BYTES = sum(entry["bytes"] for entry in MODEL_FILES if entry["required"])

# Audited template defaults. `sampler_name`/`scheduler` come from the template
# widgets `euler`/`simple`; the template's own COMBO input names are misleadingly
# `scheduler`/`scheduler_1`, and the inner links show which KSampler input each
# one really feeds. `cfg=1` and `steps=25` are the template's shipped values.
DEFAULT_STEPS = 25
DEFAULT_CFG = 1.0
DEFAULT_SAMPLER = "euler"
DEFAULT_SCHEDULER = "simple"
DEFAULT_DENOISE = 1.0
DEFAULT_RESOLUTION = 1024
DEFAULT_WEIGHT_DTYPE = "default"
DEFAULT_CACHE_DEVICE = "auto"
DEFAULT_CACHE_DTYPE = "default"
DEFAULT_CLIP_TYPE = "qwen_image"
DEFAULT_CLIP_DEVICE = "default"
FILENAME_PREFIX = "shine-material"

MAX_SEED = 0xFFFFFFFFFFFFFFFF
"""KSampler's own declared bound. The template ships 447606998181262, a 48-bit
seed, so the ledger stores seeds as BIGINT and a 32-bit column would corrupt it."""

PARAMETER_MAP: dict[str, str] = {
    "prompt": f"{TEXT_ENCODE_NODE}.inputs.prompt",
    "negative_prompt": f"{TEXT_ENCODE_NODE}.inputs.negative_prompt",
    "resolution": f"{TEXT_ENCODE_NODE}.inputs.resolution",
    "width": f"{EMPTY_LATENT_NODE}.inputs.width",
    "height": f"{EMPTY_LATENT_NODE}.inputs.height",
    "seed": f"{SAMPLER_NODE}.inputs.seed",
    "steps": f"{SAMPLER_NODE}.inputs.steps",
    "cfg": f"{SAMPLER_NODE}.inputs.cfg",
    "sampler_name": f"{SAMPLER_NODE}.inputs.sampler_name",
    "scheduler": f"{SAMPLER_NODE}.inputs.scheduler",
    "denoise": f"{SAMPLER_NODE}.inputs.denoise",
    "filename_prefix": f"{OUTPUT_NODE}.inputs.filename_prefix",
}
"""Same ``<node_id>.inputs.<field>`` convention the H3 profiles already use.

``width``/``height`` are mapped explicitly: an unmapped size is a refusal
(``WORKFLOW_SIZE_NOT_MAPPED``), never a silent render at the graph's own size.
"""


def base_graph(
    *,
    unet_name: str = UNET_FILE,
    clip_name: str = CLIP_FILE,
    vae_name: str = VAE_FILE,
) -> dict[str, Any]:
    """The expanded API graph at its audited defaults.

    Callers apply per-shot values through ``apply_parameters`` with
    ``PARAMETER_MAP``; they never edit this structure, so the graph hash stays a
    stable identity for the runtime fingerprint.
    """
    return {
        UNET_NODE: {
            "class_type": "UNETLoader",
            "inputs": {"unet_name": unet_name, "weight_dtype": DEFAULT_WEIGHT_DTYPE},
        },
        CACHE_NODE: {
            "class_type": "QwenImage21Cache",
            "inputs": {
                "model": [UNET_NODE, 0],
                "device": DEFAULT_CACHE_DEVICE,
                "dtype": DEFAULT_CACHE_DTYPE,
            },
        },
        CLIP_NODE: {
            "class_type": "CLIPLoader",
            "inputs": {"clip_name": clip_name, "type": DEFAULT_CLIP_TYPE, "device": DEFAULT_CLIP_DEVICE},
        },
        VAE_NODE: {"class_type": "VAELoader", "inputs": {"vae_name": vae_name}},
        TEXT_ENCODE_NODE: {
            "class_type": "TextEncodeQwenImage21",
            "inputs": {
                "clip": [CLIP_NODE, 0],
                "prompt": "",
                "negative_prompt": "",
                "resolution": DEFAULT_RESOLUTION,
            },
        },
        EMPTY_LATENT_NODE: {
            "class_type": "EmptyLatentImage",
            "inputs": {"width": DEFAULT_RESOLUTION, "height": DEFAULT_RESOLUTION, "batch_size": 1},
        },
        SAMPLER_NODE: {
            "class_type": "KSampler",
            "inputs": {
                "model": [CACHE_NODE, 0],
                "seed": 0,
                "steps": DEFAULT_STEPS,
                "cfg": DEFAULT_CFG,
                "sampler_name": DEFAULT_SAMPLER,
                "scheduler": DEFAULT_SCHEDULER,
                "positive": [TEXT_ENCODE_NODE, 0],
                "negative": [TEXT_ENCODE_NODE, 1],
                "latent_image": [EMPTY_LATENT_NODE, 0],
                "denoise": DEFAULT_DENOISE,
            },
        },
        VAE_DECODE_NODE: {
            "class_type": "VAEDecode",
            "inputs": {"samples": [SAMPLER_NODE, 0], "vae": [VAE_NODE, 0]},
        },
        OUTPUT_NODE: {
            "class_type": OUTPUT_CLASS_TYPE,
            "inputs": {"images": [VAE_DECODE_NODE, 0], "filename_prefix": FILENAME_PREFIX},
        },
    }


BASE_GRAPH_SHA256 = graph_hash(base_graph())


def runtime_fingerprint(
    *,
    graph_sha256: str,
    model_files: list[dict[str, Any]],
    instance_identity: dict[str, Any] | None = None,
    prompt_template_version: str | None = None,
) -> str:
    """One hash over everything that would change a generated image's meaning.

    Readiness evidence is bound to this fingerprint: a new graph, a new model
    file, a new runtime or a new prompt template invalidates the evidence rather
    than silently inheriting an older READY.
    """
    material = {
        "graph_sha256": graph_sha256,
        "model_family": MODEL_FAMILY,
        "generation_mode": GENERATION_MODE,
        "template_commit": TEMPLATE_COMMIT,
        "template_sha256": TEMPLATE_SHA256,
        "model_repository": MODEL_REPOSITORY,
        "model_revision": MODEL_REVISION,
        "model_files": sorted(
            (
                {
                    "role": entry.get("role"),
                    "filename": entry.get("filename"),
                    "bytes": entry.get("bytes"),
                    "sha256": entry.get("sha256"),
                }
                for entry in model_files
            ),
            key=lambda item: str(item.get("role")),
        ),
        "instance_identity": instance_identity or {},
        "prompt_template_version": prompt_template_version or "",
    }
    blob = json.dumps(material, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


def declared_output_slot(history_entry: dict[str, Any]) -> dict[str, Any]:
    """The images of the ONE declared output node, or an explicit mismatch.

    ComfyUI history is external data. A missing declared node, an absent images
    list, or a second image-producing node in the same history are all refused
    instead of resolved by picking whichever image happens to be first.
    """
    from shine.comfy.client import ComfyWorkflowError

    outputs = history_entry.get("outputs")
    if not isinstance(outputs, dict):
        raise ComfyWorkflowError(
            "IMAGE_OUTPUT_NODE_MISMATCH", details={"reason": "history has no outputs object"}
        )
    declared = outputs.get(OUTPUT_NODE)
    if not isinstance(declared, dict):
        raise ComfyWorkflowError(
            "IMAGE_OUTPUT_NODE_MISMATCH",
            details={"reason": "declared output node is absent from history", "expected_node": OUTPUT_NODE,
                     "present_nodes": sorted(outputs)},
        )
    producing = sorted(
        node_id
        for node_id, payload in outputs.items()
        if isinstance(payload, dict) and payload.get("images")
    )
    if producing != [OUTPUT_NODE]:
        raise ComfyWorkflowError(
            "IMAGE_OUTPUT_NODE_MISMATCH",
            details={"reason": "more than the declared node produced images", "producing": producing},
        )
    images = declared.get("images")
    if not isinstance(images, list) or not images:
        raise ComfyWorkflowError(
            "IMAGE_OUTPUT_NODE_MISMATCH",
            details={"reason": "declared output node produced no image entries", "expected_node": OUTPUT_NODE},
        )
    if len(images) != 1:
        raise ComfyWorkflowError(
            "IMAGE_OUTPUT_NODE_MISMATCH",
            details={"reason": "declared output node produced an ambiguous image count", "count": len(images)},
        )
    entry = images[0]
    if not isinstance(entry, dict) or entry.get("type") != "output" or not entry.get("filename"):
        raise ComfyWorkflowError(
            "IMAGE_OUTPUT_NODE_MISMATCH",
            details={"reason": "declared output entry is not a real output image", "entry_keys": sorted(entry)},
        )
    return entry
