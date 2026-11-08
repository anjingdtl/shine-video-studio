"""Fail-closed JSON discovery for built-in director style profiles."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from shine.director_styles.contracts import DirectorStyleProfile

MAX_PROFILE_BYTES = 64 * 1024
MAX_PROFILE_FILES = 200
DEFAULT_PROFILE_DIR = Path(__file__).resolve().parents[3] / "config" / "director_styles" / "builtin"


class DirectorStyleCatalogError(ValueError):
    """Stable rejection raised when any discovered template is unsafe or invalid."""


@dataclass(frozen=True)
class CatalogEntry:
    profile: DirectorStyleProfile
    sha256: str
    source: str = "builtin"

    @property
    def identity(self) -> tuple[str, str]:
        return self.profile.id, self.profile.version


def canonical_json(value: Any) -> str:
    """Canonical DSE serialization: UTF-8 Unicode, sorted keys, compact JSON."""
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)


def canonical_sha256(value: Any) -> str:
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def _pairs_no_duplicates(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    value: dict[str, Any] = {}
    for key, item in pairs:
        if key in value:
            raise DirectorStyleCatalogError("STYLE_PROFILE_DUPLICATE_JSON_KEY")
        value[key] = item
    return value


class DirectorStyleCatalog:
    """Loads every JSON file in a directory on each read; filenames are not identities."""

    def __init__(self, directory: Path | str | None = None):
        self.directory = Path(directory) if directory is not None else DEFAULT_PROFILE_DIR

    def all(self) -> list[CatalogEntry]:
        try:
            self.directory.resolve(strict=True)
        except FileNotFoundError as exc:
            raise DirectorStyleCatalogError("STYLE_PROFILE_CATALOG_UNAVAILABLE") from exc
        files = sorted(self.directory.glob("*.json"), key=lambda path: path.name)
        if len(files) > MAX_PROFILE_FILES:
            raise DirectorStyleCatalogError("STYLE_PROFILE_CATALOG_TOO_LARGE")
        entries: list[CatalogEntry] = []
        seen: set[tuple[str, str]] = set()
        for path in files:
            if path.is_symlink() or not path.is_file():
                raise DirectorStyleCatalogError("STYLE_PROFILE_INVALID_FILE")
            try:
                raw = path.read_bytes()
            except OSError as exc:
                raise DirectorStyleCatalogError("STYLE_PROFILE_READ_FAILED") from exc
            if len(raw) > MAX_PROFILE_BYTES:
                raise DirectorStyleCatalogError("STYLE_PROFILE_TOO_LARGE")
            try:
                parsed = json.loads(raw.decode("utf-8", errors="strict"), object_pairs_hook=_pairs_no_duplicates)
                profile = DirectorStyleProfile.model_validate(parsed)
            except DirectorStyleCatalogError:
                raise
            except (UnicodeDecodeError, json.JSONDecodeError, RecursionError, ValidationError, TypeError) as exc:
                raise DirectorStyleCatalogError("STYLE_PROFILE_INVALID") from exc
            identity = (profile.id, profile.version)
            if identity in seen:
                raise DirectorStyleCatalogError("STYLE_PROFILE_DUPLICATE_ID_VERSION")
            seen.add(identity)
            payload = profile.model_dump(mode="json")
            entries.append(CatalogEntry(profile=profile, sha256=canonical_sha256(payload)))
        return sorted(entries, key=lambda entry: (entry.profile.id, _semver(entry.profile.version)))

    def list_latest(self) -> list[CatalogEntry]:
        latest: dict[str, CatalogEntry] = {}
        for entry in self.all():
            previous = latest.get(entry.profile.id)
            if previous is None or _semver(entry.profile.version) > _semver(previous.profile.version):
                latest[entry.profile.id] = entry
        return sorted(latest.values(), key=lambda entry: entry.profile.id)

    def get(self, profile_id: str, version: str | None = None) -> CatalogEntry:
        candidates = [
            item for item in self.all()
            if item.profile.id == profile_id and (version is None or item.profile.version == version)
        ]
        if not candidates:
            raise DirectorStyleCatalogError("STYLE_PROFILE_NOT_FOUND")
        return max(candidates, key=lambda item: _semver(item.profile.version))


def _semver(value: str) -> tuple[int, int, int]:
    return tuple(int(part) for part in value.split("."))
