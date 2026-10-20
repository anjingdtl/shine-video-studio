from __future__ import annotations

import hashlib
import time
from dataclasses import dataclass


class ProviderFailure(RuntimeError):
    def __init__(self, code: str):
        self.code = code
        super().__init__(code)


class SubmissionUncertain(RuntimeError):
    """The remote provider may have accepted a request whose acknowledgement was lost."""


@dataclass(frozen=True)
class MockSubmission:
    provider_job_id: str
    duplicate: bool
    simulated: bool = True


class MockH3Provider:
    """A deterministic workflow simulator. It never creates or returns a video file."""

    def __init__(self) -> None:
        self._accepted: dict[str, str] = {}
        self.submission_count = 0

    def submit(self, idempotency_key: str, scenario: str) -> MockSubmission:
        existing = self._accepted.get(idempotency_key)
        if existing:
            return MockSubmission(existing, duplicate=True)
        if scenario == "fail":
            self.submission_count += 1
            raise ProviderFailure("MOCK_RENDER_FAILURE")
        if scenario == "delay":
            time.sleep(0.15)
        provider_job_id = f"mock-{hashlib.sha256(idempotency_key.encode()).hexdigest()[:20]}"
        self._accepted[idempotency_key] = provider_job_id
        self.submission_count += 1
        if scenario == "uncertain":
            raise SubmissionUncertain("MOCK_ACK_LOST")
        return MockSubmission(provider_job_id, duplicate=(scenario == "duplicate"))

    def reconcile(self, idempotency_key: str) -> str | None:
        return self._accepted.get(idempotency_key)
