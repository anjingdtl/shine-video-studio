"""R07: a run for one episode counts that episode, not the whole package.

The journey took its shot denominator from the confirmed package, so a run
that was started for one episode still reported two pending shots and two
production blockers.  The current Run's frozen selection is now the scope,
and the whole package stays an overview in its own field.
"""

from __future__ import annotations

from tests.test_director_long_form import LOCAL, _studio


def _two_episode_package(client, studio):
    plan = client.post(f"/api/v1/projects/{studio['project']}/episode-plans",
                       json={"target_seconds": 8, "tolerance_seconds": 6}, headers=LOCAL)
    assert plan.status_code == 201, plan.text
    episodes = plan.json()["payload"]["episodes"]
    assert len(episodes) == 2, episodes
    confirmed = client.post(
        f"/api/v1/projects/{studio['project']}/episode-plans/{plan.json()['version']}/confirm",
        json={}, headers=LOCAL)
    assert confirmed.status_code == 200, confirmed.text
    package = client.post(f"/api/v1/projects/{studio['project']}/director-packages",
                          json={"workflow_profile_id": studio["profile"]["id"],
                                "generation_mode": "Ref2VA"}, headers=LOCAL)
    assert package.status_code == 201, package.text
    ok = client.post(f"/api/v1/director-packages/{package.json()['id']}/confirm", json={}, headers=LOCAL)
    assert ok.status_code == 200, ok.text
    return package.json(), episodes


def test_a_run_for_one_episode_counts_only_that_episode(api):
    client, factory, settings = api
    studio = _studio(client, shots=2)
    package, episodes = _two_episode_package(client, studio)
    run = client.post(f"/api/v1/projects/{studio['project']}/production-runs",
                      json={"package_id": package["id"],
                            "episode_ids": [episodes[0]["episode_id"]]}, headers=LOCAL)
    assert run.status_code == 201, run.text
    assert run.json()["scope"]["shot_count"] == 1

    journey = client.get(
        f"/api/v1/projects/{studio['project']}/journey?run_id={run.json()['id']}").json()
    assert journey["scope"]["package_id"] == package["id"]
    assert journey["scope"]["shot_count"] == 1, journey["scope"]
    assert journey["package_scope"]["shot_count"] == 2
    production = next(row for row in journey["stages"] if row["id"] == "production")
    assert production["facts"]["scope_shot_count"] == 1
    assert production["facts"]["package_shot_count"] == 2
    assert [row["shot_id"] for row in production["blockers"]] == [studio["shots"][0]["id"]]


def test_an_explicit_second_episode_counts_the_second_one(api):
    client, factory, settings = api
    studio = _studio(client, shots=2)
    package, episodes = _two_episode_package(client, studio)
    run = client.post(f"/api/v1/projects/{studio['project']}/production-runs",
                      json={"package_id": package["id"],
                            "episode_ids": [episodes[1]["episode_id"]]}, headers=LOCAL)
    assert run.status_code == 201, run.text
    journey = client.get(
        f"/api/v1/projects/{studio['project']}/journey?run_id={run.json()['id']}").json()
    assert journey["scope"]["shot_count"] == 1
    production = next(row for row in journey["stages"] if row["id"] == "production")
    assert [row["shot_id"] for row in production["blockers"]] == [studio["shots"][1]["id"]]


def test_the_whole_package_scope_is_still_the_whole_package(api):
    client, factory, settings = api
    studio = _studio(client, shots=2)
    package, _episodes = _two_episode_package(client, studio)
    journey = client.get(f"/api/v1/projects/{studio['project']}/journey").json()
    assert journey["scope"]["shot_count"] == 2
    assert journey["package_scope"]["shot_count"] == 2
    production = next(row for row in journey["stages"] if row["id"] == "production")
    assert production["facts"]["scope_shot_count"] == 2
