"""Provision the optional review overlay from already verified, pinned wheels."""

from __future__ import annotations

import hashlib
import shutil
import stat
import tempfile
import zipfile
from pathlib import Path, PurePosixPath

from fastapi import HTTPException

# The local ASR executor needs these support packages as well as the five
# top-level packages in the deployment catalog's review_runtime_wheels list.
WHEEL_NAMES = (
    "accelerate",
    "interegular",
    "lm_format_enforcer",
    "nagisa",
    "qwen_asr",
    "qwen_omni_utils",
    "soynlp",
    "tokenizers",
    "transformers",
)


def prepare_review_runtime(settings, catalog: dict) -> str:
    """Extract pinned wheels into a new overlay; never replace existing files.

    This operation does not access the network and runs outside a SQLite writer
    transaction. Missing optional wheels leave the renderer usable as before.
    """
    root = settings.comfyops_dir
    target = root / "setup" / "review-runtime"
    if target.exists() and any(target.iterdir()):
        return "EXISTING_UNCHANGED"
    component = next(
        (c for c in catalog.get("components", []) if c.get("component_id") == "comfy-runtime-wheels"), None
    )
    if not component:
        return "WHEELS_MISSING"
    specs = component.get("files", [])
    wheel_paths = []
    for name in WHEEL_NAMES:
        found = [entry for entry in specs if Path(entry.get("relative_path", "")).name.startswith(name + "-")]
        if len(found) != 1:
            return "WHEELS_MISSING"
        entry = found[0]
        relative = PurePosixPath(entry["relative_path"])
        if relative.is_absolute() or ".." in relative.parts:
            raise HTTPException(409, "REVIEW_WHEEL_PATH_INVALID")
        path = root / "isolated-packages" / "comfy-runtime-wheels" / Path(*relative.parts)
        if not path.is_file() or path.stat().st_size != entry.get("size"):
            return "WHEELS_MISSING"
        with path.open("rb") as stream:
            if hashlib.file_digest(stream, "sha256").hexdigest() != entry.get("sha256"):
                raise HTTPException(409, "REVIEW_WHEEL_HASH_MISMATCH")
        wheel_paths.append(path)

    target.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="review-runtime-", dir=target.parent) as temporary:
        stage = Path(temporary) / "overlay"
        stage.mkdir()
        total = 0
        seen: set[str] = set()
        for wheel in wheel_paths:
            with zipfile.ZipFile(wheel) as archive:
                for member in archive.infolist():
                    path = PurePosixPath(member.filename)
                    if (
                        path.is_absolute()
                        or not path.parts
                        or ".." in path.parts
                        or "\\" in member.filename
                        or ":" in member.filename
                        or stat.S_ISLNK(member.external_attr >> 16)
                        or any(part.endswith(".data") for part in path.parts)
                    ):
                        raise HTTPException(409, "REVIEW_WHEEL_CONTENT_INVALID")
                    if member.is_dir():
                        continue
                    key = member.filename.casefold()
                    total += member.file_size
                    if key in seen or total > 2 * 1024**3:
                        raise HTTPException(409, "REVIEW_WHEEL_CONTENT_INVALID")
                    seen.add(key)
                    destination = stage.joinpath(*path.parts)
                    destination.parent.mkdir(parents=True, exist_ok=True)
                    with archive.open(member) as source, destination.open("xb") as output:
                        shutil.copyfileobj(source, output, length=1024 * 1024)
        if target.exists():
            try:
                target.rmdir()  # only an empty directory created by setup
            except OSError as exc:
                raise HTTPException(409, "REVIEW_RUNTIME_CHANGED_DURING_PREPARATION") from exc
        try:
            stage.rename(target)
        except OSError as exc:
            raise HTTPException(409, "REVIEW_RUNTIME_CHANGED_DURING_PREPARATION") from exc
    return "PREPARED_FROM_VERIFIED_WHEELS"
