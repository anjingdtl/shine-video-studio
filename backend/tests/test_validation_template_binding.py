"""Validation evidence is bound to the prompt template version.

A template bump changes what a sample proves; replaying an older batch under
the same idempotency key would certify composition rules it never rendered.
"""
from __future__ import annotations

import pytest
from fastapi import HTTPException

from tests.conftest import create_project


@pytest.mark.parametrize("change", ["version", "composition"])
def test_a_template_bump_cannot_replay_an_older_validation_batch(api, monkeypatch, change):
    from shine.materialprep import image_prompts, service
    client, factory, settings = api
    pid = create_project(client, "验证绑定")
    with factory() as db:
        first = service.create_validation_batch(
            db, pid, families=["character"], idempotency_key="k1", rights_id=None)
    assert first["replayed"] is False
    if change == "version":
        monkeypatch.setattr(image_prompts, "PROMPT_TEMPLATE_VERSION", "material-image-vTEST")
    else:
        monkeypatch.setitem(image_prompts.PROP_CARRIER_RULES, "paper", "A different paper carrier rule")
    with factory() as db:
        with pytest.raises(HTTPException) as exc:
            service.create_validation_batch(
                db, pid, families=["character"], idempotency_key="k1", rights_id=None)
    assert exc.value.status_code == 409
    assert exc.value.detail["code"] == "IDEMPOTENCY_KEY_CONFLICT"
