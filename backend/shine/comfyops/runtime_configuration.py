"""Operator activation shared by the API and resident Worker; no second queue."""
from __future__ import annotations

import hashlib
import json
import os
from dataclasses import replace
from pathlib import Path

from fastapi import HTTPException
from sqlalchemy import or_, select, text

from shine.comfy.workflow import graph_hash
from shine.comfyops.managed_recipe import load_managed_recipe
from shine.comfyops.models import ComfyInstance, InstallationAttempt, ResourceLease
from shine.models import AgentRoute, AppSetting, Job, ProductionSeries, ProviderProfile, WorkflowProfile
from shine.services import add_audit, require_project

CANDIDATE_KEY = 'deployment-runtime-candidate'
ACTIVE_KEY = 'deployment-runtime-active'
SERVICE_EVIDENCE_KEY = 'deployment-service-validation'

#: The series states that can still hold the GPU or be resumed by a Worker.
#: NEEDS_REVIEW is deliberately absent: it is a blocked, unclaimable record.
LIVE_SERIES_STATUSES = ('QUEUED', 'CLAIMED', 'RUNNING', 'PAUSED', 'PAUSE_REQUESTED', 'RECONCILING')


def workflow_contract_sha256(profile: WorkflowProfile) -> str:
    """Identity of everything a live node contract was checked against."""
    return graph_hash({
        'graph': profile.graph,
        'parameter_map': profile.parameter_map,
        'media_transfers': profile.media_transfers,
        'required_nodes': profile.required_nodes,
        'mode': profile.mode,
        'checkpoint_id': profile.checkpoint_id,
    })


def _current_identity(db, settings):
    """The identity the setup wizard is working on, active first then candidate."""
    active = db.get(AppSetting, ACTIVE_KEY)
    if active:
        try:
            return configuration_identity(db, settings, active.value['identity']['recipe'])
        except (HTTPException, KeyError, TypeError) as exc:
            raise HTTPException(409, 'ACTIVE_RUNTIME_CONFIGURATION_CHANGED') from exc
    candidate = db.get(AppSetting, CANDIDATE_KEY)
    if candidate:
        try:
            return configuration_identity(db, settings, candidate.value['identity']['recipe'])
        except (HTTPException, KeyError, TypeError) as exc:
            raise HTTPException(409, 'RUNTIME_CONFIGURATION_CHANGED_REGENERATE') from exc
    raise HTTPException(409, 'GENERATE_RUNTIME_CONFIGURATION_FIRST')


def _evidence_seal(payload: dict) -> str:
    return graph_hash({key: value for key, value in payload.items() if key != 'sha256'})


def record_service_evidence(db, identity, evidence: dict) -> dict:
    """Persist one sealed live service/node validation for the current identity."""
    payload = {
        'version': 1,
        'configuration_sha256': graph_hash(identity),
        'recipe_sha256': identity['recipe_sha256'],
        'instance_id': identity['instance_id'],
        'instance_sha256': identity['instance_sha256'],
        **evidence,
    }
    payload['sha256'] = _evidence_seal(payload)
    row = db.get(AppSetting, SERVICE_EVIDENCE_KEY)
    if row is None:
        db.add(AppSetting(key=SERVICE_EVIDENCE_KEY, value=payload))
    else:
        row.value = payload
    return payload


