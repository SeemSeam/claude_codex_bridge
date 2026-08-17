from __future__ import annotations

import base64
import hashlib
import json
import os
import re
import sys
import threading
import time
from pathlib import Path
from types import SimpleNamespace
from urllib.error import HTTPError
from urllib.parse import parse_qs, urlencode, urlparse
from urllib.request import Request, urlopen

import pytest

import cli.services.config_ui as config_ui_module
from cli.models import ParsedConfigUiCommand
from cli.services.config_restart_intent import load_config_restart_intent
from cli.services.config_ui import (
    config_ui_asset_path,
    config_ui_provider_capabilities,
    open_config_ui_url,
    prepare_config_ui,
)
from cli.services.config_ui_settings import resolve_config_ui_settings
from agents.config_loader import ConfigValidationError
from ccbd.services.project_namespace_state import ProjectNamespaceState, ProjectNamespaceStateStore
from storage.paths import PathLayout


def _context(project_root: Path):
    return SimpleNamespace(project=SimpleNamespace(project_root=project_root))


def test_config_ui_asset_is_packaged_source_content() -> None:
    path = config_ui_asset_path()
    repo_root = Path(__file__).resolve().parents[1]

    assert path == repo_root / 'assets' / 'config_ui' / 'index.html'
    assert path.is_file()
    page = path.read_text(encoding='utf-8')
    assert '<html lang="en">' in page
    assert '<title>CCB Config Control Panel Demo</title>' in page
    assert 'id="staticDeletePane"' in page
    assert 'id="basicDeletePane"' in page
    assert 'function deleteSelectedPane()' in page
    assert 'id="themeSelect"' in page
    assert 'data-i18n="appearance"' in page
    assert 'function loadThemePreference()' in page
    assert 'function saveThemePreference(value)' in page
    assert 'apiJson("/api/theme"' in page
    assert 'document.documentElement.dataset.ccbTheme = rendered' in page
    assert 'id="historyScanBtn"' in page
    assert 'id="historyCleanupBtn"' in page
    assert 'function scanAgentHistory()' in page
    assert 'function cleanupAgentHistory()' in page
    assert '/api/storage/history' in page
    assert 'id="config-editor-section"' in page
    assert 'id="agent-session-storage"' in page
    assert 'id="observe-section"' not in page
    assert 'id="agent-communication-flow"' not in page
    assert 'id="commFlowPanel"' not in page
    assert 'id="pauseFlow"' not in page
    assert 'data-drawer="messageTrace"' not in page
    assert '2.8 GB' not in page
    assert 'data-i18n="deleteAll"' not in page
    match = re.search(r'CCB_MOBILE_ICON_DATA = "data:image/png;base64,([^"]+)"', page)
    assert match is not None
    embedded_icon = base64.b64decode(match.group(1))
    mobile_icon = (
        repo_root
        / 'mobile'
        / 'app'
        / 'android'
        / 'app'
        / 'src'
        / 'main'
        / 'res'
        / 'mipmap-mdpi'
        / 'ic_launcher.png'
    )
    assert embedded_icon == mobile_icon.read_bytes()


def test_config_ui_capabilities_expose_role_catalog_without_private_paths(monkeypatch: pytest.MonkeyPatch) -> None:
    import rolepacks.sources as role_sources

    monkeypatch.setattr(
        role_sources,
        'role_catalog_status',
        lambda **_: (
            {
                'role_id': 'agentroles.mother',
                'name': 'Role Mother',
                'description': 'Role design and source audit',
                'version': '0.2.3',
                'installed_version': '0.2.3',
                'status': 'current',
                'source': 'agentroles',
                'path': '/private/role/source',
                'digest': 'sha256:private',
            },
        ),
    )

    payload = config_ui_provider_capabilities(environ={})

    assert payload['roles'] == [
        {
            'role_id': 'agentroles.mother',
            'name': 'Role Mother',
            'description': 'Role design and source audit',
            'version': '0.2.3',
            'installed_version': '0.2.3',
            'status': 'current',
            'source': 'agentroles',
        }
    ]


