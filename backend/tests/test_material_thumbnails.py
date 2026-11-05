"""Real JPEG preview bytes, integrity rejection and named decoder unavailability."""
import hashlib
import struct
import zlib

import pytest
from fastapi import HTTPException

from shine.media.thumbnails import thumbnail


def _png():
    def chunk(kind, body):
        return struct.pack("!I", len(body)) + kind + body + struct.pack("!I", zlib.crc32(kind + body) & 0xffffffff)
    raw = b"".join(b"\x00" + b"\xaa\xbb\xcc" * 16 for _ in range(32))
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack("!2I5B", 16, 32, 8, 2, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(raw)) + chunk(b"IEND", b""))


def test_thumbnail_contains_image_and_rejects_tampering(tmp_path):
    path = tmp_path / "source.png"
    content = _png()
    path.write_bytes(content)
    sha = hashlib.sha256(content).hexdigest()
    response = thumbnail(path, sha)
    assert response.media_type == "image/jpeg"
    assert response.body.startswith(b"\xff\xd8")
    # The derivative is addressed by the content digest, so the browser may
    # keep it; a poll of the ledger must not re-encode the original.
    assert response.headers["etag"] == f'"{sha}"'
    assert "immutable" in response.headers["cache-control"]
    unchanged = thumbnail(path, sha, if_none_match=response.headers["etag"])
    assert unchanged.status_code == 304
    path.write_bytes(b"tampered")
    with pytest.raises(HTTPException) as caught:
        thumbnail(path, sha)
    assert caught.value.status_code == 409


def test_missing_decoder_never_serves_the_full_original(tmp_path):
    path = tmp_path / "source.png"
    path.write_bytes(_png())
    with pytest.raises(HTTPException) as caught:
        thumbnail(path, hashlib.sha256(path.read_bytes()).hexdigest(), str(tmp_path / "missing-ffmpeg"))
    assert caught.value.status_code == 503
