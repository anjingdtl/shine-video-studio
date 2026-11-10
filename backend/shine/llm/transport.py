"""The HTTP transport of the text providers.

The rules live in :mod:`shine.http_env` because the same host environment
traps the renderer client; this module keeps the provider-side names.
"""
from __future__ import annotations

from shine.http_env import build_client, is_local_endpoint, sanitize_no_proxy

__all__ = ["build_client", "is_local_endpoint", "sanitize_no_proxy"]
