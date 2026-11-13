"""One read-only overview of the director journey, at project and episode grain.

Every value is derived from an existing ledger row: the confirmed episode plan,
the storyboard task trace, the landed shot rows, the asset requirements and
bindings, the confirmed package manifest and its readiness, the run/episode
rows and the delivery pointer.  Nothing here writes.

Two rules decide the shape of this module:

* **One aggregate, no second state machine.**  The V2 plan forbids a fourth
  project ledger.  A status this module cannot derive from a real row is not
  reported at all; a browser that reloads gets exactly the same answer.
* **Per-episode isolation.**  One episode waiting for a repair is a property of
  that episode.  A project-level "not materialised" flag must never freeze the
  episodes that are already shootable, so every episode carries its own state,
  its own blockers and its own next action, and the project answer is an
  aggregate over them.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from shine.director.models import (
    DirectorEpisodePlan,
    DirectorPackageEpisode,
    DirectorPackageVersion,
)
from shine.director.package import package_readiness
from shine.models import Project, Shot, StyleVersion

#: Episode states, in the order a person walks them.  ``REVIEW_READY`` means a
#: real reel exists and waits for human review; ``DELIVERED`` means the episode
#: row itself says delivered.  Technical completion never becomes acceptance.
EPISODE_STATES = (
    "WAITING_STORYBOARD",
    "STORYBOARD_RUNNING",
    "STORYBOARD_BLOCKED",
    "SHOTS_TO_CONFIRM",
    "MATERIAL_MISSING",
    "READY_TO_PACKAGE",
    "READY_TO_SHOOT",
    "SHOOTING",
    "REVIEW_READY",
    "DELIVERED",
)

_STATE_LABELS = {
    "WAITING_STORYBOARD": "等待拆镜",
    "STORYBOARD_RUNNING": "分镜生成中",
    "STORYBOARD_BLOCKED": "分镜待修复",
    "SHOTS_TO_CONFIRM": "镜头待确认",
    "MATERIAL_MISSING": "等待备料",
    "READY_TO_PACKAGE": "可打包",
    "READY_TO_SHOOT": "可开拍",
    "SHOOTING": "拍摄中",
    "REVIEW_READY": "可审片",
    "DELIVERED": "已交付",
}

#: A run in one of these states is real work in progress for its episodes.
_ACTIVE_RUN = {"DRAFT", "QUEUED", "RUNNING", "PAUSED", "BLOCKED"}


def _plans(db: Session, project_id: str) -> list[DirectorEpisodePlan]:
    return list(db.scalars(
        select(DirectorEpisodePlan)
        .where(DirectorEpisodePlan.project_id == project_id)
        .order_by(DirectorEpisodePlan.version.desc())
    ).all())


def _style(db: Session, project: Project) -> StyleVersion | None:
    return db.get(StyleVersion, project.frozen_style_version_id) if project.frozen_style_version_id else None


def _section_shots(db: Session, project_id: str, section_id: str, cache: dict[str, dict]) -> dict:
    cached = cache.get(section_id)
    if cached is not None:
        return cached
    from shine.materialprep.story import section_storyboard_shot_ids

    resolved = section_storyboard_shot_ids(db, project_id, section_id)
    cache[section_id] = resolved
    return resolved


def _storyboard_ledger(db: Session, project_id: str) -> dict[str, Any]:
    """The newest director storyboard run, as a per-episode hint only.

    The run trace says what the background task is doing; the landed shots
    (``section_storyboard_shot_ids``) say what actually exists.  When the two
    disagree the landed shots win, which is why this is a hint and not a gate.
    """
    from shine.director.streamline import director_storyboard_state

    try:
        return director_storyboard_state(db, project_id)
    except Exception:  # noqa: BLE001 - an unreadable trace must not hide the real shots
        return {"task_id": None, "status": None, "episodes": []}


def _confirmed_packages(db: Session, project_id: str) -> list[DirectorPackageVersion]:
    return list(db.scalars(
        select(DirectorPackageVersion)
        .where(
            DirectorPackageVersion.project_id == project_id,
            DirectorPackageVersion.status == "CONFIRMED",
            DirectorPackageVersion.stale.is_(False),
        )
        .order_by(DirectorPackageVersion.revision.desc())
    ).all())


def _package_episode_ids(db: Session, package: DirectorPackageVersion) -> set[str]:
    return set(db.scalars(
        select(DirectorPackageEpisode.episode_id)
        .where(DirectorPackageEpisode.package_id == package.id)
    ).all())


def _readiness(db: Session, package: DirectorPackageVersion, cache: dict[str, dict]) -> dict:
    cached = cache.get(package.id)
    if cached is None:
        cached = package_readiness(db, package)
        cache[package.id] = cached
    return cached


def _blockers_for_episode(
    readiness: dict[str, Any], *, episode_id: str, shot_ids: set[str]
) -> list[dict[str, Any]]:
    """Readiness blockers that really belong to one episode.

    A package-level blocker (a superseded style, a stale package) belongs to
    every episode; a shot or episode scoped blocker belongs only to the episode
    whose manifest names it.  This is what keeps a broken second episode from
    painting the first one as unshootable.
    """
    own: list[dict[str, Any]] = []
    for blocker in readiness.get("blockers") or []:
        blocker_episode = blocker.get("episode_id")
        blocker_shot = blocker.get("shot_id")
        if blocker_episode is not None:
            if blocker_episode == episode_id:
                own.append(blocker)
            continue
        if blocker_shot is not None:
            if blocker_shot in shot_ids:
                own.append(blocker)
            continue
        own.append(blocker)
    for dependency in readiness.get("pending_dependencies") or []:
        if dependency.get("episode_id") == episode_id or dependency.get("shot_id") in shot_ids:
            own.append({**dependency, "code": dependency.get("code") or "PENDING_DEPENDENCY"})
    return own


def _shot_problems(db: Session, shot: Shot) -> list[str]:
    from shine.director.journey import _shot_problems as journey_shot_problems

    return journey_shot_problems(db, shot)


def _material_problems(db: Session, shot: Shot) -> list[str]:
    from shine.director.journey import _shot_material_problems

    return _shot_material_problems(db, shot)


_GAP_FAMILY = {
    "SHOT_CHARACTER_MASTER_MISSING": "character",
    "SHOT_REQUIRED_ASSET_MISSING": "reference",
    "SHOT_REQUIRED_ASSET_NOT_MASTER": "reference",
    "SHOT_REQUIRED_ASSET_UNBOUND": "reference",
}


def _aggregate_gaps(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Fold per-shot material problems into one row per missing master."""
    groups: dict[str, dict[str, Any]] = {}
    for row in rows:
        code = str(row.get("code") or "")
        head, _, entity = code.partition(":")
        key = entity or code
        entry = groups.setdefault(key, {
            "code": head,
            "family": _GAP_FAMILY.get(head, "reference"),
            "entity_id": entity or None,
            "shot_ids": [],
        })
        shot_id = row.get("shot_id")
        if shot_id and shot_id not in entry["shot_ids"]:
            entry["shot_ids"].append(shot_id)
    out = [{**entry, "shot_count": len(entry["shot_ids"]), "shot_ids": entry["shot_ids"][:20]}
           for entry in groups.values()]
    out.sort(key=lambda item: (-item["shot_count"], item["code"]))
    return out


