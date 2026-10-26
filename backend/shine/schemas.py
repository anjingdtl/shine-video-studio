from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class APIModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class CreateProject(APIModel):
    title: str = Field(min_length=1, max_length=160)
    description: str = Field(default="", max_length=4000)
    language: str = Field(default="Chinese", min_length=2, max_length=32)


class ProjectSettings(APIModel):
    language: str | None = Field(default=None, min_length=2, max_length=32)
    allow_remote_review: bool | None = None
    auto_repair_enabled: bool | None = None
    quality_policy: dict[str, Any] | None = None


class QualityPolicyUpdate(APIModel):
    """Per-project quality gate configuration.

    The policy may relax non-critical thresholds per shot priority, but hard
    rules (identity, master asset, key dialogue) are never relaxed.
    """

    require_semantic_review: bool = True
    require_audio_review: bool = True
    require_style_review: bool = True
    min_confidence: float = Field(default=0.7, ge=0, le=1)
    accept_mock_review: bool = False
    allow_unverified_technical: bool = False
    priority_thresholds: dict[str, float] = Field(default_factory=dict)
    hard_rules: list[str] = Field(default_factory=list, max_length=64)


class AssetMetadataUpdate(APIModel):
    role: str | None = Field(default=None, max_length=32)
    entity_id: str | None = Field(default=None, max_length=80)
    tags: list[str] | None = Field(default=None, max_length=40)


class StyleFreezeRequest(APIModel):
    grant_id: str | None = None
    snapshot: str | None = None
    approval_note: str = Field(default="", max_length=1000)


class StyleBible(APIModel):
    photography_language: str = Field(min_length=1, max_length=2000)
    shot_blocking: str = Field(min_length=1, max_length=2000)
    color_palette: str = Field(min_length=1, max_length=2000)
    lighting_tone: str = Field(min_length=1, max_length=2000)
    lens_texture: str = Field(min_length=1, max_length=2000)
    editing_rhythm: str = Field(min_length=1, max_length=2000)
    sound_performance: str = Field(min_length=1, max_length=2000)


class DialogueLine(APIModel):
    speaker_id: str = Field(min_length=1, max_length=80)
    text: str = Field(min_length=1, max_length=1000)
    start_s: float = Field(ge=0)
    end_s: float = Field(gt=0)

    @model_validator(mode="after")
    def ordered_times(self) -> DialogueLine:
        if self.end_s <= self.start_s:
            raise ValueError("dialogue end_s must be after start_s")
        return self


class ContinuityMode(StrEnum):
    CUT = "cut"
    LAST_FRAME = "last_frame"
    HARD_FRAME_ANCHOR = "hard_frame_anchor"
    VIDEO_REFERENCE = "video_reference"
    AUDIO_REFERENCE = "audio_reference"


class ContinuitySettings(APIModel):
    mode: ContinuityMode = Field(
        default=ContinuityMode.CUT,
        description="Relation to the preceding shot. CUT starts an independent shot, not an internal edit. "
                    "last_frame is SOFT_REFERENCE_CONTINUITY, never a guaranteed first-frame anchor",
    )
    previous_shot_id: str | None = None
    previous_job_id: str | None = None
    reference_start_s: float | None = Field(default=None, ge=0)
    reference_end_s: float | None = Field(default=None, gt=0)
    notes: str = Field(default="", max_length=500)

    @field_validator("mode", mode="before")
    @classmethod
    def normalize_continuity_requirement(cls, value):
        if not isinstance(value, str):
            return value
        return {
            "SOFT_REFERENCE_CONTINUITY": "last_frame",
            "HARD_FRAME_ANCHOR_CONTINUITY": "hard_frame_anchor",
            "VIDEO_REFERENCE_CONTINUITY": "video_reference",
        }.get(value, value)

    @model_validator(mode="after")
    def ordered_reference(self) -> ContinuitySettings:
        if self.reference_start_s is not None and self.reference_end_s is not None:
            if self.reference_end_s <= self.reference_start_s:
                raise ValueError("continuity reference end_s must be after start_s")
        return self


