"""A frozen material prompt survives a re-derived requirement list.

A re-derived list opens a new plan revision with new requirement rows.  The
person's own words must travel with the item they wrote them for; otherwise a
second derivation silently resets them to the model's description.
"""

from __future__ import annotations

import pytest

from tests.test_director_streamline import (
    LOCAL,
    _confirm_book,
    _prepare_project,
    _run_storyboard_to_rest,
    _storyboard_book,
)

pytestmark = pytest.mark.integration

PROMPT = (
    "A character reference photograph for a cinematic narrative production: one person, "
    "framed from the waist up, standing against a plain neutral grey-white studio background. "
    "The subject is an old watchmaker in his fifties wearing a deep navy wool coat. "
    "The overall composition is calm and symmetrical, holding the face as the single focus."
)


def _episode_one_requirements(client, settings):
    prepared = _prepare_project(client, sections=2)
    project = prepared["project"]
    book = _storyboard_book(client, project, episode_count=1, target_seconds=60)
    _confirm_book(client, project, book["plan_version"], book["plan_sha256"])
    client.post(f"/api/v1/projects/{project}/director/storyboard/generate", json={},
                headers=LOCAL)
    _run_storyboard_to_rest(client, settings, project)
    client.post(f"/api/v1/projects/{project}/director/storyboard/episodes/ep-01/confirm",
                json={}, headers=LOCAL)
    derived = client.post(f"/api/v1/projects/{project}/director/materials/derive",
                          json={"episode_ids": ["ep-01"]}, headers=LOCAL)
    assert derived.status_code == 200, derived.text
    return project, derived.json()


def test_a_frozen_prompt_is_still_there_after_the_list_is_derived_again(api):
    client, factory, settings = api
    project, derived = _episode_one_requirements(client, settings)
    character = next(row for row in derived["requirements"] if row["family"] == "character")
    base = (f"/api/v1/projects/{project}/director/materials/requirements/"
            f"{character['requirement_id']}/prompt")

    frozen = client.put(base, headers=LOCAL, json={"prompt": PROMPT, "negative_prompt": ""})
    assert frozen.status_code == 200, frozen.text

    # A new requirement in a shot changes the derivation, so the next list is a
    # new plan revision with new requirement rows: exactly the case where a
    # frozen prompt would be dropped if it were not carried over.
    shots = client.get(
        f"/api/v1/projects/{project}/director/storyboard/episodes/ep-01/shots")
    assert shots.status_code == 200, shots.text
    shot = shots.json()["shots"][0]
    added = client.post(
        f"/api/v1/projects/{project}/director/storyboard/shots/{shot['shot_id']}/requested-assets",
        headers=LOCAL,
        json={"family": "prop", "name": "铜怀表", "description": "停在十二点的怀表"})
    assert added.status_code == 201, added.text

    again = client.post(f"/api/v1/projects/{project}/director/materials/derive",
                        json={"episode_ids": ["ep-01"]}, headers=LOCAL)
    assert again.status_code == 200, again.text
    body = again.json()
    assert body["plan_revision_id"] != derived["plan_revision_id"], \
        "the second derivation must open a new plan revision"
    rows = body["requirements"]
    carried = next(row for row in rows if row["family"] == "character")
    assert carried["requirement_id"] != character["requirement_id"], \
        "a new plan revision has new requirement rows"
    assert carried["manual_prompt"] is not None
    assert carried["manual_prompt"]["chars"] == len(PROMPT)

    read_back = client.get(
        f"/api/v1/projects/{project}/director/materials/requirements/"
        f"{carried['requirement_id']}/prompt")
    assert read_back.status_code == 200, read_back.text
    assert read_back.json()["source"] == "frozen"
    assert read_back.json()["prompt"] == PROMPT