def _run_for(db: Session, project_id: str, run_id: str | None):
    from shine.production.models import ProductionRun

    if run_id:
        row = db.get(ProductionRun, run_id)
        return row if row is not None and row.project_id == project_id else None
    return db.scalars(
        select(ProductionRun)
        .where(ProductionRun.project_id == project_id)
        .order_by(ProductionRun.created_at.desc())
    ).first()


def _latest_delivery(db: Session, project_id: str, episode_id: str, cache: dict[str, Any]):
    """The newest delivered take of one episode, across every run.

    A run owns one shooting scope; starting episode 2 must not hide the take
    episode 1 already delivered.  The delivery is per episode, so it is read
    per episode and never borrowed from "the current run".
    """
    from shine.production.models import ProductionRunEpisode

    if episode_id in cache:
        return cache[episode_id]
    row = db.scalars(select(ProductionRunEpisode).where(
        ProductionRunEpisode.project_id == project_id,
        ProductionRunEpisode.episode_id == episode_id,
        ProductionRunEpisode.delivered_artifact_id.is_not(None),
    ).order_by(ProductionRunEpisode.delivered_at.desc())).first()
    cache[episode_id] = row
    return row


def _latest_run_episode(db: Session, project_id: str, episode_id: str, cache: dict[str, Any]):
    """The newest run that ever covered one episode, plus that run.

    一次运行只覆盖它自己那一批集：拍第 2 集时，第 1 集的进度必须回它自己的
    那次运行去读，否则会显示成"这一集正在拍、0/3 镜通过"。
    """
    from shine.production.models import ProductionRun, ProductionRunEpisode

    if episode_id in cache:
        return cache[episode_id]
    row = db.scalars(select(ProductionRunEpisode).where(
        ProductionRunEpisode.project_id == project_id,
        ProductionRunEpisode.episode_id == episode_id,
    ).order_by(ProductionRunEpisode.created_at.desc())).first()
    run = db.get(ProductionRun, row.run_id) if row is not None else None
    cache[episode_id] = (row, run)
    return row, run


