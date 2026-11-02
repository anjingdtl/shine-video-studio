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
import logging
import re
import sys
import subprocess
import tempfile
import wave
from pathlib import Path
from threading import Lock
from fastapi import File, Form, UploadFile

STRUCTURED_PROTOCOL = "local-review-json-schema-v1"


def decoder_runtime_identity(lock_path=None):
    """Verify every installed decoder file before importing third-party code."""
    import importlib.metadata
    lock_path = lock_path or Path(__file__).resolve().parents[1] / "config/review-decoder-lock.json"
    lock = json.loads(Path(lock_path).read_text(encoding="utf8"))
    if lock.get("protocol") != STRUCTURED_PROTOCOL:
        raise ValueError("REVIEW_DECODER_LOCK_INVALID")
    if (len(lock.get("packages", [])) != 2 or {p.get("name") for p in lock["packages"]}
            != {"lm-format-enforcer", "interegular"}):
        raise ValueError("REVIEW_DECODER_PACKAGES_INVALID")
    verified = []
    for package in lock["packages"]:
        distribution = importlib.metadata.distribution(package["name"])
        if distribution.version != package["version"]:
            raise ValueError("REVIEW_DECODER_VERSION_CHANGED")
        base = Path(distribution.locate_file("")).resolve()
        declared = {entry["path"] for entry in package["files"]}
        if not declared or len(declared) != len(package["files"]) or not package.get("roots"):
            raise ValueError("REVIEW_DECODER_FILE_MANIFEST_INVALID")
        for entry in package["files"]:
            path = Path(distribution.locate_file(entry["path"])).resolve()
            if not path.is_relative_to(base) or not path.is_file():
                raise ValueError("REVIEW_DECODER_FILE_MISSING_OR_OUTSIDE_ROOT")
            if path.stat().st_size != entry["bytes"] or hashlib.sha256(path.read_bytes()).hexdigest() != entry["sha256"]:
                raise ValueError("REVIEW_DECODER_FILE_CHANGED")
        for name in package["roots"]:
            folder = (base / name).resolve()
            if not folder.is_relative_to(base):
                raise ValueError("REVIEW_DECODER_ROOT_INVALID")
            for candidate in folder.rglob("*"):
                if candidate.is_file() and candidate.suffix in {".py", ".pyd", ".so"}:
                    if candidate.relative_to(base).as_posix() not in declared:
                        raise ValueError("REVIEW_DECODER_UNDECLARED_CODE")
        verified.append({"name": package["name"], "version": distribution.version, "files": package["files"]})
    schema_hash = lock.get("review_schema_sha256")
    if not isinstance(schema_hash, str) or not re.fullmatch("[a-f0-9]{64}", schema_hash):
        raise ValueError("REVIEW_DECODER_SCHEMA_PIN_MISSING")
    payload = {"protocol": STRUCTURED_PROTOCOL, "packages": verified, "schema_sha256": schema_hash}
    additional = lock.get("additional_review_schema_sha256", [])
    if (not isinstance(additional, list) or len(additional) > 8
            or any(not isinstance(pin, str) or not re.fullmatch("[a-f0-9]{64}", pin) for pin in additional)
            or len(set(additional)) != len(additional) or schema_hash in additional):
        raise ValueError("REVIEW_DECODER_ADDITIONAL_SCHEMA_PINS_INVALID")
    if additional:
        payload["additional_schema_sha256"] = sorted(additional)
    result = {"protocol": STRUCTURED_PROTOCOL, "decoder_sha256": hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest(), "schema_sha256": schema_hash}
    if additional:
        result["supported_schema_sha256"] = [schema_hash, *sorted(additional)]
    return result


