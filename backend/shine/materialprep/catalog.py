"""Project-wide entity consolidation for the material requirement list.

The per-scene crew answers name characters scene by scene; the same person
appears under aliases (transmigration, titles) and in different clothing.
Deriving the material list from those names alone forks one person into
several masters and breaks on-screen identity.  This module makes ONE
project-wide call that reads the global bible plus every contracted scene
and returns a consolidated catalog: one entry per real character with
aliases and per-scene appearance variants, key scenes and key props only.

The catalog is a durable artifact keyed by its basis (bible + exact scene
script revisions); an unchanged basis reuses it instead of buying a second
call.  The unknown-outcome gate is the same as everywhere else: an uncertain
request is never replayed automatically.
"""
from __future__ import annotations

from fastapi import HTTPException
from sqlalchemy import select

from shine.h3.rulebook import digest
from shine.materialprep import text_consent
from shine.materialprep.agent_call import call_material_agent
from shine.materialprep.contracts import MaterialEntityCatalog
from shine.models import AgentArtifact, AgentTaskRecord, utcnow
from shine.services import add_audit

CATALOG_ROLE = "producer"
RULEPACK_ID = "material-entity-catalog"
CATALOG_KIND = "MaterialEntityCatalog"


def catalog_prompt() -> str:
    return (
        "你是全项目统揽的制片实体核对员。输入是全局故事圣经与本次已确认分集的全部场次剧本。"
        "只输出一个 JSON 对象，合同为 MaterialEntityCatalog。规则：\n"
        "1. 人物全项目只定义一次：同一个人（穿越前后、本名与头衔、简称与全称）必须合并为一个条目，"
        "其余写法放入 aliases；不同人物不得合并。\n"
        "2. identity 写稳定身份与核心外貌（性别、年龄、发色、标志性特征），不写某一场临时服装。\n"
        "3. 同一人物在不同场次的衣服/外貌变化写入 variants（section_id + appearance），不要另建人物。\n"
        "4. scenes 只列关键场景：反复出现或剧情关键的发生地；描述写空间与光线特征，不写剧情。\n"
        "5. props 只列关键道具：有剧情/连续性依赖的物件（信物、文书、武器等），写明 core_prop_reason 与 carrier；"
        "普通陈设一律不列。\n"
        "6. 每个条目的 section_ids 只使用输入给出的 section_id；不得发明。\n"
        "7. 描述用中文，简洁、可画；不写镜头号、时长或 H3 提示词。"
    )


def _basis(bible, scripts: dict[str, dict]) -> dict:
    return {
        "bible_sha256": bible.sha256 if bible is not None else None,
        "sections": sorted(
            ({"section_id": sid, "sha256": payload.get("_sha256")}
             for sid, payload in scripts.items()),
            key=lambda entry: entry["section_id"],
        ),
    }


