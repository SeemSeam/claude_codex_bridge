from __future__ import annotations
import base64
from dataclasses import replace
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
from types import SimpleNamespace
import uuid

import pytest

if sys.platform != 'linux':
    pytest.skip('remote provider transport currently targets Linux', allow_module_level=True)

from remote_workspace.files import write_json
from remote_workspace.objects import SyncError
from remote_workspace.provider_client import TranscriptMirror, transcript_path
from remote_workspace.provider_remote import RemoteProvider
from remote_workspace.transport import NetworkError, SshTransport
from test_remote_workspace import sync_case


def ready(mirror, **kw):
    return {
        'kind': 'ready',
        'offset': mirror.offset,
        'sha256': mirror.hash.hexdigest(),
        'boot_id': 'boot1',
        'generation': 'run1',
        **kw,
    }


def test_native_cli_arguments_cannot_override_transport_profile(tmp_path, monkeypatch):
    from remote_workspace import provider_client

    trusted = tmp_path / 'trusted-profiles.json'
    profile = {
        'project_root': str(tmp_path),
        'local_workspace': str(tmp_path),
        'agent_name': 'worker',
        'terminal': {'session_id': str(uuid.uuid4())},
    }
    loaded, launched = [], []

    def load(source, name):
        loaded.append((source, name))
        return SimpleNamespace(state_root=lambda value: tmp_path / value), profile

    def bridge(selected, state_root):
        launched.append((selected, state_root))
        return SimpleNamespace(run=lambda: 0)

    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv('CLAUDE_PROJECTS_ROOT', str(transcript_path(profile).parents[1]))
    monkeypatch.setattr(provider_client, 'load_profile', load)
    monkeypatch.setattr(provider_client, 'ProviderBridge', bridge)
    monkeypatch.setattr(
        sys,
        'argv',
        [
            'remote_provider.py',
            '--profiles',
            str(trusted),
            '--profile',
            'trusted-worker',
            'claude',
            '--profile',
            'other-worker',
            '--profiles',
            str(tmp_path / 'project-controlled.json'),
            '--help',
        ],
    )
    with pytest.raises(SystemExit) as exit_info:
        provider_client.main()
    assert exit_info.value.code == 0
    assert loaded == [(trusted, 'trusted-worker')]
    assert launched == [(profile, tmp_path / 'trusted-worker')]


def test_resume_checks_prefix_and_never_replays_bytes(tmp_path):
    path = tmp_path / 'log.jsonl'
    path.write_bytes(b'{"type":"user"}\n')
    mirror = TranscriptMirror(path, 4096)
    mirror.record(ready(mirror))
    data = b'{"type":"assistant"}\n'
    mirror.record(
        {'kind': 'data', 'offset': mirror.offset, 'data': base64.b64encode(data).decode()}
    )
    mirror.file.close()
    resumed = TranscriptMirror(path, 4096)
    resumed.record(ready(resumed))
    assert resumed.offset == path.stat().st_size
    assert path.read_bytes().count(b'assistant') == 1
    assert resumed.hash.hexdigest() == hashlib.sha256(path.read_bytes()).hexdigest()
    resumed.file.close()


@pytest.mark.parametrize(
    'change',
    [dict(offset=20), dict(sha256='0' * 64), dict(boot_id='boot2'), dict(generation='run2')],
)
def test_changed_remote_identity_or_prefix_fails_closed(tmp_path, change):
    mirror = TranscriptMirror(tmp_path / 'log', 4096)
    mirror.record(ready(mirror))
    with pytest.raises(SyncError):
        mirror.record(ready(mirror, **change))
    assert mirror.offset == 0
    mirror.file.close()


def test_transcript_symlink_and_hardlink_rejected(tmp_path):
    secret = tmp_path / 'secret'
    secret.write_text('unchanged')
    link = tmp_path / 'log'
    link.symlink_to(secret)
    with pytest.raises(OSError):
        TranscriptMirror(link, 4096)
    link.unlink()
    os.link(secret, link)
    with pytest.raises(SyncError):
        TranscriptMirror(link, 4096)
    assert secret.read_text() == 'unchanged'


