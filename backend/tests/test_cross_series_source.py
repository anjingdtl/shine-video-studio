"""A later batch must consume its own run's passed source, never a newer take."""

from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from sqlalchemy import select

from shine.comfyops.production_series import _run_source_job
from shine.director.contracts import digest_of
from shine.models import ProductionSeries, Shot
from shine.production.models import ProductionRunBatch
from shine.production.runner import _batch_plan
from tests.test_director_long_form import _advance, _complete_dispatched_series, _seed_passed_job
from tests.test_director_long_form_regressions import start


def test_opt_in_batching_splits_a_continuity_chain_without_dropping_shots():
    shots = [
        {
            "shot_id": str(i),
            "revision": 1,
            "spec_sha256": "a",
            "bindings": [],
            "continuity_mode": "cut" if i == 0 else "last_frame",
        }
        for i in range(7)
    ]
    manifest = {"episode_id": "ep", "shots": shots}
    episode = SimpleNamespace(episode_id="ep", manifest=manifest, manifest_sha256=digest_of(manifest))
    legacy = _batch_plan(episode, batch_shot_limit=2)
    split = _batch_plan(episode, batch_shot_limit=2, split_continuity=True)
    assert [len(batch["shot_ids"]) for batch in legacy] == [7]
    assert [len(batch["shot_ids"]) for batch in split] == [2, 2, 2, 1]
    assert [shot_id for batch in split for shot_id in batch["shot_ids"]] == [str(i) for i in range(7)]


def test_source_is_from_the_owning_run_and_must_precede_target(api):
    _, factory, settings = api
    studio, _, run = start(api, shots=2, batch_limit=1)
    _advance(factory, settings, run["id"])
    _complete_dispatched_series(factory, settings, run["id"])
    _advance(factory, settings, run["id"])
    with factory() as db:
        batches = list(
            db.scalars(
                select(ProductionRunBatch)
                .where(ProductionRunBatch.run_id == run["id"])
                .order_by(ProductionRunBatch.position)
            )
        )
        series_id = batches[1].series_id
        first_id = batches[0].shot_ids[0]
        second_id = batches[1].shot_ids[0]
    unrelated = _seed_passed_job(factory, settings, studio["project"], first_id, studio["style"]["id"])
    with factory.begin() as db:
        series = db.get(ProductionSeries, series_id)
        shot = db.get(Shot, second_id)
        shot.spec = {**shot.spec, "continuity": {"mode": "last_frame", "previous_shot_id": first_id}}
        source = _run_source_job(db, series, shot)
        assert source.shot_id == first_id and source.id != unrelated
        shot.spec = {**shot.spec, "continuity": {"mode": "last_frame", "previous_shot_id": second_id}}
        with pytest.raises(HTTPException, match="CONTINUITY_RUN_SOURCE_NOT_PRIOR"):
            _run_source_job(db, series, shot)
