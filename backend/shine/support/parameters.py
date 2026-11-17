"""User-level parameter contracts and change previews.

Tuning is not a free-form "set anything" API.  Each contract exposes only fields
that the product already validates:

* the project's spoken language (the same canonicaliser the PATCH route uses),
* a user workflow copy's *parameter mapping* (which logical parameter points at
  which existing node input),
* the production strategy contract (variant, accelerator, second pass, RelayVSR
  X2), which is frozen into the next director package by the existing run engine.

A preview computes a diff and a proposed capability action; it never writes.
The write itself is the existing ``support.actions`` authorization: the person
confirms, the original executor runs, and the authority is read back.  No model
weights are hashed here, no node graph is rewritten and no frozen style is
touched.
"""
from __future__ import annotations

from typing import Any

from sqlalchemy import or_, select

from shine.models import Project, WorkflowProfile
from shine.strategy import DEFAULT_STRATEGY, GenerationStrategy, accelerator_options, model_variant_options


class ParameterError(ValueError):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code
        self.message = message


def _project(db, project_id: str | None) -> Project:
    if not project_id:
        raise ParameterError("PROJECT_REQUIRED", "调参必须先指定一个项目。")
    project = db.get(Project, project_id)
    if project is None:
        raise ParameterError("PROJECT_NOT_FOUND", "目标项目不存在。")
    return project


def _visible_profiles(db, project_id: str | None) -> list[WorkflowProfile]:
    if project_id:
        return list(
            db.scalars(
                select(WorkflowProfile)
                .where(or_(WorkflowProfile.project_id == project_id, WorkflowProfile.project_id.is_(None)))
                .order_by(WorkflowProfile.name)
            ).all()
        )
    return list(db.scalars(select(WorkflowProfile).where(WorkflowProfile.project_id.is_(None))).all())


def _language_contract(db, project_id: str | None) -> dict[str, Any]:
    from shine.h3.spoken_language import SPOKEN_LANGUAGES

    current = None
    if project_id:
        project = db.get(Project, project_id)
        current = project.language if project is not None else None
    return {
        "target": f"project_language:{project_id or ''}",
        "kind": "project_language",
        "label": "项目口播语言",
        "description": "H3 提示词里声明的口播语言；只影响后续生成，不会改已冻结风格。",
        "fields": [
            {
                "key": "language",
                "label": "口播语言",
                "type": "ENUM",
                "allowed": list(SPOKEN_LANGUAGES),
                "current": current,
                "unit": None,
            }
        ],
        "apply_via": "project_settings_update",
        "reversible": "YES",
        "gpu": False,
        "paid": False,
        "notes": "中文名称会按既有口播语言清单规范化；非法值直接拒绝。",
    }


def _workflow_contract(profile: WorkflowProfile) -> dict[str, Any]:
    return {
        "target": f"workflow_map:{profile.id}",
        "kind": "workflow_map",
        "label": f"工作流参数映射：{profile.name}",
        "description": "逻辑参数指向工作流节点输入；只创建用户级副本，不改内置模板或节点源码。",
        "fields": [
            {
                "key": key,
                "label": key,
                "type": "GRAPH_PATH",
                "allowed": "node_id.inputs.field",
                "current": value,
                "unit": None,
            }
            for key, value in sorted((profile.parameter_map or {}).items())
        ],
        "apply_via": "workflow_user_copy",
        "reversible": "YES",
        "gpu": False,
        "paid": False,
        "notes": "新副本必须使用新名称；执行后会做节点校验，失败保持原模板。",
        "source_profile_id": profile.id,
        "source_name": profile.name,
        "mode": profile.mode,
    }


def _strategy_contract() -> dict[str, Any]:
    variants = model_variant_options()
    accelerators = accelerator_options()
    default = DEFAULT_STRATEGY.model_dump(mode="json")
    return {
        "target": "production_strategy:",
        "kind": "production_strategy",
        "label": "真实生产策略",
        "description": "变体、加速配方、二次采样与 RelayVSR X2；由既有策略合同校验并写入下一次导演包。",
        "fields": [
            {
                "key": "model_variant_id",
                "label": "模型变体",
                "type": "ENUM",
                "allowed": [row["variant_id"] for row in variants],
                "current": default["model_variant_id"],
                "options": variants,
            },
            {
                "key": "accelerator_id",
                "label": "加速配方",
                "type": "ENUM",
                "allowed": [row["accelerator_id"] for row in accelerators],
                "current": default["accelerator_id"],
                "options": accelerators,
            },
            {
                "key": "second_pass.enabled",
                "label": "二次采样",
                "type": "BOOL",
                "current": default["second_pass"]["enabled"],
            },
            {
                "key": "second_pass.denoise",
                "label": "二次采样去噪",
                "type": "FLOAT",
                "min": 0.0,
                "max": 1.0,
                "current": default["second_pass"]["denoise"],
            },
            {
                "key": "second_pass.latent_scale",
                "label": "二次采样潜空间倍率",
                "type": "FLOAT",
                "min": 1.0,
                "max": 2.0,
                "current": default["second_pass"]["latent_scale"],
            },
            {
                "key": "upscale.enabled",
                "label": "RelayVSR X2 超分",
                "type": "BOOL",
                "current": default["upscale"]["enabled"],
            },
            {
                "key": "upscale.quality_preset",
                "label": "超分质量档",
                "type": "ENUM",
                "allowed": ["balanced", "quality", "fast"],
                "current": default["upscale"]["quality_preset"],
            },
        ],
        "apply_via": "production_start",
        "reversible": "NO",
        "gpu": True,
        "paid": False,
        "notes": "策略只在下一次开拍时冻结；不会替换已通过版本，也不会静默改变视频核心。",
    }


