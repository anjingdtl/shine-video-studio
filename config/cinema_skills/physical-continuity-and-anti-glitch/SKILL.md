---
id: physical-continuity-and-anti-glitch
version: "1.0.0"
title: Physical Continuity & Anti-Glitch Inspection
roles:
  - continuity_supervisor
target_fields:
  - issues
  - recommendation
film_types:
  - live_action
  - animation
tags:
  - continuity
  - prop
  - two_shot
  - multi_character
  - continuation
  - internal_cut
  - action
  - dialogue
priority: 95
license: Apache-2.0
source: shine-original
---
# Physical Continuity & Anti-Glitch Inspection

## Core Method
Inspect THIS single section's `DirectorialPlan` and `CameraPlan` (plus any preceding section end-state) for real physical or render-blocking contradictions:
1. **Spatial & Ground Contact**: Flag characters or props that appear without entry/grounding ("emerging from the floor"), teleport between positions, or stand in two places at once.
2. **Prop & Costume Persistence**: Flag held props that vanish or switch hands mid-action without a transfer, or clothing descriptions that contradict approved masters.
3. **Axis & Screen Direction**: Flag reversed left/right placement or conflicting gaze directions across `last_frame` boundaries and reaction cuts.
4. **Action Completion & Timing**: Flag actions restarted awkwardly across a `last_frame` join, illegal internal cut offsets, or spoken lines exceeding their beat/segment window.

## Guardrails
- Scope strictly to THIS section: never report missing scenes, beats, or shots from other parts of the whole work.
- Never report valid `material_request` gaps or stylistic preferences as conflicts; return `issues: []` with `proceed_to_preflight` when renderable.
- Write concrete issues in Simplified Chinese citing exact `shot_id` and technical mode names.
