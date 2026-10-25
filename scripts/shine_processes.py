"""Snapshot a newly launched Windows process tree with stable OS birth identities."""

from __future__ import annotations

import json
import subprocess
import sys

from shine.comfyops.installer import process_identity
from shine.comfyops.instances import ProcessRunner


def snapshot(pid: int, birth: str) -> list[dict]:
    if process_identity(pid) != birth:
        raise ValueError("Launcher identity changed or exited")
    completed = subprocess.run(
        [
            "powershell",
            "-NoProfile",
            "-Command",
            "Get-CimInstance Win32_Process | Select-Object ProcessId,ParentProcessId | ConvertTo-Json -Compress",
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    rows = json.loads(completed.stdout)
    if process_identity(pid) != birth:
        raise ValueError("Launcher identity changed during inspection")
    owned = {pid}
    ordered = [pid]
    while True:
        children = {
            int(r["ProcessId"]) for r in rows if int(r["ParentProcessId"]) in owned
        }
        if children.issubset(owned):
            break
        ordered.extend(sorted(children - owned))
        owned |= children
    owners = [{"pid": p, "birth": process_identity(p)} for p in ordered]
    return [owner for owner in owners if owner["birth"] is not None]


if __name__ == "__main__":
    owners = snapshot(int(sys.argv[1]), sys.argv[2])
    if "--stop" in sys.argv:
        for owner in reversed(owners):
            ProcessRunner().stop(owner)
    else:
        print(json.dumps(owners))
