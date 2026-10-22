"""Image provider port; real service wire format is Shine's adapter protocol."""

import base64
from typing import Protocol

import httpx

from shine.comfyops.assets import fake_image
from shine.security import require_loopback_url


class ImageProvider(Protocol):
    is_mock: bool
    name: str

    def generate(self, prompt: str, seed: int, workflow: dict, references: list[bytes]) -> dict: ...


class FakeImageProvider:
    is_mock = True
    name = "FakeImageProvider"

    def generate(self, prompt, seed, workflow, references):
        return {
            "content": fake_image(f"{seed}:{prompt}"),
            "filename": "fake.png",
            "is_mock": True,
            "model": "fake-image-v1",
        }


class LocalImageHTTPProvider:
    is_mock = False
    name = "LocalImageHTTPProvider"

    def release_models(self):
        response = self.client.post(self.base_url + "/free", json={"unload_models": True})
        response.raise_for_status()
        if response.json().get("released") is not True:
            raise ValueError("MODEL_RELEASE_UNCONFIRMED")

    def __init__(self, base_url, client=None):
        self.base_url = require_loopback_url(base_url).rstrip("/")
        self.client = client or httpx.Client(timeout=300, follow_redirects=False)

    def generate(self, prompt, seed, workflow, references):
        response = self.client.post(
            self.base_url + "/generate",
            json={
                "prompt": prompt,
                "seed": seed,
                "workflow": workflow,
                "references": [base64.b64encode(b).decode() for b in references],
            },
        )
        response.raise_for_status()
        result = response.json()
        result["content"] = base64.b64decode(result.pop("content_base64"), validate=True)
        return result
