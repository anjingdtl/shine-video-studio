from shine.asset_identity import (
    canonical_character_name_for_asset_id,
    character_reference_matches,
    resolve_character_asset,
)
from shine.api_film import _approved_master_for_name
from shine.models import Asset


def test_film_binding_prefers_latest_exact_character_master(api):
    client, factory, _ = api
    project_id = client.post("/api/v1/projects", json={"title": "Cast"}).json()["id"]
    with factory.begin() as db:
        for identity, role, entity_id in [
            ("old", "character", "Roland"),
            ("new", "character", "Roland"),
            ("different", "character", "Roland II"),
            ("location", "scene", "Roland"),
        ]:
            db.add(Asset(id=identity, project_id=project_id, kind="image", filename="ref.png",
                         content_type="image/png", sha256=identity, storage_path="ref.png", byte_size=1,
                         approved=True, locked=True, role=role, entity_id=entity_id))
        db.flush()
        assert _approved_master_for_name(db, project_id, "Roland").id == "new"


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
