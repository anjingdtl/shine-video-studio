"""Exercise the optional adapter over loopback using a synthetic Comfy queue.

Run with the target ComfyUI Python, without importing ComfyUI, torch or weights.
This is a protocol test, not evidence of a real ComfyUI handoff.
"""

import asyncio
import importlib.util
import os
import secrets
import shutil
import sys
import tempfile
import types
from pathlib import Path

from aiohttp import ClientSession, web


class Queue:
    def __init__(self):
        self.items = []

    def put(self, item):
        self.items.append(item)

    def get_current_queue(self):
        return [], list(self.items)


async def main():
    token = secrets.token_urlsafe(32)
    os.environ["SHINE_COMFY_HANDOFF_TOKEN"] = token
    app, routes, queue = web.Application(), web.RouteTableDef(), Queue()
    server = types.SimpleNamespace(app=app, routes=routes, prompt_queue=queue)
    sys.modules["server"] = types.SimpleNamespace(PromptServer=types.SimpleNamespace(instance=server))
    source = Path(__file__).resolve().parents[1] / "integrations" / "shine_comfy_handoff"
    scratch = tempfile.TemporaryDirectory(prefix="shine-handoff-probe-")
    copied = Path(scratch.name) / "adapter"
    shutil.copytree(source, copied, ignore=shutil.ignore_patterns("__pycache__"))
    path = copied / "__init__.py"
    spec = importlib.util.spec_from_file_location("shine_comfy_handoff_probe", path,
                                                  submodule_search_locations=[str(path.parent)])
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)

    @routes.post("/prompt")
    async def submit(request):
        queue.put(await request.json())
        return web.json_response({"accepted": True})

    history = {"prior-result": {"completed": True}}
    mutation_entered, mutation_continue = asyncio.Event(), asyncio.Event()

    @routes.post("/history")
    async def clear_history(request):
        data = await request.json()
        if data.get("wait"):
            mutation_entered.set()
            await mutation_continue.wait()
        if data.get("clear"):
            history.clear()
        return web.json_response({"cleared": True})

    app.add_routes(routes)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", 0)
    await site.start()
    port = site._server.sockets[0].getsockname()[1]
    base = f"http://127.0.0.1:{port}"
    headers = {"Authorization": "Bearer " + token}
    try:
        async with ClientSession() as client:
            async with client.post(base + "/shine-handoff/drain") as response:
                assert response.status == 401
            async with client.post(base + "/prompt", json={"external": True}) as response:
                assert response.status == 200
            async with client.post(base + "/shine-handoff/drain", headers=headers) as response:
                assert response.status == 409 and (await response.json())["reason"] == "SHINE_QUEUE_BUSY"
            assert queue.items == [{"external": True}]
            queue.items.clear()  # Synthetic queue only.
            mutation = asyncio.create_task(client.post(base + "/history", json={"wait": True}))
            await asyncio.wait_for(mutation_entered.wait(), timeout=5)
            draining = asyncio.create_task(client.post(base + "/shine-handoff/drain", headers=headers))
            await asyncio.sleep(0.05)
            assert not draining.done()  # Drain waits for an already admitted mutation.
            mutation_continue.set()
            response = await mutation
            assert response.status == 200
            response.release()
            async with await draining as response:
                result = await response.json()
                assert response.status == 200 and result["status"] == "drained" and result["pid"] == os.getpid()
                assert len(result["adapter_sha256"]) == 64
            async with client.post(base + "/prompt", json={"late": True}) as response:
                assert response.status == 409 and (await response.json())["error"] == "SHINE_COMFY_DRAINED"
            async with client.post(base + "/history", json={"clear": True}) as response:
                assert response.status == 409
            assert "prior-result" in history
            async with client.get(base + "/shine-handoff/status", headers=headers) as response:
                assert response.status == 200 and (await response.json())["status"] == "drained"
            assert not queue.items
            with (copied / "barrier.py").open("a", encoding="utf-8") as changed:
                changed.write("\n# probe-only source mutation\n")
            async with client.get(base + "/shine-handoff/status", headers=headers) as response:
                assert response.status == 409 and (await response.json())["reason"] == "SHINE_ADAPTER_SOURCE_CHANGED"
        print("PASS: synthetic adapter auth, busy queue, drain and late submission")
    finally:
        await runner.cleanup()
        scratch.cleanup()


if __name__ == "__main__":
    asyncio.run(main())
