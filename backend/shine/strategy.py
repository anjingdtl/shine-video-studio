"""The user-facing production strategy contract of the dynamic per-shot round.

The user chooses a *strategy* - quantized Ref2VA model, acceleration recipe,
optional true second sampling, optional RelayVSR X2 - and never a workflow,
a slot count or a node.  The strategy is a small, typed, ``extra="forbid"``
contract that freezes into the director package manifest (it therefore takes
part in ``manifest_sha256`` and the run's idempotency) and travels with every
series and job.  Each shot's workflow is compiled per shot from this strategy
plus the shot's own reference manifest (``shine.comfy.graph_compiler``).

Availability is derived from evidence, never hard-coded: model variants come
from the audited variant manifests, accelerators from the compiler's recipe
table, and the upscale backend from the RelayVSR capability record.  A
candidate variant or an unverified recipe is named and blocked - never
silently swapped for another quantization.
"""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from shine.comfy.graph_compiler import (
    ACCELERATORS,
    BASELINE_ACCELERATOR_ID,
    LATENT_UPSCALER,
    PDD_FILE,
    VARIANT_ACCELERATORS,
    SecondPassSpec,
)
from shine.comfy.workflow import graph_hash as _dict_hash


class SecondPassConfig(BaseModel):
    """True second sampling: a second scheduler + refine stage after pass one."""

    model_config = ConfigDict(extra="forbid")
    enabled: bool = False
    recipe_id: str = Field(default="h3-tail-refine-v1", max_length=64)
    denoise: float = Field(default=0.25, gt=0.0, le=1.0)
    latent_scale: float = Field(default=1.0, ge=1.0, le=2.0)

    def to_spec(self) -> SecondPassSpec | None:
        if not self.enabled:
            return None
        return SecondPassSpec(
            recipe_id=self.recipe_id,
            denoise=self.denoise,
            latent_scale=self.latent_scale,
        )


class UpscaleConfig(BaseModel):
    """Post-production upscale stage; RelayVSR X2 runs after H3 completes."""

    model_config = ConfigDict(extra="forbid")
    enabled: bool = False
    engine: Literal["relayvsr"] = "relayvsr"
    variant: Literal["x2"] = "x2"
    quality_preset: Literal["balanced", "quality", "fast"] = "balanced"


class GenerationStrategy(BaseModel):
    """Everything the user picks before one-click shooting."""

    model_config = ConfigDict(extra="forbid")
    schema_version: Literal["1"] = "1"
    generation_mode: Literal["Ref2VA"] = "Ref2VA"
    model_variant_id: str = Field(default="h3-ref2va-int8-convrot", max_length=64)
    # Schema 1 also reads partial strategies persisted by older series.
    accelerator_id: str = Field(default="pdd8", max_length=32)
    second_pass: SecondPassConfig = Field(default_factory=SecondPassConfig)
    upscale: UpscaleConfig = Field(default_factory=UpscaleConfig)

    def strategy_sha256(self) -> str:
        return _dict_hash(self.model_dump())

    def second_pass_spec(self) -> SecondPassSpec | None:
        return self.second_pass.to_spec()


DEFAULT_STRATEGY = GenerationStrategy(accelerator_id=BASELINE_ACCELERATOR_ID)


def parse_strategy(payload: dict | None) -> GenerationStrategy | None:
    """Parse an optional strategy payload; ``None`` stays ``None``.

    An invalid payload raises ``ValueError`` with the pydantic message so API
    boundaries can surface a 422 instead of guessing a default strategy.
    """
    if payload is None:
        return None
    if isinstance(payload, GenerationStrategy):
        return payload
    return GenerationStrategy.model_validate(payload)


def model_variant_options() -> list[dict]:
    """Available model variants derived from the audited variant manifests."""
    from shine.comfyops.variants import list_variant_manifests

    options: list[dict] = []
    for manifest in list_variant_manifests():
        variant_id = str(manifest.get("variant_id") or "")
        if not variant_id:
            continue
        acceptance = str(manifest.get("acceptance_status") or "UNKNOWN")
        options.append({
            "variant_id": variant_id,
            "label": str(manifest.get("display_name") or variant_id),
            "acceptance_status": acceptance,
            "verified": acceptance in ("SUPPORTED", "EXPERIMENTAL") and variant_id in VARIANT_ACCELERATORS,
            "tier": manifest.get("tier"),
            "default_recommended": bool(manifest.get("default_recommended")),
        })
    return options


