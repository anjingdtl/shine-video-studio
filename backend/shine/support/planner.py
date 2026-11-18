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
    ("setup", ("准备环境", "准备制作环境", "补齐环境", "补齐模型", "补齐缺失文件", "下载缺失文件",
               "修复制作环境", "核验已有文件", "登记已有文件")),
    ("materials", ("缺失素材", "缺素材", "素材没准备", "备料", "素材准备", "准备素材", "生成素材")),
    ("production", ("准备", "开拍", "开始生成", "开始拍摄", "开始生产", "把它拍", "生成第一集", "拍第一集",
                    "前置条件")),
    ("release", ("释放", "显存不足", "显卡", "GPU", "占用资源", "显卡显存")),
    ("parameters", ("调参", "参数", "生产策略", "策略", "二次采样", "RelayVSR", "超分", "采样", "画质")),
    ("literary_scripts", ("分集剧本", "文学剧本", "分集文学", "生成剧本", "剧本生成")),
    ("switch_basis", ("切换底稿", "切换原文", "更改底稿", "换底稿", "正式底稿")),
    ("outline", ("故事大纲", "大纲")),
    ("control", ("暂停", "继续", "取消生产", "停止生产", "取消任务")),
    ("create_project", ("新建项目", "建立项目", "创建一个项目", "建个项目", "建立一个", "建一个", "创建项目")),
    ("web_search", ("联网检索", "联网搜索", "网络搜索", "联网开关", "开启联网", "关闭联网")),
    ("import_document", ("导入", "上传小说", "导入小说", "导入文本", "导入原文")),
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

_LANGUAGE_WORDS: tuple[tuple[str, str], ...] = (
    ("粤语", "yue"),
    ("广东话", "yue"),
    ("国语", "zh"),
    ("普通话", "zh"),
    ("中文", "zh"),
    ("英语", "en"),
    ("英文", "en"),
    ("日语", "ja"),
    ("日文", "ja"),
)

_TITLE_PATTERNS = (
    re.compile(r"名叫\s*[《「“\"]?([^》」”\"，。,\.\s]{1,60})"),
    re.compile(r"叫做\s*[《「“\"]?([^》」”\"，。,\.\s]{1,60})"),
    re.compile(r"名称为\s*[《「“\"]?([^》」”\"，。,\.\s]{1,60})"),
    re.compile(r"[《「“\"]([^》」”\"]{1,60})[》」”\"]"),
)


def extract_title(text: str) -> str | None:
    """Only a title the person actually said; never invented."""
    for pattern in _TITLE_PATTERNS:
        match = pattern.search(text or "")
        if match:
            value = match.group(1).strip()
            if value and value not in {"短剧", "项目", "一个项目"}:
                return value[:120]
    return None


def extract_language(text: str) -> str | None:
    lowered = (text or "").casefold()
    for word, code in _LANGUAGE_WORDS:
        if word.casefold() in lowered:
            return code
    return None


def _requested_orders(text: str) -> list[int]:
    """The episode numbers the person actually named (1-based orders)."""
    lowered = text or ""
    digits = re.findall(r"第\s*([0-9]+)\s*集", lowered)
    numerals = {"一": 1, "二": 2, "两": 2, "三": 3, "四": 4, "五": 5, "六": 6, "七": 7, "八": 8, "九": 9, "十": 10}
    chinese = re.findall(r"第([一二两三四五六七八九十])\s*集", lowered)
    return [int(value) for value in digits] + [numerals[value] for value in chinese]


def _numeral_ids(text: str, episodes: list[dict[str, Any]]) -> list[str]:
    """Resolve "第一集/第3集/全部" against real episode orders."""
    lowered = text or ""
    if "全部" in lowered or "每一集" in lowered or "所有集" in lowered:
        return [row["episode_id"] for row in episodes]
    wanted = _requested_orders(lowered)
    if not wanted:
        return []
    return [
        row["episode_id"]
        for row in episodes
        if isinstance(row.get("order"), int) and row["order"] in wanted
    ]


def _cited_ids(text: str, values: list[str]) -> list[str]:
    return [value for value in values if value and value in (text or "")]


def _project(db, project_id: str | None) -> Project:
    if not project_id:
        raise ValueError("PROJECT_REQUIRED")
    project = db.get(Project, project_id)
    if project is None:
        raise ValueError("PROJECT_NOT_FOUND")
    return project


