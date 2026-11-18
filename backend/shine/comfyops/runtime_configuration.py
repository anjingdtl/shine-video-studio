"""Operator activation shared by the API and resident Worker; no second queue."""
from __future__ import annotations

import hashlib
import json
from dataclasses import replace
from pathlib import Path

from fastapi import HTTPException
from sqlalchemy import or_, select, text

from shine.comfy.workflow import graph_hash
from shine.comfyops.managed_recipe import load_managed_recipe, recipe_path
from shine.comfyops.models import ComfyInstance, InstallationAttempt, ResourceLease
from shine.models import AgentRoute, AppSetting, Job, ProductionSeries, ProviderProfile, WorkflowProfile
from shine.services import add_audit, require_project

CANDIDATE_KEY = 'deployment-runtime-candidate'
ACTIVE_KEY = 'deployment-runtime-active'
SERVICE_EVIDENCE_KEY = 'deployment-service-validation'

#: Stat-keyed cache of pinned-file SHA-256 (resolved path + mtime_ns + size).
#: configuration_identity is read on every setup/status projection and used to
#: re-hash every pinned file each time. A file whose bytes change gets a new
#: mtime/size and is hashed again, so accidental configuration drift is still
#: detected; only unchanged files reuse their digest.
_PIN_HASH_CACHE: dict[tuple[str, int, int], str] = {}


def _pinned_file_digest(target: Path) -> str:
    stat = target.stat()
    key = (str(target.resolve()), stat.st_mtime_ns, stat.st_size)
    hit = _PIN_HASH_CACHE.get(key)
    if hit is not None:
        return hit
    with target.open('rb') as stream:
        digest = hashlib.file_digest(stream, 'sha256').hexdigest()
    if len(_PIN_HASH_CACHE) >= 256:
        _PIN_HASH_CACHE.clear()
    _PIN_HASH_CACHE[key] = digest
    return digest

#: The fields that describe the machine, not the directory the bookkeeping
#: lives in.  A relocation must leave all of them untouched.
_CONFIGURATION_FACTS = ('instance_id', 'instance_sha256', 'catalog_sha256',
                        'recipe_sha256', 'comfyui_base_url')


def configuration_fingerprint(identity: dict) -> str:
    """What a sealed service validation is about: the machine, not the notes.

    The recorded identity lists the absolute path of every pinned file, which is
    what got hashed - but a copied data directory or a new checkout changes
    those paths without touching one byte of the environment.  The fingerprint
    therefore covers only the facts that describe the machine.  ``instance_sha256``
    already commits to the installation payload, ``catalog_sha256`` to the
    resource catalog and ``recipe_sha256`` to the recipe's own bytes, so nothing
    that decides what will render is dropped.
    """
    return graph_hash({field: identity.get(field) for field in _CONFIGURATION_FACTS})


def _recorded_roots(recipe: dict, settings) -> list[tuple[Path, Path]]:
    """Where the recipe's own paths were recorded, and where they live now.

    A recipe is byte-identical after a data directory or worktree is copied, but
    the absolute paths inside it still name the old place.  Two roots can move
    this way: the data directory's ``comfyops`` tree and the repository that
    ships the renderer bootstrap / review entrypoint.
    """
    from shine.comfyops.deployment import REPO_ROOT

    roots: list[tuple[Path, Path]] = []
    renderer = recipe.get('renderer') if isinstance(recipe, dict) else None
    if not isinstance(renderer, dict):
        return roots
    runtime_root = Path(str(renderer.get('runtime_data_root') or renderer.get('root') or ''))
    # <data>/comfyops/adopted-runtime/<id> → <data>/comfyops
    if runtime_root.is_absolute() and len(runtime_root.parents) >= 2:
        old_comfyops = runtime_root.parent.parent
        if old_comfyops.name == 'comfyops':
            try:
                if old_comfyops.resolve() != settings.comfyops_dir.resolve():
                    roots.append((old_comfyops, settings.comfyops_dir))
            except OSError:
                pass
    bootstrap = Path(str(renderer.get('bootstrap') or ''))
    if bootstrap.is_absolute() and bootstrap.parent.name == 'scripts':
        old_repo = bootstrap.parent.parent
        try:
            if old_repo.resolve() != REPO_ROOT.resolve():
                roots.append((old_repo, REPO_ROOT))
        except OSError:
            pass
    return roots


