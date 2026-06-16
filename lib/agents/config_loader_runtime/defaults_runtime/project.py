from __future__ import annotations

from agents.models import (
    AgentSpec,
    PermissionMode,
    ProjectConfig,
    QueuePolicy,
    RestoreMode,
    RuntimeMode,
    ToolWindowSpec,
    WindowSpec,
    WorkspaceMode,
)

from ..common import DEFAULT_CCB_SELF_AGENT, DEFAULT_CCB_SELF_ROLE, DEFAULT_DEFAULT_AGENTS

DEFAULT_AGENT_PROVIDERS = (
    ('agent1', 'codex'),
    ('agent2', 'codex'),
    ('agent3', 'claude'),
    (DEFAULT_CCB_SELF_AGENT, 'claude'),
)
DEFAULT_AGENT_ROLES = {
    DEFAULT_CCB_SELF_AGENT: DEFAULT_CCB_SELF_ROLE,
}
DEFAULT_MAIN_WINDOW_LAYOUT = 'agent1:codex, agent2:codex, agent3:claude'
DEFAULT_CCB_SELF_WINDOW_LAYOUT = f'{DEFAULT_CCB_SELF_AGENT}:claude'
DEFAULT_WINDOW_LAYOUT = (
    f'{DEFAULT_MAIN_WINDOW_LAYOUT}, {DEFAULT_CCB_SELF_WINDOW_LAYOUT}'
)
DEFAULT_TOOL_WINDOW_COMMAND = 'ccb-nvim'


def build_default_project_config() -> ProjectConfig:
    agents = {
        name: build_default_agent_spec(
            name=name,
            provider=provider,
            role=DEFAULT_AGENT_ROLES.get(name),
        )
        for name, provider in DEFAULT_AGENT_PROVIDERS
    }
    main_agent_names = tuple(
        name for name in DEFAULT_DEFAULT_AGENTS if name != DEFAULT_CCB_SELF_AGENT
    )
    return ProjectConfig(
        version=2,
        default_agents=DEFAULT_DEFAULT_AGENTS,
        agents=agents,
        cmd_enabled=False,
        layout_spec=DEFAULT_WINDOW_LAYOUT,
        windows=(
            WindowSpec(
                name='main',
                order=0,
                layout_spec=DEFAULT_MAIN_WINDOW_LAYOUT,
                agent_names=main_agent_names,
            ),
            WindowSpec(
                name=DEFAULT_CCB_SELF_AGENT,
                order=1,
                layout_spec=DEFAULT_CCB_SELF_WINDOW_LAYOUT,
                agent_names=(DEFAULT_CCB_SELF_AGENT,),
            ),
        ),
        tool_windows=(
            ToolWindowSpec(
                name='neovim',
                order=0,
                command=DEFAULT_TOOL_WINDOW_COMMAND,
                label='neovim',
            ),
        ),
        entry_window='main',
        windows_explicit=True,
    )


def build_default_agent_spec(*, name: str, provider: str, role: str | None = None) -> AgentSpec:
    return AgentSpec(
        name=name,
        provider=provider,
        target='.',
        workspace_mode=WorkspaceMode.INPLACE,
        workspace_root=None,
        runtime_mode=RuntimeMode.PANE_BACKED,
        restore_default=RestoreMode.AUTO,
        permission_default=PermissionMode.MANUAL,
        queue_policy=QueuePolicy.SERIAL_PER_AGENT,
        role=role,
    )


__all__ = [
    'DEFAULT_AGENT_PROVIDERS',
    'DEFAULT_AGENT_ROLES',
    'DEFAULT_CCB_SELF_WINDOW_LAYOUT',
    'DEFAULT_MAIN_WINDOW_LAYOUT',
    'DEFAULT_TOOL_WINDOW_COMMAND',
    'DEFAULT_WINDOW_LAYOUT',
    'build_default_agent_spec',
    'build_default_project_config',
]
