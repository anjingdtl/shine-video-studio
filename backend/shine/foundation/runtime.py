"""Unified GPU arbitration and owned-process lifecycle primitives for both lines."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from fastapi import HTTPException
from sqlalchemy.orm import sessionmaker

from shine.comfyops.image_renderer import retire_idle_image_renderer
from shine.comfyops.installer import process_identity
from shine.comfyops.owned_process import (
    OwnedProcessGroup,
    inspect_named_group,
    named_group_active_processes,
    terminate_named_group,
)
from shine.comfyops.renderer_supervisor import windows_loopback_listener_pid, windows_port_listeners
from shine.platform_gpu import (
    acquire_m4_gpu_lease,
    heartbeat_m4_gpu_lease,
    inspect_gpu_status,
    release_m4_gpu_lease,
)
from shine.runtime_orchestrator.guard import renderer_lifecycle_guard


def retire_idle_director7_image_renderer(
    platform_factory: sessionmaker,
    settings: Any,
    installation_id: str | None,
) -> dict[str, Any] | None:
    """Safely yield an idle director7 image renderer before Manual4 starts ComfyUI.

    Never interrupts an active director7 image render: if image work is in flight,
    raises ``409 GPU_BUSY_OTHER_LINE``.
    """
    if not installation_id:
        return None
    try:
        with renderer_lifecycle_guard(installation_id):
            return retire_idle_image_renderer(
                platform_factory, settings, installation_id, actor="manual4:gpu-coordination"
            )
    except HTTPException as exc:
        detail = exc.detail
        code = detail.get("code") if isinstance(detail, dict) else str(detail)
        if code in {
            "IMAGE_RENDERER_BUSY_TASK_IN_FLIGHT",
            "IMAGE_RENDERER_BUSY_LEASE_ACTIVE",
            "IMAGE_RENDERER_START_IN_PROGRESS",
        }:
            raise HTTPException(
                409,
                {
                    "code": "GPU_BUSY_OTHER_LINE",
                    "message": "七阶 AI 导演线正在使用出图渲染器，请等待其结束后再启动四阶任务。",
                    "blocking_line": "director7",
                    "blocking_owner": "image:active",
                    "blocking_status": "ACTIVE",
                },
            ) from exc
        raise


def launch_owned_comfyui_group(
    argv: list[str],
    *,
    cwd: Path,
    env: dict[str, str],
    pinned_files: list[str],
    job_name: str,
) -> tuple[OwnedProcessGroup, str]:
    """Launch ComfyUI inside the shared Windows Job Object + source-file locks."""
    group = OwnedProcessGroup.launch(
        argv,
        cwd=cwd,
        env=env,
        pinned_files=pinned_files,
        job_name=job_name,
    )
    birth = process_identity(group.pid)
    if not birth:
        group.terminate(timeout=10)
        raise HTTPException(500, {"code": "M4_COMFY_IDENTITY_MISSING", "message": "无法确认受管 ComfyUI 进程身份。"})
    return group, birth


def verify_loopback_listener_ownership(port: int, group: OwnedProcessGroup, birth: str) -> None:
    """Confirm the loopback listener on ``port`` belongs strictly to ``group``."""
    if os.name != "nt":
        return
    listener_pid = windows_loopback_listener_pid(port)
    if listener_pid != group.pid or not group.owns(listener_pid, birth):
        raise ValueError("M4_COMFY_LISTENER_OWNERSHIP_UNVERIFIED")


__all__ = [
    "OwnedProcessGroup",
    "acquire_m4_gpu_lease",
    "heartbeat_m4_gpu_lease",
    "inspect_gpu_status",
    "inspect_named_group",
    "launch_owned_comfyui_group",
    "named_group_active_processes",
    "process_identity",
    "release_m4_gpu_lease",
    "retire_idle_director7_image_renderer",
    "terminate_named_group",
    "verify_loopback_listener_ownership",
    "windows_port_listeners",
]
