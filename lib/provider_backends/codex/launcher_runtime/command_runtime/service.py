from __future__ import annotations

import os
import shlex
from pathlib import Path
from typing import Callable

from agents.policy import should_restore_provider_history
from cli.services.role_command_policy import role_command_policy_for_spec, role_command_policy_requires_enforcement
from provider_core.caller_env import caller_context_env, provider_user_session_env
from provider_core.runtime_shared import apply_provider_command_template
from provider_backends.codex.runtime_artifacts import codex_runtime_artifact_layout
from provider_backends.codex.session_authority import (
    current_memory_projection_fingerprint,
    current_provider_authority_fingerprint,
)
from provider_profiles.codex_home_config import codex_api_authority

from ..session_paths import session_file_for_runtime_dir


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
    load_linked_continuation_session_id_fn: Callable[..., str | None] | None = None,
    build_codex_shell_prefix_fn: Callable[..., list[str]],
    supports_session_fork_fn: Callable[[tuple[str, ...]], bool] | None = None,
    supports_managed_app_server_fn: Callable[[tuple[str, ...]], bool] | None = None,
    build_managed_app_server_command_fn: Callable[..., tuple[str, dict[str, object]]] | None = None,
    prepared_state: dict[str, object] | None = None,
) -> str:
    profile = load_resolved_provider_profile_fn(runtime_dir)
    launch_context = prepared_state if isinstance(prepared_state, dict) else {}
    launch_context.pop('ccb_continuation_launch_mode', None)
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
    provider_start_parts = provider_start_parts_fn('codex')
    codex_args = _codex_args(
        command,
        spec,
        runtime_dir,
        profile=profile,
        provider_start_parts=provider_start_parts,
        load_resume_session_id_fn=load_resume_session_id_fn,
        load_linked_continuation_session_id_fn=load_linked_continuation_session_id_fn,
        supports_session_fork_fn=supports_session_fork_fn,
        launch_context=launch_context,
    )
    env_map = _env_map(
        runtime_dir,
        launch_session_id,
        spec=spec,
        profile=profile,
        codex_home_overrides=codex_home_overrides,
    )
    # herdr backend 适配：CODEX_TERMINAL 按实际后端设置
    backend_impl = str(prepared_state.get('ccb_backend_impl', '')).strip()
    if backend_impl and backend_impl != 'tmux':
        env_map['CODEX_TERMINAL'] = backend_impl
    prefix_parts = build_codex_shell_prefix_fn(profile=profile)
    exports = ' '.join(f'{key}={shlex.quote(str(value))}' for key, value in env_map.items() if str(value).strip())
    if exports:
        prefix_parts.append(f'export {exports}')
    managed_enabled = bool(
        not str(spec.provider_command_template or '').strip()
        # Codex 0.145.0 advertises both `--remote` and `fork`, but combining
        # them can open a fresh thread whose session metadata has no
        # `forked_from_id`.  That silently drops the context while CCB records
        # a native fork continuation.  Keep authority-changing forks on the
        # native local CLI until the remote surface proves fork semantics.
        and str(launch_context.get('ccb_continuation_launch_mode') or '').strip() != 'fork'
        and supports_managed_app_server_fn is not None
        and build_managed_app_server_command_fn is not None
        and supports_managed_app_server_fn(tuple(provider_start_parts))
    )
    if managed_enabled:
        cmd, managed_state = build_managed_app_server_command_fn(codex_args, runtime_dir=runtime_dir)
        launch_context.update(managed_state)
        launch_context['codex_app_server_env'] = dict(env_map)
        launch_context['codex_app_server_unset_env'] = [
            part.split(None, 1)[1]
            for part in prefix_parts
            if part.startswith('unset ') and len(part.split(None, 1)) == 2
        ]
    else:
        launch_context['codex_app_server_enabled'] = False
        cmd = ' '.join(shlex.quote(str(part)) for part in codex_args)
        cmd = apply_provider_command_template(cmd, spec.provider_command_template)
    if prefix_parts:
        return f"{'; '.join(prefix_parts)}; {cmd}"
    return cmd


