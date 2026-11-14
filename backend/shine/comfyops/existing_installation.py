"""Discover installations without executing them; import only pinned bytes into our cache."""

from __future__ import annotations

import hashlib
import os
import re
import subprocess
import tempfile
import time
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


#: Files a ComfyUI checkout ships that declare a release version.  The version
#: is evidence of *which software* is installed; it is deliberately not turned
#: into a Git revision, because the two are different facts.
_VERSION_FILES = ('comfyui_version.py', 'version.py', 'pyproject.toml')
_VERSION_PATTERN = re.compile(
    r'(?:__version__|version)\s*[:=]\s*["\']([0-9][^"\']*)["\']', re.IGNORECASE,
)


def declared_version(root: Path) -> tuple[str | None, str | None]:
    """Read a locally declared version, with the file that declared it.

    Never executes anything and never infers a commit.  A missing declaration
    stays unknown rather than being guessed from a directory name.
    """
    for name in _VERSION_FILES:
        path = root / name
        try:
            if not path.is_file() or path.is_symlink():
                continue
            text = path.read_text(encoding='utf-8', errors='replace')[:65536]
        except OSError:
            continue
        match = _VERSION_PATTERN.search(text)
        if match:
            return match.group(1).strip(), name
    return None, None


def _git_commit(path: Path) -> str | None:
    if not (path / '.git').exists():
        return None
    try:
        result = subprocess.run(
            ['git', '-C', str(path), 'rev-parse', 'HEAD'],
            stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=5,
            check=False, env={**os.environ, 'GIT_OPTIONAL_LOCKS': '0'},
        )
    except (OSError, subprocess.SubprocessError):
        return None
    return result.stdout.strip() if result.returncode == 0 else None


def node_package_inventory(root: Path, catalog: dict) -> dict:
    """Every custom node package on disk, with the evidence it really has.

    Installed is a directory fact; a source revision is separate evidence.  A
    package without a readable version is reported as ``VERSION_UNKNOWN`` -
    never assigned a guessed release.
    """
    expected = {
        component['component_id']
        for component in catalog.get('components', [])
        if component.get('kind') == 'node'
    }
    custom = root / 'custom_nodes'
    packages = []
    skipped = []
    if custom.is_dir():
        try:
            entries = sorted(custom.iterdir(), key=lambda row: row.name.lower())
        except OSError:
            entries = []
        for entry in entries:
            if entry.name.startswith(('.', '__')) or entry.name in {'__pycache__'}:
                continue
            if entry.is_symlink():
                skipped.append({'name': entry.name, 'reason': 'SYMLINK_NOT_FOLLOWED'})
                continue
            if not entry.is_dir():
                # A single-file node (the project's own sampling node, for
                # example) is a real package too.
                if entry.suffix == '.py' and entry.is_file():
                    packages.append({
                        'name': entry.name,
                        'path': str(entry),
                        'kind': 'SINGLE_FILE_NODE',
                        'version': None,
                        'version_source': None,
                        'version_status': 'VERSION_UNKNOWN',
                        'git_commit': None,
                        'required_by_catalog': entry.name in expected,
                    })
                continue
            version, version_source = declared_version(entry)
            commit = _git_commit(entry)
            packages.append({
                'name': entry.name,
                'path': str(entry),
                'kind': 'CUSTOM_NODE_PACKAGE',
                'version': version,
                'version_source': version_source,
                'version_status': (
                    'VERSION_DECLARED' if version
                    else ('GIT_REVISION_OBSERVED' if commit else 'VERSION_UNKNOWN')
                ),
                'git_commit': commit,
                'required_by_catalog': entry.name in expected,
                # Mapping a node *class* to the package that registers it needs
                # the running service's object_info; a directory scan cannot
                # claim it.
                'node_class_mapping': 'REQUIRES_RUNNING_SERVICE',
            })
    return {
        'source': 'READ_ONLY_DIRECTORY_SCAN',
        'custom_nodes_root': str(custom),
        'expected_by_catalog': sorted(expected),
        'packages': packages,
        'skipped': skipped,
        'node_class_mapping_verified': False,
    }


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

    local_version, local_version_source = declared_version(root)
    if actual and expected and actual != expected:
        version_status = "INSTALLED_VERSION_MISMATCH"
    elif actual and expected and tracked_clean is False:
        version_status = "INSTALLED_VERSION_MODIFIED"
    elif actual and expected and tracked_clean is True:
        version_status = "INSTALLED_VERSION_MATCH"
    elif local_version:
        # A release number read from the installation itself is real evidence
        # of which software is installed.  It is not a Git commit and is never
        # compared to the pinned revision as if it were one.
        version_status = "INSTALLED_VERSION_OBSERVED"
    else:
        version_status = "INSTALLED_VERSION_UNKNOWN"
    version_source = "git" if actual else local_version_source
    version_confidence = (
        "HIGH" if version_status == "INSTALLED_VERSION_MATCH"
        else ("MEDIUM" if version_status in {"INSTALLED_VERSION_MODIFIED",
                                            "INSTALLED_VERSION_MISMATCH",
                                            "INSTALLED_VERSION_OBSERVED"} else "NONE")
    )

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
        "actual_version": local_version,
        "version_source": version_source,
        "version_confidence": version_confidence,
        "tracked_files_clean": tracked_clean,
        "runtime": {
            "status": "PYTHON_PRESENT_COMPATIBILITY_UNVERIFIED" if python else "PYTHON_NOT_FOUND",
            "python_executable": str(python) if python else None,
        },
        "nodes": nodes,
        "version_evidence": {
            "sources": [row for row in (
                "git_revision" if actual else None,
                local_version_source,
            ) if row],
            "pinned_revision_compared": bool(actual and expected),
            "version_is_not_a_commit": True,
        },
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


