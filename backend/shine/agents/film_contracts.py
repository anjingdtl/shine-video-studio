"""Project-authored film crew contracts; proposals carry no execution authority."""

from typing import Literal

from pydantic import Field, model_validator

from shine.schemas import APIModel, DialogueLine, ShotSpec, StyleBible, StyleInputCheck


class SceneBeat(APIModel):
    id: str = Field(min_length=1, max_length=80)
    description: str = Field(min_length=1, max_length=4000)
    characters: list[str] = Field(default_factory=list, max_length=20)
    dialogue: list[DialogueLine] = Field(default_factory=list, max_length=100)


class ScriptBreakdown(APIModel):
    schema_version: Literal["1.1"] = "1.1"
    locked_story_facts: list[str] = Field(default_factory=list, max_length=100)
    beats: list[SceneBeat] = Field(min_length=1, max_length=100)


class ShotIntent(APIModel):
    id: str = Field(min_length=1, max_length=80)
    beat_id: str
    visual_description: str = Field(min_length=1, max_length=8000)
    duration_s: float = Field(ge=4, le=15)
    asset_ids: list[str] = Field(default_factory=list, max_length=12)


class DirectorialPlan(APIModel):
    schema_version: Literal["1.1"] = "1.1"
    shots: list[ShotIntent] = Field(min_length=1, max_length=100)


class CameraShot(APIModel):
    intent_id: str
    composition: str = Field(min_length=1, max_length=2000)
    movement: str = Field(min_length=1, max_length=1000)
    lighting: str = Field(min_length=1, max_length=2000)
    axis: str = Field(min_length=1, max_length=1000)


class CameraPlan(APIModel):
    schema_version: Literal["1.1"] = "1.1"
    shots: list[CameraShot] = Field(min_length=1, max_length=100)


class ContinuityReport(APIModel):
    schema_version: Literal["1.1"] = "1.1"
    issues: list[str] = Field(default_factory=list, max_length=100)
    recommendation: Literal["proceed_to_preflight", "needs_review"]


class SoundPlan(APIModel):
    schema_version: Literal["1.1"] = "1.1"
    ambience: str = Field(min_length=1, max_length=4000)
    voice_asset_ids: list[str] = Field(default_factory=list, max_length=3)


class EditClip(APIModel):
    job_id: str
    attempt_id: str
    artifact_id: str
    in_s: float = Field(ge=0, allow_inf_nan=False)
    out_s: float = Field(gt=0, allow_inf_nan=False)
    transition: Literal["CUT"] = "CUT"
    reason: str = Field(min_length=1, max_length=1000)

    @model_validator(mode="after")
    def ordered(self):
        if self.out_s <= self.in_s:
            raise ValueError("EDL out point must follow the in point")
        return self


class EditDecisionList(APIModel):
    schema_version: Literal["1.1"] = "1.1"
    clips: list[EditClip] = Field(min_length=1, max_length=100)


class StyleProposal(APIModel):
    schema_version: Literal["1.1"] = "1.1"
    suggestion: StyleBible
    rationale: str = Field(min_length=1, max_length=4000)


class ProductionPlan(APIModel):
    schema_version: Literal["1.1"] = "1.1"
    priorities: list[str] = Field(default_factory=list, max_length=100)
    blockers: list[str] = Field(default_factory=list, max_length=100)


class StyleInputComparison(StyleInputCheck):
    style_text: str = Field(min_length=1, max_length=2000)
    shot_text: str | None = Field(default=None, min_length=1, max_length=8000)
    shot_path: str | None = Field(default=None, max_length=256, pattern="^/")
    confidence: float = Field(ge=0, le=1)


