"""Where produced files live and what they are called.

The workbench keeps one output root.  Every file is stored two levels below
it: ``<root>/<project>/<素材|视频|音色>/...``.  Outputs of the global material
centre go to ``<root>/素材中心/...``.  A workbench that has not configured a
root uses the existing data directory, so nothing moves until the person asks
for it.

Changing the root is a migration, not a rename of one setting: every source
file is copied to its readable target, verified against the ledger hash, and
only then is the ledger switched in one transaction.  The old bytes are
removed afterwards.  A failure before the switch leaves the old root active;
an interruption leaves harmless extra copies in the new root, never a ledger
that points at a file which does not exist.
"""

from __future__ import annotations

import hashlib
import os
import shutil
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path

from fastapi import HTTPException
from sqlalchemy import func, select, text

from shine.media.artifacts import artifact_suffix, safe_asset_path
from shine.media.output_names import (
    MATERIAL_CENTER,
    TYPE_MATERIAL,
    TYPE_VIDEO,
    TYPE_VOICE,
    is_readable_ledger_name,
    readable_base,
    sanitize_component,
    typed_filename,
)

OUTPUT_SETTING_KEY = "output-directory"
MIGRATION_LOCK_KEY = "output-directory:migration"
MIGRATION_STALE_AFTER = timedelta(hours=6)

_ACTIVE_JOB_STATUSES = (
    "PLANNED", "WAITING_ASSET", "READY", "QUEUED",
    "SUBMITTING", "RUNNING", "QC_PENDING", "REPAIR_PENDING",
)
_ACTIVE_LIBRARY_TASKS = ("QUEUED", "RUNNING")


class StorageMigrationError(RuntimeError):
    """A migration was refused before the ledger changed."""

    def __init__(self, code: str, message: str, evidence: dict | None = None):
        super().__init__(message)
        self.code = code
        self.message = message
        self.evidence = evidence or {}


# ---------------------------------------------------------------------------
# the configured root


def _setting_value(db, key: str) -> dict:
    from shine.models import AppSetting

    row = db.get(AppSetting, key)
    return dict(row.value or {}) if row is not None else {}


def configured_output_root(db, settings) -> Path | None:
    raw = str(_setting_value(db, OUTPUT_SETTING_KEY).get("root") or "").strip()
    if not raw:
        return None
    return Path(raw)


def output_root(db, settings, *, default: Path | None = None) -> Path:
    configured = configured_output_root(db, settings)
    if configured is not None:
        if not configured.is_absolute():
            raise HTTPException(500, "OUTPUT_DIRECTORY_INVALID")
        return configured
    return Path(default or settings.data_dir)


def output_root_for_request(request, db) -> Path:
    settings = request.app.state.settings
    default = getattr(request.app.state, "asset_root", None) or settings.data_dir
    return output_root(db, settings, default=default)


def set_output_setting(db, root: Path | None, actor: str) -> None:
    from shine.models import AppSetting, utcnow

    row = db.get(AppSetting, OUTPUT_SETTING_KEY)
    if root is None:
        if row is not None:
            db.delete(row)
        return
    value = {"root": str(root), "configured_at": utcnow().isoformat(), "configured_by": actor}
    if row is None:
        db.add(AppSetting(key=OUTPUT_SETTING_KEY, value=value))
    else:
        row.value = value


# ---------------------------------------------------------------------------
# migration lock


def migration_lock(db) -> dict | None:
    value = _setting_value(db, MIGRATION_LOCK_KEY)
    if not value:
        return None
    started = value.get("started_at")
    if started:
        try:
            when = datetime.fromisoformat(str(started))
            if when.tzinfo is None:
                when = when.replace(tzinfo=UTC)
            if datetime.now(UTC) - when > MIGRATION_STALE_AFTER:
                return None
        except ValueError:
            return None
    return value


def storage_migration_active(db) -> bool:
    return migration_lock(db) is not None


def ensure_storage_writable(db) -> None:
    """Refuse a new file while the output root is being switched."""
    if storage_migration_active(db):
        raise HTTPException(409, "STORAGE_MIGRATION_IN_PROGRESS")


