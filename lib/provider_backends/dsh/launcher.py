from __future__ import annotations

from datetime import datetime, timezone
import json
import os
from pathlib import Path
import shlex
import sys
import time
import uuid

from agents.models import AgentSpec
from cli.context import CliContext
from cli.models import ParsedStartCommand
from provider_backends.native_cli_support.home import build_native_private_env
from provider_backends.session_authority import current_provider_authority_fingerprint
from provider_core.caller_env import (
    caller_context_env,
    export_env_clause,
    join_env_prefix,
    provider_user_session_env,
)
from provider_core.contracts import ProviderRuntimeLauncher
from provider_core.one_way_inheritance import ensure_private_directory
from provider_core.pathing import session_filename_for_agent
from provider_core.runtime_shared import apply_provider_command_template, provider_start_parts
from provider_profiles import load_resolved_provider_profile
from storage.atomic import atomic_write_json
from workspace.models import WorkspacePlan

from .control import dsh_rpc, load_dsh_host_endpoint
from .home import materialize_dsh_home


def build_runtime_launcher() -> ProviderRuntimeLauncher:
    return ProviderRuntimeLauncher(
        provider='dsh',
        launch_mode='simple_tmux',
        prepare_launch_context=prepare_launch_context,
        build_start_cmd=build_start_cmd,
        build_session_payload=build_session_payload,
        post_launch=post_launch,
    )


def prepare_launch_context(
    context: CliContext,
    spec: AgentSpec,
    plan: WorkspacePlan,
    runtime_dir: Path,
    prepared_state: dict[str, object],
) -> dict[str, object]:
    payload = dict(prepared_state or {})
    state_dir = context.paths.agent_provider_state_dir(spec.name, 'dsh')
    home_dir = state_dir / 'home'
    data_dir = state_dir / 'data'
    agents_home_dir = data_dir / 'agents-home'
    state_dir.mkdir(parents=True, exist_ok=True)
    data_dir.mkdir(parents=True, exist_ok=True)
    ensure_private_directory(agents_home_dir)
    endpoint_state = Path(runtime_dir) / 'dsh-host.json'
    host_instance_id = f'dsh-host-{uuid.uuid4()}'
    profile = load_resolved_provider_profile(Path(runtime_dir))
    workspace_path = Path(str(payload.get('run_cwd') or plan.workspace_path))
    materialize_dsh_home(
        home_dir,
        profile=profile,
        project_root=context.project.project_root,
        workspace_path=workspace_path,
        agent_name=spec.name,
        runtime_dir=Path(runtime_dir),
        event_path=context.paths.agent_events_path(spec.name),
    )
    authority_fingerprint = current_provider_authority_fingerprint(
        'dsh',
        profile,
        Path(runtime_dir),
    )
    # Fence a stale ready record before the replacement host is launched.
    atomic_write_json(
        endpoint_state,
        {
            'schema_version': 1,
            'record_type': 'dsh_host_state',
            'provider': 'dsh',
            'status': 'pending_launch',
            'host_instance_id': host_instance_id,
            'endpoint': None,
            'wrapper_pid': None,
            'child_pid': None,
            'updated_at': datetime.now(timezone.utc).isoformat().replace('+00:00', 'Z'),
        },
    )
    payload.update(
        {
            'agent_name': spec.name,
            'project_root': str(context.project.project_root),
            'workspace_path': str(workspace_path),
            'agent_events_path': str(context.paths.agent_events_path(spec.name)),
            'dsh_state_dir': str(state_dir),
            'dsh_home': str(home_dir),
            'dsh_data_dir': str(data_dir),
            'dsh_agents_home': str(agents_home_dir),
            'dsh_endpoint_state_path': str(endpoint_state),
            'dsh_host_instance_id': host_instance_id,
            'dsh_profile_env': dict(getattr(profile, 'env', {}) or {}),
            'dsh_model': str(spec.model or '').strip(),
            'dsh_model_provider': str(
                spec.env.get('CCB_DSH_MODEL_PROVIDER')
                or (getattr(profile, 'env', {}) or {}).get('CCB_DSH_MODEL_PROVIDER')
                or 'deepseek-official'
            ).strip(),
            'dsh_reasoning_effort': str(spec.thinking or '').strip(),
            'dsh_provider_authority_fingerprint': authority_fingerprint,
            'dsh_session_file': str(
                context.paths.ccb_dir / session_filename_for_agent('dsh', spec.name)
            ),
        }
    )
    resume = _resume_candidate(
        Path(str(payload['dsh_session_file'])),
        project_id=context.project.project_id,
        agent_name=spec.name,
        workspace_path=workspace_path,
        authority_fingerprint=authority_fingerprint,
    )
    if resume:
        payload['dsh_resume_session_id'] = resume[0]
        payload['dsh_context_generation'] = resume[1]
    return payload


