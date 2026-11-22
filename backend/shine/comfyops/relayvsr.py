"""RelayVSR X2 video upscale: capability record, graph recipe and stage runner.

RelayVSR is an independent video super-resolution model that runs *after* H3
generation (and after the optional true second sampling pass).  It is not an
H3 LoRA and never touches the H3 reference inputs.  The audited integration
path is the ComfyUI core implementation merged in upstream commit
``2f8943324e1eae89a5ee6c7db93602299246b5f1`` (PR #17511, 2026-11-05) with the
officially repackaged weights (Apache-2.0):

* ``relayvsr_x2_lightx2v_int8_convrot.safetensors``  (diffusion model, X2)
* ``relayvsr_flash_vae_x2_fp32.safetensors``         (matched Flash VAE, X2)

The official node contract (``comfy_extras/nodes_wan.py``)::

    RelayVSRConditioning(model, images)  -> positive conditioning + empty latent
    VSRDecode(vae, samples, images, detail) -> IMAGE (2x resolution)

sampled with **1 step at CFG 1**; only every 15th frame passes the model and
``VSRDecode`` restores every frame.  The audio track is never re-synthesized:
Shine muxes the source audio losslessly back over the upscaled frames with
FFmpeg after the render.

Installing the weights does not establish support. Setup and the worker
require the native core nodes reported by the managed renderer.
"""
from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any

from shine.comfy.workflow import graph_hash, validate_against_object_info

#: Upstream facts, pinned for the audit trail.
RELAYVSR_MERGE_COMMIT = "2f8943324e1eae89a5ee6c7db93602299246b5f1"
#: The audited HF revision the weight digests were read from (paths-info lfs.oid).
RELAYVSR_HF_REVISION = "202c35eb0a5bb64b7017d5a09ea9acea87b7a081"

RELAYVSR_X2_MODEL = "relayvsr_x2_lightx2v_int8_convrot.safetensors"
RELAYVSR_X2_FLASH_VAE = "relayvsr_flash_vae_x2_fp32.safetensors"

#: The exact node classes of the official core implementation.
RELAYVSR_NODE_CLASSES = ("RelayVSRConditioning", "VSRDecode")

VIDEO_TRANSFER = {
    "upload_path": "/upload/image",
    "field_name": "image",
    "subfolder": "",
    "style": "multipart",
}


class RelayVSRUnavailable(RuntimeError):
    """A named refusal: RelayVSR cannot run on this installation as-is."""

    def __init__(self, code: str, message: str, details: dict | None = None):
        super().__init__(message)
        self.code = code
        self.message = message
        self.details = details or {}


def relayvsr_weight_pins() -> list[dict]:
    """The expected weight files with their official digests and sources."""
    from shine.comfyops.deployment import RELAYVSR_CATALOG, load_deployment_catalog

    return [
        {
            "role": component["role"],
            "filename": Path(entry["relative_path"]).name,
            "destination_family": component["destination_family"],
            "sha256": entry["sha256"],
            "size": entry["size"],
            "source_repo": component["origin_repo"],
            "source_revision": component["origin_revision"],
            "url": entry["url"],
            "license": component["license_id"],
        }
        for component in load_deployment_catalog(RELAYVSR_CATALOG)["components"]
        if component["kind"] == "model"
        for entry in component["files"]
    ]


