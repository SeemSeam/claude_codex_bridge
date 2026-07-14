from __future__ import annotations

from dataclasses import replace
from pathlib import Path

from agents.models import AgentSpec
from cli.context import CliContext
from cli.models import ParsedStartCommand
from provider_backends.native_cli_support.launcher import (
    NativeCliLaunchConfig,
    build_session_payload as native_build_session_payload,
    build_start_cmd as native_build_start_cmd,
    prepare_launch_context as native_prepare_launch_context,
)
from provider_core.contracts import ProviderRuntimeLauncher
from provider_profiles import load_resolved_provider_profile
from workspace.models import WorkspacePlan

from .home import materialize_grok_home
from .skills import grok_ccb_skills_ready, grok_skill_permission_args


def _grok_visible_args(prepared_state: dict[str, object]) -> tuple[str, ...]:
    args = ['--cwd', str(_path_from_prepared(prepared_state, 'workspace_path'))]
    if bool(prepared_state.get('grok_auto_permission_enabled')):
        args.extend(['--permission-mode', 'bypassPermissions'])
    if bool(prepared_state.get('grok_skill_permissions_enabled')):
        args.extend(grok_skill_permission_args())
    return tuple(args)


_GROK_LAUNCH_CONFIG = NativeCliLaunchConfig(
    provider='grok',
    home_env='HOME',
    visible_args=('--no-auto-update', '--minimal'),
    visible_args_builder=_grok_visible_args,
)


def build_runtime_launcher() -> ProviderRuntimeLauncher:
    return ProviderRuntimeLauncher(
        provider='grok',
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
    payload = native_prepare_launch_context(_GROK_LAUNCH_CONFIG, context, spec, plan, runtime_dir, prepared_state)
    home_dir = _path_or_none(payload.get('grok_home'))
    if home_dir is not None:
        materialize_grok_home(home_dir, profile=load_resolved_provider_profile(Path(runtime_dir)))
    return payload


def build_start_cmd(
    command: ParsedStartCommand,
    spec: AgentSpec,
    runtime_dir,
    launch_session_id: str,
    *,
    prepared_state: dict[str, object] | None = None,
) -> str:
    launch_context = prepared_state if prepared_state is not None else {}
    launch_context['grok_auto_permission_enabled'] = bool(command.auto_permission)
    home_dir = _path_or_none(launch_context.get('grok_home'))
    if home_dir is not None:
        profile = load_resolved_provider_profile(Path(runtime_dir))
        materialize_grok_home(home_dir, profile=profile)
        launch_context['grok_skill_permissions_enabled'] = bool(
            command.auto_permission and grok_ccb_skills_ready(home_dir)
        )
    launch_config = _GROK_LAUNCH_CONFIG
    if '--fullscreen' in spec.startup_args:
        launch_config = replace(
            launch_config,
            visible_args=tuple(arg for arg in launch_config.visible_args if arg != '--minimal'),
        )
    return native_build_start_cmd(
        launch_config,
        command,
        spec,
        runtime_dir,
        launch_session_id,
        prepared_state=launch_context,
    )


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
    payload = native_build_session_payload(
        _GROK_LAUNCH_CONFIG,
        context,
        spec,
        plan,
        runtime_dir,
        run_cwd,
        pane_id,
        pane_title_marker,
        start_cmd,
        launch_session_id,
        prepared_state,
    )
    payload['grok_skill_permissions_enabled'] = bool(
        prepared_state.get('grok_skill_permissions_enabled')
    )
    payload['grok_auto_permission_enabled'] = bool(
        prepared_state.get('grok_auto_permission_enabled')
    )
    return payload


def _path_or_none(value: object) -> Path | None:
    raw = str(value or '').strip()
    if not raw:
        return None
    try:
        return Path(raw).expanduser()
    except Exception:
        return None


def _path_from_prepared(prepared_state: dict[str, object], key: str) -> Path:
    raw = str(prepared_state.get(key) or '').strip()
    if not raw:
        raise RuntimeError(f'grok launch requires {key} in prepared_state')
    return Path(raw).expanduser()


__all__ = ['build_runtime_launcher', 'build_session_payload', 'build_start_cmd', 'prepare_launch_context']
