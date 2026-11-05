"""Shallow, versioned contracts for the director AI material-preparation flow.

Two model-facing contracts live here and nowhere else:

``ScriptPreparation``
    What the pre-production scriptwriter returns for ONE numbered scene.  It is
    deliberately shallow and separate from the formal ``ScriptBreakdown``: a
    preparation result carries no shots, no durations, no asset UUIDs and no H3
    prompt text, and it is allowed before the Style Bible is frozen.

``MaterialImageBrief``
    What the producer returns for ONE confirmed requirement.  The identity
    (project, requirement, family, visual direction) is fixed in the server
    envelope and is never trusted back from the model.

Both reject unknown fields.  Exceeding a limit is a refusal with an actionable
reason, never a silent truncation: dropping a locked fact to fit a window would
change what the user confirmed.
"""

from __future__ import annotations

import re
from typing import Annotated, Any, Literal

from pydantic import Field, StringConstraints, ValidationInfo, field_validator

from shine.schemas import APIModel

SCHEMA_VERSION = "1.0"

MaterialFamily = Literal["character", "scene", "prop"]
"""The only three families the first version generates.

Costume and material stay attributes of a character or prop description; they
are not a separate generation category, and voice is out of scope because H3
speaks a dialogue line natively.
"""

_SpanId = Annotated[str, StringConstraints(min_length=1, max_length=80)]
_LockedFact = Annotated[str, StringConstraints(min_length=1, max_length=300)]
_ShortQuestion = Annotated[str, StringConstraints(min_length=1, max_length=200)]
_KeepItem = Annotated[str, StringConstraints(min_length=1, max_length=160)]


class ScriptPreparationRequirement(APIModel):
    """One visual-material need found in one scene."""

    family: MaterialFamily
    name: str = Field(min_length=1, max_length=100)
    description: str = Field(default="", max_length=600)
    source_span_ids: list[_SpanId] = Field(default_factory=list, max_length=8)
    questions: list[_ShortQuestion] = Field(default_factory=list, max_length=4)


_CJK = re.compile(r"[\u3400-\u9fff]")


class ScriptPreparation(APIModel):
    """The scriptwriter's per-scene answer.  Facts only, no production intent."""

    schema_version: Literal[SCHEMA_VERSION] = SCHEMA_VERSION
    scene_key: str = Field(
        min_length=1,
        max_length=80,
        description="Must equal the scene key the server sent. The model never invents one.",
    )
    summary: str = Field(default="", max_length=800)
    locked_facts: list[_LockedFact] = Field(default_factory=list, max_length=24)
    requirements: list[ScriptPreparationRequirement] = Field(default_factory=list, max_length=32)
    questions: list[_ShortQuestion] = Field(default_factory=list, max_length=8)


class MaterialImageBrief(APIModel):
    """The producer's appearance description for ONE confirmed requirement.

    No node ids, no file paths, no model choice, no sampler parameters, no seed
    and no H3 labels: the server owns the workflow, the parameters, the
    deterministic composition template and the GPU.
    """

    schema_version: Literal[SCHEMA_VERSION] = SCHEMA_VERSION
    appearance: str = Field(min_length=1, max_length=1200)
    must_keep: list[_KeepItem] = Field(default_factory=list, max_length=12)
    avoid: list[_KeepItem] = Field(default_factory=list, max_length=12)
    questions: list[_ShortQuestion] = Field(default_factory=list, max_length=6)

    @field_validator("appearance", "must_keep", "avoid")
    @classmethod
    def _english_prose_only(cls, value: Any, info: ValidationInfo) -> Any:
        """The prompt prose is English; a Chinese fragment is a contract failure.

        The prompt composer refuses a mixed-script prompt, and the producer is
        the only one who can fix its own answer.  Validating here means the
        answer is corrected once, by the model that wrote it, instead of
        reaching the renderer and being refused there.  The requirement text
        itself stays in the script language.
        """
        items = value if isinstance(value, list) else [value]
        for item in items:
            text = getattr(item, "text", item)
            if isinstance(text, str) and _CJK.search(text):
                raise ValueError(
                    f"{info.field_name} must be written in English visual prose; "
                    "do not repeat the Chinese requirement name inside it"
                )
        return value


class MaterialVisualDirection(APIModel):
    """The material look the user confirms BEFORE the full Style Bible freeze.

    Kept separate on purpose: confirming a neutral-background character look is
    not freezing the seven cinematic style fields, and a later Style Bible
    version must not silently rewrite this snapshot.
    """

    era: str = Field(default="", max_length=200)
    palette: str = Field(default="", max_length=300)
    lighting: str = Field(default="", max_length=300)
    texture: str = Field(default="", max_length=300)
    notes: str = Field(default="", max_length=600)


RequirementStrategy = Literal["reuse_master", "upload", "generate"]
"""What the user decided for one requirement.

``upload`` means a file is on its way or already pending approval, so the item
must NOT be treated as missing and regenerated behind the user's back.
"""


class RequirementDecision(APIModel):
    requirement_id: str = Field(min_length=1, max_length=36)
    strategy: RequirementStrategy
    entity_id: str = Field(min_length=1, max_length=200)
    asset_id: str | None = Field(default=None, max_length=36)
    brief_note: str = Field(default="", max_length=600)


class MaterialConfirmation(APIModel):
    """The versioned human confirmation a generation batch is allowed to consume.

    A batch references ``confirmation_id`` + ``confirmation_hash``; changing the
    requirement set or the visual direction creates a new confirmation and
    invalidates every older grant, exactly like a new plan revision does.
    """

    plan_revision_id: str = Field(min_length=1, max_length=36)
    expected_snapshot: str = Field(min_length=64, max_length=64)
    decisions: list[RequirementDecision] = Field(min_length=1, max_length=200)
    visual_direction: MaterialVisualDirection = Field(default_factory=MaterialVisualDirection)
    notes: str = Field(default="", max_length=600)