def _begin_migration(db, root: Path, actor: str) -> None:
    from shine.models import AppSetting, utcnow

    value = {"root": str(root), "started_at": utcnow().isoformat(), "actor": actor}
    row = db.get(AppSetting, MIGRATION_LOCK_KEY)
    if row is None:
        db.add(AppSetting(key=MIGRATION_LOCK_KEY, value=value))
    else:
        row.value = value
    db.commit()


def _end_migration(db, *, commit: bool = True) -> None:
    from shine.models import AppSetting

    try:
        row = db.get(AppSetting, MIGRATION_LOCK_KEY)
        if row is not None:
            db.delete(row)
            if commit:
                db.commit()
            else:
                db.flush()
    except Exception:  # noqa: BLE001 - a stale lock expires on its own
        try:
            db.rollback()
        except Exception:  # noqa: BLE001
            pass


def _abort_migration(db) -> None:
    """Discard half-applied row changes, then release the write lock."""
    try:
        db.rollback()
    except Exception:  # noqa: BLE001
        pass
    _end_migration(db)


# ---------------------------------------------------------------------------
# classification and naming


def type_for_kind(kind: str | None, content_type: str = "") -> str:
    value = (kind or "").strip().lower()
    if value in {"video", "action"}:
        return TYPE_VIDEO
    if value in {"audio", "voice", "dialogue", "sfx", "music"}:
        return TYPE_VOICE
    if value in {"image", "costume", "scene", "prop", "keyframe", "storyboard", "text", "subtitle"}:
        return TYPE_MATERIAL
    if content_type.startswith("video/"):
        return TYPE_VIDEO
    if content_type.startswith("audio/"):
        return TYPE_VOICE
    return TYPE_MATERIAL


def type_for_artifact(artifact_kind: str, content_type: str = "") -> str:
    if artifact_kind in {"video", "delivery"}:
        return TYPE_VIDEO
    return type_for_kind(None, content_type)


def project_folder_name(db, project) -> str:
    """The project's folder, disambiguated only when two titles collide."""
    from shine.models import Project

    base = sanitize_component(getattr(project, "title", None))
    if base == MATERIAL_CENTER:
        # The material centre owns this folder name; a project with the same
        # title is disambiguated instead of silently sharing its directory.
        return f"{MATERIAL_CENTER}-{project.id[:6]}"
    others = db.scalars(select(Project).where(Project.id != project.id)).all()
    if any(sanitize_component(other.title) == base for other in others):
        return f"{base}-{project.id[:6]}"
    return base


def type_dir(folder: str, type_label: str, *, subdir: str | None = None) -> Path:
    parts = [folder, type_label]
    if subdir:
        parts.extend(part for part in Path(subdir).parts if part not in {"", ".", ".."})
    return Path(*parts)


def project_type_dir(db, project, type_label: str, *, subdir: str | None = None) -> Path:
    return type_dir(project_folder_name(db, project), type_label, subdir=subdir)


def material_center_dir(type_label: str, *, subdir: str | None = None) -> Path:
    parts = [MATERIAL_CENTER, type_label]
    if subdir:
        parts.extend(part for part in Path(subdir).parts if part not in {"", ".", ".."})
    return Path(*parts)


# ---------------------------------------------------------------------------
# storing new bytes


def _same_bytes(path: Path, digest: str, byte_size: int) -> bool:
    try:
        if path.stat().st_size != byte_size:
            return False
        with path.open("rb") as stream:
            return hashlib.file_digest(stream, "sha256").hexdigest() == digest
    except OSError:
        return False


def _free_relative(root: Path, relative: Path, digest: str, byte_size: int) -> Path:
    parent, stem, suffix = relative.parent, relative.stem, relative.suffix
    index = 2
    while True:
        candidate = parent / f"{stem}_v{index}{suffix}"
        target = root / candidate
        if not target.exists() or _same_bytes(target, digest, byte_size):
            return candidate
        index += 1


