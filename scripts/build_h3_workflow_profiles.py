"""Build the real local MiniMax-H3 Ref2VA workflow profiles for the Shine workbench.

The graph is a direct derivative of the machine-proven ComfyUI API prompt that
produced the accepted PDD8 sample on 2026-10-21 (1344x768 native, Euler, CFG 1,
sigma shift 12/3, PDD strength 1/1, realism LoRA 0.65, SolAttn configuration).

Differences from that personal three-segment workflow, all deliberate:

* The personal cut/continue cache nodes (``MiniMaxSegmentRerun``,
  ``MiniMaxCutContinueCache``, ``MiniMaxSegmentRunMode``) are removed. They
  implement a hand-driven segment cache under the ComfyUI output directory.
  Shine owns shot continuity through its own job dependencies and never lets a
  renderer substitute another asset set, so the renderer must stay a pure
  function of the submitted request.
* Exactly one ``SaveVideo`` output node remains, so the artifact the workbench
  downloads is unambiguous.
* ``LoadImage`` carries no ``upload`` key; that key is not part of the live
  ``LoadImage`` input contract and would fail profile validation.
* Prompt, duration, seed and the two reference slots are the only per-request
  inputs. Resolution and the sampling mode are part of the profile identity
  (graph hash and model identity), because ``apply_parameters`` refuses any
  parameter without an explicit ``parameter_map`` entry.

Usage::

    uv run --project backend python scripts/build_h3_workflow_profiles.py
    uv run --project backend python scripts/build_h3_workflow_profiles.py --validate http://127.0.0.1:8188
"""

from __future__ import annotations

import argparse
import json
import sys
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]
OUTPUT_DIR = REPO_ROOT / "config" / "workflows"

UNET = "Minimax-H3\\minimax_h3_ref2va_pruned_int8_convrot.safetensors"
CLIP = "qwen3vl_32b_minimax_h3_nvfp4_awq.safetensors"
VIDEO_VAE = "MiniMax-H3(1)\\minimax_h3_video_vae_fp16.safetensors"
AUDIO_VAE = "MiniMax-H3(1)\\minimax_h3_audio_vae_fp32.safetensors"
REALISM_LORA = "MiniMax-H3\\h3-realism-people-t2v-i2v-r2v.safetensors"
PDD_FILE = "MiniMax-H3-Ref2VA-Acc-8Step.safetensors"

WIDTH = 1344
HEIGHT = 768
FPS = 24.0
DEFAULT_DURATION_S = 7.0

# H3 renders on a 17k+5 frame grid at 24 fps: length must be 5 (mod 17).
LENGTH_EXPRESSION = "max(5, round(a * 24)) + (5 - (max(5, round(a * 24)) % 17)) % 17"

IMAGE_TRANSFER = {
    "upload_path": "/upload/image",
    "field_name": "image",
    "subfolder": "shine",
    "style": "multipart",
}
AUDIO_TRANSFER = {
    "upload_path": "/upload/image",
    "field_name": "image",
    "subfolder": "shine",
    "style": "multipart",
    "required_node_classes": ["VHS_LoadAudioUpload"],
}


def _model_stack() -> dict[str, Any]:
    """UNET -> realism LoRA -> sigma shift -> memory savers -> SolAttn."""
    return {
        "10": {
            "class_type": "UNETLoader",
            "inputs": {"unet_name": UNET, "weight_dtype": "default"},
        },
        "11": {
            "class_type": "LoraLoaderModelOnly",
            "inputs": {"model": ["10", 0], "lora_name": REALISM_LORA, "strength_model": 0.65},
        },
        "12": {
            "class_type": "MiniMaxH3SigmaShift",
            "inputs": {"model": ["11", 0], "shift_video": 12.0, "shift_audio": 3.0},
        },
        "13": {
            "class_type": "MiniMaxChunkFeedForward",
            "inputs": {"model": ["12", 0], "chunks": 2, "seq_threshold": 4096},
        },
        "14": {
            "class_type": "MiniMaxH3MemoryEfficientSageAttentionPatch",
            "inputs": {"model": ["13", 0]},
        },
        "15": {
            "class_type": "MiniMaxLowVRAMAttention",
            "inputs": {"model": ["14", 0], "head_chunks": 4},
        },
        "16": {
            "class_type": "SolAttnPatch",
            "inputs": {
                "model": ["15", 0],
                "tau": 1.5,
                "start_percent": 0.2,
                "end_percent": 0.9,
                "min_tokens": 4096,
                "int8_qk": True,
                "sink_conditioning": "exact_kv_and_rows",
                "morton": False,
                "morton_curve": "2d_frame",
                "int8_pv": True,
                "verbose": False,
                "use_tma": False,
                "dense_blocks": "39-43",
            },
        },
    }


