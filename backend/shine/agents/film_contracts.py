"""Project-authored film crew contracts; proposals carry no execution authority."""

from typing import Literal

from pydantic import Field, model_validator

from shine.schemas import STYLE_BIBLE_FIELDS, APIModel, DialogueLine, ShotSpec, StyleBible, StyleInputCheck


class SceneBeat(APIModel):
    """One story beat; its dialogue timecodes are local to this beat."""

    id: str = Field(min_length=1, max_length=80)
    description: str = Field(min_length=1, max_length=4000)
    characters: list[str] = Field(
        default_factory=list,
        max_length=20,
        description="Human-readable approved character names/entity IDs. Never put opaque asset UUIDs here.",
    )
    dialogue: list[DialogueLine] = Field(default_factory=list, max_length=100)


class ScriptBreakdown(APIModel):
    schema_version: Literal["1.1"] = "1.1"
    character_names: dict[str, str] = Field(
        default_factory=dict,
        description=(
            "Proposed readable character names keyed by the original script label. "
            "For S1/S2 or numbered characters, suggest distinct human names in the script language "
            "for user confirmation. Preserve original labels in beats and dialogue; "
            "never rename or approve assets."
        ),
    )
    locked_story_facts: list[str] = Field(default_factory=list, max_length=100)
    prompt_story_facts: list[str] = Field(
        default_factory=list, max_length=100,
        description="For non-English locked facts, one faithful English visual-prompt translation per fact, in order.",
    )
    prompt_style: StyleBible | None = Field(
        default=None,
        description="Faithful English rendering of all seven frozen style fields; original stays frozen.",
    )
    beats: list[SceneBeat] = Field(min_length=1, max_length=100)


class ShotIntentContinuity(APIModel):
    """The shot boundary the director wants, materialised by apply-camera."""

    mode: Literal["cut", "last_frame"] = "cut"
    note: str = Field(default="", max_length=1000)


class EditorialCut(APIModel):
    beat_id: str
    start_s: float = Field(gt=0, lt=15, allow_inf_nan=False)
    visual_description: str = Field(
        min_length=1, max_length=1000,
        description="English visual prose with English role labels. Omit spoken lines and lyrics.",
    )


class ShotIntent(APIModel):
    id: str = Field(min_length=1, max_length=80)
    beat_id: str
    visual_description: str = Field(
        min_length=1, max_length=8000,
        description="English opening visual prose with English role labels. Omit speech and lyrics.",
    )
    duration_s: float = Field(
        ge=4,
        le=15,
        description="Seconds for this shot; must contain every dialogue timecode in its beat with a short tail.",
    )
    asset_ids: list[str] = Field(default_factory=list, max_length=12)
    continuity: ShotIntentContinuity | None = None
    cuts: list[EditorialCut] = Field(default_factory=list, max_length=19)


class DirectorialPlan(APIModel):
    schema_version: Literal["1.1"] = "1.1"
    shots: list[ShotIntent] = Field(min_length=1, max_length=100)


class CameraShot(APIModel):
    intent_id: str
    composition: str = Field(min_length=1, max_length=2000)
    movement: str = Field(default="", max_length=1000)
    lighting: str = Field(default="", max_length=2000)
    axis: str = Field(default="", max_length=1000)


class CameraPlan(APIModel):
    schema_version: Literal["1.1"] = "1.1"
    shots: list[CameraShot] = Field(min_length=1, max_length=100)


class ContinuityReport(APIModel):
    schema_version: Literal["1.1"] = "1.1"
    issues: list[str] = Field(default_factory=list, max_length=100)
    recommendation: Literal["proceed_to_preflight", "needs_review"]


class ContinuityArbitrationRuling(APIModel):
    """One ruling on one raised issue, against one rule of the locked pack.

    The issue is addressed by its index in the arbitration subject; the
    server binds the authoritative text, so a paraphrase can never detach a
    ruling from the issue it answers.
    """

    issue_index: int = Field(ge=0, le=99)
    issue: str = Field(default="", max_length=2000)
    verdict: Literal["not_a_conflict", "conflict"]
    rule_id: str = Field(min_length=1, max_length=80)
    rationale: str = Field(min_length=1, max_length=2000)


class ContinuityArbitration(APIModel):
    """The crew's arbiter ruling on a conflict between the roles.

    The roles of a sequence can contradict each other.  The role that owns
    the shots arbitrates, and it arbitrates on the official criteria of the
    locked rule pack: every issue is ruled on, by name, against a rule that
    exists in the pack.  The ruling is a proposal; a human still applies it.
    """

    schema_version: Literal["1.0"] = "1.0"
    rulings: list[ContinuityArbitrationRuling] = Field(min_length=1, max_length=100)
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


