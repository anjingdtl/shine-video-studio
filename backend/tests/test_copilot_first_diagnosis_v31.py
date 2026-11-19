"""Direction 2 regressions: first diagnosis, minimal repair scope, one model.

Counterexamples from the 2026-11-19 closeout audit:

* ``diagnose_snapshot`` called ``_repair_action(snapshot, db=db)`` while the
  function only accepted ``snapshot`` — every CONFIG keyword diagnosis raised
  TypeError instead of recommending an action;
* with no evidence naming a capability, the repair fell back to a blind
  ``["h3", "image"]`` double preparation;
* the planner's repair step pointed its readback at a route that does not
  exist (``GET /api/v1/setup/prepare/{task_id}``);
* the structured fallback of the tool loop used ``profile_for_role`` (no
  inheritance), so a provider without native tool calls dropped an inherited
  model into LLM_NOT_CONFIGURED while the native path worked;
* a restored session swallowed a NEW fault: the bindings stayed on the old
  job/error and the new failure was never diagnosed.
"""
from __future__ import annotations

import re
from datetime import timedelta
from types import SimpleNamespace

import pytest

from shine.copilot import tool_loop
from shine.llm.base import LLMNotConfigured
from shine.llm.tool_calls import FALLBACK_SCHEMA
from shine.models import new_id, utcnow
from shine.support import grants as grant_service
from shine.support.diagnostics import diagnose_snapshot, minimal_repair_scope
from shine.support.models import SupportSession

pytestmark = pytest.mark.integration

HEADERS = {"x-shine-local-action": "1"}

# The established managed-installation fixtures (real recipe, registered
# instance, pinned file set) — reused for the sanitized GRANT-17 repro.
from tests.test_runtime_activation import activate, candidate, prep  # noqa: F401, E402


def _snapshot(*, code=None, mode="READY", capabilities=None, error_code=None):
    return {
        "evidence_id": "ev-test",
        "page_context": {"screen": "setup", "error_code": error_code, "project_id": None},
        "setup": {
            "verdict": {"mode": mode, "label": "测试", "code": code},
            "runtime": {"status": "INVALID_REQUIRES_REVIEW" if code else "ACTIVE",
                        "services_verified": not code},
            "summary": {"capabilities": capabilities or {}},
            "idle_blockers": [],
        },
        "runtime": {"workers": [{"online": True}]},
        "task": {},
    }


def test_config_keyword_diagnosis_recommends_an_action_instead_of_crashing():
    # NODE_MISSING is a CONFIG rule; before the fix this raised TypeError
    # (_repair_action did not accept db yet the call site passed it).
    diagnosis = diagnose_snapshot(_snapshot(), question="为什么节点用不了", db=None)
    assert diagnosis["category"] == "CONFIG"
    actions = diagnosis["recommended_actions"]
    assert actions, "a CONFIG diagnosis must recommend the evidence-based action"
    assert actions[0]["kind"] in {"setup_repair", "setup_reconcile"}


def test_runtime_contract_change_repairs_only_the_invalidated_video_runtime():
    capabilities = {
        "h3": {"state": "READY", "facts": {"PRODUCTION_READY": True}},
        # image was never prepared; it is NOT implicated by the video runtime
        # contract change and must not be dragged into the repair.
        "image": {"state": "NOT_PREPARED", "facts": {}},
        "vlm": {"state": "NOT_PREPARED", "optional": True, "facts": {}},
    }
    snapshot = _snapshot(
        code="ACTIVE_RUNTIME_CONFIGURATION_CHANGED", mode="BLOCKED",
        capabilities=capabilities,
    )
    scope, has_missing = minimal_repair_scope(snapshot)
    assert scope == ["h3"]
    assert has_missing is False
    diagnosis = diagnose_snapshot(snapshot, question="环境为什么变了", db=None)
    params = diagnosis["recommended_actions"][0]["params"]
    assert params["capabilities"] == ["h3"]


def test_missing_files_name_exactly_the_missing_capability():
    capabilities = {
        "h3": {"state": "READY", "facts": {"PRODUCTION_READY": True}},
        "image": {"state": "FILES_MISSING", "missing_count": 2, "facts": {}},
    }
    scope, has_missing = minimal_repair_scope(_snapshot(capabilities=capabilities))
    assert scope == ["image"]
    assert has_missing is True