def _sampling_stack() -> dict[str, Any]:
    """Native 20-step sigmas bypassed by the PDD scheduler, plus the mode switch."""
    return {
        "17": {
            "class_type": "BasicScheduler",
            "inputs": {"model": ["16", 0], "scheduler": "simple", "steps": 20, "denoise": 1.0},
        },
        "18": {"class_type": "ShineH3SamplingMode", "inputs": {"mode": "PDD 8 steps"}},
        "19": {
            "class_type": "MiniMaxH3PDDAccApply",
            "inputs": {
                "model": ["16", 0],
                "bypass_sigmas": ["17", 0],
                "pdd_file": PDD_FILE,
                "nfe": ["18", 0],
                "lora_strength": 1.0,
                "head_strength": 1.0,
                "on_off_grid": "error",
                "partition": "",
                "enabled": ["18", 1],
                "partition_check": "error",
            },
        },
    }


def _loaders(with_audio: bool) -> dict[str, Any]:
    nodes: dict[str, Any] = {
        "20": {
            "class_type": "CLIPLoader",
            "inputs": {"clip_name": CLIP, "type": "minimax", "device": "default"},
        },
        "21": {"class_type": "VAELoader", "inputs": {"vae_name": VIDEO_VAE}},
        "22": {"class_type": "VAELoader", "inputs": {"vae_name": AUDIO_VAE}},
        "30": {
            "class_type": "ComfyMathExpression",
            "inputs": {"expression": LENGTH_EXPRESSION, "values.a": DEFAULT_DURATION_S},
        },
        "40": {"class_type": "LoadImage", "inputs": {"image": "shine-subject.png"}},
        "41": {"class_type": "LoadImage", "inputs": {"image": "shine-picture.png"}},
    }
    if with_audio:
        nodes["23"] = {
            "class_type": "VHS_LoadAudioUpload",
            "inputs": {"audio": "shine-voice.wav", "start_time": 0.0, "duration": 0.0},
        }
    return nodes


def base_graph(*, with_audio: bool) -> dict[str, Any]:
    """Single-pass PDD8 render at the model-native 1344x768 canvas."""
    graph: dict[str, Any] = {}
    graph.update(_model_stack())
    graph.update(_sampling_stack())
    graph.update(_loaders(with_audio))

    reference_inputs: dict[str, Any] = {
        "clip": ["20", 0],
        "vae": ["21", 0],
        "audio_vae": ["22", 0],
        "ref_images.ref_image_0": ["40", 0],
        "ref_images.ref_image_1": ["41", 0],
        "prompt": "",
        "width": WIDTH,
        "height": HEIGHT,
        "length": ["30", 1],
        "ref_image_size": "match",
    }
    if with_audio:
        reference_inputs["ref_audios.ref_audio_0"] = ["23", 0]
    graph["31"] = {"class_type": "MiniMaxH3ReferenceToVideo", "inputs": reference_inputs}

    graph.update(
        {
            "50": {
                "class_type": "BasicGuider",
                "inputs": {"model": ["19", 0], "conditioning": ["31", 0]},
            },
            "51": {"class_type": "RandomNoise", "inputs": {"noise_seed": 77}},
            "52": {"class_type": "KSamplerSelect", "inputs": {"sampler_name": "euler"}},
            "53": {
                "class_type": "SamplerCustomAdvanced",
                "inputs": {
                    "noise": ["51", 0],
                    "guider": ["50", 0],
                    "sampler": ["52", 0],
                    "sigmas": ["19", 1],
                    "latent_image": ["31", 1],
                },
            },
            "54": {"class_type": "VAEDecode", "inputs": {"samples": ["53", 0], "vae": ["21", 0]}},
            "55": {"class_type": "VAEDecodeAudio", "inputs": {"samples": ["53", 0], "vae": ["22", 0]}},
            "56": {
                "class_type": "CreateVideo",
                "inputs": {
                    "images": ["54", 0],
                    "fps": FPS,
                    "audio": ["55", 0],
                    "bit_depth": "auto",
                    "color_space": "sRGB",
                    "codec": "h264",
                },
            },
            "57": {
                "class_type": "SaveVideo",
                "inputs": {
                    "video": ["56", 0],
                    "filename_prefix": "shine/ref2va-pdd8",
                    "format": "mp4",
                    "codec": {"codec": "h264"},
                },
            },
        }
    )
    return graph