def _run_episodes(db: Session, run) -> dict[str, Any]:
    from shine.production.models import ProductionRunEpisode

    if run is None:
        return {}
    rows = db.scalars(
        select(ProductionRunEpisode)
        .where(ProductionRunEpisode.run_id == run.id)
        .order_by(ProductionRunEpisode.position)
    ).all()
    return {row.episode_id: row for row in rows}


def _episode_node(
    db: Session,
    project: Project,
    *,
    plan_row: dict[str, Any],
    confirmed: bool,
    section_cache: dict[str, dict],
    storyboard: dict[str, Any],
    shot_index: dict[str, Shot],
    packages: list[DirectorPackageVersion],
    package_episode_ids: dict[str, set[str]],
    readiness_cache: dict[str, dict],
    run,
    run_episodes: dict[str, Any],
    delivery_cache: dict[str, Any],
    run_episode_cache: dict[str, Any],
) -> dict[str, Any]:
    from shine.director.journey import _passed_shots

    episode_id = str(plan_row.get("episode_id"))
    section_ids = [str(item) for item in (plan_row.get("section_ids") or [])]
    landed: list[str] = []
    missing_sections: list[dict[str, Any]] = []
    for section_id in section_ids:
        resolved = _section_shots(db, project.id, section_id, section_cache)
        if resolved["shot_ids"]:
            landed.extend(str(shot_id) for shot_id in resolved["shot_ids"])
        else:
            missing_sections.append({"section_id": section_id,
                                     "missing": resolved.get("missing") or []})
    landed = list(dict.fromkeys(landed))
    confirmed_shots = [str(shot_id) for shot_id in (plan_row.get("shot_ids") or [])]
    episode_confirmed = bool(confirmed and confirmed_shots)

    shots = [shot_index[shot_id] for shot_id in landed if shot_id in shot_index]
    shot_problems = [{"shot_id": shot.id, "code": code}
                     for shot in shots for code in _shot_problems(db, shot)]
    material_rows = [{"shot_id": shot.id, "code": code}
                     for shot in shots for code in _material_problems(db, shot)]
    # One missing master used by 200 shots is one thing to fix, not 200: the
    # episode card names the entity and how many shots wait on it.
    material_gaps = _aggregate_gaps(material_rows)

    # The package that really covers this episode: the newest confirmed one
    # whose manifest names it.  A package for episode 1 is not a package for 2.
    covering: DirectorPackageVersion | None = None
    for package in packages:
        if episode_id in package_episode_ids.get(package.id, set()):
            covering = package
            break
    package_readiness_row = _readiness(db, covering, readiness_cache) if covering is not None else None
    package_blockers = (
        _blockers_for_episode(package_readiness_row, episode_id=episode_id,
                             shot_ids=set(confirmed_shots))
        if package_readiness_row is not None else []
    )
    startable = bool(covering is not None and package_readiness_row is not None
                     and package_readiness_row.get("startable") and not package_blockers)

    run_episode = run_episodes.get(episode_id)
    latest_delivery = _latest_delivery(db, project.id, episode_id, delivery_cache)
    # 这一集在本轮运行里 → 用本轮；本轮没覆盖它 → 回它自己最近的那次运行。
    in_current_run = run_episode is not None
    if in_current_run:
        active_run, active_episode = run, run_episode
    else:
        active_episode, active_run = _latest_run_episode(db, project.id, episode_id, run_episode_cache)
    passed: dict[str, Any] = {}
    if active_run is not None and active_episode is not None:
        passed, _demo = _passed_shots(db, project.id, shot_ids=list(active_episode.shot_ids or []),
                                      run_id=active_run.id)
    run_shot_count = len(active_episode.shot_ids or []) if active_episode is not None else len(confirmed_shots)
    passed_count = sum(1 for shot_id in (active_episode.shot_ids if active_episode is not None else [])
                       if shot_id in passed)
    run_active = bool(active_run is not None and active_run.status in _ACTIVE_RUN)
    # 本集的交付可以来自任何一次运行：先看这一集是否在拍，再退回它最近一次交付。
    shooting_this_episode = bool(run_active and in_current_run and active_episode is not None
                                 and active_episode.status != "DELIVERED")
    delivery_row = latest_delivery or run_episode
    delivered = bool(delivery_row is not None and delivery_row.delivered_artifact_id)
    episode_delivered = bool(delivery_row is not None and delivery_row.status == "DELIVERED")

    ledger_episode = next(
        (row for row in (storyboard.get("episodes") or []) if row.get("episode_id") == episode_id),
        None,
    )
    ledger_status = (ledger_episode or {}).get("status")

    state = _episode_state(
        shooting_this_episode=shooting_this_episode,
        episode_confirmed=episode_confirmed,
        landed=landed,
        missing_sections=missing_sections,
        ledger_status=ledger_status,
        storyboard_status=storyboard.get("status"),
        shot_problems=shot_problems,
        material_gaps=material_gaps,
        covering=covering,
        startable=startable,
        delivered=delivered,
        episode_delivered=episode_delivered,
    )
    blockers = _episode_blockers(
        state=state,
        missing_sections=missing_sections,
        shot_problems=shot_problems,
        material_gaps=material_gaps,
        package_blockers=package_blockers,
        ledger_episode=ledger_episode,
        delivery_row=delivery_row if delivered else None,
        covering=covering,
    )
    action = _episode_action(state, episode_id=episode_id, blockers=blockers)

    return {
        "episode_id": episode_id,
        "order": plan_row.get("order"),
        "title": plan_row.get("title") or "",
        "planned_seconds": float(plan_row.get("planned_seconds") or 0.0),
        "state": state,
        "state_label": _STATE_LABELS.get(state, state),
        "storyboard": {
            "section_count": len(section_ids),
            "landed_section_count": len(section_ids) - len(missing_sections),
            "pending_sections": missing_sections[:20],
            "ledger_task_id": storyboard.get("task_id"),
            "ledger_task_status": ledger_status,
            "state_version": storyboard.get("state_version"),
            "blocker": (ledger_episode or {}).get("blocker"),
        },
        "shots": {
            "landed_count": len(landed),
            "confirmed_count": len(confirmed_shots),
            "confirmed": episode_confirmed,
            "problems": shot_problems[:40],
        },
        "materials": {
            "missing_count": len(material_gaps),
            "missing": material_gaps[:40],
        },
        "package": ({
            "package_id": covering.id,
            "revision": covering.revision,
            "startable": startable,
            "blockers": package_blockers[:20],
        } if covering is not None else None),
        "production": ({
            "run_id": active_run.id,
            "status": active_run.status,
            "shot_count": run_shot_count,
            "passed_shot_count": passed_count,
            "in_current_run": in_current_run,
        } if active_run is not None else None),
        "delivery": ({
            "artifact_id": delivery_row.delivered_artifact_id,
            "sha256": delivery_row.delivered_sha256,
            "delivered_at": delivery_row.delivered_at.isoformat() if delivery_row.delivered_at else None,
            "actual_seconds": delivery_row.actual_seconds,
            "status": delivery_row.status,
            "post_contract": delivery_row.post_contract,
            "run_id": delivery_row.run_id,
        } if delivery_row is not None and delivered else None),
        "next_action": action,
    }