class ShotSpec(APIModel):
    schema_version: Literal["1.0"] = "1.0"
    story_beat: str = Field(min_length=1, max_length=2000)
    duration_target_s: float = Field(ge=4, le=15)
    shot_priority: Literal["A", "B", "C"] = "B"
    style_version_id: str = Field(default="", max_length=36)
    camera: dict[str, str] = Field(default_factory=dict)
    characters: list[str] = Field(default_factory=list, max_length=20)
    required_asset_ids: list[str] = Field(default_factory=list, max_length=12)
    dialogue: list[DialogueLine] = Field(default_factory=list, max_length=30)
    sound: dict[str, str] = Field(default_factory=dict)
    continuity: ContinuitySettings = Field(default_factory=ContinuitySettings)
    locked_story_facts: list[str] = Field(default_factory=list, max_length=30)

    @model_validator(mode="after")
    def dialogue_fits_duration(self) -> ShotSpec:
        if any(line.end_s > self.duration_target_s for line in self.dialogue):
            raise ValueError("dialogue must fit inside the target shot duration")
        spoken = {line.speaker_id for line in self.dialogue}
        if spoken and not spoken.issubset(set(self.characters)):
            raise ValueError("every dialogue speaker must be listed in characters")
        return self


class ReferenceUse(StrEnum):
    SUBJECT = "subject"
    PICTURE = "picture"
    VIDEO = "video"
    AUDIO = "audio"


class FrameRole(StrEnum):
    FIRST = "first_frame"
    LAST = "last_frame"
    KEYFRAME = "keyframe"


class AssetReference(APIModel):
    asset_id: str
    use_as: ReferenceUse
    role: str | None = Field(default=None, max_length=64)
    frame_role: FrameRole | None = None


class ShotBindingInput(APIModel):
    asset_id: str
    use_as: ReferenceUse
    role: str | None = Field(default=None, max_length=64)
    frame_role: FrameRole | None = None
    ordinal: int = Field(default=0, ge=0, le=11)


class ReplaceBindings(APIModel):
    bindings: list[ShotBindingInput] = Field(default_factory=list, max_length=12)


class CreateShot(APIModel):
    name: str = Field(min_length=1, max_length=160)
    spec: ShotSpec
    bindings: list[ShotBindingInput] = Field(default_factory=list, max_length=12)


class MockScenario(StrEnum):
    SUCCESS = "success"
    DELAY = "delay"
    FAIL = "fail"
    UNCERTAIN = "uncertain"
    DUPLICATE = "duplicate"


class RenderProvider(StrEnum):
    MOCK = "mock"
    COMFYUI = "comfyui"


class GenerationMode(StrEnum):
    T2VA = "T2VA"
    I2VA = "I2VA"
    FL2VA = "FL2VA"
    L2VA = "L2VA"
    REF2VA = "Ref2VA"


class CreateJob(APIModel):
    shot_id: str
    references: list[AssetReference] | None = Field(default=None, max_length=12)
    depends_on: list[str] = Field(default_factory=list, max_length=20)
    mock_scenario: MockScenario = MockScenario.SUCCESS
    provider: RenderProvider = RenderProvider.MOCK
    generation_mode: GenerationMode | None = None
    workflow_profile_id: str | None = None


class HardFailure(APIModel):
    code: str = Field(min_length=1, max_length=80)
    evidence: str = Field(min_length=1, max_length=1000)
    evidence_time_s: float | None = Field(default=None, ge=0)


class QCReportData(APIModel):
    result: Literal["passed", "failed", "needs_review"]
    hard_failures: list[HardFailure] = Field(default_factory=list)
    soft_scores: dict[str, float] = Field(default_factory=dict)
    review_confidence: float = Field(ge=0, le=1)
    human_review_required: bool = False
    is_mock: bool


class RepairAction(StrEnum):
    REBUILD_REFERENCE_BINDINGS = "rebuild_reference_bindings"
    REVISE_SHOT_INTENT = "revise_shot_intent"
    ADJUST_PROMPT_STRATEGY = "adjust_prompt_strategy"
    ADJUST_WORKFLOW_PARAMETERS = "adjust_workflow_parameters"
    RERENDER_AFTER_TRANSIENT = "rerender_after_transient"
    WAIT_FOR_HUMAN = "wait_for_human"


class RepairPlan(APIModel):
    action: RepairAction
    root_cause: str = Field(min_length=1, max_length=500)
    changes: list[str] = Field(default_factory=list, max_length=20)
    max_additional_attempts: int = Field(default=2, ge=0, le=2)
    human_review_required: bool = False
    error_category: str | None = Field(default=None, max_length=32)


class ReferenceManifestItem(APIModel):
    label: str
    asset_id: str
    sha256: str
    storage_key: str
    content_type: str
    use_as: ReferenceUse
    role: str | None = None
    frame_role: FrameRole | None = None
    ordinal: int = 0
    duration_s: float | None = None
    entity_id: str | None = None