class EditIntentClip(APIModel):
    """One planned slot of the pre-production edit intent (frozen before any render)."""

    intent_id: str = Field(min_length=1, max_length=80)
    order: int = Field(ge=1, le=100)
    planned_duration_s: float = Field(ge=4, le=15, allow_inf_nan=False)
    transition: Literal["CUT"] = "CUT"
    continuity_note: str = Field(default="", max_length=1000)


class EditIntentPlan(APIModel):
    """The editor's frozen pre-production intent: order, rhythm, transitions.

    It is a proposal about shots that do not exist yet, so it references
    DirectorialPlan intent ids, never jobs.  The real EditDecisionList is
    derived after generation from the real media durations.
    """

    schema_version: Literal["1.2"] = "1.2"
    intent_id: str = Field(min_length=1, max_length=80)
    clips: list[EditIntentClip] = Field(min_length=1, max_length=100)
    rhythm: str = Field(min_length=1, max_length=2000)
    dialogue_pace: str = Field(min_length=1, max_length=2000)
    director_style_id: str | None = Field(default=None, max_length=64)
    director_style_resolution_sha256: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    editing_direction: str | None = Field(default=None, max_length=1800)

    @model_validator(mode="after")
    def covers_every_intent_once_in_order(self):
        orders = [clip.order for clip in self.clips]
        if len(set(orders)) != len(orders):
            raise ValueError("EDIT_INTENT_ORDER_REPEATED")
        if self.clips and sorted(orders) != list(range(1, len(self.clips) + 1)):
            raise ValueError("EDIT_INTENT_ORDER_NOT_A_PERMUTATION")
        ids = [clip.intent_id for clip in self.clips]
        if len(set(ids)) != len(ids):
            raise ValueError("EDIT_INTENT_INTENT_REPEATED")
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
        if {check.field for check in self.checks} != set(STYLE_BIBLE_FIELDS):
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
                    "Use null if the corresponding detail is not specified. /continuity describes the "
                    "boundary to a preceding shot; its cut mode is never evidence of internal cuts.",
    )
    confidence: float = Field(ge=0, le=1)


class StyleInputWire(APIModel):
    """The model owns judgments, not immutable source quotation values."""

    protocol: Literal["independent-text-v2"]
    checks: list[StyleInputJudgment] = Field(min_length=7, max_length=7)
    recommendation: Literal["proceed_to_preflight", "needs_review"]

    @model_validator(mode="after")
    def complete_and_consistent(self):
        if {check.field for check in self.checks} != set(STYLE_BIBLE_FIELDS):
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
    tool: Literal[
        "propose",
        "style_proposal",
        "edit_intent",
        "continuity_arbitration",
        "script_prepare",
        "material_image_brief",
    ] = "propose"
    offline_preview: bool = False
    allow_remote_text: bool = False
    supersedes_artifact_id: str | None = None
    shot_spec: ShotSpec | None = None
    production_context: dict | None = None
    consent_document_id: str | None = None
    consent_document_sha256: str | None = None


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
        if role not in cls.roles or tool not in {"propose", "style_proposal", "edit_intent",
                                                 "continuity_arbitration", "script_prepare",
                                                 "material_image_brief"}:
            raise ValueError("ROLE_OR_TOOL_DENIED")
        if tool == "script_prepare":
            # Pre-production script preparation is a separate, shallower contract
            # than the formal ScriptBreakdown: no shots, no durations, no asset
            # ids and no H3 prompt text, and it is allowed before the Style Bible
            # is frozen.  It can never be presented as a storyboard.
            if role != "scriptwriter":
                raise ValueError("ROLE_OR_TOOL_DENIED")
            from shine.materialprep.contracts import ScriptPreparation

            return ScriptPreparation, ()
        if tool == "material_image_brief":
            # The producer keeps its default ProductionPlan contract.  A second
            # tool selects a second schema; the default is never globally changed
            # into an image description.
            if role != "producer":
                raise ValueError("ROLE_OR_TOOL_DENIED")
            from shine.materialprep.contracts import MaterialImageBrief

            return MaterialImageBrief, ()
        if tool == "style_proposal":
            if role not in {"scriptwriter", "director", "cinematographer", "editor"}:
                raise ValueError("ROLE_OR_TOOL_DENIED")
            return StyleProposal, ()
        if tool == "edit_intent":
            # The same editor role freezes the pre-production edit intent from
            # the DirectorialPlan; the real EDL still requires passed jobs.
            if role != "editor":
                raise ValueError("ROLE_OR_TOOL_DENIED")
            return EditIntentPlan, ("DirectorialPlan",)
        if tool == "continuity_arbitration":
            # The role that owns the shots arbitrates a contradiction between
            # the crew roles.  No other role may rule on another role, and the
            # arbitration may only be released by a human who applies it.
            if role != "director":
                raise ValueError("ROLE_OR_TOOL_DENIED")
            return ContinuityArbitration, ("DirectorialPlan", "CameraPlan", "ContinuityReport")
        _, contract, required, _ = cls.roles[role]
        return contract, required