def _episode_state(
    *,
    shooting_this_episode: bool,
    episode_confirmed: bool,
    landed: list[str],
    missing_sections: list[dict[str, Any]],
    ledger_status: str | None,
    storyboard_status: str | None,
    shot_problems: list[dict[str, Any]],
    material_gaps: list[dict[str, Any]],
    covering: DirectorPackageVersion | None,
    startable: bool,
    delivered: bool,
    episode_delivered: bool,
) -> str:
    if shooting_this_episode:
        # 正在拍（含重拍）：即使这一集以前交付过，也先显示拍摄中。
        return "SHOOTING"
    if episode_delivered:
        return "DELIVERED"
    if delivered:
        # A real reel exists but the episode row itself has not been accepted:
        # that is "ready for review", never "finished".
        return "REVIEW_READY"
    if startable:
        return "READY_TO_SHOOT"
    if not episode_confirmed:
        if ledger_status == "BLOCKED":
            return "STORYBOARD_BLOCKED"
        if ledger_status == "IN_PROGRESS" or storyboard_status in {"QUEUED", "RUNNING"}:
            return "STORYBOARD_RUNNING"
        if landed:
            return "SHOTS_TO_CONFIRM"
        if any(item.get("missing") for item in missing_sections):
            return "STORYBOARD_BLOCKED"
        return "WAITING_STORYBOARD"
    if shot_problems or material_gaps:
        return "MATERIAL_MISSING"
    return "READY_TO_PACKAGE"


