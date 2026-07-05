from __future__ import annotations

import argparse

from cli.models import (
    ParsedAckCommand,
    ParsedAgentCommand,
    ParsedCancelCommand,
    ParsedClearCommand,
    ParsedCleanupCommand,
    ParsedConfigValidateCommand,
    ParsedDoctorCommand,
    ParsedIdentityCommand,
    ParsedInboxCommand,
    ParsedKillCommand,
    ParsedLayoutCommand,
    ParsedLogsCommand,
    ParsedLoopCapacityCommand,
    ParsedLoopTopologyCommand,
    ParsedLoopRunOnceCommand,
    ParsedLoopRunnerCommand,
    ParsedMaintenanceCommand,
    ParsedMobileCommand,
    ParsedPlanTaskCommand,
    ParsedPendCommand,
    ParsedPingCommand,
    ParsedProbeCommand,
    ParsedPsCommand,
    ParsedQuestionCommand,
    ParsedQueueCommand,
    ParsedReloadCommand,
    ParsedRestartCommand,
    ParsedResubmitCommand,
    ParsedRetryCommand,
    ParsedTraceCommand,
    ParsedWaitCommand,
    ParsedWatchCommand,
    ParsedWhyCommand,
)

from .common import parse_args, require_no_extra
from .constants import WAIT_COMMAND_TO_MODE


def parse_cancel(tokens: list[str], *, project: str | None, error_type) -> ParsedCancelCommand:
    if len(tokens) != 1:
        raise error_type('cancel requires <job_id>')
    return ParsedCancelCommand(project=project, job_id=tokens[0])


def parse_clear(tokens: list[str], *, project: str | None, error_type) -> ParsedClearCommand:
    parser = argparse.ArgumentParser(prog='ccb clear', add_help=False)
    parser.add_argument('agent_names', nargs='*')
    namespace = parse_args(parser, tokens, error_message='invalid clear command', error_type=error_type)
    agent_names = tuple(str(item).strip() for item in namespace.agent_names if str(item).strip())
    if 'all' in {item.lower() for item in agent_names} and len(agent_names) > 1:
        raise error_type('clear target "all" cannot be combined with agent names')
    if tuple(item.lower() for item in agent_names) == ('all',):
        agent_names = ()
    return ParsedClearCommand(project=project, agent_names=agent_names)


def parse_restart(tokens: list[str], *, project: str | None, error_type) -> ParsedRestartCommand:
    if len(tokens) != 1:
        raise error_type('restart requires exactly one <agent_name>')
    agent_name = str(tokens[0]).strip()
    if not agent_name:
        raise error_type('restart requires exactly one <agent_name>')
    if agent_name.lower() == 'all':
        raise error_type('restart all is not supported; restart exactly one configured agent')
    return ParsedRestartCommand(project=project, agent_name=agent_name)


def parse_maintenance(tokens: list[str], *, project: str | None, error_type) -> ParsedMaintenanceCommand:
    if not tokens:
        return ParsedMaintenanceCommand(project=project, action='status')
    action = str(tokens[0] or '').strip().lower()
    if action not in {'status', 'tick', 'schedule', 'runner', 'enable', 'disable'}:
        raise error_type('maintenance supports: status, tick, schedule, runner, enable, disable')
    return ParsedMaintenanceCommand(project=project, action=action, args=tuple(tokens[1:]))


def parse_mobile(tokens: list[str], *, project: str | None, error_type) -> ParsedMobileCommand:
    if not tokens:
        raise error_type('mobile requires one of: serve, devices, revoke')
    action = str(tokens[0] or '').strip().lower()
    if action == 'devices':
        require_no_extra(tokens[1:], command='mobile devices', error_type=error_type)
        return ParsedMobileCommand(project=project, action=action)
    if action == 'revoke':
        parser = argparse.ArgumentParser(prog='ccb mobile revoke', add_help=False)
        parser.add_argument('device_id')
        namespace = parse_args(parser, tokens[1:], error_message='invalid mobile revoke command', error_type=error_type)
        device_id = str(namespace.device_id or '').strip()
        if not device_id:
            raise error_type('mobile revoke requires <device_id>')
        return ParsedMobileCommand(project=project, action=action, device_id=device_id)
    if action != 'serve':
        raise error_type('mobile only supports: serve, devices, revoke')
    parser = argparse.ArgumentParser(prog='ccb mobile serve', add_help=False)
    parser.add_argument('--listen', default='127.0.0.1:8787')
    parser.add_argument('--public-url', default=None)
    parser.add_argument(
        '--route-provider',
        default='lan',
        choices=('lan', 'tailnet', 'cloudflare_tunnel', 'relay'),
    )
    namespace = parse_args(parser, tokens[1:], error_message='invalid mobile serve command', error_type=error_type)
    public_url = str(namespace.public_url).strip() if namespace.public_url is not None else None
    return ParsedMobileCommand(
        project=project,
        action=action,
        listen=str(namespace.listen),
        public_url=public_url or None,
        route_provider=str(namespace.route_provider),
    )