def _only_project(db) -> str | None:
    """Resolve "这个项目" only when the ledger is unambiguous.

    A session created before the first project exists has no project binding;
    with exactly one project in the ledger there is no guess to make.  Two or
    more projects stay ambiguous and the plan asks which one.
    """
    from sqlalchemy import select

    rows = list(db.scalars(select(Project).limit(2)).all())
    return rows[0].id if len(rows) == 1 else None


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
        "document_switch_basis": "切换正式原文底稿",
        "production_control": "控制生产任务",
        "literary_scripts_generate": "生成文学分集剧本",
        "story_outline_generate": "生成故事大纲",
    }.get(action_kind or "", fallback)


def _project_required(intent: str, notice: str) -> dict[str, Any]:
    return {
        "intent": intent,
        "summary": "需要先选定项目。",
        "project_id": None,
        "steps": [],
        "requires_input": ["project_id"],
        "notice": notice,
        "bounded": True,
    }


def _production_plan(db, *, request: str, project_id: str, episode_ids: list[str] | None) -> dict[str, Any]:
    from shine.director.overview import build_overview

    project = _project(db, project_id)
    overview = build_overview(db, project)
    episodes = list(overview.get("episodes") or [])
    selected = [row for row in episodes if episode_ids and row["episode_id"] in set(episode_ids)]
    if not selected and not episode_ids:
        selected = [row for row in episodes if row["episode_id"] in set(_numeral_ids(request, episodes))]
    if episode_ids and not selected:
        raise ValueError("EPISODE_SCOPE_REQUIRED")

    steps: list[dict[str, Any]] = [
        {
            "order": 1,
            "title": "读取项目与每集真实状态",
            "capability_id": "read_project_journey",
            "state": "READ_ONLY",
            "state_label": _STEP_LABELS["READ_ONLY"],
            "evidence": [
                f"{row.get('title') or row['episode_id']}：{row.get('state_label') or row['state']}"
                for row in (selected or episodes)[:6]
            ],
            "readback": "GET /api/v1/projects/{project_id}/journey",
            "reason": "状态来自既有导演台账，不新建第二套状态。",
        }
    ]

    if not selected:
        named = sorted(set(_requested_orders(request)))
        if named and not episodes:
            steps.append(
                {
                    "order": 2,
                    "title": "项目还没有分集",
                    "capability_id": None,
                    "state": "BLOCKED",
                    "state_label": _STEP_LABELS["BLOCKED"],
                    "evidence": ["导演台账里还没有分集；没有可供检查的第一集。"],
                    "reason": "先生成故事大纲与分集剧本/导演分镜，再检查开拍前置条件。",
                }
            )
            return {
                "intent": "PRODUCTION",
                "summary": f"项目还没有分集，无法检查第 {', '.join(str(value) for value in named)} 集。",
                "project_id": project_id,
                "steps": steps[:MAX_PLAN_STEPS],
                "requires_input": [],
                "notice": "不会凭空创建分集或猜测集号；请先完成文学与分镜阶段。",
                "bounded": True,
            }
        steps.append(
            {
                "order": 2,
                "title": "明确要操作的集数" if not named else "目标集不存在",
                "capability_id": None,
                "state": "HUMAN_REQUIRED" if not named else "BLOCKED",
                "state_label": _STEP_LABELS["HUMAN_REQUIRED" if not named else "BLOCKED"],
                "evidence": [
                    *(
                        [f"当前项目分集：{', '.join(str(row.get('order')) for row in episodes[:12])}"]
                        if named
                        else []
                    ),
                    "只说「开始生成」不会默认选择全部可开拍集。",
                ],
                "reason": (
                    "请明确第几集，或明确说「全部」；计划不会静默扩大范围。"
                    if not named
                    else "当前项目里没有你点名的集；不会猜测集号。"
                ),
            }
        )
        return {
            "intent": "PRODUCTION",
            "summary": "先明确这次要操作哪些集。" if not named else "项目里没有你点名的集。",
            "project_id": project_id,
            "steps": steps[:MAX_PLAN_STEPS],
            "requires_input": ["episode_ids"],
            "notice": "不会默认选择全部集；请在计划里填写或改说「全部」。",
            "bounded": True,
        }

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

    summary = f"共 {len(selected)} 集：{len(shootable)} 集可开拍，{len(pending)} 个前置步骤需要处理。"
    return {
        "intent": "PRODUCTION",
        "summary": summary,
        "project_id": project_id,
        "steps": steps[:MAX_PLAN_STEPS],
        "requires_input": [],
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
        "summary": f"可调合同 {len(payload['contracts'])} 组；调参不会自动开拍。",
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
                "reason": "参数差异必须先由你确认；`/support/parameters/preview` 只返回 diff，"
                "不写配置，也不会自动开拍。",
                "requires_confirmation": "PREVIEW",
            },
        ],
        "requires_input": ["target", "values"],
        "notice": payload["notice"],
        "bounded": True,
    }


