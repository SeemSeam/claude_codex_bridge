"""Keep one native CCB pane alive across bounded SSH interruptions.

The terminal only reattaches. It never replays input, creates a new task or
overwrites a transcript prefix. A remote reboot/process replacement is fatal.
"""

from __future__ import annotations
import argparse
import base64
import hashlib
import json
import os
from pathlib import Path
import selectors
import shlex
import signal
import stat
import subprocess
import sys
import threading
import time
from types import SimpleNamespace

from .files import directory, read_json, write_json
from .objects import SyncError
from .provider_remote import project_key
from .transport import ssh_argv


def transcript_path(profile):
    return (
        Path(profile['project_root'])
        / '.ccb/agents'
        / profile['agent_name']
        / 'provider-state/claude/home/.claude/projects'
        / project_key(profile['local_workspace'])
        / (profile['terminal']['session_id'] + '.jsonl')
    )


def load_profile(source, name):
    source = Path(source)
    with directory(source.parent) as fd:
        st = os.stat(source.name, dir_fd=fd, follow_symlinks=False)
        if not stat.S_ISREG(st.st_mode) or st.st_uid != os.getuid() or st.st_mode & 0o022:
            raise SyncError('untrusted profiles file')
    raw = read_json(source, limit=128 * 1024)[name]
    os.environ['CCB_REMOTE_WORKSPACES_FILE'] = str(source)
    from .service import WorkspaceSynchronizer
    from storage.paths import PathLayout

    service = WorkspaceSynchronizer(
        PathLayout(Path(raw['project_root'])), SimpleNamespace(agents={})
    )
    return service, service.profile(name, raw['agent_name'])


class TranscriptMirror:
    def __init__(self, path, limit):
        self.path, self.limit = Path(path), limit
        self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        with directory(self.path.parent) as parent:
            fd = os.open(
                self.path.name,
                os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW | os.O_NONBLOCK,
                0o600,
                dir_fd=parent,
            )
        self.file = os.fdopen(fd, 'r+b', buffering=0)
        st = os.fstat(fd)
        if not stat.S_ISREG(st.st_mode) or st.st_nlink != 1 or st.st_size > limit:
            self.file.close()
            raise SyncError('invalid local transcript')
        self.hash = hashlib.sha256()
        while data := self.file.read(128 * 1024):
            self.hash.update(data)
        self.offset = st.st_size
        self.boot_id = self.generation = None

    def record(self, value):
        kind = value.get('kind')
        if value.get('offset') != self.offset:
            raise SyncError('transcript cursor mismatch')
        if kind in ('ready', 'heartbeat'):
            if kind == 'ready' and value.get('sha256') != self.hash.hexdigest():
                raise SyncError('transcript prefix mismatch')
            for name in ('boot_id', 'generation'):
                old, new = getattr(self, name), value.get(name)
                if old and new != old:
                    raise SyncError('remote runtime changed; explicit recovery required')
                if new:
                    setattr(self, name, new)
            return
        if kind != 'data':
            raise SyncError('unknown transcript frame')
        data = base64.b64decode(value['data'], validate=True)
        if not data or len(data) > 128 * 1024 or self.offset + len(data) > self.limit:
            raise SyncError('invalid transcript frame size')
        self.file.write(data)
        os.fsync(self.file.fileno())
        self.hash.update(data)
        self.offset += len(data)


def records(stream, stop, idle_timeout=12):
    pending = bytearray()
    last = time.monotonic()
    with selectors.DefaultSelector() as selector:
        selector.register(stream, selectors.EVENT_READ)
        while not stop.is_set():
            if not selector.select(0.5):
                if time.monotonic() - last > idle_timeout:
                    raise ConnectionError('transcript heartbeat timed out')
                continue
            data = os.read(stream.fileno(), 64 * 1024)
            if not data:
                # A torn final frame is not committed locally; resume its offset.
                raise ConnectionError('transcript channel disconnected')
            pending.extend(data)
            while b'\n' in pending:
                line, _, rest = pending.partition(b'\n')
                if len(line) > 256 * 1024:
                    raise SyncError('oversized transcript frame')
                pending[:] = rest
                yield json.loads(line)
                last = time.monotonic()
            if len(pending) > 256 * 1024:
                raise SyncError('oversized transcript frame')