def test_no_evidence_recommends_readonly_reconcile_not_a_blind_double_preparation():
    scope, _ = minimal_repair_scope(_snapshot(capabilities={}))
    assert scope == []
    diagnosis = diagnose_snapshot(
        _snapshot(code="ACTIVE_RUNTIME_CONFIGURATION_CHANGED", mode="BLOCKED", capabilities={}),
        question="帮我检查环境",
        db=None,
    )
    # With a runtime-contract code the video runtime is still named by the
    # verdict itself; the blind fallback is gone either way.
    for action in diagnosis["recommended_actions"]:
        assert action["params"].get("capabilities") != ["h3", "image"]
    empty = diagnose_snapshot(_snapshot(capabilities={}), question="为什么节点用不了", db=None)
    action = empty["recommended_actions"][0]
    assert action["kind"] == "setup_reconcile"
    assert action["mutates"] is False


def test_planner_readback_addresses_are_real_routes(studio):
    client, factory, _, settings = studio
    routes = set()
    for route in client.app.routes:
        path = getattr(route, "path", None)
        if path:
            routes.add(re.sub(r"\{[^}]+\}", "{x}", path))
    from pathlib import Path

    planner = (Path(__file__).resolve().parents[2] / "backend" / "shine" / "support" / "planner.py")
    text = planner.read_text(encoding="utf-8")
    readbacks = re.findall(r'"readback":\s*"(GET )?(/api/v1/[^"]+)"', text)
    assert readbacks, "planner must declare readback authorities"
    for _method, path in readbacks:
        normalized = re.sub(r"\{[^}]+\}", "{x}", path)
        assert normalized in routes, f"readback route does not exist: {path}"
    assert "GET /api/v1/setup/prepare/{task_id}" not in text


def test_message_rebinds_a_restored_session_to_the_new_fault(studio):
    client, factory, _, _ = studio
    created = client.post(
        "/api/v1/support/sessions",
        json={"page_id": "run", "error_code": "OLD_CODE", "task_id": "old-task", "question": ""},
        headers=HEADERS,
    )
    assert created.status_code == 201, created.text
    session_id = created.json()["session"]["id"]
    reply = client.post(
        f"/api/v1/support/sessions/{session_id}/messages",
        json={
            "content": "帮我诊断这个失败：NEW_CODE",
            "page_id": "run",
            "error_code": "NEW_CODE",
            "task_id": "new-task",
            "job_id": None,
            "attempt_id": None,
        },
        headers=HEADERS,
    )
    assert reply.status_code == 200, reply.text
    with factory() as db:
        row = db.get(SupportSession, session_id)
        assert row.error_code == "NEW_CODE"
        assert row.task_id == "new-task"
    # The diagnosis of THIS turn reads the new binding, not the old one.
    page = reply.json()["message"]["payload"]["page"]
    assert page["error_code"] == "NEW_CODE"


class InheritingGateway:
    """Mimics the real gateway: the fallback reads the pin, the route does not exist.

    Before the fix the loop never pinned the inherited profile, so
    ``complete_for_role`` raised LLMNotConfigured on the fallback path even
    though ``profile_for_role_or_inherit`` had resolved a working model.
    """

    def __init__(self):
        self.pins: dict[str, object] = {}
        self.fallback_calls = 0
        self.native_calls = 0

    def profile_for_role_or_inherit(self, db, role):
        pinned = self.pins.get(role)
        if pinned is not None:
            return pinned, role
        return SimpleNamespace(id="p", name="制作环境助理", kind="mock",
                               base_url="", model="inherited-model"), "comfyops"

    def profile_for_role(self, db, role):
        pinned = self.pins.get(role)
        if pinned is None:
            raise LLMNotConfigured(f"No provider profile is routed to the {role} role")
        return pinned

    def pin_role(self, role, profile):
        if profile is None:
            self.pins.pop(role, None)
        else:
            self.pins[role] = profile

    def clear_pins(self):
        self.pins.clear()

    def complete_tools_for_role(self, db, role, **kwargs):
        self.native_calls += 1
        return None  # provider without native tool calls -> validated fallback

    def complete_for_role(self, db, role, **kwargs):
        self.profile_for_role(db, role)  # raises without the pin
        self.fallback_calls += 1
        assert kwargs.get("schema") is FALLBACK_SCHEMA
        return SimpleNamespace(text="", parsed={"action": "finish", "final": "结束"},
                               model="inherited-model", latency_ms=1, usage={})


