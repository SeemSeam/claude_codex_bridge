from __future__ import annotations

from pathlib import Path
import shlex

from agents.models import AgentSpec
from cli.context import CliContext
from cli.models import ParsedStartCommand
from provider_core.contracts import ProviderRuntimeLauncher
from provider_core.caller_env import (
    caller_context_env,
    export_env_clause,
    join_env_prefix,
    provider_user_session_env,
)
from provider_core.runtime_shared import apply_provider_command_template, provider_start_parts
from provider_profiles import load_resolved_provider_profile
from workspace.models import WorkspacePlan

from .env import build_gemini_env_prefix


def build_runtime_launcher(
    *,
    prepare_launch_context_fn,
    build_start_cmd_fn,
    build_session_payload_fn,
    resolve_run_cwd_fn,
) -> ProviderRuntimeLauncher:
    return ProviderRuntimeLauncher(
        provider="gemini",
        launch_mode="simple_tmux",
        prepare_launch_context=prepare_launch_context_fn,
        build_start_cmd=build_start_cmd_fn,
        build_session_payload=build_session_payload_fn,
        resolve_run_cwd=resolve_run_cwd_fn,
    )


def build_start_cmd(
    command: ParsedStartCommand,
    spec: AgentSpec,
    runtime_dir,
    launch_session_id: str,
    *,
    prepared_state: dict[str, object] | None = None,
    resolve_restore_target_fn,
    prepare_home_overrides_fn,
    cli_supports_flag_fn,
) -> str:
    runtime_dir = Path(runtime_dir)
    profile = load_resolved_provider_profile(runtime_dir)
    launch_context = prepared_state if isinstance(prepared_state, dict) else {}
    launch_context.pop('ccb_continuation_launch_mode', None)
    project_root = _path_or_none(launch_context.get('project_root'))
    if project_root is None:
        raise RuntimeError('Gemini launch requires prepare_launch_context before build_start_cmd')
    home_overrides = prepare_home_overrides_fn(
        runtime_dir,
        profile,
        refresh_home=False,
        project_root=project_root,
        agent_name=spec.name,
        workspace_path=_path_or_none(launch_context.get('workspace_path')),
    )
    restore_target = resolve_restore_target_fn(
        spec=spec,
        runtime_dir=runtime_dir,
        restore=command.restore,
    )
    cmd_parts = provider_start_parts("gemini")
    if command.auto_permission:
        cmd_parts.append("--yolo")
    if (
        restore_target.continuation_mode == 'import'
        and restore_target.continuation_session_path is not None
        and cli_supports_flag_fn(cmd_parts, '--session-file')
    ):
        launch_context['ccb_continuation_launch_mode'] = 'import'
        cmd_parts.extend(['--session-file', str(restore_target.continuation_session_path)])
    elif restore_target.has_history:
        cmd_parts.extend(["--resume", "latest"])
    cmd_parts.extend(spec.startup_args)
    cmd = " ".join(shlex.quote(str(part)) for part in cmd_parts)
    cmd = apply_provider_command_template(cmd, spec.provider_command_template)
    env_prefix = join_env_prefix(
        build_gemini_env_prefix(profile=profile, extra_env=spec.env),
        export_env_clause(provider_user_session_env()),
        export_env_clause(home_overrides),
        export_env_clause(
            caller_context_env(actor=spec.name, runtime_dir=runtime_dir, launch_session_id=launch_session_id)
        ),
    )
    if env_prefix:
        return f"{env_prefix}; {cmd}"
    return cmd


def _path_or_none(value: object) -> Path | None:
    raw = str(value or '').strip()
    if not raw:
        return None
    try:
        return Path(raw).expanduser()
    except Exception:
        return None


def resolve_run_cwd(
    command: ParsedStartCommand,
    spec: AgentSpec,
    plan: WorkspacePlan,
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
        restore=command.restore,
    ).run_cwd


def build_session_payload(
    context: CliContext,
    spec: AgentSpec,
    plan: WorkspacePlan,
    runtime_dir,
    run_cwd: Path,
    pane_id: str,
    pane_title_marker: str,
    start_cmd: str,
    launch_session_id: str,
    prepared_state: dict[str, object],
) -> dict[str, object]:
    runtime_dir = Path(runtime_dir)
    payload = {
        "ccb_session_id": launch_session_id,
        "agent_name": spec.name,
        "ccb_project_id": context.project.project_id,
        "runtime_dir": str(runtime_dir),
        "completion_artifact_dir": str(runtime_dir / "completion"),
        "terminal": "tmux",
        "tmux_session": pane_id,
        "pane_id": pane_id,
        "pane_title_marker": pane_title_marker,
        "workspace_path": str(plan.workspace_path),
        "work_dir": str(run_cwd),
        "start_dir": str(context.project.project_root),
        "start_cmd": start_cmd,
    }
    layout = prepared_state.get('gemini_home_layout')
    if layout is not None:
        payload['gemini_home'] = str(layout.home_root)
        payload['gemini_root'] = str(layout.tmp_root)
    authority_fingerprint = str(
        prepared_state.get('gemini_provider_authority_fingerprint') or ''
    ).strip()
    if authority_fingerprint:
        payload['gemini_provider_authority_fingerprint'] = authority_fingerprint
    if str(prepared_state.get('ccb_continuation_launch_mode') or '').strip() == 'import':
        payload['ccb_continuation_launch_mode'] = 'import'
    return payload


__all__ = [
    "build_runtime_launcher",
    "build_session_payload",
    "build_start_cmd",
    "resolve_run_cwd",
]
