"""A person may write and freeze one shot's H3 prompt.

The frozen text is submitted verbatim; the only refusal is a changed reference
set, because a prompt that names <Subject 1> must never receive another face.
"""

from __future__ import annotations

import pytest
from sqlalchemy import select

from shine.director.prompt_revisions import resolve_manual_prompt
from shine.h3.rulebook import H3RulebookRegistry
from shine.models import Shot
from shine.quality.revisions import revision_for_prompt
from shine.schemas import CreateJob, ShotSpec, StyleBible
from shine.services import make_render_request, manifest_from_shot_bindings
from tests.conftest import approve_lock, create_project, create_shot, freeze_style, upload_asset

pytestmark = pytest.mark.integration

LOCAL = {"X-Shine-Local-Action": "1"}

MANUAL_PROMPT = (
    "subject_definitions:\n<Subject 1> is the approved reference for the lead performer.\n\n"
    "summary:\n[reference generation] A woman waits beside a sealed letter.\n\n"
    "retention_analysis:\n<Subject 1>: fully_preserved - this exact face is retained.\n\n"
    "detailed_description:\n"
    "A woman stands beside a sealed letter on a wooden table, lit by a single window.\n\n"
    "overall_soundscape:\nQuiet room tone with rain against the glass.\n\n"
    "non_diegetic_music:\nN/A"
)


def _setup(client):
    """A shot that can really render: the project is Ref2VA, so it has a subject."""
    project_id = create_project(client, "提示词人工修订")
    style = freeze_style(client, project_id)
    shot = create_shot(client, project_id, style["id"],
                       beat="A woman waits by a table.", characters=["Lin"])
    master = upload_asset(client, project_id, role="character", entity_id="Lin")
    approve_lock(client, master["id"])
    bound = client.put(f"/api/v1/shots/{shot['id']}/bindings", headers=LOCAL, json={"bindings": [
        {"asset_id": master["id"], "use_as": "subject", "role": "Lin", "ordinal": 0}],
    })
    assert bound.status_code == 200, bound.text
    return project_id, style, shot


def test_preview_freeze_and_render_use_the_manual_text(studio):
    client, factory, _tmp, _settings = studio
    project_id, _style, shot = _setup(client)

    preview = client.get(
        f"/api/v1/projects/{project_id}/director/storyboard/shots/{shot['id']}/prompt")
    assert preview.status_code == 200, preview.text
    body = preview.json()
    assert body["mode"] == "Ref2VA"
    assert body["prompt"].strip()
    assert body["frozen"] is None

    saved = client.post(
        f"/api/v1/projects/{project_id}/director/storyboard/shots/{shot['id']}/prompt",
        headers=LOCAL,
        json={"prompt": MANUAL_PROMPT, "mode": "Ref2VA", "freeze": True})
    assert saved.status_code == 201, saved.text
    assert saved.json()["status"] == "FROZEN"

    with factory() as db:
        shot_row = db.get(Shot, shot["id"])
        project = shot_row.project
        request, _assets, meta = make_render_request(
            db, project, shot_row, CreateJob(shot_id=shot["id"]))
        assert request.manual_prompt is True
        assert request.compiled_prompt == MANUAL_PROMPT
        assert meta["manual_prompt"] is True

        registry = H3RulebookRegistry(None)
        pack = registry.ensure_builtin(db)
        registry.lock(db, project_id)
        db.commit()
        fresh = db.get(Shot, shot["id"])
        request, _assets, _meta = make_render_request(
            db, fresh.project, fresh, CreateJob(shot_id=shot["id"]))
        report = registry.validate(request, pack)
        assert report.passed is True
        assert report.violations == []
        assert isinstance(report.warnings, list)


def test_a_queued_job_carries_the_frozen_prompt_as_its_request(studio):
    client, factory, _tmp, _settings = studio
    project_id, _style, shot = _setup(client)
    saved = client.post(
        f"/api/v1/projects/{project_id}/director/storyboard/shots/{shot['id']}/prompt",
        headers=LOCAL,
        json={"prompt": MANUAL_PROMPT, "mode": "Ref2VA", "freeze": True})
    assert saved.status_code == 201, saved.text

    job = client.post(f"/api/v1/projects/{project_id}/jobs",
                      headers=LOCAL, json={"shot_id": shot["id"], "provider": "mock"})
    assert job.status_code == 201, job.text
    with factory() as db:
        from shine.models import Job

        row = db.get(Job, job.json()["id"])
        assert row.request["compiled_prompt"] == MANUAL_PROMPT
        assert row.request["manual_prompt"] is True
        assert row.compiled_prompt_sha256 == row.request["prompt_sha256"]