#: A person-declared root is walked to a bounded depth because downloads are
#: commonly nested (``Downloads/<bundle>/ComfyUI``).  Fixed drives get a
#: slightly shallower walk.  Both stop at a per-seed directory budget and skip
#: system/cache trees, so a read-only probe can never become an unbounded
#: whole-disk crawl.
CONFIGURED_ROOT_DEPTH = 6
DRIVE_SCAN_DEPTH = 4
SCAN_DIRECTORY_BUDGET = 12_000
SCAN_TIME_BUDGET_S = 6.0
_SCAN_SKIP_DIRECTORIES = frozenset({
    '$recycle.bin', 'system volume information', 'windows', 'winsxs',
    'program files', 'program files (x86)', 'programdata', 'appdata',
    'recovery', 'perflogs', 'msocache', 'node_modules', '.git',
    '__pycache__', '.venv', 'venv', 'site-packages',
})


def _is_local(path: Path) -> bool:
    return not str(path).startswith(('\\\\', '//'))


def _is_comfy_directory(path: Path) -> bool:
    try:
        return (path / 'main.py').is_file() and (path / 'comfy').is_dir()
    except OSError:
        return False


def _discovery_priority(entry) -> tuple[int, str]:
    name = entry.name.lower()
    if 'comfy' in name:
        return (0, name)
    if name.startswith(('ai', 'sd', 'stable', 'download', 'tools', 'apps', 'models')):
        return (1, name)
    return (2, name)


def _scan_for_comfy(seed: Path, depth: int, unreadable: list[str],
                    deadline: float) -> tuple[list[Path], int, bool]:
    """Depth-first bounded search for a directory that looks like ComfyUI.

    Likely names (``*comfy*``, downloads, AI folders) are visited first so the
    budget is spent where a real installation usually lives.
    """
    found: list[Path] = []
    visited = 0
    truncated = False
    stack: list[tuple[Path, int]] = [(seed, 0)]
    while stack:
        if visited >= SCAN_DIRECTORY_BUDGET or time.monotonic() >= deadline:
            truncated = True
            break
        directory, level = stack.pop()
        visited += 1
        if _is_comfy_directory(directory):
            found.append(directory.resolve())
            continue
        if level >= depth:
            continue
        try:
            children = list(os.scandir(directory))
        except OSError:
            unreadable.append(str(directory))
            continue
        children.sort(key=_discovery_priority)
        # Push in reverse so the most likely child is the next directory popped.
        for entry in reversed(children):
            name = entry.name.lower()
            if name.startswith('.') or name in _SCAN_SKIP_DIRECTORIES:
                continue
            try:
                if entry.is_symlink() or getattr(entry, 'is_junction', lambda: False)():
                    continue
                if not entry.is_dir(follow_symlinks=False):
                    continue
            except OSError:
                continue
            stack.append((Path(entry.path), level + 1))
    return found, visited, truncated


