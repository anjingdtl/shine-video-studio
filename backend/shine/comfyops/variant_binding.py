"""One variant, one catalog, one profile set.

The default installation path binds every workflow profile to the default
catalog (``h3-ref2va-windows-x64``).  A candidate variant such as the
official W6A8 low-end candidate owns its own catalog and its own profiles, so
registering it has to resolve *that* catalog instead of the default one -
and a caller that names a variant and a catalog that disagree must be
refused instead of silently falling back to either of them.

This module is the only place that resolves those three objects against each
other.  It reads committed files; it never downloads, installs or renders.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from functools import lru_cache
from typing import Any

from fastapi import HTTPException

from shine.comfyops import deployment
from shine.comfyops.variants import load_variant_manifest


@dataclass(frozen=True)
class VariantBinding:
    """The resolved identity of one generation stack."""

    variant_id: str
    catalog_id: str
    catalog: dict[str, Any]
    manifest: dict[str, Any]
    profiles: tuple[dict[str, Any], ...]

    @property
    def profile_names(self) -> list[str]:
        return [str(row.get("name")) for row in self.profiles]

    @property
    def acceptance_status(self) -> str:
        return str(self.manifest.get("acceptance_status") or "")

    @property
    def is_accepted(self) -> bool:
        return self.acceptance_status in {"SUPPORTED", "EXPERIMENTAL"}


def _mismatch(variant_id: str, declared: str, requested: str | None) -> HTTPException:
    return HTTPException(409, {
        "code": "VARIANT_CATALOG_MISMATCH",
        "message": "这个变体登记的部署目录和你指定的不一致，不能混用："
                   "变体用的是 " + str(declared) + "，你指定的是 " + str(requested) + "。",
        "variant_id": variant_id,
        "variant_catalog_id": declared,
        "requested_catalog_id": requested,
    })


def profile_files(names: list[str]) -> list[dict[str, Any]]:
    """The committed workflow files for these profile names.

    A name that matches zero or several files is refused: the profile set is
    configuration, and an ambiguous file is not a profile.
    """
    available: list[dict[str, Any]] = []
    for path in sorted(deployment.PROFILE_DIR.glob("*.json")):
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if isinstance(raw, dict):
            available.append(raw)
    found: list[dict[str, Any]] = []
    for name in names:
        matches = [row for row in available if row.get("name") == name]
        if len(matches) != 1:
            raise HTTPException(409, f"PROFILE_FILE_MISSING_OR_AMBIGUOUS: {name}")
        found.append(matches[0])
    return found


def load_binding(variant_id: str, *, catalog_id: str | None = None) -> VariantBinding:
    """Resolve one variant against its own catalog and its own profile files.

    ``catalog_id`` is the caller's *expected* catalog.  When it disagrees with
    the catalog the variant declares, the request is refused: mixing a W6A8
    variant with the INT8 catalog would produce a plan whose weights and whose
    graph belong to two different stacks.

    The result is cached: it reads committed files that do not change while
    the product runs, and the production gate asks for it on every job.
    """
    manifest, catalog = _manifest_and_catalog(variant_id)
    declared = str(manifest.get("catalog_id") or "")
    if catalog_id and catalog_id != declared:
        raise _mismatch(variant_id, declared, catalog_id)
    names = [str(name) for name in (catalog.get("workflow_profiles") or [])]
    return VariantBinding(
        variant_id=variant_id,
        catalog_id=str(catalog.get("catalog_id") or declared),
        catalog=catalog,
        manifest=manifest,
        profiles=tuple(profile_files(names)),
    )


@lru_cache(maxsize=8)
def _manifest_and_catalog(variant_id: str) -> tuple[dict[str, Any], dict[str, Any]]:
    """The manifest and the catalog of one variant, checked against each other.

    Only this part is cached: reading and validating a deployment catalog is
    the expensive half, and the files it reads are committed configuration.
    The workflow files themselves stay fresh, so a profile added to the
    repository is picked up without restarting the product.
    """
    try:
        manifest = load_variant_manifest(variant_id)
    except (OSError, ValueError, FileNotFoundError) as exc:
        raise HTTPException(404, f"VARIANT_MANIFEST_UNKNOWN: {variant_id}") from exc
    declared = str(manifest.get("catalog_id") or "")
    if not declared or declared == "unknown":
        raise HTTPException(409, {
            "code": "VARIANT_CATALOG_UNKNOWN",
            "message": "这个变体还没有钉住部署目录，因此不能登记、不能安装、不能验收。",
            "variant_id": variant_id,
        })
    try:
        catalog = deployment.load_deployment_catalog(declared)
    except HTTPException as exc:
        raise HTTPException(409, {
            "code": "VARIANT_CATALOG_MISSING",
            "message": "这个变体登记的部署目录读不到，不能登记它的生成方案。",
            "variant_id": variant_id,
            "catalog_id": declared,
            "cause": exc.detail,
        }) from exc
    catalog_variant = str(catalog.get("variant_id") or "")
    if catalog_variant != variant_id:
        raise _mismatch(variant_id, catalog_variant or declared, declared)
    return manifest, catalog


def binding_for_catalog(catalog: dict[str, Any]) -> VariantBinding:
    """Resolve the binding a *catalog* declares.

    The default catalog declares the audited INT8 baseline, so the ordinary
    registration path keeps exactly the profiles it always had.  A catalog
    without a declared variant is refused rather than silently treated as the
    default one.
    """
    variant_id = str(catalog.get("variant_id") or "")
    if not variant_id:
        raise HTTPException(409, {
            "code": "CATALOG_VARIANT_UNDECLARED",
            "message": "这个部署目录没有声明变体身份，不能确定它该登记哪些生成方案。",
            "catalog_id": catalog.get("catalog_id"),
        })
    try:
        manifest = load_variant_manifest(variant_id)
    except (OSError, ValueError, FileNotFoundError) as exc:
        raise HTTPException(409, {
            "code": "VARIANT_MANIFEST_UNKNOWN",
            "message": "这个部署目录声明的变体清单读不到，不能登记它的生成方案。",
            "variant_id": variant_id,
        }) from exc
    names = [str(name) for name in (catalog.get("workflow_profiles") or [])]
    return VariantBinding(
        variant_id=variant_id,
        catalog_id=str(catalog.get("catalog_id") or ""),
        catalog=catalog,
        manifest=manifest,
        profiles=tuple(profile_files(names)),
    )


def resolve_registration_target(*, variant_id: str | None = None,
                                 catalog_id: str | None = None) -> VariantBinding:
    """The one resolution used by registration, validation and configuration.

    * no variant and no catalog -> the default catalog (unchanged behaviour);
    * a variant -> that variant's own catalog, and a mismatching catalog is
      refused;
    * a catalog only -> the variant that catalog declares.
    """
    if variant_id:
        return load_binding(variant_id, catalog_id=catalog_id)
    if catalog_id:
        try:
            catalog = deployment.load_deployment_catalog(catalog_id)
        except HTTPException as exc:
            raise HTTPException(404, {
                "code": "DEPLOYMENT_CATALOG_MISSING",
                "message": "这个部署目录读不到。",
                "catalog_id": catalog_id,
                "cause": exc.detail,
            }) from exc
        binding = binding_for_catalog(catalog)
        # ``load_deployment_catalog`` resolves by file stem while the catalog's
        # declared identity carries the "shine-" prefix; a caller may name
        # either form, and only anything else is a real mismatch.
        normalized = binding.catalog_id.removeprefix("shine-")
        if catalog_id not in (binding.catalog_id, normalized):
            raise _mismatch(binding.variant_id, binding.catalog_id, catalog_id)
        return binding
    return binding_for_catalog(deployment.load_default_catalog())