"""Read-only preparation gates for explicitly routed, tested text and voice providers."""
from sqlalchemy import select

from shine.comfy.workflow import graph_hash
from shine.comfyops.models import ModelPackage
from shine.llm.role_catalog import (
    MULTIMODAL_ROLE_CATALOG,
    TEXT_ROLE_BY_ID,
    TEXT_ROLES,
    role_payload,
    workflow_roles,
)
from shine.models import AgentRoute, ProviderProfile
from shine.voiceops.runtime_config import VOICE_ROLE_CAPABILITIES

# Backward-compatible alias for callers that imported TEXT_ROLES from this
# module. The role catalog is the only source of the list now.
__all__ = ["TEXT_ROLES", "llm_readiness", "multimodal_capability_status"]

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
                       ('kind', 'base_url', 'model', 'enabled', 'api_key_ref', 'generation_options',
                        'timeout_s', 'max_retries', 'temperature')})


def generation_evidence(profile) -> dict:
    """The recorded generation check of a profile, if there is one."""
    if profile is None:
        return {}
    capabilities = profile.capabilities or {}
    evidence = capabilities.get('generation')
    return evidence if isinstance(evidence, dict) else {}


def _profile_secret_status(profile, secrets) -> str:
    if profile is None:
        return 'not_set'
    if secrets is None:
        return 'unknown' if profile.api_key_ref else 'not_set'
    return secrets.status(profile.api_key_ref)


def profile_evidence(profile, secret_status: str = 'unknown') -> dict:
    if profile is None:
        return {
            'secret_status': secret_status,
            'connection_status': 'NOT_CHECKED',
            'catalog_status': 'NOT_CHECKED',
            'generation_status': 'NOT_CHECKED',
            'last_connection_checked_at': None,
            'last_generation_checked_at': None,
        }
    capabilities = profile.capabilities or {}
    current_identity = profile_identity(profile)
    connection_identity = capabilities.get('setup_config_sha256')
    generation = generation_evidence(profile)
    generation_identity = generation.get('config_sha256')
    if connection_identity is None:
        connection_status = 'NOT_CHECKED'
        catalog_status = 'NOT_CHECKED'
    elif connection_identity != current_identity:
        connection_status = 'EXPIRED'
        catalog_status = 'EXPIRED'
    else:
        reachable = capabilities.get('available') or capabilities.get('reachable') is True
        if not reachable or capabilities.get('auth_ok') is False:
            connection_status = 'FAILED'
        elif capabilities.get('auth_ok') is None:
            connection_status = 'REACHABLE_AUTH_UNVERIFIED'
        else:
            connection_status = 'CONNECTED'
        if capabilities.get('model_listed') is True:
            catalog_status = 'LISTED'
        elif capabilities.get('model_listed') is False:
            catalog_status = 'NOT_LISTED'
        elif (
            capabilities.get('catalog_error_code') == 'LLM_CATALOG_INVALID'
            or capabilities.get('error_code') == 'LLM_CATALOG_INVALID'
        ):
            catalog_status = 'INVALID'
        elif capabilities.get('catalog_supported') is False or capabilities.get('catalog_ok') is False:
            catalog_status = 'UNAVAILABLE'
        else:
            catalog_status = 'NOT_REPORTED'
    if not generation:
        generation_status = 'NOT_CHECKED'
    elif generation_identity != current_identity:
        generation_status = 'EXPIRED'
    else:
        generation_passed = bool(
            generation.get('generation_ok')
            and generation.get('structured_output_ok')
            and generation.get('model_matches_config') is True
        )
        if not generation_passed:
            generation_status = 'FAILED'
        elif catalog_status in {'INVALID', 'UNAVAILABLE', 'NOT_REPORTED'}:
            generation_status = 'PASSED_CATALOG_UNAVAILABLE'
        else:
            generation_status = 'PASSED'
    return {
        'secret_status': secret_status,
        'connection_status': connection_status,
        'catalog_status': catalog_status,
        'generation_status': generation_status,
        'last_connection_checked_at': capabilities.get('setup_checked_at'),
        'last_generation_checked_at': generation.get('checked_at'),
    }


