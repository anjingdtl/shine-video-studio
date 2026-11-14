"""节点包目录门禁的语义：生产必须拦，离线/夹具只降级为可诊断提示。

背景：d043a11 引入的 409 NODE_PACK_DIRECTORY_MISSING 对真实安装是对的——渲染器会把自定义
节点全部禁用，缺包必须先补装。但夹具/离线环境的 custom_nodes 里本来就没有这些包，于是
与节点包无关的测试在 setup 阶段被拦死。

修法不是删门禁，而是给门禁一个明确的模式：生产默认 enforce（缺包必须 409），
夹具用 diagnose 降级为可诊断提示——缺失必须被如实记录，不是静默通过。
"""

from dataclasses import replace
from pathlib import Path

from shine.comfyops import deployment
from shine.comfyops.instances import instance_identity, instance_root
from shine.comfyops.models import ComfyInstance
from tests.conftest import create_project
from tests.test_v13_preparation import HEADERS, prep  # noqa: F401


def _synthetic_root(settings):
    """一个合成安装根：custom_nodes 存在，但里面没有 catalog 的节点包目录。"""
    root = instance_root(settings, "shine-gate", "local")
    root.mkdir(parents=True, exist_ok=True)
    (root / ".venv" / "Scripts").mkdir(parents=True, exist_ok=True)
    (root / ".venv" / "Scripts" / "python.exe").write_bytes(b"FAKE EXECUTABLE - NEVER EXECUTE")
    (root / "custom_nodes").mkdir(exist_ok=True)
    (root / "main.py").write_bytes(b"# synthetic entrypoint")
    (root / ".shine-requirements.txt").write_text("# synthetic lock", encoding="utf-8")
    return root


def _seed_instance(factory, root, port=8195):
    with factory() as db:
        payload = {"managed": True, "name": "shine-gate", "port": port,
                   "root": str(root), "is_mock": False,
                   "model_bindings": {"diffusion_models": "minimax-h3-diffusion"}}
        db.add(ComfyInstance(id=instance_identity("local", port), payload=payload,
                             sha256=deployment.graph_hash(payload), status="STOPPED"))
        db.commit()


def _configure(client, settings, project, monkeypatch, port=8195):
    def fake_gpu():
        return {"status": "OK", "index": "0", "uuid": "GPU-TEST-UUID", "name": "Test GPU",
                "driver_version": "1.0", "vram_mb": 16384, "at_floor": True}

    monkeypatch.setattr("shine.comfyops.host_env.probe_gpu", fake_gpu)
    monkeypatch.setattr(deployment, "ENV_PATH", Path(settings.data_dir) / "setup-env")
    return client.post(
        "/api/v1/setup/configuration",
        json={"project_id": project, "instance_name": "shine-gate", "port": port},
        headers=HEADERS,
    )


def test_a_real_installation_missing_node_packs_is_still_refused(prep, monkeypatch):
    """生产语义保留：真实安装缺节点包必须 409 拦下。"""
    client, factory, settings = prep
    client.app.state.settings = replace(settings, node_pack_gate="enforce")
    project = create_project(client)
    root = _synthetic_root(settings)
    _seed_instance(factory, root)

    response = _configure(client, settings, project, monkeypatch)
    assert response.status_code == 409, response.text
    detail = response.json()["detail"]
    assert detail["code"] == "NODE_PACK_DIRECTORY_MISSING", detail
    assert detail["missing"], detail


def test_the_offline_fixture_gate_reports_the_missing_packs_instead_of_raising(prep, monkeypatch):
    """离线/夹具：降级为可诊断提示，缺失被如实记录，不是静默通过。"""
    client, factory, settings = prep
    client.app.state.settings = replace(settings, node_pack_gate="diagnose")
    project = create_project(client)
    root = _synthetic_root(settings)
    _seed_instance(factory, root)

    response = _configure(client, settings, project, monkeypatch)
    assert response.status_code == 200, response.text
    notes = response.json()["node_pack_notes"]
    assert notes and notes[0]["code"] == "NODE_PACK_DIRECTORY_MISSING", notes
    assert notes[0]["missing"], notes
    assert "custom_nodes" in notes[0]["message"], notes
