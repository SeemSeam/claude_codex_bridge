from __future__ import annotations

import pytest

from agents.config_loader_runtime.common import ConfigValidationError
from ccbd.services.project_namespace_runtime import build_namespace_topology_plan
from agents.config_loader_runtime.parsing_runtime.validation import validate_project_config
from agents.config_loader_runtime.parsing_runtime.workflow_v3 import _parse_runtime_mux_backend_v3


def _v2_doc(runtime_payload) -> dict:
    doc = {
        'version': 2,
        'entry_window': 'main',
        'windows': {'main': 'agent1:codex'},
    }
    if runtime_payload is not None:
        doc['runtime'] = runtime_payload
    return doc


def test_v2_runtime_mux_backend_herdr() -> None:
    cfg = validate_project_config(_v2_doc({'mux': {'backend': 'herdr'}}))
    assert cfg.runtime_mux_backend == 'herdr'


def test_v2_runtime_mux_backend_absent() -> None:
    cfg = validate_project_config(_v2_doc(None))
    assert cfg.runtime_mux_backend is None
    # 显式 null mux 视为 absent（raw_mux is None 分支）
    cfg2 = validate_project_config(_v2_doc({'mux': None}))
    assert cfg2.runtime_mux_backend is None


def test_v2_herdr_with_sidebar_off_disables_sidebar_projection(tmp_path) -> None:
    project_root = tmp_path / 'repo'
    config_dir = project_root / '.ccb'
    config_dir.mkdir(parents=True)
    config_path = config_dir / 'ccb.config'
    config_path.write_text(
        '''version = 2
entry_window = "main"

[runtime.mux]
backend = "herdr"

[windows]
main = "agent1:codex"

[ui.sidebar]
mode = "off"
''',
        encoding='utf-8',
    )

    from agents.config_loader_runtime.io_runtime.documents import load_project_config

    result = load_project_config(project_root, include_loop_overlays=False)
    assert result.config.runtime_mux_backend == 'herdr'

    plan = build_namespace_topology_plan(
        result.config,
        ccbd_socket_path='/tmp/ccbd.sock',
        project_root=str(project_root),
    )
    assert plan.sidebar_enabled is False
    assert plan.windows[0].sidebar is None
    assert plan.windows[0].realized_layout == 'agent1:codex'


@pytest.mark.parametrize(
    'payload',
    [
        {'mux': {'backend': 'rmux'}},
        {'mux': {'backend': 'other'}},
        {'mux': {'backend': 'herdr', 'x': 1}},
        {'foo': 1},
        'not-a-mapping',
    ],
)
def test_v2_runtime_mux_backend_rejects_invalid(payload) -> None:
    with pytest.raises(ConfigValidationError):
        validate_project_config(_v2_doc(payload))


# --- VA-5: unknown runtime.* fields fail-closed ---

@pytest.mark.parametrize(
    'runtime_payload',
    [
        {'mux': {'backend': 'herdr'}, 'other_key': 1},
        {'unknown_section': {'x': 1}},
        {'mux': {'backend': 'herdr', 'unknown_field': 'value'}},
    ],
)
def test_v2_unknown_runtime_fields_rejected(runtime_payload) -> None:
    with pytest.raises(ConfigValidationError):
        validate_project_config(_v2_doc(runtime_payload))


# --- VA-6: 非 Windows 环境 + runtime.mux.backend fail-closed ---

def test_herdr_selection_blocked_by_non_windows_platform_gate() -> None:
    """非 Windows platform gate 应阻止 herdr 后端选择。"""
    from terminal_runtime.backend_resolver import resolve_mux_backend_v2

    non_windows_gate = {
        'supported': False,
        'os_platform': 'linux',
        'cpu_arch': 'x64',
        'python_bitness': '64bit',
        'is_wsl': False,
    }
    result = resolve_mux_backend_v2(
        requested_backend='herdr',
        source='user_config',
        platform_gate=non_windows_gate,
        capability_report=None,
        capability_report_ref=None,
    )
    assert result.get('blocked') is True
    assert result.get('failure_reason') in {
        'platform-gate-blocked',
        'herdr-capability-missing',
        'unsupported-capability',
    }
    assert result.get('effective_backend') is None
    assert result.get('fallback_used') is False


