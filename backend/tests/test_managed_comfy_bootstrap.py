"""A managed renderer must establish its queue fence before opening a socket."""

import importlib.util
from pathlib import Path
from types import SimpleNamespace

import pytest


def bootstrap():
    path = Path(__file__).resolve().parents[2] / "scripts" / "managed_comfy_bootstrap.py"
    spec = importlib.util.spec_from_file_location("shine_managed_bootstrap_test", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_bootstrap_rejects_missing_or_accepting_adapter_before_listen(tmp_path):
    module = bootstrap()
    path = tmp_path / "__init__.py"
    path.write_text("synthetic", encoding="utf-8")
    queue = object()
    server = SimpleNamespace(prompt_queue=queue)
    barrier = SimpleNamespace(_queue=queue, state=lambda: "accepting")
    adapter = SimpleNamespace(__file__=str(path), _server=server, _barrier=barrier,
                              _source_sha256="a" * 64, _adapter_identity=lambda: "a" * 64)
    with pytest.raises(ValueError, match="ADAPTER_UNVERIFIED"):
        module.verify_before_listen(server, path, "a" * 64, [])
    with pytest.raises(ValueError, match="ADAPTER_UNVERIFIED"):
        module.verify_before_listen(server, path, "a" * 64, [adapter])
    barrier.state = lambda: "drained"
    assert module.verify_before_listen(server, path, "a" * 64, [adapter]) is True
    adapter._adapter_identity = lambda: "b" * 64
    with pytest.raises(ValueError, match="ADAPTER_UNVERIFIED"):
        module.verify_before_listen(server, path, "a" * 64, [adapter])