def relayvsr_readiness(*, object_info: dict[str, Any] | None = None,
                       model_root: Path | None = None,
                       vae_root: Path | None = None) -> dict:
    """Evidence-derived readiness for the RelayVSR X2 backend.

    Check local file presence and the native node contract when live
    ``object_info`` is supplied.
    """
    blockers: list[dict] = []
    if model_root is not None and not (model_root / RELAYVSR_X2_MODEL).is_file():
        blockers.append({
            "code": "RELAYVSR_MODEL_FILE_MISSING",
            "path": str(model_root / RELAYVSR_X2_MODEL),
        })
    if vae_root is not None and not (vae_root / RELAYVSR_X2_FLASH_VAE).is_file():
        blockers.append({
            "code": "RELAYVSR_VAE_FILE_MISSING",
            "path": str(vae_root / RELAYVSR_X2_FLASH_VAE),
        })
    if object_info is not None:
        missing_nodes = [name for name in RELAYVSR_NODE_CLASSES if name not in object_info]
        if missing_nodes:
            blockers.append({
                "code": "RELAYVSR_NODES_MISSING_IN_LIVE",
                "missing_nodes": missing_nodes,
                "detail": "本机 ComfyUI 不含 RelayVSR 核心节点（需要 2f89433 或更高）。",
            })
        else:
            graph_check = validate_against_object_info(
                build_relayvsr_x2_graph(), RELAYVSR_PARAMETER_MAP, object_info,
            )
            if graph_check["missing_nodes"] or graph_check["missing_inputs"]:
                blockers.append({"code": "RELAYVSR_GRAPH_UNAVAILABLE", **graph_check,
                                 "detail": "当前 ComfyUI 的 RelayVSR 工作流节点或接口不完整。"})
    # Pinning the weights is a prerequisite, not proof of a runnable backend.
    # Without either file evidence or a live node table the backend stays
    # unverified: a caller that passes no evidence must never read AVAILABLE.
    if object_info is None and model_root is None:
        blockers.append({
            "code": "RELAYVSR_RUNTIME_UNVERIFIED",
            "detail": "权重清单已钉住，但还没有本机文件或真实节点表证据，不能启用。",
        })
    if blockers:
        return {
            "engine": "relayvsr",
            "variant": "x2",
            "available": False,
            "status": "BLOCKED",
            "blockers": blockers,
            "merge_commit": RELAYVSR_MERGE_COMMIT,
        }
    return {
        "engine": "relayvsr",
        "variant": "x2",
        "available": True,
        "status": "AVAILABLE",
        "blockers": [],
        "merge_commit": RELAYVSR_MERGE_COMMIT,
    }


def build_relayvsr_x2_graph() -> dict[str, Any]:
    """The audited RelayVSR X2 upscale graph (video only; audio is muxed later).

    Follows the official node contract exactly: the conditioning node builds
    the empty latent from the low-quality frames, one Euler step at CFG 1
    refines every 15th frame, and ``VSRDecode`` restores the full frame rate
    at 2x resolution using the matched Flash VAE.
    """
    return {
        "10": {
            "class_type": "UNETLoader",
            "inputs": {"unet_name": RELAYVSR_X2_MODEL, "weight_dtype": "default"},
        },
        "11": {"class_type": "VAELoader", "inputs": {"vae_name": RELAYVSR_X2_FLASH_VAE}},
        "12": {"class_type": "LoadVideo", "inputs": {"file": "shine-source.mp4"}},
        "13": {"class_type": "GetVideoComponents", "inputs": {"video": ["12", 0]}},
        "20": {
            "class_type": "RelayVSRConditioning",
            "inputs": {"model": ["10", 0], "images": ["13", 0]},
        },
        "30": {
            "class_type": "BasicScheduler",
            "inputs": {"model": ["10", 0], "scheduler": "simple", "steps": 1, "denoise": 1.0},
        },
        "31": {"class_type": "BasicGuider", "inputs": {"model": ["10", 0], "conditioning": ["20", 0]}},
        "32": {"class_type": "RandomNoise", "inputs": {"noise_seed": 0}},
        "33": {"class_type": "KSamplerSelect", "inputs": {"sampler_name": "euler"}},
        "34": {
            "class_type": "SamplerCustomAdvanced",
            "inputs": {
                "noise": ["32", 0],
                "guider": ["31", 0],
                "sampler": ["33", 0],
                "sigmas": ["30", 0],
                "latent_image": ["20", 1],
            },
        },
        "40": {
            "class_type": "VSRDecode",
            "inputs": {
                "vae": ["11", 0],
                "samples": ["34", 0],
                "images": ["13", 0],
                "detail": 1.0,
            },
        },
        "50": {
            "class_type": "CreateVideo",
            "inputs": {
                "images": ["40", 0],
                "fps": 24.0,
            },
        },
        "51": {
            "class_type": "SaveVideo",
            "inputs": {
                "video": ["50", 0],
                "filename_prefix": "shine/relayvsr-x2",
                "format": "mp4",
                "codec": {"codec": "h264"},
            },
        },
    }