def test_herdr_config_accepted_on_windows_with_capability() -> None:
    """Windows + capability evidence → herdr 后端选择成功。

    注：完整能力矩阵需要所有 facade capabilities（workspace_create 等），
    此处仅验证 selection flow 可达到非 blocked 分支。
    """
    from terminal_runtime.backend_resolver import resolve_mux_backend_v2
    from platforms.windows.herdr.runtime.capabilities import (
        _CORE_REQUIRED_CAPABILITIES,
        _KNOWN_CAPABILITIES,
    )

    windows_gate = {
        'supported': True, 'os_platform': 'win32', 'cpu_arch': 'x64',
        'python_bitness': '64bit', 'is_wsl': False,
    }
    # 构造包含所有 facade capabilities + 元数据字段的最小 supported 报告
    all_supported = {name: 'supported' for name in _KNOWN_CAPABILITIES}
    capability = {
        'backend_impl': 'herdr',
        'command_status': dict(all_supported),
        'semantic_status': dict(all_supported),
        'windows_beta_gaps': [],
        'blocking_gaps': [],
        'source_ref': 'test/ref',
        'adapter_recommendation': 'continue',
        'verdict': 'pass',
        'failure_class': '',
    }
    result = resolve_mux_backend_v2(
        requested_backend='herdr',
        source='user_config',
        platform_gate=windows_gate,
        capability_report=capability,
        capability_report_ref='test/ref',
    )
    assert result.get('blocked') is not True
    assert result.get('effective_backend') == 'herdr'
    assert result.get('backend_family') == 'herdr-native'


# --- VA-3: herdr selection fails when herdr server unavailable ---

def test_herdr_selection_blocked_without_capability_evidence() -> None:
    """缺 capability evidence → herdr 后端选择 fail-closed，不 fallback tmux。"""
    from terminal_runtime.backend_resolver import resolve_mux_backend_v2

    windows_gate = {
        'supported': True,
        'os_platform': 'win32',
        'cpu_arch': 'x64',
        'python_bitness': '64bit',
        'is_wsl': False,
    }
    result = resolve_mux_backend_v2(
        requested_backend='herdr',
        source='user_config',
        platform_gate=windows_gate,
        capability_report=None,  # no evidence
        capability_report_ref=None,
    )
    assert result.get('blocked') is True
    assert result.get('failure_reason') == 'herdr-capability-missing'
    assert result.get('effective_backend') is None
    assert result.get('fallback_used') is False  # D5: 不静默回退


def test_herdr_selection_blocked_by_capability_gaps() -> None:
    """blocking_gaps 非空 → herdr 后端选择 fail-closed。"""
    from terminal_runtime.backend_resolver import resolve_mux_backend_v2

    windows_gate = {
        'supported': True,
        'os_platform': 'win32',
        'cpu_arch': 'x64',
        'python_bitness': '64bit',
        'is_wsl': False,
    }
    capability = {
        'backend_impl': 'herdr',
        'command_status': {'session_attach': 'supported', 'pane_spawn': 'supported'},
        'semantic_status': {'session_attach': 'supported', 'pane_spawn': 'supported'},
        'blocking_gaps': ['pane_split_unavailable'],
        'source_ref': 'test/ref',
    }
    result = resolve_mux_backend_v2(
        requested_backend='auto',
        source='platform_default',
        platform_gate=windows_gate,
        capability_report=capability,
        capability_report_ref='test/ref',
    )
    assert result.get('blocked') is True
    assert result.get('failure_reason') == 'unsupported-capability'


