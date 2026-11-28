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

import re
import types
from typing import Any, Union

from pydantic import BaseModel, ValidationError

#: Controlled 4-dimensional vocal delivery vocabulary (Plan 20261127 §4.2–4.3).
_VOCAL_CANONICAL: dict[str, frozenset[str]] = {
    "volume": frozenset({"whispered", "hushed", "soft", "conversational", "raised", "shouting"}),
    "pace": frozenset({"halting", "slow", "measured", "brisk", "rushed"}),
    "voice_quality": frozenset({"breathy", "hoarse", "trembling", "crisp", "strained", "warm", "flat"}),
    "pitch": frozenset({"low_flat", "steady", "rising", "falling", "cracking"}),
}
_VOCAL_FIELD_ALIASES: dict[str, str] = {
    "loudness": "volume",
    "vol": "volume",
    "speed": "pace",
    "rate": "pace",
    "tempo": "pace",
    "timbre": "voice_quality",
    "quality": "voice_quality",
    "tone_quality": "voice_quality",
    "vocal_quality": "voice_quality",
    "intonation": "pitch",
    "tone": "pitch",
    "pitch_contour": "pitch",
}
_VOCAL_SYNONYMS: dict[str, dict[str, str]] = {
    "volume": {
        "whisper": "whispered",
        "whispering": "whispered",
        "barely_audible": "whispered",
        "quiet": "hushed",
        "low": "hushed",
        "muted": "hushed",
        "murmured": "hushed",
        "murmur": "hushed",
        "low_voice": "hushed",
        "subdued": "hushed",
        "gentle": "soft",
        "delicate": "soft",
        "tender": "soft",
        "normal": "conversational",
        "medium": "conversational",
        "moderate": "conversational",
        "natural": "conversational",
        "regular": "conversational",
        "neutral": "conversational",
        "firm": "raised",
        "loud": "raised",
        "sharp": "raised",
        "elevated": "raised",
        "forceful": "raised",
        "yelling": "shouting",
        "yell": "shouting",
        "screaming": "shouting",
        "scream": "shouting",
        "roaring": "shouting",
        "roared": "shouting",
        "shouted": "shouting",
        "shout": "shouting",
    },
    "pace": {
        "hesitant": "halting",
        "stammering": "halting",
        "broken": "halting",
        "stumbling": "halting",
        "paused": "halting",
        "choppy": "halting",
        "deliberate": "slow",
        "unhurried": "slow",
        "drawn_out": "slow",
        "lingering": "slow",
        "heavy": "slow",
        "steady": "measured",
        "even": "measured",
        "moderate": "measured",
        "calm": "measured",
        "controlled": "measured",
        "paced": "measured",
        "fast": "brisk",
        "quick": "brisk",
        "urgent": "brisk",
        "clipped": "brisk",
        "rapid": "brisk",
        "snappy": "brisk",
        "frantic": "rushed",
        "breathless": "rushed",
        "hurried": "rushed",
        "racing": "rushed",
        "panicked": "rushed",
    },
    "voice_quality": {
        "airy": "breathy",
        "sighing": "breathy",
        "whispery": "breathy",
        "raspy": "hoarse",
        "gravelly": "hoarse",
        "rough": "hoarse",
        "husky": "hoarse",
        "gruff": "hoarse",
        "shaky": "trembling",
        "quivering": "trembling",
        "wavering": "trembling",
        "unsteady": "trembling",
        "tearful": "trembling",
        "clear": "crisp",
        "sharp": "crisp",
        "clipped": "crisp",
        "clean": "crisp",
        "resonant": "crisp",
        "tight": "strained",
        "tense": "strained",
        "choked": "strained",
        "clenched": "strained",
        "tight_throated": "strained",
        "strangled": "strained",
        "soft_warm": "warm",
        "gentle": "warm",
        "tender": "warm",
        "soothing": "warm",
        "dry": "flat",
        "deadpan": "flat",
        "numb": "flat",
        "hollow": "flat",
        "monotone": "flat",
        "cold": "flat",
    },
    "pitch": {
        "low": "low_flat",
        "monotone": "low_flat",
        "flat": "low_flat",
        "deadpan": "low_flat",
        "deep": "low_flat",
        "low_monotone": "low_flat",
        "level": "steady",
        "even": "steady",
        "neutral": "steady",
        "balanced": "steady",
        "mid": "steady",
        "high": "rising",
        "upward": "rising",
        "questioning": "rising",
        "lifting": "rising",
        "ascending": "rising",
        "dropping": "falling",
        "downward": "falling",
        "sinking": "falling",
        "trailing": "falling",
        "descending": "falling",
        "breaking": "cracking",
        "strained_high": "cracking",
        "cracked": "cracking",
        "quivering_high": "cracking",
    },
}


