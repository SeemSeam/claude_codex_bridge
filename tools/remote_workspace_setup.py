#!/usr/bin/env python3
"""Create a trusted profile, then deploy/bootstrap an existing controller worktree."""

import argparse
import fcntl
import json
import os
from pathlib import Path
import shlex
import sys
import uuid

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'lib'))
from remote_workspace.deploy import bootstrap, deploy, manage_deployment
from remote_workspace.files import read_json, write_json
from remote_workspace.provider_client import load_profile


def register_profile(source, name, profile):
    """Validate a temporary candidate, then atomically publish under a lock."""
    from remote_workspace.files import directory

    source.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    with directory(source.parent) as parent:
        lock = os.open(
            source.name + '.lock', os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600, dir_fd=parent
        )
        candidate = source.with_name('.profiles-' + uuid.uuid4().hex + '.json')
        previous = os.environ.get('CCB_REMOTE_WORKSPACES_FILE')
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            profiles = read_json(source) if source.exists() else {}
            if name in profiles:
                raise ValueError('profile exists; refusing to replace its identity')
            for existing in profiles.values():
                if (
                    existing.get('local_workspace') == profile['local_workspace']
                    or (existing.get('host'), existing.get('remote_root'))
                    == (profile['host'], profile['remote_root'])
                    or existing.get('workspace_id') == profile['workspace_id']
                ):
                    raise ValueError('another profile already owns this endpoint or worktree')
            profiles[name] = profile
            write_json(candidate, profiles)
            service, validated = load_profile(candidate, name)
            service._git(validated)
            write_json(source, profiles)
            return validated
        finally:
            candidate.unlink(missing_ok=True)
            if previous is None:
                os.environ.pop('CCB_REMOTE_WORKSPACES_FILE', None)
            else:
                os.environ['CCB_REMOTE_WORKSPACES_FILE'] = previous
            os.close(lock)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        'action', choices=('configure', 'deploy', 'bootstrap', 'verify', 'rollback')
    )
    parser.add_argument('--profiles', required=True, type=Path)
    parser.add_argument('--profile', required=True)
    parser.add_argument('--project', type=Path)
    parser.add_argument('--workspace', type=Path)
    parser.add_argument('--agent')
    parser.add_argument('--host')
    parser.add_argument('--remote-root')
    parser.add_argument('--ssh-config', type=Path)
    parser.add_argument('--include', action='append')
    parser.add_argument('--remote-claude')
    parser.add_argument('--remote-tmux', default='/usr/bin/tmux')
    parser.add_argument('--remote-git', default='/usr/bin/git')
    parser.add_argument(
        '--remote-env', type=Path, help='trusted JSON environment; never a project-supplied file'
    )
    parser.add_argument(
        '--claude-args', type=Path, help='trusted JSON argv; remote credentials remain remote'
    )
    parser.add_argument('--update', action='store_true')
    parser.add_argument('--version', help='exact deployment hash for an explicit code rollback')
    args = parser.parse_args()
    args.profiles = args.profiles.absolute()
    if args.action == 'configure':
        if not all(
            (args.project, args.workspace, args.agent, args.host, args.remote_root, args.include)
        ):
            parser.error('configure requires project/workspace/agent/host/remote-root/include')
        if args.profiles.is_relative_to(args.project.resolve()):
            parser.error('profiles must be outside the project')
        p = dict(
            project_root=str(args.project.resolve()),
            local_workspace=str(args.workspace.resolve()),
            agent_name=args.agent,
            host=args.host,
            remote_root=args.remote_root,
            include=args.include,
            workspace_id='ccb-' + uuid.uuid4().hex,
            publish='worktree',
            timeout_seconds=60,
            terminal={
                'session_id': str(uuid.uuid4()),
                'reconnect_seconds': 300,
                'transcript_limit_mb': 128,
            },
        )
        if args.ssh_config:
            p['ssh_config'] = str(args.ssh_config.resolve())
        p = register_profile(args.profiles, args.profile, p)
        command = (
            shlex.join(
                [
                    sys.executable,
                    str(Path(__file__).with_name('remote_provider.py')),
                    '--profiles',
                    str(args.profiles),
                    '--profile',
                    args.profile,
                ]
            )
            + ' {command}'
        )
        print(
            json.dumps(
                {
                    'profile': args.profile,
                    'workspace_id': p['workspace_id'],
                    'provider_command_template': command,
                },
                indent=2,
            )
        )
        return
    service, p = load_profile(args.profiles, args.profile)
    if args.action in ('verify', 'rollback'):
        if args.action == 'rollback' and not args.version:
            parser.error('rollback requires the exact --version from a verified backup')
        print(json.dumps(manage_deployment(p, args.action, args.version), indent=2))
        return
    if args.action == 'bootstrap':
        print(json.dumps(bootstrap(p), indent=2))
        return
    if not args.remote_claude:
        parser.error('deploy requires the known remote Claude executable path')
    env = read_json(args.remote_env) if args.remote_env else {}
    argv = read_json(args.claude_args) if args.claude_args else []
    if (
        not isinstance(env, dict)
        or any(not isinstance(k, str) or not isinstance(v, str) for k, v in env.items())
        or not isinstance(argv, list)
        or any(not isinstance(x, str) for x in argv)
    ):
        parser.error('invalid environment or argument JSON')
    terminal = {
        **p['terminal'],
        'claude': args.remote_claude,
        'tmux': args.remote_tmux,
        'env': env,
        'args': argv,
    }
    print(
        json.dumps(
            deploy(p, terminal, git=args.remote_git, git_env=env, update=args.update), indent=2
        )
    )


if __name__ == '__main__':
    main()
