from __future__ import annotations

from types import SimpleNamespace

import pytest

from shine.comfyops.models import (
    PLAN_PURPOSE_ASSET_PREPARATION,
    AssetRequirement,
    CandidateAsset,
    PreProductionPlan,
)
from shine.copilot import chat_investigator, objective_verify, read_tools, task_grant, tool_loop, tool_registry
from shine.copilot.material_review import REVIEW_REQUIRED
from shine.copilot.params import ParamError, routed_kwargs, validate_params
from shine.llm.gateway import LLMGateway
from shine.llm.secrets import SecretStore
from shine.llm.tool_calls import fallback_system_prompt, openai_tools
from shine.materialprep.models import MaterialImageAttempt, MaterialItem, MaterialRun
from shine.materialprep.runtime import ImageRuntimeConfig, save_config
from shine.models import AgentRoute, MaterialTextConsent, ProviderProfile, new_id
from shine.support import actions, grants
from shine.support.models import SupportAction
from tests.conftest import create_project
from tests.test_copilot_grant_durable_v31 import HEADERS, ScriptedGateway, _session_row

pytestmark = pytest.mark.integration


def test_image_family_choices_apply_to_items_in_the_tool_and_executor():
    spec, = tool_registry.tool_specs(["image_runtime_validate"])
    families = spec.parameters["properties"]["families"]
    assert families["items"] == {"type": "string", "enum": ["character", "scene", "prop"]}
    assert "enum" not in families
    assert families["maxItems"] == 3
    params = validate_params("image_runtime_validate", {"project_id": "p", "families": ["scene"]})
    assert params["families"] == ["scene"]
    with pytest.raises(ParamError):
        validate_params("image_runtime_validate", {"project_id": "p", "families": ["other"]})


def test_largest_grant_menu_preserves_read_tools_and_writes():
    tools = [*tool_registry.tool_specs(tool_registry.AUTONOMOUS_CAPABILITIES), *read_tools.read_tool_specs()]
    native_names = {entry["function"]["name"] for entry in openai_tools(tools)}
    fallback = fallback_system_prompt(tools)
    assert native_names == {tool.name for tool in tools}
    assert all(f"- {tool.name}:" in fallback for tool in tools)


def test_budget_preset_overrides_and_hard_ceilings():
    assert task_grant.normalize_limits(None) == task_grant.DEFAULT_LIMITS
    full = task_grant.normalize_limits({"max_gpu_minutes": 420}, budget_preset="full_production")
    assert full["max_gpu_minutes"] == 420
    assert full["max_llm_tool_calls"] == 180
    assert full["max_test_inference_jobs"] == 8
    assert full["max_total_runtime_seconds"] == 86400
    for key, ceiling in task_grant.LIMIT_CEILINGS.items():
        assert task_grant.normalize_limits({key: ceiling})[key] == ceiling
        with pytest.raises(task_grant.GrantError, match="超过系统允许") as raised:
            task_grant.normalize_limits({key: ceiling + 1})
        assert raised.value.code == "LIMIT_ABOVE_CEILING"


@pytest.mark.parametrize(("fields", "expected_error"), [
    ({"budget_preset": "full_production", "pause_before_production": False}, None),
    ({"budget_preset": "unlimited"}, "UNKNOWN_BUDGET_PRESET"),
    ({"pause_before_production": "false"}, "INVALID_MATERIAL_REVIEW_OPTION"),
    ({"pause_before_production": 0}, "INVALID_MATERIAL_REVIEW_OPTION"),
    ({"limits": {"max_gpu_minutes": 721}}, "INVALID_GRANT_PROPOSAL"),
])
def test_chat_grant_options_are_typed(fields, expected_error):
    proposal = chat_investigator._validate_grant_proposal(
        None, session=SimpleNamespace(project_id="project"),
        arguments={"objective": "将本集拍完并交付", **fields},
    )
    assert proposal["valid"] is (expected_error is None)
    assert proposal["error"] == expected_error
    if not expected_error:
        assert proposal["budget_preset"] == "full_production"
        assert proposal["pause_before_production"] is False


