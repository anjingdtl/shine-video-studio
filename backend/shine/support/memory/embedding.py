"""A tiny, deterministic local embedding used by the memory index.

No model weights, no GPU, no network and no extra dependency.  The vector is a
signed hashing embedding over Chinese character n-grams and ASCII words: it is
good at the lexical/semantic overlap a personal production memory actually
needs ("青橙色调" vs "青橙影调"), costs microseconds on CPU, and can be rebuilt
at any time from the authoritative memory text.

The provider name and dimension are stored with every vector, so installing a
larger local model later is an explicit, rebuildable change rather than a
silent reinterpretation of old bytes.
"""
from __future__ import annotations

import hashlib
import math
import re
import unicodedata
from array import array
from collections.abc import Iterable

PROVIDER = "local-hash-ngram-v1"
DIM = 256
#: The optional semantic model is not bundled in this version.  Reporting it
#: honestly keeps "FTS5 works, vector is the lightweight local fallback" visible
#: in the UI instead of pretending a model was verified.
SEMANTIC_MODEL = "NOT_CONFIGURED"

_CJK = re.compile(r"[\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff]")
_ASCII_WORD = re.compile(r"[a-z0-9_]{2,}")


def normalise_text(value: str) -> str:
    return unicodedata.normalize("NFKC", value or "").strip()


def _features(text: str) -> Iterable[str]:
    lowered = normalise_text(text).casefold()
    words = _ASCII_WORD.findall(lowered)
    for word in words:
        yield f"w:{word}"
        if len(word) >= 4:
            for index in range(len(word) - 2):
                yield f"w3:{word[index:index + 3]}"
    chars = list(lowered)
    for index, char in enumerate(chars):
        if _CJK.match(char):
            yield f"c:{char}"
            if index + 1 < len(chars) and _CJK.match(chars[index + 1]):
                yield f"b:{char}{chars[index + 1]}"
    # A short ASCII/Latin segment inside Chinese is still a useful signal.
    for token in re.findall(r"[a-zA-Z]{2,}", lowered):
        yield f"t:{token[:12]}"


def _bucket(feature: str) -> tuple[int, float]:
    digest = hashlib.blake2b(feature.encode("utf-8"), digest_size=8).digest()
    value = int.from_bytes(digest, "big")
    index = value % DIM
    sign = 1.0 if (value >> 8) & 1 else -1.0
    return index, sign


def embed(text: str) -> list[float]:
    vector = [0.0] * DIM
    for feature in _features(text):
        index, sign = _bucket(feature)
        vector[index] += sign
    norm = math.sqrt(sum(value * value for value in vector))
    if norm <= 0:
        return vector
    return [value / norm for value in vector]


def pack(vector: list[float]) -> bytes:
    return array("f", vector).tobytes()


def unpack(blob: bytes) -> list[float]:
    values = array("f")
    values.frombytes(blob)
    return list(values)


def cosine(left: list[float], right: list[float]) -> float:
    if not left or not right or len(left) != len(right):
        return 0.0
    # Both sides are L2-normalised by ``embed``; a defensive clamp keeps a
    # corrupt blob from producing a score outside the documented range.
    value = sum(a * b for a, b in zip(left, right, strict=False))
    return max(-1.0, min(1.0, value))


def similarity(left: str, right: str) -> float:
    return cosine(embed(left), embed(right))


def status_payload() -> dict[str, object]:
    return {
        "provider": PROVIDER,
        "dim": DIM,
        "available": True,
        "semantic_model": SEMANTIC_MODEL,
        "semantic_model_verified": False,
        "mode": "CPU 本地哈希向量（无模型、无 GPU、无网络）",
        "note": "不是大模型语义向量；语义模型未随本版本验证，检索仍可与 FTS5 混合使用。",
    }
