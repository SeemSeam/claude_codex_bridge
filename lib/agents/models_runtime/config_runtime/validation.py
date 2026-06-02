from __future__ import annotations

from agents.models_runtime.layout import LayoutLeaf, LayoutNode, build_balanced_layout, parse_layout_spec
from agents.models_runtime.enums import WorkspaceMode

from ..names import AgentValidationError, normalize_agent_name


def normalize_agent_specs(agents: dict) -> dict:
    normalized_agents: dict[str, object] = {}
    for key, spec in dict(agents).items():
        normalized_key = normalize_agent_name(key)
        if normalized_key in normalized_agents:
            raise AgentValidationError(f'duplicate agent {normalized_key!r}')
        if spec.name != normalized_key:
            raise AgentValidationError(
                f'agent key {normalized_key!r} does not match spec name {spec.name!r}'
            )
        normalized_agents[normalized_key] = spec
    if not normalized_agents:
        raise AgentValidationError('at least one agent must be configured')
    return normalized_agents


def normalize_default_agents(default_agents: tuple[str, ...], *, normalized_agents: dict) -> tuple[str, ...]:
    defaults = tuple(normalize_agent_name(item) for item in default_agents)
    if not defaults:
        raise AgentValidationError('default_agents cannot be empty')
    if len(set(defaults)) != len(defaults):
        raise AgentValidationError('default_agents cannot contain duplicates')
    missing = [name for name in defaults if name not in normalized_agents]
    if missing:
        raise AgentValidationError(f'default_agents reference unknown agents: {missing}')
    return defaults


def _default_layout_spec(
    *,
    default_agents: tuple[str, ...],
    normalized_agents: dict,
    cmd_enabled: bool,
) -> str:
    return build_balanced_layout(
        default_agents,
        providers_by_agent={name: normalized_agents[name].provider for name in default_agents},
        workspace_modes_by_agent={
            name: WorkspaceMode.GIT_WORKTREE.value
            for name in default_agents
            if normalized_agents[name].workspace_mode is WorkspaceMode.GIT_WORKTREE
        },
        cmd_enabled=bool(cmd_enabled),
    ).render()


def _parse_resolved_layout(rendered: str) -> object:
    try:
        return parse_layout_spec(rendered)
    except Exception as exc:
        raise AgentValidationError(f'invalid layout_spec: {exc}') from exc


def _normalize_layout_leaf_name(name: str) -> str:
    token = str(name or '').strip()
    if token.lower() == 'cmd':
        return 'cmd'
    return normalize_agent_name(token)


def _normalize_layout_tree(node: LayoutNode) -> LayoutNode:
    if node.kind == 'leaf':
        assert node.leaf is not None
        return LayoutNode(
            kind='leaf',
            leaf=LayoutLeaf(
                name=_normalize_layout_leaf_name(node.leaf.name),
                provider=node.leaf.provider,
                workspace_mode=node.leaf.workspace_mode,
                percent=node.leaf.percent,
            ),
        )
    assert node.left is not None
    assert node.right is not None
    return LayoutNode(
        kind=node.kind,
        left=_normalize_layout_tree(node.left),
        right=_normalize_layout_tree(node.right),
    )


def _expected_layout_names(default_agents: tuple[str, ...], *, cmd_enabled: bool) -> set[str]:
    expected = set(default_agents)
    if cmd_enabled:
        expected.add('cmd')
    return expected


def _validate_layout_names(
    layout_names: tuple[str, ...],
    *,
    default_agents: tuple[str, ...],
    cmd_enabled: bool,
) -> None:
    if set(layout_names) != _expected_layout_names(default_agents, cmd_enabled=cmd_enabled):
        raise AgentValidationError(
            'layout_spec must include each configured agent exactly once'
            + (' and cmd' if cmd_enabled else '')
        )
    if len(set(layout_names)) != len(layout_names):
        raise AgentValidationError('layout_spec cannot contain duplicate leaves')
    if cmd_enabled and layout_names[0] != 'cmd':
        raise AgentValidationError('layout_spec must anchor cmd as the first pane when cmd_enabled=true')


def resolve_layout_spec(
    *,
    default_agents: tuple[str, ...],
    normalized_agents: dict,
    cmd_enabled: bool,
    layout_spec: str | None,
) -> str:
    rendered = str(layout_spec or '').strip()
    if not rendered:
        rendered = _default_layout_spec(
            default_agents=default_agents,
            normalized_agents=normalized_agents,
            cmd_enabled=cmd_enabled,
        )
    layout = _normalize_layout_tree(_parse_resolved_layout(rendered))
    layout_names = tuple(leaf.name for leaf in layout.iter_leaves())
    _validate_layout_names(
        layout_names,
        default_agents=default_agents,
        cmd_enabled=cmd_enabled,
    )
    return layout.render()


__all__ = ['normalize_agent_specs', 'normalize_default_agents', 'resolve_layout_spec']
