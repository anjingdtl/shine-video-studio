"""一个 tick 归谁：可独立推进的工作优先，后台活让路。

复现过的缺陷：连续四轮扫描都抢在可运行的文本任务前面（扫描在 tick 里排在文本
队列之前，每一轮都 ``return "resource-scan"``）；可选审片下载（画面审片 / 语音
核对）由安装队列占住整个 tick；整备任务在「等下载」时也把 tick 交出去，能推进的
队列就这样一直等。

这里固定：扫描排在文本之后（长文本让路时仍能拿到一轮，不会被饿死）；只搬可选
审片字节的安装不让出 tick；整备步骤只是等待时也不让出 tick。
"""

from __future__ import annotations

import pytest

from shine import worker
from shine.comfyops import installation_worker, resource_discovery, setup_preparation

pytestmark = pytest.mark.integration


@pytest.fixture
def quiet_tick(api, monkeypatch):
    """A machine with nothing else to do, so one queue's turn is observable."""
    client, factory, settings = api
    monkeypatch.setattr(installation_worker, "run_pending_installation",
                        lambda *args, **kwargs: None)
    monkeypatch.setattr(setup_preparation, "advance", lambda *args, **kwargs: None)
    monkeypatch.setattr(resource_discovery, "read_state",
                        lambda db: {"scan": {"status": "IDLE"}})
    monkeypatch.setattr(worker, "_text_preparation_ticks", 0)
    return factory, settings


def test_text_work_keeps_the_tick_while_a_scan_is_queued(quiet_tick, monkeypatch):
    factory, settings = quiet_tick
    scanned: list[int] = []
    monkeypatch.setattr(resource_discovery, "read_state",
                        lambda db: {"scan": {"status": "QUEUED"}})
    monkeypatch.setattr(resource_discovery, "advance_scan",
                        lambda db, **kwargs: scanned.append(1) or {"status": "RUNNING",
                                                                  "remaining": 3})
    monkeypatch.setattr(worker, "_advance_text_preparation", lambda *args, **kwargs: True)

    results = [worker.run_configured_once(factory, None, "worker-priority", settings=settings)
               for _ in range(4)]
    assert results == ["text-preparation"] * 4, "扫描不能连续四轮抢在文本任务前面"
    assert scanned == [], "文本任务可推进时，扫描不该占用 tick"


def test_a_long_text_run_still_lets_the_scan_advance(quiet_tick, monkeypatch):
    factory, settings = quiet_tick
    scanned: list[int] = []
    monkeypatch.setattr(resource_discovery, "read_state",
                        lambda db: {"scan": {"status": "QUEUED"}})
    monkeypatch.setattr(resource_discovery, "advance_scan",
                        lambda db, **kwargs: scanned.append(1) or {"status": "RUNNING",
                                                                  "remaining": 3})
    monkeypatch.setattr(worker, "_advance_text_preparation", lambda *args, **kwargs: True)
    # 文本连跑满一个突发窗口后让出一轮：扫描仍然能前进，不会被饿死。
    monkeypatch.setattr(worker, "_text_preparation_ticks", worker.TEXT_PREPARATION_BURST)

    result = worker.run_configured_once(factory, None, "worker-priority", settings=settings)
    assert result == "resource-scan"
    assert scanned == [1]


def test_an_advisory_review_install_does_not_own_the_tick(quiet_tick, monkeypatch):
    factory, settings = quiet_tick

    def serve(_factory, _worker_id, *, settings, detail=None):
        detail.update({"review_only": True})
        return "INSTALLING"

    monkeypatch.setattr(installation_worker, "run_pending_installation", serve)
    monkeypatch.setattr(worker, "_advance_text_preparation", lambda *args, **kwargs: True)

    result = worker.run_configured_once(factory, None, "worker-priority", settings=settings)
    assert result == "text-preparation", "可选审片下载不能挡住能推进的文本任务"


def test_a_production_install_still_owns_the_tick(quiet_tick, monkeypatch):
    factory, settings = quiet_tick

    def serve(_factory, _worker_id, *, settings, detail=None):
        detail.update({"review_only": False})
        return "INSTALLING"

    monkeypatch.setattr(installation_worker, "run_pending_installation", serve)
    monkeypatch.setattr(worker, "_advance_text_preparation", lambda *args, **kwargs: True)

    result = worker.run_configured_once(factory, None, "worker-priority", settings=settings)
    assert result == "installation:INSTALLING", "生产字节仍然先走"


def test_a_waiting_preparation_step_does_not_own_the_tick(quiet_tick, monkeypatch):
    factory, settings = quiet_tick
    monkeypatch.setattr(setup_preparation, "advance",
                        lambda *args, **kwargs: "setup:DOWNLOAD:waiting")
    monkeypatch.setattr(worker, "_advance_text_preparation", lambda *args, **kwargs: True)

    result = worker.run_configured_once(factory, None, "worker-priority", settings=settings)
    assert result == "text-preparation", "整备只是等字节时，不该把整个 tick 交出去"


def test_a_preparation_step_that_advanced_keeps_the_tick(quiet_tick, monkeypatch):
    factory, settings = quiet_tick
    monkeypatch.setattr(setup_preparation, "advance",
                        lambda *args, **kwargs: "setup:RECIPE")
    monkeypatch.setattr(worker, "_advance_text_preparation", lambda *args, **kwargs: True)

    result = worker.run_configured_once(factory, None, "worker-priority", settings=settings)
    assert result == "setup:RECIPE"
