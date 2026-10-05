"""Remote half of the protocol. Deploy with a fixed endpoint.json beside code.

No command, destination path, hook, or configuration is accepted from a task.
The remote OS account is a separate trust domain, not sandboxed by this module.
"""

from __future__ import annotations

import base64
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import sys

from .files import apply_files, check_scope, read_json, scan, write_json
from .objects import Git, Objects, SyncError
from .transport import WIRE_LIMIT


def job_name(value):
    if not isinstance(value, str) or not re.fullmatch(r'[a-zA-Z0-9_-]{1,96}', value):
        raise SyncError('invalid job ID')
    return value


class Endpoint:
    def __init__(self, root, config):
        self.root = Path(root)
        self.config = config
        self.workspace = self.root / 'workspace'
        self.repository = self.root / 'repository'
        self.include = tuple(config['include'])
        self.git_options = dict(
            executable=config.get('git', '/usr/bin/git'), env=config.get('git_env', {})
        )
        self.state_path = self.root / 'state.json'

    def handle(self, request):
        if (
            request.get('version') != 1
            or request.get('workspace_id') != self.config['workspace_id']
        ):
            raise SyncError('workspace/protocol binding mismatch')
        job = job_name(request.get('job_id'))
        self.root.mkdir(exist_ok=True, parents=True)
        lock = os.open(self.root / 'lock', os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            if request['op'] == 'prepare':
                return self.prepare(job, request)
            if request['op'] == 'collect':
                return self.collect(job)
            if request['op'] == 'ack':
                return self.ack(job, request)
            if request['op'] == 'prune':
                from .maintenance import prune_remote

                return prune_remote(self, job, request)
            raise SyncError('unsupported operation')
        finally:
            os.close(lock)

    def state(self):
        return read_json(self.state_path) if self.state_path.exists() else {}

    def prepare(self, job, request):
        objects = Objects.from_wire(request['objects'])
        files = objects.validate_bootstrap(request['base'], self.include)
        old = self.state()
        if old.get('job_id') != job and (self.root / (job + '.result.json')).exists():
            raise SyncError('retired job identity cannot be reused')
        request_hash = hashlib.sha256(json.dumps(request, sort_keys=True).encode()).hexdigest()
        if old.get('job_id') == job:
            if old.get('request_hash') != request_hash:
                raise SyncError('job ID reused with different input')
            if old.get('phase') in ('prepared', 'collected', 'acked'):
                return old['prepare_receipt']
        elif old.get('phase') not in (None, 'acked'):
            raise SyncError('previous transaction is not acknowledged')
        if not (self.repository / '.git').exists():
            Git(self.root, **self.git_options).run(
                'init', '--template=', '--initial-branch=main', str(self.repository)
            )
        if not (self.workspace / '.git').exists():
            repo = Git(self.repository, **self.git_options)
            repo.import_objects(objects)
            repo.run('update-ref', 'refs/heads/main', request['base'])
            (self.repository / '.git' / 'shallow').write_text(request['base'] + '\n')
            repo.run('worktree', 'add', '-B', 'ccb/remote', str(self.workspace), request['base'])
        git = Git(self.workspace, **self.git_options)
        check_scope(self.workspace, self.include)
        if old.get('phase') == 'acked':
            old_objects = git.export(old['head'])
            before = old_objects.validate_bootstrap(old['head'], self.include)
            if git.head() != old['head'] or scan(self.workspace, self.include) != before:
                raise SyncError('remote has edits outside a dispatched task; refusing overwrite')
        elif old.get('phase') == 'preparing' and old.get('job_id') == job:
            before = Objects.from_wire(old['before_objects']).validate_bootstrap(
                old['before'], self.include
            )
        else:
            before = git.export(git.head()).validate_bootstrap(git.head(), self.include)
            if scan(self.workspace, self.include) != before:
                raise SyncError('remote bootstrap workspace has unexpected edits')
        # Preserve the previous object tree before any mutation for recovery.
        if old.get('phase') == 'preparing' and old.get('job_id') == job:
            state = old
        else:
            state = {
                'job_id': job,
                'phase': 'preparing',
                'base': request['base'],
                'before': git.head(),
                'before_objects': git.export(git.head()).wire(),
                'request_hash': request_hash,
            }
            write_json(self.state_path, state)
        git.import_objects(objects)
        shallow = self.repository / '.git' / 'shallow'
        roots = set(shallow.read_text().splitlines()) if shallow.exists() else set()
        roots.add(request['base'])
        shallow.write_text('\n'.join(sorted(roots)) + '\n')
        apply_files(self.workspace, before, files, recovery=True)
        current = git.head()
        if current not in (state['before'], request['base']):
            raise SyncError('remote branch changed during prepare')
        git.run('update-ref', git.branch(), request['base'], current)
        git.run('read-tree', request['base'])
        artifact = request.get('artifact')
        artifact_path = None
        if artifact is not None:
            data = base64.b64decode(artifact['data'], validate=True)
            if (
                len(data) > 4 * 1024 * 1024
                or hashlib.sha256(data).hexdigest() != artifact['sha256']
            ):
                raise SyncError('request artifact hash/size mismatch')
            data.decode('utf-8')
            artifacts = self.root / 'artifacts'
            artifacts.mkdir(exist_ok=True)
            artifact_path = str(artifacts / (job + '.txt'))
            existing = scan(artifacts, [job + '.txt'])
            apply_files(artifacts, existing, {job + '.txt': ('100644', data)})
        receipt = {
            'ok': True,
            'version': 1,
            'workspace_id': self.config['workspace_id'],
            'job_id': job,
            'base': request['base'],
            'artifact_path': artifact_path,
        }
        state.update(phase='prepared', prepare_receipt=receipt)
        write_json(self.state_path, state)
        return receipt

    def collect(self, job):
        state = self.state()
        if state.get('job_id') != job or state.get('phase') not in (
            'prepared',
            'collected',
            'acked',
        ):
            raise SyncError('no matching prepared transaction')
        receipt_path = self.root / (job + '.result.json')
        if receipt_path.exists():
            result = read_json(receipt_path)
            if result['job_id'] != job or result['base'] != state['base']:
                raise SyncError('persisted result binding mismatch')
            if state['phase'] == 'prepared':
                state.update(phase='collected', head=result['head'])
                write_json(self.state_path, state)
            return result
        git = Git(self.workspace, **self.git_options)
        check_scope(self.workspace, self.include)
        current = git.head()
        history = git.export(current, state['base'])
        history.validate_result(state['base'], current, self.include)
        files = scan(self.workspace, self.include)
        head = git.snapshot(files, parent=current, message=f'CCB remote result {job}')
        git.run('update-ref', git.branch(), head, current)
        git.run('read-tree', head)
        objects = git.export(head, state['base'])
        objects.validate_result(state['base'], head, self.include)
        result = {
            'ok': True,
            'version': 1,
            'workspace_id': self.config['workspace_id'],
            'job_id': job,
            'base': state['base'],
            'head': head,
            'objects': objects.wire(),
        }
        # Durable receipt precedes status transition: a lost SSH response can be
        # fetched again without asking the model to repeat its work.
        write_json(receipt_path, result)
        state.update(phase='collected', head=head)
        write_json(self.state_path, state)
        return result

    def ack(self, job, request):
        state = self.state()
        if state.get('job_id') != job or state.get('phase') not in ('collected', 'acked'):
            raise SyncError('cannot acknowledge an uncollected transaction')
        if state.get('head') != request.get('head'):
            raise SyncError('acknowledgement commit mismatch')
        state['phase'] = 'acked'
        write_json(self.state_path, state)
        return {
            'ok': True,
            'version': 1,
            'workspace_id': self.config['workspace_id'],
            'job_id': job,
            'head': state['head'],
        }


def main(root):
    try:
        data = sys.stdin.buffer.read(WIRE_LIMIT + 1)
        if len(data) > WIRE_LIMIT:
            raise SyncError('request exceeds wire limit')
        config = read_json(Path(root) / 'endpoint.json', limit=64 * 1024)
        response = Endpoint(root, config).handle(json.loads(data))
    except Exception as exc:
        response = {'ok': False, 'error': f'{type(exc).__name__}: {exc}'}
    wire = json.dumps(response, separators=(',', ':')).encode()
    if len(wire) > WIRE_LIMIT:
        wire = b'{"ok":false,"error":"response exceeds wire limit"}'
    sys.stdout.buffer.write(wire)
