from __future__ import annotations

import difflib
import hashlib
import json
import os
import re
import secrets
import shutil
import socket
import subprocess
import sys
import threading
import time
import webbrowser
from dataclasses import dataclass, replace
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Callable
from urllib.parse import parse_qs, urlparse

from agents.config_loader import (
    ConfigValidationError,
    render_default_project_config_text,
    render_project_config_text,
    validate_project_config,
)
from agents.config_loader_runtime.defaults_runtime.rendering_runtime.service import render_config_document_text
from agents.config_loader_runtime.io_runtime import parse_config_document_text
from agents.models import ProviderProfileSpec, parse_layout_spec
from cli.context import CliContext
from cli.models import ParsedConfigUiCommand, ParsedReloadCommand
from cli.output import atomic_write_text
from cli.services.config_restart_intent import (
    discard_config_restart_intent_for_digest,
    record_config_restart_intent,
)
from cli.services.config_ui_settings import resolve_config_ui_settings
from platforms.windows.herdr.surface import herdr_surface_projection_from_namespace_state
from cli.services.theme import set_theme_preference, theme_preference_payload
from platforms.windows.herdr.ccbd_surface_projection import herdr_surface_projection_passes_gate
from ccbd.services.project_namespace_state import ProjectNamespaceStateStore
from provider_core.registry import CORE_PROVIDER_NAMES, OPTIONAL_PROVIDER_NAMES
from provider_core.source_home import current_provider_source_home
from provider_model_shortcuts import supported_provider_model_shortcuts
from provider_profiles import supported_provider_api_shortcuts, validate_provider_runtime_home_uniqueness
from provider_thinking_shortcuts import provider_thinking_levels


DEFAULT_IDLE_TIMEOUT_S = 30 * 60
MAX_REQUEST_BODY_BYTES = 1024 * 1024
_BROWSER_OPEN_CONFIRM_TIMEOUT_S = 2.0
_CAPABILITIES_CLI_MODELS_BUDGET_S = 0.1
_CAPABILITIES_CLI_MODELS_RETRY_S = 0.5
_PROFILE_NAME_PATTERN = re.compile(r'^[A-Za-z][A-Za-z0-9_.-]{0,63}$')
_CONFIG_UI_RELATIVE_PATH = Path('assets/config_ui/index.html')


@dataclass
class ConfigUiHandle:
    url: str
    summary: dict[str, object]
    _server: ThreadingHTTPServer
    _last_activity: list[float]
    _idle_timeout_s: float
    _serving: list[bool]

    def serve_forever(self) -> None:
        self._last_activity[0] = time.monotonic()
        self._server.timeout = min(1.0, max(0.05, self._idle_timeout_s))
        self._serving[0] = True
        try:
            while time.monotonic() - self._last_activity[0] < self._idle_timeout_s:
                self._server.handle_request()
        finally:
            self._serving[0] = False

    def close(self) -> None:
        if self._serving[0]:
            self._last_activity[0] = time.monotonic() - self._idle_timeout_s
            try:
                with socket.create_connection(self._server.server_address[:2], timeout=0.2):
                    pass
            except OSError:
                pass
        self._server.server_close()


def prepare_config_ui(
    context: CliContext,
    command: ParsedConfigUiCommand,
    *,
    asset_path: Path | None = None,
    token: str | None = None,
    idle_timeout_s: float = DEFAULT_IDLE_TIMEOUT_S,
    reload_action: Callable[[bool], dict[str, object]] | None = None,
    history_scan_action: Callable[[int, str | None], dict[str, object]] | None = None,
    history_cleanup_action: Callable[[int, str | None], dict[str, object]] | None = None,
) -> ConfigUiHandle:
    page_path = Path(asset_path) if asset_path is not None else config_ui_asset_path()
    if not page_path.is_file():
        raise RuntimeError(f'config UI asset is missing: {page_path}')
    page = page_path.read_bytes()
    project_root = context.project.project_root.resolve()
    config_path = project_root / '.ccb' / 'ccb.config'
    settings = resolve_config_ui_settings(project_root=project_root, cli_port=command.port)
    session_payload = json.dumps(
        _config_ui_session_payload(
            context,
            project_root=project_root,
            config_path=config_path,
        ),
        ensure_ascii=False,
    ).encode('utf-8')
    capabilities_cache: list[bytes | None] = [None]
    capabilities_retry_at: list[float] = [0.0]
    capabilities_last_partial: list[bytes | None] = [None]
    capabilities_probe: list[dict[str, object] | None] = [None]
    capabilities_cache_lock = threading.Lock()
    role_catalog = _config_ui_role_catalog()

    def capabilities_payload() -> bytes:
        with capabilities_cache_lock:
            if capabilities_cache[0] is not None:
                return capabilities_cache[0]
            now = time.monotonic()
            if capabilities_last_partial[0] is not None and now < capabilities_retry_at[0]:
                return capabilities_last_partial[0]
            if capabilities_probe[0] is None:
                capabilities_probe[0] = _start_provider_cli_models_probe(dict(os.environ))
            cli_models, probe_complete = _join_provider_cli_models_probe(
                capabilities_probe[0],
                budget_s=_CAPABILITIES_CLI_MODELS_BUDGET_S,
            )
            payload = json.dumps(
                config_ui_provider_capabilities(
                    project_root=project_root,
                    cli_models=cli_models,
                    roles=role_catalog,
                ),
                ensure_ascii=False,
            ).encode('utf-8')
            if probe_complete:
                capabilities_cache[0] = payload
                capabilities_probe[0] = None
            else:
                capabilities_last_partial[0] = payload
                capabilities_retry_at[0] = now + _CAPABILITIES_CLI_MODELS_RETRY_S
            return payload
    access_token = token if token is not None else settings.token or secrets.token_urlsafe(24)
    last_activity = [time.monotonic()]
    if reload_action is None:
        from .reload import reload_config

        reload_action = lambda dry_run: reload_config(
            context,
            ParsedReloadCommand(project=None, dry_run=bool(dry_run)),
        )
    if history_scan_action is None or history_cleanup_action is None:
        from .agent_history_cleanup import cleanup_agent_history, scan_agent_history

        if history_scan_action is None:
            history_scan_action = lambda retention_days, agent: scan_agent_history(
                context,
                retention_days=retention_days,
                agent=agent,
            )
        if history_cleanup_action is None:
            history_cleanup_action = lambda retention_days, agent: cleanup_agent_history(
                context,
                retention_days=retention_days,
                agent=agent,
            )
    handler = _handler_for(
        page=page,
        session_payload=session_payload,
        capabilities_payload=capabilities_payload,
        config_path=config_path,
        project_root=project_root,
        path_layout=getattr(context, 'paths', None),
        reload_action=reload_action,
        history_scan_action=history_scan_action,
        history_cleanup_action=history_cleanup_action,
        token=access_token,
        last_activity=last_activity,
    )
    server = ThreadingHTTPServer(('127.0.0.1', settings.port), handler)
    server.daemon_threads = True
    host, port = server.server_address[:2]
    url = f'http://{host}:{port}/?token={access_token}'
    return ConfigUiHandle(
        url=url,
        summary={
            'config_ui_status': 'serving',
            'url': f'http://{host}:{port}/',
            'bind': 'loopback',
            'token_source': 'injected' if token is not None else settings.token_source,
            'project_root': str(project_root),
            'config_path': str(config_path),
            'mode': 'editor',
        },
        _server=server,
        _last_activity=last_activity,
        _idle_timeout_s=max(0.05, float(idle_timeout_s)),
        _serving=[False],
    )


