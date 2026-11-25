"""Single authoritative environment facts, capability verdict & shared launch builder.

Both ``director7`` and ``manual4`` read the exact same machine facts derived
from ``comfyops.setup_summary``, ``comfyops.runtime_configuration``,
``comfyops.managed_recipe`` and ``comfyops.resource_resolver``. Missing items
are prepared exclusively through the single Setup Worker
(``comfyops.setup_preparation`` / ``comfyops.setup_reconcile``).
"""

from __future__ import annotations

import hashlib
import os
import shutil
from pathlib import Path
from typing import Any

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from shine.comfyops.managed_recipe import adapter_identity, build_renderer_launch
from shine.comfyops.models import ComfyInstance
from shine.comfyops.runtime_configuration import (
    load_runtime_recipe,
    runtime_report,
    service_evidence_status,
)
from shine.comfyops.setup_preparation import enqueue as enqueue_setup_preparation
from shine.comfyops.setup_summary import summary as setup_summary
from shine.comfyops.storage import prepare_process_environment, storage_root
from shine.models import WorkflowProfile, utcnow


def h3_strategy_options(db: Session | None = None, settings: Any | None = None) -> dict[str, Any]:
    """Unified production option matrix facade shared by 7-stage and 4-stage."""
    from shine.strategy import strategy_options

    return strategy_options(db, settings)


def h3_shot_reference_blockers(
    db: Session | None,
    settings: Any | None = None,
    *,
    audio_count: int = 0,
    video_count: int = 0,
) -> list[dict[str, Any]]:
    """Per-shot optional loader blocker facade delegating to ``shine.strategy``."""
    from shine.strategy import shot_reference_capability_blockers

    return shot_reference_capability_blockers(
        db, settings, audio_count=audio_count, video_count=video_count
    )


def h3_strategy_blockers(
    *,
    variant_id: str,
    accelerator_id: str,
    second_pass: dict[str, Any] | None = None,
    style_loras: list[dict[str, Any]] | None = None,
    db: Session | None = None,
    settings: Any | None = None,
) -> list[dict[str, Any]]:
    """Validate strategy availability through the single ``strategy_blockers`` adjudicator."""
    from shine.strategy import GenerationStrategy, SecondPassConfig, StyleLoraConfig, strategy_blockers

    strategy_obj = GenerationStrategy(
        model_variant_id=variant_id,
        accelerator_id=accelerator_id,
        second_pass=SecondPassConfig(
            enabled=bool(second_pass and second_pass.get("enabled")),
            denoise=float((second_pass or {}).get("denoise", 0.25)),
            latent_scale=float((second_pass or {}).get("latent_scale", 1.0)),
        ),
        style_loras=[StyleLoraConfig.model_validate(item) for item in (style_loras or [])],
    )
    return strategy_blockers(strategy_obj, db, settings)


