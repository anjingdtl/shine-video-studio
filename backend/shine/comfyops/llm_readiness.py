"""Read-only preparation gates for explicitly routed, tested text and voice providers."""
from sqlalchemy import select

from shine.comfy.workflow import graph_hash
from shine.comfyops.models import ModelPackage
from shine.models import AgentRoute, ProviderProfile
from shine.voiceops.runtime_config import VOICE_ROLE_CAPABILITIES

TEXT_ROLES = ('script_planner', 'shot_planner', 'scriptwriter', 'director', 'cinematographer', 'editor',
              'continuity_supervisor', 'sound_director', 'producer', 'comfyops', 'repair_planner', 'qc_reviewer')

#: The minimum set of voice roles that lets the workbench generate and freeze a
#: voice master without a clone source.
VOICE_ROLES_REQUIRED_FOR_MASTERS = ('design',)

#: The states one text role can be in.  A connection check and a generation
#: check are two different facts and never collapse into one.
READINESS_UNCONFIGURED = 'UNCONFIGURED'
READINESS_CONNECTION_READY = 'CONNECTION_READY'
READINESS_GENERATION_READY = 'GENERATION_READY'
READINESS_BLOCKED = 'BLOCKED'
READINESS_STALE = 'STALE'


def profile_identity(profile):
    return graph_hash({key: getattr(profile, key) for key in
                       ('kind', 'base_url', 'model', 'enabled', 'api_key_ref', 'generation_options')})


def generation_evidence(profile) -> dict:
    """The recorded generation check of a profile, if there is one."""
    if profile is None:
        return {}
    capabilities = profile.capabilities or {}
    evidence = capabilities.get('generation')
    return evidence if isinstance(evidence, dict) else {}


def llm_readiness(db):
    """Report what the text roles have actually proved.

    The connection gate keeps its meaning - the provider answered and the
    configured model was listed.  The generation gate is separate: a role
    that only passed the connection check is CONNECTION_READY, never
    GENERATION_READY.
    """
    routes = {row.role: row for row in db.scalars(select(AgentRoute))}
    rows = []
    for role in TEXT_ROLES:
        route = routes.get(role)
        profile = db.get(ProviderProfile, route.provider_profile_id) if route else None
        code = None
        generation_code = None
        readiness = READINESS_UNCONFIGURED
        if profile is None:
            code = 'LLM_ROUTE_REQUIRED'
        elif profile.kind == 'mock' or profile.capabilities.get('mock') or profile.capabilities.get('is_mock'):
            code = 'REAL_LLM_PROVIDER_REQUIRED'
            readiness = READINESS_BLOCKED
        elif not profile.enabled or not profile.model.strip():
            code = 'LLM_PROFILE_DISABLED_OR_MODEL_MISSING'
            readiness = READINESS_BLOCKED
        elif (not profile.capabilities.get('available')
              or profile.capabilities.get('model_listed') is False):
            code = 'LLM_CONNECTION_TEST_REQUIRED'
            readiness = READINESS_BLOCKED
        elif profile.capabilities.get('setup_config_sha256') != profile_identity(profile):
            # The evidence belongs to an older configuration.
            code = 'LLM_CONNECTION_TEST_REQUIRED'
            readiness = READINESS_STALE
        else:
            evidence = generation_evidence(profile)
            if evidence.get('generation_ok') and evidence.get('config_sha256') == profile_identity(profile):
                readiness = READINESS_GENERATION_READY
            else:
                readiness = READINESS_CONNECTION_READY
                generation_code = 'LLM_GENERATION_TEST_REQUIRED'
        rows.append({'role': role, 'profile_id': profile.id if profile else None,
                     'profile_name': profile.name if profile else None, 'code': code,
                     'generation_code': generation_code, 'readiness_status': readiness})
    status = 'READY' if all(row['code'] is None for row in rows) else 'BLOCKED'
    if status == 'BLOCKED':
        generation_status = 'BLOCKED'
    elif all(row['readiness_status'] == READINESS_GENERATION_READY for row in rows):
        generation_status = 'GENERATION_READY'
    else:
        generation_status = 'CONNECTION_READY'
    return {
        'status': status,
        'roles': rows,
        'verification': 'CONNECTION_ONLY_NOT_GENERATION',
        'generation_verification': (
            'GENERATION_TESTED_NOT_QUALITY_ACCEPTANCE'
            if generation_status == 'GENERATION_READY'
            else 'GENERATION_TEST_NOT_RUN'
        ),
        'generation_status': generation_status,
        'text_agent_ready': generation_status == 'GENERATION_READY',
    }


def voice_readiness(db, settings=None):
    """Read-only voice-capability gate for the new-machine setup flow.

    Mirrors :func:`llm_readiness`: it never synthesizes and never claims a
    runtime works.  A role is ready only when three independent facts hold:

    1. the role is routed to a registered local package,
    2. that package's files were hash-verified against their pinned digests
       (`FILES_VERIFIED_PENDING_RUNTIME`) and it declares the capability,
    3. the role's interpreter/overlay runtime is configured and matches the
       package's pinned runtime versions.

    `voice_master_capable` is the minimum needed to generate and freeze a
    voice master (the design model); clone/dialogue roles are reported
    separately so a partially installed runtime is never shown as fully ready.
    """
    from shine.voiceops.runtime_config import (
        VoiceRuntimeConfig,
        check_role_runtime,
        load_voice_config,
        package_files_present,
    )

    config = load_voice_config(settings) if settings is not None else VoiceRuntimeConfig()
    routing = config.routing()
    runtimes = config.runtimes()
    root = settings.comfyops_dir / "isolated-packages" if settings is not None else None
    rows = []
    for role, capability in VOICE_ROLE_CAPABILITIES.items():
        package_id = routing.get(role)
        package = db.get(ModelPackage, package_id) if package_id else None
        runtime = runtimes.get(role)
        code = None
        files_present = None
        if not package_id:
            code = 'VOICE_PACKAGE_UNROUTED'
        elif not package:
            code = 'VOICE_PACKAGE_MISSING'
        elif capability not in (package.payload.get('capabilities') or []):
            code = 'VOICE_CAPABILITY_MISSING'
        # Mock packages never make the real voice path ready.
        elif package.status != 'FILES_VERIFIED_PENDING_RUNTIME':
            code = 'VOICE_PACKAGE_NOT_VERIFIED'
            if root is not None:
                files_present = package_files_present(package.payload, root / package.payload['name'])
        else:
            if runtime is None:
                code = 'VOICE_ROLE_RUNTIME_UNCONFIGURED'
            else:
                problems = check_role_runtime(role, runtime, package.payload)
                if problems:
                    code = problems[0]
        rows.append({'role': role, 'package_id': package_id or None,
                     'status': package.status if package else None,
                     'capability': capability,
                     'files_present': files_present,
                     'runtime_configured': runtime is not None,
                     'code': code})
    by_role = {row['role']: row for row in rows}
    return {
        'status': 'READY' if all(row['code'] is None for row in rows) else 'BLOCKED',
        'voice_master_capable': all(by_role[role]['code'] is None
                                    for role in VOICE_ROLES_REQUIRED_FOR_MASTERS),
        'voice_clone_capable': by_role['base']['code'] is None,
        'roles': rows,
        'verification': 'PACKAGE_ROUTING_ONLY_NOT_SYNTHESIS',
    }