def parse_agent(tokens: list[str], *, project: str | None, error_type) -> ParsedAgentCommand:
    if not tokens:
        raise error_type('agent requires one of: status, show, add, move, hide, park, resume, remove, release')
    action = str(tokens[0] or '').strip().lower()
    rest = tokens[1:]
    if action == 'status':
        parser = argparse.ArgumentParser(prog='ccb agent status', add_help=False)
        parser.add_argument('--class', dest='role_class', default=None)
        parser.add_argument('--json', dest='json_output', action='store_true')
        namespace = parse_args(parser, rest, error_message='invalid agent status command', error_type=error_type)
        return ParsedAgentCommand(
            project=project,
            action=action,
            role_class=str(namespace.role_class) if namespace.role_class is not None else None,
            json_output=bool(namespace.json_output),
        )
    if action == 'show':
        parser = argparse.ArgumentParser(prog='ccb agent show', add_help=False)
        parser.add_argument('agent_name')
        parser.add_argument('--json', dest='json_output', action='store_true')
        namespace = parse_args(parser, rest, error_message='invalid agent show command', error_type=error_type)
        return ParsedAgentCommand(
            project=project,
            action=action,
            agent_name=str(namespace.agent_name),
            json_output=bool(namespace.json_output),
        )
    if action == 'add':
        parser = argparse.ArgumentParser(prog='ccb agent add', add_help=False)
        parser.add_argument('agent_spec')
        parser.add_argument('--profile', default=None)
        parser.add_argument('--role', default=None)
        parser.add_argument('--provider', default=None)
        parser.add_argument('--model', default=None)
        parser.add_argument('--thinking', default=None)
        parser.add_argument('--workspace-mode', default=None)
        parser.add_argument('--window', dest='window_name', default=None)
        parser.add_argument('--window-class', default=None)
        parser.add_argument('--loop-id', default=None)
        parser.add_argument('--node-id', default=None)
        parser.add_argument('--lifetime', default=None)
        visibility = parser.add_mutually_exclusive_group()
        visibility.add_argument('--visible', dest='visibility', action='store_const', const='visible')
        visibility.add_argument('--hidden', dest='visibility', action='store_const', const='hidden')
        visibility.add_argument('--parked', dest='visibility', action='store_const', const='parked')
        parser.add_argument('--json', dest='json_output', action='store_true')
        namespace = parse_args(parser, rest, error_message='invalid agent add command', error_type=error_type)
        agent_name, provider = _parse_agent_spec_token(
            str(namespace.agent_spec),
            explicit_provider=str(namespace.provider) if namespace.provider is not None else None,
            error_type=error_type,
        )
        return ParsedAgentCommand(
            project=project,
            action=action,
            agent_name=agent_name,
            provider=provider,
            profile=str(namespace.profile) if namespace.profile is not None else None,
            role=str(namespace.role) if namespace.role is not None else None,
            model=str(namespace.model) if namespace.model is not None else None,
            thinking=str(namespace.thinking) if namespace.thinking is not None else None,
            workspace_mode=str(namespace.workspace_mode) if namespace.workspace_mode is not None else None,
            window_name=str(namespace.window_name) if namespace.window_name is not None else None,
            window_class=str(namespace.window_class) if namespace.window_class is not None else None,
            loop_id=str(namespace.loop_id) if namespace.loop_id is not None else None,
            node_id=str(namespace.node_id) if namespace.node_id is not None else None,
            lifetime=str(namespace.lifetime) if namespace.lifetime is not None else None,
            visibility=str(namespace.visibility) if namespace.visibility is not None else None,
            json_output=bool(namespace.json_output),
        )
    if action == 'move':
        parser = argparse.ArgumentParser(prog='ccb agent move', add_help=False)
        parser.add_argument('agent_name', nargs='?')
        parser.add_argument('--agents', dest='agent_names', default=None)
        parser.add_argument('--window', dest='window_name', default=None)
        parser.add_argument('--window-class', default=None)
        parser.add_argument('--loop-id', default=None)
        parser.add_argument('--node-id', default=None)
        parser.add_argument('--reason', default=None)
        parser.add_argument('--json', dest='json_output', action='store_true')
        namespace = parse_args(parser, rest, error_message='invalid agent move command', error_type=error_type)
        has_target = any(
            getattr(namespace, field, None) is not None
            for field in ('window_name', 'window_class', 'loop_id', 'node_id')
        )
        if not has_target:
            raise error_type('agent move requires --window, --window-class, --loop-id, or --node-id')
        batch_agents = _parse_csv_values(str(namespace.agent_names)) if namespace.agent_names is not None else ()
        positional_agent = str(namespace.agent_name).strip() if namespace.agent_name is not None else ''
        if bool(batch_agents) == bool(positional_agent):
            raise error_type('agent move requires exactly one <agent_name> or --agents a,b')
        return ParsedAgentCommand(
            project=project,
            action=action,
            agent_name=positional_agent or None,
            agent_names=batch_agents,
            window_name=str(namespace.window_name) if namespace.window_name is not None else None,
            window_class=str(namespace.window_class) if namespace.window_class is not None else None,
            loop_id=str(namespace.loop_id) if namespace.loop_id is not None else None,
            node_id=str(namespace.node_id) if namespace.node_id is not None else None,
            reason=str(namespace.reason) if namespace.reason is not None else None,
            json_output=bool(namespace.json_output),
        )
    if action in {'hide', 'park', 'resume'}:
        parser = argparse.ArgumentParser(prog=f'ccb agent {action}', add_help=False)
        parser.add_argument('agent_name', nargs='?')
        parser.add_argument('--agents', dest='agent_names', default=None)
        if action == 'resume':
            visibility = parser.add_mutually_exclusive_group()
            visibility.add_argument('--visible', dest='visibility', action='store_const', const='visible')
            visibility.add_argument('--hidden', dest='visibility', action='store_const', const='hidden')
        parser.add_argument('--reason', default=None)
        parser.add_argument('--json', dest='json_output', action='store_true')
        namespace = parse_args(parser, rest, error_message=f'invalid agent {action} command', error_type=error_type)
        batch_agents = _parse_csv_values(str(namespace.agent_names)) if namespace.agent_names is not None else ()
        positional_agent = str(namespace.agent_name).strip() if namespace.agent_name is not None else ''
        if bool(batch_agents) == bool(positional_agent):
            raise error_type(f'agent {action} requires exactly one <agent_name> or --agents a,b')
        return ParsedAgentCommand(
            project=project,
            action=action,
            agent_name=positional_agent or None,
            agent_names=batch_agents,
            visibility=(
                str(getattr(namespace, 'visibility'))
                if getattr(namespace, 'visibility', None) is not None
                else None
            ),
            reason=str(namespace.reason) if namespace.reason is not None else None,
            json_output=bool(namespace.json_output),
        )
    if action == 'remove':
        parser = argparse.ArgumentParser(prog='ccb agent remove', add_help=False)
        parser.add_argument('agent_name', nargs='?')
        parser.add_argument('--agents', dest='agent_names', default=None)
        parser.add_argument('--policy', default='auto', choices=('auto', 'hide', 'park', 'unload', 'kill'))
        parser.add_argument('--idle-only', dest='idle_only', action='store_true')
        parser.add_argument('--summary', dest='summary_policy', default=None, choices=('required', 'best-effort', 'none'))
        parser.add_argument('--force', dest='force', action='store_true')
        parser.add_argument('--reason', default=None)
        parser.add_argument('--json', dest='json_output', action='store_true')
        namespace = parse_args(parser, rest, error_message='invalid agent remove command', error_type=error_type)
        batch_agents = _parse_csv_values(str(namespace.agent_names)) if namespace.agent_names is not None else ()
        positional_agent = str(namespace.agent_name).strip() if namespace.agent_name is not None else ''
        if bool(batch_agents) == bool(positional_agent):
            raise error_type('agent remove requires exactly one <agent_name> or --agents a,b')
        return ParsedAgentCommand(
            project=project,
            action=action,
            agent_name=positional_agent or None,
            agent_names=batch_agents,
            policy=str(namespace.policy),
            idle_only=bool(namespace.idle_only),
            summary_policy=str(namespace.summary_policy) if namespace.summary_policy is not None else None,
            force=bool(namespace.force),
            reason=str(namespace.reason) if namespace.reason is not None else None,
            json_output=bool(namespace.json_output),
        )
    if action == 'release':
        parser = argparse.ArgumentParser(prog='ccb agent release', add_help=False)
        parser.add_argument('agent_name', nargs='?')
        parser.add_argument('--agents', dest='agent_names', default=None)
        parser.add_argument('--policy', default='auto', choices=('auto', 'hide', 'park', 'unload'))
        parser.add_argument('--idle-only', dest='idle_only', action='store_true')
        parser.add_argument('--summary', dest='summary_policy', default=None, choices=('required', 'best-effort', 'none'))
        parser.add_argument('--reason', default=None)
        parser.add_argument('--json', dest='json_output', action='store_true')
        namespace = parse_args(parser, rest, error_message='invalid agent release command', error_type=error_type)
        batch_agents = _parse_csv_values(str(namespace.agent_names)) if namespace.agent_names is not None else ()
        positional_agent = str(namespace.agent_name).strip() if namespace.agent_name is not None else ''
        if bool(batch_agents) == bool(positional_agent):
            raise error_type('agent release requires exactly one <agent_name> or --agents a,b')
        return ParsedAgentCommand(
            project=project,
            action=action,
            agent_name=positional_agent or None,
            agent_names=batch_agents,
            policy=str(namespace.policy),
            idle_only=bool(namespace.idle_only),
            summary_policy=str(namespace.summary_policy) if namespace.summary_policy is not None else None,
            reason=str(namespace.reason) if namespace.reason is not None else None,
            json_output=bool(namespace.json_output),
        )
    raise error_type('agent only supports: status, show, add, move, hide, park, resume, remove, release')


