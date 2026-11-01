"""Controlled instance lifecycle on the existing installation/attempt ledger.

Real subprocesses are opt-in on Windows. All filesystem, command and HTTP production
paths accept Fake runners/clients. No arbitrary command or second task store exists.
"""

from __future__ import annotations

import hashlib
import os
import shutil
import signal
import subprocess
import sys
from datetime import datetime
from pathlib import Path
from uuid import NAMESPACE_URL, uuid4, uuid5

import httpx
from fastapi import HTTPException
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import text

from shine.comfy.workflow import graph_hash
from shine.comfyops.installer import (
    CommandExecutor,
    durable_json,
    exclusive_file_lock,
    extract_zip,
    process_identity,
    safe_target,
)
from shine.comfyops.models import ComfyInstance, InstallationAttempt, InstallationPlan, ModelPackage, NodePackage
from shine.comfyops.runtime_dependencies import (
    dependency_commands,
    dependency_lock,
    prepare_build_directories,
    source_build_plans,
    verified_built_wheels,
)
from shine.comfyops.storage import prepare_process_environment, process_environment
from shine.models import utcnow
from shine.services import add_audit, require_project


class InstanceOperation(BaseModel):
    model_config = ConfigDict(extra="forbid")
    project_id: str
    name: str = Field(pattern=r"^[a-zA-Z][a-zA-Z0-9_-]{0,60}$")
    action: str = Field(pattern=r"^(CREATE|INSTALL_NODE|BIND_MODELS|START|STOP|SNAPSHOT|RESTORE|ROLLBACK)$")
    mode: str = Field(default="fake", pattern=r"^(fake|local)$")
    port: int = Field(default=8190, ge=1024, le=65535)
    source_package_id: str | None = None
    package_ids: list[str] = Field(default_factory=list)
    snapshot_id: str | None = None
    model_bindings: dict[str, str] = Field(default_factory=dict)
    risk_confirmed: bool = False


def package(db, identity):
    row = db.get(ModelPackage, identity) or db.get(NodePackage, identity)
    if not row:
        raise HTTPException(409, "PACKAGE_MISSING")
    if row.payload["license_id"].lower() in {"unknown", "unverified"}:
        raise HTTPException(409, "LICENSE_NOT_APPROVED")
    if row.payload["usage"] == "commercial" and "research" in row.payload["license_id"].lower():
        raise HTTPException(409, "LICENSE_NOT_APPROVED")
    if row.status != "FILES_VERIFIED_PENDING_RUNTIME":
        raise HTTPException(409, "PACKAGE_FILES_NOT_VERIFIED")
    return row


def instance_root(settings, name, mode):
    return safe_target(settings.comfyops_dir / ("fake-instances" if mode == "fake" else "instances"), name)


def instance_identity(mode, port):
    return str(uuid5(NAMESPACE_URL, f"shine-comfy-instance:{mode}:http://127.0.0.1:{port}"))


def tree_manifest(root):
    if not root.is_dir():
        raise ValueError("INSTANCE_FILES_MISSING")
    result = {}
    for path in sorted(root.rglob("*")):
        if path.is_symlink() or not path.resolve().is_relative_to(root.resolve()):
            raise ValueError("INSTANCE_SYMLINK_FORBIDDEN")
        if "__pycache__" in path.parts or path.suffix == ".pyc":
            continue
        if path.is_file():
            result[path.relative_to(root).as_posix()] = hashlib.sha256(path.read_bytes()).hexdigest()
    return result


