"""Derive the seven-stage journey from the project's real server state.

Every completion flag here is computed from the ledger, never from a browser
counter.  The rule the whole workbench depends on is: a stage is only complete
for the *current scope* (one confirmed package revision), so "one shot passed"
or "one material uploaded" can never light up a whole stage.

The scope is explicit.  A journey opened by a deep link names its package (and
optionally its run); the progress, playback, EDL and delivery views then all
read that same revision.  Falling back to "the newest of everything" is what
used to mix an old 1/1 run into a new 2/3 plan, so it only happens for a
project that has no package at all (the legacy path).
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from shine.director.models import DirectorEpisodePlan, DirectorPackageEpisode, DirectorPackageVersion
from shine.director.package import latest_confirmed_plan, latest_package, package_readiness
from shine.models import (
    AgentArtifact,
    Asset,
    Job,
    MediaArtifact,
    ProductionSeriesItem,
    Project,
    Shot,
    StyleVersion,
)
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


def _is_demo(db: Session, job: Job) -> bool:
    """A fixture/mock take is never current production evidence."""
    if (job.provider or "").lower() == "mock":
        return True
    if (job.quality_verification or "").lower() == "mock":
        return True
    artifact = db.get(MediaArtifact, job.artifact_id) if job.artifact_id else None
    return bool(artifact is not None and artifact.is_mock)


def _run_shot_jobs(db: Session, run_id: str) -> dict[str, str]:
    """The job each shot of a run really owns, from its series items."""
    from shine.production.models import ProductionRunBatch

    series_ids = [
        row for row in db.scalars(
            select(ProductionRunBatch.series_id).where(ProductionRunBatch.run_id == run_id)
        ).all() if row
    ]
    if not series_ids:
        return {}
    rows = db.scalars(
        select(ProductionSeriesItem)
        .where(ProductionSeriesItem.series_id.in_(series_ids))
        .order_by(ProductionSeriesItem.updated_at)
    ).all()
    return {row.shot_id: row.job_id for row in rows if row.job_id}


def _passed_shots(
    db: Session, project_id: str, *, shot_ids: list[str], run_id: str | None
) -> tuple[dict[str, Job], int]:
    """The current passed real job per shot, plus how many demo takes exist.

    A demo job is counted, never promoted: the production stage says "0 of 3"
    even when a fixture marked itself PASSED.
    """
    rows = db.scalars(
        select(Job)
        .where(Job.project_id == project_id, Job.status == "PASSED", Job.stale.is_(False))
        .order_by(Job.created_at)
    ).all()
    shots = {shot.id: shot for shot in db.scalars(select(Shot).where(Shot.project_id == project_id)).all()}
    owned = _run_shot_jobs(db, run_id) if run_id else None
    passed: dict[str, Job] = {}
    demo = 0
    for job in rows:
        if shot_ids and job.shot_id not in shot_ids:
            continue
        if owned is not None and owned.get(job.shot_id) != job.id:
            # The shot of this run was produced by a different attempt: an
            # older take of the same shot is not this run's evidence.
            continue
        shot = shots.get(job.shot_id)
        if shot is None or shot.revision != job.shot_revision or not job.artifact_id:
            continue
        if _is_demo(db, job):
            demo += 1
            continue
        passed[job.shot_id] = job
    return passed, demo


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
    from shine.asset_identity import resolve_character_asset, split_character_names

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
    bound_roles = set()
    for link in shot.bindings:
        asset = db.get(Asset, link.asset_id)
        if (asset is None or link.use_as != "subject" or asset.role != "character"
                or not asset.approved or not asset.locked):
            continue
        bound.append({"id": asset.id, "kind": asset.kind, "role": asset.role,
                      "entity_id": asset.entity_id})
        if link.role:
            bound_roles.add(link.role)
    for reference in dict.fromkeys(name for entry in spec.characters for name in split_character_names(entry)):
        if reference not in bound_roles and resolve_character_asset(reference, bound) is None:
            problems.append("SHOT_CHARACTER_MASTER_MISSING:" + reference)
    return problems


def scope_package(db: Session, project: Project, package_id: str | None) -> DirectorPackageVersion | None:
    """The package the journey is about: the named one, or the newest."""
    if package_id:
        package = db.get(DirectorPackageVersion, package_id)
        if package is None or package.project_id != project.id:
            from fastapi import HTTPException

            available = db.scalars(
                select(DirectorPackageVersion.id)
                .where(DirectorPackageVersion.project_id == project.id)
                .order_by(DirectorPackageVersion.revision.desc())
                .limit(20)
            ).all()
            raise HTTPException(404, {
                "code": "DIRECTOR_PACKAGE_NOT_FOUND",
                "message": "这个链接指向的导演包不属于当前作品",
                "package_id": package_id,
                "available_package_ids": list(available),
            })
        return package
    return latest_package(db, project.id)


def scope_run(db: Session, project: Project, package: DirectorPackageVersion | None, run_id: str | None):
    """The run that belongs to this scope, never "whatever ran last"."""
    from fastapi import HTTPException

    from shine.production.models import ProductionRun

    if run_id:
        run = db.get(ProductionRun, run_id)
        if run is None or run.project_id != project.id:
            raise HTTPException(404, {
                "code": "PRODUCTION_RUN_NOT_FOUND",
                "message": "这个链接指向的整片运行不属于当前作品",
                "run_id": run_id,
            })
        if package is not None and run.package_id != package.id:
            # A deep link that names package B and run A is two scopes; never
            # answer with one and render the other's controls (J11).
            raise HTTPException(404, {
                "code": "PRODUCTION_RUN_SCOPE_MISMATCH",
                "message": "这个整片运行属于另一个导演包，不能和当前导演包混在一起显示",
                "run_id": run_id,
                "run_package_id": run.package_id,
                "package_id": package.id,
            })
        return run
    if package is None:
        return db.scalars(
            select(ProductionRun)
            .where(ProductionRun.project_id == project.id)
            .order_by(ProductionRun.created_at.desc())
        ).first()
    return db.scalars(
        select(ProductionRun)
        .where(ProductionRun.project_id == project.id, ProductionRun.package_id == package.id)
        .order_by(ProductionRun.created_at.desc())
    ).first()


def compute_journey(
    db: Session,
    project: Project,
    *,
    package_id: str | None = None,
    run_id: str | None = None,
) -> dict[str, Any]:
    project_id = project.id
    shots = list(db.scalars(select(Shot).where(Shot.project_id == project_id)).all())
    style = db.get(StyleVersion, project.frozen_style_version_id) if project.frozen_style_version_id else None

    if run_id and not package_id:
        # Only a run was named: its own package is the scope, never the newest
        # package of the project.
        from fastapi import HTTPException

        from shine.production.models import ProductionRun

        named = db.get(ProductionRun, run_id)
        if named is None or named.project_id != project.id:
            raise HTTPException(404, {"code": "PRODUCTION_RUN_NOT_FOUND", "run_id": run_id})
        package = db.get(DirectorPackageVersion, named.package_id) if named.package_id else None
    else:
        package = scope_package(db, project, package_id)
    if package is not None and package.episode_plan_id:
        plan = db.get(DirectorEpisodePlan, package.episode_plan_id)
    else:
        plan = latest_confirmed_plan(db, project_id)
    ready = package_readiness(db, package) if package is not None else None
    run = scope_run(db, project, package, run_id)

    by_id = {shot.id: shot for shot in shots}
    plan_shot_ids = plan_shot_order(plan) if plan is not None else [shot.id for shot in shots]
    # The confirmed package is the authoritative production scope: a shot that
    # was added to a newer plan is not part of what this package promised.
    package_shot_ids: list[str] | None = None
    if package is not None:
        episodes_rows = db.scalars(
            select(DirectorPackageEpisode)
            .where(DirectorPackageEpisode.package_id == package.id)
            .order_by(DirectorPackageEpisode.position)
        ).all()
        package_shot_ids = [
            shot["shot_id"]
            for episode in episodes_rows
            for shot in (episode.manifest or {}).get("shots", [])
        ]
    overview_shot_ids = package_shot_ids if package_shot_ids else plan_shot_ids
    # The current Run's scope is the run's own frozen selection: the episodes
    # it was started with and the shots those episodes name.  A run that was
    # started for one episode therefore counts one episode's shots, not the
    # whole package's.  The package stays an overview in its own field (R07).
    run_shot_ids: list[str] = []
    run_episode_ids: list[str] = []
    if run is not None:
        from shine.production.models import ProductionRunEpisode

        rows = db.scalars(
            select(ProductionRunEpisode)
            .where(ProductionRunEpisode.run_id == run.id)
            .order_by(ProductionRunEpisode.position)
        ).all()
        run_episode_ids = [row.episode_id for row in rows]
        for row in rows:
            run_shot_ids.extend(str(shot_id) for shot_id in (row.shot_ids or []))
    scope_shot_ids = run_shot_ids or overview_shot_ids
    scoped_shots = [by_id[shot_id] for shot_id in scope_shot_ids if shot_id in by_id]

    from shine.director.package import _outline_for_document, current_script_basis

    basis = current_script_basis(db, project)
    document = basis["document"]
    outline = _outline_for_document(db, project_id, document) if document is not None else None
    if document is None:
        # No imported script: keep the legacy outline identity for hand-built
        # storyboards (there is no document lineage to be wrong about).
        outline = db.scalars(
            select(AgentArtifact)
            .where(
                AgentArtifact.project_id == project_id,
                AgentArtifact.kind == "StoryOutline",
                AgentArtifact.stale.is_(False),
            )
            .order_by(AgentArtifact.created_at.desc())
        ).first()

    stages: list[dict[str, Any]] = []

    from shine.director.creative import brief_rows

    briefs = brief_rows(db, project_id)
    brief = briefs[0] if briefs else None

    # ① 创意与剧本: a confirmed outline, or an explicitly authored shot list.
    #    An idea (creative brief) alone is not a script: it is the entry that
    #    produces one, so the stage reports it without lighting up.
    # A manual shot list is a legitimate script basis only when this project
    # has no imported document.  With a document, the current confirmed outline
    # is the authority; another script's old shots must not light this stage
    # (J06).
    script_done = bool(outline and outline.approved_at) or (document is None and bool(shots))
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
            "basis_explicit": basis["explicit"],
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
    elif document is not None:
        from shine.director.basis import plan_basis_pointer
        from shine.director.contracts import EpisodePlanPayload
        from shine.director.package import plan_basis_blockers

        try:
            payload = EpisodePlanPayload.model_validate(plan.payload)
        except Exception:
            payload = None
        if payload is not None:
            plan_blockers.extend(plan_basis_blockers(db, project, payload))
            # A plan whose shots have no script lineage is not refused here -
            # it is named, with the confirm / rebuild action that exists.
            pointer = plan_basis_pointer(db, project, payload)
            if pointer is not None:
                plan_blockers.append(pointer)
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
            "episode_plan_source": (plan.payload or {}).get("source") or {} if plan else None,
            "current_outline_artifact_id": outline.id if outline else None,
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
    # Per-episode storyboard state: a project whose second episode is still
    # pending must still show "1/2 集分镜已确认" and let the finished episode
    # move on to materials, packaging and shooting (streamline plan §3).
    episode_storyboard = []
    if plan is not None and plan_mode == "sections":
        from shine.materialprep.story import section_storyboard_shot_ids

        for episode in (plan.payload or {}).get("episodes", []):
            landed = 0
            pending_sections = 0
            for section_id in episode.get("section_ids") or []:
                resolved = section_storyboard_shot_ids(db, project_id, section_id)
                if resolved["shot_ids"]:
                    landed += len(resolved["shot_ids"])
                else:
                    pending_sections += 1
            episode_storyboard.append({
                "episode_id": episode.get("episode_id"),
                "confirmed": bool(episode.get("shot_ids")) and pending_sections == 0,
                "shot_count": len(episode.get("shot_ids") or []),
                "landed_shot_count": landed,
                "pending_section_count": pending_sections,
            })
    if plan is not None and plan_mode == "sections" and not plan_materialized:
        shot_blockers.append({"code": "EPISODE_PLAN_NOT_MATERIALIZED",
                              "message": "按剧本分好的集还没有全部拆成镜头"})
    for shot_id in scope_shot_ids:
        shot = by_id.get(shot_id)
        if shot is None:
            shot_blockers.append({"code": "PLANNED_SHOT_MISSING", "message": "计划中的镜头尚未建立",
                                  "shot_id": shot_id})
            continue
        for problem in _shot_problems(db, shot):
            shot_blockers.append({"code": problem, "message": f"镜头「{shot.name}」需要补齐",
                                  "shot_id": shot.id})
    storyboard_summary = f"{len(scope_shot_ids)} 个计划内镜头" if plan is not None else f"{len(shots)} 个镜头"
    if plan is not None and len(shots) != len(scope_shot_ids):
        storyboard_summary += f"（项目共 {len(shots)} 个）"
    confirmed_episodes = [row for row in episode_storyboard if row["confirmed"]]
    if episode_storyboard and len(confirmed_episodes) != len(episode_storyboard):
        storyboard_summary = (f"{len(confirmed_episodes)}/{len(episode_storyboard)} 集分镜已确认 · "
                              + storyboard_summary)
    stages.append({
        "id": "storyboard",
        "label": "镜头拆解",
        "done": bool(shots) and bool(scope_shot_ids) and not shot_blockers,
        "summary": storyboard_summary if shots or scope_shot_ids else "还没有分镜",
        "blockers": shot_blockers,
        "facts": {"shot_count": len(shots), "planned_shot_count": len(scope_shot_ids),
                  "planned_shot_ids": scope_shot_ids,
                  "package_shot_count": len(overview_shot_ids),
                  "scope_episode_ids": run_episode_ids,
                  "plan_mode": plan_mode, "plan_materialized": plan_materialized,
                  "episode_total": len(episode_storyboard),
                  "episode_confirmed_count": len(confirmed_episodes),
                  "ready_episode_ids": [row["episode_id"] for row in confirmed_episodes],
                  "episodes": episode_storyboard},
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
        # 素材不是落镜的硬前置，但也不是“H3 会自己生成正式资产”。必需项缺失只
        # 暂停引用它的镜头，其余镜头照常开拍；可选项可以明确跳过。
        "done": bool(scoped_shots),
        "summary": (
            f"{len(scoped_shots)} 镜的素材需求已核对" if scoped_shots and not material_blockers
            else (f"{len(scoped_shots)} 镜的素材需求已核对（{len(material_blockers)} 项必需素材缺失，"
                  "只影响引用它的镜头）" if scoped_shots
                  else "等待分镜定稿")
        ),
        "blockers": [],
        "facts": {"material_shot_count": len(scoped_shots),
                  "material_missing_count": len(material_blockers),
                  "material_notices": material_blockers},
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
            "pending_dependencies": ready.get("pending_dependencies", []),
        } if package and ready else {}),
    })

    # ⑥ 视频生产: every shot in the confirmed scope has a current passed take
    #    produced by *this* scope.  Demo takes and older runs never count.
    production_blockers: list[dict[str, Any]] = []
    passed, demo_count = _passed_shots(
        db, project_id, shot_ids=scope_shot_ids, run_id=run.id if run is not None else None
    )
    passed_scope_count = sum(shot_id in passed for shot_id in scope_shot_ids)
    for shot_id in scope_shot_ids:
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
        "done": bool(scope_shot_ids) and not production_blockers,
        "summary": (f"{passed_scope_count} / {len(scope_shot_ids)} 镜已通过"
                    if scope_shot_ids else "等待导演包"),
        "blockers": production_blockers,
        "facts": {
            "passed_shot_count": passed_scope_count,
            "scope_shot_count": len(scope_shot_ids),
            "package_shot_count": len(overview_shot_ids),
            "scope_episode_ids": run_episode_ids,
            "scope_package_id": package.id if package else None,
            "scope_run_id": run.id if run is not None else None,
            "demo_shot_count": demo_count,
        },
    })

    # ⑦ 后期成片: every required episode of *this scope* really has a reel.
    from shine.production.runner import run_summary

    # A scope without a run has not started; it must not borrow the newest run
    # of a different package just because one exists.
    summary = run_summary(db, project_id, run_id=run.id) if run is not None else None
    post_done = bool(summary
                     and summary.get("delivered_episode_count") == summary.get("required_episode_count")
                     and summary.get("required_episode_count"))
    stages.append({
        "id": "post",
        "label": "后期成片",
        "done": bool(post_done),
        "summary": (
            f"{summary['delivered_episode_count']} / {summary['required_episode_count']} 集已交付"
            if summary else ("等待这个导演包开拍" if package is not None else "还没有整部作品运行")
        ),
        "blockers": (summary or {}).get("blockers", []),
        "facts": {
            "run_id": (summary or {}).get("id"),
            "run_status": (summary or {}).get("status"),
            "scope_package_id": package.id if package else None,
        },
    })

    next_stage = next((stage["id"] for stage in stages if not stage["done"]), None)
    return {
        "project_id": project_id,
        "stages": stages,
        "next_stage": next_stage,
        "scope": {
            "package_id": package.id if package else None,
            "package_revision": package.revision if package else None,
            "run_id": run.id if run is not None else None,
            # The denominator of this scope: the run's own selection when a
            # run is in scope, the whole package otherwise.
            "shot_count": len(scope_shot_ids),
            "episode_ids": run_episode_ids,
        },
        # The whole-package overview, in its own field: it never becomes the
        # denominator of a run that selected fewer episodes.
        "package_scope": {
            "shot_count": len(overview_shot_ids),
            "episode_count": len(plan.payload.get("episodes", [])) if plan is not None else 0,
        },
        "package": ({"id": package.id, "revision": package.revision, "status": package.status,
                     "stale": package.stale} if package else None),
        "episode_plan": ({"id": plan.id, "version": plan.version,
                          "episodes": len(plan.payload.get("episodes", []))} if plan else None),
        "run": summary,
    }