def parse_layout(tokens: list[str], *, project: str | None, error_type) -> ParsedLayoutCommand:
    if not tokens:
        raise error_type('layout requires one of: plan, smoke, dynamic-smoke, status, resolve, move-plan, arrange')
    action = str(tokens[0] or '').strip().lower()
    if action not in {'plan', 'smoke', 'dynamic-smoke', 'status', 'resolve', 'move-plan', 'arrange'}:
        raise error_type('layout only supports: plan, smoke, dynamic-smoke, status, resolve, move-plan, arrange')
    if action == 'status':
        parser = argparse.ArgumentParser(prog='ccb layout status', add_help=False)
        parser.add_argument('--json', dest='json_output', action='store_true')
        namespace = parse_args(parser, tokens[1:], error_message='invalid layout status command', error_type=error_type)
        return ParsedLayoutCommand(
            project=project,
            action=action,
            json_output=bool(namespace.json_output),
        )
    if action == 'resolve':
        parser = argparse.ArgumentParser(prog='ccb layout resolve', add_help=False)
        parser.add_argument('agent_name')
        parser.add_argument('--window', dest='window_name', default=None)
        parser.add_argument('--window-class', dest='window_class', default=None)
        parser.add_argument('--loop-id', default=None)
        parser.add_argument('--node-id', default=None)
        parser.add_argument('--json', dest='json_output', action='store_true')
        namespace = parse_args(parser, tokens[1:], error_message='invalid layout resolve command', error_type=error_type)
        return ParsedLayoutCommand(
            project=project,
            action=action,
            agent_name=str(namespace.agent_name),
            window_name=str(namespace.window_name) if namespace.window_name is not None else None,
            window_class=str(namespace.window_class) if namespace.window_class is not None else None,
            loop_id=str(namespace.loop_id) if namespace.loop_id is not None else None,
            node_id=str(namespace.node_id) if namespace.node_id is not None else None,
            json_output=bool(namespace.json_output),
        )
    if action == 'move-plan':
        parser = argparse.ArgumentParser(prog='ccb layout move-plan', add_help=False)
        parser.add_argument('agent_name')
        parser.add_argument('--window', dest='window_name', default=None)
        parser.add_argument('--window-class', dest='window_class', default=None)
        parser.add_argument('--loop-id', default=None)
        parser.add_argument('--node-id', default=None)
        parser.add_argument('--json', dest='json_output', action='store_true')
        namespace = parse_args(parser, tokens[1:], error_message='invalid layout move-plan command', error_type=error_type)
        has_target = any(
            getattr(namespace, field, None) is not None
            for field in ('window_name', 'window_class', 'loop_id', 'node_id')
        )
        if not has_target:
            raise error_type('layout move-plan requires --window, --window-class, --loop-id, or --node-id')
        return ParsedLayoutCommand(
            project=project,
            action=action,
            agent_name=str(namespace.agent_name),
            window_name=str(namespace.window_name) if namespace.window_name is not None else None,
            window_class=str(namespace.window_class) if namespace.window_class is not None else None,
            loop_id=str(namespace.loop_id) if namespace.loop_id is not None else None,
            node_id=str(namespace.node_id) if namespace.node_id is not None else None,
            json_output=bool(namespace.json_output),
        )
    if action == 'arrange':
        parser = argparse.ArgumentParser(prog='ccb layout arrange', add_help=False)
        parser.add_argument('--window', dest='window_name', required=True)
        parser.add_argument('--timeout', dest='timeout_s', type=float, default=5.0)
        parser.add_argument('--json', dest='json_output', action='store_true')
        namespace = parse_args(parser, tokens[1:], error_message='invalid layout arrange command', error_type=error_type)
        if float(namespace.timeout_s) <= 0:
            raise error_type('layout arrange --timeout must be positive')
        return ParsedLayoutCommand(
            project=project,
            action=action,
            window_name=str(namespace.window_name),
            timeout_s=float(namespace.timeout_s),
            json_output=bool(namespace.json_output),
        )
    parser = argparse.ArgumentParser(prog=f'ccb layout {action}', add_help=False)
    parser.add_argument('--panes', type=int, required=True)
    parser.add_argument('--window-prefix', default='layout')
    parser.add_argument('--json', dest='json_output', action='store_true')
    if action in {'smoke', 'dynamic-smoke'}:
        parser.add_argument('--session', dest='session_name', default=None)
        parser.add_argument('--keep', dest='keep', action='store_true')
    namespace = parse_args(parser, tokens[1:], error_message=f'invalid layout {action} command', error_type=error_type)
    if int(namespace.panes) <= 0:
        raise error_type('layout --panes must be positive')
    return ParsedLayoutCommand(
        project=project,
        action=action,
        panes=int(namespace.panes),
        window_prefix=str(namespace.window_prefix),
        session_name=str(namespace.session_name) if getattr(namespace, 'session_name', None) is not None else None,
        cleanup=not bool(getattr(namespace, 'keep', False)),
        json_output=bool(namespace.json_output),
    )


