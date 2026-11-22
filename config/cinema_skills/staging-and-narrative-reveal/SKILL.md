---
id: staging-and-narrative-reveal
version: "1.0.0"
title: Staging, Blocking & Narrative Reveal
roles:
  - director
target_fields:
  - shots.visual_description
  - shots.continuity
  - shots.cuts
  - shots.asset_ids
film_types:
  - live_action
  - animation
tags:
  - blocking
  - reveal
  - continuation
  - internal_cut
  - two_shot
  - single_character
  - multi_character
  - prop
  - silent
  - dialogue
priority: 95
license: Apache-2.0
source: shine-original
---
# Staging, Blocking & Narrative Reveal

## Core Method
1. **One Dramatic Center**: Give each render segment a single primary action or reveal; develop it cleanly across the segment duration rather than crowding unrelated events.
2. **Spatial Layering & Screen Geography**: Place subjects clearly across foreground, midground, and background with explicit left/right screen placement and consistent facing direction.
3. **Boundary Choice (`last_frame` vs `cut`)**:
   - Choose `continuity.mode=last_frame` when the next segment continues the same continuous action, space, and moment (even on a closer/wider framing).
   - Choose `continuity.mode=cut` for a real break: new location, time jump, insert, or deliberate counter-angle reaction.
   - Record the concrete action-match or gaze-match handoff in `continuity.note`.
4. **Silent Reaction Discipline**: Outside timed `dialogue` blocks, keep performers with closed mouths expressing intent through posture, gaze, and physical action.

## Guardrails
- Copy `editorial_timing_plan` segment durations and `cuts[].start_s` offsets verbatim.
- Write all `visual_description` and `cuts[].visual_description` fields in English role labels (never copy CJK names or quote spoken lines inside visual prose).
- Order `asset_ids` strictly by first visual appearance in the segment, keeping within the verified reference budget.
