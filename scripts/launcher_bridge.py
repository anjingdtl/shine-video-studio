#!/usr/bin/env python3
"""Versioned machine-readable bridge between the native launcher and the studio.

The launcher owns the cross-version install transaction and the pre-API phase.
Everything that needs to know the studio's authoritative facts — who owns a
process, whether a real production job is running, how to take a consistent
database snapshot — is answered here, by code that already owns those facts.

Protocol
--------
One JSON request object on stdin, one JSON response object on stdout::

    request  {"protocol": "shine-launcher-bridge/1",
              "operation": "stack_status",
              "arguments": {...}}
    response {"ok": true, "operation": "stack_status", "result": {...}}
    response {"ok": false, "operation": "stack_status",
              "error": {"code": "MANIFEST_MISSING", "message": "..."}}

The bridge never starts, stops or mutates a studio process. It only reads.
Stopping stays with ``scripts/stop_shine.py``, which already refuses to kill
a process whose identity it cannot prove.
"""

from __future__ import annotations

import json
import os
import socket
import sqlite3
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

PROTOCOL = "shine-launcher-bridge/1"

# Stable error codes. The launcher branches on these, never on prose.
BAD_REQUEST = "BAD_REQUEST"
UNSUPPORTED_PROTOCOL = "UNSUPPORTED_PROTOCOL"
UNKNOWN_OPERATION = "UNKNOWN_OPERATION"
INVALID_ARGUMENT = "INVALID_ARGUMENT"
MANIFEST_MISSING = "MANIFEST_MISSING"
MANIFEST_INVALID = "MANIFEST_INVALID"
DATABASE_MISSING = "DATABASE_MISSING"
BACKUP_FAILED = "BACKUP_FAILED"
INTERNAL = "INTERNAL"

# The recorded process identity vocabulary, mirrored from
# shine.comfyops.process_probe so both sides agree on the words.
SAME = "same"
GONE = "gone"
RECYCLED = "recycled"
UNKNOWN = "unknown"

# Job statuses that mean work is still in flight.
_ACTIVE_JOB_STATUSES = ("RUNNING", "QUEUED", "RETRYING", "RENDERING")
# A mock provider never touches a GPU, so it never blocks a maintenance step.
_REAL_PROVIDERS = ("comfyui",)


class BridgeError(Exception):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


def _require(arguments: dict[str, Any], name: str) -> Any:
    value = arguments.get(name)
    if value is None or value == "":
        raise BridgeError(INVALID_ARGUMENT, f"缺少参数 {name}")
    return value


def _data_dir(arguments: dict[str, Any]) -> Path:
    raw = str(_require(arguments, "data_dir"))
    path = Path(raw).expanduser()
    if not path.is_absolute():
        raise BridgeError(INVALID_ARGUMENT, "data_dir 必须是绝对路径")
    return path


def _database_path(data_dir: Path) -> Path:
    url = os.getenv("SHINE_DATABASE_URL")
    if url and url.startswith("sqlite:///"):
        raw = url[len("sqlite:///") :]
        if raw and raw != ":memory:":
            return Path(raw)
    return data_dir / "shine.db"


# --------------------------------------------------------------------------
# Process identity
# --------------------------------------------------------------------------


def _identity_module():
    try:
        from shine.comfyops.installer import process_identity
        from shine.comfyops.process_probe import (
            RECYCLED,
            SAME,
            GONE,
            UNKNOWN,
            classify_unverifiable,
            creation_stamps,
        )
    except Exception as error:  # pragma: no cover - import guard
        raise BridgeError(INTERNAL, f"无法加载进程身份模块：{error}") from error
    return process_identity, classify_unverifiable, creation_stamps, {
        "same": SAME,
        "gone": GONE,
        "recycled": RECYCLED,
        "unknown": UNKNOWN,
    }


