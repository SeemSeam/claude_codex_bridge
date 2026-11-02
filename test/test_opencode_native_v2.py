"""Native v2 fixtures mirror the pinned service contract, not legacy SQL rows."""
import copy
import hashlib
from types import SimpleNamespace

import pytest

from completion.models import CompletionStatus
from opencode_runtime.native import NativeStateError, NativeTransportError, NativeRequestRejected
from opencode_runtime.native_v2 import OpenCodeV2State
from provider_backends.opencode.execution_runtime import native_v2 as execution

NOW = '2026-11-02T00:00:00Z'
PROMPT = 'CCB_REQ_ID: job_native2\nReply OK'
HASH = hashlib.sha256(PROMPT.encode()).hexdigest()


def user():
    return dict(id='msg_input', type='user', text=PROMPT, time={'created': 1},
                metadata={'ccb_job_id': 'job_native2', 'ccb_prompt_sha256': HASH})


def assistant(*, finish='stop', completed=3, text='OK', tools=()):
    return dict(id='msg_assistant', type='assistant', time={'created': 2, 'completed': completed},
                finish=finish, content=[{'type': 'text', 'text': text}, *tools])


def idle(outcome='succeeded'):
    return dict(id='msg_idle', type='idle', time={'created': 4}, outcome=outcome)


@pytest.fixture
def native():
    client = OpenCodeV2State('http://127.0.0.1:1', '/workspace')
    records, requests = [], []
    def get(path, *, query=None):
        requests.append((path, query))
        if path == '/api/info':
            return {'version': '2.2.4'}
        if path == '/api/session/ses_native':
            return {'data': {'id': 'ses_native', 'location': {'directory': '/workspace'}, 'time': {'updated': 4}}}
        if path.endswith('/inbox'):
            return {'data': []}
        return {'data': list(reversed(records)), 'cursor': {'next': None, 'previous': None}}
    client.get = get
    return client, records, requests


def observe(client):
    return client.observe('ses_native', input_id='msg_input', job_id='job_native2', prompt_hash=HASH)


def test_multistep_tools_need_native_idle_and_final_stop(native):
    client, records, _ = native
    tool = {'type': 'tool', 'id': 'tool', 'state': {'status': 'running'}}
    records[:] = [user(), assistant(finish='tool-calls', text='working', tools=[tool])]
    assert observe(client)['state'] == 'running'  # time.completed does not settle a run.
    tool['state']['status'] = 'completed'
    records.append({**assistant(text='final'), 'id': 'msg_final'})
    assert observe(client)['state'] == 'running'  # Final assistant also needs idle.
    records.append(idle())
    result = observe(client)
    assert result['state'] == 'succeeded' and result['reply'] == 'final'
    assert 'parentID' not in result  # Native v2 does not fabricate v1 causal links.


@pytest.mark.parametrize('change', ['tool', 'unfinished', 'empty', 'length', 'tool-calls'])
def test_inconsistent_or_empty_idle_never_completes(native, change):
    client, records, _ = native
    final = assistant()
    if change == 'tool':
        final['content'].append({'type': 'tool', 'state': {'status': 'running'}})
    elif change == 'unfinished':
        final['time']['completed'] = None
    elif change == 'empty':
        final['content'] = [{'type': 'reasoning', 'text': 'private reasoning'}]
    else:
        final['finish'] = change
    records[:] = [user(), final, idle()]
    assert observe(client)['state'] == 'incomplete'


@pytest.mark.parametrize('outcome', ['failed', 'interrupted'])
def test_idle_failure_and_interruption_are_explicit_without_text(native, outcome):
    client, records, _ = native
    records[:] = [user(), idle(outcome)]
    assert observe(client)['state'] == outcome


@pytest.mark.parametrize('kind', ['user', 'synthetic', 'shell', 'skill', 'location-switched'])
def test_coalesced_or_superseding_foreign_input_is_never_attributed(native, kind):
    client, records, _ = native
    records[:] = [user(), dict(id='msg_foreign', type=kind, time={'created': 2}), assistant(), idle()]
    with pytest.raises(NativeStateError, match='foreign work'):
        observe(client)


def test_later_turn_after_exact_idle_does_not_replace_reply(native):
    client, records, _ = native
    records[:] = [user(), assistant(), idle(), {**user(), 'id': 'msg_other'},
                  {**assistant(text='foreign'), 'id': 'msg_other_reply'}]
    assert observe(client)['reply'] == 'OK'


@pytest.mark.parametrize('corrupt', ['text', 'metadata', 'id', 'version', 'location'])
def test_request_and_service_binding_validation(native, corrupt):
    client, records, _ = native
    records[:] = [user(), assistant(), idle()]
    if corrupt in {'text', 'metadata'}:
        records[0][corrupt] = 'changed' if corrupt == 'text' else {}
    elif corrupt == 'id':
        records[1]['id'] = records[2]['id']
    else:
        original = client.get
        def get(path, **kwargs):
            data = original(path, **kwargs)
            if corrupt == 'version' and path == '/api/info':
                data['version'] = '3.0.0'
            if corrupt == 'location' and path == '/api/session/ses_native':
                data['data']['location']['directory'] = '/foreign'
            return data
        client.get = get
    with pytest.raises(NativeStateError):
        observe(client)


