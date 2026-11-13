"""Mode-level capability registration: template drift is advisory, not a lock.

Covers the WP1 contract of the dynamic per-shot workflow round:

* one drifted historical template must not block the whole Ref2VA mode
  (T06), neither for a v2 mode-capability seal nor for a legacy v1 seal;
* the audited core node set stays a hard gate: a renderer without it is
  never READY, and a changed core set invalidates a v2 seal;
* baseline templates a project never registered are auto-filled from the
  repository files exactly once, and a drifted row is never overwritten;
* the renderer startup node gate covers only the baseline mode's profiles -
  a candidate variant registered by one project is not a startup requirement
  for every renderer.
"""
import json
import os

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

from shine.comfy.workflow import graph_hash
from shine.comfyops import mode_capabilities
from shine.comfyops.runtime_configuration import (
    SERVICE_EVIDENCE_KEY,
    service_evidence_status,
)
from shine.models import AppSetting, WorkflowProfile
from tests.conftest import make_studio

pytestmark = pytest.mark.integration


def _identity_stub() -> dict:
    return {
        'recipe': 'recipe.json', 'recipe_sha256': 'r' * 64,
        'instance_id': 'inst', 'instance_sha256': 'i' * 64,
        'catalog_sha256': 'c' * 64, 'files': {},
        'comfyui_base_url': 'http://127.0.0.1:8188',
    }


def _register_baseline_rows(db, project_id: str, *, drift: str | None = None) -> dict[str, str]:
    """Register the baseline templates directly from repository files."""
    files = {}
    from shine.comfyops import deployment
    for path in deployment.PROFILE_DIR.glob('*.json'):
        raw = json.loads(path.read_text(encoding='utf-8'))
        if raw.get('name'):
            files[raw['name']] = raw
    created = {}
    for name in mode_capabilities.baseline_profile_names():
        raw = files[name]
        profile = WorkflowProfile(
            project_id=project_id,
            name=name,
            mode=raw.get('mode') or 'Ref2VA',
            graph=raw['graph'],
            parameter_map=raw.get('parameter_map') or {},
            media_transfers=raw.get('media_transfers') or {},
            required_nodes=raw.get('required_nodes') or [],
            checkpoint_id=raw.get('checkpoint_id'),
            graph_sha256=graph_hash(raw['graph']),
            model_identity={},
            validated_against={'missing_nodes': [], 'missing_inputs': []},
        )
        if name == drift:
            # Simulate the historical drift case: the row was written against
            # an older template generation and never re-registered.
            profile.parameter_map = dict(profile.parameter_map)
            profile.parameter_map['legacy_extra_key'] = '30.inputs.values.a'
        db.add(profile)
        db.flush()
        created[name] = profile.id
    return created


@pytest.fixture
def studio(tmp_path):
    app, factory, settings = make_studio(tmp_path)
    with TestClient(app) as client:
        from tests.conftest import create_project
        project_id = create_project(client, title='模式能力')
        yield factory, project_id
    app.state.engine.dispose()


def test_baseline_classification_splits_drift_and_missing(studio):
    factory, project_id = studio
    with factory.begin() as db:
        created = _register_baseline_rows(db, project_id, drift=mode_capabilities.baseline_profile_names()[0])
        state = mode_capabilities.classify_baseline_profiles(db, project_id)
    names = mode_capabilities.baseline_profile_names()
    drifted_name = names[0]
    assert drifted_name in state['drifted']
    assert set(state['contracts']) == {name for name in names if name != drifted_name}
    assert state['missing'] == []
    assert created[drifted_name]  # the drifted row stays untouched


def test_auto_register_fills_missing_once_and_never_overwrites_drift(studio):
    factory, project_id = studio
    names = mode_capabilities.baseline_profile_names()
    drift_name = names[0]
    with factory.begin() as db:
        _register_baseline_rows(db, project_id, drift=drift_name)
    with factory.begin() as db:
        mode_capabilities.auto_register_missing_baseline_profiles(db, project_id, actor='test')
    with factory() as db:
        state = mode_capabilities.classify_baseline_profiles(db, project_id)
        # The drifted row is still drifted - auto-fill must not absorb drift.
        assert drift_name in state['drifted']
        assert state['missing'] == []
        # A second run registers nothing new (idempotent).
        created_again = mode_capabilities.auto_register_missing_baseline_profiles(db, project_id, actor='test')
    assert created_again == []


def test_auto_register_creates_rows_for_a_fresh_project(studio):
    factory, project_id = studio
    with factory.begin() as db:
        created = mode_capabilities.auto_register_missing_baseline_profiles(db, project_id, actor='test')
    assert sorted(created) == sorted(mode_capabilities.baseline_profile_names())
    with factory() as db:
        state = mode_capabilities.classify_baseline_profiles(db, project_id)
    assert not state['missing'] and not state['drifted']
    assert set(state['contracts']) == set(mode_capabilities.baseline_profile_names())


