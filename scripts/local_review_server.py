"""Stateless loopback VLM tool over existing hash-verified model files.

Run in a separate ML runtime. The Shine Worker owns the task/GPU ledger.
This executor never approves assets or downloads model weights.
"""

from __future__ import annotations

import argparse
import base64
import gc
import hashlib
import io
import json
import sys
import subprocess
import tempfile
from pathlib import Path
from threading import Lock
from fastapi import File, Form, UploadFile


def verify_model_manifest(manifest):
    root = Path(manifest["model_root"]).resolve()
    declared = {entry["path"] for entry in manifest["files"]}
    if not {"config.json", "model.safetensors", "tokenizer.json"}.issubset(declared):
        raise ValueError("INCOMPLETE_MODEL_HASH_MANIFEST")
    if len(declared) != len(manifest["files"]):
        raise ValueError("DUPLICATE_MODEL_FILE")
    for entry in manifest["files"]:
        path = (root / entry["path"]).resolve()
        if not path.is_relative_to(root) or not path.is_file():
            raise ValueError("MODEL_FILE_OUTSIDE_ROOT_OR_MISSING")
        with path.open("rb") as stream:
            digest = hashlib.file_digest(stream, "sha256").hexdigest()
        if digest != entry["sha256"]:
            raise ValueError("MODEL_HASH_CHANGED")
    return root


def review_token_limit(body):
    limit = body.get("max_tokens", 2048)
    if type(limit) is not int or not 128 <= limit <= 4096:
        raise ValueError("REVIEW_OUTPUT_BUDGET_INVALID")
    return limit