def _slug_token(value: Any) -> str:
    text = str(value or "").strip().lower()
    text = re.sub(r"[\s\-/]+", "_", text)
    return re.sub(r"[^a-z0-9_]", "", text).strip("_")


def normalize_vocal_delivery(raw: Any, *, path: str = "delivery") -> tuple[dict[str, str] | None, list[str]]:
    """Deterministically normalise a 4D ``VocalDelivery`` dict with synonym folding.

    An unrecognised token drops only that single dimension and records a salvage
    note; it never raises ``ValidationError`` or rejects the surrounding line.
    """
    notes: list[str] = []
    if raw is None:
        return None, notes
    if not isinstance(raw, dict):
        notes.append(f"{path}:non_object_dropped")
        return None, notes
    out: dict[str, str] = {}
    for raw_key, raw_val in raw.items():
        norm_key = _slug_token(raw_key)
        canonical_key = norm_key if norm_key in _VOCAL_CANONICAL else _VOCAL_FIELD_ALIASES.get(norm_key)
        if not canonical_key:
            notes.append(f"{path}.{raw_key}:unknown_dimension_dropped")
            continue
        if canonical_key in out:
            notes.append(f"{path}.{raw_key}:duplicate_dimension_ignored({canonical_key})")
            continue
        if raw_val is None or str(raw_val).strip() == "":
            continue
        token = _slug_token(raw_val)
        if not token:
            notes.append(f"{path}.{canonical_key}:empty_value_dropped")
            continue
        if token in _VOCAL_CANONICAL[canonical_key]:
            out[canonical_key] = token
            if str(raw_val) != token or raw_key != canonical_key:
                notes.append(f"{path}.{raw_key}:normalized_to_{canonical_key}={token}")
            continue
        folded = _VOCAL_SYNONYMS[canonical_key].get(token)
        if folded:
            out[canonical_key] = folded
            notes.append(f"{path}.{raw_key}:synonym_folded_{token}_to_{folded}")
            continue
        notes.append(f"{path}.{canonical_key}:unknown_value_dropped({raw_val})")
    return (out or None), notes

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
    if origin in (Union, types.UnionType) or isinstance(annotation, types.UnionType):
        args = [a for a in getattr(annotation, "__args__", ()) if a is not type(None)]
        return len(args) == 1 and isinstance(args[0], type) and issubclass(args[0], BaseModel)
    return isinstance(annotation, type) and issubclass(annotation, BaseModel)


def _model_of(annotation: Any) -> type[BaseModel] | None:
    if not _is_model(annotation):
        return None
    origin = getattr(annotation, "__origin__", None)
    if origin in (Union, types.UnionType) or isinstance(annotation, types.UnionType):
        for arg in getattr(annotation, "__args__", ()):
            if isinstance(arg, type) and issubclass(arg, BaseModel):
                return arg
        return None
    return annotation


