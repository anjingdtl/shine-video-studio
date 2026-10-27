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


def test_release_is_confirmed_when_the_model_is_gone(fake_comfy):
    """ComfyUI 0.39 shape: reserved 64 MiB, unallocated 46.75 MiB."""
    fake_comfy.comfyui_version = "0.39.0"
    fake_comfy.torch_vram_total = 67108864
    fake_comfy.torch_vram_free = 49017344
    client = ComfyUIClient(fake_comfy.base_url, timeout_s=2)
    client.free_models()


def test_release_is_refused_while_a_model_is_resident(fake_comfy):
    """A resident MiniMax-H3 checkpoint keeps gigabytes of live tensors."""
    fake_comfy.comfyui_version = "0.39.0"
    fake_comfy.torch_vram_total = 20 * 1024 * 1024 * 1024
    fake_comfy.torch_vram_free = 4 * 1024 * 1024 * 1024
    client = ComfyUIClient(fake_comfy.base_url, timeout_s=2)
    with pytest.raises(ComfyUIError):
        client.free_models()


def test_release_is_refused_for_a_loaded_model_with_a_gigabyte_of_live_tensors(fake_comfy):
    fake_comfy.comfyui_version = "0.39.0"
    fake_comfy.torch_vram_total = 20 * 1024 * 1024 * 1024
    fake_comfy.torch_vram_free = 19 * 1024 * 1024 * 1024
    client = ComfyUIClient(fake_comfy.base_url, timeout_s=2)
    with pytest.raises(ComfyUIError):
        client.free_models()


def test_release_is_refused_for_an_older_server_that_still_reserves_torch_memory(fake_comfy):
    """The pre-0.39 accounting is strict: any reservation blocks the release."""
    fake_comfy.comfyui_version = "0.38.0"
    fake_comfy.torch_vram_total = 1024
    fake_comfy.torch_vram_free = 0
    client = ComfyUIClient(fake_comfy.base_url, timeout_s=2)
    with pytest.raises(ComfyUIError):
        client.free_models()
    fake_comfy.torch_vram_total = 0
    client.free_models()


def test_release_is_refused_when_the_reading_is_unknown(fake_comfy):
    """An unknown memory reading must keep the block, not fake a release."""
    fake_comfy.torch_vram_total = 67108864
    fake_comfy.torch_vram_free = 67108864
    client = ComfyUIClient(fake_comfy.base_url, timeout_s=2)
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
