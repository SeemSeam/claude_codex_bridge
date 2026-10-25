from __future__ import annotations

import pytest
from types import SimpleNamespace

from mobile_gateway.service import MobileGatewayService, MobileGatewayError


def test_native_status_clock_does_not_change_runtime_fingerprint():
    import copy
    from mobile_gateway.service import _omp_runtime_fingerprint

    runtime = {'observed_at': 'first', 'status_bar': {'available': True, 'observed_at': 'native-first',
               'elapsed_ms': 1000, 'started_at': 'native-start', 'is_streaming': True,
               'step': 'Reading', 'todos': {'available': True, 'done': 0, 'total': 3}}}
    before = copy.deepcopy(runtime)
    baseline = _omp_runtime_fingerprint(runtime)
    changed = copy.deepcopy(runtime)
    changed['observed_at'] = 'second'
    changed['status_bar'].update(observed_at='native-second', elapsed_ms=2000)
    assert _omp_runtime_fingerprint(changed) == baseline
    assert runtime == before
    for key, value in [('step', 'Writing'), ('started_at', 'new-start'), ('is_streaming', False),
                       ('todos', {'available': True, 'done': 1, 'total': 3})]:
        meaningful = copy.deepcopy(changed)
        meaningful['status_bar'][key] = value
        assert _omp_runtime_fingerprint(meaningful) != baseline


def test_native_status_whitelist_session_fences_and_escaped_budget():
    import copy
    import json
    from mobile_gateway.service import _omp_runtime, _omp_runtime_activity

    bar = {'available': True, 'source': 'native_events', 'session_id': 'native',
           'observed_at': '2026-10-23T10:00:01.000Z', 'is_streaming': True,
           'started_at': '2026-10-23T09:59:00.000Z', 'elapsed_ms': 61000,
           'step': 'Reading native state', 'draft': 'secret editor',
           'todos': {'available': True, 'done': 1, 'total': 300, 'in_progress': 1, 'blocked': 0,
                     'items': [{'content': '任' * 256, 'phase': '階' * 128, 'status': 'pending',
                                'raw_result': 'secret tool'} for _ in range(16)], 'truncated': True}}
    snapshot = {'session_id': 'native', 'connected': True, 'ready': True,
                'state': {'isStreaming': False}, 'runtime': {'status_bar': bar}}
    runtime = _omp_runtime(snapshot, observed_at='phone-server-clock')
    assert runtime['is_streaming'] is True
    assert _omp_runtime_activity(runtime) == ('active', 'streaming')
    assert runtime['started_at'] == bar['started_at']
    assert runtime['status_bar']['elapsed_ms'] == 61000
    assert runtime['status_bar']['todos']['total'] == 300
    assert runtime['status_bar']['todos']['done'] == 1
    assert len(json.dumps(runtime).encode()) <= 12 * 1024
    assert 'secret' not in json.dumps(runtime)
    assert _omp_runtime({**snapshot, 'runtime': {}}, observed_at='now')['status_bar'] == {'available': False}
    for key, value in [('session_id', 'foreign'), ('source', 'guest'), ('is_streaming', None)]:
        changed = copy.deepcopy(snapshot)
        changed['runtime']['status_bar'][key] = value
        assert _omp_runtime(changed, observed_at='now')['status_bar'] == {'available': False}
    offline = _omp_runtime({**snapshot, 'connected': False}, observed_at='now')
    assert offline['status_bar'] == {'available': False}
    assert offline['started_at'] is None
    snapshot['runtime']['status_bar']['is_streaming'] = False
    idle = _omp_runtime(snapshot, observed_at='now')
    assert idle['status_bar']['elapsed_ms'] is None
    assert idle['status_bar']['step'] is None
    snapshot['runtime']['status_bar']['todos'] = {'available': True, 'done': 0, 'total': 0,
                                                'in_progress': 0, 'blocked': 0, 'items': [], 'truncated': False}
    assert _omp_runtime(snapshot, observed_at='now')['status_bar']['todos']['total'] == 0


@pytest.fixture(autouse=True)
def linux_runtime(monkeypatch):
    monkeypatch.setattr('provider_backends.omp.platform.sys', SimpleNamespace(platform='linux'))


class FakeTransport:
    def __init__(self):
        self.calls = []
        self.error = None
        self.snapshot = {
            'session_id': 'native', 'generation': 'instance:1', 'revision': 1,
            'connected': True, 'ready': True, 'entries': [],
            'state': {'isStreaming': False},
        }

    def get_snapshot(self, root, agent):
        self.calls.append(('snapshot', root, agent))
        if self.error:
            raise self.error
        return {**self.snapshot, 'entries': self.snapshot['entries'][-128:]}

    def get_history_page(self, root, agent, *, cursor=None, limit=128, max_bytes=262144,
                         expected_session_id=None, expected_generation=None):
        import json
        self.calls.append(('history', root, agent))
        if self.error:
            raise self.error
        records = self.snapshot['entries']
        anchor, end = json.loads(cursor) if cursor else (len(records), len(records))
        start = max(0, end - limit)
        return {**self.snapshot, 'entries': list(reversed(records[start:end])),
                'start_cursor': json.dumps([anchor, end]), 'entry_chunks': [],
                'next_cursor': json.dumps([anchor, start]) if start else None,
                'anchor_leaf_id': records[anchor - 1]['id'] if anchor else None}

    def submit(self, root, agent, body, key, *, expected_session_id, expected_generation):
        self.calls.append(('submit', root, agent, body, key, expected_session_id, expected_generation))
        if self.error:
            raise self.error
        return {'state': 'unconfirmed', 'request_id': key}

    def close_all(self):
        pass


def gateway(tmp_path, monkeypatch, *, agent='mobile', provider='omp'):
    transport = FakeTransport()
    monkeypatch.setattr('mobile_gateway.service.OmpCollabClient', lambda: transport)
    view = {'view': {
        'namespace': {'epoch': 4, 'socket_path': '/tmp/socket', 'session_name': 'session'},
        'agents': [{'name': agent, 'provider': provider, 'pane_id': '%1'}],
    }}

    class Client:
        def project_view(self, **kwargs):
            return view

    terminal = []
    service = MobileGatewayService(
        project_id='project', project_root=tmp_path, ccbd_client_factory=Client,
        mobile_dir=tmp_path / 'mobile',
        terminal_message_sender=lambda *args: terminal.append(args),
    )
    pairing = service.create_pairing_payload(
        gateway_url='http://127.0.0.1:8787', scopes=('view', 'message_submit', 'provider_settings'),
    )
    _, claim = service.dispatch_post('/v1/pairing/claim', {'pairing_code': pairing['pairing_code']})
    service.test_headers = {'Authorization': f'Bearer {claim["device_token"]}'}
    return service, transport, terminal