def test_preview_and_authorize_bind_budget_and_review_policy(studio):
    client, factory, _, _ = studio
    project_id = create_project(client, "授权边界")
    session = _session_row(factory, project_id=project_id)
    payload = {"session_id": session.id, "objective": "完成材料并交付", "capability_groups": ["inspect", "production"]}
    url = "/api/v1/support/task-grants"
    invalid = client.post(f"{url}/preview", json={**payload, "allow_gpu": False, "limits": {"max_gpu_minutes": 721}}, headers=HEADERS)
    assert invalid.json()["detail"]["code"] == "LIMIT_ABOVE_CEILING"
    standard = client.post(f"{url}/preview", json=payload, headers=HEADERS).json()
    full = client.post(f"{url}/preview", json={**payload, "budget_preset": "full_production"}, headers=HEADERS).json()
    skip = client.post(f"{url}/preview", json={
        **payload, "budget_preset": "full_production", "pause_before_production": False,
    }, headers=HEADERS).json()
    assert standard["limits"]["max_gpu_minutes"] == 90 and standard["pause_before_production"] is True
    assert full["limits"]["max_gpu_minutes"] == 360 and full["pause_before_production"] is True
    assert len({p["grant_digest"] for p in (standard, full, skip)}) == 3
    authorize = {"session_id": session.id, "preview_id": skip["preview_id"], "confirm": True, "run": False}
    refused = client.post(f"{url}/authorize", json={**authorize, "grant_digest": full["grant_digest"]}, headers=HEADERS)
    assert refused.status_code == 409
    result = client.post(f"{url}/authorize", json={**authorize, "grant_digest": skip["grant_digest"]}, headers=HEADERS)
    assert result.status_code == 200, result.text
    assert result.json()["pause_before_production"] is False
    assert result.json()["budget_preset"] == "full_production"


def _candidate(db, plan, requirement, revision, *, status="COMPLETED"):
    run = MaterialRun(project_id=plan.project_id, plan_id=plan.id, confirmation_id="confirmation",
                      confirmation_hash="a" * 64, idempotency_key=new_id(), request_hash="b" * 64,
                      mode="real", status=status, payload={"kind": "production"}, sha256="c" * 64)
    db.add(run)
    db.flush()
    item = MaterialItem(run_id=run.id, project_id=plan.project_id, requirement_id=requirement.id,
                        generation_revision=revision, status="CANDIDATE_READY", sha256="d" * 64)
    asset = CandidateAsset(project_id=plan.project_id, status="READY_FOR_REVIEW",
                           payload={"content_sha256": "e" * 64}, sha256="f" * 64)
    db.add_all([item, asset])
    db.flush()
    db.add(MaterialImageAttempt(item_id=item.id, run_id=run.id, project_id=plan.project_id,
                                status="CANDIDATE_READY", candidate_id=asset.id, sha256="a" * 64))
    db.commit()
    return run, asset