def parse_loop(tokens: list[str], *, project: str | None, error_type) -> ParsedLoopCapacityCommand | ParsedLoopTopologyCommand | ParsedLoopRunOnceCommand | ParsedLoopRunnerCommand:
    if not tokens:
        raise error_type('loop requires one of: capacity, topology, run-once, runner')
    group = str(tokens[0] or '').strip().lower()
    if group == 'run-once':
        return _parse_loop_run_once(tokens[1:], project=project, error_type=error_type)
    if group == 'runner':
        return _parse_loop_runner(tokens[1:], project=project, error_type=error_type)
    if group == 'topology':
        return _parse_loop_topology(tokens[1:], project=project, error_type=error_type)
    if group != 'capacity':
        raise error_type('loop only supports: ccb loop capacity <ensure|status|release>, ccb loop topology <propose|validate|commit|reconcile|status|release>, ccb loop run-once, or ccb loop runner')
    if len(tokens) < 2:
        raise error_type('loop capacity requires one of: ensure, status, release')
    action = str(tokens[1] or '').strip().lower()
    rest = tokens[2:]
    if action == 'ensure':
        parser = argparse.ArgumentParser(prog='ccb loop capacity ensure', add_help=False)
        parser.add_argument('--loop-id', required=True)
        parser.add_argument('--profile', dest='profiles', action='append', default=[])
        parser.add_argument('--json', dest='json_output', action='store_true')
        namespace = parse_args(parser, rest, error_message='invalid loop capacity ensure command', error_type=error_type)
        return ParsedLoopCapacityCommand(
            project=project,
            action=action,
            loop_id=str(namespace.loop_id),
            profile_counts=_parse_loop_profile_counts(tuple(namespace.profiles), error_type=error_type),
            json_output=bool(namespace.json_output),
        )
    if action == 'status':
        parser = argparse.ArgumentParser(prog='ccb loop capacity status', add_help=False)
        parser.add_argument('--loop-id', required=True)
        parser.add_argument('--json', dest='json_output', action='store_true')
        namespace = parse_args(parser, rest, error_message='invalid loop capacity status command', error_type=error_type)
        return ParsedLoopCapacityCommand(
            project=project,
            action=action,
            loop_id=str(namespace.loop_id),
            json_output=bool(namespace.json_output),
        )
    if action == 'release':
        parser = argparse.ArgumentParser(prog='ccb loop capacity release', add_help=False)
        parser.add_argument('--loop-id', required=True)
        parser.add_argument('--policy', default='auto', choices=('auto', 'idle-only'))
        parser.add_argument('--idle-only', dest='idle_only', action='store_true')
        parser.add_argument('--json', dest='json_output', action='store_true')
        namespace = parse_args(parser, rest, error_message='invalid loop capacity release command', error_type=error_type)
        return ParsedLoopCapacityCommand(
            project=project,
            action=action,
            loop_id=str(namespace.loop_id),
            policy=str(namespace.policy),
            idle_only=bool(namespace.idle_only),
            json_output=bool(namespace.json_output),
        )
    raise error_type('loop capacity only supports: ensure, status, release')