def review_response_schema(body, expected_hash=None):
    response = body.get("response_format")
    if not isinstance(response, dict) or response.get("type") != "json_schema":
        raise ValueError("REVIEW_JSON_SCHEMA_REQUIRED")
    declaration = response.get("json_schema")
    if not isinstance(declaration, dict) or declaration.get("strict") is not True:
        raise ValueError("REVIEW_JSON_SCHEMA_STRICT_REQUIRED")
    schema = declaration.get("schema")
    if not isinstance(schema, dict) or schema.get("type") != "object":
        raise ValueError("REVIEW_JSON_SCHEMA_INVALID")
    encoded = json.dumps(schema, sort_keys=True, separators=(",", ":")).encode()
    if len(encoded) > 32768 or '"$ref":"http' in encoded.decode() or '"$ref":"file:' in encoded.decode():
        raise ValueError("REVIEW_JSON_SCHEMA_TOO_LARGE_OR_EXTERNAL")
    # The parser resolves local definitions only. Reject external references at
    # any depth, including arrays, before loading a model or constructing it.
    def check(value):
        if isinstance(value, dict):
            if "$ref" in value and (not isinstance(value["$ref"], str) or not value["$ref"].startswith("#/")):
                raise ValueError("REVIEW_JSON_SCHEMA_EXTERNAL_REFERENCE")
            for child in value.values():
                check(child)
        elif isinstance(value, list):
            for child in value:
                check(child)
    check(schema)
    actual = hashlib.sha256(encoded).hexdigest()
    pins = [expected_hash] if isinstance(expected_hash, str) else expected_hash
    if pins is not None and actual not in pins:
        raise ValueError("REVIEW_JSON_SCHEMA_PIN_MISMATCH")
    return schema, actual


def review_prefix_function(tokenizer, schema):
    from lmformatenforcer import JsonSchemaParser
    from lmformatenforcer.characterlevelparser import CharacterLevelParserConfig
    from lmformatenforcer.integrations.transformers import (
        build_token_enforcer_tokenizer_data,
        build_transformers_prefix_allowed_tokens_fn,
    )

    # JsonSchemaParser snapshots its alphabet in its constructor. Setting
    # parser.config later (as TokenEnforcer does) does not update that cached
    # value, and otherwise silently excludes Chinese characters from strings.
    token_data = build_token_enforcer_tokenizer_data(tokenizer)
    config = CharacterLevelParserConfig(alphabet=token_data.tokenizer_alphabet)
    callback = build_transformers_prefix_allowed_tokens_fn(token_data, JsonSchemaParser(schema, config=config))

    class NoGeneratedPrefixLogs(logging.Filter):
        def filter(self, record):
            # Upstream's error logger includes generated prefixes. Keep those
            # out of application logs; the caller gets a constant failure code.
            return "lmformatenforcer" not in record.pathname.replace("\\", "/")

    def guarded(batch_id, tokens):
        logger = logging.getLogger()
        redaction = NoGeneratedPrefixLogs()
        placeholder = logging.NullHandler() if not logger.handlers else None
        if placeholder:
            logger.addHandler(placeholder)
        logger.addFilter(redaction)
        try:
            return callback(batch_id, tokens)
        except Exception:
            raise ValueError("REVIEW_STRUCTURED_DECODING_FAILED") from None
        finally:
            logger.removeFilter(redaction)
            if placeholder:
                logger.removeHandler(placeholder)
    return guarded


