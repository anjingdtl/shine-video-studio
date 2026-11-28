"""Acting director pass (Plan 20261127, Phase 2 — Scheme B).

Elevates a continuity-approved ``SceneShotBoard`` (or a single ``ShotSpec``)
with anatomical micro-expression performance ramps, adaptive pre-speech pauses,
four-dimensional controlled vocal delivery cues, and cross-group emotional
carry-over across multi-shot dramatic exchanges.

Architectural guarantees (``AGENTS.md`` three principles):
* **One truth**: writes patches back into the same ``SceneShotBoard`` /
  ``ShotSpec`` (``timeline`` + ``dialogue[].delivery`` + safe pre-speech timing)
  and never creates a parallel acting ledger or second prompt copy.
* **One verdict**: reuses :mod:`shine.agents.continuity_engine`
  (``detect_bare_emotion_words`` and ``review_scene_board``); never adds a
  parallel blocking gate.
* **Non-blocking**: parses per shot with ``crew_salvage.normalize_vocal_delivery``,
  retries a broken or bare-emotion shot at most once individually, and falls
  back cleanly to the original ``scene_director`` shot with an
  ``ACTING_POLISH_FALLBACK`` warning if the retry still fails.
"""

from __future__ import annotations

import copy
import json
import re
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from pydantic import Field, ValidationError

from shine.agents import continuity_engine as engine
from shine.agents.crew_salvage import normalize_vocal_delivery
from shine.schemas import APIModel, VocalDelivery

_CJK_RE = re.compile(r"[\u3400-\u9fff]")
_CHAR_TOKEN_RE = re.compile(r"\{\{C\d+\}\}")
_WIDE_SHOT_RE = re.compile(
    r"\b(?:ews|ws|ls|els|fs|wide|long|extreme[\s_-]*wide|extreme[\s_-]*long|establishing|full[\s_-]*shot)\b",
    flags=re.IGNORECASE,
)
_CLOSE_SHOT_RE = re.compile(
    r"\b(?:ecu|xcu|cu|mcu|close|close[\s_-]*up|medium[\s_-]*close|tight|choker|portrait)\b",
    flags=re.IGNORECASE,
)
_INTENSE_ENERGIES = frozenset({"turn_pause", "erupt", "aftermath"})
_BREAK_MOTIVATIONS = frozenset({"time_or_space_jump", "establishing_reset"})
_MIN_PRE_SPEECH_WINDOW_S = 0.25
_SAFETY_TAIL_S = 0.15


# ---------------------------------------------------------------------------
# Minimal incremental ActingPatch contracts (LLM never echoes dialogue text
# or structural camera/blocking/asset fields)
# ---------------------------------------------------------------------------


class ActingWindowPatch(APIModel):
    start_s: float = Field(ge=0, allow_inf_nan=False)
    end_s: float = Field(gt=0, allow_inf_nan=False)
    action: str = Field(min_length=1, max_length=400)
    expression: str = Field(min_length=1, max_length=400)
    camera_beat: str = Field(default="", max_length=200)


class ActingDialoguePatch(APIModel):
    line_index: int = Field(ge=0)
    delivery: VocalDelivery


class ActingShotPatch(APIModel):
    shot_id: str = Field(min_length=1, max_length=80)
    windows: list[ActingWindowPatch] = Field(min_length=1, max_length=6)
    deliveries: list[ActingDialoguePatch] = Field(default_factory=list, max_length=30)
    exit_acting_state: str = Field(default="", max_length=200)


class ActingGroupPatch(APIModel):
    shots: list[ActingShotPatch] = Field(min_length=1, max_length=8)


@dataclass
class PreSpeechPlan:
    """Server-owned deterministic pre-speech pause & timeline window plan."""

    target_pause_s: float
    applied_pause_s: float
    shift_s: float
    exempt_reason: str | None
    adjusted_dialogue: list[dict[str, Any]]
    window_slots: list[dict[str, Any]] = field(default_factory=list)


# ---------------------------------------------------------------------------
# 1. Zero-token deterministic performance segment grouper (2–4 shots/group)
# ---------------------------------------------------------------------------


def _beat_energy_lookup(scene: dict[str, Any] | None) -> dict[str, str]:
    if not isinstance(scene, dict):
        return {}
    out: dict[str, str] = {}
    for pos, beat in enumerate(scene.get("beats") or [], start=1):
        if isinstance(beat, dict):
            energy = str(beat.get("beat_energy") or "").strip().lower()
            if energy:
                out[f"B{pos}"] = energy
    return out


def shot_beat_energies(shot: dict[str, Any], scene: dict[str, Any] | None) -> list[str]:
    lookup = _beat_energy_lookup(scene)
    if not lookup:
        return []
    ids: list[str] = [str(bid) for bid in (shot.get("beat_ids") or [])]
    for cut in shot.get("internal_cuts") or []:
        if isinstance(cut, dict):
            ids.extend(str(bid) for bid in (cut.get("beat_ids") or []))
    return [lookup[bid] for bid in ids if bid in lookup]


def _shot_axis(shot: dict[str, Any]) -> str:
    blocking = shot.get("blocking")
    if isinstance(blocking, dict):
        return str(blocking.get("axis") or "").strip()
    return ""


