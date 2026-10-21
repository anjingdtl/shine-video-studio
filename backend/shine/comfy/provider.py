from __future__ import annotations

import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from shine.comfy.client import ComfyError, ComfyUIError, ComfyUnavailable
from shine.comfy.workflow import (
    apply_parameters,
    generic_h3_parameters,
    graph_hash,
    validate_against_object_info,
    validate_graph,
)
from shine.media.artifacts import content_type_for_name, safe_asset_path
from shine.models import Asset
from shine.schemas import RenderProvider, RenderRequest


@dataclass(frozen=True)
class ComfySubmission:
    prompt_id: str
    workflow_hash: str
    uploaded_references: int


@dataclass(frozen=True)
class ComfyResult:
    status: str
    output_filename: str | None = None
    subfolder: str = ""
    content: bytes | None = None
    content_type: str | None = None
    error: str | None = None


class ComfyUIRenderService:
    """Maps an immutable Shine RenderRequest onto a registered API workflow."""

    def __init__(self, client, asset_root: Path):
        self.client = client
        self.asset_root = asset_root

    def _profile_graph(self, profile) -> dict[str, Any]:
        graph = profile.graph or {}
        validate_graph(graph, required_nodes=profile.required_nodes)
        return graph

    def _upload_references(
        self, request: RenderRequest, assets: dict[str, Asset], parameter_map: dict[str, str]
    ) -> dict[str, str]:
        uploaded: dict[str, str] = {}
        counts: dict[str, int] = {}
        for item in request.reference_manifest:
            counts[item.use_as.value] = counts.get(item.use_as.value, 0) + 1
            logical = f"reference_{item.use_as.value}_{counts[item.use_as.value]}"
            if logical not in parameter_map:
                continue
            asset = assets.get(item.asset_id)
            if asset is None:
                raise ComfyUIError("A manifest asset is missing from the local asset registry")
            path = safe_asset_path(self.asset_root, asset.storage_path)
            if not path.is_file():
                raise ComfyUIError("A manifest asset file is missing on disk", details={"asset_id": asset.id})
            response = self.client.upload_image(
                f"shine_{asset.id}{path.suffix or '.bin'}",
                path.read_bytes(),
                subfolder="shine",
                content_type=asset.content_type,
            )
            name = response.get("name")
            if not name:
                raise ComfyUIError("ComfyUI upload response did not include a filename")
            subfolder = response.get("subfolder") or ""
            uploaded[logical] = f"{subfolder}/{name}" if subfolder else str(name)
        return uploaded

    def submit(
        self,
        request: RenderRequest,
        profile,
        assets: dict[str, Asset],
        *,
        seed: int | None = None,
    ) -> ComfySubmission:
        graph = self._profile_graph(profile)
        uploaded = self._upload_references(request, assets, profile.parameter_map)
        values = generic_h3_parameters(
            prompt=request.compiled_prompt or "",
            seed=seed,
            duration_s=request.expected_duration_s,
            references=uploaded,
        )
        mapped = {key: value for key, value in values.items() if key in profile.parameter_map}
        if "positive_prompt" in profile.parameter_map and not mapped.get("positive_prompt"):
            raise ComfyUIError("The workflow profile does not accept the compiled H3 prompt")
        prompt = apply_parameters(graph, profile.parameter_map, mapped)
        body = self.client.queue_prompt(
            prompt,
            extra_data={
                "shine": {
                    "job_id": request.shot_id,
                    "idempotency_key": request.idempotency_key,
                    "prompt_sha256": request.prompt_sha256,
                    "style_version_id": request.style_version_id,
                    "provider": RenderProvider.COMFYUI.value,
                }
            },
        )
        return ComfySubmission(
            prompt_id=str(body["prompt_id"]),
            workflow_hash=graph_hash(graph),
            uploaded_references=len(uploaded),
        )

    def _queue_state(self, prompt_id: str) -> str:
        queue = self.client.queue()
        for key in ("queue_running", "queue_pending"):
            for item in queue.get(key) or []:
                if isinstance(item, list) and len(item) > 1 and str(item[1]) == prompt_id:
                    return "running" if key == "queue_running" else "pending"
        return "missing"

    def poll(self, prompt_id: str) -> ComfyResult:
        history = self.client.history(prompt_id)
        record = history.get(prompt_id) if isinstance(history, dict) else None
        if record:
            status = (record.get("status") or {})
            if status.get("status_str") == "error":
                return ComfyResult(status="failed", error="ComfyUI reported an execution error")
            outputs = record.get("outputs") or {}
            for _node_id, node_output in outputs.items():
                for key in ("gifs", "videos", "images", "audio"):
                    items = node_output.get(key) or []
                    if items:
                        first = items[0]
                        filename = first.get("filename")
                        if not filename:
                            continue
                        subfolder = first.get("subfolder") or ""
                        file_type = first.get("type") or "output"
                        content = self.client.view(filename, subfolder=subfolder, file_type=file_type)
                        return ComfyResult(
                            status="completed",
                            output_filename=filename,
                            subfolder=subfolder,
                            content=content,
                            content_type=content_type_for_name(filename),
                        )
            return ComfyResult(status="completed_without_output")
        queue_state = self._queue_state(prompt_id)
        if queue_state in {"running", "pending"}:
            return ComfyResult(status=queue_state)
        return ComfyResult(status="not_found")

    def wait(self, prompt_id: str, *, timeout_s: float = 120.0, interval_s: float = 0.2) -> ComfyResult:
        deadline = time.monotonic() + timeout_s
        while True:
            result = self.poll(prompt_id)
            if result.status not in {"running", "pending"}:
                return result
            if time.monotonic() >= deadline:
                return ComfyResult(status="timeout")
            time.sleep(interval_s)

    def reconcile(self, prompt_id: str) -> str:
        try:
            result = self.poll(prompt_id)
        except ComfyError as exc:
            return f"unavailable:{exc.code}"
        return result.status

    def capabilities(self, profile=None) -> dict[str, Any]:
        stats = self.client.system_stats()
        object_info = self.client.object_info()
        h3_nodes = sorted(name for name in object_info if "h3" in name.lower() or "minimax" in name.lower())
        missing: list[str] = []
        validation: dict[str, Any] = {}
        if profile is not None:
            validation = validate_against_object_info(profile.graph or {}, profile.parameter_map, object_info)
            missing = validation["missing_nodes"]
        return {
            "system_stats": stats,
            "node_count": len(object_info),
            "h3_nodes": h3_nodes,
            "missing_nodes": missing,
            "validation": validation,
        }


def is_comfy_configured(settings) -> bool:
    return bool(settings.render_provider == "comfyui" and settings.comfyui_base_url)


__all__ = ["ComfyResult", "ComfySubmission", "ComfyUIRenderService", "ComfyUnavailable"]
