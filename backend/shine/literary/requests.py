"""Compile the final request before anything measures it.

The defect this module removes is specific: the gateway estimated the input,
then appended the JSON schema to the user message, and sent a request that was
larger than the one it had just approved.  A budget computed on different bytes
than the bytes that leave the machine is not a budget.

``compile_request`` therefore produces the *only* accepted form of a request:

* the schema instruction is rendered here, once, at its final position;
* the input estimate is taken from the rendered strings;
* the fingerprint identifies those exact bytes, so an attempt ledger can prove
  what was sent and a cache can never answer a different question.

The gateway accepts a compiled request verbatim and refuses to append anything
to it, which is what makes "追加 Schema 不改变已检验预算" testable.
"""

from __future__ import annotations

import hashlib
import json
import math
from dataclasses import dataclass, field
from typing import Any

SCHEMA_INSTRUCTION = (
    "\n\nReturn one JSON object and nothing else. It must validate against "
    "this JSON schema:\n"
)


def estimate_tokens(text: str) -> int:
    """The shared conservative estimate: UTF-8 bytes / 3.

    CJK costs about one token per character and three bytes per character;
    ASCII is over-estimated, which is the safe direction for a budget that
    refuses rather than truncates.
    """
    if not text:
        return 0
    return max(1, math.ceil(len(text.encode("utf-8")) / 3))


@dataclass(frozen=True)
class CompiledRequest:
    """One request, rendered exactly as it will be sent."""

    system: str
    user: str
    schema: dict[str, Any] | None = None
    schema_json: str = ""
    input_tokens: int = 0
    _fingerprint: str = field(default="", repr=False)

    @property
    def fingerprint(self) -> str:
        if self._fingerprint:
            return self._fingerprint
        material = {
            "system_sha256": hashlib.sha256(self.system.encode("utf-8")).hexdigest(),
            "user_sha256": hashlib.sha256(self.user.encode("utf-8")).hexdigest(),
            "schema_sha256": hashlib.sha256(self.schema_json.encode("utf-8")).hexdigest(),
        }
        return hashlib.sha256(json.dumps(material, sort_keys=True).encode()).hexdigest()

    @property
    def user_chars(self) -> int:
        return len(self.user)

    def evidence(self) -> dict[str, Any]:
        return {
            "request_fingerprint": self.fingerprint,
            "system_chars": len(self.system),
            "user_chars": len(self.user),
            "schema_chars": len(self.schema_json),
            "input_tokens_estimated": self.input_tokens,
        }


def compile_request(
    *,
    system: str,
    user: str,
    schema: dict[str, Any] | None = None,
) -> CompiledRequest:
    """Render one request and measure the rendered result exactly once."""
    system = system or ""
    user = user or ""
    schema_json = ""
    if schema is not None:
        schema_json = json.dumps(schema, ensure_ascii=False, separators=(",", ":"))
        user = f"{user}{SCHEMA_INSTRUCTION}{schema_json}"
    return CompiledRequest(
        system=system,
        user=user,
        schema=schema,
        schema_json=schema_json,
        input_tokens=estimate_tokens(system) + estimate_tokens(user),
        _fingerprint="",
    )
