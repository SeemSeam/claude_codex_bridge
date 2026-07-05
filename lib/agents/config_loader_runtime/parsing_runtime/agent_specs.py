from __future__ import annotations

from typing import Any

from agents.models import (
    AgentApiSpec,
    AgentSpec,
    AgentValidationError,
    PermissionMode,
    ProviderProfileSpec,
    QueuePolicy,
    RestoreMode,
    RuntimeMode,
    WorkspaceMode,
    normalize_runtime_mode,
)
from provider_profiles import (
    provider_api_env_keys,
    provider_api_shortcut_env,
    supported_provider_api_shortcuts,
)

from ..common import ALLOWED_AGENT_KEYS, ConfigValidationError
from .agent_api import parse_agent_api_shortcut
from .expectations import expect_bool, expect_mapping, expect_string, expect_string_list, expect_string_mapping
from .provider_profiles import parse_provider_profile


def build_agent_spec(agent_name: str, raw: dict[str, Any]) -> AgentSpec:
    unknown = sorted(set(raw) - ALLOWED_AGENT_KEYS)
    if unknown:
        raise ConfigValidationError(
            f'agents.{agent_name} contains unknown fields: {", ".join(unknown)}'
        )
    provider = expect_string(raw.get('provider'), field_name=f'agents.{agent_name}.provider')
    env = expect_string_mapping(raw.get('env', {}), field_name=f'agents.{agent_name}.env')
    api = parse_agent_api_shortcut(agent_name, raw)
    provider_profile = (
        parse_provider_profile(agent_name, raw['provider_profile'])
        if raw.get('provider_profile') is not None
        else ProviderProfileSpec()
    )
    _validate_provider_profile_runtime_home(agent_name, provider=provider, provider_profile=provider_profile)
    if api != AgentApiSpec():
        provider_profile = _apply_agent_api_shortcut(
            agent_name,
            provider=provider,
            env=env,
            api=api,
            provider_profile=provider_profile,
            raw_provider_profile=raw.get('provider_profile'),
        )
    try:
        return AgentSpec(
            name=agent_name,
            provider=provider,
            target=expect_string(raw.get('target'), field_name=f'agents.{agent_name}.target'),
            workspace_mode=WorkspaceMode(
                expect_string(raw.get('workspace_mode'), field_name=f'agents.{agent_name}.workspace_mode')
            ),
            workspace_root=(
                expect_string(raw['workspace_root'], field_name=f'agents.{agent_name}.workspace_root')
                if raw.get('workspace_root') is not None
                else None
            ),
            workspace_path=(
                expect_string(raw['workspace_path'], field_name=f'agents.{agent_name}.workspace_path')
                if raw.get('workspace_path') is not None
                else None
            ),
            workspace_group=(
                expect_string(raw['workspace_group'], field_name=f'agents.{agent_name}.workspace_group')
                if raw.get('workspace_group') is not None
                else None
            ),
            provider_command_template=(
                expect_string(
                    raw['provider_command_template'],
                    field_name=f'agents.{agent_name}.provider_command_template',
                )
                if raw.get('provider_command_template') is not None
                else None
            ),
            runtime_mode=normalize_runtime_mode(
                expect_string(
                    raw.get('runtime_mode', RuntimeMode.PANE_BACKED.value),
                    field_name=f'agents.{agent_name}.runtime_mode',
                )
            ),
            restore_default=RestoreMode(
                expect_string(raw.get('restore'), field_name=f'agents.{agent_name}.restore')
            ),
            permission_default=PermissionMode(
                expect_string(raw.get('permission'), field_name=f'agents.{agent_name}.permission')
            ),
            queue_policy=QueuePolicy(str(raw.get('queue_policy') or QueuePolicy.SERIAL_PER_AGENT.value)),
            model=(
                expect_string(raw['model'], field_name=f'agents.{agent_name}.model')
                if raw.get('model') is not None
                else None
            ),
            startup_args=expect_string_list(raw.get('startup_args', []), field_name=f'agents.{agent_name}.startup_args'),
            env=env,
            api=api,
            provider_profile=provider_profile,
            branch_template=(
                expect_string(raw['branch_template'], field_name=f'agents.{agent_name}.branch_template')
                if raw.get('branch_template') is not None
                else None
            ),
            labels=expect_string_list(raw.get('labels', []), field_name=f'agents.{agent_name}.labels'),
            description=(
                expect_string(raw['description'], field_name=f'agents.{agent_name}.description')
                if raw.get('description') is not None
                else None
            ),
            role=(
                expect_string(raw['role'], field_name=f'agents.{agent_name}.role')
                if raw.get('role') is not None
                else None
            ),
            watch_paths=expect_string_list(raw.get('watch_paths', []), field_name=f'agents.{agent_name}.watch_paths'),
            dispatch_disabled=(
                expect_bool(raw['dispatch_disabled'], field_name=f'agents.{agent_name}.dispatch_disabled')
                if raw.get('dispatch_disabled') is not None
                else False
            ),
            account=(
                expect_string(raw['account'], field_name=f'agents.{agent_name}.account')
                if raw.get('account') is not None
                else None
            ),
        )
    except AgentValidationError as exc:
        raise ConfigValidationError(str(exc)) from exc
    except ValueError as exc:
        raise ConfigValidationError(f'agents.{agent_name}: {exc}') from exc


