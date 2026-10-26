"""In-process local Qwen SDK bridge, no unprotected clone HTTP endpoint.

SDK imports are lazy and local_files_only. Exact package/runtime/file hashes must
be registered and installed. Tests inject the same upstream-shaped SDK methods.
"""

from __future__ import annotations

import gc
import hashlib
import importlib.metadata
import io
import struct
import unicodedata
import wave

from shine.comfyops.installer import ControlledDownloader, PackageFile, safe_target
from shine.comfyops.models import ModelPackage

CAPABILITIES = {"design": "VOICE_DESIGN", "base": "VOICE_CLONE", "asr": "ASR", "align": "FORCED_ALIGNMENT"}


def restore_unspoken_text(words, text):
    """Attach script punctuation/spacing to observed tokens; never change timing.

    A spoken-character difference is left untouched for the strict dialogue QC
    to reject. In particular this cannot repair a dropped word or a zero span.
    """
    original = [dict(word) for word in words]
    if "".join(w["text"] for w in original) == text or not original:
        return original

    def unspoken(char):
        return char.isspace() or unicodedata.category(char).startswith("P")

    counts = [sum(not unspoken(c) for c in w["text"]) for w in original]
    actual = "".join(c for w in original for c in w["text"] if not unspoken(c))
    expected = "".join(c for c in text if not unspoken(c))
    if actual != expected or not all(counts):
        return original
    cursor, result = 0, []
    for word, count in zip(original, counts, strict=True):
        start = cursor
        while count:
            count -= not unspoken(text[cursor])
            cursor += 1
        while cursor < len(text) and unspoken(text[cursor]):
            cursor += 1
        result.append({**word, "text": text[start:cursor]})
    return result


def encode_wav(samples, rate):
    if hasattr(samples, "tolist"):
        samples = samples.tolist()
    output = io.BytesIO()
    with wave.open(output, "wb") as stream:
        stream.setnchannels(1)
        stream.setsampwidth(2)
        stream.setframerate(rate)
        stream.writeframes(
            b"".join(struct.pack("<h", max(-32768, min(32767, round(float(x) * 32767)))) for x in samples)
        )
    return output.getvalue(), len(samples) / rate


def decode_wav(audio, *, numpy=False):
    with wave.open(io.BytesIO(audio), "rb") as stream:
        if stream.getnchannels() != 1 or stream.getsampwidth() != 2:
            raise ValueError("VOICE_REFERENCE_WAV_PCM_MONO_REQUIRED")
        values = [x[0] / 32768 for x in struct.iter_unpack("<h", stream.readframes(stream.getnframes()))]
        rate = stream.getframerate()
    if numpy:
        import numpy as np

        values = np.asarray(values, dtype=np.float32)
    return values, rate


class QwenLoader:
    is_mock = False

    def __call__(self, role, path):
        if role in {"design", "base"}:
            from qwen_tts import Qwen3TTSModel

            return Qwen3TTSModel.from_pretrained(str(path), device_map="cuda:0", local_files_only=True)
        if role == "asr":
            from qwen_asr import Qwen3ASRModel

            return Qwen3ASRModel.from_pretrained(str(path), device_map="cuda:0", local_files_only=True)
        from qwen_asr import Qwen3ForcedAligner

        return Qwen3ForcedAligner.from_pretrained(str(path), device_map="cuda:0", local_files_only=True)


