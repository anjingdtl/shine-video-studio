"""Release the backends this installation started before it starts a new stack.

ComfyUI and the local reviewers are part of the workbench, not of the machine:
they are started by this installation and they must not outlive it.  A
launcher overwrites its process record on every start, so a reviewer that was
started by an earlier run can lose its record and survive every stop.

Only processes whose ownership can be proven are touched:

* a managed renderer or reviewer is owned when its recorded PID/birth identity
  and the membership of its named owned group agree;
* a leftover reviewer is owned when its command line names this
  installation's own review script.

Anything else is reported and left alone.  A renderer that serves a running
attempt is never stopped.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "backend"))

from shine.comfyops.installer import process_identity  # noqa: E402
from shine.comfyops.instances import ProcessRunner  # noqa: E402
from shine.comfyops.renderer_release import (  # noqa: E402
    managed_review_ports,
    release_managed_processes,
    residual_owned_processes,
)


def _stack_is_running(data_dir: Path) -> bool:
    """Whether a recorded process of this stack is still alive."""
    manifest = data_dir / "run" / "processes.json"
    if not manifest.is_file():
        return False
    try:
        record = json.loads(manifest.read_text(encoding="utf-8-sig"))
    except (OSError, ValueError):
        return False
    for entry in record.get("processes", []):
        for owner in entry.get("owners", []):
            pid, birth = owner.get("pid"), owner.get("birth")
            if not isinstance(pid, int) or not isinstance(birth, str):
                continue
            try:
                if process_identity(pid) == birth:
                    return True
            except (OSError, ValueError):
                continue
    return False


def release_residual_backends(data_dir: Path, *, repo_root: Path = REPO_ROOT,
                              extra_ports: list[int] | None = None) -> dict:
    """Stop every owned backend of this installation and report the rest."""
    report: dict = {"managed": {"stopped": [], "refused": []}, "residual": [], "errors": []}
    if _stack_is_running(data_dir):
        report["errors"].append("the workbench stack is still running; stop it first")
        return report

    database = data_dir / "shine.db"
    review_ports: list[int] = []
    if database.is_file():
        try:
            from shine.db import make_engine, make_session_factory

            engine = make_engine(f"sqlite:///{database.as_posix()}")
            factory = make_session_factory(engine)
            with factory.begin() as db:
                result = release_managed_processes(db, actor="local-user", reason="launcher_residual_cleanup")
                review_ports = managed_review_ports(db)
            report["managed"] = {
                "stopped": [item.get("id") for item in result["stopped"]],
                "refused": [{"id": item.get("id"), "outcome": item.get("outcome"),
                             "reason": item.get("reason")} for item in result["refused"]],
            }
        except Exception as exc:  # noqa: BLE001 - the report must survive any backend failure
            report["errors"].append(f"managed release failed: {type(exc).__name__}")

    runner = ProcessRunner()
    for leftover in residual_owned_processes(sorted(set(review_ports) | set(extra_ports or [])),
                                            repo_root=str(repo_root)):
        pid = leftover["pid"]
        try:
            birth = process_identity(pid)
        except (OSError, ValueError):
            birth = None
        if not birth:
            report["residual"].append({"pid": pid, "outcome": "gone"})
            continue
        try:
            runner.stop({"pid": pid, "birth": birth})
            report["residual"].append({"pid": pid, "outcome": "stopped"})
        except (OSError, ValueError) as exc:
            report["residual"].append({"pid": pid, "outcome": "refused", "reason": type(exc).__name__})
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-dir", default=os.environ.get("SHINE_DATA_DIR", str(REPO_ROOT / ".local-data")))
    parser.add_argument("--review-port", action="append", type=int, default=[],
                        help="a loopback port this installation uses for a local reviewer")
    parser.add_argument("--json", action="store_true", help="print the report as JSON")
    args = parser.parse_args()
    report = release_residual_backends(Path(args.data_dir), extra_ports=list(args.review_port))
    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
    else:
        for item in report["managed"].get("stopped", []):
            print(f"[released] managed backend {item}")
        for item in report["managed"].get("refused", []):
            print(f"[refused] managed backend {item.get('id')}: {item.get('reason')}")
        for item in report["residual"]:
            print(f"[{item['outcome']}] residual backend pid {item['pid']}")
        for error in report["errors"]:
            print(f"[error] {error}")
    return 1 if report["errors"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
