"""Static isolation enforcement for Manual4 and Foundation facade boundaries."""

from __future__ import annotations

import ast
import re
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit

REPO_ROOT = Path(__file__).resolve().parents[3]
M4_BACKEND = REPO_ROOT / "backend" / "shine" / "manual4"
FOUNDATION_BACKEND = REPO_ROOT / "backend" / "shine" / "foundation"
M4_FRONTEND = REPO_ROOT / "frontend" / "src" / "manual4"

# Strict allowlist: Manual4 backend may ONLY import its own package or explicit
# platform/foundation facades — never director7 business internals.
ALLOWED_EXTERNAL_SHINE_PREFIXES = (
    "shine.manual4",
    "shine.config",
    "shine.platform_projects",
    "shine.platform_gpu",
    "shine.foundation",
)

# Foundation facades themselves must never depend on director7 business modules.
FOUNDATION_FORBIDDEN_PREFIXES = (
    "shine.director",
    "shine.director_styles",
    "shine.literary",
    "shine.materialprep",
    "shine.agents",
    "shine.production",
    "shine.voiceops",
    "shine.copilot",
    "shine.support",
    "shine.services",
    "shine.worker",
    "shine.operator_review",
    "shine.main",
    "shine.manual4",
)


def _is_allowed_m4_import(module_name: str) -> bool:
    if not module_name.startswith("shine.") and module_name != "shine":
        return True
    return any(
        module_name == prefix or module_name.startswith(prefix + ".")
        for prefix in ALLOWED_EXTERNAL_SHINE_PREFIXES
    )


def _is_forbidden_foundation_import(module_name: str) -> bool:
    if module_name.startswith("shine.api_"):
        return True
    return any(
        module_name == prefix or module_name.startswith(prefix + ".")
        for prefix in FOUNDATION_FORBIDDEN_PREFIXES
    )


def _dynamic_import_targets(tree: ast.AST) -> list[tuple[int, str]]:
    found: list[tuple[int, str]] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func_name = ""
        if isinstance(node.func, ast.Name):
            func_name = node.func.id
        elif isinstance(node.func, ast.Attribute):
            func_name = node.func.attr
        if func_name in {"import_module", "__import__"} and node.args:
            arg0 = node.args[0]
            if isinstance(arg0, ast.Constant) and isinstance(arg0.value, str):
                found.append((node.lineno, arg0.value))
    return found


def test_manual4_backend_only_imports_allowed_platform_foundations():
    assert M4_BACKEND.is_dir()
    violations: list[str] = []
    for py_file in sorted(M4_BACKEND.rglob("*.py")):
        tree = ast.parse(py_file.read_text(encoding="utf-8"), filename=str(py_file))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    if not _is_allowed_m4_import(alias.name):
                        violations.append(f"{py_file.relative_to(REPO_ROOT)}:{node.lineno} import {alias.name}")
            elif isinstance(node, ast.ImportFrom) and node.module:
                if not _is_allowed_m4_import(node.module):
                    violations.append(f"{py_file.relative_to(REPO_ROOT)}:{node.lineno} from {node.module}")
        for lineno, mod in _dynamic_import_targets(tree):
            if not _is_allowed_m4_import(mod):
                violations.append(f"{py_file.relative_to(REPO_ROOT)}:{lineno} dynamic {mod}")
    assert not violations, "Manual4 may only import manual4 or platform/foundation facades:\n" + "\n".join(violations)


def test_foundation_facades_never_import_director7_business_modules():
    assert FOUNDATION_BACKEND.is_dir()
    violations: list[str] = []
    for py_file in sorted(FOUNDATION_BACKEND.rglob("*.py")):
        tree = ast.parse(py_file.read_text(encoding="utf-8"), filename=str(py_file))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    if _is_forbidden_foundation_import(alias.name):
                        violations.append(f"{py_file.relative_to(REPO_ROOT)}:{node.lineno} import {alias.name}")
            elif isinstance(node, ast.ImportFrom) and node.module:
                if _is_forbidden_foundation_import(node.module):
                    violations.append(f"{py_file.relative_to(REPO_ROOT)}:{node.lineno} from {node.module}")
    assert not violations, "Foundation facades must stay business-neutral:\n" + "\n".join(violations)


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