def _create_plan(*, request: str, project_id: str | None) -> dict[str, Any]:
    title = extract_title(request)
    language = extract_language(request)
    params: dict[str, Any] = {}
    if title:
        params["title"] = title
    if language:
        params["language"] = language
    ready = bool(title)
    return {
        "intent": "CREATE_PROJECT",
        "summary": "可以创建一个空项目；不会自动导入原文或冻结风格。",
        "project_id": project_id,
        "steps": [
            {
                "order": 1,
                "title": "创建影视项目",
                "capability_id": "project_create",
                "state": "READY" if ready else "HUMAN_REQUIRED",
                "state_label": _STEP_LABELS["READY" if ready else "HUMAN_REQUIRED"],
                "evidence": [f"标题来自你的原话：{title}"] if title else [],
                "action": {"kind": "project_create", "params": params},
                "readback": "GET /api/v1/projects/{project_id}",
                "requires_confirmation": "PREVIEW",
                "reason": (
                    "标题已从你的原话提取；创建后仍是空项目。"
                    if title
                    else "还需要一个项目标题；补齐后即可预览。"
                ),
            }
        ],
        "requires_input": [] if ready else ["title"],
        "notice": "创建不会覆盖已有项目；也不代表环境已就绪。",
        "bounded": True,
    }


def _web_search_plan(*, request: str, project_id: str | None) -> dict[str, Any]:
    lowered = request or ""
    disabled = any(word in lowered for word in ("关闭", "禁用", "关掉", "停止联网"))
    enabled = not disabled
    return {
        "intent": "WEB_SEARCH",
        "summary": f"可以把管家的联网检索{'关闭' if disabled else '开启'}；只改检索开关。",
        "project_id": project_id,
        "steps": [
            {
                "order": 1,
                "title": "关闭管家联网检索" if disabled else "开启管家联网检索",
                "capability_id": "support_web_search_update",
                "state": "READY",
                "state_label": _STEP_LABELS["READY"],
                "evidence": ["官方资料仍只作参考证据，不执行下载或脚本。"],
                "action": {
                    "kind": "support_web_search_update",
                    "params": {"web_search": enabled},
                },
                "readback": "GET /api/v1/support/preferences",
                "requires_confirmation": "PREVIEW",
                "reason": "只写一个本机偏好；不会触发任何生产任务。",
            }
        ],
        "requires_input": [],
        "notice": "联网检索开关只影响 AI 管家自身的检索，不代表本机已支持官方能力。",
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
                # The text itself is the person's own file/content and is never
                # generated by the model, so the step waits for them to supply
                # it in the panel instead of pretending to be ready.
                "state": "HUMAN_REQUIRED" if project_id else "BLOCKED",
                "state_label": _STEP_LABELS["HUMAN_REQUIRED" if project_id else "BLOCKED"],
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
        "requires_input": ([] if project_id else ["project_id"]) + ["text"],
        "notice": "导入不会自动生成大纲；生成大纲仍需下一步授权。",
        "bounded": True,
    }


