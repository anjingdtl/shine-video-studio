"""Upstream reachability report: honest blockers, no secrets, no network in tests."""

from __future__ import annotations

import httpx

from shine.comfyops.connectivity import PROBES, connectivity_blockers, probe_upstreams


class FakeResponse:
    def __init__(self, status_code: int):
        self.status_code = status_code

    def __enter__(self):
        return self

    def __exit__(self, *exc_info):
        return False


class FakeClient:
    """Records calls and answers per host, so the policy is testable offline."""

    def __init__(self, behaviour):
        self.behaviour = behaviour
        self.calls: list[tuple[str, dict]] = []
        self.closed_streams = 0

    def stream(self, method, url, headers=None, timeout=None):
        self.calls.append((url, dict(headers or {})))
        result = self.behaviour(url)
        if isinstance(result, Exception):
            raise result
        return FakeResponse(result)


def host_of(url: str) -> str:
    for probe in PROBES:
        if probe["url"] == url:
            return probe["host"]
    raise AssertionError(f"unexpected url {url}")


def test_all_upstreams_reachable_reports_ok():
    client = FakeClient(lambda url: 200)
    report = probe_upstreams(client=client)
    assert report["status"] == "OK"
    assert report["blocked"] == [] and report["degraded"] == []
    assert {row["host"] for row in report["upstreams"]} == {
        "huggingface.co", "codeload.github.com", "files.pythonhosted.org", "download.pytorch.org"}
    # One tiny ranged GET per host, never a full artifact.
    assert len(client.calls) == len(PROBES)
    assert all(call[1] == {"Range": "bytes=0-0"} for call in client.calls)


def test_transport_failure_is_a_named_blocker_without_leaking_urls():
    def behaviour(url):
        return httpx.ConnectError("blocked") if host_of(url) == "huggingface.co" else 200

    report = probe_upstreams(client=FakeClient(behaviour))
    assert report["status"] == "PARTIAL"
    assert report["blocked"] == ["huggingface"]
    blockers = connectivity_blockers(report)
    assert blockers == [{"code": "UPSTREAM_UNREACHABLE", "host": "huggingface.co", "error": "ConnectError"}]
    for row in report["upstreams"]:
        assert "url" not in row and "?" not in str(row.get("error") or "")


def test_every_upstream_blocked_reports_blocked():
    report = probe_upstreams(client=FakeClient(lambda url: httpx.ConnectTimeout("no route")))
    assert report["status"] == "BLOCKED"
    assert len(report["blocked"]) == len(PROBES)
    assert len(connectivity_blockers(report)) == len(PROBES)


def test_server_error_is_degraded_not_a_false_success():
    def behaviour(url):
        return 503 if host_of(url) == "download.pytorch.org" else 200

    report = probe_upstreams(client=FakeClient(behaviour))
    assert report["status"] == "PARTIAL"
    assert report["blocked"] == [] and report["degraded"] == ["torch"]
    torch_row = next(row for row in report["upstreams"] if row["id"] == "torch")
    assert torch_row["status"] == "DEGRADED" and torch_row["http_status"] == 503


def test_guidance_explains_how_to_recover():
    report = probe_upstreams(client=FakeClient(lambda url: 200))
    assert any("代理" in line or "VPN" in line for line in report["guidance"])
    assert report["checked_at"]