def test_idempotent_rpc_retry_has_one_request_identity(monkeypatch):
    calls = []
    transport = SshTransport({'retry_attempts': 3, 'timeout_seconds': 30})

    def one(request, deadline):
        calls.append(dict(request))
        if len(calls) == 1:
            raise NetworkError('lost ack')
        return {'ok': True}

    monkeypatch.setattr(transport, '_call', one)
    monkeypatch.setattr('remote_workspace.transport.time.sleep', lambda _: None)
    request = {'op': 'ack', 'job_id': 'same-task', 'head': 'fixed'}
    assert transport.call(request) == {'ok': True}
    assert calls == [request, request]


def test_semantic_rejection_is_not_retried(monkeypatch):
    transport = SshTransport({'timeout_seconds': 30})
    calls = []

    def one(*_):
        calls.append(1)
        raise SyncError('local edits')

    monkeypatch.setattr(transport, '_call', one)
    with pytest.raises(SyncError, match='local edits'):
        transport.call({})
    assert len(calls) == 1


def test_remote_boot_loss_does_not_launch_a_second_task(tmp_path, monkeypatch):
    config = {'terminal': {'session_id': str(uuid.uuid4()), 'claude': '/bin/false'}}
    write_json(tmp_path / 'endpoint.json', config)
    write_json(tmp_path / 'state.json', {'phase': 'prepared', 'job_id': 'unfinished'})
    provider = RemoteProvider(tmp_path)
    monkeypatch.setattr(provider, 'status', lambda: {'alive': False})
    invoked = []
    monkeypatch.setattr(provider, 'tmux_call', lambda *a, **k: invoked.append(a))
    with pytest.raises(SyncError, match='unfinished'):
        provider.attach(create=True)
    assert not invoked


def test_explicit_binding_survives_wrong_discovery_and_restart(sync_case):
    from provider_execution.base import ProviderRuntimeContext
    from provider_execution.state_models import (
        _runtime_context_to_record,
        _runtime_context_from_record,
    )
    from provider_backends.claude.execution_runtime.start import configure_resume_reader
    from provider_backends.claude.comm import ClaudeLogReader

    c = sync_case
    c.p['terminal'] = {'session_id': str(uuid.uuid4())}
    c.profile.write_text(json.dumps({'demo': c.p}))
    job = SimpleNamespace(agent_name='worker')
    context = ProviderRuntimeContext(
        'worker', str(c.workspace), 'tmux', 'pane', 'old-wrong-session'
    )
    context = c.service.bind_context(job, context)
    path = transcript_path(c.p)
    path.parent.mkdir(parents=True)
    path.write_text('')
    restored = _runtime_context_from_record(_runtime_context_to_record(context))
    reader = ClaudeLogReader(root=c.project / 'wrong', work_dir=c.project)
    configure_resume_reader(reader, {'session_path': '/wrong'}, restored)
    assert reader.current_session_path() == path
    assert reader._preferred_session_locked is True


def test_disconnected_transport_blocks_before_new_transaction(sync_case):
    c = sync_case
    c.p['terminal'] = {'session_id': str(uuid.uuid4())}
    with pytest.raises(SyncError, match='disconnected'):
        c.service.wait_for_transport('demo', c.p, timeout=0)
    assert not (c.service.state_root('demo') / 'state.json').exists()


@pytest.mark.parametrize('status', ['cancelled', 'failed', 'incomplete'])
def test_non_success_preserves_remote_work_for_explicit_recovery(sync_case, status):
    from completion.models import CompletionStatus

    c = sync_case
    job = c.submit()
    original = (c.workspace / 'src/calc.py').read_text()
    (c.remote / 'workspace/src/calc.py').write_text('PARTIAL_REMOTE_WORK = True\n')
    result = c.dispatcher.complete(
        job.job_id, c.decision(status=CompletionStatus(status), reply='stopped')
    )
    assert result.status.value == status
    assert (c.workspace / 'src/calc.py').read_text() == original
    assert json.loads((c.service.state_root('demo') / 'state.json').read_text())['blocked']
    assert c.service.recover('demo', 'worker', job.job_id)['status'] == 'synced'
    assert (c.workspace / 'src/calc.py').read_text() == 'PARTIAL_REMOTE_WORK = True\n'
    assert c.dispatcher.get(job.job_id).status.value == status