class CompiledPrompt(APIModel):
    mode: GenerationMode
    compiler_version: str
    rule_version: str
    skill_sha256: str
    base_rule_sha256: str
    ref_rule_sha256: str
    sections: dict[str, str]
    prompt: str
    prompt_sha256: str
    labels: dict[str, str]
    warnings: list[str] = Field(default_factory=list)


class RenderRequest(APIModel):
    project_language: str = "Chinese"
    style_input_assessment_id: str | None = None
    style_input_assessment_sha256: str | None = None
    rulepack_id: str | None = None
    rulepack_hash: str | None = None
    idempotency_key: str
    shot_id: str
    shot_revision: int
    style_version_id: str
    style_hash: str
    model_checkpoint_id: str = Field(default="MiniMax-H3-Base", max_length=160)
    provider: Literal["mock", "comfyui"] = "mock"
    generation_mode: GenerationMode = GenerationMode.T2VA
    reference_manifest: list[ReferenceManifestItem] = Field(default_factory=list, max_length=12)
    shot_spec: ShotSpec
    style_snapshot: StyleBible
    compiled_prompt: str | None = None
    compiled_prompt_sections: dict[str, str] = Field(default_factory=dict)
    prompt_sha256: str | None = None
    prompt_skill_hash: str | None = None
    compiler_version: str | None = None
    rule_version: str | None = None
    workflow_profile_hash: str | None = None
    seed: int | None = Field(default=None, ge=0)
    expected_duration_s: float = Field(ge=4, le=15)

    @field_validator("compiled_prompt")
    @classmethod
    def reject_unreviewed_prompt(cls, value: str | None) -> str | None:
        if value and len(value) > 30000:
            raise ValueError("compiled prompt exceeds the maximum length")
        return value

    @model_validator(mode="after")
    def require_style(self) -> RenderRequest:
        if not self.style_version_id:
            raise ValueError("render requests require a frozen style version")
        return self


# ---------------------------------------------------------------------------
# Audit, workers, and system status
# ---------------------------------------------------------------------------


class AuditEventItem(APIModel):
    id: str
    project_id: str | None
    actor: str
    event_type: str
    entity_type: str
    entity_id: str
    payload: dict[str, Any]
    created_at: datetime


class AuditPage(APIModel):
    items: list[AuditEventItem]
    total: int
    limit: int
    offset: int


class WorkerStatus(APIModel):
    worker_id: str
    online: bool
    version: str
    pid: int | None
    status: str
    current_job_id: str | None
    current_phase: str | None
    last_heartbeat_at: datetime
    last_error: str | None
    processed_count: int


class SystemStatus(APIModel):
    api_online: bool = True
    worker_online: bool
    mock_provider_available: bool
    h3_connected: bool
    render_mode: str
    pending_jobs: int
    running_jobs: int
    failed_jobs: int
    blocked_jobs: int
    workers: list[WorkerStatus]
    last_error: str | None


class ArtifactItem(APIModel):
    id: str
    job_id: str | None
    artifact_kind: str
    sha256: str
    byte_size: int
    content_type: str
    is_mock: bool
    created_at: datetime


# ---------------------------------------------------------------------------
# LLM gateway
# ---------------------------------------------------------------------------


class ProviderKind(StrEnum):
    OPENAI_COMPATIBLE = "openai_compatible"
    ANTHROPIC = "anthropic"
    OLLAMA = "ollama"
    MOCK = "mock"


class TextGenerationOptions(APIModel):
    max_completion_tokens: int | None = Field(default=None, ge=1, le=524288, strict=True)
    reasoning_effort: Literal["low", "medium", "high", "xhigh", "max"] | None = None


class ProviderProfileCreate(APIModel):
    name: str = Field(min_length=1, max_length=80)
    kind: ProviderKind
    base_url: str = Field(default="", max_length=500)
    model: str = Field(default="", max_length=160)
    api_key: str | None = Field(default=None, max_length=4096)
    timeout_s: float = Field(default=60, ge=1, le=600)
    max_retries: int = Field(default=2, ge=0, le=5)
    temperature: float = Field(default=0.2, ge=0, le=2)
    enabled: bool = True
    generation_options: TextGenerationOptions = Field(default_factory=TextGenerationOptions)

    @model_validator(mode="after")
    def validate_endpoint(self) -> ProviderProfileCreate:
        if self.generation_options.model_dump(exclude_none=True) and self.kind != ProviderKind.OPENAI_COMPATIBLE:
            raise ValueError("Generation options currently require an OpenAI-compatible provider")
        if self.kind in {ProviderKind.OPENAI_COMPATIBLE, ProviderKind.ANTHROPIC, ProviderKind.OLLAMA}:
            if not self.base_url.startswith(("http://", "https://")):
                raise ValueError("base_url must start with http:// or https://")
            if not self.model:
                raise ValueError("model is required for remote providers")
        return self


