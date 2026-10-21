from __future__ import annotations

import json
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any
from urllib.parse import parse_qs, urlparse


class _BaseServer:
    def __init__(self) -> None:
        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), self._handler())
        self.thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)

    @property
    def base_url(self) -> str:
        host, port = self.httpd.server_address
        return f"http://{host}:{port}"

    def start(self) -> _BaseServer:
        self.thread.start()
        return self

    def stop(self) -> None:
        self.httpd.shutdown()
        self.httpd.server_close()
        self.thread.join(timeout=5)

    def _handler(self):
        raise NotImplementedError


class FakeOpenAIServer(_BaseServer):
    """A local fake OpenAI-compatible endpoint for integration tests."""

    def __init__(self) -> None:
        self.requests: list[dict[str, Any]] = []
        self.auth_headers: list[str | None] = []
        self.mode = "ok"
        self.response_content: str = json.dumps({"ok": True, "value": "mock"})
        self.required_key = "test-key"
        self.server_error_times = 0
        super().__init__()

    def _handler(self):
        outer = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *_args):
                return

            def _authorized(self) -> bool:
                return outer.required_key in (self.headers.get("Authorization") or "")

            def _json(self, status: int, payload: dict[str, Any]) -> None:
                body = json.dumps(payload).encode("utf-8")
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def do_GET(self) -> None:
                if self.path.startswith("/v1/models"):
                    if not self._authorized():
                        self._json(401, {"error": "unauthorized"})
                        return
                    self._json(200, {"data": [{"id": "test-model"}, {"id": "other-model"}]})
                    return
                self._json(404, {"error": "not found"})

            def do_POST(self) -> None:
                length = int(self.headers.get("Content-Length", "0"))
                raw = self.rfile.read(length)
                outer.requests.append(json.loads(raw or b"{}"))
                outer.auth_headers.append(self.headers.get("Authorization"))
                if self.path != "/v1/chat/completions":
                    self._json(404, {"error": "not found"})
                    return
                if not self._authorized():
                    self._json(401, {"error": "unauthorized"})
                    return
                if outer.mode == "status_500" and outer.server_error_times > 0:
                    outer.server_error_times -= 1
                    self._json(500, {"error": "temporary"})
                    return
                if outer.mode == "timeout":
                    time.sleep(0.6)
                if outer.mode == "status_400":
                    self._json(400, {"error": "response_format unsupported"})
                    return
                if outer.mode == "reject_json_mode":
                    body = json.loads(raw or b"{}")
                    if "response_format" in body:
                        self._json(400, {"error": "response_format unsupported"})
                        return
                self._json(
                    200,
                    {
                        "id": "chatcmpl-test",
                        "model": "test-model",
                        "choices": [{"message": {"content": outer.response_content}}],
                        "usage": {"prompt_tokens": 3, "completion_tokens": 4},
                    },
                )

        return Handler


class FakeComfyServer(_BaseServer):
    """Fake ComfyUI HTTP server covering queue, history, uploads, and outputs."""

    def __init__(self) -> None:
        self.mode = "ok"
        self.prompts: dict[str, dict[str, Any]] = {}
        self.queue_running: list[list[Any]] = []
        self.queue_pending: list[list[Any]] = []
        self.idempotency: dict[str, str] = {}
        self.output_bytes = b"fake-video-bytes"
        self.output_filename = "shine_output.mp4"
        self.upload_count = 0
        self.prompt_count = 0
        self.timeout_sleep_s = 0.6
        super().__init__()

    def _handler(self):
        outer = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *_args):
                return

            def _json(self, status: int, payload: dict[str, Any]) -> None:
                body = json.dumps(payload).encode("utf-8")
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def do_GET(self) -> None:
                parsed = urlparse(self.path)
                if parsed.path == "/system_stats":
                    self._json(200, {"system": {"comfyui_version": "fake"}, "devices": [{"name": "fake-gpu"}]})
                    return
                if parsed.path == "/object_info":
                    self._json(
                        200,
                        {
                            "H3BaseSampler": {
                                "input": {
                                    "required": {
                                        "positive_prompt": ["STRING"],
                                        "seed": ["INT"],
                                        "duration": ["FLOAT"],
                                    }
                                }
                            },
                            "SaveVideo": {"input": {"required": {"images": ["IMAGE"]}}},
                        },
                    )
                    return
                if parsed.path == "/queue":
                    self._json(200, {"queue_running": outer.queue_running, "queue_pending": outer.queue_pending})
                    return
                if parsed.path.startswith("/history/"):
                    prompt_id = parsed.path.rsplit("/", 1)[-1]
                    record = outer.prompts.get(prompt_id)
                    if record is None:
                        self._json(200, {})
                    else:
                        status = "success" if outer.mode != "error" else "error"
                        outputs = (
                            {}
                            if outer.mode == "missing_output"
                            else {
                                "9": {
                                    "gifs": [
                                        {
                                            "filename": outer.output_filename,
                                            "subfolder": "",
                                            "type": "output",
                                        }
                                    ]
                                }
                            }
                        )
                        self._json(
                            200,
                            {prompt_id: {"status": {"status_str": status}, "outputs": outputs}},
                        )
                    return
                if parsed.path == "/history":
                    self._json(200, outer.prompts)
                    return
                if parsed.path == "/view":
                    query = parse_qs(parsed.query)
                    if query.get("filename", [""])[0] != outer.output_filename:
                        self._json(404, {"error": "missing"})
                        return
                    self.send_response(200)
                    self.send_header("Content-Type", "video/mp4")
                    self.send_header("Content-Length", str(len(outer.output_bytes)))
                    self.end_headers()
                    self.wfile.write(outer.output_bytes)
                    return
                self._json(404, {"error": "not found"})

            def do_POST(self) -> None:
                parsed = urlparse(self.path)
                length = int(self.headers.get("Content-Length", "0"))
                raw = self.rfile.read(length)
                if parsed.path == "/upload/image":
                    outer.upload_count += 1
                    filename = "upload.png"
                    marker = b'filename="'
                    if marker in raw:
                        start = raw.index(marker) + len(marker)
                        end = raw.index(b'"', start)
                        filename = raw[start:end].decode("utf-8", errors="replace")
                    self._json(200, {"name": filename, "subfolder": "shine", "type": "input"})
                    return
                if parsed.path == "/prompt":
                    body = json.loads(raw or b"{}")
                    if outer.mode == "node_error":
                        self._json(200, {"prompt_id": "bad", "node_errors": {"1": "invalid input"}})
                        return
                    if outer.mode == "reject":
                        self._json(400, {"error": "invalid prompt"})
                        return
                    outer.prompt_count += 1
                    prompt_id = f"prompt-{outer.prompt_count}"
                    key = (body.get("extra_data") or {}).get("shine", {}).get("idempotency_key")
                    if key and key in outer.idempotency:
                        prompt_id = outer.idempotency[key]
                    elif key:
                        outer.idempotency[key] = prompt_id
                    outer.prompts[prompt_id] = {"prompt": body.get("prompt")}
                    if outer.mode == "timeout_then_accept":
                        time.sleep(outer.timeout_sleep_s)
                    self._json(200, {"prompt_id": prompt_id, "number": 1, "node_errors": {}})
                    return
                if parsed.path == "/interrupt":
                    self._json(200, {})
                    return
                self._json(404, {"error": "not found"})

        return Handler