def environment_snapshot(
    db: Session,
    settings: Any,
    *,
    variant_id: str | None = None,
) -> dict[str, Any]:
    """Return the unified read-only machine environment snapshot (shared across lines)."""
    try:
        report = runtime_report(db, settings)
    except Exception:
        report = {}
    snap = setup_summary(db, settings, project_id=None, variant_id=variant_id, report=report)
    selected = (snap.get("installation") or {}).get("selected") or {}
    evidence = report.get("service_validation")
    if evidence is None:
        try:
            evidence = service_evidence_status(db, settings)
        except Exception:
            evidence = {"status": "PENDING", "blockers": ["SERVICE_VALIDATION_UNAVAILABLE"]}

    recipe_meta: dict[str, Any] = {}
    try:
        recipe, recipe_sha = load_runtime_recipe(settings)
        renderer = recipe.get("renderer") or {}
        recipe_meta = {
            "recipe_sha256": recipe_sha,
            "instance_id": renderer.get("instance_id"),
            "root": renderer.get("root"),
            "python": renderer.get("python"),
            "adapter_dir": renderer.get("adapter_dir"),
            "port": renderer.get("port"),
            "node_pack_dirs": dict(renderer.get("node_pack_dirs") or {}),
        }
    except Exception:
        recipe_meta = {}

    workflows = [
        {"id": wf.id, "name": wf.name, "mode": wf.mode, "checkpoint_id": wf.checkpoint_id}
        for wf in db.scalars(select(WorkflowProfile).order_by(WorkflowProfile.name)).all()
    ]
    caps = snap.get("capabilities") or {}
    return {
        "schema": "shine.environment.v1",
        "checked_at": snap.get("computed_at") or (utcnow().isoformat() + "Z"),
        "snapshot_version": snap.get("snapshot_version", 1),
        "comfyops_dir": str(storage_root(settings.data_dir)),
        "configuration_sha256": report.get("configuration_sha256"),
        "runtime_status": report.get("status"),
        "render_provider": report.get("render_provider"),
        "services_verified": bool(report.get("services_verified")),
        "installation": snap.get("installation"),
        "comfyui_root": recipe_meta.get("root") or selected.get("root"),
        "python": recipe_meta.get("python"),
        "custom_nodes": recipe_meta.get("node_pack_dirs") or {},
        "recipe": recipe_meta,
        "service_validation": evidence,
        "workflows": workflows,
        "capabilities": {
            key: {
                "key": key,
                "label": row.get("label"),
                "state": row["state"],
                "optional": bool(row.get("optional")),
                "facts": row.get("facts") or {},
                "summary": row.get("summary"),
                "missing_bytes": row.get("missing_bytes", 0),
                "missing_files_count": len(row.get("missing_files") or []),
                "next_step": row.get("next_step"),
            }
            for key, row in caps.items()
        },
        "optional_capabilities": snap.get("optional_capabilities") or {},
        "preparation": snap.get("preparation"),
        "verdict": snap.get("verdict"),
        "tools": {
            "ffmpeg": bool(shutil.which(getattr(settings, "ffmpeg_path", "ffmpeg") or "ffmpeg")),
            "ffprobe": bool(shutil.which(getattr(settings, "ffprobe_path", "ffprobe") or "ffprobe")),
        },
        "source_evidence": {
            "selected_instance_id": selected.get("instance_id"),
            "adopted": bool(selected.get("adopted")),
            "runtime_report_status": report.get("status"),
        },
    }


