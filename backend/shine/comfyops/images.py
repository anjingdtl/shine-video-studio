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


class ComfyImageProvider:
    is_mock = False
    name = "ComfyImageProvider"

    def __init__(self, client, *, poll_limit=300, interval=1):
        self.client, self.poll_limit, self.interval = client, poll_limit, interval
        self.is_mock = bool(getattr(client, "is_mock", False))

    def release_models(self):
        self.client.free_models()

    def generate(self, prompt, seed, workflow, references):
        import hashlib
        import time

        from shine.comfy.workflow import apply_parameters

        if workflow.get("category", "").startswith("H3_"):
            raise ValueError("IMAGE_BRIDGE_CANNOT_GENERATE_VIDEO")
        parameters = {"prompt": prompt, "seed": seed}
        slots = [s for s in workflow.get("reference_slots", []) if s.startswith("reference_picture_")]
        if len(references) > len(slots):
            raise ValueError("PENDING_WORKFLOW_MAPPING")
        info = self.client.object_info()
        from shine.comfyops.registry import WorkflowDraft, validate_workflow

        errors = validate_workflow(
            WorkflowDraft.model_validate(workflow),
            info,
            {key: value["revision"] for key, value in info.items() if "revision" in value},
        )
        if errors:
            raise ValueError("WORKFLOW_CONTRACT_CHANGED")
        for slot, content in zip(slots, references, strict=False):
            uploaded = self.client.upload_image(hashlib.sha256(content).hexdigest() + ".png", content)
            parameters[slot] = uploaded.get("name")
        graph = apply_parameters(workflow["graph"], workflow["parameter_map"], parameters)
        queued = self.client.queue_prompt(graph, extra_data={"shine_kind": "candidate_image"})
        prompt_id = queued.get("prompt_id")
        if not prompt_id:
            raise ValueError("IMAGE_SUBMISSION_OUTCOME_UNKNOWN")
        from shine.comfyops.model_calls import record_external_submission

        record_external_submission(self.name, prompt_id, graph)
        for _ in range(self.poll_limit):
            history = self.client.history(prompt_id).get(prompt_id)
            if history:
                if history.get("status", {}).get("status_str") == "error":
                    raise ValueError("IMAGE_EXECUTION_FAILED")
                for output in history.get("outputs", {}).values():
                    for item in output.get("images", []):
                        if item.get("type") != "output":
                            continue
                        content = self.client.view(
                            item["filename"], subfolder=item.get("subfolder", ""), file_type="output"
                        )
                        return {
                            "content": content,
                            "filename": item["filename"],
                            "is_mock": self.is_mock,
                            "model": ",".join(workflow["model_ids"]),
                            "prompt_id": prompt_id,
                        }
            time.sleep(self.interval)
        raise ValueError("IMAGE_OUTCOME_RECONCILIATION_REQUIRED")