def service_evidence_status(db, settings, *, identity=None) -> dict:
    """Return readiness derived from evidence, never from a hard-coded flag.

    Core readiness depends on the complete H3 configuration and workflow
    contracts. Reviewer routes are reported separately because they are
    optional suggestions and must not gate production.
    """
    try:
        identity = identity or _current_identity(db, settings)
    except HTTPException as exc:
        return {'status': 'PENDING', 'blockers': [str(exc.detail)], 'validated_at': None}
    row = db.get(AppSetting, SERVICE_EVIDENCE_KEY)
    if row is None or not isinstance(row.value, dict):
        return {'status': 'PENDING', 'blockers': ['SERVICE_VALIDATION_REQUIRED'], 'validated_at': None}
    evidence = dict(row.value)
    blockers = []
    if evidence.get('sha256') != _evidence_seal(evidence):
        blockers.append('SERVICE_VALIDATION_TAMPERED')
    if evidence.get('configuration_sha256') != graph_hash(identity):
        blockers.append('SERVICE_VALIDATION_STALE_CONFIGURATION')
    if evidence.get('recipe_sha256') != identity.get('recipe_sha256'):
        blockers.append('SERVICE_VALIDATION_STALE_RECIPE')
    for name, expected in (evidence.get('workflow_profiles') or {}).items():
        profile = db.get(WorkflowProfile, expected.get('id'))
        if (profile is None or profile.name != name
                or workflow_contract_sha256(profile) != expected.get('contract_sha256')):
            blockers.append(f'WORKFLOW_CONTRACT_CHANGED:{name}')
    advisory_reviews = dict(evidence.get('advisory_reviews') or {})
    for role, expected in (evidence.get('review_profiles') or {}).items():
        route_role = {'VLM': 'vlm_reviewer', 'ASR': 'asr_reviewer'}.get(role, role)
        profile = db.get(ProviderProfile, expected.get('id'))
        route = db.scalar(select(AgentRoute).where(AgentRoute.role == route_role))
        if (profile is None or route is None or route.provider_profile_id != profile.id
                or profile.model != expected.get('model') or profile.base_url != expected.get('base_url')
                or graph_hash(profile.capabilities or {}) != expected.get('capabilities_sha256')):
            advisory_reviews[role] = {
                'status': 'UNAVAILABLE', 'advisory': True,
                'codes': ['REVIEW_ROUTE_CHANGED'],
            }
    if blockers:
        return {'status': 'PENDING', 'blockers': sorted(set(blockers)),
                'validated_at': evidence.get('validated_at')}
    return {
        'status': 'READY',
        'blockers': [],
        'validated_at': evidence.get('validated_at'),
        'node_count': evidence.get('node_count'),
        'node_inventory_sha256': evidence.get('node_inventory_sha256'),
        'workflow_profiles': sorted((evidence.get('workflow_profiles') or {}).keys()),
        'review_models': {role: item.get('model') for role, item in
                          (evidence.get('review_profiles') or {}).items()},
        'advisory_reviews': advisory_reviews,
    }


def configuration_identity(db, settings, path):
    from shine.comfyops.deployment import load_default_catalog
    from shine.comfyops.instances import instance_identity, instance_root

    path = Path(path).resolve()
    if path != (settings.comfyops_dir / 'setup/managed_recipe.json').resolve():
        raise HTTPException(409, 'GENERATED_RUNTIME_RECIPE_REQUIRED')
    try:
        raw = json.loads(path.read_text(encoding='utf-8'))
        recipe = load_managed_recipe(path, expected_sha256=graph_hash(raw))
        renderer = recipe['renderer']
        instance = db.get(ComfyInstance, renderer['instance_id'])
        adopted = bool(instance and instance.payload.get('adopted'))
        expected_root = (Path(instance.payload.get('root', '')).resolve() if adopted
                         else instance_root(settings, instance.payload['name'], 'local').resolve()
                         if instance else None)
        if (not instance or not instance.payload.get('managed') or instance.payload.get('is_mock', True)
                or instance.sha256 != graph_hash(instance.payload)
                or instance.id != instance_identity('local', renderer['port'])
                or Path(renderer['root']).resolve() != expected_root
                or Path(instance.payload['root']).resolve() != Path(renderer['root']).resolve()
                or instance.payload['port'] != renderer['port']):
            raise HTTPException(409, 'REAL_RUNTIME_INSTANCE_IDENTITY_REQUIRED')
        if adopted:
            from shine.comfyops.adoption import adopted_installation_blockers
            from shine.comfyops.deployment import load_default_catalog

            if adopted_installation_blockers(instance, load_default_catalog()):
                raise HTTPException(409, 'ADOPTED_COMFY_INSTALLATION_CHANGED')
            runtime_root = Path(renderer['runtime_data_root']).resolve()
            if not runtime_root.is_relative_to(settings.comfyops_dir.resolve()):
                raise HTTPException(409, 'ADOPTED_RUNTIME_DATA_ROOT_INVALID')
        if renderer.get('installation') != {'id': instance.id, 'sha256': instance.sha256}:
            raise HTTPException(409, 'RUNTIME_INSTALLATION_BINDING_REQUIRED')
        paths = {path}
        if not adopted:
            paths.add(Path(renderer['root']) / '.shine-requirements.txt')
        values = {**renderer, 'port': str(renderer['port'])}
        for template in renderer['pinned_files']:
            paths.add(Path(template.format_map(values)))
        for review in recipe['reviews'].values():
            paths.add(Path(review['manifest']))
            # The controlled review entrypoint is part of the confirmed identity.
            paths.add(Path(review['argv'][1].format_map(review)))
        pins = {}
        for target in sorted(paths):
            if not target.is_absolute() or not target.is_file():
                raise HTTPException(409, 'RUNTIME_PINNED_FILE_MISSING')
            with target.open('rb') as stream:
                pins[str(target.resolve())] = hashlib.file_digest(stream, 'sha256').hexdigest()
        return {'recipe': str(path), 'recipe_sha256': graph_hash(raw),
                'instance_id': instance.id, 'instance_sha256': instance.sha256,
                'catalog_sha256': graph_hash(load_default_catalog()), 'files': pins,
                'comfyui_base_url': f"http://127.0.0.1:{renderer['port']}"}
    except (OSError, ValueError, KeyError, IndexError, TypeError) as exc:
        raise HTTPException(409, 'RUNTIME_CONFIGURATION_INVALID') from exc


