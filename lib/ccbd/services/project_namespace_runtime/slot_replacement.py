from __future__ import annotations

from dataclasses import dataclass

from terminal_runtime.backend_selection import make_terminal_backend
from terminal_runtime.tmux_identity import apply_ccb_pane_identity

from ..project_namespace import ProjectNamespaceController
from ..project_namespace_pane import same_tmux_socket_path


@dataclass(frozen=True)
class ProjectSlotRecoveryContext:
    project_id: str
    slot_key: str
    tmux_socket_path: str
    tmux_session_name: str
    namespace_epoch: int
    workspace_window_name: str | None
    workspace_window_id: str | None
    workspace_epoch: int
    workspace_root_pane_id: str
    style_index: int


def resolve_project_slot_recovery_context(
    *,
    layout,
    config,
    runtime,
    agent_name: str,
) -> ProjectSlotRecoveryContext | None:
    project_id = str(getattr(runtime, 'project_id', '') or '').strip()
    if not project_id:
        return None
    controller = ProjectNamespaceController(layout, project_id)
    namespace = controller.load()
    if namespace is None or not namespace.ui_attachable:
        return None
    runtime_socket = str(getattr(runtime, 'tmux_socket_path', None) or '').strip()
    if runtime_socket and not same_tmux_socket_path(runtime_socket, namespace.tmux_socket_path):
        return None
    if not runtime_socket and str(getattr(runtime, 'managed_by', '') or '').strip() != 'ccbd':
        return None
    try:
        root_pane_id = controller.root_pane_id(namespace)
    except Exception:
        return None
    if not str(root_pane_id or '').strip().startswith('%'):
        return None
    slot_key = str(getattr(runtime, 'slot_key', None) or agent_name).strip() or str(agent_name).strip()
    return ProjectSlotRecoveryContext(
        project_id=project_id,
        slot_key=slot_key,
        tmux_socket_path=namespace.tmux_socket_path,
        tmux_session_name=namespace.tmux_session_name,
        namespace_epoch=namespace.namespace_epoch,
        workspace_window_name=namespace.workspace_window_name,
        workspace_window_id=namespace.workspace_window_id,
        workspace_epoch=namespace.workspace_epoch,
        workspace_root_pane_id=root_pane_id,
        style_index=style_index_for_agent(config, slot_key),
    )


def inject_project_slot_recovery_hints(session, context: ProjectSlotRecoveryContext | None) -> None:
    if context is None:
        return
    data = getattr(session, 'data', None)
    if not isinstance(data, dict):
        return
    data['ccb_project_id'] = context.project_id
    data['ccb_slot'] = context.slot_key
    data['ccb_managed_by'] = 'ccbd'
    data['ccb_namespace_epoch'] = str(context.namespace_epoch)
    data['ccb_replacement_parent_pane'] = context.workspace_root_pane_id


def relabel_project_slot_pane(
    *,
    pane_id: str,
    context: ProjectSlotRecoveryContext | None,
    session_id: str | None = None,
) -> None:
    if context is None:
        return
    pane_text = str(pane_id or '').strip()
    if not pane_text.startswith('%'):
        return
    try:
        backend = make_terminal_backend(socket_path=context.tmux_socket_path)
    except TypeError:
        backend = make_terminal_backend()
    apply_ccb_pane_identity(
        backend,
        pane_text,
        title=context.slot_key,
        agent_label=context.slot_key,
        project_id=context.project_id,
        order_index=context.style_index,
        slot_key=context.slot_key,
        session_id=session_id,
        namespace_epoch=context.namespace_epoch,
        managed_by='ccbd',
    )


def style_index_for_agent(config, agent_name: str) -> int:
    defaults = tuple(getattr(config, 'default_agents', ()) or ())
    try:
        return defaults.index(agent_name)
    except ValueError:
        return 0


__all__ = [
    'ProjectSlotRecoveryContext',
    'inject_project_slot_recovery_hints',
    'relabel_project_slot_pane',
    'resolve_project_slot_recovery_context',
    'style_index_for_agent',
]