def test_v2_evidence_stays_ready_when_one_template_drifts(studio):
    """T06: one drifted historical template is advisory, never a mode block."""
    factory, project_id = studio
    identity = _identity_stub()
    drift_name = mode_capabilities.baseline_profile_names()[0]
    from shine.comfyops.runtime_configuration import workflow_contract_sha256
    with factory.begin() as db:
        created = _register_baseline_rows(db, project_id)
        sealed = db.get(WorkflowProfile, created[drift_name])
        inventory = {
            name: {'id': pid, 'contract_sha256': workflow_contract_sha256(db.get(WorkflowProfile, pid))}
            for name, pid in created.items()
        }
        row = AppSetting(key=SERVICE_EVIDENCE_KEY, value={
            'version': 2,
            'configuration_sha256': graph_hash(identity),
            'recipe_sha256': identity['recipe_sha256'],
            'instance_id': identity['instance_id'],
            'instance_sha256': identity['instance_sha256'],
            'workflow_profiles': inventory,
            'mode_capabilities': {'required_nodes': sorted(mode_capabilities.core_mode_node_types())},
        })
        row.value['sha256'] = graph_hash(
            {k: v for k, v in row.value.items() if k != 'sha256'})
        db.add(row)
        # Drift happens *after* the seal: the row was validated against the
        # repository template and has since been edited on one machine.
        sealed.parameter_map = dict(sealed.parameter_map)
        sealed.parameter_map['legacy_extra_key'] = '30.inputs.values.a'
        db.flush()
        status = service_evidence_status(db, None, identity=identity)
    assert status['status'] == 'READY', status
    advisories = status.get('template_advisories') or []
    assert any(item['code'] == 'WORKFLOW_TEMPLATE_DRIFTED' and item['template'] == drift_name
               for item in advisories)


def test_v1_legacy_evidence_drift_is_advisory_not_blocker(studio):
    """A legacy v1 seal upgrades in place: drift advisories replace blockers."""
    factory, project_id = studio
    identity = _identity_stub()
    with factory.begin() as db:
        _register_baseline_rows(db, project_id, drift=mode_capabilities.baseline_profile_names()[0])
        row = AppSetting(key=SERVICE_EVIDENCE_KEY, value={
            'version': 1,
            'configuration_sha256': graph_hash(identity),
            'recipe_sha256': identity['recipe_sha256'],
            'instance_id': identity['instance_id'],
            'instance_sha256': identity['instance_sha256'],
            'workflow_profiles': {},
        })
        row.value['sha256'] = graph_hash(
            {k: v for k, v in row.value.items() if k != 'sha256'})
        db.add(row)
        db.flush()
        status = service_evidence_status(db, None, identity=identity)
    assert status['status'] == 'READY', status


def test_changed_core_node_set_invalidates_v2_seal(studio):
    factory, _project_id = studio
    identity = _identity_stub()
    with factory.begin() as db:
        row = AppSetting(key=SERVICE_EVIDENCE_KEY, value={
            'version': 2,
            'configuration_sha256': graph_hash(identity),
            'recipe_sha256': identity['recipe_sha256'],
            'instance_id': identity['instance_id'],
            'instance_sha256': identity['instance_sha256'],
            'mode_capabilities': {'required_nodes': ['SomeRemovedNode']},
        })
        row.value['sha256'] = graph_hash(
            {k: v for k, v in row.value.items() if k != 'sha256'})
        db.add(row)
        db.flush()
        status = service_evidence_status(db, None, identity=identity)
    assert status['status'] == 'PENDING'
    assert 'MODE_CAPABILITY_CONTRACT_CHANGED' in status['blockers']


def test_core_mode_nodes_refuse_renderer_without_baseline_nodes():
    object_info = {'Reroute': {}, 'Note': {}}
    with pytest.raises(HTTPException) as exc:
        from shine.comfyops.service_validation import _validate_core_mode_nodes
        _validate_core_mode_nodes(object_info)
    detail = exc.value.detail
    assert detail['code'] == 'CORE_MODE_NODES_MISSING'
    assert detail['missing_nodes']


def test_core_mode_nodes_accept_full_baseline_inventory():
    from shine.comfyops.service_validation import _validate_core_mode_nodes
    inventory = {name: {'input': {}} for name in mode_capabilities.core_mode_node_types()}
    _validate_core_mode_nodes(inventory)  # must not raise


def test_startup_node_gate_ignores_non_baseline_variant_profiles(studio):
    """A candidate variant's node pack is not a startup requirement."""
    factory, project_id = studio
    from shine.comfyops.handoff_startup import required_workflow_node_types
    with factory.begin() as db:
        _register_baseline_rows(db, project_id)
        db.add(WorkflowProfile(
            project_id=project_id,
            name='local-h3-ref2va-lowend-pdd-free-2img',
            mode='Ref2VA',
            graph={'1': {'class_type': 'CandidateOnlyNode', 'inputs': {}}},
            parameter_map={}, media_transfers={}, required_nodes=[],
            checkpoint_id=None, graph_sha256='x' * 64, model_identity={},
        ))
    required = required_workflow_node_types(factory)
    assert 'CandidateOnlyNode' not in required
    assert required  # the baseline classes still gate the renderer


def test_startup_node_gate_covers_registered_baseline_graphs(studio):
    factory, project_id = studio
    from shine.comfyops.handoff_startup import required_workflow_node_types
    with factory.begin() as db:
        _register_baseline_rows(db, project_id)
    required = required_workflow_node_types(factory)
    assert 'MiniMaxH3ReferenceToVideo' in required


@pytest.mark.skipif(os.name != 'nt', reason='variant binding fixtures are Windows-pathed')
def test_catalog_only_registration_resolves_declared_variant():
    """The catalog-only path accepts the file-stem form of the catalog id."""
    from shine.comfyops.variant_binding import resolve_registration_target
    binding = resolve_registration_target(catalog_id='h3-ref2va-windows-x64')
    assert binding.variant_id == 'h3-ref2va-int8-convrot'
    assert binding.catalog_id == 'shine-h3-ref2va-windows-x64'
