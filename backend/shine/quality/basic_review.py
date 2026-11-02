"""Evidence-based basic self-review, separate from production approval.

This evaluator does not run reviewers or authorize assets. Callers must supply
results for the same artifact and preserve the underlying evidence. It is not
yet connected to the worker or the production final gate.
"""

import hashlib
import re
from dataclasses import dataclass
from typing import Literal

from pydantic import ConfigDict, Field

from shine.schemas import APIModel, SemanticFinding, ShotSpec, TechnicalQCResult

Status = Literal["passed", "failed", "needs_review", "not_applicable"]


class CharacterObservation(APIModel):
    """One visual observation; never a claim about dialogue or style."""

    model_config = ConfigDict(extra="forbid", strict=True)
    character: str = Field(min_length=1, max_length=80)
    status: Literal["passed", "failed", "needs_review"]
    observation: str = Field(min_length=1, max_length=240)
    reference_compared: bool


class CharacterReviewResponse(APIModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    characters: list[CharacterObservation] = Field(max_length=20)


def validate_character_response(
    response: CharacterReviewResponse, *, expected_characters: list[str], reference_characters: set[str]
) -> None:
    """Require explicit, unambiguous coverage and honest reference claims.

    Matching a text name alone cannot establish identity against a reference.
    A bound reference requires comparison before that character can pass.
    """
    names = [item.character for item in response.characters]
    if (len(names) != len(expected_characters) or len(set(names)) != len(names)
            or set(names) != set(expected_characters)):
        raise ValueError("CHARACTER_COVERAGE_INVALID")
    for item in response.characters:
        has_reference = item.character in reference_characters
        if (item.status == "passed"
                and item.observation.strip().casefold() in {"passed", "pass", "ok", "yes", "通过", "合格"}):
            raise ValueError("CHARACTER_OBSERVATION_MISSING")
        # Constrained decoding can force-close a string at the schema's exact
        # maxLength, yielding valid JSON while cutting a sentence mid-token.
        # Such an observation cannot support a pass, even if the model says
        # it compared a reference. Keep failure/uncertainty conservative.
        if item.status == "passed" and len(item.observation) == 240:
            raise ValueError("CHARACTER_OBSERVATION_POSSIBLY_TRUNCATED")
        if item.reference_compared and not has_reference:
            raise ValueError("CHARACTER_REFERENCE_UNBOUND")
        if has_reference and item.status == "passed" and not item.reference_compared:
            raise ValueError("CHARACTER_REFERENCE_NOT_COMPARED")
        if len(expected_characters) > 1 and not has_reference and item.status == "passed":
            raise ValueError("CHARACTER_IDENTITY_UNBOUND")


@dataclass(frozen=True)
class CharacterEvidence:
    character: str
    status: Literal["passed", "failed", "needs_review"]
    artifact_sha256: str
    reviewer: str
    is_mock: bool


def character_evidence_from_response(
    response: CharacterReviewResponse,
    *,
    expected_characters: list[str],
    reference_characters: set[str],
    artifact_sha256: str,
    reviewer: str,
    is_mock: bool,
) -> tuple[CharacterEvidence, ...]:
    """Convert a validated response; caller is responsible for execution provenance."""
    validate_character_response(
        response, expected_characters=expected_characters, reference_characters=reference_characters
    )
    return tuple(
        CharacterEvidence(item.character, item.status, artifact_sha256, reviewer, is_mock)
        for item in response.characters
    )


@dataclass(frozen=True)
class DialogueEvidence:
    artifact_sha256: str
    model: str
    is_mock: bool
    matched_script: bool
    findings: tuple[SemanticFinding, ...] = ()
    transcript_text: str | None = None
    transcript_sha256: str | None = None
    transcript_truncated: bool = False


def dialogue_evidence_from_transcript(shot: ShotSpec, transcript, artifact_sha256: str) -> DialogueEvidence:
    """Convert a completed ASR result; empty or unmatched script never counts as a match.

    The caller must bind the ASR execution to the artifact and check its
    provenance; this helper does not turn an untrusted transcript into proof.
    """
    from shine.quality.semantic import _normalize, dialogue_findings

    expected = [_normalize(line.text) for line in shot.dialogue]
    spoken = _normalize(transcript.text)
    matched = bool(expected and spoken)
    cursor = 0
    for line in expected:
        found = spoken.find(line, cursor) if line else -1
        if found < 0:
            matched = False
            break
        cursor = found + len(line)
    return DialogueEvidence(
        artifact_sha256=artifact_sha256,
        model=transcript.model or "",
        is_mock=transcript.is_mock,
        matched_script=matched,
        findings=tuple(dialogue_findings(shot, transcript)),
        transcript_text=transcript.text[:20000],
        transcript_sha256=hashlib.sha256(transcript.text.encode("utf-8")).hexdigest(),
        transcript_truncated=len(transcript.text) > 20000,
    )


@dataclass(frozen=True)
class BasicReview:
    resolution: Status
    characters: Status
    dialogue: Status
    result: Literal["passed", "failed", "needs_review"]


def evaluate_basic_review(
    *,
    shot: ShotSpec,
    technical: TechnicalQCResult | None,
    expected_resolution: tuple[int, int],
    characters: tuple[CharacterEvidence, ...] = (),
    dialogue: DialogueEvidence | None = None,
) -> BasicReview:
    """Evaluate only resolution, declared characters, and scripted dialogue.

    The expected dimensions must be supplied by the caller from the render
    request; video metadata alone does not establish expected resolution.
    Technical integrity checks are prerequisites to interpreting the video.
    """
    if (len(expected_resolution) != 2
            or any(type(value) is not int or value <= 0 for value in expected_resolution)):
        raise ValueError("Expected resolution must contain two positive integers")
    resolution: Status = "needs_review"
    digest = technical.artifact_sha256 if technical else ""
    if not re.fullmatch(r"[0-9a-f]{64}", digest):
        digest = ""
    required_checks = {"file_exists", "file_nonempty", "container", "stream_metadata", "resolution", "decode"}
    if technical:
        checks = {name: [c.status for c in technical.checks if c.name == name] for name in required_checks}
        if any("fail" in statuses for statuses in checks.values()):
            resolution = "failed"
        elif (digest and technical.byte_size > 0 and technical.tool not in {"", "none"}
              and all(statuses == ["pass"] for statuses in checks.values())):
            if (type(technical.width) is int and technical.width > 0
                    and type(technical.height) is int and technical.height > 0):
                resolution = "passed" if (technical.width, technical.height) == expected_resolution else "failed"

    character_status: Status = "not_applicable" if not shot.characters else "needs_review"
    if shot.characters:
        evidence_by_name = {item.character: item for item in characters}
        valid = (digest and len(characters) == len(shot.characters)
                 and len(evidence_by_name) == len(characters)
                 and set(evidence_by_name) == set(shot.characters)
                 and all(item.artifact_sha256 == digest and item.reviewer and item.is_mock is False
                         for item in characters))
        if valid:
            if any(item.status == "failed" for item in characters):
                character_status = "failed"
            elif all(item.status == "passed" for item in characters):
                character_status = "passed"

    dialogue_status: Status = "not_applicable" if not shot.dialogue else "needs_review"
    if (shot.dialogue and dialogue and digest and dialogue.artifact_sha256 == digest
            and dialogue.model and dialogue.is_mock is False):
        if any(f.severity == "hard" for f in dialogue.findings):
            dialogue_status = "failed"
        elif dialogue.findings or dialogue.matched_script is not True:
            dialogue_status = "needs_review"
        else:
            dialogue_status = "passed"

    statuses = (resolution, character_status, dialogue_status)
    result: Literal["passed", "failed", "needs_review"] = (
        "failed" if "failed" in statuses else "needs_review" if "needs_review" in statuses else "passed"
    )
    return BasicReview(resolution, character_status, dialogue_status, result)


def evaluate_basic_gates(
    *, shot: ShotSpec, technical: TechnicalQCResult | None, expected_resolution: tuple[int, int],
    artifact_sha256: str, characters: tuple[CharacterEvidence, ...] = (),
    dialogue: DialogueEvidence | None = None, character_response: dict | None = None,
    character_error: str | None = None, audio_error: str | None = None, offline_simulation: bool = False,
):
    """Record the three self-review decisions with their scope and evidence."""
    from shine.quality.gates import GateOutcome, _as_advisory, _final_gate
    from shine.schemas import FusedQCResult, HardFailure

    # Do not attach valid evidence for some other file to this attempt.
    if technical and technical.artifact_sha256 != artifact_sha256:
        technical = None
    basic = evaluate_basic_review(shot=shot, technical=technical, expected_resolution=expected_resolution,
                                  characters=characters, dialogue=dialogue)
    character_mock = any(item.is_mock is not False for item in characters)
    dialogue_mock = bool(dialogue and dialogue.is_mock is not False)

    def gate(name, status, required, *, is_mock=False, model=None, evidence=None):
        return GateOutcome(
            gate_name=name, status=status, required=required, is_mock=is_mock,
            coverage=("mock_covered" if is_mock else "covered" if status in {"passed", "failed"} else "not_covered"),
            model=model,
            error_code=f"BASIC_{name.upper()}_{status.upper()}" if status in {"failed", "needs_review"} else None,
            evidence={"scope": "basic-self-review-v1", **(evidence or {})},
        )

    resolution_status = ("needs_review" if offline_simulation and basic.resolution == "passed" else basic.resolution)
    technical_gate = gate("technical", resolution_status, True, is_mock=offline_simulation, evidence={
        "expected_resolution": list(expected_resolution),
        "technical": technical.model_dump(mode="json") if technical else None,
        "offline_simulation": offline_simulation,
    })
    character_gate = gate("semantic", basic.characters, bool(shot.characters), is_mock=character_mock,
                          model=characters[0].reviewer if characters else None,
                          evidence={"characters": character_response, "expected_characters": shot.characters,
                                    "error": character_error})
    audio_gate = gate("audio", basic.dialogue, bool(shot.dialogue), is_mock=dialogue_mock,
                      model=dialogue.model if dialogue else None,
                      evidence={"script_matched": dialogue.matched_script if dialogue else None,
                                "finding_codes": [f.code for f in dialogue.findings] if dialogue else [],
                                "artifact_sha256": dialogue.artifact_sha256 if dialogue else None,
                                "transcript": dialogue.transcript_text if dialogue else None,
                                "transcript_sha256": dialogue.transcript_sha256 if dialogue else None,
                                "transcript_truncated": dialogue.transcript_truncated if dialogue else False,
                                "error": audio_error})
    # Small local VLM/ASR checks remain visible as suggestions for later human
    # review. They do not gate creation of the shot or the assembled episode.
    character_gate = _as_advisory(character_gate)
    audio_gate = _as_advisory(audio_gate)
    style_gate = gate("style", "not_applicable", False, evidence={"reason": "outside basic self-review"})
    final = _final_gate([technical_gate, character_gate, audio_gate, style_gate])
    final.evidence["scope"] = "basic-self-review-v1"
    gates = [technical_gate, character_gate, audio_gate, style_gate, final]
    hard_failures = [HardFailure(code=f"BASIC_{name.upper()}_FAILED", evidence=f"Basic {name} check failed")
                     for name, status in (("resolution", basic.resolution), ("character", basic.characters),
                                          ("dialogue", basic.dialogue)) if status == "failed"]
    coverage = {"technical": technical_gate.coverage, "semantic": character_gate.coverage,
                "asr": audio_gate.coverage, "style": style_gate.coverage, "final": final.coverage,
                "scope": "basic-self-review-v1"}
    fused = FusedQCResult(result=final.status, technical=technical, hard_failures=hard_failures,
                          review_confidence=0.0, human_review_required=final.status != "passed",
                          is_mock=any(g.is_mock for g in gates), coverage=coverage)
    return gates, fused
