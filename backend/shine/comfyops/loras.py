"""Single authoritative Style LoRA registry, importer, discovery, and adjudicator.

Three Principles alignment (一套事实、一个裁决、一个执行者):
- One Fact: ``style_lora_assets`` persists immutable file/metadata identity
  only (``id/display_name/filename/storage_path/sha256/file_size_bytes/
  default_strength/trigger_word/origin``). Live ComfyUI visibility and runtime
  ``status`` are projected dynamically from disk + ``/object_info/LoraLoaderModelOnly``
  and never cached in SQLite.
- One Managed Storage Root: user-imported LoRAs are stored atomically under
  ``<comfyops_dir>/models/loras/shine_user/`` and exposed to both managed and
  adopted ComfyUI instances via ``<comfyops_dir>/setup/shine-style-loras.yaml``.
- One Adjudicator: ``evaluate_style_lora_selection()`` is the single gate
  shared by 7-stage package readiness/start, 4-stage snapshot/run, strategy
  validation, and Worker pre-submit verification.
- One Executor: ``import_style_lora()`` is the single import path for both
  multipart uploads and local-path imports.
"""

from __future__ import annotations

import hashlib
import json
import math
import os
import re
import struct
import tempfile
from collections.abc import Iterable, Mapping, Sequence
from pathlib import Path
from typing import Any, BinaryIO
from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.orm import Session

from shine.comfy.graph_compiler import (
    INT8_VARIANT_ID,
    MAX_STYLE_LORAS,
    STYLE_LORA_STRENGTH_MAX,
    STYLE_LORA_STRENGTH_MIN,
    GraphCompileError,
    StyleLoraSpec,
    validate_style_lora_trigger_word,
)
from shine.comfyops.models import StyleLoraAsset
from shine.models import utcnow

REALISM_LORA_ID = "h3-realism-people"
REALISM_LORA_DISPLAY_NAME = "电影化写实 LoRA (H3 Realism)"
REALISM_LORA_FILENAME = "h3-realism-people-t2v-i2v-r2v.safetensors"
REALISM_LORA_SUBDIR = "MiniMax-H3"

MAX_SAFETENSORS_HEADER_BYTES = 32 * 1024 * 1024  # 32 MiB anti-DoS cap
_COPY_CHUNK_BYTES = 1024 * 1024
_SHA256_RE = re.compile(r"^[a-f0-9]{64}$")
_SAFE_FILENAME_RE = re.compile(r"[^A-Za-z0-9._-]+")

#: Stat-keyed SHA-256 cache for on-disk LoRA integrity checks.
#: Keyed by ``(resolved_path, mtime_ns, size)`` so any modification or tampering
#: on disk invalidates the entry immediately and re-hashes the file.
_LORA_HASH_CACHE: dict[tuple[str, int, int], str] = {}


class StyleLoraError(ValueError):
    """Domain error raised by the single Style LoRA executor/adjudicator."""

    def __init__(
        self,
        code: str,
        message: str,
        *,
        http_status: int = 409,
        details: dict[str, Any] | None = None,
    ):
        super().__init__(message)
        self.code = code
        self.message = message
        self.http_status = http_status
        self.details = details or {}


def _comfyops_dir(settings: Any) -> Path | None:
    if settings is None:
        return None
    raw = getattr(settings, "comfyops_dir", None)
    if raw is not None:
        return Path(raw).resolve()
    data_dir = getattr(settings, "data_dir", None)
    if data_dir is not None:
        from shine.comfyops.storage import storage_root

        return storage_root(Path(data_dir)).resolve()
    return None


def managed_loras_root(settings: Any) -> Path:
    base = _comfyops_dir(settings)
    if base is None:
        raise StyleLoraError(
            "MANAGED_INSTANCE_NOT_CONFIGURED",
            "未配置受管模型目录，无法管理风格 LoRA。",
            http_status=409,
        )
    return (base / "models" / "loras").resolve()


def managed_user_loras_dir(settings: Any) -> Path:
    return (managed_loras_root(settings) / "shine_user").resolve()


def style_lora_extra_paths_file(settings: Any) -> Path | None:
    base = _comfyops_dir(settings)
    if base is None:
        return None
    return (base / "setup" / "shine-style-loras.yaml").resolve()


def ensure_style_lora_extra_paths_config(settings: Any) -> Path | None:
    """Expose ``<comfyops_dir>/models/loras`` to both managed and adopted ComfyUI."""
    cfg_path = style_lora_extra_paths_file(settings)
    if cfg_path is None:
        return None
    loras_root = managed_loras_root(settings)
    user_dir = managed_user_loras_dir(settings)
    user_dir.mkdir(parents=True, exist_ok=True)
    cfg_path.parent.mkdir(parents=True, exist_ok=True)

    from shine.comfyops.installer import durable_json

    durable_json(cfg_path, {"shine_style_loras": {"loras": str(loras_root)}})
    return cfg_path


