"""Media transfer adapters for ComfyUI reference inputs.

ComfyUI's stock HTTP API only exposes ``/upload/image``, but the concrete
request shape (multipart field name, subfolder, and the file extension a
downstream loader node expects) depends on the custom nodes the user actually
installed. Shine therefore models every media family with an explicit,
configurable transfer adapter instead of assuming one universal upload call.

Two rules are enforced by this module:

* The transfer for a media family is never guessed for video and audio. If the
  registered workflow profile does not declare how those families reach the
  H3 nodes, the render is blocked with ``PENDING_WORKFLOW_MAPPING`` instead of
  pretending the reference was delivered.
* The file extension is derived from the *content type* of the asset, never
  from the content-addressed storage key (which has no extension at all).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from shine.comfy.client import ComfyError

IMAGE = "image"
VIDEO = "video"
AUDIO = "audio"
FAMILIES = (IMAGE, VIDEO, AUDIO)

FAMILY_BY_PREFIX = {"image/": IMAGE, "video/": VIDEO, "audio/": AUDIO}

EXTENSION_BY_CONTENT_TYPE = {
    "image/png": ".png",
    "image/jpeg": ".jpg",
    "image/jpg": ".jpg",
    "image/webp": ".webp",
    "video/mp4": ".mp4",
    "video/quicktime": ".mov",
    "video/x-matroska": ".mkv",
    "video/webm": ".webm",
    "audio/wav": ".wav",
    "audio/x-wav": ".wav",
    "audio/mpeg": ".mp3",
    "audio/flac": ".flac",
    "audio/ogg": ".ogg",
}


class ComfyPendingWorkflowMapping(ComfyError):
    """The registered workflow cannot express a required reference yet."""

    code = "PENDING_WORKFLOW_MAPPING"


class ComfyReferenceError(ComfyError):
    """A reference is unusable for this request (type, hash, approval, count)."""

    code = "COMFY_REFERENCE_INVALID"


@dataclass(frozen=True)
class MediaTransfer:
    """How one media family is handed to ComfyUI for a specific workflow."""

    family: str
    upload_path: str
    field_name: str
    subfolder: str
    extension: str
    content_type: str
    required_node_classes: tuple[str, ...] = ()
    style: str = "multipart"  # "multipart" or "binary"

    def as_dict(self) -> dict[str, Any]:
        return {
            "family": self.family,
            "upload_path": self.upload_path,
            "field_name": self.field_name,
            "subfolder": self.subfolder,
            "extension": self.extension,
            "content_type": self.content_type,
            "required_node_classes": list(self.required_node_classes),
            "style": self.style,
        }


def family_for_content_type(content_type: str) -> str:
    for prefix, family in FAMILY_BY_PREFIX.items():
        if content_type.startswith(prefix):
            return family
    raise ComfyReferenceError(
        "The reference content type is not a supported media family",
        details={"content_type": content_type},
    )


def extension_for(content_type: str, original_filename: str | None = None) -> str:
    """Derive the upload extension from the content type.

    The content-addressed storage key has no extension, so the original
    filename is only used as a fallback when the content type is unknown.
    """
    normalized = (content_type or "").split(";")[0].strip().lower()
    extension = EXTENSION_BY_CONTENT_TYPE.get(normalized)
    if extension:
        return extension
    if original_filename and "." in original_filename:
        candidate = "." + original_filename.rsplit(".", 1)[-1].lower()
        if candidate in set(EXTENSION_BY_CONTENT_TYPE.values()):
            return candidate
    raise ComfyReferenceError(
        "No file extension could be derived for the reference content type",
        details={"content_type": content_type, "original_filename": original_filename},
    )


def image_transfer(content_type: str) -> MediaTransfer:
    """ComfyUI's documented default for images (LoadImage-compatible)."""
    return MediaTransfer(
        family=IMAGE,
        upload_path="/upload/image",
        field_name="image",
        subfolder="shine",
        extension=extension_for(content_type),
        content_type=content_type,
        required_node_classes=(),
        style="multipart",
    )


def transfer_from_profile(
    profile_transfers: dict[str, Any] | None,
    family: str,
    content_type: str,
) -> MediaTransfer:
    """Resolve the transfer for a family, honouring the workflow profile.

    Image has a documented ComfyUI default. Video and audio do not: their
    transport depends on the loader nodes the user installed, so an explicit
    profile declaration is required before Shine will upload them.
    """
    declared = (profile_transfers or {}).get(family)
    if declared:
        upload_path = str(declared.get("upload_path") or "").strip()
        if not upload_path.startswith("/"):
            raise ComfyPendingWorkflowMapping(
                "The declared media transfer has no usable upload path",
                details={"family": family, "declared": declared},
            )
        style = str(declared.get("style") or "multipart").strip().lower()
        if style not in {"multipart", "binary"}:
            raise ComfyPendingWorkflowMapping(
                "The declared media transfer uses an unsupported transport style",
                details={"family": family, "style": style},
            )
        field_name = str(declared.get("field_name") or "").strip()
        if style == "multipart" and not field_name:
            raise ComfyPendingWorkflowMapping(
                "The declared media transfer has no multipart field name",
                details={"family": family, "declared": declared},
            )
        declared_extension = str(declared.get("extension") or "").strip().lower()
        extension = declared_extension or extension_for(content_type)
        if not extension.startswith("."):
            extension = "." + extension
        return MediaTransfer(
            family=family,
            upload_path=upload_path,
            field_name=field_name,
            subfolder=str(declared.get("subfolder") or "shine"),
            extension=extension,
            content_type=content_type,
            required_node_classes=tuple(declared.get("required_node_classes") or ()),
            style=style,
        )
    if family == IMAGE:
        return image_transfer(content_type)
    raise ComfyPendingWorkflowMapping(
        (
            f"The workflow profile does not declare how {family} references are transferred to the H3 nodes. "
            "Configure a media transfer for this family before rendering."
        ),
        details={"family": family, "content_type": content_type},
    )


def check_transfer_capability(
    transfer: MediaTransfer,
    *,
    graph_class_types: set[str],
    object_info: dict[str, Any] | None = None,
) -> None:
    """Fail loudly when the declared loader nodes are not installed."""
    if not transfer.required_node_classes:
        return
    missing_in_graph = sorted(
        node_class for node_class in transfer.required_node_classes if node_class not in graph_class_types
    )
    if missing_in_graph:
        raise ComfyPendingWorkflowMapping(
            "The workflow graph does not contain the loader nodes required by the declared media transfer",
            details={
                "family": transfer.family,
                "missing_node_classes": missing_in_graph,
                "graph_class_types": sorted(graph_class_types),
            },
        )
    if object_info:
        missing_on_server = sorted(
            node_class
            for node_class in transfer.required_node_classes
            if node_class not in object_info
        )
        if missing_on_server:
            raise ComfyPendingWorkflowMapping(
                "ComfyUI does not expose the loader nodes required by the declared media transfer",
                details={"family": transfer.family, "missing_node_classes": missing_on_server},
            )


__all__ = [
    "AUDIO",
    "FAMILIES",
    "IMAGE",
    "VIDEO",
    "ComfyPendingWorkflowMapping",
    "ComfyReferenceError",
    "MediaTransfer",
    "check_transfer_capability",
    "extension_for",
    "family_for_content_type",
    "image_transfer",
    "transfer_from_profile",
]
