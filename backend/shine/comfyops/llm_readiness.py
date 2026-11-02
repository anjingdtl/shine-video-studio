"""Read-only preparation gate for explicitly routed, tested text providers."""
from sqlalchemy import select

from shine.comfy.workflow import graph_hash
from shine.models import AgentRoute, ProviderProfile

TEXT_ROLES = ('script_planner', 'shot_planner', 'scriptwriter', 'director', 'cinematographer', 'editor',
              'continuity_supervisor', 'sound_director', 'producer', 'comfyops', 'repair_planner', 'qc_reviewer')


def profile_identity(profile):
    return graph_hash({key: getattr(profile, key) for key in
                       ('kind', 'base_url', 'model', 'enabled', 'api_key_ref', 'generation_options')})


def llm_readiness(db):
    routes = {row.role: row for row in db.scalars(select(AgentRoute))}
    rows = []
    for role in TEXT_ROLES:
        route = routes.get(role)
        profile = db.get(ProviderProfile, route.provider_profile_id) if route else None
        code = None
        if profile is None:
            code = 'LLM_ROUTE_REQUIRED'
        elif profile.kind == 'mock' or profile.capabilities.get('mock') or profile.capabilities.get('is_mock'):
            code = 'REAL_LLM_PROVIDER_REQUIRED'
        elif not profile.enabled or not profile.model.strip():
            code = 'LLM_PROFILE_DISABLED_OR_MODEL_MISSING'
        elif (not profile.capabilities.get('available') or profile.capabilities.get('model_listed') is False
              or profile.capabilities.get('setup_config_sha256') != profile_identity(profile)):
            code = 'LLM_CONNECTION_TEST_REQUIRED'
        rows.append({'role': role, 'profile_id': profile.id if profile else None,
                     'profile_name': profile.name if profile else None, 'code': code})
    return {'status': 'READY' if all(row['code'] is None for row in rows) else 'BLOCKED',
            'roles': rows, 'verification': 'CONNECTION_ONLY_NOT_GENERATION'}
