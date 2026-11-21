from __future__ import annotations

import hashlib
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from shine.comfy.client import (
    ComfyError,
    ComfyNodeUnavailable,
    ComfyPromptRejected,
    ComfyUIClient,
    ComfyUIError,
    ComfyUnavailable,
    ComfyWorkflowVersionMismatch,
)
from shine.comfy.media import (
    ComfyPendingWorkflowMapping,
    ComfyReferenceError,
    MediaTransfer,
    check_transfer_capability,
    extension_for,
    family_for_content_type,
    transfer_from_profile,
)
from shine.comfy.workflow import (
    apply_parameters,
    generic_h3_parameters,
    graph_hash,
    prune_unused_reference_nodes,
    validate_against_object_info,
    validate_graph,
)
from shine.media.artifacts import content_type_for_name, safe_asset_path
from shine.models import Asset
from shine.schemas import RenderProvider, RenderRequest
from shine.security import sha256_bytes

REQUIRED_LOGICAL_PARAMETERS = ("positive_prompt", "duration")


@dataclass(frozen=True)
class PreparedSubmission:
    """A fully prepared ComfyUI prompt, ready to be queued.

    The prompt graph hash is computed *before* the external call so the
    submission intent can be persisted first. If the request is then accepted
    but the response is lost, the hash is enough to find the original task in
    ComfyUI's history without ever submitting a second one.
    """

    prompt: dict[str, Any]
    prompt_graph_sha256: str
    workflow_hash: str
    uploaded_references: int


@dataclass(frozen=True)
class ComfySubmission:
    prompt_id: str
    workflow_hash: str
    uploaded_references: int
    prompt_graph_sha256: str = ""
    idempotency_key: str = ""


@dataclass(frozen=True)
class ComfyResult:
    status: str
    output_filename: str | None = None
    subfolder: str = ""
    content: bytes | None = None
    content_type: str | None = None
    error: str | None = None
    error_code: str | None = None


def prepare_workflow_submission(
    profile, *, prompt: str, seed: int | None, duration_s: float,
    references: dict[str, str], width: int | None, height: int | None,
) -> PreparedSubmission:
    """Materialize the exact graph both production lines submit."""
    parameter_map = profile.parameter_map
    values = generic_h3_parameters(
        prompt=prompt, seed=seed, duration_s=duration_s, references=references, width=width, height=height,
    )
    mapped = {key: value for key, value in values.items() if key in parameter_map}
    if "positive_prompt" in parameter_map and not mapped.get("positive_prompt"):
        raise ComfyUIError("The workflow profile does not accept the compiled H3 prompt")
    graph = apply_parameters(profile.graph, parameter_map, mapped)
    # Frozen static profiles can have more loaders than this request binds.
    used_loaders = {parameter_map[key].split(".", 1)[0] for key in references}
    graph = prune_unused_reference_nodes(graph, used_loaders)
    return PreparedSubmission(
        prompt=graph,
        prompt_graph_sha256=graph_hash(graph),
        workflow_hash=graph_hash(profile.graph),
        uploaded_references=len(references),
    )


def submit_prepared_prompt(
    client: ComfyUIClient,
    prepared: PreparedSubmission,
    *,
    idempotency_key: str,
    prompt_sha256: str,
    extra_data: dict[str, Any] | None = None,
) -> ComfySubmission:
    """Submit exactly once after the caller has committed the graph intent."""
    metadata = dict(extra_data or {})
    metadata["shine"] = {
        **(metadata.get("shine") or {}),
        "idempotency_key": idempotency_key,
        "prompt_sha256": prompt_sha256,
        "prompt_graph_sha256": prepared.prompt_graph_sha256,
        "provider": RenderProvider.COMFYUI.value,
    }
    body = client.queue_prompt(prepared.prompt, extra_data=metadata)
    return ComfySubmission(
        prompt_id=str(body["prompt_id"]),
        workflow_hash=prepared.workflow_hash,
        uploaded_references=prepared.uploaded_references,
        prompt_graph_sha256=prepared.prompt_graph_sha256,
        idempotency_key=idempotency_key,
    )