def _verified_model_files(manifest):
    root = Path(manifest["model_root"]).resolve()
    declared = {entry["path"] for entry in manifest["files"]}
    # A tokenizer is pinned by its bytes: a fast tokenizer by ``tokenizer.json``,
    # a slow one by the configuration, the vocabulary and the merges.
    fast_tokenizer = "tokenizer.json" in declared
    slow_tokenizer = {"tokenizer_config.json", "vocab.json", "merges.txt"}.issubset(declared)
    if not {"config.json"}.issubset(declared) or not (fast_tokenizer or slow_tokenizer) or not (
        "model.safetensors" in declared or "model.safetensors.index.json" in declared
    ):
        raise ValueError("INCOMPLETE_MODEL_HASH_MANIFEST")
    if len(declared) != len(manifest["files"]):
        raise ValueError("DUPLICATE_MODEL_FILE")
    resolved, verified = set(), []
    for entry in manifest["files"]:
        path = (root / entry["path"]).resolve()
        if not path.is_relative_to(root) or not path.is_file():
            raise ValueError("MODEL_FILE_OUTSIDE_ROOT_OR_MISSING")
        if path in resolved:
            raise ValueError("DUPLICATE_MODEL_FILE")
        resolved.add(path)
        size = path.stat().st_size
        if "bytes" in entry and (type(entry["bytes"]) is not int or entry["bytes"] != size):
            raise ValueError("MODEL_SIZE_CHANGED")
        with path.open("rb") as stream:
            digest = hashlib.file_digest(stream, "sha256").hexdigest()
        if digest != entry["sha256"]:
            raise ValueError("MODEL_HASH_CHANGED")
        verified.append({"path": path.relative_to(root).as_posix(), "sha256": digest, "bytes": size})
    # Local loaders can prefer an alternative weight file, adapter, tokenizer
    # or generation configuration over the declared files. Hashing only listed
    # bytes must not authorize these unlisted loader inputs.
    audit_notes = {"source-audit.json", "configuration.json"}
    runtime_suffixes = {".safetensors", ".bin", ".json", ".model", ".jinja", ".py", ".tiktoken", ".bpe"}
    root_files = [path for path in root.iterdir() if path.is_file()]
    for candidate in root_files:
        if (candidate.is_file() and candidate.name not in declared
                and candidate.name.casefold() not in audit_notes
                and (candidate.suffix.lower() in runtime_suffixes
                     or candidate.name.casefold() in {"merges.txt", "vocab.txt"})):
            raise ValueError("UNDECLARED_MODEL_RUNTIME_FILE")
    # PEFT may redirect from this folder to another base model before loading.
    # This adapter supports complete model packages, not unverified external bases.
    if any(path.name.casefold() == "adapter_config.json" for path in root_files):
        raise ValueError("UNSUPPORTED_MODEL_ADAPTER_LAYOUT")
    if "model.safetensors.index.json" in declared:
        # Transformers follows this index during loading. Every referenced shard
        # must belong to the same verified manifest, with no alternative weights.
        try:
            index = json.loads((root / "model.safetensors.index.json").read_text(encoding="utf8"))
            mapping = index["weight_map"]
            if not isinstance(mapping, dict) or not mapping:
                raise ValueError("empty or invalid weight map")
            if not all(isinstance(key, str) and key and isinstance(value, str) for key, value in mapping.items()):
                raise ValueError("invalid weight mapping")
            shards = set(mapping.values())
            weights = {name for name in declared if name.endswith(".safetensors")}
            if ("model.safetensors" in declared or shards != weights
                    or not all(name.endswith(".safetensors") for name in shards)):
                raise ValueError("unverified or ambiguous shards")
        except (ValueError, KeyError, TypeError) as exc:
            raise ValueError("MODEL_SHARD_INDEX_INVALID") from exc
    return root, sorted(verified, key=lambda entry: entry["path"])


def verify_model_manifest(manifest):
    return _verified_model_files(manifest)[0]


def verified_model_identity(manifest):
    """Fingerprint checked bytes, not the operator-supplied manifest label.

    No absolute paths or credentials belong in the public executor identity.
    This is a startup identity, not proof of inference or quality acceptance.
    """
    _, files = _verified_model_files(manifest)
    canonical = json.dumps(files, sort_keys=True, separators=(",", ":")).encode()
    return {
        "model_id": manifest["model_id"],
        "files_sha256": hashlib.sha256(canonical).hexdigest(),
        "file_count": len(files),
        "bytes": sum(entry["bytes"] for entry in files),
    }


def review_token_limit(body):
    limit = body.get("max_tokens", 2048)
    if type(limit) is not int or not 128 <= limit <= 4096:
        raise ValueError("REVIEW_OUTPUT_BUDGET_INVALID")
    return limit