def test_pagination_preserves_native_order_and_rejects_cycles(native):
    client, _, requests = native
    original = client.get
    pages = [dict(data=[idle(), assistant()], cursor={'next': 'older'}),
             dict(data=[user()], cursor={'next': None})]
    def get(path, *, query=None):
        if path.endswith('/message'):
            requests.append((path, query))
            return pages[1] if 'cursor' in query else pages[0]
        return original(path, query=query)
    client.get = get
    assert observe(client)['state'] == 'succeeded'
    assert requests[-1][1] == {'cursor': 'older', 'limit': 200}
    pages[1] = dict(data=[dict(id='msg_old', type='system', time={})], cursor={'next': 'older'})
    with pytest.raises(NativeStateError, match='cursor'):
        observe(client)


def test_native_start_and_lost_response_are_observed_without_replay(native, monkeypatch):
    client, records, _ = native
    calls = []
    def submit(session_id, **fields):
        calls.append(fields)
        records[:] = [{**user(), 'id': fields['input_id']}, assistant(), idle()]
        raise NativeTransportError('lost response')
    client.submit = submit
    monkeypatch.setattr(execution, 'OpenCodeV2State', lambda *args, **kwargs: client)
    prepared = SimpleNamespace(session=SimpleNamespace(work_dir='/workspace'), backend=object(), pane_id='%1')
    job = SimpleNamespace(job_id='job_native2', agent_name='worker')
    submission = execution.start(job, prepared, api_url=client.url, session_id='ses_native', prompt=PROMPT, now=NOW)
    result = execution.poll(submission, SimpleNamespace(reader=client), now='2026-11-02T00:00:01Z')
    assert result.decision.status is CompletionStatus.COMPLETED
    assert result.decision.reply == 'OK'
    assert len(calls) == 1
    assert result.decision.diagnostics['automatic_replay'] is False


@pytest.mark.parametrize('error,reason', [(NativeTransportError, 'admission_unknown'), (NativeRequestRejected, 'admission_rejected')])
def test_admission_unknown_or_rejected_is_bounded(native, monkeypatch, error, reason):
    client, _, _ = native
    client.submit = lambda *args, **kwargs: (_ for _ in ()).throw(error('failure'))
    monkeypatch.setattr(execution, 'OpenCodeV2State', lambda *args, **kwargs: client)
    prepared = SimpleNamespace(session=SimpleNamespace(work_dir='/workspace'), backend=object(), pane_id='%1')
    submission = execution.start(SimpleNamespace(job_id='job_native2', agent_name='worker'), prepared,
                                 api_url=client.url, session_id='ses_native', prompt=PROMPT, now=NOW)
    result = execution.poll(submission, SimpleNamespace(reader=client), now='2026-11-02T00:00:31Z')
    assert result.decision.terminal and reason in result.decision.reason


def test_explicit_auth_reference_is_private_and_missing_reference_fails(monkeypatch):
    monkeypatch.setenv('CCB_TEST_V2_PASSWORD', 'public-fixture-value')
    client = OpenCodeV2State('http://localhost:1', '/workspace', password_env='CCB_TEST_V2_PASSWORD')
    assert client.headers['Authorization'].startswith('Basic ')
    with pytest.raises(NativeStateError, match='unavailable'):
        OpenCodeV2State('http://localhost:1', '/workspace', password_env='CCB_TEST_MISSING_VALUE')


def test_queued_binding_survives_long_wait_and_active_cancel_never_sends_keys(native, monkeypatch):
    client, _, _ = native
    original = client.get
    queued = {'id': 'msg_input', 'sessionID': 'ses_native', 'type': 'user',
              'payload': {'text': PROMPT, 'metadata': user()['metadata']}}
    client.get = lambda path, **kwargs: {'data': [queued]} if path.endswith('/inbox') else original(path, **kwargs)
    assert observe(client)['queued']
    requests = []
    client.request = lambda *args, **kwargs: requests.append(args)
    submission = SimpleNamespace(runtime_state={'reader': client, 'native_v2': True,
                                               'native_session_id': 'ses_native', 'native_input_id': 'msg_input'})
    execution.cancel(submission)
    assert requests == [('DELETE', '/api/session/ses_native/inbox/msg_input')]
    client.get = original
    with pytest.raises(NativeStateError, match='cancellation unsupported'):
        execution.cancel(submission)
    assert len(requests) == 1