class ProviderKeyUpdate(APIModel):
    api_key: str = Field(min_length=1, max_length=4096)


class ProviderProfileUpdate(APIModel):
    name: str | None = Field(default=None, min_length=1, max_length=80)
    kind: ProviderKind | None = None
    base_url: str | None = Field(default=None, max_length=500)
    model: str | None = Field(default=None, max_length=160)
    timeout_s: float | None = Field(default=None, ge=1, le=600)
    max_retries: int | None = Field(default=None, ge=0, le=5)
    temperature: float | None = Field(default=None, ge=0, le=2)
    enabled: bool | None = None
    generation_options: TextGenerationOptions | None = None


class ProviderProfilePublic(APIModel):
    id: str
    name: str
    kind: ProviderKind
    base_url: str
    model: str
    has_api_key: bool
    capabilities: dict[str, Any]
    generation_options: TextGenerationOptions = Field(default_factory=TextGenerationOptions)
    timeout_s: float
    max_retries: int
    temperature: float
    enabled: bool
    created_at: datetime
    updated_at: datetime


class ProviderTestResult(APIModel):
    ok: bool
    latency_ms: int | None = None
    error_code: str | None = None
    message: str = ""
    capabilities: dict[str, Any] = Field(default_factory=dict)


class AgentRole(StrEnum):
    SCRIPTWRITER = "scriptwriter"
    DIRECTOR = "director"
    CINEMATOGRAPHER = "cinematographer"
    EDITOR = "editor"
    CONTINUITY_SUPERVISOR = "continuity_supervisor"
    SOUND_DIRECTOR = "sound_director"
    PRODUCER = "producer"
    COMFYOPS = "comfyops"
    SCRIPT_PLANNER = "script_planner"
    SHOT_PLANNER = "shot_planner"
    REPAIR_PLANNER = "repair_planner"
    QC_REVIEWER = "qc_reviewer"
    VLM_REVIEWER = "vlm_reviewer"
    ASR_REVIEWER = "asr_reviewer"


class AgentRouteUpdate(APIModel):
    role: AgentRole
    provider_profile_id: str


class AgentRoutePublic(APIModel):
    role: str
    provider_profile_id: str
    profile_name: str


# ---------------------------------------------------------------------------
# Script and agent planning
# ---------------------------------------------------------------------------


class ScriptBeat(APIModel):
    summary: str = Field(min_length=1, max_length=1000)
    characters: list[str] = Field(default_factory=list, max_length=20)


class ScriptScene(APIModel):
    name: str = Field(min_length=1, max_length=160)
    location: str = Field(default="", max_length=300)
    beats: list[ScriptBeat] = Field(default_factory=list, max_length=30)


class ScriptEpisode(APIModel):
    name: str = Field(min_length=1, max_length=160)
    scenes: list[ScriptScene] = Field(default_factory=list, max_length=30)


class ScriptPlan(APIModel):
    schema_version: Literal["1.0"] = "1.0"
    title: str = Field(default="", max_length=200)
    episodes: list[ScriptEpisode] = Field(default_factory=list, max_length=50)


class AgentPlanRequest(APIModel):
    text: str | None = Field(default=None, max_length=200000)
    document_id: str | None = None
    max_shots: int = Field(default=6, ge=1, le=24)
    apply: bool = False


class ShotProposal(APIModel):
    name: str = Field(min_length=1, max_length=160)
    spec: ShotSpec


class AgentPlanResult(APIModel):
    planner: str
    provider_profile_id: str | None = None
    model: str | None = None
    plan: ScriptPlan
    shots: list[ShotProposal]
    warnings: list[str] = Field(default_factory=list)
    applied_shot_ids: list[str] = Field(default_factory=list)


class AgentShotRequest(APIModel):
    brief: str = Field(min_length=1, max_length=4000)
    max_shots: int = Field(default=1, ge=1, le=24)
    apply: bool = False