def discover(roots: list[str], drives: list[Path] | None = None) -> dict:
    """Find ComfyUI inside declared roots and fixed drives with bounded depth.

    Declared roots (``SHINE_COMFY_PROBE_ROOTS``) are the person's explicit
    statement of where to look, so they are searched several levels deep; fixed
    drives are only a convenience fallback.  Links/junctions, network paths and
    system/cache directories are never followed, and the walk has a directory
    budget, so the result reports its own coverage instead of implying a full
    disk audit.
    """
    seeds: list[tuple[Path, int]] = [
        (local_path(r), CONFIGURED_ROOT_DEPTH) for r in roots
    ]
    seeds.extend((Path(drive), DRIVE_SCAN_DEPTH)
                 for drive in (fixed_drives() if drives is None else drives))
    found: set[str] = set()
    unreadable: list[str] = []
    scanned: list[str] = []
    visited = 0
    truncated = False
    deadline = time.monotonic() + SCAN_TIME_BUDGET_S
    for seed, depth in seeds:
        if time.monotonic() >= deadline:
            truncated = True
            break
        if not _is_local(seed):
            continue
        try:
            if not seed.is_dir():
                continue
        except OSError:
            unreadable.append(str(seed))
            continue
        scanned.append(str(seed))
        matches, used, hit_budget = _scan_for_comfy(seed, depth, unreadable, deadline)
        visited += used
        truncated = truncated or hit_budget
        found.update(str(path) for path in matches)
    return {
        'installations': sorted(found),
        'unreadable': unreadable,
        'coverage': 'BOUNDED_RECURSIVE_SCAN',
        'scan': {
            'configured_root_depth': CONFIGURED_ROOT_DEPTH,
            'drive_depth': DRIVE_SCAN_DEPTH,
            'directories_visited': visited,
            'directory_budget': SCAN_DIRECTORY_BUDGET,
            'time_budget_s': SCAN_TIME_BUDGET_S,
            'truncated': truncated,
            'seeds': scanned,
        },
        'runtime_verified': False,
    }


def _local_declared_dir(value: str, base: Path) -> Path | None:
    """Resolve a path written in ComfyUI's own extra_model_paths.yaml.

    Only local directories are accepted. Network/UNC paths, links, junctions
    and missing directories are ignored: reading weights over SMB must never
    become an implicit side effect of a read-only probe.
    """
    candidate = Path(os.path.expanduser(value))
    if not candidate.is_absolute():
        candidate = base / candidate
    if not _is_local(candidate):
        return None
    try:
        if candidate.is_symlink():
            return None
        resolved = candidate.resolve()
        if not _is_local(resolved) or resolved != candidate:
            return None
        return resolved if resolved.is_dir() else None
    except OSError:
        return None


def declared_model_family_roots(root: Path) -> dict[str, list[Path]]:
    """The model directories the installation declares, grouped by family.

    ComfyUI reads this file at startup; a read-only parse is evidence of which
    directory a *family* (``unet``, ``clip``, ``vae``...) really comes from.
    The same defensive rules as :func:`_declared_model_roots` apply: local
    directories only, links and network paths ignored.
    """
    config = root / 'extra_model_paths.yaml'
    families: dict[str, list[Path]] = {}
    try:
        if not config.is_file() or config.is_symlink():
            return families
        lines = config.read_text(encoding='utf-8', errors='replace').splitlines()
    except OSError:
        return families
    base = root
    for raw in lines:
        line = raw.split('#', 1)[0].strip()
        if not line or ':' not in line:
            continue
        key, _, value = line.partition(':')
        key = key.strip().lower()
        value = value.strip().strip('"\'')
        if key == 'is_default':
            continue
        if key == 'base_path':
            if value:
                base = _local_declared_dir(value, base) or base
            continue
        if not value:
            continue
        directory = _local_declared_dir(value, base)
        if directory and directory not in families.setdefault(key, []):
            families[key].append(directory)
    return families