def test_changed_references_make_the_frozen_prompt_inactive(studio):
    """Same generation mode, different performer: the frozen text is not submitted."""
    client, factory, _tmp, _settings = studio
    project_id = create_project(client, "参考集变化")
    style = freeze_style(client, project_id)
    shot = create_shot(client, project_id, style["id"],
                       beat="Lin waits beside a sealed letter.", characters=["Lin"])

    first = upload_asset(client, project_id, role="character", entity_id="Lin")
    approve_lock(client, first["id"])
    assert client.put(f"/api/v1/shots/{shot['id']}/bindings", headers=LOCAL, json={"bindings": [
        {"asset_id": first["id"], "use_as": "subject", "role": "Lin", "ordinal": 0}],
    }).status_code == 200

    preview = client.get(
        f"/api/v1/projects/{project_id}/director/storyboard/shots/{shot['id']}/prompt")
    assert preview.status_code == 200, preview.text
    assert preview.json()["mode"] == "Ref2VA"
    saved = client.post(
        f"/api/v1/projects/{project_id}/director/storyboard/shots/{shot['id']}/prompt",
        headers=LOCAL,
        json={"prompt": MANUAL_PROMPT, "mode": "Ref2VA", "freeze": True})
    assert saved.status_code == 201, saved.text

    # Another performer becomes the subject.  Binding is the request, so the
    # shot's own declaration moves through the one revision entry; the mode
    # stays Ref2VA and the written <Subject 1> now names the wrong performer.
    second = upload_asset(client, project_id, content=b"\x89PNG\r\n\x1a\n" + b"synthetic-fixture-2",
                          filename="另一个.png", role="character", entity_id="Zhou")
    approve_lock(client, second["id"])
    with factory() as db:
        spec_payload = dict(db.get(Shot, shot["id"]).spec or {})
    spec_payload["characters"] = ["Zhou"]
    spec_payload["required_asset_ids"] = [second["id"]]
    revised = client.post(f"/api/v1/shots/{shot['id']}/revise", json=spec_payload)
    assert revised.status_code == 200, revised.text
    rebound = client.put(f"/api/v1/shots/{shot['id']}/bindings", headers=LOCAL, json={"bindings": [
        {"asset_id": second["id"], "use_as": "subject", "role": "Zhou", "ordinal": 0}],
    })
    assert rebound.status_code == 200, rebound.text

    with factory() as db:
        from shine.h3.compiler import H3PromptCompiler
        from shine.schemas import ShotSpec

        shot_row = db.get(Shot, shot["id"])
        spec = ShotSpec.model_validate(shot_row.spec)
        manifest = manifest_from_shot_bindings(db, shot_row)
        compiled = H3PromptCompiler().compile(
            spec=spec, style=_style_bible(db, project_id), manifest=manifest,
            project_language="Chinese")
        resolution = resolve_manual_prompt(
            db, shot_row, compiled=compiled, manifest=manifest, spec=spec)
        assert resolution.manual is False
        assert resolution.status == "MANUAL_PROMPT_REFERENCES_CHANGED"
        assert resolution.source == "compiler"
        # The frozen text is kept, not deleted: it becomes effective again the
        # moment the shot returns to the cast it was written for.
        assert any(row.status == "FROZEN" for row in shot_row_prompt_rows(db, shot_row.id))


def shot_row_prompt_rows(db, shot_id: str):
    from shine.director.models import ShotPromptRevision

    return db.scalars(select(ShotPromptRevision).where(
        ShotPromptRevision.shot_id == shot_id)).all()


def _style_bible(db, project_id: str):
    from shine.models import StyleVersion
    from shine.schemas import StyleBible

    row = db.scalars(select(StyleVersion).where(
        StyleVersion.project_id == project_id).order_by(StyleVersion.version.desc())).first()
    return StyleBible.model_validate(row.snapshot)


def test_automatic_repair_never_rewrites_a_manual_prompt():
    assert revision_for_prompt(
        request={"manual_prompt": True, "compiled_prompt": MANUAL_PROMPT},
        spec=ShotSpec(story_beat="A woman waits.", duration_target_s=6,
                      style_version_id="s1"),
        style=StyleBible(
            photography_language="a", shot_blocking="b", color_palette="c",
            lighting_tone="d", lens_texture="e", editing_rhythm="f", sound_performance="g"),
        manifest=[],
        root_cause="SEMANTIC_DRIFT",
        attempt_no=1,
    ) is None


def test_stale_expected_revision_is_a_named_conflict(studio):
    client, _factory, _tmp, _settings = studio
    project_id, _style, shot = _setup(client)
    response = client.post(
        f"/api/v1/projects/{project_id}/director/storyboard/shots/{shot['id']}/prompt",
        headers=LOCAL,
        json={"prompt": MANUAL_PROMPT, "mode": "Ref2VA", "freeze": False,
              "expected_revision": shot["revision"] + 5})
    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "SHOT_REVISION_CHANGED"
