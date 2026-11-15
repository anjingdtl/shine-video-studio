"""「待你确认」必须有真实动作：分集级人工验收闭环。

技术交付与艺术验收是两个事实。装配冻结的是建成时的事实；这里钉住的是
「人看过之后怎么决定」：接受必须声明看完，拒绝必须给理由，决定绑定看到的
SHA-256，文件被改动不得验收，mock 不得验收，重放被拒，历史不重写。
"""

from __future__ import annotations

import hashlib
from uuid import uuid4

from shine.director.models import DirectorPackageVersion
from shine.media.delivery_models import DeliveryAcceptance
from shine.models import MediaArtifact
from shine.production.models import ProductionRun, ProductionRunEpisode
from tests.conftest import create_project, make_test_video_bytes

LOCAL = {"X-Shine-Local-Action": "1"}


def _delivery(factory, settings, project_id, *, is_mock=False, provenance=None):
    content = make_test_video_bytes(duration_s=1.0, width=160, height=120, with_audio=False)
    digest = hashlib.sha256(content).hexdigest()
    relative = f"artifacts/{project_id}/delivery/{digest}.mp4"
    target = settings.data_dir / relative
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(content)
    with factory.begin() as db:
        artifact = MediaArtifact(
            project_id=project_id,
            artifact_kind="delivery",
            storage_path=relative,
            sha256=digest,
            byte_size=len(content),
            content_type="video/mp4",
            is_mock=is_mock,
            provenance=provenance if provenance is not None else {
                "kind": "delivery", "review_state": "pending_review", "clips": [],
            },
        )
        db.add(artifact)
        db.flush()
        artifact_id = artifact.id
    return artifact_id, digest, content


def _decide(client, artifact_id, sha, decision="accepted", **overrides):
    body = {
        "decision": decision,
        "watched": decision == "accepted",
        "evidence": "完整看过，画面与声音符合预期" if decision == "accepted" else "第 3 秒口型不对",
        "request_id": uuid4().hex,
        "expected_sha256": sha,
        **overrides,
    }
    return client.post(f"/api/v1/deliveries/{artifact_id}/acceptance", headers=LOCAL, json=body)


def test_watched_acceptance_is_recorded_and_visible(api):
    client, factory, settings = api
    project = create_project(client)
    artifact_id, sha, _ = _delivery(factory, settings, project)

    response = _decide(client, artifact_id, sha)
    assert response.status_code == 200, response.text
    assert response.json()["acceptance"]["decision"] == "accepted"
    assert response.json()["acceptance"]["actor"] == "local-user"

    current = client.get(f"/api/v1/deliveries/{artifact_id}/acceptance")
    assert current.status_code == 200
    assert current.json()["acceptance"]["decision"] == "accepted"

    library = client.get(f"/api/v1/projects/{project}/video-library").json()
    entry = next(row for row in library["entries"] if row["kind"] == "delivery")
    assert entry["review_state"] == "accepted"
    # 装配时冻结的事实仍是历史，不被追改。
    assert entry["assembly_review_state"] == "pending_review"
    assert entry["acceptance"]["decision"] == "accepted"
    assert library["summary"]["pending_review"] == 0


def test_acceptance_requires_watched_and_rejection_requires_reason(api):
    client, factory, settings = api
    project = create_project(client)
    artifact_id, sha, _ = _delivery(factory, settings, project)

    unwatched = _decide(client, artifact_id, sha, watched=False)
    assert unwatched.status_code == 422
    assert unwatched.json()["detail"] == "ACCEPTANCE_REQUIRES_WATCHED"

    silent_rejection = _decide(client, artifact_id, sha, "rejected", evidence="  ")
    assert silent_rejection.status_code == 422
    assert silent_rejection.json()["detail"] == "ACCEPTANCE_REJECTION_NEEDS_EVIDENCE"


def test_request_replay_and_version_drift_are_refused(api):
    client, factory, settings = api
    project = create_project(client)
    artifact_id, sha, _ = _delivery(factory, settings, project)

    body = {
        "decision": "accepted", "watched": True, "evidence": "",
        "request_id": uuid4().hex, "expected_sha256": sha,
    }
    first = client.post(f"/api/v1/deliveries/{artifact_id}/acceptance", headers=LOCAL, json=body)
    assert first.status_code == 200
    replay = client.post(f"/api/v1/deliveries/{artifact_id}/acceptance", headers=LOCAL, json=body)
    assert replay.status_code == 409
    assert replay.json()["detail"] == "ACCEPTANCE_REQUEST_REPLAYED"

    drift = _decide(client, artifact_id, "0" * 64)
    assert drift.status_code == 409
    assert drift.json()["detail"] == "DELIVERY_VERSION_CHANGED"