def store_named_file(root: Path, relative: Path, content: bytes, digest: str) -> str:
    """Write bytes under ``root/relative`` and return the actual relative path.

    An existing different file is never overwritten; it gets a short ``_vN``
    suffix.  Identical bytes reuse the existing file.
    """
    if relative.is_absolute() or ".." in relative.parts:
        raise HTTPException(500, "OUTPUT_PATH_INVALID")
    target = safe_asset_path(root, relative.as_posix())
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        if target.exists():
            if _same_bytes(target, digest, len(content)):
                return relative.as_posix()
            relative = _free_relative(root, relative, digest, len(content))
            target = root / relative
        if not target.exists():
            temporary = target.with_name(f"{target.name}.{uuid.uuid4().hex}.partial")
            try:
                temporary.write_bytes(content)
                os.replace(temporary, target)
            finally:
                temporary.unlink(missing_ok=True)
    except OSError as exc:
        if exc.errno in {28, 122}:  # ENOSPC / EDQUOT
            raise HTTPException(507, "Local storage is full while storing the output") from exc
        raise HTTPException(500, "Unable to store the output file") from exc
    return relative.as_posix()


def store_project_asset(
    db,
    settings,
    project,
    content: bytes,
    digest: str,
    *,
    kind: str,
    base: str,
    extension: str | None = None,
    root: Path | None = None,
    subdir: str | None = None,
) -> tuple[str, str]:
    """Store one project asset as ``<项目>/<素材|视频|音色>/项目_类型_名称``."""
    type_label = type_for_kind(kind)
    folder = project_type_dir(db, project, type_label, subdir=subdir)
    ext = extension or artifact_suffix("application/octet-stream")
    name = typed_filename(project_folder_name(db, project), type_label, base, ext)
    relative = folder / name
    actual = store_named_file(root or output_root(db, settings), relative, content, digest)
    return actual, Path(actual).name


def store_library_asset(
    db,
    settings,
    content: bytes,
    digest: str,
    *,
    kind: str,
    base: str,
    extension: str | None = None,
    root: Path | None = None,
    subdir: str | None = None,
) -> tuple[str, str]:
    """Store one material-centre output under ``素材中心/<类型>/``."""
    type_label = type_for_kind(kind)
    folder = material_center_dir(type_label, subdir=subdir)
    ext = extension or artifact_suffix("application/octet-stream")
    name = typed_filename(MATERIAL_CENTER, type_label, base, ext)
    actual = store_named_file(root or output_root(db, settings), folder / name, content, digest)
    return actual, Path(actual).name


def refresh_reference_paths(db, request):
    """Point a stored request's manifest at the files' current locations.

    A stored request keeps the path its references had when the job was
    created.  Moving the output root changes that path without changing the
    bytes or the asset identity, so in-memory consumers are refreshed here.
    """
    from shine.models import Asset, MediaArtifact

    changed = False
    items = []
    for item in request.reference_manifest:
        asset = db.get(Asset, item.asset_id)
        if asset is not None:
            current = asset.storage_path
        else:
            artifact = db.get(MediaArtifact, item.asset_id)
            current = artifact.storage_path if artifact is not None else None
        if current and current != item.storage_key:
            item = item.model_copy(update={"storage_key": current})
            changed = True
        items.append(item)
    return request.model_copy(update={"reference_manifest": items}) if changed else request


# ---------------------------------------------------------------------------
# readiness


def active_work_blockers(db) -> list[str]:
    """Everything that could still write media while a migration runs."""
    from shine.asset_center.models import LibraryRenderTask
    from shine.comfyops.ownership import LIVE_SERIES_STATUSES
    from shine.models import Job, ProductionSeries
    from shine.production.models import ACTIVE_RUN_STATUSES, ProductionRun

    blockers: list[str] = []
    jobs = db.scalar(
        select(func.count(Job.id)).where(Job.status.in_(_ACTIVE_JOB_STATUSES))
    ) or 0
    if jobs:
        blockers.append(f"{jobs} 个镜头任务待执行或正在生成")
    series = db.scalar(
        select(func.count(ProductionSeries.id)).where(
            ProductionSeries.status.in_(LIVE_SERIES_STATUSES)
        )
    ) or 0
    if series:
        blockers.append(f"{series} 个生产序列尚未结束")
    runs = db.scalar(
        select(func.count(ProductionRun.id)).where(
            ProductionRun.status.in_(ACTIVE_RUN_STATUSES)
        )
    ) or 0
    if runs:
        blockers.append(f"{runs} 个整片生产运行中")
    tasks = db.scalar(
        select(func.count(LibraryRenderTask.id)).where(
            LibraryRenderTask.status.in_(_ACTIVE_LIBRARY_TASKS)
        )
    ) or 0
    if tasks:
        blockers.append(f"{tasks} 个素材生成任务待执行或正在生成")
    return blockers


