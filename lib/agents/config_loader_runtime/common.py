from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from agents.models import ProjectConfig

CONFIG_FILENAME = 'ccb.config'
CONFIG_SOURCE_PROJECT = 'project_config'
CONFIG_SOURCE_USER = 'user_config'
CONFIG_SOURCE_BUILTIN_DEFAULT = 'builtin_default'
CONFIG_SOURCE_KINDS = (CONFIG_SOURCE_PROJECT, CONFIG_SOURCE_USER, CONFIG_SOURCE_BUILTIN_DEFAULT)
DEFAULT_CCB_SELF_AGENT = 'ccb_self'
DEFAULT_CCB_SELF_ROLE = 'agentroles.ccb_self'
DEFAULT_AGENT_ORDER = ('agent1', 'agent2', 'agent3', DEFAULT_CCB_SELF_AGENT)
DEFAULT_DEFAULT_AGENTS = DEFAULT_AGENT_ORDER
ALLOWED_TOP_LEVEL_KEYS = {
    'version',
    'default_agents',
    'agents',
    'cmd_enabled',
    'layout',
    'ui',
    'windows',
    'tool_windows',
    'entry_window',
    'maintenance',
    'loop',
}
ALLOWED_PROVIDER_PROFILE_KEYS = {
    'mode',
    'home',
    'env',
    'mcp_servers',
    'plugins',
    'inherited_skill_include',
    'inherited_skill_exclude',
    'skill_overlays',
    'inherit_api',
    'inherit_auth',
    'inherit_config',
    'inherit_skills',
    'inherit_commands',
    'inherit_memory',
}
ALLOWED_AGENT_KEYS = {
    'provider',
    'target',
    'workspace_mode',
    'workspace_root',
    'workspace_path',
    'workspace_group',
    'provider_command_template',
    'runtime_mode',
    'restore',
    'permission',
    'queue_policy',
    'model',
    'key',
    'url',
    'startup_args',
    'env',
    'api',
    'provider_profile',
    'branch_template',
    'labels',
    'description',
    'role',
    'watch_paths',
    'dispatch_disabled',
    'account',
}


class ConfigValidationError(ValueError):
    pass


@dataclass(frozen=True)
class ConfigLoadResult:
    config: ProjectConfig
    source_path: Path | None
    source_kind: str
    used_default: bool = False


__all__ = [
    'ALLOWED_AGENT_KEYS',
    'ALLOWED_PROVIDER_PROFILE_KEYS',
    'ALLOWED_TOP_LEVEL_KEYS',
    'CONFIG_FILENAME',
    'CONFIG_SOURCE_BUILTIN_DEFAULT',
    'CONFIG_SOURCE_KINDS',
    'CONFIG_SOURCE_PROJECT',
    'CONFIG_SOURCE_USER',
    'DEFAULT_AGENT_ORDER',
    'DEFAULT_CCB_SELF_AGENT',
    'DEFAULT_CCB_SELF_ROLE',
    'DEFAULT_DEFAULT_AGENTS',
    'ConfigLoadResult',
    'ConfigValidationError',
]