class StyleInputAssessment(APIModel):
    """Text consistency proposal, never pixel/audio coverage or film approval."""

    schema_version: Literal["1.1"] = "1.1"
    checks: list[StyleInputComparison] = Field(min_length=7, max_length=7)
    recommendation: Literal["proceed_to_preflight", "needs_review"]

    @model_validator(mode="after")
    def complete_and_consistent(self):
        if {check.field for check in self.checks} != set(StyleBible.model_fields):
            raise ValueError("STYLE_INPUT_FIELD_COVERAGE")
        if self.recommendation == "proceed_to_preflight" and any(
            check.status != "consistent" or check.confidence < 0.7 for check in self.checks
        ):
            raise ValueError("STYLE_INPUT_RECOMMENDATION_CONTRADICTION")
        return self


class StyleInputJudgment(StyleInputCheck):
    shot_path: str | None = Field(
        default=None, max_length=256, pattern="^/",
        description="JSON Pointer to one existing nonempty ShotSpec string, e.g. /story_beat, "
                    "/camera/shot_size or /dialogue/0/text. Never select an object, array or number. "
                    "Use null if the corresponding detail is not specified.",
    )
    confidence: float = Field(ge=0, le=1)


class StyleInputWire(APIModel):
    """The model owns judgments, not immutable source quotation values."""

    protocol: Literal["independent-text-v2"]
    checks: list[StyleInputJudgment] = Field(min_length=7, max_length=7)
    recommendation: Literal["proceed_to_preflight", "needs_review"]

    @model_validator(mode="after")
    def complete_and_consistent(self):
        if {check.field for check in self.checks} != set(StyleBible.model_fields):
            raise ValueError("STYLE_INPUT_FIELD_COVERAGE")
        if self.recommendation == "proceed_to_preflight" and any(
            check.status != "consistent" or check.confidence < 0.7 for check in self.checks
        ):
            raise ValueError("STYLE_INPUT_RECOMMENDATION_CONTRADICTION")
        return self


class FilmRunRequest(APIModel):
    brief: str = Field(default="", max_length=20000)
    locked_story_facts: list[str] = Field(default_factory=list, max_length=100)
    input_revision_ids: list[str] = Field(default_factory=list, max_length=100)
    tool: Literal["propose", "style_proposal"] = "propose"
    offline_preview: bool = False
    allow_remote_text: bool = False
    supersedes_artifact_id: str | None = None
    shot_spec: ShotSpec | None = None


class ComfyOpsProposal(APIModel):
    schema_version: Literal["1.3"] = "1.3"
    operations: list[Literal["inspect_environment", "propose_install_plan", "search_capabilities",
                             "build_workflow_draft", "assess_project_readiness"]] = Field(max_length=30)
    capability_requests: list[str] = Field(default_factory=list, max_length=30)
    rationale: str = Field(min_length=1, max_length=4000)


class FilmAgentRegistry:
    """Closed capabilities. Extending a role requires a contract and reviewed code."""

    roles = {
        "qc_reviewer": ("输入一致性审核", StyleInputAssessment, (), ("base",)),
        "comfyops": ("环境与能力工程师", ComfyOpsProposal, (), ("limits",)),
        "scriptwriter": ("编剧", ScriptBreakdown, (), ("base", "dialogue")),
        "director": ("导演", DirectorialPlan, ("ScriptBreakdown",), ("base", "references")),
        "cinematographer": ("摄影指导", CameraPlan, ("DirectorialPlan",), ("base", "keyframes")),
        "editor": ("剪辑师", EditDecisionList, (), ("timing",)),
        "continuity_supervisor": ("场记", ContinuityReport, ("DirectorialPlan", "CameraPlan"), ("references",)),
        "sound_director": ("声音导演", SoundPlan, ("ScriptBreakdown",), ("dialogue",)),
        "producer": ("制片统筹", ProductionPlan, (), ("limits",)),
    }

    @classmethod
    def get(cls, role: str, tool: str = "propose"):
        if role not in cls.roles or tool not in {"propose", "style_proposal"}:
            raise ValueError("ROLE_OR_TOOL_DENIED")
        if tool == "style_proposal":
            if role not in {"scriptwriter", "director", "cinematographer", "editor"}:
                raise ValueError("ROLE_OR_TOOL_DENIED")
            return StyleProposal, ()
        _, contract, required, _ = cls.roles[role]
        return contract, required
