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
    if template not in {"fake-image-v1", "fake-voice-v1"}:
        raise HTTPException(422, "UNTRUSTED_TEMPLATE")
    return WorkflowDraft(
        name=name,
        category="IMAGE_GENERATION" if "image" in template else "VOICE_DESIGN",
        graph={"1": {"class_type": "ShineFake", "inputs": {"prompt": ""}}},
        parameter_map={"prompt": "1.inputs.prompt"},
    )