# v3 完整 config 校验依赖 rolepack 安装（测试基础设施），此处直接测 mux 解析 helper。
# helper 的 raw 是 workflow.runtime 内容（含 'mux' 键），不是 mux 内容本身。
def test_v3_runtime_mux_backend_herdr() -> None:
    assert (
        _parse_runtime_mux_backend_v3({'mux': {'backend': 'herdr'}}, path='workflow.runtime.mux')
        == 'herdr'
    )


def test_v3_runtime_mux_backend_absent() -> None:
    assert _parse_runtime_mux_backend_v3({}, path='workflow.runtime.mux') is None
    # 显式 null mux 视为 absent（mux_value is None 分支，与 v2 对齐）
    assert _parse_runtime_mux_backend_v3({'mux': None}, path='workflow.runtime.mux') is None


@pytest.mark.parametrize(
    'payload',
    [
        {'mux': {'backend': 'rmux'}},
        {'mux': {'backend': 'other'}},
        {'mux': {'backend': 'herdr', 'x': 1}},
    ],
)
def test_v3_runtime_mux_backend_rejects_invalid(payload) -> None:
    with pytest.raises(ConfigValidationError):
        _parse_runtime_mux_backend_v3(payload, path='workflow.runtime.mux')


# ═══════════════════════════════════════════════════════════════════
# VA-1: 无 runtime.mux 配置 → env 检测回退
# ═══════════════════════════════════════════════════════════════════


def test_va1_config_absence_clears_env_and_falls_to_detection(monkeypatch) -> None:
    """VA-1: 无 runtime.mux 配置时 env var 不被设置，get_backend 回退到终端检测。

    验证链条：config 缺失 → _propagate_runtime_mux_backend(None)
    → pop CCB_RUNTIME_MUX_BACKEND → get_backend() 因无 env var
    回退到 detect_terminal()。
    """
    import os

    from agents.config_loader_runtime.io_runtime.documents import _propagate_runtime_mux_backend
    import terminal_runtime.api as terminal_api

    # 清除 env var 和模块级缓存
    monkeypatch.delenv('CCB_RUNTIME_MUX_BACKEND', raising=False)
    monkeypatch.setattr(terminal_api, '_backend_cache', None)
    monkeypatch.setattr(terminal_api, '_backend_cache_key', None)
    monkeypatch.setattr(terminal_api, '_backend_config_preference', None)

    # 模拟 config 缺失路径
    _propagate_runtime_mux_backend(None)

    # env var 被清除（设计保证不残留）
    assert 'CCB_RUNTIME_MUX_BACKEND' not in os.environ

    # 模拟终端检测被触发
    detect_calls = []

    def _fake_detect_terminal():
        detect_calls.append(1)
        return 'tmux'

    monkeypatch.setattr(terminal_api, 'detect_terminal', _fake_detect_terminal)
    monkeypatch.setattr(terminal_api, '_herdr_runtime_configured', lambda: False)

    # 调用 get_backend（无显式 terminal_type）
    terminal_api.get_backend()

    # 因无 env var 也无 config preference，回退到终端检测
    assert detect_calls == [1], (
        'VA-1: get_backend 应在 env var / config preference 均为 None '
        '时调用 detect_terminal()'
    )


# ═══════════════════════════════════════════════════════════════════
# VA-2.1: _runtime_ref_prefix 返回正确后端前缀
# ═══════════════════════════════════════════════════════════════════


@pytest.mark.parametrize(
    'namespace_backend_impl, assigned_pane_ref, expected_prefix',
    [
        ('herdr', None, 'mux'),
        ('HERDR', None, 'mux'),                      # 大小写不敏感
        ('herdr', {}, 'mux'),
        ('tmux', None, 'tmux'),
        ('rmux', None, 'tmux'),
        (None, {'backend_impl': 'herdr'}, 'mux'),
        (None, {'backend_impl': 'HERDR'}, 'mux'),
        (None, {'backend_impl': 'tmux'}, 'tmux'),
        (None, None, 'tmux'),                         # 默认
        (None, {}, 'tmux'),                           # 空 dict 无 backend_impl key
        (None, {'backend_impl': None}, 'tmux'),
    ],
)
def test_va2_runtime_ref_prefix(
    namespace_backend_impl, assigned_pane_ref, expected_prefix,
) -> None:
    """VA-2: _runtime_ref_prefix 按后端类型返回 'mux' 或 'tmux'。

    优先 namespace_backend_impl，其次 assigned_pane_ref['backend_impl']，
    两个都无/空时默认 'tmux'。
    """
    from ccbd.start_runtime.agent_runtime import _runtime_ref_prefix

    assert _runtime_ref_prefix(namespace_backend_impl, assigned_pane_ref) == expected_prefix