def record_candidate(db, settings, project_id, recipe_path):
    identity = configuration_identity(db, settings, recipe_path)
    value = {'version': 1, 'project_id': project_id, 'identity': identity}
    row = db.get(AppSetting, CANDIDATE_KEY)
    if row is None:
        db.add(AppSetting(key=CANDIDATE_KEY, value=value))
    else:
        row.value = value


def resolve_runtime(db, settings):
    row = db.get(AppSetting, ACTIVE_KEY)
    if row is None:
        return settings
    if settings.offline_simulation:
        raise HTTPException(409, 'ACTIVE_RUNTIME_OFFLINE_SIMULATION_FORBIDDEN')
    try:
        value = row.value
        identity = value['identity']
        if (value['version'] != 1 or graph_hash(identity) != value['sha256']
                or configuration_identity(db, settings, identity['recipe']) != identity):
            raise HTTPException(409, 'ACTIVE_RUNTIME_CONFIGURATION_CHANGED')
    except HTTPException as exc:
        raise HTTPException(409, 'ACTIVE_RUNTIME_CONFIGURATION_CHANGED') from exc
    except (KeyError, TypeError) as exc:
        raise HTTPException(409, 'ACTIVE_RUNTIME_CONFIGURATION_INVALID') from exc
    return replace(settings, render_provider='comfyui', allow_remote_comfyui=False,
                   comfyui_base_url=identity['comfyui_base_url'],
                   managed_recipe_path=identity['recipe'], managed_recipe_sha256=identity['recipe_sha256'])


def require_idle_configuration(db):
    from shine.services import ACTIVE_JOB_STATUSES

    # Only work that can still touch the GPU blocks a re-validation. A
    # NEEDS_REVIEW record with no active lease is an audit trail, not live work.
    #
    # The series check therefore asks which series are *live*, not which ones
    # are not finished: a Worker only claims QUEUED/RUNNING series, and
    # PAUSED/PAUSE_REQUESTED/RECONCILING are the in-flight states around them.
    # A NEEDS_REVIEW series is blocked and unclaimable - it holds no lease and
    # cannot render - so an unrelated project's blocked series must not freeze
    # the whole workbench.
    if (db.scalar(select(ResourceLease.id).where(ResourceLease.status.in_(['ACTIVE', 'RECONCILE_REQUIRED'])).limit(1))
            or db.scalar(select(Job.id).where(
                Job.status.in_(list(ACTIVE_JOB_STATUSES)), Job.stale.is_(False)).limit(1))
            or db.scalar(select(ProductionSeries.id).where(
                ProductionSeries.status.in_(LIVE_SERIES_STATUSES)).limit(1))
            or db.scalar(select(InstallationAttempt.id).where(or_(
                InstallationAttempt.status.in_(['SUBMIT_INTENT', 'INSTALLING', 'FAILED_RECONCILE_REQUIRED']),
                (InstallationAttempt.status == 'NEEDS_REVIEW') & InstallationAttempt.lease_owner.is_not(None),
            )).limit(1))):
        raise HTTPException(409, 'RUNTIME_CONFIGURATION_REQUIRES_IDLE_AND_RECONCILED_WORK')