def test_mock_deleted_and_tampered_deliveries_cannot_be_accepted(api):
    client, factory, settings = api
    project = create_project(client)

    mock_id, mock_sha, _ = _delivery(factory, settings, project, is_mock=True)
    mock = _decide(client, mock_id, mock_sha)
    assert mock.status_code == 409
    assert mock.json()["detail"] == "MOCK_DELIVERY_NOT_ACCEPTABLE"

    artifact_id, sha, content = _delivery(factory, settings, project)
    with factory.begin() as db:
        artifact = db.get(MediaArtifact, artifact_id)
        path = settings.data_dir / artifact.storage_path
    path.write_bytes(b"changed after delivery")
    tampered = _decide(client, artifact_id, sha)
    assert tampered.status_code == 409
    assert tampered.json()["detail"] == "DELIVERY_FILE_HASH_MISMATCH"

    with factory.begin() as db:
        from shine.models import utcnow

        db.get(MediaArtifact, artifact_id).deleted_at = utcnow()
    deleted = _decide(client, artifact_id, sha)
    assert deleted.status_code == 404
    assert deleted.json()["detail"] == "VIDEO_DELETED"


def test_a_later_decision_supersedes_without_rewriting_history(api):
    client, factory, settings = api
    project = create_project(client)
    artifact_id, sha, _ = _delivery(factory, settings, project)
    assert _decide(client, artifact_id, sha).status_code == 200
    assert _decide(client, artifact_id, sha, "rejected").status_code == 200

    current = client.get(f"/api/v1/deliveries/{artifact_id}/acceptance").json()
    assert current["acceptance"]["decision"] == "rejected"
    with factory.begin() as db:
        rows = db.query(DeliveryAcceptance).filter_by(artifact_id=artifact_id).all()
        assert len(rows) == 2  # 两次决定都留痕


def test_acceptance_needs_the_local_action_boundary(api):
    client, factory, settings = api
    project = create_project(client)
    artifact_id, sha, _ = _delivery(factory, settings, project)
    body = {
        "decision": "accepted", "watched": True, "evidence": "",
        "request_id": uuid4().hex, "expected_sha256": sha,
    }
    response = client.post(f"/api/v1/deliveries/{artifact_id}/acceptance", json=body)
    assert response.status_code == 403


def _run_with_delivered_episode(factory, project_id, artifact_id, sha):
    with factory.begin() as db:
        package = DirectorPackageVersion(
            project_id=project_id, revision=1, manifest={}, scope_digest="a" * 64,
            manifest_sha256="b" * 64, policy_hash="c" * 64,
        )
        db.add(package)
        db.flush()
        run = ProductionRun(
            project_id=project_id, package_id=package.id, package_revision=1,
            manifest_sha256="b" * 64, scope_digest="a" * 64, policy_hash="c" * 64,
            idempotency_key="run-accept", request_hash="d" * 64, status="COMPLETED",
        )
        db.add(run)
        db.flush()
        episode = ProductionRunEpisode(
            run_id=run.id, project_id=project_id, episode_id="ep-1", position=1,
            manifest_sha256="e" * 64, shot_ids=[], status="DELIVERED",
            delivered_artifact_id=artifact_id, delivered_sha256=sha,
        )
        db.add(episode)
        db.flush()
        run_id = run.id
    return run_id


def test_run_episodes_and_journey_report_the_acceptance(api):
    client, factory, settings = api
    project = create_project(client)
    artifact_id, sha, _ = _delivery(factory, settings, project)
    run_id = _run_with_delivered_episode(factory, project, artifact_id, sha)

    before = client.get(f"/api/v1/production-runs/{run_id}/episodes").json()
    assert before["items"][0]["acceptance"] is None

    assert _decide(client, artifact_id, sha).status_code == 200

    after = client.get(f"/api/v1/production-runs/{run_id}/episodes").json()
    assert after["items"][0]["acceptance"]["decision"] == "accepted"

    journey = client.get(f"/api/v1/projects/{project}/journey").json()
    post = next(stage for stage in journey["stages"] if stage["id"] == "post")
    assert post["done"] is True  # 交付判定不变
    assert post["facts"]["accepted_episode_count"] == 1
    assert "1 集已确认" in post["summary"]
    assert journey["run"]["accepted_episode_count"] == 1