def test_va2_runtime_ref_prefix_composes_with_pane_id() -> None:
    """VA-2: _runtime_ref_prefix 返回值与 pane_id 组合成 runtime_ref。

    验证 'mux:%pane_id' 和 'tmux:%pane_id' 两种模板正确复合。
    """
    from ccbd.start_runtime.agent_runtime import _runtime_ref_prefix

    herdr_prefix = _runtime_ref_prefix('herdr', None)
    assert herdr_prefix == 'mux'
    assert f'{herdr_prefix}:%42' == 'mux:%42'

    tmux_prefix = _runtime_ref_prefix(None, None)
    assert tmux_prefix == 'tmux'
    assert f'{tmux_prefix}:%42' == 'tmux:%42'


# ═══════════════════════════════════════════════════════════════════
# VA-2.2: env var 传播链
# ═══════════════════════════════════════════════════════════════════


def test_va2_propagate_runtime_mux_backend_sets_env_for_herdr(monkeypatch) -> None:
    """VA-2: _propagate_runtime_mux_backend 将 herdr 写入 CCB_RUNTIME_MUX_BACKEND。"""
    import os

    from agents.config_loader_runtime.io_runtime.documents import _propagate_runtime_mux_backend

    monkeypatch.delenv('CCB_RUNTIME_MUX_BACKEND', raising=False)

    class _FakeConfig:
        runtime_mux_backend = 'herdr'

    _propagate_runtime_mux_backend(_FakeConfig())
    assert os.environ.get('CCB_RUNTIME_MUX_BACKEND') == 'herdr'


def test_va2_propagate_runtime_mux_backend_clears_env_for_none(monkeypatch) -> None:
    """VA-2: _propagate_runtime_mux_backend(None) 清除 env var。"""
    import os

    from agents.config_loader_runtime.io_runtime.documents import _propagate_runtime_mux_backend

    monkeypatch.setenv('CCB_RUNTIME_MUX_BACKEND', 'herdr')
    _propagate_runtime_mux_backend(None)
    assert 'CCB_RUNTIME_MUX_BACKEND' not in os.environ


def test_va2_get_backend_reads_env_var(monkeypatch) -> None:
    """VA-2: get_backend() 读取 CCB_RUNTIME_MUX_BACKEND 作为 env_pref。

    env var 设为 herdr 时，backend 解析路径应被触发。
    不要求真实 herdr 环境——验证的是 env var 被读取并传递。
    """
    import terminal_runtime.api as terminal_api

    # 清除缓存
    monkeypatch.setattr(terminal_api, '_backend_cache', None)
    monkeypatch.setattr(terminal_api, '_backend_cache_key', None)
    monkeypatch.setattr(terminal_api, '_backend_config_preference', None)

    # 设置 env var
    monkeypatch.setenv('CCB_RUNTIME_MUX_BACKEND', 'herdr')

    # 捕获 terminal_type 以验证 env var 被消费
    captured = []

    def _fake_resolve_backend(*, terminal_type, **kwargs):
        captured.append(terminal_type)
        return None  # 后端创建失败不重要，只验证 terminal_type 被传递

    monkeypatch.setattr(terminal_api, '_resolve_backend', _fake_resolve_backend)

    terminal_api.get_backend()
    assert captured == ['herdr'], (
        'VA-2: get_backend 应将 CCB_RUNTIME_MUX_BACKEND=herdr '
        '作为 terminal_type 传递到 _resolve_backend'
    )


