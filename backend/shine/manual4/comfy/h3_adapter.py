"""Mirrored Ref2VA ComfyUI graph compiler & media handoff helpers for Manual4."""

from __future__ import annotations

import json
import shutil
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from shine.manual4.comfy.client import M4ComfyWorkflowError
from shine.manual4.comfy.workflow import graph_hash, validate_against_object_info, validate_graph
from shine.manual4.h3.output_frame import frame_size

IMAGE_REFERENCE_LIMIT = 9
AUDIO_REFERENCE_LIMIT = 3
VIDEO_REFERENCE_LIMIT = 3
TOTAL_REFERENCE_LIMIT = 12
FPS = 24.0
LENGTH_EXPRESSION = "max(5, round(a * 24)) + (5 - (max(5, round(a * 24)) % 17)) % 17"

UNET_BASELINE = "Minimax-H3\\minimax_h3_ref2va_pruned_int8_convrot.safetensors"
CLIP_MODEL = "qwen3vl_32b_minimax_h3_nvfp4_awq.safetensors"
VIDEO_VAE = "MiniMax-H3(1)\\minimax_h3_video_vae_fp16.safetensors"
AUDIO_VAE = "MiniMax-H3(1)\\minimax_h3_audio_vae_fp32.safetensors"
REALISM_LORA = "MiniMax-H3\\h3-realism-people-t2v-i2v-r2v.safetensors"
PDD_FILE = "MiniMax-H3-Ref2VA-Acc-8Step.safetensors"
LATENT_UPSCALER = "minimax_h3_latent_upscaler_3d_conv_v1_bf16.safetensors"

ACCELERATORS: dict[str, dict[str, Any]] = {
    "pdd8": {"steps": 8, "requires_pdd": True, "label": "PDD 8 步加速（已真机验证）"},
    "native20": {"steps": 20, "requires_pdd": False, "label": "原生 20 步采样"},
    "turbo4": {"steps": 4, "requires_pdd": None, "label": "Turbo 4 步（尚未在本机验收，禁止编译）"},
}

BASE_MODEL_NODES = [
    "MiniMaxH3ReferenceToVideo",
    "MiniMaxH3SigmaShift",
    "MiniMaxChunkFeedForward",
    "MiniMaxH3MemoryEfficientSageAttentionPatch",
    "MiniMaxLowVRAMAttention",
    "SolAttnPatch",
    "VAEDecodeAudio",
    "SaveVideo",
]
PDD_NODES = ["MiniMaxH3PDDAccApply", "ShineH3SamplingMode"]
SECOND_PASS_NODES = [
    "MinimaxH3LatentUpscaler3DRefineHandoff",
    "MiniMaxH3PDDAccScheduler",
    "VRAM_Debug",
]


class M4GraphCompileError(ValueError):
    def __init__(self, code: str, message: str, details: dict[str, Any] | None = None):
        super().__init__(message)
        self.code = code
        self.message = message
        self.details = details or {}


@dataclass(frozen=True)
class M4SecondPassSpec:
    denoise: float = 0.25
    latent_scale: float = 1.0
    nfe: str = "8"

    def recipe_hash(self) -> str:
        return graph_hash({"denoise": self.denoise, "latent_scale": self.latent_scale, "nfe": self.nfe})


@dataclass(frozen=True)
class M4CompiledWorkflow:
    graph: dict[str, Any]
    parameter_map: dict[str, str]
    required_nodes: list[str]
    variant_id: str
    accelerator_id: str
    second_pass: M4SecondPassSpec | None
    graph_sha256: str
    recipe_sha256: str
    reference_counts: dict[str, int]
    validated_against: dict[str, Any] | None = field(default=None)


def verified_variants_catalog() -> list[dict[str, Any]]:
    return [
        {
            "variant_id": "h3-ref2va-int8-convrot",
            "label": "H3 Ref2VA INT8 ConvRot（本机真机基线）",
            "verified": True,
            "accelerators": ["pdd8", "native20"],
        }
    ]


