"""A bounded, read-only plan for a natural-language operation request.

The planner never executes and never owns a second task system.  It reads the
same director overview / occupancy / parameter contracts the pages read, and
turns them into an ordered list of steps, each pointing at either

* a registered capability (which still needs its own preview + approval), or
* a step the person must perform in the original page, or
* an operation this build does not support (with the engineering reason).

That is the honest answer to "帮我准备好第一集并开始生成": what can be done for
them, what needs their decision, and what is still blocked by a real gate.
"""
from __future__ import annotations

import re
from typing import Any

from shine.models import Project
from shine.support.capability_catalog import plan_step_for
from shine.support.occupancy import release_occupancy

MAX_PLAN_STEPS = 8
SHOOTABLE_STATES = {"READY_TO_SHOOT", "READY_TO_PACKAGE", "SHOOTING"}

_INTENT_RULES: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("production", ("准备", "开拍", "开始生成", "开始拍摄", "开始生产", "把它拍", "生成第一集", "拍第一集")),
    ("materials", ("缺失素材", "缺素材", "素材没准备", "备料", "素材准备")),
    ("release", ("释放", "显存不足", "显卡", "GPU", "占用资源", "显卡显存")),
    ("parameters", ("调参", "参数", "二次采样", "RelayVSR", "超分", "采样", "画质")),
    ("create_project", ("新建项目", "建立项目", "创建一个项目", "建个项目", "建立一个", "建一个", "创建项目")),
    ("import_document", ("导入", "上传小说", "导入小说", "导入文本")),
    ("diagnose", ("为什么", "失败", "报错", "诊断", "原因", "怎么回事")),
    ("knowledge", ("是什么", "怎么用", "有什么区别", "介绍", "支持吗", "能不能")),
)


def detect_intent(text: str) -> str | None:
    lowered = (text or "").casefold()
    for intent, words in _INTENT_RULES:
        if any(word.casefold() in lowered for word in words):
            return intent
    return None


_STEP_LABELS = {
    "READY": "可执行（仍需预览并确认）",
    "READ_ONLY": "只读查询",
    "BLOCKED": "被原业务门禁阻塞",
    "HUMAN_REQUIRED": "需要你亲自确认",
    "NOT_SUPPORTED": "当前不对接",
}


def _numeral_ids(text: str, episodes: list[dict[str, Any]]) -> list[str]:
    """Resolve "第一集/第3集/全部" against real episode orders."""
    lowered = text or ""
    if "全部" in lowered or "每一集" in lowered or "所有集" in lowered:
        return [row["episode_id"] for row in episodes]
    digits = re.findall(r"第\s*([0-9]+)\s*集", lowered)
    numerals = {"一": 1, "二": 2, "两": 2, "三": 3, "四": 4, "五": 5, "六": 6, "七": 7, "八": 8, "九": 9, "十": 10}
    chinese = re.findall(r"第([一二两三四五六七八九十])\s*集", lowered)
    wanted = [int(value) for value in digits] + [numerals[value] for value in chinese]
    if not wanted:
        return []
    return [
        row["episode_id"]
        for row in episodes
        if isinstance(row.get("order"), int) and row["order"] in wanted
    ]


def _project(db, project_id: str | None) -> Project:
    if not project_id:
        raise ValueError("PROJECT_REQUIRED")
    project = db.get(Project, project_id)
    if project is None:
        raise ValueError("PROJECT_NOT_FOUND")
    return project


def _step_title(action_kind: str | None, fallback: str) -> str:
    return {
        "storyboard_generate": "生成这一集的导演分镜",
        "production_start": "开始真实拍摄",
        "document_import_text": "导入原文",
        "project_settings_update": "调整项目设置",
        "workflow_user_copy": "创建工作流参数副本",
        "managed_release": "释放受管渲染服务",
        "setup_repair": "补齐真实环境",
        "setup_reconcile": "核验并登记已有文件",
    }.get(action_kind or "", fallback)