# ═══════════════════════════════════════════════════════════════════
# VA-2.3: overlay 保留 runtime_mux_backend
# ═══════════════════════════════════════════════════════════════════


def test_va2_loop_overlay_preserves_runtime_mux_backend(tmp_path) -> None:
    """VA-2: apply_loop_capacity_overlays 传递 runtime_mux_backend，不丢失。"""
    from agents.config_loader_runtime.loop_overlays import apply_loop_capacity_overlays
    from agents.config_loader_runtime.parsing_runtime.validation import validate_project_config

    doc = {
        'version': 2,
        'entry_window': 'main',
        'windows': {'main': 'agent1:codex'},
        'runtime': {'mux': {'backend': 'herdr'}},
    }
    config = validate_project_config(doc)
    assert config.runtime_mux_backend == 'herdr'

    # 应用 loop overlay（tmp_path 无活跃状态，直接返回原 config）
    overlayed = apply_loop_capacity_overlays(config, tmp_path)
    assert overlayed.runtime_mux_backend == 'herdr', (
        'VA-2: loop overlay 必须保留 runtime_mux_backend'
    )


def test_va2_dynamic_agent_overlay_preserves_runtime_mux_backend(tmp_path) -> None:
    """VA-2: apply_dynamic_agent_overlays 传递 runtime_mux_backend，不丢失。"""
    from agents.config_loader_runtime.dynamic_agent_overlays import apply_dynamic_agent_overlays
    from agents.config_loader_runtime.parsing_runtime.validation import validate_project_config

    doc = {
        'version': 2,
        'entry_window': 'main',
        'windows': {'main': 'agent1:codex'},
        'runtime': {'mux': {'backend': 'herdr'}},
    }
    config = validate_project_config(doc)
    assert config.runtime_mux_backend == 'herdr'

    overlayed = apply_dynamic_agent_overlays(config, tmp_path)
    assert overlayed.runtime_mux_backend == 'herdr', (
        'VA-2: dynamic agent overlay 必须保留 runtime_mux_backend'
    )


def test_va2_load_project_config_preserves_runtime_mux_backend_through_overlays(
    tmp_path,
    monkeypatch,
) -> None:
    """VA-2: e2e — load_project_config 经过两个 overlay 后仍保留 runtime_mux_backend。

    同时验证 _propagate_runtime_mux_backend 将值写入 CCB_RUNTIME_MUX_BACKEND。
    """
    import os
    from pathlib import Path

    from agents.config_loader_runtime.io_runtime.documents import load_project_config

    project_root = tmp_path / 'repo'
    config_dir = project_root / '.ccb'
    config_dir.mkdir(parents=True)
    config_path = config_dir / 'ccb.config'

    config_path.write_text(
        '''version = 2
entry_window = "main"

[windows]
main = "agent1:codex"

[runtime.mux]
backend = "herdr"
''',
        encoding='utf-8',
    )

    import terminal_runtime.api as terminal_api

    monkeypatch.delenv('CCB_RUNTIME_MUX_BACKEND', raising=False)
    monkeypatch.setattr(terminal_api, '_backend_config_preference', None)
    result = load_project_config(project_root, include_loop_overlays=True)

    # 断言 1: config 保留
    assert result.config.runtime_mux_backend == 'herdr', (
        'VA-2: load_project_config 在 overlay 后必须保留 runtime_mux_backend'
    )

    # 断言 2: env var 传播
    assert os.environ.get('CCB_RUNTIME_MUX_BACKEND') == 'herdr', (
        'VA-2: _propagate_runtime_mux_backend 应将 herdr 传播到 CCB_RUNTIME_MUX_BACKEND'
    )


# ═══════════════════════════════════════════════════════════════════
# VA-7: 各 provider 的 launch_mode 矩阵
# ═══════════════════════════════════════════════════════════════════

