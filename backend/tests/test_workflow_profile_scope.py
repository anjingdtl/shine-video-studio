"""Regression: the second project's wizard registration must yield usable profiles.

Counterexample (audit 2026-11-01, profile-project-scope.json): project A
registered the two default Ref2VA profiles; project B then clicked
"登记工作流 Profile", received ALREADY_REGISTERED with A's profile ids, and
B's own list stayed empty — B had no usable generation template from the
normal wizard path.
"""

from __future__ import annotations

import hashlib
import threading
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace

from fastapi.testclient import TestClient
from sqlalchemy import select

from shine.comfy.workflow import graph_hash
from shine.comfyops import deployment
from shine.comfyops.deployment import load_default_catalog
from shine.models import WorkflowProfile, utcnow
from tests.conftest import create_project, make_studio
from tests.test_v13_preparation import TOKEN

HEADERS = {"Authorization": "Bearer " + TOKEN}


def _prep(tmp_path):
    app, factory, settings = make_studio(tmp_path)
    settings = replace(
        settings,
        operator_id="profile-scope-operator",
        operator_token_sha256=hashlib.sha256(TOKEN.encode()).hexdigest(),
    )
    app.state.settings = settings
    client = TestClient(app)
    return client, factory, app


def _register(client: TestClient, project_id: str):
    response = client.post("/api/v1/setup/profiles", json={"project_id": project_id}, headers=HEADERS)
    assert response.status_code == 200, response.text
    return response.json()


def _names_of(client: TestClient, project_id: str) -> list[str]:
    rows = client.get(f"/api/v1/workflow-profiles?project_id={project_id}").json()
    return [row["name"] for row in rows]


def test_second_project_wizard_registration_yields_its_own_usable_profiles(tmp_path):
    """A/B/C register in order on a fresh database; each project lists its own two."""
    client, _factory, app = _prep(tmp_path)
    try:
        project_a = create_project(client, title="项目A")
        project_b = create_project(client, title="项目B")
        project_c = create_project(client, title="项目C")

        result_a = _register(client, project_a)
        result_b = _register(client, project_b)
        result_c = _register(client, project_c)

        default_names = deployment.load_default_catalog()["workflow_profiles"]
        assert [row["status"] for row in result_a["profiles"]] == ["REGISTERED"] * len(default_names)
        assert [row["status"] for row in result_b["profiles"]] == ["REGISTERED"] * len(default_names)
        assert [row["status"] for row in result_c["profiles"]] == ["REGISTERED"] * len(default_names)

        for project in (project_a, project_b, project_c):
            names = _names_of(client, project)
            assert sorted(names) == sorted(default_names)
        # The projects must not share profile rows: each has its own ids.
        ids_a = {row["id"] for row in client.get(f"/api/v1/workflow-profiles?project_id={project_a}").json()}
        ids_b = {row["id"] for row in client.get(f"/api/v1/workflow-profiles?project_id={project_b}").json()}
        ids_c = {row["id"] for row in client.get(f"/api/v1/workflow-profiles?project_id={project_c}").json()}
        assert ids_a.isdisjoint(ids_b)
        assert ids_a.isdisjoint(ids_c)
        assert ids_b.isdisjoint(ids_c)
    finally:
        app.state.engine.dispose()


def test_repeated_registration_on_the_same_project_is_idempotent(tmp_path):
    client, _factory, app = _prep(tmp_path)
    try:
        project = create_project(client, title="重复登记")
        first = _register(client, project)
        ids_first = [row["profile_id"] for row in first["profiles"]]
        second = _register(client, project)
        expected_catalog = len(load_default_catalog()["workflow_profiles"])
        assert [row["status"] for row in second["profiles"]] == ["ALREADY_REGISTERED"] * expected_catalog
        assert [row["profile_id"] for row in second["profiles"]] == ids_first

        rows = client.get(f"/api/v1/workflow-profiles?project_id={project}").json()
        assert len(rows) == expected_catalog
    finally:
        app.state.engine.dispose()


def test_same_name_with_different_content_in_the_same_project_is_rejected(tmp_path):
    client, factory, app = _prep(tmp_path)
    try:
        project_a = create_project(client, title="冲突宿主")
        project_b = create_project(client, title="冲突项目")
        _register(client, project_a)
        # Plant a same-name profile inside project B with a different graph.
        graph = {"1": {"class_type": "UnrelatedWorkflow", "inputs": {}}}
        from shine.comfy.workflow import graph_hash

        with factory.begin() as db:
            for name in deployment.load_default_catalog()["workflow_profiles"]:
                db.add(WorkflowProfile(project_id=project_b, name=name, mode="Ref2VA", graph=graph,
                                       graph_sha256=graph_hash(graph), parameter_map={}, required_nodes=[]))
        response = client.post("/api/v1/setup/profiles", json={"project_id": project_b}, headers=HEADERS)
        assert response.status_code == 409, response.text
        assert response.json()["detail"] == "PROFILE_CONTENT_CHANGED"
        # Project A's profiles are untouched by B's rejected registration.
        assert _names_of(client, project_a) == deployment.load_default_catalog()["workflow_profiles"]
    finally:
        app.state.engine.dispose()