def open_config_ui_url(url: str) -> bool:
    for command in _browser_open_commands(url):
        if shutil.which(command[0]) is None:
            continue
        try:
            process = subprocess.Popen(
                command,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                start_new_session=True,
            )
            try:
                return_code = process.wait(timeout=_BROWSER_OPEN_CONFIRM_TIMEOUT_S)
            except subprocess.TimeoutExpired:
                threading.Thread(
                    target=_reap_browser_open_process,
                    args=(process,),
                    daemon=True,
                ).start()
                return True
        except (OSError, subprocess.SubprocessError):
            continue
        if return_code == 0:
            return True
    try:
        if webbrowser.open(url, new=2):
            return True
    except (OSError, webbrowser.Error):
        pass
    return False


def _reap_browser_open_process(process: subprocess.Popen) -> None:
    try:
        process.wait()
    except (OSError, subprocess.SubprocessError):
        pass


def _browser_open_commands(url: str) -> tuple[tuple[str, ...], ...]:
    if _is_wsl_environment():
        return (
            ('wslview', url),
            ('cmd.exe', '/c', 'start', '', url),
            ('explorer.exe', url),
            ('xdg-open', url),
        )
    if sys.platform == 'darwin':
        return (('open', url),)
    if sys.platform.startswith(('linux', 'freebsd', 'openbsd')):
        return (
            ('sensible-browser', url),
            ('x-www-browser', url),
            ('xdg-open', url),
            ('gio', 'open', url),
        )
    return ()


def _is_wsl_environment() -> bool:
    if str(os.environ.get('WSL_DISTRO_NAME') or '').strip():
        return True
    if str(os.environ.get('WSL_INTEROP') or '').strip():
        return True
    try:
        return 'microsoft' in os.uname().release.lower()
    except (AttributeError, OSError):
        return False


def _config_ui_session_payload(
    context: CliContext,
    *,
    project_root: Path,
    config_path: Path,
) -> dict[str, object]:
    payload: dict[str, object] = {
        'schema_version': 2,
        'mode': 'editor',
        'project_root': str(project_root),
        'config_path': str(config_path),
        'config_exists': config_path.is_file(),
    }
    payload['runtime_summary'] = _config_ui_runtime_summary(project_root)
    projection = _config_ui_herdr_surface_projection(context)
    if projection is not None:
        payload['herdr_surface_projection'] = projection
        payload['config_ui_readonly_status'] = _config_ui_readonly_status(projection)
    return payload


def _config_ui_runtime_summary(project_root: Path) -> dict[str, object]:
    """Detected OS and effective ``runtime.mux.backend`` for the Config Control.

    Lets the user see before editing whether the current config targets
    ``tmux``, ``rmux``, or ``herdr`` semantics, and on which OS.  ``backend``
    is the effective ``runtime.mux.backend`` from the loaded config
    (``None`` when the config does not declare one — meaning the platform
    default backend applies).
    """
    backend: str | None = None
    try:
        from agents.config_loader import load_project_config

        loaded = load_project_config(project_root)
        config = getattr(loaded, 'config', None)
        backend = str(getattr(config, 'runtime_mux_backend', '') or '').strip() or None
    except Exception:
        backend = None
    return {
        'os_platform': _config_ui_os_platform(),
        'effective_mux_backend': backend,
    }


def _config_ui_os_platform() -> str:
    if sys.platform == 'win32':
        return 'windows'
    if sys.platform == 'darwin':
        return 'darwin'
    if sys.platform.startswith('linux'):
        return 'linux'
    return sys.platform


def _config_ui_herdr_surface_projection(context: CliContext) -> dict[str, object] | None:
    paths = getattr(context, 'paths', None)
    if paths is None:
        return None
    try:
        namespace_state = ProjectNamespaceStateStore(paths).load()
    except Exception:
        return None
    return herdr_surface_projection_from_namespace_state(namespace_state)


def _config_ui_readonly_status(projection: dict[str, object]) -> dict[str, object]:
    capability_status = str(projection.get('capability_status') or '').strip() or 'blocked'
    status = 'pass' if herdr_surface_projection_passes_gate(projection) else 'blocked'
    reason = None if status == 'pass' else f'capability_status={capability_status}'
    return {
        'status': status,
        'backend_impl': 'herdr',
        'reason': reason,
        'degraded_next_action': projection.get('degraded_next_action'),
    }


def config_ui_asset_path() -> Path:
    return Path(__file__).resolve().parents[3] / _CONFIG_UI_RELATIVE_PATH