_VA7_EXPECTED_LAUNCH_MODES = {
    'codex': 'codex_tmux',
    'claude': 'simple_tmux',
    'gemini': 'simple_tmux',
    'opencode': 'simple_tmux',
    'droid': 'simple_tmux',
    'agy': 'simple_tmux',
    'kimi': 'simple_tmux',
    'deepseek': 'simple_tmux',
    'dsh': 'simple_tmux',
    'mimo': 'simple_tmux',
    'qwen': 'simple_tmux',
    'qoder': 'simple_tmux',
    'qoderclicn': 'simple_tmux',
    'cursor': 'simple_tmux',
    'copilot': 'simple_tmux',
    'crush': 'simple_tmux',
    'grok': 'simple_tmux',
    'kiro': 'simple_tmux',
    'pi': 'simple_tmux',
    'omp': 'simple_tmux',
    'zai': 'simple_tmux',
}


def test_va7_all_providers_have_correct_launch_mode() -> None:
    """VA-7: 21 providers — codex 唯一 codex_tmux，其余 20 均 simple_tmux。

    验证 launch_mode 值有效且与预期注册表一致。
    """
    from provider_core.registry import build_default_runtime_launcher_map

    launchers = build_default_runtime_launcher_map(include_optional=True)

    # 断言 1: 恰好 21 个 provider
    assert len(launchers) == 21, (
        f'expected 21 providers with runtime launchers, got {len(launchers)}'
    )

    # 断言 2: 每个 provider 的 launch_mode 与预期一致
    for provider, expected_mode in _VA7_EXPECTED_LAUNCH_MODES.items():
        launcher = launchers.get(provider)
        assert launcher is not None, f'VA-7: {provider} missing from runtime launcher map'
        assert launcher.launch_mode == expected_mode, (
            f'VA-7: {provider} expected launch_mode={expected_mode}, '
            f'got {launcher.launch_mode}'
        )

    # 断言 3: 无多余的 provider（防御：未来新增时此测试会提醒更新）
    assert set(launchers) == set(_VA7_EXPECTED_LAUNCH_MODES), (
        'VA-7: runtime launcher map 包含未预期的 provider，'
        '请更新 _VA7_EXPECTED_LAUNCH_MODES'
    )


def test_va7_codex_is_only_codex_tmux_provider() -> None:
    """VA-7: codex 是唯一的 codex_tmux provider，无第二个。"""
    from provider_core.registry import build_default_runtime_launcher_map

    launchers = build_default_runtime_launcher_map(include_optional=True)
    codex_tmux_providers = [
        p for p, l in launchers.items() if l.launch_mode == 'codex_tmux'
    ]
    assert codex_tmux_providers == ['codex'], (
        f'VA-7: 预期只有 codex 使用 codex_tmux，实际: {codex_tmux_providers}'
    )


def test_va7_launch_mode_is_valid_literal() -> None:
    """VA-7: 所有 launch_mode 值均为有效 Literal（防御 schema 漂移）。"""
    from provider_core.registry import build_default_runtime_launcher_map

    VALID_MODES = {'simple_tmux', 'codex_tmux'}
    launchers = build_default_runtime_launcher_map(include_optional=True)

    for provider, launcher in launchers.items():
        assert launcher.launch_mode in VALID_MODES, (
            f'VA-7: {provider} 的 launch_mode={launcher.launch_mode!r} '
            f'不是有效的 Literal 值 {VALID_MODES}'
        )


# ═══════════════════════════════════════════════════════════════════
# §9 第4步: config 优先级 + provider fail-closed
# ═══════════════════════════════════════════════════════════════════


