from __future__ import annotations

from typing import Any, Protocol

from pydantic import ValidationError

from shine.schemas import ShotSpec


class StructuredModel(Protocol):
    """Agent model port. Implementations return JSON data and have no DB access."""

    def complete_json(self, *, system: str, user: str, schema: dict[str, Any]) -> dict[str, Any]: ...


class ProducerAgentRuntime:
    """Validates model proposals before application services can consider them."""

    def __init__(self, model: StructuredModel):
        self._model = model

    def propose_shot(self, brief: str, *, style_version_id: str) -> ShotSpec:
        schema = ShotSpec.model_json_schema()
        response = self._model.complete_json(
            system=(
                "Return a proposed shot as schema-valid JSON. Respect the locked style, user assets, "
                "story facts, and the requested duration. You cannot approve assets or modify state."
            ),
            user=f"Style version: {style_version_id}\nBrief:\n{brief}",
            schema=schema,
        )
        try:
            proposal = ShotSpec.model_validate(response)
        except ValidationError as exc:
            raise ValueError("Agent proposal failed the ShotSpec contract") from exc
        if proposal.style_version_id != style_version_id:
            raise ValueError("Agent proposal cannot select a different style version")
        return proposal