def _session_row(factory) -> SupportSession:
    with factory() as db:
        row = SupportSession(
            id=new_id(), project_id=None, page_id="copilot", status="OPEN",
            actor="local-user", context_json={}, expires_at=utcnow() + timedelta(days=1),
        )
        db.add(row)
        db.commit()
        return row


def test_grant17_sanitized_repro_configuration_change_diagnoses_minimal_repair(prep, candidate):  # noqa: F811
    """GRANT-17 (2026-11-18 user-machine fault), sanitized on the dev machine.

    A pinned renderer file really changes → the runtime contract is invalid →
    BOTH the page-failure path (copilot diagnosis) and the Setup-blocking path
    (production verdict) read the SAME named code, and the recommended repair
    is the minimal one (the invalidated video runtime), not a blind h3+image
    double preparation. The user-machine现场 verification remains separate.
    """
    client, factory, settings = prep
    assert activate(prep, candidate).status_code == 200
    root = candidate[1]
    (root / "main.py").write_text(
        "# sanitized 11-18 repro: pinned renderer file changed\n", encoding="utf-8"
    )
    runtime = client.get("/api/v1/setup/runtime").json()
    assert runtime["status"] == "INVALID_REQUIRES_REVIEW"
    assert runtime["error"] == "ACTIVE_RUNTIME_CONFIGURATION_CHANGED"
    # Setup-blocking path: the single verdict names the same code.
    status = client.get("/api/v1/system/status").json()
    assert status["production_mode"]["code"] == "ACTIVE_RUNTIME_CONFIGURATION_CHANGED"
    # Page-failure path: the copilot reads the same authority and recommends
    # the evidence-based minimal repair.
    from shine.support.context import build_evidence_snapshot
    from shine.support.diagnostics import diagnose_snapshot

    with factory() as db:
        snapshot = build_evidence_snapshot(db, settings, page_id="setup")
        diagnosis = diagnose_snapshot(snapshot, question="环境为什么变了，帮我修好", db=db)
    assert diagnosis["readiness_reference"]["code"] == "ACTIVE_RUNTIME_CONFIGURATION_CHANGED"
    actions = diagnosis["recommended_actions"]
    assert actions, "the configuration-changed diagnosis must recommend an action"
    assert actions[0]["kind"] == "setup_repair"
    # The recommendation must equal the evidence-derived scope exactly: the
    # capabilities whose fixed-manifest files are really missing in THIS
    # fixture (a fresh managed install has none downloaded), or — when no
    # files are missing — only the invalidated video runtime. Never a blind
    # addition beyond what the same authority names.
    caps = ((snapshot.get("setup") or {}).get("summary") or {}).get("capabilities") or {}
    missing = sorted(
        key for key, row in caps.items()
        if isinstance(row, dict) and row.get("missing_count") and not row.get("optional")
    )
    assert actions[0]["params"]["capabilities"] == (missing or ["h3"])


def test_fallback_inheritance_with_a_real_tool_menu(studio):
    _, factory, _, settings = studio
    from shine.copilot import tool_registry

    session = _session_row(factory)
    with factory() as db:
        loaded, _ = grant_service.create(
            db, session=session, actor="local-user",
            objective="检查并准备本机环境", groups=["inspect", "resources", "workflows"],
        )
        grant_id = loaded.id
        tools = tool_registry.grant_tool_specs(loaded.grant)
    assert tools, "the grant must offer its declared capabilities"
    gateway = InheritingGateway()
    with factory() as db:
        result = tool_loop.run(
            db, settings, grant_id=grant_id, objective="检查并准备本机环境",
            tools=tools, actor="local-user", session_id=session.id, gateway=gateway,
        )
    assert gateway.native_calls >= 1
    assert gateway.fallback_calls >= 1, "the fallback must run on the inherited profile"
    assert result.stop_reason == "NO_AUTHORITATIVE_EVIDENCE"
    assert result.llm.get("inherited_from") == "comfyops"
    assert result.llm.get("degraded_reason") == "PROVIDER_WITHOUT_NATIVE_TOOL_CALLS"
    # The pin is released after the task: the next turn resolves the route again.
    assert "support_agent" not in gateway.pins