@pytest.mark.parametrize('agent', ['mobile', 'frontdesk'])
def test_omp_submit_never_uses_terminal_or_frontdesk(tmp_path, monkeypatch, agent):
    service, transport, terminal = gateway(tmp_path, monkeypatch, agent=agent)
    monkeypatch.setattr(service, '_submit_frontdesk_message', lambda **kw: pytest.fail('frontdesk RPC used'))
    result = service._submit_agent_message(
        project_id='project', agent=agent, headers=service.test_headers,
        payload={'namespace_epoch': 4, 'body': 'hello', 'idempotency_key': 'key',
                 'expected_omp_session_id': 'native', 'expected_omp_generation': 'instance:1'},
    )
    assert result['message_submit']['state'] == 'unconfirmed'
    assert result['message_submit']['message']['state'] == 'unconfirmed'
    assert result['message_submit']['message']['source'] == 'mobile/collab'
    assert transport.calls[-1][0] == 'submit'
    assert transport.calls[-1][-2:] == ('native', 'instance:1')
    assert not terminal


def test_omp_queued_receipt_and_snapshot_visibility(tmp_path, monkeypatch):
    service, transport, terminal = gateway(tmp_path, monkeypatch)
    monkeypatch.setattr(transport, 'submit', lambda *args, **kwargs: {
        'state': 'queued', 'request_id': 'request', 'queue_position': 2,
    })
    receipt = service._submit_agent_message(
        project_id='project', agent='mobile', headers=service.test_headers,
        payload={'namespace_epoch': 4, 'body': 'next', 'idempotency_key': 'key',
                 'expected_omp_session_id': 'native', 'expected_omp_generation': 'instance:1'},
    )['message_submit']
    assert receipt['state'] == 'queued'
    assert receipt['queue_position'] == receipt['message']['queue_position'] == 2
    transport.snapshot.update(branch_id='branch-1', pending_inputs=[{
        'idempotency_key': 'key', 'body': 'next', 'state': 'queued',
        'session_id': 'native', 'generation': 'instance:1', 'position': 2,
    }])
    for state in ('queued', 'unconfirmed', 'sent', 'failed', 'held'):
        transport.snapshot['pending_inputs'][0]['state'] = state
        conversation = service.agent_conversation_payload(
            'project', agent='mobile', query={'namespace_epoch': ['4']},
            headers=service.test_headers,
        )['conversation']
        item = conversation['items'][0]
        assert item['id'] == 'key'
        assert item['body'] == 'next'
        assert item['state'] == ('unconfirmed' if state == 'held' else state)
        assert item['source'] == 'mobile/collab'
        assert 'native_turn' not in item
        assert conversation['branch_id'] == 'branch-1'
    assert not terminal


def test_omp_exact_entry_binding_preserves_queue_identity(tmp_path):
    from mobile_gateway.service import _omp_native_conversation_items
    snapshot = {
        'session_id': 'native', 'generation': 'instance:1',
        'entries': [{'type': 'message', 'id': 'entry', 'parentId': None,
                     'message': {'role': 'user', 'content': 'same'}}],
        'state': {}, 'pending_inputs': [
            {'id': 'key', 'body': 'same', 'state': 'sent', 'entry_id': 'entry',
             'session_id': 'native', 'generation': 'instance:1'},
            {'id': 'other', 'body': 'same', 'state': 'queued'},
        ],
    }
    items = _omp_native_conversation_items(tmp_path, project_id='project', agent='mobile', snapshot=snapshot)
    assert [item['id'] for item in items] == ['key', 'other']
    assert items[0]['native_turn']['entries'][0]['id'] == 'entry'
    assert 'native_turn' not in items[1]
    completed = snapshot['pending_inputs'].pop(0)
    snapshot['input_bindings'] = [{key: value for key, value in completed.items()
                                   if key not in {'body', 'state'}}]
    refreshed = _omp_native_conversation_items(
        tmp_path, project_id='project', agent='mobile', snapshot=snapshot,
    )
    assert [item['id'] for item in refreshed] == ['key', 'other']
    snapshot['input_bindings'][0]['generation'] = 'old'
    stale = _omp_native_conversation_items(
        tmp_path, project_id='project', agent='mobile', snapshot=snapshot,
    )
    assert stale[0]['id'] != 'key'


def test_non_omp_submit_keeps_terminal_route(tmp_path, monkeypatch):
    service, transport, terminal = gateway(tmp_path, monkeypatch, provider='pi')
    result = service._submit_agent_message(
        project_id='project', agent='mobile', headers=service.test_headers,
        payload={'namespace_epoch': 4, 'body': 'hello', 'idempotency_key': 'key'},
    )
    assert result['message_submit']['state'] == 'sent'
    assert len(terminal) == 1
    assert not transport.calls


def test_macos_omp_submit_keeps_legacy_terminal_route(tmp_path, monkeypatch):
    monkeypatch.setattr('provider_backends.omp.platform.sys', SimpleNamespace(platform='darwin'))
    service, transport, terminal = gateway(tmp_path, monkeypatch)
    result = service._submit_agent_message(
        project_id='project', agent='mobile', headers=service.test_headers,
        payload={'namespace_epoch': 4, 'body': 'legacy', 'idempotency_key': 'mac-key'},
    )
    assert result['message_submit']['state'] == 'sent'
    assert len(terminal) == 1
    assert not transport.calls


def test_omp_conversation_always_obtains_ready_live_snapshot(tmp_path, monkeypatch):
    service, transport, _ = gateway(tmp_path, monkeypatch)
    query = {'namespace_epoch': ['4']}
    first = service.agent_conversation_payload('project', agent='mobile', query=query, headers=service.test_headers)
    transport.snapshot['revision'] = 2
    second = service.agent_conversation_payload('project', agent='mobile', query=query, headers=service.test_headers)
    assert first['conversation']['revision'] == 1
    assert second['conversation']['revision'] == 2
    assert second['conversation']['transport'] == 'collab'
    assert [call[0] for call in transport.calls] == ['snapshot', 'history', 'snapshot', 'history']
    transport.snapshot['ready'] = False
    with pytest.raises(MobileGatewayError) as error:
        service.agent_conversation_payload('project', agent='mobile', query=query, headers=service.test_headers)
    assert error.value.status_code == 503