def test_original_project_profiles_and_history_survive_later_registrations(tmp_path):
    client, factory, app = _prep(tmp_path)
    try:
        project_a = create_project(client, title="历史宿主")
        first = _register(client, project_a)
        ids_before = {row["name"]: row["profile_id"] for row in first["profiles"]}

        project_b = create_project(client, title="后注册者")
        _register(client, project_b)

        # A's profile rows keep their ids and content.
        with factory.begin() as db:
            for name, profile_id in ids_before.items():
                row = db.get(WorkflowProfile, profile_id)
                assert row is not None and row.project_id == project_a and row.name == name
        assert _names_of(client, project_a) == list(deployment.load_default_catalog()["workflow_profiles"])
    finally:
        app.state.engine.dispose()


def test_a_private_profile_cannot_be_used_by_another_project(tmp_path):
    """Production authorization still rejects cross-project profile reuse."""
    from tests.conftest import approve_lock, create_shot, freeze_style, upload_asset

    client, _factory, app = _prep(tmp_path)
    try:
        project_a = create_project(client, title="私有模板宿主")
        project_b = create_project(client, title="越权者")
        first = _register(client, project_a)
        profile_a_id = first["profiles"][0]["profile_id"]

        style = freeze_style(client, project_b)
        shot = create_shot(client, project_b, style["id"])
        asset = upload_asset(client, project_b)
        approve_lock(client, asset["id"])
        response = client.post(
            f"/api/v1/projects/{project_b}/jobs",
            json={"shot_id": shot["id"], "provider": "comfyui", "workflow_profile_id": profile_a_id},
        )
        assert response.status_code == 404, response.text
    finally:
        app.state.engine.dispose()


