"""Explicit output bounds must persist, reach transport and invalidate stale proofs."""
import json
from dataclasses import replace

import httpx
import pytest

from shine.llm.base import ChatMessage, LLMFormatError, text_provider_snapshot
from shine.llm.gateway import LLMGateway
from shine.llm.openai_compatible import OpenAICompatibleProvider
from shine.models import ProviderProfile
from tests.test_llm_gateway import config


@pytest.mark.parametrize("reason", ["length", "max_tokens", "content_filter"])
def test_truncated_completion_never_passes_even_if_json_is_valid(reason, monkeypatch):
    provider = OpenAICompatibleProvider(config(type("Endpoint", (), {"base_url": "http://127.0.0.1"})()))
    monkeypatch.setattr(provider, "_request", lambda *a, **kw: httpx.Response(200, json={
        "choices": [{"finish_reason": reason, "message": {"content": '{"ok":true}'}}],
    }))
    with pytest.raises(LLMFormatError, match="truncated or filtered"):
        provider.complete([ChatMessage("user", "bounded check")])


def test_explicit_budget_reaches_exact_request_without_unset_options(monkeypatch):
    provider = OpenAICompatibleProvider(replace(
        config(type("Endpoint", (), {"base_url": "http://127.0.0.1"})()),
        generation_options={"max_completion_tokens": 8192, "reasoning_effort": "low"},
    ))
    seen = []
    def request(*args, **kwargs):
        seen.append(kwargs["payload"])
        return httpx.Response(200, json={"choices": [{"finish_reason": "stop", "message": {"content": '{"ok":true}'}}]})
    monkeypatch.setattr(provider, "_request", request)
    assert provider.complete([ChatMessage("user", "bounded check")]).parsed == {"ok": True}
    assert len(seen) == 1 and seen[0]["max_completion_tokens"] == 8192 and seen[0]["reasoning_effort"] == "low"
    assert "max_tokens" not in seen[0]


@pytest.mark.parametrize("options", [
    {"max_completion_tokens": 0}, {"max_completion_tokens": True}, {"max_completion_tokens": 1.5},
    {"max_completion_tokens": 524289}, {"reasoning_effort": "none"}, {"api_key": "forbidden"},
])
def test_generation_options_reject_unsupported_or_invalid_fields(api, options):
    client, _, _ = api
    response = client.post("/api/v1/provider-profiles", json={
        "name": "bad-options", "kind": "openai_compatible", "base_url": "http://127.0.0.1:9999/v1",
        "model": "test-model", "generation_options": options,
    })
    assert response.status_code == 422


def test_options_roundtrip_clear_and_bind_provider_identity(api, tmp_path):
    from shine.llm.secrets import SecretStore
    client, factory, _ = api
    options = {"max_completion_tokens": 8192, "reasoning_effort": "low"}
    response = client.post("/api/v1/provider-profiles", json={
        "name": "bounded-options", "kind": "openai_compatible", "base_url": "http://127.0.0.1:9999/v1",
        "model": "test-model", "generation_options": options,
    })
    assert response.status_code == 201
    identity = response.json()["id"]
    with factory() as db:
        profile = db.get(ProviderProfile, identity)
        assert profile.generation_options == options
        gateway = LLMGateway(SecretStore(tmp_path / "secrets"))
        assert gateway.config_for(profile).generation_options == options
        bound = text_provider_snapshot(profile)
        assert "generation_options_sha256" in bound
    assert client.put(f"/api/v1/provider-profiles/{identity}", json={"kind": "anthropic"}).status_code == 422
    assert client.put(f"/api/v1/provider-profiles/{identity}", json={"generation_options": {}}).status_code == 200
    with factory() as db:
        profile = db.get(ProviderProfile, identity)
        assert profile.generation_options == {}
        legacy = text_provider_snapshot(profile)
        assert "generation_options_sha256" not in legacy and bound != legacy
    assert not any(k in json.dumps(response.json()) for k in ['"api_key_ref"', '"api_key"'])


def test_legacy_migration_preserves_profile_and_adds_empty_options(tmp_path, monkeypatch):
    import sqlite3

    from alembic import command
    from tests.test_alembic import alembic_config
    path = tmp_path / "legacy.db"
    monkeypatch.setenv("SHINE_DATABASE_URL", f"sqlite:///{path.as_posix()}")
    monkeypatch.setenv("SHINE_DATA_DIR", str(tmp_path))
    command.upgrade(alembic_config(path), "f130a0020001")
    with sqlite3.connect(path) as db:
        db.execute(
            "INSERT INTO provider_profiles (id,name,kind,base_url,model,api_key_ref,capabilities,timeout_s,"
            "max_retries,temperature,enabled,created_at,updated_at) VALUES "
            "('legacy','legacy','openai_compatible','http://localhost','unchanged','opaque-ref','{}',"
            "120,0,0.2,1,'2026-01-01','2026-01-01')"
        )
    command.upgrade(alembic_config(path), "head")
    with sqlite3.connect(path) as db:
        row = db.execute("SELECT model,api_key_ref,timeout_s,max_retries,generation_options "
                         "FROM provider_profiles WHERE id='legacy'").fetchone()
    assert row == ("unchanged", "opaque-ref", 120, 0, "{}")
