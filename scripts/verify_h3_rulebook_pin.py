"""Prove that the local H3 rulebook still matches the official skill material.

The compiler may only submit prompts that the pinned official skill describes.
This check recomputes the official source digests from a local copy of the
official skill directory and compares them with the constants the compiler is
pinned to, and it compares the section orders and the reference label families
with the official structure.

    python scripts/verify_h3_rulebook_pin.py --skill-dir <official skill dir>
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]

# The official structure, transcribed from the official guide's own headings.
OFFICIAL_REF_SECTIONS = (
    "subject_definitions",
    "summary",
    "retention_analysis",
    "detailed_description",
    "overall_soundscape",
    "non_diegetic_music",
)
OFFICIAL_BASE_SECTIONS = (
    "integrated_multimodal_description",
    "overall_soundscape",
    "non_diegetic_music",
)
OFFICIAL_LABEL_FAMILIES = ("Subject", "Picture", "Video", "Audio")


def main() -> int:
    parser = argparse.ArgumentParser(description="Verify the pinned H3 rulebook against the official skill")
    parser.add_argument("--skill-dir", required=True, help="the local copy of the official skill directory")
    parser.add_argument("--evidence", default="")
    args = parser.parse_args()

    sys.path.insert(0, str(REPO_ROOT / "backend"))
    from shine.h3 import compiler
    from shine.h3.rulebook import PACK_ID, SOURCES

    skill_dir = Path(args.skill_dir).resolve()
    pinned = {
        "SKILL.md": compiler.SKILL_SHA256,
        "references/base-en.txt": compiler.BASE_RULE_SHA256,
        "references/ref-en.txt": compiler.REF_RULE_SHA256,
    }
    files = []
    failures = []
    for relative, expected in pinned.items():
        path = skill_dir / relative
        if not path.is_file():
            failures.append(f"the official source is missing: {relative}")
            files.append({"path": relative, "expected_sha256": expected, "actual_sha256": None,
                          "match": False})
            continue
        actual = hashlib.sha256(path.read_bytes()).hexdigest()
        match = actual == expected
        if not match:
            failures.append(f"the official source drifted: {relative}")
        files.append({"path": relative, "expected_sha256": expected, "actual_sha256": actual,
                      "match": match})

    checks = {
        "ref_sections_match_official": tuple(compiler.REF_SECTION_ORDER) == OFFICIAL_REF_SECTIONS,
        "base_sections_match_official": tuple(compiler.BASE_SECTION_ORDER) == OFFICIAL_BASE_SECTIONS,
        "label_families_match_official": all(
            family in compiler.LABEL_PATTERN.pattern for family in OFFICIAL_LABEL_FAMILIES),
        "rule_version_pins_a_commit": "@" in compiler.RULE_VERSION,
    }
    for name, ok in checks.items():
        if not ok:
            failures.append(f"the compiled structure drifted: {name}")

    result = {
        "rulepack_id": PACK_ID,
        "rule_version": compiler.RULE_VERSION,
        "compiler_version": compiler.COMPILER_VERSION,
        "sources": files,
        "checks": checks,
        "failures": failures,
        "verdict": "PASS" if not failures else "FAIL",
        "pinned_sources": SOURCES,
    }
    if args.evidence:
        Path(args.evidence).write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({k: v for k, v in result.items() if k != "pinned_sources"},
                     ensure_ascii=False, indent=2))
    return 0 if not failures else 1


if __name__ == "__main__":
    raise SystemExit(main())
