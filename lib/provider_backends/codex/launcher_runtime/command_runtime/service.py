from __future__ import annotations

import os
import shlex
from pathlib import Path
from typing import Callable

from agents.policy import should_restore_provider_history
from provider_core.caller_env import caller_context_env, provider_user_session_env
from provider_core.identity_prompt import materialize_codex_identity_agents_md
from provider_core.runtime_shared import apply_provider_command_template
from provider_backends.codex.runtime_artifacts import codex_runtime_artifact_layout
from provider_backends.codex.session_authority import (
    current_memory_projection_fingerprint,
    current_provider_authority_fingerprint,
)
from provider_profiles.codex_home_config import codex_api_authority


def build_start_cmd(
    command,
    spec,
    runtime_dir: Path,
    launch_session_id: str,
    *,
    load_resolved_provider_profile_fn: Callable[[Path], object | None],
    prepare_codex_home_overrides_fn: Callable[..., dict[str, str]],
    provider_start_parts_fn: Callable[[str], list[str]],
    load_resume_session_id_fn: Callable[..., str | None],
    build_codex_shell_prefix_fn: Callable[..., list[str]],
    prepared_state: dict[str, object] | None = None,
) -> str:
    profile = load_resolved_provider_profile_fn(runtime_dir)
    launch_context = prepared_state or {}
    project_root = _path_or_none(launch_context.get('project_root'))
    if project_root is None:
        raise RuntimeError('Codex launch requires prepare_launch_context before build_start_cmd')
    codex_home_overrides = prepare_codex_home_overrides_fn(
        runtime_dir,
        profile,
        refresh_home=False,
        project_root=project_root,
        agent_name=spec.name,
        workspace_path=_path_or_none(launch_context.get('workspace_path')),
    )
    materialize_codex_identity_agents_md(
        codex_home_overrides.get('CODEX_HOME'),
        name=spec.name,
        role=getattr(spec, 'role', None),
        window=spec.name,
    )
    codex_args = _codex_args(
        command,
        spec,
        runtime_dir,
        profile=profile,
        provider_start_parts_fn=provider_start_parts_fn,
        load_resume_session_id_fn=load_resume_session_id_fn,
    )
    env_map = _env_map(
        runtime_dir,
        launch_session_id,
        spec=spec,
        profile=profile,
        codex_home_overrides=codex_home_overrides,
    )
    prefix_parts = build_codex_shell_prefix_fn(profile=profile)
    exports = ' '.join(f'{key}={shlex.quote(str(value))}' for key, value in env_map.items() if str(value).strip())
    if exports:
        prefix_parts.append(f'export {exports}')
    cmd = ' '.join(shlex.quote(str(part)) for part in codex_args)
    cmd = apply_provider_command_template(cmd, spec.provider_command_template)
    if prefix_parts:
        return f"{'; '.join(prefix_parts)}; {cmd}"
    return cmd


def build_codex_shell_prefix(*, profile, provider_api_env_keys_fn: Callable[[str], list[str]]) -> list[str]:
    if profile is None or profile.inherit_api:
        return []
    return [f'unset {key}' for key in sorted(provider_api_env_keys_fn('codex'))]


def _path_or_none(value: object) -> Path | None:
    raw = str(value or '').strip()
    if not raw:
        return None
    try:
        return Path(raw).expanduser()
    except Exception:
        return None


def _codex_args(command, spec, runtime_dir: Path, *, profile, provider_start_parts_fn, load_resume_session_id_fn) -> list[str]:
    codex_args = provider_start_parts_fn('codex')
    codex_args.extend(['-c', 'disable_paste_burst=true'])
    if command.auto_permission:
        codex_args.extend(
            [
                '--ask-for-approval',
                'never',
                '--sandbox',
                'danger-full-access',
                '--dangerously-bypass-hook-trust',
            ]
        )
    codex_args.extend(spec.startup_args)
    if should_restore_provider_history(spec.restore_default, cli_restore=command.restore):
        session_id = load_resume_session_id_fn(
            spec,
            runtime_dir,
            profile,
            current_fingerprint=current_provider_authority_fingerprint(profile),
            current_memory_fingerprint=current_memory_projection_fingerprint(runtime_dir),
        )
        if session_id:
            codex_args.extend(['resume', session_id])
    return codex_args


def _env_map(runtime_dir: Path, launch_session_id: str, *, spec, profile, codex_home_overrides: dict[str, str]) -> dict[str, str]:
    artifacts = codex_runtime_artifact_layout(runtime_dir)
    inherited_api_env = _inherited_api_env(profile=profile)
    explicit_env: dict[str, str] = {}
    if profile is not None:
        explicit_env.update(profile.env)
    explicit_env.update(spec.env)
    if codex_api_authority(profile) is not None:
        explicit_env.pop('OPENAI_BASE_URL', None)
        explicit_env.pop('OPENAI_API_BASE', None)
    return {
        **provider_user_session_env(),
        **inherited_api_env,
        'RUST_LOG': 'off',
        **explicit_env,
        'CODEX_RUNTIME_DIR': str(runtime_dir),
        'CODEX_INPUT_FIFO': str(artifacts.input_fifo),
        'CODEX_OUTPUT_FIFO': str(artifacts.output_fifo),
        'CODEX_TERMINAL': 'tmux',
        **codex_home_overrides,
        **caller_context_env(actor=spec.name, runtime_dir=runtime_dir, launch_session_id=launch_session_id),
    }


def _inherited_api_env(*, profile) -> dict[str, str]:
    if profile is not None and not profile.inherit_api:
        return {}
    return {
        key: value
        for key, value in os.environ.items()
        if key in {
            'OPENAI_API_KEY',
            'OPENAI_BASE_URL',
            'OPENAI_API_BASE',
            'OPENAI_ORG_ID',
            'OPENAI_ORGANIZATION',
        }
        and str(value).strip()
    }


__all__ = ['build_codex_shell_prefix', 'build_start_cmd']