def _anchored(value: str, roots: list[tuple[Path, Path]]) -> str:
    """The same file, found where *this* data directory keeps it.

    The current location wins whenever it holds the file: a recipe that was
    recorded in another directory describes that directory's bookkeeping, not
    the one this process serves.  The recorded path is the fallback for a file
    that only exists where it was recorded (a partial copy, or a root that has
    not moved at all).
    """
    path = Path(str(value))
    if not path.is_absolute():
        return str(value)
    for old, new in roots:
        try:
            if path.is_relative_to(old):
                moved = new / path.relative_to(old)
                if moved.exists():
                    return str(moved)
        except OSError:
            continue
    return str(value)


def load_runtime_recipe(settings, *, expected_sha256=None, path=None):
    """Read the managed recipe where this data directory keeps it now.

    Returns ``(recipe, sha256_of_the_bytes_on_disk)`` so a caller that records
    an identity hashes exactly what it validated.  The bytes are never rewritten:
    a moved directory is recognised through the digest the operator activated,
    and only the paths the renderer is *handed* follow the move.
    """
    target = Path(path) if path is not None else recipe_path(settings)
    try:
        raw = json.loads(Path(target).read_text(encoding='utf-8'))
    except (OSError, ValueError) as exc:
        raise HTTPException(409, 'RUNTIME_CONFIGURATION_INVALID') from exc
    roots = _recorded_roots(raw, settings)
    recipe = load_managed_recipe(
        target, expected_sha256=expected_sha256 or graph_hash(raw),
        anchor=lambda value: _anchored(value, roots))
    return recipe, graph_hash(raw)


def _workbench_pin_names(settings) -> set[str]:
    """Pinned files that ship with the workbench, not with the install.

    Two pinned files are this repository's own code: the bootstrap that starts
    the renderer and the review entrypoint.  They change whenever the product is
    upgraded, and an upgrade must not invalidate an environment the operator
    already confirmed - the new code is simply what runs next.  Everything else
    the recipe pins (the interpreter, the ComfyUI tree, the adapter bytes, the
    sampling node, the review manifests) is what the operator installed, and
    those still have to hash exactly as recorded.
    """
    try:
        recipe = json.loads(recipe_path(settings).read_text(encoding='utf-8'))
    except (OSError, ValueError):
        return set()
    names: set[str] = set()
    renderer = recipe.get('renderer')
    if isinstance(renderer, dict) and renderer.get('bootstrap'):
        names.add(Path(str(renderer['bootstrap'])).name)
    for spec in (recipe.get('reviews') or {}).values():
        argv = (spec or {}).get('argv') or []
        if len(argv) > 1:
            names.add(Path(str(argv[1])).name)
    return {name for name in names if name}


def _production_digests(identity: dict, workbench: set[str]) -> list[str]:
    return sorted(digest for path, digest in (identity.get('files') or {}).items()
                  if Path(str(path)).name not in workbench)


def _same_configuration(stored: dict, current: dict, *, workbench: set[str]) -> bool:
    """The same confirmed machine, recorded under other bookkeeping paths.

    Compare what the machine will render with, not where the notes live:
    every installed pinned file must still hash to the digest the identity
    recorded, and the instance, catalog, recipe and renderer endpoint must be
    unchanged.  A different data directory, a new worktree or an upgraded
    bootstrap all describe the same machine and stay ready.
    """
    if any(stored.get(field) != current.get(field) for field in _CONFIGURATION_FACTS):
        return False
    stored_files = _production_digests(stored, workbench)
    current_files = _production_digests(current, workbench)
    return bool(stored_files) and stored_files == current_files


def same_configuration(stored: dict, current: dict, *, settings) -> bool:
    """Exact match, or the same production configuration recorded elsewhere."""
    return stored == current or _same_configuration(
        stored, current, workbench=_workbench_pin_names(settings))


#: Candidates for the shared in-flight decision. A queued or paused series
#: without active work keeps its place while the runtime is being checked.
LIVE_SERIES_STATUSES = ('QUEUED', 'CLAIMED', 'RUNNING', 'PAUSED', 'PAUSE_REQUESTED',
                       'CANCEL_REQUESTED', 'RECONCILING')


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


