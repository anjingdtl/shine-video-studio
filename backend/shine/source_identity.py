"""Which code is this process actually running? (launcher + workbench authority)

One implementation, three consumers: the Windows launcher records it in
``processes.json`` at start, the API exposes it on ``/health`` and
``/api/v1/system/status``, and the supervisor names it in its own health and
record.  The launcher compares the recorded identity with the current checkout
to decide whether a *running* stack must be replaced — a decision that is
deliberately separate from "did this run fast-forward Git".

Pure stdlib on purpose: the launcher must be able to compute it through any
interpreter before ``uv sync`` has installed the project's dependencies.

Identity rules:

* Git checkout: HEAD commit plus a content digest of every tracked change and
  every untracked file under the runtime roots (``backend``, ``frontend``,
  ``scripts``).  A docs-only edit does not change the identity; an edit to a
  running module does, even when it is never committed.
* Distribution package (no ``.git``): ``BUILD_INFO.json``'s source commit,
  package version and build time.
* Neither: a bounded marker digest, explicitly ``unknown`` — never silently
  treated as current.
"""
from __future__ import annotations

import hashlib
import json
import subprocess
from pathlib import Path
from typing import Any

#: Directories whose content decides what the running processes execute.
RUNTIME_ROOTS = ("backend", "frontend", "scripts")

#: Untracked files larger than this are represented by name+size, not bytes.
_UNTRACKED_CONTENT_CAP = 10 * 1024 * 1024

_CACHE: dict[str, dict[str, Any]] = {}


def _git(repo_root: Path, *args: str) -> tuple[int, str]:
    try:
        result = subprocess.run(
            ["git", "-C", str(repo_root), "-c", "core.quotepath=false", *args],
            capture_output=True, text=True, encoding="utf-8", errors="replace",
            timeout=20,
        )
    except (OSError, subprocess.TimeoutExpired):
        return 1, ""
    return result.returncode, result.stdout


def _sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8", errors="replace")).hexdigest()


def _untracked_fingerprint(repo_root: Path, status_text: str) -> str:
    parts: list[str] = []
    for line in status_text.splitlines():
        if not line.startswith("?? "):
            continue
        rel = line[3:].strip().strip('"')
        if not rel:
            continue
        path = repo_root / rel
        if path.is_dir():
            # An untracked directory (rare inside runtime roots): enumerate it
            # so a new module inside it still changes the identity.
            for child in sorted(path.rglob("*")):
                if child.is_file():
                    parts.append(_file_fingerprint(child))
        elif path.is_file():
            parts.append(_file_fingerprint(path))
    return _sha256_text("\n".join(parts))


def _file_fingerprint(path: Path) -> str:
    try:
        size = path.stat().st_size
        if size > _UNTRACKED_CONTENT_CAP:
            return f"{path.name}:{size}"
        return f"{path.name}:{size}:{hashlib.sha256(path.read_bytes()).hexdigest()}"
    except OSError:
        return f"{path.name}:unreadable"


def compute_source_identity(repo_root: Path | str) -> dict[str, Any]:
    """The identity of the code in ``repo_root`` right now (never cached stale)."""
    root = Path(repo_root).resolve()
    if (root / ".git").exists():
        code, head = _git(root, "rev-parse", "HEAD")
        if code == 0 and head.strip():
            head = head.strip()
            _, short = _git(root, "rev-parse", "--short", "HEAD")
            diff_code, diff_text = _git(root, "diff", "HEAD", "--", *RUNTIME_ROOTS)
            status_code, status_text = _git(root, "status", "--porcelain", "--", *RUNTIME_ROOTS)
            if diff_code == 0 and status_code == 0:
                dirty = bool(status_text.strip())
                material = "\n".join([
                    head, diff_text, status_text,
                    _untracked_fingerprint(root, status_text),
                ])
                digest = _sha256_text(material)
                display = f"git:{short.strip() or head[:7]}" + ("+改动" if dirty else "")
                return {
                    "kind": "git",
                    "commit": head,
                    "short_commit": short.strip() or head[:7],
                    "dirty": dirty,
                    "digest": digest,
                    "display": display,
                    "package_version": None,
                    "built_at": None,
                }
    build_info = root / "BUILD_INFO.json"
    if build_info.is_file():
        try:
            info = json.loads(build_info.read_text(encoding="utf-8-sig"))
        except (OSError, ValueError):
            info = {}
        commit = str(info.get("source_commit") or "")
        version = str(info.get("package_version") or "")
        built_at = str(info.get("built_at") or "")
        if commit or version:
            digest = _sha256_text("\n".join([commit, version, built_at]))
            short = str(info.get("source_short_commit") or commit[:7])
            return {
                "kind": "package",
                "commit": commit,
                "short_commit": short,
                "dirty": False,
                "digest": digest,
                "display": f"pkg:{short}" + (f" v{version}" if version else ""),
                "package_version": version or None,
                "built_at": built_at or None,
            }
    markers: list[str] = []
    for rel in ("backend/shine/main.py", "backend/shine/worker.py", "frontend/package.json"):
        path = root / rel
        if path.is_file():
            markers.append(f"{rel}:{_file_fingerprint(path)}")
    return {
        "kind": "unknown",
        "commit": None,
        "short_commit": None,
        "dirty": None,
        "digest": _sha256_text("\n".join(markers)) if markers else "",
        "display": "unknown",
        "package_version": None,
        "built_at": None,
    }


def source_identity(repo_root: Path | str | None = None) -> dict[str, Any]:
    """Cached identity of this installation's own checkout.

    The running process's code does not change while it lives, so one read per
    process is the truth; the launcher calls :func:`compute_source_identity`
    directly because the checkout *can* change between its invocations.
    """
    root = str(Path(repo_root).resolve()) if repo_root else str(Path(__file__).resolve().parents[2])
    if root not in _CACHE:
        _CACHE[root] = compute_source_identity(root)
    return dict(_CACHE[root])


def same_identity(recorded: Any, current: dict[str, Any]) -> bool:
    """A recorded identity matches only when both digests exist and are equal.

    An old manifest without an identity is *not* proven current: the launcher
    must replace that generation once so the record carries the fact.
    """
    if not isinstance(recorded, dict):
        return False
    recorded_digest = str(recorded.get("digest") or "")
    current_digest = str(current.get("digest") or "")
    return bool(recorded_digest) and recorded_digest == current_digest
