from __future__ import annotations

import pytest

from shine.director.models import DirectorEpisodePlan, DirectorPackageVersion
from shine.models import Asset, StyleVersion, utcnow
from tests.conftest import create_project, create_shot, freeze_style, style_payload, upload_asset

pytestmark = pytest.mark.integration
HEADERS = {"x-shine-local-action": "1"}


def test_style_unlock_refreezes_and_applies_to_every_old_shot(api):
    client, factory, _ = api
    project_id = create_project(client, "冻结解锁与重冻")
    first = freeze_style(client, project_id)
    shot = create_shot(client, project_id, first["id"])
    changed = {**style_payload(), "color_palette": "新的冷色与硬边平涂", "film_type": "animation"}
    assert client.put(f"/api/v1/projects/{project_id}/style/draft", json=changed).status_code == 200

    response = client.post(
        f"/api/v1/projects/{project_id}/freeze/style/refresh",
        json={"expected_style_version_id": first["id"], "approval_note": "用户手动解锁并应用新冻结"},
        headers=HEADERS,
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["style"]["version"] == 2
    assert body["style"]["snapshot"]["film_type"] == "animation"
    assert shot["id"] in body["revised_shot_ids"]

    rows = client.get(f"/api/v1/projects/{project_id}/shots").json()
    assert rows[0]["revision"] == 2
    assert rows[0]["spec"]["style_version_id"] == body["style"]["id"]
    assert rows[0]["spec"]["prompt_style"] is None
    with factory() as db:
        assert db.query(StyleVersion).filter_by(project_id=project_id).count() == 2


def test_current_freeze_can_be_applied_to_shots_without_another_version(api):
    client, _factory, _ = api
    project_id = create_project(client, "应用当前冻结")
    first = freeze_style(client, project_id)
    shot = create_shot(client, project_id, first["id"])
    changed = {**style_payload(), "lens_texture": "新的镜头质感"}
    assert client.put(f"/api/v1/projects/{project_id}/style/draft", json=changed).status_code == 200
    second = client.post(f"/api/v1/projects/{project_id}/style/freeze", json={}).json()

    response = client.post(
        f"/api/v1/projects/{project_id}/freeze/shots/style",
        json={"reason": "把已冻结的新风格应用到旧镜头"},
        headers=HEADERS,
    )
    assert response.status_code == 200, response.text
    assert shot["id"] in response.json()["revised_shot_ids"]
    rows = client.get(f"/api/v1/projects/{project_id}/shots").json()
    assert rows[0]["spec"]["style_version_id"] == second["id"]


def test_an_approved_asset_can_be_unlocked_and_reconfirmed(api):
    client, factory, _ = api
    project_id = create_project(client, "素材解锁")
    asset = upload_asset(client, project_id, entity_id="安娜", role="character")
    with factory.begin() as db:
        row = db.get(Asset, asset["id"])
        row.approved = True
        row.locked = True
        row.approved_at = utcnow()
        row.locked_at = utcnow()

    response = client.post(
        f"/api/v1/projects/{project_id}/freeze/assets/{asset['id']}/unlock",
        json={"reason": "用户要修改素材元数据"},
        headers=HEADERS,
    )
    assert response.status_code == 200, response.text
    assert response.json()["approved"] is False
    assert response.json()["locked"] is False
    with factory() as db:
        row = db.get(Asset, asset["id"])
        assert row.approved is False and row.locked is False


def test_unlocking_an_episode_plan_stales_its_confirmed_package(api):
    client, factory, _ = api
    project_id = create_project(client, "分集解锁")
    with factory.begin() as db:
        plan = DirectorEpisodePlan(
            project_id=project_id, version=1, status="CONFIRMED",
            target_seconds=60, tolerance_seconds=0,
            payload={"mode": "shots", "episodes": []}, sha256="a" * 64,
            source="shots", confirmed_by="local-user", confirmed_at=utcnow(),
        )
        db.add(plan)
        db.flush()
        package = DirectorPackageVersion(
            project_id=project_id, revision=1, status="CONFIRMED",
            manifest={}, scope_digest="b" * 64, manifest_sha256="c" * 64,
            policy_hash="d" * 64, episode_count=0, shot_count=0,
            episode_plan_id=plan.id, confirmed_by="local-user", confirmed_at=utcnow(),
        )
        db.add(package)
        db.flush()
        plan_id, package_id = plan.id, package.id

    response = client.post(
        f"/api/v1/projects/{project_id}/freeze/episode-plans/1/unlock",
        json={"reason": "用户要重新分集"},
        headers=HEADERS,
    )
    assert response.status_code == 200, response.text
    assert response.json()["stale_package_ids"] == [package_id]
    with factory() as db:
        assert db.get(DirectorEpisodePlan, plan_id).status == "DRAFT"
        package = db.get(DirectorPackageVersion, package_id)
        assert package.status == "CONFIRMED"
        assert package.stale is True
        assert package.stale_reason == "EPISODE_PLAN_UNFROZEN"

    unlocked = client.post(
        f"/api/v1/projects/{project_id}/freeze/director-packages/{package_id}/unlock",
        json={"reason": "用户要重新生成导演包"},
        headers=HEADERS,
    )
    assert unlocked.status_code == 200, unlocked.text
    assert unlocked.json()["stale_reason"] == "MANUAL_UNLOCK"