def _recorded_identity(db, settings, key: str, changed_code: str):
    """The identity recorded under one key, re-read in this data directory.

    The recorded identity is returned when it still describes this machine -
    even if the data directory moved - so the service-validation seal (which is
    keyed by that identity) keeps meaning what it says.  A genuine change is
    refused with the caller's own code.
    """
    row = db.get(AppSetting, key)
    if row is None:
        return None
    if key == ACTIVE_KEY and settings.offline_simulation:
        raise HTTPException(409, 'ACTIVE_RUNTIME_OFFLINE_SIMULATION_FORBIDDEN')
    try:
        stored = row.value['identity']
        if not isinstance(stored, dict):
            raise TypeError('runtime identity must be a mapping')
        if key == ACTIVE_KEY and (row.value['version'] != 1 or graph_hash(stored) != row.value['sha256']):
            raise HTTPException(409, changed_code)
    except (KeyError, TypeError) as exc:
        code = 'ACTIVE_RUNTIME_CONFIGURATION_INVALID' if key == ACTIVE_KEY else changed_code
        raise HTTPException(409, code) from exc
    try:
        current = configuration_identity(db, settings, recipe_path(settings))
    except (HTTPException, KeyError, TypeError) as exc:
        raise HTTPException(409, changed_code) from exc
    if not same_configuration(stored, current, settings=settings):
        raise HTTPException(409, changed_code)
    return stored


def _current_identity(db, settings):
    """The identity the setup wizard is working on, active first then candidate."""
    active = _recorded_identity(db, settings, ACTIVE_KEY, 'ACTIVE_RUNTIME_CONFIGURATION_CHANGED')
    if active is not None:
        return active
    candidate = _recorded_identity(db, settings, CANDIDATE_KEY, 'RUNTIME_CONFIGURATION_CHANGED_REGENERATE')
    if candidate is not None:
        return candidate
    raise HTTPException(409, 'GENERATE_RUNTIME_CONFIGURATION_FIRST')


def _evidence_seal(payload: dict) -> str:
    return graph_hash({key: value for key, value in payload.items() if key != 'sha256'})