def _classify_owners(owners: list[dict[str, Any]]) -> tuple[str, list[dict[str, Any]]]:
    """Classify every recorded owner of one process entry.

    The result is deliberately conservative: anything the OS cannot describe
    is ``unknown``, and unknown is never read as "stopped".
    """
    process_identity, classify_unverifiable, creation_stamps, vocabulary = _identity_module()
    stamps: dict[int, int] | None = None
    detail: list[dict[str, Any]] = []
    overall = GONE

    for owner in owners:
        pid = owner.get("pid")
        birth = owner.get("birth")
        if not isinstance(pid, int) or not birth:
            detail.append({"pid": pid, "state": UNKNOWN, "reason": "记录缺少进程身份"})
            overall = UNKNOWN if overall != SAME else SAME
            continue
        try:
            current = process_identity(pid)
        except Exception:
            current = None
            resolved = UNKNOWN
        else:
            if current is None:
                resolved = GONE
            elif current == birth:
                resolved = SAME
            else:
                if stamps is None:
                    stamps = creation_stamps()
                resolved = classify_unverifiable(pid, birth, stamps)
        detail.append({"pid": pid, "state": resolved})
        if resolved == SAME:
            overall = SAME
        elif resolved == RECYCLED and overall == GONE:
            overall = RECYCLED
        elif resolved == UNKNOWN and overall == GONE:
            overall = UNKNOWN
    return vocabulary.get(overall, overall), detail


def _port_owner(port: int) -> int:
    """The PID that currently listens on a local TCP port, or 0."""
    if port <= 0:
        return 0
    if os.name == "nt":
        command = (
            "Get-NetTCPConnection -LocalPort {0} -State Listen -ErrorAction SilentlyContinue "
            "| Select-Object -First 1 -ExpandProperty OwningProcess".format(port)
        )
        try:
            completed = subprocess.run(
                ["powershell", "-NoProfile", "-Command", command],
                check=False,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=20,
            )
        except (OSError, subprocess.SubprocessError):
            return 0
        try:
            return int((completed.stdout or "").strip() or 0)
        except ValueError:
            return 0
    # Non-Windows fallback: the port owner cannot be attributed to a PID here,
    # so the launcher is told "unknown owner" rather than given a guess.
    return 0


def _port_answers(port: int) -> bool:
    if port <= 0:
        return False
    try:
        with socket.create_connection(("127.0.0.1", port), timeout=1.5):
            return True
    except OSError:
        return False


def _operation_stack_status(arguments: dict[str, Any]) -> dict[str, Any]:
    data_dir = _data_dir(arguments)
    manifest_path = data_dir / "run" / "processes.json"
    if not manifest_path.is_file():
        return {"state": "not_running", "manifest_present": False, "processes": []}
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8-sig"))
    except (OSError, ValueError) as error:
        raise BridgeError(MANIFEST_INVALID, f"进程记录无法读取：{error}") from error
    if not isinstance(manifest, dict):
        raise BridgeError(MANIFEST_INVALID, "进程记录格式无效")

    entries: list[dict[str, Any]] = []
    for entry in manifest.get("processes", []) or []:
        if not isinstance(entry, dict):
            continue
        owners = entry.get("owners") or []
        state, detail = _classify_owners(owners if isinstance(owners, list) else [])
        entries.append(
            {
                "name": entry.get("name"),
                "pid": entry.get("pid"),
                "listener_pid": entry.get("listener_pid"),
                "state": state,
                "owners": detail,
                "command": entry.get("command"),
            }
        )

    api_port = int(manifest.get("api_port") or 0)
    web_port = int(manifest.get("web_port") or 0)
    api_owner = _port_owner(api_port)
    web_owner = _port_owner(web_port)

    recorded_api = next((item for item in entries if item["name"] == "api"), None)
    recorded_web = next((item for item in entries if item["name"] == "web"), None)
    api_expected = int((recorded_api or {}).get("listener_pid") or 0)
    web_expected = int((recorded_web or {}).get("listener_pid") or 0)

    api_conflict = bool(api_port and api_owner and api_expected and api_owner != api_expected)
    web_conflict = bool(web_port and web_owner and web_expected and web_owner != web_expected)

    alive = [item for item in entries if item["state"] == SAME]
    unverifiable = [item for item in entries if item["state"] == UNKNOWN]

    if not entries:
        state = "not_running"
    elif api_conflict or web_conflict:
        state = "port_conflict"
    elif len(alive) == len(entries) and api_owner == api_expected and web_owner == web_expected:
        state = "running"
    elif alive:
        state = "half_started"
    elif unverifiable:
        state = "unverifiable"
    else:
        state = "stale_record"

    return {
        "state": state,
        "manifest_present": True,
        "recorded_at": manifest.get("recorded_at"),
        "data_dir": str(data_dir),
        "api_port": api_port,
        "web_port": web_port,
        "api_port_owner": api_owner,
        "web_port_owner": web_owner,
        "api_answers": _port_answers(api_port),
        "web_answers": _port_answers(web_port),
        "port_conflict": api_conflict or web_conflict,
        "processes": entries,
    }


