"""A generated material is named after its entity and its place in the script.

A person matches a generated picture to the script and the storyboard by its
name, so the file name carries the entity and the number of the place the
entity takes in the script.
"""

from __future__ import annotations

from shine.comfyops.models import (
    PLAN_PURPOSE_ASSET_PREPARATION,
    AssetRequirement,
    PreProductionPlan,
)
from shine.materialprep.naming import material_filename, readable_name
from shine.models import Project


def _script_plan(db, project_id):
    """Three scenes, and the materials each of them needs."""
    entries = [
        {"family": "character", "entity_id": "林素", "scene_keys": ["场景一"]},
        {"family": "character", "entity_id": "周野", "scene_keys": ["场景二"]},
        {"family": "prop", "entity_id": "黄铜怀表", "scene_keys": ["场景一"]},
        {"family": "scene", "entity_id": "旧船坞·夜", "scene_keys": ["场景一"]},
    ]
    scenes = ["场景一", "场景二", "场景三"]
    plan = PreProductionPlan(
        project_id=project_id,
        payload={"requirements": [], "source_scenes": scenes},
        sha256="s" * 64,
        status="CONFIRMED",
        purpose=PLAN_PURPOSE_ASSET_PREPARATION,
    )
    db.add(plan)
    db.flush()
    stored = []
    for entry in entries:
        row = AssetRequirement(
            project_id=project_id,
            plan_id=plan.id,
            payload={**entry, "critical": True, "strategy": "generate"},
            sha256="r" * 64,
            status="CONFIRMED",
        )
        db.add(row)
        db.flush()
        stored.append({**entry, "requirement_id": row.id})
    plan.payload = {"requirements": stored, "source_scenes": scenes}
    db.flush()
    return plan


def test_a_material_carries_its_entity_and_its_place_in_the_script(studio):
    client, factory, _, _ = studio
    with factory.begin() as db:
        project = Project(title="雾港素材", language="Chinese")
        db.add(project)
        db.flush()
        project_id = project.id
        _script_plan(db, project_id)
    with factory() as db:
        # 场景一 needs 林素, 黄铜怀表 and 旧船坞·夜; 场景二 needs 周野.
        assert material_filename(db, project_id, "character", "林素") == "01-林素.png"
        assert material_filename(db, project_id, "prop", "黄铜怀表") == "02-黄铜怀表.png"
        assert material_filename(db, project_id, "scene", "旧船坞·夜") == "03-旧船坞·夜.png"
        assert material_filename(db, project_id, "character", "周野") == "04-周野.png"
        # An entity with no place in the script keeps a plain readable name.
        assert material_filename(db, project_id, "prop", "手工上传的道具") == "手工上传的道具.png"


def test_a_material_name_never_carries_a_character_windows_rejects():
    assert readable_name('林素: "定妆"', "material") == "林素_ _定妆_"
    assert readable_name("", "prop") == "prop"
    assert readable_name("   ", "prop") == "prop"
