"""Dedicated managed ComfyUI renderer lifecycle for Manual4.

Isolation guarantees:
* Dedicated loopback port (default 8198+, never touching 7-stage port 8188).
* Dedicated runtime directories (``<m4_root>/comfy-runtime/``) and log file.
* Dedicated Windows Job Object namespace (``Local\\ShineM4Handoff-*``).
* Read-only model weight sharing from the machine's ComfyUI ``models/`` root.
* Platform GPU mutex + ``resource_leases`` (owner ``m4:...``) acquired before
  starting and released only after the owned process group is provably exited.
"""

from __future__ import annotations

import json
import os
import secrets
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Any
from uuid import uuid4

from fastapi import HTTPException

from shine.manual4.comfy.client import M4ComfyClient, M4ComfyUnavailable
from shine.manual4.comfy.owned_process import (
    M4OwnedProcess,
    file_sha256,
    launch_m4_owned_process,
    m4_group_active_processes,
    m4_pick_free_port,
    m4_process_identity,
    m4_terminate_named_group,
)
from shine.manual4.storage.files import sha256_bytes
from shine.manual4.storage.models import M4RendererState, m4_utcnow
from shine.platform_gpu import acquire_m4_gpu_lease, release_m4_gpu_lease


def resolve_m4_comfy_spec(settings, m4_root: Path) -> dict[str, Any]:
    """Discover the ComfyUI Python/root/adapter configuration read-only."""
    repo_root = Path(__file__).resolve().parents[4]
    recipe_file = Path(settings.data_dir) / "comfyops" / "setup" / "managed_recipe.json"
    env_recipe = os.getenv("SHINE_SETUP_RECIPE", "").strip()
    if env_recipe and Path(env_recipe).is_file():
        recipe_file = Path(env_recipe)

    if recipe_file.is_file():
        raw = json.loads(recipe_file.read_text(encoding="utf-8"))
        renderer = raw.get("renderer") or {}
        comfy_root = Path(str(renderer.get("root") or ""))
        python_exe = Path(str(renderer.get("python") or ""))
        adapter_dir = Path(str(renderer.get("adapter_dir") or ""))
        bootstrap = Path(str(renderer.get("bootstrap") or (repo_root / "scripts" / "managed_comfy_bootstrap.py")))
        if comfy_root.is_dir() and python_exe.is_file() and adapter_dir.is_dir():
            adapter_sha = renderer.get("adapter_sha256")
            if not adapter_sha and (adapter_dir / "__init__.py").is_file() and (adapter_dir / "barrier.py").is_file():
                import hashlib

                parts = [
                    hashlib.sha256((adapter_dir / "__init__.py").read_bytes()).digest(),
                    hashlib.sha256((adapter_dir / "barrier.py").read_bytes()).digest(),
                ]
                adapter_sha = hashlib.sha256(b"".join(parts)).hexdigest()
            return {
                "python": str(python_exe),
                "root": str(comfy_root),
                "models_dir": str(comfy_root / "models"),
                "adapter_dir": str(adapter_dir),
                "adapter_sha256": str(adapter_sha or ""),
                "bootstrap": str(bootstrap),
                "whitelist_nodes": [
                    "shine_comfy_handoff",
                    "shine_h3_sampling_mode.py",
                    "ComfyUI-KJNodes",
                    "ComfyUI-MiniMax-H3-PDD-Acc",
                    "ComfyUI-SolAttn_triton",
                    "Comfyui_Minimax_h3_latent_Upscaler",
                    "comfyui-videohelpersuite",
                ],
                "runtime_root": str(m4_root / "comfy-runtime"),
                "log_file": str(m4_root / "logs" / "comfy-m4.log"),
            }

    probe_roots = list(getattr(settings, "comfy_probe_roots", ()) or ())
    env_root = os.getenv("SHINE_M4_COMFY_ROOT", "").strip()
    if env_root:
        probe_roots.insert(0, env_root)
    for root_str in probe_roots:
        comfy_root = Path(root_str)
        if not (comfy_root / "main.py").is_file():
            continue
        python_exe = comfy_root.parent / "python_embeded" / "python.exe"
        adapter_dir = comfy_root / "custom_nodes" / "shine_comfy_handoff"
        bootstrap = repo_root / "scripts" / "managed_comfy_bootstrap.py"
        if python_exe.is_file() and adapter_dir.is_dir() and bootstrap.is_file():
            import hashlib

            parts = [
                hashlib.sha256((adapter_dir / "__init__.py").read_bytes()).digest(),
                hashlib.sha256((adapter_dir / "barrier.py").read_bytes()).digest(),
            ]
            return {
                "python": str(python_exe),
                "root": str(comfy_root),
                "models_dir": str(comfy_root / "models"),
                "adapter_dir": str(adapter_dir),
                "adapter_sha256": hashlib.sha256(b"".join(parts)).hexdigest(),
                "bootstrap": str(bootstrap),
                "whitelist_nodes": [
                    "shine_comfy_handoff",
                    "shine_h3_sampling_mode.py",
                    "ComfyUI-KJNodes",
                    "ComfyUI-MiniMax-H3-PDD-Acc",
                    "ComfyUI-SolAttn_triton",
                    "Comfyui_Minimax_h3_latent_Upscaler",
                    "comfyui-videohelpersuite",
                ],
                "runtime_root": str(m4_root / "comfy-runtime"),
                "log_file": str(m4_root / "logs" / "comfy-m4.log"),
            }
    raise HTTPException(
        409,
        {
            "code": "M4_COMFY_RUNTIME_NOT_CONFIGURED",
            "message": "未找到可用的本机 ComfyUI 与 H3 节点环境，请先确认 ComfyUI 目录与模型位置。",
        },
    )


