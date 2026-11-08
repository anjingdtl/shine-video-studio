"""Browser acceptance for the four-stage pipeline on an isolated database.

The script seeds a synthetic long novel through the real pipeline code
paths (plan -> outline -> blueprint -> scene scripts -> storyboard -> apply
-> episode contracts -> delivery) with a role aware mock model, then serves
the workbench so a real browser can drive it.

It never touches the user database and it never claims a real model call:
the model is a local mock, and the render provider is not involved.
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
BACKEND = REPO / "backend"


def _seed(data_dir: Path) -> dict:
    sys.path.insert(0, str(BACKEND))
    os.environ["SHINE_DATA_DIR"] = str(data_dir)
    os.environ.setdefault("SHINE_RENDER_PROVIDER", "mock")
    from fastapi.testclient import TestClient

    from shine.config import Settings
    from shine.director import pipeline
    from shine.llm.gateway import LLMGateway
    from shine.llm.secrets import SecretStore
    from shine.main import create_app
    from shine.models import AgentArtifact, AgentTaskRecord

    from tests.conftest import create_project
    from tests.test_film_crew import prepare as crew_prepare
    from tests.test_pipeline import _make_doc, _outline_artifact, _sections_for

    settings = Settings.from_env()
    app = create_app(settings=settings)

    bible_payload = {
        "schema_version": "1.0", "mainline": "渔村少年的成长", "characters": [],
        "world": "", "timeline": [], "foreshadowing": [],
        "locked_facts": ["Lin is the keeper of the harbour"],
        "open_questions": [], "ending_direction": "Homecoming",
    }

    def scene_responder(scene_text: str = "Lin：你来了。"):
        def answer(messages, schema):
            return {
                "scene_key": "S1", "section_id": "sec-1", "title": "出海",
                "text": scene_text, "characters": ["Lin"],
                "dialogue": [{"speaker": "Lin", "line": "Lin：你来了。"}],
                "estimated_seconds": 30,
                "source_refs": {}, "continuity_in": "Lin 在码头",
                "continuity_out": "Lin 登船离开", "questions": [],
            }
        return answer

    with TestClient(app) as client:
        project_id, _style, _profile = crew_prepare(client)
        assets = client.get(f"/api/v1/projects/{project_id}/assets").json()
        lin = next(a for a in assets if a["role"] == "character" and a["entity_id"] == "Lin")
        from tests.test_storyboard import _responder as chain_responder_factory
        for role in ("four_stage_bible_run", "four_stage_scene_script_run"):
            profile = client.post("/api/v1/provider-profiles", json={
                "name": f"browser-{role}", "kind": "mock", "model": "mm"})
            profile.raise_for_status()
            routed = client.put("/api/v1/agent-routes", json={
                "role": role, "provider_profile_id": profile.json()["id"]})
            routed.raise_for_status()

        original = chain_responder_factory({"lin_asset": lin["id"]})

        def chain_responder(messages, schema):
            value = original(messages, schema)
            if (schema or {}).get("title") == "ScriptBreakdown":
                context = json.JSONDecoder().raw_decode(messages[1].content)[0]
                value["locked_story_facts"] = context.get("locked_story_facts", [])
            return value

        def responder(messages, schema):
            title = (schema or {}).get("title") or ""
            if "GlobalStoryBible" in title:
                return dict(bible_payload)
            if "SceneScript" in title:
                return scene_responder()(messages, schema)
            return chain_responder(messages, schema)

        app.state.mock_llm_responder = responder
        # Six chapters of ~20k characters: three volumes of two scenes each,
        # so every volume really carries two episode contracts.
        text = "\n".join(f"第{index}章 章名{index}\nLin：你来了。\n" + "浪。" * 9900
                          for index in range(1, 7))
        doc = _make_doc(app.state.session_factory, project_id, text)
        with app.state.session_factory.begin() as db:
            from shine.models import ProjectDocument
            doc_row = db.get(ProjectDocument, doc.id)
            plan = pipeline.plan_volumes(db, project_id, doc_row)
            pipeline.confirm_volume_plan(db, project_id, plan.id, plan.sha256)
            pipeline.materialize_volume_documents(db, project_id, plan.id)
            volumes = plan.payload_json["volumes"]
            for volume in volumes:
                volume_doc = pipeline._volume_document(db, project_id, volume["volume_id"])
                _outline_artifact(db, project_id, volume_doc, _sections_for(volume_doc.text))
            bible = None
        gateway = LLMGateway(SecretStore(settings.secret_store_dir),
                             mock_responder=responder)
        with app.state.session_factory.begin() as db:
            task = pipeline.queue_bible(db, project_id, gateway)
        for _ in range(4):
            if not pipeline.run_pending_pipeline_task(app.state.session_factory, settings,
                                                      "w-browser", gateway):
                break
        with app.state.session_factory.begin() as db:
            row = db.get(AgentTaskRecord, task.id)
            if "artifact_id" not in (row.trace or {}):
                raise SystemExit(f"the bible task did not answer: {row.trace}")
            artifact = db.get(AgentArtifact, row.trace["artifact_id"])
            pipeline.confirm_bible(db, project_id, artifact.id, artifact.sha256)
        for volume in volumes:
            with app.state.session_factory.begin() as db:
                tasks = pipeline.queue_scene_scripts(db, project_id, volume["volume_id"], gateway)
            for _ in range(8):
                if not pipeline.run_pending_pipeline_task(app.state.session_factory, settings,
                                                          "w-browser", gateway):
                    break
            with app.state.session_factory.begin() as db:
                rows = db.scalars(__import__("sqlalchemy").select(AgentTaskRecord).where(
                    AgentTaskRecord.project_id == project_id,
                    AgentTaskRecord.role_id == pipeline.SCENE_SCRIPT_RUN_ROLE,
                )).all()
                for row in rows:
                    if row.status != "SUCCEEDED":
                        continue
                    artifact = db.get(AgentArtifact, row.trace["artifact_id"])
                    pipeline.confirm_scene_script(db, project_id, artifact.id, artifact.sha256)
        # the storyboard draft of the first volume, applied scene by scene
        first = volumes[0]["volume_id"]
        draft = client.post(f"/api/v1/projects/{project_id}/pipeline/volumes/{first}/storyboard-draft",
                            json={})
        draft.raise_for_status()
        body = draft.json()
        for unit in body["units"]:
            if unit["status"] != "PROPOSED":
                continue
            applied = client.post(
                f"/api/v1/projects/{project_id}/film-artifacts/{unit['camera_artifact_id']}/apply-camera",
                json={"continuity_artifact_id": unit["continuity_artifact_id"]},
                headers={"x-shine-local-action": "1"})
            applied.raise_for_status()
        # two episode contracts over the first volume
        with app.state.session_factory.begin() as db:
            outline = pipeline._volume_outline(db, project_id, first)
            sections = [s["production_section_id"]
                        for s in (outline.payload_json.get("production_sections") or [])]
        if sections:
            for title, part in (("第一集", sections[:1]), ("第二集", sections[1:] or sections[:1])):
                drafted = client.post(
                    f"/api/v1/projects/{project_id}/pipeline/volumes/{first}/episode-scripts",
                    json={"title": title, "section_ids": part},
                    headers={"x-shine-local-action": "1"})
                drafted.raise_for_status()
                client.post(
                    f"/api/v1/projects/{project_id}/pipeline/episode-scripts/"
                    f"{drafted.json()['artifact_id']}/confirm",
                    json={"expected_sha256": drafted.json()["sha256"]},
                    headers={"x-shine-local-action": "1"})
        from tests.conftest import register_workflow_profile
        profile_id = register_workflow_profile(client)["id"]
        handoff = client.post(
            f"/api/v1/projects/{project_id}/pipeline/volumes/{first}/handoff",
            json={"workflow_profile_id": profile_id},
            headers={"x-shine-local-action": "1"})
        if handoff.status_code != 201:
            raise SystemExit(f"the handoff was refused: {handoff.status_code} {handoff.text}")
        handoff = handoff.json()
    return {
        "project_id": project_id,
        "volume_ids": [volume["volume_id"] for volume in volumes],
        "volume_titles": [volume["title"] for volume in volumes],
        "package_id": handoff.get("package_id"),
        "episodes": handoff.get("episodes"),
        "shots": handoff.get("shots"),
    }


def _migrate(data_dir: Path) -> None:
    env = {**os.environ, "SHINE_DATA_DIR": str(data_dir)}
    subprocess.run([sys.executable, "-m", "alembic", "-c", str(BACKEND / "alembic.ini"),
                    "upgrade", "head"], cwd=str(BACKEND), env=env, check=True)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--data-dir", default=str(REPO / ".local-acceptance" / "four-stage-browser"))
    parser.add_argument("--port", type=int, default=8129)
    parser.add_argument("--seed-only", action="store_true")
    parser.add_argument("--serve-only", action="store_true")
    args = parser.parse_args()
    data_dir = Path(args.data_dir).resolve()
    data_dir.mkdir(parents=True, exist_ok=True)
    os.environ["SHINE_DATA_DIR"] = str(data_dir)
    os.environ.setdefault("SHINE_RENDER_PROVIDER", "mock")
    state = {}
    state_file = data_dir / "browser-journey.json"
    if not args.serve_only:
        _migrate(data_dir)
        state = _seed(data_dir)
        state_file.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")
        print(json.dumps(state, ensure_ascii=False))
    if args.seed_only:
        return 0
    if state_file.exists():
        state = json.loads(state_file.read_text(encoding="utf-8"))
    import uvicorn
    from shine.config import Settings
    from shine.main import create_app
    app = create_app(settings=Settings.from_env())
    uvicorn.run(app, host="127.0.0.1", port=args.port, log_level="warning")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
