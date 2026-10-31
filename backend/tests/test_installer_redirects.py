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
        assert kwargs == {
            "headers": kwargs["headers"], "follow_redirects": False,
            "timeout": httpx.Timeout(30, read=120),
        }
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


def test_github_origin_may_use_only_github_release_assets(tmp_path=None):
    """New-machine deployment pins GitHub source archives and release assets.

    The origin set grows from HF-only to {huggingface.co, github.com} (documented
    in docs/NEW_MACHINE_DEPLOYMENT.md); each origin may still only redirect to
    its own operator's CDN, never across operators.
    """
    github_release = PackageFile(
        url="https://github.com/astral-sh/uv/releases/download/0.9.5/uv-x86_64-pc-windows-msvc.zip",
        relative_path="uv.zip",
        sha256=hashlib.sha256(b"data").hexdigest(),
        size=4,
        redirect_hosts=["release-assets.githubusercontent.com"],
    )
    assert github_release.redirect_hosts == ["release-assets.githubusercontent.com"]
    # Cross-operator redirect mix stays rejected: a GitHub origin may not
    # authorize the HF CDN, and HF may not authorize GitHub's asset CDN.
    with pytest.raises((ValidationError, ValueError)):
        PackageFile(
            url="https://github.com/astral-sh/uv/releases/download/0.9.5/uv-x86_64-pc-windows-msvc.zip",
            relative_path="uv.zip",
            sha256=hashlib.sha256(b"data").hexdigest(),
            size=4,
            redirect_hosts=["us.aws.cdn.hf.co"],
        )
    with pytest.raises((ValidationError, ValueError)):
        PackageFile(
            url="https://huggingface.co/Qwen/model/resolve/" + "a" * 40 + "/model.safetensors",
            relative_path="model.safetensors",
            sha256=hashlib.sha256(b"data").hexdigest(),
            size=4,
            redirect_hosts=["release-assets.githubusercontent.com"],
        )


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


def test_connect_only_retry_is_bounded_and_keeps_range(monkeypatch, tmp_path):
    file = spec()
    (tmp_path / (file.sha256 + ".part")).write_bytes(b"da")
    calls = []

    @contextmanager
    def stream(*args, **kwargs):
        calls.append(kwargs["headers"])
        if len(calls) < 3:
            raise httpx.ConnectError("TLS failed https://cdn.invalid/?Signature=secret")
        yield httpx.Response(206, content=b"ta", headers={"content-range": "bytes 2-3/4"})

    monkeypatch.setattr(httpx, "stream", stream)
    assert ControlledDownloader({"huggingface.co"}, HTTPTransport()).download(file, tmp_path).read_bytes() == b"data"
    assert calls == [{"Range": "bytes=2-"}] * 3


def test_exhausted_connection_does_not_expose_signed_url(monkeypatch, tmp_path):
    calls = []

    @contextmanager
    def stream(*args, **kwargs):
        calls.append(args)
        raise httpx.ConnectTimeout("https://cdn.invalid/?Signature=secret")
        yield  # pragma: no cover

    monkeypatch.setattr(httpx, "stream", stream)
    with pytest.raises(ValueError, match="^DOWNLOAD_CONNECTION_FAILED:ConnectTimeout$"):
        ControlledDownloader({"huggingface.co"}, HTTPTransport()).download(spec(), tmp_path)
    assert len(calls) == 3
    assert not list(tmp_path.iterdir())


def test_partial_response_is_not_replayed(monkeypatch, tmp_path):
    file = spec()
    calls = []

    class InterruptedBody(httpx.SyncByteStream):
        def __iter__(self):
            yield b"da"
            raise httpx.ReadError("https://cdn.invalid/?Signature=secret")

    @contextmanager
    def stream(*args, **kwargs):
        calls.append(args)
        yield httpx.Response(200, stream=InterruptedBody())

    monkeypatch.setattr(httpx, "stream", stream)
    with pytest.raises(ValueError, match="^DOWNLOAD_STREAM_FAILED:ReadError$"):
        ControlledDownloader({"huggingface.co"}, HTTPTransport(), max_stream_attempts=1).download(file, tmp_path)
    assert len(calls) == 1
    assert (tmp_path / (file.sha256 + ".part")).read_bytes() == b"da"
    assert not (tmp_path / file.sha256).exists()