def _episode_blockers(
    *,
    state: str,
    missing_sections: list[dict[str, Any]],
    shot_problems: list[dict[str, Any]],
    material_gaps: list[dict[str, Any]],
    package_blockers: list[dict[str, Any]],
    ledger_episode: dict[str, Any] | None,
    delivery_row,
    covering: DirectorPackageVersion | None,
) -> list[dict[str, Any]]:
    blockers: list[dict[str, Any]] = []
    if state == "STORYBOARD_BLOCKED":
        rejection = ((ledger_episode or {}).get("blocker") or {}).get("rejections") or []
        if rejection:
            first = rejection[0]
            blockers.append({
                "code": first.get("rejection_reason_code") or first.get("code") or "STORYBOARD_BLOCKED",
                "message": f"分镜工种 {first.get('role') or ''} 拒绝：请按提示修改这一集",
                "external_outcome": first.get("external_outcome"),
            })
        elif missing_sections:
            blockers.append({"code": "EPISODE_SECTION_NOT_STORYBOARDED",
                             "message": "本集还有场次没有拆成镜头"})
    for problem in shot_problems[:6]:
        blockers.append({"code": problem["code"], "message": "镜头内容需要补齐",
                         "shot_id": problem["shot_id"]})
    for gap in material_gaps[:6]:
        label = gap.get("entity_id") or "参考素材"
        blockers.append({
            "code": gap["code"],
            "message": f"「{label}」还没有成为主素材（{gap['shot_count']} 镜在等它）",
            "entity_id": gap.get("entity_id"),
            "shot_count": gap["shot_count"],
        })
    for blocker in package_blockers[:6]:
        blockers.append(blocker)
    if state == "REVIEW_READY" and delivery_row is not None:
        blockers.append({
            "code": "EPISODE_PENDING_REVIEW",
            "message": "成片已生成，等待你播放确认后再交付",
        })
    if state == "READY_TO_PACKAGE" and covering is None:
        blockers.append({"code": "PACKAGE_REQUIRED",
                         "message": "这一集已具备开拍条件，等待生成导演包"})
    return blockers


