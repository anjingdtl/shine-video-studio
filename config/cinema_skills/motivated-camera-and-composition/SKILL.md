---
id: motivated-camera-and-composition
version: "1.0.0"
title: Motivated Camera, Composition & Directional Lighting
roles:
  - cinematographer
target_fields:
  - shots.composition
  - shots.movement
  - shots.lighting
  - shots.axis
film_types:
  - live_action
  - animation
tags:
  - composition
  - camera_movement
  - lighting
  - suspense
  - emotion
  - action
  - two_shot
  - prop
priority: 94
license: Apache-2.0
source: shine-original
---
# Motivated Camera, Composition & Directional Lighting

## Core Method
1. **Static Frame Default**: Hold the camera still unless a physical character move, gaze shift, or story reveal justifies camera travel.
2. **Mandatory Movement Formula**: When moving the camera, always specify `motive -> movement type -> amplitude -> speed -> settling composition` (for example: "motivated by her realization on the torn seal, a slow small push-in over the desk, settling into a tight medium close-up").
3. **Composition & Physical Grounding**: State starting shot scale, rule-of-thirds placement, relative camera height/side (eye-level, slight low angle, three-quarter front), ending framing, and firm ground/surface contact.
4. **Directional Lighting**: Name a single motivated practical or natural key source direction and shadow behavior that keeps facial identity clear.

## Guardrails
- Distinguish physical camera travel (`push-in`, `dolly back`, `lateral track`) from optical `zoom`; never stack conflicting moves or call for unmotivated `orbit`.
- Keep all camera fields (`composition`, `movement`, `lighting`, `axis`) strictly in English prose with no quoted spoken dialogue or CJK characters.
- Preserve the 180-degree dialogue/action axis recorded in `axis`.