@pytest.mark.parametrize('operation', ['submit', 'snapshot'])
def test_omp_unknown_error_is_explicit_and_does_not_leak(tmp_path, monkeypatch, operation):
    service, transport, terminal = gateway(tmp_path, monkeypatch)
    transport.error = RuntimeError('wss://secret/r/key.write-token')
    with pytest.raises(MobileGatewayError) as error:
        if operation == 'submit':
            service._submit_agent_message(
                project_id='project', agent='mobile', headers=service.test_headers,
                payload={'namespace_epoch': 4, 'body': 'hello', 'idempotency_key': 'key',
                         'expected_omp_session_id': 'native', 'expected_omp_generation': 'instance:1'},
            )
        else:
            service.agent_conversation_payload('project', agent='mobile', query={'namespace_epoch': ['4']}, headers=service.test_headers)
    assert error.value.status_code == 502
    assert 'secret' not in str(error.value)
    assert not terminal


def test_omp_stale_namespace_blocks_transport(tmp_path, monkeypatch):
    service, transport, _ = gateway(tmp_path, monkeypatch)
    with pytest.raises(MobileGatewayError) as error:
        service._submit_agent_message(
            project_id='project', agent='mobile', headers=service.test_headers,
            payload={'namespace_epoch': 3, 'body': 'hello', 'idempotency_key': 'key'},
        )
    assert error.value.status_code == 409
    assert not transport.calls


def test_omp_preserves_attachment_paths_in_prompt(tmp_path, monkeypatch):
    service, transport, _ = gateway(tmp_path, monkeypatch)
    service._submit_agent_message(
        project_id='project', agent='mobile', headers=service.test_headers,
        payload={
            'namespace_epoch': 4, 'body': 'inspect', 'idempotency_key': 'attachment-key',
            'expected_omp_session_id': 'native', 'expected_omp_generation': 'instance:1',
            'attachments': [{'file_id': 'file', 'file_name': 'report.txt',
                             'project_relative_path': '.ccb/uploads/report.txt'}],
        },
    )
    assert '.ccb/uploads/report.txt' in transport.calls[-1][3]
    assert [call[0] for call in transport.calls] == ['snapshot', 'submit']


def test_omp_authentication_precedes_transport(tmp_path, monkeypatch):
    service, transport, _ = gateway(tmp_path, monkeypatch)
    with pytest.raises(MobileGatewayError):
        service._submit_agent_message(
            project_id='project', agent='mobile', headers=None,
            payload={'namespace_epoch': 4, 'body': 'hello', 'idempotency_key': 'key'},
        )
    with pytest.raises(MobileGatewayError):
        service.agent_conversation_payload('project', agent='mobile', query={'namespace_epoch': ['4']})
    assert not transport.calls


def test_omp_unavailable_is_503_without_history_fallback(tmp_path, monkeypatch):
    from provider_backends.omp.collab import OmpCollabError
    service, transport, _ = gateway(tmp_path, monkeypatch)
    transport.error = OmpCollabError('secret capability', status_code=503)
    with pytest.raises(MobileGatewayError) as error:
        service.agent_conversation_payload(
            'project', agent='mobile', query={'namespace_epoch': ['4']}, headers=service.test_headers,
        )
    assert error.value.status_code == 503
    assert 'Reconnect' in str(error.value)
    assert 'secret' not in str(error.value)


@pytest.mark.parametrize('identity', [
    {},
    {'expected_omp_session_id': 'native'},
    {'expected_omp_generation': 'instance:1'},
    {'expected_omp_session_id': 'old', 'expected_omp_generation': 'instance:1'},
    {'expected_omp_session_id': 'native', 'expected_omp_generation': 'instance:0'},
])
def test_omp_missing_or_stale_caller_identity_never_submits(tmp_path, monkeypatch, identity):
    service, transport, terminal = gateway(tmp_path, monkeypatch)
    with pytest.raises(MobileGatewayError) as error:
        service._submit_agent_message(
            project_id='project', agent='mobile', headers=service.test_headers,
            payload={'namespace_epoch': 4, 'body': 'hello', 'idempotency_key': 'key', **identity},
        )
    assert error.value.status_code == 409
    assert not any(call[0] == 'submit' for call in transport.calls)
    assert not terminal


def native_models():
    return {
        'session_id': 'native', 'generation': 'instance:1', 'model_revision': 'opaque-1',
        'model': {'provider': 'test', 'id': 'old'}, 'thinking_level': 'off',
        'models': [{'provider': 'test', 'id': 'old', 'name': 'Old', 'reasoning_levels': ['off']},
                   {'provider': 'test', 'id': 'new', 'name': 'New', 'reasoning_levels': ['off', 'high'],
                    'apiKey': 'secret', 'baseUrl': 'https://secret'}],
        'credentials': 'secret',
    }


def setup_models(service, transport, monkeypatch):
    native = native_models()
    monkeypatch.setattr(transport, 'get_models', lambda *args: dict(native), raising=False)
    def set_model(root, agent, model, thinking, **expected):
        transport.calls.append(('set_model', model, thinking, expected))
        return {**native, 'model': model, 'thinking_level': thinking, 'model_revision': 'opaque-2'}
    monkeypatch.setattr(transport, 'set_model', set_model, raising=False)
    control = service.agent_provider_control_payload('project', agent='mobile', headers=service.test_headers)
    payload = {
        'idempotency_key': 'model-change-123', 'expected_namespace_epoch': 4,
        'expected_provider': 'omp', 'expected_revision': control['config_revision'],
        'expected_runtime_revision': control['provider_control']['runtime_revision'],
        'model': 'test/new', 'thinking': 'high',
    }
    return native, control, payload


def test_omp_native_model_control_and_confirmed_mutation(tmp_path, monkeypatch):
    import json
    service, transport, terminal = gateway(tmp_path, monkeypatch)
    native, control, payload = setup_models(service, transport, monkeypatch)
    assert control['provider_control']['mutation_mode'] == 'native'
    assert control['provider_catalog']['model_shortcut'] is True
    assert 'secret' not in json.dumps(control)
    monkeypatch.setattr(service._provider_settings_store, 'apply', lambda **kw: pytest.fail('config write'))
    result = service.update_agent_provider_settings(
        'project', agent='mobile', payload=payload, headers=service.test_headers,
    )
    assert result['status'] == 'applied'
    assert result['active_model'] == 'test/new'
    assert result['restart_required'] is False
    assert result['mutation_mode'] == 'native'
    assert transport.calls[-1][3]['expected_generation'] == 'instance:1'
    assert transport.calls[-1][3]['expected_model_revision'] == 'opaque-1'
    assert not terminal
    assert service.update_agent_provider_settings(
        'project', agent='mobile', payload=payload, headers=service.test_headers,
    ) == result
    assert len(transport.calls) == 1