def _outline_plan(db, *, project_id: str) -> dict[str, Any]:
    from sqlalchemy import select

    from shine.models import ProjectDocument

    project = _project(db, project_id)
    documents = list(
        db.scalars(
            select(ProjectDocument)
            .where(ProjectDocument.project_id == project.id)
            .order_by(ProjectDocument.created_at.desc())
        ).all()
    )
    if not documents:
        return {
            "intent": "OUTLINE",
            "summary": "项目还没有原文底稿，无法生成故事大纲。",
            "project_id": project_id,
            "steps": [
                {
                    "order": 1,
                    "title": "先导入原文",
                    "capability_id": "document_import_text",
                    "state": "HUMAN_REQUIRED",
                    "state_label": _STEP_LABELS["HUMAN_REQUIRED"],
                    "action": {"kind": "document_import_text", "params": {"project_id": project_id}},
                    "reason": "请在计划里粘贴原文或选择本地文件；模型不会替你编写正文。",
                }
            ],
            "requires_input": ["text"],
            "notice": "大纲必须基于你自己的原文。",
            "bounded": True,
        }
    return {
        "intent": "OUTLINE",
        "summary": "可以用当前底稿生成故事大纲；确认大纲仍由你亲自完成。",
        "project_id": project_id,
        "steps": [
            {
                "order": 1,
                "title": "以当前正式底稿生成故事大纲",
                "capability_id": "story_outline_generate",
                "state": "READY",
                "state_label": _STEP_LABELS["READY"],
                "evidence": [f"底稿：{documents[0].filename}"],
                "action": {"kind": "story_outline_generate", "params": {"project_id": project_id}},
                "readback": "GET /api/v1/projects/{project_id}/story-preparation",
                "requires_confirmation": "EXECUTE_GUARDED",
                "reason": "复用持久大纲任务；模型角色未配置时会由原门禁具名拒绝。",
            },
            {
                "order": 2,
                "title": "确认大纲",
                "capability_id": None,
                "state": "HUMAN_REQUIRED",
                "state_label": _STEP_LABELS["HUMAN_REQUIRED"],
                "reason": "大纲是创作方向的裁决，必须由你在导演页确认。",
            },
        ],
        "requires_input": [],
        "notice": "生成大纲不等于确认大纲。",
        "bounded": True,
    }


def _control_plan(db, *, request: str, project_id: str | None) -> dict[str, Any]:
    from sqlalchemy import select

    from shine.production.models import ProductionRun

    query = select(ProductionRun).order_by(ProductionRun.created_at.desc())
    if project_id:
        query = query.where(ProductionRun.project_id == project_id)
    runs = [
        row
        for row in db.scalars(query).all()
        if str(row.status) not in {"COMPLETED", "FAILED", "CANCELLED", "SUPERSEDED"}
    ]
    if not runs:
        return {
            "intent": "CONTROL",
            "summary": "当前没有可暂停/继续/取消的真实生产任务。",
            "project_id": project_id,
            "steps": [],
            "requires_input": [],
            "notice": "不会创建一个假任务来满足控制请求。",
            "bounded": True,
        }
    cited = _cited_ids(request, [row.id for row in runs])
    if len(cited) == 1:
        runs = [row for row in runs if row.id == cited[0]]
    elif len(runs) > 1:
        return {
            "intent": "CONTROL",
            "summary": f"有 {len(runs)} 个在途生产任务，需要先选定一个。",
            "project_id": project_id,
            "steps": [],
            "requires_input": ["run_id"],
            "notice": "不会一次控制多个任务。",
            "bounded": True,
        }
    run = runs[0]
    if "暂停" in (request or ""):
        operation = "pause"
    elif "取消" in (request or "") or "停止" in (request or ""):
        operation = "cancel"
    elif "继续" in (request or "") or "恢复" in (request or ""):
        operation = "resume"
    else:
        return {
            "intent": "CONTROL",
            "summary": "请说明是暂停、继续还是取消。",
            "project_id": run.project_id,
            "steps": [],
            "requires_input": ["operation"],
            "notice": "只支持 pause/resume/cancel。",
            "bounded": True,
        }
    return {
        "intent": "CONTROL",
        "summary": f"将{ {'pause': '暂停', 'resume': '继续', 'cancel': '取消'}[operation] }这一个生产任务。",
        "project_id": run.project_id,
        "steps": [
            {
                "order": 1,
                "title": f"{ {'pause': '暂停', 'resume': '继续', 'cancel': '取消'}[operation] }生产任务",
                "capability_id": "production_control",
                "state": "READY",
                "state_label": _STEP_LABELS["READY"],
                "evidence": [f"Run {run.id}：{run.status}"],
                "action": {"kind": "production_control", "params": {"run_id": run.id, "operation": operation}},
                "readback": "GET /api/v1/production-runs/{run_id}",
                "requires_confirmation": "EXECUTE_GUARDED",
                "gpu": True,
                "reason": "只操作这一个已存在的 Run；复用原状态机与版本校验。",
            }
        ],
        "requires_input": [],
        "notice": "不会创建新任务，也不会取消其它运行。",
        "bounded": True,
    }