def test_protocol_failure_before_headers_is_sanitized_without_retry(monkeypatch, tmp_path):
    calls = []

    @contextmanager
    def stream(*args, **kwargs):
        calls.append(args)
        raise httpx.RemoteProtocolError("https://cdn.invalid/?Signature=secret")
        yield  # pragma: no cover

    monkeypatch.setattr(httpx, "stream", stream)
    with pytest.raises(ValueError, match="^DOWNLOAD_REQUEST_FAILED:RemoteProtocolError$"):
        ControlledDownloader({"huggingface.co"}, HTTPTransport()).download(spec(), tmp_path)
    assert len(calls) == 1


@pytest.mark.parametrize("mode", ["resume", "ignore-range", "budget", "complete", "bad-range", "changed-object"])
def test_fixed_file_stream_continuation_is_bounded_and_hash_verified(monkeypatch, tmp_path, mode):
    file = spec()
    headers_seen = []

    class Interrupted(httpx.SyncByteStream):
        def __init__(self, prefix):
            self.prefix = prefix

        def __iter__(self):
            yield self.prefix
            raise httpx.ReadTimeout("Transient connection stalled")

    @contextmanager
    def stream(*args, **kwargs):
        headers_seen.append(kwargs["headers"])
        offset = int(kwargs["headers"].get("Range", "bytes=0-")[6:-1])
        if len(headers_seen) == 1 or mode == "budget":
            prefix = b"data" if mode == "complete" else b"data"[offset:offset+1] if mode == "budget" else b"da"
            yield httpx.Response(206 if offset else 200, stream=Interrupted(prefix),
                                 headers={"content-range": f"bytes {offset}-3/4"} if offset else {})
        elif mode == "ignore-range":
            yield httpx.Response(200, content=b"data")
        else:
            yield httpx.Response(206, content=b"xx" if mode == "changed-object" else b"ta",
                                 headers={"content-range": "bytes 1-3/4" if mode == "bad-range" else "bytes 2-3/4"})

    monkeypatch.setattr(httpx, "stream", stream)
    downloader = ControlledDownloader({"huggingface.co"}, HTTPTransport())
    errors = {"budget": "DOWNLOAD_STREAM_FAILED:ReadTimeout", "bad-range": "INVALID_RESUME_RANGE",
              "changed-object": "FILE_CHECKSUM_FAILED"}
    if mode in errors:
        with pytest.raises(ValueError, match=errors[mode]):
            downloader.download(file, tmp_path)
        assert not (tmp_path / file.sha256).exists()
    else:
        assert downloader.download(file, tmp_path).read_bytes() == b"data"
    assert len(headers_seen) == (3 if mode == "budget" else 1 if mode == "complete" else 2)
    if mode == "budget":
        assert headers_seen == [{}, {"Range": "bytes=1-"}, {"Range": "bytes=2-"}]
        assert (tmp_path / (file.sha256 + ".part")).read_bytes() == b"dat"
    elif mode != "complete":
        assert headers_seen == [{}, {"Range": "bytes=2-"}]