def _model_stack(unet: str, *, sage_attention: bool = True) -> dict[str, Any]:
    nodes = {
        "10": {"class_type": "UNETLoader", "inputs": {"unet_name": unet, "weight_dtype": "default"}},
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
        "14": {"class_type": "MiniMaxH3MemoryEfficientSageAttentionPatch", "inputs": {"model": ["13", 0]}},
        "15": {"class_type": "MiniMaxLowVRAMAttention", "inputs": {"model": ["14", 0], "head_chunks": 4}},
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
    if not sage_attention:
        del nodes["14"]
        nodes["15"]["inputs"]["model"] = ["13", 0]
    return nodes


def _sampling_stack(accelerator_id: str, steps: int) -> tuple[dict[str, Any], list[Any], list[Any]]:
    if accelerator_id == "pdd8":
        nodes = {
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
        return nodes, ["19", 0], ["19", 1]
    nodes = {
        "17": {
            "class_type": "BasicScheduler",
            "inputs": {"model": ["16", 0], "scheduler": "simple", "steps": steps, "denoise": 1.0},
        },
    }
    return nodes, ["16", 0], ["17", 0]


def compile_m4_ref2va_workflow(
    *,
    variant_id: str = "h3-ref2va-int8-convrot",
    accelerator_id: str = "pdd8",
    second_pass: M4SecondPassSpec | None = None,
    aspect_ratio: str = "16:9",
    resolution_scale: float = 1.0,
    image_count: int,
    audio_count: int = 0,
    video_count: int = 0,
    object_info: dict[str, Any] | None = None,
) -> M4CompiledWorkflow:
    if variant_id not in (None, "", "h3-ref2va-int8-convrot"):
        raise M4GraphCompileError("M4_VARIANT_UNVERIFIED", f"四阶仅允许已验收变体；{variant_id} 未在本机验收。")
    variant_id = "h3-ref2va-int8-convrot"
    if accelerator_id not in ACCELERATORS:
        raise M4GraphCompileError("M4_ACCELERATOR_UNKNOWN", f"未知加速配方：{accelerator_id}")
    if ACCELERATORS[accelerator_id]["requires_pdd"] is None:
        raise M4GraphCompileError("M4_ACCELERATOR_UNVERIFIED", f"加速配方 {accelerator_id} 尚未真机验收，禁止编译。")
    if image_count < 1:
        raise M4GraphCompileError("M4_PROJECT_REQUIRES_REF2VA", "Ref2VA 模式至少需要 1 张参考图。")
    if image_count > IMAGE_REFERENCE_LIMIT:
        raise M4GraphCompileError("M4_IMAGE_SLOT_LIMIT", f"图片参考超过 H3 上限 {IMAGE_REFERENCE_LIMIT}。")
    if audio_count > AUDIO_REFERENCE_LIMIT:
        raise M4GraphCompileError("M4_AUDIO_SLOT_LIMIT", f"音频参考超过 H3 上限 {AUDIO_REFERENCE_LIMIT}。")
    if video_count > 0:
        raise M4GraphCompileError(
            "M4_VIDEO_REFERENCE_UNVERIFIED",
            "原始视频参考装载节点尚未在本机 object_info 核验；镜头衔接请使用 last_frame 尾帧参考图模式。",
        )
    if image_count + audio_count > TOTAL_REFERENCE_LIMIT:
        raise M4GraphCompileError("M4_TOTAL_SLOT_LIMIT", f"总参考数量超过 H3 上限 {TOTAL_REFERENCE_LIMIT}。")

    width, height = frame_size(aspect_ratio, float(resolution_scale))
    steps = int(ACCELERATORS[accelerator_id]["steps"])

    graph: dict[str, Any] = {}
    graph.update(_model_stack(UNET_BASELINE, sage_attention=accelerator_id != "pdd8"))
    sampling, guider_model_input, sampler_sigmas_input = _sampling_stack(accelerator_id, steps)
    graph.update(sampling)

    graph.update(
        {
            "20": {
                "class_type": "CLIPLoader",
                "inputs": {"clip_name": CLIP_MODEL, "type": "minimax", "device": "default"},
            },
            "21": {"class_type": "VAELoader", "inputs": {"vae_name": VIDEO_VAE}},
            "22": {"class_type": "VAELoader", "inputs": {"vae_name": AUDIO_VAE}},
            "30": {"class_type": "ComfyMathExpression", "inputs": {"expression": LENGTH_EXPRESSION, "values.a": 6.0}},
        }
    )

    parameter_map: dict[str, str] = {
        "positive_prompt": "31.inputs.prompt",
        "duration": "30.inputs.values.a",
        "seed": "51.inputs.noise_seed",
        "width": "31.inputs.width",
        "height": "31.inputs.height",
    }
    reference_inputs: dict[str, Any] = {}
    for index in range(image_count):
        node_id = str(40 + index)
        graph[node_id] = {"class_type": "LoadImage", "inputs": {"image": f"shine-m4-ref-{index + 1}.png"}}
        reference_inputs[f"ref_images.ref_image_{index}"] = [node_id, 0]
        parameter_map[f"reference_image_{index + 1}"] = f"{node_id}.inputs.image"
    for index in range(audio_count):
        node_id = str(23 + index)
        graph[node_id] = {
            "class_type": "VHS_LoadAudioUpload",
            "inputs": {"audio": f"shine-m4-audio-{index + 1}.wav", "start_time": 0.0, "duration": 0.0},
        }
        reference_inputs[f"ref_audios.ref_audio_{index}"] = [node_id, 0]
        parameter_map[f"reference_audio_{index + 1}"] = f"{node_id}.inputs.audio"

    graph["31"] = {
        "class_type": "MiniMaxH3ReferenceToVideo",
        "inputs": {
            "clip": ["20", 0],
            "vae": ["21", 0],
            "audio_vae": ["22", 0],
            **reference_inputs,
            "prompt": "",
            "width": width,
            "height": height,
            "length": ["30", 1],
            "ref_image_size": "match",
        },
    }

    required_nodes = list(BASE_MODEL_NODES)
    if accelerator_id == "pdd8":
        required_nodes.remove("MiniMaxH3MemoryEfficientSageAttentionPatch")
        required_nodes += PDD_NODES
    if audio_count:
        required_nodes.append("VHS_LoadAudioUpload")

    graph.update(
        {
            "50": {"class_type": "BasicGuider", "inputs": {"model": guider_model_input, "conditioning": ["31", 0]}},
            "51": {"class_type": "RandomNoise", "inputs": {"noise_seed": 77}},
            "52": {"class_type": "KSamplerSelect", "inputs": {"sampler_name": "euler"}},
            "53": {
                "class_type": "SamplerCustomAdvanced",
                "inputs": {
                    "noise": ["51", 0],
                    "guider": ["50", 0],
                    "sampler": ["52", 0],
                    "sigmas": sampler_sigmas_input,
                    "latent_image": ["31", 1],
                },
            },
        }
    )

    if second_pass is None:
        graph.update(
            {
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
                        "filename_prefix": "shine_m4/ref2va",
                        "format": "mp4",
                        "codec": {"codec": "h264"},
                    },
                },
            }
        )
    else:
        if not 0.0 < float(second_pass.denoise) <= 1.0:
            raise M4GraphCompileError("M4_SECOND_PASS_DENOISE_INVALID", "二采 denoise 必须在 (0, 1] 区间。")
        if not 1.0 <= float(second_pass.latent_scale) <= 2.0:
            raise M4GraphCompileError("M4_SECOND_PASS_SCALE_INVALID", "二采 latent_scale 必须在 [1.0, 2.0] 区间。")
        required_nodes += SECOND_PASS_NODES
        up_w, up_h = frame_size(aspect_ratio, float(resolution_scale) * float(second_pass.latent_scale))
        graph["55"] = {"class_type": "VAEDecodeAudio", "inputs": {"samples": ["53", 0], "vae": ["22", 0]}}
        graph.update(
            {
                "60": {
                    "class_type": "VRAM_Debug",
                    "inputs": {
                        "empty_cache": True,
                        "gc_collect": True,
                        "unload_all_models": True,
                        "any_input": ["53", 0],
                    },
                },
                "61": {
                    "class_type": "MiniMaxH3PDDAccScheduler",
                    "inputs": {"nfe": second_pass.nfe, "denoise": float(second_pass.denoise), "partition": ""},
                },
                "62": {
                    "class_type": "MiniMaxH3PDDAccApply",
                    "inputs": {
                        "model": ["16", 0],
                        "pdd_file": PDD_FILE,
                        "nfe": second_pass.nfe,
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
                        "model_name": LATENT_UPSCALER,
                        "mode": "scale by multiplier",
                        "scale": float(second_pass.latent_scale),
                        "width": up_w,
                        "height": up_h,
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
                        "filename_prefix": "shine_m4/ref2va-second-pass",
                        "format": "mp4",
                        "codec": {"codec": "h264"},
                    },
                },
            }
        )

    try:
        validate_graph(graph, required_nodes=required_nodes)
    except M4ComfyWorkflowError as exc:
        raise M4GraphCompileError("M4_GRAPH_INVALID", str(exc)) from exc

    validated_against = None
    if object_info is not None:
        validated_against = validate_against_object_info(graph, parameter_map, object_info)
        if validated_against["missing_nodes"] or validated_against["missing_inputs"]:
            raise M4GraphCompileError(
                "M4_COMFY_NODES_UNAVAILABLE",
                f"当前 ComfyUI 节点合同未满足：{json.dumps(validated_against, ensure_ascii=False)}",
                details=validated_against,
            )

    recipe_sha = graph_hash(
        {
            "variant_id": variant_id,
            "accelerator_id": accelerator_id,
            "steps": steps,
            "second_pass": second_pass.recipe_hash() if second_pass else None,
            "width": width,
            "height": height,
            "image_count": image_count,
            "audio_count": audio_count,
        }
    )
    return M4CompiledWorkflow(
        graph=graph,
        parameter_map=parameter_map,
        required_nodes=sorted(set(required_nodes)),
        variant_id=variant_id,
        accelerator_id=accelerator_id,
        second_pass=second_pass,
        graph_sha256=graph_hash(graph),
        recipe_sha256=recipe_sha,
        reference_counts={"image": image_count, "audio": audio_count, "video": video_count},
        validated_against=validated_against,
    )