def _literary_plan(db, *, request: str, project_id: str) -> dict[str, Any]:
    from shine.director import pipeline

    project = _project(db, project_id)
    plan = pipeline.current_volume_plan(db, project.id)
    volumes = list((plan.payload_json or {}).get("volumes") or []) if plan else []
    if not volumes:
        return {
            "intent": "LITERARY_SCRIPTS",
            "summary": "还没有已确认的分集方案，无法生成分集剧本。",
            "project_id": project_id,
            "steps": [
                {
                    "order": 1,
                    "title": "先确认分集方案",
                    "capability_id": None,
                    "state": "HUMAN_REQUIRED",
                    "state_label": _STEP_LABELS["HUMAN_REQUIRED"],
                    "reason": "请在原导演页生成并确认分集方案与圣经，再回来生成分集剧本。",
                }
            ],
            "requires_input": [],
            "notice": "不会跳过文学门禁直接生成剧本。",
            "bounded": True,
        }
    cited = _cited_ids(request, [str(row.get("volume_id")) for row in volumes])
    if len(cited) == 1:
        volume = next(row for row in volumes if str(row.get("volume_id")) == cited[0])
    elif len(volumes) == 1:
        volume = volumes[0]
    else:
        return {
            "intent": "LITERARY_SCRIPTS",
            "summary": f"有 {len(volumes)} 卷，需要先选定一卷。",
            "project_id": project_id,
            "steps": [],
            "requires_input": ["volume_id"],
            "notice": "不会默认扩大全部卷。",
            "bounded": True,
        }
    return {
        "intent": "LITERARY_SCRIPTS",
        "summary": f"为《{volume.get('title') or volume.get('volume_id')}》生成缺失的分节剧本。",
        "project_id": project_id,
        "steps": [
            {
                "order": 1,
                "title": "生成文学分集剧本",
                "capability_id": "literary_scripts_generate",
                "state": "READY",
                "state_label": _STEP_LABELS["READY"],
                "evidence": [
                    f"卷：{volume.get('title') or volume.get('volume_id')}",
                    f"分节：{len(volume.get('sections') or [])}",
                ],
                "action": {
                    "kind": "literary_scripts_generate",
                    "params": {"project_id": project_id, "volume_id": volume.get("volume_id")},
                },
                "readback": "GET /api/v1/projects/{project_id}/pipeline/volumes/{volume_id}",
                "requires_confirmation": "EXECUTE_GUARDED",
                "paid": True,
                "reason": "只排队还没有确认剧本的分节，不覆盖已确认稿。",
            }
        ],
        "requires_input": [],
        "notice": "确认剧本仍由你在导演页完成。",
        "bounded": True,
    }


def _switch_plan(db, *, request: str, project_id: str) -> dict[str, Any]:
    from sqlalchemy import select

    from shine.models import ProjectDocument

    project = _project(db, project_id)
    documents = list(
        db.scalars(
            select(ProjectDocument)
            .where(ProjectDocument.project_id == project.id)
            .order_by(ProjectDocument.created_at.desc())
        ).all()
    )
    candidates = [row for row in documents if row.id != project.current_document_id]
    cited = _cited_ids(request, [row.id for row in candidates] + [row.filename for row in candidates])
    if len(cited) == 1:
        match = next(
            row for row in candidates if row.id == cited[0] or row.filename == cited[0]
        )
    elif len(candidates) == 1:
        match = candidates[0]
    else:
        return {
            "intent": "SWITCH_BASIS",
            "summary": (
                "当前项目没有其它已导入文档可切换。"
                if not candidates
                else f"有 {len(candidates)} 份候选文档，需要先选定一份。"
            ),
            "project_id": project_id,
            "steps": [],
            "requires_input": [] if not candidates else ["document_id"],
            "notice": "不会自动切换正式底稿。",
            "bounded": True,
        }
    return {
        "intent": "SWITCH_BASIS",
        "summary": f"把《{match.filename}》切换为正式原文底稿。",
        "project_id": project_id,
        "steps": [
            {
                "order": 1,
                "title": "切换正式原文底稿",
                "capability_id": "document_switch_basis",
                "state": "READY",
                "state_label": _STEP_LABELS["READY"],
                "evidence": [f"候选：{match.filename}"],
                "action": {
                    "kind": "document_switch_basis",
                    "params": {"project_id": project_id, "document_id": match.id},
                },
                "readback": "GET /api/v1/projects/{project_id}/script-basis",
                "requires_confirmation": "PREVIEW",
                "reason": "只切换底稿指针；原文与其它文档不变。",
            }
        ],
        "requires_input": [],
        "notice": "旧导演包仍按原 id 可读。",
        "bounded": True,
    }