def find_output(
    record: dict[str, Any],
    families: tuple[str, ...] = ("gifs", "videos", "images", "audio"),
) -> dict[str, Any] | None:
    outputs = record.get("outputs")
    if not isinstance(outputs, dict):
        return None
    for node_output in outputs.values():
        if not isinstance(node_output, dict):
            continue
        for family in families:
            items = node_output.get(family) or []
            if items and isinstance(items[0], dict) and items[0].get("filename"):
                return items[0]
    return None


def _history_outcome(record: dict[str, Any]) -> tuple[str, dict[str, Any] | None]:
    status = record.get("status")
    if isinstance(status, dict) and status.get("status_str") == "error":
        return "failed", None
    output = find_output(record)
    return ("completed", output) if output else ("completed_without_output", None)


def _queue_entries(queue):
    if isinstance(queue, dict):
        for key, state in (("queue_running", "running"), ("queue_pending", "pending")):
            for item in queue.get(key) or []:
                if isinstance(item, list | tuple) and len(item) > 1:
                    yield item, state


def reconcile_submission(
    client: ComfyUIClient,
    *,
    prompt_id: str | None,
    prompt_graph_sha256: str | None = None,
    idempotency_key: str | None = None,
    legacy_job_id: str | None = None,
) -> tuple[str | None, str, dict[str, Any] | None]:
    """Recover an ACK by identity using ComfyUI history and queue. Never submit."""
    if not (prompt_id or prompt_graph_sha256):
        return None, "not_found", None

    def matches(graph, extra_data) -> bool:
        if not isinstance(graph, dict) or graph_hash(graph) != prompt_graph_sha256:
            return False
        if not idempotency_key:
            return True
        metadata = extra_data if isinstance(extra_data, dict) else {}
        shine = metadata.get("shine")
        if isinstance(shine, dict) and shine.get("idempotency_key"):
            return shine["idempotency_key"] == idempotency_key
        # Pre-convergence Manual4 submissions carried the business job ID at top level.
        return bool(
            legacy_job_id and metadata.get("shine_line") == "manual4" and metadata.get("job_id") == legacy_job_id
        )

    try:
        history = client.history(prompt_id) if prompt_id else client.history()
        if not isinstance(history, dict):
            return None, "unavailable:COMFY_PROVIDER_ERROR", None
        if prompt_id:
            record = history.get(prompt_id)
            if isinstance(record, dict):
                return prompt_id, _history_outcome(record)[0], record
        else:
            found = []
            for candidate_id, record in history.items():
                if not isinstance(record, dict):
                    continue
                graph, extra_data = record.get("prompt"), record.get("extra_data")
                if isinstance(graph, list | tuple) and len(graph) >= 3:
                    graph, extra_data = graph[2], graph[3] if len(graph) > 3 else extra_data
                if matches(graph, extra_data):
                    found.append((str(candidate_id), _history_outcome(record)[0], record))
            if len(found) > 1:
                return None, "ambiguous", None
            if found:
                return found[0]
        queued = {}
        for item, state in _queue_entries(client.queue()):
            candidate_id = str(item[1])
            if prompt_id:
                matched = candidate_id == prompt_id
            else:
                matched = len(item) >= 3 and matches(item[2], item[3] if len(item) > 3 else None)
            if matched:
                queued.setdefault(candidate_id, state)
        if len(queued) > 1:
            return None, "ambiguous", None
        if queued:
            matched_id, state = next(iter(queued.items()))
            return matched_id, state, None
    except ComfyError as exc:
        return None, f"unavailable:{exc.code}", None
    return None, "not_found", None


def graph_class_types(graph: dict[str, Any]) -> set[str]:
    return {
        str(node.get("class_type"))
        for node in (graph or {}).values()
        if isinstance(node, dict) and node.get("class_type")
    }


def reference_slots(manifest) -> list[tuple[str, Any]]:
    """Map every manifest entry onto its deterministic logical workflow slot."""
    counters: dict[str, int] = {}
    slots: list[tuple[str, Any]] = []
    for item in manifest:
        family = item.use_as.value
        counters[family] = counters.get(family, 0) + 1
        slots.append((f"reference_{family}_{counters[family]}", item))
    return slots