def _parse_loop_topology(tokens: list[str], *, project: str | None, error_type) -> ParsedLoopTopologyCommand:
    if len(tokens) < 1:
        raise error_type('loop topology requires one of: propose, validate, commit, reconcile, status, release')
    action = str(tokens[0] or '').strip().lower()
    rest = tokens[1:]
    if action == 'propose':
        parser = argparse.ArgumentParser(prog='ccb loop topology propose', add_help=False)
        parser.add_argument('--loop-id', required=True)
        parser.add_argument('--from', dest='from_path', required=True)
        parser.add_argument('--proposal-id', dest='proposal_id', default=None)
        parser.add_argument('--json', dest='json_output', action='store_true')
        namespace = parse_args(parser, rest, error_message='invalid loop topology propose command', error_type=error_type)
        return ParsedLoopTopologyCommand(
            project=project,
            action=action,
            loop_id=str(namespace.loop_id),
            from_path=str(namespace.from_path),
            proposal_id=str(namespace.proposal_id) if namespace.proposal_id is not None else None,
            json_output=bool(namespace.json_output),
        )
    if action == 'validate':
        parser = argparse.ArgumentParser(prog='ccb loop topology validate', add_help=False)
        parser.add_argument('--loop-id', required=True)
        parser.add_argument('--proposal', dest='proposal_id', required=True)
        parser.add_argument('--json', dest='json_output', action='store_true')
        namespace = parse_args(parser, rest, error_message='invalid loop topology validate command', error_type=error_type)
        return ParsedLoopTopologyCommand(
            project=project,
            action=action,
            loop_id=str(namespace.loop_id),
            proposal_id=str(namespace.proposal_id),
            json_output=bool(namespace.json_output),
        )
    if action == 'commit':
        parser = argparse.ArgumentParser(prog='ccb loop topology commit', add_help=False)
        parser.add_argument('--loop-id', required=True)
        parser.add_argument('--proposal', dest='proposal_id', required=True)
        parser.add_argument('--apply', dest='apply', action='store_true')
        parser.add_argument('--json', dest='json_output', action='store_true')
        namespace = parse_args(parser, rest, error_message='invalid loop topology commit command', error_type=error_type)
        return ParsedLoopTopologyCommand(
            project=project,
            action=action,
            loop_id=str(namespace.loop_id),
            proposal_id=str(namespace.proposal_id),
            apply=bool(namespace.apply),
            json_output=bool(namespace.json_output),
        )
    if action in {'reconcile', 'status'}:
        parser = argparse.ArgumentParser(prog=f'ccb loop topology {action}', add_help=False)
        parser.add_argument('--loop-id', required=True)
        parser.add_argument('--json', dest='json_output', action='store_true')
        namespace = parse_args(parser, rest, error_message=f'invalid loop topology {action} command', error_type=error_type)
        return ParsedLoopTopologyCommand(
            project=project,
            action=action,
            loop_id=str(namespace.loop_id),
            json_output=bool(namespace.json_output),
        )
    if action == 'release':
        parser = argparse.ArgumentParser(prog='ccb loop topology release', add_help=False)
        parser.add_argument('--loop-id', required=True)
        parser.add_argument('--policy', default='auto', choices=('auto', 'idle-only'))
        parser.add_argument('--idle-only', dest='idle_only', action='store_true')
        parser.add_argument('--json', dest='json_output', action='store_true')
        namespace = parse_args(parser, rest, error_message='invalid loop topology release command', error_type=error_type)
        return ParsedLoopTopologyCommand(
            project=project,
            action=action,
            loop_id=str(namespace.loop_id),
            policy=str(namespace.policy),
            idle_only=bool(namespace.idle_only),
            json_output=bool(namespace.json_output),
        )
    raise error_type('loop topology only supports: propose, validate, commit, reconcile, status, release')


def parse_plan(tokens: list[str], *, project: str | None, error_type) -> ParsedPlanTaskCommand:
    if not tokens:
        raise error_type('plan requires one of: task-create, task-artifact, task-status, task-bind-loop, task-import-round, task-show, task-list, breadcrumb')
    action = str(tokens[0] or '').strip().lower()
    rest = tokens[1:]
    if action == 'task-create':
        parser = argparse.ArgumentParser(prog='ccb plan task-create', add_help=False)
        parser.add_argument('--plan', required=True)
        parser.add_argument('--title', required=True)
        parser.add_argument('--task-id', default=None)
        parser.add_argument('--json', dest='json_output', action='store_true')
        namespace = parse_args(parser, rest, error_message='invalid plan task-create command', error_type=error_type)
        return ParsedPlanTaskCommand(
            project=project,
            action=action,
            plan_slug=str(namespace.plan),
            title=str(namespace.title),
            task_id=str(namespace.task_id) if namespace.task_id is not None else None,
            json_output=bool(namespace.json_output),
        )
    if action == 'task-artifact':
        parser = argparse.ArgumentParser(prog='ccb plan task-artifact', add_help=False)
        parser.add_argument('--task', required=True)
        parser.add_argument('--kind', required=True)
        parser.add_argument('--file', required=True)
        parser.add_argument('--json', dest='json_output', action='store_true')
        namespace = parse_args(parser, rest, error_message='invalid plan task-artifact command', error_type=error_type)
        return ParsedPlanTaskCommand(
            project=project,
            action=action,
            task_id=str(namespace.task),
            artifact_kind=str(namespace.kind),
            file_path=str(namespace.file),
            json_output=bool(namespace.json_output),
        )
    if action == 'task-status':
        parser = argparse.ArgumentParser(prog='ccb plan task-status', add_help=False)
        parser.add_argument('--task', required=True)
        parser.add_argument('--status', required=True)
        parser.add_argument('--json', dest='json_output', action='store_true')
        namespace = parse_args(parser, rest, error_message='invalid plan task-status command', error_type=error_type)
        return ParsedPlanTaskCommand(
            project=project,
            action=action,
            task_id=str(namespace.task),
            status=str(namespace.status),
            json_output=bool(namespace.json_output),
        )
    if action == 'task-bind-loop':
        parser = argparse.ArgumentParser(prog='ccb plan task-bind-loop', add_help=False)
        parser.add_argument('--task', required=True)
        parser.add_argument('--loop', dest='loop_id', required=True)
        parser.add_argument('--json', dest='json_output', action='store_true')
        namespace = parse_args(parser, rest, error_message='invalid plan task-bind-loop command', error_type=error_type)
        return ParsedPlanTaskCommand(
            project=project,
            action=action,
            task_id=str(namespace.task),
            loop_id=str(namespace.loop_id),
            json_output=bool(namespace.json_output),
        )
    if action == 'task-import-round':
        parser = argparse.ArgumentParser(prog='ccb plan task-import-round', add_help=False)
        parser.add_argument('--task', required=True)
        parser.add_argument('--loop', dest='loop_id', required=True)
        parser.add_argument('--result', required=True)
        parser.add_argument('--report', dest='file_path', required=True)
        parser.add_argument('--json', dest='json_output', action='store_true')
        namespace = parse_args(parser, rest, error_message='invalid plan task-import-round command', error_type=error_type)
        return ParsedPlanTaskCommand(
            project=project,
            action=action,
            task_id=str(namespace.task),
            loop_id=str(namespace.loop_id),
            result=str(namespace.result),
            file_path=str(namespace.file_path),
            json_output=bool(namespace.json_output),
        )
    if action == 'task-show':
        parser = argparse.ArgumentParser(prog='ccb plan task-show', add_help=False)
        parser.add_argument('--task', required=True)
        parser.add_argument('--json', dest='json_output', action='store_true')
        namespace = parse_args(parser, rest, error_message='invalid plan task-show command', error_type=error_type)
        return ParsedPlanTaskCommand(
            project=project,
            action=action,
            task_id=str(namespace.task),
            json_output=bool(namespace.json_output),
        )
    if action == 'task-list':
        parser = argparse.ArgumentParser(prog='ccb plan task-list', add_help=False)
        parser.add_argument('--plan', required=True)
        parser.add_argument('--json', dest='json_output', action='store_true')
        namespace = parse_args(parser, rest, error_message='invalid plan task-list command', error_type=error_type)
        return ParsedPlanTaskCommand(
            project=project,
            action=action,
            plan_slug=str(namespace.plan),
            json_output=bool(namespace.json_output),
        )
    if action == 'breadcrumb':
        parser = argparse.ArgumentParser(prog='ccb plan breadcrumb', add_help=False)
        parser.add_argument('--task', required=True)
        namespace = parse_args(parser, rest, error_message='invalid plan breadcrumb command', error_type=error_type)
        return ParsedPlanTaskCommand(project=project, action=action, task_id=str(namespace.task))
    raise error_type('plan only supports: task-create, task-artifact, task-status, task-bind-loop, task-import-round, task-show, task-list, breadcrumb')