def check_capability(
    db: Session,
    settings: Any,
    *,
    recipe: dict[str, Any] | None = None,
    source_line: str = "manual4",
    project_id: str | None = None,
) -> dict[str, Any]:
    """Evaluate readiness strictly for the selected recipe without false cross-capability blocks.

    * Missing optional RelayVSR upscale or unselected low-VRAM variants never
      block an available baseline H3 route.
    * Missing Qwen image weights never block Manual4 / Ref2VA video when the
      user uploads valid reference images.
    """
    from shine.comfy.graph_compiler import BASELINE_ACCELERATOR_ID
    from shine.comfyops.resource_resolver import resolve_resources
    from shine.platform_gpu import inspect_gpu_status
    from shine.strategy import (
        GenerationStrategy,
        SecondPassConfig,
        UpscaleConfig,
        shot_reference_capability_blockers,
        strategy_blockers,
    )

    req = dict(recipe or {})
    capability = str(req.get("capability") or "h3").strip().lower()
    variant_id = str(req.get("variant_id") or "h3-ref2va-int8-convrot")
    accelerator_id = str(req.get("accelerator_id") or BASELINE_ACCELERATOR_ID)
    sp_raw = req.get("second_pass") or {}
    up_raw = req.get("upscale") or {}
    loras_raw = req.get("style_loras") or []
    audio_count = int(req.get("audio_count") or 0)
    video_count = int(req.get("video_count") or 0)

    if capability == "h3":
        if accelerator_id == "turbo4":
            return {
                "capability": "h3",
                "source_line": source_line,
                "project_id": project_id,
                "status": "unsupported",
                "blockers": [{"code": "ACCELERATOR_UNVERIFIED", "message": "turbo4 尚未真机验收，不支持编译。"}],
                "optional_gaps": [],
                "repair_action": None,
                "source_evidence": {"variant_id": variant_id, "accelerator_id": accelerator_id},
            }
        try:
            from shine.strategy import StyleLoraConfig

            strategy = GenerationStrategy(
                model_variant_id=variant_id,
                accelerator_id=accelerator_id,
                second_pass=SecondPassConfig(
                    enabled=bool(sp_raw.get("enabled")),
                    denoise=float(sp_raw.get("denoise", 0.25)),
                    latent_scale=float(sp_raw.get("latent_scale", 1.0)),
                ),
                upscale=UpscaleConfig(enabled=bool(up_raw.get("enabled"))),
                style_loras=[StyleLoraConfig.model_validate(item) for item in loras_raw],
            )
        except Exception as exc:
            return {
                "capability": "h3",
                "source_line": source_line,
                "project_id": project_id,
                "status": "unsupported",
                "blockers": [{"code": "RECIPE_INVALID", "message": str(exc)}],
                "optional_gaps": [],
                "repair_action": None,
                "source_evidence": {},
            }

        snap = setup_summary(db, settings, project_id=None, variant_id=variant_id)
        h3_cap = (snap.get("capabilities") or {}).get("h3") or {}
        img_cap = (snap.get("capabilities") or {}).get("image") or {}
        relay_cap = (snap.get("capabilities") or {}).get("relayvsr") or {}
        h3_facts = h3_cap.get("facts") or {}

        # Resolve pinned catalog components for the underlying recipe (without
        # failing on optional unpinned latent-upscaler weights), while evaluating
        # the full requested strategy (including second_pass and upscale) via
        # the single strategy_blockers adjudicator.
        base_strategy = (
            strategy.model_copy(update={"second_pass": SecondPassConfig(enabled=False)})
            if strategy.second_pass.enabled
            else strategy
        )
        res_preview = resolve_resources(db, settings, base_strategy)
        blockers = list(res_preview.get("blockers") or [])
        seen_blocker_codes = {b.get("code") for b in blockers if isinstance(b, dict)}
        for prod_blocker in strategy_blockers(strategy, db, settings):
            if isinstance(prod_blocker, dict) and prod_blocker.get("code") not in seen_blocker_codes:
                blockers.append(prod_blocker)
                seen_blocker_codes.add(prod_blocker.get("code"))
        for ref_blocker in shot_reference_capability_blockers(
            db, settings, audio_count=audio_count, video_count=video_count
        ):
            if ref_blocker.get("code") not in seen_blocker_codes:
                blockers.append(ref_blocker)
                seen_blocker_codes.add(ref_blocker.get("code"))
        missing_weights = [
            c for c in (res_preview.get("components") or []) if c.get("state") == "MISSING"
        ]
        # Check physical disk presence too so already-present files on a newly
        # configured machine aren't falsely marked missing before register.
        files_found = bool(h3_facts.get("FILES_FOUND"))
        files_verified = bool(h3_facts.get("FILES_VERIFIED"))
        runtime_ready = bool(h3_facts.get("RUNTIME_SERVICES_VERIFIED") or h3_facts.get("WORKFLOW_VALIDATED"))

        optional_gaps: list[dict[str, Any]] = []
        if not bool((img_cap.get("facts") or {}).get("FILES_VERIFIED")):
            optional_gaps.append({
                "capability": "image",
                "code": "QWEN_IMAGE_OPTIONAL_MISSING",
                "message": "Qwen 生图未就绪；仍可直接上传人物/场景参考图完成 H3 生产。",
            })
        if not up_raw.get("enabled") and not bool((relay_cap.get("facts") or {}).get("FILES_VERIFIED")):
            optional_gaps.append({
                "capability": "relayvsr",
                "code": "RELAYVSR_OPTIONAL_MISSING",
                "message": "未开启 RelayVSR 超分且超分模型未安装，不影响基础 H3 路线。",
            })

        gpu_busy = False
        try:
            # Read-only inspect without requiring sessionmaker
            from shine.comfyops.models import ResourceLease

            now = utcnow()
            live = db.scalars(
                select(ResourceLease).where(ResourceLease.status.in_(["ACTIVE", "RECONCILE_REQUIRED"]))
            ).all()
            gpu_busy = any(r.status == "RECONCILE_REQUIRED" or r.expires_at > now for r in live)
        except Exception:
            _ = inspect_gpu_status

        local_enabled = (
            getattr(settings, "render_provider", "mock") == "comfyui"
            or os.getenv("SHINE_ENABLE_LOCAL_EXECUTION", "").strip().lower() in {"1", "true", "yes"}
        )
        has_probe_or_install = bool((snap.get("installation") or {}).get("selected")) or bool(
            getattr(settings, "comfy_probe_roots", ())
        )

        if blockers or (missing_weights and not files_found) or (not files_verified and not has_probe_or_install):
            status = "missing_required"
            repair = {"action": "prepare_setup", "capabilities": ["h3"], "variant_id": variant_id}
        elif not runtime_ready and not has_probe_or_install:
            status = "missing_required"
            repair = {"action": "prepare_setup", "capabilities": ["h3"], "variant_id": variant_id}
        elif gpu_busy and local_enabled:
            status = "temporarily_busy"
            repair = {"action": "wait_gpu"}
        elif optional_gaps:
            status = "optional_missing"
            repair = None
        else:
            status = "ready"
            repair = None

        return {
            "capability": "h3",
            "source_line": source_line,
            "project_id": project_id,
            "status": status,
            "blockers": blockers,
            "optional_gaps": optional_gaps,
            "repair_action": repair,
            "source_evidence": {
                "variant_id": variant_id,
                "accelerator_id": accelerator_id,
                "files_found": files_found,
                "files_verified": files_verified,
                "runtime_services_verified": runtime_ready,
                "recipe_sha256": res_preview.get("compiled_recipe_sha256"),
                "local_execution_enabled": local_enabled,
            },
        }

    # Image (Qwen) / VLM / ASR / RelayVSR
    if capability not in {"image", "vlm", "asr", "relayvsr"}:
        return {
            "capability": capability,
            "source_line": source_line,
            "project_id": project_id,
            "status": "unsupported",
            "blockers": [{"code": "CAPABILITY_UNKNOWN", "message": f"未知能力：{capability}"}],
            "optional_gaps": [],
            "repair_action": None,
            "source_evidence": {},
        }

    snap = setup_summary(db, settings, project_id=None)
    cap = (snap.get("capabilities") or {}).get(capability) or {}
    facts = cap.get("facts") or {}
    ready = bool(facts.get("PRODUCTION_READY")) or (
        capability == "image"
        and (
            bool(facts.get("FILES_VERIFIED") and (facts.get("INSTANCE_BOUND") or facts.get("FILES_FOUND")))
            or bool(facts.get("RUNTIME_SERVICES_VERIFIED"))
        )
    )
    status = "ready" if ready else "missing_required"
    return {
        "capability": capability,
        "source_line": source_line,
        "project_id": project_id,
        "status": status,
        "blockers": [] if ready else (cap.get("advisories") or [{"code": f"{capability.upper()}_NOT_READY"}]),
        "optional_gaps": [],
        "repair_action": None if ready else {"action": "prepare_setup", "capabilities": [capability]},
        "source_evidence": {"facts": facts, "missing_bytes": cap.get("missing_bytes", 0)},
    }


