from __future__ import annotations

import shlex
from pathlib import Path

from agents.models import AgentSpec
from cli.context import CliContext
from cli.models import ParsedStartCommand
from provider_core.caller_env import (
    caller_context_env,
    export_env_clause,
    join_env_prefix,
    provider_user_session_env,
)
from provider_core.contracts import ProviderRuntimeLauncher
from provider_core.runtime_shared import provider_start_parts
from workspace.models import WorkspacePlan


_YOLO_FLAG = '--dangerously-skip-permissions'


def build_runtime_launcher() -> ProviderRuntimeLauncher:
    return ProviderRuntimeLauncher(
        provider='agy',
        launch_mode='simple_tmux',
        prepare_launch_context=prepare_launch_context,
        build_start_cmd=build_start_cmd,
        build_session_payload=build_session_payload,
    )


def prepare_launch_context(
    context: CliContext,
    spec: AgentSpec,
    plan: WorkspacePlan,
    runtime_dir: Path,
    prepared_state: dict[str, object],
) -> dict[str, object]:
    del runtime_dir
    payload = dict(prepared_state or {})
    payload['agent_name'] = spec.name
    payload['project_root'] = str(context.project.project_root)
    payload['workspace_path'] = str(prepared_state.get('run_cwd') or plan.workspace_path)
    payload['agent_events_path'] = str(context.paths.agent_events_path(spec.name))
    return payload


def build_start_cmd(
    command: ParsedStartCommand,
    spec: AgentSpec,
    runtime_dir,
    launch_session_id: str,
    *,
    prepared_state: dict[str, object] | None = None,
) -> str:
    del prepared_state
    cmd_parts = provider_start_parts('agy')
    if command.auto_permission and _YOLO_FLAG not in cmd_parts and _YOLO_FLAG not in spec.startup_args:
        cmd_parts.append(_YOLO_FLAG)
    if command.restore and not _has_restore_arg(cmd_parts) and not _has_restore_arg(spec.startup_args):
        cmd_parts.append('--continue')
    cmd_parts.extend(spec.startup_args)
    cmd = ' '.join(shlex.quote(str(part)) for part in cmd_parts)
    runtime_dir = Path(runtime_dir)
    env_prefix = join_env_prefix(
        export_env_clause(provider_user_session_env()),
        export_env_clause(spec.env),
        export_env_clause(
            caller_context_env(actor=spec.name, runtime_dir=runtime_dir, launch_session_id=launch_session_id)
        ),
    )
    if env_prefix:
        return f'{env_prefix}; {cmd}'
    return cmd


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
    del context, spec, prepared_state
    return {
        'ccb_session_id': launch_session_id,
        'runtime_dir': str(runtime_dir),
        'completion_artifact_dir': str(runtime_dir / 'completion'),
        'terminal': 'tmux',
        'tmux_session': pane_id,
        'pane_id': pane_id,
        'pane_title_marker': pane_title_marker,
        'workspace_path': str(plan.workspace_path),
        'work_dir': str(run_cwd),
        'start_cmd': start_cmd,
    }


def _has_restore_arg(parts: tuple[str, ...] | list[str]) -> bool:
    normalized = {str(part).strip() for part in parts}
    return bool({'--continue', '-c', '--conversation'} & normalized)


__all__ = ['build_runtime_launcher', 'build_start_cmd', 'prepare_launch_context']
