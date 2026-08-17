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

    assert set(bindings) == {
        'codex',
        'claude',
        'gemini',
        'opencode',
        'droid',
        'agy',
        'kimi',
        'deepseek',
        'dsh',
        'mimo',
        'qwen',
        'qoder',
        'qoderclicn',
        'cursor',
        'copilot',
        'crush',
        'grok',
        'kiro',
        'pi',
        'omp',
        'zai',
        'grok',
    }
    assert bindings['codex'].session_id_attr == 'codex_session_id'
    assert bindings['opencode'].session_path_attr == 'session_file'
    assert bindings['agy'].session_path_attr == 'agy_session_path'
    assert bindings['kimi'].session_path_attr == 'kimi_session_path'
    assert bindings['deepseek'].session_path_attr == 'deepseek_session_path'
    assert bindings['dsh'].session_path_attr == 'dsh_session_path'
    assert bindings['mimo'].session_path_attr == 'mimo_session_path'
    assert bindings['qwen'].session_path_attr == 'qwen_session_path'
    assert bindings['qoder'].session_path_attr == 'qoder_session_path'
    assert bindings['qoderclicn'].session_path_attr == 'qoderclicn_session_path'
    assert bindings['cursor'].session_path_attr == 'cursor_session_path'
    assert bindings['copilot'].session_path_attr == 'copilot_session_path'
    assert bindings['crush'].session_path_attr == 'crush_session_path'
    assert bindings['grok'].session_path_attr == 'grok_session_path'
    assert bindings['kiro'].session_path_attr == 'kiro_session_path'
    assert bindings['pi'].session_path_attr == 'pi_session_path'
    assert bindings['omp'].session_path_attr == 'omp_session_path'
    assert bindings['zai'].session_path_attr == 'zai_session_path'
    assert bindings['grok'].session_path_attr == 'grok_session_path'


def test_default_runtime_launcher_map_uses_backend_owned_entries() -> None:
    launchers = build_default_runtime_launcher_map(include_optional=True)

    assert set(launchers) == {
        'codex',
        'claude',
        'gemini',
        'opencode',
        'droid',
        'agy',
        'kimi',
        'deepseek',
        'dsh',
        'mimo',
        'qwen',
        'qoder',
        'qoderclicn',
        'cursor',
        'copilot',
        'crush',
        'grok',
        'kiro',
        'pi',
        'omp',
        'zai',
        'grok',
    }
    assert launchers['codex'].launch_mode == 'codex_tmux'
    assert launchers['gemini'].launch_mode == 'simple_tmux'
    assert launchers['agy'].launch_mode == 'simple_tmux'
    assert launchers['kimi'].launch_mode == 'simple_tmux'
    assert launchers['deepseek'].launch_mode == 'simple_tmux'
    assert launchers['dsh'].launch_mode == 'simple_tmux'
    assert launchers['mimo'].launch_mode == 'simple_tmux'
    assert launchers['qwen'].launch_mode == 'simple_tmux'
    assert launchers['qoder'].launch_mode == 'simple_tmux'
    assert launchers['qoderclicn'].launch_mode == 'simple_tmux'
    assert launchers['cursor'].launch_mode == 'simple_tmux'
    assert launchers['copilot'].launch_mode == 'simple_tmux'
    assert launchers['crush'].launch_mode == 'simple_tmux'
    assert launchers['grok'].launch_mode == 'simple_tmux'
    assert launchers['kiro'].launch_mode == 'simple_tmux'
    assert launchers['pi'].launch_mode == 'simple_tmux'
    assert launchers['omp'].launch_mode == 'simple_tmux'
    assert launchers['zai'].launch_mode == 'simple_tmux'
    assert launchers['grok'].launch_mode == 'simple_tmux'


def test_session_filename_for_agent_follows_agent_first_naming() -> None:
    assert session_filename_for_agent('codex', 'writer') == '.codex-writer-session'
    assert session_filename_for_agent('codex', 'codex') == '.codex-codex-session'
    assert session_filename_for_agent('agy', 'antigravity') == '.agy-antigravity-session'
    assert session_filename_for_agent('kimi', 'moon') == '.kimi-moon-session'
    assert session_filename_for_agent('deepseek', 'coder') == '.deepseek-coder-session'
    assert session_filename_for_agent('dsh', 'harness') == '.dsh-harness-session'
    assert session_filename_for_agent('mimo', 'mimoer') == '.mimo-mimoer-session'
    assert session_filename_for_agent('qwen', 'qwen1') == '.qwen-qwen1-session'
    assert session_filename_for_agent('qoder', 'qoder1') == '.qoder-qoder1-session'
    assert session_filename_for_agent('qoderclicn', 'qoderclicn1') == '.qoderclicn-qoderclicn1-session'
    assert session_filename_for_agent('cursor', 'cursor1') == '.cursor-cursor1-session'
    assert session_filename_for_agent('copilot', 'copilot1') == '.copilot-copilot1-session'
    assert session_filename_for_agent('crush', 'crush1') == '.crush-crush1-session'
    assert session_filename_for_agent('grok', 'grok1') == '.grok-grok1-session'
    assert session_filename_for_agent('kiro', 'kiro1') == '.kiro-kiro1-session'
    assert session_filename_for_agent('pi', 'pi1') == '.pi-pi1-session'
    assert session_filename_for_agent('omp', 'omp1') == '.omp-omp1-session'
    assert session_filename_for_agent('zai', 'zai1') == '.zai-zai1-session'
    assert session_filename_for_agent('grok', 'grok1') == '.grok-grok1-session'
