from __future__ import annotations

from pathlib import Path
import json
import shlex

from agents.policy import should_restore_provider_history
from provider_core.caller_env import (
    caller_context_env,
    export_env_clause,
    join_env_prefix,
    provider_user_session_env,
)
from provider_core.contracts import ProviderRuntimeLauncher
from provider_core.runtime_shared import apply_provider_command_template


_ROOT_SANDBOX_ENV = {'IS_SANDBOX': '1'}
_ROOT_SKIP_PERMISSIONS_FLAG = '--dangerously-skip-permissions'
_SENSITIVE_PERSISTED_ENV = {'ANTHROPIC_API_KEY', 'ANTHROPIC_AUTH_TOKEN'}
_SHELL_OPERATORS = {';', '&&', '||', '|', '&', '<', '>', '<<', '>>'}


def build_runtime_launcher(
    *,
    prepare_runtime_fn,
    prepare_launch_context_fn,
    build_start_cmd_fn,
    build_session_payload_fn,
    resolve_run_cwd_fn,
) -> ProviderRuntimeLauncher:
    return ProviderRuntimeLauncher(
        provider='claude',
        launch_mode='simple_tmux',
        prepare_runtime=prepare_runtime_fn,
        prepare_launch_context=prepare_launch_context_fn,
        build_start_cmd=build_start_cmd_fn,
        build_session_payload=build_session_payload_fn,
        resolve_run_cwd=resolve_run_cwd_fn,
    )


def prepare_runtime(runtime_dir: Path) -> dict[str, object]:
    runtime_dir.mkdir(parents=True, exist_ok=True)
    return {}


def prepare_launch_context(context, spec, plan, runtime_dir: Path, prepared_state: dict[str, object]) -> dict[str, object]:
    payload = dict(prepared_state or {})
    payload['project_root'] = str(context.project.project_root)
    payload['workspace_path'] = str(payload.get('run_cwd') or plan.workspace_path)
    payload['agent_events_path'] = str(context.paths.agent_events_path(spec.name))
    return payload


def build_start_cmd(
    command,
    spec,
    runtime_dir: Path,
    launch_session_id: str,
    *,
    load_profile_fn,
    prepare_home_overrides_fn,
    write_settings_overlay_fn,
    build_env_prefix_fn,
    resolve_restore_target_fn,
    provider_start_parts_fn,
    cli_supports_flag_fn,
    is_root_user_fn,
    prepared_state: dict[str, object] | None = None,
) -> str:
    launch_context = prepared_state if isinstance(prepared_state, dict) else {}
    launch_context.pop('ccb_continuation_launch_mode', None)
    root_user = bool(is_root_user_fn())
    profile = load_profile_fn(runtime_dir)
    restore_target = resolve_restore_target_fn(
        spec=spec,
        runtime_dir=runtime_dir,
        restore=should_restore_provider_history(spec.restore_default, cli_restore=command.restore),
    )
    home_overrides = prepare_home_overrides_fn(
        runtime_dir,
        profile,
        auto_permission=bool(command.auto_permission),
        agent_name=spec.name,
        workspace_path=restore_target.run_cwd,
    )
    settings_path = write_settings_overlay_fn(runtime_dir, profile=profile)
    if command.auto_permission:
        settings_path = _ensure_skip_prompt_settings(runtime_dir, settings_path)
        _ensure_skip_prompt_home_settings(home_overrides)
        _ensure_bypass_permission_acceptance(home_overrides, project_root=restore_target.run_cwd)
    env_prefix = join_env_prefix(
        build_env_prefix_fn(profile=profile, extra_env=spec.env),
        export_env_clause(
            {
                'DISABLE_AUTOUPDATER': '1',
                # A managed Claude process inherits a private credential copy.
                # Disable /login and /logout so neither command can reach an
                # ambient OS credential backend and mutate the user's external
                # login. Authentication changes are made outside CCB and
                # inherited again on the next managed start.
                'DISABLE_LOGIN_COMMAND': '1',
                'DISABLE_LOGOUT_COMMAND': '1',
            }
        ),
        export_env_clause(provider_user_session_env()),
        export_env_clause(home_overrides),
        export_env_clause(_ROOT_SANDBOX_ENV if root_user else {}),
        export_env_clause(
            caller_context_env(actor=spec.name, runtime_dir=runtime_dir, launch_session_id=launch_session_id)
        ),
    )
    cmd_parts = provider_start_parts_fn('claude')
    if root_user:
        _append_unique_flag(cmd_parts, _ROOT_SKIP_PERMISSIONS_FLAG, spec.startup_args)
    if cli_supports_flag_fn(cmd_parts, '--setting-sources'):
        cmd_parts.extend(['--setting-sources', 'user,project,local'])
    if settings_path is not None and cli_supports_flag_fn(cmd_parts, '--settings'):
        try:
            settings_inline = json.dumps(
                json.loads(settings_path.read_text(encoding='utf-8')),
                ensure_ascii=False,
            )
            cmd_parts.extend(['--settings', settings_inline])
        except Exception:
            cmd_parts.extend(['--settings', str(settings_path)])
    if command.auto_permission:
        if cli_supports_flag_fn(cmd_parts, '--permission-mode'):
            cmd_parts.extend(['--permission-mode', 'bypassPermissions'])
        else:
            _append_unique_flag(cmd_parts, _ROOT_SKIP_PERMISSIONS_FLAG, spec.startup_args)
    if (
        restore_target.continuation_mode == 'fork'
        and restore_target.continuation_session_id
        and cli_supports_flag_fn(cmd_parts, '--fork-session')
    ):
        launch_context['ccb_continuation_launch_mode'] = 'fork'
        cmd_parts.extend(
            [
                '--resume',
                restore_target.continuation_session_id,
                '--fork-session',
            ]
        )
    elif restore_target.has_history:
        cmd_parts.append('--continue')
    cmd_parts.extend(spec.startup_args)

    cmd = ' '.join(shlex.quote(str(part)) for part in cmd_parts)
    cmd = apply_provider_command_template(cmd, spec.provider_command_template)
    if env_prefix:
        return f'{env_prefix}; {cmd}'
    return cmd