@pytest.mark.parametrize('change', ['generation', 'model', 'thinking_level', 'model_revision'])
def test_omp_model_stale_runtime_rejected(tmp_path, monkeypatch, change):
    service, transport, _ = gateway(tmp_path, monkeypatch)
    native, _, payload = setup_models(service, transport, monkeypatch)
    native[change] = {'provider': 'test', 'id': 'other'} if change == 'model' else 'changed'
    with pytest.raises(MobileGatewayError) as error:
        service.update_agent_provider_settings('project', agent='mobile', payload=payload, headers=service.test_headers)
    assert error.value.status_code == 409
    assert not transport.calls


def test_omp_model_busy_and_unconfirmed_are_not_applied(tmp_path, monkeypatch):
    from provider_backends.omp.collab import OmpCollabError
    service, transport, _ = gateway(tmp_path, monkeypatch)
    native, _, payload = setup_models(service, transport, monkeypatch)
    def busy(*args, **kwargs):
        raise OmpCollabError('secret capability', 409)
    monkeypatch.setattr(transport, 'set_model', busy)
    with pytest.raises(MobileGatewayError) as error:
        service.update_agent_provider_settings('project', agent='mobile', payload=payload, headers=service.test_headers)
    assert error.value.status_code == 409
    assert 'secret' not in str(error.value)
    monkeypatch.setattr(transport, 'set_model', lambda *args, **kwargs: native)
    with pytest.raises(MobileGatewayError) as error:
        service.update_agent_provider_settings('project', agent='mobile', payload=payload, headers=service.test_headers)
    assert error.value.status_code == 502


def test_project_view_advertises_omp_controls_and_observes_native_runtime(tmp_path, monkeypatch):
    service, transport, _ = gateway(tmp_path, monkeypatch)
    payload = service.project_view_payload('project')
    control = payload['view']['agents'][0]['provider_control']
    assert control['provider'] == 'omp'
    assert control['mutation_mode'] == 'native'
    assert control['capabilities']['model_select'] is True
    assert [call[0] for call in transport.calls] == ['snapshot']
    assert payload['view']['agents'][0]['omp_runtime']['is_streaming'] is False


def test_darwin_omp_submit_uses_terminal_without_collab_fence(tmp_path, monkeypatch):
    monkeypatch.setattr('provider_backends.omp.platform.sys', SimpleNamespace(platform='darwin'))
    service, transport, terminal = gateway(tmp_path, monkeypatch)
    assert service._omp_collab is None
    result = service._submit_agent_message(
        project_id='project', agent='mobile', headers=service.test_headers,
        payload={'namespace_epoch': 4, 'body': 'hello', 'idempotency_key': 'key'},
    )
    assert result['message_submit']['state'] == 'sent'
    assert len(terminal) == 1
    assert not transport.calls


def test_darwin_omp_frontdesk_keeps_legacy_rpc(tmp_path, monkeypatch):
    monkeypatch.setattr('provider_backends.omp.platform.sys', SimpleNamespace(platform='darwin'))
    service, transport, terminal = gateway(tmp_path, monkeypatch, agent='frontdesk')
    calls = []
    def submit(**kwargs):
        calls.append(kwargs)
        return {'job_id': 'job', 'status': 'accepted'}
    monkeypatch.setattr(service, '_submit_frontdesk_message', submit)
    result = service._submit_agent_message(
        project_id='project', agent='frontdesk', headers=service.test_headers,
        payload={'namespace_epoch': 4, 'body': 'hello', 'idempotency_key': 'key'},
    )
    assert result['message_submit']['job_id'] == 'job'
    assert len(calls) == 1
    assert not terminal and not transport.calls


def test_darwin_omp_jsonl_history_and_cache(tmp_path, monkeypatch):
    import json
    import mobile_gateway.service as module
    monkeypatch.setattr('provider_backends.omp.platform.sys', SimpleNamespace(platform='darwin'))
    service, transport, _ = gateway(tmp_path, monkeypatch)
    transcript = tmp_path / 'native.jsonl'
    records = [
        {'type': 'session', 'id': 'native', 'cwd': str(tmp_path)},
        {'type': 'message', 'id': 'user', 'parentId': None,
         'message': {'role': 'user', 'content': 'legacy question'}},
        {'type': 'message', 'id': 'answer', 'parentId': 'user',
         'message': {'role': 'assistant', 'content': 'legacy answer'}},
    ]
    transcript.write_text('\n'.join(json.dumps(record) for record in records) + '\n')
    def paths(root, *, agent, provider):
        assert provider == 'omp'
        return [transcript]
    monkeypatch.setattr(module, '_pi_family_native_session_paths', paths)
    monkeypatch.setattr(module, '_omp_native_conversation_items', lambda *a, **kw: pytest.fail('Collab history used'))
    native = module._agent_native_conversation_items(tmp_path, project_id='project', agent='mobile', provider='omp')
    assert [item['body'] for item in native.items] == ['legacy question', 'legacy answer']
    fingerprint = module._agent_native_conversation_cache_fingerprint(tmp_path, agent='mobile', provider='omp')
    assert fingerprint and fingerprint[0][0] == str(transcript)
    response = service.agent_conversation_payload('project', agent='mobile', query={'namespace_epoch': ['4']}, headers=service.test_headers)
    assert 'transport' not in response['conversation']
    assert not transport.calls


def test_darwin_omp_model_readback_does_not_use_native_client(tmp_path, monkeypatch):
    monkeypatch.setattr('provider_backends.omp.platform.sys', SimpleNamespace(platform='darwin'))
    service, transport, _ = gateway(tmp_path, monkeypatch)
    monkeypatch.setattr(service, '_omp_model_snapshot', lambda *a: pytest.fail('native models used'))
    control = service.agent_provider_control_payload('project', agent='mobile', headers=service.test_headers)
    assert control['provider_control'].get('runtime_source') != 'collab'
    assert control['provider_control'].get('mutation_mode') != 'native'
    assert not transport.calls


def test_darwin_project_view_preserves_existing_provider_controls(monkeypatch):
    from mobile_gateway.service import _redact_project_view_payload
    monkeypatch.setattr('provider_backends.omp.platform.sys', SimpleNamespace(platform='darwin'))
    control = {'provider': 'omp', 'active_model': 'legacy',
               'capabilities': {'model_select': False}, 'mutation_mode': 'restart'}
    payload = {'view': {'agents': [{'provider': 'omp', 'provider_control': control}]}}
    assert _redact_project_view_payload(payload)['view']['agents'][0]['provider_control'] == control