def _production_plan(db, *, request: str, project_id: str, episode_ids: list[str] | None) -> dict[str, Any]:
    from shine.director.overview import build_overview

    project = _project(db, project_id)
    overview = build_overview(db, project)
    episodes = list(overview.get("episodes") or [])
    selected = [row for row in episodes if not episode_ids or row["episode_id"] in set(episode_ids)]
    if not selected:
        raise ValueError("EPISODE_SCOPE_REQUIRED")

    # Resolve "第一集" from the real plan order when the caller did not name ids.
    resolved = _numeral_ids(request, episodes)
    if resolved and not episode_ids:
        selected = [row for row in episodes if row["episode_id"] in set(resolved)]

    steps: list[dict[str, Any]] = [
        {
            "order": 1,
            "title": "读取项目与每集真实状态",
            "capability_id": "read_project_journey",
            "state": "READ_ONLY",
            "state_label": _STEP_LABELS["READ_ONLY"],
            "evidence": [
                f"{row.get('title') or row['episode_id']}：{row.get('state_label') or row['state']}"
                for row in selected[:6]
            ],
            "readback": "GET /api/v1/projects/{project_id}/journey",
            "reason": "状态来自既有导演台账，不新建第二套状态。",
        }
    ]
    shootable = [row for row in selected if row["state"] in SHOOTABLE_STATES]
    pending: list[dict[str, Any]] = []
    for row in selected:
        if row["state"] in SHOOTABLE_STATES:
            continue
        action = row.get("next_action") or {}
        mapping = plan_step_for(str(action.get("kind") or ""))
        if mapping["status"] == "COVERED" and mapping["ai_action"] == "storyboard_generate":
            pending.append(
                {
                    "order": 0,
                    "title": _step_title("storyboard_generate", action.get("label") or "推进这一集"),
                    "capability_id": "storyboard_generate",
                    "state": "READY",
                    "state_label": _STEP_LABELS["READY"],
                    "episode_id": row["episode_id"],
                    "evidence": [f"{row.get('title') or row['episode_id']}：{row.get('state_label') or row['state']}"],
                    "action": {"kind": "storyboard_generate", "params": {"project_id": project_id}},
                    "readback": "GET /api/v1/projects/{project_id}/director/storyboard/state",
                    "requires_confirmation": "EXECUTE_GUARDED",
                    "reason": "复用持久分镜任务；失败按集隔离。",
                }
            )
        else:
            pending.append(
                {
                    "order": 0,
                    "title": action.get("label") or "先在导演页处理这一集",
                    "capability_id": None,
                    "state": "HUMAN_REQUIRED" if mapping["status"] != "NOT_SUPPORTED" else "NOT_SUPPORTED",
                    "state_label": _STEP_LABELS[
                        "HUMAN_REQUIRED" if mapping["status"] != "NOT_SUPPORTED" else "NOT_SUPPORTED"
                    ],
                    "episode_id": row["episode_id"],
                    "evidence": [f"{row.get('title') or row['episode_id']}：{row.get('state_label') or row['state']}"],
                    "reason": (
                        "这一步涉及创作取舍或艺术确认，必须由你在原页面完成。"
                        if mapping["status"] != "NOT_SUPPORTED"
                        else "该操作还没有稳定的自然语言合同，当前不代理。"
                    ),
                }
            )

    # Placeholder orders are assigned below so the plan reads top-down.
    for indicator, step in enumerate(pending, start=len(steps) + 1):
        step["order"] = indicator
        steps.append(step)

    occupancy: dict[str, Any] | None = None
    if shootable:
        from shine.support.actions import _occupancy

        try:
            occupancy = _occupancy(db)
        except Exception as exc:  # noqa: BLE001 - an unreadable ledger is reported as unknown
            occupancy = {"busy": True, "code": "OCCUPANCY_UNREADABLE", "label": type(exc).__name__, "signals": []}
        steps.append(
            {
                "order": len(steps) + 1,
                "title": "开始真实拍摄选定集",
                "capability_id": "production_start",
                "state": "BLOCKED" if occupancy.get("busy") else "READY",
                "state_label": _STEP_LABELS["BLOCKED" if occupancy.get("busy") else "READY"],
                "evidence": [
                    *(
                        f"{row.get('title') or row['episode_id']}：{row.get('state_label') or row['state']}"
                        for row in shootable[:6]
                    ),
                    *(f"{signal}" for signal in (occupancy.get("signals") or [])[:3]),
                ],
                "action": {
                    "kind": "production_start",
                    "params": {
                        "project_id": project_id,
                        "episode_ids": [row["episode_id"] for row in shootable],
                    },
                },
                "readback": "GET /api/v1/production-runs/{run_id}",
                "requires_confirmation": "EXECUTE_GUARDED",
                "gpu": True,
                "reason": "只有原 Run 引擎能决定是否开拍；AI 不抢 GPU、不重复提交。",
            }
        )
        if occupancy.get("busy"):
            steps[-1]["reason"] = (
                f"当前 {occupancy.get('label') or occupancy.get('code')}；等任务结束或释放后，"
                "再重新预览这一集的开拍授权。"
            )

    requires: list[str] = []
    if not selected:
        requires.append("episode_ids")
    summary = (
        f"共 {len(selected)} 集：{len(shootable)} 集可开拍，{len(pending)} 个前置步骤需要处理。"
        if selected
        else "还没有可操作的分集。"
    )
    return {
        "intent": "PRODUCTION",
        "summary": summary,
        "project_id": project_id,
        "steps": steps[:MAX_PLAN_STEPS],
        "requires_input": requires,
        "notice": "计划只是只读建议；每个可执行步骤仍需单独预览并确认，才会调用原执行器。",
        "bounded": True,
    }


