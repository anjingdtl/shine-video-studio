"""审片是可选增强：机器级「唯一下一步」不得把它报成必做。

没有 VLM/ASR 的机器照样能真实出片，语义验收留给人工。summary 的能力卡
如实标注 optional，_next_action 跳过可选能力；各能力自己的 next_step 保留。
"""
# ruff: noqa: F811  (the `prep` fixture is shadowed by design)

from __future__ import annotations

import pytest

from shine.comfyops.setup_summary import _next_action
from tests.conftest import create_project
from tests.test_v13_preparation import HEADERS, prep  # noqa: F401

pytestmark = pytest.mark.integration


def test_summary_marks_review_capabilities_optional(prep):
    client, _, _ = prep
    project = create_project(client)
    body = client.get(f"/api/v1/setup/summary?project_id={project}", headers=HEADERS).json()
    caps = body["capabilities"]
    assert caps["vlm"]["optional"] is True
    assert caps["asr"]["optional"] is True
    assert caps["h3"]["optional"] is False
    assert caps["image"]["optional"] is False


def test_next_action_skips_optional_review():
    caps = {
        "h3": {"key": "h3", "optional": False, "next_step": None},
        "image": {"key": "image", "optional": False, "next_step": None},
        "vlm": {"key": "vlm", "optional": True,
                "next_step": {"code": "FILES_MISSING", "label": "准备环境", "action": "PREPARE_ENVIRONMENT"}},
        "asr": {"key": "asr", "optional": True,
                "next_step": {"code": "FILES_MISSING", "label": "准备环境", "action": "PREPARE_ENVIRONMENT"}},
    }
    assert _next_action({"choice_required": False}, caps, project_id="p") is None
    # 必做能力仍有下一步时，正常返回。
    caps["h3"]["next_step"] = {"code": "WEIGHTS_MISSING", "label": "下载", "action": "INSTALL_PLAN"}
    step = _next_action({"choice_required": False}, caps, project_id="p")
    assert step["capability"] == "h3"


def test_installation_choice_still_outranks_everything():
    caps = {key: {"key": key, "optional": False, "next_step": None}
            for key in ("h3", "image", "vlm", "asr")}
    step = _next_action({"choice_required": True, "candidates": []}, caps, project_id="p")
    assert step["code"] == "INSTALLATION_SELECTION_REQUIRED"