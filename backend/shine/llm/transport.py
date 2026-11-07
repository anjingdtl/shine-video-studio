from __future__ import annotations

from urllib.parse import urlparse

import httpx

#: Host names that always mean "this machine".  A loopback provider must never
#: be routed through a system proxy: the proxy is usually remote, it cannot
#: reach the local port, and its failure looks exactly like "the provider is
#: broken".
_LOCAL_HOSTS = {"localhost", "::1", "[::1]"}


def is_local_endpoint(url: str) -> bool:
    """True when the URL points at this machine."""
    try:
        host = (urlparse(url).hostname or "").lower()
    except ValueError:
        return False
    if host in _LOCAL_HOSTS:
        return True
    return host.startswith("127.")


def build_client(url: str, timeout: float, **kwargs) -> httpx.Client:
    """An HTTP client that only trusts the environment proxy for remote hosts."""
    kwargs.setdefault("follow_redirects", True)
    return httpx.Client(timeout=timeout, trust_env=not is_local_endpoint(url), **kwargs)