def parse_question(tokens: list[str], *, project: str | None, error_type) -> ParsedQuestionCommand:
    if not tokens:
        raise error_type('question requires one of: candidate-import, user-batch-import, answer-import, normalized-import, status')
    action = str(tokens[0] or '').strip().lower()
    rest = tokens[1:]
    if action in {'candidate-import', 'user-batch-import', 'answer-import', 'normalized-import'}:
        parser = argparse.ArgumentParser(prog=f'ccb question {action}', add_help=False)
        parser.add_argument('--task', required=True)
        parser.add_argument('--file', required=True)
        parser.add_argument('--json', dest='json_output', action='store_true')
        namespace = parse_args(parser, rest, error_message=f'invalid question {action} command', error_type=error_type)
        return ParsedQuestionCommand(
            project=project,
            action=action,
            task_id=str(namespace.task),
            file_path=str(namespace.file),
            json_output=bool(namespace.json_output),
        )
    if action == 'status':
        parser = argparse.ArgumentParser(prog='ccb question status', add_help=False)
        parser.add_argument('--task', required=True)
        parser.add_argument('--json', dest='json_output', action='store_true')
        namespace = parse_args(parser, rest, error_message='invalid question status command', error_type=error_type)
        return ParsedQuestionCommand(
            project=project,
            action=action,
            task_id=str(namespace.task),
            json_output=bool(namespace.json_output),
        )
    raise error_type('question only supports: candidate-import, user-batch-import, answer-import, normalized-import, status')


def _parse_loop_run_once(tokens: list[str], *, project: str | None, error_type) -> ParsedLoopRunOnceCommand:
    parser = argparse.ArgumentParser(prog='ccb loop run-once', add_help=False)
    parser.add_argument('--loop-id', default=None)
    parser.add_argument('--task', default=None)
    parser.add_argument('--task-id', default=None)
    parser.add_argument('--worker-profile', default='worker')
    parser.add_argument('--reviewer-profile', default='code_reviewer')
    parser.add_argument('--orchestrator', default='orchestrator')
    parser.add_argument('--round-checker', default='round_checker')
    parser.add_argument('--timeout', type=float, default=None)
    parser.add_argument('--json', dest='json_output', action='store_true')
    namespace = parse_args(parser, tokens, error_message='invalid loop run-once command', error_type=error_type)
    if namespace.timeout is not None and float(namespace.timeout) <= 0:
        raise error_type('loop run-once --timeout must be positive')
    if namespace.task is None and namespace.task_id is None:
        raise error_type('loop run-once requires --task or --task-id')
    if namespace.task is not None and namespace.task_id is not None:
        raise error_type('loop run-once cannot combine --task and --task-id')
    if namespace.task is not None and namespace.loop_id is None:
        raise error_type('loop run-once --task requires --loop-id')
    return ParsedLoopRunOnceCommand(
        project=project,
        loop_id=str(namespace.loop_id) if namespace.loop_id is not None else None,
        task=str(namespace.task) if namespace.task is not None else None,
        task_id=str(namespace.task_id) if namespace.task_id is not None else None,
        worker_profile=str(namespace.worker_profile),
        reviewer_profile=str(namespace.reviewer_profile),
        orchestrator=str(namespace.orchestrator),
        round_checker=str(namespace.round_checker),
        timeout_s=float(namespace.timeout) if namespace.timeout is not None else None,
        json_output=bool(namespace.json_output),
    )


def _parse_loop_runner(tokens: list[str], *, project: str | None, error_type) -> ParsedLoopRunnerCommand:
    parser = argparse.ArgumentParser(prog='ccb loop runner', add_help=False)
    parser.add_argument('--once', action='store_true')
    parser.add_argument('--timeout', type=float, default=None)
    parser.add_argument('--consume-role-output', action='store_true')
    parser.add_argument('--json', dest='json_output', action='store_true')
    namespace = parse_args(parser, tokens, error_message='invalid loop runner command', error_type=error_type)
    if not bool(namespace.once):
        raise error_type('loop runner currently requires --once')
    if namespace.timeout is not None and float(namespace.timeout) <= 0:
        raise error_type('loop runner --timeout must be positive')
    return ParsedLoopRunnerCommand(
        project=project,
        once=True,
        timeout_s=float(namespace.timeout) if namespace.timeout is not None else None,
        consume_role_output=bool(namespace.consume_role_output),
        json_output=bool(namespace.json_output),
    )


def _parse_agent_spec_token(
    token: str,
    *,
    explicit_provider: str | None,
    error_type,
) -> tuple[str, str | None]:
    text = str(token or '').strip()
    if not text:
        raise error_type('agent add requires <name[:provider]>')
    if ':' not in text:
        return text, explicit_provider
    name, provider = text.split(':', 1)
    name = name.strip()
    provider = provider.strip()
    if not name:
        raise error_type('agent add name cannot be empty')
    if not provider:
        raise error_type('agent add provider cannot be empty')
    if explicit_provider is not None and str(explicit_provider).strip().lower() != provider.lower():
        raise error_type('agent add provider conflicts with --provider')
    return name, provider