def plan_operation(db, settings, body):
    require_project(db, body.project_id)
    if body.mode == 'local':
        from shine.comfyops.storage import require_storage
        require_storage(settings)
    root = instance_root(settings, body.name, body.mode)
    identity = instance_identity(body.mode, body.port)
    instance = db.get(ComfyInstance, identity)
    if instance and instance.payload.get("managed") and instance.payload.get("root") != str(root):
        raise HTTPException(409, "INSTANCE_IDENTITY_CHANGED")
    if body.action == "CREATE":
        if (instance and instance.status != "ROLLED_BACK") or root.exists() or not body.source_package_id:
            raise HTTPException(409, "INSTANCE_EXISTS_OR_SOURCE_REQUIRED")
    elif not instance or not instance.payload.get("managed"):
        raise HTTPException(409, "EXTERNAL_INSTANCE_PROTECTED")
    elif instance.payload["port"] != body.port:
        raise HTTPException(409, "INSTANCE_PORT_CHANGED")
    ids = list(
        dict.fromkeys([x for x in [body.source_package_id, *body.package_ids, *body.model_bindings.values()] if x])
    )
    packages = []
    for identity_ in ids:
        row = package(db, identity_)
        # Mutable refs, source licenses and every file digest are part of the approved plan.
        packages.append({"id": row.id, "hash": row.sha256, "spec": row.payload})
    if body.action == "INSTALL_NODE" and not body.package_ids:
        raise HTTPException(422, "NODE_PACKAGE_REQUIRED")
    for identity_ in body.model_bindings.values():
        if not db.get(ModelPackage, identity_):
            raise HTTPException(409, "MODEL_PACKAGE_REQUIRED")
    if set(body.model_bindings) - {"checkpoints", "diffusion_models", "vae", "loras", "text_encoders", "pdd_acc"}:
        raise HTTPException(422, "MODEL_BINDING_KIND_INVALID")
    if body.action == "ROLLBACK" and not body.snapshot_id:
        body = body.model_copy(update={"snapshot_id": instance.payload.get("last_attempt")})
    if body.action in {"RESTORE", "ROLLBACK"}:
        snapshot = db.get(InstallationAttempt, body.snapshot_id)
        if (
            not snapshot
            or snapshot.status not in {"SNAPSHOT", "NEEDS_REVIEW"}
            or snapshot.payload.get("instance_id") != identity
        ):
            raise HTTPException(409, "SNAPSHOT_NOT_OWNED")
    tier = "L2" if body.action == "CREATE" else "L3"
    if tier == "L3" and not body.risk_confirmed:
        raise HTTPException(409, "L3_RISK_CONFIRMATION_REQUIRED")
    python = str(Path(sys.executable).resolve())
    workspace_python = root / ".venv" / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
    commands = []
    builds = []
    if body.action == "CREATE":
        commands.append([python, "-m", "venv", str(root / ".venv")])
    if body.action in {"CREATE", "INSTALL_NODE"}:
        builds = source_build_plans(root, packages, settings.comfyops_dir / 'isolated-packages', uuid4().hex)
        commands.extend(dependency_commands(root, workspace_python, builds))
    if body.action == "START":
        commands.append(
            [str(workspace_python), str(root / "main.py"), "--listen", "127.0.0.1", "--port", str(body.port)]
        )
    payload = {
        "kind": "INSTANCE_LIFECYCLE",
        "operation": body.model_dump(),
        "instance_id": identity,
        "root": str(root),
        "tier": tier,
        "commands": commands,
        "command_environment": process_environment(root),
        "source_builds": builds,
        "packages": packages,
        "python": python,
        "python_sha256": hashlib.sha256(Path(python).read_bytes()).hexdigest(),
        "instance_hash": instance.sha256 if instance else None,
        "tree_hash": graph_hash(tree_manifest(root)) if root.exists() else None,
        "isolated": True,
        "executor": "FAKE_ONLY" if body.mode == "fake" else "LOCAL_ISOLATED",
    }
    row = InstallationPlan(
        project_id=body.project_id, payload=payload, sha256=graph_hash(payload), status="AWAITING_APPROVAL"
    )
    db.add(row)
    db.flush()
    add_audit(
        db,
        "comfyops",
        "INSTANCE_PLAN_CREATED",
        "installation_plan",
        row.id,
        {"tier": tier, "action": body.action, "hash": row.sha256},
        project_id=body.project_id,
    )
    return row