# --------------------------------------------------------------------------
# Production activity
# --------------------------------------------------------------------------


def _operation_active_production(arguments: dict[str, Any]) -> dict[str, Any]:
    """Report real production work and held resource leases.

    A mock render is not a reason to block a maintenance step; a real GPU
    render is. Unknown is reported as unknown and blocks, because a blocked
    upgrade is recoverable while a killed render is not.
    """
    data_dir = _data_dir(arguments)
    database = _database_path(data_dir)
    if not database.is_file():
        return {
            "state": "no_database",
            "active_jobs": [],
            "active_leases": [],
            "blocks_maintenance": False,
        }
    try:
        connection = sqlite3.connect(f"file:{database}?mode=ro", uri=True, timeout=5)
    except sqlite3.Error as error:
        raise BridgeError(DATABASE_MISSING, f"无法以只读方式打开数据库：{error}") from error
    try:
        connection.row_factory = sqlite3.Row
        tables = {
            row["name"]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()
        }
        active_jobs: list[dict[str, Any]] = []
        if "jobs" in tables:
            placeholders = ",".join("?" for _ in _ACTIVE_JOB_STATUSES)
            rows = connection.execute(
                "SELECT id, status, provider, lease_owner, lease_expires_at, updated_at "
                f"FROM jobs WHERE status IN ({placeholders})",
                _ACTIVE_JOB_STATUSES,
            ).fetchall()
            for row in rows:
                active_jobs.append(
                    {
                        "id": row["id"],
                        "status": row["status"],
                        "provider": row["provider"],
                        "lease_owner": row["lease_owner"],
                        "lease_expires_at": row["lease_expires_at"],
                        "updated_at": row["updated_at"],
                        "real_provider": str(row["provider"] or "").lower() in _REAL_PROVIDERS,
                    }
                )
        active_leases: list[dict[str, Any]] = []
        if "resource_leases" in tables:
            rows = connection.execute(
                "SELECT id, owner, gpu, status, exclusive, expires_at, heartbeat_at "
                "FROM resource_leases"
            ).fetchall()
            now = datetime.now(UTC).replace(tzinfo=None)
            for row in rows:
                expires = row["expires_at"]
                held = True
                if expires:
                    try:
                        held = datetime.fromisoformat(str(expires)) > now
                    except ValueError:
                        held = True
                if held:
                    active_leases.append(
                        {
                            "id": row["id"],
                            "owner": row["owner"],
                            "gpu": row["gpu"],
                            "status": row["status"],
                            "exclusive": bool(row["exclusive"]),
                            "expires_at": expires,
                        }
                    )
    except sqlite3.Error as error:
        raise BridgeError(DATABASE_MISSING, f"读取数据库失败：{error}") from error
    finally:
        connection.close()

    real_jobs = [job for job in active_jobs if job["real_provider"]]
    exclusive_leases = [lease for lease in active_leases if lease["exclusive"]]
    return {
        "state": "active" if (real_jobs or exclusive_leases) else "idle",
        "active_jobs": active_jobs,
        "active_leases": active_leases,
        "real_production_jobs": len(real_jobs),
        "exclusive_leases": len(exclusive_leases),
        "blocks_maintenance": bool(real_jobs or exclusive_leases),
    }


# --------------------------------------------------------------------------
# Database snapshot
# --------------------------------------------------------------------------