def build_start_cmd(
    command: ParsedStartCommand,
    spec: AgentSpec,
    runtime_dir,
    launch_session_id: str,
    *,
    prepared_state: dict[str, object] | None = None,
) -> str:
    prepared = prepared_state if prepared_state is not None else {}
    runtime_dir = Path(runtime_dir)
    endpoint_state = _required_path(prepared, 'dsh_endpoint_state_path')
    home_dir = _required_path(prepared, 'dsh_home')
    data_dir = _required_path(prepared, 'dsh_data_dir')
    agents_home_dir = ensure_private_directory(
        Path(str(prepared.get('dsh_agents_home') or data_dir / 'agents-home')).expanduser()
    )
    prepared['dsh_agents_home'] = str(agents_home_dir)

    if command.restore and str(prepared.get('dsh_resume_session_id') or '').strip():
        native_session_id = str(prepared['dsh_resume_session_id']).strip()
        prepared['dsh_resume_status'] = 'exact_session_selected'
    else:
        native_session_id = f'session-{uuid.uuid4()}'
        prepared['dsh_context_generation'] = 0
        prepared['dsh_resume_status'] = 'fresh_restore_disabled' if not command.restore else 'fresh_no_binding'
    prepared['dsh_session_id'] = native_session_id
    prepared['dsh_auto_permission_enabled'] = bool(command.auto_permission)

    child_parts = _dsh_web_command((*provider_start_parts('dsh'), *spec.startup_args))
    child_rendered = ' '.join(shlex.quote(str(part)) for part in child_parts)
    child_rendered = apply_provider_command_template(child_rendered, spec.provider_command_template)
    wrapper = [
        sys.executable,
        '-m',
        'provider_backends.dsh.host_runtime',
        '--state-file',
        str(endpoint_state),
        '--instance-id',
        _required_text(prepared, 'dsh_host_instance_id'),
    ]
    if spec.provider_command_template:
        wrapper.extend(['--shell-command', child_rendered])
    else:
        wrapper.extend(['--command-json', json.dumps(child_parts, ensure_ascii=False)])
    wrapper_cmd = ' '.join(shlex.quote(str(part)) for part in wrapper)

    private_env = build_native_private_env(
        home_dir,
        data_dir=data_dir,
        extra_path_env_names=('DSH_HOME', 'DSH_AGENTS_HOME'),
    )
    private_env.update(
        {
            'DSH_HOME': str(home_dir),
            # Keep DSH's compatibility ~/.agents scan isolated without
            # scanning the same managed skills root twice.
            'DSH_AGENTS_HOME': str(agents_home_dir),
            # Login shells used by the pane carrier do not reliably preserve
            # the daemon's PYTHONPATH.  Keep the CCB host wrapper importable in
            # both an installed build and a source ``ccb_test`` run.
            'PYTHONPATH': _runtime_pythonpath(),
        }
    )
    profile_env = {
        str(key): str(value)
        for key, value in dict(prepared.get('dsh_profile_env') or {}).items()
        if str(key) not in {'CCB_DSH_MODEL_PROVIDER'}
    }
    env_prefix = join_env_prefix(
        export_env_clause(provider_user_session_env()),
        export_env_clause(profile_env),
        export_env_clause(spec.env),
        # The managed state boundary wins over user/profile home overrides.
        export_env_clause(private_env),
        export_env_clause(
            caller_context_env(
                actor=spec.name,
                runtime_dir=runtime_dir,
                launch_session_id=launch_session_id,
            )
        ),
    )
    return f'{env_prefix}; {wrapper_cmd}' if env_prefix else wrapper_cmd