def activate_runtime(db, settings, project_id, recipe_sha256, actor):
    from shine.comfyops.deployment import installed_instance_blockers, load_default_catalog

    db.execute(text('BEGIN IMMEDIATE'))
    require_project(db, project_id)
    candidate = db.get(AppSetting, CANDIDATE_KEY)
    if not candidate or candidate.value.get('project_id') != project_id:
        raise HTTPException(409, 'GENERATE_RUNTIME_CONFIGURATION_FIRST')
    identity = candidate.value['identity']
    if recipe_sha256 != identity['recipe_sha256']:
        raise HTTPException(409, 'RUNTIME_CONFIRMATION_HASH_CHANGED')
    if configuration_identity(db, settings, identity['recipe']) != identity:
        raise HTTPException(409, 'RUNTIME_CONFIGURATION_CHANGED_REGENERATE')
    if settings.offline_simulation:
        raise HTTPException(409, 'OFFLINE_SIMULATION_CANNOT_ACTIVATE_REAL_RUNTIME')
    active = db.get(AppSetting, ACTIVE_KEY)
    if (active and active.value.get('identity') == identity
            and active.value.get('sha256') == graph_hash(identity)):
        return runtime_report(db, settings)
    instance = db.get(ComfyInstance, identity['instance_id'])
    blockers = installed_instance_blockers(db, settings, instance, load_default_catalog())
    if blockers:
        raise HTTPException(409, {'code': 'REAL_INSTALLATION_EVIDENCE_REQUIRED', 'blockers': blockers})
    if instance.status != 'STOPPED' or instance.payload.get('process'):
        raise HTTPException(409, 'STOP_OWNED_INSTANCE_BEFORE_ACTIVATION')
    require_idle_configuration(db)
    value = {'version': 1, 'identity': identity, 'sha256': graph_hash(identity),
             'project_id': project_id, 'actor': actor}
    if active is None:
        db.add(AppSetting(key=ACTIVE_KEY, value=value))
    else:
        active.value = value
    add_audit(db, actor, 'DEPLOYMENT_RUNTIME_ACTIVATED', 'comfy_instance', instance.id,
              {'configuration_sha256': value['sha256'], 'recipe_sha256': recipe_sha256,
               'starts_processes': False}, project_id=project_id)
    # Activation is the operator's statement that this host now renders for real.
    # Persist the renderer choice so a later START_APP.cmd reuses it instead of
    # silently dropping back to Mock after a reboot.
    from shine.comfyops.deployment import ENV_PATH, persist_env
    runtime_env_override = os.getenv('SHINE_RUNTIME_ENV_PATH')
    runtime_env_path = Path(runtime_env_override).resolve() if runtime_env_override else ENV_PATH
    if runtime_env_override and not runtime_env_path.is_relative_to(settings.data_dir.resolve()):
        raise HTTPException(409, 'RUNTIME_SETTINGS_PATH_OUTSIDE_DATA_DIR')
    env_keys = persist_env({'SHINE_RENDER_PROVIDER': 'comfyui'}, runtime_env_path)
    add_audit(db, actor, 'DEPLOYMENT_RENDER_PROVIDER_PERSISTED', 'comfy_instance', instance.id,
              {'env_keys': env_keys, 'value': 'comfyui'}, project_id=project_id)
    db.commit()
    return runtime_report(db, settings)


def runtime_report(db, settings):
    candidate = db.get(AppSetting, CANDIDATE_KEY)
    active = db.get(AppSetting, ACTIVE_KEY)
    try:
        effective = resolve_runtime(db, settings)
        status, error = ('ACTIVE' if active else 'NOT_ACTIVATED'), None
        mode = effective.render_provider
    except HTTPException as exc:
        status, error, mode = 'INVALID_REQUIRES_REVIEW', exc.detail, 'unavailable'
    validation = None
    if active or candidate:
        validation = service_evidence_status(db, settings)
    return {'status': status, 'render_provider': mode, 'error': error,
            'candidate': ({'project_id': candidate.value['project_id'],
                           'recipe_sha256': candidate.value['identity']['recipe_sha256']} if candidate else None),
            'configuration_sha256': active.value.get('sha256') if active else None,
            'services_verified': bool(active and validation and validation.get('status') == 'READY'),
            'service_validation': validation,
            'real_inference_verified': False}