def verify_model_file(
    models_dir: Path,
    folder: str,
    relative_name: str,
    expected_sha256: str | None = None,
) -> dict[str, Any]:
    rel = relative_name.replace("\\", "/")
    path = (models_dir / folder / rel).resolve()
    if not path.is_file():
        return {"exists": False, "path": str(path), "sha256": None, "matches": False}
    if expected_sha256:
        digest = file_sha256(path)
        return {"exists": True, "path": str(path), "sha256": digest, "matches": digest == expected_sha256}
    return {"exists": True, "path": str(path), "sha256": None, "matches": True}


def reconcile_m4_renderer(platform_factory, m4_factory, data_dir: Path) -> dict[str, Any]:
    """Release an expired Manual4 renderer/lease only after proving its process is gone."""
    with m4_factory.begin() as db:
        state = db.get(M4RendererState, "comfyui-m4")
        if state is None or state.status == "STOPPED":
            return {"reconciled": False, "status": "STOPPED"}
        alive = False
        if state.pid and state.birth:
            try:
                alive = m4_process_identity(state.pid) == state.birth
            except ValueError:
                alive = True
        active_members = m4_group_active_processes(state.job_name or "") if state.job_name else 0
        if alive or active_members > 0:
            raise HTTPException(
                409,
                {
                    "code": "M4_RENDERER_STILL_RUNNING",
                    "message": f"四阶 ComfyUI 渲染器仍在运行（PID {state.pid}），请先停止或等待其结束。",
                },
            )
        if state.lease_id and state.lease_owner:
            release_m4_gpu_lease(
                platform_factory, data_dir, lease_id=state.lease_id, owner=state.lease_owner, reconciled=True
            )
        state.status = "STOPPED"
        state.pid = None
        state.birth = None
        state.lease_id = None
        state.lease_owner = None
        state.updated_at = m4_utcnow()
        return {"reconciled": True, "status": "STOPPED"}