def _operation_sqlite_backup(arguments: dict[str, Any]) -> dict[str, Any]:
    """Take a consistent snapshot of the studio database.

    The SQLite online backup API is used, so the snapshot is consistent even
    while the studio is writing. A plain file copy of a live WAL database is
    not, and restoring one would lose the newest writes.
    """
    data_dir = _data_dir(arguments)
    destination = Path(str(_require(arguments, "destination"))).expanduser()
    if not destination.is_absolute():
        raise BridgeError(INVALID_ARGUMENT, "destination 必须是绝对路径")
    database = _database_path(data_dir)
    if not database.is_file():
        raise BridgeError(DATABASE_MISSING, "找不到工作台数据库，未创建备份")
    destination.parent.mkdir(parents=True, exist_ok=True)
    if destination.exists():
        destination.unlink()
    try:
        source = sqlite3.connect(f"file:{database}?mode=ro", uri=True, timeout=30)
        target = sqlite3.connect(destination)
        try:
            source.backup(target)
            integrity = target.execute("PRAGMA integrity_check").fetchone()
            pages = target.execute("PRAGMA page_count").fetchone()[0]
        finally:
            target.close()
    except sqlite3.Error as error:
        raise BridgeError(BACKUP_FAILED, f"数据库备份失败：{error}") from error
    finally:
        try:
            source.close()
        except Exception:
            pass
    if not integrity or str(integrity[0]).lower() != "ok":
        raise BridgeError(BACKUP_FAILED, "备份校验未通过，已丢弃该快照")
    return {
        "destination": str(destination),
        "size_bytes": destination.stat().st_size,
        "page_count": pages,
        "integrity": "ok",
    }


def _operation_database_write_state(arguments: dict[str, Any]) -> dict[str, Any]:
    """Report whether the database was written after a given moment.

    This is what keeps a rollback from overwriting work the new version has
    already accepted: a blind restore is only safe while the answer is no.
    """
    data_dir = _data_dir(arguments)
    since = arguments.get("since_unix")
    database = _database_path(data_dir)
    if not database.is_file():
        return {"state": "no_database", "written_since": False, "modified_unix": None}
    try:
        modified = database.stat().st_mtime
    except OSError as error:
        raise BridgeError(DATABASE_MISSING, f"无法读取数据库状态：{error}") from error
    written = False
    if since is not None:
        try:
            written = modified > float(since)
        except (TypeError, ValueError):
            raise BridgeError(INVALID_ARGUMENT, "since_unix 必须是数字") from None
    return {
        "state": "present",
        "written_since": written,
        "modified_unix": modified,
        "size_bytes": database.stat().st_size,
    }


OPERATIONS = {
    "stack_status": _operation_stack_status,
    "active_production": _operation_active_production,
    "sqlite_backup": _operation_sqlite_backup,
    "database_write_state": _operation_database_write_state,
}


def main() -> int:
    try:
        raw = sys.stdin.read()
    except Exception:
        raw = ""
    try:
        request = json.loads(raw or "{}")
    except ValueError as error:
        _emit(False, None, None, BAD_REQUEST, f"请求不是有效 JSON：{error}")
        return 2
    if not isinstance(request, dict):
        _emit(False, None, None, BAD_REQUEST, "请求必须是 JSON 对象")
        return 2
    protocol = request.get("protocol")
    operation = request.get("operation")
    arguments = request.get("arguments") or {}
    if protocol != PROTOCOL:
        _emit(
            False,
            operation,
            None,
            UNSUPPORTED_PROTOCOL,
            f"不支持的桥接协议版本：{protocol}",
        )
        return 2
    if not isinstance(operation, str) or operation not in OPERATIONS:
        _emit(False, operation, None, UNKNOWN_OPERATION, f"未知操作：{operation}")
        return 2
    if not isinstance(arguments, dict):
        _emit(False, operation, None, BAD_REQUEST, "arguments 必须是 JSON 对象")
        return 2
    try:
        result = OPERATIONS[operation](arguments)
    except BridgeError as error:
        _emit(False, operation, None, error.code, error.message)
        return 1
    except Exception as error:  # pragma: no cover - defensive
        _emit(False, operation, None, INTERNAL, f"桥接操作失败：{error}")
        return 1
    _emit(True, operation, result, None, None)
    return 0


def _emit(
    ok: bool,
    operation: str | None,
    result: Any,
    error_code: str | None,
    error_message: str | None,
) -> None:
    payload: dict[str, Any] = {"ok": ok, "protocol": PROTOCOL, "operation": operation}
    if ok:
        payload["result"] = result
    else:
        payload["error"] = {"code": error_code, "message": error_message}
    sys.stdout.write(json.dumps(payload, ensure_ascii=False))
    sys.stdout.flush()


if __name__ == "__main__":
    raise SystemExit(main())
