"""Exercise the production HTTP transport without network/model downloads."""

import hashlib
from contextlib import contextmanager

import httpx
import pytest
from pydantic import ValidationError

from shine.comfy.workflow import graph_hash
from shine.comfyops.installer import ControlledDownloader, HTTPTransport, PackageFile, PackageSpec


def spec(hosts=()):
    return PackageFile(
        url="https://huggingface.co/Qwen/model/resolve/" + "a" * 40 + "/model.safetensors",
        relative_path="model.safetensors",
        sha256=hashlib.sha256(b"data").hexdigest(),
        size=4,
        redirect_hosts=list(hosts),
    )


def transport(monkeypatch, responses):
    requests = []

    @contextmanager
    def stream(method, url, **kwargs):
        requests.append((method, url, kwargs))
        assert kwargs == {"headers": kwargs["headers"], "follow_redirects": False, "timeout": 30}
        yield responses[len(requests) - 1]

    monkeypatch.setattr(httpx, "stream", stream)
    return HTTPTransport(), requests


def test_old_manifest_hash_preserved_and_policy_changes_package_hash():
    old = spec().model_dump()
    assert "redirect_hosts" not in old
    assert PackageFile.model_validate(old).model_dump() == old
    package = PackageSpec(name="test", kind="model", revision="v1", license_id="TEST", usage="research", files=[old])
    before = graph_hash(package.model_dump())
    updated = package.model_copy(update={"files": [spec(["huggingface.co", "us.aws.cdn.hf.co"])]})
    assert graph_hash(updated.model_dump()) != before
    assert updated.model_dump()["files"][0]["redirect_hosts"] == ["huggingface.co", "us.aws.cdn.hf.co"]


@pytest.mark.parametrize("hosts", [["evil.com"], ["127.0.0.1"], ["*.hf.co"], ["huggingface.co"] * 2])
def test_manifest_cannot_authorize_arbitrary_hosts(hosts):
    with pytest.raises((ValidationError, ValueError)):
        spec(hosts)


def test_policy_only_available_for_official_hf_origin():
    value = spec(["us.aws.cdn.hf.co"]).model_dump()
    value["url"] = "https://github.com/Qwen/model"
    with pytest.raises(ValidationError):
        PackageFile.model_validate(value)


def test_default_still_rejects_even_same_host_redirect(monkeypatch, tmp_path):
    http, requests = transport(monkeypatch, [httpx.Response(302, headers={"location": "/api/resolve-cache/file"})])
    with pytest.raises(ValueError, match="DOWNLOAD_HTTP_REJECTED"):
        ControlledDownloader({"huggingface.co"}, http).download(spec(), tmp_path)
    assert len(requests) == 1


def test_declared_same_host_then_signed_cdn_resume(monkeypatch, tmp_path):
    file = spec(["huggingface.co", "us.aws.cdn.hf.co"])
    (tmp_path / (file.sha256 + ".part")).write_bytes(b"da")
    http, requests = transport(
        monkeypatch,
        [
            httpx.Response(307, headers={"location": "/api/resolve-cache/file"}),
            httpx.Response(302, headers={"location": "https://us.aws.cdn.hf.co/file?Signature=private"}),
            httpx.Response(206, headers={"content-range": "bytes 2-3/4"}, content=b"ta"),
        ],
    )
    assert ControlledDownloader({"huggingface.co"}, http).download(file, tmp_path).read_bytes() == b"data"
    assert len(requests) == 3
    assert all(r[2]["headers"] == {"Range": "bytes=2-"} for r in requests)
    assert sorted(p.name for p in tmp_path.iterdir()) == [file.sha256]


@pytest.mark.parametrize(
    "target",
    [
        "https://127.0.0.1/secret", "https://evil.com/file", "http://us.aws.cdn.hf.co/file",
        "https://user:pass@us.aws.cdn.hf.co/file", "https://us.aws.cdn.hf.co:8443/file",
        "https://us.aws.cdn.hf.co/file#fragment", "https://us.aws.cdn.hf.co.evil.com/file",
    ],
)
def test_undeclared_or_unsafe_hop_rejected_before_request(monkeypatch, tmp_path, target):
    http, requests = transport(monkeypatch, [httpx.Response(302, headers={"location": target})])
    with pytest.raises(ValueError, match="UNTRUSTED_DOWNLOAD_URL"):
        ControlledDownloader({"huggingface.co"}, http).download(spec(["us.aws.cdn.hf.co"]), tmp_path)
    assert len(requests) == 1
    assert not list(tmp_path.iterdir())


def test_redirect_loop_bounded(monkeypatch, tmp_path):
    http, requests = transport(monkeypatch, [httpx.Response(302, headers={"location": "/loop"})] * 6)
    with pytest.raises(ValueError, match="DOWNLOAD_REDIRECT_LIMIT"):
        ControlledDownloader({"huggingface.co"}, http).download(spec(["huggingface.co"]), tmp_path)
    assert len(requests) == 6


@pytest.mark.parametrize("body,error", [(b"evil", "FILE_CHECKSUM_FAILED"), (b"longer", "DOWNLOAD_SIZE_EXCEEDED")])
def test_redirect_keeps_checksum_and_size_enforcement(monkeypatch, tmp_path, body, error):
    file = spec(["us.aws.cdn.hf.co"])
    http, _ = transport(monkeypatch, [
        httpx.Response(302, headers={"location": "https://us.aws.cdn.hf.co/file?Signature=private"}),
        httpx.Response(200, content=body),
    ])
    with pytest.raises(ValueError, match=error):
        ControlledDownloader({"huggingface.co"}, http).download(file, tmp_path)
    assert not list(tmp_path.iterdir())
