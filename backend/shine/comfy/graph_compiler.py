"""Per-shot dynamic ComfyUI graph compiler for the Ref2VA mode.

The compiler turns one shot's frozen need - the user's chosen model variant,
accelerator recipe, optional true second sampling pass, and the shot's actual
reference set - into a legal ComfyUI API workflow graph plus its parameter
map, media transfer declarations and identity hashes.  It composes audited
node recipes only: the blueprint segments are direct derivatives of the
machine-proven PDD8 graph (``scripts/build_h3_workflow_profiles.py``) and of
the repository's variant manifests.  It never invents node class names, never
substitutes a different checkpoint, and refuses video references until a live
``object_info`` proves the loader node contract (no guessing).

The result (:class:`CompiledWorkflow`) is duck-type compatible with a
registered ``WorkflowProfile`` so the existing provider ``prepare()`` chain,
submit-intent persistence, history reconciliation and graph-hash accounting
all work unchanged.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from shine.comfy.workflow import (
    graph_hash,
    validate_against_object_info,
    validate_graph,
)

#: Hard bounds of the official MiniMax-H3 ReferenceToVideo autogrow groups,
#: mirrored from the pinned node contract (COMFY_AUTOGROW_V3).  The compiler
#: refuses anything beyond these before a graph is ever built.
IMAGE_REFERENCE_LIMIT = 9
AUDIO_REFERENCE_LIMIT = 3
VIDEO_REFERENCE_LIMIT = 3
TOTAL_REFERENCE_LIMIT = 12

WIDTH_DEFAULT = 1344
HEIGHT_DEFAULT = 768
FPS = 24.0

# H3 renders on a 17k+5 frame grid at 24 fps: length must be 5 (mod 17).
LENGTH_EXPRESSION = "max(5, round(a * 24)) + (5 - (max(5, round(a * 24)) % 17)) % 17"

UNET_BASELINE = "Minimax-H3\\minimax_h3_ref2va_pruned_int8_convrot.safetensors"
CLIP = "qwen3vl_32b_minimax_h3_nvfp4_awq.safetensors"
VIDEO_VAE = "MiniMax-H3(1)\\minimax_h3_video_vae_fp16.safetensors"
AUDIO_VAE = "MiniMax-H3(1)\\minimax_h3_audio_vae_fp32.safetensors"
REALISM_LORA = "MiniMax-H3\\h3-realism-people-t2v-i2v-r2v.safetensors"
PDD_FILE = "MiniMax-H3-Ref2VA-Acc-8Step.safetensors"
LATENT_UPSCALER = "minimax_h3_latent_upscaler_3d_conv_v1_bf16.safetensors"

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

#: Accelerator recipes the compiler can legally build today.  ``turbo4`` is a
#: declared-but-unverified recipe: it stays listed so the UI can name it, but
#: compilation refuses until a real acceptance records its exact nodes.
ACCELERATORS = {
    "pdd8": {
        "steps": 8,
        "requires_pdd": True,
    },
    "native20": {
        "steps": 20,
        "requires_pdd": False,
    },
    "turbo4": {
        "steps": 4,
        "requires_pdd": None,  # unverified recipe - never compiled silently
    },
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


class GraphCompileError(ValueError):
    """A named refusal: the requested combination cannot be compiled legally."""

    def __init__(self, code: str, message: str, details: dict | None = None):
        super().__init__(message)
        self.code = code
        self.message = message
        self.details = details or {}


@dataclass(frozen=True)
class SecondPassSpec:
    """Parameters of the true second sampling stage.

    ``latent_scale=1.0`` keeps the first pass' latent geometry (a pure tail
    refine at the same resolution); a value above 1 grows the latent through
    the 3D upscaler before the second sampler refines it.  Either way the
    second stage is a real second sampler with its own scheduler and model
    patch - never a renamed step count.
    """

    recipe_id: str = "h3-tail-refine-v1"
    denoise: float = 0.25
    latent_scale: float = 1.0
    nfe: str = "8"

    def recipe_hash(self) -> str:
        return graph_hash({
            "recipe_id": self.recipe_id,
            "denoise": self.denoise,
            "latent_scale": self.latent_scale,
            "nfe": self.nfe,
        })


@dataclass(frozen=True)
class CompiledWorkflow:
    """A per-shot compiled workflow, duck-type compatible with WorkflowProfile."""

    graph: dict[str, Any]
    parameter_map: dict[str, str]
    media_transfers: dict[str, Any]
    required_nodes: list[str]
    mode: str
    checkpoint_id: str | None
    variant_id: str
    accelerator_id: str
    second_pass: SecondPassSpec | None
    graph_sha256: str
    recipe_sha256: str
    model_identity: dict[str, Any]
    reference_counts: dict[str, int]
    validated_against: dict[str, Any] | None = field(default=None)

    def execution_identity(self) -> dict[str, Any]:
        """The frozen execution snapshot fields persisted with the attempt."""
        return {
            "compiler": "shine.graph_compiler.v1",
            "variant_id": self.variant_id,
            "accelerator_id": self.accelerator_id,
            "second_pass": (self.second_pass.recipe_hash() if self.second_pass else None),
            "reference_counts": dict(self.reference_counts),
            "recipe_sha256": self.recipe_sha256,
            "graph_sha256": self.graph_sha256,
        }


def _variant_checkpoint(variant_id: str) -> str:
    """Resolve the DiT weight file from the audited variant manifest."""
    if variant_id in (None, "", "h3-ref2va-int8-convrot"):
        return UNET_BASELINE
    from shine.comfyops.variants import load_variant_manifest

    manifest = load_variant_manifest(variant_id)
    checkpoint = str(manifest.get("checkpoint_id") or "")
    if not checkpoint:
        raise GraphCompileError(
            "VARIANT_CHECKPOINT_UNDECLARED",
            "这个模型变体的清单没有声明 checkpoint 文件，不能编译执行图。",
            {"variant_id": variant_id},
        )
    return checkpoint


def _model_stack(unet: str) -> dict[str, Any]:
    """UNET -> realism LoRA -> sigma shift -> memory savers -> SolAttn."""
    return {
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


def _sampling_stack(accelerator_id: str, steps: int) -> tuple[dict[str, Any], list, list]:
    """Return (nodes, guider_model_input, sampler_sigmas_input) for pass one."""
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
    # native20: the model and sigmas come straight from the base chain.
    nodes = {
        "17": {
            "class_type": "BasicScheduler",
            "inputs": {"model": ["16", 0], "scheduler": "simple", "steps": steps, "denoise": 1.0},
        },
    }
    return nodes, ["16", 0], ["17", 0]


def compile_ref2va_workflow(
    *,
    variant_id: str = "h3-ref2va-int8-convrot",
    accelerator_id: str = "pdd8",
    second_pass: SecondPassSpec | None = None,
    image_count: int,
    audio_count: int = 0,
    video_count: int = 0,
    object_info: dict[str, Any] | None = None,
) -> CompiledWorkflow:
    """Compile one shot's Ref2VA execution graph from audited recipes.

    ``image_count`` counts every image reference (subjects and pictures
    share the ref_images group).  The generated ``parameter_map`` uses the
    positional ``reference_image_N`` / ``reference_audio_N`` form so the
    provider's existing slot resolver keeps working for any cast mix.
    """
    if accelerator_id not in ACCELERATORS:
        raise GraphCompileError(
            "ACCELERATOR_UNKNOWN",
            "未登记的加速配方，不能编译执行图。",
            {"accelerator_id": accelerator_id, "known": sorted(ACCELERATORS)},
        )
    if ACCELERATORS[accelerator_id]["requires_pdd"] is None:
        raise GraphCompileError(
            "ACCELERATOR_UNVERIFIED",
            "这个加速配方还没有真机验收，不能用于生产编译。",
            {"accelerator_id": accelerator_id},
        )
    if image_count < 1:
        raise GraphCompileError(
            "REF2VA_IMAGE_REFERENCE_REQUIRED",
            "Ref2VA 模式至少需要一张图片参考；纯音频参考不被官方模式允许。",
            {},
        )
    if image_count > IMAGE_REFERENCE_LIMIT:
        raise GraphCompileError(
            "IMAGE_REFERENCE_LIMIT_EXCEEDED",
            f"图片参考超过官方上限 {IMAGE_REFERENCE_LIMIT} 张。",
            {"image_count": image_count, "limit": IMAGE_REFERENCE_LIMIT},
        )
    if audio_count > AUDIO_REFERENCE_LIMIT:
        raise GraphCompileError(
            "AUDIO_REFERENCE_LIMIT_EXCEEDED",
            f"音频参考超过官方上限 {AUDIO_REFERENCE_LIMIT} 个。",
            {"audio_count": audio_count, "limit": AUDIO_REFERENCE_LIMIT},
        )
    if video_count > VIDEO_REFERENCE_LIMIT:
        raise GraphCompileError(
            "VIDEO_REFERENCE_LIMIT_EXCEEDED",
            f"视频参考超过官方上限 {VIDEO_REFERENCE_LIMIT} 个。",
            {"video_count": video_count, "limit": VIDEO_REFERENCE_LIMIT},
        )
    if video_count:
        # No audited loader recipe exists for the ref_videos group in this
        # installation yet.  Building one would mean inventing a node name -
        # refused by name until a live object_info proves the contract.
        raise GraphCompileError(
            "VIDEO_REFERENCE_LOADER_UNVERIFIED",
            "视频参考的装载节点还没有在本机 object_info 上核验，不能编译。",
            {"video_count": video_count},
        )
    if image_count + audio_count + video_count > TOTAL_REFERENCE_LIMIT:
        raise GraphCompileError(
            "TOTAL_REFERENCE_LIMIT_EXCEEDED",
            f"参考素材总数超过官方上限 {TOTAL_REFERENCE_LIMIT}。",
            {"total": image_count + audio_count + video_count, "limit": TOTAL_REFERENCE_LIMIT},
        )
    if second_pass is not None:
        if not 0.0 < float(second_pass.denoise) <= 1.0:
            raise GraphCompileError(
                "SECOND_PASS_DENOISE_OUT_OF_RANGE",
                "二采 denoise 必须在 (0, 1] 区间。",
                {"denoise": second_pass.denoise},
            )
        if not 1.0 <= float(second_pass.latent_scale) <= 2.0:
            raise GraphCompileError(
                "SECOND_PASS_SCALE_OUT_OF_RANGE",
                "二采 latent 倍率必须在 [1.0, 2.0] 区间。",
                {"latent_scale": second_pass.latent_scale},
            )

    checkpoint = _variant_checkpoint(variant_id)
    steps = int(ACCELERATORS[accelerator_id]["steps"])
    graph: dict[str, Any] = {}
    graph.update(_model_stack(checkpoint))
    sampling, guider_model_input, sampler_sigmas_input = _sampling_stack(accelerator_id, steps)
    graph.update(sampling)

    # Loaders: text encoder, both VAEs, the frame-grid length expression and
    # one LoadImage per image reference starting at node 40.
    graph.update({
        "20": {"class_type": "CLIPLoader", "inputs": {"clip_name": CLIP, "type": "minimax", "device": "default"}},
        "21": {"class_type": "VAELoader", "inputs": {"vae_name": VIDEO_VAE}},
        "22": {"class_type": "VAELoader", "inputs": {"vae_name": AUDIO_VAE}},
        "30": {
            "class_type": "ComfyMathExpression",
            "inputs": {"expression": LENGTH_EXPRESSION, "values.a": 7.0},
        },
    })
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
        graph[node_id] = {
            "class_type": "LoadImage",
            "inputs": {"image": f"shine-reference-{index + 1}.png"},
        }
        reference_inputs[f"ref_images.ref_image_{index}"] = [node_id, 0]
        parameter_map[f"reference_image_{index + 1}"] = f"{node_id}.inputs.image"
    for index in range(audio_count):
        node_id = str(23 + index)
        graph[node_id] = {
            "class_type": "VHS_LoadAudioUpload",
            "inputs": {"audio": f"shine-audio-{index + 1}.wav", "start_time": 0.0, "duration": 0.0},
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
            "width": WIDTH_DEFAULT,
            "height": HEIGHT_DEFAULT,
            "length": ["30", 1],
            "ref_image_size": "match",
        },
    }

    required_nodes = list(BASE_MODEL_NODES)
    if accelerator_id == "pdd8":
        required_nodes += PDD_NODES

    graph.update({
        "50": {
            "class_type": "BasicGuider",
            "inputs": {"model": guider_model_input, "conditioning": ["31", 0]},
        },
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
    })

    if second_pass is None:
        graph.update({
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
                    "filename_prefix": "shine/ref2va-dynamic",
                    "format": "mp4",
                    "codec": {"codec": "h264"},
                },
            },
        })
        required_nodes = required_nodes + (["VHS_LoadAudioUpload"] if audio_count else [])
    else:
        # True second sampling stage: the first pass' latent is handed to a
        # second scheduler + model patch and refined through the audited 3D
        # refine handoff, then decoded and packed with the FIRST pass' audio
        # track (the refine stage must not re-synthesize audio).
        required_nodes = required_nodes + SECOND_PASS_NODES + (["VHS_LoadAudioUpload"] if audio_count else [])
        graph["55"] = {"class_type": "VAEDecodeAudio", "inputs": {"samples": ["53", 0], "vae": ["22", 0]}}
        graph.update({
            "60": {
                "class_type": "VRAM_Debug",
                "inputs": {"empty_cache": True, "gc_collect": True, "unload_all_models": True},
            },
            "61": {
                "class_type": "MiniMaxH3PDDAccScheduler",
                "inputs": {"nfe": second_pass.nfe, "denoise": second_pass.denoise, "partition": ""},
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
                    "filename_prefix": "shine/ref2va-dynamic-second-pass",
                    "format": "mp4",
                    "codec": {"codec": "h264"},
                },
            },
        })
        graph["60"]["inputs"]["any_input"] = ["53", 0]

    media_transfers: dict[str, Any] = {"image": dict(IMAGE_TRANSFER)}
    if audio_count:
        media_transfers["audio"] = dict(AUDIO_TRANSFER)

    recipe_payload = {
        "compiler": "shine.graph_compiler.v1",
        "variant_id": variant_id,
        "checkpoint_id": checkpoint,
        "accelerator_id": accelerator_id,
        "steps": steps,
        "second_pass": second_pass.recipe_hash() if second_pass else None,
        "image_count": image_count,
        "audio_count": audio_count,
    }
    recipe_sha256 = graph_hash(recipe_payload)

    validate_graph(graph, required_nodes=required_nodes)
    validated_against = None
    if object_info is not None:
        validated_against = validate_against_object_info(graph, parameter_map, object_info)

    model_identity = {
        "model_family": "MiniMax-H3",
        "generation_mode": "Ref2VA",
        "variant_id": variant_id,
        "variant_verified": variant_id in (None, "", "h3-ref2va-int8-convrot"),
        "accelerator_id": accelerator_id,
        "second_pass": bool(second_pass),
        "graph_version": "dynamic-compiler-v1",
        "source": "per_shot_compiled_recipe",
        "continuity_modes": ["soft_reference_continuity"],
        "reference_slots": sorted(k for k in parameter_map if k.startswith("reference_")),
        "media_transfer_families": sorted(media_transfers),
        "dynamic_autogrow": True,
        "recipe_sha256": recipe_sha256,
    }
    return CompiledWorkflow(
        graph=graph,
        parameter_map=parameter_map,
        media_transfers=media_transfers,
        required_nodes=sorted(set(required_nodes)),
        mode="Ref2VA",
        checkpoint_id=checkpoint,
        variant_id=variant_id,
        accelerator_id=accelerator_id,
        second_pass=second_pass,
        graph_sha256=graph_hash(graph),
        recipe_sha256=recipe_sha256,
        model_identity=model_identity,
        reference_counts={"image": image_count, "audio": audio_count, "video": video_count},
        validated_against=validated_against,
    )
