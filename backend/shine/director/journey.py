"""Derive the six-stage journey from the project's real server state.

Every completion flag here is computed from the ledger, never from a browser
counter.  The rule the whole workbench depends on is: a stage is only complete
for the *current scope* (the confirmed package revision), so "one shot passed"
or "one material uploaded" can never light up a whole stage.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from shine.director.models import DirectorEpisodePlan
from shine.director.package import latest_confirmed_plan, latest_package, package_readiness
from shine.models import Asset, Job, Project, Shot, StyleVersion
from shine.schemas import ShotSpec


def plan_shot_order(plan: DirectorEpisodePlan) -> list[str]:
    """The planned shot order of a confirmed episode plan, in episode order."""
    return [
        shot_id
        for episode in plan.payload.get("episodes", [])
        for shot_id in episode.get("shot_ids", [])
    ]

STAGES = [
    ("script", "创意与剧本"),
    ("plan", "剧本统筹"),
    ("storyboard", "镜头拆解"),
    ("assets", "拍摄素材准备"),
    ("package", "导演包"),
    ("production", "视频生产"),
    ("post", "后期成片"),
]


def _passed_shots(db: Session, project_id: str) -> dict[str, Job]:
    """The current passed job per shot: passed, not stale, matching the revision."""
    rows = db.scalars(
        select(Job)
        .where(Job.project_id == project_id, Job.status == "PASSED", Job.stale.is_(False))
        .order_by(Job.created_at)
    ).all()
    shots = {shot.id: shot for shot in db.scalars(select(Shot).where(Shot.project_id == project_id)).all()}
    passed: dict[str, Job] = {}
    for job in rows:
        shot = shots.get(job.shot_id)
        if shot is None or shot.revision != job.shot_revision or not job.artifact_id:
            continue
        passed[job.shot_id] = job
    return passed


def _shot_problems(db: Session, shot: Shot) -> list[str]:
    """What the shot itself still lacks: its own contract, nothing else."""
    problems: list[str] = []
    try:
        spec = ShotSpec.model_validate(shot.spec)
    except Exception:
        return ["SHOT_SPEC_INVALID"]
    if not spec.story_beat.strip():
        problems.append("SHOT_BEAT_EMPTY")
    return problems


def _shot_material_problems(db: Session, shot: Shot) -> list[str]:
    """What the material library still owes this shot.

    The requirement comes from the storyboard, never from the script: the
    shot names what it needs, and only then does the library have to answer.
    A character the shot names counts as a requirement: a name alone is not
    renderable, so the shot must carry the bound master.
    """
    from shine.asset_identity import resolve_character_asset

    problems: list[str] = []
    try:
        spec = ShotSpec.model_validate(shot.spec)
    except Exception:
        return []
    for asset_id in spec.required_asset_ids:
        asset = db.get(Asset, asset_id)
        if asset is None:
            problems.append("SHOT_REQUIRED_ASSET_MISSING")
        elif not (asset.approved and asset.locked):
            problems.append("SHOT_REQUIRED_ASSET_NOT_MASTER")
    if not list(shot.bindings) and spec.required_asset_ids:
        problems.append("SHOT_REQUIRED_ASSET_UNBOUND")
    bound = []
    for link in shot.bindings:
        asset = db.get(Asset, link.asset_id)
        if asset is None:
            continue
        bound.append({"id": asset.id, "kind": asset.kind, "role": asset.role,
                      "entity_id": asset.entity_id})
    for name in spec.characters:
        reference = str(name or "").strip()
        if reference and resolve_character_asset(reference, bound) is None:
            problems.append("SHOT_CHARACTER_MASTER_MISSING:" + reference)
    return problems


def compute_journey(db: Session, project: Project) -> dict[str, Any]:
    project_id = project.id
    shots = list(db.scalars(select(Shot).where(Shot.project_id == project_id)).all())
    style = db.get(StyleVersion, project.frozen_style_version_id) if project.frozen_style_version_id else None
    plan = latest_confirmed_plan(db, project_id)
    package = latest_package(db, project_id)
    ready = package_readiness(db, package) if package is not None else None
    passed = _passed_shots(db, project_id)
    planned_shot_ids = plan_shot_order(plan) if plan is not None else [shot.id for shot in shots]
    by_id = {shot.id: shot for shot in shots}
    scoped_shots = [by_id[shot_id] for shot_id in planned_shot_ids if shot_id in by_id]

    from shine.models import AgentArtifact, ProjectDocument

    outline = db.scalars(
        select(AgentArtifact)
        .where(
            AgentArtifact.project_id == project_id,
            AgentArtifact.kind == "StoryOutline",
            AgentArtifact.stale.is_(False),
        )
        .order_by(AgentArtifact.created_at.desc())
    ).first()
    document = db.scalars(
        select(ProjectDocument)
        .where(ProjectDocument.project_id == project_id)
        .order_by(ProjectDocument.created_at.desc())
    ).first()

    stages: list[dict[str, Any]] = []

    from shine.director.creative import brief_rows

    briefs = brief_rows(db, project_id)
    brief = briefs[0] if briefs else None

    # ① 创意与剧本: a confirmed outline, or an explicitly authored shot list.
    #    An idea (creative brief) alone is not a script: it is the entry that
    #    produces one, so the stage reports it without lighting up.
    script_done = bool(outline and outline.approved_at) or bool(shots)
    if outline and outline.approved_at:
        script_summary = f"大纲已确认（{outline.sha256[:8]}）"
    elif shots:
        script_summary = f"已手工建立 {len(shots)} 个分镜"
    elif outline:
        script_summary = "剧本大纲待确认"
    elif document:
        script_summary = f"已有剧本「{document.filename}」，待整理大纲"
    elif brief and brief.approved_at:
        script_summary = "创意简报已确认，等待生成剧本"
    elif brief:
        script_summary = "创意简报待确认"
    else:
        script_summary = "还没有剧本：输入想法或导入现成剧本"
    stages.append({
        "id": "script",
        "label": "创意与剧本",
        "done": script_done,
        "summary": script_summary,
        "blockers": [] if script_done else [{"code": "SCRIPT_NOT_PREPARED",
                                            "message": "输入想法生成剧本，或导入现成剧本并确认大纲"}],
        "facts": {
            "document_id": document.id if document else None,
            "document_sha256": document.sha256 if document else None,
            "document_source": document.source_format if document else None,
            "outline_artifact_id": outline.id if outline else None,
            "outline_confirmed": bool(outline and outline.approved_at),
            "brief_id": brief.id if brief else None,
            "brief_confirmed": bool(brief and brief.approved_at),
        },
    })

    # ② 剧本统筹: the episode plan and the frozen style, nothing else.
    #    Materials are not decided here.  What a shot needs is a question
    #    the storyboard asks, and the storyboard comes after this stage.
    plan_blockers: list[dict[str, Any]] = []
    if not plan:
        plan_blockers.append({"code": "EPISODE_PLAN_NOT_CONFIRMED", "message": "分集方案尚未确认"})
    if style is None:
        plan_blockers.append({"code": "STYLE_NOT_FROZEN", "message": "风格尚未冻结"})
    stages.append({
        "id": "plan",
        "label": "剧本统筹",
        "done": not plan_blockers,
        "summary": (
            f"{len(plan.payload.get('episodes', []))} 集 · 风格第 {style.version} 版" if plan and style
            else ("分集方案待确认" if style is None else "风格待冻结")
        ),
        "blockers": plan_blockers,
        "facts": {
            "episode_plan_id": plan.id if plan else None,
            "episode_plan_version": plan.version if plan else None,
            "style_version_id": style.id if style else None,
            "style_sha256": style.sha256 if style else None,
        },
    })

    # ③ 镜头拆解: every planned shot exists and its own contract is whole.
    #    A script-first plan is only storyboarded once it was materialised into
    #    the shots its sections produced; until then the stage names the gap.
    #    Materials are deliberately NOT a condition here: the design is done
    #    first, and what it asks for is answered in the next stage.
    shot_blockers: list[dict[str, Any]] = []
    plan_mode = (plan.payload or {}).get("mode", "shots") if plan is not None else None
    plan_materialized = bool(plan is not None and all(
        episode.get("shot_ids") for episode in (plan.payload or {}).get("episodes", [])
    ))
    if plan is not None and plan_mode == "sections" and not plan_materialized:
        shot_blockers.append({"code": "EPISODE_PLAN_NOT_MATERIALIZED",
                              "message": "按剧本分好的集还没有全部拆成镜头"})
    for shot_id in planned_shot_ids:
        shot = by_id.get(shot_id)
        if shot is None:
            shot_blockers.append({"code": "PLANNED_SHOT_MISSING", "message": "计划中的镜头尚未建立",
                                  "shot_id": shot_id})
            continue
        for problem in _shot_problems(db, shot):
            shot_blockers.append({"code": problem, "message": f"镜头「{shot.name}」需要补齐",
                                  "shot_id": shot.id})
    storyboard_summary = f"{len(planned_shot_ids)} 个计划内镜头" if plan is not None else f"{len(shots)} 个镜头"
    if plan is not None and len(shots) != len(planned_shot_ids):
        storyboard_summary += f"（项目共 {len(shots)} 个）"
    stages.append({
        "id": "storyboard",
        "label": "镜头拆解",
        "done": bool(shots) and bool(planned_shot_ids) and not shot_blockers,
        "summary": storyboard_summary if shots or planned_shot_ids else "还没有分镜",
        "blockers": shot_blockers,
        "facts": {"shot_count": len(shots), "planned_shot_count": len(planned_shot_ids),
                  "planned_shot_ids": planned_shot_ids,
                  "plan_mode": plan_mode, "plan_materialized": plan_materialized},
    })

    # ④ 拍摄素材准备: what the storyboard asked for is really prepared.
    #    The requirement is the shot's own: every reference it names must be
    #    an approved, locked master.  This is the only place the material
    #    library is judged, and it comes after the design that asks for it.
    material_blockers: list[dict[str, Any]] = []
    for shot in scoped_shots:
        for problem in _shot_material_problems(db, shot):
            material_blockers.append({
                "code": problem,
                "message": f"镜头「{shot.name}」需要的素材尚未确认并锁定",
                "shot_id": shot.id,
            })
    stages.append({
        "id": "assets",
        "label": "拍摄素材准备",
        "done": bool(scoped_shots) and not material_blockers,
        "summary": (f"{len(scoped_shots)} 镜的素材已备齐" if scoped_shots and not material_blockers
                    else (f"{len(material_blockers)} 项素材还没备齐" if material_blockers
                          else "等待分镜定稿")),
        "blockers": material_blockers,
        "facts": {"material_shot_count": len(scoped_shots),
                  "material_missing_count": len(material_blockers)},
    })

    # ⑤ 导演包: a confirmed, non-stale package that is really startable now.
    stages.append({
        "id": "package",
        "label": "导演包",
        "done": bool(package and package.status == "CONFIRMED" and not package.stale
                     and ready and ready["startable"]),
        "summary": (
            f"第 {package.revision} 版 · {package.episode_count} 集 · {package.shot_count} 镜" if package
            else "尚未生成导演包"
        ),
        "blockers": (ready or {}).get("blockers", []),
        "facts": ({
            "package_id": package.id,
            "revision": package.revision,
            "status": package.status,
            "scope_digest": package.scope_digest,
            "manifest_sha256": package.manifest_sha256,
            "config_fingerprint": ready.get("config_fingerprint"),
        } if package and ready else {}),
    })

    # ⑥ 视频生产: every shot in the confirmed scope has a current passed take.
    production_blockers: list[dict[str, Any]] = []
    scope_shots = planned_shot_ids
    passed_scope_count = sum(shot_id in passed for shot_id in scope_shots)
    for shot_id in scope_shots:
        if shot_id not in passed:
            shot = by_id.get(shot_id)
            production_blockers.append({
                "code": "SHOT_NOT_PRODUCED",
                "message": f"「{shot.name if shot else shot_id}」还没有当前有效成片",
                "shot_id": shot_id,
            })
    stages.append({
        "id": "production",
        "label": "视频生产",
        "done": bool(scope_shots) and not production_blockers,
        "summary": f"{passed_scope_count} / {len(scope_shots)} 镜已通过" if scope_shots else "等待导演包",
        "blockers": production_blockers,
        "facts": {"passed_shot_count": passed_scope_count, "scope_shot_count": len(scope_shots)},
    })

    # ⑦ 后期成片: every required episode really has a delivered reel.
    from shine.production.runner import run_summary

    run = run_summary(db, project_id)
    post_done = bool(run and run.get("delivered_episode_count") == run.get("required_episode_count")
                     and run.get("required_episode_count"))
    stages.append({
        "id": "post",
        "label": "后期成片",
        "done": bool(post_done),
        "summary": (
            f"{run['delivered_episode_count']} / {run['required_episode_count']} 集已交付"
            if run else "还没有整部作品运行"
        ),
        "blockers": (run or {}).get("blockers", []),
        "facts": {"run_id": (run or {}).get("id"), "run_status": (run or {}).get("status")},
    })

    next_stage = next((stage["id"] for stage in stages if not stage["done"]), None)
    return {
        "project_id": project_id,
        "stages": stages,
        "next_stage": next_stage,
        "package": ({"id": package.id, "revision": package.revision, "status": package.status,
                     "stale": package.stale} if package else None),
        "episode_plan": ({"id": plan.id, "version": plan.version,
                          "episodes": len(plan.payload.get("episodes", []))} if plan else None),
        "run": run,
    }