@pytest.mark.parametrize("pause", [True, False])
def test_material_readback_pauses_before_any_new_model_call_and_resumes_same_grant(studio, pause):
    client, factory, _, settings = studio
    project_id = create_project(client, "素材审片合同")
    session = _session_row(factory, project_id=project_id)
    with factory() as db:
        plan = PreProductionPlan(project_id=project_id, status="CONFIRMED",
                                 purpose=PLAN_PURPOSE_ASSET_PREPARATION, sha256="1" * 64)
        db.add(plan)
        db.flush()
        requirement = AssetRequirement(project_id=project_id, plan_id=plan.id, status="CONFIRMED",
                                       payload={"family": "character", "entity_id": "宁岚"}, sha256="2" * 64)
        db.add(requirement)
        db.flush()
        run, asset = _candidate(db, plan, requirement, 1, status="RUNNING")
        loaded, _ = grants.create(db, session=session, actor=session.actor, objective="继续备料和生产",
                                  groups=["inspect", "materials", "production", "test"], pause_before_production=pause)
        action = SupportAction(session_id=session.id, project_id=project_id, kind="material_generate",
                               grant_id=loaded.id, status="DELEGATED", request_key="test-material-step",
                               request_identity="test-material-step:1", preview_hash="a" * 64,
                               snapshot_revision="b" * 64, original_task_id=run.id)
        db.add(action)
        db.commit()
        grant_id, plan_id, requirement_id, candidate_id = loaded.id, plan.id, requirement.id, asset.id
        grants.save_loop(db, grant_id, {"pending_action_id": action.id})
        grants.set_status(db, grant_id, "WAITING_EXECUTOR")
        gateway = ScriptedGateway([])
        call = {"grant_id": grant_id, "objective": loaded.grant["objective"], "owner": "worker:test",
                "session_id": session.id, "gateway": gateway, "tools": tool_registry.grant_tool_specs(loaded.grant)}
        pending = tool_loop.advance(db, settings, **call)
        assert pending.status == "WAITING_EXECUTOR"
        run.status = "COMPLETED"
        db.commit()
        completed = tool_loop.advance(db, settings, **call)
        assert gateway.tool_turns == 0
        assert completed.status == ("PAUSED_USER_DECISION" if pause else "PAUSED_BUDGET")
        assert completed.stop_reason == (REVIEW_REQUIRED if pause else "STEP_BUDGET_EXHAUSTED")

    url = f"/api/v1/support/task-grants/{grant_id}"
    projection = client.get(url).json()
    assert projection["pending_action_id"] is None
    assert projection["steps"][-1]["satisfied"] is True
    if not pause:
        assert projection["status"] == "RUNNING" and projection["material_review"] is None
        return
    review = projection["material_review"]
    assert review["candidates"][0]["candidate_id"] == candidate_id
    resumed = {"session_id": session.id, "material_review_digest": review["digest"], "run_in_background": True}
    with factory() as db:
        _, replacement = _candidate(db, db.get(PreProductionPlan, plan_id), db.get(AssetRequirement, requirement_id), 2)
        replacement_id = replacement.id
    refused = client.post(f"{url}/run", json=resumed, headers=HEADERS)
    assert refused.status_code == 409
    new_review = client.get(url).json()["material_review"]
    assert [row["candidate_id"] for row in new_review["candidates"]] == [replacement_id]
    granted = client.post(f"{url}/run", json={**resumed, "material_review_digest": new_review["digest"]}, headers=HEADERS)
    assert granted.status_code == 200, granted.text
    assert granted.json()["grant_id"] == grant_id and granted.json()["status"] == "RUNNING"
    with factory() as db:
        assert grants.load(db, grant_id).grant["reviewed_materials"] == {replacement_id: "e" * 64}


def test_production_objective_cannot_finish_on_import_receipt(studio):
    client, factory, _, settings = studio
    project_id = create_project(client, "交付权威")
    with factory() as db:
        result = objective_verify.verify(db, settings, grant={
            "objective": "从原文制作到成片交付",
            "scope": {"project_ids": [project_id], "capability_groups": ["production"]},
        }, steps=[{"capability_id": "document_import_text", "satisfied": True}])
    assert result["missing"] == ["episode_delivery"]
    assert result["satisfied"] is False


def test_grant_binds_only_the_previewed_document_and_text_route(studio):
    client, factory, _, settings = studio
    project_id = create_project(client, "云端授权绑定")
    session = _session_row(factory, project_id=project_id)
    script = "第一场\n灯亮了。"
    with factory() as db:
        profile = ProviderProfile(name="测试文本路由", model="test-model", kind="openai_compatible",
                                  base_url="https://provider.example/v1")
        db.add(profile)
        db.flush()
        db.add(AgentRoute(role="scriptwriter", provider_profile_id=profile.id))
        db.commit()
    url = "/api/v1/support/task-grants"
    result = client.post(f"{url}/preview", headers=HEADERS, json={
        "session_id": session.id, "objective": "导入刚提供的原文并生成大纲", "capability_groups": ["inspect", "literary"],
        "script_text": script, "allow_remote_text": True,
    })
    assert result.status_code == 200, result.text
    preview = result.json()
    assert preview["text_consent"]["roles"][0]["model"] == "test-model"
    created = client.post(f"{url}/authorize", headers=HEADERS, json={
        "session_id": session.id, "preview_id": preview["preview_id"], "grant_digest": preview["grant_digest"],
        "confirm": True, "run": False,
    })
    assert created.status_code == 200, created.text
    with factory() as db:
        from shine.models import ProjectDocument

        assert db.query(MaterialTextConsent).count() == 0
        receipt = actions.execute_under_grant(
            db, settings, grant_id=created.json()["grant_id"], capability_id="document_import_text",
            params={"project_id": project_id, "text": "模型重写后的文本"}, tool_call_id="import-test",
            session_id=session.id, gateway=LLMGateway(SecretStore(settings.secret_store_dir)),
        )
        assert receipt["status"] == "SUCCEEDED", receipt
        document = db.get(ProjectDocument, receipt["original_task_id"])
        assert document.text == script
        consent = db.query(MaterialTextConsent).one()
        assert consent.document_sha256 == document.sha256
        assert consent.role == "scriptwriter" and consent.granted