def _declared_model_roots(root: Path) -> list[Path]:
    """Model directories the existing ComfyUI itself declares.

    People commonly keep weights outside the checkout and point ComfyUI at them
    with ``extra_model_paths.yaml``.  Those roots hold exactly the models the
    installation can actually load, so the read-only scan follows them instead
    of only looking at ``<root>/models``.
    """
    config = root / 'extra_model_paths.yaml'
    try:
        if not config.is_file():
            return []
        lines = config.read_text(encoding='utf-8', errors='replace').splitlines()
    except OSError:
        return []
    declared: list[Path] = []
    base = root
    for raw in lines:
        line = raw.split('#', 1)[0].strip()
        if not line or ':' not in line:
            continue
        key, _, value = line.partition(':')
        key = key.strip().lower()
        value = value.strip().strip('"\'')
        if key == 'is_default':
            continue
        if key == 'base_path':
            if value:
                base = _local_declared_dir(value, base) or base
            continue
        if not value:
            continue
        directory = _local_declared_dir(value, base)
        if directory and directory not in declared:
            declared.append(directory)
    return declared


def inspect(root_value: str, catalog: dict) -> dict:
    selected = local_path(root_value)
    root = comfy_root(selected)
    if root is None:
        raise HTTPException(422, '该目录不是 ComfyUI，请选择包含 main.py 的目录或整合包目录。')
    # Inventory model files only. Links/junctions are never followed; the
    # external roots ComfyUI itself declares in extra_model_paths.yaml are
    # included because that is where the installation actually loads models
    # from, which is exactly what the person downloaded.
    sizes: dict[int, list[tuple[Path, dict]]] = {}
    skipped = []
    seen_files: set[str] = set()
    model_roots: list[str] = []
    count = 0
    candidates = [root / 'models', *_declared_model_roots(root)]
    for models in candidates:
        if str(models) in model_roots:
            continue
        if models.resolve() != models:
            skipped.append(str(models))
            continue
        if not models.is_dir():
            continue
        model_roots.append(str(models))

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
                key = str(file)
                if key in seen_files:
                    continue
                seen_files.add(key)
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
               'skipped': skipped, 'scanned_files': count, 'model_roots': model_roots,
               'models_root': str(root / 'models'),
               'declared_model_family_roots': {
                   family: [str(directory) for directory in directories]
                   for family, directories in sorted(declared_model_family_roots(root).items())
               },
               'node_packages': node_package_inventory(root, catalog),
               'runtime_verified': False,
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


def register_verified_packages(db, verified: list[dict], catalog: dict,
                               root: str | None = None) -> list[str]:
    """Register the catalog packages whose every file was hash-verified in place.

    The existing-install reuse path historically only cached or verified bytes;
    formal pre-production also needs those identities in the package registry.
    Only components whose complete catalog file set is verified become
    FILES_VERIFIED_PENDING_RUNTIME. Nothing is copied, downloaded or promoted.
    """
    from shine.comfyops.deployment import _component_map, register_catalog_packages

    matched = {(row['component_id'], row['relative_path']) for row in verified}
    complete = []
    for component_id, component in _component_map(catalog).items():
        files = component.get('files') or []
        if files and all((component_id, item['relative_path']) in matched for item in files):
            complete.append(component_id)
    if not complete:
        return []
    rows = register_catalog_packages(db, catalog, complete)
    from pathlib import Path

    from shine.comfyops.verification_receipts import save_receipt

    components = _component_map(catalog)
    registered = []
    root_path = Path(root) if root else None
    allowed_roots = (
        [root_path, *[directory for dirs in declared_model_family_roots(root_path).values()
                      for directory in dirs]]
        if root_path is not None else []
    )
    for component_id, row in rows.items():
        if row.status == 'UNVERIFIED_ORIGIN':
            row.status = 'FILES_VERIFIED_PENDING_RUNTIME'
            registered.append(component_id)
        # An in-place verification proves the files at the selected existing
        # installation, not at the isolated-package directory.  Each verified
        # file's absolute path is recorded, so readiness re-checks exactly the
        # bytes that were hashed and cannot borrow this evidence for another
        # copy (J04).
        file_paths = {
            item['relative_path']: Path(item['source'])
            for item in verified
            if item.get('component_id') == component_id and item.get('source')
        }
        variant_block = catalog.get('variant')
        save_receipt(
            db,
            variant_id=(variant_block or {}).get('variant_id')
            if isinstance(variant_block, dict) else None,
            catalog_id=catalog.get('catalog_id'),
            catalog_version=catalog.get('catalog_version'),
            component=components[component_id],
            root=root_path,
            directory=root_path,
            file_paths=file_paths or None,
            allowed_roots=allowed_roots or None,
        )
    return registered


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