def _optional_capability_blockers(db, settings, capability: str) -> list[dict]:
    """Check one optional capability (pdd / second_pass) against sealed evidence.

    When sealed live ``object_info`` evidence exists (from ``validate_services``),
    both the recorded node/weight probe and current on-disk weights are checked
    so missing optional dependencies are named explicitly without silent fallback.
    Before live validation runs (e.g. unconfigured machines or offline unit
    tests), recipe-verified options stay selectable.
    """
    if db is None:
        return []
    from pathlib import Path

    from shine.comfyops.existing_installation import declared_model_family_roots, model_family_directories
    from shine.comfyops.setup_summary import resolve_installation, sealed_optional_capabilities

    code = "PDD_BACKEND_UNAVAILABLE" if capability == "pdd" else "SECOND_PASS_BACKEND_UNAVAILABLE"
    sealed = sealed_optional_capabilities(db, settings)
    if sealed is None:
        return []
    live = sealed.get(capability)
    if isinstance(live, dict) and live.get("available") is False:
        missing_nodes = list(live.get("missing_nodes") or [])
        missing_weights = list(live.get("missing_weights") or [])
        return [{
            "code": code,
            "missing_nodes": missing_nodes,
            "missing_weights": missing_weights,
            "detail": (
                f"本机运行环境缺少 {capability} 依赖："
                + ("、".join(missing_nodes + missing_weights) or "未就绪")
            ),
        }]

    installation = resolve_installation(db, settings)["selected"]
    root_value = (installation or {}).get("root") if isinstance(installation, dict) else None
    if not root_value or (installation or {}).get("is_mock"):
        return []
    root = Path(str(root_value))
    models_root = root / "models"
    if not models_root.is_dir():
        return []
    declared = declared_model_family_roots(root)

    def _has_weight(family: str, filename: str) -> bool:
        leaf = filename.replace("\\", "/").split("/")[-1]
        dirs = model_family_directories(models_root, family, declared)
        return any((directory / leaf).is_file() for directory in dirs)

    missing_weights: list[str] = []
    if not _has_weight("pdd_acc", PDD_FILE):
        missing_weights.append(PDD_FILE)
    if capability == "second_pass" and not _has_weight("latent_upscale_models", LATENT_UPSCALER):
        missing_weights.append(LATENT_UPSCALER)
    if missing_weights:
        return [{
            "code": code,
            "missing_nodes": [],
            "missing_weights": missing_weights,
            "detail": f"本机缺少可选权重：{', '.join(missing_weights)}",
        }]
    return []


def accelerator_options(db=None, settings=None) -> list[dict]:
    """Available acceleration recipes derived from the compiler's table."""
    rows: list[dict] = []
    for accelerator_id, spec in ACCELERATORS.items():
        recipe_verified = spec["requires_pdd"] is not None
        blockers = (
            _optional_capability_blockers(db, settings, "pdd")
            if (recipe_verified and spec["requires_pdd"])
            else []
        )
        verified = recipe_verified and not blockers
        unverified_reason = (
            None if verified
            else (blockers[0]["code"] if blockers else "ACCELERATOR_UNVERIFIED")
        )
        rows.append({
            "accelerator_id": accelerator_id,
            "steps": int(spec["steps"] or 0),
            "verified": verified,
            "unverified_reason": unverified_reason,
            "blockers": blockers,
        })
    return rows


def upscale_options(db, settings=None) -> list[dict]:
    """Available upscale backends; RelayVSR readiness is evidence-derived.

    Reads the same machine verdict as the setup capability card — never a
    bare no-evidence call, which would report BLOCKED on a prepared machine.
    """
    from shine.comfyops.setup_summary import relayvsr_machine_readiness

    readiness = relayvsr_machine_readiness(db, settings)
    return [{
        "engine": "relayvsr",
        "variant": "x2",
        "enabled": readiness.get("available") is True,
        "status": readiness.get("status") or "UNKNOWN",
        "blockers": readiness.get("blockers") or [],
    }]


def strategy_options(db, settings=None) -> dict:
    """The full option matrix the director UI renders."""
    sp_blockers = _optional_capability_blockers(db, settings, "second_pass")
    return {
        "model_variants": model_variant_options(),
        "accelerators": accelerator_options(db, settings),
        "second_pass": {
            "supported": not sp_blockers,
            "recipes": ["h3-tail-refine-v1"],
            "blockers": sp_blockers,
        },
        "upscale": upscale_options(db, settings),
    }


def strategy_blockers(strategy: GenerationStrategy, db, settings=None) -> list[dict]:
    """Named blockers that make this strategy not shootable as-is.

    A blocked strategy is refused at package build and job creation; it never
    falls back to another variant, accelerator or backend.
    """
    blockers: list[dict] = []
    for option in model_variant_options():
        if option["variant_id"] == strategy.model_variant_id:
            if not option["verified"]:
                blockers.append({
                    "code": "MODEL_VARIANT_NOT_ACCEPTED",
                    "variant_id": strategy.model_variant_id,
                    "acceptance_status": option["acceptance_status"],
                })
            break
    else:
        blockers.append({
            "code": "MODEL_VARIANT_UNKNOWN",
            "variant_id": strategy.model_variant_id,
        })
    spec = ACCELERATORS.get(strategy.accelerator_id)
    if spec is None:
        blockers.append({
            "code": "ACCELERATOR_UNKNOWN",
            "accelerator_id": strategy.accelerator_id,
        })
    elif spec["requires_pdd"] is None:
        blockers.append({
            "code": "ACCELERATOR_UNVERIFIED",
            "accelerator_id": strategy.accelerator_id,
        })
    elif spec["requires_pdd"]:
        blockers.extend(_optional_capability_blockers(db, settings, "pdd"))
    if strategy.second_pass.enabled:
        blockers.extend(_optional_capability_blockers(db, settings, "second_pass"))
    if strategy.upscale.enabled:
        from shine.comfyops.setup_summary import relayvsr_machine_readiness

        readiness = relayvsr_machine_readiness(db, settings)
        if readiness.get("available") is not True:
            blockers.append({
                "code": "UPSCALE_BACKEND_UNAVAILABLE",
                "engine": strategy.upscale.engine,
                "detail": readiness.get("blockers") or readiness.get("status"),
            })
    return blockers


def strategy_summary(strategy: GenerationStrategy) -> dict:
    """A compact, human-facing description of the strategy."""
    return {
        "schema_version": strategy.schema_version,
        "model_variant_id": strategy.model_variant_id,
        "accelerator_id": strategy.accelerator_id,
        "second_pass_enabled": strategy.second_pass.enabled,
        "upscale_enabled": strategy.upscale.enabled,
        "strategy_sha256": strategy.strategy_sha256(),
    }