def validate_output_root(path: str | None, settings) -> Path | None:
    """Normalise and prove the directory can receive outputs.  ``None`` = default."""
    raw = str(path or "").strip()
    if not raw:
        return None
    candidate = Path(raw).expanduser()
    if not candidate.is_absolute():
        raise HTTPException(422, "OUTPUT_DIRECTORY_ABSOLUTE_REQUIRED")
    resolved = candidate.resolve()
    if resolved.parent == resolved:
        raise HTTPException(422, "OUTPUT_DIRECTORY_DRIVE_ROOT_REFUSED")
    try:
        if resolved.exists() and not resolved.is_dir():
            raise HTTPException(422, "OUTPUT_DIRECTORY_NOT_A_DIRECTORY")
        resolved.mkdir(parents=True, exist_ok=True)
        probe = resolved / f".shine-output-write-test-{uuid.uuid4().hex}"
        probe.write_text("ok", encoding="utf-8")
        probe.unlink(missing_ok=True)
    except HTTPException:
        raise
    except OSError as exc:
        raise HTTPException(422, "OUTPUT_DIRECTORY_NOT_WRITABLE") from exc
    return resolved


# ---------------------------------------------------------------------------
# migration


@dataclass
class PlannedMove:
    old_rel: str
    new_rel: str
    sha256: str
    byte_size: int
    label: str
    apply: Callable[[str, str], None]


@dataclass
class _Target:
    order: tuple
    directory: Path
    project_label: str
    type_label: str
    base: str
    extension: str
    old_rel: str
    sha256: str
    byte_size: int
    label: str
    apply: Callable[[str, str], None]


def _extension_for(old_rel: str, filename: str | None, content_type: str = "") -> str:
    suffix = Path(old_rel).suffix or Path(str(filename or "")).suffix
    return suffix or artifact_suffix(content_type)