def build_session_payload(
    context: CliContext,
    spec: AgentSpec,
    plan: WorkspacePlan,
    runtime_dir,
    run_cwd,
    pane_id: str,
    pane_title_marker: str,
    start_cmd: str,
    launch_session_id: str,
    prepared_state: dict[str, object],
) -> dict[str, object]:
    prepared = prepared_state or {}
    return {
        'ccb_session_id': launch_session_id,
        'dsh_session_id': str(prepared.get('dsh_session_id') or ''),
        'dsh_context_generation': int(prepared.get('dsh_context_generation') or 0),
        'agent_name': spec.name,
        'provider': 'dsh',
        'ccb_project_id': context.project.project_id,
        'runtime_dir': str(runtime_dir),
        'completion_artifact_dir': str(Path(runtime_dir) / 'completion'),
        'terminal': 'tmux',
        'tmux_session': pane_id,
        'pane_id': pane_id,
        'pane_title_marker': pane_title_marker,
        'workspace_path': str(plan.workspace_path),
        'work_dir': str(run_cwd),
        'start_dir': str(context.project.project_root),
        'start_cmd': start_cmd,
        'dsh_state_dir': str(prepared.get('dsh_state_dir') or ''),
        'dsh_home': str(prepared.get('dsh_home') or ''),
        'dsh_data_dir': str(prepared.get('dsh_data_dir') or ''),
        'dsh_agents_home': str(prepared.get('dsh_agents_home') or ''),
        'dsh_endpoint_state_path': str(prepared.get('dsh_endpoint_state_path') or ''),
        'dsh_host_instance_id': str(prepared.get('dsh_host_instance_id') or ''),
        'dsh_session_file_path': str(prepared.get('dsh_session_file') or ''),
        'dsh_model_provider': str(prepared.get('dsh_model_provider') or 'deepseek-official'),
        'dsh_model': str(prepared.get('dsh_model') or ''),
        'dsh_reasoning_effort': str(prepared.get('dsh_reasoning_effort') or ''),
        'dsh_provider_authority_fingerprint': str(
            prepared.get('dsh_provider_authority_fingerprint') or ''
        ),
        'dsh_auto_permission_enabled': bool(prepared.get('dsh_auto_permission_enabled')),
        'dsh_resume_status': str(prepared.get('dsh_resume_status') or 'fresh_no_binding'),
    }


def post_launch(
    backend: object,
    pane_id: str,
    runtime_dir: Path,
    launch_session_id: str,
    prepared_state: dict[str, object],
) -> None:
    del runtime_dir, launch_session_id
    state_path = _required_path(prepared_state, 'dsh_endpoint_state_path')
    host_instance_id = _required_text(prepared_state, 'dsh_host_instance_id')
    timeout = _positive_float(os.environ.get('CCB_DSH_START_TIMEOUT_S'), 30.0)
    deadline = time.monotonic() + timeout
    last_error = 'host state not published'
    while time.monotonic() < deadline:
        try:
            endpoint = load_dsh_host_endpoint(
                state_path,
                expected_instance_id=host_instance_id,
            )
            dsh_rpc(endpoint, 'host.describe', {}, timeout=min(3.0, timeout))
            prepared_state['dsh_endpoint'] = endpoint
            return
        except Exception as exc:
            last_error = str(exc)
            terminal_detail = _host_terminal_detail(
                state_path,
                expected_instance_id=host_instance_id,
            )
            if terminal_detail is not None:
                raise RuntimeError(f'dsh host failed before readiness: {terminal_detail}')
            pane_alive = getattr(backend, 'is_pane_alive', None)
            if callable(pane_alive):
                try:
                    if not pane_alive(pane_id):
                        raise RuntimeError(
                            f'dsh host carrier exited before readiness: {last_error}'
                        )
                except RuntimeError:
                    raise
                except Exception:
                    # The endpoint-state record remains the service authority;
                    # an unavailable carrier probe must not overwrite it.
                    pass
            time.sleep(0.1)
    raise RuntimeError(f'dsh host did not become ready within {timeout:g}s: {last_error}')