def test_v2_launcher_binds_same_server_session_and_only_password_reference(monkeypatch, tmp_path):
    from provider_backends.opencode import launcher
    monkeypatch.setenv('CCB_TEST_V2_PASSWORD', 'fixture-private-value')
    monkeypatch.setattr(launcher, 'load_resolved_provider_profile', lambda *_: None)
    monkeypatch.setattr(launcher, '_materialize_opencode_auth', lambda *args, **kwargs: None)
    monkeypatch.setattr(launcher, '_opencode_memory_env', lambda *args: {})
    monkeypatch.setattr(launcher, 'provider_start_parts', lambda *_: ['opencode'])
    spec = SimpleNamespace(name='worker', startup_args=('--session', 'ses_native'), provider_command_template=None)
    context = dict(project_root=str(tmp_path), workspace_path=str(tmp_path),
                   opencode_state_dir=str(tmp_path / 'private'), opencode_api_version=2,
                   opencode_api_url='http://127.0.0.1:42', opencode_api_password_env='CCB_TEST_V2_PASSWORD')
    cmd = launcher.build_start_cmd(SimpleNamespace(), spec, tmp_path, 'launch', prepared_state=context)
    assert '--server http://127.0.0.1:42' in cmd and '--session ses_native' in cmd
    assert context['opencode_session_id'] == 'ses_native'
    assert '${CCB_TEST_V2_PASSWORD}' in cmd and 'fixture-private-value' not in cmd
    spec.startup_args = ('--session', 'ses_native', '--server', 'http://127.0.0.1:43')
    with pytest.raises(RuntimeError, match='same --server'):
        launcher.build_start_cmd(SimpleNamespace(), spec, tmp_path, 'launch', prepared_state=context)
    spec.startup_args = ()
    with pytest.raises(RuntimeError, match='explicit --session'):
        launcher.build_start_cmd(SimpleNamespace(), spec, tmp_path, 'launch', prepared_state=context)


@pytest.mark.parametrize('metadata', [None, [], 'invalid'])
def test_malformed_native_metadata_fails_closed(native, metadata):
    client, records, _ = native
    records[:] = [{**user(), 'metadata': metadata}, assistant(), idle()]
    with pytest.raises(NativeStateError, match='binding mismatch'):
        observe(client)


def test_transport_timeout_counts_continuous_failure_not_task_age(native, monkeypatch):
    client, records, _ = native
    client.submit = lambda *args, **kwargs: {'data': {}}
    monkeypatch.setattr(execution, 'OpenCodeV2State', lambda *args, **kwargs: client)
    prepared = SimpleNamespace(session=SimpleNamespace(work_dir='/workspace'), backend=object(), pane_id='%1')
    submission = execution.start(SimpleNamespace(job_id='job_native2', agent_name='worker'), prepared,
                                 api_url=client.url, session_id='ses_native', prompt=PROMPT, now=NOW)
    original = client.observe
    def unavailable(*args, **kwargs):
        raise NativeTransportError('fixture unavailable')
    client.observe = unavailable
    result = execution.poll(submission, SimpleNamespace(reader=client), now='2026-11-02T01:00:00Z')
    assert result.decision is None
    client.observe = original
    records[:] = [{**user(), 'id': submission.runtime_state['native_input_id']}]
    result = execution.poll(result.submission, SimpleNamespace(reader=client), now='2026-11-02T01:00:20Z')
    assert result.decision is None and 'native_transport_since' not in result.submission.runtime_state
    client.observe = unavailable
    result = execution.poll(result.submission, SimpleNamespace(reader=client), now='2026-11-02T01:00:21Z')
    assert result.decision is None
    result = execution.poll(result.submission, SimpleNamespace(reader=client), now='2026-11-02T01:00:52Z')
    assert result.decision.status is CompletionStatus.INCOMPLETE


def test_service_native_cancellation_never_interrupts_terminal():
    from provider_execution.service import _cancel_submission, interrupt_active_submission
    calls = []
    submission = SimpleNamespace(provider='opencode', runtime_state={
        'native_v2': True, 'backend': SimpleNamespace(send_keys=lambda *args: calls.append(args)), 'pane_id': '%1'})
    def cancel(_submission):
        raise NativeStateError('exact active-turn cancellation unsupported')
    with pytest.raises(NativeStateError, match='unsupported'):
        _cancel_submission(SimpleNamespace(cancel=cancel), submission)
    interrupt_active_submission(submission)
    assert calls == []


def test_queued_cancellation_detects_native_delivery_race(native):
    client, records, _ = native
    original = client.get
    queued = {'id': 'msg_input', 'sessionID': 'ses_native'}
    client.get = lambda path, **kwargs: {'data': [queued]} if path.endswith('/inbox') else original(path, **kwargs)
    def delete(*args, **kwargs):
        records.append(user())  # Native DELETE returns 204 even after consumption.
        return None
    client.request = delete
    submission = SimpleNamespace(runtime_state={'reader': client, 'native_session_id': 'ses_native',
                                                'native_input_id': 'msg_input'})
    with pytest.raises(NativeStateError, match='raced with delivery'):
        execution.cancel(submission)
