"""Discover installations without executing them; import only pinned bytes into our cache."""

from __future__ import annotations

import hashlib
import os
import subprocess
import tempfile
from pathlib import Path

from fastapi import HTTPException

from shine.comfy.workflow import graph_hash
from shine.comfyops.installer import ControlledDownloader, PackageFile


def local_path(value: str) -> Path:
    # Never turn a local setup action into an SMB/network read.
    if value.startswith(('\\\\', '//')) or not Path(value).is_absolute():
        raise HTTPException(422, '请选择本机上的绝对目录，不支持网络路径。')
    path = Path(value).resolve()
    if str(path).startswith(('\\\\', '//')):
        raise HTTPException(422, '请选择本机目录，不支持网络链接。')
    return path


def comfy_root(path: Path) -> Path | None:
    for candidate in (path, path / 'ComfyUI'):
        if (candidate / 'main.py').is_file() and (candidate / 'comfy').is_dir():
            return candidate.resolve()
    return None


def _source_software_state(root: Path, catalog: dict) -> dict:
    """Identify an existing ComfyUI checkout without executing its Python code.

    A matching Git revision is useful installation evidence, but it is not
    runtime or node compatibility evidence. Tracked edits make the source
    version non-matching; untracked user files such as runtime caches are not
    used to reject the installation.
    """
    source = next((c for c in catalog.get("components", []) if c.get("kind") == "source"), None)
    expected = str(source.get("revision") or "") if source else ""
    actual = None
    tracked_clean = None
    if (root / ".git").exists():
        env = {**os.environ, "GIT_OPTIONAL_LOCKS": "0"}
        try:
            revision = subprocess.run(
                ["git", "-C", str(root), "rev-parse", "HEAD"],
                stdin=subprocess.DEVNULL, capture_output=True, text=True,
                timeout=5, check=False, env=env,
            )
            if revision.returncode == 0:
                actual = revision.stdout.strip()
                dirty = subprocess.run(
                    ["git", "-C", str(root), "diff", "--quiet", "HEAD", "--"],
                    stdin=subprocess.DEVNULL, capture_output=True, text=True,
                    timeout=5, check=False, env=env,
                )
                if dirty.returncode in {0, 1}:
                    tracked_clean = dirty.returncode == 0
        except (OSError, subprocess.SubprocessError):
            pass

    if actual and expected and actual != expected:
        version_status = "INSTALLED_VERSION_MISMATCH"
    elif actual and expected and tracked_clean is False:
        version_status = "INSTALLED_VERSION_MODIFIED"
    elif actual and expected and tracked_clean is True:
        version_status = "INSTALLED_VERSION_MATCH"
    else:
        version_status = "INSTALLED_VERSION_UNKNOWN"

    python_candidates = (
        root.parent / "python_embeded" / "python.exe",
        root.parent / "python_embedded" / "python.exe",
        root / ".venv" / "Scripts" / "python.exe",
        root / "venv" / "Scripts" / "python.exe",
    )
    python = next((path.resolve() for path in python_candidates if path.is_file()), None)
    expected_nodes = [c["component_id"] for c in catalog.get("components", [])
                      if c.get("kind") == "node"]
    nodes = {node: (root / "custom_nodes" / node).is_dir() for node in expected_nodes}
    return {
        "installed": True,
        "version_status": version_status,
        "actual_revision": actual,
        "expected_revision": expected or None,
        "tracked_files_clean": tracked_clean,
        "runtime": {
            "status": "PYTHON_PRESENT_COMPATIBILITY_UNVERIFIED" if python else "PYTHON_NOT_FOUND",
            "python_executable": str(python) if python else None,
        },
        "nodes": nodes,
        "node_revisions_verified": False,
    }


def _stat_identity(stat) -> dict:
    return {
        "size": stat.st_size,
        "mtime_ns": getattr(stat, "st_mtime_ns", int(stat.st_mtime * 1_000_000_000)),
        "device": getattr(stat, "st_dev", None),
        "inode": getattr(stat, "st_ino", None),
    }