class ShotProposalList(APIModel):
    shots: list[ShotProposal] = Field(default_factory=list, max_length=24)


# ---------------------------------------------------------------------------
# ComfyUI profiles
# ---------------------------------------------------------------------------


class ComfyConnectionConfig(APIModel):
    base_url: str = Field(default="http://127.0.0.1:8188", max_length=500)
    timeout_s: float = Field(default=10, ge=1, le=120)

    @field_validator("base_url")
    @classmethod
    def loopback_only(cls, value: str) -> str:
        if not value.startswith(("http://", "https://")):
            raise ValueError("base_url must start with http:// or https://")
        return value


class WorkflowProfileCreate(APIModel):
    name: str = Field(min_length=1, max_length=120)
    mode: GenerationMode
    graph: dict[str, Any]
    parameter_map: dict[str, str] = Field(default_factory=dict)
    media_transfers: dict[str, Any] | None = None
    required_nodes: list[str] = Field(default_factory=list, max_length=100)
    checkpoint_id: str = Field(default="MiniMax-H3-Base", max_length=160)
    project_id: str | None = None


class WorkflowProfilePublic(APIModel):
    id: str
    project_id: str | None
    name: str
    mode: GenerationMode
    graph_sha256: str
    parameter_map: dict[str, str]
    media_transfers: dict[str, Any] | None = None
    required_nodes: list[str]
    checkpoint_id: str
    model_identity: dict[str, Any] = Field(default_factory=dict)
    validated_against: dict[str, Any] | None
    created_at: datetime


class ComfyTestResult(APIModel):
    ok: bool
    message: str = ""
    system_stats: dict[str, Any] = Field(default_factory=dict)
    node_count: int = 0
    missing_nodes: list[str] = Field(default_factory=list)
    h3_nodes: list[str] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# Quality control
# ---------------------------------------------------------------------------


class TechnicalCheck(APIModel):
    name: str
    status: Literal["pass", "fail", "unavailable", "skipped"]
    detail: str = ""
    measured: float | int | str | None = None


class TechnicalQCResult(APIModel):
    result: Literal["passed", "failed", "needs_review"]
    checks: list[TechnicalCheck]
    tool: str
    artifact_sha256: str
    byte_size: int
    duration_s: float | None = None
    width: int | None = None
    height: int | None = None
    fps: float | None = None
    has_audio: bool | None = None


class SemanticFinding(APIModel):
    code: str = Field(min_length=1, max_length=80)
    category: str = Field(min_length=1, max_length=40)
    severity: Literal["hard", "soft", "uncertain"]
    confidence: float = Field(ge=0, le=1)
    message: str = Field(default="", max_length=1000)
    evidence_time_s: float | None = Field(default=None, ge=0)
    subject: str | None = Field(default=None, max_length=80)


class StyleInputCheck(APIModel):
    field: Literal["photography_language", "shot_blocking", "color_palette", "lighting_tone",
                   "lens_texture", "editing_rhythm", "sound_performance"]
    status: Literal["consistent", "conflict", "uncertain"]
    detail: str = Field(min_length=1, max_length=240)


class SemanticReviewResult(APIModel):
    reviewer: str
    model: str | None = None
    is_mock: bool
    input_sha256: str
    # Optional only for immutable historical records and injected fixtures.
    # Current VLM wire responses must explicitly declare this field.
    verdict: Literal["passed", "failed", "needs_review"] | None = None
    findings: list[SemanticFinding] = Field(default_factory=list)
    # Historical records remain readable. New media-review responses must cover
    # every frozen input criterion explicitly; the wire adapter enforces this.
    style_input_checks: list[StyleInputCheck] | None = None
    overall_confidence: float = Field(ge=0, le=1)
    raw_summary: str = Field(default="", max_length=2000)


class FusedQCResult(APIModel):
    result: Literal["passed", "failed", "needs_review"]
    technical: TechnicalQCResult | None = None
    semantic: list[SemanticReviewResult] = Field(default_factory=list)
    hard_failures: list[HardFailure] = Field(default_factory=list)
    soft_scores: dict[str, float] = Field(default_factory=dict)
    review_confidence: float = Field(ge=0, le=1)
    human_review_required: bool = False
    is_mock: bool
    coverage: dict[str, str] = Field(default_factory=dict)


class RepairSuggestionRequest(APIModel):
    job_id: str
    qc_report_id: str | None = None