def llm_readiness(db, *, workflow: str | None = None, secrets=None):
    """Report what the text roles have actually proved.

    A successful connection/catalog check proves an exact model listing when
    the provider exposes one. If the catalog endpoint is unsupported, the
    service can be reachable while authentication remains unverified until
    the structured generation probe succeeds. A connection check alone is
    always CONNECTION_READY, never GENERATION_READY.
    """
    role_ids = workflow_roles(workflow)
    routes = {row.role: row for row in db.scalars(select(AgentRoute))}
    rows = []
    for role in role_ids:
        definition = TEXT_ROLE_BY_ID[role]
        route = routes.get(role)
        profile = db.get(ProviderProfile, route.provider_profile_id) if route else None
        secret_status = _profile_secret_status(profile, secrets)
        evidence_status = profile_evidence(profile, secret_status)
        code = None
        generation_code = None
        readiness = READINESS_UNCONFIGURED
        if profile is None:
            code = 'LLM_ROUTE_REQUIRED'
        elif (
            profile.kind == 'mock'
            or (profile.capabilities or {}).get('mock')
            or (profile.capabilities or {}).get('is_mock')
        ):
            code = 'REAL_LLM_PROVIDER_REQUIRED'
            readiness = READINESS_BLOCKED
        elif not profile.enabled or not profile.model.strip():
            code = 'LLM_PROFILE_DISABLED_OR_MODEL_MISSING'
            readiness = READINESS_BLOCKED
        elif secret_status in {'missing', 'unreadable'}:
            code = 'LLM_CREDENTIAL_UNAVAILABLE'
            readiness = READINESS_BLOCKED
        elif (
            not (
                (profile.capabilities or {}).get('available')
                or (profile.capabilities or {}).get('reachable') is True
            )
            or (profile.capabilities or {}).get('auth_ok') is False
            or (profile.capabilities or {}).get('model_listed') is False
        ):
            code = 'LLM_CONNECTION_TEST_REQUIRED'
            readiness = READINESS_BLOCKED
        elif (profile.capabilities or {}).get('setup_config_sha256') != profile_identity(profile):
            # The evidence belongs to an older configuration.
            code = 'LLM_CONNECTION_TEST_REQUIRED'
            readiness = READINESS_STALE
        else:
            evidence = generation_evidence(profile)
            if (
                evidence.get('generation_ok')
                and evidence.get('structured_output_ok')
                and evidence.get('model_matches_config') is True
                and evidence.get('config_sha256') == profile_identity(profile)
            ):
                readiness = READINESS_GENERATION_READY
            else:
                readiness = READINESS_CONNECTION_READY
                generation_code = 'LLM_GENERATION_TEST_REQUIRED'
        rows.append({
            **role_payload(definition),
            'role': role,
            'profile_id': profile.id if profile else None,
            'profile_name': profile.name if profile else None,
            'profile_kind': profile.kind if profile else None,
            'profile_enabled': profile.enabled if profile else None,
            'profile_valid': bool(
                profile
                and profile.kind != 'mock'
                and profile.enabled
                and profile.model.strip()
            and secret_status not in {'missing', 'unreadable'}
            ),
            'code': code,
            'generation_code': generation_code,
            'readiness_status': readiness,
            **evidence_status,
        })
    status = 'READY' if all(row['code'] is None for row in rows) else 'BLOCKED'
    if status == 'BLOCKED':
        generation_status = 'BLOCKED'
    elif all(row['readiness_status'] == READINESS_GENERATION_READY for row in rows):
        generation_status = 'GENERATION_READY'
    else:
        generation_status = 'CONNECTION_READY'
    return {
        'status': status,
        'workflow': workflow or 'all_text',
        'role_count': len(rows),
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


def multimodal_capability_status(db, secrets=None):
    """Describe VLM/ASR separately from the text Agent directory."""
    routes = {row.role: row for row in db.scalars(select(AgentRoute))}
    profiles = []
    for definition in MULTIMODAL_ROLE_CATALOG:
        route = routes.get(definition['role'])
        profile = db.get(ProviderProfile, route.provider_profile_id) if route else None
        secret_status = _profile_secret_status(profile, secrets)
        profiles.append({
            **definition,
            'provider_profile_id': profile.id if profile else None,
            'profile_name': profile.name if profile else None,
            'profile_kind': profile.kind if profile else None,
            'profile_enabled': profile.enabled if profile else None,
            'secret_status': secret_status,
            'status': (
                'NOT_CONFIGURED' if profile is None
                else 'BLOCKED' if profile.kind == 'mock' or not profile.enabled
                else 'ROUTED_REQUIRES_CAPABILITY_CHECK'
            ),
        })
    return profiles


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