def resolve_run_cwd(
    command,
    spec,
    plan,
    runtime_dir: Path,
    launch_session_id: str | None,
    *,
    resolve_restore_target_fn,
) -> Path | str | None:
    del launch_session_id
    return resolve_restore_target_fn(
        spec=spec,
        runtime_dir=runtime_dir,
        workspace_path=plan.workspace_path,
        restore=should_restore_provider_history(spec.restore_default, cli_restore=command.restore),
    ).run_cwd


def build_session_payload(
    context,
    spec,
    plan,
    runtime_dir: Path,
    run_cwd: Path,
    pane_id: str,
    pane_title_marker: str,
    start_cmd: str,
    launch_session_id: str,
    prepared_state: dict[str, object],
) -> dict[str, object]:
    layout = prepared_state.get('claude_home_layout')
    persisted_start_cmd = _persistable_start_cmd(
        start_cmd,
        settings_path=runtime_dir / 'claude-settings.json',
    )
    payload = {
        'ccb_session_id': launch_session_id,
        'agent_name': spec.name,
        'ccb_project_id': context.project.project_id,
        'runtime_dir': str(runtime_dir),
        'completion_artifact_dir': str(runtime_dir / 'completion'),
        'terminal': 'tmux',
        'tmux_session': pane_id,
        'pane_id': pane_id,
        'pane_title_marker': pane_title_marker,
        'workspace_path': str(plan.workspace_path),
        'work_dir': str(run_cwd),
        'start_dir': str(context.project.project_root),
        'claude_start_cmd': persisted_start_cmd,
        'start_cmd': persisted_start_cmd,
    }
    if layout is not None:
        payload['claude_home'] = str(layout.home_root)
        payload['claude_projects_root'] = str(layout.projects_root)
        payload['claude_session_env_root'] = str(layout.session_env_root)
    authority_fingerprint = str(
        prepared_state.get('claude_provider_authority_fingerprint') or ''
    ).strip()
    if authority_fingerprint:
        payload['claude_provider_authority_fingerprint'] = authority_fingerprint
    if str(prepared_state.get('ccb_continuation_launch_mode') or '').strip() == 'fork':
        payload['ccb_continuation_launch_mode'] = 'fork'
    return payload


