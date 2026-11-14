"""前端的事件码清单必须与后端真正写下的码一致。

前端的 auditEventCodes.json 是「后端实际写下哪些事件码」的清单：它让「未知码不再裸奔」
这条断言可以逐码执行，而不是只挑几个例子。两边只要有一边漏了，这个测试就红。

这里用 AST 扫源码而不是运行时统计：审计事件是在 worker、API 与各个 service 里零散写下的，
没有一个集中的注册表，扫调用点是唯一能覆盖全部写法的方式。条件表达式会在运行时二选一，
两个分支都是真实写下的码，所以都要收。
"""

from __future__ import annotations

import ast
import json
from pathlib import Path

import pytest

pytestmark = pytest.mark.integration

BACKEND_PACKAGE = Path(__file__).resolve().parents[1] / "shine"
FRONTEND_CODES = Path(__file__).resolve().parents[2] / "frontend" / "src" / "auditEventCodes.json"


def _written_event_codes() -> set[str]:
    """The event codes the backend actually writes.

    Every string that can reach the event slot of ``add_audit`` counts.
    """
    codes: set[str] = set()
    for path in sorted(BACKEND_PACKAGE.rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            func = node.func
            name = func.attr if isinstance(func, ast.Attribute) else (
                func.id if isinstance(func, ast.Name) else "")
            if name != "add_audit" or len(node.args) < 3:
                continue
            for part in ast.walk(node.args[2]):
                if isinstance(part, ast.Constant) and isinstance(part.value, str):
                    codes.add(part.value)
    return codes


def _listed_event_codes() -> set[str]:
    payload = json.loads(FRONTEND_CODES.read_text(encoding="utf-8"))
    return {str(code) for code in payload}


def test_the_scanner_actually_finds_the_event_codes():
    """A scanner that finds nothing means the scan broke, not the backend."""
    assert len(_written_event_codes()) > 300


def test_the_frontend_list_covers_every_event_code_the_backend_writes():
    written = _written_event_codes()
    listed = _listed_event_codes()
    missing = sorted(written - listed)
    assert not missing, "前端码表漏了这些后端事件码：" + repr(missing)


def test_the_frontend_list_holds_no_code_the_backend_stopped_writing():
    written = _written_event_codes()
    listed = _listed_event_codes()
    extra = sorted(listed - written)
    assert not extra, "前端码表里这些码后端已经不再写：" + repr(extra)