def _transcribe_qwen3_asr(asr_manifest, asr_root, decoded, model, executor_identity, guard, state,
                          current_decoder_identity, decoder_identity, context=""):
    """Transcribe with the locally installed Qwen3-ASR model.

    The model is hash-verified like every other executor model and is loaded
    from its own pinned runtime packages, so the executor identity still proves
    which bytes and which runtime produced the transcript.

    ``context`` is the script's own terminology.  The model uses it to pick the
    right characters for a proper name; it never rewrites the transcript.
    """
    import sys

    packages = asr_manifest.get("runtime_packages")
    if not packages or not Path(packages).is_dir():
        raise ValueError("ASR_RUNTIME_PACKAGES_MISSING")
    if str(packages) not in sys.path:
        sys.path.insert(0, str(packages))
    from qwen_asr import Qwen3ASRModel

    with tempfile.TemporaryDirectory(prefix="shine-local-qwen-asr-") as directory:
        source = Path(directory) / "evidence.wav"
        # The decoder emits raw mono 16 kHz float PCM; the model reads a file,
        # so the samples are written as a real WAV container.
        import numpy as np

        samples = np.frombuffer(decoded.stdout, dtype=np.float32)
        pcm = (np.clip(samples, -1.0, 1.0) * 32767).astype("<i2")
        with wave.open(str(source), "wb") as handle:
            handle.setnchannels(1)
            handle.setsampwidth(2)
            handle.setframerate(16000)
            handle.writeframes(pcm.tobytes())
        with guard:
            if current_decoder_identity() != decoder_identity:
                raise ValueError("EXECUTOR_DECODER_FILES_CHANGED")
            if state["asr"] is None:
                verify_model_manifest(asr_manifest)
                state["asr"] = Qwen3ASRModel.from_pretrained(str(asr_root), device_map="cpu")
            if context:
                results = state["asr"].transcribe(audio=str(source), language="Chinese", context=context)
            else:
                results = state["asr"].transcribe(audio=str(source), language="Chinese")
    rows = results if isinstance(results, list) else [results]
    texts, segments = [], []
    for row in rows:
        text = str(getattr(row, "text", row)).strip()
        if text:
            texts.append(text)
        start = getattr(row, "start_time", None)
        end = getattr(row, "end_time", None)
        if start is not None and end is not None:
            segments.append({"start": float(start), "end": float(end), "text": text})
    return {
        "text": " ".join(texts),
        "segments": segments,
        "is_mock": False,
        "model": model,
        "executor_identity": executor_identity(model),
        "speaker": "UNKNOWN",
        "confidence": None,
    }