def test_omp_original_content_uses_authenticated_conversation_and_fences(tmp_path, monkeypatch):
    service, transport, _ = gateway(tmp_path, monkeypatch)
    transport.snapshot['entries'] = [
        {'type': 'message', 'id': 'u', 'parentId': None,
         'message': {'role': 'user', 'content': 'question'}},
        {'type': 'message', 'id': 'a', 'parentId': 'u',
         'message': {'role': 'assistant', 'content': '中文😀' * 100000}},
    ]
    query = {'namespace_epoch': ['4']}
    response = service.agent_conversation_payload('project', agent='mobile', query=query, headers=service.test_headers)
    cursor = response['conversation']['items'][-1]['native_turn']['read_cursor']
    query['cursor'] = [cursor]
    response = service.agent_conversation_payload('project', agent='mobile', query=query, headers=service.test_headers)
    assert response['conversation']['items'] == []
    assert 'next_cursor' not in response['conversation']
    assert response['conversation']['native_content']['next_cursor']
    content = response['conversation']['native_content']
    assert content['schema_version'] == 2
    assert content['page_index'] == 0
    assert content['total_blocks'] == 1
    fragments = []
    while True:
        fragments.extend(block['text'] for block in content['blocks'])
        assert all(block['entry_id'] == 'a' and block['type'] == 'text'
                   for block in content['blocks'])
        if not content['next_cursor']:
            break
        next_page = service.agent_conversation_payload('project', agent='mobile',
            query={'namespace_epoch': ['4'], 'cursor': [content['next_cursor']]},
            headers=service.test_headers)['conversation']['native_content']
        assert next_page['page_index'] == content['page_index'] + 1
        assert next_page['version'] == content['version']
        content = next_page
    assert ''.join(fragments) == '中文😀' * 100000
    with pytest.raises(MobileGatewayError) as error:
        service.agent_conversation_payload('project', agent='mobile', query=query, headers={})
    assert error.value.status_code == 401
    transport.snapshot['generation'] = 'instance:2'
    with pytest.raises(MobileGatewayError) as error:
        service.agent_conversation_payload('project', agent='mobile', query=query, headers=service.test_headers)
    assert error.value.status_code == 409


@pytest.mark.parametrize('cursor', ['', 'not-a-cursor', 'omp-content-v1.%%%', 'omp-content-v2.%%%'])
def test_omp_invalid_conversation_cursor_is_bad_request(tmp_path, monkeypatch, cursor):
    service, _, _ = gateway(tmp_path, monkeypatch)
    with pytest.raises(MobileGatewayError) as error:
        service.agent_conversation_payload('project', agent='mobile',
            query={'namespace_epoch': ['4'], 'cursor': [cursor]}, headers=service.test_headers)
    assert error.value.status_code == 400


def test_native_history_traverses_large_session_without_replica_projection(tmp_path, monkeypatch):
    service, transport, _ = gateway(tmp_path, monkeypatch)
    records = []
    for index in range(14010):
        parent = records[-1]['id'] if records else None
        records.extend([
            {'type': 'message', 'id': f'u{index}', 'parentId': parent,
             'message': {'role': 'user', 'content': f'question {index}'}},
            {'type': 'message', 'id': f'a{index}', 'parentId': f'u{index}',
             'message': {'role': 'assistant', 'content': f'answer {index} ' + 'x' * 3700,
                         'stopReason': 'stop'}},
        ])
    transport.snapshot.update(entries=records, branch_id='branch',
                              state={'leafId': records[-1]['id'], 'isStreaming': False})
    import json
    assert len(json.dumps(records).encode()) > 48 * 1024 * 1024
    query = {'namespace_epoch': ['4'], 'limit': ['50']}
    seen = set()
    first_cursor = None
    while True:
        page = service.agent_conversation_payload(
            'project', agent='mobile', query=query, headers=service.test_headers)['conversation']
        assert page['items']
        for item in page['items']:
            assert item['id'] not in seen
            seen.add(item['id'])
            if item['kind'] == 'agent_reply':
                assert item['native_turn']['status'] == 'completed'
        cursor = page.get('next_cursor')
        if first_cursor is None:
            first_cursor = cursor
        if cursor is None:
            break
        query['cursor'] = [cursor]
    assert len(seen) == len(records)
    records.append({'type': 'message', 'id': 'appended', 'parentId': records[-1]['id'],
                    'message': {'role': 'user', 'content': 'new'}})
    transport.snapshot['state']['leafId'] = 'appended'
    query['cursor'] = [first_cursor]
    replay = service.agent_conversation_payload(
        'project', agent='mobile', query=query, headers=service.test_headers)['conversation']
    assert replay['items']
    assert all(item['id'] in seen for item in replay['items'])
    transport.snapshot['branch_id'] = 'other-branch'
    with pytest.raises(MobileGatewayError) as error:
        service.agent_conversation_payload(
            'project', agent='mobile', query=query, headers=service.test_headers)
    assert error.value.status_code == 409


def test_history_append_race_does_not_merge_stale_stream_or_pending(tmp_path, monkeypatch):
    service, transport, _ = gateway(tmp_path, monkeypatch)
    old = {'type': 'message', 'id': 'old', 'parentId': None,
           'message': {'role': 'user', 'content': 'old question'}}
    appended = {'type': 'message', 'id': 'new', 'parentId': 'old',
                'message': {'role': 'user', 'content': 'new question'}}
    transport.snapshot.update(
        entries=[old], branch_id='branch',
        state={'leafId': 'old', 'isStreaming': True},
        streaming_message={'role': 'assistant', 'content': 'stale stream'}, stream_id='stream',
        pending_inputs=[{'idempotency_key': 'receipt', 'body': 'new question',
                         'state': 'sent', 'entry_id': 'new',
                         'session_id': 'native', 'generation': 'instance:1'}],
    )
    original_history = transport.get_history_page

    def appended_history(*args, **kwargs):
        transport.snapshot['entries'] = [old, appended]
        return original_history(*args, **kwargs)

    monkeypatch.setattr(transport, 'get_history_page', appended_history)
    conversation = service.agent_conversation_payload(
        'project', agent='mobile', query={'namespace_epoch': ['4']},
        headers=service.test_headers)['conversation']
    items = conversation['items']
    assert [item['body'] for item in items] == ['old question', 'new question']
    assert [item['id'] for item in items].count('receipt') == 1
    assert items[-1]['id'] == 'receipt'
    assert all(item['kind'] == 'user_message' for item in items)


@pytest.mark.parametrize('state,expected,reason', [
    ({'isStreaming': True}, 'active', 'streaming'),
    ({'isStreaming': False, 'isAborting': True}, 'active', 'aborting'),
    ({'isStreaming': False, 'queuedMessageCount': 2}, 'pending', 'native_queue'),
    ({'isStreaming': False}, 'idle', 'native_idle'),
    ({}, 'unknown', 'native_state_missing'),
])
def test_project_view_native_activity_transitions(tmp_path, monkeypatch, state, expected, reason):
    service, transport, _ = gateway(tmp_path, monkeypatch)
    transport.snapshot['state'] = state
    record = service.project_view_payload('project')['view']['agents'][0]
    assert record['activity_state'] == expected
    assert record['activity_source'] == 'native_collab'
    assert record['activity_reason'] == reason
    assert record['omp_runtime']['started_at'] is None