def get_or_create_catalog(db, gateway, session_factory, project_id, document,
                          scripts: dict[str, dict], bible) -> dict:
    """Reuse the catalog for this exact basis, or make one project-wide call."""
    basis = _basis(bible, scripts)
    for row in db.scalars(select(AgentArtifact).where(
        AgentArtifact.project_id == project_id,
        AgentArtifact.kind == CATALOG_KIND,
        AgentArtifact.stale.is_(False),
    ).order_by(AgentArtifact.created_at.desc())):
        if (row.payload_json or {}).get("basis") == basis:
            return row.payload_json

    profile = gateway.profile_for_role(db, CATALOG_ROLE)
    identity = digest({"basis": basis, "profile": profile.id, "model": profile.model,
                       "tool": RULEPACK_ID})
    existing = db.scalar(select(AgentTaskRecord).where(
        AgentTaskRecord.project_id == project_id,
        AgentTaskRecord.role_id == CATALOG_ROLE,
        AgentTaskRecord.rulepack_id == RULEPACK_ID,
        AgentTaskRecord.request_identity == identity,
        AgentTaskRecord.status != "SUPERSEDED",
    ).order_by(AgentTaskRecord.created_at.desc()))
    if existing is not None:
        outcome = (existing.trace or {}).get("external_outcome")
        if existing.status == "SUCCEEDED":
            artifact = db.scalar(select(AgentArtifact).where(
                AgentArtifact.source_task_id == existing.id,
                AgentArtifact.kind == CATALOG_KIND, AgentArtifact.stale.is_(False)))
            if artifact is not None:
                return artifact.payload_json
        if existing.status == "RUNNING":
            raise HTTPException(409, "MATERIAL_CATALOG_RUNNING")
        if outcome == "UNKNOWN":
            raise HTTPException(409, {"code": "MATERIAL_CATALOG_OUTCOME_UNKNOWN",
                                      "task_id": existing.id,
                                      "state_version": existing.state_version})
        if outcome in {"NOT_SENT", "RESPONSE_RECEIVED"}:
            previous = existing.request_identity
            existing.status = "SUPERSEDED"
            existing.request_identity = None
            existing.state_version += 1
            existing.trace = {**(existing.trace or {}), "reconciliation": {
                "outcome": "NOT_SENT_PROVEN" if outcome == "NOT_SENT" else "RESPONSE_RECEIVED_NO_ANSWER",
                "previous_request_identity": previous, "checked_at": utcnow().isoformat()}}
        else:
            raise HTTPException(409, {"code": "MATERIAL_CATALOG_OUTCOME_UNKNOWN",
                                      "task_id": existing.id,
                                      "state_version": existing.state_version})

    task = AgentTaskRecord(
        request_identity=identity, project_id=project_id, role_id=CATALOG_ROLE,
        input_hash=identity, asset_manifest_hash=identity,
        rulepack_id=RULEPACK_ID, rulepack_hash=digest(RULEPACK_ID),
        model_profile_id=profile.id, model_id=profile.model, schema_version="1.0",
        status="RUNNING",
        trace={"tool": "entity_catalog", "document_id": document.id,
               "document_sha256": document.sha256, "basis": basis,
               "phase": "PREPARING", "external_outcome": "UNKNOWN",
               "automatic_replay": False},
    )
    db.add(task)
    db.flush()
    task_id = task.id
    db.commit()

    context = {
        "bible": {
            "characters": (bible.payload_json or {}).get("characters") or [] if bible else [],
            "world": (bible.payload_json or {}).get("world") or [] if bible else [],
        },
        "scenes": [
            {"section_id": sid, "title": payload.get("title"),
             "characters": payload.get("characters") or [],
             "dialogue": payload.get("dialogue") or [],
             "text": payload.get("text") or ""}
            for sid, payload in scripts.items()
        ],
    }
    outcome = call_material_agent(
        gateway, session_factory, CATALOG_ROLE,
        system=catalog_prompt(), context=context,
        schema=MaterialEntityCatalog.model_json_schema(),
        contract=MaterialEntityCatalog,
        project_id=project_id, task_id=task_id, purpose="entity_catalog",
        before_call=text_consent.guard(project_id, document.id, document.sha256,
                                       CATALOG_ROLE, text_consent.fingerprint(profile), gateway),
    )
    with session_factory.begin() as db:
        row = db.get(AgentTaskRecord, task_id)
        if not outcome.ok:
            external = outcome.details.get("external_outcome", "UNKNOWN")
            row.status = "NEEDS_REVIEW" if (outcome.uncertain or external == "UNKNOWN") else "REJECTED"
            row.error_code = outcome.error_code
            row.state_version += 1
            row.trace = {**row.trace, "phase": "FAILED", "external_outcome": external,
                         "response_status": outcome.details.get("response_status"),
                         "automatic_replay": False}
            db.commit()
            code = ("MATERIAL_CATALOG_OUTCOME_UNKNOWN" if row.status == "NEEDS_REVIEW"
                    else (outcome.error_code or "MATERIAL_CATALOG_FAILED"))
            raise HTTPException(409, {"code": code, "task_id": task_id,
                                      "state_version": row.state_version})
        catalog = MaterialEntityCatalog.model_validate(outcome.parsed).model_dump(mode="json")
        payload = {**catalog, "basis": basis}
        artifact = AgentArtifact(
            project_id=project_id, kind=CATALOG_KIND, schema_version="1.0",
            payload_json=payload, sha256=digest(payload),
            producer_role=CATALOG_ROLE, source_task_id=task_id, input_revision_ids=[],
        )
        db.add(artifact)
        db.flush()
        row.status = "SUCCEEDED"
        row.state_version += 1
        row.trace = {**row.trace, "phase": "SUCCEEDED", "external_outcome": "SUCCEEDED",
                     "output_hash": artifact.sha256}
        add_audit(db, "local-user", "MATERIAL_ENTITY_CATALOG_READY", "agent_artifact", artifact.id,
                  {"characters": len(catalog["characters"]), "scenes": len(catalog["scenes"]),
                   "props": len(catalog["props"])}, project_id=project_id)
    return payload