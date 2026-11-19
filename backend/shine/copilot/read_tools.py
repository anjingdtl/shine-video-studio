"""Read-only investigation tools for one task grant (V3.1 closeout).

The autonomous loop may *look* before it writes.  These tools add no new
authority: every handler calls an existing reader (setup verdict/summary,
resource discovery, installation resolution, the live renderer's object_info,
the original SupportAction readback, the production run ledger).  A read never
creates a SupportAction, never starts a renderer, never downloads, and never
mutates anything; it is still counted against the grant's tool-call budget and
refused when it names a project outside the frozen scope.

The menu is derived from this module alone, and the write menu stays derived
from ``copilot.declarations`` — the two can never disagree about what exists.
"""
from __future__ import annotations

from typing import Any

from shine.copilot import task_grant as grant_contract
from shine.llm.tool_calls import ToolSpec
from shine.models import utcnow

#: name -> (description, JSON schema). Handlers live in :func:`execute`.
READ_TOOL_DEFINITIONS: dict[str, tuple[str, dict[str, Any]]] = {
    "inspect_environment": (
        "只读读取本机 Setup verdict、运行配置状态与各能力就绪事实（缺件数/层级）。",
        {"type": "object", "additionalProperties": False,
         "properties": {"project_id": {"type": "string", "maxLength": 36}}},
    ),
    "read_resource_inventory": (
        "只读读取资源检索台账与已发现的 ComfyUI 安装清单（含受管/收编状态）。",
        {"type": "object", "additionalProperties": False, "properties": {}},
    ),
    "read_node_inventory": (
        "只读读取当前绑定渲染器的真实 object_info 节点清单；渲染器未运行时如实报告不可用，不会为读取启动渲染器。",
        {"type": "object", "additionalProperties": False,
         "properties": {"node_filter": {"type": "string", "maxLength": 80}}},
    ),
    "read_original_task": (
        "回读一个已绑定原任务的最新权威结果（绝不重新提交）。",
        {"type": "object", "additionalProperties": False,
         "properties": {"action_id": {"type": "string", "maxLength": 36}},
         "required": ["action_id"]},
    ),
    "read_production_run": (
        "只读读取一次生产运行的状态、分集交付与镜头通过数。",
        {"type": "object", "additionalProperties": False,
         "properties": {"run_id": {"type": "string", "maxLength": 36}},
         "required": ["run_id"]},
    ),
}


def read_tool_specs() -> list[ToolSpec]:
    return [
        ToolSpec(name=name, description=description, parameters=schema)
        for name, (description, schema) in READ_TOOL_DEFINITIONS.items()
    ]


def is_read_tool(name: str) -> bool:
    return name in READ_TOOL_DEFINITIONS


def _bounded_receipt(name: str, facts: dict[str, Any], *, authority: str) -> dict[str, Any]:
    return {
        "status": "OK",
        "capability_id": name,
        "tool_call_id": None,
        "grant_id": None,
        "action_id": None,
        "original_task_id": None,
        "observed_facts": facts,
        "authority": authority,
        "resource_usage": {},
        "next_available_actions": [],
        "error_code": None,
        "message": "",
        "read_only": True,
        "checked_at": utcnow().isoformat(),
    }