def test_setup_readback_uses_current_capability_facts(studio, monkeypatch):
    _, factory, _, settings = studio
    from shine.comfyops import setup_summary

    monkeypatch.setattr(setup_summary, "summary", lambda *args, **kwargs: {"capabilities": {"image": {
        "state": "VERIFIED_RENDER",
        "facts": {"FILES_VERIFIED": True, "INSTANCE_BOUND": True, "PRODUCTION_READY": True},
    }}})
    with factory() as db:
        evidence = actions._capability_evidence(db, settings, SimpleNamespace(
            kind="setup_repair", project_id=None, params={"capabilities": ["image"]},
        ))
    assert evidence["image"]["instance_bound"] and evidence["image"]["files_verified"]
    assert actions._target_satisfied("setup_repair", evidence)


def test_image_validation_queues_through_material_executor(studio):
    client, factory, _, settings = studio
    from shine.materialprep.service import validate_image_runtime_setup

    project_id = create_project(client, "生图实证入口")
    save_config(settings, ImageRuntimeConfig(instance_id="image-instance", base_url="http://127.0.0.1:8188"))
    with factory() as db:
        result = validate_image_runtime_setup(db, settings, project_id=project_id,
                                              idempotency_key="image-proof", families=["scene"])
    assert result["run_id"] and len(result["items"]) == 1


def test_storyboard_book_generate_and_confirm_zero_dialogue(studio, monkeypatch):
    client, factory, _, settings = studio
    from tests.test_director_streamline import _prepare_project

    monkeypatch.setattr(client.app.state, "session_factory", factory, raising=False)
    prepared = _prepare_project(client, sections=2, section_seconds=5.0)
    project_id = prepared["project"]
    session = _session_row(factory, project_id=project_id)
    with factory() as db:
        loaded, _ = grants.create(
            db, session=session, actor=session.actor,
            objective="按已确认大纲生成并确认无对白分镜书",
            groups=["inspect", "literary"],
        )
        grant_id = loaded.id
        gen = actions.execute_under_grant(
            db, settings, grant_id=grant_id, capability_id="storyboard_book_generate",
            params={
                "project_id": project_id, "episode_count": 1,
                "target_seconds": 10, "tolerance_seconds": 3, "max_shot_seconds": 5.0,
            },
            tool_call_id="sb-book-gen", session_id=session.id,
        )
        assert gen["status"] == "SUCCEEDED", gen
        from shine.director.models import DirectorEpisodePlan

        plan = db.get(DirectorEpisodePlan, gen["original_task_id"])
        assert plan is not None and plan.version == 1
        plan_version = plan.version

        conf = actions.execute_under_grant(
            db, settings, grant_id=grant_id, capability_id="storyboard_book_confirm",
            params={
                "project_id": project_id, "plan_version": plan_version,
                "style": {"dialogue_ratio_percent": 0, "narration_level": "none"},
                "acknowledge_deviation": True,
            },
            tool_call_id="sb-book-confirm", session_id=session.id,
        )
        assert conf["status"] == "SUCCEEDED", conf

    with factory() as db:
        from shine.models import Project, StyleVersion

        project = db.get(Project, project_id)
        assert project.frozen_style_version_id is not None
        style = db.get(StyleVersion, project.frozen_style_version_id)
        assert style.snapshot["dialogue_ratio_percent"] == 0
        assert style.snapshot["narration_level"] == "none"


def test_align_bound_recipe_uses_normalized_image_recipe_state(studio, monkeypatch):
    _, _, _, settings = studio
    from shine.comfyops import image_renderer
    from shine.materialprep.runtime import align_bound_recipe, load_config

    save_config(settings, ImageRuntimeConfig(instance_id="inst-1", base_url="http://127.0.0.1:8188"))
    monkeypatch.setattr(image_renderer, "image_recipe_state", lambda _s: {
        "state": "READY", "instance_id": "inst-1", "port": 8188, "recipe_sha256": "a" * 64,
    })
    assert align_bound_recipe(settings, instance_id="inst-1", port=8188) is True
    assert load_config(settings).recipe_sha256 == "a" * 64
    assert align_bound_recipe(settings, instance_id="inst-1", port=8189) is False
    monkeypatch.setattr(image_renderer, "image_recipe_state", lambda _s: {
        "state": "MISSING", "instance_id": "inst-1", "port": 8188,
    })
    assert align_bound_recipe(settings, instance_id="inst-1", port=8188) is False