def _collect_targets(db, old_root: Path) -> list[_Target]:
    from shine.asset_center.models import LibraryAsset, LibraryMedia, LibraryVersion
    from shine.comfyops.models import CandidateAsset
    from shine.models import Asset, Job, MediaArtifact, Project

    targets: list[_Target] = []
    projects: dict[str, object] = {}
    folders: dict[str, str] = {}

    def project_of(project_id: str):
        if project_id not in projects:
            projects[project_id] = db.get(Project, project_id)
        return projects[project_id]

    def folder_of(project) -> str:
        if project.id not in folders:
            folders[project.id] = project_folder_name(db, project)
        return folders[project.id]

    def safe_size(value) -> int:
        return int(value or 0)

    for asset in db.scalars(select(Asset)).all():
        project = project_of(asset.project_id)
        if project is None:
            continue
        kind = asset.kind
        type_label = type_for_kind(kind)
        base = asset.entity_id or readable_base(asset.filename, kind or "素材")
        extension = _extension_for(asset.storage_path, asset.filename, asset.content_type)
        where = type_dir(folder_of(project), type_label)

        def apply(rel: str, name: str, row=asset) -> None:
            row.storage_path = rel
            if not is_readable_ledger_name(row.filename):
                row.filename = name

        targets.append(_Target(
            (folder_of(project), type_label, asset.created_at, asset.id),
            where, folder_of(project), type_label, base, extension,
            asset.storage_path, asset.sha256, safe_size(asset.byte_size),
            f"主素材 {asset.filename}", apply,
        ))

    for row in db.scalars(select(CandidateAsset)).all():
        project = project_of(row.project_id)
        payload = dict(row.payload or {})
        if project is None or not payload.get("storage_path"):
            continue
        kind = str(payload.get("kind") or "image")
        type_label = type_for_kind(kind, str(payload.get("content_type") or ""))
        base = readable_base(
            payload.get("filename"),
            str(payload.get("entity_id") or payload.get("family") or "素材"),
        )
        extension = _extension_for(str(payload["storage_path"]), payload.get("filename"),
                                   str(payload.get("content_type") or ""))
        where = type_dir(folder_of(project), type_label)

        def apply(rel: str, name: str, item=row, previous=payload) -> None:
            from shine.comfy.workflow import graph_hash

            display = previous.get("filename")
            if not is_readable_ledger_name(display):
                display = name
            updated = {**previous, "storage_path": rel, "filename": display}
            item.payload = updated
            item.sha256 = graph_hash(updated)

        targets.append(_Target(
            (folder_of(project), type_label, row.created_at, row.id),
            where, folder_of(project), type_label, base, extension,
            str(payload["storage_path"]), str(payload.get("content_sha256") or ""),
            safe_size(payload.get("byte_size")),
            f"候选素材 {payload.get('filename')}", apply,
        ))

    for media in db.scalars(select(LibraryMedia)).all():
        type_label = type_for_kind(media.kind, media.content_type)
        base = readable_base(media.filename, media.purpose or media.kind)
        extension = _extension_for(media.storage_path, media.filename, media.content_type)
        where = material_center_dir(type_label)

        def apply(rel: str, name: str, row=media) -> None:
            row.storage_path = rel
            if not is_readable_ledger_name(row.filename):
                row.filename = name

        targets.append(_Target(
            (MATERIAL_CENTER, type_label, media.created_at, media.id),
            where, MATERIAL_CENTER, type_label, base, extension,
            media.storage_path, media.sha256, safe_size(media.byte_size),
            f"素材中心文件 {media.filename}", apply,
        ))

    for version in db.scalars(select(LibraryVersion)).all():
        asset = db.get(LibraryAsset, version.library_asset_id)
        title = asset.title if asset is not None else version.filename
        base = f"{readable_base(title, version.filename)}_v{version.ordinal}"
        type_label = type_for_kind(version.kind, version.content_type)
        extension = _extension_for(version.storage_path, version.filename, version.content_type)
        where = material_center_dir(type_label)

        def apply(rel: str, name: str, row=version) -> None:
            row.storage_path = rel
            if not is_readable_ledger_name(row.filename):
                row.filename = name

        targets.append(_Target(
            (MATERIAL_CENTER, type_label, version.created_at, version.id),
            where, MATERIAL_CENTER, type_label, base, extension,
            version.storage_path, version.content_sha256, safe_size(version.byte_size),
            f"素材中心版本 {title}", apply,
        ))

    from shine.media.video_layout import delivery_subdir, shot_output_subdir

    for artifact in db.scalars(select(MediaArtifact)).all():
        project = project_of(artifact.project_id)
        if project is None or artifact.deleted_at is not None:
            # A deleted video is a tombstone; its bytes were intentionally
            # removed and must not block the directory change.
            continue
        job = db.get(Job, artifact.job_id) if artifact.job_id else None
        if artifact.artifact_kind == "video":
            from shine.media.video_naming import shot_label

            base = shot_label(db, job) if job is not None else "镜号未登记"
            type_label = TYPE_VIDEO
            where = type_dir(
                folder_of(project), TYPE_VIDEO,
                subdir=shot_output_subdir(db, job) if job else None,
            )
        elif artifact.artifact_kind == "delivery":
            from shine.media.video_naming import delivery_label

            job_ids = [
                str(clip.get("job_id"))
                for clip in ((artifact.provenance or {}).get("clips") or [])
                if clip.get("job_id")
            ]
            jobs = [db.get(Job, job_id) for job_id in job_ids]
            jobs = [item for item in jobs if item is not None]
            base = delivery_label(db, artifact.project_id, jobs) if jobs else "成片"
            type_label = TYPE_VIDEO
            where = type_dir(folder_of(project), TYPE_VIDEO, subdir=delivery_subdir(db, artifact))
        else:
            type_label = type_for_artifact(artifact.artifact_kind, artifact.content_type)
            source = ((artifact.provenance or {}).get("source") or {}) if isinstance(artifact.provenance, dict) else {}
            source_job = db.get(Job, source.get("job_id")) if source.get("job_id") else None
            mode = str((artifact.provenance or {}).get("mode") or "reference")
            if source_job is not None:
                from shine.media.video_naming import shot_label

                base = f"连续性_{mode}_{shot_label(db, source_job)}"
            else:
                base = f"连续性_{mode}"
            where = type_dir(folder_of(project), type_label, subdir="连续性")

        def apply(rel: str, _name: str, row=artifact) -> None:
            row.storage_path = rel

        targets.append(_Target(
            (folder_of(project), type_label, artifact.created_at, artifact.id),
            where, folder_of(project), type_label, base,
            _extension_for(artifact.storage_path, None, artifact.content_type),
            artifact.storage_path, artifact.sha256, safe_size(artifact.byte_size),
            f"成片文件 {artifact.storage_path}", apply,
        ))

    targets.sort(key=lambda item: item.order)
    return targets


