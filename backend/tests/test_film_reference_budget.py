"""The director prompt states the verified workflow reference-slot budget.

A plan that promises more references than every registered workflow can carry
only fails at render time (PENDING_WORKFLOW_MAPPING), so the budget note is
injected into the director prompt — but only from a validated profile, because
an unverified registration proves nothing about what a render can carry.
"""

from __future__ import annotations

import pytest

from shine.agents.film import reference_budget_note

pytestmark = pytest.mark.unit


def _budget(validated=True, image=3, audio=1, name="suZhen-ref2va-3img-1aud"):
    return {"id": "p1", "name": name, "validated": validated,
            "image_slots": image, "audio_slots": audio}


def test_budget_note_states_verified_slot_counts():
    note = reference_budget_note([_budget()])
    assert "3 image reference slots" in note
    assert "1 audio reference slot" in note
    assert "blocks the render" in note


def test_budget_note_prefers_the_largest_verified_budget():
    note = reference_budget_note([_budget(), _budget(validated=True, image=7, audio=1, name="wide")])
    assert "'wide'" in note
    assert "7 image reference slots" in note


def test_budget_note_silent_without_verified_profiles():
    assert reference_budget_note([]) == ""
    assert reference_budget_note([_budget(validated=False)]) == ""
    assert reference_budget_note([_budget(image=0)]) == ""