def test_config_ui_role_catalog_never_downloads_missing_default(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import rolepacks.sources as role_sources

    calls: list[dict[str, object]] = []

    def fake_role_catalog_status(**kwargs):
        calls.append(dict(kwargs))
        return ()

    monkeypatch.setattr(role_sources, 'role_catalog_status', fake_role_catalog_status)

    assert config_ui_module._config_ui_role_catalog() == ()
    assert calls == [
        {
            'refresh_default': False,
            'download_missing_default': False,
        }
    ]


def test_config_ui_layout_canvas_can_fill_stretched_workspace_column() -> None:
    page = config_ui_asset_path().read_text(encoding='utf-8')

    def css_rule(selector: str) -> str:
        match = re.search(
            rf'^\s*{re.escape(selector)}\s*\{{(?P<body>.*?)^\s*\}}',
            page,
            flags=re.MULTILINE | re.DOTALL,
        )
        assert match is not None
        return match.group('body')

    assert 'display: grid' in css_rule('.layout-card')
    assert 'height: 100%' in css_rule('.layout-shell')
    preview_rule = css_rule('.layout-preview')
    assert 'height: 100%' in preview_rule
    assert 'max-height: none' in preview_rule


def test_config_ui_serves_token_guarded_page_and_project_session(tmp_path: Path) -> None:
    project_root = tmp_path / 'repo'
    config_path = project_root / '.ccb' / 'ccb.config'
    config_path.parent.mkdir(parents=True)
    config_path.write_text('agent1:codex\n', encoding='utf-8')
    page = tmp_path / 'index.html'
    page.write_text('<!doctype html><title>settings</title>', encoding='utf-8')
    handle = prepare_config_ui(
        _context(project_root),
        ParsedConfigUiCommand(project=None),
        asset_path=page,
        token='test-token',
        idle_timeout_s=0.3,
    )
    assert 'test-token' not in json.dumps(handle.summary)
    assert handle.summary['url'].endswith('/')
    assert handle.summary['bind'] == 'loopback'
    time.sleep(0.35)
    thread = threading.Thread(target=handle.serve_forever, daemon=True)
    thread.start()

    try:
        with urlopen(handle.url, timeout=2) as response:
            assert response.status == 200
            assert b'<title>settings</title>' in response.read()

        parsed = urlparse(handle.url)
        token = parse_qs(parsed.query)['token'][0]
        session_url = f'{parsed.scheme}://{parsed.netloc}/api/session?{urlencode({"token": token})}'
        with urlopen(session_url, timeout=2) as response:
            payload = json.loads(response.read())
        assert payload['schema_version'] == 2
        assert payload['mode'] == 'editor'
        assert payload['project_root'] == str(project_root.resolve())
        assert payload['config_path'] == str(config_path.resolve())
        assert payload['config_exists'] is True
        assert payload['runtime_summary']['os_platform'] in {'windows', 'linux', 'darwin'}
        assert payload['runtime_summary']['effective_mux_backend'] is None

        capabilities_url = f'{parsed.scheme}://{parsed.netloc}/api/capabilities?{urlencode({"token": token})}'
        with urlopen(capabilities_url, timeout=2) as response:
            capabilities = json.loads(response.read())
        assert capabilities['schema_version'] == 1
        assert 'roles' in capabilities
        assert isinstance(capabilities['roles'], list)
        assert {provider['id'] for provider in capabilities['providers']} >= {
            'codex',
            'claude',
            'gemini',
            'deepseek',
            'dsh',
        }
        by_provider = {provider['id']: provider for provider in capabilities['providers']}
        assert by_provider['codex']['static_thinking'] is True
        assert by_provider['deepseek']['model_shortcut'] is True
        assert by_provider['deepseek']['api_shortcut'] is True
        assert by_provider['deepseek']['static_thinking'] is True
        assert by_provider['dsh']['model_shortcut'] is True
        assert by_provider['dsh']['api_shortcut'] is True
        assert by_provider['dsh']['static_thinking'] is True
        assert {
            model['id']: model['reasoning_levels']
            for model in by_provider['dsh']['models']
        } == {
            'deepseek-v4-flash': ['off', 'high', 'max'],
            'deepseek-v4-pro': ['off', 'high', 'max'],
        }
        assert {
            model['id']: model['reasoning_levels']
            for model in by_provider['deepseek']['models']
        } == {
            'deepseek-v4-pro': ['off', 'high', 'max'],
            'deepseek-v4-flash': ['off', 'high', 'max'],
        }

        with pytest.raises(HTTPError) as exc_info:
            urlopen(f'{parsed.scheme}://{parsed.netloc}/', timeout=2)
        assert exc_info.value.code == 403
    finally:
        handle.close()
        thread.join(timeout=2)
    assert not thread.is_alive()


def test_config_ui_capabilities_probe_cli_models_lazily(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    project_root = tmp_path / 'repo-lazy-capabilities'
    config_path = project_root / '.ccb' / 'ccb.config'
    config_path.parent.mkdir(parents=True)
    config_path.write_text('agent1:codex\n', encoding='utf-8')
    page = tmp_path / 'index.html'
    page.write_text('<!doctype html><title>settings</title>', encoding='utf-8')
    calls: list[str] = []

    def fake_provider_cli_models(program: str, _environ: dict[str, str]) -> list[str]:
        calls.append(program)
        if program == 'opencode':
            return ['openai/gpt-5.6-sol']
        if program == 'mimo':
            return ['xiaomi/mimo-v2.5-pro']
        return []

    monkeypatch.setattr(config_ui_module, '_provider_cli_models', fake_provider_cli_models)

    handle = prepare_config_ui(
        _context(project_root),
        ParsedConfigUiCommand(project=None),
        asset_path=page,
        token='test-token',
        idle_timeout_s=0.3,
    )
    assert calls == []
    thread = threading.Thread(target=handle.serve_forever)
    thread.start()

    try:
        capabilities = _get_json(handle.url, '/api/capabilities')
        providers = {provider['id']: provider for provider in capabilities['providers']}
        assert [model['id'] for model in providers['opencode']['models']] == ['openai/gpt-5.6-sol']
        assert [model['id'] for model in providers['mimo']['models']] == ['xiaomi/mimo-v2.5-pro']
        assert calls == ['opencode', 'mimo']
        _get_json(handle.url, '/api/capabilities')
        assert calls == ['opencode', 'mimo']
    finally:
        handle.close()
        thread.join(timeout=2)
    assert not thread.is_alive()


def test_config_ui_capabilities_endpoint_bounds_slow_cli_model_probe(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project_root = tmp_path / 'repo-slow-capabilities'
    config_path = project_root / '.ccb' / 'ccb.config'
    config_path.parent.mkdir(parents=True)
    config_path.write_text('agent1:codex\n', encoding='utf-8')
    page = tmp_path / 'index.html'
    page.write_text('<!doctype html><title>settings</title>', encoding='utf-8')
    calls: list[str] = []
    release_probe = threading.Event()
    probe_started = {
        'opencode': threading.Event(),
        'mimo': threading.Event(),
    }

    def slow_provider_cli_models(program: str, _environ: dict[str, str]) -> list[str]:
        calls.append(program)
        probe_started[program].set()
        release_probe.wait()
        return [f'{program}/slow-model']

    monkeypatch.setattr(config_ui_module, '_provider_cli_models', slow_provider_cli_models)
    monkeypatch.setattr(config_ui_module, '_CAPABILITIES_CLI_MODELS_BUDGET_S', 0.05)
    monkeypatch.setattr(config_ui_module, '_CAPABILITIES_CLI_MODELS_RETRY_S', 0.0)

    handle = prepare_config_ui(
        _context(project_root),
        ParsedConfigUiCommand(project=None),
        asset_path=page,
        token='test-token',
        idle_timeout_s=2.0,
    )
    thread = threading.Thread(target=handle.serve_forever)
    thread.start()

    try:
        started_at = time.monotonic()
        capabilities = _get_json(handle.url, '/api/capabilities')
        elapsed = time.monotonic() - started_at
        providers = {provider['id']: provider for provider in capabilities['providers']}
        assert elapsed < 0.75
        assert {provider['id'] for provider in capabilities['providers']} >= {'codex', 'opencode', 'mimo'}
        assert providers['opencode']['models'] == []
        assert providers['mimo']['models'] == []
        assert set(calls) == {'opencode', 'mimo'}
        assert all(started.is_set() for started in probe_started.values())

        release_probe.set()
        deadline = time.monotonic() + 2.0
        while True:
            capabilities = _get_json(handle.url, '/api/capabilities')
            providers = {provider['id']: provider for provider in capabilities['providers']}
            if providers['opencode']['models'] and providers['mimo']['models']:
                break
            assert time.monotonic() < deadline
        assert [model['id'] for model in providers['opencode']['models']] == ['opencode/slow-model']
        assert [model['id'] for model in providers['mimo']['models']] == ['mimo/slow-model']
        assert set(calls) == {'opencode', 'mimo'}
        _get_json(handle.url, '/api/capabilities')
        assert set(calls) == {'opencode', 'mimo'}
    finally:
        release_probe.set()
        handle.close()
        thread.join(timeout=2)
    assert not thread.is_alive()


def test_config_ui_session_projects_herdr_readonly_status(tmp_path: Path) -> None:
    project_root = tmp_path / 'repo-herdr'
    config_path = project_root / '.ccb' / 'ccb.config'
    config_path.parent.mkdir(parents=True)
    config_path.write_text('agent1:codex\n', encoding='utf-8')
    paths = PathLayout(project_root)
    ProjectNamespaceStateStore(paths).save(
        ProjectNamespaceState(
            project_id=paths.project_id,
            namespace_epoch=4,
            tmux_socket_path='',
            tmux_session_name='ccb-herdr',
            namespace_backend_family='herdr-native',
            backend_impl='herdr',
            namespace_id='workspace-1',
            namespace_session_name='ccb-herdr',
            namespace_ipc_kind='herdr_socket',
            namespace_ipc_ref='herdr://workspace-1',
            namespace_restore_token='raw-secret-token',
        )
    )
    context = SimpleNamespace(project=SimpleNamespace(project_root=project_root), paths=paths)
    payload = config_ui_module._config_ui_session_payload(
        context,
        project_root=project_root.resolve(),
        config_path=config_path.resolve(),
    )

    projection = payload['herdr_surface_projection']
    assert projection['backend_impl'] == 'herdr'
    assert projection['capability_status'] == 'partial'
    assert projection['support_tier_projection'] == 'experimental'
    assert projection['support_tier_projection_source'] == 'validation_pending'
    assert projection['evidence_refs']['namespace_ref']['namespace_id'] == 'workspace-1'
    assert payload['config_ui_readonly_status'] == {
        'status': 'blocked',
        'backend_impl': 'herdr',
        'reason': 'capability_status=partial',
        'degraded_next_action': None,
    }
    assert 'raw-secret-token' not in json.dumps(payload)


def test_config_ui_runtime_summary_reports_os_and_effective_mux_backend(tmp_path: Path) -> None:
    project_root = tmp_path / 'repo-mux'
    config_path = project_root / '.ccb' / 'ccb.config'
    config_path.parent.mkdir(parents=True)
    config_path.write_text(
        'version = 2\nentry_window = "main"\n\n[runtime.mux]\nbackend = "herdr"\n\n'
        '[windows]\nmain = "agent1:codex"\n',
        encoding='utf-8',
    )
    paths = PathLayout(project_root)
    context = SimpleNamespace(project=SimpleNamespace(project_root=project_root), paths=paths)
    payload = config_ui_module._config_ui_session_payload(
        context,
        project_root=project_root.resolve(),
        config_path=config_path.resolve(),
    )
    summary = payload['runtime_summary']
    assert summary['effective_mux_backend'] == 'herdr'
    assert summary['os_platform'] in {'windows', 'linux', 'darwin'}
    assert 'config_exists' in payload


def test_config_ui_runtime_summary_tolerates_missing_or_invalid_config(tmp_path: Path) -> None:
    project_root = tmp_path / 'repo-empty'
    payload = config_ui_module._config_ui_runtime_summary(project_root.resolve())
    assert payload['effective_mux_backend'] is None
    assert payload['os_platform'] in {'windows', 'linux', 'darwin'}


def test_config_ui_herdr_readonly_status_fails_closed_for_contradictory_projection() -> None:
    base_projection = {
        'backend_impl': 'herdr',
        'capability_status': 'supported',
        'support_tier_projection': 'beta',
        'support_tier_projection_source': 'backend_capability',
        'beta_gaps': [],
        'blocking_gaps': [],
        'degraded_next_action': None,
    }
    contradictions = [
        {'beta_gaps': ['config-ui-validation-pending']},
        {'support_tier_projection': 'experimental'},
        {'support_tier_projection_source': 'validation_pending'},
    ]

    for contradiction in contradictions:
        projection = {**base_projection, **contradiction}
        payload = config_ui_module._config_ui_readonly_status(projection)

        assert payload == {
            'status': 'blocked',
            'backend_impl': 'herdr',
            'reason': 'capability_status=supported',
            'degraded_next_action': None,
        }

    incomplete_projection = dict(base_projection)
    incomplete_projection.pop('beta_gaps')
    assert config_ui_module.herdr_surface_projection_passes_gate(incomplete_projection) is False
    malformed_projection = {**base_projection, 'blocking_gaps': {}}
    assert config_ui_module.herdr_surface_projection_passes_gate(malformed_projection) is False


def test_config_ui_reads_and_saves_user_theme_preference(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project_root = tmp_path / 'repo-theme'
    (project_root / '.ccb').mkdir(parents=True)
    (project_root / '.ccb' / 'ccb.config').write_text(
        'version = 2\nentry_window = "main"\n\n[windows]\nmain = "demo:codex"\n',
        encoding='utf-8',
    )
    config_home = tmp_path / 'config'
    monkeypatch.setenv('XDG_CONFIG_HOME', str(config_home))
    monkeypatch.setenv('CCB_SYSTEM_THEME', 'light')
    monkeypatch.delenv('TMUX', raising=False)
    monkeypatch.delenv('TMUX_PANE', raising=False)
    handle = prepare_config_ui(
        _context(project_root),
        ParsedConfigUiCommand(project=None),
        token='theme-token',
        idle_timeout_s=0.3,
    )
    thread = threading.Thread(target=handle.serve_forever)
    thread.start()

    try:
        initial = _get_json(handle.url, '/api/theme')
        assert initial['theme'] == 'dark'
        assert initial['effective_theme'] == 'dark'
        assert initial['available_themes'][0] == 'system'

        saved = _post_json(handle.url, '/api/theme', {'theme': 'system'})
        assert saved['status'] == 'ok'
        assert saved['theme'] == 'system'
        assert saved['palette'] == 'system'
        assert saved['effective_theme'] == 'light'
        assert saved['effective_palette'] == 'latte'
        assert saved['effective_tmux_profile'] == 'light'
        assert saved['tmux_refresh'] == 'skipped'

        theme_path = config_home / 'ccb' / 'theme.json'
        assert json.loads(theme_path.read_text(encoding='utf-8')) == {
            'palette': 'system',
            'schema_version': 1,
            'theme': 'system',
            'tmux_profile': 'system',
        }

        with pytest.raises(HTTPError) as invalid:
            _post_json(handle.url, '/api/theme', {'theme': 'unknown'})
        assert invalid.value.code == 422
    finally:
        handle.close()
        thread.join(timeout=2)
    assert not thread.is_alive()


def test_config_ui_uses_project_port_and_environment_token(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project_root = tmp_path / 'repo'
    config_path = project_root / '.ccb' / 'ccb.config'
    config_path.parent.mkdir(parents=True)
    config_path.write_text(
        '''version = 2
entry_window = "main"

[windows]
main = "agent1:codex"

[config_ui]
port = 43123
token_env = "CCB_CONFIG_UI_TEST_TOKEN"
''',
        encoding='utf-8',
    )
    monkeypatch.setenv('CCB_CONFIG_UI_TEST_TOKEN', 'stable-secret')
    page = tmp_path / 'index.html'
    page.write_text('<!doctype html><title>settings</title>', encoding='utf-8')

    handle = prepare_config_ui(
        _context(project_root),
        ParsedConfigUiCommand(project=None),
        asset_path=page,
    )
    try:
        assert urlparse(handle.url).port == 43123
        assert parse_qs(urlparse(handle.url).query)['token'] == ['stable-secret']
        assert handle.summary['token_source'] == 'environment'
        assert 'stable-secret' not in json.dumps(handle.summary)
    finally:
        handle.close()


def test_config_ui_cli_port_overrides_project_port(tmp_path: Path) -> None:
    project_root = tmp_path / 'repo'
    config_path = project_root / '.ccb' / 'ccb.config'
    config_path.parent.mkdir(parents=True)
    config_path.write_text(
        '''version = 2
entry_window = "main"

[windows]
main = "agent1:codex"

[config_ui]
port = 43123
''',
        encoding='utf-8',
    )

    resolved = resolve_config_ui_settings(project_root=project_root, cli_port=0)

    assert resolved.port == 0
    assert resolved.token is None
    assert resolved.token_source == 'ephemeral'


def test_config_ui_reads_owner_only_project_token_file(tmp_path: Path) -> None:
    project_root = tmp_path / 'repo'
    config_path = project_root / '.ccb' / 'ccb.config'
    token_path = project_root / '.ccb' / 'config-ui.token'
    config_path.parent.mkdir(parents=True)
    config_path.write_text(
        '''version = 2
entry_window = "main"

[windows]
main = "agent1:codex"

[config_ui]
token_file = ".ccb/config-ui.token"
''',
        encoding='utf-8',
    )
    token_path.write_text('file-secret\n', encoding='utf-8')
    token_path.chmod(0o600)

    resolved = resolve_config_ui_settings(project_root=project_root, cli_port=None)

    assert resolved.token == 'file-secret'
    assert resolved.token_source == 'file'


@pytest.mark.parametrize(
    ('config_ui', 'message'),
    [
        ('token = "must-not-be-accepted"', 'unknown fields: token'),
        ('token_env = "TOKEN"\ntoken_file = ".ccb/token"', 'mutually exclusive'),
        ('token_env = "not-a-valid-name"', 'valid environment variable name'),
        ('port = 70000', 'between 0 and 65535'),
    ],
)
def test_config_ui_rejects_unsafe_project_settings(
    tmp_path: Path,
    config_ui: str,
    message: str,
) -> None:
    project_root = tmp_path / 'repo'
    config_path = project_root / '.ccb' / 'ccb.config'
    config_path.parent.mkdir(parents=True)
    config_path.write_text(
        f'''version = 2
entry_window = "main"

[windows]
main = "agent1:codex"

[config_ui]
{config_ui}
''',
        encoding='utf-8',
    )

    with pytest.raises(ConfigValidationError, match=message) as exc_info:
        resolve_config_ui_settings(project_root=project_root, cli_port=None)
    assert 'must-not-be-accepted' not in str(exc_info.value)


def test_config_ui_rejects_insecure_token_file_without_leaking_contents(tmp_path: Path) -> None:
    if os.name == 'nt':
        pytest.skip('Windows chmod does not expose POSIX owner-only mode bits')
    project_root = tmp_path / 'repo'
    config_path = project_root / '.ccb' / 'ccb.config'
    token_path = project_root / '.ccb' / 'config-ui.token'
    config_path.parent.mkdir(parents=True)
    config_path.write_text(
        '''version = 2
entry_window = "main"

[windows]
main = "agent1:codex"

[config_ui]
token_file = ".ccb/config-ui.token"
''',
        encoding='utf-8',
    )
    token_path.write_text('do-not-leak', encoding='utf-8')
    token_path.chmod(0o644)

    with pytest.raises(ConfigValidationError, match='owner-only permissions') as exc_info:
        resolve_config_ui_settings(project_root=project_root, cli_port=None)
    assert 'do-not-leak' not in str(exc_info.value)


def test_config_ui_uses_builtin_demo_config_when_project_config_is_missing(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project_root = tmp_path / 'repo'
    (project_root / '.ccb').mkdir(parents=True)
    page = tmp_path / 'index.html'
    page.write_text('<!doctype html><title>settings</title>', encoding='utf-8')
    monkeypatch.setattr(
        config_ui_module,
        'render_default_project_config_text',
        lambda: 'version = 2\n\n[windows]\nmain = "demo:claude"\n',
    )
    handle = prepare_config_ui(
        _context(project_root),
        ParsedConfigUiCommand(project=None),
        asset_path=page,
        token='test-token',
        idle_timeout_s=0.3,
    )
    thread = threading.Thread(target=handle.serve_forever)
    thread.start()

    try:
        config = _get_json(handle.url, '/api/config')
        assert config['exists'] is False
        assert config['digest'] is None
        assert config['text'].endswith('main = "demo:claude"\n')
        assert config['editor']['windows'][0]['tree'] == {
            'kind': 'leaf',
            'name': 'demo',
            'provider': 'claude',
            'workspace_mode': 'inplace',
            'percent': None,
        }
    finally:
        handle.close()
        thread.join(timeout=2)
    assert not thread.is_alive()


def test_config_ui_validates_saves_with_digest_guard_and_hot_reloads(tmp_path: Path) -> None:
    project_root = tmp_path / 'repo'
    config_path = project_root / '.ccb' / 'ccb.config'
    config_path.parent.mkdir(parents=True)
    original = 'version = 2\n\n[windows]\nmain = "agent1:codex"\n'
    updated = original.replace('agent1:codex', 'agent1:codex, agent2:claude')
    config_path.write_text(original, encoding='utf-8')
    page = tmp_path / 'index.html'
    page.write_text('<!doctype html><title>settings</title>', encoding='utf-8')
    reload_calls: list[bool] = []

    def _reload(dry_run: bool) -> dict[str, object]:
        reload_calls.append(dry_run)
        if dry_run:
            return {'status': 'ok', 'plan_class': 'add_agent', 'future_safe_to_apply': True}
        return {'status': 'published', 'plan_class': 'add_agent'}

    handle = prepare_config_ui(
        _context(project_root),
        ParsedConfigUiCommand(project=None),
        asset_path=page,
        token='test-token',
        idle_timeout_s=1.0,
        reload_action=_reload,
    )
    thread = threading.Thread(target=handle.serve_forever)
    thread.start()

    try:
        config = _get_json(handle.url, '/api/config')
        assert config['text'] == original
        assert isinstance(config['digest'], str)
        assert config['editor']['entry_window'] == 'main'
        assert config['editor']['windows'][0]['tree'] == {
            'kind': 'leaf',
            'name': 'agent1',
            'provider': 'codex',
            'workspace_mode': 'inplace',
            'percent': None,
        }

        document = config['editor']['document']
        document['ui'] = {
            'sidebar': {
                'agents_height': '45%',
                'comms_height': '20%',
                'tips_height': '35%',
                'tips': ['first line', 'second line'],
            }
        }
        rendered = _post_json(handle.url, '/api/render', {'document': document})
        assert rendered['status'] == 'rendered'
        assert 'agents_height = "45%"' in rendered['text']
        assert 'tips = ["first line", "second line"]' in rendered['text']
        assert rendered['validation']['agent_names'] == ['agent1']

        thinking_document = json.loads(json.dumps(document))
        thinking_document['windows']['secondary'] = 'agent2:codex(worktree)'
        thinking_document['agents'] = {
            'agent1': {'model': 'gpt-5.5', 'thinking': 'high'},
            'agent2': {'model': 'gpt-5.6-sol', 'thinking': 'xhigh'},
        }
        thinking_rendered = _post_json(handle.url, '/api/render', {'document': thinking_document})
        assert 'model = "gpt-5.5"' in thinking_rendered['text']
        assert 'thinking = "high"' in thinking_rendered['text']
        assert '[agents.agent2]' in thinking_rendered['text']
        assert 'model = "gpt-5.6-sol"' in thinking_rendered['text']
        assert 'thinking = "xhigh"' in thinking_rendered['text']
        assert 'model_reasoning_effort' not in thinking_rendered['text']
        assert thinking_rendered['editor']['document']['agents']['agent2'] == {
            'model': 'gpt-5.6-sol',
            'thinking': 'xhigh',
        }

        rich_document = json.loads(json.dumps(document))
        rich_document['windows']['main'] = 'agent1:codex, rich'
        rich_rendered = _post_json(handle.url, '/api/render', {'document': rich_document})
        assert 'main = "agent1:codex, rich"' in rich_rendered['text']
        assert rich_rendered['editor']['windows'][0]['tree']['right']['name'] == 'rich'

        validation = _post_json(handle.url, '/api/validate', {'text': updated})
        assert validation['status'] == 'valid'
        assert validation['agent_names'] == ['agent1', 'agent2']

        preview = _post_json(
            handle.url,
            '/api/preview',
            {'text': updated, 'expected_digest': config['digest']},
        )
        assert preview['status'] == 'previewed'
        assert preview['changed'] is True
        assert '-main = "agent1:codex"' in preview['diff']
        assert '+main = "agent1:codex, agent2:claude"' in preview['diff']

        profile = _post_json(
            handle.url,
            '/api/profile',
            {'name': 'two-agents', 'text': updated},
        )
        assert profile['status'] == 'saved'
        assert Path(profile['path']).read_text(encoding='utf-8') == updated
        loaded_profile = _get_json(handle.url, '/api/profile?name=two-agents')
        assert loaded_profile['name'] == 'two-agents'
        assert loaded_profile['editor']['windows'][0]['tree']['kind'] == 'vertical'

        _post_json(
            handle.url,
            '/api/profile',
            {'name': 'compact-cmd', 'text': 'cmd, demo:codex\n'},
        )
        compact_profile = _get_json(handle.url, '/api/profile?name=compact-cmd')
        assert compact_profile['editor']['visual_supported'] is False

        with pytest.raises(HTTPError) as conflict:
            _post_json(
                handle.url,
                '/api/apply',
                {'text': updated, 'expected_digest': 'stale', 'mode': 'save'},
            )
        assert conflict.value.code == 409
        assert config_path.read_text(encoding='utf-8') == original

        applied = _post_json(
            handle.url,
            '/api/apply',
            {'text': updated, 'expected_digest': config['digest'], 'mode': 'hot_reload'},
        )
        assert applied['status'] == 'reloaded'
        assert applied['saved'] is True
        assert applied['dry_run']['plan_class'] == 'add_agent'
        assert applied['reload']['status'] == 'published'
        assert reload_calls == [True, False]
        assert config_path.read_text(encoding='utf-8') == updated
        backup_path = Path(applied['backup_path'])
        assert backup_path.read_text(encoding='utf-8') == original
    finally:
        handle.close()
        thread.join(timeout=2)
    assert not thread.is_alive()


def test_config_ui_crlf_noop_save_preserves_file_and_reports_unchanged(tmp_path: Path) -> None:
    project_root = tmp_path / 'repo-crlf-save'
    config_path = project_root / '.ccb' / 'ccb.config'
    config_path.parent.mkdir(parents=True)
    original_crlf = 'version = 2\r\n\r\n[windows]\r\nmain = "agent1:codex"\r\n'
    normalized = original_crlf.replace('\r\n', '\n')
    config_path.write_text(original_crlf, encoding='utf-8', newline='')
    page = tmp_path / 'index.html'
    page.write_text('<!doctype html><title>settings</title>', encoding='utf-8')
    handle = prepare_config_ui(
        _context(project_root),
        ParsedConfigUiCommand(project=None),
        asset_path=page,
        token='test-token',
        idle_timeout_s=0.3,
    )
    thread = threading.Thread(target=handle.serve_forever)
    thread.start()

    try:
        config = _get_json(handle.url, '/api/config')
        assert config['text'] == normalized
        applied = _post_json(
            handle.url,
            '/api/apply',
            {'text': config['text'], 'expected_digest': config['digest'], 'mode': 'save'},
        )
        assert applied['status'] == 'saved'
        assert applied['changed'] is False
        assert applied['backup_path'] is None
        assert applied['restart_required'] is False
        assert applied['restart_intent'] is None
        assert config_path.read_bytes().decode('utf-8') == original_crlf
    finally:
        handle.close()
        thread.join(timeout=2)
    assert not thread.is_alive()


def test_config_ui_saves_api_change_without_hot_reload_and_schedules_restart(
    tmp_path: Path,
) -> None:
    project_root = tmp_path / 'repo-api-restart'
    config_path = project_root / '.ccb' / 'ccb.config'
    config_path.parent.mkdir(parents=True)
    original = '''version = 2

[windows]
main = "agent1:codex"

[agents.agent1]
key = "old-secret"
url = "https://old.example.test"
'''
    updated = original.replace('old-secret', 'new-secret').replace(
        'https://old.example.test',
        'https://new.example.test',
    )
    config_path.write_text(original, encoding='utf-8')
    page = tmp_path / 'index.html'
    page.write_text('<!doctype html><title>settings</title>', encoding='utf-8')
    reload_calls: list[bool] = []

    def _reload(dry_run: bool) -> dict[str, object]:
        reload_calls.append(dry_run)
        assert dry_run is True
        return {
            'status': 'ok',
            'plan_class': 'replace_agent',
            'future_safe_to_apply': True,
            'operations': [
                {
                    'op': 'replace_agent',
                    'agent': 'agent1',
                    'fields': ['api', 'provider_profile'],
                }
            ],
        }

    handle = prepare_config_ui(
        _context(project_root),
        ParsedConfigUiCommand(project=None),
        asset_path=page,
        token='test-token',
        idle_timeout_s=1.0,
        reload_action=_reload,
    )
    thread = threading.Thread(target=handle.serve_forever)
    thread.start()

    try:
        config = _get_json(handle.url, '/api/config')
        applied = _post_json(
            handle.url,
            '/api/apply',
            {
                'text': updated,
                'expected_digest': config['digest'],
                'mode': 'hot_reload',
            },
        )

        assert applied['status'] == 'restart_required'
        assert applied['restart_required'] is True
        assert applied['affected_agents'] == ['agent1']
        assert applied['dry_run']['plan_class'] == 'replace_agent'
        assert 'reload' not in applied
        assert reload_calls == [True]
        assert config_path.read_text(encoding='utf-8') == updated

        layout = PathLayout(project_root)
        intent = load_config_restart_intent(layout)
        assert intent is not None
        assert intent.affected_agents == ('agent1',)
        persisted = layout.ccbd_config_restart_intent_path.read_text(encoding='utf-8')
        assert 'old-secret' not in persisted
        assert 'new-secret' not in persisted
        assert 'https://new.example.test' not in persisted
    finally:
        handle.close()
        thread.join(timeout=2)
    assert not thread.is_alive()


def test_config_ui_schedules_api_restart_when_daemon_dry_run_is_unavailable(
    tmp_path: Path,
) -> None:
    project_root = tmp_path / 'repo-api-restart-offline'
    config_path = project_root / '.ccb' / 'ccb.config'
    config_path.parent.mkdir(parents=True)
    original = '''version = 2

[windows]
main = "agent1:codex"

[agents.agent1]
key = "old-secret"
url = "https://old.example.test"
'''
    updated = original.replace('old-secret', 'new-secret')
    config_path.write_text(original, encoding='utf-8')
    expected_digest = hashlib.sha256(config_path.read_bytes()).hexdigest()

    def _unavailable(_dry_run: bool) -> dict[str, object]:
        raise RuntimeError('daemon unavailable')

    status, applied = config_ui_module._apply_candidate(
        {
            'text': updated,
            'expected_digest': expected_digest,
            'mode': 'hot_reload',
        },
        config_path=config_path,
        project_root=project_root,
        path_layout=PathLayout(project_root),
        reload_action=_unavailable,
        mutation_lock=threading.Lock(),
    )

    assert int(status) == 200
    assert applied['status'] == 'restart_required'
    assert applied['restart_required'] is True
    assert applied['affected_agents'] == ['agent1']
    assert applied['dry_run']['status'] == 'unavailable'
    assert applied['reload_warning'] == 'daemon unavailable'
    assert config_path.read_text(encoding='utf-8') == updated
    assert load_config_restart_intent(PathLayout(project_root)) is not None


def test_config_ui_safe_apply_clears_matching_save_only_restart_intent(
    tmp_path: Path,
) -> None:
    project_root = tmp_path / 'repo-safe-apply-after-save'
    config_path = project_root / '.ccb' / 'ccb.config'
    config_path.parent.mkdir(parents=True)
    original = 'version = 2\n\n[windows]\nmain = "agent1:codex"\n'
    updated = original.replace('agent1:codex', 'agent1:codex, agent2:claude')
    config_path.write_text(original, encoding='utf-8')
    layout = PathLayout(project_root)
    original_digest = hashlib.sha256(config_path.read_bytes()).hexdigest()

    status, saved = config_ui_module._apply_candidate(
        {
            'text': updated,
            'expected_digest': original_digest,
            'mode': 'save',
        },
        config_path=config_path,
        project_root=project_root,
        path_layout=layout,
        reload_action=lambda _dry_run: {},
        mutation_lock=threading.Lock(),
    )

    assert int(status) == 200
    assert saved['restart_required'] is True
    assert load_config_restart_intent(layout) is not None
    reload_calls: list[bool] = []

    def _reload(dry_run: bool) -> dict[str, object]:
        reload_calls.append(dry_run)
        if dry_run:
            return {
                'status': 'ok',
                'plan_class': 'add_agent',
                'future_safe_to_apply': True,
            }
        return {'status': 'published', 'plan_class': 'add_agent'}

    status, applied = config_ui_module._apply_candidate(
        {
            'text': updated,
            'expected_digest': saved['digest'],
            'mode': 'hot_reload',
        },
        config_path=config_path,
        project_root=project_root,
        path_layout=layout,
        reload_action=_reload,
        mutation_lock=threading.Lock(),
    )

    assert int(status) == 200
    assert applied['status'] == 'reloaded'
    assert reload_calls == [True, False]
    assert load_config_restart_intent(layout) is None


def test_config_ui_rejects_invalid_candidate_without_writing(tmp_path: Path) -> None:
    project_root = tmp_path / 'repo'
    config_path = project_root / '.ccb' / 'ccb.config'
    config_path.parent.mkdir(parents=True)
    original = 'version = 2\n\n[windows]\nmain = "agent1:codex"\n'
    config_path.write_text(original, encoding='utf-8')
    page = tmp_path / 'index.html'
    page.write_text('<!doctype html><title>settings</title>', encoding='utf-8')
    handle = prepare_config_ui(
        _context(project_root),
        ParsedConfigUiCommand(project=None),
        asset_path=page,
        token='test-token',
        idle_timeout_s=0.5,
        reload_action=lambda _dry_run: {},
    )
    thread = threading.Thread(target=handle.serve_forever)
    thread.start()

    try:
        config = _get_json(handle.url, '/api/config')
        with pytest.raises(HTTPError) as invalid:
            _post_json(
                handle.url,
                '/api/apply',
                {
                    'text': 'version = 3\n',
                    'expected_digest': config['digest'],
                    'mode': 'save',
                },
            )
        assert invalid.value.code == 422
        assert config_path.read_text(encoding='utf-8') == original
        assert not tuple(config_path.parent.glob('ccb.config.bak.*'))
    finally:
        handle.close()
        thread.join(timeout=2)
    assert not thread.is_alive()


def test_config_ui_hot_reload_removes_agent_and_preserves_remaining_overlay(tmp_path: Path) -> None:
    project_root = tmp_path / 'repo-remove-agent'
    config_path = project_root / '.ccb' / 'ccb.config'
    config_path.parent.mkdir(parents=True)
    original = '''version = 2

[windows]
main = "agent1:codex, agent2:claude"

[agents.agent1]
role = "agentroles.coder"

[agents.agent2]
role = "agentroles.code_reviewer"
'''
    updated = '''version = 2

[windows]
main = "agent1:codex"

[agents.agent1]
role = "agentroles.coder"
'''
    config_path.write_text(original, encoding='utf-8')
    page = tmp_path / 'index.html'
    page.write_text('<!doctype html><title>settings</title>', encoding='utf-8')
    reload_calls: list[bool] = []

    def _reload(dry_run: bool) -> dict[str, object]:
        reload_calls.append(dry_run)
        if dry_run:
            return {'status': 'ok', 'plan_class': 'remove_agent', 'future_safe_to_apply': True}
        return {'status': 'published', 'plan_class': 'remove_agent'}

    handle = prepare_config_ui(
        _context(project_root),
        ParsedConfigUiCommand(project=None),
        asset_path=page,
        token='test-token',
        idle_timeout_s=1.0,
        reload_action=_reload,
    )
    thread = threading.Thread(target=handle.serve_forever)
    thread.start()

    try:
        config = _get_json(handle.url, '/api/config')
        applied = _post_json(
            handle.url,
            '/api/apply',
            {'text': updated, 'expected_digest': config['digest'], 'mode': 'hot_reload'},
        )

        assert applied['status'] == 'reloaded'
        assert applied['dry_run']['plan_class'] == 'remove_agent'
        assert applied['reload']['status'] == 'published'
        assert reload_calls == [True, False]
        saved_text = config_path.read_text(encoding='utf-8')
        assert saved_text == updated
        assert '[agents.agent1]' in saved_text
        assert '[agents.agent2]' not in saved_text
        assert Path(applied['backup_path']).read_text(encoding='utf-8') == original
    finally:
        handle.close()
        thread.join(timeout=2)
    assert not thread.is_alive()


def test_config_ui_scans_and_cleans_agent_history_through_token_guarded_api(tmp_path: Path) -> None:
    project_root = tmp_path / 'repo-history'
    (project_root / '.ccb').mkdir(parents=True)
    scan_calls: list[tuple[int, str | None]] = []
    cleanup_calls: list[tuple[int, str | None]] = []
    scan_payload = {
        'schema_version': 1,
        'status': 'ok',
        'retention_days': 7,
        'agent': 'worker1',
        'candidate_count': 2,
        'candidate_bytes': 123,
        'agents': [],
    }

    def _scan(retention_days: int, agent: str | None) -> dict[str, object]:
        scan_calls.append((retention_days, agent))
        return dict(scan_payload)

    def _cleanup(retention_days: int, agent: str | None) -> dict[str, object]:
        cleanup_calls.append((retention_days, agent))
        return {
            'schema_version': 1,
            'status': 'ok',
            'retention_days': retention_days,
            'agent': agent or 'all',
            'deleted_count': 2,
            'deleted_bytes': 123,
            'skipped_count': 0,
            'scan': {**scan_payload, 'candidate_count': 0, 'candidate_bytes': 0},
        }

    handle = prepare_config_ui(
        _context(project_root),
        ParsedConfigUiCommand(project=None),
        token='history-token',
        idle_timeout_s=1.0,
        reload_action=lambda _dry_run: {'status': 'unused'},
        history_scan_action=_scan,
        history_cleanup_action=_cleanup,
    )
    thread = threading.Thread(target=handle.serve_forever)
    thread.start()

    try:
        scanned = _get_json(handle.url, '/api/storage/history?retention_days=7&agent=worker1')
        assert scanned['candidate_count'] == 2
        assert scan_calls == [(7, 'worker1')]

        cleaned = _post_json(
            handle.url,
            '/api/storage/history/cleanup',
            {'retention_days': 7, 'agent': 'worker1'},
        )
        assert cleaned['deleted_count'] == 2
        assert cleanup_calls == [(7, 'worker1')]

        with pytest.raises(HTTPError) as invalid_get:
            _get_json(handle.url, '/api/storage/history?retention_days=1&agent=worker1')
        assert invalid_get.value.code == 422
        with pytest.raises(HTTPError) as invalid_post:
            _post_json(
                handle.url,
                '/api/storage/history/cleanup',
                {'retention_days': 365, 'agent': 'worker1'},
            )
        assert invalid_post.value.code == 422
    finally:
        thread.join(timeout=2)
        handle.close()
    assert not thread.is_alive()


def _get_json(base_url: str, path: str) -> dict[str, object]:
    parsed = urlparse(base_url)
    token = parse_qs(parsed.query)['token'][0]
    separator = '&' if '?' in path else '?'
    url = f'{parsed.scheme}://{parsed.netloc}{path}{separator}{urlencode({"token": token})}'
    with urlopen(url, timeout=2) as response:
        return json.loads(response.read())


def _post_json(base_url: str, path: str, payload: dict[str, object]) -> dict[str, object]:
    parsed = urlparse(base_url)
    token = parse_qs(parsed.query)['token'][0]
    url = f'{parsed.scheme}://{parsed.netloc}{path}?{urlencode({"token": token})}'
    request = Request(
        url,
        data=json.dumps(payload).encode('utf-8'),
        headers={'Content-Type': 'application/json'},
        method='POST',
    )
    with urlopen(request, timeout=2) as response:
        return json.loads(response.read())


def test_config_ui_browser_open_uses_wsl_fallback(monkeypatch) -> None:
    seen: list[tuple[str, ...]] = []
    monkeypatch.setenv('WSL_DISTRO_NAME', 'Ubuntu')
    monkeypatch.setattr(config_ui_module.webbrowser, 'open', lambda *_args, **_kwargs: False)
    monkeypatch.setattr(
        config_ui_module.shutil,
        'which',
        lambda name: f'/usr/bin/{name}' if name == 'wslview' else None,
    )
    monkeypatch.setattr(
        config_ui_module.subprocess,
        'Popen',
        lambda command, **_kwargs: (
            seen.append(tuple(command))
            or SimpleNamespace(wait=lambda **_wait_kwargs: 0)
        ),
    )

    assert open_config_ui_url('http://127.0.0.1:43123/?token=test') is True
    assert seen == [('wslview', 'http://127.0.0.1:43123/?token=test')]


def test_config_ui_browser_open_prefers_wsl_host_opener_over_webbrowser(monkeypatch) -> None:
    seen: list[object] = []
    url = 'http://127.0.0.1:43123/?token=test'
    monkeypatch.setenv('WSL_DISTRO_NAME', 'Ubuntu')
    monkeypatch.setattr(
        config_ui_module.webbrowser,
        'open',
        lambda *_args, **_kwargs: seen.append('webbrowser') or True,
    )
    monkeypatch.setattr(
        config_ui_module.shutil,
        'which',
        lambda name: f'/usr/bin/{name}' if name == 'wslview' else None,
    )
    monkeypatch.setattr(
        config_ui_module.subprocess,
        'Popen',
        lambda command, **_kwargs: (
            seen.append(tuple(command))
            or SimpleNamespace(wait=lambda **_wait_kwargs: 0)
        ),
    )

    assert open_config_ui_url(url) is True
    assert seen == [('wslview', url)]


def test_config_ui_browser_open_prefers_macos_open_over_linux_opener(monkeypatch) -> None:
    seen: list[tuple[str, ...]] = []
    url = 'http://127.0.0.1:43123/?token=test'
    monkeypatch.delenv('WSL_DISTRO_NAME', raising=False)
    monkeypatch.delenv('WSL_INTEROP', raising=False)
    monkeypatch.setattr(sys, 'platform', 'darwin')
    monkeypatch.setattr(config_ui_module, '_is_wsl_environment', lambda: False)
    monkeypatch.setattr(config_ui_module.webbrowser, 'open', lambda *_args, **_kwargs: False)
    monkeypatch.setattr(
        config_ui_module.shutil,
        'which',
        lambda name: f'/usr/bin/{name}' if name in {'open', 'xdg-open'} else None,
    )
    monkeypatch.setattr(
        config_ui_module.subprocess,
        'Popen',
        lambda command, **_kwargs: (
            seen.append(tuple(command))
            or SimpleNamespace(wait=lambda **_wait_kwargs: 0)
        ),
    )

    assert open_config_ui_url(url) is True
    assert seen == [('open', url)]


def test_config_ui_browser_open_prefers_linux_configured_browser_over_xdg(monkeypatch) -> None:
    seen: list[tuple[str, ...]] = []
    url = 'http://127.0.0.1:43123/?token=test'
    monkeypatch.delenv('WSL_DISTRO_NAME', raising=False)
    monkeypatch.delenv('WSL_INTEROP', raising=False)
    monkeypatch.setattr(sys, 'platform', 'linux')
    monkeypatch.setattr(config_ui_module, '_is_wsl_environment', lambda: False)
    monkeypatch.setattr(config_ui_module.webbrowser, 'open', lambda *_args, **_kwargs: False)
    monkeypatch.setattr(
        config_ui_module.shutil,
        'which',
        lambda name: f'/usr/bin/{name}' if name in {'sensible-browser', 'xdg-open'} else None,
    )
    monkeypatch.setattr(
        config_ui_module.subprocess,
        'Popen',
        lambda command, **_kwargs: (
            seen.append(tuple(command))
            or SimpleNamespace(wait=lambda **_wait_kwargs: 0)
        ),
    )

    assert open_config_ui_url(url) is True
    assert seen == [('sensible-browser', url)]


def test_config_ui_browser_open_retries_after_wsl_opener_exits_nonzero(monkeypatch) -> None:
    seen: list[tuple[str, ...]] = []
    url = 'http://127.0.0.1:43123/?token=test'
    monkeypatch.setenv('WSL_DISTRO_NAME', 'Ubuntu')
    monkeypatch.setattr(config_ui_module.webbrowser, 'open', lambda *_args, **_kwargs: False)
    monkeypatch.setattr(
        config_ui_module.shutil,
        'which',
        lambda name: f'/usr/bin/{name}' if name in {'wslview', 'cmd.exe'} else None,
    )

    def _popen(command, **_kwargs):
        argv = tuple(command)
        seen.append(argv)
        return SimpleNamespace(wait=lambda **_wait_kwargs: 1 if argv[0] == 'wslview' else 0)

    monkeypatch.setattr(config_ui_module.subprocess, 'Popen', _popen)

    assert open_config_ui_url(url) is True
    assert seen == [
        ('wslview', url),
        ('cmd.exe', '/c', 'start', '', url),
    ]


def test_config_ui_browser_open_reaps_opener_that_outlives_confirmation(monkeypatch) -> None:
    reaped = threading.Event()
    url = 'http://127.0.0.1:43123/?token=test'
    monkeypatch.setenv('WSL_DISTRO_NAME', 'Ubuntu')
    monkeypatch.setattr(
        config_ui_module.shutil,
        'which',
        lambda name: f'/usr/bin/{name}' if name == 'wslview' else None,
    )

    class _SlowProcess:
        def wait(self, timeout=None):
            if timeout is not None:
                raise config_ui_module.subprocess.TimeoutExpired('wslview', timeout)
            reaped.set()
            return 0

    monkeypatch.setattr(
        config_ui_module.subprocess,
        'Popen',
        lambda _command, **_kwargs: _SlowProcess(),
    )

    assert open_config_ui_url(url) is True
    assert reaped.wait(timeout=1.0)


def test_config_ui_provider_capabilities_use_current_safe_model_sources(tmp_path: Path) -> None:
    cache_path = tmp_path / 'models_cache.json'
    cache_path.write_text(
        json.dumps(
            {
                'models': [
                    {
                        'slug': 'gpt-5.6-sol',
                        'display_name': 'GPT-5.6 SOL',
                        'visibility': 'list',
                        'default_reasoning_level': 'low',
                        'supported_reasoning_levels': [
                            {'effort': 'low'},
                            {'effort': 'medium'},
                            {'effort': 'ultra'},
                        ],
                    },
                    {
                        'slug': 'gpt-5.5',
                        'display_name': 'GPT-5.5',
                        'visibility': 'list',
                        'default_reasoning_level': 'medium',
                        'supported_reasoning_levels': [{'effort': 'high'}],
                    },
                    {'slug': 'gpt-5.4', 'display_name': 'GPT-5.4', 'visibility': 'list'},
                    {'slug': 'codex-auto-review', 'display_name': 'Auto Review', 'visibility': 'hide'},
                ]
            }
        ),
        encoding='utf-8',
    )

    payload = config_ui_provider_capabilities(
        environ={'HOME': str(tmp_path), 'PATH': ''},
        codex_models_path=cache_path,
        cli_models={
            'opencode': ['openai/gpt-5.6-sol'],
            'mimo': ['xiaomi/mimo-v2.5-pro'],
        },
    )
    providers = {provider['id']: provider for provider in payload['providers']}

    assert [model['id'] for model in providers['codex']['models']] == ['gpt-5.6-sol', 'gpt-5.5']
    assert providers['codex']['models'][0]['reasoning_levels'] == ['low', 'medium', 'ultra']
    assert providers['codex']['models'][0]['default_reasoning_level'] == 'low'
    assert {model['id'] for model in providers['claude']['models']} >= {
        'claude-fable-5',
        'claude-opus-4-8',
        'claude-sonnet-5',
        'claude-haiku-4-5',
    }
    assert providers['claude']['models'][0]['reasoning_levels'] == [
        'low',
        'medium',
        'high',
        'xhigh',
        'max',
    ]
    assert {model['id'] for model in providers['gemini']['models']} >= {
        'gemini-3.5-flash',
        'gemini-3.1-pro-preview',
        'gemini-3.1-flash-lite',
    }
    assert [model['id'] for model in providers['deepseek']['models']] == [
        'deepseek-v4-pro',
        'deepseek-v4-flash',
    ]
    assert providers['deepseek']['models'][0]['reasoning_levels'] == ['off', 'high', 'max']
    assert providers['deepseek']['model_shortcut'] is True
    assert providers['codex']['api_shortcut'] is True
    assert providers['deepseek']['api_shortcut'] is True
    assert providers['deepseek']['model_source'] == 'deepseek_v4_and_deepcode_contract'
    assert [model['id'] for model in providers['dsh']['models']] == [
        'deepseek-v4-flash',
        'deepseek-v4-pro',
    ]
    assert providers['dsh']['models'][0]['reasoning_levels'] == ['off', 'high', 'max']
    assert providers['dsh']['models'][0]['default_reasoning_level'] == 'high'
    assert providers['dsh']['model_shortcut'] is True
    assert providers['dsh']['api_shortcut'] is True
    assert providers['dsh']['model_source'] == 'deepseek_harness_official_catalog'
    assert [model['id'] for model in providers['opencode']['models']] == ['openai/gpt-5.6-sol']
    assert [model['id'] for model in providers['mimo']['models']] == ['xiaomi/mimo-v2.5-pro']
    assert providers['codex']['static_thinking'] is True
    assert providers['claude']['static_thinking'] is True
    assert providers['deepseek']['static_thinking'] is True
    assert all(
        provider['static_thinking'] is False
        for name, provider in providers.items()
        if name not in {'codex', 'claude', 'deepseek', 'dsh'}
    )


def test_config_ui_codex_fallback_keeps_current_56_family_and_55(tmp_path: Path) -> None:
    payload = config_ui_provider_capabilities(
        environ={'HOME': str(tmp_path), 'PATH': ''},
        codex_models_path=tmp_path / 'missing-models-cache.json',
        cli_models={'opencode': [], 'mimo': []},
    )
    codex = next(provider for provider in payload['providers'] if provider['id'] == 'codex')

    assert codex['model_source'] == 'ccb_catalog_fallback'
    assert [model['id'] for model in codex['models']] == [
        'gpt-5.6-sol',
        'gpt-5.6-terra',
        'gpt-5.6-luna',
        'gpt-5.5',
    ]


def test_config_ui_prefers_project_managed_codex_model_cache(tmp_path: Path) -> None:
    project_root = tmp_path / 'repo'
    managed_cache = (
        project_root
        / '.ccb'
        / 'agents'
        / 'coder'
        / 'provider-state'
        / 'codex'
        / 'home'
        / 'models_cache.json'
    )
    managed_cache.parent.mkdir(parents=True)
    managed_cache.write_text(
        json.dumps(
            {
                'models': [
                    {
                        'slug': 'gpt-5.6-sol',
                        'display_name': 'GPT-5.6 SOL',
                        'visibility': 'list',
                        'default_reasoning_level': 'ultra',
                        'supported_reasoning_levels': [{'effort': 'ultra'}],
                    }
                ]
            }
        ),
        encoding='utf-8',
    )

    payload = config_ui_provider_capabilities(
        environ={'HOME': str(tmp_path / 'empty-home'), 'PATH': ''},
        project_root=project_root,
        cli_models={'opencode': [], 'mimo': []},
    )
    codex = next(provider for provider in payload['providers'] if provider['id'] == 'codex')

    assert codex['model_source'] == 'codex_cache_managed'
    assert [model['id'] for model in codex['models']] == ['gpt-5.6-sol']