class ProcessRunner(CommandExecutor):
    is_mock = False
    on_started = None
    last_owner = None
    environment = None

    def child_environment(self):
        patch = dict(self.environment or {})
        if self.environment and self.environment.get('TEMP'):
            # Old approved plans may predate compiler-cache routing. Their
            # existing instance root remains the authority for safe defaults.
            patch = {**prepare_process_environment(Path(self.environment['TEMP']).parent), **patch}
        return {**os.environ, **patch}

    def run(self, args, *, cwd):
        process = subprocess.Popen(
            args, cwd=cwd, shell=False, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL, env=self.child_environment(),
        )
        self.last_owner = {"pid": process.pid, "birth": process_identity(process.pid)}
        if self.on_started:
            self.on_started(self.last_owner)
        try:
            result = process.wait(timeout=600)
        except subprocess.TimeoutExpired as exc:
            raise ValueError("COMMAND_TIMEOUT_RECONCILIATION_REQUIRED") from exc
        if result:
            raise ValueError("CONTROLLED_COMMAND_FAILED")

    def start(self, args, *, cwd):
        process = subprocess.Popen(
            args, cwd=cwd, shell=False, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL, env=self.child_environment(),
        )
        birth = process_identity(process.pid)
        if birth is None or process.poll() is not None:
            raise ValueError("INSTANCE_START_FAILED")
        return {"pid": process.pid, "birth": birth}

    def stop(self, owner):
        pid, birth = owner["pid"], owner["birth"]
        if process_identity(pid) is None:
            return
        if os.name == "nt":
            import ctypes
            from ctypes import wintypes

            kernel = ctypes.WinDLL("kernel32", use_last_error=True)
            kernel.OpenProcess.restype = wintypes.HANDLE
            kernel.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
            kernel.GetProcessTimes.argtypes = [wintypes.HANDLE] + [ctypes.c_void_p] * 4
            kernel.TerminateProcess.argtypes = [wintypes.HANDLE, wintypes.UINT]
            kernel.CloseHandle.argtypes = [wintypes.HANDLE]
            handle = kernel.OpenProcess(0x101001, False, pid)
            if not handle:
                raise ValueError("OWNED_PROCESS_UNVERIFIABLE")
            try:
                times = [ctypes.c_ulonglong() for _ in range(4)]
                if (
                    not kernel.GetProcessTimes(handle, *(ctypes.byref(v) for v in times))
                    or str(times[0].value) != birth
                ):
                    raise ValueError("OWNED_PROCESS_CHANGED")
                if not kernel.TerminateProcess(handle, 0):
                    raise ValueError("OWNED_PROCESS_STOP_FAILED")
                kernel.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
                if kernel.WaitForSingleObject(handle, 5000) != 0:
                    raise ValueError("OWNED_PROCESS_STOP_OUTCOME_UNKNOWN")
            finally:
                kernel.CloseHandle(handle)
        else:
            if not hasattr(os, "pidfd_open"):
                raise ValueError("OWNED_PROCESS_STOP_UNSUPPORTED")
            fd = os.pidfd_open(pid)
            try:
                if process_identity(pid) != birth:
                    raise ValueError("OWNED_PROCESS_CHANGED")
                signal.pidfd_send_signal(fd, signal.SIGTERM)
                import select

                poller = select.poll()
                poller.register(fd, select.POLLIN)
                if not poller.poll(5000):
                    raise ValueError("OWNED_PROCESS_STOP_OUTCOME_UNKNOWN")
            finally:
                os.close(fd)


class FakeCommandRunner:
    is_mock = True

    def __init__(self):
        self.calls = []
        self.owner = None

    def run(self, args, *, cwd):
        self.calls.append(list(args))
        if args[1:3] == ["-m", "venv"]:
            executable = Path(args[-1]) / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
            executable.parent.mkdir(parents=True)
            executable.write_bytes(b"FAKE EXECUTABLE - NEVER EXECUTE")

    def start(self, args, *, cwd):
        self.calls.append(list(args))
        self.owner = {"pid": 99999999, "birth": "FAKE", "is_mock": True}
        return self.owner

    def stop(self, owner):
        if owner != self.owner:
            raise ValueError("OWNED_PROCESS_CHANGED")
        self.calls.append(["FAKE_STOP", str(owner["pid"])])
        self.owner = None


