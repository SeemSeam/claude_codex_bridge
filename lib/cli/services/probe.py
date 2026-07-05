from __future__ import annotations

from agents.config_loader import load_project_config
from cli.models import ParsedPingCommand
from cli.services.daemon import ping_local_state
from cli.services.doctor_runtime import requirements_summary
from cli.services.ping import ping_target


def probe_summary(context) -> dict[str, object]:
    config = load_project_config(context.project.project_root).config
    local = ping_local_state(context)
    ccbd_status: dict[str, object]
    if local.mount_state == 'unmounted':
        ccbd_status = {
            'mount_state': 'unmounted',
            'health': 'unknown',
            'reason': local.reason,
        }
    else:
        ccbd_status = ping_target(context, ParsedPingCommand(project=None, target='ccbd'))

    agent_statuses: list[dict[str, object]] = []
    for agent_name in sorted(config.agents):
        try:
            status = ping_target(context, ParsedPingCommand(project=None, target=agent_name))
        except Exception as exc:  # pragma: no cover - defensive
            status = {'error': str(exc)}
        agent_statuses.append({'agent_name': agent_name, 'status': status})

    return {
        'project_id': context.project.project_id,
        'ccbd': ccbd_status,
        'agents': agent_statuses,
        'requirements': requirements_summary(),
    }


__all__ = ['probe_summary']