def test_project_runtime_disconnect_isolated_from_other_agents(tmp_path, monkeypatch):
    service, transport, _ = gateway(tmp_path, monkeypatch)
    project = service._require_project('project')
    other = {'name': 'other', 'provider': 'pi', 'activity_state': 'active', 'activity_source': 'pane'}
    healthy = {'name': 'healthy', 'provider': 'omp'}
    payload = {'view': {'agents': [{'name': 'mobile', 'provider': 'omp'}, healthy, other]}}
    def snapshot(root, agent):
        if agent == 'mobile':
            raise RuntimeError('secret/path')
        return {**transport.snapshot, 'state': {'isStreaming': False}}
    monkeypatch.setattr(transport, 'get_snapshot', snapshot)
    records = service._with_omp_runtime(project, payload)['view']['agents']
    assert records[0]['activity_state'] == 'unknown'
    assert records[0]['omp_runtime']['connected'] is False
    assert records[1]['activity_state'] == 'idle'
    assert records[2] == other
    assert 'omp_runtime' not in payload['view']['agents'][0]


@pytest.mark.parametrize('connected,ready', [(False, False), (False, True), (True, False)])
def test_stale_busy_runtime_does_not_mark_disconnected_agent_active(tmp_path, monkeypatch, connected, ready):
    service, transport, _ = gateway(tmp_path, monkeypatch)
    transport.snapshot.update(connected=connected, ready=ready,
        runtime={'is_streaming': True, 'is_aborting': True, 'queued_message_count': 4})
    record = service.project_view_payload('project')['view']['agents'][0]
    assert record['activity_state'] == 'unknown'
    assert not record['omp_runtime']['is_streaming']
    assert record['omp_runtime']['is_aborting'] is False
    assert record['omp_runtime']['queued_message_count'] == 0


def test_runtime_watch_tracks_tasks_revision_and_disconnect_not_observation_time(tmp_path, monkeypatch):
    service, transport, _ = gateway(tmp_path, monkeypatch)
    monkeypatch.setattr(service, '_agent_activity_probe', lambda **kw: pytest.fail('pane probe used'))
    service._register_invalidation_watch({
        'watch_project_id': ['project'], 'watch_agent': ['mobile'],
        'watch_provider': ['omp'], 'watch_namespace_epoch': ['4'],
    })
    def watch():
        return service._selected_invalidation_watch_snapshots()[0]
    initial = watch()
    monkeypatch.setattr(service, '_clock', lambda: '2026-10-23T12:00:01Z')
    assert watch().conversation_fingerprint == initial.conversation_fingerprint
    transport.snapshot['runtime'] = {'tasks': [
        {'id': 'task', 'agent': 'worker', 'status': 'running', 'updated_at': '2026-10-23T12:00:00Z'}]}
    running = watch()
    assert running.conversation_fingerprint != initial.conversation_fingerprint
    assert running.activity_state == 'idle'
    transport.snapshot['runtime']['tasks'][0]['status'] = 'completed'
    completed = watch()
    assert completed.conversation_fingerprint != running.conversation_fingerprint
    transport.snapshot['revision'] += 1
    revised = watch()
    assert revised.conversation_fingerprint != completed.conversation_fingerprint
    transport.error = RuntimeError('offline')
    disconnected = watch()
    assert disconnected.activity_state == 'unknown'
    assert disconnected.conversation_fingerprint != revised.conversation_fingerprint


def test_history_page_reports_current_runtime_without_grafting_live_items(tmp_path, monkeypatch):
    service, transport, _ = gateway(tmp_path, monkeypatch)
    records = []
    for index in range(4):
        records.extend([
            {'type': 'message', 'id': f'u{index}', 'parentId': records[-1]['id'] if records else None,
             'message': {'role': 'user', 'content': f'question {index}'}},
            {'type': 'message', 'id': f'a{index}', 'parentId': f'u{index}',
             'message': {'role': 'assistant', 'content': f'answer {index}', 'stopReason': 'stop'}},
        ])
    transport.snapshot.update(entries=records, branch_id='branch',
                              state={'leafId': records[-1]['id'], 'isStreaming': False})
    query = {'namespace_epoch': ['4'], 'limit': ['2']}
    latest = service.agent_conversation_payload(
        'project', agent='mobile', query=query, headers=service.test_headers)['conversation']
    assert latest['next_cursor']
    transport.snapshot['state']['isStreaming'] = True
    transport.snapshot['runtime'] = {
        'is_streaming': True, 'started_at': '2026-10-23T12:00:00Z',
        'tasks': [{'id': 'native-task', 'agent': 'worker', 'status': 'running',
                   'updated_at': '2026-10-23T12:00:00Z', 'raw_progress': 'secret'}],
    }
    transport.snapshot.update(streaming_message={'role': 'assistant', 'content': 'live only'},
                              stream_id='live')
    query['cursor'] = [latest['next_cursor']]
    older = service.agent_conversation_payload(
        'project', agent='mobile', query=query, headers=service.test_headers)['conversation']
    assert older['omp_runtime']['is_streaming'] is True
    assert older['omp_runtime']['started_at'] == '2026-10-23T12:00:00Z'
    assert older['omp_runtime']['tasks'][0]['id'] == 'native-task'
    assert 'raw_progress' not in older['omp_runtime']['tasks'][0]
    assert all(item.get('body') != 'live only' for item in older['items'])


@pytest.mark.parametrize('platform,provider', [('darwin', 'omp'), ('linux', 'pi')])
def test_project_view_runtime_keeps_other_providers_and_macos_unchanged(tmp_path, monkeypatch, platform, provider):
    monkeypatch.setattr('provider_backends.omp.platform.sys', SimpleNamespace(platform=platform))
    service, transport, _ = gateway(tmp_path, monkeypatch, provider=provider)
    record = service.project_view_payload('project')['view']['agents'][0]
    assert 'omp_runtime' not in record
    assert 'activity_source' not in record
    assert not transport.calls


