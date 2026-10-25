"""One-shot SDK entrypoint, stdin JSON only. No database, HTTP server or approval API.

Standalone stdlib imports let different Python/Transformers environments execute
the same reviewed adapter without importing the FastAPI environment.
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import importlib.metadata
import io
import json
import os
import re
import struct
import sys
import wave
from pathlib import Path

METHODS = {"design": "generate_voice_design", "base": "generate_voice_clone", "asr": "transcribe", "align": "align"}
KEYS = {
    "generate_voice_design": {"text", "language", "instruct"},
    "generate_voice_clone": {"text", "language", "ref_audio", "ref_text"},
    "transcribe": {"audio", "language"},
    "align": {"audio", "text", "language"},
}


def verify_request(body):
    role, method = body["role"], body["method"]
    if METHODS.get(role) != method or set(body["kwargs"]) != KEYS[method]:
        raise ValueError("SDK_ROLE_METHOD_CONTRACT_INVALID")
    if body["kwargs"]["language"] != "Chinese":
        raise ValueError("SDK_LANGUAGE_CONTRACT_INVALID")
    for value in body["kwargs"].values():
        if isinstance(value, str) and len(value) > 8000:
            raise ValueError("SDK_TEXT_TOO_LARGE")
    sdk = "qwen-tts" if role in {"design", "base"} else "qwen-asr"
    versions = body["versions"]
    if not {sdk, "torch", "transformers"}.issubset(versions):
        raise ValueError("SDK_VERSIONS_INCOMPLETE")
    for name, version in {**versions, **body["package"]["runtime_versions"]}.items():
        if versions.get(name) != version or importlib.metadata.version(name) != version:
            raise ValueError("SDK_VERSION_NOT_PINNED_OR_CHANGED")
    root = Path(body["root"]).resolve()
    for entry in body["package"]["files"]:
        path = (root / entry["relative_path"]).resolve()
        if not path.is_relative_to(root) or path.stat().st_size != entry["size"]:
            raise ValueError("LOCAL_MODEL_FILE_CHANGED")
        with path.open("rb") as stream:
            if hashlib.file_digest(stream, "sha256").hexdigest() != entry["sha256"]:
                raise ValueError("LOCAL_MODEL_HASH_CHANGED")
    return role, method, root


def execute(body):
    role, method, root = verify_request(body)
    import numpy as np
    import torch

    budget = body["package"].get("resource_mb")
    if not budget or not torch.cuda.is_available() or torch.cuda.mem_get_info()[0] < budget * 1024**2:
        raise ValueError("VOICE_PHYSICAL_GPU_BUDGET_UNAVAILABLE")
    kwargs = body["kwargs"]
    for key in {"audio", "ref_audio"} & set(kwargs):
        samples, rate = kwargs[key]
        if not isinstance(rate, int) or not 8000 <= rate <= 192000 or not 0 < len(samples) <= rate * 120:
            raise ValueError("SDK_REFERENCE_AUDIO_CONTRACT_INVALID")
        data = np.asarray(samples, dtype=np.float32)
        if data.ndim != 1 or not np.isfinite(data).all():
            raise ValueError("SDK_REFERENCE_AUDIO_CONTRACT_INVALID")
        kwargs[key] = data, rate
    if role in {"design", "base"}:
        from qwen_tts import Qwen3TTSModel

        cls = Qwen3TTSModel
    elif role == "asr":
        from qwen_asr import Qwen3ASRModel

        cls = Qwen3ASRModel
    else:
        from qwen_asr import Qwen3ForcedAligner

        cls = Qwen3ForcedAligner
    model = cls.from_pretrained(str(root), device_map="cuda:0", dtype=torch.bfloat16, local_files_only=True)
    result = getattr(model, method)(**kwargs)
    output = {"is_mock": bool(getattr(model, "is_mock", False))}
    if role in {"design", "base"}:
        waves, rate = result
        if len(waves) != 1 or not 8000 <= rate <= 192000 or not 0 < len(waves[0]) <= rate * 120:
            raise ValueError("SDK_GENERATED_AUDIO_CONTRACT_INVALID")
        samples = np.asarray(waves[0])
        if samples.ndim != 1 or not np.isfinite(samples).all():
            raise ValueError("SDK_GENERATED_AUDIO_CONTRACT_INVALID")
        buffer = io.BytesIO()
        with wave.open(buffer, "wb") as audio:
            audio.setnchannels(1)
            audio.setsampwidth(2)
            audio.setframerate(rate)
            audio.writeframes(
                b"".join(struct.pack("<h", max(-32768, min(32767, round(float(x) * 32767)))) for x in samples)
            )
        output["audio_pcm16_base64"] = base64.b64encode(buffer.getvalue()).decode("ascii")
    elif role == "asr":
        output["transcript"] = {
            "text": result[0].text,
            "language": result[0].language,
            "speaker": "UNKNOWN",
            "confidence": 0,
        }
    else:
        output["words"] = [
            {"text": word.text, "start_time": word.start_time, "end_time": word.end_time} for word in result[0]
        ]
    return output


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--packages", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--ready", type=Path, required=True)
    args = parser.parse_args()
    pending_ready = args.ready.with_suffix(".tmp")
    pending_ready.write_text(json.dumps({"pid": os.getpid()}), encoding="utf8")
    pending_ready.replace(args.ready)
    sys.path.insert(0, str(args.packages.resolve()))
    try:
        content = sys.stdin.buffer.read(32 * 1024**2 + 1)
        if len(content) > 32 * 1024**2:
            raise ValueError("SDK_REQUEST_TOO_LARGE")
        result = execute(json.loads(content))
    except Exception as exc:
        code = str(exc)
        result = {"error_code": code if re.fullmatch(r"[A-Z0-9_]{1,80}", code) else type(exc).__name__}
        args.output.write_text(json.dumps(result), encoding="utf8")
        return 1
    args.output.write_text(json.dumps(result, ensure_ascii=False, allow_nan=False), encoding="utf8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