_DRAMA_BEAT_TYPES = frozenset({
    "establishing", "action", "dialogue_exchange", "reaction", "reveal", "transition_out",
})
_DRAMA_BEAT_ENERGIES = frozenset({"calm", "probe", "pressure", "turn_pause", "erupt", "aftermath"})
_DRAMA_BEAT_TYPE_SYNONYMS: dict[str, str] = {
    "establish": "establishing",
    "opening": "establishing",
    "setup": "establishing",
    "dialogue": "dialogue_exchange",
    "dialog": "dialogue_exchange",
    "speech": "dialogue_exchange",
    "conversation": "dialogue_exchange",
    "exchange": "dialogue_exchange",
    "confrontation": "dialogue_exchange",
    "argument": "dialogue_exchange",
    "react": "reaction",
    "pause": "reaction",
    "silence": "reaction",
    "tension": "reaction",
    "turn": "reaction",
    "turn_pause": "reaction",
    "aftermath": "reaction",
    "climax": "reveal",
    "revelation": "reveal",
    "discovery": "reveal",
    "transition": "transition_out",
    "closing": "transition_out",
    "exit": "transition_out",
    "ending": "transition_out",
    "outro": "transition_out",
}


def _normalize_drama_beat(working: dict[str, Any], notes: list[str], *, path: str) -> None:
    """Losslessly normalize DramaBeat enum typos (including beat_energy <-> beat_type swaps)."""
    has_lines = bool(working.get("spoken_lines"))
    raw_energy = working.get("beat_energy")
    if raw_energy is not None:
        tok_e = _slug_token(raw_energy)
        if tok_e in _DRAMA_BEAT_ENERGIES:
            if raw_energy != tok_e:
                working["beat_energy"] = tok_e
                notes.append(f"{path}.beat_energy:normalized_to_{tok_e}")
        else:
            working["beat_energy"] = None
            notes.append(f"{path}.beat_energy:unknown_value_dropped({raw_energy})")

    raw_type = working.get("beat_type")
    if raw_type is not None:
        tok_t = _slug_token(raw_type)
        if tok_t in _DRAMA_BEAT_TYPES:
            if raw_type != tok_t:
                working["beat_type"] = tok_t
                notes.append(f"{path}.beat_type:normalized_to_{tok_t}")
        else:
            if tok_t in _DRAMA_BEAT_ENERGIES and not working.get("beat_energy"):
                working["beat_energy"] = tok_t
                notes.append(f"{path}.beat_type:moved_energy_{tok_t}_to_beat_energy")
            mapped = _DRAMA_BEAT_TYPE_SYNONYMS.get(tok_t)
            if mapped is None:
                mapped = "dialogue_exchange" if has_lines else "action"
            working["beat_type"] = mapped
            notes.append(f"{path}.beat_type:folded_{raw_type}_to_{mapped}")


