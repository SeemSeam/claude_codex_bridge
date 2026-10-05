"""Remote tmux lifecycle and resumable, authenticated-by-SSH transcript stream.

Only deployment-owned endpoint.json determines executables or environment.
Reattach never submits a task. An absent session with an unfinished transaction
is a recovery condition, not permission to launch another model turn.
"""

from __future__ import annotations
import base64
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import shlex
import stat
import subprocess
import sys
import time
import uuid

from .files import read_json, write_json
from .objects import SyncError


def project_key(path):
    return re.sub(r'[^A-Za-z0-9]', '-', str(path))


class RemoteProvider:
    def __init__(self, root):
        self.root = Path(root)
        self.config = read_json(self.root / 'endpoint.json', limit=64 * 1024)
        self.options = self.config['terminal']
        self.workspace = self.root / 'workspace'
        self.session = self.options['session_id']
        self.tmux = self.options.get('tmux', '/usr/bin/tmux')
        self.env = {
            k: v
            for k, v in os.environ.items()
            if not k.startswith(('CCB_', 'GIT_'))
            and k not in ('TMUX', 'TMUX_PANE', 'SSH_AUTH_SOCK')
        }
        self.env.update(self.options.get('env', {}))
        self.env.setdefault('TZ', 'UTC')
        self.env['DISABLE_AUTOUPDATER'] = '1'
        config_dir = Path(self.options.get('claude_config_dir', str(Path.home() / '.claude')))
        self.transcript = (
            config_dir / 'projects' / project_key(self.workspace) / (self.session + '.jsonl')
        )
        self.limit = self.options.get('transcript_limit_mb', 128) * 1024 * 1024

    def tmux_call(self, *args, check=False):
        return subprocess.run(
            [self.tmux, '-S', str(self.root / 'tmux.sock'), *args],
            env=self.env,
            capture_output=True,
            text=True,
            timeout=10,
            check=check,
        )

    def status(self):
        result = self.tmux_call(
            'display-message', '-p', '-t', 'worker:0.0', '#{pane_dead}:#{pane_pid}'
        )
        generation_path = self.root / 'provider-generation.json'
        generation = read_json(generation_path) if generation_path.exists() else {}
        return {
            'session_id': self.session,
            'boot_id': Path('/proc/sys/kernel/random/boot_id').read_text().strip(),
            'generation': generation.get('generation'),
            'alive': result.returncode == 0 and result.stdout.startswith('0:'),
            'pane': result.stdout.strip() if result.returncode == 0 else None,
        }

    def attach(self, create):
        fd = os.open(
            self.root / 'provider-start.lock', os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600
        )
        try:
            fcntl.flock(fd, fcntl.LOCK_EX)
            info = self.status()
            if not info['alive']:
                state_path = self.root / 'state.json'
                state = read_json(state_path) if state_path.exists() else {}
                if not create or state.get('phase') != 'acked':
                    raise SyncError(
                        'remote session stopped with unfinished work; explicit recovery required'
                    )
                self.tmux_call('kill-session', '-t', 'worker')  # only a dead, owned session
                command = shlex.join([sys.executable, str(self.root / 'provider.py'), 'claude'])
                self.tmux_call(
                    'new-session',
                    '-d',
                    '-s',
                    'worker',
                    '-c',
                    str(self.workspace),
                    command,
                    check=True,
                )
                self.tmux_call('set-option', '-t', 'worker', 'remain-on-exit', 'on', check=True)
        finally:
            os.close(fd)
        os.execve(
            self.tmux,
            [self.tmux, '-S', str(self.root / 'tmux.sock'), 'attach-session', '-t', 'worker'],
            self.env,
        )

    def claude(self):
        write_json(
            self.root / 'provider-generation.json',
            {'generation': str(uuid.uuid4()), 'pid': os.getpid()},
        )
        os.chdir(self.workspace)
        executable = self.options['claude']
        resume = (
            '--resume'
            if self.transcript.exists() and self.transcript.stat().st_size
            else '--session-id'
        )
        artifacts = self.root / 'artifacts'
        artifacts.mkdir(mode=0o700, exist_ok=True)
        args = [
            executable,
            resume,
            self.session,
            '--add-dir',
            str(artifacts),
            *self.options.get('args', []),
        ]
        os.execve(executable, args, self.env)

    def tail(self, offset, digest):
        if not 0 <= offset <= self.limit or not re.fullmatch('[0-9a-f]{64}', digest):
            raise SyncError('invalid resume cursor')
        try:
            fd = os.open(self.transcript, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        except FileNotFoundError:
            if offset:
                raise SyncError('remote transcript disappeared')
            fd = None
        if fd is not None:
            with os.fdopen(fd, 'rb') as source:
                self.check_file(source, offset)
                h = hashlib.sha256()
                remaining = offset
                while remaining:
                    data = source.read(min(remaining, 128 * 1024))
                    if not data:
                        raise SyncError('remote transcript shortened')
                    h.update(data)
                    remaining -= len(data)
                if h.hexdigest() != digest:
                    raise SyncError('remote transcript prefix changed')
        elif digest != hashlib.sha256(b'').hexdigest():
            raise SyncError('invalid empty prefix')
        self.emit({'kind': 'ready', 'offset': offset, 'sha256': digest, **self.status()})
        last_heartbeat = 0
        while True:
            try:
                fd = os.open(self.transcript, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
                with os.fdopen(fd, 'rb') as source:
                    self.check_file(source, offset)
                    source.seek(offset)
                    data = source.read(128 * 1024)
                    if data:
                        self.emit(
                            {
                                'kind': 'data',
                                'offset': offset,
                                'data': base64.b64encode(data).decode(),
                            }
                        )
                        offset += len(data)
            except FileNotFoundError:
                if offset:
                    raise SyncError('remote transcript disappeared')
            if time.monotonic() - last_heartbeat > 1:
                self.emit({'kind': 'heartbeat', 'offset': offset, **self.status()})
                last_heartbeat = time.monotonic()
            time.sleep(0.1)

    def check_file(self, source, offset):
        st = os.fstat(source.fileno())
        if (
            not stat.S_ISREG(st.st_mode)
            or st.st_nlink != 1
            or not offset <= st.st_size <= self.limit
        ):
            raise SyncError('invalid transcript or transcript size limit reached')

    @staticmethod
    def emit(value):
        print(json.dumps(value, separators=(',', ':')), flush=True)


def main(root):
    provider = RemoteProvider(root)
    mode = sys.argv[1]
    if mode in ('attach', 'reattach'):
        provider.attach(create=mode == 'attach')
    elif mode == 'claude':
        provider.claude()
    elif mode == 'tail':
        provider.tail(int(sys.argv[2]), sys.argv[3])
    elif mode == 'status':
        provider.emit(provider.status())
    else:
        raise SyncError('unsupported provider operation')
