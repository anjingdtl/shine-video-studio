"""Shine-authored executable rules and source metadata, never official full text."""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Any, Literal

from pydantic import Field
from sqlalchemy.orm import Session

from shine.h3.compiler import (
    BASE_RULE_SHA256,
    BASE_SECTION_ORDER,
    REF_RULE_SHA256,
    REF_SECTION_ORDER,
    RULE_VERSION,
    SKILL_SHA256,
    H3PromptCompiler,
    PromptCompilationError,
)
from shine.models import ProjectRuleLock, RulePack, utcnow
from shine.schemas import APIModel, GenerationMode, RenderRequest

COMMIT = "d21241f0a4b3acbb34c97dae47fa417b7065e438"
REPO = "https://github.com/MiniMax-AI/MiniMax-H3"
PACK_ID = "shine-h3-rules/1.1.0"
SOURCE_DIR = "skills/h3-prompt-writing/"
SOURCES = [
    {"path": SOURCE_DIR + path, "sha256": digest, "blob_sha": blob, "url": f"{REPO}/blob/{COMMIT}/{SOURCE_DIR}{path}"}
    for path, digest, blob in (
        ("SKILL.md", SKILL_SHA256, "b6d9b2839384a588763a9c24315225dd8ce19d56"),
        ("references/base-en.txt", BASE_RULE_SHA256, "40cf586a634d677d6b7107b367cf0ec9621be728"),
        ("references/ref-en.txt", REF_RULE_SHA256, "7ae1b2d07d743fd2392258a96449be9e9e322d35"),
    )
]
SOURCES.append(
    {
        "path": "README.md",
        "sha256": "7964f3044d3c7efaa054bb823644ad98cf9ea43389f994c51e08977822f16bac",
        "blob_sha": "bdfd2473d24434599913d8d9edb9b96a6dc13603",
        "url": f"{REPO}/blob/{COMMIT}/README.md",
    }
)


def digest(value: Any) -> str:
    data = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(data.encode("utf-8")).hexdigest()


# Text below is authored for Shine. Locators identify evidence, not copied guide text.
RULES = [
    {
        "id": rid,
        "origin": origin,
        "section": section,
        "summary": summary,
        "source_path": (path if path == "README.md" else SOURCE_DIR + path)
        if origin == "OFFICIAL_H3"
        else "workflow_profiles"
        if origin == "LOCAL_WORKFLOW"
        else "AGENTS.md",
        "locator": locator,
    }
    for rid, origin, section, path, locator, summary in (
        (
            "H3_SECTIONS",
            "OFFICIAL_H3",
            "base",
            "SKILL.md",
            "Workflow; Base Modes; Full-Reference Mode",
            "Use the ordered base or reference section family for the selected mode.",
        ),
        (
            "H3_ENGLISH",
            "OFFICIAL_H3",
            "base",
            "SKILL.md",
            "Output Rules",
            "Describe visuals in English; preserve spoken words and visible text in their original language.",
        ),
        (
            "H3_LABELS",
            "OFFICIAL_H3",
            "references",
            "references/ref-en.txt",
            "2; 5.3; 5.4",
            "Every request label must resolve to its own reference manifest.",
        ),
        (
            "H3_TIMING",
            "OFFICIAL_H3",
            "timing",
            "references/base-en.txt",
            "4.2; 4.4",
            "Cut times increase within the shot; dialogue must fit its target timeline.",
        ),
        (
            "H3_KEYFRAMES",
            "OFFICIAL_H3",
            "keyframes",
            "references/base-en.txt",
            "2.1; 3",
            "First and last frame anchors must match the selected mode and duration.",
        ),
        (
            "H3_DIALOGUE",
            "OFFICIAL_H3",
            "dialogue",
            "references/base-en.txt",
            "4.4",
            "Keep each original dialogue line inside a language-tagged dialogue block.",
        ),
        (
            "H3_LIMITS",
            "OFFICIAL_H3",
            "limits",
            "README.md",
            "System Overview; Model Variants and Input Specifications",
            "Pinned capability limits: 4–15 seconds, 24 FPS, 9 images, 3 videos, 3 audio, 12 total.",
        ),
        (
            "SHINE_PROVENANCE",
            "SHINE_POLICY",
            "limits",
            "",
            "M2/M3 offline additions",
            "Freeze style and approve human assets; validate immutable prompt and rule provenance.",
        ),
        (
            "LOCAL_MAPPING",
            "LOCAL_WORKFLOW",
            "references",
            "",
            "workflow_profiles; parameter_map; object_info",
            "A real workflow needs explicit slot mappings and verified node capabilities.",
        ),
    )
]