def build_app(manifest):
    from fastapi import FastAPI, HTTPException
    from PIL import Image

    identity = verified_model_identity(manifest)
    root = Path(manifest["model_root"]).resolve()
    verified_models = {manifest["model_id"]: identity}
    asr_manifest = manifest.get("asr")
    if asr_manifest:
        if asr_manifest["model_id"] in verified_models:
            raise ValueError("DUPLICATE_EXECUTOR_MODEL_ID")
        verified_models[asr_manifest["model_id"]] = verified_model_identity(asr_manifest)
        asr_root = Path(asr_manifest["model_root"]).resolve()
    from transformers import AutoProcessor, Qwen3VLForConditionalGeneration
    import torch

    import importlib.metadata

    decoder_identity = decoder_runtime_identity()

    runtime = {
        "python": sys.version,
        "packages": {name: importlib.metadata.version(name) for name in (
            "torch", "transformers", "pydantic", "packaging", "pyyaml",
        )},
        "adapter_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        "structured_output": decoder_identity,
    }
    runtime_sha256 = hashlib.sha256(
        json.dumps(runtime, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    def executor_identity(model_id):
        return {"schema_version": "1", "runtime_sha256": runtime_sha256, **verified_models[model_id]}

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
            "verified_models": verified_models,
            "runtime_sha256": runtime_sha256,
            "structured_output": decoder_identity,
        }

    @app.get("/identity")
    def identity_probe():
        # Preparation reads fresh file identity without loading any ML model.
        with guard:
            try:
                if decoder_runtime_identity() != decoder_identity:
                    raise ValueError("REVIEW_DECODER_IDENTITY_CHANGED")
            except (ValueError, OSError):
                raise HTTPException(409, "EXECUTOR_DECODER_FILES_CHANGED") from None
            try:
                current = {manifest["model_id"]: verified_model_identity(manifest)}
                if asr_manifest:
                    current[asr_manifest["model_id"]] = verified_model_identity(asr_manifest)
            except (ValueError, OSError, KeyError) as exc:
                raise HTTPException(409, "EXECUTOR_MODEL_FILES_CHANGED") from exc
            if current != verified_models:
                raise HTTPException(409, "EXECUTOR_MODEL_IDENTITY_CHANGED")
            return {"status": "ok", "is_mock": False, "verified_models": current,
                    "runtime_sha256": runtime_sha256, "structured_output": decoder_identity}

    @app.get("/ready")
    def ready():
        """Prove the reviewer's runtime can import its executor, without loading weights.

        A health check alone only proves the wrapper started. This endpoint
        surfaces a Python/decoder incompatibility as an advisory "unavailable"
        instead of letting the first real review call fail inside the handoff.
        """
        try:
            if asr_manifest:
                from qwen_asr import Qwen3ASRModel  # noqa: F401
            else:
                import transformers  # noqa: F401
                import tokenizers  # noqa: F401
                import lmformatenforcer  # noqa: F401
                import interegular  # noqa: F401
        except Exception as exc:
            raise HTTPException(503, f"REVIEW_EXECUTOR_IMPORT_FAILED:{type(exc).__name__}") from None
        return {"status": "ok", "model": manifest["model_id"], "weighted": False}

    @app.get("/v1/models")
    def models():
        ids = [manifest["model_id"]] + (
            [asr_manifest["model_id"]] if asr_manifest else []
        )
        return {"data": [{"id": value} for value in ids], "is_mock": False,
                "capabilities": {"review_response_protocol": decoder_identity["protocol"],
                                 "review_decoder_sha256": decoder_identity["decoder_sha256"]}}

    @app.post("/v1/audio/transcriptions")
    def transcribe(
        file: UploadFile = File(...),
        model: str = Form(...),
        response_format: str = Form("verbose_json"),
        prompt: str = Form(""),
    ):
        if not asr_manifest or model != asr_manifest["model_id"]:
            raise HTTPException(404, "EXACT_ASR_MODEL_ID_REQUIRED")
        if response_format != "verbose_json":
            raise HTTPException(422, "UNSUPPORTED_ASR_RESPONSE_FORMAT")
        # The script's terminology.  It is a hint for the recognizer and never
        # a rewrite of the transcript that comes back.
        context = (prompt or "").strip()
        if len(context) > 4000:
            raise HTTPException(422, "ASR_CONTEXT_TOO_LONG")
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

        if asr_manifest.get("executor") == "qwen3-asr":
            # The locally installed Qwen3-ASR model.  It is an existing local
            # resource, independent of the H3 video core, and it reads Chinese
            # far more reliably than the Whisper executor.
            return _transcribe_qwen3_asr(
                asr_manifest, asr_root, decoded, model, executor_identity, guard, state,
                decoder_runtime_identity, decoder_identity, context=context,
            )
        from transformers import pipeline

        with guard:
            if decoder_runtime_identity() != decoder_identity:
                raise HTTPException(409, "EXECUTOR_DECODER_FILES_CHANGED")
            if state["asr"] is None:
                verify_model_manifest(asr_manifest)
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
                "executor_identity": executor_identity(model),
                "speaker": "UNKNOWN",
                "confidence": None,
            }

    @app.post("/v1/chat/completions")
    def complete(body: dict):
        if body.get("model") != manifest["model_id"]:
            raise HTTPException(404, "EXACT_MODEL_ID_REQUIRED")
        try:
            token_limit = review_token_limit(body)
            response_schema, schema_hash = review_response_schema(
                body, decoder_identity.get("supported_schema_sha256", decoder_identity["schema_sha256"])
            )
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
            if decoder_runtime_identity() != decoder_identity:
                raise HTTPException(409, "REVIEW_DECODER_IDENTITY_CHANGED")
            if state["model"] is None:
                verify_model_manifest(manifest)
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
            try:
                prefix = review_prefix_function(state["processor"].tokenizer, response_schema)
                with torch.inference_mode():
                    output = state["model"].generate(
                        **inputs, max_new_tokens=token_limit, do_sample=False, prefix_allowed_tokens_fn=prefix
                    )
            except Exception:
                raise HTTPException(422, "REVIEW_STRUCTURED_DECODING_FAILED") from None
            generated = output[:, inputs["input_ids"].shape[1] :]
            answer = state["processor"].batch_decode(
                generated, skip_special_tokens=True
            )[0]
            return {
                "model": manifest["model_id"],
                "is_mock": False,
                "executor_identity": executor_identity(manifest["model_id"]),
                "structured_output": {**decoder_identity, "schema_sha256": schema_hash},
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
