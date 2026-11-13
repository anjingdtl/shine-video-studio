"""Local, bounded recovery of a crew answer that broke the envelope.

A model answer that carries a complete proposal inside a slightly wrong JSON
envelope is not a failed scene (the 2026-11-13 run had scene 0009 die four
times on ``SCHEMA_VALIDATION_FAILED`` while its shots were all there).  The
contracts are ``extra="forbid"`` on purpose — an unknown key must not slip into
a frozen artifact — so the refusal is right; what was missing was the local
normalisation step the streamline plan §6.2 prescribes:

    保存可用内容 → 本地规范化 → 有限的定向修复 → 用户编辑

This module is that second step.  It is deliberately lossless and timid:

* it only *removes* keys the contract does not know and fills a canonical
  field from a documented alias when the canonical one is absent;
* it never writes a required creative field (visual prose, dialogue text,
  composition) — content the model did not produce stays missing and the
  validation keeps refusing it honestly;
* every change is recorded as a note so the artifact's trace can say how the
  answer was read;
* an answer that validates directly is never touched.
"""

from __future__ import annotations

from typing import Any, Union

from pydantic import BaseModel, ValidationError

#: Field aliases real models write instead of the canonical contract name.
#: Applied only when the canonical field is absent; the original key is
#: dropped only because ``extra="forbid"`` would reject it anyway.
_FIELD_ALIASES: dict[str, dict[str, str]] = {
    "DialogueLine": {"speaker": "speaker_id", "character": "speaker_id",
                     "name": "speaker_id", "line": "text", "dialogue": "text",
                     "content": "text", "start": "start_s", "time": "start_s"},
    "SceneBeat": {"summary": "description", "beat": "description",
                  "cast": "characters", "speakers": "characters"},
    "ShotIntent": {"duration": "duration_s", "length_s": "duration_s",
                   "seconds": "duration_s", "visual": "visual_description",
                   "assets": "asset_ids"},
    "EditorialCut": {"cut_start_s": "start_s", "time": "start_s",
                     "visual": "visual_description"},
    "CameraShot": {"intent": "intent_id", "shot_id": "intent_id",
                   "frame": "composition", "camera": "composition",
                   "camera_movement": "movement"},
}


def _fields_of(model: type[BaseModel]) -> dict[str, Any]:
    return dict(model.model_fields)


def _coerce_scalar(value: Any, annotation: Any) -> Any:
    """Numeric strings for numeric fields; nothing else is rewritten."""
    if isinstance(value, str) and annotation in (float, int, "float", "int"):
        try:
            return float(value) if annotation in (float, "float") else int(value)
        except ValueError:
            return value
    return value


def _annotation_items(annotation: Any) -> Any:
    """The item type of ``list[X]`` (or ``Field(default_factory=list)``)."""
    if hasattr(annotation, "__origin__") and annotation.__origin__ is list:
        args = getattr(annotation, "__args__", ())
        return args[0] if args else None
    return None


def _is_model(annotation: Any) -> bool:
    if annotation is None:
        return False
    origin = getattr(annotation, "__origin__", None)
    if origin is Union:
        args = [a for a in getattr(annotation, "__args__", ()) if a is not type(None)]
        return len(args) == 1 and isinstance(args[0], type) and issubclass(args[0], BaseModel)
    return isinstance(annotation, type) and issubclass(annotation, BaseModel)


def _model_of(annotation: Any) -> type[BaseModel] | None:
    if not _is_model(annotation):
        return None
    origin = getattr(annotation, "__origin__", None)
    if origin is Union:
        for arg in getattr(annotation, "__args__", ()):
            if isinstance(arg, type) and issubclass(arg, BaseModel):
                return arg
        return None
    return annotation