def _materials_plan(db, *, project_id: str, episode_ids: list[str] | None) -> dict[str, Any]:
    from shine.director.overview import build_overview

    project = _project(db, project_id)
    overview = build_overview(db, project)
    episodes = [
        row
        for row in overview.get("episodes") or []
        if not episode_ids or row["episode_id"] in set(episode_ids)
    ]
    steps = [
        {
            "order": 1,
            "title": "检查每集缺失素材",
            "capability_id": "read_missing_materials",
            "state": "READ_ONLY",
            "state_label": _STEP_LABELS["READ_ONLY"],
            "evidence": [
                f"{row.get('title') or row['episode_id']}："
                f"缺 {int((row.get('materials') or {}).get('missing_count') or 0)} 项"
                for row in episodes[:8]
            ],
            "readback": "GET /api/v1/projects/{project_id}/director/overview",
            "reason": "缺失清单来自既有备料台账；不会为凑齐而生成候选图。",
        }
    ]
    gaps = [
        (row.get("title") or row["episode_id"], item)
        for row in episodes
        for item in ((row.get("materials") or {}).get("missing") or [])[:5]
    ]
    if gaps:
        steps.append(
            {
                "order": 2,
                "title": "决定每项素材复用/生成/跳过",
                "capability_id": None,
                "state": "HUMAN_REQUIRED",
                "state_label": _STEP_LABELS["HUMAN_REQUIRED"],
                "evidence": [f"{title}：{item}" for title, item in gaps[:8]],
                "reason": "备料涉及版权与创作选择，必须由你在导演页确认；AI 只列出缺口。",
                "readback": "GET /api/v1/projects/{project_id}/director/materials/requirements",
            }
        )
    return {
        "intent": "MATERIALS",
        "summary": f"{len(episodes)} 集共 {len(gaps)} 项可见素材缺口。",
        "project_id": project_id,
        "steps": steps[:MAX_PLAN_STEPS],
        "requires_input": [],
        "notice": "素材缺口是事实，不是授权；AI 不会替你生成或采用素材。",
        "bounded": True,
    }


def _release_plan(db, *, project_id: str | None) -> dict[str, Any]:
    occupancy = release_occupancy(db)
    processes = occupancy.get("release_targets") or []
    return {
        "intent": "RELEASE",
        "summary": occupancy.get("label")
        or ("可以安全释放空闲的受管服务" if not occupancy["busy"] else "当前不能释放"),
        "project_id": project_id,
        "steps": [
            {
                "order": 1,
                "title": "读取受管服务与 GPU 占用",
                "capability_id": "read_gpu_occupancy",
                "state": "READ_ONLY",
                "state_label": _STEP_LABELS["READ_ONLY"],
                "evidence": [
                    f"{row.get('kind') or row.get('id')}：{row.get('state')}（在途 {bool(row.get('in_flight'))}）"
                    for row in processes[:6]
                ],
                "readback": "GET /api/v1/setup/managed-processes",
            },
            {
                "order": 2,
                "title": "安全释放受管渲染服务",
                "capability_id": "managed_release",
                "state": "BLOCKED" if occupancy["busy"] else "READY",
                "state_label": _STEP_LABELS["BLOCKED" if occupancy["busy"] else "READY"],
                "action": {
                    "kind": "managed_release",
                    "params": {
                        "only": [row["key"] for row in processes if row.get("key")][:10]
                    },
                },
                "readback": "/api/v1/setup/verdict",
                "requires_confirmation": "PREVIEW",
                "reason": (
                    "只停止本工作台拥有且能证明归属的服务；外部 ComfyUI、待核对结果与在途生产不会被强停。"
                ),
            },
        ][:MAX_PLAN_STEPS],
        "requires_input": [],
        "notice": "释放结果以受保护执行器的真实回执为准；没有释放证据就不会报告显存已释放。",
        "bounded": True,
    }