def _persistable_start_cmd(start_cmd: str, *, settings_path: Path) -> str:
    """Return a restart command without persisting provider credentials."""
    lexer = shlex.shlex(str(start_cmd or ''), posix=True, punctuation_chars=';&|<>')
    lexer.whitespace_split = True
    lexer.commenters = ''
    tokens = list(lexer)
    filtered: list[str] = []
    replace_settings_value = False
    for token in tokens:
        if replace_settings_value:
            filtered.append(str(settings_path))
            replace_settings_value = False
            continue
        if token == '--settings':
            filtered.append(token)
            replace_settings_value = True
            continue
        if any(token.startswith(f'{key}=') for key in _SENSITIVE_PERSISTED_ENV):
            continue
        filtered.append(token)
    if replace_settings_value:
        filtered.pop()

    compact: list[str] = []
    for index, token in enumerate(filtered):
        if token != 'export':
            compact.append(token)
            continue
        next_token = filtered[index + 1] if index + 1 < len(filtered) else ''
        if next_token in _SHELL_OPERATORS or not next_token:
            continue
        compact.append(token)

    rendered = ''
    for token in compact:
        if token in _SHELL_OPERATORS:
            separator = f'{token} ' if token == ';' else f' {token} '
            rendered = rendered.rstrip() + separator
        else:
            rendered += f'{shlex.quote(token)} '
    command = rendered.strip()
    command = command.replace(' ; ; ', ' ; ')
    return command.strip(' ;')


__all__ = ['build_runtime_launcher', 'build_session_payload', 'build_start_cmd', 'prepare_runtime', 'resolve_run_cwd']


def _append_unique_flag(parts: list[str], flag: str, startup_args: tuple[str, ...]) -> None:
    if flag not in parts and flag not in startup_args:
        parts.append(flag)


def _ensure_skip_prompt_settings(runtime_dir: Path, existing_path: Path | None) -> Path:
    path = existing_path or (runtime_dir / 'claude-settings.json')
    payload = {}
    if path.is_file():
        try:
            payload = json.loads(path.read_text(encoding='utf-8'))
        except Exception:
            pass
    payload['skipDangerousModePermissionPrompt'] = True
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding='utf-8')
    return path


def _ensure_skip_prompt_home_settings(home_overrides: dict[str, str]) -> None:
    home = str(home_overrides.get('HOME') or '').strip()
    if not home:
        return
    path = Path(home).expanduser() / '.claude' / 'settings.json'
    payload = {}
    if path.is_file():
        try:
            loaded = json.loads(path.read_text(encoding='utf-8'))
            if isinstance(loaded, dict):
                payload = dict(loaded)
        except Exception:
            payload = {}
    payload['skipDangerousModePermissionPrompt'] = True
    if not isinstance(payload.get('allowedTools'), list):
        payload['allowedTools'] = []
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')


def _ensure_bypass_permission_acceptance(home_overrides: dict[str, str], *, project_root: Path | None = None) -> None:
    home = str(home_overrides.get('HOME') or '').strip()
    if not home:
        return
    config_dir = str(home_overrides.get('CLAUDE_CONFIG_DIR') or '').strip()
    path = (
        Path(config_dir).expanduser() / '.claude.json'
        if config_dir
        else Path(home).expanduser() / '.claude.json'
    )
    payload = {}
    if path.is_file():
        try:
            loaded = json.loads(path.read_text(encoding='utf-8'))
            if isinstance(loaded, dict):
                payload = dict(loaded)
        except Exception:
            payload = {}
    payload['bypassPermissionsModeAccepted'] = True
    if project_root is not None:
        project_key = str(project_root.expanduser().resolve(strict=False))
        projects = payload.get('projects')
        if not isinstance(projects, dict):
            projects = {}
        else:
            projects = dict(projects)
        project_record = dict(projects.get(project_key)) if isinstance(projects.get(project_key), dict) else {}
        top_record = dict(payload.get(project_key)) if isinstance(payload.get(project_key), dict) else {}
        for record in (project_record, top_record):
            record['hasTrustDialogAccepted'] = True
            if not isinstance(record.get('allowedTools'), list):
                record['allowedTools'] = []
        projects[project_key] = project_record
        payload['projects'] = projects
        payload[project_key] = top_record
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