def _runtime_pythonpath() -> str:
    lib_root = str(Path(__file__).resolve().parents[2])
    existing = str(os.environ.get('PYTHONPATH') or '').strip()
    if not existing:
        return lib_root
    parts = [part for part in existing.split(os.pathsep) if part]
    if lib_root not in parts:
        parts.insert(0, lib_root)
    return os.pathsep.join(parts)


def _host_terminal_detail(
    state_path: Path,
    *,
    expected_instance_id: str,
) -> str | None:
    try:
        payload = json.loads(state_path.read_text(encoding='utf-8'))
    except (OSError, json.JSONDecodeError, TypeError):
        return None
    if not isinstance(payload, dict):
        return None
    if str(payload.get('host_instance_id') or '') != expected_instance_id:
        return None
    status = str(payload.get('status') or '').strip().lower()
    if status not in {'failed', 'stopped'}:
        return None
    detail = str(payload.get('detail') or '').strip()
    return detail or status


def _dsh_web_command(parts: tuple[str, ...]) -> list[str]:
    command = [str(part) for part in parts if str(part)]
    if not command:
        command = ['dsh']
    if 'web' not in command[1:]:
        command.append('web')
    host = _option_value(command, '--host')
    if host is None:
        command.extend(['--host', '127.0.0.1'])
    elif host != '127.0.0.1':
        raise ValueError('managed DSH host must bind explicitly to 127.0.0.1')
    if not _has_option(command, '--port'):
        command.extend(['--port', '0'])
    return command


def _has_option(parts: list[str], option: str) -> bool:
    return any(part == option or part.startswith(f'{option}=') for part in parts)


def _option_value(parts: list[str], option: str) -> str | None:
    for index, part in enumerate(parts):
        if part.startswith(f'{option}='):
            value = part.split('=', 1)[1].strip()
            if not value:
                raise ValueError(f'{option} requires a value')
            return value
        if part == option:
            if index + 1 >= len(parts) or not str(parts[index + 1]).strip():
                raise ValueError(f'{option} requires a value')
            return str(parts[index + 1]).strip()
    return None


def _resume_candidate(
    session_file: Path,
    *,
    project_id: str,
    agent_name: str,
    workspace_path: Path,
    authority_fingerprint: str,
) -> tuple[str, int] | None:
    try:
        payload = json.loads(Path(session_file).read_text(encoding='utf-8-sig'))
    except Exception:
        return None
    if not isinstance(payload, dict):
        return None
    if str(payload.get('provider') or '') != 'dsh':
        return None
    if str(payload.get('ccb_project_id') or '') != str(project_id):
        return None
    if str(payload.get('agent_name') or '') != str(agent_name):
        return None
    if _normalized_path(payload.get('work_dir')) != _normalized_path(workspace_path):
        return None
    if str(payload.get('dsh_provider_authority_fingerprint') or '') != str(
        authority_fingerprint or ''
    ):
        return None
    value = str(payload.get('dsh_session_id') or '').strip()
    if not value:
        return None
    try:
        generation = max(0, int(payload.get('dsh_context_generation') or 0))
    except (TypeError, ValueError):
        generation = 0
    return value, generation


def _normalized_path(value: object) -> str:
    try:
        return str(Path(str(value or '')).expanduser().resolve())
    except Exception:
        return str(value or '').strip()


def _required_path(payload: dict[str, object], key: str) -> Path:
    value = str(payload.get(key) or '').strip()
    if not value:
        raise RuntimeError(f'dsh launch requires {key}')
    return Path(value).expanduser()


def _required_text(payload: dict[str, object], key: str) -> str:
    value = str(payload.get(key) or '').strip()
    if not value:
        raise RuntimeError(f'dsh launch context omitted {key}')
    return value


def _positive_float(value: object, default: float) -> float:
    try:
        return max(0.1, float(value))
    except (TypeError, ValueError):
        return default


__all__ = [
    'build_runtime_launcher',
    'build_session_payload',
    'build_start_cmd',
    'post_launch',
    'prepare_launch_context',
]