def _split_long_run(
    run: list[int],
    shots: list[dict[str, Any]],
    scene: dict[str, Any] | None,
    *,
    max_group_size: int = 4,
) -> list[list[int]]:
    """Split a contiguous same-setup run > ``max_group_size`` into 2–4 shot groups.

    Prefers splitting at ``beat_energy`` step transitions (e.g. ``pressure ->
    turn_pause`` or ``erupt -> aftermath``) while avoiding orphan 1-shot tails.
    """
    n = len(run)
    if n <= max_group_size:
        return [list(run)]
    energies_per_pos = [shot_beat_energies(shots[idx], scene) for idx in run]
    primary = [vals[0] if vals else "" for vals in energies_per_pos]

    groups: list[list[int]] = []
    start = 0
    while start < n:
        remaining = n - start
        if remaining <= max_group_size:
            groups.append(run[start:])
            break
        # Legal split sizes keep every group in [2, max_group_size] and avoid a 1-shot tail.
        valid_sizes = [
            size
            for size in range(2, min(max_group_size, remaining) + 1)
            if (remaining - size) == 0 or (remaining - size) >= 2
        ]
        if not valid_sizes:
            valid_sizes = [min(max_group_size, remaining)]
        # Prefer a beat_energy step boundary (especially into turn_pause / erupt / aftermath).
        chosen: int | None = None
        best_score = -1
        for size in valid_sizes:
            next_pos = start + size
            if next_pos >= n:
                continue
            prev_e = primary[next_pos - 1]
            next_e = primary[next_pos]
            score = 0
            if prev_e and next_e and prev_e != next_e:
                score = 2 if next_e in _INTENSE_ENERGIES else 1
            if score > best_score:
                best_score = score
                chosen = size
        if best_score <= 0 or chosen is None:
            # Balanced default (e.g. 5 -> 3+2, 6 -> 3+3, 7 -> 4+3, 8 -> 4+4, 9 -> 3+3+3).
            if remaining == 5:
                chosen = 3
            elif remaining == 6:
                chosen = 3
            elif remaining == 9:
                chosen = 3
            else:
                chosen = max(valid_sizes)
        groups.append(run[start : start + chosen])
        start += chosen
    return groups


def group_performance_segments(
    shots: list[dict[str, Any]],
    scene: dict[str, Any] | None = None,
    *,
    max_group_size: int = 4,
) -> list[list[int]]:
    """Group a scene's shots into continuous 2–4 shot acting performance segments.

    Breaks on ``time_or_space_jump`` / ``establishing_reset``, ``location``
    changes, or ``blocking.axis`` changes; subdivides longer same-setup runs at
    ``beat_energy`` transitions so every group stays <= ``max_group_size``.
    A standalone single-shot scene returns ``[[0]]``.
    """
    if not shots:
        return []
    runs: list[list[int]] = []
    current: list[int] = [0]
    for idx in range(1, len(shots)):
        prev = shots[idx - 1]
        cur = shots[idx]
        motivation = str(cur.get("transition_motivation") or "").strip().lower()
        prev_loc = str(prev.get("location") or prev.get("location_id") or "").strip()
        cur_loc = str(cur.get("location") or cur.get("location_id") or "").strip()
        prev_axis = _shot_axis(prev)
        cur_axis = _shot_axis(cur)
        hard_break = (
            motivation in _BREAK_MOTIVATIONS
            or (bool(prev_loc) and bool(cur_loc) and prev_loc != cur_loc)
            or (bool(prev_axis) and bool(cur_axis) and prev_axis != cur_axis)
        )
        if hard_break:
            runs.append(current)
            current = [idx]
        else:
            current.append(idx)
    runs.append(current)

    groups: list[list[int]] = []
    for run in runs:
        groups.extend(_split_long_run(run, shots, scene, max_group_size=max_group_size))
    return groups


# ---------------------------------------------------------------------------
# 2. Zero-token pre-speech pause budget & safe dialogue time-shift calculator
# ---------------------------------------------------------------------------


def _first_cut_start(shot: dict[str, Any], duration_s: float) -> float:
    cuts = [
        float(c["start_s"])
        for c in (shot.get("internal_cuts") or [])
        if isinstance(c, dict) and c.get("start_s") is not None
    ]
    valid = sorted(t for t in cuts if 0.0 < t < duration_s)
    return valid[0] if valid else duration_s