def test_config_priority_sets_backend_config_preference(monkeypatch) -> None:
    """config runtime.mux.backend=herdr → set_backend_config_preference('herdr')。

    design D2：config 声明式偏好直接设置模块级 _backend_config_preference，
    在 get_backend() 优先级链中高于 env var。
    """
    import os

    from agents.config_loader_runtime.io_runtime.documents import _propagate_runtime_mux_backend
    import terminal_runtime.api as terminal_api

    monkeypatch.delenv('CCB_RUNTIME_MUX_BACKEND', raising=False)
    monkeypatch.setattr(terminal_api, '_backend_config_preference', None)

    class _FakeConfig:
        runtime_mux_backend = 'herdr'

    _propagate_runtime_mux_backend(_FakeConfig())

    assert os.environ.get('CCB_RUNTIME_MUX_BACKEND') == 'herdr'
    assert terminal_api._backend_config_preference == 'herdr', (
        'config herdr → _backend_config_preference 应为 herdr'
    )


def test_config_priority_clears_backend_config_preference_on_none(monkeypatch) -> None:
    """config 缺失 → set_backend_config_preference(None) 清除偏好。"""
    from agents.config_loader_runtime.io_runtime.documents import _propagate_runtime_mux_backend
    import terminal_runtime.api as terminal_api

    monkeypatch.setattr(terminal_api, '_backend_config_preference', 'herdr')
    monkeypatch.setenv('CCB_RUNTIME_MUX_BACKEND', 'herdr')

    _propagate_runtime_mux_backend(None)

    assert terminal_api._backend_config_preference is None, (
        'config 缺失 → _backend_config_preference 应为 None'
    )


def test_herdr_provider_gate_allows_codex(monkeypatch) -> None:
    """codex provider 在显式 config herdr 下允许通过。

    显式 opt-in：CCB_RUNTIME_MUX_BACKEND=herdr + codex provider → 不抛异常。
    """
    from cli.services.runtime_launch_runtime.ensure import _is_herdr_runtime_launch

    monkeypatch.setenv('CCB_RUNTIME_MUX_BACKEND', 'herdr')
    result = _is_herdr_runtime_launch(
        namespace_backend_impl='herdr',
        assigned_pane_ref={'backend_impl': 'herdr', 'pane_id': 'w1:p1'},
    )
    assert result is True
    # codex: _explicit_herdr=True and spec.provider=='codex' → gate 不触发


def test_herdr_provider_gate_requires_explicit_config(monkeypatch) -> None:
    """gate 前提：显式 config herdr（_is_herdr_runtime_launch + env）成立。

    gate 仅当 CCB_RUNTIME_MUX_BACKEND=herdr（config 显式声明）时触发。
    自动检测的 herdr 不触发此 gate（设计 I-3：fail-closed 仅针对显式 opt-in）。
    具体 provider 是否 fail-closed 见 _herdr_explicit_gate_error 测试。
    """
    from cli.services.runtime_launch_runtime.ensure import _is_herdr_runtime_launch
    import os

    monkeypatch.setenv('CCB_RUNTIME_MUX_BACKEND', 'herdr')
    is_herdr = _is_herdr_runtime_launch(
        namespace_backend_impl='herdr',
        assigned_pane_ref={'backend_impl': 'herdr', 'pane_id': 'w1:p1'},
    )
    assert is_herdr is True
    assert os.environ.get('CCB_RUNTIME_MUX_BACKEND') == 'herdr'
    # gate 条件：is_herdr=True + CCB_RUNTIME_MUX_BACKEND=herdr + provider 不在
    # _HERDR_NATIVE_VERIFIED_PROVIDERS（allow-list）→ fail-closed


def test_herdr_provider_gate_allows_non_codex_when_auto_detected(monkeypatch) -> None:
    """herdr 自动检测（无显式 config）→ 非 codex provider 不触发 gate。

    CCB_RUNTIME_MUX_BACKEND 未设置 → 自动检测不会 fail-closed 非 codex provider。
    """
    from cli.services.runtime_launch_runtime.ensure import _is_herdr_runtime_launch
    import os

    monkeypatch.delenv('CCB_RUNTIME_MUX_BACKEND', raising=False)
    is_herdr = _is_herdr_runtime_launch(
        namespace_backend_impl='herdr',
        assigned_pane_ref={'backend_impl': 'herdr', 'pane_id': 'w1:p1'},
    )
    assert is_herdr is True
    assert 'CCB_RUNTIME_MUX_BACKEND' not in os.environ
    # _explicit_herdr = False（env var 未设置） → gate 不触发