def _validate_safetensors_and_hash(path: Path) -> tuple[str, int]:
    """Validate `.safetensors` extension, header structure, and stream-hash bytes."""
    if path.suffix.lower() != ".safetensors":
        raise StyleLoraError(
            "LORA_INVALID_EXTENSION",
            f"仅支持 .safetensors 格式的风格 LoRA 文件（收到 {path.suffix or '无后缀'}）。",
            http_status=422,
            details={"filename": path.name},
        )
    try:
        file_size = path.stat().st_size
    except OSError as exc:
        raise StyleLoraError(
            "LORA_FILE_MISSING",
            f"无法读取 LoRA 文件：{path.name}",
            http_status=409,
            details={"filename": path.name},
        ) from exc

    if file_size < 10:
        raise StyleLoraError(
            "LORA_CORRUPT_HEADER",
            f"LoRA 文件「{path.name}」过小，不是合法的 safetensors 文件。",
            http_status=422,
            details={"filename": path.name, "size": file_size},
        )

    hasher = hashlib.sha256()
    try:
        with path.open("rb") as stream:
            len_bytes = stream.read(8)
            if len(len_bytes) != 8:
                raise StyleLoraError(
                    "LORA_CORRUPT_HEADER",
                    f"LoRA 文件「{path.name}」缺少 safetensors 文件头长度。",
                    http_status=422,
                )
            hasher.update(len_bytes)
            (header_len,) = struct.unpack("<Q", len_bytes)
            if header_len < 2 or header_len > MAX_SAFETENSORS_HEADER_BYTES or 8 + header_len > file_size:
                raise StyleLoraError(
                    "LORA_CORRUPT_HEADER",
                    f"LoRA 文件「{path.name}」safetensors 文件头长度非法（{header_len} 字节）。",
                    http_status=422,
                    details={"header_length": header_len, "file_size": file_size},
                )
            header_raw = stream.read(header_len)
            if len(header_raw) != header_len:
                raise StyleLoraError(
                    "LORA_CORRUPT_HEADER",
                    f"LoRA 文件「{path.name}」safetensors 文件头截断。",
                    http_status=422,
                )
            hasher.update(header_raw)
            try:
                header = json.loads(header_raw.decode("utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError) as exc:
                raise StyleLoraError(
                    "LORA_CORRUPT_HEADER",
                    f"LoRA 文件「{path.name}」safetensors JSON 头无法解析。",
                    http_status=422,
                ) from exc
            if not isinstance(header, dict):
                raise StyleLoraError(
                    "LORA_CORRUPT_HEADER",
                    f"LoRA 文件「{path.name}」safetensors 头格式不是对象。",
                    http_status=422,
                )
            data_bytes = file_size - 8 - header_len
            tensor_entries = 0
            for key, meta in header.items():
                if key == "__metadata__":
                    if not isinstance(meta, dict):
                        raise StyleLoraError(
                            "LORA_CORRUPT_HEADER",
                            f"LoRA 文件「{path.name}」__metadata__ 格式非法。",
                            http_status=422,
                        )
                    continue
                if not isinstance(meta, dict):
                    raise StyleLoraError(
                        "LORA_CORRUPT_HEADER",
                        f"LoRA 文件「{path.name}」张量头条目格式非法。",
                        http_status=422,
                    )
                offsets = meta.get("data_offsets")
                if (
                    "dtype" not in meta
                    or not isinstance(meta.get("shape"), list)
                    or not isinstance(offsets, list)
                    or len(offsets) != 2
                    or not all(isinstance(v, int) and not isinstance(v, bool) for v in offsets)
                    or offsets[0] < 0
                    or offsets[1] < offsets[0]
                    or offsets[1] > data_bytes
                ):
                    raise StyleLoraError(
                        "LORA_CORRUPT_HEADER",
                        f"LoRA 文件「{path.name}」张量偏移或结构校验失败。",
                        http_status=422,
                        details={"tensor": str(key)[:80]},
                    )
                tensor_entries += 1
            if tensor_entries == 0:
                raise StyleLoraError(
                    "LORA_CORRUPT_HEADER",
                    f"LoRA 文件「{path.name}」不包含任何模型权重张量。",
                    http_status=422,
                )
            while True:
                chunk = stream.read(_COPY_CHUNK_BYTES)
                if not chunk:
                    break
                hasher.update(chunk)
    except StyleLoraError:
        raise
    except OSError as exc:
        raise StyleLoraError(
            "LORA_CORRUPT_HEADER",
            f"读取 LoRA 文件「{path.name}」失败：{exc}",
            http_status=422,
        ) from exc

    return hasher.hexdigest(), int(file_size)


def _cached_file_sha256(path: Path) -> str:
    stat = path.stat()
    resolved = str(path.resolve())
    key = (resolved, stat.st_mtime_ns, stat.st_size)
    hit = _LORA_HASH_CACHE.get(key)
    if hit is not None:
        return hit
    with path.open("rb") as stream:
        digest = hashlib.file_digest(stream, "sha256").hexdigest()
    if len(_LORA_HASH_CACHE) >= 256:
        _LORA_HASH_CACHE.clear()
    _LORA_HASH_CACHE[key] = digest
    return digest


def _validate_strength(value: float | int) -> float:
    try:
        strength = float(value)
    except (TypeError, ValueError) as exc:
        raise StyleLoraError(
            "STYLE_LORA_STRENGTH_OUT_OF_RANGE",
            "风格 LoRA 权重必须是数字。",
            http_status=422,
        ) from exc
    if (
        not math.isfinite(strength)
        or strength < STYLE_LORA_STRENGTH_MIN
        or strength > STYLE_LORA_STRENGTH_MAX
    ):
        raise StyleLoraError(
            "STYLE_LORA_STRENGTH_OUT_OF_RANGE",
            f"风格 LoRA 权重必须在 [{STYLE_LORA_STRENGTH_MIN}, {STYLE_LORA_STRENGTH_MAX}] 之间。",
            http_status=422,
            details={"strength": value},
        )
    return round(strength, 4)


def _validate_trigger(value: str | None) -> str:
    try:
        return validate_style_lora_trigger_word(value)
    except GraphCompileError as exc:
        raise StyleLoraError(
            exc.code,
            exc.message,
            http_status=422,
            details=exc.details,
        ) from exc


def _sanitize_lora_filename(filename: str) -> str:
    base = Path(str(filename or "").replace("\\", "/")).name
    stem = Path(base).stem
    safe_stem = _SAFE_FILENAME_RE.sub("_", stem).strip("._-") or "style_lora"
    return f"{safe_stem[:120]}.safetensors"


def _known_lora_roots(db: Session | None, settings: Any | None) -> list[Path]:
    roots: list[Path] = []
    seen: set[str] = set()

    def _add(candidate: Path | None) -> None:
        if candidate is None:
            return
        try:
            resolved = candidate.resolve()
        except OSError:
            return
        key = str(resolved).casefold()
        if key not in seen and resolved.is_dir():
            seen.add(key)
            roots.append(resolved)

    base = _comfyops_dir(settings)
    if base is not None:
        _add(base / "models" / "loras")
        _add(base / "isolated-packages" / "minimax-h3-lora")

    if db is not None:
        try:
            from shine.comfyops.existing_installation import declared_model_family_roots, model_family_directories
            from shine.comfyops.setup_summary import resolve_installation

            sel = resolve_installation(db, settings).get("selected") or {}
            root_val = sel.get("root")
            if root_val and not sel.get("is_mock"):
                inst_root = Path(str(root_val))
                models_dir = inst_root / "models"
                declared = declared_model_family_roots(inst_root)
                for directory in model_family_directories(models_dir, "loras", declared):
                    _add(directory)
        except Exception:  # noqa: BLE001 - best-effort root discovery
            pass

    for probe_root in getattr(settings, "comfy_probe_roots", ()) or ():
        _add(Path(str(probe_root)) / "models" / "loras")

    return roots


def _find_realism_lora_on_disk(db: Session | None, settings: Any | None) -> Path | None:
    target_lower = REALISM_LORA_FILENAME.lower()
    for root in _known_lora_roots(db, settings):
        direct = root / REALISM_LORA_SUBDIR / REALISM_LORA_FILENAME
        if direct.is_file() and direct.resolve() == direct:
            return direct
        # Also handle case variations (e.g. Minimax-H3 vs MiniMax-H3) or flat package layout.
        for child in root.iterdir():
            if child.is_dir() and child.name.lower() == REALISM_LORA_SUBDIR.lower():
                candidate = child / REALISM_LORA_FILENAME
                if candidate.is_file() and candidate.resolve() == candidate:
                    return candidate
            elif child.is_file() and child.name.lower() == target_lower and child.resolve() == child:
                return child
    return None


def sync_discovered_loras(db: Session, settings: Any | None) -> list[StyleLoraAsset]:
    """Idempotently register the migrated realism LoRA when present on disk."""
    found = _find_realism_lora_on_disk(db, settings)
    if found is None:
        return []
    try:
        sha256, size_bytes = _validate_safetensors_and_hash(found)
    except StyleLoraError:
        return []

    existing = db.scalar(select(StyleLoraAsset).where(StyleLoraAsset.sha256 == sha256))
    if existing is not None:
        return [existing]

    by_id = db.get(StyleLoraAsset, REALISM_LORA_ID)
    if by_id is not None:
        return [by_id]

    row = StyleLoraAsset(
        id=REALISM_LORA_ID,
        display_name=REALISM_LORA_DISPLAY_NAME,
        filename=found.name,
        storage_path=str(found.resolve()),
        sha256=sha256,
        file_size_bytes=size_bytes,
        default_strength=0.65,
        trigger_word="",
        origin="discovered",
    )
    db.add(row)
    db.flush()
    return [row]


def import_style_lora(
    db: Session,
    settings: Any,
    *,
    local_path: str | Path | None = None,
    upload_filename: str | None = None,
    upload_stream: BinaryIO | None = None,
    display_name: str | None = None,
    default_strength: float = 0.65,
    trigger_word: str = "",
    actor: str = "local-user",
) -> tuple[StyleLoraAsset, bool]:
    """Single import executor for both multipart file uploads and local-path imports."""
    strength = _validate_strength(default_strength)
    trigger = _validate_trigger(trigger_word)

    if local_path is not None and upload_stream is not None:
        raise StyleLoraError(
            "LORA_IMPORT_AMBIGUOUS",
            "请仅选择一种导入方式（上传文件或本地路径）。",
            http_status=422,
        )
    if local_path is None and upload_stream is None:
        raise StyleLoraError(
            "LORA_IMPORT_INPUT_REQUIRED",
            "请提供 .safetensors 文件或本地文件路径。",
            http_status=422,
        )

    user_dir = managed_user_loras_dir(settings)
    user_dir.mkdir(parents=True, exist_ok=True)
    tmp_path: Path | None = None
    source_name = ""

    try:
        if local_path is not None:
            src = Path(str(local_path).strip())
            if not src.is_absolute() or (os.name == "nt" and str(src).startswith("\\\\")):
                raise StyleLoraError(
                    "LORA_FILE_MISSING",
                    "请提供本机绝对路径（不支持相对路径或网络 UNC 路径）。",
                    http_status=422,
                )
            if src.suffix.lower() != ".safetensors":
                raise StyleLoraError(
                    "LORA_INVALID_EXTENSION",
                    f"仅支持 .safetensors 格式的风格 LoRA 文件（收到 {src.suffix or '无后缀'}）。",
                    http_status=422,
                )
            if not src.is_file() or src.is_symlink():
                raise StyleLoraError(
                    "LORA_FILE_MISSING",
                    f"找不到指定的 LoRA 文件：{src}",
                    http_status=409,
                )
            source_name = src.name
            sha256, size_bytes = _validate_safetensors_and_hash(src)
        else:
            raw_name = Path(str(upload_filename or "style_lora.safetensors").replace("\\", "/")).name
            if Path(raw_name).suffix.lower() != ".safetensors":
                raise StyleLoraError(
                    "LORA_INVALID_EXTENSION",
                    f"仅支持 .safetensors 格式的风格 LoRA 文件（收到 {Path(raw_name).suffix or '无后缀'}）。",
                    http_status=422,
                )
            source_name = raw_name
            fd, tmp_name = tempfile.mkstemp(dir=user_dir, prefix=".tmp_lora_", suffix=".safetensors")
            tmp_path = Path(tmp_name)
            with os.fdopen(fd, "wb") as dst:
                while True:
                    chunk = upload_stream.read(_COPY_CHUNK_BYTES)  # type: ignore[union-attr]
                    if not chunk:
                        break
                    dst.write(chunk)
                dst.flush()
                os.fsync(dst.fileno())
            sha256, size_bytes = _validate_safetensors_and_hash(tmp_path)

        existing = db.scalar(select(StyleLoraAsset).where(StyleLoraAsset.sha256 == sha256))
        if existing is not None:
            existing_path = Path(existing.storage_path)
            if existing_path.is_file() and _cached_file_sha256(existing_path) == sha256:
                ensure_style_lora_extra_paths_config(settings)
                return existing, True
            # Repopulate missing managed file atomically if previous file disappeared.
            if existing.origin == "imported":
                target = existing_path if existing_path.parent.resolve() == user_dir else (
                    user_dir / _sanitize_lora_filename(existing.filename)
                )
                if local_path is not None:
                    _atomic_copy_file(Path(str(local_path).strip()), target)
                elif tmp_path is not None:
                    os.replace(tmp_path, target)
                    tmp_path = None
                existing.storage_path = str(target.resolve())
                existing.file_size_bytes = size_bytes
                existing.updated_at = utcnow()
                db.flush()
                ensure_style_lora_extra_paths_config(settings)
                return existing, True

        safe_name = _sanitize_lora_filename(source_name)
        target_path = (user_dir / safe_name).resolve()
        if target_path.exists():
            stem = target_path.stem
            target_path = (user_dir / f"{stem}_{sha256[:8]}.safetensors").resolve()

        if local_path is not None:
            _atomic_copy_file(Path(str(local_path).strip()), target_path)
        elif tmp_path is not None:
            os.replace(tmp_path, target_path)
            tmp_path = None

        ensure_style_lora_extra_paths_config(settings)
        clean_display = str(display_name or "").strip()[:160] or Path(source_name).stem[:160] or target_path.stem
        row = StyleLoraAsset(
            id=f"lora_{uuid4().hex[:12]}",
            display_name=clean_display,
            filename=source_name[:255],
            storage_path=str(target_path),
            sha256=sha256,
            file_size_bytes=size_bytes,
            default_strength=strength,
            trigger_word=trigger,
            origin="imported",
        )
        db.add(row)
        db.flush()

        from shine.services import add_audit

        add_audit(
            db,
            actor,
            "STYLE_LORA_IMPORTED",
            "style_lora",
            row.id,
            {
                "lora_id": row.id,
                "filename": row.filename,
                "sha256": row.sha256,
                "file_size_bytes": row.file_size_bytes,
                "origin": row.origin,
            },
        )
        return row, False
    finally:
        if tmp_path is not None and tmp_path.exists():
            try:
                tmp_path.unlink()
            except OSError:
                pass


def _atomic_copy_file(src: Path, dst: Path) -> None:
    dst.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(dir=dst.parent, prefix=".tmp_lora_", suffix=".safetensors")
    tmp_path = Path(tmp_name)
    try:
        with os.fdopen(fd, "wb") as out, src.open("rb") as inp:
            while True:
                chunk = inp.read(_COPY_CHUNK_BYTES)
                if not chunk:
                    break
                out.write(chunk)
            out.flush()
            os.fsync(out.fileno())
        os.replace(tmp_path, dst)
    finally:
        if tmp_path.exists():
            try:
                tmp_path.unlink()
            except OSError:
                pass


def extract_comfy_lora_names(object_info: dict[str, Any] | None) -> list[str] | None:
    """Extract the live ``LoraLoaderModelOnly.lora_name`` enum list from ComfyUI ``object_info``."""
    if not isinstance(object_info, dict):
        return None
    node = object_info.get("LoraLoaderModelOnly")
    if not isinstance(node, dict):
        return None
    input_spec = node.get("input") or {}
    entry = (input_spec.get("required") or {}).get("lora_name") or (input_spec.get("optional") or {}).get("lora_name")
    if not isinstance(entry, list) or not entry:
        return None
    first = entry[0]
    if isinstance(first, list):
        return [str(item) for item in first if isinstance(item, str)]
    if first == "COMBO" and len(entry) > 1 and isinstance(entry[1], dict):
        options = entry[1].get("options")
        if isinstance(options, list):
            return [str(item) for item in options if isinstance(item, str)]
    return None


def probe_live_lora_object_info(db: Session | None, settings: Any | None) -> tuple[dict[str, Any] | None, bool]:
    """Read-only live probe of ``/object_info/LoraLoaderModelOnly`` without starting ComfyUI."""
    if settings is None:
        return None, False
    urls: list[str] = []

    def _add_url(raw: str | None) -> None:
        if not raw:
            return
        cleaned = str(raw).strip().rstrip("/")
        if cleaned and cleaned not in urls:
            urls.append(cleaned)

    if db is not None:
        try:
            from shine.comfyops.runtime_configuration import ACTIVE_KEY, resolve_runtime
            from shine.models import AppSetting

            if db.get(AppSetting, ACTIVE_KEY) is not None:
                resolved_settings = resolve_runtime(db, settings)
                _add_url(getattr(resolved_settings, "comfyui_base_url", None))
        except Exception:  # noqa: BLE001 - offline/unconfigured is not an error here
            pass

    if getattr(settings, "render_provider", "mock") == "comfyui":
        _add_url(getattr(settings, "comfyui_base_url", None))

    if not urls:
        return None, False

    from shine.comfy.client import ComfyError, ComfyUIClient
    from shine.http_env import is_local_endpoint

    allow_remote = bool(getattr(settings, "allow_remote_comfyui", False))
    for base_url in urls:
        if not allow_remote and not is_local_endpoint(base_url):
            continue
        client = ComfyUIClient(base_url, timeout_s=1.2, max_retries=0)
        try:
            info = client.object_info_node("LoraLoaderModelOnly", timeout_s=1.2, max_retries=0)
            if isinstance(info, dict) and "LoraLoaderModelOnly" in info:
                return info, True
        except (ComfyError, ValueError):
            continue
    return None, False


def _canonical_relative_lora_name(storage_path: Path, lora_roots: list[Path]) -> str:
    try:
        resolved = storage_path.resolve()
    except OSError:
        resolved = storage_path
    for root in lora_roots:
        try:
            rel = resolved.relative_to(root.resolve())
            return str(rel)
        except ValueError:
            continue
    return storage_path.name


def _match_comfy_lora_name(
    asset: StyleLoraAsset,
    available_names: list[str],
    lora_roots: list[Path],
) -> str | None:
    if not available_names:
        return None
    norm_to_exact: dict[str, str] = {}
    for candidate in available_names:
        norm = candidate.replace("\\", "/").lstrip("/").casefold()
        norm_to_exact.setdefault(norm, candidate)

    rel = _canonical_relative_lora_name(Path(asset.storage_path), lora_roots)
    rel_norm = rel.replace("\\", "/").lstrip("/").casefold()
    if rel_norm in norm_to_exact:
        return norm_to_exact[rel_norm]

    suffix = f"/{rel_norm}"
    suffix_matches = [exact for norm, exact in norm_to_exact.items() if norm.endswith(suffix)]
    if len(suffix_matches) == 1:
        return suffix_matches[0]

    file_norm = Path(asset.storage_path).name.casefold()
    basename_matches = [
        exact
        for norm, exact in norm_to_exact.items()
        if norm == file_norm or norm.endswith(f"/{file_norm}")
    ]
    if len(basename_matches) == 1:
        return basename_matches[0]
    return None


def project_style_lora_asset(
    asset: StyleLoraAsset,
    *,
    available_names: list[str] | None,
    comfy_reachable: bool,
    lora_roots: list[Path],
) -> dict[str, Any]:
    """Compute live runtime projection (status + comfy_lora_name) without caching to SQLite."""
    disk_path = Path(asset.storage_path)
    comfy_name: str | None = None
    if not disk_path.is_file():
        status = "MISSING_FILE"
    else:
        try:
            actual_sha = _cached_file_sha256(disk_path)
        except OSError:
            actual_sha = ""
        if actual_sha != asset.sha256:
            status = "HASH_MISMATCH"
        elif comfy_reachable and available_names is not None:
            matched = _match_comfy_lora_name(asset, available_names, lora_roots)
            if matched is not None:
                status = "READY"
                comfy_name = matched
            else:
                status = "NEEDS_COMFY_REFRESH"
        else:
            status = "COMFY_UNREACHABLE"
            comfy_name = _canonical_relative_lora_name(disk_path, lora_roots)

    return {
        "id": asset.id,
        "display_name": asset.display_name,
        "filename": asset.filename,
        "storage_path": asset.storage_path,
        "sha256": asset.sha256,
        "file_size_bytes": int(asset.file_size_bytes),
        "default_strength": float(asset.default_strength),
        "trigger_word": asset.trigger_word or "",
        "origin": asset.origin,
        "comfy_lora_name": comfy_name,
        "status": status,
        "created_at": asset.created_at.isoformat() + "Z",
        "updated_at": asset.updated_at.isoformat() + "Z",
    }


def list_style_loras(
    db: Session,
    settings: Any | None,
    *,
    object_info: dict[str, Any] | None = None,
    probe_comfy: bool = True,
) -> dict[str, Any]:
    """Idempotently discover built-in realism LoRA and project live ComfyUI visibility."""
    sync_discovered_loras(db, settings)
    lora_roots = _known_lora_roots(db, settings)
    if object_info is not None:
        comfy_reachable = True
        available_names = extract_comfy_lora_names(object_info)
    elif probe_comfy:
        live_info, comfy_reachable = probe_live_lora_object_info(db, settings)
        available_names = extract_comfy_lora_names(live_info) if live_info is not None else None
    else:
        comfy_reachable = False
        available_names = None

    rows = db.scalars(
        select(StyleLoraAsset).order_by(StyleLoraAsset.origin.asc(), StyleLoraAsset.created_at.asc())
    ).all()
    items = [
        project_style_lora_asset(
            row,
            available_names=available_names,
            comfy_reachable=comfy_reachable,
            lora_roots=lora_roots,
        )
        for row in rows
    ]
    return {
        "items": items,
        "comfy_reachable": comfy_reachable,
        "max_style_loras": MAX_STYLE_LORAS,
    }


def _extract_entry_fields(item: Any) -> tuple[str, str, str, float, str]:
    if isinstance(item, StyleLoraSpec):
        return (
            item.lora_id,
            item.lora_name,
            item.sha256,
            item.strength_model,
            item.trigger_word,
        )
    if isinstance(item, Mapping):
        return (
            str(item.get("lora_id") or "").strip(),
            str(item.get("lora_name") or "").strip(),
            str(item.get("sha256") or "").strip().lower(),
            item.get("strength_model", 0.65),
            str(item.get("trigger_word") or ""),
        )
    return (
        str(getattr(item, "lora_id", "") or "").strip(),
        str(getattr(item, "lora_name", "") or "").strip(),
        str(getattr(item, "sha256", "") or "").strip().lower(),
        getattr(item, "strength_model", 0.65),
        str(getattr(item, "trigger_word", "") or ""),
    )


def evaluate_style_lora_selection(
    db: Session | None,
    settings: Any | None,
    selection: Sequence[Any] | None,
    *,
    variant_id: str,
    object_info: dict[str, Any] | None = None,
) -> tuple[StyleLoraSpec, ...]:
    """Single authoritative adjudicator for style LoRA assembly across all gates.

    Called by strategy validation, 7-stage package readiness/start, 4-stage
    snapshot/run, and Worker pre-submit verification.  Returns an empty tuple
    with zero overhead or blockers when ``selection`` is empty.
    """
    entries = list(selection or ())
    if not entries:
        return ()
    if len(entries) > MAX_STYLE_LORAS:
        raise StyleLoraError(
            "STYLE_LORA_LIMIT_EXCEEDED",
            f"风格 LoRA 最多允许串联 {MAX_STYLE_LORAS} 个，当前选择了 {len(entries)} 个。",
            http_status=422,
            details={"count": len(entries), "limit": MAX_STYLE_LORAS},
        )
    if (variant_id or INT8_VARIANT_ID) != INT8_VARIANT_ID:
        raise StyleLoraError(
            "VARIANT_LORA_UNSUPPORTED",
            f"模型变体 {variant_id} 暂不支持挂载风格 LoRA（V1 仅在 INT8 主线启用）。",
            http_status=409,
            details={"variant_id": variant_id},
        )

    if db is not None:
        sync_discovered_loras(db, settings)
    lora_roots = _known_lora_roots(db, settings)
    available_names = extract_comfy_lora_names(object_info) if object_info is not None else None

    seen_ids: set[str] = set()
    seen_hashes: set[str] = set()
    resolved_specs: list[StyleLoraSpec] = []

    for item in entries:
        lora_id, _req_name, req_sha, raw_strength, raw_trigger = _extract_entry_fields(item)
        if not lora_id:
            raise StyleLoraError(
                "LORA_FILE_MISSING",
                "风格 LoRA 缺少 lora_id。",
                http_status=422,
            )
        if lora_id in seen_ids or (req_sha and req_sha in seen_hashes):
            raise StyleLoraError(
                "STYLE_LORA_DUPLICATE",
                f"风格 LoRA「{lora_id}」重复选择，同一套风格 LoRA 只允许挂载一次。",
                http_status=422,
                details={"lora_id": lora_id, "sha256": req_sha},
            )
        strength = _validate_strength(raw_strength)
        trigger = _validate_trigger(raw_trigger)

        if db is None:
            raise StyleLoraError(
                "LORA_FILE_MISSING",
                f"缺少风格 LoRA 数据库台账，无法校验「{lora_id}」。",
                http_status=409,
                details={"lora_id": lora_id},
            )
        asset = db.get(StyleLoraAsset, lora_id)
        if asset is None:
            raise StyleLoraError(
                "LORA_FILE_MISSING",
                f"风格 LoRA「{lora_id}」不存在或已从资产台账删除。",
                http_status=409,
                details={"lora_id": lora_id},
            )
        if asset.sha256 in seen_hashes:
            raise StyleLoraError(
                "STYLE_LORA_DUPLICATE",
                f"风格 LoRA「{asset.display_name}」内容哈希重复。",
                http_status=422,
                details={"lora_id": lora_id, "sha256": asset.sha256},
            )
        if req_sha and (not _SHA256_RE.fullmatch(req_sha) or req_sha != asset.sha256):
            raise StyleLoraError(
                "LORA_HASH_MISMATCH",
                f"风格 LoRA「{asset.display_name}」冻结哈希与台账不一致。",
                http_status=409,
                details={"lora_id": lora_id, "expected": req_sha, "actual": asset.sha256},
            )
        disk_path = Path(asset.storage_path)
        if not disk_path.is_file():
            raise StyleLoraError(
                "LORA_FILE_MISSING",
                f"风格 LoRA 文件缺失：{asset.filename}",
                http_status=409,
                details={"lora_id": lora_id, "filename": asset.filename},
            )
        try:
            actual_disk_sha = _cached_file_sha256(disk_path)
        except OSError as exc:
            raise StyleLoraError(
                "LORA_FILE_MISSING",
                f"无法读取风格 LoRA 文件：{asset.filename}",
                http_status=409,
                details={"lora_id": lora_id, "filename": asset.filename},
            ) from exc
        if actual_disk_sha != asset.sha256 or (req_sha and actual_disk_sha != req_sha):
            raise StyleLoraError(
                "LORA_HASH_MISMATCH",
                f"风格 LoRA「{asset.display_name}」磁盘文件校验和已变化，拒绝执行。",
                http_status=409,
                details={"lora_id": lora_id, "expected": asset.sha256, "actual": actual_disk_sha},
            )

        if object_info is not None:
            matched_name = _match_comfy_lora_name(asset, available_names or [], lora_roots)
            if not matched_name:
                raise StyleLoraError(
                    "LORA_NOT_VISIBLE_IN_COMFYUI",
                    (
                        f"ComfyUI 当前未扫描到风格 LoRA「{asset.display_name}」"
                        f"（{asset.filename}），请刷新节点列表或重启受管实例。"
                    ),
                    http_status=409,
                    details={"lora_id": lora_id, "filename": asset.filename},
                )
            comfy_lora_name = matched_name
        else:
            comfy_lora_name = _canonical_relative_lora_name(disk_path, lora_roots)

        seen_ids.add(lora_id)
        seen_hashes.add(asset.sha256)
        resolved_specs.append(
            StyleLoraSpec(
                lora_id=asset.id,
                lora_name=comfy_lora_name,
                sha256=asset.sha256,
                strength_model=strength,
                trigger_word=trigger,
            )
        )

    return tuple(resolved_specs)


def update_style_lora(
    db: Session,
    settings: Any | None,
    lora_id: str,
    *,
    display_name: str | None = None,
    default_strength: float | None = None,
    trigger_word: str | None = None,
    actor: str = "local-user",
) -> dict[str, Any]:
    sync_discovered_loras(db, settings)
    asset = db.get(StyleLoraAsset, lora_id)
    if asset is None:
        raise StyleLoraError("LORA_FILE_MISSING", f"风格 LoRA「{lora_id}」不存在。", http_status=404)
    if display_name is not None:
        cleaned = str(display_name).strip()[:160]
        if not cleaned:
            raise StyleLoraError("LORA_INVALID_DISPLAY_NAME", "展示名称不能为空。", http_status=422)
        asset.display_name = cleaned
    if default_strength is not None:
        asset.default_strength = _validate_strength(default_strength)
    if trigger_word is not None:
        asset.trigger_word = _validate_trigger(trigger_word)
    asset.updated_at = utcnow()
    db.flush()

    from shine.services import add_audit

    add_audit(
        db,
        actor,
        "STYLE_LORA_UPDATED",
        "style_lora",
        asset.id,
        {
            "lora_id": asset.id,
            "display_name": asset.display_name,
            "default_strength": asset.default_strength,
            "trigger_word": asset.trigger_word,
        },
    )

    live_info, comfy_reachable = probe_live_lora_object_info(db, settings)
    available_names = extract_comfy_lora_names(live_info) if live_info is not None else None
    return project_style_lora_asset(
        asset,
        available_names=available_names,
        comfy_reachable=comfy_reachable,
        lora_roots=_known_lora_roots(db, settings),
    )


def _strategy_references_lora(strategy: Any, lora_id: str, sha256: str) -> bool:
    if not isinstance(strategy, dict):
        return False
    for row in strategy.get("style_loras") or []:
        if not isinstance(row, dict):
            continue
        if row.get("lora_id") == lora_id or row.get("sha256") == sha256:
            return True
    return False


def _ensure_lora_not_in_use(
    db: Session,
    asset: StyleLoraAsset,
    *,
    m4_session_factory: Any = None,
) -> None:
    from shine.director.models import DirectorPackageVersion
    from shine.models import Job
    from shine.production.models import ProductionRun

    active_runs = db.scalars(
        select(ProductionRun).where(
            ProductionRun.status.in_(["DRAFT", "QUEUED", "RUNNING", "PAUSED", "NEEDS_REVIEW"])
        )
    ).all()
    for run in active_runs:
        pkg = db.get(DirectorPackageVersion, run.package_id) if run.package_id else None
        strategy = ((pkg.manifest or {}).get("binding") or {}).get("strategy") if pkg else None
        if _strategy_references_lora(strategy, asset.id, asset.sha256):
            raise StyleLoraError(
                "LORA_IN_USE",
                f"风格 LoRA「{asset.display_name}」正被运行中生产批次 {run.id[:8]} 引用，无法删除。",
                http_status=409,
                details={"lora_id": asset.id, "run_id": run.id},
            )

    active_jobs = db.scalars(
        select(Job).where(
            Job.status.in_(["QUEUED", "SUBMITTING", "RUNNING", "QC_PENDING", "REPAIR_PENDING"]),
            Job.stale.is_(False),
        )
    ).all()
    for job in active_jobs:
        strategy = (job.request or {}).get("strategy")
        if _strategy_references_lora(strategy, asset.id, asset.sha256):
            raise StyleLoraError(
                "LORA_IN_USE",
                f"风格 LoRA「{asset.display_name}」正被在途镜头任务 {job.id[:8]} 引用，无法删除。",
                http_status=409,
                details={"lora_id": asset.id, "job_id": job.id},
            )

    if m4_session_factory is not None:
        try:
            from shine.manual4.storage.models import M4ExecutionSnapshot, M4ProductionRun

            with m4_session_factory() as m4_db:
                m4_runs = m4_db.scalars(
                    select(M4ProductionRun).where(
                        M4ProductionRun.status.in_(
                            ["PENDING", "CLAIMED", "RUNNING", "PAUSED", "PAUSE_REQUESTED", "NEEDS_REVIEW"]
                        )
                    )
                ).all()
                for m4_run in m4_runs:
                    snap = (
                        m4_db.get(M4ExecutionSnapshot, m4_run.execution_snapshot_id)
                        if m4_run.execution_snapshot_id
                        else None
                    )
                    cfg = (snap.config_json or {}) if snap else {}
                    if _strategy_references_lora(cfg, asset.id, asset.sha256):
                        raise StyleLoraError(
                            "LORA_IN_USE",
                            f"风格 LoRA「{asset.display_name}」正被四阶排片批次 {m4_run.id[:8]} 引用，无法删除。",
                            http_status=409,
                            details={"lora_id": asset.id, "run_id": m4_run.id},
                        )
        except StyleLoraError:
            raise
        except Exception:  # noqa: BLE001 - degraded manual4 DB must not crash delete
            pass


def delete_style_lora(
    db: Session,
    settings: Any | None,
    lora_id: str,
    *,
    m4_session_factory: Any = None,
    actor: str = "local-user",
) -> dict[str, Any]:
    asset = db.get(StyleLoraAsset, lora_id)
    if asset is None:
        raise StyleLoraError("LORA_FILE_MISSING", f"风格 LoRA「{lora_id}」不存在。", http_status=404)

    _ensure_lora_not_in_use(db, asset, m4_session_factory=m4_session_factory)

    file_deleted = False
    if asset.origin == "imported" and settings is not None:
        user_dir = managed_user_loras_dir(settings)
        candidate = Path(asset.storage_path)
        try:
            resolved = candidate.resolve()
            if resolved.is_file() and resolved.is_relative_to(user_dir):
                resolved.unlink()
                file_deleted = True
        except OSError:
            pass

    deleted_id = asset.id
    deleted_origin = asset.origin
    db.delete(asset)
    db.flush()

    from shine.services import add_audit

    add_audit(
        db,
        actor,
        "STYLE_LORA_DELETED",
        "style_lora",
        deleted_id,
        {"lora_id": deleted_id, "origin": deleted_origin, "file_deleted": file_deleted},
    )
    return {"deleted": True, "lora_id": deleted_id, "origin": deleted_origin, "file_deleted": file_deleted}


def apply_style_lora_triggers(
    prompt_text: str,
    style_loras: Iterable[StyleLoraSpec | Any],
) -> str:
    """Single trigger-word injector executed prior to freezing prompt & prompt_sha256."""
    base = str(prompt_text or "")
    triggers: list[str] = []
    seen: set[str] = set()
    for item in style_loras or ():
        raw = item.get("trigger_word") if isinstance(item, Mapping) else getattr(item, "trigger_word", "")
        cleaned = _validate_trigger(raw)
        if cleaned and cleaned.casefold() not in seen:
            seen.add(cleaned.casefold())
            triggers.append(cleaned)
    if not triggers:
        return base
    suffix = f"Style LoRA triggers: {', '.join(triggers)}."
    if not base.strip():
        return suffix
    return f"{base.rstrip()}\n\n{suffix}"