def test_resume_from_user_decision_clears_no_progress_and_last_block_code(studio):
    client, factory, _, _ = studio
    project_id = create_project(client, "恢复清零计数")
    session = _session_row(factory, project_id=project_id)
    with factory() as db:
        loaded, _ = grants.create(
            db, session=session, actor=session.actor,
            objective="测试显式恢复清零计数", groups=["inspect"],
        )
        grants.save_loop(db, loaded.id, {"no_progress": 3, "last_block_code": "SOME_BLOCK", "steps": []})
        grants.set_status(db, loaded.id, "PAUSED_USER_DECISION", reason="USER_CHECK")
        resumed = grants.set_status(db, loaded.id, "RUNNING")
        loop = grants.loop_state(resumed.grant)
        assert loop["no_progress"] == 0
        assert loop["last_block_code"] is None


def test_image_runtime_validate_preflight_copy_tracks_family_count(studio):
    client, factory, _, _ = studio
    project_id = create_project(client, "小样文案复验")
    session = _session_row(factory, project_id=project_id)
    save_config(client.app.state.settings, ImageRuntimeConfig(
        instance_id="image-instance", base_url="http://127.0.0.1:8188",
    ))
    one = client.post("/api/v1/support/actions/preview", headers=HEADERS, json={
        "kind": "image_runtime_validate", "session_id": session.id,
        "params": {"project_id": project_id, "families": ["scene"]},
    })
    assert one.status_code == 200, one.text
    assert "生成 1 个验证小样" in one.json()["action"]["preview"]["summary"]
    assert "三合一" not in one.json()["action"]["preview"]["summary"]


def test_production_start_routes_optional_output_spec():
    from shine.copilot import declarations as decl
    from shine.director.streamline import start_director_production

    clean = validate_params("production_start", {
        "project_id": "p-1", "episode_ids": ["ep-1"],
        "output": {"width": 1344, "height": 768, "fps": 24},
    })
    kwargs = routed_kwargs(decl.write_action("production_start"), clean, start_director_production)
    assert kwargs["output"] == {"width": 1344, "height": 768, "fps": 24}
    assert kwargs["episode_ids"] == ["ep-1"]


def test_read_original_task_converts_unknown_action_to_grant_error(studio):
    client, factory, _, settings = studio
    project_id = create_project(client, "未知动作回读")
    session = _session_row(factory, project_id=project_id)
    with factory() as db:
        loaded, _ = grants.create(
            db, session=session, actor=session.actor,
            objective="检查未知动作回读不会 500", groups=["inspect"],
        )
        with pytest.raises(task_grant.GrantError) as raised:
            read_tools.execute(
                db, settings, name="read_original_task",
                arguments={"action_id": "missing-action-id"}, grant=loaded.grant,
            )
    assert raised.value.code == "ACTION_NOT_FOUND"


def test_allow_download_false_keeps_zero_download_setup_repair_and_blocks_downloads(studio, monkeypatch):
    client, factory, _, settings = studio
    project_id = create_project(client, "零下载对齐配方")
    session = _session_row(factory, project_id=project_id)
    prev = client.post("/api/v1/support/task-grants/preview", headers=HEADERS, json={
        "session_id": session.id, "project_id": project_id,
        "objective": "对齐本地已就绪的出图配方",
        "capability_groups": ["inspect", "runtime", "test"],
        "allow_download": False,
    })
    assert prev.status_code == 200, prev.text
    preview_body = prev.json()
    assert "setup_repair" in preview_body["capabilities"]
    auth = client.post("/api/v1/support/task-grants/authorize", headers=HEADERS, json={
        "session_id": session.id, "preview_id": preview_body["preview_id"],
        "grant_digest": preview_body["grant_digest"], "confirm": True, "run": False,
    })
    assert auth.status_code == 200, auth.text
    grant_id = auth.json()["grant_id"]
    with factory() as db:
        loaded = grants.load(db, grant_id)
        names = {spec.name for spec in tool_registry.grant_tool_specs(loaded.grant)}
        assert "setup_repair" in names
        monkeypatch.setattr(actions, "preview_action", lambda *a, **kw: ({
            "id": "fake-act", "preview": {"download_bytes": 1024, "missing_components": ["x"]},
        }, {}))
        monkeypatch.setattr(db, "get", lambda model, pk: SimpleNamespace(
            id="fake-act", status="PREVIEWED", preview_hash="h", target_fingerprint="t",
        ) if pk == "fake-act" else None)
        refused = actions.execute_under_grant(
            db, settings, grant_id=grant_id, capability_id="setup_repair",
            params={"capabilities": ["image"]}, tool_call_id="tc-dl-block",
            session_id=session.id,
        )
    assert refused["status"] == "BLOCKED"
    assert refused["error_code"] == "GRANT_PERMISSION_NOT_GRANTED"