RELAYVSR_PARAMETER_MAP = {
    "source_video": "12.inputs.file",
    "seed": "32.inputs.noise_seed",
    "fps": "50.inputs.fps",
    "detail": "40.inputs.detail",
}


def relayvsr_graph_identity(detail: float = 1.0, fps: float = 24.0) -> dict[str, Any]:
    """The frozen identity of the RelayVSR stage for one item."""
    graph = build_relayvsr_x2_graph()
    return {
        "compiler": "shine.relayvsr.v1",
        "engine": "relayvsr",
        "variant": "x2",
        "model": RELAYVSR_X2_MODEL,
        "vae": RELAYVSR_X2_FLASH_VAE,
        "merge_commit": RELAYVSR_MERGE_COMMIT,
        "detail": detail,
        "fps": fps,
        "graph_sha256": graph_hash(graph),
    }


def expected_upscale_output(source_width: int, source_height: int) -> tuple[int, int]:
    """RelayVSR X2 output is exactly 2x per axis - never a quiet 1.5x."""
    return source_width * 2, source_height * 2


def verify_upscale_contract(
    *,
    source_width: int,
    source_height: int,
    source_duration_s: float,
    source_fps: float,
    output_width: int,
    output_height: int,
    output_duration_s: float,
    output_fps: float,
    tolerance_s: float = 0.12,
    fps_tolerance: float = 0.05,
) -> list[str]:
    """Deterministic technical checks of one upscaled artifact.

    Returns a list of named violations; an empty list means the contract
    holds (2x per axis, same frame rate, same duration within tolerance).
    """
    violations: list[str] = []
    expected_w, expected_h = expected_upscale_output(source_width, source_height)
    if (output_width, output_height) != (expected_w, expected_h):
        violations.append(
            f"RELAYVSR_OUTPUT_SIZE_MISMATCH:{output_width}x{output_height}"
            f":expected:{expected_w}x{expected_h}"
        )
    if abs(output_fps - source_fps) > fps_tolerance * max(source_fps, 1.0):
        violations.append(f"RELAYVSR_FPS_MISMATCH:{output_fps}:source:{source_fps}")
    if abs(output_duration_s - source_duration_s) > tolerance_s:
        violations.append(f"RELAYVSR_DURATION_MISMATCH:{output_duration_s:.3f}:source:{source_duration_s:.3f}")
    return violations


def source_digest(path: Path) -> str:
    """SHA-256 of the source artifact bytes, checked again before the stage."""
    return hashlib.sha256(path.read_bytes()).hexdigest()


def mux_source_audio(upscaled_path: Path, source_path: Path, output_path: Path,
                     *, ffmpeg: str = "ffmpeg") -> list[str]:
    """Losslessly copy the source audio track over the upscaled video.

    ``-c:v copy`` keeps the RelayVSR frames untouched; ``-c:a copy`` keeps the
    original audio bytes.  A silent source simply has no audio stream, which
    ffmpeg reports without failing.  Returns the ffmpeg stderr lines on
    failure (empty list means success).
    """
    import subprocess

    output_path.parent.mkdir(parents=True, exist_ok=True)
    command = [
        ffmpeg, "-y", "-loglevel", "error",
        "-i", str(upscaled_path), "-i", str(source_path),
        "-map", "0:v:0", "-map", "1:a:0?",
        "-c:v", "copy", "-c:a", "copy",
        str(output_path),
    ]
    result = subprocess.run(command, capture_output=True, text=True, timeout=600)
    if result.returncode != 0:
        return [line for line in (result.stderr or "").splitlines() if line.strip()]
    return []


def stage_intent_digest(*, source_sha256: str, detail: float, fps: float) -> str:
    """Idempotent identity of one upscale submission (intent-before-submit)."""
    return graph_hash({
        "source_sha256": source_sha256,
        "detail": detail,
        "fps": fps,
        "engine": "relayvsr",
        "variant": "x2",
        "graph": graph_hash(build_relayvsr_x2_graph()),
    })
