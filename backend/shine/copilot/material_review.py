"""Project the candidate images the user is about to review for a task grant."""
from __future__ import annotations

from shine.copilot.contracts import digest

REVIEW_REQUIRED = "MATERIAL_REVIEW_REQUIRED"
GENERATION_ACTIONS = frozenset({"material_generate", "material_regenerate"})
REVIEW_BOUNDARY_ACTIONS = frozenset({"material_promote", "director_package_create", "production_start"})


def review_project_id(grant: dict) -> str | None:
    scope = grant.get("scope") or {}
    projects = scope.get("project_ids") or []
    if grant.get("pause_before_production") and len(projects) == 1 and "production" in (
        scope.get("capability_groups") or []
    ):
        return projects[0]
    return None


def candidate_snapshot(db, grant: dict) -> dict | None:
    project_id = review_project_id(grant)
    if project_id is None:
        return None

    from shine.comfyops.preproduction import current_material_plan
    from shine.materialprep.service import RUN_TERMINAL, generation_progress

    plan = current_material_plan(db, project_id)
    if plan is None:
        return None
    runs = [run for run in generation_progress(db, project_id)["runs"] if run["plan_revision_id"] == plan.id]
    latest_items: dict[str, dict] = {}
    for run in runs:
        for item in run["items"]:
            requirement_id = item["requirement_id"]
            if not requirement_id:
                continue
            previous = latest_items.get(requirement_id)
            if previous is None or item["generation_revision"] > previous["generation_revision"]:
                latest_items[requirement_id] = item

    approved = grant.get("reviewed_materials") or {}
    candidates = []
    for item in latest_items.values():
        for candidate in item["candidates"]:
            candidate_id = candidate["candidate_id"]
            sha = candidate["content_sha256"]
            if candidate["status"] == "READY_FOR_REVIEW" and sha and approved.get(candidate_id) != sha:
                candidates.append({
                    "candidate_id": candidate_id,
                    "content_sha256": sha,
                    "requirement_id": item["requirement_id"],
                    "family": item["family"],
                    "name": item["entity_id"],
                    "generation_revision": item["generation_revision"],
                })
    candidates.sort(key=lambda row: (row["family"] or "", row["name"] or "", row["candidate_id"]))
    generating = any(run["status"] not in RUN_TERMINAL for run in runs)
    return {
        "project_id": project_id,
        "candidates": candidates,
        "generating": generating,
        "digest": digest([plan.id, candidates, generating]),
    }