def record_service_evidence(db, identity, evidence: dict) -> dict:
    """Persist one sealed live service/node validation for the current identity.

    ``version: 2`` seals the mode-level capability contract: the core node
    classes the baseline Ref2VA mode needs, plus the node inventory digest
    they were checked against.  The per-template contract hashes are kept as
    an identity record of what was validated, but a later drift of one
    historical template is advisory - it never blocks the whole mode.
    """
    payload = {
        'version': 2,
        'configuration_sha256': configuration_fingerprint(identity),
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


def _template_drift_advisories(db, evidence: dict) -> list[dict]:
    """Report drifted historical templates as advisories, never as blockers.

    The v1 seal treated any workflow-contract drift as a machine-wide block,
    which let one historical template lock the entire Ref2VA mode.  The same
    drift is now a re-validation hint: the audited repository template is the
    source of truth and the operator can re-register at any time.  This
    function never rewrites the stored hashes - it only reads them.
    """
    advisories = []
    for name, expected in (evidence.get('workflow_profiles') or {}).items():
        if expected.get('id') is None:
            continue
        profile = db.get(WorkflowProfile, expected.get('id'))
        if (profile is None or profile.name != name
                or workflow_contract_sha256(profile) != expected.get('contract_sha256')):
            advisories.append({
                'code': 'WORKFLOW_TEMPLATE_DRIFTED',
                'template': name,
                'advisory': True,
            })
    return advisories


def service_evidence_status(db, settings, *, identity=None) -> dict:
    """Return readiness derived from evidence, never from a hard-coded flag.

    Core readiness depends on the sealed H3 configuration and the mode-level
    capability contract (v2) or the legacy per-template seal (v1, upgraded to
    advisory drift handling). Reviewer routes are reported separately because
    they are optional suggestions and must not gate production.
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
    # Seals written before the fingerprint existed committed to the identity as
    # a whole, including the bookkeeping paths.  Both readings describe the same
    # machine, so an old seal stays valid instead of demanding a new setup.
    sealed_configurations = {configuration_fingerprint(identity), graph_hash(identity)}
    if evidence.get('configuration_sha256') not in sealed_configurations:
        blockers.append('SERVICE_VALIDATION_STALE_CONFIGURATION')
    if evidence.get('recipe_sha256') != identity.get('recipe_sha256'):
        blockers.append('SERVICE_VALIDATION_STALE_RECIPE')
    template_advisories = _template_drift_advisories(db, evidence)
    version = evidence.get('version')
    if version != 1:
        # v2+ seals the mode capability set; a change in the audited core
        # node classes means the seal was computed against another recipe
        # generation and must be re-derived, not trusted.
        from shine.comfyops.mode_capabilities import sealed_mode_nodes_compatible
        sealed = (evidence.get('mode_capabilities') or {}).get('required_nodes') or []
        if not sealed_mode_nodes_compatible(sealed):
            blockers.append('MODE_CAPABILITY_CONTRACT_CHANGED')
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
                'validated_at': evidence.get('validated_at'),
                'template_advisories': template_advisories}
    return {
        'status': 'READY',
        'blockers': [],
        'validated_at': evidence.get('validated_at'),
        'node_count': evidence.get('node_count'),
        'node_inventory_sha256': evidence.get('node_inventory_sha256'),
        'workflow_profiles': sorted((evidence.get('workflow_profiles') or {}).keys()),
        'mode_capabilities': evidence.get('mode_capabilities'),
        'template_advisories': template_advisories,
        'review_models': {role: item.get('model') for role, item in
                          (evidence.get('review_profiles') or {}).items()},
        'advisory_reviews': advisory_reviews,
    }


def configuration_identity(db, settings, path):
    from shine.comfyops.deployment import load_default_catalog
    from shine.comfyops.instances import (
        installation_binding_matches,
        installation_identity_matches,
        instance_root,
        relocated_managed_root,
    )

    path = Path(path).resolve()
    if path != recipe_path(settings).resolve():
        raise HTTPException(409, 'GENERATED_RUNTIME_RECIPE_REQUIRED')
    try:
        # The recipe this data directory serves, with the paths inside it
        # resolved to where they live now; the digest stays the recorded bytes.
        recipe, recipe_sha256 = load_runtime_recipe(settings)
        renderer = recipe['renderer']
        instance = db.get(ComfyInstance, renderer['instance_id'])
        adopted = bool(instance and instance.payload.get('adopted'))
        expected_root = (Path(instance.payload.get('root', '')).resolve() if adopted
                         else instance_root(settings, instance.payload['name'], 'local').resolve()
                         if instance else None)
        recorded_root = Path(instance.payload.get('root', '')) if instance else Path()
        relocated_managed = (
            bool(instance)
            and expected_root is not None
            and relocated_managed_root(instance, expected_root)
            and Path(renderer['root']).resolve() == expected_root
        )
        if (not instance or not instance.payload.get('managed') or instance.payload.get('is_mock', True)
                or instance.sha256 != graph_hash(instance.payload)
                or not installation_identity_matches(instance, renderer['port'])
                or Path(renderer['root']).resolve() != expected_root
                or (not relocated_managed and recorded_root.resolve() != Path(renderer['root']).resolve())
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
        inst_ref = renderer.get('installation')
        if (not isinstance(inst_ref, dict) or inst_ref.get('id') != instance.id
                or not installation_binding_matches(db, instance, inst_ref.get('sha256'))):
            raise HTTPException(409, 'RUNTIME_INSTALLATION_BINDING_REQUIRED')
        paths = {path}
        if not adopted:
            paths.add(Path(renderer['root']) / '.shine-requirements.txt')
        values = {**renderer, 'port': str(renderer['port'])}
        for template in renderer['pinned_files']:
            paths.add(Path(template.format_map(values)))
        for review in recipe['reviews'].values():
            paths.add(Path(str(review['manifest'])))
            # The controlled review entrypoint is part of the confirmed identity.
            paths.add(Path(review['argv'][1].format_map(review)))
        pins = {}
        for target in sorted(paths):
            if not target.is_absolute() or not target.is_file():
                raise HTTPException(409, 'RUNTIME_PINNED_FILE_MISSING')
            pins[str(target.resolve())] = _pinned_file_digest(target)
        return {'recipe': str(path), 'recipe_sha256': recipe_sha256,
                'instance_id': instance.id, 'instance_sha256': inst_ref['sha256'],
                'catalog_sha256': graph_hash(load_default_catalog()), 'files': pins,
                'comfyui_base_url': f"http://127.0.0.1:{renderer['port']}"}
    except (OSError, ValueError, KeyError, IndexError, TypeError) as exc:
        raise HTTPException(409, 'RUNTIME_CONFIGURATION_INVALID') from exc


def record_candidate(db, settings, project_id, path, *, variant_id=None):
    identity = configuration_identity(db, settings, path)
    # R5 B2：变体身份记在 value 顶层（不进 identity、不改配方字节），旧记录
    # 缺省按默认基线读。整备链据此判断「沿用现有配置」是否真的同一套方案。
    value = {'version': 1, 'project_id': project_id, 'identity': identity, 'variant_id': variant_id}
    row = db.get(AppSetting, CANDIDATE_KEY)
    if row is None:
        db.add(AppSetting(key=CANDIDATE_KEY, value=value))
    else:
        row.value = value


def resolve_runtime(db, settings):
    identity = _recorded_identity(db, settings, ACTIVE_KEY, 'ACTIVE_RUNTIME_CONFIGURATION_CHANGED')
    if identity is None:
        return settings
    # The bytes are the ones the operator activated; only the directory that
    # holds the recipe moved.  Hand the renderer the recipe that exists *now*.
    return replace(settings, render_provider='comfyui', allow_remote_comfyui=False,
                   comfyui_base_url=identity['comfyui_base_url'],
                   managed_recipe_path=str(recipe_path(settings)), managed_recipe_sha256=identity['recipe_sha256'])


def idle_configuration_blockers(db) -> list[dict]:
    """Single authoritative verdict of what currently blocks runtime configuration.

    Every surface (``require_idle_configuration``, setup preparation step details,
    the work-stack monitor, and Shine Copilot diagnostics/reports) reads this one
    list so no screen can claim the machine is idle while configuration refuses.
    """
    from shine.comfyops.models import InstallationPlan
    from shine.models import Project, Shot
    from shine.production.runner import _series_has_inflight_work
    from shine.runtime_orchestrator.identity import IN_FLIGHT_JOB_STATUSES

    def _project_title(project_id: str | None) -> str | None:
        if not project_id:
            return None
        row = db.get(Project, project_id)
        return row.title if row is not None else None

    blockers: list[dict] = []
    for lease in db.scalars(
        select(ResourceLease).where(ResourceLease.status.in_(['ACTIVE', 'RECONCILE_REQUIRED'])).limit(8)
    ).all():
        blockers.append({
            'kind': 'gpu_lease',
            'id': lease.id,
            'owner': lease.owner,
            'status': lease.status,
            'project_id': None,
            'project_title': None,
            'run_id': None,
            'label': f"GPU 租约未释放（{lease.owner} · {lease.status}）",
        })

    run_by_series: dict[str, str] = {}
    try:
        from shine.production.models import ACTIVE_RUN_STATUSES, ProductionRun, ProductionRunBatch

        for batch, run in db.execute(
            select(ProductionRunBatch, ProductionRun)
            .join(ProductionRun, ProductionRun.id == ProductionRunBatch.run_id)
            .where(ProductionRun.status.in_(ACTIVE_RUN_STATUSES))
        ).all():
            if batch.series_id:
                run_by_series[str(batch.series_id)] = str(run.id)
    except Exception:  # noqa: BLE001 - optional join enrichment only
        run_by_series = {}

    for series in db.scalars(
        select(ProductionSeries).where(ProductionSeries.status.in_(LIVE_SERIES_STATUSES))
    ).all():
        if series.status != 'CLAIMED' and not _series_has_inflight_work(db, series):
            continue
        title = _project_title(series.project_id)
        prefix = f"作品《{title}》" if title else f"作品 {str(series.project_id)[:8]}"
        run_id = run_by_series.get(str(series.id))
        blockers.append({
            'kind': 'production_series',
            'id': series.id,
            'status': series.status,
            'project_id': series.project_id,
            'project_title': title,
            'run_id': run_id,
            'label': f"{prefix} 的拍摄序列尚未结束（{series.status}）",
        })

    for job in db.scalars(
        select(Job).where(Job.status.in_(IN_FLIGHT_JOB_STATUSES), Job.stale.is_(False)).limit(8)
    ).all():
        title = _project_title(job.project_id)
        shot = db.get(Shot, job.shot_id) if job.shot_id else None
        shot_label = shot.name if shot and shot.name else str(job.shot_id or job.id)[:8]
        prefix = f"作品《{title}》" if title else f"作品 {str(job.project_id)[:8]}"
        blockers.append({
            'kind': 'job',
            'id': job.id,
            'status': job.status,
            'project_id': job.project_id,
            'project_title': title,
            'shot_id': job.shot_id,
            'shot_name': shot.name if shot else None,
            'run_id': None,
            'label': f"{prefix} 镜头「{shot_label}」处于 {job.status}",
        })

    # Stopped installation history stays available for per-package recovery.
    # The instance and byte checks still decide what can actually be used.
    installing = db.scalars(
        select(InstallationAttempt).where(
            or_(
                InstallationAttempt.status.in_(['SUBMIT_INTENT', 'INSTALLING']),
                InstallationAttempt.status.in_(['NEEDS_REVIEW', 'FAILED_RECONCILE_REQUIRED'])
                & InstallationAttempt.lease_owner.is_not(None),
            )
        ).limit(8)
    ).all()
    for attempt in installing:
        plan = db.get(InstallationPlan, attempt.plan_id) if attempt.plan_id else None
        payload = (plan.payload or {}) if plan is not None and isinstance(plan.payload, dict) else {}
        packages = [str(row.get('id') or '') for row in (payload.get('packages') or []) if isinstance(row, dict)]
        pkg_note = '、'.join([p for p in packages if p][:3]) or '未知组件'
        blockers.append({
            'kind': 'installation_attempt',
            'id': attempt.id,
            'plan_id': attempt.plan_id,
            'status': attempt.status,
            'error_code': attempt.error_code,
            'packages': packages,
            'project_id': plan.project_id if plan is not None else None,
            'project_title': _project_title(plan.project_id if plan is not None else None),
            'run_id': None,
            'label': f"安装记录待收尾（{pkg_note} · {attempt.error_code or attempt.status}）",
        })
    return blockers


class RuntimeConfigurationBusy(HTTPException):
    def __init__(self, blockers: list[dict]):
        super().__init__(409, 'RUNTIME_CONFIGURATION_REQUIRES_IDLE_AND_RECONCILED_WORK')
        labels = '；'.join(row['label'] for row in blockers[:4])
        self.wait_message = f'等待本机资源可用后自动继续。{labels}'


def require_idle_configuration(db):
    blockers = idle_configuration_blockers(db)
    if blockers:
        raise RuntimeConfigurationBusy(blockers)


def activate_runtime(db, settings, project_id, recipe_sha256, actor, *, claim_guard=None):
    from shine.comfyops.deployment import installed_instance_blockers, load_default_catalog

    db.execute(text('BEGIN IMMEDIATE'))
    if claim_guard is not None:
        claim_guard(db)
    if project_id is not None:
        require_project(db, project_id)
    candidate = db.get(AppSetting, CANDIDATE_KEY)
    if not candidate or candidate.value.get('project_id') != project_id:
        raise HTTPException(409, 'GENERATE_RUNTIME_CONFIGURATION_FIRST')
    identity = candidate.value['identity']
    if recipe_sha256 != identity['recipe_sha256']:
        raise HTTPException(409, 'RUNTIME_CONFIRMATION_HASH_CHANGED')
    if not same_configuration(
            identity, configuration_identity(db, settings, recipe_path(settings)),
            settings=settings):
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
             'project_id': project_id, 'actor': actor,
             'variant_id': candidate.value.get('variant_id')}
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
    from shine.comfyops.deployment import persist_env, runtime_settings_path
    env_keys = persist_env({'SHINE_RENDER_PROVIDER': 'comfyui'}, runtime_settings_path(settings))
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
