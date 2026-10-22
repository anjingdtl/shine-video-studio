"""Loopback HTTP voice fixture. Emits test tones, never speech or a cloned voice."""

import base64
import json
from http.server import BaseHTTPRequestHandler

from shine.voiceops.providers import FakeVoiceProvider
from tests.fake_http import _BaseServer


class FakeVoiceServer(_BaseServer):
    def __init__(self):
        self.provider = FakeVoiceProvider()
        self.calls = []
        super().__init__()

    def _handler(self):
        outer = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *_args):
                pass

            def do_POST(self):
                body = json.loads(self.rfile.read(int(self.headers.get("Content-Length", 0))))
                outer.calls.append(self.path)
                provider = outer.provider
                if self.path == "/design":
                    result = provider.design(body["description"], body["text"], body["character"])
                elif self.path == "/clone":
                    result = provider.clone(
                        base64.b64decode(body["reference_base64"]), body["reference_text"], body["character"]
                    )
                elif self.path == "/tts":
                    result = provider.synthesize(
                        body["text"],
                        body["character"],
                        body["conditioning"],
                        body["emotion"],
                        body["speed"],
                        body["pause"],
                    )
                elif self.path == "/asr":
                    result = provider.transcribe(base64.b64decode(body["audio_base64"]))
                elif self.path == "/align":
                    result = {"words": provider.align(base64.b64decode(body["audio_base64"]), body["text"])}
                elif self.path == "/conditioning":
                    import hashlib

                    digest = hashlib.sha256(base64.b64decode(body["audio_base64"])).hexdigest()
                    result = {"sha256": digest, "is_mock": True}
                elif self.path == "/free":
                    result = {"released": True, "is_mock": True}
                else:
                    self.send_error(404)
                    return
                if "audio" in result:
                    result["audio_base64"] = base64.b64encode(result.pop("audio")).decode()
                data = json.dumps(result).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

        return Handler