def config_ui_provider_capabilities(
    *,
    environ: dict[str, str] | None = None,
    project_root: Path | None = None,
    codex_models_path: Path | None = None,
    pi_models_path: Path | None = None,
    cli_models: dict[str, list[str]] | None = None,
    roles: tuple[dict[str, object], ...] | list[dict[str, object]] | None = None,
) -> dict[str, object]:
    env = dict(os.environ if environ is None else environ)
    supported = set(supported_provider_model_shortcuts())
    api_supported = set(supported_provider_api_shortcuts())
    codex_models, codex_source = _codex_models(
        env,
        project_root=project_root,
        explicit_path=codex_models_path,
    )
    pi_models = _pi_models(
        explicit_path=pi_models_path,
        source_home=_pi_models_source_home(env, use_process_identity=environ is None),
    )
    discovered_cli_models = (
        {
            'opencode': _provider_cli_models('opencode', env),
            'mimo': _provider_cli_models('mimo', env),
        }
        if cli_models is None
        else cli_models
    )
    suggestions: dict[str, list[dict[str, object]]] = {
        'codex': codex_models,
        'claude': [
            _model(
                'claude-fable-5',
                'Claude Fable 5',
                reasoning_levels=list(provider_thinking_levels('claude')),
                default_reasoning_level='high',
            ),
            _model(
                'claude-opus-4-8',
                'Claude Opus 4.8',
                reasoning_levels=list(provider_thinking_levels('claude')),
                default_reasoning_level='high',
            ),
            _model(
                'claude-sonnet-5',
                'Claude Sonnet 5',
                reasoning_levels=list(provider_thinking_levels('claude')),
                default_reasoning_level='high',
            ),
            _model(
                'claude-haiku-4-5',
                'Claude Haiku 4.5',
                reasoning_levels=list(provider_thinking_levels('claude')),
                default_reasoning_level='high',
            ),
            _model(
                'sonnet',
                'Sonnet (latest alias)',
                reasoning_levels=list(provider_thinking_levels('claude')),
                default_reasoning_level='high',
            ),
            _model(
                'opus',
                'Opus (latest alias)',
                reasoning_levels=list(provider_thinking_levels('claude')),
                default_reasoning_level='high',
            ),
            _model(
                'haiku',
                'Haiku (latest alias)',
                reasoning_levels=list(provider_thinking_levels('claude')),
                default_reasoning_level='high',
            ),
        ],
        'gemini': [
            _model('gemini-3.5-flash', 'Gemini 3.5 Flash'),
            _model('gemini-3.1-pro-preview', 'Gemini 3.1 Pro Preview'),
            _model('gemini-3.1-flash-lite', 'Gemini 3.1 Flash-Lite'),
            _model('gemini-3-flash-preview', 'Gemini 3 Flash Preview'),
            _model('gemini-flash-latest', 'Gemini Flash (latest alias)'),
        ],
        'deepseek': [
            _model(
                'deepseek-v4-pro',
                'DeepSeek V4 Pro',
                reasoning_levels=['off', 'high', 'max'],
                default_reasoning_level='max',
            ),
            _model(
                'deepseek-v4-flash',
                'DeepSeek V4 Flash',
                reasoning_levels=['off', 'high', 'max'],
                default_reasoning_level='max',
            ),
        ],
        'dsh': [
            _model(
                'deepseek-v4-flash',
                'DeepSeek V4 Flash',
                reasoning_levels=list(provider_thinking_levels('dsh')),
                default_reasoning_level='high',
            ),
            _model(
                'deepseek-v4-pro',
                'DeepSeek V4 Pro',
                reasoning_levels=list(provider_thinking_levels('dsh')),
                default_reasoning_level='high',
            ),
        ],
        'opencode': [_model(model_id, model_id) for model_id in discovered_cli_models.get('opencode', [])],
        'mimo': [_model(model_id, model_id) for model_id in discovered_cli_models.get('mimo', [])],
        'pi': pi_models,
    }
    providers = []
    for provider in (*CORE_PROVIDER_NAMES, *OPTIONAL_PROVIDER_NAMES):
        model_shortcut = provider in supported
        source = 'none'
        if provider == 'codex':
            source = codex_source
        elif provider == 'claude':
            source = 'provider_aliases'
        elif provider == 'gemini':
            source = 'official_suggestions'
        elif provider == 'opencode':
            source = 'provider_cli_cache' if suggestions['opencode'] else 'provider_catalog_required'
        elif provider == 'mimo':
            source = 'provider_cli_cache' if suggestions['mimo'] else 'custom_only'
        elif provider == 'deepseek':
            source = 'deepseek_v4_and_deepcode_contract'
        elif provider == 'dsh':
            source = 'deepseek_harness_official_catalog'
        elif provider == 'pi':
            source = 'pi_models_json'
        providers.append(
            {
                'id': provider,
                'model_shortcut': model_shortcut,
                'api_shortcut': provider in api_supported,
                'model_source': source,
                'models': suggestions.get(provider, []),
                'custom_model': model_shortcut and provider != 'pi',
                'static_thinking': bool(provider_thinking_levels(provider)),
            }
        )
    role_rows = _config_ui_role_catalog() if roles is None else tuple(roles)
    return {
        'schema_version': 1,
        'providers': providers,
        'roles': list(role_rows),
    }


def _config_ui_role_catalog() -> tuple[dict[str, object], ...]:
    # Role bindings are part of the same editor surface as provider/model
    # overlays. Keep the local catalog authoritative without allowing an HTTP
    # request to clone/download a missing default source.
    try:
        from rolepacks.sources import role_catalog_status

        return tuple(
            {
                'v2_selectable': _config_ui_v2_role_selectable(row.get('role_id')),
                **{
                    key: row.get(key)
                    for key in (
                        'role_id',
                        'name',
                        'description',
                        'version',
                        'installed_version',
                        'status',
                        'source',
                        'warning',
                    )
                    if key in row
                },
            }
            for row in role_catalog_status(
                refresh_default=False,
                download_missing_default=False,
            )
        )
    except Exception:
        # A missing/unavailable role source must not make the config editor
        # unusable.  The editor still preserves a currently configured role
        # and the full TOML editor remains available.
        return ()


def _config_ui_v2_role_selectable(role_id: object) -> bool:
    logical_name = str(role_id or '').strip().lower().rsplit('.', 1)[-1]
    return logical_name == 'ccb_self' or not logical_name.startswith('ccb_')


def _codex_models(
    environ: dict[str, str],
    *,
    project_root: Path | None,
    explicit_path: Path | None,
) -> tuple[list[dict[str, object]], str]:
    for path, source in _codex_models_cache_paths(
        environ,
        project_root=project_root,
        explicit_path=explicit_path,
    ):
        try:
            payload = json.loads(path.read_text(encoding='utf-8'))
        except (OSError, ValueError, TypeError):
            payload = {}
        rows = []
        for item in payload.get('models', []) if isinstance(payload, dict) else []:
            if not isinstance(item, dict) or item.get('visibility') != 'list':
                continue
            model_id = str(item.get('slug') or '').strip()
            if not model_id or not (model_id.startswith('gpt-5.6') or model_id == 'gpt-5.5'):
                continue
            levels = []
            for level in item.get('supported_reasoning_levels') or []:
                if not isinstance(level, dict):
                    continue
                effort = str(level.get('effort') or '').strip()
                if effort:
                    levels.append(effort)
            rows.append(
                _model(
                    model_id,
                    str(item.get('display_name') or model_id),
                    reasoning_levels=levels,
                    default_reasoning_level=str(item.get('default_reasoning_level') or '').strip() or None,
                    context_window_max_tokens=(
                        int(item.get('context_window'))
                        if str(item.get('context_window') or '').isdigit()
                        else None
                    ),
                )
            )
        if rows:
            return rows, source
    return [
        _model(
            'gpt-5.6-sol',
            'GPT-5.6 SOL',
            reasoning_levels=['low', 'medium', 'high', 'xhigh', 'max', 'ultra'],
            default_reasoning_level='low',
        ),
        _model(
            'gpt-5.6-terra',
            'GPT-5.6 Terra',
            reasoning_levels=['low', 'medium', 'high', 'xhigh', 'max', 'ultra'],
            default_reasoning_level='medium',
        ),
        _model(
            'gpt-5.6-luna',
            'GPT-5.6 Luna',
            reasoning_levels=['low', 'medium', 'high', 'xhigh', 'max'],
            default_reasoning_level='medium',
        ),
        _model('gpt-5.5', 'GPT-5.5', reasoning_levels=['low', 'medium', 'high', 'xhigh']),
    ], 'ccb_catalog_fallback'


def _source_home_from_capability_environ(environ: dict[str, str]) -> Path | None:
    for name in ('CCB_SOURCE_HOME', 'HOME'):
        raw = str(environ.get(name) or '').strip()
        if raw:
            return Path(raw).expanduser()
    return None


def _pi_models_source_home(
    environ: dict[str, str],
    *,
    use_process_identity: bool,
) -> Path | None:
    if not use_process_identity:
        return _source_home_from_capability_environ(environ)
    try:
        return current_provider_source_home()
    except (OSError, RuntimeError, TypeError, ValueError):
        return None


