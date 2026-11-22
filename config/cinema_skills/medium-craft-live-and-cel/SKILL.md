---
id: medium-craft-live-and-cel
version: "1.0.0"
title: Medium Craft for Live-Action & 2D Cel Animation
roles:
  - director
  - cinematographer
target_fields:
  - shots.visual_description
  - shots.composition
  - shots.lighting
film_types:
  - live_action
  - animation
tags:
  - live_action
  - animation
  - lighting
  - composition
  - action
priority: 88
license: Apache-2.0
source: shine-original
---
# Medium Craft for Live-Action & 2D Cel Animation

## Core Method
Tailor staging, lighting, and motion prose strictly to the frozen `film_type` without ever introducing terms from the opposite medium:
- **Live-Action (`live_action`)**: Anchor optical depth of field, natural physical inertia in clothing and hair, practical set materials (stone, wood grain, damp fabric), and directional realistic light falloff.
- **2D Cel Animation (`animation`)**: Emphasize clean contour silhouettes, readable key-pose timing with crisp anticipation and settle frames, two-tone cel shadow planes, and painted background depth separation.

## Guardrails
- Never mix `cel-shaded`, `anime`, or `hand-drawn` cues into a `live_action` shot, and never introduce `photorealistic skin pores`, `DSLR sensor grain`, or `live-action footage` cues into an `animation` shot.
- Keep all medium craft descriptions compatible with the frozen Style Bible and the approved reference masters.
