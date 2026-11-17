"""服务栈必须自报读的是哪个数据目录、归属哪个 supervisor。

回归：验收实例接管默认端口后，页面读着空台账却无从分辨；presence 心跳
也发往硬编码 8790 的日常 supervisor，而不是服务栈自己的监督进程。
"""

import json

import pytest

pytestmark = pytest.mark.unit


def test_system_status_names_its_data_directory_and_supervisor(api):
    client, factory, settings = api
    body = client.get("/api/v1/system/status").json()
    assert body["data_dir"] == str(settings.data_dir)
    assert body["supervisor_port"] is None
    run = settings.data_dir / "run"
    run.mkdir(parents=True, exist_ok=True)
    (run / "supervisor.json").write_text(json.dumps({"pid": 1, "port": 8791}), encoding="utf-8")
    assert client.get("/api/v1/system/status").json()["supervisor_port"] == 8791
