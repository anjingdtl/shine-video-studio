"""Contract tests for the loopback-only boundary of the local API.

The ordinary project API has no per-request authentication, so it must only
answer a client on this machine. A socket bound to the unspecified address
(``0.0.0.0``) is reachable from the network and must never pass as "local".
"""

import importlib

import pytest

from shine.security import is_loopback_peer, is_loopback_url

pytestmark = pytest.mark.unit


@pytest.mark.parametrize(
    "url",
    [
        "http://127.0.0.1:8188",
        "http://localhost:8188",
        "http://[::1]:8188",
        "http://127.0.0.53:8188",
    ],
)
def test_local_urls_are_loopback(url):
    assert is_loopback_url(url) is True


@pytest.mark.parametrize(
    "url",
    [
        "http://0.0.0.0:8188",
        "http://[::]:8188",
        "http://192.168.10.245:8188",
        "http://10.0.0.5:8188",
        "http://example.com:8188",
        "http://127.0.0.1.evil.example:8188",
        "http://not-a-host:8188",
    ],
)
def test_non_local_urls_are_not_loopback(url):
    assert is_loopback_url(url) is False


@pytest.mark.parametrize("host", ["127.0.0.1", "::1", "::ffff:127.0.0.1", "localhost", "[::1]:8000"])
def test_peer_addresses(host):
    assert is_loopback_peer(host) is True


@pytest.mark.parametrize("host", ["0.0.0.0", "::", "192.168.10.245", "10.0.0.5", "", "testclient"])
def test_non_loopback_peers(host):
    assert is_loopback_peer(host) is False


def test_wildcard_renderer_url_is_refused_by_settings(monkeypatch):
    """A renderer URL on the unspecified address is not "this machine"."""
    monkeypatch.setenv("SHINE_COMFYUI_URL", "http://0.0.0.0:8188")
    monkeypatch.delenv("SHINE_ALLOW_REMOTE_COMFYUI", raising=False)
    config = importlib.import_module("shine.config")
    with pytest.raises(ValueError, match="must point at this machine"):
        config.Settings.from_env()


def test_renderer_url_needs_the_explicit_remote_opt_in(monkeypatch):
    monkeypatch.setenv("SHINE_COMFYUI_URL", "http://0.0.0.0:8188")
    monkeypatch.setenv("SHINE_ALLOW_REMOTE_COMFYUI", "1")
    config = importlib.import_module("shine.config")
    assert config.Settings.from_env().comfyui_base_url == "http://0.0.0.0:8188"


def test_api_refuses_a_non_loopback_client(tmp_path):
    """An ordinary project request from another host is refused."""
    from fastapi.testclient import TestClient

    from shine.config import Settings
    from shine.db import Base, make_engine, make_session_factory
    from shine.main import create_app

    data_dir = tmp_path / "data"
    data_dir.mkdir()
    database_url = f"sqlite:///{(data_dir / 'shine.sqlite3').as_posix()}"
    engine = make_engine(database_url)
    Base.metadata.create_all(engine)
    factory = make_session_factory(engine)
    settings = Settings(data_dir=data_dir, database_url=database_url, secret_store_dir=data_dir / "secrets")
    app = create_app(settings=settings, database_url=database_url, asset_root=data_dir)
    app.state.engine.dispose()
    app.state.engine = engine
    app.state.session_factory = factory
    with TestClient(app, client=("192.168.10.245", 51234)) as client:
        refused = client.get("/api/v1/projects")
        assert refused.status_code == 403
        assert "only answers clients on this machine" in refused.text
    with TestClient(app, client=("127.0.0.1", 51234)) as client:
        assert client.get("/api/v1/projects").status_code == 200


def test_non_loopback_exposure_needs_an_explicit_opt_in(tmp_path):
    from fastapi.testclient import TestClient

    from shine.config import Settings
    from shine.db import Base, make_engine, make_session_factory
    from shine.main import create_app

    data_dir = tmp_path / "data2"
    data_dir.mkdir()
    database_url = f"sqlite:///{(data_dir / 'shine.sqlite3').as_posix()}"
    engine = make_engine(database_url)
    Base.metadata.create_all(engine)
    factory = make_session_factory(engine)
    settings = Settings(data_dir=data_dir, database_url=database_url, secret_store_dir=data_dir / "secrets",
                        allow_non_loopback_api=True)
    app = create_app(settings=settings, database_url=database_url, asset_root=data_dir)
    app.state.engine.dispose()
    app.state.engine = engine
    app.state.session_factory = factory
    with TestClient(app, client=("192.168.10.245", 51234)) as client:
        assert client.get("/api/v1/projects").status_code == 200
