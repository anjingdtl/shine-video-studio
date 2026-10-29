"""Contract tests for deterministic "newest row" selection.

The host wall clock does not advance inside one tick, so two rows written
milliseconds apart can carry the same timestamp. Every selection that means
"the newest row" must still resolve deterministically, otherwise a pre-
production plan, a frozen manifest or an audit page can point at the wrong
record.
"""

from datetime import timedelta

import pytest

from shine.comfyops.models import PreProductionPlan
from shine.comfyops.preproduction import current_plan
from shine.models import utcnow
from tests.conftest import create_project
from tests.test_v13_preparation import HEADERS, post, prep, workflow  # noqa: F401


def test_clock_reads_are_strictly_increasing():
    """Consecutive reads inside one host tick must not repeat a timestamp."""
    readings = [utcnow() for _ in range(500)]
    assert all(a < b for a, b in zip(readings, readings[1:], strict=False))
    assert readings[-1] - readings[0] < timedelta(seconds=5)


def test_clock_never_runs_far_ahead_of_the_wall_clock():
    """The monotonic guard must not pin the recorded time in the future."""
    from datetime import UTC, datetime

    first = utcnow()
    wall = datetime.now(UTC).replace(tzinfo=None)
    assert first >= wall - timedelta(seconds=1)
    assert first <= wall + timedelta(seconds=1)


def test_two_plans_written_in_one_tick_resolve_to_the_newest(prep):  # noqa: F811
    """Two plans with an identical timestamp must still order by insertion."""
    client, factory, _settings = prep
    project = create_project(client)
    stamp = utcnow()
    with factory.begin() as db:
        first = PreProductionPlan(project_id=project, payload={"a": 1}, sha256="a" * 64,
                                  status="ANALYZED", created_at=stamp)
        db.add(first)
        db.flush()
        second = PreProductionPlan(project_id=project, payload={"b": 2}, sha256="b" * 64,
                                   status="ANALYZED", created_at=stamp)
        db.add(second)
        db.flush()
        assert first.id != second.id
        assert current_plan(db, project).id == second.id


def test_the_newest_plan_wins_even_when_the_clock_does_not_move(prep, monkeypatch):  # noqa: F811
    """A repeated analyze must not be able to displace the newest plan."""
    client, factory, _settings = prep
    project = create_project(client)
    document = client.post(f"/api/v1/projects/{project}/documents",
                           files={"file": ("t.txt", b"A: hello", "text/plain")}).json()
    workflow(client, category="VLM_REVIEW")
    basic = workflow(client, category="VLM_REVIEW")
    from shine.comfy.workflow import graph_hash
    from shine.comfyops.models import WorkflowRevision

    with factory.begin() as db:
        for identity, scope in ((basic["id"], "basic_character"),):
            row = db.get(WorkflowRevision, identity)
            row.status = "PRODUCTION_APPROVED"
            row.payload = {**row.payload, "native_provider": {"review_scope": scope}}
            row.sha256 = graph_hash(row.payload)
    monkeypatch.setattr("shine.comfyops.native.native_errors", lambda *args, **kwargs: [])
    first = post(client, f"/projects/{project}/preproduction/analyze",
                 {"document_id": document["id"]})
    second = post(client, f"/projects/{project}/preproduction/analyze",
                  {"document_id": document["id"], "review_scope": "basic"})
    assert second["id"] != first["id"]
    for _ in range(20):
        report = post(client, f"/projects/{project}/preproduction/readiness", {"offline": False})
        assert report["manifest"]["review_scope"] == "basic"
        assert [p["id"] for p in report["manifest"]["workflows"]] == [basic["id"]]


@pytest.mark.parametrize("repeat", [1, 2, 3])
def test_repeated_analysis_keeps_one_plan_per_scope(prep, repeat):  # noqa: F811
    """An identical analyze is idempotent and never creates a second plan."""
    client, factory, _settings = prep
    project = create_project(client)
    document = client.post(f"/api/v1/projects/{project}/documents",
                           files={"file": ("t.txt", b"A: hello", "text/plain")}).json()
    ids = []
    for _ in range(repeat + 1):
        ids.append(post(client, f"/projects/{project}/preproduction/analyze",
                         {"document_id": document["id"]})["id"])
    assert len(set(ids)) == 1
    with factory() as db:
        rows = db.query(PreProductionPlan).filter(PreProductionPlan.project_id == project).all()
        assert len(rows) == 1
        assert current_plan(db, project).id == ids[0]