def _parse_loop_profile_counts(raw_profiles: tuple[str, ...], *, error_type) -> tuple[tuple[str, int], ...]:
    result: list[tuple[str, int]] = []
    for raw in raw_profiles:
        text = str(raw or '').strip()
        if not text:
            raise error_type('loop capacity --profile cannot be empty')
        if '=' in text:
            name, count_text = text.split('=', 1)
        else:
            name, count_text = text, '1'
        name = name.strip()
        count_text = count_text.strip()
        if not name:
            raise error_type('loop capacity --profile requires a profile name')
        try:
            count = int(count_text)
        except ValueError as exc:
            raise error_type(f'loop capacity --profile {name} count must be an integer') from exc
        if count <= 0:
            raise error_type(f'loop capacity --profile {name} count must be positive')
        result.append((name, count))
    return tuple(result)


def parse_kill(tokens: list[str], *, project: str | None, error_type) -> ParsedKillCommand:
    parser = argparse.ArgumentParser(prog='ccb kill', add_help=False)
    parser.add_argument('-f', '--force', action='store_true')
    namespace = parse_args(parser, tokens, error_message='invalid kill command', error_type=error_type)
    return ParsedKillCommand(project=project, force=bool(namespace.force))


def parse_cleanup(tokens: list[str], *, project: str | None, error_type) -> ParsedCleanupCommand:
    require_no_extra(tokens, command='cleanup', error_type=error_type)
    return ParsedCleanupCommand(project=project)


def parse_ps(tokens: list[str], *, project: str | None, error_type) -> ParsedPsCommand:
    require_no_extra(tokens, command='ps', error_type=error_type)
    return ParsedPsCommand(project=project)


def parse_ping(tokens: list[str], *, project: str | None, error_type) -> ParsedPingCommand:
    if len(tokens) != 1:
        raise error_type('ping requires <agent_name|all>')
    return ParsedPingCommand(project=project, target=tokens[0])


def parse_watch(tokens: list[str], *, project: str | None, error_type) -> ParsedWatchCommand:
    if len(tokens) != 1:
        raise error_type('watch requires <agent_name|job_id>')
    return ParsedWatchCommand(project=project, target=tokens[0])


def parse_pend(tokens: list[str], *, project: str | None, error_type) -> ParsedPendCommand:
    parser = argparse.ArgumentParser(prog='ccb pend', add_help=False)
    parser.add_argument('--watch', action='store_true')
    parser.add_argument('--inbox', action='store_true')
    parser.add_argument('--queue', action='store_true')
    parser.add_argument('--detail', action='store_true')
    parser.add_argument('target')
    parser.add_argument('count', nargs='?')
    namespace = parse_args(parser, tokens, error_message='invalid pend command', error_type=error_type)
    selected_modes = [name for name in ('watch', 'inbox', 'queue') if bool(getattr(namespace, name))]
    if len(selected_modes) > 1:
        raise error_type('pend supports at most one observer mode: --watch, --inbox, or --queue')
    observer_mode = 'snapshot'
    if namespace.watch:
        observer_mode = 'watch'
    elif namespace.inbox:
        observer_mode = 'inbox'
    elif namespace.queue:
        observer_mode = 'queue'
    if namespace.detail and observer_mode not in {'inbox', 'queue'}:
        raise error_type('pend --detail requires --inbox or --queue')
    count: int | None = None
    if namespace.count is not None:
        try:
            count = int(namespace.count)
        except ValueError as exc:
            raise error_type('pend count must be an integer') from exc
        if count <= 0:
            raise error_type('pend count must be positive')
    if count is not None and observer_mode != 'snapshot':
        raise error_type('pend count is only supported for snapshot mode')
    return ParsedPendCommand(
        project=project,
        target=str(namespace.target),
        count=count,
        observer_mode=observer_mode,
        detail=bool(namespace.detail),
    )


def parse_queue(tokens: list[str], *, project: str | None, error_type) -> ParsedQueueCommand:
    parser = argparse.ArgumentParser(prog='ccb queue', add_help=False)
    parser.add_argument('--detail', action='store_true')
    parser.add_argument('target')
    namespace = parse_args(parser, tokens, error_message='invalid queue command', error_type=error_type)
    return ParsedQueueCommand(project=project, target=str(namespace.target), detail=bool(namespace.detail))


def parse_trace(tokens: list[str], *, project: str | None, error_type) -> ParsedTraceCommand:
    if len(tokens) != 1:
        raise error_type('trace requires <submission_id|message_id|attempt_id|reply_id|job_id>')
    return ParsedTraceCommand(project=project, target=tokens[0])


def parse_why(tokens: list[str], *, project: str | None, error_type) -> ParsedWhyCommand:
    if len(tokens) != 1:
        raise error_type('why requires <job_id>')
    return ParsedWhyCommand(project=project, job_id=tokens[0])


def parse_resubmit(tokens: list[str], *, project: str | None, error_type) -> ParsedResubmitCommand:
    if len(tokens) != 1:
        raise error_type('resubmit requires <message_id>')
    return ParsedResubmitCommand(project=project, message_id=tokens[0])


def parse_repair(tokens: list[str], *, project: str | None, error_type):
    if not tokens:
        raise error_type('repair requires one of: ack, retry, resubmit')
    mode = tokens[0]
    rest = tokens[1:]
    if mode == 'ack':
        return parse_ack(rest, project=project, error_type=error_type)
    if mode == 'retry':
        return parse_retry(rest, project=project, error_type=error_type)
    if mode == 'resubmit':
        return parse_resubmit(rest, project=project, error_type=error_type)
    raise error_type('repair only supports: ack, retry, resubmit')


def parse_retry(tokens: list[str], *, project: str | None, error_type) -> ParsedRetryCommand:
    if len(tokens) != 1:
        raise error_type('retry requires <job_id|attempt_id>')
    return ParsedRetryCommand(project=project, target=tokens[0])


