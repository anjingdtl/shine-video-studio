from __future__ import annotations

import base64
import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol

import httpx

from shine.llm.base import ProviderConfig
from shine.schemas import (
    FusedQCResult,
    HardFailure,
    SemanticFinding,
    SemanticReviewResult,
    ShotSpec,
    StyleBible,
)


@dataclass(frozen=True)
class ReviewContext:
    job_id: str
    shot_spec: ShotSpec
    style: StyleBible
    artifact_path: Path | None
    artifact_sha256: str
    frame_paths: tuple[Path, ...] = ()
    transcript: str | None = None
    remote_consent: bool = False


@dataclass(frozen=True)
class TranscriptSegment:
    start_s: float
    end_s: float
    text: str


@dataclass
class Transcript:
    text: str
    segments: list[TranscriptSegment] = field(default_factory=list)
    is_mock: bool = True
    model: str | None = None


class SemanticReviewer(Protocol):
    name: str
    is_mock: bool

    def review(self, context: ReviewContext) -> SemanticReviewResult: ...


class ASRReviewer(Protocol):
    name: str
    is_mock: bool

    def transcribe(self, path: Path) -> Transcript: ...


class MockSemanticReviewer:
    """Returns operator-supplied findings. It never inspects pixels."""

    is_mock = True

    def __init__(self, findings: list[SemanticFinding] | None = None, name: str = "mock-vlm"):
        self.name = name
        self._findings = findings or []

    def review(self, context: ReviewContext) -> SemanticReviewResult:
        confidence = min((finding.confidence for finding in self._findings), default=1.0)
        return SemanticReviewResult(
            reviewer=self.name,
            model="mock",
            is_mock=True,
            input_sha256=context.artifact_sha256,
            findings=list(self._findings),
            overall_confidence=confidence,
            raw_summary="Mock semantic review; no pixels were analysed.",
        )


class MockASRReviewer:
    is_mock = True
    name = "mock-asr"

    def __init__(self, text: str = "", segments: list[TranscriptSegment] | None = None):
        self._text = text
        self._segments = segments or []

    def transcribe(self, path: Path) -> Transcript:
        return Transcript(text=self._text, segments=list(self._segments), is_mock=True, model="mock")


class OpenAICompatibleVisionReviewer:
    """Sends derived frames only after explicit project consent. Never sends master assets."""

    is_mock = False
    name = "openai-compatible-vlm"

    def __init__(self, config: ProviderConfig):
        self.config = config

    def _endpoint(self) -> str:
        base = self.config.base_url.rstrip("/")
        if base.endswith("/chat/completions"):
            return base
        if base.endswith("/v1"):
            return f"{base}/chat/completions"
        return f"{base}/v1/chat/completions"

    def review(self, context: ReviewContext) -> SemanticReviewResult:
        if not context.remote_consent:
            raise PermissionError("Remote semantic review requires explicit user consent in the project settings")
        if not context.frame_paths:
            raise ValueError("Remote semantic review requires derived evidence frames")
        schema = SemanticReviewResult.model_json_schema()
        content: list[dict[str, Any]] = [
            {
                "type": "text",
                "text": (
                    "Review the supplied derived evidence frames for identity, costume, scene, style, and "
                    "continuity. Return JSON with reviewer, is_mock=false, input_sha256, findings, "
                    "overall_confidence, raw_summary. "
                    f"ShotSpec: {context.shot_spec.model_dump_json()}\n"
                    f"Style: {context.style.model_dump_json()}\n"
                    f"JSON Schema: {json.dumps(schema, ensure_ascii=False)}"
                ),
            }
        ]
        for frame in context.frame_paths[:6]:
            encoded = base64.b64encode(frame.read_bytes()).decode("ascii")
            content.append({"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{encoded}"}})
        headers = {"Content-Type": "application/json"}
        if self.config.api_key:
            headers["Authorization"] = f"Bearer {self.config.api_key}"
        payload = {
            "model": self.config.model,
            "messages": [{"role": "user", "content": content}],
            "temperature": 0.0,
            "response_format": {"type": "json_object"},
        }
        with httpx.Client(timeout=self.config.timeout_s) as client:
            response = client.post(self._endpoint(), headers=headers, json=payload)
        if response.status_code >= 400:
            raise RuntimeError(f"VLM request failed with status {response.status_code}")
        body = response.json()
        text = body["choices"][0]["message"]["content"]
        parsed = json.loads(text)
        parsed["is_mock"] = False
        parsed["input_sha256"] = context.artifact_sha256
        return SemanticReviewResult.model_validate(parsed)


