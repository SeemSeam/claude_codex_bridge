"""CCB transaction boundary: prepare before dispatch, import before completion.

Profiles are explicitly pinned in a user-owned file outside the project. A
project config may select a profile by name; it cannot choose a host or path.
"""

from __future__ import annotations

import base64
from contextlib import contextmanager
from dataclasses import replace
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import time
import uuid

from .endpoint import job_name
from .files import apply_files, directory, read_at, read_json, scan, write_json
from .objects import Git, Objects, SyncError, safe_path
from .transport import SshTransport


class WorkspaceSynchronizer:
    def __init__(self, layout, config, *, transport_factory=SshTransport):
        self.layout = layout
        self.config = config
        self.transport_factory = transport_factory

    def selected(self, job):
        spec = self.config.agents.get(job.agent_name)
        return getattr(spec, 'remote_workspace', None)

    def profile(self, name, agent):
        if not isinstance(name, str) or not re.fullmatch(r'[a-zA-Z0-9_-]{1,64}', name):
            raise SyncError('invalid remote workspace profile name')
        source = Path(os.environ.get('CCB_REMOTE_WORKSPACES_FILE', ''))
        if (
            not source.is_absolute()
            or '..' in source.parts
            or source.is_relative_to(self.layout.project_root)
        ):
            raise SyncError('CCB_REMOTE_WORKSPACES_FILE must be absolute and outside the project')
        with directory(source.parent) as fd:
            st = os.stat(source.name, dir_fd=fd, follow_symlinks=False)
            if not stat.S_ISREG(st.st_mode) or st.st_uid != os.getuid() or st.st_mode & 0o022:
                raise SyncError(
                    'remote profiles must be owned by the controller and not group/world writable'
                )
        profiles = read_json(source, limit=128 * 1024)
        p = profiles.get(name)
        if not isinstance(p, dict):
            raise SyncError('unknown remote workspace profile')
        required = {
            'host',
            'remote_root',
            'local_workspace',
            'project_root',
            'agent_name',
            'include',
            'workspace_id',
        }
        if not required <= set(p) or set(p) - required - {
            'python',
            'timeout_seconds',
            'publish',
            'ssh_config',
            'terminal',
            'retry_attempts',
        }:
            raise SyncError('unknown or missing remote workspace profile fields')
        if p.get('ssh_config'):
            ssh_config = Path(p['ssh_config'])
            if (
                not ssh_config.is_absolute()
                or '..' in ssh_config.parts
                or ssh_config.is_relative_to(self.layout.project_root)
            ):
                raise SyncError('SSH config must be absolute and outside the project')
            with directory(ssh_config.parent) as fd:
                st = os.stat(ssh_config.name, dir_fd=fd, follow_symlinks=False)
                if not stat.S_ISREG(st.st_mode) or st.st_uid != os.getuid() or st.st_mode & 0o022:
                    raise SyncError(
                        'SSH config must be owned by the controller and not group/world writable'
                    )
        root = Path(p['project_root'])
        workspace = Path(p['local_workspace'])
        if root != self.layout.project_root or p['agent_name'] != agent:
            raise SyncError('remote profile project/agent binding mismatch')
        if (
            not workspace.is_absolute()
            or '..' in workspace.parts
            or not workspace.is_relative_to(root)
            or workspace == root
        ):
            raise SyncError(
                'remote profile requires a dedicated worker workspace inside the project'
            )
        if not re.fullmatch(r'[a-zA-Z0-9][a-zA-Z0-9._-]*', p['host']):
            raise SyncError('SSH destination must be a fixed host alias')
        remote = Path(p['remote_root'])
        if not remote.is_absolute() or '..' in remote.parts or len(remote.parts) < 4:
            raise SyncError('a dedicated absolute remote root is required')
        if not isinstance(p['include'], list) or not p['include']:
            raise SyncError('an explicit path allowlist is required')
        for path in p['include']:
            safe_path(path)
        for a in p['include']:
            if any(a != b and a.startswith(b + '/') for b in p['include']):
                raise SyncError('overlapping include paths')
        if len(set(p['include'])) != len(p['include']):
            raise SyncError('duplicate include paths')
        if not re.fullmatch(r'[a-zA-Z0-9_-]{8,96}', p['workspace_id']):
            raise SyncError('invalid workspace identity')
        if not 1 <= p.get('timeout_seconds', 60) <= 120:
            raise SyncError('timeout must be between 1 and 120 seconds')
        if p.get('publish', 'worktree') not in ('worktree', 'commits'):
            raise SyncError('publish must be worktree or commits')
        if type(p.get('retry_attempts', 3)) is not int or not 1 <= p.get('retry_attempts', 3) <= 4:
            raise SyncError('retry_attempts must be 1..4')
        if 'terminal' in p:
            terminal = p['terminal']
            if not isinstance(terminal, dict) or set(terminal) - {
                'session_id',
                'reconnect_seconds',
                'transcript_limit_mb',
            }:
                raise SyncError('invalid terminal configuration')
            try:
                if str(uuid.UUID(terminal['session_id'])) != terminal['session_id']:
                    raise ValueError('noncanonical UUID')
            except (ValueError, KeyError, TypeError, AttributeError) as exc:
                raise SyncError('terminal requires a canonical session UUID') from exc
            if not 10 <= terminal.get('reconnect_seconds', 300) <= 3600:
                raise SyncError('reconnect_seconds must be 10..3600')
            if not 1 <= terminal.get('transcript_limit_mb', 128) <= 1024:
                raise SyncError('transcript_limit_mb must be 1..1024')
        return p

    def bind_context(self, job, context):
        """Pin this provider to its mirrored transcript, not session discovery."""
        name = self.selected(job)
        if not name or context is None:
            return context
        p = self.profile(name, job.agent_name)
        if 'terminal' not in p:
            return context
        from .provider_client import transcript_path

        return replace(context, remote_session_path=str(transcript_path(p)))

    def wait_for_transport(self, name, p, timeout=20):
        if 'terminal' not in p:
            return
        deadline = time.monotonic() + timeout
        path = self.state_root(name) / 'transport.json'
        while True:
            try:
                state = read_json(path, limit=64 * 1024)
                if (
                    state.get('session_id') == p['terminal']['session_id']
                    and state.get('state') == 'connected'
                    and 0 <= time.time() - state['updated_at'] < 8
                ):
                    os.kill(state['pid'], 0)
                    return
            except (OSError, SyncError, KeyError):
                pass
            if time.monotonic() >= deadline:
                raise SyncError('remote provider is disconnected; no task was delivered')
            time.sleep(0.25)

    def state_root(self, name):
        return self.layout.runtime_state_root / 'remote-workspaces' / name

    @contextmanager
    def locked(self, name):
        root = self.state_root(name)
        root.mkdir(parents=True, exist_ok=True, mode=0o700)
        with directory(root) as fd:
            lock = os.open('lock', os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600, dir_fd=fd)
            try:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
                yield root
            except BlockingIOError as exc:
                raise SyncError('workspace synchronization already in progress') from exc
            finally:
                os.close(lock)

    def _binding(self, profile, response, job, **expected):
        values = {
            'ok': True,
            'version': 1,
            'workspace_id': profile['workspace_id'],
            'job_id': job,
            **expected,
        }
        if any(response.get(k) != v for k, v in values.items()):
            raise SyncError('remote receipt does not match the transaction')

    def _git(self, p):
        workspace = Path(p['local_workspace'])
        with directory(workspace):
            pass
        git = Git(workspace)
        project = Git(p['project_root'])
        if not (workspace / '.git').is_file() or (workspace / '.git').is_symlink():
            raise SyncError('a controller-created Git worktree is required')
        common = Path(git.run('rev-parse', '--git-common-dir').decode().strip())
        expected = Path(project.run('rev-parse', '--git-common-dir').decode().strip())
        if not common.is_absolute():
            common = workspace / common
        if not expected.is_absolute():
            expected = Path(p['project_root']) / expected
        if common.resolve() != expected.resolve() or git.branch() == project.branch():
            raise SyncError('worker must share project objects but not its main branch')
        if git.run('rev-parse', '--show-object-format').strip() != b'sha1':
            raise SyncError('this protocol version supports SHA-1 repositories only')
        return git

    def _artifact(self, job):
        ref = job.request.body_artifact
        if not ref:
            return None
        path = Path(ref['path'])
        permitted = Path(self.layout.ccbd_text_artifacts_dir)
        if not path.is_absolute() or not path.is_relative_to(permitted) or '..' in path.parts:
            raise SyncError('request artifact outside the CCB text artifact directory')
        with directory(path.parent) as fd:
            item = read_at(fd, path.name)
        if item is None:
            raise SyncError('request artifact is missing')
        data = item[1]
        if (
            len(data) > 4 * 1024 * 1024
            or len(data) != ref['bytes']
            or hashlib.sha256(data).hexdigest() != ref['sha256']
        ):
            raise SyncError('request artifact size/hash mismatch')
        data.decode('utf-8')
        return {'data': base64.b64encode(data).decode(), 'sha256': ref['sha256']}

    def before_dispatch(self, job, context):
        name = self.selected(job)
        if not name:
            return job
        from ccbd.services.dispatcher_runtime.reply_delivery import is_reply_delivery_job

        if is_reply_delivery_job(job):
            raise SyncError(
                'remote passive reply delivery is not supported; use a tracked chain continuation'
            )
        p = self.profile(name, job.agent_name)
        if context is None or context.workspace_path != p['local_workspace']:
            raise SyncError('runtime workspace does not match pinned remote profile')
        if source := os.environ.get('CCB_REMOTE_PREFLIGHT_SOURCE'):
            from .preflight import check

            check(
                self.layout.project_root,
                Path(os.environ['CCB_REMOTE_WORKSPACES_FILE']),
                Path(source),
                os.environ,
            )
        self.wait_for_transport(name, p)
        job_name(job.job_id)
        with self.locked(name) as root:
            state_path = root / 'state.json'
            previous = read_json(state_path) if state_path.exists() else {}
            if previous and (previous.get('phase') != 'done' or previous.get('blocked')):
                raise SyncError(
                    'remote workspace is blocked by an unfinished transaction; recover it first'
                )
            git = self._git(p)
            original = git.head()
            # Native integration owns the full local branch. A worktree-mode
            # input is a filtered snapshot and does not export excluded files
            # or ancestors. Commit publication instead requires the entire
            # branch tree to fit the allowlist, since it moves local HEAD.
            if p.get('publish', 'worktree') == 'commits':
                git.export(original).validate_bootstrap(original, p['include'])
            files = scan(p['local_workspace'], p['include'])
            base = git.snapshot(files, parent=original, message=f'CCB remote input {job.job_id}')
            objects = git.export(base)
            objects.validate_bootstrap(base, p['include'])
            artifact = self._artifact(job)
            request = {
                'version': 1,
                'workspace_id': p['workspace_id'],
                'op': 'prepare',
                'job_id': job.job_id,
                'base': base,
                'objects': objects.wire(),
                'artifact': artifact,
            }
            state = {
                'job_id': job.job_id,
                'agent_name': job.agent_name,
                'phase': 'preparing',
                'base': base,
                'original': original,
                'branch': git.branch(),
                'index_tree': git.run('write-tree').decode().strip(),
                'profile': p,
                'blocked': False,
            }
            write_json(root / (job.job_id + '.request.json'), request)
            write_json(state_path, state)
            try:
                git.run(
                    'update-ref', f"refs/ccb/remote-input/{p['workspace_id']}/{job.job_id}", base
                )
                if p.get('publish', 'worktree') == 'commits':
                    git.run('update-ref', state['branch'], base, original)
                    git.run('read-tree', base)
                receipt = self.transport_factory(p).call(request)
                expected_path = (
                    p['remote_root'] + '/artifacts/' + job.job_id + '.txt' if artifact else None
                )
                self._binding(p, receipt, job.job_id, base=base, artifact_path=expected_path)
                state.update(phase='prepared', artifact_path=expected_path)
                write_json(state_path, state)
            except Exception as exc:
                state.update(blocked=True, error=str(exc))
                write_json(state_path, state)
                raise
            return self.provider_request(job, p)

    @staticmethod
    def provider_request(job, p):
        body = job.request.body
        if job.request.body_artifact:
            body = body.replace(
                job.request.body_artifact['path'],
                p['remote_root'] + '/artifacts/' + job.job_id + '.txt',
            )
        # Describe the mapping rather than rewriting arbitrary user prose or
        # artifact bytes. The persisted request retains its original hash.
        context = (
            '[CCB remote execution context]\n'
            f"This Claude process and its shell tools run on the remote machine selected by SSH alias {p['host']}.\n"
            'The SSH alias is a controller connection label; the operating-system hostname may differ.\n'
            f"Your current project/worktree is {p['remote_root']}/workspace.\n"
            f"Controller-side workspace {p['local_workspace']} maps to that remote directory.\n"
            'Use the corresponding project-relative files here; controller absolute paths are not local paths.\n'
            'Commands in this session execute on the remote machine, including requested VPS tests.\n'
            'CCB transfers approved files and returns results; do not connect back to the controller.\n'
            '[/CCB remote execution context]\n\n'
        )
        return replace(job, request=replace(job.request, body=context + body))

    def before_complete(self, job, decision):
        name = self.selected(job)
        if not name:
            return decision
        from completion.models import CompletionStatus

        if decision.status is not CompletionStatus.COMPLETED:
            self.block(
                name, job.job_id, 'provider did not complete normally; explicit recovery required'
            )
            return decision
        try:
            with self.locked(name) as root:
                write_json(root / (job.job_id + '.completion.json'), decision.to_record())
            result = self.recover(name, job.agent_name, job.job_id)
            diagnostics = {**decision.diagnostics, 'workspace_sync': result}
            return replace(
                decision,
                diagnostics=diagnostics,
                reply=(decision.reply or '')
                + '\n\n[CCB workspace synced]\n'
                + f"Imported remote commit: {result['head']}\n"
                + f"Local workspace: {result['workspace']}\nPublish mode: {result['publish']}\n",
            )
        except Exception as exc:
            self.block(name, job.job_id, str(exc))
            return replace(
                decision,
                status=CompletionStatus.FAILED,
                reason='workspace_sync_failed',
                reply='Remote execution ended, but workspace synchronization failed. '
                'Do not integrate or treat this as a successful result. ' + str(exc),
                diagnostics={
                    **decision.diagnostics,
                    'workspace_sync': {'status': 'blocked', 'error': str(exc)},
                },
            )

    def for_resume(self, job, context):
        """Restore the provider-visible artifact mapping without another prepare."""
        name = self.selected(job)
        if not name:
            return job
        p = self.profile(name, job.agent_name)
        state = read_json(self.state_root(name) / 'state.json')
        if (
            state.get('job_id') != job.job_id
            or state.get('profile') != p
            or state.get('phase') not in ('prepared', 'applying', 'done')
            or context is None
            or context.workspace_path != p['local_workspace']
        ):
            raise SyncError('incomplete remote dispatch journal; explicit recovery required')
        return self.provider_request(job, p)

    def block(self, name, job_id, error):
        with self.locked(name) as root:
            path = root / 'state.json'
            state = read_json(path) if path.exists() else {}
            if state.get('job_id') == job_id and state.get('phase') != 'done':
                state.update(blocked=True, error=error)
                write_json(path, state)

    def recover(self, name, agent, job_id):
        p = self.profile(name, agent)
        with self.locked(name) as root:
            state_path = root / 'state.json'
            state = read_json(state_path)
            if state['job_id'] != job_id or state['profile'] != p:
                raise SyncError('recovery job/profile does not match the journal')
            if state['phase'] == 'done':
                return state['result']
            transport = self.transport_factory(p)
            try:
                if state['phase'] == 'preparing':
                    request = read_json(root / (job_id + '.request.json'))
                    receipt = transport.call(request)
                    self._binding(p, receipt, job_id, base=state['base'])
                    state['phase'] = 'prepared'
                    write_json(state_path, state)
                result_path = root / (job_id + '.result.json')
                if result_path.exists():
                    response = read_json(result_path)
                else:
                    response = transport.call(
                        {
                            'version': 1,
                            'workspace_id': p['workspace_id'],
                            'op': 'collect',
                            'job_id': job_id,
                        }
                    )
                self._binding(p, response, job_id, base=state['base'])
                objects = Objects.from_wire(response['objects'])
                after = objects.validate_result(state['base'], response['head'], p['include'])
                git = self._git(p)
                before = git.export(state['base']).validate_bootstrap(state['base'], p['include'])
                head = git.head()
                recovering_apply = state['phase'] == 'applying'
                commits = p.get('publish', 'worktree') == 'commits'
                permitted_heads = (
                    ((state['base'], response['head']) if recovering_apply else (state['base'],))
                    if commits
                    else (state['original'],)
                )
                if git.branch() != state['branch'] or head not in permitted_heads:
                    raise SyncError('local worker branch changed during remote execution')
                actual = scan(p['local_workspace'], p['include'])
                if not recovering_apply and actual != before:
                    raise SyncError('local worktree changed during remote execution')
                if recovering_apply and set(actual) - set(before) - set(after):
                    raise SyncError('local files were added during interrupted synchronization')
                index_tree = git.run('write-tree').decode().strip()
                acceptable_trees = {
                    objects.commit(state['base'])[0] if commits else state['index_tree']
                }
                if recovering_apply and commits:
                    acceptable_trees.add(objects.commit(response['head'])[0])
                if index_tree not in acceptable_trees:
                    raise SyncError('local index changed during remote execution')
                write_json(result_path, response)
                state['phase'] = 'applying'
                write_json(state_path, state)
                git.import_objects(objects)
                imported_ref = f"refs/ccb/remote/{p['workspace_id']}/{job_id}"
                git.run('update-ref', imported_ref, response['head'])
                apply_files(p['local_workspace'], before, after, recovery=recovering_apply)
                if commits:
                    git.run('update-ref', state['branch'], response['head'], head)
                    git.run('read-tree', response['head'])
                receipt = transport.call(
                    {
                        'version': 1,
                        'workspace_id': p['workspace_id'],
                        'op': 'ack',
                        'job_id': job_id,
                        'head': response['head'],
                    }
                )
                self._binding(p, receipt, job_id, head=response['head'])
                result = {
                    'status': 'synced',
                    'base': state['base'],
                    'head': response['head'],
                    'local_head': git.head(),
                    'publish': p.get('publish', 'worktree'),
                    'import_ref': imported_ref,
                    'workspace': p['local_workspace'],
                    'changed_paths': sorted(
                        path
                        for path in set(before) | set(after)
                        if before.get(path) != after.get(path)
                    ),
                }
                state.update(phase='done', blocked=False, result=result)
                state.pop('error', None)
                write_json(state_path, state)
                write_json(root / (job_id + '.receipt.json'), result)
                return result
            except Exception as exc:
                state.update(blocked=True, error=str(exc))
                write_json(state_path, state)
                raise