def execute(
    db,
    settings,
    *,
    name: str,
    arguments: dict[str, Any],
    grant: dict[str, Any],
) -> dict[str, Any]:
    """Run one read-only tool inside the grant's scope. Refusals are named."""
    if not is_read_tool(name):
        raise grant_contract.GrantError(
            "TOOL_NOT_DECLARED", "这个只读工具不存在。", details={"tool": name}
        )
    arguments = dict(arguments or {})
    project_id = str(arguments.get("project_id") or "") or None
    if project_id:
        grant_contract.assert_project_scope(grant, project_id=project_id, capability_id=name)

    if name == "inspect_environment":
        from shine.comfyops.setup_summary import summary as setup_summary

        verdict = setup_summary(db, settings, project_id=project_id, discovered=[])
        capabilities = {
            key: {
                "label": row.get("label"),
                "state": row.get("state"),
                "optional": bool(row.get("optional")),
                "missing_count": row.get("missing_count"),
                "facts": row.get("facts"),
            }
            for key, row in (verdict.get("capabilities") or {}).items()
            if isinstance(row, dict)
        }
        return _bounded_receipt(
            name,
            {
                "verdict": verdict.get("verdict"),
                "preparation": verdict.get("preparation"),
                "capabilities": capabilities,
                "installation": (verdict.get("installation") or {}).get("selected"),
            },
            authority="GET /api/v1/setup/verdict",
        )

    if name == "read_resource_inventory":
        from shine.comfyops.resource_discovery import read_state
        from shine.comfyops.setup_summary import resolve_installation

        state = read_state(db)
        resolved = resolve_installation(db, settings, path=None, discovered=[])
        candidates = [
            {
                key: row.get(key)
                for key in ("instance_id", "root", "port", "managed", "adopted", "status", "source")
            }
            for row in (resolved.get("candidates") or [])[:12]
        ]
        return _bounded_receipt(
            name,
            {
                "scan": state.get("scan"),
                "verification_candidates": len(state.get("verification_candidates") or []),
                "installations": candidates,
                "choice_required": bool(resolved.get("choice_required")),
                "selected": resolved.get("selected"),
            },
            authority="shine.comfyops.resource_discovery.read_state",
        )

    if name == "read_node_inventory":
        from shine.comfy.client import ComfyError, ComfyUIClient
        from shine.comfyops.runtime_configuration import resolve_runtime

        try:
            effective = resolve_runtime(db, settings)
        except Exception as exc:  # noqa: BLE001 - a blocked runtime is a named fact
            return _bounded_receipt(
                name,
                {"available": False, "reason": "RUNTIME_UNAVAILABLE",
                 "detail": str(getattr(exc, "detail", exc))[:200]},
                authority="live /object_info (renderer not started for a read)",
            )
        client = ComfyUIClient(effective.comfyui_base_url, timeout_s=5, max_retries=0)
        try:
            info = client.object_info()
        except ComfyError as exc:
            return _bounded_receipt(
                name,
                {"available": False, "reason": "OBJECT_INFO_UNAVAILABLE",
                 "detail": str(exc)[:200]},
                authority="live /object_info (renderer not started for a read)",
            )
        node_filter = str(arguments.get("node_filter") or "").casefold()
        names = sorted(info)
        if node_filter:
            names = [item for item in names if node_filter in item.casefold()]
        return _bounded_receipt(
            name,
            {
                "available": True,
                "node_count": len(info),
                "nodes": names[:200],
                "h3_nodes": [item for item in sorted(info)
                             if "h3" in item.casefold() or "minimax" in item.casefold()][:40],
            },
            authority="live /object_info",
        )

    if name == "read_original_task":
        from shine.support.actions import read_under_grant

        action_id = str(arguments.get("action_id") or "")
        if not action_id:
            raise grant_contract.GrantError(
                "INTENT_PARAMS_MALFORMED", "read_original_task 需要 action_id。"
            )
        result = read_under_grant(
            db, settings, action_id=action_id,
            grant_id=str(grant.get("grant_id") or ""),
        )
        return {
            **_bounded_receipt(name, result.get("observed_facts") or {},
                               authority=str(result.get("authority") or "")),
            "status": str(result.get("status") or ""),
            "action_id": action_id,
            "original_task_id": result.get("original_task_id"),
            "error_code": result.get("error_code"),
            "message": str(result.get("message") or ""),
        }

    if name == "read_production_run":
        from sqlalchemy import select

        from shine.production.models import ProductionRun, ProductionRunEpisode

        run_id = str(arguments.get("run_id") or "")
        run = db.get(ProductionRun, run_id)
        if run is None:
            raise grant_contract.GrantError(
                "TARGET_FACTS_UNAVAILABLE", "找不到这次生产运行。", details={"run_id": run_id}
            )
        episodes = list(db.scalars(
            select(ProductionRunEpisode).where(ProductionRunEpisode.run_id == run.id)
        ).all())
        return _bounded_receipt(
            name,
            {
                "run_id": run.id,
                "run_status": run.status,
                "project_id": run.project_id,
                "episodes": [
                    {"episode_id": item.episode_id, "status": item.status,
                     "delivery_order_id": item.delivery_order_id}
                    for item in episodes
                ],
            },
            authority="GET /api/v1/production-runs/{run_id}",
        )

    raise grant_contract.GrantError("TOOL_NOT_DECLARED", "这个只读工具不存在。")