def _pi_models(
    *,
    explicit_path: Path | None,
    source_home: Path | None,
) -> list[dict[str, object]]:
    path = (
        Path(explicit_path).expanduser()
        if explicit_path is not None
        else Path(source_home).expanduser() / '.pi' / 'agent' / 'models.json'
        if source_home is not None
        else None
    )
    if path is None:
        return []
    try:
        payload = json.loads(path.read_text(encoding='utf-8'))
    except (OSError, ValueError, TypeError):
        return []
    providers = payload.get('providers') if isinstance(payload, dict) else None
    if not isinstance(providers, dict):
        return []
    rows: list[dict[str, object]] = []
    seen: set[str] = set()
    for raw_provider, provider_payload in providers.items():
        if not isinstance(raw_provider, str):
            continue
        provider = raw_provider.strip()
        if not provider or '/' in provider or not isinstance(provider_payload, dict):
            continue
        models = provider_payload.get('models')
        if not isinstance(models, list):
            continue
        for item in models:
            if not isinstance(item, dict):
                continue
            raw_model_id = item.get('id')
            if not isinstance(raw_model_id, str):
                continue
            model_id = raw_model_id.strip()
            if not model_id:
                continue
            qualified_id = f'{provider}/{model_id}'
            if qualified_id in seen:
                continue
            seen.add(qualified_id)
            rows.append(_model(qualified_id, qualified_id))
    return rows


def _codex_models_cache_paths(
    environ: dict[str, str],
    *,
    project_root: Path | None,
    explicit_path: Path | None,
) -> list[tuple[Path, str]]:
    if explicit_path is not None:
        return [(Path(explicit_path), 'codex_cache_explicit')]

    candidates: list[tuple[Path, str]] = []
    configured_home = str(environ.get('CODEX_HOME') or '').strip()
    if configured_home:
        candidates.append((Path(configured_home).expanduser() / 'models_cache.json', 'codex_cache_env'))
    if project_root is not None:
        candidates.extend(
            (path, 'codex_cache_managed')
            for path in Path(project_root).glob(
                '.ccb/agents/*/provider-state/codex/home/models_cache.json'
            )
        )
    home = Path(str(environ.get('HOME') or Path.home())).expanduser()
    candidates.append((home / '.codex' / 'models_cache.json', 'codex_cache_home'))

    unique: dict[Path, str] = {}
    for path, source in candidates:
        resolved = path.resolve()
        if resolved.is_file():
            unique.setdefault(resolved, source)
    return sorted(
        unique.items(),
        key=lambda item: item[0].stat().st_mtime_ns,
        reverse=True,
    )


def _provider_cli_models(program: str, environ: dict[str, str]) -> list[str]:
    executable = shutil.which(program, path=environ.get('PATH'))
    if executable is None:
        return []
    try:
        result = subprocess.run(
            [executable, 'models'],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
            encoding='utf-8',
            errors='replace',
            timeout=0.5,
            check=False,
            env=environ,
        )
    except (OSError, subprocess.SubprocessError):
        return []
    if result.returncode != 0:
        return []
    return list(dict.fromkeys(line.strip() for line in result.stdout.splitlines() if '/' in line.strip()))


def _start_provider_cli_models_probe(environ: dict[str, str]) -> dict[str, object]:
    programs = ('opencode', 'mimo')
    results: dict[str, list[str] | None] = {program: None for program in programs}

    def probe(program: str) -> None:
        results[program] = _provider_cli_models(program, environ)

    workers = [
        threading.Thread(target=probe, args=(program,), daemon=True)
        for program in programs
    ]
    for worker in workers:
        worker.start()
    return {'programs': programs, 'results': results, 'workers': workers}


def _join_provider_cli_models_probe(
    state: dict[str, object],
    *,
    budget_s: float,
) -> tuple[dict[str, list[str]], bool]:
    programs = tuple(str(program) for program in state.get('programs', ()))
    results = state.get('results')
    workers = state.get('workers')
    if not isinstance(results, dict) or not isinstance(workers, list):
        return {program: [] for program in programs}, False
    if budget_s <= 0:
        return {program: list(results.get(program) or []) for program in programs}, False
    deadline = time.monotonic() + budget_s
    for worker in workers:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            break
        worker.join(remaining)
    return (
        {
            program: list(results[program] or [])
            for program in programs
        },
        all(not worker.is_alive() for worker in workers),
    )


def _model(
    model_id: str,
    label: str,
    *,
    reasoning_levels: list[str] | None = None,
    default_reasoning_level: str | None = None,
    context_window_max_tokens: int | None = None,
) -> dict[str, object]:
    return {
        'id': model_id,
        'label': label,
        'reasoning_levels': list(reasoning_levels or []),
        'default_reasoning_level': default_reasoning_level,
        'context_window_max_tokens': context_window_max_tokens,
    }