def test_objective_verify_reads_image_readiness_for_image_setup_objectives(studio, monkeypatch):
    client, factory, _, settings = studio
    from shine.comfyops import setup_summary
    from shine.materialprep import runtime as img_runtime

    project_id = create_project(client, "出图整备验收")
    state = {"binding": "PENDING_RECIPE", "runnable": False, "bound": False, "attempt": None}
    monkeypatch.setattr(img_runtime, "readiness", lambda *a, **kw: {
        "binding_state": state["binding"], "runnable": state["runnable"],
        "families": {"scene": {"can_generate": bool(state["attempt"]), "evidence_attempt_id": state["attempt"]}},
    })
    monkeypatch.setattr(setup_summary, "summary", lambda *a, **kw: {
        "verdict": {"mode": "READY"},
        "capabilities": {"image": {"state": "READY" if state["bound"] else "FOUND",
                                   "facts": {"FILES_VERIFIED": True, "INSTANCE_BOUND": state["bound"]}}},
    })
    grant = {
        "objective": "对齐 image 配方绑定并完成 scene 场景小样验证",
        "scope": {"project_ids": [project_id], "capability_groups": ["inspect", "runtime", "test"]},
    }
    steps = [{"capability_id": "image_runtime_validate", "satisfied": True, "outcome": "VERIFIED"}]
    with factory() as db:
        pending = objective_verify.verify(db, settings, grant=grant, steps=steps)
        assert pending["satisfied"] is False
        assert set(pending["missing"]) == {"image_binding_ready", "image_sample_verified"}
        state.update(binding="READY", runnable=True, bound=True, attempt="att-1")
        ready = objective_verify.verify(db, settings, grant=grant, steps=steps)
        assert ready["satisfied"] is True
        assert ready["missing"] == []
        no_delivery_grant = {
            **grant,
            "objective": "仅整备、不产出成片：对齐 image 配方并验证 1 个 scene 场景小样",
        }
        ready_neg = objective_verify.verify(db, settings, grant=no_delivery_grant, steps=steps)
        assert ready_neg["satisfied"] is True
        assert ready_neg["missing"] == []


def test_full_production_and_allow_gpu_true_preserve_test_group_while_allow_gpu_false_strips(studio):
    client, factory, _, _ = studio
    project_id = create_project(client, "全流程授权补齐测试分组")
    session = _session_row(factory, project_id=project_id)
    with factory() as db:
        card = chat_investigator._validate_grant_proposal(
            db,
            session=session,
            arguments={
                "objective": "完成素材晋升并使用 pdd8 启动正式生产",
                "capability_groups": ["inspect", "materials", "production"],
                "budget_preset": "full_production",
                "pause_before_production": True,
            },
        )
    assert card["valid"] is True
    assert "test" in card["capability_groups"]

    prev_gpu = client.post("/api/v1/support/task-grants/preview", headers=HEADERS, json={
        "session_id": session.id, "project_id": project_id,
        "objective": "完成素材晋升并正式生产",
        "capability_groups": ["inspect", "materials", "production"],
        "budget_preset": "full_production",
        "allow_gpu": True, "allow_download": False,
    })
    assert prev_gpu.status_code == 200, prev_gpu.text
    gpu_body = prev_gpu.json()
    assert "test" in gpu_body["capability_groups"]
    assert {"material_generate", "production_start"} <= set(gpu_body["capabilities"])

    prev_no_gpu = client.post("/api/v1/support/task-grants/preview", headers=HEADERS, json={
        "session_id": session.id, "project_id": project_id,
        "objective": "只做文字确认，不执行 GPU 推理",
        "capability_groups": ["inspect", "materials", "production"],
        "budget_preset": "full_production",
        "allow_gpu": False, "allow_download": False,
    })
    assert prev_no_gpu.status_code == 200, prev_no_gpu.text
    no_gpu_body = prev_no_gpu.json()
    assert "test" not in no_gpu_body["capability_groups"]
    assert no_gpu_body["limits"]["max_test_inference_jobs"] == 0
    assert no_gpu_body["limits"]["max_gpu_minutes"] == 0
    assert "production_start" not in no_gpu_body["capabilities"]


