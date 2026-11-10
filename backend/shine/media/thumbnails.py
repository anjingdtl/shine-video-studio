"""Bounded technical image previews; never crop or promote a derivative."""

import hashlib
import subprocess
from pathlib import Path

from fastapi import HTTPException, Response

# The derivative is a pure function of content that is addressed by its own
# SHA-256, so the same bytes always come back for the same digest.  Saying so
# lets the browser keep the preview instead of re-encoding the original on
# every poll of the durable ledger.
CACHE_CONTROL = "private, max-age=31536000, immutable"


def thumbnail(path: Path, expected_sha256: str, ffmpeg_path: str = "ffmpeg",
              if_none_match: str | None = None, *, seek_s: float = 0.0) -> Response:
    try:
        content = path.read_bytes()
    except OSError as exc:
        raise HTTPException(404, "ASSET_FILE_MISSING") from exc
    if hashlib.sha256(content).hexdigest() != expected_sha256:
        raise HTTPException(409, "ASSET_HASH_CHANGED")
    etag = f'"{expected_sha256}"'
    if if_none_match and etag in {part.strip() for part in if_none_match.split(",")}:
        return Response(status_code=304, headers={"ETag": etag, "Cache-Control": CACHE_CONTROL})
    poster = _grab_frame(content, ffmpeg_path, seek_s)
    if not poster and seek_s:
        # A shorter clip than the seek point still gets a first-frame cover.
        poster = _grab_frame(content, ffmpeg_path, 0.0)
    if not poster:
        raise HTTPException(422, "IMAGE_PREVIEW_UNAVAILABLE")
    return Response(poster, media_type="image/jpeg",
                    headers={"ETag": etag, "Cache-Control": CACHE_CONTROL})


def _grab_frame(content: bytes, ffmpeg_path: str, seek_s: float) -> bytes | None:
    command = [ffmpeg_path, "-v", "error", "-i", "pipe:0"]
    if seek_s:
        # Seek inside the decoded stream: a dark fade-in first frame is not a
        # useful cover, while the first second is representative.
        command.extend(["-ss", f"{seek_s:g}"])
    command.extend([
        "-frames:v", "1",
        "-vf", "scale=360:280:force_original_aspect_ratio=decrease",
        "-f", "image2pipe", "-c:v", "mjpeg", "pipe:1",
    ])
    try:
        result = subprocess.run(
            command, input=content, capture_output=True, timeout=20, check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise HTTPException(503, "IMAGE_PREVIEW_UNAVAILABLE") from exc
    if result.returncode or not result.stdout:
        return None
    return result.stdout