def prepare_missing(
    db: Session,
    settings: Any,
    *,
    capabilities: list[str],
    project_id: str | None = None,
    variant_id: str | None = None,
    actor: str = "local-user",
    deep_scan: bool = False,
    reconcile_only: bool = False,
) -> dict[str, Any]:
    """Delegate missing environment/node/model preparation to the ONE Setup Worker."""
    from shine.platform_projects import get_workflow_kind_if_known

    # Setup preparation tasks only pass a 7-stage project_id to project-scoped steps;
    # for manual4 (or machine-level prep), pass project_id=None so setup_preparation
    # never tries to query 7-stage project tables for a manual4 id.
    safe_project_id = project_id
    if safe_project_id and get_workflow_kind_if_known(db, safe_project_id) == "manual4":
        safe_project_id = None
    task = enqueue_setup_preparation(
        db,
        project_id=safe_project_id,
        capabilities=capabilities,
        variant_id=variant_id,
        actor=actor,
        settings=settings,
        deep_scan=deep_scan,
        reconcile_only=reconcile_only,
    )
    db.commit()
    return task


def _append_optional_comfy_launch_flags(argv: list[str]) -> None:
    """Append opt-in ComfyUI memory/NVMe flags cleanly before whitelist args."""
    extra: list[str] = []
    if os.getenv("SHINE_COMFY_FAST_DISK", "").strip().lower() in {"1", "true", "yes"}:
        if "--fast-disk" not in argv:
            extra.append("--fast-disk")
    if os.getenv("SHINE_COMFY_CACHE_NONE", "").strip().lower() in {"1", "true", "yes"}:
        if "--cache-none" not in argv:
            extra.append("--cache-none")
    if not extra:
        return
    if "--disable-all-custom-nodes" in argv:
        idx = argv.index("--disable-all-custom-nodes")
        argv[idx:idx] = extra
    else:
        argv.extend(extra)


