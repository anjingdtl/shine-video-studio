"""Start an explicitly managed ComfyUI only after its queue fence is installed.

This entry point does not create approvals, resource leases, or start any job.
The caller must own the process group and retain its resource ledger.
"""

import os
import re
import sys
from pathlib import Path


def verify_before_listen(server, adapter_file, expected_sha256, modules):
    target = Path(adapter_file).resolve(strict=True)
    matches = []
    for module in modules:
        try:
            source = vars(module).get("__file__")
            if source and Path(source).resolve() == target:
                matches.append(module)
        except (TypeError, ValueError, OSError):
            continue
    if len(matches) != 1:
        raise ValueError("COMFY_BOOTSTRAP_ADAPTER_UNVERIFIED")
    adapter = matches[0]
    try:
        valid = (adapter._server is server and adapter._barrier._queue is server.prompt_queue
                 and adapter._barrier.state() == "drained" and adapter._source_sha256 == expected_sha256
                 and adapter._adapter_identity() == expected_sha256)
    except Exception as exc:
        raise ValueError("COMFY_BOOTSTRAP_ADAPTER_UNVERIFIED") from exc
    if not valid:
        raise ValueError("COMFY_BOOTSTRAP_ADAPTER_UNVERIFIED")
    return True


def main():
    root = Path(os.environ.get("SHINE_COMFY_BOOTSTRAP_ROOT", ""))
    adapter_file = Path(os.environ.get("SHINE_COMFY_ADAPTER_FILE", ""))
    digest = os.environ.get("SHINE_COMFY_EXPECTED_ADAPTER_SHA256", "")
    if (not root.is_absolute() or not (root / "main.py").is_file() or not adapter_file.is_absolute()
            or not adapter_file.is_file() or not re.fullmatch("[a-f0-9]{64}", digest)
            or os.environ.get("SHINE_COMFY_START_DRAINED") != "1"
            or len(os.environ.get("SHINE_COMFY_HANDOFF_TOKEN", "")) < 32):
        raise ValueError("COMFY_BOOTSTRAP_CONFIGURATION_INVALID")
    sys.path.insert(0, str(root.resolve()))
    import main as comfy_main

    if Path(comfy_main.__file__).resolve() != (root / "main.py").resolve():
        raise ValueError("COMFY_BOOTSTRAP_ENTRY_CHANGED")
    if comfy_main.args.listen != "127.0.0.1" or not 0 < comfy_main.args.port < 65536:
        raise ValueError("COMFY_BOOTSTRAP_LISTEN_INVALID")
    loop, server, start_all = comfy_main.start_comfyui()
    try:
        verify_before_listen(server, adapter_file, digest, tuple(sys.modules.values()))
        routes = {(route.method, route.resource.canonical) for route in server.app.router.routes()
                  if route.resource is not None}
        required = {("GET", "/shine-handoff/status"), ("POST", "/shine-handoff/drain"),
                    ("POST", "/shine-handoff/resume")}
        if not required.issubset(routes):
            raise ValueError("COMFY_BOOTSTRAP_ROUTES_UNVERIFIED")
        loop.run_until_complete(start_all())
    except KeyboardInterrupt:
        pass
    finally:
        server.asset_manager.shutdown()


if __name__ == "__main__":
    main()
