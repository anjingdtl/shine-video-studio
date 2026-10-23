from __future__ import annotations

from typing import Literal

from fastapi import HTTPException
from jsonschema import Draft202012Validator
from pydantic import BaseModel, ConfigDict, Field

from shine.comfy.client import ComfyWorkflowError
from shine.comfy.workflow import apply_parameters, validate_against_object_info, validate_graph

CATEGORIES = (
    "IMAGE_GENERATION",
    "IMAGE_EDIT",
    "CHARACTER_SHEET",
    "OUTFIT_DESIGN",
    "SCENE_CONCEPT",
    "PROP_DESIGN",
    "STORYBOARD",
    "VOICE_DESIGN",
    "VOICE_CLONE",
    "TTS_DIALOGUE",
    "ASR",
    "FORCED_ALIGNMENT",
    "SFX",
    "MUSIC",
    "VLM_REVIEW",
    "H3_REF2VA",
    "H3_I2VA",
    "H3_L2VA",
    "H3_FL2VA",
    "H3_T2VA",
    "VIDEO_UPSCALE",
    "FRAME_INTERPOLATION",
    "ASSEMBLY",
)


class WorkflowDraft(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str = Field(min_length=1, max_length=120)
    category: str
    graph: dict
    parameter_map: dict[str, str]
    input_schema: dict = Field(default_factory=lambda: {"type": "object"})
    output_schema: dict = Field(default_factory=lambda: {"type": "object"})
    node_versions: dict[str, str] = Field(default_factory=dict)
    model_ids: list[str] = Field(default_factory=list)
    reference_slots: list[str] = Field(default_factory=list)
    media_transfers: dict = Field(default_factory=dict)
    continuity: Literal["cut", "soft_reference"] = "cut"
    model_family: str = ""
    legacy_profile_id: str | None = None
    generation_modes: list[str] = Field(default_factory=list)
    duration_range: tuple[float, float] | None = None
    aspect_ratios: list[str] = Field(default_factory=list)
    instance_ids: list[str] = Field(default_factory=list)
    node_package_ids: list[str] = Field(default_factory=list)
    package_hashes: dict[str, str] = Field(default_factory=dict)
    resource_mb: int | None = Field(default=None, gt=0)
    template_audit: dict = Field(default_factory=dict)


def validate_workflow(body: WorkflowDraft, object_info: dict, installed_versions: dict):
    if body.category not in CATEGORIES:
        raise HTTPException(422, "UNKNOWN_CAPABILITY")
    if body.category.startswith("H3_") and body.model_family != "MiniMax-H3":
        raise HTTPException(422, "ONLY_MINIMAX_H3_VIDEO")
    try:
        Draft202012Validator.check_schema(body.input_schema)
        Draft202012Validator.check_schema(body.output_schema)
        validate_graph(body.graph)
        report = validate_against_object_info(body.graph, body.parameter_map, object_info)
        apply_parameters(body.graph, body.parameter_map, {})
        visiting, visited = set(), set()

        def visit(node_id):
            if node_id in visiting:
                raise ValueError("WORKFLOW_CYCLE")
            if node_id in visited:
                return
            visiting.add(node_id)
            for value in body.graph[node_id]["inputs"].values():
                if isinstance(value, list) and len(value) == 2 and isinstance(value[0], str):
                    if value[0] not in body.graph or not isinstance(value[1], int) or value[1] < 0:
                        raise ValueError("BROKEN_NODE_LINK")
                    visit(value[0])
            visiting.remove(node_id)
            visited.add(node_id)

        for node_id in body.graph:
            visit(node_id)
    except (ValueError, ComfyWorkflowError) as exc:
        raise HTTPException(422, str(exc)) from exc
    errors = []
    if report["missing_nodes"] or report["missing_inputs"]:
        errors.append({"code": "WORKFLOW_NODE_MISSING", "details": report})
    for node, version in body.node_versions.items():
        if installed_versions.get(node) != version:
            errors.append({"code": "NODE_VERSION_MISMATCH", "node": node})
    for slot in body.reference_slots:
        if slot not in body.parameter_map:
            errors.append({"code": "PENDING_WORKFLOW_MAPPING", "slot": slot})
        family = "audio" if "audio" in slot else "video" if "video" in slot else "image"
        if family != "image" and family not in body.media_transfers:
            errors.append({"code": "PENDING_WORKFLOW_MAPPING", "family": family})
    return errors


def assemble_template(template: str, name: str):
    # These nodes exist only on the explicitly Fake provider, never on a real instance.
    if template in {"h3-ref2va-2img", "h3-ref2va-2img-1aud"}:
        import json
        from pathlib import Path

        from shine.comfy.workflow import graph_hash

        filename = "ref2va-2img" + ("-1aud" if template.endswith("-1aud") else "") + ".json"
        source = json.loads(
            (Path(__file__).resolve().parents[3] / "config" / "workflows" / filename).read_text(encoding="utf-8")
        )
        pinned = (
            "7d86f7b6d994b0b38cc379a4fe0268f68776c4a43e80fb0f7651a3005f57dcd6"
            if template.endswith("-1aud")
            else "6f7a5fb19c548ca945f8a2169e05c836d7c06f03466cf2b893b3be599505d112"
        )
        if graph_hash(source["graph"]) != pinned:
            raise HTTPException(409, "AUDITED_TEMPLATE_HASH_CHANGED")
        return WorkflowDraft(
            name=name,
            category="H3_REF2VA",
            graph=source["graph"],
            parameter_map=source["parameter_map"],
            media_transfers=source.get("media_transfers", {}),
            reference_slots=[k for k in source["parameter_map"] if k.startswith("reference_")],
            model_family="MiniMax-H3",
            generation_modes=["Ref2VA"],
            duration_range=(4, 15),
            template_audit={
                "source": "config/workflows/" + filename,
                "graph_sha256": pinned,
                "review": "H3_REF2VA_V1.2.1_FINAL_CLOSEOUT",
                "verification": "DRAFT_REQUIRES_LOCAL_VALIDATION",
            },
        )
    if template not in {"fake-image-v1", "fake-voice-v1"}:
        raise HTTPException(422, "UNTRUSTED_TEMPLATE")
    return WorkflowDraft(
        name=name,
        category="IMAGE_GENERATION" if "image" in template else "VOICE_DESIGN",
        graph={"1": {"class_type": "ShineFake", "inputs": {"prompt": ""}}},
        parameter_map={"prompt": "1.inputs.prompt"},
    )


def match_constraints(row, constraints, db):
    from shine.comfyops.models import ComfyInstance, ModelPackage, NodePackage

    p = row.payload
    errors = []
    mode = constraints.get("generation_mode")
    if mode and mode not in p.get("generation_modes", []):
        errors.append("GENERATION_MODE_NOT_VERIFIED")
    duration = constraints.get("duration_s")
    bounds = p.get("duration_range")
    if duration is not None and (not bounds or not bounds[0] <= duration <= bounds[1]):
        errors.append("DURATION_NOT_VERIFIED")
    ratio = constraints.get("aspect_ratio")
    if ratio and ratio not in p.get("aspect_ratios", []):
        errors.append("ASPECT_RATIO_NOT_VERIFIED")
    instance_id = constraints.get("instance_id")
    if instance_id:
        instance = db.get(ComfyInstance, instance_id)
        if not instance or instance_id not in p.get("instance_ids", []):
            errors.append("INSTANCE_NOT_VERIFIED")
    for family, count in constraints.get("references", {}).items():
        prefix = "reference_" + family + "_"
        if count > sum(slot.startswith(prefix) for slot in p.get("reference_slots", [])):
            errors.append("REFERENCE_CAPACITY_EXCEEDED:" + family)
        if count and family in {"audio", "video"} and family not in p.get("media_transfers", {}):
            errors.append("MEDIA_TRANSFER_NOT_VERIFIED:" + family)
    budget = constraints.get("budget_mb")
    if budget is not None and (p.get("resource_mb") is None or p["resource_mb"] > budget):
        errors.append("RESOURCE_BUDGET_UNKNOWN_OR_EXCEEDED")
    for identity in p.get("model_ids", []) + p.get("node_package_ids", []):
        package = db.get(ModelPackage, identity) or db.get(NodePackage, identity)
        allowed = (
            {"MOCK_AVAILABLE"} if constraints.get("offline", True) else {"AVAILABLE", "FILES_VERIFIED_PENDING_RUNTIME"}
        )
        if not package or package.status not in allowed:
            errors.append("DEPENDENCY_NOT_AVAILABLE:" + identity)
            continue
        if package.payload["license_id"].lower() in {"unknown", "unverified"} or (
            constraints.get("usage") == "commercial" and "research" in package.payload["license_id"].lower()
        ):
            errors.append("DEPENDENCY_LICENSE_NOT_APPROVED:" + identity)
        if p.get("package_hashes", {}).get(identity) != package.sha256:
            errors.append("DEPENDENCY_SNAPSHOT_CHANGED:" + identity)
        expected = constraints.get("package_revisions", {}).get(identity)
        if expected and package.payload["revision"] != expected:
            errors.append("DEPENDENCY_VERSION_MISMATCH:" + identity)
    if constraints.get("continuity", "cut") not in {"cut", "soft_reference"}:
        errors.append("PENDING_HARD_CONTINUITY_TEST")
    return errors
