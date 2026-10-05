"""Deploy versioned endpoint code, without copying repository configuration."""

from __future__ import annotations
import base64
import hashlib
import json
from pathlib import Path
import shlex
import subprocess

from .objects import Git, SyncError
from .transport import SshTransport, ssh_argv

PROTOCOL_VERSION = 1


def deployment_payload(profile, terminal, *, git='/usr/bin/git', git_env=None):
    package = Path(__file__).parent
    names = (
        '__init__.py',
        'objects.py',
        'files.py',
        'transport.py',
        'endpoint.py',
        'provider_remote.py',
        'maintenance.py',
        'installer.py',
    )
    files = {'remote_workspace/' + name: (package / name).read_bytes() for name in names}
    for filename, module in [('endpoint.py', 'endpoint'), ('provider.py', 'provider_remote')]:
        files[filename] = (
            'from pathlib import Path\n'
            'root=Path(__file__).resolve().parent\n'
            "if (root/'deployment-pending.json').exists(): raise SystemExit('deployment incomplete')\n"
            f'from remote_workspace.{module} import main\nmain(root)\n'
        ).encode()
    config = {
        'workspace_id': profile['workspace_id'],
        'include': profile['include'],
        'git': git,
        'git_env': git_env or {},
        'terminal': terminal,
    }
    files['endpoint.json'] = json.dumps(config, sort_keys=True, indent=2).encode()
    hashes = {name: hashlib.sha256(data).hexdigest() for name, data in files.items()}
    version = hashlib.sha256(json.dumps(hashes, sort_keys=True).encode()).hexdigest()
    files['deployment.json'] = json.dumps(
        {'protocol': PROTOCOL_VERSION, 'version': version, 'hashes': hashes}, indent=2
    ).encode()
    return {
        'root': profile['remote_root'],
        'version': version,
        'files': {name: base64.b64encode(data).decode() for name, data in files.items()},
    }


INSTALL_SCRIPT = Path(__file__).with_name('installer.py').read_text()


def deploy(profile, terminal, *, git='/usr/bin/git', git_env=None, update=False):
    payload = deployment_payload(profile, terminal, git=git, git_env=git_env)
    payload['update'] = update
    return _install(profile, payload)


def manage_deployment(profile, action, version=None):
    return _install(profile, {'root': profile['remote_root'], 'action': action, 'version': version})


def _install(profile, payload):
    command = shlex.join([profile.get('python', '/usr/bin/python3'), '-c', INSTALL_SCRIPT])
    result = subprocess.run(
        [*ssh_argv(profile), profile['host'], command],
        input=json.dumps(payload),
        text=True,
        capture_output=True,
        timeout=90,
    )
    if result.returncode:
        raise SyncError('endpoint deployment rejected: ' + result.stderr[-1500:])
    return json.loads(result.stdout)


def bootstrap(profile):
    git = Git(profile['local_workspace'])
    from .files import scan

    base = git.snapshot(
        scan(profile['local_workspace'], profile['include']),
        parent=git.head(),
        message='CCB remote workspace bootstrap',
    )
    objects = git.export(base)
    objects.validate_bootstrap(base, profile['include'])
    transport = SshTransport(profile)
    job = 'bootstrap_' + profile['workspace_id']
    transport.call(
        {
            'version': 1,
            'workspace_id': profile['workspace_id'],
            'job_id': job,
            'op': 'prepare',
            'base': base,
            'objects': objects.wire(),
            'artifact': None,
        }
    )
    result = transport.call(
        {'version': 1, 'workspace_id': profile['workspace_id'], 'job_id': job, 'op': 'collect'}
    )
    if result['base'] != base or result['head'] != base:
        raise SyncError('bootstrap produced unexpected changes')
    transport.call(
        {
            'version': 1,
            'workspace_id': profile['workspace_id'],
            'job_id': job,
            'op': 'ack',
            'head': base,
        }
    )
    return {'ok': True, 'base': base, 'workspace_id': profile['workspace_id']}