def _plan(db, new_root: Path) -> list[PlannedMove]:
    planned: dict[str, tuple[str, int]] = {}
    moves: list[PlannedMove] = []
    for target in _collect_targets(db, new_root):
        if not target.old_rel:
            continue
        if not target.sha256:
            raise StorageMigrationError(
                "OUTPUT_DIRECTORY_SOURCE_INVALID",
                "有一条台账记录缺少内容哈希，无法证明迁移后的文件是同一份",
                {"label": target.label},
            )
        name = typed_filename(target.project_label, target.type_label, target.base, target.extension)
        relative = target.directory / name
        existing = planned.get(relative.as_posix())
        if existing is not None:
            if existing == (target.sha256, target.byte_size):
                moves.append(PlannedMove(
                    target.old_rel, relative.as_posix(), target.sha256, target.byte_size,
                    target.label, target.apply,
                ))
                continue
            relative = _free_planned(new_root, relative, target.sha256, target.byte_size, planned)
        else:
            on_disk = new_root / relative
            if on_disk.exists() and not _same_bytes(on_disk, target.sha256, target.byte_size):
                relative = _free_planned(new_root, relative, target.sha256, target.byte_size, planned)
        planned[relative.as_posix()] = (target.sha256, target.byte_size)
        moves.append(PlannedMove(
            target.old_rel, relative.as_posix(), target.sha256, target.byte_size,
            target.label, target.apply,
        ))
    return moves


def _free_planned(root: Path, relative: Path, digest: str, byte_size: int,
                  planned: dict[str, tuple[str, int]]) -> Path:
    parent, stem, suffix = relative.parent, relative.stem, relative.suffix
    index = 2
    while True:
        candidate = parent / f"{stem}_v{index}{suffix}"
        if candidate.as_posix() in planned:
            index += 1
            continue
        target = root / candidate
        if not target.exists() or _same_bytes(target, digest, byte_size):
            return candidate
        index += 1


def _copy_verified(source: Path, target: Path, digest: str, byte_size: int) -> bool:
    """Copy (or hardlink) one file and prove the target has the ledger bytes."""
    try:
        if source.stat().st_size != byte_size:
            raise StorageMigrationError(
                "OUTPUT_DIRECTORY_SOURCE_INVALID",
                "源文件大小与台账不一致，已停止迁移，未切换目录",
                {"source": str(source), "expected_bytes": byte_size},
            )
        with source.open("rb") as stream:
            actual = hashlib.file_digest(stream, "sha256").hexdigest()
        if actual != digest:
            raise StorageMigrationError(
                "OUTPUT_DIRECTORY_SOURCE_INVALID",
                "源文件内容与台账哈希不一致，已停止迁移，未切换目录",
                {"source": str(source)},
            )
        target.parent.mkdir(parents=True, exist_ok=True)
        if target.exists():
            if _same_bytes(target, digest, byte_size):
                return False
            raise StorageMigrationError(
                "OUTPUT_DIRECTORY_TARGET_CONFLICT",
                "目标目录里已有同名但内容不同的文件，已停止迁移",
                {"target": str(target)},
            )
        temporary = target.with_name(f"{target.name}.{uuid.uuid4().hex}.partial")
        try:
            try:
                os.link(source, temporary)
            except OSError:
                shutil.copyfile(source, temporary)
            os.replace(temporary, target)
        finally:
            temporary.unlink(missing_ok=True)
        return True
    except StorageMigrationError:
        raise
    except OSError as exc:
        raise StorageMigrationError(
            "OUTPUT_DIRECTORY_COPY_FAILED",
            "复制文件失败，已停止迁移，未切换目录",
            {"source": str(source), "target": str(target), "error": str(exc)[:200]},
        ) from exc