def contracts(db, project_id: str | None = None) -> dict[str, Any]:
    """All tunable contracts visible to this project (read-only)."""
    project = db.get(Project, project_id) if project_id else None
    if project_id and project is None:
        raise ParameterError("PROJECT_NOT_FOUND", "目标项目不存在。")
    workflow = [_workflow_contract(row) for row in _visible_profiles(db, project_id)]
    return {
        "version": "shine-copilot-parameters-2026.11.16-v2",
        "project_id": project_id,
        "contracts": [_language_contract(db, project_id), *workflow, _strategy_contract()],
        "notice": "调参只写用户级配置或独立工作流副本；冻结风格、主资产与内置模板不会被改动。",
        "forbidden": ["修改节点源码", "修改内置模板", "替换 MiniMax-H3 视频核心", "静默解冻导演风格"],
    }


def _dig(payload: dict[str, Any], dotted: str) -> Any:
    node: Any = payload
    for part in dotted.split("."):
        if not isinstance(node, dict):
            return None
        node = node.get(part)
    return node


def _merge(payload: dict[str, Any], dotted: str, value: Any) -> None:
    parts = dotted.split(".")
    node = payload
    for part in parts[:-1]:
        node = node.setdefault(part, {})
    node[parts[-1]] = value


def _diff(base: dict[str, Any], draft: dict[str, Any]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []

    def walk(prefix: str, before: Any, after: Any) -> None:
        if isinstance(before, dict) and isinstance(after, dict):
            for key in sorted(set(before) | set(after)):
                walk(f"{prefix}.{key}" if prefix else key, before.get(key), after.get(key))
        elif before != after:
            rows.append({"key": prefix, "before": before, "after": after})

    walk("", base, draft)
    return rows


_GRAPH_PATH_RE = None


def _graph_path(value: str) -> tuple[str, str] | None:
    global _GRAPH_PATH_RE
    if _GRAPH_PATH_RE is None:
        import re

        _GRAPH_PATH_RE = re.compile(r"^([A-Za-z0-9_]{1,64})\.inputs\.([A-Za-z0-9_]{1,64})$")
    match = _GRAPH_PATH_RE.match((value or "").strip())
    return (match.group(1), match.group(2)) if match else None


def _preview_workflow_map(
    db, *, values: dict[str, Any], target_id: str, project_id: str | None, name: str | None
) -> dict[str, Any]:
    profile = db.get(WorkflowProfile, target_id)
    if profile is None:
        raise ParameterError("PROFILE_NOT_FOUND", "找不到该工作流合同。")
    if profile.project_id is not None and project_id and profile.project_id != project_id:
        raise ParameterError("CROSS_PROJECT_PROFILE", "不能修改其它项目的私有工作流。")
    if name is not None and str(name).strip() in {"", profile.name}:
        raise ParameterError("NAME_REQUIRED", "新副本必须使用新的工作流名称。")
    current = dict(profile.parameter_map or {})
    unknown = sorted(set(values) - set(current))
    if unknown:
        raise ParameterError(
            "UNKNOWN_PARAMETER",
            f"只能调整该工作流已映射的参数：{', '.join(sorted(current))}",
        )
    graph = profile.graph if isinstance(profile.graph, dict) else {}
    draft = dict(current)
    for key, value in values.items():
        if not isinstance(value, str):
            raise ParameterError("PARAMETER_TYPE", f"{key} 必须是节点输入路径字符串。")
        parsed = _graph_path(value)
        if not parsed:
            raise ParameterError("PARAMETER_PATH", f"{key} 必须是 node_id.inputs.field 形式。")
        node_id, field = parsed
        node = graph.get(node_id)
        if not isinstance(node, dict):
            raise ParameterError("PARAMETER_NODE_MISSING", f"工作流里没有节点 {node_id}。")
        inputs = node.get("inputs")
        if not isinstance(inputs, dict) or field not in inputs:
            raise ParameterError("PARAMETER_INPUT_MISSING", f"节点 {node_id} 没有输入 {field}。")
        draft[key] = value.strip()
    diff = _diff(current, draft)
    proposed = None
    if diff:
        proposed = {
            "kind": "workflow_user_copy",
            "label": "创建工作流参数副本",
            "params": {
                "source_profile_id": profile.id,
                "name": str(name).strip(),
                "parameter_overrides": {key: draft[key] for key in values},
                "project_id": project_id,
            },
        }
    return {
        "target": f"workflow_map:{profile.id}",
        "changed": bool(diff),
        "diff": diff,
        "draft": draft,
        "proposed_action": proposed,
        "requires_input": [] if proposed or not diff else ["name"],
        "reversible": "YES",
        "notes": "只新增副本；源合同与内置模板不变，可执行后回滚删除副本。",
        "source_unchanged": True,
    }


def _preview_language(db, *, values: dict[str, Any], project_id: str | None) -> dict[str, Any]:
    from shine.services import canonical_project_language

    project = _project(db, project_id)
    if "language" not in values:
        raise ParameterError("PARAMETER_REQUIRED", "需要提供 language。")
    try:
        language = canonical_project_language(values["language"])
    except Exception as exc:  # HTTPException carries the product's own wording
        detail = getattr(exc, "detail", None)
        raise ParameterError("PARAMETER_VALUE", str(detail or exc)) from exc
    diff = [] if project.language == language else [{"key": "language", "before": project.language, "after": language}]
    return {
        "target": f"project_language:{project.id}",
        "changed": bool(diff),
        "diff": diff,
        "draft": {"language": language},
        "proposed_action": {
            "kind": "project_settings_update",
            "label": "修改项目口播语言",
            "params": {"project_id": project.id, "language": language},
        } if diff else None,
        "requires_input": [],
        "reversible": "YES",
        "notes": "不会触碰已冻结风格、主资产或生产合同。",
    }


def _preview_strategy(
    values: dict[str, Any], *, project_id: str | None, episode_ids: list[str] | None
) -> dict[str, Any]:
    base = DEFAULT_STRATEGY.model_dump(mode="json")
    merged = {key: (dict(value) if isinstance(value, dict) else value) for key, value in base.items()}
    unknown = sorted(set(values) - {field["key"] for field in _strategy_contract()["fields"]})
    if unknown:
        raise ParameterError("UNKNOWN_PARAMETER", f"策略没有这些字段：{', '.join(unknown)}")
    for key, value in values.items():
        _merge(merged, key, value)
    try:
        strategy = GenerationStrategy.model_validate(merged)
    except ValueError as exc:
        raise ParameterError("PARAMETER_VALUE", str(exc)[:300]) from exc
    draft = strategy.model_dump(mode="json")
    diff = _diff(base, draft)
    variants = {row["variant_id"]: row for row in model_variant_options()}
    chosen = variants.get(strategy.model_variant_id)
    return {
        "target": "production_strategy:",
        "changed": bool(diff),
        "diff": diff,
        "draft": draft,
        "strategy_sha256": strategy.strategy_sha256(),
        # Tuning is not shooting.  A strategy only exists inside the next
        # production submission, so nothing is saved and no action is proposed.
        "proposed_action": None,
        "pending_strategy": draft,
        "apply_via": "production_start",
        "scope": "NEXT_PRODUCTION_ONLY",
        "requires_input": ["episode_ids", "production_start_authorization"] if diff else [],
        "reversible": "NO",
        "hardware_note": (
            "该变体没有已验证记录，执行器会按原门禁拒绝或具名阻断。"
            if chosen and not chosen.get("verified")
            else "变体已验证记录存在，但仍以开拍时的真实门禁为准。"
        ),
        "notes": (
            "这是一份待使用策略，尚未保存也不会自动开拍；只有你在开拍时再次明确授权，"
            "它才会随那次生产提交冻结。不会替换已通过的镜头版本，也不会静默更换 MiniMax-H3 视频核心。"
        ),
    }


def preview_change(
    db,
    settings,
    *,
    target: str,
    values: dict[str, Any],
    project_id: str | None = None,
    name: str | None = None,
    episode_ids: list[str] | None = None,
) -> dict[str, Any]:
    """Validate a tuning request and describe exactly what would change."""
    if not isinstance(values, dict) or not values:
        raise ParameterError("PARAMETER_REQUIRED", "请提供要调整的参数。")
    target = str(target or "")
    if target.startswith("workflow_map:"):
        body = _preview_workflow_map(
            db,
            values=values,
            target_id=target.split(":", 1)[1],
            project_id=project_id,
            name=name,
        )
        contract = _workflow_contract(db.get(WorkflowProfile, target.split(":", 1)[1]))
    elif target.startswith("project_language:"):
        body = _preview_language(db, values=values, project_id=project_id)
        contract = _language_contract(db, project_id)
    elif target.startswith("production_strategy:"):
        body = _preview_strategy(values, project_id=project_id, episode_ids=episode_ids)
        contract = _strategy_contract()
    else:
        raise ParameterError("UNKNOWN_TARGET", "未知的参数目标；请从参数合同中选择。")
    return {
        "contract": contract,
        "preview": body,
        "notice": "以上只是差异预览，尚未修改任何配置；确认后才会调用原有执行器。",
    }
