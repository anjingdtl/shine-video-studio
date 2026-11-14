"""Regression tests for the GPU model-release confirmation.

ComfyUI 0.39 reports the torch allocator's reserved pool and the reserved but
unallocated part of that pool. Comparing them directly can never succeed on
that build, so the shared exclusive GPU lease could never be released and every
later job stayed blocked. The confirmation has to look at the difference, which
is the live tensor bytes, and it still has to refuse an unknown reading.
"""

from __future__ import annotations

import pytest

from shine.comfy.client import ComfyUIClient, ComfyUIError
from tests.fake_http import FakeComfyServer


@pytest.fixture
def fake_comfy():
    server = FakeComfyServer().start()
    try:
        yield server
    finally:
        server.stop()


# A real loopback HTTP round-trip through a freshly built httpx client can take
# tens of milliseconds on a loaded CI runner (Windows especially).  The release
# window must cover that latency or a *confirmed* release is reported as
# unconfirmed purely because the first poll outran the budget.  The product
# default is 120 s; these positive cases only need a window that is robust to
# scheduler jitter, not a tight one.  Refusal cases keep a short window because
# they expect the wait to expire.
RELEASE_WINDOW_S = 5.0
REFUSAL_WINDOW_S = 0.05


def test_release_is_confirmed_when_the_model_is_gone(fake_comfy):
    """ComfyUI 0.39 shape: reserved 64 MiB, unallocated 46.75 MiB."""
    fake_comfy.comfyui_version = "0.39.0"
    fake_comfy.torch_vram_total = 67108864
    fake_comfy.torch_vram_free = 49017344
    client = ComfyUIClient(fake_comfy.base_url, timeout_s=2, model_release_timeout_s=RELEASE_WINDOW_S)
    client.free_models()


def test_release_is_refused_while_a_model_is_resident(fake_comfy):
    """A resident MiniMax-H3 checkpoint keeps gigabytes of live tensors."""
    fake_comfy.comfyui_version = "0.39.0"
    fake_comfy.torch_vram_total = 20 * 1024 * 1024 * 1024
    fake_comfy.torch_vram_free = 4 * 1024 * 1024 * 1024
    client = ComfyUIClient(fake_comfy.base_url, timeout_s=2, model_release_timeout_s=0.05)
    with pytest.raises(ComfyUIError):
        client.free_models()


def test_release_is_refused_for_a_loaded_model_with_a_gigabyte_of_live_tensors(fake_comfy):
    fake_comfy.comfyui_version = "0.39.0"
    fake_comfy.torch_vram_total = 20 * 1024 * 1024 * 1024
    fake_comfy.torch_vram_free = 19 * 1024 * 1024 * 1024
    client = ComfyUIClient(fake_comfy.base_url, timeout_s=2, model_release_timeout_s=0.05)
    with pytest.raises(ComfyUIError):
        client.free_models()


def test_release_is_refused_for_an_older_server_that_still_reserves_torch_memory(fake_comfy):
    """The pre-0.39 accounting is strict: any reservation blocks the release."""
    fake_comfy.comfyui_version = "0.38.0"
    fake_comfy.torch_vram_total = 1024
    fake_comfy.torch_vram_free = 0
    client = ComfyUIClient(fake_comfy.base_url, timeout_s=2, model_release_timeout_s=REFUSAL_WINDOW_S)
    with pytest.raises(ComfyUIError):
        client.free_models()
    fake_comfy.torch_vram_total = 0
    # The confirmation phase needs a window that survives CI HTTP latency.
    confirmed = ComfyUIClient(fake_comfy.base_url, timeout_s=2, model_release_timeout_s=RELEASE_WINDOW_S)
    confirmed.free_models()


def test_release_is_refused_when_the_reading_is_unknown(fake_comfy):
    """An unknown memory reading must keep the block, not fake a release."""
    fake_comfy.torch_vram_total = 67108864
    fake_comfy.torch_vram_free = 67108864
    client = ComfyUIClient(fake_comfy.base_url, timeout_s=2, model_release_timeout_s=0.05)
    fake_comfy.torch_vram_total = None
    fake_comfy.torch_vram_free = None
    with pytest.raises(ComfyUIError):
        client.free_models()


def test_release_is_refused_while_the_renderer_is_busy(fake_comfy):
    fake_comfy.queue_running = [[0, "prompt-1", {}, {}, ["1"]]]
    client = ComfyUIClient(fake_comfy.base_url, timeout_s=2)
    with pytest.raises(ComfyUIError):
        client.free_models()

@pytest.mark.parametrize("total,free", [
    (True, True), (-1, -1), (1024, 2048),
    (float("nan"), 0), (float("inf"), float("inf")),
])
def test_release_refuses_invalid_renderer_memory_accounting(total, free):
    stats = {"system": {"comfyui_version": "0.39.0"}, "devices": [
        {"torch_vram_total": total, "torch_vram_free": free}
    ]}
    assert not ComfyUIClient._release_confirmed(stats)


@pytest.mark.parametrize("queue", [{}, {"queue_running": [], "queue_pending": None},
                                    {"queue_running": False, "queue_pending": []}])
def test_unknown_queue_never_initiates_unload(queue):
    import httpx

    posts = []

    def handle(request):
        if request.method == "POST":
            posts.append(request.url.path)
            return httpx.Response(200, json={})
        return httpx.Response(200, json=queue)

    client = ComfyUIClient("http://127.0.0.1:1", transport=httpx.MockTransport(handle), max_retries=0)
    with pytest.raises(ComfyUIError, match="QUEUE_STATE_UNCONFIRMED"):
        client.free_models()
    assert posts == []


def test_unknown_queue_after_unload_never_confirms_release(monkeypatch):
    import httpx

    reads = 0

    def handle(request):
        nonlocal reads
        if request.url.path == "/queue":
            reads += 1
            return httpx.Response(200, json={"queue_running": [], "queue_pending": []} if reads == 1 else {})
        if request.url.path == "/system_stats":
            return httpx.Response(200, json={"devices": [{"torch_vram_total": 0, "torch_vram_free": 0}]})
        return httpx.Response(200, json={})

    client = ComfyUIClient("http://127.0.0.1:1", transport=httpx.MockTransport(handle), max_retries=0,
                           model_release_timeout_s=0.05, model_release_poll_interval_s=0.01)
    with pytest.raises(ComfyUIError, match="MODEL_RELEASE_UNCONFIRMED"):
        client.free_models()


def test_release_waits_longer_than_three_seconds_for_confirmed_unload(monkeypatch):
    import httpx

    clock = [0.0]
    monkeypatch.setattr("shine.comfy.client.time.monotonic", lambda: clock[0])
    monkeypatch.setattr("shine.comfy.client.time.sleep", lambda delay: clock.__setitem__(0, clock[0] + delay))
    client = ComfyUIClient(
        "http://127.0.0.1:1", transport=httpx.MockTransport(lambda _request: httpx.Response(200, json={})),
        max_retries=0, model_release_timeout_s=5, model_release_poll_interval_s=0.25,
    )
    monkeypatch.setattr(client, "queue", lambda **_kwargs: {"queue_running": [], "queue_pending": []})
    monkeypatch.setattr(
        client, "system_stats",
        lambda **_kwargs: {"system": {"comfyui_version": "0.39.0"}, "devices": [
            {"torch_vram_total": 20 * 1024 * 1024 * 1024, "torch_vram_free": 0}
            if clock[0] < 3.5 else {"torch_vram_total": 0, "torch_vram_free": 0}
        ]},
    )

    client.free_models()
    assert clock[0] >= 3.5
