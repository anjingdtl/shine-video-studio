"""A precheck driven by the real installation plan.

The plan already names every pinned file, its origin and its redirect policy.
The precheck therefore asks each real source for one byte with
"Range: bytes=0-0" and checks the redirect contract, so the operator sees
which source is unreachable, whether it can be resumed and whether it blocks
the install.

"DEGRADED" is never presented as "everything passed": a source that answers
but ignores the Range is degraded, because a weak network then has to restart
the file instead of continuing it.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from urllib.parse import urljoin, urlsplit

import httpx

from shine.comfyops.installer import check_url

logger = logging.getLogger(__name__)

MAX_REDIRECTS = 5


@dataclass
class SourceProbe:
    """What one pinned source answered."""

    url: str
    host: str
    reachable: bool = False
    resumable: bool = False
    blocking: bool = True
    error_code: str | None = None
    message: str | None = None
    redirects: list[str] = field(default_factory=list)

    def as_dict(self) -> dict:
        return {
            "url": self.url,
            "host": self.host,
            "reachable": self.reachable,
            "resumable": self.resumable,
            "blocking": self.blocking,
            "error_code": self.error_code,
            "message": self.message,
            "redirects": list(self.redirects),
        }


def default_opener(url: str, headers: dict) -> tuple[int, dict]:
    """Ask one source for a single byte and read only its answer."""
    with httpx.stream(
        "GET", url, headers=headers, follow_redirects=False, timeout=httpx.Timeout(20, read=30)
    ) as response:
        return response.status_code, dict(response.headers)


class PlanPrecheck:
    """Check every source of a real plan with a light Range request."""

    def __init__(self, opener=None):
        self.opener = opener or default_opener

    def check(self, plan_payload: dict) -> dict:
        sources: list[SourceProbe] = []
        for package in plan_payload.get("packages", []):
            for file in package.get("spec", {}).get("files", []):
                sources.append(self.check_file(file))
        unreachable = [s for s in sources if not s.reachable]
        not_resumable = [s for s in sources if s.reachable and not s.resumable]
        status = "BLOCKED" if unreachable else ("DEGRADED" if not_resumable else "OK")
        return {
            "status": status,
            "sources": [s.as_dict() for s in sources],
            "unreachable": [s.host for s in unreachable],
            "not_resumable": [s.host for s in not_resumable],
            "blocking": bool(unreachable),
            "summary": self._summary(status, unreachable, not_resumable),
        }

    @staticmethod
    def _summary(status: str, unreachable, not_resumable) -> str:
        if status == "OK":
            return "Every planned source answers and supports byte continuation."
        if status == "BLOCKED":
            hosts = ", ".join(sorted({s.host for s in unreachable}))
            return f"Unreachable planned sources block the install: {hosts}."
        hosts = ", ".join(sorted({s.host for s in not_resumable}))
        return f"Reachable, but not resumable (the server ignores Range): {hosts}."

    def check_file(self, file: dict) -> SourceProbe:
        url = file.get("url", "")
        host = urlsplit(url).hostname or ""
        probe = SourceProbe(url=url, host=host)
        allowed = {host} | set(file.get("redirect_hosts") or [])
        current = url
        for _hop in range(MAX_REDIRECTS + 1):
            try:
                status, headers = self.opener(current, {"Range": "bytes=0-0"})
            except Exception as exc:  # noqa: BLE001 - any transport failure is a source failure
                probe.error_code = "SOURCE_UNREACHABLE"
                probe.message = type(exc).__name__
                return probe
            if status in {301, 302, 303, 307, 308}:
                location = headers.get("location")
                if not location:
                    probe.error_code = "REDIRECT_LOCATION_MISSING"
                    probe.message = "The source redirected without a target."
                    return probe
                target = urljoin(current, location)
                try:
                    check_url(target, allowed, signed_query=True)
                except ValueError:
                    probe.error_code = "UNTRUSTED_REDIRECT"
                    probe.message = "The source redirects outside its declared hosts."
                    return probe
                probe.redirects.append(target)
                current = target
                continue
            if status in {200, 206}:
                probe.reachable = True
                content_range = headers.get("content-range", "")
                probe.resumable = status == 206 and content_range.startswith("bytes 0-0/")
                if not probe.resumable:
                    probe.error_code = "RANGE_NOT_SUPPORTED"
                    probe.message = "The source answers but ignores Range; a broken download restarts the file."
                return probe
            probe.error_code = f"SOURCE_HTTP_{status}"
            probe.message = "The source refused the request."
            return probe
        probe.error_code = "REDIRECT_LIMIT"
        probe.message = "The source redirects too often."
        return probe