def _episode_action(state: str, *, episode_id: str, blockers: list[dict[str, Any]]) -> dict[str, Any] | None:
    base = {"episode_id": episode_id}
    if state == "STORYBOARD_BLOCKED":
        return {**base, "kind": "RETRY_STORYBOARD", "label": "修复这一集的分镜"}
    if state == "WAITING_STORYBOARD":
        return {**base, "kind": "RUN_STORYBOARD", "label": "生成这一集的分镜"}
    if state == "SHOTS_TO_CONFIRM":
        return {**base, "kind": "CONFIRM_EPISODE_SHOTS", "label": "确认这一集的镜头"}
    if state == "MATERIAL_MISSING":
        return {**base, "kind": "PREPARE_MATERIALS", "label": "补齐这一集缺少的素材"}
    if state == "READY_TO_PACKAGE":
        return {**base, "kind": "BUILD_PACKAGE", "label": "把这一集生成导演包"}
    if state == "READY_TO_SHOOT":
        return {**base, "kind": "START_PRODUCTION", "label": "开始真实拍摄这一集"}
    if state == "REVIEW_READY":
        return {**base, "kind": "REVIEW_EPISODE", "label": "播放并确认这一集"}
    if state == "SHOOTING":
        return {**base, "kind": "WAIT", "label": "拍摄进行中，可离开页面"}
    return None


def build_overview(
    db: Session,
    project: Project,
    *,
    package_id: str | None = None,
    run_id: str | None = None,
) -> dict[str, Any]:
    """The whole director overview: project summary plus one node per episode."""
    project_id = project.id
    plans = _plans(db, project_id)
    confirmed_plan = next((row for row in plans if row.status == "CONFIRMED"), None)
    plan = confirmed_plan or (plans[0] if plans else None)
    style = _style(db, project)

    storyboard = _storyboard_ledger(db, project_id)
    shot_index: dict[str, Shot] = {
        shot.id: shot for shot in db.scalars(select(Shot).where(Shot.project_id == project_id)).all()
    }
    packages = _confirmed_packages(db, project_id)
    package_episode_ids = {row.id: _package_episode_ids(db, row) for row in packages}
    readiness_cache: dict[str, dict] = {}

    scoped_package = None
    if package_id:
        scoped_package = db.get(DirectorPackageVersion, package_id)
        if scoped_package is not None and scoped_package.project_id != project_id:
            scoped_package = None
    if scoped_package is None and packages:
        scoped_package = packages[0]
    run = _run_for(db, project_id, run_id)
    if run is not None and scoped_package is not None and run.package_id != scoped_package.id:
        # A named run belongs to its own package; the episode cards still read
        # that run, because it is the run that really produced them.
        scoped_package = db.get(DirectorPackageVersion, run.package_id) or scoped_package
    run_episodes = _run_episodes(db, run)

    section_cache: dict[str, dict] = {}
    delivery_cache: dict[str, Any] = {}
    run_episode_cache: dict[str, Any] = {}
    episodes: list[dict[str, Any]] = []
    for plan_row in ((plan.payload or {}).get("episodes") or []) if plan is not None else []:
        episodes.append(_episode_node(
            db, project,
            plan_row=plan_row,
            confirmed=bool(confirmed_plan is not None and confirmed_plan.id == (plan.id if plan else None)),
            section_cache=section_cache,
            storyboard=storyboard,
            shot_index=shot_index,
            packages=packages,
            package_episode_ids=package_episode_ids,
            readiness_cache=readiness_cache,
            run=run,
            run_episodes=run_episodes,
            delivery_cache=delivery_cache,
            run_episode_cache=run_episode_cache,
        ))

    counts = {
        "episode_count": len(episodes),
        "confirmed_episode_count": sum(1 for row in episodes if row["shots"]["confirmed"]),
        "shootable_episode_count": sum(
            1 for row in episodes if row["state"] in {"READY_TO_SHOOT", "SHOOTING", "REVIEW_READY", "DELIVERED"}
        ),
        "delivered_episode_count": sum(1 for row in episodes if row["state"] == "DELIVERED"),
        "shot_count": sum(row["shots"]["confirmed_count"] for row in episodes),
        "passed_shot_count": sum(
            row["production"]["passed_shot_count"] for row in episodes if row["production"]
        ),
    }
    next_action = _project_action(
        project=project, plan=plan, confirmed_plan=confirmed_plan, style=style, episodes=episodes
    )
    return {
        "project_id": project_id,
        "title": project.title,
        "plan": ({
            "version": plan.version,
            "sha256": plan.sha256,
            "status": plan.status,
            "episode_count": len(episodes),
        } if plan is not None else None),
        "style": ({"version": style.version, "sha256": style.sha256} if style is not None else None),
        "counts": counts,
        "episodes": episodes,
        "next_action": next_action,
        "package": ({
            "package_id": scoped_package.id,
            "revision": scoped_package.revision,
            "episode_count": scoped_package.episode_count,
            "shot_count": scoped_package.shot_count,
            "startable": bool(
                (readiness_cache.get(scoped_package.id)
                 or _readiness(db, scoped_package, readiness_cache)).get("startable")
            ),
        } if scoped_package is not None else None),
        "run": ({
            "run_id": run.id,
            "status": run.status,
            "active": run.status in _ACTIVE_RUN,
            "scope_episode_ids": list(run_episodes.keys()),
        } if run is not None else None),
        "storyboard_task": {
            "task_id": storyboard.get("task_id"),
            "status": storyboard.get("status"),
            "state_version": storyboard.get("state_version"),
        },
    }


