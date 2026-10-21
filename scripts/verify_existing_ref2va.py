"""Read-only media recheck. Never creates a render, approval, or production DB write.

Run with uv run --project backend python scripts/verify_existing_ref2va.py.
Only hashes, IDs and deterministic QC summaries leave the local data directory.
"""
from __future__ import annotations

import hashlib
import json
import sqlite3
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "backend"))

from shine.config import Settings  # noqa: E402
from shine.quality.technical import TechnicalExpectations, TechnicalQC  # noqa: E402


def main():
    root = REPO / ".local-data-v12"
    database = root / "shine.db"
    settings = Settings.from_env()
    db = sqlite3.connect(f"file:{database.as_posix()}?mode=ro", uri=True)
    db.row_factory = sqlite3.Row
    results = []
    try:
        for row in db.execute(
            "SELECT j.id,j.status,j.request,j.shot_revision,j.stale,j.provider_job_id,"
            "a.id attempt_id,a.attempt_no,a.output_sha256,m.sha256,m.storage_path,m.byte_size "
            "FROM jobs j JOIN media_artifacts m ON m.id=j.artifact_id "
            "JOIN job_attempts a ON a.id=m.attempt_id ORDER BY j.created_at"
        ):
            path = root / row["storage_path"]
            request = json.loads(row["request"])
            with path.open("rb") as media:
                digest = hashlib.file_digest(media, "sha256").hexdigest()
            technical = TechnicalQC(ffprobe_path=settings.ffprobe_path, ffmpeg_path=settings.ffmpeg_path).check(
                path, expectations=TechnicalExpectations(
                    duration_s=request["expected_duration_s"], expect_audio=bool(request["shot_spec"]["dialogue"])))
            result = {k: row[k] for k in ("id", "status", "attempt_id", "attempt_no", "provider_job_id")}
            result.update(sha256=digest, integrity_matches=digest == row["sha256"] == row["output_sha256"],
                          size_matches=path.stat().st_size == row["byte_size"],
                          technical=technical.model_dump(mode="json"),
                          manual_reapproval="NOT_PERFORMED", gpu_submissions=0)
            results.append(result)
    finally:
        db.close()
    target = REPO / ".local-data-v121-audit" / "existing-media-recheck.json"
    target.parent.mkdir(exist_ok=True)
    target.write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(results, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