def fixed_drives() -> list[Path]:
    if os.name != 'nt':
        return []
    import ctypes

    return [Path(f'{chr(code)}:/') for code in range(67, 91)
            if ctypes.windll.kernel32.GetDriveTypeW(f'{chr(code)}:\\') == 3]


def discover(roots: list[str], drives: list[Path] | None = None) -> dict:
    """Bounded common-location discovery, never a recursive whole-disk scan."""
    candidates = [local_path(r) for r in roots]
    unreadable = []
    for drive in fixed_drives() if drives is None else drives:
        try:
            children = list(drive.iterdir())[:200]
            candidates.extend(p for p in children if 'comfy' in p.name.lower())
            for parent in children:
                if parent.is_dir() and parent.name.lower().startswith(('ai', 'tools', 'apps', 'comfy')):
                    try:
                        candidates.extend(p for p in list(parent.iterdir())[:200] if 'comfy' in p.name.lower())
                    except OSError:
                        unreadable.append(str(parent))
        except OSError:
            unreadable.append(str(drive))
    found = set()
    for candidate in candidates:
        try:
            root = comfy_root(candidate)
            if root and not str(root).startswith(('\\\\', '//')):
                found.add(str(root))
        except OSError:
            unreadable.append(str(candidate))
    return {'installations': sorted(found), 'unreadable': unreadable,
            'coverage': 'COMMON_LOCATIONS_ONLY', 'runtime_verified': False}


def inspect(root_value: str, catalog: dict) -> dict:
    selected = local_path(root_value)
    root = comfy_root(selected)
    if root is None:
        raise HTTPException(422, '该目录不是 ComfyUI，请选择包含 main.py 的目录或整合包目录。')
    # Inventory model files only. Junctions, external model paths and linked folders
    # are deliberately not followed without selecting those roots separately.
    sizes: dict[int, list[tuple[Path, dict]]] = {}
    skipped = []
    count = 0
    models = root / 'models'
    if models.resolve() != models:
        skipped.append(str(models))
    elif models.is_dir():
        def onerror(error):
            skipped.append(str(error.filename))

        for folder, dirs, files in os.walk(models, followlinks=False, onerror=onerror):
            directory = Path(folder)
            linked = [d for d in dirs if (directory / d).resolve() != directory / d]
            skipped.extend(str(directory / d) for d in linked)
            dirs[:] = sorted(d for d in dirs if d not in linked)
            for name in sorted(files):
                count += 1
                if count > 50000:
                    raise HTTPException(409, '模型目录文件过多，请缩小扫描范围。')
                file = directory / name
                if file.resolve() != file:
                    skipped.append(str(file))
                    continue
                try:
                    stat = file.stat()
                    sizes.setdefault(stat.st_size, []).append((file, _stat_identity(stat)))
                except OSError:
                    skipped.append(str(file))
    matches, missing = [], []
    for component in catalog['components']:
        if component['kind'] not in {'model', 'review-model'}:
            continue
        for spec in component['files']:
            # Size is a candidate filter only. Renamed files are eligible; hashes
            # are verified while copying, never inferred from names or size.
            candidates = sizes.get(spec['size'], [])
            entry = {'component_id': component['component_id'], 'relative_path': spec['relative_path'],
                     'sha256': spec['sha256'], 'size': spec['size']}
            if candidates:
                matches.append({**entry,
                                'sources': [str(path) for path, _ in candidates],
                                'source_stats': {str(path): stat for path, stat in candidates},
                                'status': 'PENDING_HASH'})
            else:
                missing.append(entry)
    software = _source_software_state(root, catalog)
    payload = {'root': str(root), 'software': software, 'matches': matches, 'missing': missing,
               'skipped': skipped, 'scanned_files': count, 'runtime_verified': False,
               'catalog_id': catalog['catalog_id'], 'catalog_version': catalog['catalog_version']}
    return {**payload, 'snapshot': graph_hash(payload),
            'candidate_bytes': sum(row['size'] for row in matches)}


