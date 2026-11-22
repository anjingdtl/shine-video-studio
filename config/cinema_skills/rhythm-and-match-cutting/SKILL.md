---
id: rhythm-and-match-cutting
version: "1.0.0"
title: Pre-Production Rhythm & Match-Cutting Intent
roles:
  - editor
target_fields:
  - clips.continuity_note
  - rhythm
  - dialogue_pace
film_types:
  - live_action
  - animation
tags:
  - rhythm
  - continuation
  - internal_cut
  - dialogue
  - silent
  - action
  - emotion
priority: 92
license: Apache-2.0
source: shine-original
---
# Pre-Production Rhythm & Match-Cutting Intent

## Core Method
1. **Honest Pre-Render Intent**: Plan sequence pacing and joining rationale from the script and `DirectorialPlan` boundaries without claiming to have watched unrendered footage or inventing media timecodes.
2. **Boundary Matching**:
   - When a segment enters via `last_frame`, specify the exact held pose, gaze vector, or trailing prop motion inherited at the join in `continuity_note`.
   - When a segment enters via `CUT`, specify the rhythmic motivation (action-match cut, eyeline reaction cut, or spatial reset) and leave a brief emotional breath after spoken lines.
3. **Dialogue Pace Alignment**: Match `dialogue_pace` to the natural delivery cadence of the scene without truncating lines or altering `planned_duration_s`.

## Guardrails
- Copy every `DirectorialPlan` segment `id` verbatim to `intent_id` in contiguous order `1..N`, keeping `planned_duration_s` identical to `duration_s`.
- Never split internal cuts into separate `EditIntentClip` entries.
