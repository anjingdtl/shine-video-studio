"""A6 回归：项目里已经有同一份内容的素材时，再分配全局已晋升版本。

症状：service.assign_to_project 无条件 db.add(project_asset); db.flush()，撞上 assets 表
(project_id, sha256) 唯一约束；异常发生在 db.commit() 之前，API 层那个只包住 commit 的
IntegrityError 兜底接不住 → 用户看到裸英文 500 Internal Server Error。

期望：同一份内容不再变成裸 500 —— 身份一致就幂等复用项目里那一份，身份不同则具名拒绝
并说清下一步。
"""

from sqlalchemy import select

from shine.asset_center import service
from shine.asset_center.models import LibraryRights
from shine.models import Asset
from tests.test_asset_center import _png, _rights_upload

LOCAL = {"X-Shine-Local-Action": "1"}


def _promoted_version(client, factory, project_id, png_bytes, title):
    """上传 → 登记为全局素材 → 权利允许本项目 → 技术检查 → 人工晋升。"""
    uploaded = _rights_upload(client, png_bytes)
    media_id = uploaded.json()["id"]
    rights_id = uploaded.json()["rights_id"]
    with factory() as db:
        snapshot = service.canonical_hash(service.rights_snapshot(db.get(LibraryRights, rights_id)))
    approved = client.post(
        f"/api/v1/asset-center/rights/{rights_id}/approve",
        headers=LOCAL,
        json={
            "expected_snapshot": snapshot,
            "evidence": "fixture owned by the test",
            "allowed_project_ids": [project_id],
            "permitted_uses": ["research"],
            "commercial_allowed": False,
        },
    )
    assert approved.status_code == 200, approved.text
    asset = client.post(
        "/api/v1/asset-center/assets",
        json={"title": title, "kind": "image", "category": "character", "media_id": media_id},
    ).json()
    version_id = asset["versions"][0]["id"]
    qc = client.post(f"/api/v1/asset-center/versions/{version_id}/qc")
    assert qc.status_code == 200, qc.text
    grant = client.post(f"/api/v1/asset-center/versions/{version_id}/grant", headers=LOCAL)
    assert grant.status_code == 200, grant.text
    promoted = client.post(
        f"/api/v1/asset-center/versions/{version_id}/promote",
        headers=LOCAL,
        json={
            "grant_id": grant.json()["grant_id"],
            "snapshot": grant.json()["snapshot"],
            "evidence": "reviewed synthetic fixture",
        },
    )
    assert promoted.status_code == 200, promoted.text
    return version_id


def _assign(client, project_id, version_id, entity_id, key):
    return client.post(
        "/api/v1/asset-center/assignments",
        headers=LOCAL,
        json={
            "project_id": project_id,
            "library_version_id": version_id,
            "role": "character",
            "entity_id": entity_id,
            "usage_scope": "research",
            "idempotency_key": key,
        },
    )


def test_assigning_content_already_in_the_project_is_refused_by_name(api):
    client, factory, _settings = api
    project_id = client.post("/api/v1/projects", json={"title": "重复内容分配"}).json()["id"]
    png = _png(2, [[(25, 35, 45, 255), (55, 65, 75, 255)]])
    version_id = _promoted_version(client, factory, project_id, png, "共享素材")
    first = _assign(client, project_id, version_id, "林知远", "assign-first-01")
    assert first.status_code == 201, first.text

    again = _assign(client, project_id, version_id, "林知远二号", "assign-second-01")
    assert again.status_code == 409, again.text
    detail = again.json()["detail"]
    assert detail["code"] == "ASSET_ALREADY_IN_PROJECT", detail
    assert "已经在项目里" in detail["message"], detail
    assert detail["existing_asset_id"], detail
    # 台账没有多出一份内容。
    with factory() as db:
        rows = db.scalars(select(Asset).where(Asset.project_id == project_id)).all()
        assert len(rows) == 1, rows


def test_assigning_the_same_content_again_reuses_the_project_asset(api):
    client, factory, _settings = api
    project_id = client.post("/api/v1/projects", json={"title": "同内容复用"}).json()["id"]
    png = _png(2, [[(25, 35, 45, 255), (55, 65, 75, 255)]])
    first_version = _promoted_version(client, factory, project_id, png, "共享素材一")
    second_version = _promoted_version(client, factory, project_id, png, "共享素材二")

    first = _assign(client, project_id, first_version, "林知远", "assign-reuse-first")
    assert first.status_code == 201, first.text
    second = _assign(client, project_id, second_version, "林知远", "assign-reuse-second")
    assert second.status_code == 201, second.text
    assert second.json()["project_asset_id"] == first.json()["project_asset_id"]
    with factory() as db:
        rows = db.scalars(select(Asset).where(Asset.project_id == project_id)).all()
        assert len(rows) == 1, rows
