"""Manual4's loopback renderer control. H3 traffic uses the shared ComfyUI client."""

from __future__ import annotations

from ipaddress import ip_address
from typing import Any
from urllib.parse import urlparse

import httpx

from shine.foundation.h3 import ComfyUIClient

LOOPBACK_HOSTS = {"localhost", "localhost.localdomain", "127.0.0.1", "::1"}


def is_loopback_url(url: str) -> bool:
    parsed = urlparse(url)
    host = (parsed.hostname or "").strip().lower()
    if host in LOOPBACK_HOSTS:
        return True
    try:
        return ip_address(host).is_loopback
    except ValueError:
        return False


class M4ComfyClient(ComfyUIClient):
    def __init__(self, base_url: str, *, timeout_s: float = 30.0):
        if not is_loopback_url(base_url):
            raise ValueError("Manual4 ComfyUI URL must be loopback")
        super().__init__(base_url, timeout_s=timeout_s, max_retries=0, client_id="shine-manual4", trust_env=False)

    def handoff_resume(self, token: str) -> dict[str, Any]:
        return self._handoff("resume", token, timeout_s=10.0)

    def handoff_drain(self, token: str) -> dict[str, Any]:
        return self._handoff("drain", token, timeout_s=30.0)

    def _handoff(self, action: str, token: str, *, timeout_s: float) -> dict[str, Any]:
        with httpx.Client(timeout=timeout_s, trust_env=False) as client:
            response = client.post(
                f"{self.base_url}/shine-handoff/{action}", headers={"Authorization": f"Bearer {token}"}
            )
            response.raise_for_status()
            return response.json()
