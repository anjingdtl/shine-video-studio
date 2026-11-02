"""Resolve a provider's declared model context without imposing a small fallback cap."""

from __future__ import annotations

from urllib.parse import urlparse


def _positive_int(value) -> int | None:
    if isinstance(value, bool):
        return None
    try:
        parsed = int(value)
    except (TypeError, ValueError, OverflowError):
        return None
    return parsed if parsed > 0 else None


def official_model_context(base_url: str, model: str) -> dict | None:
    """Known official 1M-context model declarations, used when /models omits limits."""
    host = (urlparse(base_url).hostname or "").lower()
    model_id = model.strip().lower().replace("_", "-")
    if host in {"api.minimax.cn", "api.minimax.io", "api.minimaxi.com", "platform.minimax.io"} \
            and model_id in {"minimax-m3.1-flash", "minimax-m3-1-flash"}:
        return {
            "model": model,
            "context_window_tokens": 1_000_000,
            "max_output_tokens": None,
            "source": "minimax_official_m3_1_flash_1m",
        }
    if host == "api.deepseek.com" and model_id in {
        "deepseek-flash", "deepseek-v4.1-flash", "deepseek-v4.1-flash-1017", "deepseek-v4-flash",
    }:
        return {
            "model": model,
            "context_window_tokens": 1_048_576,
            "max_output_tokens": 393_216,
            "source": "deepseek_official_model_metadata",
        }
    return None


def model_context_from_capabilities(capabilities: dict | None, base_url: str, model: str) -> dict:
    capabilities = capabilities if isinstance(capabilities, dict) else {}
    official = official_model_context(base_url, model)
    for item in capabilities.get("model_metadata") or []:
        if not isinstance(item, dict) or str(item.get("id", "")).casefold() != model.casefold():
            continue
        context_window = _positive_int(item.get("context_window_tokens") or item.get("context_window"))
        max_output = _positive_int(item.get("max_output_tokens"))
        if context_window is not None or max_output is not None:
            return {
                "model": model,
                "context_window_tokens": context_window or (official or {}).get("context_window_tokens"),
                "max_output_tokens": max_output or (official or {}).get("max_output_tokens"),
                "source": "provider_models_metadata" if context_window is not None else (
                    "provider_output_with_official_context" if official else "provider_models_metadata"
                ),
            }
    declared = capabilities.get("model_context")
    if isinstance(declared, dict) and str(declared.get("model", "")).casefold() == model.casefold():
        context_window = _positive_int(declared.get("context_window_tokens"))
        max_output = _positive_int(declared.get("max_output_tokens"))
        if context_window is not None or max_output is not None:
            return {
                "model": model,
                "context_window_tokens": context_window or (official or {}).get("context_window_tokens"),
                "max_output_tokens": max_output or (official or {}).get("max_output_tokens"),
                "source": str(declared.get("source") or "provider_models_metadata") if context_window is not None else (
                    "provider_output_with_official_context" if official else "provider_models_metadata"
                ),
            }
    return official or {
        "model": model,
        "context_window_tokens": None,
        "max_output_tokens": None,
        "source": "provider_did_not_declare_limits",
    }


def model_context_for_profile(profile) -> dict:
    return model_context_from_capabilities(
        getattr(profile, "capabilities", None), profile.base_url, profile.model,
    )
