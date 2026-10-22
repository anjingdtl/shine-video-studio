"""Explicit software-only HTTP execution, reusable by offline UI and tests."""

import json
from uuid import uuid4

import httpx

from shine.comfy.client import ComfyUIClient


class FakeComfyExecution:
    def __init__(self):
        self.history = {}
        self.requests = []

    def handle(self, request):
        if request.url.path == "/prompt":
            body = json.loads(request.content)
            self.requests.append(body)
            identity = str(uuid4())
            self.history[identity] = {
                "status": {"completed": True, "status_str": "success"},
                "prompt": [0, identity, body["prompt"], {}, []],
                "outputs": {},
            }
            return httpx.Response(200, json={"prompt_id": identity, "node_errors": {}})
        if request.url.path.startswith("/history/"):
            identity = request.url.path.split("/")[-1]
            return httpx.Response(200, json={identity: self.history[identity]} if identity in self.history else {})
        return httpx.Response(404)

    def client(self):
        return ComfyUIClient("http://127.0.0.1:1", transport=httpx.MockTransport(self.handle), max_retries=0)
