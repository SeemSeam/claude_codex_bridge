from __future__ import annotations

from provider_core.pathing import session_filename_for_agent
from provider_core.registry import (
    build_default_backend_registry,
    build_default_runtime_launcher_map,
    build_default_session_binding_map,
)


def test_backend_registry_exposes_manifests_execution_and_session_bindings() -> None:
    registry = build_default_backend_registry(include_optional=True, include_test_doubles=True)

    codex = registry.get('codex')
    assert codex is not None
    assert codex.manifest.provider == 'codex'
    assert codex.execution_adapter is not None
    assert codex.execution_adapter.provider == 'codex'
    assert codex.session_binding is not None
    assert codex.session_binding.provider == 'codex'
    assert codex.runtime_launcher is not None
    assert codex.runtime_launcher.provider == 'codex'

    fake = registry.get('fake')
    assert fake is not None
    assert fake.execution_adapter is not None
    assert fake.session_binding is None
    assert fake.runtime_launcher is None


def test_default_session_binding_map_uses_backend_owned_entries() -> None:
    bindings = build_default_session_binding_map(include_optional=True)

    assert set(bindings) == {'codex', 'claude', 'gemini', 'opencode', 'droid', 'agy'}
    assert bindings['codex'].session_id_attr == 'codex_session_id'
    assert bindings['opencode'].session_path_attr == 'session_file'
    assert bindings['agy'].session_path_attr == 'agy_session_path'


def test_default_runtime_launcher_map_uses_backend_owned_entries() -> None:
    launchers = build_default_runtime_launcher_map(include_optional=True)

    assert set(launchers) == {'codex', 'claude', 'gemini', 'opencode', 'droid', 'agy'}
    assert launchers['codex'].launch_mode == 'codex_tmux'
    assert launchers['gemini'].launch_mode == 'simple_tmux'
    assert launchers['agy'].launch_mode == 'simple_tmux'


def test_session_filename_for_agent_follows_agent_first_naming() -> None:
    assert session_filename_for_agent('codex', 'writer') == '.codex-writer-session'
    assert session_filename_for_agent('codex', 'codex') == '.codex-codex-session'
