from __future__ import annotations

import pytest
from types import SimpleNamespace

from mobile_gateway.service import MobileGatewayService, MobileGatewayError


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


def test_project_view_advertises_omp_controls_without_hidden_connections(tmp_path, monkeypatch):
    service, transport, _ = gateway(tmp_path, monkeypatch)
    payload = service.project_view_payload('project')
    control = payload['view']['agents'][0]['provider_control']
    assert control['provider'] == 'omp'
    assert control['mutation_mode'] == 'native'
    assert control['capabilities']['model_select'] is True
    assert not transport.calls


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


@pytest.mark.parametrize('platform', ['linux', 'darwin', 'win32'])
def test_omp_project_and_focused_capabilities_match(tmp_path, monkeypatch, platform):
    monkeypatch.setattr('provider_backends.omp.platform.sys', SimpleNamespace(platform=platform))
    service, transport, _ = gateway(tmp_path, monkeypatch)
    project = service._require_project('project')
    ordinary = service.project_view_payload('project')['view']['agents'][0]
    focused = service._focused_project_view_payload(project, {})['view']['agents'][0]
    assert ordinary == focused
    if platform == 'linux':
        assert ordinary['provider_control']['runtime_source'] == 'collab'
        assert ordinary['provider_control']['mutation_mode'] == 'native'
    else:
        assert 'provider_control' not in ordinary
    assert not transport.calls


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
    with pytest.raises(MobileGatewayError) as error:
        service.agent_conversation_payload('project', agent='mobile', query=query, headers={})
    assert error.value.status_code == 401
    transport.snapshot['generation'] = 'instance:2'
    with pytest.raises(MobileGatewayError) as error:
        service.agent_conversation_payload('project', agent='mobile', query=query, headers=service.test_headers)
    assert error.value.status_code == 409


@pytest.mark.parametrize('cursor', ['', 'not-a-cursor', 'omp-content-v1.%%%'])
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