def _adapter_sha(adapter_dir: Path) -> str:
    if (adapter_dir / "__init__.py").is_file() and (adapter_dir / "barrier.py").is_file():
        return adapter_identity(adapter_dir)
    return ""


def _pick_free_port(preferred: int = 8198, *, exclude: set[int] | None = None) -> int:
    import socket

    reserved = {8000, 5173, 8188, 8790, 8116, 8117} | set(exclude or set())
    for offset in range(60):
        candidate = preferred + offset
        if candidate in reserved:
            continue
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            try:
                sock.bind(("127.0.0.1", candidate))
                return candidate
            except OSError:
                continue
    raise HTTPException(503, {"code": "M4_NO_FREE_COMFY_PORT", "message": "未找到可用的回环端口用于四阶 ComfyUI。"})


def resolve_shared_comfy_launch(
    db: Session,
    settings: Any,
    m4_root: Path,
    *,
    port: int | None = None,
    preferred_port: int = 8198,
) -> dict[str, Any]:
    """Resolve ComfyUI launch spec from the single authoritative machine recipe/ledger.

    Shares the exact ComfyUI root, Python executable, custom_nodes, extra model
    paths, and verified weights with director7 while isolating the runtime data
    directories (``<m4_root>/comfy-runtime/``), log file, and loopback port.
    """
    repo_root = Path(__file__).resolve().parents[3]
    registered_ports: set[int] = set()
    for inst in db.scalars(select(ComfyInstance)).all():
        p = (inst.payload or {}).get("port") if isinstance(inst.payload, dict) else None
        if isinstance(p, int):
            registered_ports.add(p)

    m4_runtime_root = (m4_root / "comfy-runtime").resolve()
    for folder in ("user/default/ComfyUI-Manager", "input", "output", "temp"):
        (m4_runtime_root / folder).mkdir(parents=True, exist_ok=True)
    manager_ini = m4_runtime_root / "user/default/ComfyUI-Manager/config.ini"
    if not manager_ini.exists():
        manager_ini.write_text(
            "[default]\nnetwork_mode=offline\nbypass_ssl=false\nsecurity_level=strong\n",
            encoding="utf-8",
        )
    log_file = (m4_root / "logs" / "comfy-m4.log").resolve()
    log_file.parent.mkdir(parents=True, exist_ok=True)

    from shine.comfyops.managed_recipe import recipe_path as _managed_recipe_path

    local_recipe = _managed_recipe_path(settings)
    recipe_override = os.getenv("SHINE_SETUP_RECIPE", "").strip()
    recipe_override_path = Path(recipe_override) if recipe_override else None
    recipe: dict[str, Any] | None = None
    recipe_sha = ""
    try:
        if local_recipe.is_file():
            recipe, recipe_sha = load_runtime_recipe(settings)
        elif recipe_override_path and recipe_override_path.is_file():
            recipe, recipe_sha = load_runtime_recipe(settings, path=recipe_override_path)
    except HTTPException:
        recipe = None

    if recipe is not None:
        renderer = recipe["renderer"]
        comfy_root = Path(renderer["root"]).resolve()
        python_exe = Path(renderer["python"]).resolve()
        adapter_dir = Path(renderer["adapter_dir"]).resolve()
        bootstrap = Path(renderer["bootstrap"]).resolve()
        adapter_sha = str(renderer["adapter_sha256"])
        shared_runtime_root = Path(str(renderer.get("runtime_data_root") or comfy_root)).resolve()
        shared_paths_yaml = shared_runtime_root / "shine-node-paths.yaml"
        chosen_port = int(port) if port else _pick_free_port(preferred_port, exclude=registered_ports)
        argv, cwd, env, shared_pins, _, _ = build_renderer_launch(renderer, port=chosen_port)
        for name in ("user", "input", "output", "temp"):
            flag = f"--{name}-directory"
            path = str(m4_runtime_root / name)
            if flag in argv:
                argv[argv.index(flag) + 1] = path
            else:
                argv.extend([flag, path])
        if shared_paths_yaml.is_file() and str(shared_paths_yaml) not in argv:
            argv.extend(["--extra-model-paths-config", str(shared_paths_yaml)])

        # Preserve whitelist from the authoritative recipe argv (which already includes
        # shine_managed_comfy_handoff / shine_comfy_handoff and resolved node_pack_dirs)
        if "--whitelist-custom-nodes" in argv:
            tail = argv[argv.index("--whitelist-custom-nodes") + 1:]
            end = next((index for index, value in enumerate(tail) if value.startswith("--")), len(tail))
            whitelist = tail[:end]
        else:
            pack_dirs = list((renderer.get("node_pack_dirs") or {}).values())
            sampling_name = (
                "shine_managed_h3_sampling_mode.py"
                if adapter_dir.name == "shine_managed_comfy_handoff"
                else "shine_h3_sampling_mode.py"
            )
            whitelist = [adapter_dir.name, sampling_name, *sorted(set(pack_dirs))]
            if "--disable-all-custom-nodes" not in argv:
                argv.append("--disable-all-custom-nodes")
            argv.extend(["--whitelist-custom-nodes", *whitelist])
        sampling_candidate = adapter_dir.parent / (
            "shine_managed_h3_sampling_mode.py"
            if adapter_dir.name == "shine_managed_comfy_handoff"
            else "shine_h3_sampling_mode.py"
        )
        pinned_files = [
            *shared_pins,
            str(adapter_dir / "__init__.py"),
            str(adapter_dir / "barrier.py"),
            str(bootstrap),
        ]
        if sampling_candidate.is_file():
            pinned_files.append(str(sampling_candidate))
        if shared_paths_yaml.is_file():
            pinned_files.append(str(shared_paths_yaml))

        _append_optional_comfy_launch_flags(argv)
        env.update(prepare_process_environment(m4_runtime_root))
        return {
            "source": "managed_recipe",
            "recipe_sha256": recipe_sha,
            "instance_id": renderer.get("instance_id"),
            "python": str(python_exe),
            "root": str(comfy_root),
            "cwd": cwd,
            "models_dir": str(Path(renderer["base_directory"]) / "models"),
            "adapter_dir": str(adapter_dir),
            "adapter_sha256": adapter_sha,
            "bootstrap": str(bootstrap),
            "whitelist_nodes": whitelist,
            "runtime_root": str(m4_runtime_root),
            "log_file": str(log_file),
            "port": chosen_port,
            "argv": argv,
            "pinned_files": list(dict.fromkeys(pinned_files)),
            "env_base": env,
        }

    # Fallback when managed_recipe.json has not yet been generated: read
    # setup_summary.resolve_installation + ComfyInstance + comfy_probe_roots.
    from shine.comfyops.deployment import load_default_catalog
    from shine.comfyops.existing_installation import resolve_node_pack_dirs
    from shine.comfyops.setup_summary import resolve_installation

    candidates: list[Path] = []
    inst_info = resolve_installation(db, settings)
    sel = inst_info.get("selected") or {}
    if sel.get("root"):
        candidates.append(Path(str(sel["root"])))
    env_root = os.getenv("SHINE_M4_COMFY_ROOT", "").strip()
    if env_root:
        candidates.append(Path(env_root))
    for r in getattr(settings, "comfy_probe_roots", ()) or ():
        candidates.append(Path(str(r)))

    bootstrap = (repo_root / "scripts" / "managed_comfy_bootstrap.py").resolve()
    for root_cand in candidates:
        comfy_root = root_cand.resolve()
        if not (comfy_root / "main.py").is_file():
            continue
        py_candidates = [
            comfy_root / ".venv" / "Scripts" / "python.exe",
            comfy_root.parent / "python_embeded" / "python.exe",
        ]
        python_exe = next((p.resolve() for p in py_candidates if p.is_file()), None)
        adapter_dir = (comfy_root / "custom_nodes" / "shine_comfy_handoff").resolve()
        if python_exe is None or not adapter_dir.is_dir() or not bootstrap.is_file():
            continue
        adapter_sha = _adapter_sha(adapter_dir)
        if not adapter_sha:
            continue
        pack_dirs, _missing = resolve_node_pack_dirs(comfy_root, load_default_catalog())
        whitelist = ["shine_comfy_handoff", "shine_h3_sampling_mode.py", *sorted(set(pack_dirs.values()))]
        chosen_port = int(port) if port else _pick_free_port(preferred_port, exclude=registered_ports)
        argv = [
            str(python_exe),
            str(bootstrap),
            "--listen", "127.0.0.1",
            "--port", str(chosen_port),
            "--user-directory", str(m4_runtime_root / "user"),
            "--input-directory", str(m4_runtime_root / "input"),
            "--output-directory", str(m4_runtime_root / "output"),
            "--temp-directory", str(m4_runtime_root / "temp"),
            "--disable-async-offload",
            "--disable-pinned-memory",
            "--reserve-vram", "1",
            "--fast", "fp16_accumulation",
            "--disable-all-custom-nodes",
            "--whitelist-custom-nodes",
            *whitelist,
        ]
        _append_optional_comfy_launch_flags(argv)
        style_lora_yaml = (storage_root(settings.data_dir) / "setup" / "shine-style-loras.yaml").resolve()
        if style_lora_yaml.is_file() and str(style_lora_yaml) not in argv:
            argv.extend(["--extra-model-paths-config", str(style_lora_yaml)])
        pinned_files = [
            str(python_exe),
            str(comfy_root / "main.py"),
            str(adapter_dir / "__init__.py"),
            str(adapter_dir / "barrier.py"),
            str(bootstrap),
        ]
        sampling_file = comfy_root / "custom_nodes" / "shine_h3_sampling_mode.py"
        if sampling_file.is_file():
            pinned_files.append(str(sampling_file))
        env = {**os.environ, **prepare_process_environment(m4_runtime_root)}
        return {
            "source": "installation_ledger",
            "recipe_sha256": hashlib.sha256(str(comfy_root).encode("utf-8")).hexdigest(),
            "instance_id": sel.get("instance_id"),
            "python": str(python_exe),
            "root": str(comfy_root),
            "models_dir": str(comfy_root / "models"),
            "adapter_dir": str(adapter_dir),
            "adapter_sha256": adapter_sha,
            "bootstrap": str(bootstrap),
            "whitelist_nodes": whitelist,
            "runtime_root": str(m4_runtime_root),
            "log_file": str(log_file),
            "port": chosen_port,
            "argv": argv,
            "pinned_files": pinned_files,
            "env_base": env,
        }

    raise HTTPException(
        409,
        {
            "code": "M4_COMFY_RUNTIME_NOT_CONFIGURED",
            "message": "未找到可用的本机 ComfyUI 与 H3 节点环境，请先在平台「基础配置」完成一次配置。",
        },
    )
