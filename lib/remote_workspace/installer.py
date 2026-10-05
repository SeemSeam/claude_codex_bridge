"""Standalone SSH installer; code updates require an idle, stopped endpoint."""

from __future__ import annotations
import base64
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import subprocess
import sys
import uuid


def read(path):
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(fd, 'rb') as stream:
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or info.st_size > 16 * 1024 * 1024:
            raise ValueError('invalid deployment file')
        return stream.read()


def write(path, data):
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    if path.is_symlink() or path.parent.is_symlink():
        raise ValueError('symlink deployment target')
    temporary = path.with_name('.install-' + uuid.uuid4().hex)
    fd = os.open(temporary, os.O_CREAT | os.O_EXCL | os.O_WRONLY | os.O_NOFOLLOW, 0o600)
    try:
        with os.fdopen(fd, 'wb') as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
        parent = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(parent)
        finally:
            os.close(parent)
    finally:
        temporary.unlink(missing_ok=True)


def check_name(name):
    if name not in (
        'endpoint.py',
        'provider.py',
        'endpoint.json',
        'deployment.json',
    ) and not re.fullmatch(r'remote_workspace/[a-z_]+\.py', name):
        raise ValueError('invalid deployment path')


def validate(files):
    for name in files:
        check_name(name)
    manifest = json.loads(files['deployment.json'])
    hashes = {
        name: hashlib.sha256(data).hexdigest()
        for name, data in files.items()
        if name != 'deployment.json'
    }
    version = hashlib.sha256(json.dumps(hashes, sort_keys=True).encode()).hexdigest()
    if (
        manifest.get('protocol') != 1
        or manifest.get('hashes') != hashes
        or manifest.get('version') != version
    ):
        raise ValueError('deployment checksum mismatch')
    return manifest


def read_release(root):
    manifest_bytes = read(root / 'deployment.json')
    manifest = json.loads(manifest_bytes)
    files = {'deployment.json': manifest_bytes}
    for name in manifest['hashes']:
        check_name(name)
        if (root / name).parent.is_symlink():
            raise ValueError('symlink release directory')
        files[name] = read(root / name)
    validate(files)
    return files


def install(payload):
    root = Path(payload['root'])
    action = payload.get('action', 'deploy')
    if not root.is_absolute() or '..' in root.parts or len(root.parts) < 4:
        raise ValueError('invalid dedicated root')
    if action != 'deploy' and not root.is_dir():
        raise ValueError('deployment does not exist')
    root.mkdir(parents=True, exist_ok=True, mode=0o700)
    if any(p.is_symlink() for p in (root, *root.parents)):
        raise ValueError('symlink deployment root')
    locks = []
    try:
        # Synchronize with legacy endpoint and provider creation as well.
        for name in ('deployment.lock', 'lock', 'provider-start.lock'):
            fd = os.open(root / name, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
            locks.append(fd)
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        pending = root / 'deployment-pending.json'
        if action == 'verify':
            if pending.exists():
                raise ValueError('interrupted deployment; explicit rollback required')
            files = read_release(root)
            manifest = validate(files)
            backups = sorted(
                p.name
                for p in (root / 'releases').glob('*')
                if re.fullmatch('[0-9a-f]{64}', p.name)
            )
            return {
                'ok': True,
                'version': manifest['version'],
                'protocol': manifest['protocol'],
                'backups': backups,
            }
        if action == 'rollback':
            version = payload.get('version', '')
            if not re.fullmatch('[0-9a-f]{64}', version):
                raise ValueError('exact rollback version required')
            release = root / 'releases' / version
            if release.is_symlink() or release.parent.is_symlink():
                raise ValueError('symlink backup')
            files = read_release(release)
        elif action == 'deploy':
            if pending.exists():
                raise ValueError('interrupted deployment; roll back the saved version first')
            files = {
                name: base64.b64decode(data, validate=True)
                for name, data in payload['files'].items()
            }
        else:
            raise ValueError('unknown deployment action')
        manifest = validate(files)
        old_path = root / 'deployment.json'
        old = json.loads(read(old_path)) if old_path.exists() else None
        if old and not pending.exists() and old['version'] == manifest['version']:
            read_release(root)
            return {'ok': True, 'version': old['version'], 'unchanged': True}
        old_files = None
        if (root / 'endpoint.json').exists() or (root / 'state.json').exists():
            if not old or (action != 'rollback' and not payload.get('update')):
                raise ValueError('existing endpoint; explicit update required')
            state = json.loads(read(root / 'state.json'))
            if state.get('phase') != 'acked':
                raise ValueError('unfinished transaction; update refused')
            config = json.loads(read(root / 'endpoint.json'))
            options = config.get('terminal', {})
            result = subprocess.run(
                [
                    options.get('tmux', '/usr/bin/tmux'),
                    '-S',
                    str(root / 'tmux.sock'),
                    'has-session',
                    '-t',
                    'worker',
                ],
                env=dict(os.environ, **options.get('env', {})),
                capture_output=True,
                timeout=10,
            )
            if result.returncode == 0:
                raise ValueError('stop owned provider session before update')
            if json.loads(files['endpoint.json']) != config:
                raise ValueError('code-only updates require unchanged endpoint configuration')
            if not pending.exists():
                old_files = read_release(root)
        if old_files:
            backup = root / 'releases' / old['version']
            if backup.is_symlink() or backup.parent.is_symlink():
                raise ValueError('symlink backup')
            for name, data in old_files.items():
                if name != 'deployment.json':
                    write(backup / name, data)
            write(backup / 'deployment.json', old_files['deployment.json'])
        write(
            pending,
            json.dumps(
                {'previous': old['version'] if old else None, 'target': manifest['version']}
            ).encode(),
        )
        # Guarded launchers first. A partial deployment cannot execute tasks.
        for name in ('endpoint.py', 'provider.py'):
            write(root / name, files[name])
        for name, data in files.items():
            if name not in ('endpoint.py', 'provider.py', 'deployment.json'):
                write(root / name, data)
        write(root / 'deployment.json', files['deployment.json'])
        pending.unlink()
        return {
            'ok': True,
            'version': manifest['version'],
            'previous': old['version'] if old else None,
            'updated': bool(old),
            'rollback': action == 'rollback',
        }
    finally:
        for fd in reversed(locks):
            os.close(fd)


if __name__ == '__main__':
    try:
        print(json.dumps(install(json.load(sys.stdin))))
    except Exception as exc:
        print(str(exc), file=sys.stderr)
        raise SystemExit(1)