def test_resume_resolves_and_revalidates_cdn_again(monkeypatch, tmp_path):
    file = spec(["us.aws.cdn.hf.co"])
    requests = []

    class Interrupted(httpx.SyncByteStream):
        def __iter__(self):
            yield b"da"
            raise httpx.ReadError("Disconnected")

    @contextmanager
    def stream(method, url, **kwargs):
        requests.append((url, kwargs["headers"]))
        if len(requests) in {1, 3}:
            yield httpx.Response(302, headers={"location": f"https://us.aws.cdn.hf.co/file?Signature={len(requests)}"})
        elif len(requests) == 2:
            yield httpx.Response(200, stream=Interrupted())
        else:
            yield httpx.Response(206, content=b"ta", headers={"content-range": "bytes 2-3/4"})

    monkeypatch.setattr(httpx, "stream", stream)
    assert ControlledDownloader({"huggingface.co"}, HTTPTransport()).download(file, tmp_path).read_bytes() == b"data"
    assert requests[0] == (file.url, {}) and requests[2] == (file.url, {"Range": "bytes=2-"})
    assert requests[3][1] == {"Range": "bytes=2-"}


@pytest.mark.parametrize("budget", [0, True, 4])
def test_resume_budget_cannot_be_unbounded(tmp_path, budget):
    with pytest.raises(ValueError, match="DOWNLOAD_RESUME_BUDGET_INVALID"):
        ControlledDownloader({"huggingface.co"}, HTTPTransport(), max_stream_attempts=budget).download(spec(), tmp_path)


@pytest.mark.parametrize("mode", ["fresh", "resume", "ignored", "wrong-range", "short", "overflow"])
def test_bounded_ranges_preserve_offsets_and_verify_whole_object(monkeypatch, tmp_path, mode):
    data = b"abcdefgh"
    file = spec(["us.aws.cdn.hf.co"]).model_copy(update={
        "size": len(data), "sha256": hashlib.sha256(data).hexdigest(),
    })
    if mode == "resume":
        (tmp_path / (file.sha256 + ".part")).write_bytes(data[:2])
    requests = []

    @contextmanager
    def stream(method, url, **kwargs):
        headers = kwargs["headers"]
        assert headers["Accept-Encoding"] == "identity"
        start, end = map(int, headers["Range"][6:].split("-"))
        requests.append((url, start, end))
        if len(requests) % 2:
            yield httpx.Response(302, headers={"location": "https://us.aws.cdn.hf.co/file?Signature=transient"})
        elif mode == "ignored":
            yield httpx.Response(200, content=data)
        else:
            content = data[start:end+1]
            if mode == "short":
                content = content[:-1]
            if mode == "overflow":
                content += b"x"
            actual_end = end + 1 if mode == "wrong-range" else end
            yield httpx.Response(206, content=content, headers={
                "content-range": f"bytes {start}-{actual_end}/{len(data)}",
            })

    monkeypatch.setattr(httpx, "stream", stream)
    downloader = ControlledDownloader({"huggingface.co"}, HTTPTransport(), segment_bytes=3)
    errors = {"wrong-range": "INVALID_RESUME_RANGE", "short": "DOWNLOAD_SEGMENT_SIZE_MISMATCH",
              "overflow": "DOWNLOAD_SIZE_EXCEEDED"}
    if mode in errors:
        with pytest.raises(ValueError, match=errors[mode]):
            downloader.download(file, tmp_path)
        assert len(requests) == 2 and not (tmp_path / file.sha256).exists()
    else:
        assert downloader.download(file, tmp_path).read_bytes() == data
        ranges = [(a, b) for _, a, b in requests[::2]]
        assert ranges == ([(0, 2)] if mode == "ignored" else [(2, 2), (3, 5), (6, 7)]
                          if mode == "resume" else [(0, 2), (3, 5), (6, 7)])
        assert all(requests[i][1:] == requests[i+1][1:] for i in range(0, len(requests), 2))


@pytest.mark.parametrize("size", [0, True, 64 * 1024**2 + 1])
def test_bounded_range_size_has_a_fixed_upper_limit(tmp_path, size):
    with pytest.raises(ValueError, match="DOWNLOAD_SEGMENT_BUDGET_INVALID"):
        ControlledDownloader({"huggingface.co"}, HTTPTransport(), segment_bytes=size).download(spec(), tmp_path)


