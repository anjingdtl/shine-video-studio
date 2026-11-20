"""Static isolation enforcement for Manual4 backend and frontend modules."""

from __future__ import annotations

import ast
import re
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit

REPO_ROOT = Path(__file__).resolve().parents[3]
M4_BACKEND = REPO_ROOT / "backend" / "shine" / "manual4"
M4_FRONTEND = REPO_ROOT / "frontend" / "src" / "manual4"

FORBIDDEN_BACKEND_PREFIXES = (
    "shine.director",
    "shine.director_styles",
    "shine.literary",
    "shine.materialprep",
    "shine.asset_center",
    "shine.agents",
    "shine.production",
    "shine.media",
    "shine.quality",
    "shine.voiceops",
    "shine.h3",
    "shine.comfy",
    "shine.comfyops",
    "shine.copilot",
    "shine.support",
    "shine.services",
    "shine.worker",
    "shine.operator_review",
    "shine.production_mode",
    "shine.main",
    "shine.models",
    "shine.db",
    "shine.schemas",
    "shine.security",
    "shine.providers",
)


def _is_forbidden_import(module_name: str) -> bool:
    if module_name.startswith("shine.api_"):
        return True
    return any(
        module_name == prefix or module_name.startswith(prefix + ".")
        for prefix in FORBIDDEN_BACKEND_PREFIXES
    )


def test_manual4_backend_never_imports_seven_stage_modules():
    assert M4_BACKEND.is_dir()
    violations: list[str] = []
    for py_file in sorted(M4_BACKEND.rglob("*.py")):
        tree = ast.parse(py_file.read_text(encoding="utf-8"), filename=str(py_file))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    if _is_forbidden_import(alias.name):
                        violations.append(f"{py_file.relative_to(REPO_ROOT)}:{node.lineno} import {alias.name}")
            elif isinstance(node, ast.ImportFrom) and node.module:
                if _is_forbidden_import(node.module):
                    violations.append(f"{py_file.relative_to(REPO_ROOT)}:{node.lineno} from {node.module}")
    assert not violations, "Manual4 must not import 7-stage modules:\n" + "\n".join(violations)


def test_manual4_frontend_never_imports_seven_stage_or_calls_seven_stage_urls():
    if not M4_FRONTEND.is_dir():
        pytest.skip("Manual4 frontend not yet created")
    forbidden_import = re.compile(r"""from\s+['"](?:\.\./)+(?:components|asset-center|useStudio)""")
    forbidden_endpoint = re.compile(
        r"""['"`](?:/api/v1)?/(?:projects/|director/|material-preparation|creative-briefs|journey)"""
    )
    violations: list[str] = []
    for src_file in sorted(M4_FRONTEND.rglob("*.ts*")):
        text = src_file.read_text(encoding="utf-8")
        for lineno, line in enumerate(text.splitlines(), 1):
            stripped = line.strip()
            if stripped.startswith(("//", "/*", "*")):
                continue
            if forbidden_import.search(line) or forbidden_endpoint.search(line):
                violations.append(f"{src_file.relative_to(REPO_ROOT)}:{lineno}: {stripped}")
    assert not violations, "Manual4 frontend must stay isolated:\n" + "\n".join(violations)