def _referenced_paths(db) -> set[str]:
    """Every path the ledger still names after the switch."""
    from shine.asset_center.models import LibraryMedia, LibraryVersion
    from shine.comfyops.models import CandidateAsset
    from shine.models import Asset, JobAttempt, MediaArtifact

    paths: set[str] = set()
    for value in db.scalars(select(Asset.storage_path)).all():
        if value:
            paths.add(str(value))
    for payload in db.scalars(select(CandidateAsset.payload)).all():
        if payload and payload.get("storage_path"):
            paths.add(str(payload["storage_path"]))
    for value in db.scalars(select(LibraryMedia.storage_path)).all():
        paths.add(str(value))
    for value in db.scalars(select(LibraryVersion.storage_path)).all():
        paths.add(str(value))
    for value in db.scalars(select(MediaArtifact.storage_path)).all():
        paths.add(str(value))
    for value in db.scalars(select(JobAttempt.output_path)).all():
        if value:
            paths.add(str(value))
    for provenance in db.scalars(select(MediaArtifact.provenance)).all():
        source = (provenance or {}).get("source") if isinstance(provenance, dict) else None
        if isinstance(source, dict) and source.get("storage_path"):
            paths.add(str(source["storage_path"]))
    return paths


def _apply_moves(db, root: Path | None, moves: list[PlannedMove], *, actor: str,
                 moved_count: int) -> None:
    from shine.models import JobAttempt, MediaArtifact

    db.execute(text("BEGIN IMMEDIATE"))
    for move in moves:
        move.apply(move.new_rel, Path(move.new_rel).name)
    path_map = {move.old_rel: move.new_rel for move in moves if move.old_rel != move.new_rel}
    if path_map:
        attempts = db.scalars(select(JobAttempt).where(JobAttempt.output_path.is_not(None))).all()
        for attempt in attempts:
            replacement = path_map.get(str(attempt.output_path))
            if replacement:
                attempt.output_path = replacement
        rows = db.scalars(
            select(MediaArtifact).where(MediaArtifact.artifact_kind == "continuity")
        ).all()
        for row in rows:
            provenance = row.provenance if isinstance(row.provenance, dict) else None
            source = provenance.get("source") if provenance and isinstance(provenance.get("source"), dict) else None
            if not source:
                continue
            replacement = path_map.get(str(source.get("storage_path")))
            if replacement:
                row.provenance = {**provenance, "source": {**source, "storage_path": replacement}}
    set_output_setting(db, root, actor)
    _end_migration(db, commit=False)
    from shine.services import add_audit

    add_audit(
        db, actor, "OUTPUT_DIRECTORY_CHANGED", "app_setting", OUTPUT_SETTING_KEY,
        {"root": str(root) if root else None, "moved": moved_count},
    )
    db.commit()


def _cleanup_old_files(db, old_root: Path, new_root: Path,
                       moves: list[PlannedMove]) -> tuple[int, list[str]]:
    live = _referenced_paths(db)
    deleted = 0
    kept: list[str] = []
    seen: set[str] = set()
    for move in moves:
        if move.old_rel in seen:
            continue
        same_root = old_root.resolve() == new_root.resolve()
        if move.old_rel == move.new_rel and same_root:
            continue
        seen.add(move.old_rel)
        if same_root and move.old_rel in live:
            continue
        try:
            path = safe_asset_path(old_root, move.old_rel)
        except HTTPException:
            kept.append(move.old_rel)
            continue
        if not path.is_file():
            continue
        try:
            path.unlink()
            deleted += 1
        except OSError:
            kept.append(move.old_rel)
            continue
        parent = path.parent
        while parent != old_root and parent.is_dir():
            try:
                parent.rmdir()
            except OSError:
                break
            parent = parent.parent
    return deleted, kept