@contextmanager
def managed_m4_comfyui(
    platform_factory,
    m4_factory,
    settings,
    m4_root: Path,
    *,
    owner: str,
    startup_timeout_s: float = 300.0,
):
    """Acquire platform GPU lease, launch Manual4's isolated ComfyUI, yield client, then stop."""
    spec = resolve_m4_comfy_spec(settings, m4_root)
    lease = acquire_m4_gpu_lease(platform_factory, settings.data_dir, owner=owner, budget_mb=16384)
    lease_id = lease["lease_id"]

    runtime_root = Path(spec["runtime_root"])
    for sub in ("user", "input", "output", "temp"):
        (runtime_root / sub).mkdir(parents=True, exist_ok=True)
    Path(spec["log_file"]).parent.mkdir(parents=True, exist_ok=True)

    port = m4_pick_free_port(8198)
    url = f"http://127.0.0.1:{port}"
    token = secrets.token_urlsafe(32)
    job_name = f"Local\\ShineM4Handoff-{uuid4().hex}"

    argv = [
        spec["python"],
        spec["bootstrap"],
        "--listen", "127.0.0.1",
        "--port", str(port),
        "--user-directory", str(runtime_root / "user"),
        "--input-directory", str(runtime_root / "input"),
        "--output-directory", str(runtime_root / "output"),
        "--temp-directory", str(runtime_root / "temp"),
        "--disable-async-offload",
        "--disable-pinned-memory",
        "--reserve-vram", "1",
        "--fast", "fp16_accumulation",
        "--disable-all-custom-nodes",
        "--whitelist-custom-nodes",
        *spec["whitelist_nodes"],
    ]
    env = {
        **os.environ,
        "PYTHONDONTWRITEBYTECODE": "1",
        "HF_HUB_OFFLINE": "1",
        "TRANSFORMERS_OFFLINE": "1",
        "PIP_NO_INDEX": "1",
        "UV_NO_INDEX": "1",
        "SHINE_COMFY_HANDOFF_TOKEN": token,
        "SHINE_COMFY_START_DRAINED": "1",
        "SHINE_COMFY_BOOTSTRAP_ROOT": spec["root"],
        "SHINE_COMFY_ADAPTER_FILE": str(Path(spec["adapter_dir"]) / "__init__.py"),
        "SHINE_COMFY_EXPECTED_ADAPTER_SHA256": spec["adapter_sha256"],
        "SHINE_COMFY_LOG_FILE": spec["log_file"],
    }

    with m4_factory.begin() as db:
        row = db.get(M4RendererState, "comfyui-m4")
        if row is None:
            row = M4RendererState(id="comfyui-m4")
            db.add(row)
        row.status = "STARTING"
        row.port = port
        row.job_name = job_name
        row.url = url
        row.handoff_token_sha256 = sha256_bytes(token.encode("utf-8"))
        row.runtime_root = str(runtime_root)
        row.log_file = spec["log_file"]
        row.lease_id = lease_id
        row.lease_owner = owner
        row.updated_at = m4_utcnow()

    owned: M4OwnedProcess | None = None
    client = M4ComfyClient(url, timeout_s=30.0)
    try:
        owned = launch_m4_owned_process(argv, cwd=Path(spec["root"]), env=env, job_name=job_name)
        with m4_factory.begin() as db:
            row = db.get(M4RendererState, "comfyui-m4")
            row.pid = owned.pid
            row.birth = owned.birth
            row.updated_at = m4_utcnow()

        deadline = time.monotonic() + startup_timeout_s
        while time.monotonic() < deadline:
            try:
                client.system_stats()
                client.handoff_resume(token)
                break
            except Exception as exc:
                if owned.pid and m4_process_identity(owned.pid) is None:
                    raise HTTPException(
                        500,
                        {
                            "code": "M4_COMFY_EXITED_DURING_STARTUP",
                            "message": f"四阶 ComfyUI 启动过程中退出，请查看日志：{spec['log_file']}",
                        },
                    ) from exc
                time.sleep(0.75)
        else:
            raise HTTPException(504, {"code": "M4_COMFY_STARTUP_TIMEOUT", "message": "四阶 ComfyUI 启动超时"})

        with m4_factory.begin() as db:
            row = db.get(M4RendererState, "comfyui-m4")
            row.status = "RUNNING"
            row.updated_at = m4_utcnow()

        yield client, spec
    finally:
        stop_ok = False
        try:
            try:
                client.handoff_drain(token)
            except Exception:
                pass
            if owned is not None:
                res = m4_terminate_named_group(job_name, timeout_s=15.0)
                stop_ok = res.get("active_processes", 1) == 0
                if owned.job is not None:
                    owned.job.close()
        except Exception:
            stop_ok = False
        if stop_ok:
            release_m4_gpu_lease(platform_factory, settings.data_dir, lease_id=lease_id, owner=owner)
            with m4_factory.begin() as db:
                row = db.get(M4RendererState, "comfyui-m4")
                if row is not None:
                    row.status = "STOPPED"
                    row.pid = None
                    row.birth = None
                    row.lease_id = None
                    row.lease_owner = None
                    row.updated_at = m4_utcnow()
        else:
            with m4_factory.begin() as db:
                row = db.get(M4RendererState, "comfyui-m4")
                if row is not None:
                    row.status = "RECONCILE_REQUIRED"
                    row.updated_at = m4_utcnow()


def _probe_fallback() -> M4ComfyUnavailable:
    return M4ComfyUnavailable("unused")
