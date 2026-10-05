"""Explicit launch baseline for the trusted controller and SSH endpoint.

This detects deployment/configuration drift; it is not attestation against a
compromised account. Never include credentials or remote environment values.
"""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import shlex
import shutil
import stat
import subprocess
import sys

from .files import directory, read_json, write_json
from .objects import SyncError
from .transport import ssh_argv


def launch_environment(environ, home, state, project):
    env = dict(environ)
    paths = [str(Path(home) / '.local/bin'), '/usr/local/bin', '/usr/bin', '/bin']
    for item in environ.get('PATH', '').split(':'):
        if item and Path(item).is_absolute() and not Path(item).resolve().is_relative_to(project):
            paths.append(item)
    env['PATH'] = ':'.join(dict.fromkeys(paths))
    runtime = Path('/run/user') / str(os.getuid())
    if not runtime.exists():
        runtime = Path(state) / 'run'
        runtime.mkdir(parents=True, exist_ok=True, mode=0o700)
    with directory(runtime):
        st = runtime.stat()
        if st.st_uid != os.getuid() or stat.S_IMODE(st.st_mode) & 0o077:
            raise SyncError('runtime directory must be owned by the controller with mode 0700')
    # A non-login terminal and an external CLI must address the same daemon.
    env['XDG_RUNTIME_DIR'] = str(runtime)
    return env


def tool_identity(path, env):
    path = Path(path).resolve(strict=True)
    result = subprocess.run(
        [str(path), '--version'],
        env=env,
        capture_output=True,
        stdin=subprocess.DEVNULL,
        text=True,
        timeout=15,
    )
    if result.returncode:
        # ssh and tmux use -V and may print their version to stderr.
        result = subprocess.run(
            [str(path), '-V'],
            env=env,
            capture_output=True,
            stdin=subprocess.DEVNULL,
            text=True,
            timeout=15,
        )
    if result.returncode:
        raise SyncError(f'cannot inspect required executable: {path}')
    return {'path': str(path), 'version': (result.stdout + result.stderr).strip()[:500]}


def source_identity(source):
    paths = subprocess.check_output(
        ['/usr/bin/git', '-C', str(source), 'ls-files', '-z', '--', 'ccb.py', 'lib', 'tools', 'bin']
    ).split(b'\0')
    hashes = {}
    for raw in paths:
        if raw:
            name = raw.decode()
            hashes[name] = hashlib.sha256((source / name).read_bytes()).hexdigest()
    # New modules may not have been committed yet during an explicit enrollment.
    for root in ('lib/remote_workspace', 'tools'):
        for path in (source / root).glob('*.py'):
            hashes[str(path.relative_to(source))] = hashlib.sha256(path.read_bytes()).hexdigest()
    return {
        'path': str(source),
        'sha256': hashlib.sha256(json.dumps(hashes, sort_keys=True).encode()).hexdigest(),
    }


REMOTE_PROBE = r'''
import json, os, pathlib, subprocess, sys
root=pathlib.Path(sys.argv[1])
sys.path.insert(0,str(root))
from remote_workspace.installer import install
from remote_workspace.files import read_json
verified=install({'root':str(root),'action':'verify'})
cfg=read_json(root/'endpoint.json')
manifest=read_json(root/'deployment.json')
env={**os.environ,**cfg.get('terminal',{}).get('env',{}),**cfg.get('git_env',{})}
tools={}
for name,path in [('python',sys.executable),('git',cfg.get('git','/usr/bin/git')),
                  ('tmux',cfg['terminal']['tmux']),('claude',cfg['terminal']['claude'])]:
    p=pathlib.Path(path).resolve(strict=True)
    result=subprocess.run([str(p),'-V' if name=='tmux' else '--version'],env=env,
                          capture_output=True,text=True,timeout=15)
    if result.returncode: raise RuntimeError('cannot inspect required remote tool: '+name)
    tools[name]={'path':str(p),'version':(result.stdout+result.stderr).strip()[:500]}
print(json.dumps({'version':verified['version'],'protocol':manifest['protocol'],
    'hashes':manifest['hashes'],'workspace_id':cfg['workspace_id'],
    'session_id':cfg['terminal']['session_id'],'tools':tools}))
'''