def health(port, http):
    base = f"http://127.0.0.1:{port}"
    stats = http.get(base + "/system_stats")
    stats.raise_for_status()
    queue = http.get(base + "/queue")
    queue.raise_for_status()
    return {"stats": stats.json(), "queue": queue.json()}


def require_idle(port, http):
    result = health(port, http)
    if result["queue"].get("queue_running") or result["queue"].get("queue_pending"):
        raise ValueError("INSTANCE_QUEUE_NOT_IDLE")
    return result


def snapshot_tree(root, directory):
    manifest = tree_manifest(root)
    shutil.copytree(root, directory, ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
    if tree_manifest(directory) != manifest:
        raise ValueError("SNAPSHOT_CHANGED_DURING_COPY")
    return manifest


def copy_package(source, target):
    """Only content-verified package files; ZIP traversal and symlinks are denied."""
    if target.exists():
        raise ValueError("NODE_DESTINATION_EXISTS")
    target.mkdir(parents=True)
    files = list(source.iterdir())
    if len(files) == 1 and files[0].suffix == ".zip":
        extract_zip(files[0], target)
        children = list(target.iterdir())
        if len(children) == 1 and children[0].is_dir():
            nested = children[0]
            for path in list(nested.iterdir()):
                shutil.move(str(path), target / path.name)
            nested.rmdir()
    else:
        shutil.copytree(source, target, dirs_exist_ok=True)
    tree_manifest(target)


def execute_operation(factory, settings, identity, digest, actor, *, runner=None, http=None, checkpoint=None):
    checkpoint = checkpoint or (lambda stage: None)
    runner = runner or ProcessRunner()
    http = http or httpx.Client(timeout=10, follow_redirects=False)
    with factory() as db:
        db.execute(text("BEGIN IMMEDIATE"))
        plan = db.get(InstallationPlan, identity)
        if not plan or plan.status != "APPROVED" or plan.sha256 != digest or graph_hash(plan.payload) != digest:
            raise HTTPException(409, "INSTALL_APPROVAL_REQUIRED_OR_PLAN_HASH_CHANGED")
        payload = plan.payload
        if payload.get("kind") != "INSTANCE_LIFECYCLE" or payload.get("approved_by") != actor:
            raise HTTPException(403, "INSTANCE_OPERATION_NOT_AUTHORIZED")
        if datetime.fromisoformat(payload["execute_before"]) <= utcnow():
            raise HTTPException(409, "INSTALL_APPROVAL_EXPIRED")
        op = InstanceOperation.model_validate(payload["operation"])
        if runner.is_mock != (op.mode == "fake"):
            raise HTTPException(409, "EXECUTOR_MODE_MISMATCH")
        if not runner.is_mock and (os.name != "nt" or os.getenv("SHINE_ENABLE_LOCAL_EXECUTION") != "1"):
            raise HTTPException(409, "LOCAL_EXECUTION_NOT_ENABLED")
        root = instance_root(settings, op.name, op.mode)
        if (
            str(root) != payload["root"]
            or hashlib.sha256(Path(payload["python"]).read_bytes()).hexdigest() != payload["python_sha256"]
        ):
            raise HTTPException(409, "INSTANCE_PLAN_CHANGED")
        instance = db.get(ComfyInstance, payload["instance_id"])
        if (instance.sha256 if instance else None) != payload["instance_hash"]:
            raise HTTPException(409, "INSTANCE_PLAN_CHANGED")
        for p in payload["packages"]:
            row = package(db, p["id"])
            if row.sha256 != p["hash"]:
                raise HTTPException(409, "PACKAGE_CHANGED")
        plan.status = "INSTALLING"
        attempt = InstallationAttempt(
            plan_id=identity,
            payload={
                "instance_id": payload["instance_id"],
                "plan_hash": digest,
                "owner_pid": os.getpid(),
                "owner_birth": process_identity(os.getpid()),
                "stage": "INTENT",
            },
            sha256=digest,
            status="SUBMIT_INTENT",
        )
        db.add(attempt)
        db.flush()
        attempt_id = attempt.id
        previous = dict(instance.payload) if instance else {}
        if instance is None:
            initial = {
                "managed": True,
                "name": op.name,
                "port": op.port,
                "root": str(root),
                "is_mock": runner.is_mock,
                "last_attempt": attempt_id,
            }
            instance = ComfyInstance(
                id=payload["instance_id"], payload=initial, sha256=graph_hash(initial), status="INSTALLING"
            )
            db.add(instance)
        db.commit()  # durable intent, never automatically replayed after an uncertain command
    checkpoint("INTENT")
    backup = settings.comfyops_dir / "instance-snapshots" / attempt_id
    package_root = settings.comfyops_dir / "isolated-packages"
    root.parent.mkdir(parents=True, exist_ok=True)
    snapshot = None
    command_phase = "FILES"
    result = {
        **previous,
        "managed": True,
        "name": op.name,
        "port": op.port,
        "root": str(root),
        "url": f"http://127.0.0.1:{op.port}",
        "is_mock": runner.is_mock,
    }
    runner.environment = payload.get('command_environment', process_environment(root))
    try:
        with exclusive_file_lock(root.with_suffix(".lock")):
            if root.exists() and graph_hash(tree_manifest(root)) != payload["tree_hash"]:
                raise ValueError("INSTANCE_FILES_CHANGED")
            if op.action not in {"CREATE", "ROLLBACK"}:
                if previous.get("process"):
                    require_idle(op.port, http)
                elif op.action == "START" and not runner.is_mock:
                    try:
                        health(op.port, http)
                    except httpx.ConnectError:
                        pass
                    else:
                        raise ValueError("INSTANCE_PORT_ALREADY_OCCUPIED")
                if op.action in {"INSTALL_NODE", "BIND_MODELS", "RESTORE"} and previous.get("process"):
                    raise ValueError("STOP_OWNED_INSTANCE_BEFORE_MUTATION")
                backup.parent.mkdir(parents=True, exist_ok=True)
                snapshot = snapshot_tree(root, backup)
                with factory.begin() as journal_db:
                    entry = journal_db.get(InstallationAttempt, attempt_id)
                    entry.payload = {
                        **entry.payload,
                        "snapshot_path": str(backup),
                        "files": snapshot,
                        "stage": "BACKUP_VERIFIED",
                    }
                checkpoint("BACKUP_VERIFIED")
            # Reverify installed bytes before use; filesystem corruption never borrows package status.
            for p in payload["packages"]:
                from shine.comfyops.installer import ControlledDownloader, PackageFile

                for f in p["spec"]["files"]:
                    if not ControlledDownloader.valid(
                        safe_target(package_root / p["spec"]["name"], f["relative_path"]), PackageFile.model_validate(f)
                    ):
                        raise ValueError("PACKAGE_BYTES_CHANGED")
            if op.action == "CREATE":
                source = next(p for p in payload["packages"] if p["id"] == op.source_package_id)
                copy_package(package_root / source["spec"]["name"], root)
                checkpoint("SOURCE_COPIED")
                if not (root / "main.py").is_file():
                    raise ValueError("COMFY_ENTRYPOINT_MISSING")
            if op.action == "INSTALL_NODE":
                for p in payload["packages"]:
                    if (p["id"] in op.package_ids and p['spec']['kind'] != 'wheel'
                            and not all(f["relative_path"].endswith(".whl") for f in p["spec"]["files"])):
                        copy_package(
                            package_root / p["spec"]["name"], safe_target(root / "custom_nodes", p["spec"]["name"])
                        )
                        result["node_versions"] = {
                            **result.get("node_versions", {}),
                            p["spec"]["name"]: p["spec"]["revision"],
                        }
            if op.action in {"CREATE", "INSTALL_NODE"}:
                builds = payload.get('source_builds', [])
                result["dependency_hash"] = dependency_lock(
                    root, payload["packages"], package_root, pending_builds=bool(builds),
                )
                prepare_build_directories(root, builds)
                for command in payload["commands"]:
                    # The final install sees a new hash-locked manifest containing
                    # the derived source wheels, never an unresolved source input.
                    if (builds and command[1:4] == ['-m', 'pip', 'install']
                            and ('--no-deps' not in command or '--force-reinstall' in command)):
                        built = verified_built_wheels(root, builds)
                        result['dependency_hash'] = dependency_lock(
                            root, payload['packages'], package_root, built_wheels=built,
                        )
                        result['source_builds'] = {
                            'wheels': built, 'python_sha256': payload['python_sha256'],
                            'package_hashes': {p['id']: p['hash'] for p in payload['packages']},
                            'commands': payload['commands'],
                        }
                        durable_json(root / '.shine-source-builds.json', result['source_builds'])
                    command_phase = "COMMAND_RUNNING"

                    def command_started(owner, command=command, command_phase=command_phase):
                        with factory.begin() as command_db:
                            entry = command_db.get(InstallationAttempt, attempt_id)
                            entry.payload = {
                                **entry.payload,
                                "command_owner": owner,
                                "command_phase": command_phase,
                                "argv_hash": graph_hash(command),
                            }

                    runner.on_started = command_started
                    runner.run(command, cwd=root)
                    command_phase = "COMMAND_COMPLETED"
                    checkpoint("COMMAND_COMPLETED")
            elif op.action == "BIND_MODELS":
                bindings = {}
                for kind, package_id in op.model_bindings.items():
                    p = next(p for p in payload["packages"] if p["id"] == package_id)
                    bindings[kind] = str(package_root / p["spec"]["name"])
                durable_json(root / "extra_model_paths.yaml", {"shine": bindings})  # JSON is valid YAML
                result["model_bindings"] = op.model_bindings
            elif op.action == "START":
                if previous.get("process"):
                    raise ValueError("INSTANCE_ALREADY_STARTED")
                result["process"] = runner.start(payload["commands"][0], cwd=root)
                with factory.begin() as started_db:
                    started = started_db.get(InstallationAttempt, attempt_id)
                    started.payload = {**started.payload, "started_process": result["process"]}
                checkpoint("PROCESS_STARTED")
            elif op.action == "STOP":
                if not previous.get("process"):
                    raise ValueError("OWNED_PROCESS_REQUIRED")
                runner.stop(previous["process"])
                result["process"] = None
            elif op.action == "ROLLBACK":
                with factory() as recovery_db:
                    failed = recovery_db.get(InstallationAttempt, op.snapshot_id)
                    evidence = dict(failed.payload)
                    if failed.status != "NEEDS_REVIEW":
                        raise ValueError("FAILED_ATTEMPT_REQUIRED")
                    owner = evidence.get("command_owner")
                    if owner and not owner.get("is_mock") and process_identity(owner["pid"]) == owner["birth"]:
                        raise ValueError("COMMAND_OWNER_STILL_ACTIVE")
                    if not runner.is_mock and evidence.get("command_phase") == "COMMAND_RUNNING" and not owner:
                        raise ValueError("COMMAND_OWNER_UNVERIFIABLE")
                    if previous.get("process"):
                        raise ValueError("STOP_OWNED_INSTANCE_BEFORE_ROLLBACK")
                    saved_path = evidence.get("snapshot_path")
                    with factory.begin() as journal_db:
                        entry = journal_db.get(InstallationAttempt, attempt_id)
                        entry.payload = {
                            **entry.payload,
                            "snapshot_path": saved_path,
                            "files": evidence.get("files"),
                            "stage": "ROLLBACK_INTENT",
                        }
                    if saved_path:
                        saved = Path(saved_path)
                        if not saved.resolve().is_relative_to(
                            (settings.comfyops_dir / "instance-snapshots").resolve()
                        ):
                            raise ValueError("SNAPSHOT_PATH_INVALID")
                        if tree_manifest(saved) != evidence["files"]:
                            raise ValueError("SNAPSHOT_HASH_CHANGED")
                        if root.exists():
                            shutil.rmtree(root)
                        shutil.copytree(saved, root)
                    elif root.exists():
                        failed_plan = recovery_db.get(InstallationPlan, failed.plan_id)
                        if failed_plan.payload["operation"]["action"] != "CREATE":
                            raise ValueError("BACKUP_UNAVAILABLE_DO_NOT_DELETE_EXISTING_INSTANCE")
                        shutil.rmtree(root)
                    result["process"] = None
            elif op.action == "RESTORE":
                with factory() as db:
                    saved = db.get(InstallationAttempt, op.snapshot_id)
                    restore = Path(saved.payload["snapshot_path"])
                    if not restore.is_relative_to(settings.comfyops_dir / "instance-snapshots"):
                        raise ValueError("SNAPSHOT_PATH_INVALID")
                    if tree_manifest(restore) != saved.payload["files"]:
                        raise ValueError("SNAPSHOT_HASH_CHANGED")
                    replacement = root.with_name(root.name + ".restore-" + attempt_id)
                    shutil.copytree(restore, replacement)
                    old = root.with_name(root.name + ".previous-" + attempt_id)
                    with factory.begin() as journal_db:
                        entry = journal_db.get(InstallationAttempt, attempt_id)
                        entry.payload = {
                            **entry.payload,
                            "swap_paths": [str(replacement), str(old)],
                            "stage": "SWAP_INTENT",
                        }
                    checkpoint("STAGED")
                    os.replace(root, old)
                    checkpoint("OLD_RENAMED")
                    os.replace(replacement, root)
                    checkpoint("NEW_RENAMED")
                    shutil.rmtree(old)
                    checkpoint("CLEANED")
            checkpoint("FILES_COMPLETE")
            result["tree_hash"] = graph_hash(tree_manifest(root)) if root.exists() else None
            result["last_attempt"] = attempt_id
    except Exception as exc:
        # Never assume a timed-out process/command did nothing. Backup and intent remain inspectable.
        with factory.begin() as db:
            failed = db.get(InstallationAttempt, attempt_id)
            failed.status = "NEEDS_REVIEW"
            failed.payload = {
                **failed.payload,
                "error": type(exc).__name__,
                "reason": str(exc),
                "snapshot_path": str(backup) if snapshot else failed.payload.get("snapshot_path"),
                "files": snapshot if snapshot is not None else failed.payload.get("files"),
                "command_phase": command_phase,
                "command_owner": getattr(runner, "last_owner", None),
            }
            current = db.get(ComfyInstance, payload["instance_id"])
            current.payload = {**result, "last_attempt": attempt_id}
            current.sha256, current.status = graph_hash(current.payload), "NEEDS_REVIEW"
            db.get(InstallationPlan, identity).status = "NEEDS_REVIEW"
        raise
    checkpoint("BEFORE_COMMIT")
    with factory.begin() as db:
        instance = db.get(ComfyInstance, payload["instance_id"])
        if not instance:
            instance = ComfyInstance(id=payload["instance_id"], payload={}, sha256=digest, status="CREATED")
            db.add(instance)
        instance.payload, instance.sha256 = result, graph_hash(result)
        instance.status = (
            "ROLLED_BACK"
            if op.action == "ROLLBACK"
            else (
                "MOCK_VERIFIED" if runner.is_mock else "RUNNING_PENDING_HEALTH" if result.get("process") else "STOPPED"
            )
        )
        attempt = db.get(InstallationAttempt, attempt_id)
        attempt.status = "SNAPSHOT" if op.action == "SNAPSHOT" else "COMPLETED"
        attempt.payload = {
            **attempt.payload,
            "result": result,
            "snapshot_path": str(backup) if snapshot else None,
            "files": snapshot,
            "is_mock": runner.is_mock,
        }
        attempt.sha256 = graph_hash(attempt.payload)
        db.get(InstallationPlan, identity).status = instance.status
        add_audit(
            db,
            actor,
            "INSTANCE_OPERATION_COMPLETED",
            "comfy_instance",
            instance.id,
            {"action": op.action, "attempt": attempt.id, "is_mock": runner.is_mock},
            project_id=op.project_id,
        )
    checkpoint("COMMITTED")
    return attempt_id
