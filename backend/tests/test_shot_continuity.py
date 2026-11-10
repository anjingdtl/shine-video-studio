from __future__ import annotations

from tests.conftest import create_project, create_shot, freeze_style

# 本地操作者的动作要带这一张凭据，界面里的写请求会自动带上同一个头。
LOCAL = {"X-Shine-Local-Action": "1"}


def _project_with_shots(client, count: int):
    project_id = create_project(client)
    style = freeze_style(client, project_id)
    shots = [
        create_shot(client, project_id, style["id"], title=f"镜头{index + 1}")
        for index in range(count)
    ]
    return project_id, shots


def test_first_shot_cannot_continue_a_take_that_does_not_exist(studio):
    client, _factory, _root, _settings = studio
    project_id, shots = _project_with_shots(client, 2)

    response = client.post(f"/api/v1/shots/{shots[0]['id']}/continuity", json={"mode": "last_frame"},
                         headers=LOCAL)

    assert response.status_code == 422, response.text
    assert response.json()["detail"]["code"] == "SHOT_CONTINUITY_NEEDS_PREDECESSOR"
    # The refusal leaves the shot exactly as it was.
    listed = client.get(f"/api/v1/projects/{project_id}/shots").json()
    assert listed[0]["spec"]["continuity"]["mode"] == "cut"


def test_a_boundary_is_declared_without_revising_the_shot(studio):
    """The switch belongs to the cut, not to the take.

    Flipping it must not bump the revision: a shot that already passed stays a
    valid take, and what changes is only what the *next* shot is handed.
    """
    client, _factory, _root, _settings = studio
    _project_id, shots = _project_with_shots(client, 2)
    second = shots[1]

    response = client.post(f"/api/v1/shots/{second['id']}/continuity", json={"mode": "last_frame"},
                         headers=LOCAL)

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["spec"]["continuity"]["mode"] == "last_frame"
    # The boundary names the take it continues, so a cut that starts mid-scene
    # (a rerun of the last five shots) can still find it.
    assert body["spec"]["continuity"]["previous_shot_id"] == shots[0]["id"]
    assert body["revision"] == second["revision"]

    # Switching back is just as cheap, and leaves no stale source behind.
    back = client.post(f"/api/v1/shots/{second['id']}/continuity", json={"mode": "cut"},
                       headers=LOCAL)
    assert back.status_code == 200
    assert back.json()["spec"]["continuity"]["mode"] == "cut"
    assert back.json()["spec"]["continuity"]["previous_shot_id"] is None


def test_a_range_applies_one_boundary_and_keeps_its_first_shot(studio):
    """One request for a whole cut, and the range's first shot keeps its own.

    Storyboard materialisation writes every shot in one transaction, so their
    timestamps tie.  The order must still be the story order, otherwise the
    "previous" shot of a tie is whatever the database happens to return.
    """
    client, _factory, _root, _settings = studio
    project_id, shots = _project_with_shots(client, 4)

    response = client.post(
        f"/api/v1/projects/{project_id}/shots/continuity",
        json={"shot_ids": [shot["id"] for shot in shots], "mode": "last_frame"},
        headers=LOCAL,
    )

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["skipped_first"] == [shots[0]["id"]]
    assert body["updated"] == [shots[1]["id"], shots[2]["id"], shots[3]["id"]]

    listed = client.get(f"/api/v1/projects/{project_id}/shots").json()
    assert [shot["spec"]["continuity"]["mode"] for shot in listed] == [
        "cut", "last_frame", "last_frame", "last_frame",
    ]