def _build_window_slots(
    duration_s: float,
    sub_end: float,
    applied_pause_s: float,
    opening_lines: list[dict[str, Any]],
    existing_timeline: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Construct strictly non-overlapping ``[0.0, duration_s]`` slots for the LLM."""
    slots: list[dict[str, Any]] = []
    if opening_lines and applied_pause_s >= _MIN_PRE_SPEECH_WINDOW_S:
        pause_end = round(applied_pause_s, 3)
        slots.append({"start_s": 0.0, "end_s": pause_end, "role": "pre_speech"})
        last_line_end = max(float(line["end_s"]) for line in opening_lines)
        delivery_end = round(min(sub_end, max(pause_end + 0.5, last_line_end + 0.3)), 3)
        if sub_end - delivery_end >= 0.6:
            slots.append({"start_s": pause_end, "end_s": delivery_end, "role": "delivery"})
            slots.append({"start_s": delivery_end, "end_s": round(sub_end, 3), "role": "aftermath"})
        else:
            slots.append({"start_s": pause_end, "end_s": round(sub_end, 3), "role": "delivery"})
        if sub_end < duration_s - 0.05:
            slots.append({"start_s": round(sub_end, 3), "end_s": round(duration_s, 3), "role": "post_cut"})
        return slots

    # Preserve existing valid timeline boundaries when no pre-speech ramp is inserted.
    cleaned: list[dict[str, Any]] = []
    prev_end = 0.0
    for idx, win in enumerate(existing_timeline or []):
        if not isinstance(win, dict):
            continue
        s = round(max(prev_end, float(win.get("start_s") or 0.0)), 3)
        e = round(min(duration_s, float(win.get("end_s") or 0.0)), 3)
        if e > s + 0.05:
            role = "delivery" if opening_lines and idx == 0 else "action"
            cleaned.append({"start_s": s, "end_s": e, "role": role})
            prev_end = e
    if cleaned:
        return cleaned[:6]

    # Fallback: clean 2-window split.
    if sub_end < duration_s - 0.05:
        return [
            {"start_s": 0.0, "end_s": round(sub_end, 3), "role": "action"},
            {"start_s": round(sub_end, 3), "end_s": round(duration_s, 3), "role": "post_cut"},
        ]
    mid = round(duration_s * 0.45, 3)
    return [
        {"start_s": 0.0, "end_s": mid, "role": "action"},
        {"start_s": mid, "end_s": round(duration_s, 3), "role": "aftermath"},
    ]


def compute_pre_speech_budget(
    shot: dict[str, Any],
    beat_energies: list[str] | None = None,
) -> PreSpeechPlan:
    """Deterministically compute adaptive pre-speech pause and safe dialogue shift.

    Never lets the LLM perform timecode arithmetic and never produces a shift
    that violates ``sub_shot.end_s - 0.15`` or collides with ``internal_cuts``.
    """
    duration_s = float(shot.get("duration_s") or shot.get("duration_target_s") or 6.0)
    sub_end = _first_cut_start(shot, duration_s)
    raw_dialogue = [dict(line) for line in (shot.get("dialogue") or []) if isinstance(line, dict)]
    existing_timeline = [dict(w) for w in (shot.get("timeline") or []) if isinstance(w, dict)]
    energies = {str(e).strip().lower() for e in (beat_energies or []) if str(e).strip()}

    if not raw_dialogue:
        slots = _build_window_slots(duration_s, sub_end, 0.0, [], existing_timeline)
        return PreSpeechPlan(0.0, 0.0, 0.0, "silent_shot", [], slots)

    motivation = str(shot.get("transition_motivation") or "").strip().lower()
    camera = shot.get("camera") or {}
    shot_size_text = " ".join(
        str(camera.get(k) or "") for k in ("shot_size", "composition")
    ).strip()

    opening_indices = [
        idx for idx, line in enumerate(raw_dialogue) if float(line.get("start_s") or 0.0) < sub_end - 1e-6
    ]
    if not opening_indices:
        slots = _build_window_slots(duration_s, sub_end, 0.0, [], existing_timeline)
        return PreSpeechPlan(0.0, 0.0, 0.0, "post_cut_only_dialogue", raw_dialogue, slots)

    if motivation == "continuous_action":
        slots = _build_window_slots(
            duration_s, sub_end, 0.0, [raw_dialogue[i] for i in opening_indices], existing_timeline
        )
        return PreSpeechPlan(0.0, 0.0, 0.0, "continuous_action", raw_dialogue, slots)

    if _WIDE_SHOT_RE.search(shot_size_text) and not _CLOSE_SHOT_RE.search(shot_size_text):
        slots = _build_window_slots(
            duration_s, sub_end, 0.0, [raw_dialogue[i] for i in opening_indices], existing_timeline
        )
        return PreSpeechPlan(0.0, 0.0, 0.0, "wide_shot", raw_dialogue, slots)

    is_close_or_intense = bool(_CLOSE_SHOT_RE.search(shot_size_text) or (energies & _INTENSE_ENERGIES))
    target_pause_s = 1.0 if is_close_or_intense else 0.4

    opening_lines = [raw_dialogue[i] for i in opening_indices]
    first_start = min(float(line["start_s"]) for line in opening_lines)
    last_end = max(float(line["end_s"]) for line in opening_lines)

    if first_start >= target_pause_s - 1e-6:
        applied_pause_s = round(first_start, 3)
        slots = _build_window_slots(duration_s, sub_end, applied_pause_s, opening_lines, existing_timeline)
        return PreSpeechPlan(target_pause_s, applied_pause_s, 0.0, None, raw_dialogue, slots)

    needed = target_pause_s - first_start
    available_headroom = max(0.0, (sub_end - _SAFETY_TAIL_S) - last_end)
    shift_s = round(min(needed, available_headroom), 3)

    adjusted = [dict(line) for line in raw_dialogue]
    if shift_s > 0.01:
        for idx in opening_indices:
            adjusted[idx]["start_s"] = round(float(adjusted[idx]["start_s"]) + shift_s, 3)
            adjusted[idx]["end_s"] = round(float(adjusted[idx]["end_s"]) + shift_s, 3)
    else:
        shift_s = 0.0

    adjusted_opening = [adjusted[i] for i in opening_indices]
    applied_pause_s = round(min(float(line["start_s"]) for line in adjusted_opening), 3)
    exempt_reason = "no_headroom" if applied_pause_s < _MIN_PRE_SPEECH_WINDOW_S else None
    slots = _build_window_slots(duration_s, sub_end, applied_pause_s, adjusted_opening, existing_timeline)
    return PreSpeechPlan(
        target_pause_s=target_pause_s,
        applied_pause_s=applied_pause_s if exempt_reason is None else 0.0,
        shift_s=shift_s,
        exempt_reason=exempt_reason,
        adjusted_dialogue=adjusted,
        window_slots=slots,
    )


# ---------------------------------------------------------------------------
# 3. Acting Director prompt & compact payload builder
# ---------------------------------------------------------------------------


def acting_director_system_prompt() -> str:
    """Compact system prompt for anatomical facial acting + 4D vocal delivery."""
    return (
        "You are Shine's acting director for MiniMax-H3 Ref2VA film production. "
        "You receive a continuous 2-4 shot performance group with server-computed window_slots and "
        "indexed dialogue_lines. Return ONLY a compact ActingGroupPatch JSON object (no Markdown, "
        "no commentary). Never echo dialogue text, speaker_id, camera, blocking, internal_cuts, or asset_ids.\n"
        "HARD RULES:\n"
        "1. ANATOMICAL FACIAL DISSECTION ONLY: NEVER use bare abstract emotion words (angry, furious, "
        "sad, depressed, shocked, surprised, fearful, scared, happy, nervous, anxious, worried, upset, "
        "frustrated, disappointed, guilty, confused, relieved) on their own. Always describe concrete "
        "observable facial anatomy and breath progression across: upper face (inner/outer brows, forehead "
        "furrow), eyes (upper/lower eyelids, pupil/gaze shift, blink/unbroken stare), mid-face (nostrils, "
        "cheekbones), lower face (lip line, mouth corners, jaw clench, chin, throat swallow, inhale/exhale).\n"
        "2. PRE-SPEECH MICRO-EXPRESSION RAMP: When a window_slot has role='pre_speech' (0.8-1.2s for "
        "close-ups/emotional turns or 0.3-0.5s for tight cuts), write a 2-3 step anatomical build-up "
        "leading directly into the lips parting for the line; when role='aftermath', describe the "
        "post-syllable physical resonance (swallow, jaw release, gaze hold).\n"
        "3. CONTROLLED 4D VOCAL DELIVERY: For every line in dialogue_lines, emit an entry in deliveries "
        "keyed ONLY by line_index with controlled English values:\n"
        "   - volume: whispered | hushed | soft | conversational | raised | shouting\n"
        "   - pace: halting | slow | measured | brisk | rushed\n"
        "   - voice_quality: breathy | hoarse | trembling | crisp | strained | warm | flat\n"
        "   - pitch: low_flat | steady | rising | falling | cracking\n"
        "   Vary vocal delivery across beats to match emotional shifts (never make every line flat conversational).\n"
        "4. CROSS-GROUP EMOTIONAL CARRY-OVER: Honour previous_group_carry_over_state at the opening of "
        "shot 1 of this group, build a coherent emotional arc across all shots in this group, and record "
        "the ending facial tension + vocal register in exit_acting_state (<=180 chars) on each shot.\n"
        "5. STRICT LANGUAGE & CAST SAFETY: Every action, expression and exit_acting_state MUST be pure "
        "English (zero Chinese characters). Refer to characters ONLY by the exact {{Ck}} tokens listed in "
        "that shot's allowed_tokens; NEVER mention an off-cast {{Ck}} token not in allowed_tokens.\n"
        "6. EXACT WINDOW COUNT: Emit one window entry per server window_slot in exact order, copying "
        "start_s and end_s verbatim. Keep each action/expression crisp (15-45 words).\n"
        "7. SINGLE-EXECUTION ACTION & SPATIAL CONTINUITY: Never repeat a one-shot prop or movement beat "
        "(such as taking out/placing a key or crossing a room) across multiple windows of the same shot — "
        "perform each discrete gesture once in one window and advance forward in later windows (reaction, "
        "stillness, gaze hold). Preserve each character's physical side of fixed set landmarks (e.g. behind "
        "vs in front of the counter) and never materialize a hidden prop without a visible reach/draw motion.\n"
        "8. OPTIONAL CAMERA-BEAT SYNC: When a shot has a dynamic camera_move (not 'static'), you may set "
        "an optional pure-English camera_beat (<=180 chars) on each window to synchronize camera acceleration, "
        "push/track/pan, settle/hold, or pull-back with pre_speech / delivery / aftermath windows (e.g. slow "
        "push-in through pre_speech -> settle and hold steady on eyes during delivery -> gentle breathing hold "
        "or slow pull-back on aftermath). Leave camera_beat empty on static shots; never mention off-cast tokens."
    )


def _allowed_tokens_for_shot(shot: dict[str, Any], token_map: dict[str, str]) -> dict[str, str]:
    placed = {
        str(pl.get("character") or "").strip()
        for pl in ((shot.get("blocking") or {}).get("placements") or [])
        if isinstance(pl, dict) and str(pl.get("character") or "").strip()
    }
    speakers = {
        str(line.get("speaker_id") or "").strip()
        for line in (shot.get("dialogue") or [])
        if isinstance(line, dict) and str(line.get("speaker_id") or "").strip()
    }
    visible = placed | speakers | {str(c).strip() for c in (shot.get("characters") or []) if str(c).strip()}
    if not token_map:
        return {}
    return {tok: name for tok, name in token_map.items() if name in visible}


def build_acting_group_payload(
    group_shots: list[tuple[dict[str, Any], PreSpeechPlan, list[str]]],
    *,
    token_map: dict[str, str],
    carry_over_state: str = "",
    retry_feedback: str = "",
) -> dict[str, Any]:
    rows: list[dict[str, Any]] = []
    for shot, plan, energies in group_shots:
        camera = shot.get("camera") or {}
        allowed = _allowed_tokens_for_shot(shot, token_map)
        rows.append({
            "shot_id": str(shot.get("id") or "S1"),
            "duration_s": float(shot.get("duration_s") or shot.get("duration_target_s") or 6.0),
            "shot_size": str(camera.get("shot_size") or ""),
            "camera_move": str(camera.get("move") or "static"),
            "camera_movement": str(camera.get("movement") or "")[:160],
            "visual_summary": str(shot.get("visual_description") or shot.get("story_beat") or "")[:300],
            "entry_state": str(shot.get("entry_state") or "")[:200],
            "exit_state": str(shot.get("exit_state") or "")[:200],
            "beat_energies": list(energies),
            "allowed_tokens": allowed,
            "window_slots": list(plan.window_slots),
            "dialogue_lines": [
                {
                    "line_index": idx,
                    "speaker_id": str(line.get("speaker_id") or ""),
                    "start_s": float(line.get("start_s") or 0.0),
                    "end_s": float(line.get("end_s") or 0.0),
                    "text": str(line.get("text") or ""),
                }
                for idx, line in enumerate(plan.adjusted_dialogue)
            ],
        })
    payload: dict[str, Any] = {
        "previous_group_carry_over_state": carry_over_state,
        "shots": rows,
    }
    if retry_feedback:
        payload["retry_feedback"] = retry_feedback
    return payload


# ---------------------------------------------------------------------------
# 4. Tolerant per-shot JSON salvage, validation, and safe merging
# ---------------------------------------------------------------------------


def _extract_json_payload(raw: Any) -> dict[str, Any] | None:
    if isinstance(raw, dict):
        return raw
    if not isinstance(raw, str):
        return None
    text = raw.strip()
    if not text:
        return None
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*", "", text, flags=re.IGNORECASE)
        text = re.sub(r"\s*```$", "", text)
    try:
        parsed = json.loads(text)
        return parsed if isinstance(parsed, dict) else None
    except json.JSONDecodeError:
        pass
    start = text.find("{")
    end = text.rfind("}")
    if start >= 0 and end > start:
        snippet = re.sub(r",\s*([}\]])", r"\1", text[start : end + 1])
        try:
            parsed = json.loads(snippet)
            return parsed if isinstance(parsed, dict) else None
        except json.JSONDecodeError:
            return None
    return None


def _salvage_one_shot_patch(raw_item: Any, *, idx: int) -> tuple[ActingShotPatch | None, str | None, list[str]]:
    notes: list[str] = []
    if not isinstance(raw_item, dict):
        return None, "shot_patch_not_object", notes
    shot_id = str(raw_item.get("shot_id") or raw_item.get("id") or "").strip()
    if not shot_id:
        return None, "missing_shot_id", notes

    raw_windows = raw_item.get("windows") or raw_item.get("timeline") or []
    if not isinstance(raw_windows, list) or not raw_windows:
        return None, "missing_windows", notes

    clean_windows: list[dict[str, Any]] = []
    for w_idx, w in enumerate(raw_windows[:6]):
        if not isinstance(w, dict):
            notes.append(f"{shot_id}.windows[{w_idx}]:non_object_dropped")
            continue
        try:
            s = float(w.get("start_s", w.get("start", 0.0)))
            e = float(w.get("end_s", w.get("end", 0.0)))
        except (TypeError, ValueError):
            notes.append(f"{shot_id}.windows[{w_idx}]:invalid_timecode")
            continue
        action = str(w.get("action") or "").strip()[:400]
        expression = str(w.get("expression") or "").strip()[:400]
        camera_beat = str(w.get("camera_beat") or "").strip()[:200]
        if not action or not expression or e <= s:
            notes.append(f"{shot_id}.windows[{w_idx}]:incomplete_window_dropped")
            continue
        clean_windows.append({
            "start_s": s,
            "end_s": e,
            "action": action,
            "expression": expression,
            **({"camera_beat": camera_beat} if camera_beat else {}),
        })

    if not clean_windows:
        return None, "no_valid_windows", notes

    clean_deliveries: list[dict[str, Any]] = []
    raw_deliveries = raw_item.get("deliveries") or []
    if isinstance(raw_deliveries, list):
        for d_idx, d in enumerate(raw_deliveries):
            if not isinstance(d, dict):
                notes.append(f"{shot_id}.deliveries[{d_idx}]:non_object_dropped")
                continue
            raw_li = d.get("line_index", d.get("index"))
            try:
                li = int(raw_li)
            except (TypeError, ValueError):
                notes.append(f"{shot_id}.deliveries[{d_idx}]:invalid_line_index")
                continue
            if li < 0:
                continue
            raw_del = d.get("delivery") if "delivery" in d else {
                k: v for k, v in d.items() if k not in {"line_index", "index"}
            }
            norm_del, d_notes = normalize_vocal_delivery(raw_del, path=f"{shot_id}.deliveries[{d_idx}]")
            notes.extend(d_notes)
            if norm_del:
                clean_deliveries.append({"line_index": li, "delivery": norm_del})

    exit_state = str(raw_item.get("exit_acting_state") or "").strip()[:200]
    try:
        patch = ActingShotPatch.model_validate({
            "shot_id": shot_id,
            "windows": clean_windows,
            "deliveries": clean_deliveries,
            "exit_acting_state": exit_state,
        })
        return patch, None, notes
    except ValidationError as exc:
        return None, f"schema_invalid:{exc.error_count()}", notes


def parse_acting_group_patch(
    raw: Any,
    expected_shot_ids: list[str],
) -> tuple[dict[str, ActingShotPatch], dict[str, str], list[str]]:
    """Parse an ``ActingGroupPatch`` per shot so one bad shot never ruins its group."""
    notes: list[str] = []
    payload = _extract_json_payload(raw)
    if payload is None:
        return {}, {sid: "invalid_json" for sid in expected_shot_ids}, ["group:invalid_json"]

    raw_shots = payload.get("shots")
    if not isinstance(raw_shots, list):
        if "shot_id" in payload and "windows" in payload:
            raw_shots = [payload]
        else:
            return {}, {sid: "missing_shots_list" for sid in expected_shot_ids}, ["group:missing_shots_list"]

    valid: dict[str, ActingShotPatch] = {}
    errors: dict[str, str] = {}
    expected_set = set(expected_shot_ids)
    for idx, item in enumerate(raw_shots):
        patch, err, item_notes = _salvage_one_shot_patch(item, idx=idx)
        notes.extend(item_notes)
        if patch is not None and patch.shot_id in expected_set:
            valid[patch.shot_id] = patch
        elif isinstance(item, dict):
            sid = str(item.get("shot_id") or item.get("id") or "").strip()
            if sid in expected_set and err:
                errors[sid] = err
    for sid in expected_shot_ids:
        if sid not in valid and sid not in errors:
            errors[sid] = "missing_in_response"
    return valid, errors, notes


def validate_shot_patch(
    patch: ActingShotPatch,
    shot: dict[str, Any],
    *,
    token_map: dict[str, str],
) -> str | None:
    """Validate language, cast token safety, and bare abstract emotion words."""
    allowed_tokens = set(_allowed_tokens_for_shot(shot, token_map))
    for w_idx, win in enumerate(patch.windows):
        for field_name, text in (("action", win.action), ("expression", win.expression)):
            if _CJK_RE.search(text):
                return f"cjk_in_{field_name}_window_{w_idx}"
            if token_map:
                used = set(_CHAR_TOKEN_RE.findall(text))
                disallowed = sorted(used - allowed_tokens)
                if disallowed:
                    return f"off_cast_token:{','.join(disallowed)}"
        bare = engine.detect_bare_emotion_words(win.expression)
        if bare:
            return f"bare_emotion_words:{','.join(bare)}"
    if patch.exit_acting_state and _CJK_RE.search(patch.exit_acting_state):
        return "cjk_in_exit_acting_state"
    return None


def _derive_carry_over(patch: ActingShotPatch) -> str:
    if patch.exit_acting_state.strip():
        return patch.exit_acting_state.strip()[:200]
    parts: list[str] = []
    if patch.windows:
        parts.append(patch.windows[-1].expression[:120])
    if patch.deliveries:
        last_d = patch.deliveries[-1].delivery.model_dump(mode="json")
        if last_d:
            parts.append("vocal=" + "-".join(str(v) for v in last_d.values()))
    return "; ".join(parts)[:200]


def _valid_camera_beat(
    text: str,
    *,
    allowed_tokens: set[str],
    has_token_map: bool,
) -> bool:
    cleaned = text.strip()
    if not cleaned:
        return False
    if _CJK_RE.search(cleaned):
        return False
    if has_token_map:
        used = set(_CHAR_TOKEN_RE.findall(cleaned))
        if used - allowed_tokens:
            return False
    return True


def apply_shot_patch(
    shot: dict[str, Any],
    patch: ActingShotPatch,
    plan: PreSpeechPlan,
    *,
    token_map: dict[str, str] | None = None,
) -> dict[str, Any]:
    """Merge a validated ``ActingShotPatch`` + server ``PreSpeechPlan`` onto one shot."""
    updated = copy.deepcopy(shot)
    existing_timeline = [w for w in (shot.get("timeline") or []) if isinstance(w, dict)]
    default_camera = str((shot.get("camera") or {}).get("movement") or "The camera holds still.").strip()
    allowed_tokens = set(_allowed_tokens_for_shot(shot, token_map or {}))
    has_token_map = bool(token_map)

    # Bind windows strictly to the server-computed window_slots so LLM timecode drift
    # can never violate ShotSpec's non-overlapping timeline validator.
    merged_timeline: list[dict[str, Any]] = []
    slots = plan.window_slots
    for idx, slot in enumerate(slots):
        patch_win = patch.windows[idx] if idx < len(patch.windows) else patch.windows[-1]
        fallback_cam = (
            str(existing_timeline[idx].get("camera") or "").strip()
            if idx < len(existing_timeline)
            else default_camera
        ) or default_camera
        candidate_cam = str(patch_win.camera_beat or "").strip()
        cam_text = (
            candidate_cam
            if _valid_camera_beat(candidate_cam, allowed_tokens=allowed_tokens, has_token_map=has_token_map)
            else fallback_cam
        )
        merged_timeline.append({
            "start_s": float(slot["start_s"]),
            "end_s": float(slot["end_s"]),
            "action": patch_win.action.strip(),
            "expression": patch_win.expression.strip(),
            "camera": cam_text,
        })
    updated["timeline"] = merged_timeline

    # Apply server-shifted dialogue timestamps + attach normalized VocalDelivery by line_index.
    delivery_by_index: dict[int, VocalDelivery] = {
        entry.line_index: entry.delivery
        for entry in patch.deliveries
        if not entry.delivery.is_empty()
    }
    merged_dialogue: list[dict[str, Any]] = []
    for idx, line in enumerate(plan.adjusted_dialogue):
        row = dict(line)
        delivery = delivery_by_index.get(idx)
        if delivery is not None:
            dumped = delivery.model_dump(mode="json")
            if dumped:
                row["delivery"] = dumped
        merged_dialogue.append(row)
    updated["dialogue"] = merged_dialogue
    return updated


# ---------------------------------------------------------------------------
# 5. Main non-blocking orchestration entrypoints
# ---------------------------------------------------------------------------


LLMCaller = Callable[[str, dict[str, Any], str], Any]


def polish_scene_board_acting(
    board: dict[str, Any],
    scene: dict[str, Any],
    context: dict[str, Any],
    *,
    call_llm: LLMCaller,
    max_group_size: int = 4,
) -> tuple[dict[str, Any], list[dict[str, Any]], dict[str, Any]]:
    """Run Scheme B acting polish across a continuity-approved ``SceneShotBoard``.

    * Groups shots into 2–4 shot performance segments via ``group_performance_segments``.
    * Computes deterministic pre-speech budgets via ``compute_pre_speech_budget``.
    * Calls ``call_llm(system_prompt, user_payload, label)`` once per group, carrying
      ``carry_over_state`` forward across groups.
    * Retries any broken / timed-out / bare-emotion shot at most once individually.
    * Verifies merged shots against ``continuity_engine.review_scene_board`` and rolls
      back any individual shot that would cause a refusal, logging
      ``ACTING_POLISH_FALLBACK`` without ever blocking the scene.
    """
    polished = copy.deepcopy(board)
    shots = polished.get("shots") or []
    if not shots:
        return polished, [], {"groups": 0, "polished_shots": 0, "fallback_shots": 0}

    token_map = engine._character_token_map(
        context.get("character_tokens"),
        board.get("character_tokens"),
    )
    groups = group_performance_segments(shots, scene, max_group_size=max_group_size)
    system_prompt = acting_director_system_prompt()
    warnings: list[dict[str, Any]] = []
    salvage_notes_all: list[str] = []
    polished_ids: list[str] = []
    fallback_ids: list[str] = []
    carry_over = ""

    for g_idx, group_indices in enumerate(groups):
        group_entries: list[tuple[int, dict[str, Any], PreSpeechPlan, list[str]]] = []
        for s_idx in group_indices:
            shot = shots[s_idx]
            energies = shot_beat_energies(shot, scene)
            plan = compute_pre_speech_budget(shot, energies)
            group_entries.append((s_idx, shot, plan, energies))

        expected_ids = [str(item[1].get("id") or f"S{item[0] + 1}") for item in group_entries]
        user_payload = build_acting_group_payload(
            [(s, p, e) for _i, s, p, e in group_entries],
            token_map=token_map,
            carry_over_state=carry_over,
        )

        raw_answer: Any = None
        group_call_error: str | None = None
        try:
            raw_answer = call_llm(system_prompt, user_payload, f"acting_group_{g_idx + 1}")
        except Exception as exc:  # noqa: BLE001 - non-blocking by design
            group_call_error = f"{type(exc).__name__}:{str(exc)[:120]}"

        if group_call_error is not None:
            parsed_patches: dict[str, ActingShotPatch] = {}
            parse_errors: dict[str, str] = {sid: group_call_error for sid in expected_ids}
        else:
            parsed_patches, parse_errors, g_notes = parse_acting_group_patch(raw_answer, expected_ids)
            salvage_notes_all.extend(g_notes)

        accepted_group_patches: dict[str, ActingShotPatch] = {}
        for s_idx, shot, plan, energies in group_entries:
            sid = str(shot.get("id") or f"S{s_idx + 1}")
            patch = parsed_patches.get(sid)
            issue = parse_errors.get(sid)
            if patch is not None and issue is None:
                issue = validate_shot_patch(patch, shot, token_map=token_map)
                if issue is not None:
                    patch = None

            # At most ONE targeted single-shot retry for broken/bare-emotion/timed-out shot.
            if patch is None:
                feedback = (
                    f"Previous attempt for shot {sid} failed ({issue}). "
                    "Return valid ActingGroupPatch JSON for ONLY this 1 shot with concrete facial "
                    "anatomy (brow/eyelid/gaze/nostril/lip/jaw/breath), zero bare emotion words, "
                    "zero Chinese in action/expression, and exact allowed_tokens."
                )
                retry_payload = build_acting_group_payload(
                    [(shot, plan, energies)],
                    token_map=token_map,
                    carry_over_state=carry_over,
                    retry_feedback=feedback,
                )
                retry_issue = issue or "unknown_error"
                try:
                    retry_raw = call_llm(system_prompt, retry_payload, f"acting_retry_{sid}")
                    r_patches, r_errors, r_notes = parse_acting_group_patch(retry_raw, [sid])
                    salvage_notes_all.extend(r_notes)
                    candidate = r_patches.get(sid)
                    if candidate is not None:
                        val_err = validate_shot_patch(candidate, shot, token_map=token_map)
                        if val_err is None:
                            patch = candidate
                        else:
                            retry_issue = val_err
                    else:
                        retry_issue = r_errors.get(sid) or retry_issue
                except Exception as exc:  # noqa: BLE001 - non-blocking fallback
                    retry_issue = f"{type(exc).__name__}:{str(exc)[:120]}"

                if patch is None:
                    fallback_ids.append(sid)
                    warnings.append({
                        "code": "ACTING_POLISH_FALLBACK",
                        "shot_id": sid,
                        "reason": retry_issue,
                    })
                    continue

            # Apply patch to a candidate copy and verify zero continuity_engine regressions.
            candidate_shot = apply_shot_patch(shot, patch, plan, token_map=token_map)
            prev_shot_backup = polished["shots"][s_idx]
            polished["shots"][s_idx] = candidate_shot
            verdict = engine.review_scene_board(polished, scene, context)
            if not verdict.accepted:
                polished["shots"][s_idx] = prev_shot_backup
                fallback_ids.append(sid)
                warnings.append({
                    "code": "ACTING_POLISH_FALLBACK",
                    "shot_id": sid,
                    "reason": f"continuity_guard:{verdict.first_code()}",
                })
                continue

            accepted_group_patches[sid] = patch
            polished_ids.append(sid)

        # Carry forward the final accepted shot's acting state into the next group.
        for s_idx, shot, _plan, _energies in reversed(group_entries):
            sid = str(shot.get("id") or f"S{s_idx + 1}")
            if sid in accepted_group_patches:
                carry_over = _derive_carry_over(accepted_group_patches[sid])
                break

    meta = {
        "groups": len(groups),
        "group_sizes": [len(g) for g in groups],
        "polished_shots": len(polished_ids),
        "polished_shot_ids": polished_ids,
        "fallback_shots": len(fallback_ids),
        "fallback_shot_ids": fallback_ids,
        "salvage_notes_count": len(salvage_notes_all),
    }
    return polished, warnings, meta


def polish_single_shot_spec(
    spec_dict: dict[str, Any],
    *,
    shot_id: str,
    call_llm: LLMCaller,
    previous_exit_state: str = "",
    beat_energies: list[str] | None = None,
) -> tuple[dict[str, Any], list[dict[str, Any]], dict[str, Any]]:
    """Repolish one landed ``ShotSpec`` dict via the same non-blocking Acting Pass."""
    working = copy.deepcopy(spec_dict)
    working_with_id = {**working, "id": shot_id, "duration_s": float(working.get("duration_target_s") or 6.0)}
    plan = compute_pre_speech_budget(working_with_id, beat_energies)
    token_map = dict(working.get("character_tokens") or {})
    system_prompt = acting_director_system_prompt()
    payload = build_acting_group_payload(
        [(working_with_id, plan, list(beat_energies or []))],
        token_map=token_map,
        carry_over_state=previous_exit_state,
    )

    warnings: list[dict[str, Any]] = []
    patch: ActingShotPatch | None = None
    last_issue = "unknown_error"
    for attempt in range(2):
        call_payload = payload if attempt == 0 else build_acting_group_payload(
            [(working_with_id, plan, list(beat_energies or []))],
            token_map=token_map,
            carry_over_state=previous_exit_state,
            retry_feedback=(
                f"Previous attempt failed ({last_issue}). Output valid ActingGroupPatch JSON for "
                f"shot {shot_id} with concrete facial anatomy, zero bare emotion words, and pure English."
            ),
        )
        try:
            raw = call_llm(system_prompt, call_payload, f"acting_repolish_{shot_id}_{attempt + 1}")
        except Exception as exc:  # noqa: BLE001
            last_issue = f"{type(exc).__name__}:{str(exc)[:120]}"
            continue
        patches, errors, _notes = parse_acting_group_patch(raw, [shot_id])
        candidate = patches.get(shot_id)
        if candidate is None:
            last_issue = errors.get(shot_id) or "missing_in_response"
            continue
        val_err = validate_shot_patch(candidate, working_with_id, token_map=token_map)
        if val_err is not None:
            last_issue = val_err
            continue
        patch = candidate
        break

    if patch is None:
        warnings.append({"code": "ACTING_POLISH_FALLBACK", "shot_id": shot_id, "reason": last_issue})
        return working, warnings, {"polished": False, "fallback": True, "reason": last_issue}

    merged = apply_shot_patch(working_with_id, patch, plan, token_map=token_map)
    merged.pop("id", None)
    merged.pop("duration_s", None)
    return merged, warnings, {
        "polished": True,
        "fallback": False,
        "applied_pause_s": plan.applied_pause_s,
        "shift_s": plan.shift_s,
        "exit_acting_state": _derive_carry_over(patch),
    }