def verify_models(report: dict, snapshot: str) -> dict:
    """Confirm exact catalog model bytes in place; never copies or writes them."""
    if snapshot != report['snapshot']:
        raise HTTPException(409, '已有目录或部署清单已变化，请重新检测后确认。')
    verified, rejected = [], []
    for match in report['matches']:
        valid_source = None
        for source_value in match['sources']:
            source = Path(source_value)
            try:
                if source.resolve() != source or not source.is_file():
                    continue
                expected_stat = match.get('source_stats', {}).get(source_value)
                with source.open('rb') as stream:
                    before = _stat_identity(os.fstat(stream.fileno()))
                    if expected_stat and before != expected_stat:
                        continue
                    digest = hashlib.file_digest(stream, 'sha256').hexdigest()
                    after = _stat_identity(os.fstat(stream.fileno()))
                path_after = _stat_identity(source.stat())
                if (before == after == path_after and before['size'] == match['size']
                        and digest == match['sha256']):
                    valid_source = source_value
                    break
            except OSError:
                continue
        if valid_source:
            verified.append({**match, 'source': valid_source, 'status': 'INSTALLED_HASH_MATCH'})
        else:
            rejected.append({**match, 'status': 'HASH_MISMATCH_OR_CHANGED'})
    return {
        'status': 'INSTALLED_VERIFIED',
        'software': report['software'],
        'verified': verified,
        'rejected': rejected,
        'verified_bytes': sum(row['size'] for row in verified),
        'copied_bytes': 0,
        'runtime_verified': False,
    }


def import_models(settings, report: dict, snapshot: str) -> dict:
    if snapshot != report['snapshot']:
        raise HTTPException(409, '已有目录或部署清单已变化，请重新检测后确认复用。')
    from shine.comfyops.storage import require_storage

    cache = require_storage(settings) / 'isolated-packages' / '.cache'
    cache.mkdir(parents=True, exist_ok=True)
    imported, rejected = [], []
    for match in report['matches']:
        target = cache / match['sha256']
        spec = PackageFile(relative_path=match['relative_path'], sha256=match['sha256'],
                           size=match['size'], url='https://huggingface.co/local-import')
        if target.exists() and ControlledDownloader.valid(target, spec):
            imported.append({**match, 'status': 'CACHED'})
            continue
        valid = False
        for source in match['sources']:
            temporary = None
            try:
                if Path(source).resolve() != Path(source):
                    raise HTTPException(409, '模型路径已变为链接，请重新检测目录。')
                # Copy and hash the same stream. The source is never modified or
                # hardlinked, and partial copies never become valid cache entries.
                with Path(source).open('rb') as stream, tempfile.NamedTemporaryFile(
                    dir=cache, suffix='.import', delete=False,
                ) as output:
                    temporary = Path(output.name)
                    digest, size = hashlib.sha256(), 0
                    while chunk := stream.read(8 * 1024**2):
                        size += len(chunk)
                        if size > match['size']:
                            break
                        digest.update(chunk)
                        output.write(chunk)
                    output.flush()
                    os.fsync(output.fileno())
                if size == match['size'] and digest.hexdigest() == match['sha256']:
                    os.replace(temporary, target)
                    imported.append({**match, 'status': 'CACHED', 'source': source})
                    valid = True
                    break
            except OSError as exc:
                raise HTTPException(409, '模型读取或缓存写入失败，请检查权限及磁盘空间后重新检测。') from exc
            finally:
                if temporary:
                    temporary.unlink(missing_ok=True)
        if not valid:
            rejected.append({**match, 'status': 'HASH_MISMATCH'})
    return {'status': 'CACHED_NOT_INSTALLED', 'imported': imported, 'rejected': rejected,
            'reused_bytes': sum(row['size'] for row in imported), 'runtime_verified': False}
