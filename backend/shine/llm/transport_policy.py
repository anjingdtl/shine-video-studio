"""Transport timeouts: one policy, separated from the worker's own liveness.

A provider that is thinking deeply sends no bytes for a long time.  The old
single ``timeout_s`` could not tell three different situations apart:

* the connection was never established (nothing was sent - safe to retry);
* the request was sent and no first event arrived (the answer may exist);
* the answer is streaming and stalled (bytes arrived, so the provider is alive).

This module names those deadlines.  It deliberately does **not** decide whether
a worker is alive: the worker renews its own lease on its own heartbeat, and a
silent stream never touches that lease.
"""

from __future__ import annotations

import os
from dataclasses import dataclass

from shine.llm.base import ProviderConfig

MIN_CONNECT_TIMEOUT_S = 10.0
MIN_FIRST_EVENT_S = 120.0
MIN_IDLE_S = 45.0
TOTAL_TIMEOUT_MULTIPLE = 6.0


def streaming_enabled(config: ProviderConfig) -> bool:
    """Whether streaming is explicitly switched on for this process.

    Off by default: a provider that was never probed must not be sent a
    streaming request just because the adapter happens to support SSE.
    """
    mode = os.getenv("SHINE_LLM_STREAMING", "off").strip().casefold()
    if mode not in {"on", "1", "true", "yes"}:
        return False
    return config.kind in {"openai_compatible", "ollama"}


@dataclass(frozen=True)
class TransportPolicy:
    connect_s: float
    first_event_s: float
    idle_s: float
    total_s: float
    streaming: bool

    def as_dict(self) -> dict:
        return {
            "connect_s": self.connect_s, "first_event_s": self.first_event_s,
            "idle_s": self.idle_s, "total_s": self.total_s,
            "streaming": self.streaming,
        }


def policy_for(config: ProviderConfig, *, streaming: bool | None = None) -> TransportPolicy:
    """Derive the four deadlines from the profile's own timeout."""
    base = float(config.timeout_s or 60.0)
    enabled = streaming_enabled(config) if streaming is None else bool(streaming)
    return TransportPolicy(
        connect_s=max(MIN_CONNECT_TIMEOUT_S, min(base / 4, 30.0)),
        first_event_s=max(MIN_FIRST_EVENT_S, base),
        idle_s=max(MIN_IDLE_S, base / 2),
        total_s=max(base * TOTAL_TIMEOUT_MULTIPLE, 180.0),
        streaming=enabled,
    )
