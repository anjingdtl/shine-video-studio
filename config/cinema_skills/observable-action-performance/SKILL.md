---
id: observable-action-performance
version: "1.0.0"
title: Observable Action & Physical Performance Breakdown
roles:
  - scriptwriter
target_fields:
  - beats.description
  - beats.characters
  - beats.dialogue
film_types:
  - live_action
  - animation
tags:
  - action
  - emotion
  - prop
  - dialogue
  - silent
  - single_character
  - multi_character
priority: 95
license: Apache-2.0
source: shine-original
---
# Observable Action & Physical Performance Breakdown

## Core Method
Translate literary exposition and internal emotion into concrete, filmable physical behavior across three observable phases:
1. **Anchor Posture & Contact**: State where the character stands, sits, or leans, and how feet, hands, or body contact the floor, furniture, or props.
2. **Externalized Action**: Show inner state through visible micro-actions (a tightening grip, paused breathing, averted gaze, hesitant reach, lowered shoulders) instead of writing invisible mental verbs ("remembers", "realizes", "fears").
3. **Connective End-State**: End every beat on a stable physical pose, completed handoff, or held look that the next beat or segment can pick up cleanly.

## Guardrails
- Keep every spoken line character-for-character verbatim from the user script inside `beat.dialogue`.
- Never convert stage directions, inner thoughts, or scene prose into unauthorized narrator or voice-over lines.
- List only characters who visibly appear on screen (or deliver a spoken line) in `beat.characters`, in first-appearance order.
- Never alter costumes, headgear, or traits that contradict approved character masters.
