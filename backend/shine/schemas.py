from __future__ import annotations

from enum import StrEnum
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class APIModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class CreateProject(APIModel):
    title: str = Field(min_length=1, max_length=160)
    description: str = Field(default="", max_length=4000)


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


class ShotSpec(APIModel):
    schema_version: Literal["1.0"] = "1.0"
    story_beat: str = Field(min_length=1, max_length=2000)
    duration_target_s: float = Field(ge=4, le=15)
    shot_priority: Literal["A", "B", "C"] = "B"
    style_version_id: str
    camera: dict[str, str] = Field(default_factory=dict)
    characters: list[str] = Field(default_factory=list, max_length=20)
    required_asset_ids: list[str] = Field(default_factory=list, max_length=12)
    dialogue: list[DialogueLine] = Field(default_factory=list, max_length=30)
    sound: dict[str, str] = Field(default_factory=dict)
    continuity: dict[str, Any] = Field(default_factory=dict)
    locked_story_facts: list[str] = Field(default_factory=list, max_length=30)

    @model_validator(mode="after")
    def dialogue_fits_duration(self) -> ShotSpec:
        if any(line.end_s > self.duration_target_s for line in self.dialogue):
            raise ValueError("dialogue must fit inside the target shot duration")
        return self


class CreateShot(APIModel):
    name: str = Field(min_length=1, max_length=160)
    spec: ShotSpec


class ReferenceUse(StrEnum):
    SUBJECT = "subject"
    PICTURE = "picture"
    VIDEO = "video"
    AUDIO = "audio"


class AssetReference(APIModel):
    asset_id: str
    use_as: ReferenceUse


class MockScenario(StrEnum):
    SUCCESS = "success"
    DELAY = "delay"
    FAIL = "fail"
    UNCERTAIN = "uncertain"
    DUPLICATE = "duplicate"


class CreateJob(APIModel):
    shot_id: str
    references: list[AssetReference] = Field(default_factory=list, max_length=12)
    depends_on: list[str] = Field(default_factory=list, max_length=20)
    mock_scenario: MockScenario = MockScenario.SUCCESS


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
    WAIT_FOR_HUMAN = "wait_for_human"


class RepairPlan(APIModel):
    action: RepairAction
    root_cause: str = Field(min_length=1, max_length=500)
    changes: list[str] = Field(default_factory=list, max_length=20)
    max_additional_attempts: int = Field(default=2, ge=0, le=2)
    human_review_required: bool = False


class ReferenceManifestItem(APIModel):
    label: str
    asset_id: str
    sha256: str
    storage_key: str
    content_type: str
    use_as: ReferenceUse


class RenderRequest(APIModel):
    idempotency_key: str
    shot_id: str
    shot_revision: int
    style_version_id: str
    style_hash: str
    model_checkpoint_id: Literal["MiniMax-H3-Base"] = "MiniMax-H3-Base"
    provider: Literal["mock"] = "mock"
    reference_manifest: list[ReferenceManifestItem] = Field(default_factory=list, max_length=12)
    shot_spec: ShotSpec
    style_snapshot: StyleBible
    compiled_prompt: str | None = None
    prompt_skill_hash: str | None = None
    workflow_profile_hash: str | None = None
    seed: int | None = Field(default=None, ge=0)
    expected_duration_s: float = Field(ge=4, le=15)

    @field_validator("compiled_prompt")
    @classmethod
    def reject_unreviewed_prompt(cls, value: str | None) -> str | None:
        if value and len(value) > 20000:
            raise ValueError("compiled prompt exceeds the maximum length")
        return value