def test_runtime_metadata_is_bounded_and_prioritizes_active_work():
    import json
    from mobile_gateway.service import _omp_runtime
    tasks = [{'id': f'finished-{index}', 'agent': 'worker', 'status': 'completed',
              'description': '中' * 1000, 'updated_at': '2026-10-23T12:00:00Z',
              'raw_progress': '/private/session'} for index in range(140)]
    tasks.append({'id': 'active-task', 'agent': 'worker', 'status': 'running',
                  'description': 'current work', 'updated_at': '2026-10-23T12:00:01Z'})
    runtime = _omp_runtime({'connected': True, 'ready': True, 'runtime': {
        'is_streaming': False, 'tasks': tasks,
        'model': {'provider': 'native', 'id': 'model', 'apiKey': 'secret'},
        'context_usage': {'tokens': 10, 'contextWindow': 100, 'percent': 10},
        'agents': [{'id': 'worker', 'displayName': 'Worker', 'status': 'running',
                    'sessionFile': '/private/session', 'hasSessionFile': True}],
    }}, observed_at='2026-10-23T12:00:01Z')
    assert len(json.dumps(runtime).encode('utf-8')) <= 12 * 1024
    assert runtime['truncated'] is True
    assert runtime['tasks'][0]['id'] == 'active-task'
    assert runtime['agents'][0]['id'] == 'worker'
    assert runtime['context_usage']['percent'] == 10
    assert 'secret' not in json.dumps(runtime)
    assert '/private/session' not in json.dumps(runtime)


def test_unwrapped_view_observes_runtime_without_stale_queue_fallback(tmp_path, monkeypatch):
    from mobile_gateway.service import _project_activity_summary_from_view
    service, transport, _ = gateway(tmp_path, monkeypatch)
    payload = {'agents': [{'name': 'mobile', 'provider': 'omp',
                          'activity_state': 'active', 'queue_depth': 5}]}
    transport.snapshot['state'] = {'isStreaming': False, 'contextUsage': {
        'tokens': 20, 'contextWindow': 100, 'percent': 20}}
    project = service._require_project('project')
    idle = service._with_omp_runtime(project, payload)
    assert idle['agents'][0]['activity_state'] == 'idle'
    assert idle['agents'][0]['omp_runtime']['context_usage']['tokens'] == 20
    assert not _project_activity_summary_from_view(idle).get('has_working_agents')
    transport.error = RuntimeError('disconnected')
    disconnected = service._with_omp_runtime(project, payload)
    assert disconnected['agents'][0]['activity_state'] == 'unknown'
    assert not _project_activity_summary_from_view(disconnected).get('has_working_agents')


@pytest.mark.parametrize('streaming', [True, False])
def test_server_project_activity_refresh_uses_native_runtime(tmp_path, monkeypatch, streaming):
    service, transport, _ = gateway(tmp_path, monkeypatch)
    transport.snapshot['state'] = {'isStreaming': streaming}
    summaries = []
    monkeypatch.setattr(service, '_project_activity_store', SimpleNamespace(
        record_summary=lambda **kwargs: summaries.append(kwargs['summary'])))
    service._refresh_server_project_activity(service._require_project('project'))
    assert len(summaries) == 1
    assert bool(summaries[0].get('has_working_agents')) is streaming
    assert summaries[0].get('working_agent_count', 0) == int(streaming)


def test_project_activity_refresh_clears_cached_working_count(tmp_path, monkeypatch):
    import time
    service, transport, _ = gateway(tmp_path, monkeypatch)
    transport.snapshot['state'] = {'isStreaming': False}
    monkeypatch.setattr(service, '_project_activity_store', SimpleNamespace(
        project=lambda project_id: {'has_working_agents': True, 'working_agent_count': 2},
        record_summary=lambda **kwargs: None))
    monkeypatch.setattr(service, '_request_project_view_with_timeout',
                        lambda *args, **kwargs: {'view': {'agents': [
                            {'name': 'mobile', 'provider': 'omp', 'activity_state': 'active'}]}})
    summary, refreshed = service._project_activity_summary(
        service._require_project('project'), allow_refresh=True, deadline=time.monotonic() + 10)
    assert refreshed is True
    assert not summary.get('has_working_agents')
    assert summary.get('working_agent_count', 0) == 0


def test_runtime_projects_semantic_progress_and_optional_task_metrics():
    from mobile_gateway.service import _omp_runtime, _omp_runtime_activity, _omp_runtime_fingerprint
    native = {'progress_available': True, 'is_streaming': False,
              'activities': [{'id': 'tool:call', 'kind': 'tool', 'status': 'completed',
                              'title': 'Inspect schema', 'text': 'Current output', 'tool_name': 'read',
                              'updated_at': '2026-10-23T12:00:00Z', 'args': {'secret': 'hidden'}}],
              'tasks': [{'id': 'child', 'status': 'running', 'task': 'Inspect subsystem', 'intent': 'Read schema',
                         'stage': 'Inspecting', 'progress_text': 'Latest result', 'completion_percent': 42,
                         'tool_count': 3, 'requests': 2, 'tokens': 900, 'context_tokens': 200, 'context_window': 1000,
                         'currentToolArgs': 'hidden', 'tokens_invalid': float('inf')} ]}
    snapshot = {'connected': True, 'ready': True, 'runtime': native}
    runtime = _omp_runtime(snapshot, observed_at='now')
    assert runtime['progress_available'] is True
    assert runtime['activities'][0]['title'] == 'Inspect schema'
    assert 'args' not in runtime['activities'][0]
    task = runtime['tasks'][0]
    for key in ('task', 'intent', 'stage', 'progress_text', 'completion_percent', 'tool_count', 'requests',
                'tokens', 'context_tokens', 'context_window'):
        assert task[key] == native['tasks'][0][key]
    assert 'currentToolArgs' not in task
    assert _omp_runtime_activity(runtime) == ('idle', 'native_idle')
    initial = _omp_runtime_fingerprint(runtime)
    native['activities'][0]['text'] = 'Updated before result'
    assert _omp_runtime_fingerprint(_omp_runtime(snapshot, observed_at='now')) != initial
    native['tasks'][0].update(completion_percent=101, requests=True, tokens=float('inf'), context_tokens=-1)
    task = _omp_runtime(snapshot, observed_at='now')['tasks'][0]
    assert all(key not in task for key in ('completion_percent', 'requests', 'tokens', 'context_tokens'))


def test_old_bridge_runtime_explicitly_reports_progress_unavailable():
    from mobile_gateway.service import _omp_runtime
    for native in (None, {}, {'is_streaming': True}):
        runtime = _omp_runtime({'connected': True, 'ready': True, 'runtime': native}, observed_at='now')
        assert runtime['progress_available'] is False
        assert runtime['activities'] == []


