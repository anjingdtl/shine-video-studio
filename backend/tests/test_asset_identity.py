from shine.asset_identity import (
    canonical_character_name_for_asset_id,
    character_reference_matches,
    resolve_character_asset,
)


def test_character_reference_matches_common_asset_view_suffixes():
    assert character_reference_matches("R01 杜万山", "R01 杜万山胸上")
    assert character_reference_matches("杜万山", "R01 杜万山胸上.png")
    assert not character_reference_matches("杜万山二号", "R01 杜万山胸上")


def test_character_reference_resolution_requires_one_unique_approved_master():
    master = {"id": "asset-1", "kind": "image", "role": "character", "entity_id": "R01 杜万山胸上"}

    assert resolve_character_asset("R01 杜万山", [master]) == master
    assert resolve_character_asset("R01 杜万山", [master, {**master, "id": "asset-2"}]) is None
    assert resolve_character_asset("R01 林岚", [master]) is None


def test_exact_character_asset_uuid_resolves_and_canonicalizes_to_readable_name():
    master = {
        "id": "69feae73-6f69-48f8-9ecf-92e9db230923",
        "kind": "image",
        "role": "character",
        "entity_id": "R01 杜万山胸上",
    }

    assert resolve_character_asset(master["id"], [master]) == master
    assert canonical_character_name_for_asset_id(master["id"], [master]) == master["entity_id"]
    assert canonical_character_name_for_asset_id("R01 杜万山", [master]) == "R01 杜万山"
