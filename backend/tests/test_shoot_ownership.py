"""R05: one authoritative answer to "who is shooting this shot right now?".

The production series refused a shot that was already being produced, and the
single-shot Job entry did not: a series that had queued the first shot could
be joined by a second, independent Job for the very same shot.  Both entries
now read one helper, and a shot a series reserved - before its job exists -
counts as taken.
"""

from __future__ import annotations

from sqlalchemy import select

from shine.models import Job, ProductionSeries
from tests.conftest import (
    PNG,
    approve_lock,
    create_project,
    create_shot,
    freeze_style,
    register_workflow_profile,
    upload_asset,
)

LOCAL = {"X-Shine-Local-Action": "1"}


def _studio(client, *, shots: int = 1):
    project = create_project(client, "拍摄归属")
    style = freeze_style(client, project)
    subject = upload_asset(client, project, content=PNG + b"-subject", filename="角色.png",
                           role="character", entity_id="Lin")
    approve_lock(client, subject["id"])
    created = [create_shot(
        client, project, style["id"], title=f"镜头{index + 1}", characters=["Lin"],
        asset_ids=[subject["id"]],
        bindings=[{"asset_id": subject["id"], "use_as": "subject",
                   "role": "Lin", "ordinal": 0}],
    ) for index in range(shots)]
    profile = register_workflow_profile(client, mode="Ref2VA")
    return {"project": project, "style": style, "shots": created, "subject": subject,
            "profile": profile}


def _series(client, studio, shot_ids=None, **body):
    payload = {
        "shot_ids": shot_ids or [shot["id"] for shot in studio["shots"]],
        "provider": "comfyui",
        "workflow_profile_id": studio["profile"]["id"],
        **body,
    }
    return client.post(f"/api/v1/projects/{studio['project']}/production-series",
                        headers=LOCAL, json=payload)


def _job(client, studio, shot_id, **body):
    payload = {"shot_id": shot_id, "provider": "comfyui",
               "workflow_profile_id": studio["profile"]["id"], **body}
    return client.post(f"/api/v1/projects/{studio['project']}/jobs", headers=LOCAL, json=payload)


def test_a_series_owns_its_first_shot(api):
    """Series -> Job: the second entry is pointed at the task that owns it."""
    client, factory, _settings = api
    studio = _studio(client)
    shot = studio["shots"][0]
    series = _series(client, studio)
    assert series.status_code == 201, series.text

    refused = _job(client, studio, shot["id"], request_key="independent-1")
    assert refused.status_code == 409, refused.text
    detail = refused.json()["detail"]
    assert detail["code"] == "SHOT_ALREADY_IN_PRODUCTION"
    assert detail["series_id"] == series.json()["id"], detail

    with factory() as db:
        assert len(db.scalars(select(Job).where(Job.project_id == studio["project"])).all()) == 1


def test_a_live_job_owns_its_shot_against_a_new_series(api):
    """Job -> Series: the series refuses and names the shot."""
    client, factory, _settings = api
    studio = _studio(client)
    shot = studio["shots"][0]
    queued = _job(client, studio, shot["id"], request_key="first-take")
    assert queued.status_code == 201, queued.text

    refused = _series(client, studio)
    assert refused.status_code == 409, refused.text
    detail = refused.json()["detail"]
    assert detail["code"] == "PRODUCTION_SERIES_SHOT_ALREADY_QUEUED"
    assert detail["shot_ids"] == [shot["id"]], detail
    with factory() as db:
        assert db.scalars(select(ProductionSeries).where(
            ProductionSeries.project_id == studio["project"])).all() == []


def test_two_independent_requests_produce_one_shoot(api):
    """Concurrent requests: the second one never becomes a second shoot."""
    client, factory, _settings = api
    studio = _studio(client)
    shot = studio["shots"][0]
    first = _job(client, studio, shot["id"], request_key="a")
    assert first.status_code == 201, first.text
    second = _job(client, studio, shot["id"], request_key="b")
    assert second.status_code == 409, second.text
    with factory() as db:
        assert len(db.scalars(select(Job).where(Job.project_id == studio["project"])).all()) == 1


def test_the_same_request_replays_its_receipt(api):
    """A lost response: the same request identity finds the same job."""
    client, factory, _settings = api
    studio = _studio(client)
    shot = studio["shots"][0]
    first = _job(client, studio, shot["id"], request_key="same")
    assert first.status_code == 201, first.text
    again = _job(client, studio, shot["id"], request_key="same")
    assert again.status_code == 201, again.text
    assert again.json()["id"] == first.json()["id"]
    assert again.json()["replayed"] is True
    with factory() as db:
        assert len(db.scalars(select(Job).where(Job.project_id == studio["project"])).all()) == 1


def test_the_same_series_request_replays_its_series(api):
    client, factory, _settings = api
    studio = _studio(client)
    first = _series(client, studio, request_key="scope-1")
    assert first.status_code == 201, first.text
    again = _series(client, studio, request_key="scope-1")
    assert again.status_code == 201, again.text
    assert again.json()["id"] == first.json()["id"]
    with factory() as db:
        assert len(db.scalars(select(ProductionSeries).where(
            ProductionSeries.project_id == studio["project"])).all()) == 1


def test_another_shot_may_be_shot_at_the_same_time(api):
    client, factory, _settings = api
    studio = _studio(client, shots=2)
    first, second = studio["shots"]
    queued = _job(client, studio, first["id"], request_key="a")
    assert queued.status_code == 201, queued.text
    other = _job(client, studio, second["id"], request_key="b")
    assert other.status_code == 201, other.text
    assert other.json()["id"] != queued.json()["id"]