def test_unicode_activity_budget_keeps_current_semantic_progress_before_agent_history():
    import json
    from mobile_gateway.service import _omp_runtime
    native = {'progress_available': True,
              'activities': [{'id': 'tool:' + 'x' * 128, 'kind': 'tool', 'status': 'running',
                              'title': '😀' * 512, 'text': '😀' * 768, 'updated_at': 'now'}],
              'agents': [{'id': str(index), 'status': 'parked', 'displayName': '😀' * 128} for index in range(128)]}
    runtime = _omp_runtime({'connected': True, 'ready': True, 'runtime': native}, observed_at='now')
    assert len(json.dumps(runtime).encode()) <= 12 * 1024
    assert runtime['activities'][0]['id'] == 'tool:' + 'x' * 128
    assert runtime['activities'][0]['status'] == 'running'
    assert runtime['activities'][0]['text']
    assert runtime['truncated'] is True
    disconnected = _omp_runtime({'connected': False, 'ready': False, 'runtime': native}, observed_at='now')
    assert disconnected['activities'][0]['status'] == 'aborted'
    assert disconnected['started_at'] is None


def test_ui_response_route_preserves_fences_and_isolates_message_submit(tmp_path, monkeypatch):
    service, transport, terminal = gateway(tmp_path, monkeypatch)
    calls = []
    def respond(root, agent, req_id, value, **fences):
        calls.append((agent, req_id, value, fences))
        return {'req_id': req_id}
    monkeypatch.setattr(transport, 'ui_response', respond, raising=False)
    payload = {'namespace_epoch': 4, 'expected_omp_session_id': 'native',
               'expected_omp_generation': 'instance:1', 'req_id': 7, 'value': 'Answer'}
    status, result = service.dispatch_post('/v1/projects/project/agents/mobile/ui-response', payload,
                                           service.test_headers)
    assert status == 200
    assert result['ui_response'] == {'req_id': 7}
    assert calls == [('mobile', 7, 'Answer', {'expected_session_id': 'native',
                                           'expected_generation': 'instance:1'})]
    assert not terminal
    assert not transport.calls
    transport.snapshot['runtime'] = {'pending_ui_request': {'req_id': 7}}
    with pytest.raises(MobileGatewayError, match='active OMP prompt'):
        service._submit_agent_message(project_id='project', agent='mobile', headers=service.test_headers,
                                      payload={**payload, 'body': 'not an answer', 'idempotency_key': 'key'})
    assert not any(call[0] == 'submit' for call in transport.calls)


@pytest.mark.parametrize('changes', [ {'req_id': True}, {'req_id': -1}, {'req_id': 1.5},
    {'req_id': 9007199254740992}, {'value': 'x' * 513}, {'value': []},
    {'expected_omp_session_id': None}, {'expected_omp_generation': ''}, {'namespace_epoch': 3}])
def test_ui_response_rejects_invalid_and_stale_requests(tmp_path, monkeypatch, changes):
    service, transport, _ = gateway(tmp_path, monkeypatch)
    monkeypatch.setattr(transport, 'ui_response', lambda *args, **kw: pytest.fail('invalid request sent'), raising=False)
    payload = {'namespace_epoch': 4, 'expected_omp_session_id': 'native',
               'expected_omp_generation': 'instance:1', 'req_id': 7, 'value': None, **changes}
    with pytest.raises(MobileGatewayError) as error:
        service.dispatch_post('/v1/projects/project/agents/mobile/ui-response', payload, service.test_headers)
    assert error.value.status_code == 409


@pytest.mark.parametrize('status', [409, 502, 503])
def test_ui_response_errors_are_safe_and_never_replayed(tmp_path, monkeypatch, status):
    from provider_backends.omp.collab import OmpCollabError
    service, transport, _ = gateway(tmp_path, monkeypatch)
    calls = []
    def respond(*args, **kwargs):
        calls.append(True)
        raise OmpCollabError('wss://secret/host-capability', status)
    monkeypatch.setattr(transport, 'ui_response', respond, raising=False)
    with pytest.raises(MobileGatewayError) as error:
        service.dispatch_post('/v1/projects/project/agents/mobile/ui-response',
                              {'namespace_epoch': 4, 'expected_omp_session_id': 'native',
                               'expected_omp_generation': 'instance:1', 'req_id': 7}, service.test_headers)
    assert error.value.status_code == status
    assert 'secret' not in str(error.value)
    assert calls == [True]


def test_pending_ui_projection_is_bounded_whitelisted_and_not_replayed_offline():
    from mobile_gateway.service import _omp_runtime
    pending = {'req_id': 1, 'kind': 'select', 'title': 'Choose', 'options': [
        {'label': 'One', 'description': 'Description', 'raw_args': 'secret'}],
        'initial_index': 0, 'checked_indices': [0], 'help_text': 'Help', 'prefill': None,
        'marker': '✓', 'path': '/secret'}
    snapshot = {'connected': True, 'ready': True, 'runtime': {'pending_ui_request': pending}}
    projected = _omp_runtime(snapshot, observed_at='now')['pending_ui_request']
    assert projected['options'] == [{'label': 'One', 'description': 'Description'}]
    assert 'path' not in projected
    assert projected['req_id'] == 1
    for changes in ({'req_id': True}, {'title': 'x' * 513}, {'options': [{'label': 'x' * 257}]},
                    {'options': [{'label': 'x'}] * 17}, {'checked_indices': [2]}):
        snapshot['runtime']['pending_ui_request'] = {**pending, **changes}
        assert _omp_runtime(snapshot, observed_at='now')['pending_ui_request'] is None
    snapshot['runtime']['pending_ui_request'] = pending
    snapshot['connected'] = False
    assert _omp_runtime(snapshot, observed_at='now')['pending_ui_request'] is None


def test_ui_response_authentication_precedes_transport(tmp_path, monkeypatch):
    service, transport, _ = gateway(tmp_path, monkeypatch)
    monkeypatch.setattr(transport, 'ui_response', lambda *args, **kw: pytest.fail('unauthorized request sent'), raising=False)
    with pytest.raises(MobileGatewayError) as error:
        service.dispatch_post('/v1/projects/project/agents/mobile/ui-response', {}, {})
    assert error.value.status_code == 401


def test_pending_ui_metadata_budget_preserves_options():
    import json
    from mobile_gateway.service import _omp_runtime
    labels = [f'option-{index}' for index in range(16)]
    pending = {'req_id': 1, 'kind': 'select', 'title': '題' * 512,
               'help_text': '助' * 512, 'options': [
                   {'label': label, 'description': '説' * 512} for label in labels]}
    runtime = _omp_runtime({'connected': True, 'ready': True, 'runtime': {'pending_ui_request': pending}},
                           observed_at='now')
    assert [row['label'] for row in runtime['pending_ui_request']['options']] == labels
    assert len(json.dumps(runtime).encode()) <= 12 * 1024