def test_bounded_range_interruption_continues_from_actual_written_byte(monkeypatch, tmp_path):
    data = b"abcdefgh"
    file = spec().model_copy(update={"size": len(data), "sha256": hashlib.sha256(data).hexdigest()})
    ranges = []

    class Interrupted(httpx.SyncByteStream):
        def __iter__(self):
            yield b"ab"
            raise httpx.ReadError("stream interrupted")

    @contextmanager
    def stream(method, url, **kwargs):
        start, end = map(int, kwargs["headers"]["Range"][6:].split("-"))
        ranges.append((start, end))
        headers = {"content-range": f"bytes {start}-{end}/{len(data)}"}
        yield (httpx.Response(206, stream=Interrupted(), headers=headers) if len(ranges) == 1
               else httpx.Response(206, content=data[start:end+1], headers=headers))

    monkeypatch.setattr(httpx, "stream", stream)
    result = ControlledDownloader({"huggingface.co"}, HTTPTransport(), segment_bytes=3).download(file, tmp_path)
    assert result.read_bytes() == data
    assert ranges == [(0, 2), (2, 2), (3, 5), (6, 7)]


def test_resume_budget_resets_only_after_a_complete_verified_range(monkeypatch, tmp_path):
    data = b"abcdefghijklmnop"
    file = spec().model_copy(update={"size": len(data), "sha256": hashlib.sha256(data).hexdigest()})
    ranges = []

    class Interrupted(httpx.SyncByteStream):
        def __init__(self, prefix):
            self.prefix = prefix

        def __iter__(self):
            yield self.prefix
            raise httpx.ReadTimeout("stream stalled")

    @contextmanager
    def stream(method, url, **kwargs):
        start, end = map(int, kwargs["headers"]["Range"][6:].split("-"))
        ranges.append((start, end))
        headers = {"content-range": f"bytes {start}-{end}/{len(data)}"}
        yield (httpx.Response(206, stream=Interrupted(data[start:start+1]), headers=headers)
               if len(ranges) % 2 else httpx.Response(206, content=data[start:end+1], headers=headers))

    monkeypatch.setattr(httpx, "stream", stream)
    path = ControlledDownloader({"huggingface.co"}, HTTPTransport(), segment_bytes=3).download(file, tmp_path)
    assert path.read_bytes() == data
    assert ranges == [(0, 2), (1, 2), (3, 5), (4, 5), (6, 8), (7, 8),
                      (9, 11), (10, 11), (12, 14), (13, 14), (15, 15)]


def test_interrupted_ranges_finish_the_original_block_instead_of_extending_its_end(monkeypatch, tmp_path):
    data = b"abcdefgh"
    file = spec().model_copy(update={"size": len(data), "sha256": hashlib.sha256(data).hexdigest()})
    ranges = []

    class Interrupted(httpx.SyncByteStream):
        def __init__(self, prefix):
            self.prefix = prefix

        def __iter__(self):
            yield self.prefix
            raise httpx.ReadError("interrupted before end of range")

    @contextmanager
    def stream(method, url, **kwargs):
        start, end = map(int, kwargs["headers"]["Range"][6:].split("-"))
        ranges.append((start, end))
        headers = {"content-range": f"bytes {start}-{end}/{len(data)}"}
        yield (httpx.Response(206, stream=Interrupted(data[start:start+1]), headers=headers)
               if start < end else httpx.Response(206, content=data[start:end+1], headers=headers))

    monkeypatch.setattr(httpx, "stream", stream)
    path = ControlledDownloader({"huggingface.co"}, HTTPTransport(), segment_bytes=3).download(file, tmp_path)
    assert path.read_bytes() == data
    assert ranges == [(0, 2), (1, 2), (2, 2), (3, 5), (4, 5), (5, 5), (6, 7), (7, 7)]