def build_app(manifest):
    from fastapi import FastAPI, HTTPException
    from PIL import Image

    root = verify_model_manifest(manifest)
    asr_manifest = manifest.get("asr")
    if asr_manifest:
        asr_root = verify_model_manifest(asr_manifest)
    from transformers import AutoProcessor, Qwen3VLForConditionalGeneration
    import torch

    guard = Lock()
    state = {"model": None, "processor": None}
    state["asr"] = None
    app = FastAPI(title="Shine local VLM executor", docs_url=None, redoc_url=None)

    @app.get("/health")
    def health():
        return {
            "status": "ok",
            "model": manifest["model_id"],
            "is_mock": False,
            "loaded": state["model"] is not None,
            "asr_loaded": state["asr"] is not None,
            "allocated_bytes": torch.cuda.memory_allocated(),
            "reserved_bytes": torch.cuda.memory_reserved(),
            "manifest_sha256": manifest["sha256"],
        }

    @app.get("/v1/models")
    def models():
        ids = [manifest["model_id"]] + (
            [asr_manifest["model_id"]] if asr_manifest else []
        )
        return {"data": [{"id": value} for value in ids], "is_mock": False}

    @app.post("/v1/audio/transcriptions")
    def transcribe(
        file: UploadFile = File(...),
        model: str = Form(...),
        response_format: str = Form("verbose_json"),
    ):
        if not asr_manifest or model != asr_manifest["model_id"]:
            raise HTTPException(404, "EXACT_ASR_MODEL_ID_REQUIRED")
        if response_format != "verbose_json":
            raise HTTPException(422, "UNSUPPORTED_ASR_RESPONSE_FORMAT")
        content = file.file.read(25 * 1024**2 + 1)
        if len(content) > 25 * 1024**2:
            raise HTTPException(413, "AUDIO_EVIDENCE_TOO_LARGE")
        with tempfile.TemporaryDirectory(prefix="shine-local-asr-") as directory:
            source = Path(directory) / "evidence.bin"
            source.write_bytes(content)
            probe = subprocess.run(
                [
                    "ffprobe",
                    "-v",
                    "error",
                    "-show_entries",
                    "format=duration",
                    "-of",
                    "json",
                    str(source),
                ],
                capture_output=True,
                timeout=30,
                check=True,
            )
            duration = float(json.loads(probe.stdout)["format"]["duration"])
            if not 0 < duration <= 120:
                raise HTTPException(422, "ASR_EVIDENCE_DURATION_OUTSIDE_LIMIT")
            decoded = subprocess.run(
                [
                    "ffmpeg",
                    "-nostdin",
                    "-v",
                    "error",
                    "-i",
                    str(source),
                    "-vn",
                    "-ac",
                    "1",
                    "-ar",
                    "16000",
                    "-f",
                    "f32le",
                    "pipe:1",
                ],
                capture_output=True,
                timeout=60,
                check=True,
            )
        import numpy as np
        from transformers import pipeline

        with guard:
            if state["asr"] is None:
                state["asr"] = pipeline(
                    "automatic-speech-recognition",
                    model=str(asr_root),
                    device=-1,
                    model_kwargs={"local_files_only": True},
                )
            result = state["asr"](
                {
                    "raw": np.frombuffer(decoded.stdout, dtype=np.float32).copy(),
                    "sampling_rate": 16000,
                },
                return_timestamps=True,
                generate_kwargs={"language": "chinese", "task": "transcribe"},
            )
            segments = [
                {
                    "start": chunk["timestamp"][0],
                    "end": chunk["timestamp"][1],
                    "text": chunk["text"],
                }
                for chunk in result.get("chunks", [])
                if all(value is not None for value in chunk["timestamp"])
            ]
            return {
                "text": result["text"],
                "segments": segments,
                "is_mock": False,
                "model": model,
                "speaker": "UNKNOWN",
                "confidence": None,
            }

    @app.post("/v1/chat/completions")
    def complete(body: dict):
        if body.get("model") != manifest["model_id"]:
            raise HTTPException(404, "EXACT_MODEL_ID_REQUIRED")
        try:
            token_limit = review_token_limit(body)
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc
        messages, count = [], 0
        for message in body.get("messages", []):
            content = message.get("content", [])
            if isinstance(content, str):
                content = [{"type": "text", "text": content}]
            converted = []
            for item in content:
                if item.get("type") == "text":
                    text = item.get("text", "")
                    if len(text) > 80000:
                        raise HTTPException(413, "REVIEW_TEXT_TOO_LARGE")
                    converted.append({"type": "text", "text": text})
                elif item.get("type") == "image_url":
                    url = item.get("image_url", {}).get("url", "")
                    if (
                        not url.startswith("data:image/jpeg;base64,")
                        or len(url) > 12 * 1024**2
                    ):
                        raise HTTPException(422, "ONLY_DERIVED_JPEG_BYTES_ALLOWED")
                    count += 1
                    if count > 16:
                        raise HTTPException(422, "TOO_MANY_EVIDENCE_FRAMES")
                    raw = base64.b64decode(url.split(",", 1)[1], validate=True)
                    image = Image.open(io.BytesIO(raw)).convert("RGB")
                    if image.width * image.height > 16000000:
                        raise HTTPException(413, "EVIDENCE_FRAME_TOO_LARGE")
                    converted.append({"type": "image", "image": image})
                else:
                    raise HTTPException(422, "UNSUPPORTED_REVIEW_CONTENT")
            if message.get("role") not in {"system", "user", "assistant"}:
                raise HTTPException(422, "INVALID_ROLE")
            messages.append({"role": message["role"], "content": converted})
        if not count:
            raise HTTPException(422, "EVIDENCE_FRAMES_REQUIRED")
        with guard:
            if state["model"] is None:
                if torch.cuda.mem_get_info()[0] < 6000 * 1024**2:
                    raise HTTPException(409, "PHYSICAL_GPU_BUDGET_UNAVAILABLE")
                state["processor"] = AutoProcessor.from_pretrained(
                    str(root), local_files_only=True
                )
                state["model"] = Qwen3VLForConditionalGeneration.from_pretrained(
                    str(root),
                    local_files_only=True,
                    device_map={"": "cuda:0"},
                    dtype=torch.bfloat16,
                ).eval()
            inputs = (
                state["processor"]
                .apply_chat_template(
                    messages,
                    tokenize=True,
                    add_generation_prompt=True,
                    return_dict=True,
                    return_tensors="pt",
                )
                .to("cuda:0")
            )
            with torch.inference_mode():
                output = state["model"].generate(
                    **inputs, max_new_tokens=token_limit, do_sample=False
                )
            generated = output[:, inputs["input_ids"].shape[1] :]
            answer = state["processor"].batch_decode(
                generated, skip_special_tokens=True
            )[0]
            return {
                "model": manifest["model_id"],
                "is_mock": False,
                "choices": [{"message": {"role": "assistant", "content": answer},
                             "finish_reason": "length" if generated.shape[1] >= token_limit else "stop"}],
                "usage": {"prompt_tokens": inputs["input_ids"].shape[1], "completion_tokens": generated.shape[1]},
            }

    @app.post("/free")
    def free(body: dict):
        if body.get("unload_models") is not True:
            raise HTTPException(422, "EXPLICIT_UNLOAD_REQUIRED")
        with guard:
            state["model"], state["processor"] = None, None
            state["asr"] = None
            gc.collect()
            torch.cuda.synchronize()
            # cuBLAS workspaces survive deleting model tensors. Clear those
            # framework allocations too before reporting physical release.
            clear_workspaces = getattr(torch._C, "_cuda_clearCublasWorkspaces", None)
            if clear_workspaces:
                clear_workspaces()
            torch.cuda.empty_cache()
            allocated = torch.cuda.memory_allocated()
            return {
                "released": allocated == 0,
                "allocated_bytes": allocated,
                "is_mock": False,
            }

    return app


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--runtime-packages", type=Path, required=True)
    parser.add_argument("--port", type=int, default=8116)
    args = parser.parse_args()
    sys.path.insert(0, str(args.runtime_packages.resolve()))
    manifest = json.loads(args.manifest.read_text(encoding="utf8"))
    import uvicorn

    uvicorn.run(build_app(manifest), host="127.0.0.1", port=args.port, access_log=False)


if __name__ == "__main__":
    main()