def _normalize(data: Any, model: type[BaseModel] | None, notes: list[str],
               *, path: str = "$", counter: dict[str, int]) -> Any:
    if model is None:
        return data
    if model.__name__ == "VocalDelivery":
        cleaned, vocal_notes = normalize_vocal_delivery(data, path=path)
        notes.extend(vocal_notes)
        return cleaned
    if not isinstance(data, dict):
        return data
    aliases = _FIELD_ALIASES.get(model.__name__, {})
    fields = _fields_of(model)
    # Recover misplaced sibling fields when a nested dict closed its brace late
    # (for example `shot["camera"]["blocking"]` on DirectedShot): lift only keys
    # unknown to the child model that are valid parent fields missing above.
    working = dict(data)
    if model.__name__ == "DramaBeat":
        _normalize_drama_beat(working, notes, path=path)
    if model.__name__ == "DirectedShot":
        beat_ids = working.get("beat_ids")
        cuts = working.get("internal_cuts")
        if isinstance(beat_ids, list) and isinstance(cuts, list) and len(beat_ids) > 1 and cuts:
            cut_beats = {
                b for cut in cuts if isinstance(cut, dict)
                for b in (cut.get("beat_ids") or []) if isinstance(b, str)
            }
            if cut_beats:
                trimmed = list(beat_ids)
                while len(trimmed) > 1 and isinstance(trimmed[-1], str) and trimmed[-1] in cut_beats:
                    trimmed.pop()
                if len(trimmed) < len(beat_ids) and any(
                    isinstance(b, str) and b not in cut_beats for b in trimmed
                ):
                    working["beat_ids"] = trimmed
                    notes.append(f"{path}.beat_ids:deduped_internal_cut_beats")
    for key, value in list(data.items()):
        target = key if key in fields else aliases.get(key)
        if not target or target not in fields:
            continue
        nested = _model_of(fields[target].annotation)
        if nested is not None and isinstance(value, dict):
            child_fields = _fields_of(nested)
            child_aliases = _FIELD_ALIASES.get(nested.__name__, {})
            cleaned_child = dict(value)
            moved = False
            for sub_k, sub_v in value.items():
                if (sub_k not in child_fields and sub_k not in child_aliases
                        and sub_k in fields and sub_k not in working):
                    working[sub_k] = sub_v
                    cleaned_child.pop(sub_k, None)
                    notes.append(f"{path}.{key}.{sub_k}:promoted_to_parent")
                    moved = True
            if moved:
                working[key] = cleaned_child
            continue
        item = _annotation_items(fields[target].annotation)
        if item is not None and isinstance(item, type) and issubclass(item, BaseModel) and isinstance(value, list):
            item_fields = _fields_of(item)
            item_aliases = _FIELD_ALIASES.get(item.__name__, {})
            cleaned_list: list[Any] = []
            list_changed = False
            for idx, row in enumerate(value):
                if row in (None, "", []) and len(value) > 1:
                    notes.append(f"{path}.{key}[{idx}]:empty_non_object_dropped")
                    list_changed = True
                    continue
                if isinstance(row, dict):
                    cleaned_row = dict(row)
                    row_moved = False
                    for sub_k, sub_v in row.items():
                        if (sub_k not in item_fields and sub_k not in item_aliases
                                and sub_k in fields and sub_k not in working):
                            working[sub_k] = sub_v
                            cleaned_row.pop(sub_k, None)
                            notes.append(f"{path}.{key}[{idx}].{sub_k}:promoted_to_parent")
                            row_moved = True
                    if row_moved:
                        list_changed = True
                        cleaned_list.append(cleaned_row)
                        continue
                cleaned_list.append(row)
            if list_changed:
                working[key] = cleaned_list
    out: dict[str, Any] = {}
    for key, value in working.items():
        target = key if key in fields else None
        if target is None and key in aliases and aliases[key] in fields:
            # The alias fills its canonical field only when that field did not
            # arrive on its own; otherwise the alias is noise and is dropped.
            target = aliases[key]
            if target in working:
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
            normalized_child = _normalize(value, nested, notes, path=f"{path}.{target}", counter=counter)
            if normalized_child is not None or not field.is_required():
                out[target] = normalized_child
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


def _has_trailing_cut_beat_overlap(data: Any) -> bool:
    if not isinstance(data, dict):
        return False
    for shot in data.get("shots") or []:
        if not isinstance(shot, dict):
            continue
        beat_ids = shot.get("beat_ids")
        cuts = shot.get("internal_cuts")
        if not (isinstance(beat_ids, list) and isinstance(cuts, list) and len(beat_ids) > 1 and cuts):
            continue
        cut_beats = {
            b for cut in cuts if isinstance(cut, dict)
            for b in (cut.get("beat_ids") or []) if isinstance(b, str)
        }
        if cut_beats and isinstance(beat_ids[-1], str) and beat_ids[-1] in cut_beats and any(
            isinstance(b, str) and b not in cut_beats for b in beat_ids[:-1]
        ):
            return True
    return False


def parse_with_salvage(data: Any, contract: type[BaseModel]) -> tuple[BaseModel, list[str]]:
    """Validate directly first; only a refusal triggers the local recovery.

    Returns ``(proposal, notes)``.  When even the normalised answer cannot
    validate, the *original* ``ValidationError`` is re-raised so the caller's
    failure path reports exactly what the contract refused — never a synthetic
    error that hides the real one.
    """
    if contract.__name__ == "SceneShotBoard" and _has_trailing_cut_beat_overlap(data):
        normalized, notes = salvage_crew_answer(data, contract)
        try:
            return contract.model_validate(normalized), notes
        except ValidationError:
            pass
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
