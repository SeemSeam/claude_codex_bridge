from __future__ import annotations

import json
import os
import shlex
import signal
import subprocess
import tempfile
import time

from .objects import SyncError

WIRE_LIMIT = 48 * 1024 * 1024


def ssh_argv(profile, *, tty=False):
    argv = [
        '/usr/bin/ssh',
        '-tt' if tty else '-T',
        '-o',
        'BatchMode=yes',
        '-o',
        'StrictHostKeyChecking=yes',
        '-o',
        'ForwardAgent=no',
        '-o',
        'ClearAllForwardings=yes',
        '-o',
        'SendEnv=-*',
        '-o',
        'ControlMaster=no',
        '-o',
        'ControlPath=none',
        '-o',
        'PermitLocalCommand=no',
        '-o',
        'ConnectTimeout=10',
        '-o',
        'ServerAliveInterval=5',
        '-o',
        'ServerAliveCountMax=3',
    ]
    if profile.get('ssh_config'):
        argv.extend(['-F', profile['ssh_config']])
    return argv


class NetworkError(SyncError):
    """Ambiguous delivery; only replay the same idempotent RPC, never a prompt."""


class SshTransport:
    def __init__(self, profile):
        self.profile = profile

    def call(self, request):
        deadline = time.monotonic() + self.profile.get('timeout_seconds', 60)
        for attempt in range(self.profile.get('retry_attempts', 3)):
            try:
                return self._call(request, deadline)
            except NetworkError:
                if (
                    attempt + 1 >= self.profile.get('retry_attempts', 3)
                    or time.monotonic() + 1 >= deadline
                ):
                    raise
                time.sleep(min(2**attempt, 4))

    def _call(self, request, deadline):
        p = self.profile
        command = shlex.join(
            [p.get('python', '/usr/bin/python3'), p['remote_root'] + '/endpoint.py']
        )
        argv = ssh_argv(p)
        argv.extend([p['host'], command])
        payload = json.dumps(request, separators=(',', ':')).encode()
        if len(payload) > WIRE_LIMIT:
            raise SyncError('request exceeds wire limit')
        with tempfile.TemporaryFile() as source, tempfile.TemporaryFile() as out, tempfile.TemporaryFile() as err:
            source.write(payload)
            source.seek(0)
            process = subprocess.Popen(
                argv, stdin=source, stdout=out, stderr=err, start_new_session=True
            )
            try:
                while process.poll() is None:
                    if time.monotonic() > deadline:
                        raise NetworkError('SSH synchronization timed out; transaction retained')
                    if (
                        os.fstat(out.fileno()).st_size > WIRE_LIMIT
                        or os.fstat(err.fileno()).st_size > 128 * 1024
                    ):
                        raise SyncError('SSH response exceeds limit')
                    time.sleep(0.02)
                out.seek(0)
                result = out.read(WIRE_LIMIT + 1)
                if len(result) > WIRE_LIMIT:
                    raise SyncError('SSH response exceeds limit')
                if process.returncode:
                    if process.returncode == 255:
                        raise NetworkError('SSH connection failed; transaction retained')
                    raise SyncError(f'SSH synchronization exited {process.returncode}')
            finally:
                if process.poll() is None:
                    os.killpg(process.pid, signal.SIGKILL)
                process.wait()
        try:
            response = json.loads(result)
        except (ValueError, UnicodeError) as exc:
            raise SyncError('invalid synchronization response') from exc
        if not isinstance(response, dict) or response.get('ok') is not True:
            raise SyncError(
                'remote synchronization rejected: '
                + str(response.get('error', 'invalid response'))[:400]
                if isinstance(response, dict)
                else 'invalid response'
            )
        return response
