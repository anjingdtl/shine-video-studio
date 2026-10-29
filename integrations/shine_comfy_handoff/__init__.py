"""Optional, separately installed ComfyUI handoff adapter.

Only an explicitly configured dedicated process enables this adapter. The
barrier remains closed until the process exits; it never deletes queue/history.
"""

import asyncio
import hashlib
import os
from pathlib import Path

from aiohttp import web
from server import PromptServer

from .barrier import DrainBarrier, DrainBusy, authorized

NODE_CLASS_MAPPINGS = {}
NODE_DISPLAY_NAME_MAPPINGS = {}

_token = os.environ.get("SHINE_COMFY_HANDOFF_TOKEN", "")
_start_mode = os.environ.get("SHINE_COMFY_START_DRAINED", "0")
if _start_mode not in {"0", "1"} or (_start_mode == "1" and len(_token) < 32):
    raise RuntimeError("SHINE_HANDOFF_START_CONFIGURATION_INVALID")
if len(_token) >= 32:
    _server = PromptServer.instance
    _barrier = DrainBarrier(_server.prompt_queue, start_drained=_start_mode == "1")
    _mutation_lock = asyncio.Lock()

    def _adapter_identity():
        files = (Path(__file__), Path(__file__).with_name("barrier.py"))
        return hashlib.sha256(b"".join(hashlib.sha256(path.read_bytes()).digest() for path in files)).hexdigest()

    _source_sha256 = _adapter_identity()

    @web.middleware
    async def _drained_response(request, handler):
        try:
            if request.method not in {"GET", "HEAD", "OPTIONS"} and request.path in {
                "/history", "/api/history", "/queue", "/api/queue",
            }:
                async with _mutation_lock:
                    if _barrier.state() == "drained":
                        raise DrainBusy("SHINE_COMFY_DRAINED")
                    return await handler(request)
            return await handler(request)
        except DrainBusy:
            return web.json_response({"error": "SHINE_COMFY_DRAINED"}, status=409)

    _server.app.middlewares.append(_drained_response)

    @_server.routes.post("/shine-handoff/drain")
    async def _drain(request):
        if not authorized(_token, request.headers.get("Authorization")):
            raise web.HTTPUnauthorized()
        async with _mutation_lock:
            try:
                if _adapter_identity() != _source_sha256:
                    raise DrainBusy("SHINE_ADAPTER_SOURCE_CHANGED")
                _barrier.drain()
            except (DrainBusy, OSError) as exc:
                return web.json_response({"status": "blocked", "reason": str(exc)}, status=409)
        return web.json_response({"status": "drained", "protocol": 1, "pid": os.getpid(),
                                  "adapter_sha256": _source_sha256})

    @_server.routes.post("/shine-handoff/resume")
    async def _resume(request):
        if not authorized(_token, request.headers.get("Authorization")):
            raise web.HTTPUnauthorized()
        async with _mutation_lock:
            try:
                if _adapter_identity() != _source_sha256:
                    raise DrainBusy("SHINE_ADAPTER_SOURCE_CHANGED")
                _barrier.resume()
            except (DrainBusy, OSError) as exc:
                return web.json_response({"status": "blocked", "reason": str(exc)}, status=409)
        return web.json_response({"status": "accepting", "protocol": 1, "pid": os.getpid(),
                                  "adapter_sha256": _source_sha256})

    @_server.routes.get("/shine-handoff/status")
    async def _status(request):
        if not authorized(_token, request.headers.get("Authorization")):
            raise web.HTTPUnauthorized()
        try:
            if _adapter_identity() != _source_sha256:
                raise DrainBusy("SHINE_ADAPTER_SOURCE_CHANGED")
            state = _barrier.state()
        except (DrainBusy, OSError) as exc:
            return web.json_response({"status": "blocked", "reason": str(exc)}, status=409)
        return web.json_response({"status": state, "protocol": 1, "pid": os.getpid(),
                                  "adapter_sha256": _source_sha256})