def build_codex_shell_prefix(*, profile, provider_api_env_keys_fn: Callable[[str], list[str]]) -> list[str]:
    if profile is None:
        return []
    if not profile.inherit_api:
        cleared = set(provider_api_env_keys_fn('codex'))
    else:
        cleared = _explicit_api_owned_names(profile)
    return [f'unset {key}' for key in sorted(cleared)]


def _path_or_none(value: object) -> Path | None:
    raw = str(value or '').strip()
    if not raw:
        return None
    try:
        return Path(raw).expanduser()
    except Exception:
        return None


def _codex_args(
    command,
    spec,
    runtime_dir: Path,
    *,
    profile,
    provider_start_parts,
    load_resume_session_id_fn,
    load_linked_continuation_session_id_fn=None,
    supports_session_fork_fn=None,
    launch_context: dict[str, object] | None = None,
) -> list[str]:
    codex_args = list(provider_start_parts)
    codex_args.extend(['-c', 'disable_paste_burst=true'])
    if role_command_policy_requires_enforcement(role_command_policy_for_spec(spec)):
        codex_args.extend(['--ask-for-approval', 'never', '--sandbox', 'read-only'])
    elif command.auto_permission:
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
            current_fingerprint=current_provider_authority_fingerprint(profile, runtime_dir=runtime_dir),
            current_memory_fingerprint=current_memory_projection_fingerprint(runtime_dir),
        )
        if session_id:
            codex_args.extend(['resume', session_id])
        elif load_linked_continuation_session_id_fn is not None:
            continuation_id = load_linked_continuation_session_id_fn(
                spec,
                runtime_dir,
                current_fingerprint=current_provider_authority_fingerprint(
                    profile,
                    runtime_dir=runtime_dir,
                ),
            )
            if (
                continuation_id
                and supports_session_fork_fn is not None
                and supports_session_fork_fn(tuple(provider_start_parts))
            ):
                codex_args.extend(['fork', continuation_id])
                if launch_context is not None:
                    launch_context['ccb_continuation_launch_mode'] = 'fork'
    return codex_args


def _env_map(runtime_dir: Path, launch_session_id: str, *, spec, profile, codex_home_overrides: dict[str, str]) -> dict[str, str]:
    artifacts = codex_runtime_artifact_layout(runtime_dir)
    inherited_api_env = _inherited_api_env(profile=profile)
    explicit_env: dict[str, str] = {}
    if profile is not None:
        explicit_env.update(profile.env)
    explicit_env.update(spec.env)
    explicit_env.pop('model_catalog_json', None)
    if codex_api_authority(profile) is not None:
        explicit_env.pop('OPENAI_BASE_URL', None)
        explicit_env.pop('OPENAI_API_BASE', None)
    session_file = session_file_for_runtime_dir(runtime_dir)
    session_binding_env = (
        {'CCB_SESSION_FILE': str(session_file)}
        if session_file is not None
        else {}
    )
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
        **session_binding_env,
        **caller_context_env(actor=spec.name, runtime_dir=runtime_dir, launch_session_id=launch_session_id),
    }


def _inherited_api_env(*, profile) -> dict[str, str]:
    if profile is not None and not profile.inherit_api:
        return {}
    explicitly_owned = _explicit_api_owned_names(profile)
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
        and key not in explicitly_owned
        and str(value).strip()
    }


def _explicit_api_owned_names(profile) -> set[str]:
    if profile is None:
        return set()
    env = {
        str(key): str(value)
        for key, value in dict(getattr(profile, 'env', {}) or {}).items()
        if str(value).strip()
    }
    groups = (
        {'OPENAI_API_KEY'},
        {'OPENAI_BASE_URL', 'OPENAI_API_BASE'},
        {'OPENAI_ORG_ID', 'OPENAI_ORGANIZATION'},
    )
    owned: set[str] = set()
    for group in groups:
        if group & set(env):
            owned.update(group)
    return owned


__all__ = ['build_codex_shell_prefix', 'build_start_cmd']