def test_promote_material_candidate_and_material_promote_action_succeed(studio):
    client, factory, _, settings = studio
    from shine.comfyops import assets
    from shine.models import Asset, Project

    project_id = create_project(client, "候选素材晋升回归")
    session = _session_row(factory, project_id=project_id)
    with factory() as db:
        project = db.get(Project, project_id)
        cand = assets.create_candidate(
            db, settings, project, "character", "林雾",
            assets.fake_image("linwu-promote"), "林雾.png", None,
            {"is_mock": False, "renderer_owned": True, "source": "material_preparation"},
        )
        cand.status = "READY_FOR_REVIEW"
        db.commit()
        cid = cand.id
        loaded, _ = grants.create(
            db, session=session, actor=session.actor,
            objective="晋升已审核的真实候选素材", groups=["inspect", "materials"],
        )
        receipt = actions.execute_under_grant(
            db, settings, grant_id=loaded.id, capability_id="material_promote",
            params={"project_id": project_id, "candidate_id": cid},
            tool_call_id="tc-promote-ok", session_id=session.id,
        )
        assert receipt["status"] == "SUCCEEDED", receipt
        assert receipt["original_task_id"] == cid
        refreshed = db.get(CandidateAsset, cid)
        assert refreshed is not None and refreshed.status == "PROMOTED"
        assert refreshed.formal_asset_id
        asset = db.get(Asset, refreshed.formal_asset_id)
        assert asset is not None
        assert (asset.provenance or {}).get("offline_only") is False


def test_health_check_uses_worker_online_authority(studio):
    _, factory, _, settings = studio
    from shine.models import WorkerInstance, utcnow
    from shine.support.health_check import build_health_check

    with factory() as db:
        db.add(WorkerInstance(
            worker_id="worker-live-1", version="shine-worker/3.0",
            pid=12345, status="online", last_heartbeat_at=utcnow(),
        ))
        db.commit()
        report_online = build_health_check(db, settings)
        assert not any(row["code"] == "WORKER_OFFLINE" for row in report_online["blockers"])

        db.query(WorkerInstance).update({"status": "stopped"})
        db.commit()
        report_offline = build_health_check(db, settings)
        assert any(row["code"] == "WORKER_OFFLINE" for row in report_offline["blockers"])


def test_approve_action_rolls_back_uncommitted_writes_on_handler_exception(studio, monkeypatch):
    client, factory, _, settings = studio
    from dataclasses import replace

    from shine.models import Asset

    project_id = create_project(client, "动作异常回滚校验")
    session = _session_row(factory, project_id=project_id)
    with factory() as db:
        prepared, _ = actions.preview_action(
            db, settings, kind="project_settings_update",
            params={"project_id": project_id, "language": "zh"},
            actor=session.actor, session_id=session.id, project_id=project_id,
        )
        orig_get_handler = actions.get_handler
        handler = orig_get_handler("project_settings_update")

        def broken_execute(db_sess, *args, **kwargs):
            db_sess.add(Asset(
                project_id=project_id, filename="orphan.png", kind="image",
                content_type="image/png", sha256="a" * 64,
                storage_path="orphan.png", byte_size=12,
            ))
            db_sess.flush()
            raise RuntimeError("boom-unhandled")

        monkeypatch.setattr(
            actions,
            "get_handler",
            lambda kind: (
                replace(handler, execute=broken_execute)
                if kind == "project_settings_update"
                else orig_get_handler(kind)
            ),
        )
        result, _ = actions.approve_action(
            db, settings, factory, action_id=prepared["id"],
            preview_hash=prepared["preview_hash"], actor=session.actor,
        )
        assert result["status"] == "FAILED"
        assert "RuntimeError: boom-unhandled" in str(result.get("last_error") or "")
        assert db.query(Asset).filter(Asset.project_id == project_id).count() == 0