def upscale_graph(*, with_audio: bool) -> dict[str, Any]:
    """PDD8 render plus the proven 3D latent upscale and PDD tail refine.

    The native latent is upscaled by 1.5x (1344x768 -> 2016x1152) and the last
    trained blocks are repainted with the PDD8 scheduler at denoise 0.25.
    """
    graph = base_graph(with_audio=with_audio)
    # The native decode branch is replaced by the upscale handoff.
    del graph["54"]
    del graph["56"]
    del graph["57"]

    graph.update(
        {
            "60": {"class_type": "VRAM_Debug", "inputs": {"empty_cache": True, "gc_collect": True, "unload_all_models": True}},
            "61": {
                "class_type": "MiniMaxH3PDDAccScheduler",
                "inputs": {"nfe": "8", "denoise": 0.25, "partition": ""},
            },
            "62": {
                "class_type": "MiniMaxH3PDDAccApply",
                "inputs": {
                    "model": ["16", 0],
                    "pdd_file": PDD_FILE,
                    "nfe": "8",
                    "lora_strength": 1.0,
                    "head_strength": 1.0,
                    "on_off_grid": "error",
                    "partition": "",
                    "enabled": True,
                    "partition_check": "error",
                },
            },
            "63": {
                "class_type": "MinimaxH3LatentUpscaler3DRefineHandoff",
                "inputs": {
                    "latent": ["60", 0],
                    "noise": ["51", 0],
                    "sampler": ["52", 0],
                    "sigmas": ["61", 0],
                    "model": ["62", 0],
                    "positive": ["31", 0],
                    "model_name": "minimax_h3_latent_upscaler_3d_conv_v1_bf16.safetensors",
                    "mode": "scale by multiplier",
                    "scale": 1.5,
                    "width": 864,
                    "height": 1536,
                    "megapixels": 1.3,
                    "align": 32,
                    "keep_proportion": True,
                    "lock_audio": True,
                    "cfg": 1.0,
                    "device": "cuda",
                    "precision": "bf16",
                    "offload_after_upscale": True,
                },
            },
            "64": {"class_type": "VAEDecode", "inputs": {"samples": ["63", 0], "vae": ["21", 0]}},
            "65": {
                "class_type": "CreateVideo",
                "inputs": {
                    "images": ["64", 0],
                    "fps": FPS,
                    "audio": ["55", 0],
                    "bit_depth": "auto",
                    "color_space": "sRGB",
                    "codec": "h264",
                },
            },
            "66": {
                "class_type": "SaveVideo",
                "inputs": {
                    "video": ["65", 0],
                    "filename_prefix": "shine/ref2va-pdd8-upscale",
                    "format": "mp4",
                    "codec": {"codec": "h264"},
                },
            },
        }
    )
    graph["60"]["inputs"]["any_input"] = ["53", 0]
    return graph


def _profile(
    name: str,
    mode: str,
    graph: dict[str, Any],
    parameter_map: dict[str, str],
    media_transfers: dict[str, Any],
    required_nodes: list[str],
    description: str,
) -> dict[str, Any]:
    return {
        "name": name,
        "mode": mode,
        "checkpoint_id": UNET,
        "description": description,
        "required_nodes": required_nodes,
        "media_transfers": media_transfers,
        "parameter_map": parameter_map,
        "graph": graph,
    }


COMMON_NODES = [
    "MiniMaxH3ReferenceToVideo",
    "MiniMaxH3SigmaShift",
    "MiniMaxChunkFeedForward",
    "MiniMaxH3MemoryEfficientSageAttentionPatch",
    "MiniMaxLowVRAMAttention",
    "SolAttnPatch",
    "MiniMaxH3PDDAccApply",
    "ShineH3SamplingMode",
    "VAEDecodeAudio",
    "SaveVideo",
]


