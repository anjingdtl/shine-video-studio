"""V3.1 runtime guarantee: the copilot cannot write source or run commands.

Three structural properties are asserted here, and each one fails loudly if a
future change tries to hand the runtime agent more power than the design allows:

1. **one capability catalog** - the grant's permission map and the declared
   write actions are the same set, so a preview can never advertise a capability
   that has no reviewed contract (or hide one that does);
2. **no source-writing executor** - every declared executor is an existing
   business entry point, and none of them writes project source, node source or
   templates;
3. **no command-shaped parameter** - no tool schema offers a field a model could
   use to smuggle a shell command, a patch or a script.
"""
from __future__ import annotations

import inspect

import pytest

from shine.copilot import declarations as decl
from shine.copilot import task_grant as grant_contract
from shine.copilot import tool_registry
from shine.support.tools import ACTION_SPECS, FORBIDDEN_CAPABILITIES

pytestmark = pytest.mark.integration

#: Executor modules that are allowed to appear in a declaration.  Each is an
#: existing product entry point; none of them edits source files.
ALLOWED_EXECUTOR_MODULES = (
    "shine.api_setup",
    "shine.api_comfy",
    "shine.services",
    "shine.comfyops.",
    "shine.director.",
    "shine.materialprep.",
    "shine.production.",
    "shine.support.agent",
    "shine.support.parameters",
)

#: Name fragments that would indicate a source-writing or command-running port.
FORBIDDEN_EXECUTOR_FRAGMENTS = (
    "write_source",
    "write_file",
    "patch",
    "apply_diff",
    "run_command",
    "run_shell",
    "exec(",
    "install_script",
)


def test_the_grant_catalog_and_the_declarations_are_one_set():
    """A permission-mapped capability must be declared, and vice versa."""
    declared = {row.action_id for row in decl.WRITE_ACTIONS}
    mapped = set(grant_contract.CAPABILITY_PERMISSIONS)
    assert mapped <= declared, f"grant maps undeclared capabilities: {sorted(mapped - declared)}"
    # The autonomous subset must also be a subset of the declared set.
    assert set(tool_registry.AUTONOMOUS_CAPABILITIES) <= declared
    # And every declared action has a tool spec, so the menu is complete.
    assert declared == set(ACTION_SPECS)


def test_every_executor_is_an_existing_business_entry_point():
    for row in decl.WRITE_ACTIONS:
        executor = str(row.executor or "")
        assert executor, f"{row.action_id} has no executor"
        assert executor.startswith(ALLOWED_EXECUTOR_MODULES), (
            f"{row.action_id} points at an unexpected executor: {executor}"
        )
        lowered = executor.casefold()
        for fragment in FORBIDDEN_EXECUTOR_FRAGMENTS:
            assert fragment not in lowered, (
                f"{row.action_id} executor looks like a source/command port: {executor}"
            )


def test_every_declared_executor_really_exists():
    """A declaration that names a missing entry point fails at preview time.

    Two shapes are legitimate: a module-level function
    (``shine.api_comfy.create_workflow_profile``) and a whole module whose
    public entry point the adapter calls explicitly
    (``shine.production.runner``).
    """
    import importlib

    for row in decl.WRITE_ACTIONS:
        path = str(row.executor)
        module_path, _, function_name = path.rpartition(".")
        try:
            module = importlib.import_module(module_path)
        except ModuleNotFoundError:
            module = importlib.import_module(path)
            assert inspect.ismodule(module), f"{row.action_id}: {path} does not exist"
            continue
        target = getattr(module, function_name, None)
        assert target is not None, f"{row.action_id}: {path} does not exist"
        assert callable(target) or inspect.ismodule(target), (
            f"{row.action_id}: {path} is neither callable nor a module"
        )


def test_no_capability_can_be_turned_into_a_shell_or_a_patch():
    names = set(ACTION_SPECS)
    for forbidden in ("shell", "python_exec", "git", "pip_install", "write_source",
                      "write_node_source", "network_script", "bypass_gate"):
        assert forbidden not in names, f"{forbidden} must not be a callable capability"
    assert set(FORBIDDEN_CAPABILITIES) >= {
        "shell", "python_exec", "git", "pip_install", "write_source", "write_node_source",
    }


def test_tool_schemas_expose_no_command_shaped_field():
    """No tool may accept a command, script, patch or destination source path."""
    specs = tool_registry.tool_specs([row.action_id for row in decl.WRITE_ACTIONS])
    banned = (
        "command", "cmd", "shell", "script", "patch", "diff", "source_path",
        "target_path", "code", "executable",
    )
    for spec in specs:
        fields = {name.casefold() for name in (spec.parameters.get("properties") or {})}
        overlap = fields & set(banned)
        assert not overlap, f"{spec.name} exposes command-shaped fields: {sorted(overlap)}"
        # Every tool is closed: an unlisted argument is a contract violation.
        assert spec.parameters.get("additionalProperties") is False


def test_an_authorization_cannot_enable_source_or_shell_permissions():
    grant = grant_contract.build_proposed_grant(
        grant_id="g",
        session_id="s",
        actor_id="local-user",
        objective="做任何事",
        project_ids=[],
        installation_ids=[],
        allowed_resource_roots=[],
        groups=list(grant_contract.GROUP_PERMISSIONS),
        limits=None,
        created_at=__import__("shine.models", fromlist=["utcnow"]).utcnow(),
    )
    for permission in grant_contract.PERMANENTLY_FORBIDDEN:
        assert grant["permissions"][permission] is False
    assert "modify_source_code" in grant_contract.PERMANENTLY_FORBIDDEN
    assert "arbitrary_shell" in grant_contract.PERMANENTLY_FORBIDDEN
    assert "arbitrary_http" in grant_contract.PERMANENTLY_FORBIDDEN


def test_the_grant_module_holds_no_execution_surface():
    """The contract layer must stay pure: no subprocess, no file writes."""
    from shine.copilot import task_grant

    source = inspect.getsource(task_grant)
    for fragment in ("subprocess", "os.system", "shutil", "open(", "eval(", "exec("):
        assert fragment not in source, f"task_grant must not contain {fragment}"


def test_the_tool_loop_holds_no_execution_surface():
    from shine.copilot import tool_loop

    source = inspect.getsource(tool_loop)
    for fragment in ("subprocess", "os.system", "shutil", "eval(", "exec("):
        assert fragment not in source, f"tool_loop must not contain {fragment}"


def test_the_grant_store_holds_no_business_execution():
    """``support.grants`` only writes the authorization ledger."""
    from shine.support import grants

    source = inspect.getsource(grants)
    for fragment in ("subprocess", "os.system", "shutil", "LocalInstaller",
                     "create_workflow_profile", "preview_action", "approve_action"):
        assert fragment not in source, f"grants must not call {fragment}"
