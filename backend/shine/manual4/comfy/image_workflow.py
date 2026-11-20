"""Mirrored Qwen-Image-2.1 T2I API graph for Manual4 (from shine/materialprep/qwen_workflow.py)."""

from __future__ import annotations

from typing import Any

from shine.manual4.comfy.workflow import graph_hash

MODEL_FAMILY = "Qwen-Image-2.1"
GENERATION_MODE = "T2I"
OUTPUT_NODE = "461"
OUTPUT_CLASS_TYPE = "SaveImage"

UNET_FILE = "qwen_image_2.1_int8_convrot.safetensors"
CLIP_FILE = "qwen3vl_8b_int8_convrot.safetensors"
VAE_FILE = "qwen_image_2.1_vae_bf16.safetensors"

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

DEFAULT_STEPS = 25
DEFAULT_CFG = 1.0
DEFAULT_SAMPLER = "euler"
DEFAULT_SCHEDULER = "simple"
DEFAULT_RESOLUTION = 1024


def build_qwen_t2i_graph(
    *,
    prompt: str,
    seed: int = 1,
    width: int = DEFAULT_RESOLUTION,
    height: int = DEFAULT_RESOLUTION,
    steps: int = DEFAULT_STEPS,
    cfg: float = DEFAULT_CFG,
) -> dict[str, Any]:
    return {
        "451": {
            "class_type": "UNETLoader",
            "inputs": {"unet_name": UNET_FILE, "weight_dtype": "default"},
        },
        "480": {
            "class_type": "QwenImage21Cache",
            "inputs": {"model": ["451", 0], "threshold": 0.12, "max_skip": 3},
        },
        "453": {
            "class_type": "CLIPLoader",
            "inputs": {"clip_name": CLIP_FILE, "type": "qwen_image", "device": "default"},
        },
        "454": {
            "class_type": "VAELoader",
            "inputs": {"vae_name": VAE_FILE},
        },
        "452": {
            "class_type": "TextEncodeQwenImage21",
            "inputs": {"clip": ["453", 0], "prompt": prompt},
        },
        "455": {
            "class_type": "ConditioningZeroOut",
            "inputs": {"conditioning": ["452", 0]},
        },
        "456": {
            "class_type": "EmptyLatentImage",
            "inputs": {"width": int(width), "height": int(height), "batch_size": 1},
        },
        "458": {
            "class_type": "KSampler",
            "inputs": {
                "model": ["480", 0],
                "positive": ["452", 0],
                "negative": ["455", 0],
                "latent_image": ["456", 0],
                "seed": int(seed),
                "steps": int(steps),
                "cfg": float(cfg),
                "sampler_name": DEFAULT_SAMPLER,
                "scheduler": DEFAULT_SCHEDULER,
                "denoise": 1.0,
            },
        },
        "457": {
            "class_type": "VAEDecode",
            "inputs": {"samples": ["458", 0], "vae": ["454", 0]},
        },
        "461": {
            "class_type": OUTPUT_CLASS_TYPE,
            "inputs": {"images": ["457", 0], "filename_prefix": "shine_m4_asset"},
        },
    }


def qwen_t2i_graph_hash() -> str:
    return graph_hash(build_qwen_t2i_graph(prompt="hash-probe"))
