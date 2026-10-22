"""Typed reviewer port; evidence and confidence never become human approval."""

from typing import Literal, Protocol

from pydantic import BaseModel, ConfigDict, Field


class CandidateFinding(BaseModel):
    model_config = ConfigDict(extra="forbid")
    code: str
    result: Literal["PASS", "FAIL", "UNCERTAIN"]
    confidence: float = Field(ge=0, le=1)
    evidence: str = Field(min_length=1)


class CandidateAssessment(BaseModel):
    model_config = ConfigDict(extra="forbid")
    model: str
    is_mock: bool
    findings: list[CandidateFinding] = Field(min_length=1)


class CandidateReviewer(Protocol):
    is_mock: bool

    def review(self, content: bytes, metadata: dict) -> dict: ...


class FakeCandidateReviewer:
    is_mock = True

    def __init__(self, result="PASS", confidence=0.99):
        self.result, self.confidence = result, confidence

    def review(self, content, metadata):
        return {
            "model": "fake-candidate-review-v1",
            "is_mock": True,
            "findings": [
                {
                    "code": "SYNTHETIC_FIXTURE_ONLY",
                    "result": self.result,
                    "confidence": self.confidence,
                    "evidence": "Fake reviewer does not inspect real identity or aesthetics",
                }
            ],
        }
