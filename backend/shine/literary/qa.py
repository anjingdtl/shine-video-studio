"""Literary QA: prove what can be proven locally, ask only about real doubts.

Two levels, deliberately separated:

``L0`` - deterministic checks on data the server already owns: is the text
present, does it still match its source range, are the characters known, does a
secret stay secret, does every shot carry a line of dialogue it claims.  L0 never
calls a model.

``L1`` - a *targeted* semantic question, sent only when L0 raised a named doubt.
It gets the doubt, the evidence and the smallest useful excerpt, returns a short
report, and may never rewrite approved story content.

A QA failure never triggers regeneration by itself.  A QA format error is a
``QA_PENDING`` note, not a failed scene.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any, Literal

from pydantic import BaseModel, Field

from shine.literary import triage
from shine.literary.requests import estimate_tokens

L0 = "L0"
L1 = "L1"

REPORT_KIND = "LiteraryQaReport"
"""The artifact kind a QA verdict is stored as: versioned, never a task state."""

SECRET_MARKERS = ("其实是", "真凶是", "卧底是", "幕后", "真实身份是")


class SemanticFinding(BaseModel):
    """One targeted opinion.  It can never be a hard blocker by construction."""

    code: str = Field(min_length=1, max_length=48)
    severity: Literal["WARNING", "LOCAL_REVIEW"] = "WARNING"
    message: str = Field(min_length=1, max_length=300)
    evidence: str | None = Field(default=None, max_length=400)


class SemanticReview(BaseModel):
    findings: list[SemanticFinding] = Field(default_factory=list, max_length=8)


SEMANTIC_SYSTEM_PROMPT = (
    "你是文学终校。只回答被点名的疑点：依据给出的正文与已确认事实，判断该疑点是否成立，"
    "并用一条 findings 说明（code 用英文大写短语，message 用中文一句话）。"
    "不要重写正文，不要提出新剧情，不要评价未被点名的内容；没有疑点就返回空数组。"
    "severity 只能为 WARNING 或 LOCAL_REVIEW。只输出 JSON。"
)


@dataclass
class QaCheck:
    code: str
    severity: str
    message_zh: str
    evidence: dict[str, Any] = field(default_factory=dict)
    unit_id: str = ""

    def as_dict(self) -> dict[str, Any]:
        return {"code": self.code, "severity": self.severity, "message": self.message_zh,
                "evidence": dict(self.evidence), "unit_id": self.unit_id}


@dataclass
class QaReport:
    level: str
    checks: list[QaCheck] = field(default_factory=list)
    needs_semantic_review: bool = False

    @property
    def state(self) -> str:
        if any(check.severity == "HARD_BLOCK" for check in self.checks):
            return triage.HARD_BLOCK
        if any(check.severity == "LOCAL_REVIEW" for check in self.checks):
            return triage.LOCAL_REVIEW
        if any(check.severity == "WARNING" for check in self.checks):
            return triage.WARNING
        return triage.INFO

    def as_dict(self) -> dict[str, Any]:
        return {"level": self.level, "state": self.state,
                "needs_semantic_review": self.needs_semantic_review,
                "checks": [check.as_dict() for check in self.checks]}


def check_scene_script(
    payload: dict[str, Any],
    *,
    unit_id: str,
    section: dict[str, Any],
    source_text: str,
    source_length: int | None = None,
    known_characters: tuple[str, ...] = (),
    secret_statements: tuple[str, ...] = (),
) -> QaReport:
    """L0 checks for one scene script.  No model, no cost, no guessing."""
    report = QaReport(level=L0)
    text = str(payload.get("text") or "")

    if not text.strip():
        report.checks.append(QaCheck(
            "SCENE_TEXT_MISSING", triage.HARD_BLOCK,
            "这一场没有正文，不能确认。", {"section_id": section.get("section_id")}, unit_id))
        return report

    start, end = section.get("start_offset"), section.get("end_offset")
    # The range is measured against the WHOLE volume document, never against the
    # excerpt: an excerpt is exactly ``end - start`` long, so comparing the two
    # would refuse every scene whose range does not start at offset zero.
    length = source_length if isinstance(source_length, int) else len(source_text or "")
    if isinstance(start, int) and isinstance(end, int) and length:
        if not 0 <= start < end <= length:
            report.checks.append(QaCheck(
                "SCENE_SOURCE_RANGE_INVALID", triage.HARD_BLOCK,
                "这一场引用的原文范围无效，不能确认。",
                {"start": start, "end": end, "document_length": length}, unit_id))

    if known_characters:
        introduced = [str(name) for name in (payload.get("characters") or [])]
        unknown = [name for name in introduced
                   if name not in known_characters and name not in text]
        if unknown:
            report.checks.append(QaCheck(
                "SCENE_CHARACTER_UNKNOWN", triage.LOCAL_REVIEW,
                "这一场出现了蓝图里没有的人物，请确认是否为新角色。",
                {"characters": unknown[:8]}, unit_id))

    leaks = [marker for marker in SECRET_MARKERS
             if marker in text and any(marker in secret for secret in secret_statements)]
    if leaks:
        report.checks.append(QaCheck(
            "SCENE_SECRET_LEAK_RISK", triage.LOCAL_REVIEW,
            "这一场可能提前说出了尚未揭晓的秘密，请复核。", {"markers": leaks}, unit_id))

    if not str(payload.get("continuity_out") or "").strip():
        report.checks.append(QaCheck(
            "SCENE_CONTINUITY_OUT_MISSING", triage.WARNING,
            "这一场没有写下收尾状态，下一场会缺少接续信息。",
            {"section_id": section.get("section_id")}, unit_id))

    report.needs_semantic_review = any(
        check.severity in {triage.LOCAL_REVIEW, triage.HARD_BLOCK} for check in report.checks)
    return report


def check_shot_dialogue_coverage(script_text: str, shots: list[dict[str, Any]]) -> QaReport:
    """L0: every line of dialogue must be carried by some shot."""
    report = QaReport(level=L0)
    lines = [line.strip() for line in (script_text or "").splitlines()
             if line.strip() and "：" in line]
    carried = " ".join(str(shot.get("dialogue") or "") + str(shot.get("prompt") or "")
                       for shot in shots)
    missing = [line for line in lines if line.split("：", 1)[1][:8] not in carried]
    if missing:
        report.checks.append(QaCheck(
            "STORYBOARD_DIALOGUE_UNCOVERED", triage.LOCAL_REVIEW,
            "有台词没有被任何镜头承载，请补镜头或确认删改。",
            {"lines": [line[:60] for line in missing[:8]], "count": len(missing)}))
    return report


def check_beat_coverage(breakdown: dict[str, Any], plan: dict[str, Any]) -> QaReport:
    """L0: every beat of the breakdown is claimed, and no shot names a ghost beat.

    Both directions matter.  A beat nobody shoots is a hole in the film; a shot
    pointing at a beat that does not exist cannot be traced back to the script.
    Neither is inferred - both are read from the two stored artifacts.
    """
    report = QaReport(level=L0)
    beats = [str(beat.get("id")) for beat in (breakdown.get("beats") or [])
             if isinstance(beat, dict) and beat.get("id")]
    known = set(beats)
    shot_beat_ids: list[str] = []
    for shot in plan.get("shots") or []:
        if not isinstance(shot, dict):
            continue
        if shot.get("beat_id"):
            shot_beat_ids.append(str(shot["beat_id"]))
        for cut in shot.get("cuts") or []:
            if isinstance(cut, dict) and cut.get("beat_id"):
                shot_beat_ids.append(str(cut["beat_id"]))
    covered = set(shot_beat_ids)
    missing = [beat_id for beat_id in beats if beat_id not in covered]
    if missing:
        report.checks.append(QaCheck(
            "STORYBOARD_BEAT_UNCOVERED", triage.HARD_BLOCK,
            "有剧情节拍没有被任何镜头覆盖，分镜不能作为正式交付。",
            {"beat_ids": missing[:12], "beats_total": len(beats),
             "beats_covered": len(beats) - len(missing)}))
    ghosts = sorted({beat_id for beat_id in shot_beat_ids if beat_id not in known})
    if ghosts:
        report.checks.append(QaCheck(
            "STORYBOARD_BEAT_UNKNOWN", triage.LOCAL_REVIEW,
            "有镜头引用了剧本里不存在的节拍，请核对。",
            {"beat_ids": ghosts[:12]}))
    if not beats:
        report.checks.append(QaCheck(
            "STORYBOARD_BREAKDOWN_EMPTY", triage.LOCAL_REVIEW,
            "剧本拆解没有节拍，分镜无法核对覆盖。", {}))
    report.needs_semantic_review = any(
        check.severity in {triage.LOCAL_REVIEW, triage.HARD_BLOCK} for check in report.checks)
    return report


def semantic_question(report: QaReport) -> dict[str, Any] | None:
    """The smallest useful L1 request, or None when nothing needs a model."""
    doubts = [check for check in report.checks
              if check.severity in {triage.LOCAL_REVIEW, triage.HARD_BLOCK}]
    if not doubts:
        return None
    return {"doubts": [{"code": check.code, "evidence": check.evidence,
                        "question": check.message_zh} for check in doubts[:4]]}


def estimate_semantic_cost(question: dict[str, Any], excerpt: str) -> dict[str, Any]:
    payload = json.dumps({"question": question, "excerpt": excerpt}, ensure_ascii=False)
    return {"tokens_estimated": estimate_tokens(payload), "excerpt_chars": len(excerpt)}


def memory_guards(db, project_id: str) -> dict[str, Any]:
    """What the confirmed fact ledger lets L0 check without a model.

    A fact that has not been approved is not a guard: an unconfirmed AI guess
    must never turn a legitimate scene into a "leak".
    """
    from shine.literary import memory
    from shine.literary.models import StoryMemoryFact

    facts: list[StoryMemoryFact] = memory.authoritative_facts(db, project_id)
    characters = tuple(sorted({
        str(fact.subjects[0]) for fact in facts
        if fact.kind == "character" and fact.subjects
        and str(fact.subjects[0]).strip()}))
    secrets = tuple(
        fact.statement for fact in facts
        if (fact.visibility or {}).get("scope") == "production_only")
    return {"known_characters": characters, "secret_statements": secrets,
            "fact_ids": tuple(fact.id for fact in facts)}


def semantic_request(
    report: QaReport,
    *,
    text: str,
    section: dict[str, Any],
    guards: dict[str, Any] | None = None,
    excerpt_chars: int = 6_000,
) -> tuple[dict[str, Any], dict[str, Any]] | None:
    """The context and question for one L1 call, or None when nothing is doubted.

    The excerpt is the scene itself, cut at its head and tail - not the whole
    volume.  The reviewer is told which doubts exist and nothing else.
    """
    question = semantic_question(report)
    if question is None:
        return None
    excerpt = text if len(text) <= excerpt_chars else (
        text[: excerpt_chars * 2 // 3] + "\n……\n" + text[-excerpt_chars // 3:])
    context: dict[str, Any] = {
        "section": {"section_id": section.get("section_id"),
                    "title": section.get("title")},
        "doubts": question["doubts"],
        "excerpt": excerpt,
    }
    if guards:
        known = list(guards.get("known_characters") or ())
        secrets = list(guards.get("secret_statements") or ())
        if known:
            context["known_characters"] = known[:60]
        if secrets:
            # The reviewer is the one caller allowed to see withheld facts.
            context["withheld_facts"] = secrets[:8]
    return context, question


def merge_semantic_report(report: QaReport, outcome) -> QaReport:
    """Attach an L1 answer without letting it rewrite anything.

    A failed or unparsable semantic review is recorded as ``QA_PENDING``: the
    scene keeps its state, and nothing is regenerated because a reviewer was
    unavailable.
    """
    if outcome is None:
        return report
    if not outcome.ok:
        report.checks.append(QaCheck(
            "QA_PENDING", triage.WARNING,
            "语义复核未完成，已记录；不影响本场正文。",
            {"error_code": outcome.error_code,
             "external_outcome": outcome.details.get("external_outcome")}))
        return report
    parsed = outcome.parsed or {}
    findings = parsed.get("findings") if isinstance(parsed, dict) else None
    for item in findings or []:
        if not isinstance(item, dict):
            continue
        severity = item.get("severity")
        severity = severity if severity in {triage.WARNING, triage.LOCAL_REVIEW} else triage.WARNING
        report.checks.append(QaCheck(
            str(item.get("code") or "QA_SEMANTIC_NOTE")[:48], severity,
            str(item.get("message") or "语义复核提示")[:200],
            {"evidence": item.get("evidence")} if item.get("evidence") else {}))
    report.level = f"{L0}+{L1}"
    return report