def remote_identity(profile, source):
    command = shlex.join(
        [
            profile.get('python', '/usr/bin/python3'),
            '-I',
            '-c',
            REMOTE_PROBE,
            profile['remote_root'],
        ]
    )
    result = subprocess.run(
        [*ssh_argv(profile), profile['host'], command],
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=True,
        timeout=75,
    )
    if result.returncode:
        raise SyncError('remote preflight failed: ' + result.stderr[-1200:])
    remote = json.loads(result.stdout)
    if (
        remote['workspace_id'] != profile['workspace_id']
        or remote['session_id'] != profile['terminal']['session_id']
    ):
        raise SyncError('remote preflight workspace/session binding mismatch')
    for name, digest in remote.pop('hashes').items():
        if name.startswith('remote_workspace/'):
            local = source / 'lib' / name
            if not local.is_file() or hashlib.sha256(local.read_bytes()).hexdigest() != digest:
                raise SyncError('remote source differs from controller: ' + name)
    return remote


def inspect(project, profiles, source, env):
    from agents.config_loader import load_project_config
    from provider_command_defaults import SUPPORTED_PROVIDER_NAMES, provider_executable
    from .provider_client import load_profile

    project, profiles, source = map(Path, (project, profiles, source))
    configured = read_json(profiles)
    selected = {name: p for name, p in configured.items() if p['project_root'] == str(project)}
    if not selected:
        raise SyncError('no remote profile for this project')
    identities = {}
    ssh_configs = {}
    for name in selected:
        _, profile = load_profile(profiles, name)
        identities[name] = remote_identity(profile, source)
        if profile.get('ssh_config'):
            ssh_configs[name] = hashlib.sha256(Path(profile['ssh_config']).read_bytes()).hexdigest()
    tools = {'python': tool_identity(sys.executable, env)}
    # Match the pinned executables used by Git and ssh_argv, not PATH aliases.
    required = {'git': '/usr/bin/git', 'ssh': '/usr/bin/ssh', 'tmux': 'tmux'}
    config = load_project_config(project, include_loop_overlays=False).config
    # Remote native CLIs are checked by the endpoint probe. Only configured
    # local providers need a controller-side executable.
    for spec in config.agents.values():
        if not spec.remote_workspace:
            if spec.provider not in SUPPORTED_PROVIDER_NAMES:
                raise SyncError(
                    'preflight cannot identify native CLI for provider: ' + spec.provider
                )
            required[spec.provider] = provider_executable(spec.provider)
    for name, executable in sorted(required.items()):
        path = shutil.which(executable, path=env['PATH'])
        if not path:
            raise SyncError(
                f'required executable not found: {name}; fix launcher PATH before starting CCB'
            )
        tools[name] = tool_identity(path, env)
    return {
        'schema': 1,
        'project': str(project),
        'source': source_identity(source),
        'runtime_directory': env['XDG_RUNTIME_DIR'],
        'tools': tools,
        'ssh_configs_sha256': ssh_configs,
        'profiles_sha256': hashlib.sha256(profiles.read_bytes()).hexdigest(),
        'config_sha256': hashlib.sha256((project / '.ccb/ccb.config').read_bytes()).hexdigest(),
        'remote': identities,
    }


def baseline_path(profiles):
    return Path(profiles).with_suffix('.baseline.json')


def check(project, profiles, source, env, *, record=False):
    path = baseline_path(profiles)
    if path.is_relative_to(project):
        raise SyncError('launch baseline must be outside the project')
    if record and path.exists():
        raise SyncError(
            'baseline exists; archive it explicitly before accepting a changed deployment'
        )
    if not record:
        if not path.exists():
            raise SyncError(
                'launch baseline missing; inspect the deployment and run preflight --record'
            )
        with directory(path.parent) as fd:
            st = os.stat(path.name, dir_fd=fd, follow_symlinks=False)
            if not stat.S_ISREG(st.st_mode) or st.st_uid != os.getuid() or st.st_mode & 0o022:
                raise SyncError(
                    'launch baseline must be controller-owned and not group/world writable'
                )
        expected = read_json(path, limit=128 * 1024)
        running_baseline = env.get('CCB_REMOTE_PREFLIGHT_BASELINE_HASH')
        if running_baseline and hashlib.sha256(path.read_bytes()).hexdigest() != running_baseline:
            raise SyncError(
                'running controller has an older launch baseline; restart the idle controller explicitly'
            )
    actual = inspect(project, profiles, source, env)
    if record:
        write_json(path, actual)
    else:
        changed = [
            key
            for key in sorted(set(actual) | set(expected))
            if actual.get(key) != expected.get(key)
        ]
        if changed:
            raise SyncError(
                'launch baseline drift: '
                + ', '.join(changed)
                + '; inspect and explicitly enroll the new deployment before dispatch'
            )
    return {'ok': True, 'baseline': str(path), 'recorded': record, 'identity': actual}