def _setup_plan(db, settings, *, request: str, project_id: str | None) -> dict[str, Any]:
    from shine.comfyops.setup_summary import summary as build_setup_summary
    from shine.support.occupancy import resource_occupancy

    summary = build_setup_summary(db, settings, project_id=project_id, variant_id=None, discovered=[])
    verdict = summary.get("verdict") or {}
    capabilities = summary.get("capabilities") or {}
    occupancy = resource_occupancy(db)
    lowered = (request or "").casefold()
    evidence = [
        f"生产裁决：{verdict.get('label') or verdict.get('mode') or '未知'}"
        f"（{verdict.get('code') or '无错误码'}）"
    ]
    for key, row in capabilities.items():
        if isinstance(row, dict) and row.get("missing_count"):
            evidence.append(f"{row.get('label') or key}：缺 {row.get('missing_count')} 个文件")

    steps: list[dict[str, Any]] = [
        {
            "order": 1,
            "title": "读取制作环境与能力就绪事实",
            "capability_id": "read_setup_verdict",
            "state": "READ_ONLY",
            "state_label": _STEP_LABELS["READ_ONLY"],
            "evidence": evidence[:6],
            "readback": "GET /api/v1/setup/summary",
        }
    ]
    if any(word in lowered for word in ("核验", "登记已有")):
        cap = "image" if "出图" in lowered else "h3"
        step_state = "BLOCKED" if occupancy["busy"] else "READY"
        steps.append(
            {
                "order": 2,
                "title": "核验并登记已有本地文件",
                "capability_id": "setup_reconcile",
                "state": step_state,
                "state_label": _STEP_LABELS[step_state],
                "action": {
                    "kind": "setup_reconcile",
                    "params": {"capability": cap, "adopt": False},
                },
                "readback": "GET /api/v1/setup/summary",
                "requires_confirmation": "PREVIEW",
                "reason": "交给后台整备 Worker 执行字节核验与登记，不产生重复下载。",
            }
        )
        return {
            "intent": "SETUP",
            "summary": "读取制作环境台账，并在预览确认后交给后台 Worker 核验登记已有文件。",
            "project_id": project_id,
            "steps": steps,
            "requires_input": [],
            "notice": "只核验登记已有字节，不会自动发起网络下载。",
            "bounded": True,
        }

    explicit_map = (
        ("h3", ("h3", "视频")),
        ("image", ("出图", "qwen")),
        ("vlm", ("审片", "vlm")),
        ("asr", ("语音", "asr")),
        ("relayvsr", ("精修", "relayvsr", "超分")),
    )
    wanted = [
        cap
        for cap, words in explicit_map
        if any(word in lowered for word in words)
    ]
    if not wanted:
        wanted = [
            key
            for key, row in capabilities.items()
            if isinstance(row, dict)
            and not row.get("optional")
            and (row.get("missing_count") or not (row.get("facts") or {}).get("PRODUCTION_READY"))
        ]
    if not wanted and verdict.get("mode") == "REAL_READY":
        return {
            "intent": "SETUP",
            "summary": "本机必选制作环境已就绪，无需重复准备；如需可选增强可显式指定能力。",
            "project_id": project_id,
            "steps": steps,
            "requires_input": [],
            "notice": "不会在环境已就绪时隐式重跑整备或下载可选模块。",
            "bounded": True,
        }
    if not wanted:
        wanted = ["h3", "image"]
    step_state = "BLOCKED" if occupancy["busy"] else "READY"
    steps.append(
        {
            "order": 2,
            "title": "准备并核验指定制作环境能力",
            "capability_id": "setup_repair",
            "state": step_state,
            "state_label": _STEP_LABELS[step_state],
            "action": {
                "kind": "setup_repair",
                "params": {"capabilities": wanted, "verify_runtime": True},
            },
            "readback": "GET /api/v1/setup/prepare/{task_id}",
            "requires_confirmation": "EXECUTE_GUARDED",
            "reason": "复用统一整备任务队列与下载计划裁决；仍需先预览下载清单并确认。",
        }
    )
    return {
        "intent": "SETUP",
        "summary": f"按同一份环境台账准备 {', '.join(wanted)}（需预览确认后由后台整备 Worker 执行）。",
        "project_id": project_id,
        "steps": steps,
        "requires_input": [],
        "notice": "预览确认前不会写入配置或发起下载。",
        "bounded": True,
    }