# --- design I-3: 显式 herdr gate allow-list（_HERDR_NATIVE_VERIFIED_PROVIDERS） ---


def test_herdr_explicit_gate_allows_verified_providers() -> None:
    """已验证 provider（codex/claude）通过显式 herdr gate。"""
    from cli.services.runtime_launch_runtime.ensure import _herdr_explicit_gate_error

    assert _herdr_explicit_gate_error('codex') is None
    assert _herdr_explicit_gate_error('claude') is None


def test_herdr_explicit_gate_blocks_unverified_provider() -> None:
    """未验证 provider（gemini 等）在显式 herdr 下 fail-closed。"""
    from cli.services.runtime_launch_runtime.ensure import _herdr_explicit_gate_error

    reason = _herdr_explicit_gate_error('gemini')
    assert reason is not None
    assert 'gemini' in reason
    assert 'does not support herdr-native launch' in reason
    dsh_reason = _herdr_explicit_gate_error('dsh')
    assert dsh_reason is not None
    assert 'dsh' in dsh_reason
    assert 'does not support herdr-native launch' in dsh_reason


def test_herdr_explicit_gate_is_case_insensitive() -> None:
    """provider 名大小写不敏感。"""
    from cli.services.runtime_launch_runtime.ensure import _herdr_explicit_gate_error

    assert _herdr_explicit_gate_error('CODEX') is None
    assert _herdr_explicit_gate_error(' Claude ') is None


def test_herdr_gate_raises_on_unverified_provider_via_ensure(monkeypatch) -> None:
    """ensure_agent_runtime 对未验证 provider + 显式 herdr → raise fail-closed。"""
    from types import SimpleNamespace

    from agents.models import RuntimeMode
    from cli.services.runtime_launch_runtime.ensure import ensure_agent_runtime

    monkeypatch.setenv('CCB_RUNTIME_MUX_BACKEND', 'herdr')
    spec = SimpleNamespace(runtime_mode=RuntimeMode.PANE_BACKED, provider='gemini', name='agent1')
    with pytest.raises(RuntimeError, match='does not support herdr-native launch'):
        ensure_agent_runtime(
            context=None,
            command=None,
            spec=spec,
            plan=None,
            binding=None,
            runtime_launch_result_cls=None,
            binding_runtime_alive_fn=None,
            provider_executable_fn=None,
            cleanup_stale_tmux_binding_fn=None,
            launch_runtime_fn=None,
            resolve_agent_binding_fn=None,
            namespace_backend_impl='herdr',
        )


def test_herdr_gate_allows_verified_claude_via_ensure(monkeypatch) -> None:
    """ensure_agent_runtime 对已验证 claude + 显式 herdr → 放行到 launch（不 raise gate）。"""
    import sys
    from types import SimpleNamespace

    from agents.models import RuntimeMode
    from cli.services.runtime_launch_runtime.ensure import ensure_agent_runtime

    monkeypatch.setenv('CCB_RUNTIME_MUX_BACKEND', 'herdr')

    class _Sentinel(Exception):
        pass

    def _launch_fn(*args, **kwargs):
        raise _Sentinel('passed gate')

    spec = SimpleNamespace(runtime_mode=RuntimeMode.PANE_BACKED, provider='claude', name='agent1')
    with pytest.raises(_Sentinel, match='passed gate'):
        ensure_agent_runtime(
            context=None,
            command=None,
            spec=spec,
            plan=None,
            binding=None,
            runtime_launch_result_cls=SimpleNamespace,
            binding_runtime_alive_fn=lambda binding: False,
            provider_executable_fn=lambda provider: sys.executable,
            cleanup_stale_tmux_binding_fn=lambda binding: None,
            launch_runtime_fn=_launch_fn,
            resolve_agent_binding_fn=lambda **kwargs: SimpleNamespace(),
            namespace_backend_impl='herdr',
        )
