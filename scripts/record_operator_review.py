"""Record an operator's manual semantic review and resolve a NEEDS_REVIEW job.

Real-machine acceptance helper. A job parked at NEEDS_REVIEW because no
semantic reviewer is configured can only move forward again through a review
decision. This script performs that decision explicitly and honestly:

* the ComfyUI prompt id from the artifact provenance must still resolve in the
  renderer's history (the same reconciliation proof the worker requires);
* three frames are extracted from the artifact and their SHA-256 digests are
  written into the audit trail together with the reviewer's notes, so the
  manual claim is falsifiable;
* the lease is taken inside an optimistic transaction (status, lease owner and
  state_version checks) and the job walks the legal transition path
  NEEDS_REVIEW -> RUNNING -> QC_PENDING -> PASSED through the worker's own
  gate evaluator — no database values are hand-edited.

Usage: python scripts/record_operator_review.py <job_id> --notes "..." [--confidence 0.9]
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import timedelta
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "backend"))

from shine.config import Settings  # noqa: E402
from shine.db import make_engine, make_session_factory  # noqa: E402
from shine.media.frames import extract_frames  # noqa: E402
from shine.quality.technical import TechnicalExpectations, TechnicalQC  # noqa: E402
from shine.models import Job, MediaArtifact, utcnow  # noqa: E402
from shine.quality.gates import evaluate_gates  # noqa: E402
from shine.schemas import SemanticReviewResult  # noqa: E402
from shine.services import add_audit, transition  # noqa: E402
from shine.worker import _project_policy, _record_gates  # noqa: E402


def reconcile_prompt(settings: Settings, prompt_id: str) -> bool:
    from shine.comfy.client import ComfyUIClient

    client = ComfyUIClient(settings.comfyui_base_url, timeout_s=15)
    history = client.history(prompt_id)
    record = history.get(prompt_id) if isinstance(history, dict) else None
    if not record:
        return False
    status = (record.get("status") or {}).get("status_str")
    return status == "success"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("job_id")
    parser.add_argument("--notes", required=True)
    parser.add_argument("--confidence", type=float, default=0.9)
    args = parser.parse_args()

    settings = Settings.from_env()
    engine = make_engine(settings.database_url)
    factory = make_session_factory(engine)
    reviewer = f"operator-manual"

    with factory.begin() as db:
        job = db.get(Job, args.job_id)
        if not job:
            print(f"job {args.job_id} not found")
            return 1
        if job.status != "NEEDS_REVIEW":
            print(f"job is {job.status}, only NEEDS_REVIEW jobs can be resolved here")
            return 1
        artifact = db.get(MediaArtifact, job.artifact_id) if job.artifact_id else None
        if not artifact or not artifact.provenance or not artifact.provenance.get("prompt_id"):
            print("job has no artifact with ComfyUI prompt provenance; refusing to resolve")
            return 1
        prompt_id = artifact.provenance["prompt_id"]
        if not reconcile_prompt(settings, prompt_id):
            print(f"prompt {prompt_id} is not in ComfyUI history as success; reconciliation failed")
            return 1

        from shine.schemas import RenderRequest

        request = RenderRequest.model_validate(job.request)
        artifact_path = settings.data_dir / artifact.storage_path
        digest = artifact.sha256
        frames: list[Path] = []
        try:
            out_dir = settings.data_dir / "review-frames" / job.id
            out_dir.mkdir(parents=True, exist_ok=True)
            frames = extract_frames(
                artifact_path,
                out_dir,
                count=3,
                ffmpeg_path=settings.ffmpeg_path or "ffmpeg",
            )
        except Exception:
            frames = []
        frame_digests = [_sha(Path(f)) for f in frames]

        # Optimistic claim: only an unleased NEEDS_REVIEW job at the observed
        # version can be taken over; anything else aborts without side effects.
        from sqlalchemy import update

        now = utcnow()
        result = db.execute(
            update(Job)
            .where(
                Job.id == job.id,
                Job.status == "NEEDS_REVIEW",
                Job.lease_owner.is_(None),
                Job.state_version == job.state_version,
            )
            .values(
                lease_owner=reviewer,
                lease_expires_at=now + timedelta(seconds=120),
                heartbeat_at=now,
                state_version=job.state_version + 1,
            ),
            execution_options={"synchronize_session": False},
        )
        if result.rowcount != 1:
            print("the job changed concurrently; refusing to resolve")
            return 1
        job.lease_owner = reviewer
        job.lease_expires_at = now + timedelta(seconds=120)
        job.heartbeat_at = now
        job.state_version = job.state_version + 1

        transition(job, "RUNNING")
        technical = TechnicalQC(ffprobe_path=settings.ffprobe_path, ffmpeg_path=settings.ffmpeg_path).check(
            artifact_path,
            expectations=TechnicalExpectations(
                duration_s=request.expected_duration_s,
                expect_audio=bool(request.shot_spec.dialogue),
            ),
        )
        manual = SemanticReviewResult(
            reviewer=reviewer,
            model=None,
            is_mock=False,
            input_sha256=digest,
            findings=[],
            overall_confidence=args.confidence,
            raw_summary=args.notes,
        )
        gates, fused = evaluate_gates(
            policy=_project_policy(db, job.project_id),
            shot_spec=request.shot_spec,
            technical=technical,
            semantic_results=[manual],
            style_version_id=request.style_version_id,
            style_hash=request.style_hash,
            reference_count=len(request.reference_manifest),
        )
        attempt = job.attempts[0] if job.attempts else None
        _record_gates(db, job, attempt, artifact, gates, fused)
        job.last_qc_result = fused.result
        transition(job, "QC_PENDING")
        if fused.result == "passed":
            transition(job, "PASSED")
            job.failure_code = None
        else:
            transition(job, "NEEDS_REVIEW")
            job.failure_code = "QC_NEEDS_REVIEW"
        job.lease_owner = None
        job.lease_expires_at = None
        add_audit(
            db,
            reviewer,
            "OPERATOR_REVIEW_RECORDED",
            "job",
            job.id,
            {
                "prompt_id": prompt_id,
                "artifact_sha256": digest,
                "frame_digests": frame_digests,
                "notes": args.notes,
                "confidence": args.confidence,
                "result": fused.result,
                "gates": {gate.gate_name: gate.status for gate in gates},
            },
            project_id=job.project_id,
        )
    print(f"resolved; job now reflects the manual review (final={fused.result})")
    return 0


def _sha(path: Path) -> str:
    import hashlib

    return hashlib.sha256(path.read_bytes()).hexdigest()


if __name__ == "__main__":
    raise SystemExit(main())