def _normalize(data: Any, model: type[BaseModel] | None, notes: list[str],
               *, path: str = "$", counter: dict[str, int]) -> Any:
    if model is None:
        return data
    if not isinstance(data, dict):
        return data
    aliases = _FIELD_ALIASES.get(model.__name__, {})
    fields = _fields_of(model)
    out: dict[str, Any] = {}
    for key, value in data.items():
        target = key if key in fields else None
        if target is None and key in aliases and aliases[key] in fields:
            # The alias fills its canonical field only when that field did not
            # arrive on its own; otherwise the alias is noise and is dropped.
            target = aliases[key]
            if target in data:
                notes.append(f"{path}.{key}:alias_ignored({target}_present)")
                continue
            notes.append(f"{path}.{key}:aliased_to_{target}")
        elif target is None:
            notes.append(f"{path}.{key}:unknown_field_dropped")
            continue
        field = fields[target]
        nested = _model_of(field.annotation)
        item = _annotation_items(field.annotation)
        if nested is not None and isinstance(value, dict):
            out[target] = _normalize(value, nested, notes, path=f"{path}.{target}", counter=counter)
        elif item is not None and isinstance(item, type) and issubclass(item, BaseModel) \
                and isinstance(value, list):
            out[target] = [_normalize(row, item, notes, path=f"{path}.{target}[{i}]",
                                      counter=counter) for i, row in enumerate(value)]
        else:
            out[target] = _coerce_scalar(value, field.annotation)
    for name, field in fields.items():
        if name in out:
            continue
        if not field.is_required():
            continue
        if field.annotation is str and name == "id":
            # The entity's own name may be minted locally and stably; a
            # *reference* to another entity (beat_id, intent_id, rule_id) may
            # not — a fabricated join key would detach the proposal from the
            # shot it belongs to, so it stays missing and keeps refusing.
            counter[name] = counter.get(name, 0) + 1
            out[name] = f"salvaged-id-{counter[name]:02d}"
            notes.append(f"{path}.{name}:stable_id_generated")
            continue
        # Deterministic timecodes the plan §6.2 allows the program to supply:
        # a dialogue line without its end gets one from a fixed speech-rate
        # estimate of its own text (CJK ≈ 4 characters per second), never
        # from a guess about neighbouring lines.
        if model.__name__ == "DialogueLine" and name == "end_s":
            start = float(out.get("start_s") or 0.0)
            text = str(out.get("text") or "")
            out[name] = round(start + max(0.8, len(text) / 4.0), 3)
            notes.append(f"{path}.{name}:deterministic_timecode_filled")
            continue
        # Required-with-factory containers the model omitted: an empty
        # container satisfies a min_length=0 contract and keeps the rest of
        # the answer alive.
        if field.default_factory is not None:
            try:
                if field.default_factory() == []:
                    out[name] = []
                    notes.append(f"{path}.{name}:empty_list_filled")
            except Exception:  # noqa: BLE001 - a factory failure is not our business
                pass
    return out


def salvage_crew_answer(data: Any, contract: type[BaseModel]) -> tuple[Any, list[str]]:
    """Normalise one crew answer against its contract, losslessly.

    Returns the (possibly same) data and the notes describing every change.
    The caller re-validates; this function never claims the result is valid.
    """
    if not isinstance(data, dict):
        return data, []
    notes: list[str] = []
    counter: dict[str, int] = {}
    normalized = _normalize(data, contract, notes, counter=counter)
    return normalized, notes


def parse_with_salvage(data: Any, contract: type[BaseModel]) -> tuple[BaseModel, list[str]]:
    """Validate directly first; only a refusal triggers the local recovery.

    Returns ``(proposal, notes)``.  When even the normalised answer cannot
    validate, the *original* ``ValidationError`` is re-raised so the caller's
    failure path reports exactly what the contract refused — never a synthetic
    error that hides the real one.
    """
    direct: ValidationError | None = None
    try:
        return contract.model_validate(data), []
    except ValidationError as exc:
        direct = exc
    normalized, notes = salvage_crew_answer(data, contract)
    try:
        return contract.model_validate(normalized), notes
    except ValidationError:
        raise direct from None  # type: ignore[misc]