def resolve_reference_slots(parameter_map: dict[str, str], manifest) -> list[tuple[str, str, Any]]:
    """Pair every reference with the parameter that actually carries it.

    A profile can declare a slot per reference family (``reference_subject_1``,
    ``reference_picture_1``) or one positional image sequence
    (``reference_image_1..n``).  The positional form is what lets one profile
    carry any cast composition: H3 numbers every image in a single sequence, and
    the graph wires its image loaders in that same order, so a two-hander and a
    one-hander are the same request with a different number of images.  Without
    it, every cast mix needed its own hand-made profile, and a shot naming two
    people was refused by the only profiles a project had.

    The family name stays the logical identity (it is what every message and
    test names); only the parameter that carries it changes.
    """
    counters: dict[str, int] = {}
    image_position = 0
    resolved: list[tuple[str, str, Any]] = []
    for item in manifest:
        family = item.use_as.value
        counters[family] = counters.get(family, 0) + 1
        logical = f"reference_{family}_{counters[family]}"
        key = logical
        if family in {"subject", "picture"}:
            image_position += 1
            if key not in parameter_map:
                positional = f"reference_image_{image_position}"
                if positional in parameter_map:
                    key = positional
        resolved.append((logical, key, item))
    return resolved


def validate_required_parameters(parameter_map: dict[str, str], *, needs_seed: bool) -> None:
    missing = [name for name in REQUIRED_LOGICAL_PARAMETERS if name not in parameter_map]
    if needs_seed and "seed" not in parameter_map:
        missing.append("seed")
    if missing:
        raise ComfyPendingWorkflowMapping(
            "The workflow profile does not map every required generation parameter",
            details={"missing_parameters": missing, "mapped_parameters": sorted(parameter_map)},
        )


def validate_reference_slots(parameter_map: dict[str, str], manifest) -> list[tuple[str, Any]]:
    """Every reference in this request must have a workflow slot.

    Silently dropping a reference is forbidden: the render would silently lose
    a user-approved asset.
    """
    slots = resolve_reference_slots(parameter_map, manifest)
    missing = [logical for logical, key, _item in slots if key not in parameter_map]
    if missing:
        raise ComfyPendingWorkflowMapping(
            "The workflow profile has no mapping for one or more required references",
            details={
                "missing_reference_slots": missing,
                "mapped_parameters": sorted(parameter_map),
                "reference_count": len(slots),
            },
        )
    return slots


def collect_unbound_transfer_families(profile_transfers, manifest, assets) -> list[str]:
    """Report the declared media families this request does not bind.

    The workflow graph holds a loader node for every declared family.  When a
    request binds no reference of a declared family, the submitted graph
    deterministically prunes that unused loader (``prune_unused_reference_nodes``),
    so the render can still proceed — this is a warning, not a refusal.

    The audio family is exempt by product decision: a voice master is optional
    and H3 speaks a dialogue line natively ("音色可选、H3 自己说话").  Forcing a
    human-voice reference onto a shot made H3 treat it as a speech cue even
    against the no-speech contract — observed as foreign-language muttering on
    the real 2026-11-04 journey run.  An audio reference is therefore never
    required, for dialogue and dialogue-free shots alike; a bound one is still
    used, and the unused audio loader is pruned either way.

    The image family follows the same product line (R09): a missing picture is
    generated by H3, so an image family with no bound reference is a warning
    too.  A bound picture is never dropped: a reference that exists is always
    submitted — the reference-slot gate (``validate_reference_slots``) stays a
    refusal, because it would silently lose a user-approved asset.
    """
    if not profile_transfers:
        return []
    families: set[str] = set()
    for item in manifest:
        asset = assets.get(item.asset_id) if isinstance(assets, dict) else None
        if asset is None:
            continue
        try:
            families.add(family_for_content_type(asset.content_type))
        except Exception:
            continue
    return sorted(family for family in profile_transfers if family not in families)


def unbound_transfer_family_warning(family: str) -> str:
    """The user-facing warning line for one unbound declared family."""
    if family == "audio":
        return (
            "workflow-media-family-unbound: the profile declares an audio transfer but this shot "
            "binds no voice reference. Voice is optional - H3 speaks the dialogue natively, and "
            "the unused audio loader is pruned from the submitted graph."
        )
    return (
        f"workflow-media-family-unbound: the profile declares a {family} transfer but this shot "
        f"binds no {family} reference. The unused loader is pruned and the shot still renders; "
        "bind one if the render should follow that reference."
    )


