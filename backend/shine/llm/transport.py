from __future__ import annotations

import os
from urllib.parse import urlparse

import httpx

#: Host names that always mean "this machine".  A loopback provider must never
#: be routed through a system proxy: the proxy is usually remote, it cannot
#: reach the local port, and its failure looks exactly like "the provider is
#: broken".
_LOCAL_HOSTS = {"localhost", "::1", "[::1]"}

_NO_PROXY_KEYS = ("NO_PROXY", "no_proxy")
_no_proxy_sanitized = False


def _sanitize_no_proxy() -> None:
    """Rewrite a bracketed IPv6 ``NO_PROXY`` entry into the form httpx accepts.

    httpx turns every ``NO_PROXY`` entry into a URL pattern, and ``[::1]`` reads
    as a host carrying the port ``:1]`` -- ``InvalidURL: Invalid port: ':1]'``.
    ``InvalidURL`` is not an ``httpx.HTTPError``, so it escaped every transport
    handler and surfaced as a provider fault: one host-level environment detail
    broke *every* remote text call before a socket was opened.  ``[::1]`` and
    ``::1`` name the same host, so dropping the brackets preserves the meaning.
    """
    global _no_proxy_sanitized
    if _no_proxy_sanitized:
        return
    _no_proxy_sanitized = True
    for key in _NO_PROXY_KEYS:
        value = os.environ.get(key)
        if not value or "[" not in value:
            continue
        entries = [entry.strip() for entry in value.split(",") if entry.strip()]
        os.environ[key] = ",".join(
            entry[1:-1] if entry.startswith("[") and entry.endswith("]") else entry
            for entry in entries
        )


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
    local = is_local_endpoint(url)
    if not local:
        _sanitize_no_proxy()
    return httpx.Client(timeout=timeout, trust_env=not local, **kwargs)