class QwenLocalVoiceProvider:
    name = "QwenLocalVoiceSDK"

    def __init__(self, factory, settings, packages, loader=None):
        self.factory, self.settings, self.packages = factory, settings, packages
        self.loader = loader or QwenLoader()
        if getattr(self.loader, "is_external", False):
            self.name = "QwenRoleProcessSDK"
        self.models, self.references = {}, {}
        self.last_alignment_evidence = None
        self.resource_mb = 4000

    @property
    def is_mock(self):
        return self.loader.is_mock or bool(getattr(self.loader, "result_is_mock", False))

    def _numpy_input(self):
        return not self.loader.is_mock and not getattr(self.loader, "is_external", False)

    def resource_budget_mb(self, method):
        if getattr(self.loader, "is_external", False):
            if method == "prepare_conditioning":
                return 1  # Pinning PCM reference bytes performs no inference.
            role = {
                "design": "design",
                "clone": "base",
                "synthesize": "base",
                "transcribe": "asr",
                "align": "align",
            }.get(method)
            if role:
                return self.loader.resource_budget_mb(role)
        return self.resource_mb

    def _load(self, role):
        if role in self.models:
            return self.models[role]
        if self.models:
            self.release_models()  # Only one checkpoint may reside inside the admitted budget.
        identity = self.packages.get(role)
        with self.factory() as db:
            package = db.get(ModelPackage, identity) if identity else None
            if not package or package.status != "FILES_VERIFIED_PENDING_RUNTIME":
                raise ValueError("LOCAL_MODEL_PACKAGE_NOT_VERIFIED")
            p = package.payload
            if CAPABILITIES[role] not in p["capabilities"] or p["license_id"].lower() in {"unknown", "unverified"}:
                raise ValueError("LOCAL_MODEL_CAPABILITY_OR_LICENSE_MISSING")
            if not self.loader.is_mock:
                versions = p.get("runtime_versions", {})
                required = "qwen-tts" if role in {"base", "design"} else "qwen-asr"
                external = getattr(self.loader, "is_external", False)
                if required not in versions:
                    raise ValueError("SDK_VERSION_NOT_PINNED_OR_CHANGED")
                if external:
                    self.loader.validate_package(role, p)
                elif any(importlib.metadata.version(k) != v for k, v in versions.items()):
                    raise ValueError("SDK_VERSION_NOT_PINNED_OR_CHANGED")
                budget = self.loader.resource_budget_mb(role) if external else self.resource_mb
                if p.get("resource_mb") is None or p["resource_mb"] > budget:
                    raise ValueError("VOICE_RESOURCE_PROFILE_MISSING_OR_EXCEEDED")
                if not external:
                    import torch

                    if not torch.cuda.is_available() or torch.cuda.mem_get_info()[0] < p["resource_mb"] * 1024**2:
                        raise ValueError("VOICE_PHYSICAL_GPU_BUDGET_UNAVAILABLE")
            root = self.settings.data_dir / "comfyops" / "isolated-packages" / p["name"]
            for entry in p["files"]:
                if not ControlledDownloader.valid(
                    safe_target(root, entry["relative_path"]), PackageFile.model_validate(entry)
                ):
                    raise ValueError("LOCAL_MODEL_HASH_CHANGED")
        self.models[role] = self.loader(role, root)
        return self.models[role]

    def _result(self, waves, rate, character, role):
        if len(waves) != 1:
            raise ValueError("VOICE_BATCH_CONTRACT_INVALID")
        audio, duration = encode_wav(waves[0], rate)
        return {
            "audio": audio,
            "duration_s": duration,
            "speaker": character,
            "language": "zh-CN",
            "model": self.packages[role],
            "is_mock": self.is_mock,
            "speaker_verification": "PENDING_HUMAN_REVIEW",
        }

    def design(self, description, text, character):
        waves, rate = self._load("design").generate_voice_design(text=text, language="Chinese", instruct=description)
        return self._result(waves, rate, character, "design")

    def clone(self, reference, reference_text, character):
        waves, rate = self._load("base").generate_voice_clone(
            text=reference_text,
            language="Chinese",
            ref_audio=decode_wav(reference, numpy=self._numpy_input()),
            ref_text=reference_text,
        )
        return self._result(waves, rate, character, "base")

    def prepare_conditioning(self, audio, digest, reference_text):
        if hashlib.sha256(audio).hexdigest() != digest:
            raise ValueError("VOICE_CONDITIONING_HASH_MISMATCH")
        decode_wav(audio)
        self.references[digest] = (audio, reference_text)

    def synthesize(self, text, character, conditioning, emotion, speed, pause):
        if emotion != "neutral" or speed != 1 or pause != 0:
            raise ValueError("CAPABILITY_UNSUPPORTED_EXPRESSIVE_PARAMETERS")
        if conditioning not in self.references:
            raise ValueError("APPROVED_CONDITIONING_REQUIRED")
        audio, ref_text = self.references[conditioning]
        waves, rate = self._load("base").generate_voice_clone(
            text=text, language="Chinese", ref_audio=decode_wav(audio, numpy=self._numpy_input()), ref_text=ref_text
        )
        return self._result(waves, rate, character, "base")

    def transcribe(self, audio):
        result = self._load("asr").transcribe(audio=decode_wav(audio, numpy=self._numpy_input()), language="Chinese")[0]
        # Upstream ASR does not establish voice identity or calibrated confidence.
        return {
            "text": result.text,
            "speaker": getattr(result, "speaker", "UNKNOWN"),
            "language": "zh-CN" if result.language == "Chinese" else result.language,
            "confidence": getattr(result, "confidence", 0),
            "is_mock": self.is_mock,
            "model": self.packages["asr"],
        }

    def align(self, audio, text):
        words = self._load("align").align(
            audio=decode_wav(audio, numpy=self._numpy_input()), text=text, language="Chinese"
        )[0]
        raw = [{"text": word.text, "start_s": word.start_time, "end_s": word.end_time} for word in words]
        restored = restore_unspoken_text(raw, text)
        self.last_alignment_evidence = {
            "raw_words": raw,
            "unspoken_text_restored": restored != raw,
            "timing_modified": False,
        }
        return restored

    def release_models(self):
        self.models.clear()
        gc.collect()
        if getattr(self.loader, "is_external", False):
            return self.loader.release_models()
        if not self.is_mock:
            import torch

            if torch.cuda.is_available():
                torch.cuda.synchronize()
                torch.cuda.empty_cache()
                if torch.cuda.memory_allocated() != 0:
                    raise ValueError("MODEL_RELEASE_UNCONFIRMED")
        if self.models:
            raise ValueError("MODEL_RELEASE_UNCONFIRMED")