class ComfyUIRenderService:
    """Maps an immutable Shine RenderRequest onto a registered API workflow."""

    def __init__(self, client, asset_root: Path):
        self.client = client
        self.asset_root = asset_root

    def _profile_graph(self, profile) -> dict[str, Any]:
        graph = profile.graph or {}
        validate_graph(graph, required_nodes=profile.required_nodes)
        return graph

    @staticmethod
    def _check_profile_compatibility(profile) -> None:
        """Refuse a profile that the live ComfyUI already proved incompatible.

        The stored validation is refreshed by ``/comfy/capabilities`` and
        ``POST /workflow-profiles/{id}/validate``. A missing node class and a
        changed node definition are different failures and are reported apart.
        """
        validation = getattr(profile, "validated_against", None) or {}
        missing_nodes = list(validation.get("missing_nodes") or [])
        missing_inputs = list(validation.get("missing_inputs") or [])
        if missing_nodes:
            raise ComfyNodeUnavailable(
                "The workflow needs node classes that this ComfyUI does not provide",
                details={"missing_nodes": sorted(set(missing_nodes))},
            )
        if missing_inputs:
            raise ComfyWorkflowVersionMismatch(
                "The installed node definition no longer matches the registered parameter mapping",
                details={"missing_inputs": missing_inputs[:20]},
            )

    def _profile_transfers(self, profile) -> dict[str, Any]:
        return getattr(profile, "media_transfers", None) or {}

    def _resolve_transfer(
        self,
        profile,
        *,
        family: str,
        content_type: str,
        graph_class_types_: set[str],
        object_info: dict[str, Any] | None,
    ) -> MediaTransfer:
        transfer = transfer_from_profile(self._profile_transfers(profile), family, content_type)
        check_transfer_capability(
            transfer,
            graph_class_types=graph_class_types_,
            object_info=object_info,
        )
        return transfer

    def _verify_asset_for_upload(
        self,
        item,
        asset: Asset,
        *,
        project_id: str | None,
    ) -> bytes:
        """Re-check the master asset right before it leaves the machine."""
        from shine.media.continuity import ContinuityReference

        derived = isinstance(asset, ContinuityReference)
        if derived and item.role != "continuity_derivative":
            raise ComfyReferenceError("A derived continuity reference has an invalid manifest role")
        if derived:
            source = asset.provenance["source"]
            source_path = safe_asset_path(self.asset_root, source["storage_path"])
            if not source_path.is_file():
                raise ComfyReferenceError("The continuity source is missing")
            with source_path.open("rb") as stream:
                if hashlib.file_digest(stream, "sha256").hexdigest() != source["sha256"]:
                    raise ComfyReferenceError("The continuity source changed after derivation")
        if not derived and not asset.approved:
            raise ComfyReferenceError(
                "A reference asset is not approved by the user",
                details={"asset_id": asset.id, "label": item.label},
            )
        if not derived and not asset.locked:
            raise ComfyReferenceError(
                "A reference asset is not locked by the user",
                details={"asset_id": asset.id, "label": item.label},
            )
        if project_id and asset.project_id != project_id:
            raise ComfyReferenceError(
                "A reference asset belongs to another project",
                details={"asset_id": asset.id, "label": item.label},
            )
        # The manifest path is a historical storage location; the asset's
        # SHA-256 is its identity.  An output-root change may have moved the
        # file, so the path is not part of this equality.
        if (asset.sha256 != item.sha256 or asset.content_type != item.content_type):
            raise ComfyReferenceError(
                "The reference manifest hash does not match the current master asset",
                details={"asset_id": asset.id, "label": item.label},
            )
        path = safe_asset_path(self.asset_root, asset.storage_path)
        if not path.is_file():
            raise ComfyReferenceError(
                "A reference asset file is missing on disk",
                details={"asset_id": asset.id, "label": item.label},
            )
        content = path.read_bytes()
        digest = sha256_bytes(content)
        if digest != asset.sha256:
            raise ComfyReferenceError(
                "The reference asset file changed on disk after it was approved",
                details={"asset_id": asset.id, "label": item.label, "expected": asset.sha256, "actual": digest},
            )
        expected_family = family_for_content_type(asset.content_type)
        declared_family = {"subject": "image", "picture": "image"}.get(item.use_as.value, item.use_as.value)
        if expected_family != declared_family:
            raise ComfyReferenceError(
                "A reference asset media type does not match the role it is bound to",
                details={
                    "asset_id": asset.id,
                    "label": item.label,
                    "use_as": item.use_as.value,
                    "content_type": asset.content_type,
                },
            )
        return content

    def _upload_references(
        self,
        request: RenderRequest,
        assets: dict[str, Asset],
        parameter_map: dict[str, str],
        *,
        project_id: str | None = None,
        graph_class_types_: set[str] | None = None,
        object_info: dict[str, Any] | None = None,
        profile=None,
    ) -> dict[str, str]:
        uploaded: dict[str, str] = {}
        graph_types = graph_class_types_ or set()
        for _logical, parameter_key, item in resolve_reference_slots(parameter_map, request.reference_manifest):
            asset = assets.get(item.asset_id)
            if asset is None:
                raise ComfyReferenceError(
                    "A manifest asset is missing from the local asset registry",
                    details={"label": item.label, "asset_id": item.asset_id},
                )
            content = self._verify_asset_for_upload(item, asset, project_id=project_id)
            family = family_for_content_type(asset.content_type)
            transfer = (
                self._resolve_transfer(
                    profile,
                    family=family,
                    content_type=asset.content_type,
                    graph_class_types_=graph_types,
                    object_info=object_info,
                )
                if profile is not None
                else transfer_from_profile({}, family, asset.content_type)
            )
            extension = extension_for(asset.content_type, asset.filename)
            upload_name = f"shine_{asset.id}{extension}"
            response = self.client.upload_file(
                upload_name,
                content,
                upload_path=transfer.upload_path,
                field_name=transfer.field_name,
                subfolder=transfer.subfolder,
                content_type=asset.content_type,
                style=transfer.style,
            )
            name = response.get("name")
            if not name:
                raise ComfyUIError(
                    "ComfyUI upload response did not include a filename",
                    details={"label": item.label, "asset_id": asset.id},
                )
            returned_suffix = Path(str(name)).suffix.lower()
            if returned_suffix and returned_suffix != extension.lower():
                raise ComfyUIError(
                    "ComfyUI stored the reference under an unexpected file name",
                    details={
                        "label": item.label,
                        "asset_id": asset.id,
                        "expected_extension": extension,
                        "returned_name": str(name),
                    },
                )
            subfolder = response.get("subfolder") or ""
            uploaded[parameter_key] = f"{subfolder}/{name}" if subfolder else str(name)
        if len(uploaded) != len(request.reference_manifest):
            raise ComfyReferenceError(
                "Not every reference in the manifest was transferred to ComfyUI",
                details={
                    "manifest_count": len(request.reference_manifest),
                    "uploaded_count": len(uploaded),
                },
            )
        return uploaded

    def prepare(
        self,
        request: RenderRequest,
        profile,
        assets: dict[str, Asset],
        *,
        seed: int | None = None,
        project_id: str | None = None,
        object_info: dict[str, Any] | None = None,
    ) -> PreparedSubmission:
        """Upload the references and build the exact prompt graph to submit."""
        graph = self._profile_graph(profile)
        from shine.comfy.capabilities import continuity_capability, profile_metadata

        capability = continuity_capability(request.shot_spec.continuity.mode.value)
        capabilities = profile_metadata(
            graph, request.generation_mode.value, profile.parameter_map, self._profile_transfers(profile)
        )
        if capability and capability not in capabilities["continuity_modes"]:
            raise ComfyPendingWorkflowMapping(
                "Continuity capability is not verified for this workflow",
                details={"requested_continuity": capability},
            )
        self._check_profile_compatibility(profile)
        class_types = graph_class_types(graph)
        validate_required_parameters(profile.parameter_map, needs_seed=seed is not None)
        validate_reference_slots(profile.parameter_map, request.reference_manifest)
        uploaded = self._upload_references(
            request,
            assets,
            profile.parameter_map,
            project_id=project_id,
            graph_class_types_=class_types,
            object_info=object_info,
            profile=profile,
        )
        return prepare_workflow_submission(
            profile,
            prompt=request.compiled_prompt or "",
            seed=seed,
            duration_s=request.expected_duration_s,
            references=uploaded,
            width=request.output_width,
            height=request.output_height,
        )

    def submit_prepared(
        self,
        prepared: PreparedSubmission,
        request: RenderRequest,
    ) -> ComfySubmission:
        """Queue a prepared prompt. The caller must persist the intent first."""
        return submit_prepared_prompt(
            self.client,
            prepared,
            idempotency_key=request.idempotency_key,
            prompt_sha256=request.prompt_sha256,
            extra_data={
                "shine": {
                    "job_id": request.shot_id,
                    "style_version_id": request.style_version_id,
                }
            },
        )

    def submit(
        self,
        request: RenderRequest,
        profile,
        assets: dict[str, Asset],
        *,
        seed: int | None = None,
        project_id: str | None = None,
        object_info: dict[str, Any] | None = None,
    ) -> ComfySubmission:
        prepared = self.prepare(
            request,
            profile,
            assets,
            seed=seed,
            project_id=project_id,
            object_info=object_info,
        )
        return self.submit_prepared(prepared, request)

    def _queue_state(self, prompt_id: str) -> str:
        for item, state in _queue_entries(self.client.queue()):
            if str(item[1]) == prompt_id:
                return state
        return "missing"

    def poll(self, prompt_id: str) -> ComfyResult:
        history = self.client.history(prompt_id)
        record = history.get(prompt_id) if isinstance(history, dict) else None
        if isinstance(record, dict):
            state, output = _history_outcome(record)
            if state == "failed":
                return ComfyResult(
                    status="failed",
                    error="ComfyUI reported an execution error",
                    error_code="COMFY_EXECUTION_ERROR",
                )
            if output:
                filename = output["filename"]
                subfolder = output.get("subfolder") or ""
                file_type = output.get("type") or "output"
                content = self.client.view(filename, subfolder=subfolder, file_type=file_type)
                return ComfyResult(
                    status="completed",
                    output_filename=filename,
                    subfolder=subfolder,
                    content=content,
                    content_type=content_type_for_name(filename),
                )
            return ComfyResult(
                status="completed_without_output",
                error="ComfyUI finished the prompt without producing an output file",
                error_code="COMFY_OUTPUT_MISSING",
            )
        queue_state = self._queue_state(prompt_id)
        if queue_state in {"running", "pending"}:
            return ComfyResult(status=queue_state)
        return ComfyResult(
            status="not_found",
            error="ComfyUI has no record of this prompt",
            error_code="COMFY_JOB_NOT_FOUND",
        )

    def wait(self, prompt_id: str, *, timeout_s: float = 120.0, interval_s: float = 0.2) -> ComfyResult:
        deadline = time.monotonic() + timeout_s
        while True:
            result = self.poll(prompt_id)
            if result.status not in {"running", "pending"}:
                return result
            if time.monotonic() >= deadline:
                return ComfyResult(status="timeout", error_code="COMFY_TIMEOUT")
            time.sleep(interval_s)

    def reconcile(self, prompt_id: str) -> str:
        try:
            result = self.poll(prompt_id)
        except ComfyError as exc:
            return f"unavailable:{exc.code}"
        return result.status

    def find_submission(
        self,
        *,
        prompt_id: str | None,
        prompt_graph_sha256: str | None,
        idempotency_key: str | None = None,
    ) -> tuple[str | None, str]:
        """Look for an existing ComfyUI submission that matches this request."""
        matched_id, status, _rec = reconcile_submission(
            self.client,
            prompt_id=prompt_id,
            prompt_graph_sha256=prompt_graph_sha256,
            idempotency_key=idempotency_key,
        )
        return matched_id, status

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


def request_fingerprint(request: RenderRequest) -> str:
    return hashlib.sha256(request.idempotency_key.encode("utf-8")).hexdigest()


__all__ = [
    "ComfyPendingWorkflowMapping",
    "ComfyPromptRejected",
    "ComfyReferenceError",
    "ComfyResult",
    "ComfySubmission",
    "ComfyUIRenderService",
    "ComfyUnavailable",
    "graph_class_types",
    "reference_slots",
    "validate_reference_slots",
    "validate_required_parameters",
]
