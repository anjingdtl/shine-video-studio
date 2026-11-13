"""Exercise the production HTTP transport without network/model downloads."""

import hashlib
from contextlib import contextmanager

import httpx
import pytest
from pydantic import ValidationError

from shine.comfy.workflow import graph_hash
from shine.comfyops.installer import ControlledDownloader, HTTPTransport, PackageFile, PackageSpec, mirror_url


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


MIRROR_HOSTS = {"huggingface.co", "hf-mirror.com", "cas-bridge.xethub.hf.co"}


def test_mirror_url_only_rewrites_pinned_huggingface_files():
    revision = "a" * 40
    url = f"https://huggingface.co/Qwen/Qwen3-ASR-0.6B/resolve/{revision}/config.json"
    assert mirror_url(url) == f"https://hf-mirror.com/Qwen/Qwen3-ASR-0.6B/resolve/{revision}/config.json"
    assert mirror_url("https://huggingface.co/Qwen/Qwen3-ASR-0.6B") is None
    assert mirror_url("https://evil.com/Qwen/Qwen3-ASR-0.6B/resolve/master/config.json") is None
    assert mirror_url(url + "?download=1") is None


def test_unreachable_primary_falls_back_to_the_mirror(monkeypatch, tmp_path):
    file = spec()
    calls = []

    @contextmanager
    def stream(method, url, **kwargs):
        calls.append(url)
        if len(calls) <= 3:
            raise httpx.ConnectError("unreachable")
        yield httpx.Response(200, content=b"data")

    monkeypatch.setattr(httpx, "stream", stream)
    downloader = ControlledDownloader(MIRROR_HOSTS, HTTPTransport())
    assert downloader.download(file, tmp_path).read_bytes() == b"data"
    assert calls[:3] == [file.url] * 3
    assert calls[3] == "https://hf-mirror.com/Qwen/model/resolve/" + "a" * 40 + "/model.safetensors"


def test_mirror_is_not_used_without_its_trusted_host(monkeypatch, tmp_path):
    file = spec()
    calls = []

    @contextmanager
    def stream(method, url, **kwargs):
        calls.append(url)
        raise httpx.ConnectError("unreachable")
        yield  # pragma: no cover

    monkeypatch.setattr(httpx, "stream", stream)
    with pytest.raises(ValueError, match="DOWNLOAD_CONNECTION_FAILED:ConnectError"):
        ControlledDownloader({"huggingface.co"}, HTTPTransport()).download(file, tmp_path)
    assert calls == [file.url] * 3
    assert not list(tmp_path.iterdir())


def test_mirror_bytes_are_still_verified_against_the_pin(monkeypatch, tmp_path):
    file = spec()

    @contextmanager
    def stream(method, url, **kwargs):
        if url == file.url:
            raise httpx.ConnectError("unreachable")
        yield httpx.Response(200, content=b"evil")

    monkeypatch.setattr(httpx, "stream", stream)
    with pytest.raises(ValueError, match="FILE_CHECKSUM_FAILED"):
        ControlledDownloader(MIRROR_HOSTS, HTTPTransport()).download(file, tmp_path)
    assert not list(tmp_path.iterdir())


def test_mirror_is_used_directly_after_the_primary_is_proven_unreachable(monkeypatch, tmp_path):
    first = spec()
    second = PackageFile(
        url=first.url.replace("model.safetensors", "other.safetensors"),
        relative_path="other.safetensors",
        sha256=hashlib.sha256(b"other").hexdigest(),
        size=5,
    )
    calls = []

    @contextmanager
    def stream(method, url, **kwargs):
        calls.append(url)
        if url.startswith("https://huggingface.co/"):
            raise httpx.ConnectError("unreachable")
        yield httpx.Response(200, content=b"data" if url.endswith("model.safetensors") else b"other")

    monkeypatch.setattr(httpx, "stream", stream)
    downloader = ControlledDownloader(MIRROR_HOSTS, HTTPTransport())
    assert downloader.download(first, tmp_path).read_bytes() == b"data"
    before = len(calls)
    assert downloader.download(second, tmp_path).read_bytes() == b"other"
    assert len(calls) == before + 1
    assert calls[-1].startswith("https://hf-mirror.com/")


def test_mirror_follows_only_its_declared_redirect_hosts(monkeypatch, tmp_path):
    file = spec()
    calls = []

    @contextmanager
    def stream(method, url, **kwargs):
        calls.append(url)
        if url == file.url:
            raise httpx.ConnectError("unreachable")
        yield httpx.Response(302, headers={"location": "https://evil.example/x?Signature=private"})

    monkeypatch.setattr(httpx, "stream", stream)
    with pytest.raises(ValueError, match="UNTRUSTED_DOWNLOAD_URL"):
        ControlledDownloader(MIRROR_HOSTS, HTTPTransport()).download(file, tmp_path)
    assert calls[-1] == "https://hf-mirror.com/Qwen/model/resolve/" + "a" * 40 + "/model.safetensors"


def test_mirror_signed_cdn_redirect_keeps_the_range(monkeypatch, tmp_path):
    file = spec()
    (tmp_path / (file.sha256 + ".part")).write_bytes(b"da")
    headers = []
    signed = "https://cas-bridge.xethub.hf.co/xet/file?Signature=transient"

    @contextmanager
    def stream(method, url, **kwargs):
        headers.append(kwargs["headers"])
        if url == file.url:
            raise httpx.ConnectError("unreachable")
        if url.startswith("https://hf-mirror.com/"):
            yield httpx.Response(302, headers={"location": signed})
        else:
            yield httpx.Response(206, content=b"ta", headers={"content-range": "bytes 2-3/4"})

    monkeypatch.setattr(httpx, "stream", stream)
    assert ControlledDownloader(MIRROR_HOSTS, HTTPTransport()).download(file, tmp_path).read_bytes() == b"data"
    assert headers[-2:] == [{"Range": "bytes=2-"}, {"Range": "bytes=2-"}]


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


def test_explicit_source_preference_only_uses_approved_mirror():
    url = "https://huggingface.co/Qwen/model/resolve/" + "a" * 40 + "/model.safetensors"
    file = spec().model_copy(update={"url": url})
    mirror_first = ControlledDownloader(MIRROR_HOSTS, HTTPTransport(), source_preference="MIRROR_FIRST")
    official_first = ControlledDownloader(MIRROR_HOSTS, HTTPTransport(), source_preference="OFFICIAL_FIRST")
    assert [kind for kind, _ in mirror_first._candidates(file)] == ["mirror", "primary"]
    assert [kind for kind, _ in official_first._candidates(file)] == ["primary", "mirror"]
    only_official = ControlledDownloader({"huggingface.co"}, HTTPTransport(), source_preference="MIRROR_FIRST")
    assert only_official._candidates(file) == [("primary", url)]
    non_hf = file.model_copy(update={"url": "https://github.com/org/repo/archive/abc.zip"})
    assert mirror_first._candidates(non_hf) == [("primary", non_hf.url)]


def test_auto_source_probe_is_bounded_to_trusted_pinned_candidates():
    file = spec()
    class TimedTransport:
        def __init__(self):
            self.probed = []
        def probe(self, url):
            self.probed.append(url)
            return 0.3 if url.startswith("https://huggingface.co/") else 0.1
    transport = TimedTransport()
    downloader = ControlledDownloader(MIRROR_HOSTS, transport, probe_sources=True)
    assert [kind for kind, _ in downloader._candidates(file)] == ["mirror", "primary"]
    assert len(transport.probed) == 2
    downloader._candidates(file)
    assert len(transport.probed) == 2  # one bounded probe per host per attempt