def migrate_output_root(
    db, settings, *, target: Path | None, actor: str, default_root: Path | None = None,
) -> dict:
    """Copy, verify and switch every produced file to the new layout.

    ``target=None`` means the existing default data directory.  The migration
    is refused while any media-producing work is still pending.  The ledger is
    changed only after every planned file has been copied and verified.
    """
    from shine.models import utcnow

    if storage_migration_active(db):
        raise HTTPException(409, {
            "code": "STORAGE_MIGRATION_IN_PROGRESS",
            "message": "另一次输出目录迁移还没有结束，请等它完成",
        })
    blockers = active_work_blockers(db)
    if blockers:
        raise HTTPException(409, {
            "code": "OUTPUT_DIRECTORY_BUSY",
            "message": "本机还有拍摄或生成任务，等它们结束后再调整输出目录",
            "blockers": blockers,
        })
    old_root = output_root(db, settings, default=default_root)
    new_root = Path(target) if target is not None else Path(default_root or settings.data_dir)
    if new_root == old_root:
        # Re-organising the current root is still a migration: legacy files
        # move into the two-level layout and get readable names.
        pass
    new_root.mkdir(parents=True, exist_ok=True)
    _begin_migration(db, new_root, actor)
    # The lock makes the Worker defer new claims; re-check the work that may
    # have been claimed between the first check and the lock.
    blockers = active_work_blockers(db)
    if blockers:
        _end_migration(db)
        raise HTTPException(409, {
            "code": "OUTPUT_DIRECTORY_BUSY",
            "message": "本机还有拍摄或生成任务，等它们结束后再调整输出目录",
            "blockers": blockers,
        })
    started = utcnow()
    try:
        moves = _plan(db, new_root)
        copied = 0
        bytes_copied = 0
        for move in moves:
            source = safe_asset_path(old_root, move.old_rel)
            target_path = safe_asset_path(new_root, move.new_rel)
            if source.resolve() == target_path.resolve():
                continue
            if _copy_verified(source, target_path, move.sha256, move.byte_size):
                copied += 1
                bytes_copied += move.byte_size
        relocated = [
            move for move in moves
            if (old_root / move.old_rel).resolve() != (new_root / move.new_rel).resolve()
        ]
        _apply_moves(db, target, moves, actor=actor, moved_count=len(relocated))
        deleted, kept = _cleanup_old_files(db, old_root, new_root, moves)
        return {
            "root": str(new_root),
            "custom": target is not None,
            "moved": len(relocated),
            "copied": copied,
            "bytes_copied": bytes_copied,
            "deleted": deleted,
            "kept": kept,
            "duration_s": round((utcnow() - started).total_seconds(), 2),
        }
    except HTTPException:
        _abort_migration(db)
        raise
    except StorageMigrationError as exc:
        _abort_migration(db)
        raise HTTPException(409, {
            "code": exc.code,
            "message": exc.message,
            "evidence": exc.evidence,
        }) from exc
    except Exception:
        _abort_migration(db)
        raise


# ---------------------------------------------------------------------------
# summary for the settings page


def directory_summary(db, settings, *, default_root: Path | None = None) -> dict:
    from shine.models import Project

    root = output_root(db, settings, default=default_root)
    configured = configured_output_root(db, settings)
    projects = db.scalars(select(Project).order_by(Project.created_at)).all()
    total_bytes = 0
    files = 0
    for path in _referenced_paths(db):
        try:
            candidate = safe_asset_path(root, path)
        except HTTPException:
            continue
        if candidate.is_file():
            files += 1
            total_bytes += candidate.stat().st_size
    lock = migration_lock(db)
    free_gb = 0.0
    try:
        free_gb = round(shutil.disk_usage(root).free / (1024 ** 3), 1)
    except OSError:
        pass
    return {
        "root": str(root),
        "default_root": str(default_root or settings.data_dir),
        "custom": configured is not None,
        "layout": {
            "material": TYPE_MATERIAL,
            "video": TYPE_VIDEO,
            "voice": TYPE_VOICE,
            "material_center": MATERIAL_CENTER,
        },
        "projects": [
            {"project_id": project.id, "title": project.title, "folder": project_folder_name(db, project)}
            for project in projects
        ],
        "file_count": files,
        "byte_size": total_bytes,
        "free_gb": free_gb,
        "migration": lock,
        "blockers": active_work_blockers(db),
    }
