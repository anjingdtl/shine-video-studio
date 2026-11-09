import httpx
import pytest

from shine.llm.base import ProviderConfig
from shine.llm.context_window import model_context_for_profile, model_context_from_capabilities
from shine.llm.gateway import LLMGateway
from shine.llm.openai_compatible import OpenAICompatibleProvider
from shine.llm.secrets import SecretStore
from shine.models import ProviderProfile

pytestmark = pytest.mark.unit


def test_minimax_m31_flash_uses_official_one_million_context_when_endpoint_omits_limits():
    context = model_context_from_capabilities(
        {}, "https://api.minimax.cn/v1", "MiniMax-M3.1-Flash",
    )
    assert context == {
        "model": "MiniMax-M3.1-Flash",
        "context_window_tokens": 1_000_000,
        "max_output_tokens": None,
        "source": "minimax_official_m3_1_flash_1m",
    }


def test_deepseek_model_metadata_supplies_input_and_output_windows():
    context = model_context_from_capabilities(
        {"model_metadata": [{
            "id": "deepseek-flash", "context_window_tokens": 1_048_576,
            "max_output_tokens": 393_216,
        }]},
        "https://api.deepseek.com/v1", "deepseek-flash",
    )
    assert context["context_window_tokens"] == 1_048_576
    assert context["max_output_tokens"] == 393_216
    assert context["source"] == "provider_models_metadata"


def test_unknown_models_keep_full_input_policy_without_fake_small_limit():
    context = model_context_from_capabilities({}, "https://llm.example/v1", "unknown-model")
    assert context["context_window_tokens"] is None
    assert context["max_output_tokens"] is None
    assert context["source"] == "provider_did_not_declare_limits"


def test_model_list_context_metadata_is_returned_by_connection_check(monkeypatch):
    provider = OpenAICompatibleProvider(ProviderConfig(
        "p", "DeepSeek", "openai_compatible", "https://api.deepseek.com/v1", "deepseek-flash", None,
    ))
    monkeypatch.setattr(provider, "_request", lambda *args, **kwargs: httpx.Response(200, json={
        "data": [{"id": "deepseek-flash", "context_window": 1_048_576, "max_output_tokens": 393_216}],
    }))
    result = provider.detect_capabilities()
    assert result["model_context"]["context_window_tokens"] == 1_048_576
    assert result["model_metadata"] == [{
        "id": "deepseek-flash", "context_window_tokens": 1_048_576, "max_output_tokens": 393_216,
    }]


def test_gateway_passes_discovered_context_capability_to_each_role_provider(tmp_path):
    profile = ProviderProfile(
        id="profile", name="DeepSeek", kind="openai_compatible",
        base_url="https://api.deepseek.com/v1", model="deepseek-flash", enabled=True,
        capabilities={"model_metadata": [{
            "id": "deepseek-flash", "context_window_tokens": 1_048_576,
            "max_output_tokens": 393_216,
        }]},
        generation_options={}, timeout_s=180, max_retries=2, temperature=0.2,
    )
    config = LLMGateway(SecretStore(tmp_path / "secrets")).config_for(profile)
    assert config.context_window_tokens == 1_048_576
    assert config.max_output_tokens == 393_216
    assert config.context_window_source == "provider_models_metadata"


def test_profile_context_helper_uses_detected_model_metadata():
    profile = ProviderProfile(
        id="profile", name="DeepSeek", kind="openai_compatible",
        base_url="https://api.deepseek.com/v1", model="deepseek-flash", enabled=True,
        capabilities={"model_metadata": [{"id": "deepseek-flash", "context_window": 1_048_576}]},
        generation_options={}, timeout_s=180, max_retries=2, temperature=0.2,
    )
    assert model_context_for_profile(profile)["context_window_tokens"] == 1_048_576