def test_remote_prompt_explains_both_paths_without_changing_original(sync_case):
    from provider_execution.base import ProviderRuntimeContext

    c = sync_case
    job = c.submit('Implement src/calc.py and run the tests on the VPS.')
    sent = c.execution.started[-1]
    assert 'selected by SSH alias' in sent.request.body
    assert 'operating-system hostname may differ' in sent.request.body
    assert c.p['local_workspace'] in sent.request.body
    assert c.p['remote_root'] + '/workspace' in sent.request.body
    assert job.request.body == 'Implement src/calc.py and run the tests on the VPS.'
    context = ProviderRuntimeContext('worker', str(c.workspace), 'tmux', 'pane', 'session')
    assert c.service.for_resume(job, context).request.body == sent.request.body


def test_retention_only_retires_old_acknowledged_payloads(sync_case):
    import time
    from remote_workspace.maintenance import prune_local

    c = sync_case
    jobs = []
    for index in range(4):
        job = c.submit('task ' + str(index))
        jobs.append(job)
        (c.remote / 'workspace/src/calc.py').write_text(f'VALUE = {index}\n')
        c.dispatcher.complete(job.job_id, c.decision(reply='done'))
    root = c.service.state_root('demo')
    for job in jobs:
        for file in (
            root / (job.job_id + '.receipt.json'),
            c.remote / (job.job_id + '.result.json'),
        ):
            age = time.time() - 30 * 86400
            os.utime(file, (age, age))
    before = (c.workspace / 'src/calc.py').read_text()
    plan = prune_local(c.service, 'demo', 'worker', keep=2, days=14)
    assert len(plan['jobs']) == 2 and jobs[-1].job_id not in plan['jobs']
    assert all((root / (j + '.result.json')).exists() for j in plan['jobs'])
    result = prune_local(c.service, 'demo', 'worker', keep=2, days=14, apply=True)
    assert set(result['retired']) == set(plan['jobs'])
    for job in result['retired']:
        assert not (root / (job + '.result.json')).exists()
        assert json.loads((root / (job + '.receipt.json')).read_text())['payloads_retired']
        assert json.loads((c.remote / (job + '.result.json')).read_text())['payloads_retired']
    assert (c.workspace / 'src/calc.py').read_text() == before
    assert (root / (jobs[-1].job_id + '.result.json')).exists()


def test_retention_refuses_unfinished_task(sync_case):
    from remote_workspace.maintenance import prune_local

    c = sync_case
    c.submit()
    with pytest.raises(SyncError, match='unfinished'):
        prune_local(c.service, 'demo', 'worker', apply=True)


def test_provider_auth_source_can_differ_from_controller_state(tmp_path, monkeypatch):
    from provider_core.source_home import current_provider_source_home

    monkeypatch.setenv('CCB_SOURCE_HOME', str(tmp_path / 'controller'))
    monkeypatch.setenv('CCB_PROVIDER_SOURCE_HOME', str(tmp_path / 'real-user'))
    assert current_provider_source_home() == tmp_path / 'real-user'


def test_remote_project_exposes_pinned_cli_without_changing_normal_agents(tmp_path, monkeypatch):
    from provider_core.caller_env import caller_context_env

    runtime = tmp_path / '.ccb/agents/reviewer/provider-runtime/codex'
    runtime.mkdir(parents=True)
    monkeypatch.setenv('CCB_REMOTE_WORKSPACES_FILE', str(tmp_path.parent / 'profiles.json'))
    values = caller_context_env(actor='reviewer', runtime_dir=runtime, launch_session_id='fixture')
    assert Path(values['CCB_PINNED_CLI']) == Path(__file__).parents[1] / 'bin/ccb'
    assert values['CCB_CALLER_ACTOR'] == 'reviewer'
    monkeypatch.delenv('CCB_REMOTE_WORKSPACES_FILE')
    assert 'CCB_PINNED_CLI' not in caller_context_env(
        actor='reviewer', runtime_dir=runtime, launch_session_id='fixture'
    )