def parse_wait(command_name: str, tokens: list[str], *, project: str | None, error_type) -> ParsedWaitCommand:
    parser = argparse.ArgumentParser(prog=f'ccb {command_name}', add_help=False)
    parser.add_argument('--timeout', type=float, default=None)
    if command_name == 'wait-quorum':
        parser.add_argument('quorum', type=int)
        parser.add_argument('target')
    else:
        parser.add_argument('target')
    namespace = parse_args(parser, tokens, error_message=f'invalid {command_name} command', error_type=error_type)
    timeout_s = float(namespace.timeout) if namespace.timeout is not None else None
    if timeout_s is not None and timeout_s <= 0:
        raise error_type('wait timeout must be positive')
    quorum = int(namespace.quorum) if getattr(namespace, 'quorum', None) is not None else None
    if quorum is not None and quorum <= 0:
        raise error_type('wait quorum must be positive')
    return ParsedWaitCommand(
        project=project,
        mode=WAIT_COMMAND_TO_MODE[command_name],
        target=str(namespace.target),
        quorum=quorum,
        timeout_s=timeout_s,
    )


def parse_inbox(tokens: list[str], *, project: str | None, error_type) -> ParsedInboxCommand:
    parser = argparse.ArgumentParser(prog='ccb inbox', add_help=False)
    parser.add_argument('--detail', action='store_true')
    parser.add_argument('agent_name')
    namespace = parse_args(parser, tokens, error_message='invalid inbox command', error_type=error_type)
    return ParsedInboxCommand(project=project, agent_name=str(namespace.agent_name), detail=bool(namespace.detail))


def parse_ack(tokens: list[str], *, project: str | None, error_type) -> ParsedAckCommand:
    if not tokens or len(tokens) > 2:
        raise error_type('ack requires <agent_name> [inbound_event_id]')
    inbound_event_id = tokens[1] if len(tokens) == 2 else None
    return ParsedAckCommand(project=project, agent_name=tokens[0], inbound_event_id=inbound_event_id)


def parse_logs(tokens: list[str], *, project: str | None, error_type) -> ParsedLogsCommand:
    if len(tokens) != 1:
        raise error_type('logs requires <agent_name>')
    return ParsedLogsCommand(project=project, agent_name=tokens[0])


def parse_doctor(tokens: list[str], *, project: str | None, error_type) -> ParsedDoctorCommand:
    if tokens[:1] in (['ps'], ['--runtime']):
        return parse_ps(tokens[1:], project=project, error_type=error_type)
    if tokens[:1] in (['logs'], ['--logs']):
        return parse_logs(tokens[1:], project=project, error_type=error_type)
    if tokens[:1] == ['storage']:
        parser = argparse.ArgumentParser(prog='ccb doctor storage', add_help=False)
        parser.add_argument('--json', dest='json_output', action='store_true')
        namespace = parse_args(parser, tokens[1:], error_message='invalid doctor storage command', error_type=error_type)
        return ParsedDoctorCommand(project=project, storage=True, json_output=bool(namespace.json_output))
    parser = argparse.ArgumentParser(prog='ccb doctor', add_help=False)
    parser.add_argument('--output', dest='output_path', nargs='?', const='', default=None)
    parser.add_argument('--identity', dest='identity', action='store_true')
    parser.add_argument('--deep', dest='deep', action='store_true')
    parser.add_argument('--json', dest='json_output', action='store_true')
    try:
        namespace = parse_args(parser, tokens, error_message='invalid doctor command', error_type=error_type)
    except Exception as exc:
        if '--bundle' in tokens:
            raise error_type('`doctor --bundle` is no longer supported; use `doctor --output`') from exc
        raise
    bundle = namespace.output_path is not None
    output_path = str(namespace.output_path) if namespace.output_path else None
    return ParsedDoctorCommand(
        project=project,
        bundle=bundle,
        output_path=output_path,
        identity=bool(namespace.identity),
        deep=bool(namespace.deep),
        json_output=bool(namespace.json_output),
    )


def parse_identity(tokens: list[str], *, project: str | None, error_type) -> ParsedIdentityCommand:
    parser = argparse.ArgumentParser(prog='ccb identity', add_help=False)
    parser.add_argument('--json', dest='json_output', action='store_true')
    namespace = parse_args(parser, tokens, error_message='invalid identity command', error_type=error_type)
    return ParsedIdentityCommand(project=project, json_output=bool(namespace.json_output))


def parse_probe(tokens: list[str], *, project: str | None, error_type) -> ParsedProbeCommand:
    parser = argparse.ArgumentParser(prog='ccb probe', add_help=False)
    parser.add_argument('--json', dest='json_output', action='store_true')
    namespace = parse_args(parser, tokens, error_message='invalid probe command', error_type=error_type)
    return ParsedProbeCommand(project=project, json_output=bool(namespace.json_output))


def parse_config(tokens: list[str], *, project: str | None, error_type) -> ParsedConfigValidateCommand:
    if tokens != ['validate']:
        raise error_type('config only supports: ccb config validate')
    return ParsedConfigValidateCommand(project=project)


def parse_reload(tokens: list[str], *, project: str | None, error_type) -> ParsedReloadCommand:
    parser = argparse.ArgumentParser(prog='ccb reload', add_help=False)
    parser.add_argument('--dry-run', dest='dry_run', action='store_true')
    namespace = parse_args(parser, tokens, error_message='invalid reload command', error_type=error_type)
    return ParsedReloadCommand(project=project, dry_run=bool(namespace.dry_run))


def _parse_csv_values(text: str) -> tuple[str, ...]:
    values = tuple(item.strip() for item in str(text or '').split(',') if item.strip())
    return values


__all__ = [
    'parse_ack',
    'parse_agent',
    'parse_cancel',
    'parse_clear',
    'parse_cleanup',
    'parse_config',
    'parse_doctor',
    'parse_inbox',
    'parse_kill',
    'parse_logs',
    'parse_loop',
    'parse_maintenance',
    'parse_mobile',
    'parse_pend',
    'parse_ping',
    'parse_ps',
    'parse_queue',
    'parse_repair',
    'parse_reload',
    'parse_restart',
    'parse_resubmit',
    'parse_retry',
    'parse_trace',
    'parse_wait',
    'parse_watch',
]