def parse_agents(raw_agents: Any) -> dict[str, AgentSpec]:
    from agents.models import normalize_agent_name

    raw_agents_map = expect_mapping(raw_agents, field_name='agents')
    parsed_agents: dict[str, AgentSpec] = {}
    for raw_name, raw_spec in raw_agents_map.items():
        if not isinstance(raw_name, str):
            raise ConfigValidationError('agents table keys must be strings')
        try:
            normalized_name = normalize_agent_name(raw_name)
        except AgentValidationError as exc:
            raise ConfigValidationError(str(exc)) from exc
        if normalized_name in parsed_agents:
            raise ConfigValidationError(f'duplicate agent name after normalization: {normalized_name}')
        parsed_agents[normalized_name] = build_agent_spec(
            normalized_name,
            expect_mapping(raw_spec, field_name=f'agents.{raw_name}'),
        )
    return parsed_agents


def _apply_agent_api_shortcut(
    agent_name: str,
    *,
    provider: str,
    env: dict[str, str],
    api: AgentApiSpec,
    provider_profile: ProviderProfileSpec,
    raw_provider_profile: Any,
) -> ProviderProfileSpec:
    api_env_keys = provider_api_env_keys(provider)
    if not api_env_keys:
        supported = ', '.join(supported_provider_api_shortcuts())
        raise ConfigValidationError(
            f'agents.{agent_name}.key/url is supported only for providers: {supported}'
        )
    _ensure_no_api_env_conflict(
        agent_name,
        source='agents.{agent}.env',
        env_map=env,
        api_env_keys=api_env_keys,
    )
    _ensure_no_api_env_conflict(
        agent_name,
        source='agents.{agent}.provider_profile.env',
        env_map=provider_profile.env,
        api_env_keys=api_env_keys,
    )
    raw_profile = (
        expect_mapping(raw_provider_profile, field_name=f'agents.{agent_name}.provider_profile')
        if raw_provider_profile is not None
        else {}
    )
    if 'inherit_api' in raw_profile and provider_profile.inherit_api:
        raise ConfigValidationError(
            f'agents.{agent_name}.key/url cannot be combined with agents.{agent_name}.provider_profile.inherit_api = true'
        )
    if provider == 'codex' and 'inherit_config' in raw_profile and provider_profile.inherit_config:
        raise ConfigValidationError(
            f'agents.{agent_name}.key/url cannot be combined with agents.{agent_name}.provider_profile.inherit_config = true for codex'
        )
    if (api.key or api.url) and 'inherit_auth' in raw_profile and provider_profile.inherit_auth:
        raise ConfigValidationError(
            f'agents.{agent_name}.key/url cannot be combined with agents.{agent_name}.provider_profile.inherit_auth = true'
        )
    try:
        api_env = provider_api_shortcut_env(provider, key=api.key, url=api.url)
    except ValueError as exc:
        raise ConfigValidationError(f'agents.{agent_name}.key/url: {exc}') from exc
    inherit_auth = False
    inherit_config = provider_profile.inherit_config
    if provider == 'codex':
        inherit_config = False
    return ProviderProfileSpec(
        mode=provider_profile.mode,
        home=provider_profile.home,
        env={**provider_profile.env, **api_env},
        inherit_api=False,
        inherit_auth=inherit_auth,
        inherit_config=inherit_config,
        inherited_skill_include=provider_profile.inherited_skill_include,
        inherited_skill_exclude=provider_profile.inherited_skill_exclude,
        skill_overlays=dict(provider_profile.skill_overlays),
        inherit_skills=provider_profile.inherit_skills,
        inherit_commands=provider_profile.inherit_commands,
        inherit_memory=provider_profile.inherit_memory,
    )


def _ensure_no_api_env_conflict(
    agent_name: str,
    *,
    source: str,
    env_map: dict[str, str],
    api_env_keys: set[str],
) -> None:
    overlap = sorted(set(env_map) & set(api_env_keys))
    if not overlap:
        return
    joined = ', '.join(overlap)
    rendered_source = source.format(agent=agent_name)
    raise ConfigValidationError(
        f'agents.{agent_name}.key/url cannot be mixed with provider API env in {rendered_source}: {joined}'
    )


def _validate_provider_profile_runtime_home(
    agent_name: str,
    *,
    provider: str,
    provider_profile: ProviderProfileSpec,
) -> None:
    normalized_provider = str(provider or '').strip().lower()
    if normalized_provider == 'codex' or provider_profile.home is None:
        return
    raise ConfigValidationError(
        f'agents.{agent_name}.provider_profile.home is supported only for codex runtime_home overrides'
    )


__all__ = ['build_agent_spec', 'parse_agents']