def _build_plan_raw(
    db,
    settings,
    *,
    request: str,
    project_id: str | None = None,
    episode_ids: list[str] | None = None,
) -> dict[str, Any] | None:
    """The bounded plan for this request, or ``None`` when no operation applies."""
    intent = detect_intent(request or "")
    if project_id is None:
        project_id = _only_project(db)
    if intent == "setup":
        return _setup_plan(db, settings, request=request, project_id=project_id)
    if intent == "production":
        if not project_id:
            return _project_required("PRODUCTION", "拍摄必须绑定一个真实项目。")
        return _production_plan(db, request=request, project_id=project_id, episode_ids=episode_ids)
    if intent == "materials":
        if not project_id:
            return _project_required("MATERIALS", "素材缺口按项目查询。")
        return _materials_plan(db, project_id=project_id, episode_ids=episode_ids)
    if intent == "release":
        return _release_plan(db, project_id=project_id)
    if intent == "parameters":
        return _parameter_plan(db, project_id=project_id)
    if intent == "literary_scripts":
        if not project_id:
            return _project_required("LITERARY_SCRIPTS", "分集剧本按项目生成。")
        return _literary_plan(db, request=request, project_id=project_id)
    if intent == "switch_basis":
        if not project_id:
            return _project_required("SWITCH_BASIS", "底稿按项目切换。")
        return _switch_plan(db, request=request, project_id=project_id)
    if intent == "outline":
        if not project_id:
            return _project_required("OUTLINE", "故事大纲按项目生成。")
        return _outline_plan(db, project_id=project_id)
    if intent == "control":
        return _control_plan(db, request=request, project_id=project_id)
    if intent == "create_project":
        return _create_plan(request=request, project_id=project_id)
    if intent == "web_search":
        return _web_search_plan(request=request, project_id=project_id)
    if intent == "import_document":
        return _import_plan(project_id=project_id)
    return None


def _bind_contracts(plan: dict[str, Any] | None) -> dict[str, Any] | None:
    """Attach the packaged contract to every planned action (V2.1).

    Planning never invents a capability, and a capability whose contract does
    not belong to this build is shown as BLOCKED with the plain-language notice
    instead of pretending it can be previewed.
    """
    if not plan:
        return plan
    from shine.support import contracts as contract_registry

    for step in plan.get("steps") or []:
        action = step.get("action") or {}
        kind = action.get("kind")
        if not kind:
            continue
        state = contract_registry.execution_state(str(kind))
        action["contract"] = {
            "action_id": kind,
            "state": state["state"],
            "contract_digest": state.get("contract_digest"),
            "contract_revision": state.get("contract_revision"),
        }
        if not state["executable"] and step.get("state") in {"READY", "COVERED"}:
            step["state"] = "BLOCKED"
            step["notice"] = state["notice"]
    return plan


def identify_plan(plan: dict[str, Any], episode_ids: list[str] | None = None) -> dict[str, Any]:
    """Give a previewed/chat plan its immutable identity.

    Only semantic plan content and the explicitly previewed scope are hashed,
    never a storage timestamp, so re-rendering the same plan is stable while any
    changed step produces a new identity.
    """
    from shine.copilot.contracts import canonical, digest

    scope = list(episode_ids if episode_ids is not None else plan.get("episode_ids") or [])
    core = {
        "intent": plan.get("intent"),
        "project_id": plan.get("project_id"),
        "episode_ids": scope,
        "steps": plan.get("steps") or [],
        "requires_input": plan.get("requires_input") or [],
    }
    plan_hash = digest(canonical(core))
    return {
        **plan,
        "episode_ids": scope,
        "plan_id": f"plan-{plan_hash[:16]}",
        "plan_hash": plan_hash,
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
    return _bind_contracts(
        _build_plan_raw(
            db,
            settings,
            request=request,
            project_id=project_id,
            episode_ids=episode_ids,
        )
    )
