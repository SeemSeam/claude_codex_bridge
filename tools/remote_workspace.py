#!/usr/bin/env python3
"""Inspect/recover a pinned workspace without rerunning a provider task."""

import argparse
import json
from pathlib import Path
import sys
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'lib'))
from agents.store import AgentSpecStore
from jobs.store import JobStore
from remote_workspace.files import read_json
from remote_workspace.service import WorkspaceSynchronizer
from storage.paths import PathLayout


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=('status', 'recover', 'prune'))
    parser.add_argument('--project', required=True, type=Path)
    parser.add_argument('--agent', required=True)
    parser.add_argument('--job')
    parser.add_argument('--keep', type=int, default=20)
    parser.add_argument('--days', type=int, default=14)
    parser.add_argument(
        '--apply', action='store_true', help='apply a reviewed retention plan; default is dry-run'
    )
    parser.add_argument(
        '--provider-stopped',
        action='store_true',
        help='assert the remote provider is idle/stopped; required for manual recovery',
    )
    args = parser.parse_args()
    layout = PathLayout(args.project)
    spec = AgentSpecStore(layout).load(args.agent)
    if spec is None or not spec.remote_workspace:
        parser.error('agent has no stored remote workspace profile')
    service = WorkspaceSynchronizer(layout, SimpleNamespace(agents={args.agent: spec}))
    # Validate the configured identity even for read-only inspection.
    service.profile(spec.remote_workspace, args.agent)
    path = service.state_root(spec.remote_workspace) / 'state.json'
    state = read_json(path) if path.exists() else {'phase': 'not_started'}
    if args.action == 'status':
        output = {k: v for k, v in state.items() if k != 'profile'}
        transport_path = service.state_root(spec.remote_workspace) / 'transport.json'
        if transport_path.exists():
            output['transport'] = read_json(transport_path)
    elif args.action == 'prune':
        from remote_workspace.maintenance import prune_local

        output = prune_local(
            service,
            spec.remote_workspace,
            args.agent,
            keep=args.keep,
            days=args.days,
            apply=args.apply,
        )
    else:
        if not args.job or not args.provider_stopped:
            parser.error('recover requires --job and --provider-stopped; it never reruns Claude')
        job = JobStore(layout).get_latest(args.agent, args.job)
        if job is None or job.status.value not in (
            'failed',
            'incomplete',
            'cancelled',
            'completed',
        ):
            parser.error('refusing recovery while the CCB job is nonterminal or unknown')
        output = service.recover(spec.remote_workspace, args.agent, args.job)
    print(json.dumps(output, indent=2, ensure_ascii=False))


if __name__ == '__main__':
    main()