def test_full_pasted_prompt_activates_but_quoted_marker_does_not():
    from test_claude_queued_prompt_activation import _submission, _process_raw
    from provider_backends.claude.execution_runtime.state_machine_runtime import build_poll_state

    submission = _submission()
    expected = submission.runtime_state['prompt_text']
    for text, valid in [
        ('\n<pasted_content id="0827">\n' + expected + '\n</pasted_content id="0827">\n', True),
        ('<pasted_content id="0827">' + expected + '</pasted_content>', True),
        ('Quoted example: <pasted_content id="0827">' + expected + '</pasted_content>', False),
        ('<pasted_content id="0827">CCB_REQ_ID: job_current\nwrong task</pasted_content>', False),
        ('<pasted_content id="0827">' + expected + '</pasted_content id="DIFFERENT">', False),
    ]:
        poll = build_poll_state(submission)
        _process_raw(
            submission,
            poll,
            {'type': 'user', 'message': {'role': 'user', 'content': text}, 'isSidechain': False},
        )
        assert poll.anchor_seen is valid


def test_resume_replays_only_a_unique_exact_missed_prompt(tmp_path):
    from provider_backends.claude.execution_runtime.start import _recover_exact_pasted_prompt

    expected = 'CCB_REQ_ID: job_same\n\nexact body'
    record = {
        'type': 'user',
        'message': {
            'role': 'user',
            'content': '<pasted_content id="1234">' + expected + '</pasted_content id="1234">',
        },
    }
    path = tmp_path / 'session.jsonl'
    path.write_text(json.dumps({'type': 'summary'}) + '\n' + json.dumps(record) + '\n')
    state = {'prompt_text': expected, 'prompt_sent': True, 'state': {'offset': path.stat().st_size}}
    _recover_exact_pasted_prompt(state, path)
    assert state['state']['offset'] < path.stat().st_size
    assert state['prompt_sent'] is True
    path.write_text(path.read_text() + json.dumps(record) + '\n')
    duplicate = {'prompt_text': expected, 'state': {'offset': path.stat().st_size}}
    _recover_exact_pasted_prompt(duplicate, path)
    assert duplicate['state']['offset'] == path.stat().st_size


@pytest.mark.skipif(
    os.environ.get('CCB_TEST_REMOTE_SANDBOX') != '1',
    reason='opt-in Linux bubblewrap/libseccomp integration test',
)
def test_local_verification_has_no_host_files_network_or_control_data(tmp_path):
    (tmp_path / 'hello.py').write_text('print("hello")\n')
    (tmp_path / '.git').write_text('gitdir: /host/project/.git\n')
    tool = Path(__file__).resolve().parents[1] / 'tools/remote_verify.py'
    code = '''import os,socket,pathlib,ctypes,errno
assert not pathlib.Path('/mnt').exists()
assert not pathlib.Path('/init').exists()
assert list(pathlib.Path('/home').iterdir()) == [pathlib.Path('/home/sandbox')]
try: assert pathlib.Path('/work/.git').read_bytes()==b''
except PermissionError: pass
assert 'SSH_AUTH_SOCK' not in os.environ
assert 'ANTHROPIC_API_KEY' not in os.environ
assert not list(pathlib.Path('/run').iterdir())
try: pathlib.Path('/work/hello.py').write_text('bad')
except OSError: pass
else: raise AssertionError('worktree writable')
try: socket.create_connection(('1.1.1.1',443),timeout=.2)
except OSError: pass
else: raise AssertionError('external network accessible')
libc=ctypes.CDLL(None,use_errno=True)
assert libc.unshare(0x10000000)==-1
assert 'NoNewPrivs:\\t1' in pathlib.Path('/proc/self/status').read_text()
print('VERIFY_BOUNDARY_OK')
'''
    result = subprocess.run(
        [
            '/usr/bin/python3',
            str(tool),
            '--workspace',
            str(tmp_path),
            '--',
            '/usr/bin/python3',
            '-I',
            '-c',
            code,
        ],
        text=True,
        capture_output=True,
        timeout=20,
        env={**os.environ, 'ANTHROPIC_API_KEY': 'synthetic-not-a-secret'},
    )
    assert result.returncode == 0, result.stderr
    assert 'VERIFY_BOUNDARY_OK' in result.stdout
    assert (tmp_path / 'hello.py').read_text() == 'print("hello")\n'