def _project_action(
    *,
    project: Project,
    plan: DirectorEpisodePlan | None,
    confirmed_plan: DirectorEpisodePlan | None,
    style: StyleVersion | None,
    episodes: list[dict[str, Any]],
) -> dict[str, Any] | None:
    """The single main operation, chosen from real per-episode states."""
    if project.current_document_id is None and not episodes:
        return {"kind": "IMPORT_SCRIPT", "label": "导入小说或剧本"}
    if plan is None:
        return {"kind": "GENERATE_BOOK", "label": "生成分镜书"}
    if confirmed_plan is None:
        return {"kind": "CONFIRM_BOOK", "label": "确认分镜书与风格"}
    if style is None:
        return {"kind": "CONFIRM_BOOK", "label": "确认并冻结导演风格"}

    # The main action follows work that can move now.  A blocked episode is a
    # to-do item beside it, never the reason a ready episode waits: a person
    # finishing episode 1 must not be stopped by episode 2's repair.
    priority = (
        "SHOTS_TO_CONFIRM",
        "MATERIAL_MISSING",
        "READY_TO_PACKAGE",
        "READY_TO_SHOOT",
        "REVIEW_READY",
        "STORYBOARD_BLOCKED",
        "WAITING_STORYBOARD",
        "STORYBOARD_RUNNING",
        "SHOOTING",
        "DELIVERED",
    )
    for state in priority:
        row = next((item for item in episodes if item["state"] == state), None)
        if row is None:
            continue
        if state == "DELIVERED":
            continue
        action = row.get("next_action")
        if action is not None:
            return {**action, "kind": action["kind"], "label": action["label"],
                    "episode_id": row["episode_id"]}
    delivered = [row for row in episodes if row["state"] == "DELIVERED"]
    if episodes and len(delivered) == len(episodes):
        return {"kind": "COMPLETE", "label": "全部集已交付"}
    return None


def build_episode_overview(db: Session, project: Project, episode_id: str, **kwargs: Any) -> dict[str, Any]:
    overview = build_overview(db, project, **kwargs)
    row = next((item for item in overview["episodes"] if item["episode_id"] == episode_id), None)
    if row is None:
        from fastapi import HTTPException

        raise HTTPException(404, {
            "code": "EPISODE_NOT_IN_PLAN",
            "message": "这一集不在当前分集方案里",
            "episode_id": episode_id,
        })
    return {
        "project_id": overview["project_id"],
        "episode": row,
        "plan": overview["plan"],
        "style": overview["style"],
        "counts": overview["counts"],
    }