def builtin_manifest() -> dict:
    return {
        "id": PACK_ID,
        "source_repo": REPO,
        "source_commit": COMMIT,
        "compiler_rule_version": RULE_VERSION,
        "sources": SOURCES,
        "machine_rules": RULES,
        "derived_rules_version": "1.1.0",
        "license_review_status": "METADATA_AND_ORIGINAL_RULES_ONLY",
    }


class RuleViolation(APIModel):
    rule_id: str
    origin: Literal["OFFICIAL_H3", "SHINE_POLICY", "LOCAL_WORKFLOW"]
    severity: Literal["error", "warning"] = "error"
    source_path: str
    locator: str
    affected_field: str
    evidence: str
    remediation: str
    rulepack_id: str


class RuleValidationReport(APIModel):
    rulepack_id: str
    rulepack_hash: str
    passed: bool
    violations: list[RuleViolation] = Field(default_factory=list)


class H3RulebookRegistry:
    def __init__(self, cache_root: Path | None = None):
        self.cache_root = cache_root

    def ensure_builtin(self, db: Session) -> RulePack:
        pack = db.get(RulePack, PACK_ID)
        if pack is None:
            manifest = builtin_manifest()
            pack = RulePack(id=PACK_ID, manifest=manifest, sha256=digest(manifest), approved_at=utcnow())
            db.add(pack)
            db.flush()
        self.verify_pack(pack)
        return pack

    def verify_pack(self, pack: RulePack):
        if (
            set(pack.manifest) != set(builtin_manifest())
            or pack.manifest.get("id") != pack.id
            or not isinstance(pack.manifest.get("derived_rules_version"), str)
            or len(pack.manifest["derived_rules_version"]) > 80
        ):
            raise ValueError("UNSUPPORTED_RULEPACK_METADATA")
        if digest(pack.manifest) != pack.sha256:
            raise ValueError("RULEPACK_HASH_MISMATCH")
        # New source revisions require an implementation/test change, never a remote text update.
        if (
            pack.manifest.get("source_commit") != COMMIT
            or pack.manifest.get("sources") != SOURCES
            or pack.manifest.get("compiler_rule_version") != RULE_VERSION
            or pack.manifest.get("machine_rules") != RULES
        ):
            raise ValueError("UNSUPPORTED_RULEPACK")
        if pack.manifest.get("license_review_status") != "METADATA_AND_ORIGINAL_RULES_ONLY":
            raise ValueError("LICENSE_REVIEW_REQUIRED")

    def lock(self, db: Session, project_id: str, pack_id: str = PACK_ID) -> ProjectRuleLock:
        self.ensure_builtin(db)
        pack = db.get(RulePack, pack_id)
        if pack is None or not pack.approved_at:
            raise ValueError("RULEPACK_NOT_APPROVED")
        self.verify_pack(pack)
        lock = db.get(ProjectRuleLock, project_id)
        if lock:
            if lock.rulepack_id != pack.id or lock.rulepack_hash != pack.sha256:
                raise ValueError("PROJECT_RULEPACK_IMMUTABLE")
            return lock
        lock = ProjectRuleLock(project_id=project_id, rulepack_id=pack.id, rulepack_hash=pack.sha256)
        db.add(lock)
        db.flush()
        return lock

    def for_project(self, db: Session, project_id: str) -> RulePack | None:
        lock = db.get(ProjectRuleLock, project_id)
        if not lock:
            return None  # Explicit legacy compatibility; never changes an existing project's rule pin.
        pack = db.get(RulePack, lock.rulepack_id)
        if not pack or not pack.approved_at or pack.sha256 != lock.rulepack_hash:
            raise ValueError("PROJECT_RULEPACK_INVALID")
        self.verify_pack(pack)
        return pack

    def cache_path(self, source: dict) -> Path:
        if self.cache_root is None:
            raise ValueError("CACHE_UNAVAILABLE")
        return self.cache_root / COMMIT / source["sha256"]

    def cache_status(self) -> list[dict]:
        rows = []
        for source in SOURCES:
            path = self.cache_path(source)
            status = "SOURCE_UNAVAILABLE"
            if path.is_file():
                status = "VERIFIED" if hashlib.sha256(path.read_bytes()).hexdigest() == source["sha256"] else "TAMPERED"
            rows.append({**source, "local_cache_status": status})
        return rows

    def import_source(self, source_path: str, content: bytes):
        source = next((s for s in SOURCES if s["path"] == source_path), None)
        if source is None or hashlib.sha256(content).hexdigest() != source["sha256"]:
            raise ValueError("SOURCE_HASH_MISMATCH")
        blob = hashlib.sha1(b"blob " + str(len(content)).encode() + b"\0" + content).hexdigest()
        if blob != source["blob_sha"]:
            raise ValueError("SOURCE_BLOB_MISMATCH")
        target = self.cache_path(source)
        target.parent.mkdir(parents=True, exist_ok=True)
        # Only a verified, fixed digest destination is written. No caller-controlled paths.
        target.write_bytes(content)
        target.with_suffix(".meta.json").write_text(
            json.dumps(
                {"fetched_at": utcnow().isoformat(), "source": source, "license": "USER_AUTHORIZED_LOCAL_CACHE_ONLY"}
            ),
            encoding="utf-8",
        )

    def search(self, sections: tuple[str, ...] = (), query: str = "") -> list[dict]:
        return [
            r
            for r in RULES
            if (not sections or r["section"] in sections) and (not query or query.lower() in json.dumps(r).lower())
        ]

    def compilation_failure(self, pack: RulePack, exc: PromptCompilationError) -> RuleValidationReport:
        code = exc.code
        if code in {"H3_DURATION", "H3_REFERENCE_LIMIT", "H3_REFERENCE_DURATION", "H3_REFERENCE_DURATION_UNKNOWN"}:
            rule_id = "H3_LIMITS"
        elif code in {"H3_DIALOGUE_TIMING", "H3_DIALOGUE_OVERLAP"}:
            rule_id = "H3_TIMING"
        elif code in {"H3_FRAME_ROLE_MISMATCH", "H3_MODE_REFERENCE_MISMATCH"}:
            rule_id = "H3_KEYFRAMES"
        elif code == "H3_STYLE_MISSING":
            rule_id = "SHINE_PROVENANCE"
        elif code == "H3_SECTION_MISSING":
            rule_id = "H3_SECTIONS"
        elif code == "H3_LOCKED_FACT_TRANSLATION_REQUIRED":
            rule_id = "H3_ENGLISH"
        else:
            rule_id = "H3_LABELS"
        rule = next(r for r in RULES if r["id"] == rule_id)
        return RuleValidationReport(
            rulepack_id=pack.id,
            rulepack_hash=pack.sha256,
            passed=False,
            violations=[
                RuleViolation(
                    rule_id=rule_id,
                    origin=rule["origin"],
                    source_path=rule["source_path"],
                    locator=rule["locator"],
                    affected_field="shot_spec/reference_manifest",
                    evidence=code,
                    remediation=rule["summary"],
                    rulepack_id=pack.id,
                )
            ],
        )

    def validate(self, request: RenderRequest, pack: RulePack) -> RuleValidationReport:
        self.verify_pack(pack)
        violations = []

        def reject(rule_id: str, field: str, evidence: str):
            rule = next(r for r in RULES if r["id"] == rule_id)
            violations.append(
                RuleViolation(
                    rule_id=rule_id,
                    origin=rule["origin"],
                    source_path=rule["source_path"],
                    locator=rule["locator"],
                    affected_field=field,
                    evidence=evidence,
                    remediation=rule["summary"],
                    rulepack_id=pack.id,
                )
            )

        if request.rulepack_id != pack.id or request.rulepack_hash != pack.sha256:
            reject("SHINE_PROVENANCE", "rulepack_hash", "Request rule pin does not match the project lock")
        try:
            compiled = H3PromptCompiler().compile(
                spec=request.shot_spec,
                style=request.style_snapshot,
                manifest=request.reference_manifest,
                mode_hint=request.generation_mode,
                project_language=request.project_language,
            )
            # The single compiler is also the verifier: arbitrary agent strings cannot be submitted.
            if (
                compiled.prompt != request.compiled_prompt
                or compiled.prompt_sha256 != request.prompt_sha256
                or compiled.sections != request.compiled_prompt_sections
                or request.rule_version != RULE_VERSION
                or request.prompt_skill_hash != SKILL_SHA256
            ):
                reject("SHINE_PROVENANCE", "compiled_prompt", "Prompt differs from the deterministic compiler output")
        except PromptCompilationError as exc:
            violations.extend(self.compilation_failure(pack, exc).violations)
        order = REF_SECTION_ORDER if request.generation_mode == GenerationMode.REF2VA else BASE_SECTION_ORDER
        sections = request.compiled_prompt_sections
        if (
            request.style_version_id != request.shot_spec.style_version_id
            or digest(request.style_snapshot.model_dump(mode="json")) != request.style_hash
            or request.shot_spec.duration_target_s != request.expected_duration_s
        ):
            reject("SHINE_PROVENANCE", "style_hash", "Style snapshot, version, or duration differs from request")
        if tuple(sections) != order or any(not sections.get(k, "").strip() for k in order):
            reject("H3_SECTIONS", "compiled_prompt_sections", "Missing or misordered sections")
        # Test the text outside dialogue and visible quoted text. CJK is deterministic;
        # fluency and creative quality still need probabilistic review and are not auto-passed here.
        text = request.compiled_prompt or ""
        descriptive = re.sub(r"<d>.*?</d>|\"[^\"]*\"|「[^」]*」", "", text, flags=re.S)
        if re.search(r"[\u3400-\u9fff]", descriptive):
            reject("H3_ENGLISH", "compiled_prompt", "CJK found outside dialogue or quoted visible text")
        for line in request.shot_spec.dialogue:
            if line.text not in text or line.end_s > request.expected_duration_s:
                reject("H3_DIALOGUE", "shot_spec.dialogue", "Dialogue changed, missing, or outside duration")
        # Dialogue intervals are validated above. Their timestamps may precede
        # editorial cuts even though the compiler appends dialogue after visuals.
        # A chorus line credits several speakers in one sentence, so the
        # sentence may carry more than one reference label.
        visual_timeline = re.sub(
            r"At \d{2}:\d{2}\.\d{3}, [^\n]*?\([^)]*\) says: <d>.*?</d>",
            "", text, flags=re.S,
        )
        times = [int(m) * 60 + float(s) for m, s in re.findall(r"At (\d{2}):(\d{2}\.\d{3})", visual_timeline)]
        if times != sorted(set(times)) or any(t <= 0 or t >= request.expected_duration_s for t in times):
            reject("H3_TIMING", "compiled_prompt", "Cut timestamps are not increasing inside the duration")
        return RuleValidationReport(
            rulepack_id=pack.id, rulepack_hash=pack.sha256, passed=not violations, violations=violations
        )
