from __future__ import annotations

from agents.models import (
    AgentApiSpec,
    PermissionMode,
    ProviderProfileSpec,
    QueuePolicy,
    RestoreMode,
    RuntimeMode,
)
from provider_model_shortcuts import strip_provider_model_startup_args
from provider_profiles import provider_api_env_keys
from provider_profiles.models import ProviderProfileSpec
from provider_thinking_shortcuts import strip_provider_thinking_startup_args


def agent_spec_to_config_dict(spec) -> dict[str, object]:
    payload: dict[str, object] = {
        'provider': spec.provider,
        'target': spec.target,
        'workspace_mode': spec.workspace_mode.value,
        'runtime_mode': spec.runtime_mode.value,
        'restore': spec.restore_default.value,
        'permission': spec.permission_default.value,
        'queue_policy': spec.queue_policy.value,
    }
    update_optional_agent_fields(payload, spec)
    return payload


def update_optional_agent_fields(payload: dict[str, object], spec) -> None:
    if spec.workspace_root is not None:
        payload['workspace_root'] = spec.workspace_root
    if spec.workspace_path is not None:
        payload['workspace_path'] = spec.workspace_path
    if spec.workspace_group is not None:
        payload['workspace_group'] = spec.workspace_group
    if spec.provider_command_template is not None:
        payload['provider_command_template'] = spec.provider_command_template
    if spec.model is not None:
        payload['model'] = spec.model
    model_catalog_json = spec.provider_profile.env.get('model_catalog_json')
    if model_catalog_json is not None:
        payload['model_catalog_json'] = model_catalog_json
    if spec.thinking is not None:
        payload['thinking'] = spec.thinking
    startup_args = _config_startup_args(spec)
    if startup_args:
        payload['startup_args'] = list(startup_args)
    if spec.env:
        payload['env'] = dict(spec.env)
    if spec.api != AgentApiSpec():
        payload.update(spec.api.to_record())
    provider_profile_payload = _provider_profile_config_dict(spec)
    if provider_profile_payload is not None:
        payload['provider_profile'] = provider_profile_payload
    if spec.branch_template is not None:
        payload['branch_template'] = spec.branch_template
    if spec.labels:
        payload['labels'] = list(spec.labels)
    if spec.description is not None:
        payload['description'] = spec.description
    if spec.role is not None:
        payload['role'] = spec.role
    if spec.watch_paths:
        payload['watch_paths'] = list(spec.watch_paths)
    if bool(getattr(spec, 'dispatch_disabled', False)):
        payload['dispatch_disabled'] = True


def agent_spec_to_hybrid_overlay_dict(
    spec,
    *,
    compact_provider: str,
    compact_workspace_mode: str,
) -> dict[str, object]:
    payload = agent_spec_to_config_dict(spec)
    defaults = {
        'provider': compact_provider,
        'target': '.',
        'workspace_mode': compact_workspace_mode,
        'runtime_mode': RuntimeMode.PANE_BACKED.value,
        'restore': RestoreMode.AUTO.value,
        'permission': PermissionMode.MANUAL.value,
        'queue_policy': QueuePolicy.SERIAL_PER_AGENT.value,
    }
    for key, expected in defaults.items():
        if payload.get(key) == expected:
            payload.pop(key, None)
    return payload


def _provider_profile_config_dict(spec) -> dict[str, object] | None:
    profile = spec.provider_profile
    default_profile = ProviderProfileSpec()
    if spec.api == AgentApiSpec():
        if profile == default_profile:
            return None
        return profile.to_record()

    filtered_env = {
        key: value
        for key, value in profile.env.items()
        if key not in provider_api_env_keys(spec.provider)
        and not (spec.provider == 'codex' and key == 'model_catalog_json')
    }
    payload: dict[str, object] = {}
    if profile.mode != default_profile.mode:
        payload['mode'] = profile.mode
    if profile.home is not None:
        payload['home'] = profile.home
    if filtered_env:
        payload['env'] = filtered_env
    if profile.mcp_servers:
        payload['mcp_servers'] = dict(profile.mcp_servers)
    if profile.plugins:
        payload['plugins'] = dict(profile.plugins)
    if profile.inherited_skill_include:
        payload['inherited_skill_include'] = list(profile.inherited_skill_include)
    if profile.inherited_skill_exclude:
        payload['inherited_skill_exclude'] = list(profile.inherited_skill_exclude)
    if profile.skill_overlays:
        payload['skill_overlays'] = {
            name: overlay.to_record() for name, overlay in profile.skill_overlays.items()
        }
    if profile.inherit_auth != default_profile.inherit_auth:
        payload['inherit_auth'] = profile.inherit_auth
    if profile.inherit_config != default_profile.inherit_config:
        payload['inherit_config'] = profile.inherit_config
    if profile.inherit_skills != default_profile.inherit_skills:
        payload['inherit_skills'] = profile.inherit_skills
    if profile.inherit_commands != default_profile.inherit_commands:
        payload['inherit_commands'] = profile.inherit_commands
    if profile.inherit_memory != default_profile.inherit_memory:
        payload['inherit_memory'] = profile.inherit_memory
    return payload or None


def loop_capacity_to_config_dict(loop_capacity) -> dict[str, object] | None:
    if loop_capacity is None:
        return None
    capacity_record = loop_capacity.to_record()
    role_profiles = dict(capacity_record.pop('role_profiles', {}) or {})
    default_capacity = type(loop_capacity)()
    default_record = default_capacity.to_record()
    capacity_payload = {
        key: value
        for key, value in capacity_record.items()
        if value != default_record.get(key)
    }
    payload: dict[str, object] = {}
    if capacity_payload:
        payload['capacity'] = capacity_payload
    if role_profiles:
        payload['role_profiles'] = {
            name: _loop_role_profile_config_dict(profile)
            for name, profile in loop_capacity.role_profiles.items()
        }
    return payload or None


def _loop_role_profile_config_dict(profile) -> dict[str, object]:
    payload: dict[str, object] = {
        'role': profile.role,
        'provider': profile.provider,
        'max_instances': profile.max_instances,
    }
    if profile.model is not None:
        payload['model'] = profile.model
    if profile.thinking is not None:
        payload['thinking'] = profile.thinking
    if profile.workspace_mode.value != 'inplace':
        payload['workspace_mode'] = profile.workspace_mode.value
    if profile.workspace_group is not None:
        payload['workspace_group'] = profile.workspace_group
    if profile.startup_args:
        payload['startup_args'] = list(profile.startup_args)
    provider_profile_payload = _loop_provider_profile_config_dict(profile.provider_profile)
    if provider_profile_payload is not None:
        payload['provider_profile'] = provider_profile_payload
    if profile.reuse != 'prefer_idle':
        payload['reuse'] = profile.reuse
    return payload


def _loop_provider_profile_config_dict(profile) -> dict[str, object] | None:
    default_profile = ProviderProfileSpec()
    if profile == default_profile:
        return None
    return profile.to_record()


def _config_startup_args(spec) -> tuple[str, ...]:
    startup_args = tuple(spec.startup_args)
    if spec.model is not None:
        startup_args = strip_provider_model_startup_args(
            spec.provider,
            startup_args,
            model=spec.model,
        )
    if spec.thinking is not None:
        startup_args = strip_provider_thinking_startup_args(
            spec.provider,
            startup_args,
            thinking=spec.thinking,
        )
    return startup_args


__all__ = [
    'agent_spec_to_config_dict',
    'agent_spec_to_hybrid_overlay_dict',
    'loop_capacity_to_config_dict',
    'update_optional_agent_fields',
]
