from __future__ import annotations

from agents.config_loader import build_default_project_config
from agents.models import AgentSpec, PermissionMode, ProjectConfig, QueuePolicy, RestoreMode, RuntimeMode, SidebarSpec, ToolWindowSpec, WindowSpec, WorkspaceMode
from ccbd.services.project_namespace_runtime import build_namespace_topology_plan


def _spec(name: str, provider: str) -> AgentSpec:
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
    )


def test_namespace_topology_plan_projects_sidebar_outside_user_layout() -> None:
    config = build_default_project_config()

    plan = build_namespace_topology_plan(
        config,
        ccbd_socket_path='/tmp/ccbd.sock',
        project_root='/repo',
    )

    assert plan.signature == config.topology_signature
    assert plan.entry_window == 'main'
    assert plan.sidebar_enabled is True
    assert len(plan.windows) == 3
    window = plan.windows[0]
    assert window.name == 'main'
    assert window.user_layout == 'agent1:codex, agent2:codex, agent3:claude'
    assert window.realized_layout == 'sidebar; (agent1:codex, agent2:codex, agent3:claude)'
    assert window.sidebar is not None
    assert window.sidebar.width == '15%'
    assert window.sidebar.launch_args == (
        'ccb-agent-sidebar',
        '--ccbd-socket',
        '/tmp/ccbd.sock',
        '--project-root',
        '/repo',
        '--pane-window',
        'main',
    )
    ccb_self_window = plan.windows[1]
    assert ccb_self_window.name == 'ccb_self'
    assert ccb_self_window.user_layout == 'ccb_self:claude'
    assert ccb_self_window.realized_layout == 'sidebar; (ccb_self:claude)'
    tool = plan.windows[2]
    assert tool.name == 'neovim'
    assert tool.kind == 'tool'
    assert tool.command == 'ccb-nvim'
    assert tool.agent_names == ()


def test_namespace_topology_plan_leaves_layout_plain_when_sidebar_off() -> None:
    config = ProjectConfig(
        version=2,
        default_agents=('agent1',),
        agents={'agent1': _spec('agent1', 'codex')},
        layout_spec='agent1:codex',
        windows=(WindowSpec(name='main', order=0, layout_spec='agent1:codex', agent_names=('agent1',)),),
        entry_window='main',
        sidebar=SidebarSpec(mode='off', width='15%', bottom_height=20),
    )

    plan = build_namespace_topology_plan(config)

    assert plan.sidebar_enabled is False
    assert plan.windows[0].realized_layout == 'agent1:codex'
    assert plan.windows[0].sidebar is None


def test_namespace_topology_plan_includes_tool_window_without_agent_names() -> None:
    config = ProjectConfig(
        version=2,
        default_agents=('agent1',),
        agents={'agent1': _spec('agent1', 'codex')},
        layout_spec='agent1:codex',
        windows=(WindowSpec(name='main', order=0, layout_spec='agent1:codex', agent_names=('agent1',)),),
        tool_windows=(ToolWindowSpec(name='neovim', order=0, command='ccb-nvim'),),
        entry_window='main',
    )

    plan = build_namespace_topology_plan(config, ccbd_socket_path='/tmp/ccbd.sock', project_root='/repo')

    assert [window.name for window in plan.windows] == ['main', 'neovim']
    tool = plan.windows[1]
    assert tool.kind == 'tool'
    assert tool.label == 'neovim'
    assert tool.command == 'ccb-nvim'
    assert tool.agent_names == ()
    assert tool.user_layout == 'ccb-nvim'
    assert tool.realized_layout == 'sidebar; (tool)'
    assert tool.sidebar is not None
    assert tool.sidebar.launch_args[-2:] == ('--pane-window', 'neovim')


def test_namespace_topology_plan_keeps_rich_alias_inside_agent_window() -> None:
    config = ProjectConfig(
        version=2,
        default_agents=('agent1',),
        agents={'agent1': _spec('agent1', 'codex')},
        layout_spec='agent1:codex, rich',
        windows=(
            WindowSpec(
                name='main',
                order=0,
                layout_spec='agent1:codex, rich',
                agent_names=('agent1',),
                tool_names=('rich',),
            ),
        ),
        entry_window='main',
    )

    plan = build_namespace_topology_plan(config, ccbd_socket_path='/tmp/ccbd.sock', project_root='/repo')

    assert len(plan.windows) == 1
    window = plan.windows[0]
    assert window.kind == 'agents'
    assert window.agent_names == ('agent1',)
    assert window.tool_names == ('rich',)
    assert window.user_layout == 'agent1:codex, rich'
    assert window.realized_layout == 'sidebar; (agent1:codex, rich)'


def test_namespace_topology_plan_keeps_sidebar_pane_for_hidden_tool_row() -> None:
    config = ProjectConfig(
        version=2,
        default_agents=('agent1',),
        agents={'agent1': _spec('agent1', 'codex')},
        layout_spec='agent1:codex',
        windows=(WindowSpec(name='main', order=0, layout_spec='agent1:codex', agent_names=('agent1',)),),
        tool_windows=(ToolWindowSpec(name='logs', order=0, command='tail -f app.log', show_in_sidebar=False),),
        entry_window='main',
    )

    plan = build_namespace_topology_plan(config, ccbd_socket_path='/tmp/ccbd.sock', project_root='/repo')

    tool = plan.windows[1]
    assert tool.name == 'logs'
    assert tool.sidebar is not None
    assert tool.realized_layout == 'sidebar; (tool)'