class OpenAICompatibleASRReviewer:
    is_mock = False
    name = "openai-compatible-asr"

    def __init__(self, config: ProviderConfig):
        self.config = config

    def _endpoint(self) -> str:
        base = self.config.base_url.rstrip("/")
        if base.endswith("/v1"):
            return f"{base}/audio/transcriptions"
        return f"{base}/v1/audio/transcriptions"

    def transcribe(self, path: Path) -> Transcript:
        headers: dict[str, str] = {}
        if self.config.api_key:
            headers["Authorization"] = f"Bearer {self.config.api_key}"
        with httpx.Client(timeout=self.config.timeout_s) as client:
            with path.open("rb") as handle:
                response = client.post(
                    self._endpoint(),
                    headers=headers,
                    data={"model": self.config.model, "response_format": "verbose_json"},
                    files={"file": (path.name, handle, "application/octet-stream")},
                )
        if response.status_code >= 400:
            raise RuntimeError(f"ASR request failed with status {response.status_code}")
        body = response.json()
        segments = [
            TranscriptSegment(
                start_s=float(item.get("start", 0)),
                end_s=float(item.get("end", 0)),
                text=str(item.get("text", "")),
            )
            for item in body.get("segments") or []
        ]
        return Transcript(text=str(body.get("text", "")), segments=segments, is_mock=False, model=self.config.model)


def _normalize(text: str) -> str:
    return re.sub(r"[\s，。！？、,.!?\"'“”‘’：:；;]", "", text).lower()


def dialogue_findings(spec: ShotSpec, transcript: Transcript) -> list[SemanticFinding]:
    if not spec.dialogue:
        return []
    spoken = _normalize(transcript.text)
    findings: list[SemanticFinding] = []
    for line in spec.dialogue:
        expected = _normalize(line.text)
        if not expected:
            continue
        if expected in spoken:
            continue
        # ASR is probabilistic: a missing line is evidence, not proof.
        findings.append(
            SemanticFinding(
                code="WRONG_LINE",
                category="dialogue",
                severity="uncertain",
                confidence=0.6,
                message=f"The expected line for {line.speaker_id} was not found in the ASR transcript",
                evidence_time_s=line.start_s,
                subject=line.speaker_id,
            )
        )
    return findings


def fuse(
    *,
    technical=None,
    semantic_results: list[SemanticReviewResult] | None = None,
    transcript_findings: list[SemanticFinding] | None = None,
    coverage_extra: dict[str, str] | None = None,
) -> FusedQCResult:
    semantic_results = semantic_results or []
    transcript_findings = transcript_findings or []
    hard_failures: list[HardFailure] = []
    soft_scores: dict[str, float] = {}
    needs_review = False
    failed = False
    is_mock = any(result.is_mock for result in semantic_results)
    confidence_values: list[float] = []

    if technical is not None:
        if technical.result == "failed":
            failed = True
            for check in technical.checks:
                if check.status == "fail":
                    hard_failures.append(
                        HardFailure(code=f"TECHNICAL_{check.name.upper()}", evidence=check.detail or check.name)
                    )
        elif technical.result == "needs_review":
            needs_review = True

    all_findings = list(transcript_findings)
    for result in semantic_results:
        confidence_values.append(result.overall_confidence)
        all_findings.extend(result.findings)

    for finding in all_findings:
        if finding.severity == "hard":
            if finding.confidence >= 0.7:
                failed = True
                hard_failures.append(
                    HardFailure(
                        code=finding.code,
                        evidence=finding.message or finding.code,
                        evidence_time_s=finding.evidence_time_s,
                    )
                )
            else:
                needs_review = True
        elif finding.severity == "uncertain":
            needs_review = True
        else:
            soft_scores[finding.category] = max(
                soft_scores.get(finding.category, 0.0),
                round(1.0 - finding.confidence, 3),
            )
            if finding.confidence >= 0.8:
                needs_review = True

    if failed:
        result = "failed"
    elif needs_review:
        result = "needs_review"
    else:
        result = "passed"

    coverage = {
        "technical": "not_run" if technical is None else technical.result,
        "semantic": "not_run" if not semantic_results else ("mock" if is_mock else "real"),
        "asr": "not_run" if not transcript_findings else "checked",
    }
    coverage.update(coverage_extra or {})
    if technical is None:
        coverage["technical"] = "not_run"
    return FusedQCResult(
        result=result,
        technical=technical,
        semantic=semantic_results,
        hard_failures=hard_failures,
        soft_scores=soft_scores,
        review_confidence=min(confidence_values, default=1.0),
        human_review_required=result != "passed",
        is_mock=is_mock,
        coverage=coverage,
    )