def extract_video_output(history_record: dict[str, Any]) -> tuple[str, str, str]:
    outputs = history_record.get("outputs") or {}
    for _node_id, node_output in outputs.items():
        for key in ("videos", "gifs", "images"):
            items = node_output.get(key) or []
            if items and isinstance(items[0], dict) and items[0].get("filename"):
                item = items[0]
                return (
                    str(item["filename"]),
                    str(item.get("subfolder") or ""),
                    str(item.get("type") or "output"),
                )
    raise M4GraphCompileError("M4_COMFY_VIDEO_OUTPUT_MISSING", "ComfyUI 已完成执行但未返回视频输出。")


def extract_last_frame_png(video_path: Path, out_png_path: Path, *, ffmpeg_path: str = "ffmpeg") -> Path:
    executable = shutil.which(ffmpeg_path)
    if not executable:
        raise M4GraphCompileError("M4_FFMPEG_MISSING", "未检测到 FFmpeg，无法提取上一镜尾帧用于 last_frame 衔接。")
    out_png_path.parent.mkdir(parents=True, exist_ok=True)
    cmd = [
        executable,
        "-y",
        "-hide_banner",
        "-loglevel",
        "error",
        "-sseof",
        "-0.08",
        "-i",
        str(video_path),
        "-vframes",
        "1",
        str(out_png_path),
    ]
    res = subprocess.run(cmd, capture_output=True, timeout=60, check=False)
    if res.returncode != 0 or not out_png_path.is_file() or out_png_path.stat().st_size == 0:
        stderr = res.stderr.decode("utf-8", errors="replace").strip()
        raise M4GraphCompileError("M4_CONTINUITY_FRAME_FAILED", f"提取上一镜尾帧失败：{stderr[:200]}")
    return out_png_path
