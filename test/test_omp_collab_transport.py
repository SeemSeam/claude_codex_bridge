from __future__ import annotations

import json
import os
import socket
import threading
from pathlib import Path

import pytest

from provider_backends.omp.collab import OmpCollabClient, OmpCollabError


def _binding(tmp_path: Path):
    runtime = tmp_path / '.ccb' / 'runtime'
    endpoint_dir = runtime / 'collab'
    endpoint_dir.mkdir(parents=True, mode=0o700)
    endpoint = endpoint_dir / 'owner.sock'
    data = {'provider': 'omp', 'agent_name': 'a', 'ccb_session_id': 'launch',
            'start_dir': str(tmp_path), 'runtime_dir': str(runtime),
            'omp_collab_socket': str(endpoint)}
    session = tmp_path / '.ccb' / '.omp-a-session'
    session.write_text(json.dumps(data))
    return endpoint, session, data


def _server(endpoint, response):
    server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    server.bind(str(endpoint))
    os.chmod(endpoint, 0o600)
    server.listen(1)
    requests = []
    def serve():
        try:
            connection, _ = server.accept()
            with connection:
                stream = connection.makefile('rb')
                requests.append(json.loads(stream.readline()))
                connection.sendall(json.dumps(response).encode() + b'\n')
        finally:
            server.close()
    thread = threading.Thread(target=serve, daemon=True)
    thread.start()
    return thread, requests


def test_snapshot_real_owner_binding(tmp_path):
    endpoint, _, _ = _binding(tmp_path)
    snapshot = {'ready': True, 'connected': True, 'session_id': 'native', 'generation': 'owner:1', 'entries': [], 'revision': 1}
    thread, requests = _server(endpoint, {'actor': 'a', 'launch_session_id': 'launch', 'result': snapshot})
    client = OmpCollabClient()
    assert client.get_snapshot(tmp_path, 'a') == snapshot
    thread.join(2)
    assert requests == [{'operation': 'snapshot', 'actor': 'a', 'launch_session_id': 'launch'}]
    client.close_all()


def test_history_page_preserves_fences_cursors_and_chunk_payload(tmp_path):
    endpoint, _, _ = _binding(tmp_path)
    page = {'session_id': 'native', 'generation': 'owner:1', 'entries': [],
            'entry_chunks': [{'entry_id': 'large', 'offset': 0, 'total_bytes': 999999,
                              'encoding': 'base64', 'data': 'e30='}],
            'start_cursor': 'before', 'next_cursor': 'after', 'has_more': True}
    thread, requests = _server(endpoint, {'actor': 'a', 'launch_session_id': 'launch', 'result': page})
    client = OmpCollabClient()
    assert client.get_history_page(tmp_path, 'a', cursor='before', limit=64, max_bytes=65536,
                                   expected_session_id='native', expected_generation='owner:1') == page
    thread.join(2)
    assert requests == [{'operation': 'history', 'actor': 'a', 'launch_session_id': 'launch',
                         'cursor': 'before', 'limit': 64, 'max_bytes': 65536,
                         'expected_session_id': 'native', 'expected_generation': 'owner:1'}]


@pytest.mark.parametrize('fields', [{'limit': 129}, {'limit': True}, {'max_bytes': 4095},
                                   {'max_bytes': 262145}, {'cursor': ''}, {'cursor': 1}])
def test_history_page_rejects_unbounded_requests_before_connecting(tmp_path, fields):
    with pytest.raises(OmpCollabError, match='Invalid OMP history page') as error:
        OmpCollabClient().get_history_page(tmp_path, 'a', **fields)
    assert error.value.status_code == 409


def test_wrong_provider_never_connects(tmp_path):
    _, session, data = _binding(tmp_path)
    data['provider'] = 'pi'
    session.write_text(json.dumps(data))
    with pytest.raises(OmpCollabError) as error:
        OmpCollabClient().get_snapshot(tmp_path, 'a')
    assert error.value.status_code == 409