def terminate(process):
    if process is None:
        return
    if process.poll() is None:
        process.terminate()
        try:
            process.wait(timeout=3)
        except subprocess.TimeoutExpired:
            process.kill()
    process.wait()


class ProviderBridge:
    def __init__(self, profile, state_root):
        self.profile, self.root = profile, Path(state_root)
        self.stop = threading.Event()
        self.lock = threading.Lock()
        self.failure = None
        self.errors = None
        self.terminal = self.tail = None
        self.connected = False
        self.limit = profile['terminal'].get('transcript_limit_mb', 128) * 1024 * 1024
        self.budget = profile['terminal'].get('reconnect_seconds', 300)
        self.state = {
            'pid': os.getpid(),
            'session_id': profile['terminal']['session_id'],
            'terminal_reconnects': 0,
            'log_reconnects': 0,
        }
        self.expected_runtime = {}
        if (self.root / 'state.json').exists() and (self.root / 'transport.json').exists():
            transaction = read_json(self.root / 'state.json')
            previous = read_json(self.root / 'transport.json')
            if transaction.get('phase') != 'done' or transaction.get('blocked'):
                if previous.get('session_id') != profile['terminal']['session_id']:
                    raise SyncError('unfinished transaction has a different provider identity')
                self.expected_runtime = {k: previous.get(k) for k in ('boot_id', 'generation')}
                self.state.update(self.expected_runtime)

    def status(self, **updates):
        with self.lock:
            self.trim_errors()
            self.state.update(updates, updated_at=time.time())
            self.state['state'] = (
                'connected'
                if self.connected and self.terminal is not None and self.terminal.poll() is None
                else 'reconnecting'
            )
            if self.stop.is_set():
                self.state['state'] = 'stopped'
            if self.failure:
                self.state.update(state='failed', error=self.failure)
            write_json(self.root / 'transport.json', self.state)

    def trim_errors(self):
        if self.errors is not None and os.fstat(self.errors.fileno()).st_size > 1024 * 1024:
            fd = self.errors.fileno()
            size = os.fstat(fd).st_size
            tail = os.pread(fd, 256 * 1024, max(0, size - 256 * 1024))
            os.ftruncate(fd, 0)
            os.write(fd, b'[older SSH diagnostics retired; task receipts are retained]\n' + tail)

    def command(self, mode, *args, tty=False):
        command = shlex.join(
            [
                self.profile.get('python', '/usr/bin/python3'),
                self.profile['remote_root'] + '/provider.py',
                mode,
                *map(str, args),
            ]
        )
        return [*ssh_argv(self.profile, tty=tty), self.profile['host'], command]

    def copy_logs(self, errors):
        mirror = None
        lost_at = None
        try:
            mirror = TranscriptMirror(transcript_path(self.profile), self.limit)
            for key, value in self.expected_runtime.items():
                setattr(mirror, key, value)
            while not self.stop.is_set():
                self.tail = subprocess.Popen(
                    self.command('tail', mirror.offset, mirror.hash.hexdigest()),
                    stdin=subprocess.DEVNULL,
                    stdout=subprocess.PIPE,
                    stderr=errors,
                )
                self.status(log_pid=self.tail.pid)
                try:
                    for record in records(self.tail.stdout, self.stop):
                        if (
                            record.get('session_id', self.profile['terminal']['session_id'])
                            != self.profile['terminal']['session_id']
                        ):
                            raise SyncError('remote session mismatch')
                        mirror.record(record)
                        if record['kind'] in ('ready', 'heartbeat'):
                            self.connected = bool(record.get('alive') and record.get('generation'))
                            if mirror.generation and not record.get('alive'):
                                raise SyncError('remote provider stopped; recover unfinished work')
                        if self.connected:
                            lost_at = None
                        self.status(
                            offset=mirror.offset,
                            boot_id=mirror.boot_id,
                            generation=mirror.generation,
                        )
                    return
                except (ConnectionError, OSError):
                    self.connected = False
                    lost_at = lost_at or time.monotonic()
                    if time.monotonic() - lost_at >= self.budget:
                        raise SyncError('transcript reconnect deadline exceeded')
                    self.status(log_reconnects=self.state['log_reconnects'] + 1)
                finally:
                    terminate(self.tail)
                self.stop.wait(1)
        except Exception as exc:
            self.failure = str(exc)
            self.stop.set()
        finally:
            if mirror is not None:
                mirror.file.close()
            self.status()

    def run(self):
        self.root.mkdir(parents=True, exist_ok=True, mode=0o700)
        import fcntl

        fd = os.open(
            self.root / 'provider-client.lock', os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600
        )
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        self.status()
        error_fd = os.open(
            self.root / 'transport.stderr',
            os.O_CREAT | os.O_RDWR | os.O_APPEND | os.O_NOFOLLOW,
            0o600,
        )
        info = os.fstat(error_fd)
        if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
            os.close(error_fd)
            os.close(fd)
            raise SyncError('invalid diagnostic log')
        with os.fdopen(error_fd, 'a+b', buffering=0) as errors:
            self.errors = errors
            thread = threading.Thread(target=self.copy_logs, args=(errors,), daemon=True)
            thread.start()
            first = True
            lost_at = None
            signal.signal(signal.SIGTERM, lambda *_: self.stop.set())
            signal.signal(signal.SIGHUP, lambda *_: self.stop.set())
            try:
                while not self.stop.is_set():
                    self.terminal = subprocess.Popen(
                        self.command('attach' if first else 'reattach', tty=True)
                    )
                    first = False
                    self.status(terminal_pid=self.terminal.pid)
                    started = time.monotonic()
                    while self.terminal.poll() is None and not self.stop.wait(0.25):
                        if self.connected and time.monotonic() - started > 2:
                            lost_at = None
                    if self.stop.is_set():
                        break
                    code = self.terminal.returncode
                    self.connected = False
                    if code not in (255, -signal.SIGTERM, -signal.SIGKILL):
                        if code:
                            self.failure = f'remote terminal exited {code}; no task replayed'
                        break
                    lost_at = lost_at or time.monotonic()
                    self.status(terminal_reconnects=self.state['terminal_reconnects'] + 1)
                    if time.monotonic() - lost_at >= self.budget:
                        raise SyncError('terminal reconnect deadline exceeded')
                    print(
                        '\n[CCB: connection interrupted; reattaching the same remote session]',
                        flush=True,
                    )
                    self.stop.wait(1)
            except Exception as exc:
                self.failure = str(exc)
            finally:
                self.stop.set()
                terminate(self.terminal)
                terminate(self.tail)
                thread.join(timeout=5)
                self.status()
                os.close(fd)
            self.errors = None
        if self.failure:
            print('CCB remote provider: ' + self.failure, file=sys.stderr)
        return 1 if self.failure else 0


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--profiles', required=True, type=Path)
    parser.add_argument('--profile', required=True)
    # Everything after the native executable belongs to the wrapped CLI. In
    # particular, a project startup argument named --profile/--profiles must
    # not override the administrator's transport selection.
    parser.add_argument('native_command', nargs=argparse.REMAINDER, help=argparse.SUPPRESS)
    args = parser.parse_args()
    service, profile = load_profile(args.profiles, args.profile)
    if Path.cwd().resolve() != Path(profile['local_workspace']).resolve():
        parser.error('unexpected provider workspace')
    if (
        Path(os.environ.get('CLAUDE_PROJECTS_ROOT', '')).resolve()
        != transcript_path(profile).parents[1]
    ):
        parser.error('provider must use the standard CCB isolated local Claude profile')
    # Native CLI arguments carry local hooks/paths; the deployed remote profile
    # is the sole authority for remote executable arguments and authentication.
    raise SystemExit(ProviderBridge(profile, service.state_root(args.profile)).run())