def _handler_for(
    *,
    page: bytes,
    session_payload: bytes,
    capabilities_payload: Callable[[], bytes],
    config_path: Path,
    project_root: Path,
    path_layout,
    reload_action: Callable[[bool], dict[str, object]],
    history_scan_action: Callable[[int, str | None], dict[str, object]],
    history_cleanup_action: Callable[[int, str | None], dict[str, object]],
    token: str,
    last_activity: list[float],
):
    mutation_lock = threading.Lock()

    class ConfigUiRequestHandler(BaseHTTPRequestHandler):
        server_version = 'CCBConfigUI/1'

        def do_GET(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler API
            parsed = urlparse(self.path)
            if not self._authorized(parsed):
                self._send(HTTPStatus.FORBIDDEN, b'forbidden\n', 'text/plain; charset=utf-8')
                return
            last_activity[0] = time.monotonic()
            if parsed.path in {'/', '/index.html'}:
                self._send(HTTPStatus.OK, page, 'text/html; charset=utf-8')
                return
            if parsed.path == '/api/session':
                self._send(HTTPStatus.OK, session_payload, 'application/json; charset=utf-8')
                return
            if parsed.path == '/api/capabilities':
                self._send(HTTPStatus.OK, capabilities_payload(), 'application/json; charset=utf-8')
                return
            if parsed.path == '/api/theme':
                self._send_json(HTTPStatus.OK, theme_preference_payload())
                return
            if parsed.path == '/api/config':
                try:
                    payload = _config_payload(
                        config_path,
                        project_root=project_root,
                        include_editor=True,
                    )
                    payload['profiles'] = _profiles_payload(project_root)
                except (ConfigValidationError, TypeError, ValueError) as exc:
                    self._send_json(
                        HTTPStatus.UNPROCESSABLE_ENTITY,
                        {'status': 'error', 'error': str(exc)},
                    )
                else:
                    self._send_json(HTTPStatus.OK, payload)
                return
            if parsed.path == '/api/storage/history':
                try:
                    retention_days, agent = _history_request_parameters(parse_qs(parsed.query))
                    payload = history_scan_action(retention_days, agent)
                except ValueError as exc:
                    self._send_json(
                        HTTPStatus.UNPROCESSABLE_ENTITY,
                        {'status': 'error', 'error': str(exc)},
                    )
                except Exception as exc:  # noqa: BLE001 - HTTP boundary returns a structured failure
                    self._send_json(
                        HTTPStatus.INTERNAL_SERVER_ERROR,
                        {'status': 'error', 'error': f'history scan failed: {exc}'},
                    )
                else:
                    self._send_json(HTTPStatus.OK, payload)
                return
            if parsed.path == '/api/profile':
                profile_name = parse_qs(parsed.query).get('name', [''])[0]
                try:
                    profile_payload = _load_profile(profile_name, project_root=project_root)
                except _ConfigUiHttpError as exc:
                    self._send_json(exc.status, {'status': 'error', 'error': exc.message})
                else:
                    self._send_json(HTTPStatus.OK, profile_payload)
                return
            self._send(HTTPStatus.NOT_FOUND, b'not found\n', 'text/plain; charset=utf-8')

        def do_POST(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler API
            parsed = urlparse(self.path)
            if not self._authorized(parsed):
                self._send(HTTPStatus.FORBIDDEN, b'forbidden\n', 'text/plain; charset=utf-8')
                return
            last_activity[0] = time.monotonic()
            try:
                payload = self._read_json_body()
                if parsed.path == '/api/validate':
                    result = _validate_candidate(
                        payload,
                        config_path=config_path,
                        project_root=project_root,
                        path_layout=path_layout,
                    )
                    self._send_json(HTTPStatus.OK, result)
                    return
                if parsed.path == '/api/render':
                    result = _render_candidate_document(
                        payload,
                        config_path=config_path,
                        project_root=project_root,
                        path_layout=path_layout,
                    )
                    self._send_json(HTTPStatus.OK, result)
                    return
                if parsed.path == '/api/preview':
                    status, result = _preview_candidate(
                        payload,
                        config_path=config_path,
                        project_root=project_root,
                        path_layout=path_layout,
                    )
                    self._send_json(status, result)
                    return
                if parsed.path == '/api/apply':
                    status, result = _apply_candidate(
                        payload,
                        config_path=config_path,
                        project_root=project_root,
                        path_layout=path_layout,
                        reload_action=reload_action,
                        mutation_lock=mutation_lock,
                    )
                    self._send_json(status, result)
                    return
                if parsed.path == '/api/reload':
                    status, result = _reload_saved_config(
                        payload,
                        config_path=config_path,
                        reload_action=reload_action,
                        mutation_lock=mutation_lock,
                    )
                    self._send_json(status, result)
                    return
                if parsed.path == '/api/profile':
                    with mutation_lock:
                        result = _save_profile(
                            payload,
                            project_root=project_root,
                            path_layout=path_layout,
                        )
                    self._send_json(HTTPStatus.OK, result)
                    return
                if parsed.path == '/api/theme':
                    with mutation_lock:
                        result = _save_ui_theme(payload)
                    self._send_json(HTTPStatus.OK, result)
                    return
                if parsed.path == '/api/storage/history/cleanup':
                    try:
                        retention_days, agent = _history_request_parameters(payload)
                    except ValueError as exc:
                        raise _ConfigUiHttpError(HTTPStatus.UNPROCESSABLE_ENTITY, str(exc)) from exc
                    with mutation_lock:
                        try:
                            result = history_cleanup_action(retention_days, agent)
                        except ValueError as exc:
                            raise _ConfigUiHttpError(HTTPStatus.UNPROCESSABLE_ENTITY, str(exc)) from exc
                    self._send_json(HTTPStatus.OK, result)
                    return
                self._send(HTTPStatus.NOT_FOUND, b'not found\n', 'text/plain; charset=utf-8')
            except _ConfigUiHttpError as exc:
                self._send_json(exc.status, {'status': 'error', 'error': exc.message})
            except Exception as exc:
                self._send_json(
                    HTTPStatus.INTERNAL_SERVER_ERROR,
                    {'status': 'error', 'error': f'config UI operation failed: {exc}'},
                )

        def log_message(self, _format: str, *_args: object) -> None:
            return

        def _authorized(self, parsed) -> bool:
            supplied_token = parse_qs(parsed.query).get('token', [''])[0]
            return secrets.compare_digest(supplied_token, token)

        def _read_json_body(self) -> dict[str, object]:
            raw_length = self.headers.get('Content-Length')
            try:
                length = int(raw_length or '')
            except ValueError as exc:
                raise _ConfigUiHttpError(HTTPStatus.BAD_REQUEST, 'valid Content-Length is required') from exc
            if length < 0:
                raise _ConfigUiHttpError(HTTPStatus.BAD_REQUEST, 'valid Content-Length is required')
            if length > MAX_REQUEST_BODY_BYTES:
                raise _ConfigUiHttpError(HTTPStatus.REQUEST_ENTITY_TOO_LARGE, 'request body is too large')
            try:
                payload = json.loads(self.rfile.read(length).decode('utf-8'))
            except (UnicodeDecodeError, json.JSONDecodeError) as exc:
                raise _ConfigUiHttpError(HTTPStatus.BAD_REQUEST, 'request body must be UTF-8 JSON') from exc
            if not isinstance(payload, dict):
                raise _ConfigUiHttpError(HTTPStatus.BAD_REQUEST, 'request body must be a JSON object')
            return payload

        def _send_json(self, status: HTTPStatus, payload: dict[str, object]) -> None:
            self._send(
                status,
                json.dumps(payload, ensure_ascii=False).encode('utf-8'),
                'application/json; charset=utf-8',
            )

        def _send(self, status: HTTPStatus, body: bytes, content_type: str) -> None:
            self.send_response(int(status))
            self.send_header('Content-Type', content_type)
            self.send_header('Content-Length', str(len(body)))
            self.send_header('Cache-Control', 'no-store')
            self.send_header('X-Content-Type-Options', 'nosniff')
            self.send_header('Referrer-Policy', 'no-referrer')
            self.end_headers()
            self.wfile.write(body)

    return ConfigUiRequestHandler


class _ConfigUiHttpError(RuntimeError):
    def __init__(self, status: HTTPStatus, message: str) -> None:
        super().__init__(message)
        self.status = status
        self.message = message


def _save_ui_theme(payload: dict[str, object]) -> dict[str, object]:
    value = payload.get('theme')
    if not isinstance(value, str) or not value.strip():
        raise _ConfigUiHttpError(
            HTTPStatus.BAD_REQUEST,
            'theme is required',
        )
    requested = value.strip().lower()
    available = set(theme_preference_payload()['available_themes'])
    if requested not in available:
        raise _ConfigUiHttpError(
            HTTPStatus.UNPROCESSABLE_ENTITY,
            f'unsupported theme: {value}',
        )
    try:
        result = set_theme_preference(requested)
    except ValueError as exc:
        raise _ConfigUiHttpError(
            HTTPStatus.UNPROCESSABLE_ENTITY,
            str(exc),
        ) from exc
    return {'status': 'ok', **result}


def _config_payload(
    config_path: Path,
    *,
    project_root: Path | None = None,
    include_editor: bool = False,
) -> dict[str, object]:
    if not config_path.is_file():
        payload: dict[str, object] = {
            'schema_version': 1,
            'exists': False,
            'path': str(config_path),
            'text': '',
            'digest': None,
        }
        if include_editor:
            assert project_root is not None
            default_text = render_default_project_config_text()
            payload['text'] = default_text
            payload['editor'] = _editor_payload(
                default_text,
                config_path=config_path,
                project_root=project_root,
            )
        return payload
    raw = config_path.read_bytes()
    try:
        text = raw.decode('utf-8').replace('\r\n', '\n').replace('\r', '\n')
    except UnicodeDecodeError as exc:
        raise _ConfigUiHttpError(HTTPStatus.UNPROCESSABLE_ENTITY, 'ccb.config must be UTF-8') from exc
    payload: dict[str, object] = {
        'schema_version': 1,
        'exists': True,
        'path': str(config_path),
        'text': text,
        'digest': hashlib.sha256(raw).hexdigest(),
    }
    if include_editor:
        assert project_root is not None
        payload['editor'] = _editor_payload(
            text,
            config_path=config_path,
            project_root=project_root,
        )
    return payload


def _candidate_text(payload: dict[str, object]) -> str:
    text = payload.get('text')
    if not isinstance(text, str):
        raise _ConfigUiHttpError(HTTPStatus.BAD_REQUEST, 'text must be a string')
    if len(text.encode('utf-8')) > MAX_REQUEST_BODY_BYTES:
        raise _ConfigUiHttpError(HTTPStatus.REQUEST_ENTITY_TOO_LARGE, 'config text is too large')
    return text


def _history_request_parameters(payload: dict[str, object]) -> tuple[int, str | None]:
    raw_days = payload.get('retention_days', 30)
    if isinstance(raw_days, list):
        raw_days = raw_days[0] if raw_days else 30
    try:
        retention_days = int(raw_days)
    except (TypeError, ValueError) as exc:
        raise ValueError('retention_days must be one of: 7, 30, 90') from exc
    if retention_days not in {7, 30, 90}:
        raise ValueError('retention_days must be one of: 7, 30, 90')

    raw_agent = payload.get('agent')
    if isinstance(raw_agent, list):
        raw_agent = raw_agent[0] if raw_agent else None
    agent = str(raw_agent or '').strip().lower()
    return retention_days, None if agent in {'', 'all'} else agent


def _profile_name(value: object) -> str:
    name = str(value or '').strip()
    if not _PROFILE_NAME_PATTERN.fullmatch(name):
        raise _ConfigUiHttpError(
            HTTPStatus.BAD_REQUEST,
            'profile name must start with a letter and contain only letters, digits, dot, dash, or underscore',
        )
    return name


def _profile_path(project_root: Path, name: str) -> Path:
    return project_root / '.ccb' / 'config-profiles' / f'{name}.toml'


def _profiles_payload(project_root: Path) -> list[dict[str, object]]:
    root = project_root / '.ccb' / 'config-profiles'
    if not root.is_dir():
        return []
    return [
        {
            'name': path.stem,
            'path': str(path),
            'size_bytes': path.stat().st_size,
        }
        for path in sorted(root.glob('*.toml'))
        if _PROFILE_NAME_PATTERN.fullmatch(path.stem)
    ]


def _load_profile(name: object, *, project_root: Path) -> dict[str, object]:
    profile_name = _profile_name(name)
    path = _profile_path(project_root, profile_name)
    if not path.is_file():
        raise _ConfigUiHttpError(HTTPStatus.NOT_FOUND, f'config profile not found: {profile_name}')
    try:
        payload = _config_payload(path, project_root=project_root, include_editor=True)
    except (ConfigValidationError, TypeError, ValueError) as exc:
        raise _ConfigUiHttpError(HTTPStatus.UNPROCESSABLE_ENTITY, str(exc)) from exc
    payload.update(status='loaded', name=profile_name)
    return payload


def _save_profile(
    payload: dict[str, object],
    *,
    project_root: Path,
    path_layout,
) -> dict[str, object]:
    name = _profile_name(payload.get('name'))
    text = _candidate_text(payload)
    path = _profile_path(project_root, name)
    validation = _validate_candidate(
        {'text': text},
        config_path=path,
        project_root=project_root,
        path_layout=path_layout,
    )
    backup_path = _backup_config(path)
    atomic_write_text(path, text)
    saved = _config_payload(path)
    return {
        'status': 'saved',
        'name': name,
        'path': str(path),
        'digest': saved['digest'],
        'backup_path': str(backup_path) if backup_path is not None else None,
        'validation': validation,
        'profiles': _profiles_payload(project_root),
    }


def _validate_candidate(
    payload: dict[str, object],
    *,
    config_path: Path,
    project_root: Path,
    path_layout,
) -> dict[str, object]:
    text = _candidate_text(payload)
    try:
        document = parse_config_document_text(
            text,
            path=config_path,
            project_root=project_root,
        )
        config = _validate_document(
            document,
            config_path=config_path,
            project_root=project_root,
            path_layout=path_layout,
        )
    except (ConfigValidationError, ValueError) as exc:
        raise _ConfigUiHttpError(HTTPStatus.UNPROCESSABLE_ENTITY, str(exc)) from exc
    return {
        'status': 'valid',
        'version': 2,
        'agent_names': sorted(config.agents),
        'restart_bound_agents': list(_restart_bound_agent_names(config)),
        'default_agents': list(config.default_agents),
        'warnings': _candidate_warnings(text),
        'editor': _editor_payload(
            text,
            config_path=config_path,
            project_root=project_root,
        ),
    }


def _restart_bound_agent_names(config) -> tuple[str, ...]:
    names: list[str] = []
    for agent_name, spec in sorted(config.agents.items()):
        api = getattr(spec, 'api', None)
        profile = getattr(spec, 'provider_profile', None)
        if (
            str(getattr(api, 'key', '') or '').strip()
            or str(getattr(api, 'url', '') or '').strip()
            or getattr(spec, 'provider_command_template', None) is not None
            or getattr(spec, 'model', None) is not None
            or getattr(spec, 'thinking', None) is not None
            or bool(tuple(getattr(spec, 'startup_args', ()) or ()))
            or bool(dict(getattr(spec, 'env', {}) or {}))
            or (profile is not None and profile != ProviderProfileSpec())
        ):
            names.append(str(agent_name))
    return tuple(names)


def _restart_bound_changed_agents(current_config, candidate_config) -> tuple[str, ...]:
    if candidate_config is None:
        return ()
    names: list[str] = []
    current_agents = dict(getattr(current_config, 'agents', {}) or {})
    candidate_agents = dict(getattr(candidate_config, 'agents', {}) or {})
    for agent_name in sorted(set(current_agents) & set(candidate_agents)):
        current_record = dict(current_agents[agent_name].to_record())
        candidate_record = dict(candidate_agents[agent_name].to_record())
        for record in (current_record, candidate_record):
            record.pop('schema_version', None)
            record.pop('record_type', None)
            record.pop('dispatch_disabled', None)
        if current_record != candidate_record:
            names.append(str(agent_name))
    return tuple(names)


def _validate_document(
    document: dict[str, object],
    *,
    config_path: Path,
    project_root: Path,
    path_layout,
):
    config = validate_project_config(
        document,
        source_path=config_path,
        project_root=project_root,
    )
    if path_layout is not None:
        validate_provider_runtime_home_uniqueness(
            layout=path_layout,
            specs=config.agents.values(),
        )
    return config


def _render_candidate_document(
    payload: dict[str, object],
    *,
    config_path: Path,
    project_root: Path,
    path_layout,
) -> dict[str, object]:
    raw_document = payload.get('document')
    if not isinstance(raw_document, dict):
        raise _ConfigUiHttpError(HTTPStatus.BAD_REQUEST, 'document must be a JSON object')
    document = dict(raw_document)
    try:
        _validate_document(
            document,
            config_path=config_path,
            project_root=project_root,
            path_layout=path_layout,
        )
        text = render_config_document_text(document)
        validation = _validate_candidate(
            {'text': text},
            config_path=config_path,
            project_root=project_root,
            path_layout=path_layout,
        )
    except (ConfigValidationError, TypeError, ValueError) as exc:
        raise _ConfigUiHttpError(HTTPStatus.UNPROCESSABLE_ENTITY, str(exc)) from exc
    return {
        'status': 'rendered',
        'text': text,
        'validation': validation,
        'editor': _editor_payload(
            text,
            config_path=config_path,
            project_root=project_root,
        ),
    }


def _editor_payload(
    text: str,
    *,
    config_path: Path,
    project_root: Path,
) -> dict[str, object]:
    raw_document = parse_config_document_text(
        text,
        path=config_path,
        project_root=project_root,
    )
    config = validate_project_config(
        raw_document,
        source_path=config_path,
        project_root=project_root,
    )
    canonical_text = render_project_config_text(replace(config, windows_explicit=True))
    canonical_document = parse_config_document_text(
        canonical_text,
        path=config_path,
        project_root=project_root,
    )
    if isinstance(raw_document.get('maintenance'), dict):
        canonical_document['maintenance'] = raw_document['maintenance']
    if isinstance(raw_document.get('config_ui'), dict):
        canonical_document['config_ui'] = raw_document['config_ui']
    raw_ui = raw_document.get('ui')
    if isinstance(raw_ui, dict) and isinstance(raw_ui.get('sidebar'), dict):
        canonical_ui = canonical_document.setdefault('ui', {})
        if isinstance(canonical_ui, dict):
            canonical_sidebar = canonical_ui.setdefault('sidebar', {})
            if isinstance(canonical_sidebar, dict):
                canonical_sidebar.update(raw_ui['sidebar'])
    windows = []
    # Compact `cmd` is represented by ProjectConfig.cmd_enabled and may not
    # survive canonical window rendering as an explicit layout leaf.
    visual_supported = not config.cmd_enabled
    for name, layout_text in dict(canonical_document.get('windows') or {}).items():
        tree = _layout_record(parse_layout_spec(str(layout_text)))
        if any(item.get('name') == 'cmd' for item in _layout_leaf_records(tree)):
            visual_supported = False
        windows.append(
            {
                'name': str(name),
                'layout': str(layout_text),
                'tree': tree,
            }
        )
    return {
        'schema_version': 1,
        'source_shape': 'windows' if 'windows' in raw_document else 'compact',
        'comments_present': any('#' in line for line in text.splitlines()),
        'visual_supported': visual_supported,
        'unsupported_reason': None if visual_supported else 'cmd panes require Full TOML editing',
        'document': canonical_document,
        'entry_window': str(canonical_document.get('entry_window') or 'main'),
        'windows': windows,
        'rich_available': shutil.which('ccb-workbench') is not None,
    }


def _layout_record(node) -> dict[str, object]:
    if node.kind == 'leaf':
        leaf = node.leaf
        assert leaf is not None
        return {
            'kind': 'leaf',
            'name': leaf.name,
            'provider': leaf.provider,
            'workspace_mode': 'worktree'
            if str(leaf.workspace_mode or '').strip() == 'worktree'
            else 'inplace',
            'percent': leaf.percent,
        }
    return {
        'kind': node.kind,
        'left': _layout_record(node.left),
        'right': _layout_record(node.right),
    }


def _layout_leaf_records(node: dict[str, object]) -> list[dict[str, object]]:
    if node.get('kind') == 'leaf':
        return [node]
    return [
        *_layout_leaf_records(dict(node.get('left') or {})),
        *_layout_leaf_records(dict(node.get('right') or {})),
    ]


def _candidate_warnings(text: str) -> list[str]:
    lowered = text.lower()
    if any(marker in lowered for marker in ('api_key =', 'apikey =', 'token =', 'secret =')):
        return ['candidate may contain an inline secret; prefer an environment-variable reference']
    return []


def _preview_candidate(
    payload: dict[str, object],
    *,
    config_path: Path,
    project_root: Path,
    path_layout,
) -> tuple[HTTPStatus, dict[str, object]]:
    text = _candidate_text(payload)
    if 'expected_digest' not in payload:
        raise _ConfigUiHttpError(HTTPStatus.BAD_REQUEST, 'expected_digest is required')
    expected_digest = payload.get('expected_digest')
    if expected_digest is not None and not isinstance(expected_digest, str):
        raise _ConfigUiHttpError(HTTPStatus.BAD_REQUEST, 'expected_digest must be a string or null')
    validation = _validate_candidate(
        {'text': text},
        config_path=config_path,
        project_root=project_root,
        path_layout=path_layout,
    )
    current = _config_payload(config_path)
    if current['digest'] != expected_digest:
        return HTTPStatus.CONFLICT, {
            'status': 'conflict',
            'error': 'ccb.config changed outside this editor; reload before previewing',
            'current_digest': current['digest'],
        }
    before = str(current.get('text') or '')
    diff = ''.join(
        difflib.unified_diff(
            before.splitlines(keepends=True),
            text.splitlines(keepends=True),
            fromfile='.ccb/ccb.config (active)',
            tofile='.ccb/ccb.config (candidate)',
        )
    )
    return HTTPStatus.OK, {
        'status': 'previewed',
        'changed': before != text,
        'diff': diff,
        'validation': validation,
        'digest': expected_digest,
    }


def _apply_candidate(
    payload: dict[str, object],
    *,
    config_path: Path,
    project_root: Path,
    path_layout,
    reload_action: Callable[[bool], dict[str, object]],
    mutation_lock: threading.Lock,
) -> tuple[HTTPStatus, dict[str, object]]:
    text = _candidate_text(payload)
    if 'expected_digest' not in payload:
        raise _ConfigUiHttpError(HTTPStatus.BAD_REQUEST, 'expected_digest is required')
    expected_digest = payload.get('expected_digest')
    if expected_digest is not None and not isinstance(expected_digest, str):
        raise _ConfigUiHttpError(HTTPStatus.BAD_REQUEST, 'expected_digest must be a string or null')
    mode = str(payload.get('mode') or 'save')
    if mode not in {'save', 'hot_reload'}:
        raise _ConfigUiHttpError(HTTPStatus.BAD_REQUEST, 'mode must be save or hot_reload')
    validation = _validate_candidate(
        {'text': text},
        config_path=config_path,
        project_root=project_root,
        path_layout=path_layout,
    )
    candidate_config = None
    if mode == 'hot_reload':
        candidate_config = _validate_document(
            parse_config_document_text(
                text,
                path=config_path,
                project_root=project_root,
            ),
            config_path=config_path,
            project_root=project_root,
            path_layout=path_layout,
        )

    with mutation_lock:
        current = _config_payload(config_path)
        if current['digest'] != expected_digest:
            return HTTPStatus.CONFLICT, {
                'status': 'conflict',
                'error': 'ccb.config changed outside this editor; reload the current config before saving',
                'current_digest': current['digest'],
            }
        changed = str(current.get('text') or '') != text
        restart_change_agents: tuple[str, ...] = ()
        if mode == 'hot_reload' and bool(current.get('exists')):
            current_config = _validate_document(
                parse_config_document_text(
                    str(current.get('text') or ''),
                    path=config_path,
                    project_root=project_root,
                ),
                config_path=config_path,
                project_root=project_root,
                path_layout=path_layout,
            )
            restart_change_agents = _restart_bound_changed_agents(
                current_config,
                candidate_config,
            )
        backup_path = _backup_config(config_path) if changed else None
        if changed:
            atomic_write_text(config_path, text)
            saved = _config_payload(config_path)
        else:
            saved = current
        validation = _validate_candidate(
            {'text': str(saved['text'])},
            config_path=config_path,
            project_root=project_root,
            path_layout=path_layout,
        )
        result: dict[str, object] = {
            'status': 'saved',
            'saved': True,
            'changed': changed,
            'digest': saved['digest'],
            'backup_path': str(backup_path) if backup_path is not None else None,
            'validation': validation,
        }
        if mode == 'save':
            intent = (
                record_config_restart_intent(
                    project_root,
                    target_config_digest=str(saved['digest']),
                    affected_agents=validation.get('restart_bound_agents') or (),
                    reason='active_config_saved',
                    layout=path_layout,
                )
                if changed
                else None
            )
            result.update(
                restart_required=changed,
                restart_intent=intent.to_record() if changed else None,
            )
            return HTTPStatus.OK, result

        try:
            dry_run = dict(reload_action(True))
        except Exception as exc:
            if restart_change_agents:
                dry_run = {
                    'status': 'unavailable',
                    'plan_class': 'replace_agent',
                    'future_safe_to_apply': False,
                    'operations': [
                        {'op': 'replace_agent', 'agent': agent_name}
                        for agent_name in restart_change_agents
                    ],
                }
                result['dry_run'] = dry_run
                result['reload_warning'] = str(exc)
                intent = record_config_restart_intent(
                    project_root,
                    target_config_digest=str(saved['digest']),
                    affected_agents=restart_change_agents,
                    reason='provider_launch_config_changed',
                    layout=path_layout,
                )
                result.update(
                    status='restart_required',
                    restart_required=True,
                    restart_intent=intent.to_record(),
                    affected_agents=list(restart_change_agents),
                )
                return HTTPStatus.OK, result
            result.update(status='reload_unavailable', error=str(exc), dry_run=None)
            return HTTPStatus.SERVICE_UNAVAILABLE, result
        result['dry_run'] = dry_run
        restart_agents = _restart_agents_from_dry_run(
            dry_run,
            fallback=(
                restart_change_agents
                or validation.get('restart_bound_agents')
                or ()
            ),
        )
        if _dry_run_requires_provider_restart(
            dry_run,
            restart_bound_agents=validation.get('restart_bound_agents') or (),
            changed_agents=restart_change_agents,
        ):
            intent = record_config_restart_intent(
                project_root,
                target_config_digest=str(saved['digest']),
                affected_agents=restart_agents,
                reason='provider_launch_config_changed',
                layout=path_layout,
            )
            result.update(
                status='restart_required',
                restart_required=True,
                restart_intent=intent.to_record(),
                affected_agents=list(restart_agents),
            )
            return HTTPStatus.OK, result
        if not _dry_run_allows_apply(dry_run):
            result.update(status='reload_blocked', error='reload dry-run did not allow apply')
            return HTTPStatus.CONFLICT, result
        try:
            reload_result = dict(reload_action(False))
        except Exception as exc:
            result.update(status='reload_failed', error=str(exc), reload=None)
            return HTTPStatus.SERVICE_UNAVAILABLE, result
        result['reload'] = reload_result
        if str(reload_result.get('status') or '') not in {'published', 'noop'}:
            result.update(status='reload_blocked', error='daemon did not publish the saved config')
            return HTTPStatus.CONFLICT, result
        result['status'] = 'reloaded'
        discard_config_restart_intent_for_digest(
            project_root,
            str(saved['digest']),
            layout=path_layout,
        )
        return HTTPStatus.OK, result


def _backup_config(config_path: Path) -> Path | None:
    if not config_path.is_file():
        return None
    backup_path = config_path.with_name(f'{config_path.name}.bak.{time.time_ns()}')
    shutil.copy2(config_path, backup_path)
    return backup_path


def _dry_run_allows_apply(payload: dict[str, object]) -> bool:
    return (
        str(payload.get('status') or '') == 'ok'
        and bool(payload.get('future_safe_to_apply'))
    )


def _dry_run_requires_provider_restart(
    payload: dict[str, object],
    *,
    restart_bound_agents,
    changed_agents=(),
) -> bool:
    if bool(tuple(changed_agents or ())):
        return True
    plan_class = str(payload.get('plan_class') or '')
    if plan_class == 'replace_agent':
        return True
    return plan_class == 'no_change' and bool(tuple(restart_bound_agents or ()))


def _restart_agents_from_dry_run(
    payload: dict[str, object],
    *,
    fallback,
) -> tuple[str, ...]:
    agents = {
        str(item.get('agent') or '').strip().lower()
        for item in tuple(payload.get('operations') or ())
        if isinstance(item, dict)
        and str(item.get('op') or '') == 'replace_agent'
        and str(item.get('agent') or '').strip()
    }
    if not agents:
        agents.update(
            str(item or '').strip().lower()
            for item in tuple(fallback or ())
            if str(item or '').strip()
        )
    return tuple(sorted(agents))


def _reload_saved_config(
    payload: dict[str, object],
    *,
    config_path: Path,
    reload_action: Callable[[bool], dict[str, object]],
    mutation_lock: threading.Lock,
) -> tuple[HTTPStatus, dict[str, object]]:
    if payload.get('dry_run') is not True:
        raise _ConfigUiHttpError(HTTPStatus.BAD_REQUEST, 'only dry_run=true is supported')
    expected_digest = payload.get('expected_digest')
    if expected_digest is not None and not isinstance(expected_digest, str):
        raise _ConfigUiHttpError(HTTPStatus.BAD_REQUEST, 'expected_digest must be a string or null')
    with mutation_lock:
        current = _config_payload(config_path)
        if current['digest'] != expected_digest:
            return HTTPStatus.CONFLICT, {
                'status': 'conflict',
                'error': 'the editor draft is not the saved active config',
                'current_digest': current['digest'],
            }
        try:
            result = dict(reload_action(True))
        except Exception as exc:
            return HTTPStatus.SERVICE_UNAVAILABLE, {
                'status': 'reload_unavailable',
                'error': str(exc),
            }
    status = HTTPStatus.OK if str(result.get('status') or '') == 'ok' else HTTPStatus.CONFLICT
    return status, {'status': 'dry_run', 'result': result}


__all__ = [
    'ConfigUiHandle',
    'config_ui_asset_path',
    'config_ui_provider_capabilities',
    'open_config_ui_url',
    'prepare_config_ui',
]