def test_endpoint_symlink_rejected(tmp_path):
    endpoint, _, _ = _binding(tmp_path)
    endpoint.symlink_to(tmp_path / 'elsewhere')
    with pytest.raises(OmpCollabError):
        OmpCollabClient().get_snapshot(tmp_path, 'a')


def test_world_accessible_socket_rejected(tmp_path):
    endpoint, _, _ = _binding(tmp_path)
    server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    try:
        server.bind(str(endpoint))
        os.chmod(endpoint, 0o666)
        with pytest.raises(OmpCollabError):
            OmpCollabClient().get_snapshot(tmp_path, 'a')
    finally:
        server.close()


def test_submit_requires_observed_generation(tmp_path):
    endpoint, _, _ = _binding(tmp_path)
    server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    try:
        server.bind(str(endpoint))
        os.chmod(endpoint, 0o600)
        with pytest.raises(OmpCollabError, match='Refresh') as error:
            OmpCollabClient().submit(tmp_path, 'a', 'body', 'key')
        assert error.value.status_code == 409
    finally:
        server.close()


def test_path_like_agent_rejected(tmp_path):
    with pytest.raises(OmpCollabError):
        OmpCollabClient().get_snapshot(tmp_path, '../other')


def test_command_wrapper_preserves_quoted_semicolons(tmp_path):
    import shlex
    from provider_backends.omp.launcher import _wrap_collab_command
    command = "omp --append-system-prompt 'literal; semicolon'"
    result = shlex.split(_wrap_collab_command(command, {'omp_collab_binding': str(tmp_path / 'binding.json')}))
    assert result[-3:] == ['/bin/sh', '-c', command]


def test_wrapped_restart_preserves_literal_session_path(tmp_path):
    import shlex
    from provider_backends.omp.launcher import _wrap_collab_command
    from provider_backends.omp.session import OMP_RESTART_SESSION_MARKER, render_restart_command
    path = tmp_path / "native 'quoted'; session.jsonl"
    template = _wrap_collab_command(f"omp {OMP_RESTART_SESSION_MARKER}", {'omp_collab_binding': str(tmp_path / 'binding.json')})
    outer = shlex.split(render_restart_command(template, path))
    assert shlex.split(outer[-1]) == ['omp', '--resume', str(path)]


def test_submit_returns_queued_receipt_without_business_job(tmp_path):
    endpoint, _, _ = _binding(tmp_path)
    receipt = {'state': 'queued', 'request_id': 'fifo-1', 'session_id': 'native', 'generation': 'owner:1'}
    thread, requests = _server(endpoint, {'actor': 'a', 'launch_session_id': 'launch', 'result': receipt})
    assert OmpCollabClient().submit(tmp_path, 'a', 'second input', 'key',
                                    expected_session_id='native', expected_generation='owner:1') == receipt
    thread.join(2)
    assert requests[0]['body'] == 'second input'
    assert requests[0]['idempotency_key'] == 'key'
    assert 'job_id' not in receipt


def test_submit_bounds_utf8_body_before_transport(tmp_path):
    with pytest.raises(OmpCollabError, match='too large') as error:
        OmpCollabClient().submit(tmp_path, 'a', '界' * (256 * 1024 // 3 + 1), 'key',
                                 expected_session_id='native', expected_generation='owner:1')
    assert error.value.status_code == 409


def test_snapshot_preserves_safe_pending_inputs(tmp_path):
    endpoint, _, _ = _binding(tmp_path)
    pending = {'id': 'fifo-1', 'idempotency_key': 'key', 'body': 'waiting', 'state': 'queued',
               'request_id': 'fifo-1', 'session_id': 'native', 'generation': 'owner:1', 'position': 1}
    snapshot = {'pending_inputs': [pending], 'ready': True, 'connected': True}
    thread, _ = _server(endpoint, {'actor': 'a', 'launch_session_id': 'launch', 'result': snapshot})
    assert OmpCollabClient().get_snapshot(tmp_path, 'a')['pending_inputs'] == [pending]
    thread.join(2)
