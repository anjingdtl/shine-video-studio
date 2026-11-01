"""Host-local deployment location shared by the API and the separate worker.

Changing this preference does not move the database, assets, instances or model
files. Once deployment bytes exist, the old location requires reconciliation.
"""

from __future__ import annotations

import json
import os
import shutil
import tempfile
from pathlib import Path

from fastapi import HTTPException
from sqlalchemy import select, text

from shine.comfyops.installer import durable_json
from shine.comfyops.models import ComfyInstance, InstallationAttempt, InstallationPlan
from shine.services import add_audit


def preferences_path(data_dir):
    return data_dir / 'deployment-storage.json'


def preferences(data_dir):
    path = preferences_path(data_dir)
    if not path.exists():
        return {}
    value = json.loads(path.read_text(encoding='utf-8'))
    root = Path(value['root'])
    if value.get('version') != 1 or not root.is_absolute():
        raise ValueError('DEPLOYMENT_STORAGE_CONFIGURATION_INVALID')
    return value


def storage_root(data_dir):
    return Path(preferences(data_dir).get('root', data_dir / 'comfyops')).resolve()


def on_system_drive(path):
    return os.name == 'nt' and Path(path).drive.casefold() == os.getenv('SystemDrive', 'C:').casefold()


def disk_info(path):
    existing = Path(path)
    while not existing.exists() and existing != existing.parent:
        existing = existing.parent
    usage = shutil.disk_usage(existing)
    return {'free_bytes': usage.free, 'total_bytes': usage.total, 'free_gb': round(usage.free / 1024**3, 2)}


def process_environment(root):
    """Pinned local commands must not spill pip/model caches or temp files onto C:."""
    root = Path(root)
    return {'TEMP': str(root / '.runtime-tmp'), 'TMP': str(root / '.runtime-tmp'),
            'PIP_CACHE_DIR': str(root / '.runtime-cache/pip'),
            'HF_HOME': str(root / '.runtime-cache/huggingface'),
            'TORCH_HOME': str(root / '.runtime-cache/torch'),
            'XDG_CACHE_HOME': str(root / '.runtime-cache'),
            'TRITON_HOME': str(root / '.runtime-cache/triton-home'),
            'TRITON_CACHE_DIR': str(root / '.runtime-cache/triton'),
            'TORCHINDUCTOR_CACHE_DIR': str(root / '.runtime-cache/torchinductor'),
            'TORCH_EXTENSIONS_DIR': str(root / '.runtime-cache/torch-extensions'),
            'CUDA_CACHE_PATH': str(root / '.runtime-cache/cuda'),
            'NUMBA_CACHE_DIR': str(root / '.runtime-cache/numba'),
            'PYTHONPYCACHEPREFIX': str(root / '.runtime-cache/pycache'),
            'PIP_DISABLE_PIP_VERSION_CHECK': '1'}


def prepare_process_environment(root):
    patch = process_environment(root)
    for key, value in patch.items():
        if key != 'PIP_DISABLE_PIP_VERSION_CHECK':
            Path(value).mkdir(parents=True, exist_ok=True)
    return patch


def require_storage(settings):
    root = settings.comfyops_dir
    configured = preferences(settings.data_dir)
    if on_system_drive(root) and not configured.get('allow_system_drive', False):
        raise HTTPException(409, 'CHOOSE_DEPLOYMENT_DIRECTORY_OUTSIDE_SYSTEM_DRIVE')
    return root


def storage_report(settings):
    root = settings.comfyops_dir
    configured = preferences(settings.data_dir)
    drives = []
    if os.name == 'nt':
        for letter in 'ABCDEFGHIJKLMNOPQRSTUVWXYZ':
            drive = Path(letter + ':\\')
            if drive.exists():
                try:
                    drives.append({'path': str(drive), 'system_drive': on_system_drive(drive), **disk_info(drive)})
                except OSError:
                    continue
    blocked = on_system_drive(root) and not configured.get('allow_system_drive', False)
    return {'root': str(root), 'configured': bool(configured), 'system_drive': on_system_drive(root),
            'requires_selection': blocked, 'drives': drives, **disk_info(root),
            'paths': {name: str(root / relative) for name, relative in {
                'download_cache': 'isolated-packages/.cache', 'packages_and_models': 'isolated-packages',
                'comfyui_instances': 'instances', 'snapshots': 'instance-snapshots',
            }.items()}}


def configure_storage(db, settings, path, actor, *, allow_system_drive=False):
    candidate = Path(path)
    if not candidate.is_absolute() or (os.name == 'nt' and str(candidate).startswith('\\\\')):
        raise HTTPException(422, 'ABSOLUTE_LOCAL_DEPLOYMENT_DIRECTORY_REQUIRED')
    candidate = candidate.resolve()
    if candidate == candidate.parent or candidate == settings.data_dir.resolve():
        raise HTTPException(422, 'DEDICATED_DEPLOYMENT_DIRECTORY_REQUIRED')
    if on_system_drive(candidate) and not allow_system_drive:
        raise HTTPException(409, 'SYSTEM_DRIVE_REQUIRES_EXPLICIT_SELECTION')
    db.execute(text('BEGIN IMMEDIATE'))
    old = settings.comfyops_dir
    if old != candidate:
        if (any(row.payload.get('managed') for row in db.scalars(select(ComfyInstance)))
                or db.scalars(select(InstallationAttempt).where(
                    InstallationAttempt.status.in_(['SUBMIT_INTENT', 'INSTALLING', 'NEEDS_REVIEW',
                                                     'FAILED_RECONCILE_REQUIRED']))).first()
                or (old.exists() and any(old.iterdir()))):
            raise HTTPException(409, 'EXISTING_DEPLOYMENT_REQUIRES_RECONCILIATION_NO_FILES_MOVED')
        if candidate.exists() and (not candidate.is_dir() or any(candidate.iterdir())):
            raise HTTPException(409, 'CHOOSE_EMPTY_DEDICATED_DEPLOYMENT_DIRECTORY')
    try:
        candidate.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryFile(dir=candidate) as probe:
            probe.write(b'shine-storage-write-probe')
            probe.flush()
    except OSError as exc:
        raise HTTPException(409, 'DEPLOYMENT_DIRECTORY_NOT_WRITABLE') from exc
    cancelled = []
    if old != candidate:
        for plan in db.scalars(select(InstallationPlan).where(
                InstallationPlan.status.in_(['AWAITING_APPROVAL', 'APPROVED']))):
            plan.status = 'CANCELLED_STORAGE_CHANGED'
            cancelled.append(plan.id)
    durable_json(preferences_path(settings.data_dir), {
        'version': 1, 'root': str(candidate), 'allow_system_drive': allow_system_drive,
    })
    add_audit(db, actor, 'DEPLOYMENT_STORAGE_CONFIGURED', 'system', 'deployment-storage',
              {'old_root': str(old), 'root': str(candidate), 'cancelled_plans': cancelled,
               'files_moved': False, 'allow_system_drive': allow_system_drive})
    db.commit()
    return {**storage_report(settings), 'cancelled_plans': cancelled, 'files_moved': False}
