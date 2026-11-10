from __future__ import annotations

from shine.comfy.provider import ComfyPendingWorkflowMapping, resolve_reference_slots, validate_reference_slots
from shine.schemas import ReferenceManifestItem, ReferenceUse


def _item(label: str, use_as: ReferenceUse) -> ReferenceManifestItem:
    return ReferenceManifestItem(
        label=label,
        asset_id=f"asset-{label}",
        sha256="0" * 64,
        storage_key=f"references/{label}.png",
        content_type="image/png",
        use_as=use_as,
    )


TWO_HANDER = [
    _item("<Subject 1>", ReferenceUse.SUBJECT),
    _item("<Subject 2>", ReferenceUse.SUBJECT),
    _item("<Picture 3>", ReferenceUse.PICTURE),
]

POSITIONAL_MAP = {
    "positive_prompt": "31.inputs.prompt",
    "reference_image_1": "40.inputs.image",
    "reference_image_2": "41.inputs.image",
    "reference_image_3": "42.inputs.image",
}


def test_one_positional_profile_carries_every_cast_composition():
    """A two-hander and a one-hander are the same request with more images.

    H3 numbers images in a single sequence, and the graph wires its loaders in
    that order, so the slot that carries image N is positional - not one slot
    name per family.  Without this a project needed a hand-made profile per cast
    mix, and a shot naming two people was refused by every profile it had.
    """
    resolved = resolve_reference_slots(POSITIONAL_MAP, TWO_HANDER)

    assert [key for _logical, key, _item_ in resolved] == [
        "reference_image_1", "reference_image_2", "reference_image_3",
    ]
    # The family name stays the logical identity every message and test names.
    assert [logical for logical, _key, _item_ in resolved] == [
        "reference_subject_1", "reference_subject_2", "reference_picture_1",
    ]
    assert [item.label for _logical, _key, item in resolved] == [
        "<Subject 1>", "<Subject 2>", "<Picture 3>",
    ]
    validate_reference_slots(POSITIONAL_MAP, TWO_HANDER)


def test_a_family_profile_keeps_its_own_slot_names():
    family_map = {
        "positive_prompt": "31.inputs.prompt",
        "reference_subject_1": "40.inputs.image",
        "reference_picture_1": "41.inputs.image",
    }
    one_hander = [_item("<Subject 1>", ReferenceUse.SUBJECT), _item("<Picture 2>", ReferenceUse.PICTURE)]

    assert [key for _logical, key, _item_ in resolve_reference_slots(family_map, one_hander)] == [
        "reference_subject_1", "reference_picture_1",
    ]
    validate_reference_slots(family_map, one_hander)


def test_a_cast_that_does_not_fit_is_refused_not_silently_cut():
    """Three images into a two-image profile is a refusal with a name."""
    two_image_map = {
        "positive_prompt": "31.inputs.prompt",
        "reference_image_1": "40.inputs.image",
        "reference_image_2": "41.inputs.image",
    }
    try:
        validate_reference_slots(two_image_map, TWO_HANDER)
    except ComfyPendingWorkflowMapping as exc:
        assert exc.details["missing_reference_slots"] == ["reference_picture_1"]
    else:  # pragma: no cover - the gate must refuse, not drop the plate
        raise AssertionError("a dropped reference must be a refusal")