def _parameter_plan(db, *, project_id: str | None) -> dict[str, Any]:
    from shine.support.parameters import contracts

    payload = contracts(db, project_id)
    return {
        "intent": "PARAMETERS",
        "summary": f"可调合同 {len(payload['contracts'])} 组。",
        "project_id": project_id,
        "steps": [
            {
                "order": 1,
                "title": "读取可调参数合同",
                "capability_id": "read_parameter_contracts",
                "state": "READ_ONLY",
                "state_label": _STEP_LABELS["READ_ONLY"],
                "evidence": [row["label"] for row in payload["contracts"][:6]],
                "readback": "GET /api/v1/support/parameters/contracts",
            },
            {
                "order": 2,
                "title": "预览参数差异并确认",
                "capability_id": None,
                "state": "HUMAN_REQUIRED",
                "state_label": _STEP_LABELS["HUMAN_REQUIRED"],
                "reason": "参数差异必须先由你确认；`/support/parameters/preview` 只返回 diff，不写配置。",
                "requires_confirmation": "PREVIEW",
            },
        ],
        "requires_input": ["target", "values"],
        "notice": payload["notice"],
        "bounded": True,
    }


def _create_plan(*, project_id: str | None) -> dict[str, Any]:
    return {
        "intent": "CREATE_PROJECT",
        "summary": "可以创建一个空项目；不会自动导入原文或冻结风格。",
        "project_id": project_id,
        "steps": [
            {
                "order": 1,
                "title": "创建影视项目",
                "capability_id": "project_create",
                "state": "READY",
                "state_label": _STEP_LABELS["READY"],
                "action": {"kind": "project_create", "params": {}},
                "readback": "GET /api/v1/projects/{project_id}",
                "requires_confirmation": "PREVIEW",
                "reason": "标题等参数需要你补充；创建后仍是空项目。",
            }
        ],
        "requires_input": ["title"],
        "notice": "创建不会覆盖已有项目；也不代表环境已就绪。",
        "bounded": True,
    }


def _import_plan(*, project_id: str | None) -> dict[str, Any]:
    return {
        "intent": "IMPORT_DOCUMENT",
        "summary": "可以把一段原文文本导入当前项目；更长的原文请在导演页选择文件。",
        "project_id": project_id,
        "steps": [
            {
                "order": 1,
                "title": "导入原文",
                "capability_id": "document_import_text",
                "state": "READY" if project_id else "BLOCKED",
                "state_label": _STEP_LABELS["READY" if project_id else "BLOCKED"],
                "action": {"kind": "document_import_text", "params": {"project_id": project_id or ""}},
                "readback": "GET /api/v1/projects/{project_id}/script-basis",
                "requires_confirmation": "PREVIEW",
                "reason": (
                    "与界面导入同一解析、去重与落库；不会自动切换正式底稿。"
                    if project_id
                    else "需要先选定项目。"
                ),
            }
        ],
        "requires_input": ["text"],
        "notice": "导入不会自动生成大纲；生成大纲仍需下一步授权。",
        "bounded": True,
    }


def build_plan(
    db,
    settings,
    *,
    request: str,
    project_id: str | None = None,
    episode_ids: list[str] | None = None,
) -> dict[str, Any] | None:
    """The bounded plan for this request, or ``None`` when no operation applies."""
    intent = detect_intent(request or "")
    if intent == "production":
        if not project_id:
            return {
                "intent": "PRODUCTION",
                "summary": "需要先选定项目。",
                "project_id": None,
                "steps": [],
                "requires_input": ["project_id"],
                "notice": "拍摄必须绑定一个真实项目。",
                "bounded": True,
            }
        return _production_plan(db, request=request, project_id=project_id, episode_ids=episode_ids)
    if intent == "materials":
        if not project_id:
            return {
                "intent": "MATERIALS",
                "summary": "需要先选定项目。",
                "project_id": None,
                "steps": [],
                "requires_input": ["project_id"],
                "notice": "素材缺口按项目查询。",
                "bounded": True,
            }
        return _materials_plan(db, project_id=project_id, episode_ids=episode_ids)
    if intent == "release":
        return _release_plan(db, project_id=project_id)
    if intent == "parameters":
        return _parameter_plan(db, project_id=project_id)
    if intent == "create_project":
        return _create_plan(project_id=project_id)
    if intent == "import_document":
        return _import_plan(project_id=project_id)
    return None