def test_concurrent_registrations_do_not_create_conflicting_copies(tmp_path):
    client, _factory, app = _prep(tmp_path)
    try:
        project = create_project(client, title="并发登记")
        codes: list[int] = []
        lock = threading.Lock()

        def fire():
            response = client.post("/api/v1/setup/profiles", json={"project_id": project}, headers=HEADERS)
            with lock:
                codes.append(response.status_code)

        threads = [threading.Thread(target=fire) for _ in range(4)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()

        assert set(codes) <= {200, 409}, codes
        rows = client.get(f"/api/v1/workflow-profiles?project_id={project}").json()
        assert len(rows) == len(load_default_catalog()["workflow_profiles"]), rows
    finally:
        app.state.engine.dispose()


def test_concurrent_global_creates_are_database_unique(tmp_path, monkeypatch):
    """All requests pass the precheck before inserting: exactly one may commit."""
    from shine import api_comfy
    from tests.conftest import COMFY_GRAPH, COMFY_PARAM_MAP

    client, factory, app = _prep(tmp_path)
    count = 4
    barrier = threading.Barrier(count)
    entered = threading.local()
    real_metadata = api_comfy.profile_metadata

    def synchronized_metadata(*args, **kwargs):
        result = real_metadata(*args, **kwargs)
        # Serialization derives metadata again after the winning commit.
        # Synchronize only the pre-insert call for each request thread.
        if not getattr(entered, "precheck_complete", False):
            entered.precheck_complete = True
            barrier.wait(timeout=10)
        return result

    monkeypatch.setattr(api_comfy, "profile_metadata", synchronized_metadata)
    body = {"name": "global-concurrent-profile", "mode": "T2VA", "graph": COMFY_GRAPH,
            "parameter_map": COMFY_PARAM_MAP, "required_nodes": ["H3BaseSampler"]}
    try:
        with ThreadPoolExecutor(max_workers=count) as pool:
            responses = list(pool.map(lambda _: client.post("/api/v1/workflow-profiles", json=body), range(count)))
        assert sorted(r.status_code for r in responses) == [201, 409, 409, 409]
        with factory() as db:
            rows = db.scalars(select(WorkflowProfile).where(
                WorkflowProfile.name == body["name"], WorkflowProfile.project_id.is_(None),
            )).all()
            assert len(rows) == 1
    finally:
        app.state.engine.dispose()


def test_a_project_local_profile_matches_a_published_revision_by_exact_graph_identity():
    """A new project may use a frozen published revision with its own same-graph profile."""
    from shine.comfyops.models import WorkflowRevision
    from shine.comfyops.preproduction import _profile_matches_frozen_revision

    graph = {"1": {"class_type": "H3BaseSampler", "inputs": {"prompt": ""}}}
    param_map = {"positive_prompt": "1.inputs.prompt", "duration": "1.inputs.duration"}
    transfers = {"image": {"upload_path": "/upload/image", "field_name": "image"}}
    revision = WorkflowRevision(
        id="published-revision",
        payload={"category": "H3_REF2VA", "graph": graph, "parameter_map": param_map,
                 "media_transfers": transfers},
        sha256=graph_hash({"graph": graph}), status="PRODUCTION_APPROVED",
    )
    fields = dict(mode="Ref2VA", graph=graph, graph_sha256=graph_hash(graph),
                  parameter_map=param_map, media_transfers=transfers, required_nodes=[])
    profile_b = WorkflowProfile(project_id="project-b", name="b", **fields)
    assert _profile_matches_frozen_revision(revision, profile_b, "project-b")
    assert not _profile_matches_frozen_revision(revision, profile_b, "project-c")
    assert not _profile_matches_frozen_revision(
        revision, WorkflowProfile(project_id="project-b", name="b2", **{
            **fields, "parameter_map": {**param_map, "duration": "1.inputs.other"},
        }), "project-b",
    )
    assert not _profile_matches_frozen_revision(
        revision, WorkflowProfile(project_id="project-b", name="b3", **{
            **fields, "graph_sha256": "0" * 64,
        }), "project-b",
    )
    assert not _profile_matches_frozen_revision(
        revision, WorkflowProfile(project_id="project-b", name="b4", **{
            **fields, "mode": "T2VA",
        }), "project-b",
    )


def test_old_database_copy_upgrade_preserves_profiles_and_job_references(tmp_path, monkeypatch):
    """Old DB one revision before head: upgrade keeps profile ids, content and job refs.

    The fixture starts at ``f180b0040001`` rather than a much older schema: the
    ORM can only write columns the schema actually has, so the sample sits at the
    immediate predecessor of the migration under test (``jobs.request_fingerprint``).
    """
    from alembic import command
    from shine.db import make_engine, make_session_factory
    from tests.test_alembic import alembic_config

    db_path = tmp_path / "旧库副本.db"
    url = f"sqlite:///{db_path.as_posix()}"
    monkeypatch.setenv("SHINE_DATABASE_URL", url)
    monkeypatch.setenv("SHINE_DATA_DIR", str(tmp_path))
    config = alembic_config(db_path)
    command.upgrade(config, "f180b0040001")

    engine = make_engine(url)
    factory = make_session_factory(engine)
    from shine.models import Job, Project, Shot, StyleVersion, WorkflowProfile

    with factory.begin() as db:
        project = Project(title="旧库项目")
        db.add(project)
        db.flush()
        db.add(Shot(id="legacy-shot", project_id=project.id, name="旧库镜头", spec={}))
        db.add(StyleVersion(
            id="legacy-style", project_id=project.id, version=1, snapshot={}, sha256="b" * 64,
            approved_by="legacy-migration-fixture",
        ))
        db.flush()
        graph = {"1": {"class_type": "H3BaseSampler", "inputs": {}}, "2": {"class_type": "SaveVideo", "inputs": {}}}
        digest = graph_hash(graph)
        profile_ids = []
        for name in ("local-h3-ref2va", "local-h3-t2va"):
            row = WorkflowProfile(
                project_id=project.id, name=name, mode="Ref2VA", graph=graph,
                graph_sha256=digest, parameter_map={"positive_prompt": "1.inputs.positive_prompt",
                                                    "duration": "1.inputs.duration"},
                required_nodes=["H3BaseSampler"], checkpoint_id="MiniMax-H3-Base",
            )
            db.add(row)
            db.flush()
            profile_ids.append(row.id)
        db.execute(
            Job.__table__.insert().values(
                id="legacy-job-ref", project_id=project.id, shot_id="legacy-shot", shot_revision=1,
                status="PASSED", state_version=3, provider="comfyui",
                style_version_id="legacy-style", workflow_profile_id=profile_ids[0],
                request={}, idempotency_key="legacy-job-ref-key",
                created_at=utcnow(), updated_at=utcnow(),
            )
        )
        legacy_ids = list(profile_ids)
    engine.dispose()

    command.upgrade(config, "head")

    engine = make_engine(url)
    factory = make_session_factory(engine)
    with factory.begin() as db:
        rows = list(db.scalars(select(WorkflowProfile)).all())
        assert sorted(row.name for row in rows) == ["local-h3-ref2va", "local-h3-t2va"]
        assert [row.id for row in rows if row.name == "local-h3-ref2va"] == [legacy_ids[0]]
        assert all(row.project_id == project.id for row in rows)
        job = db.get(Job, "legacy-job-ref")
        assert job.workflow_profile_id == legacy_ids[0]
        # The new namespace accepts a second project's same-name copy.
        second = Project(title="升级后第二项目")
        db.add(second)
        db.flush()
        db.add(WorkflowProfile(
            id="post-upgrade-copy", project_id=second.id, name="local-h3-ref2va", mode="Ref2VA",
            graph=rows[0].graph, graph_sha256=rows[0].graph_sha256, parameter_map=rows[0].parameter_map,
            required_nodes=["H3BaseSampler"], checkpoint_id="MiniMax-H3-Base",
        ))
    engine.dispose()