def build_profiles() -> list[dict[str, Any]]:
    image_map = {
        "positive_prompt": "31.inputs.prompt",
        "duration": "30.inputs.values.a",
        "seed": "51.inputs.noise_seed",
        "reference_subject_1": "40.inputs.image",
        "reference_picture_1": "41.inputs.image",
    }
    audio_map = {**image_map, "reference_audio_1": "23.inputs.audio"}

    pdd8 = _profile(
        name="local-h3-ref2va-pdd8-2img",
        mode="Ref2VA",
        graph=base_graph(with_audio=False),
        parameter_map=image_map,
        media_transfers={"image": dict(IMAGE_TRANSFER)},
        required_nodes=COMMON_NODES,
        description=(
            "Real local MiniMax-H3 Ref2VA, PDD 8 steps, model-native 1344x768 at 24 fps. "
            "INT8 ConvRot diffusion weights, nvfp4 AWQ Qwen3-VL text encoder, fp16 video VAE, "
            "fp32 audio VAE, Euler sampler, CFG 1, sigma shift 12/3, realism LoRA 0.65, "
            "SolAttn attention. Exactly one subject image and one picture image. "
            "Duration is snapped to the H3 17k+5 frame grid by ComfyMathExpression."
        ),
    )
    pdd8_aud = _profile(
        name="local-h3-ref2va-pdd8-2img-1aud",
        mode="Ref2VA",
        graph=base_graph(with_audio=True),
        parameter_map=audio_map,
        media_transfers={"image": dict(IMAGE_TRANSFER), "audio": dict(AUDIO_TRANSFER)},
        required_nodes=COMMON_NODES + ["VHS_LoadAudioUpload"],
        description=(
            "Same PDD8 Ref2VA stack as local-h3-ref2va-pdd8-2img plus one standalone audio "
            "(voice timbre) reference loaded through VHS_LoadAudioUpload into ref_audio_0."
        ),
    )
    upscale = _profile(
        name="local-h3-ref2va-pdd8-upscale-2img",
        mode="Ref2VA",
        graph=upscale_graph(with_audio=False),
        parameter_map=image_map,
        media_transfers={"image": dict(IMAGE_TRANSFER)},
        required_nodes=COMMON_NODES
        + ["MinimaxH3LatentUpscaler3DRefineHandoff", "MiniMaxH3PDDAccScheduler", "VRAM_Debug"],
        description=(
            "PDD8 Ref2VA render followed by the proven 3D latent upscale (x1.5 -> 2016x1152) "
            "and a PDD tail refine at denoise 0.25 over the last trained blocks. Audio is "
            "decoded from the first pass so the soundtrack is not altered by the refine pass."
        ),
    )
    return [pdd8, pdd8_aud, upscale]


def validate_against_live(graph: dict[str, Any], parameter_map: dict[str, str], base_url: str) -> dict[str, Any]:
    """Validate the graph with the backend's own validator against live object_info."""
    sys.path.insert(0, str(REPO_ROOT / "backend"))
    from shine.comfy.workflow import validate_against_object_info  # noqa: PLC0415

    with urllib.request.urlopen(base_url.rstrip("/") + "/object_info", timeout=120) as response:
        object_info = json.load(response)
    return validate_against_object_info(graph, parameter_map, object_info)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--validate", metavar="COMFYUI_URL", help="validate against a live ComfyUI")
    parser.add_argument("--write", action="store_true", help="write the JSON artifacts")
    args = parser.parse_args()

    profiles = build_profiles()
    failed = False
    for profile in profiles:
        filename = profile["name"].replace("local-h3-", "") + ".json"
        path = OUTPUT_DIR / filename
        print(f"--- {profile['name']} -> {path.relative_to(REPO_ROOT)}")
        if args.validate:
            report = validate_against_live(profile["graph"], profile["parameter_map"], args.validate)
            print(f"    live validation: {json.dumps(report, ensure_ascii=False)}")
            if report["missing_nodes"] or report["missing_inputs"]:
                failed = True
        if args.write:
            OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
            path.write_text(
                json.dumps(profile, ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
            )
            print(f"    written: {path}")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
