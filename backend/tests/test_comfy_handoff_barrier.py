"""A renderer drain must serialize against every new queue insertion."""

import importlib.util
import threading
from pathlib import Path

import pytest


def barrier_class():
    source = Path(__file__).resolve().parents[2] / "integrations" / "shine_comfy_handoff" / "barrier.py"
    spec = importlib.util.spec_from_file_location("shine_handoff_barrier_test", source)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.DrainBarrier, module.DrainBusy


class Queue:
    def __init__(self):
        self.items = []

    def put(self, item):
        self.items.append(item)

    def get_current_queue(self):
        return [], list(self.items)


def test_drain_blocks_late_submission_and_never_discards_queued_work():
    DrainBarrier, DrainBusy = barrier_class()
    queue = Queue()
    barrier = DrainBarrier(queue)
    queue.put("external")
    with pytest.raises(DrainBusy):
        barrier.drain()
    assert queue.items == ["external"]
    queue.items.clear()
    assert barrier.drain() is True
    with pytest.raises(DrainBusy):
        queue.put("late")
    assert queue.items == []


def test_managed_start_is_drained_until_explicit_resume():
    DrainBarrier, DrainBusy = barrier_class()
    queue = Queue()
    barrier = DrainBarrier(queue, start_drained=True)
    assert barrier.state() == "drained"
    with pytest.raises(DrainBusy):
        queue.put("unleased")
    assert barrier.resume() is True
    queue.put("leased")
    assert queue.items == ["leased"]


def test_resume_rejects_busy_queue_and_replaced_guard():
    DrainBarrier, DrainBusy = barrier_class()
    queue = Queue()
    barrier = DrainBarrier(queue, start_drained=True)
    queue.items.append("unexpected")
    with pytest.raises(DrainBusy, match="QUEUE_BUSY"):
        barrier.resume()
    queue.items.clear()
    queue.put = lambda _item: None
    with pytest.raises(DrainBusy, match="GUARD_CHANGED"):
        barrier.resume()


def test_drain_waits_for_inflight_queue_insert_and_refuses_busy_queue():
    DrainBarrier, DrainBusy = barrier_class()
    queue = Queue()
    entered, release = threading.Event(), threading.Event()

    def slow_put(item):
        entered.set()
        assert release.wait(5)
        queue.items.append(item)

    queue.put = slow_put
    barrier = DrainBarrier(queue)
    thread = threading.Thread(target=queue.put, args=("external",))
    thread.start()
    assert entered.wait(5)
    result = []

    def drain():
        try:
            barrier.drain()
        except DrainBusy:
            result.append("busy")

    drainer = threading.Thread(target=drain)
    drainer.start()
    release.set()
    thread.join(5)
    drainer.join(5)
    assert not thread.is_alive() and not drainer.is_alive()
    assert result == ["busy"] and queue.items == ["external"]


def test_drain_rejects_unknown_queue_or_replaced_guard():
    DrainBarrier, DrainBusy = barrier_class()
    queue = Queue()
    barrier = DrainBarrier(queue)
    queue.get_current_queue = lambda: (None, [])
    with pytest.raises(DrainBusy, match="STATE_UNKNOWN"):
        barrier.drain()
    queue.get_current_queue = lambda: ([], [])
    queue.put = lambda _item: None
    with pytest.raises(DrainBusy, match="GUARD_CHANGED"):
        barrier.drain()


def test_control_auth_rejects_missing_short_and_incorrect_secrets():
    source = Path(__file__).resolve().parents[2] / "integrations" / "shine_comfy_handoff" / "barrier.py"
    spec = importlib.util.spec_from_file_location("shine_handoff_auth_test", source)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    token = "a" * 32
    assert module.authorized(token, "Bearer " + token)
    assert not module.authorized("short", "Bearer short")
    assert not module.authorized(token, None)
    assert not module.authorized(token, "Bearer " + "b" * 32)
    assert not module.authorized(token, "Bearer 中文")
