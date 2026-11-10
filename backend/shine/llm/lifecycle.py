"""Local text models must unload through a declared protocol, never by assumption."""

from __future__ import annotations

from shine.http_env import build_client
from shine.security import is_loopback_url


def release_text_model(config):
    if config.kind == "mock" or not is_loopback_url(config.base_url):
        return
    base = config.base_url.rstrip("/")
    if base.endswith("/v1"):
        base = base[:-3]
    headers = {"Authorization": f"Bearer {config.api_key}"} if config.api_key else {}
    with build_client(base, config.timeout_s, follow_redirects=False) as client:
        if config.kind == "ollama":
            unloaded = client.post(
                base + "/api/generate", json={"model": config.model, "keep_alive": 0}, headers=headers
            )
            unloaded.raise_for_status()
            running = client.get(base + "/api/ps", headers=headers)
            running.raise_for_status()
            if running.json().get("models"):
                raise ValueError("LOCAL_LLM_RELEASE_UNCONFIRMED")
            return
        response = client.post(base + "/free", json={"unload_models": True}, headers=headers)
        response.raise_for_status()
        proof = response.json()
        allocated = proof.get("allocated_bytes")
        if proof.get("released") is not True or type(allocated) is not int or allocated != 0:
            raise ValueError("LOCAL_LLM_RELEASE_UNCONFIRMED")
