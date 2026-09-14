from __future__ import annotations

import base64
import hashlib
import json
import os
from pathlib import Path
import socket
import subprocess
import struct
import sys
import tempfile
import time
from threading import Event, Thread
from types import SimpleNamespace

import pytest

from completion.models import CompletionSourceKind
import provider_backends.codex.app_server_followup as app_server_followup
from provider_backends.codex.app_server_followup import steer_active_turn
from provider_backends.codex.bridge_runtime.app_server import ManagedCodexAppServer
from provider_backends.codex.execution import CodexProviderAdapter
from provider_backends.codex.launcher_runtime.command_runtime.managed_app_server import (
    build_managed_app_server_command,
    supports_managed_app_server,
    supports_session_fork,
)
from provider_backends.codex.runtime_artifacts import codex_runtime_artifact_layout
from provider_backends.codex.start_cmd_runtime.parsing import extract_resume_session_id
from provider_backends.codex.start_cmd_runtime.rewriting import (
    build_resume_start_cmd,
    strip_resume_start_cmd,
)
from provider_execution.base import ProviderSubmission
from provider_execution.followups import ActiveFollowupRequest
from storage.path_helpers import unix_socket_path_is_safe


def _read_exact(connection: socket.socket, length: int) -> bytes:
    payload = bytearray()
    while len(payload) < length:
        chunk = connection.recv(length - len(payload))
        if not chunk:
            raise AssertionError('test WebSocket connection closed')
        payload.extend(chunk)
    return bytes(payload)


def _receive_client_json(connection: socket.socket) -> dict[str, object]:
    first, second = _read_exact(connection, 2)
    assert first & 0x0F == 0x1
    assert second & 0x80
    length = second & 0x7F
    if length == 126:
        length = struct.unpack('!H', _read_exact(connection, 2))[0]
    elif length == 127:
        length = struct.unpack('!Q', _read_exact(connection, 8))[0]
    mask = _read_exact(connection, 4)
    encoded = _read_exact(connection, length)
    decoded = bytes(value ^ mask[index % 4] for index, value in enumerate(encoded))
    return json.loads(decoded.decode('utf-8'))


def _send_server_json(connection: socket.socket, payload: dict[str, object]) -> None:
    encoded = json.dumps(payload).encode('utf-8')
    header = bytearray([0x81])
    if len(encoded) < 126:
        header.append(len(encoded))
    else:
        header.append(126)
        header.extend(struct.pack('!H', len(encoded)))
    connection.sendall(bytes(header) + encoded)


def _serve_test_websocket(socket_path: Path, ready: Event, seen: list[dict[str, object]]) -> None:
    server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    server.bind(str(socket_path))
    server.listen(1)
    ready.set()
    try:
        connection, _address = server.accept()
        with connection:
            request = bytearray()
            while b'\r\n\r\n' not in request:
                request.extend(connection.recv(4096))
            headers = request.decode('iso-8859-1')
            key_line = next(line for line in headers.split('\r\n') if line.lower().startswith('sec-websocket-key:'))
            key = key_line.split(':', 1)[1].strip()
            accept = base64.b64encode(
                hashlib.sha1(f'{key}258EAFA5-E914-47DA-95CA-C5AB0DC85B11'.encode('ascii')).digest()
            ).decode('ascii')
            connection.sendall(
                (
                    'HTTP/1.1 101 Switching Protocols\r\n'
                    'Upgrade: websocket\r\n'
                    'Connection: Upgrade\r\n'
                    f'Sec-WebSocket-Accept: {accept}\r\n\r\n'
                ).encode('ascii')
            )
            initialize = _receive_client_json(connection)
            seen.append(initialize)
            _send_server_json(connection, {'id': initialize['id'], 'result': {'userAgent': 'fake'}})
            seen.append(_receive_client_json(connection))
            steer = _receive_client_json(connection)
            seen.append(steer)
            _send_server_json(connection, {'id': steer['id'], 'result': {'turnId': 'turn_1'}})
    finally:
        server.close()


def test_steer_active_turn_uses_exact_active_turn_precondition_and_idempotency_key(monkeypatch) -> None:
    captured: dict[str, object] = {}

    def exchange(socket_path, **kwargs):
        captured['socket_path'] = str(socket_path)
        captured.update(kwargs)
        return app_server_followup._WebSocketExchange(
            initialize={'id': 'fup_1:initialize', 'result': {'userAgent': 'codex-test'}},
            steer={'id': 'fup_1:steer', 'result': {'turnId': 'turn_1'}},
        )

    monkeypatch.setattr(app_server_followup, '_websocket_exchange', exchange)
    result = steer_active_turn(
        '/tmp/codex.sock',
        thread_id='thread_1',
        turn_id='turn_1',
        followup_id='fup_1',
        message='correct the current task',
    )

    assert result.accepted is True
    assert captured['socket_path'] == '/tmp/codex.sock'
    wire = [captured['initialize'], captured['initialized'], captured['steer']]
    assert [record['method'] for record in wire] == ['initialize', 'initialized', 'turn/steer']
    assert wire[2]['params'] == {
        'threadId': 'thread_1',
        'expectedTurnId': 'turn_1',
        'input': [{'type': 'text', 'text': 'correct the current task'}],
        'clientUserMessageId': 'fup_1',
    }


def test_steer_active_turn_uses_masked_websocket_frames_over_unix_socket(tmp_path: Path) -> None:
    socket_path = tmp_path / 'app-server.sock'
    ready = Event()
    seen: list[dict[str, object]] = []
    server = Thread(target=_serve_test_websocket, args=(socket_path, ready, seen))
    server.start()
    assert ready.wait(timeout=5)

    result = steer_active_turn(
        socket_path,
        thread_id='thread_1',
        turn_id='turn_1',
        followup_id='fup_1',
        message='correct over WebSocket',
    )
    server.join(timeout=5)

    assert not server.is_alive()
    assert result.accepted is True
    assert [record['method'] for record in seen] == ['initialize', 'initialized', 'turn/steer']
    assert seen[2]['params']['expectedTurnId'] == 'turn_1'


def test_steer_active_turn_fails_closed_on_terminal_or_mismatched_turn(monkeypatch) -> None:
    monkeypatch.setattr(
        app_server_followup,
        '_websocket_exchange',
        lambda *args, **kwargs: app_server_followup._WebSocketExchange(
            initialize={'id': 'fup_1:initialize', 'result': {}},
            steer={
                'id': 'fup_1:steer',
                'error': {'code': -32602, 'message': 'expected turn is not the active turn'},
            }
        ),
    )
    terminal = steer_active_turn(
        '/tmp/codex.sock',
        thread_id='thread_1',
        turn_id='turn_1',
        followup_id='fup_1',
        message='late',
    )
    assert terminal.accepted is False
    assert terminal.reason == 'provider_turn_not_active'

    monkeypatch.setattr(
        app_server_followup,
        '_websocket_exchange',
        lambda *args, **kwargs: app_server_followup._WebSocketExchange(
            initialize={'id': 'fup_1:initialize', 'result': {}},
            steer={'id': 'fup_1:steer', 'result': {'turnId': 'turn_2'}}
        ),
    )
    mismatch = steer_active_turn(
        '/tmp/codex.sock',
        thread_id='thread_1',
        turn_id='turn_1',
        followup_id='fup_1',
        message='wrong turn',
    )
    assert mismatch.accepted is False
    assert mismatch.reason == 'app_server_steer_turn_mismatch'

    monkeypatch.setattr(
        app_server_followup,
        '_websocket_exchange',
        lambda *args, **kwargs: app_server_followup._WebSocketExchange(
            initialize={'id': 'fup_1:initialize', 'result': {}},
            steer={
                'id': 'fup_1:steer',
                'error': {'code': -32600, 'message': 'thread not found: thread_1'},
            },
        ),
    )
    missing = steer_active_turn(
        '/tmp/codex.sock',
        thread_id='thread_1',
        turn_id='turn_1',
        followup_id='fup_1',
        message='missing thread',
    )
    assert missing.accepted is False
    assert missing.reason == 'provider_turn_not_active'


def test_codex_adapter_requires_live_managed_socket_and_exact_bound_turn(tmp_path: Path, monkeypatch) -> None:
    session_id = '12345678-1234-1234-1234-123456789abc'
    session_path = tmp_path / f'rollout-{session_id}.jsonl'
    session_path.write_text('', encoding='utf-8')
    socket_path = tmp_path / 'app-server.sock'
    remote_marker = tmp_path / 'app-server.remote'
    remote_marker.write_text(f'{socket_path}\n', encoding='utf-8')
    listener = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    listener.bind(str(socket_path))
    listener.listen()
    monkeypatch.setattr('provider_backends.codex.execution.app_server_socket_ready', lambda path: True)
    submission = ProviderSubmission(
        job_id='job_1',
        agent_name='codex',
        provider='codex',
        accepted_at='2026-07-21T00:00:00Z',
        ready_at='2026-07-21T00:00:00Z',
        source_kind=CompletionSourceKind.PROTOCOL_EVENT_STREAM,
        reply='',
        runtime_state={
            'mode': 'active',
            'anchor_seen': True,
            'bound_turn_id': 'turn_1',
            'session_path': str(session_path),
            'codex_app_server_enabled': True,
            'codex_app_server_socket': str(socket_path),
            'codex_app_server_remote_marker': str(remote_marker),
        },
    )
    adapter = CodexProviderAdapter()
    try:
        capability = adapter.active_followup_capability(submission)
        assert capability.supported is True
        assert capability.provider_turn_ref == f'codex:{session_id}:turn_1'

        monkeypatch.setattr(
            'provider_backends.codex.execution.steer_active_turn',
            lambda *args, **kwargs: SimpleNamespace(
                accepted=True,
                reason='provider_turn_steered',
                error='',
            ),
        )
        injected = adapter.inject_active_followup(
            submission,
            request=ActiveFollowupRequest(
                followup_id='fup_1',
                job_id='job_1',
                message='correct it',
                expected_provider_turn_ref=capability.provider_turn_ref,
            ),
            now='2026-07-21T00:00:01Z',
        )
        assert injected.status == 'injected'
        assert injected.submission.runtime_state['active_followup_ids'] == ['fup_1']

        steer_calls: list[object] = []
        monkeypatch.setattr(
            'provider_backends.codex.execution.steer_active_turn',
            lambda *args, **kwargs: steer_calls.append((args, kwargs)),
        )
        replayed = adapter.inject_active_followup(
            injected.submission,
            request=ActiveFollowupRequest(
                followup_id='fup_1',
                job_id='job_1',
                message='must not send twice',
                expected_provider_turn_ref=capability.provider_turn_ref,
            ),
            now='2026-07-21T00:00:01Z',
        )
        assert replayed.status == 'injected'
        assert replayed.reason == 'provider_turn_steered_idempotent_replay'
        assert steer_calls == []

        changed = adapter.inject_active_followup(
            submission,
            request=ActiveFollowupRequest(
                followup_id='fup_2',
                job_id='job_1',
                message='wrong binding',
                expected_provider_turn_ref=f'codex:{session_id}:turn_old',
            ),
            now='2026-07-21T00:00:02Z',
        )
        assert changed.status == 'terminal'
        assert changed.reason == 'codex_active_turn_binding_changed'
    finally:
        listener.close()

    monkeypatch.setattr('provider_backends.codex.execution.app_server_socket_ready', lambda path: False)
    missing = adapter.active_followup_capability(submission)
    assert missing.supported is False
    assert missing.reason == 'codex_managed_app_server_unavailable'
    legacy = adapter.active_followup_capability(
        ProviderSubmission(
            **{
                **submission.__dict__,
                'runtime_state': {**submission.runtime_state, 'codex_app_server_enabled': False},
            }
        )
    )
    assert legacy.supported is False
    assert legacy.reason == 'codex_legacy_tui_missing_expected_turn_precondition'

    remote_marker.unlink()
    local_fallback = adapter.active_followup_capability(submission)
    assert local_fallback.supported is False
    assert local_fallback.reason == 'codex_managed_remote_tui_unconfirmed'


def test_codex_adapter_keeps_ambiguous_transport_result_durably_accepted(tmp_path: Path, monkeypatch) -> None:
    session_id = '12345678-1234-1234-1234-123456789abc'
    session_path = tmp_path / f'rollout-{session_id}.jsonl'
    session_path.write_text('', encoding='utf-8')
    socket_path = tmp_path / 'app-server.sock'
    remote_marker = tmp_path / 'app-server.remote'
    remote_marker.write_text(f'{socket_path}\n', encoding='utf-8')
    submission = ProviderSubmission(
        job_id='job_1',
        agent_name='codex',
        provider='codex',
        accepted_at='2026-07-21T00:00:00Z',
        ready_at='2026-07-21T00:00:00Z',
        source_kind=CompletionSourceKind.PROTOCOL_EVENT_STREAM,
        reply='',
        runtime_state={
            'mode': 'active',
            'anchor_seen': True,
            'bound_turn_id': 'turn_1',
            'session_path': str(session_path),
            'codex_app_server_enabled': True,
            'codex_app_server_socket': str(socket_path),
            'codex_app_server_remote_marker': str(remote_marker),
        },
    )
    monkeypatch.setattr('provider_backends.codex.execution.app_server_socket_ready', lambda path: True)
    monkeypatch.setattr(
        'provider_backends.codex.execution.steer_active_turn',
        lambda *args, **kwargs: SimpleNamespace(
            accepted=False,
            reason='app_server_websocket_timeout',
            error='',
        ),
    )
    adapter = CodexProviderAdapter()
    capability = adapter.active_followup_capability(submission)
    result = adapter.inject_active_followup(
        submission,
        request=ActiveFollowupRequest(
            followup_id='fup_ambiguous',
            job_id='job_1',
            message='may already be delivered',
            expected_provider_turn_ref=capability.provider_turn_ref,
        ),
        now='2026-07-21T00:00:01Z',
    )
    assert result.status == 'accepted'
    assert result.reason == 'app_server_websocket_timeout'


def test_managed_launcher_preserves_resume_rewrites_and_fallback(tmp_path: Path) -> None:
    session_id = '12345678-1234-1234-1234-123456789abc'
    command, state = build_managed_app_server_command(
        ['codex', '--profile', 'ccb', 'resume', session_id],
        runtime_dir=tmp_path,
    )

    assert state['codex_app_server_command'] == [
        'codex',
        'app-server',
        '--listen',
        f'unix://{tmp_path / "app-server.sock"}',
    ]
    assert state['codex_app_server_remote_marker'] == str(tmp_path / 'app-server.remote')
    assert f'codex --remote unix://{tmp_path / "app-server.sock"} --profile ccb' in command
    assert f"printf '%s\\n' {tmp_path / 'app-server.sock'} > {tmp_path / 'app-server.remote'}" in command
    assert 'else exec codex --profile ccb' in command
    assert extract_resume_session_id(command) == session_id

    rewritten = build_resume_start_cmd(command, 'aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa')
    assert extract_resume_session_id(rewritten) == 'aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa'
    stripped = strip_resume_start_cmd(rewritten)
    assert extract_resume_session_id(stripped) is None
    assert 'CCB_CODEX_MANAGED_REMOTE=1' in stripped


def test_managed_launcher_rejects_unverified_remote_fork_combination(tmp_path: Path) -> None:
    session_id = '12345678-1234-1234-1234-123456789abc'

    with pytest.raises(ValueError, match='does not provide verified fork semantics'):
        build_managed_app_server_command(
            ['codex', '--profile', 'ccb', 'fork', session_id],
            runtime_dir=tmp_path,
        )


def test_managed_app_server_capability_probe_is_explicit(monkeypatch) -> None:
    supports_managed_app_server.cache_clear()

    def run(command, **kwargs):
        del kwargs
        if command[-1] == '--version':
            return SimpleNamespace(returncode=0, stdout='codex-cli 0.144.6')
        if command[-1] == '--help' and command[1:] == ['--help']:
            return SimpleNamespace(returncode=0, stdout='usage: codex --remote unix://PATH')
        return SimpleNamespace(returncode=0, stdout='usage: codex app-server --listen unix://PATH')

    monkeypatch.setattr(subprocess, 'run', run)
    assert supports_managed_app_server(('codex',)) is True
    assert supports_managed_app_server(('env', 'codex')) is False


def test_codex_fork_capability_probe_is_explicit(monkeypatch) -> None:
    supports_session_fork.cache_clear()

    monkeypatch.setattr(
        subprocess,
        'run',
        lambda command, **kwargs: SimpleNamespace(
            returncode=0,
            stdout='Fork a previous interactive session' if command[1:3] == ['fork', '--help'] else '',
        ),
    )

    assert supports_session_fork(('codex',)) is True
    assert supports_session_fork(('env', 'codex')) is False


def test_managed_app_server_supervisor_starts_and_stops_exact_child(tmp_path: Path, monkeypatch) -> None:
    socket_path = tmp_path / 'app-server.sock'
    child = (
        'import socket,time; '
        f's=socket.socket(socket.AF_UNIX); s.bind({str(socket_path)!r}); s.listen(); time.sleep(30)'
    )
    monkeypatch.setenv('CCB_CODEX_APP_SERVER_COMMAND_JSON', json.dumps([sys.executable, '-c', child]))
    monkeypatch.setenv('CCB_CODEX_APP_SERVER_SOCKET', str(socket_path))
    supervisor = ManagedCodexAppServer(tmp_path)

    assert supervisor.start() is True
    pid = json.loads((tmp_path / 'app-server.pid').read_text(encoding='utf-8'))['pid']
    assert pid > 0
    assert socket_path.is_socket()
    supervisor.stop()

    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        pass
    else:
        raise AssertionError('managed app-server child remained alive after supervisor stop')
    assert not socket_path.exists()
    assert not (tmp_path / 'app-server.pid').exists()
    assert not (tmp_path / 'app-server.remote').exists()


def test_managed_app_server_failed_start_cleans_owned_runtime_artifacts(tmp_path: Path, monkeypatch) -> None:
    socket_path = tmp_path / 'app-server.sock'
    stale = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    stale.bind(str(socket_path))
    stale.close()
    monkeypatch.setenv(
        'CCB_CODEX_APP_SERVER_COMMAND_JSON',
        json.dumps([sys.executable, '-c', 'raise SystemExit(7)']),
    )
    monkeypatch.setenv('CCB_CODEX_APP_SERVER_SOCKET', str(socket_path))

    assert ManagedCodexAppServer(tmp_path).start() is False
    assert not socket_path.exists()
    assert not (tmp_path / 'app-server.pid').exists()

    monkeypatch.setenv(
        'CCB_CODEX_APP_SERVER_COMMAND_JSON',
        json.dumps([str(tmp_path / 'missing-codex'), 'app-server']),
    )
    assert ManagedCodexAppServer(tmp_path).start() is False
    assert not (tmp_path / 'app-server.pid').exists()


def test_managed_app_server_refuses_foreign_socket_path_without_unlinking_it(tmp_path: Path, monkeypatch) -> None:
    foreign_socket = tmp_path / 'foreign.sock'
    stale = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    stale.bind(str(foreign_socket))
    stale.close()
    monkeypatch.setenv(
        'CCB_CODEX_APP_SERVER_COMMAND_JSON',
        json.dumps([sys.executable, '-c', 'raise SystemExit(0)']),
    )
    monkeypatch.setenv('CCB_CODEX_APP_SERVER_SOCKET', str(foreign_socket))

    assert ManagedCodexAppServer(tmp_path / 'runtime').start() is False
    assert foreign_socket.is_socket()


def test_managed_app_server_uses_owned_short_socket_for_long_runtime_path(tmp_path: Path, monkeypatch) -> None:
    # Use a short, writable fallback root instead of hardcoding /tmp: the
    # host's /tmp may be unwritable (inode pressure), and the contract under
    # test is "long preferred path falls back to a short runtime root".
    # pytest's tmp_path can itself be long, so derive a short root directly
    # under the process tempdir.
    short_root = Path(tempfile.gettempdir()) / f'ccb-t{os.getpid()}'
    short_root.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv('XDG_RUNTIME_DIR', str(short_root))
    runtime_dir = tmp_path / ('long-runtime-' * 5) / ('nested-' * 5) / 'codex'
    runtime_dir.mkdir(parents=True)
    artifacts = codex_runtime_artifact_layout(runtime_dir)
    assert artifacts.app_server_socket_placement.preferred_path == runtime_dir / 'app-server.sock'
    assert artifacts.app_server_socket_placement.fallback_reason == 'path_too_long'
    assert artifacts.app_server_socket.parent == short_root / 'ccb-runtime'
    assert unix_socket_path_is_safe(artifacts.app_server_socket)

    child = (
        'import socket,time; '
        f's=socket.socket(socket.AF_UNIX); s.bind({str(artifacts.app_server_socket)!r}); '
        's.listen(); time.sleep(30)'
    )
    monkeypatch.setenv(
        'CCB_CODEX_APP_SERVER_COMMAND_JSON',
        json.dumps([sys.executable, '-c', child]),
    )
    monkeypatch.setenv('CCB_CODEX_APP_SERVER_SOCKET', str(artifacts.app_server_socket))
    supervisor = ManagedCodexAppServer(runtime_dir)
    assert supervisor.start() is True
    assert artifacts.app_server_socket.is_socket()
    supervisor.stop()
    assert not artifacts.app_server_socket.exists()


def _socket_child_command(socket_path: Path, lifetime_s: float) -> list[str]:
    child = (
        'import socket,sys,time; '
        f's=socket.socket(socket.AF_UNIX); s.bind({str(socket_path)!r}); s.listen(8); '
        f'time.sleep({lifetime_s})'
    )
    return [sys.executable, '-c', child]




def test_shutdown_cleanup_preserves_live_successor_artifact_set(tmp_path: Path) -> None:
    """#345 review P1-1: the whole successor-owned set (socket, PID record,
    remote marker) must survive a shutdown cleanup while its owner lives."""
    from provider_backends.codex.runtime_artifacts import cleanup_codex_app_server_shutdown_artifacts

    runtime_dir = tmp_path / 'runtime'
    runtime_dir.mkdir()
    artifacts = codex_runtime_artifact_layout(runtime_dir)
    listener = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    listener.bind(str(artifacts.app_server_socket))
    listener.listen(1)
    try:
        artifacts.app_server_pid.write_text(
            json.dumps({'pid': os.getpid(), 'generation': 'successor'}) + '\n',
            encoding='utf-8',
        )
        artifacts.app_server_remote_marker.write_text(
            str(artifacts.app_server_socket) + '\n', encoding='utf-8'
        )

        removed = cleanup_codex_app_server_shutdown_artifacts(runtime_dir)

        assert removed == ()
        assert artifacts.app_server_pid.exists()
        assert artifacts.app_server_remote_marker.exists()
        assert artifacts.app_server_socket.is_socket()
    finally:
        listener.close()
        for path in (artifacts.app_server_socket, artifacts.app_server_pid, artifacts.app_server_remote_marker):
            try:
                path.unlink()
            except OSError:
                pass


def test_shutdown_cleanup_removes_dead_owner_artifacts(tmp_path: Path) -> None:
    from provider_backends.codex.runtime_artifacts import cleanup_codex_app_server_shutdown_artifacts

    runtime_dir = tmp_path / 'runtime'
    runtime_dir.mkdir()
    artifacts = codex_runtime_artifact_layout(runtime_dir)
    stale = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    stale.bind(str(artifacts.app_server_socket))
    stale.close()
    artifacts.app_server_pid.write_text(
        json.dumps({'pid': 999999, 'generation': 'dead-owner'}) + '\n',
        encoding='utf-8',
    )
    artifacts.app_server_remote_marker.write_text('x\n', encoding='utf-8')

    removed = cleanup_codex_app_server_shutdown_artifacts(runtime_dir)

    assert set(removed) == {
        artifacts.app_server_pid,
        artifacts.app_server_remote_marker,
        artifacts.app_server_socket,
    }


def test_failed_start_restore_never_overwrites_live_successor_record(tmp_path: Path) -> None:
    """#345 review P1-2: a failed start must not restore an older PID
    snapshot over a live foreign generation's claim."""
    runtime_dir = tmp_path / 'runtime'
    runtime_dir.mkdir()
    artifacts = codex_runtime_artifact_layout(runtime_dir)
    old_snapshot = json.dumps({'pid': 999999, 'generation': 'old'}) + '\n'
    live_record = json.dumps({'pid': os.getpid(), 'generation': 'new'}) + '\n'
    artifacts.app_server_pid.write_text(live_record, encoding='utf-8')

    ManagedCodexAppServer._restore_pid_record(artifacts.app_server_pid, old_snapshot)

    assert artifacts.app_server_pid.read_text(encoding='utf-8') == live_record


def test_failed_start_restore_overwrites_dead_foreign_record(tmp_path: Path) -> None:
    runtime_dir = tmp_path / 'runtime'
    runtime_dir.mkdir()
    artifacts = codex_runtime_artifact_layout(runtime_dir)
    old_snapshot = json.dumps({'pid': 999999, 'generation': 'old'}) + '\n'
    dead_record = json.dumps({'pid': 999998, 'generation': 'dead'}) + '\n'
    artifacts.app_server_pid.write_text(dead_record, encoding='utf-8')

    ManagedCodexAppServer._restore_pid_record(artifacts.app_server_pid, old_snapshot)

    assert artifacts.app_server_pid.read_text(encoding='utf-8') == old_snapshot


def test_concurrent_first_starts_yield_exactly_one_claim(tmp_path: Path, monkeypatch) -> None:
    """#345 review P1-3: overlapping first starts must serialize so exactly
    one generation claims the endpoint; the other fails without destroying
    the winner's artifacts."""
    runtime_dir = tmp_path / 'runtime'
    runtime_dir.mkdir()
    artifacts = codex_runtime_artifact_layout(runtime_dir)
    socket_path = artifacts.app_server_socket
    monkeypatch.setenv('CCB_CODEX_APP_SERVER_COMMAND_JSON', json.dumps(_socket_child_command(socket_path, 30.0)))
    monkeypatch.setenv('CCB_CODEX_APP_SERVER_SOCKET', str(socket_path))

    supervisors = [ManagedCodexAppServer(runtime_dir) for _ in range(2)]
    outcomes: dict[int, bool] = {}

    def _start(index: int) -> None:
        outcomes[index] = supervisors[index].start()

    threads = [Thread(target=_start, args=(index,)) for index in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=30)

    started = [index for index, ok in outcomes.items() if ok]
    assert len(started) == 1, f"expected exactly one winner, got {outcomes}"
    winner = supervisors[started[0]]

    # The winner's endpoint is live and its registry claim is intact.
    ok, err = _pane_connect(socket_path)
    assert ok, f"winner endpoint unusable: {err}"
    record = json.loads(artifacts.app_server_pid.read_text(encoding='utf-8'))
    assert record['generation'] == winner._generation

    for supervisor in supervisors:
        supervisor.stop()


def _pane_connect(socket_path: Path) -> tuple[bool, str]:
    try:
        client = socket.socket(socket.AF_UNIX)
        client.settimeout(1.0)
        client.connect(str(socket_path))
        client.close()
        return True, ''
    except OSError as exc:
        return False, str(exc)


def test_managed_app_server_overlap_keeps_foreign_endpoint_and_restores_after_exit(
    tmp_path: Path, monkeypatch
) -> None:
    """#345: during a restart overlap the old endpoint must survive, and the
    new generation must take over cleanly once the old one exits."""
    runtime_dir = tmp_path / 'runtime'
    runtime_dir.mkdir()
    artifacts = codex_runtime_artifact_layout(runtime_dir)
    socket_path = artifacts.app_server_socket
    monkeypatch.setenv('CCB_CODEX_APP_SERVER_COMMAND_JSON', json.dumps(_socket_child_command(socket_path, 30.0)))
    monkeypatch.setenv('CCB_CODEX_APP_SERVER_SOCKET', str(socket_path))

    supervisor_a = ManagedCodexAppServer(runtime_dir)
    assert supervisor_a.start() is True
    ok, err = _pane_connect(socket_path)
    assert ok, f"pane A could not attach to generation A: {err}"

    supervisor_b = ManagedCodexAppServer(runtime_dir)
    started: list[bool] = []

    def _start_b() -> None:
        started.append(supervisor_b.start())

    thread = Thread(target=_start_b)
    thread.start()
    try:
        time.sleep(0.2)
        ok_mid, err_mid = _pane_connect(socket_path)
        assert ok_mid, f"foreign endpoint destroyed during overlap: {err_mid}"
        supervisor_a.stop()
    finally:
        thread.join(timeout=15)
        supervisor_a.stop()
    assert started == [True]
    ok_b, err_b = _pane_connect(socket_path)
    assert ok_b, f"new generation endpoint unusable after takeover: {err_b}"
    supervisor_b.stop()


def _managed_pane_fixture(tmp_path: Path):
    runtime_dir = tmp_path / 'runtime'
    runtime_dir.mkdir()
    artifacts = codex_runtime_artifact_layout(runtime_dir)
    bin_dir = tmp_path / 'bin'
    bin_dir.mkdir()
    fake_codex = bin_dir / 'codex'
    fake_codex.write_text(
        '#!/bin/sh\n'
        'printf \'%s\\n\' "$*" >> "$CCB_FAKE_CODEX_LOG"\n'
        'exit 0\n',
        encoding='utf-8',
    )
    fake_codex.chmod(0o755)
    log_path = tmp_path / 'codex-invocations.log'
    env = {
        **os.environ,
        'PATH': f'{bin_dir}:{os.environ.get("PATH", "")}',
        'CCB_FAKE_CODEX_LOG': str(log_path),
    }
    return artifacts, env, log_path


def test_managed_pane_command_ignores_stale_socket_node(tmp_path: Path) -> None:
    """#345 review: the generated pane command must not attach to a leftover
    (dead) socket node from a previous generation; the wait reference keeps
    the stale node from satisfying the socket wait."""
    from provider_backends.codex.launcher_runtime.command_runtime.managed_app_server import (
        _managed_shell_command,
    )

    artifacts, env, log_path = _managed_pane_fixture(tmp_path)
    socket_path = artifacts.app_server_socket
    pane_cmd = _managed_shell_command(
        remote_args=['codex', '--remote', f'unix://{socket_path}'],
        local_args=['codex'],
        socket_path=socket_path,
        remote_marker=artifacts.app_server_remote_marker,
        resume_id='session-1',
        continuation_mode='resume',
    )

    # Stale node on the exact path the pane command will read: bound then
    # closed, no listener.
    stale = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    stale.bind(str(socket_path))
    stale.close()

    started = time.monotonic()
    result = subprocess.run(
        ['sh', '-c', pane_cmd],
        env=env,
        capture_output=True,
        text=True,
        timeout=15,
    )
    elapsed = time.monotonic() - started

    invocations = log_path.read_text(encoding='utf-8') if log_path.exists() else ''
    # The stale node must not satisfy the wait: no remote exec and no marker.
    assert '--remote' not in invocations, invocations
    assert not artifacts.app_server_remote_marker.exists()
    assert elapsed >= 4.0, f"pane attached to the stale node after only {elapsed:.2f}s"
    # The local fallback kept the resume continuation.
    assert 'resume session-1' in invocations, invocations


def test_managed_pane_command_attaches_when_socket_appears_after_start(tmp_path: Path) -> None:
    """#345 review control: a socket bound by the owning generation after the
    pane started satisfies the wait and takes the remote branch."""
    from provider_backends.codex.launcher_runtime.command_runtime.managed_app_server import (
        _managed_shell_command,
    )

    artifacts, env, log_path = _managed_pane_fixture(tmp_path)
    socket_path = artifacts.app_server_socket
    pane_cmd = _managed_shell_command(
        remote_args=['codex', '--remote', f'unix://{socket_path}'],
        local_args=['codex'],
        socket_path=socket_path,
        remote_marker=artifacts.app_server_remote_marker,
        resume_id='',
        continuation_mode='resume',
    )

    server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)

    def _bind_later() -> None:
        time.sleep(0.4)
        server.bind(str(socket_path))
        server.listen(4)

    binder = Thread(target=_bind_later)
    binder.start()
    try:
        result = subprocess.run(
            ['sh', '-c', pane_cmd],
            env=env,
            capture_output=True,
            text=True,
            timeout=15,
        )
        binder.join(timeout=5)
    finally:
        server.close()

    invocations = log_path.read_text(encoding='utf-8') if log_path.exists() else ''
    assert f'unix://{socket_path}' in invocations, invocations
    assert artifacts.app_server_remote_marker.exists()


def test_managed_app_server_preserves_foreign_endpoint_when_child_cannot_bind(
    tmp_path: Path, monkeypatch
) -> None:
    """#345: a start that cannot claim the endpoint must report False without
    destroying the live foreign generation's socket or PID registry."""
    runtime_dir = tmp_path / 'runtime'
    runtime_dir.mkdir()
    artifacts = codex_runtime_artifact_layout(runtime_dir)
    socket_path = artifacts.app_server_socket
    monkeypatch.setenv('CCB_CODEX_APP_SERVER_COMMAND_JSON', json.dumps(_socket_child_command(socket_path, 30.0)))
    monkeypatch.setenv('CCB_CODEX_APP_SERVER_SOCKET', str(socket_path))

    supervisor_a = ManagedCodexAppServer(runtime_dir)
    assert supervisor_a.start() is True
    pid_record_before = (runtime_dir / 'app-server.pid').read_text(encoding='utf-8')

    supervisor_b = ManagedCodexAppServer(runtime_dir)
    # The foreign owner never exits within the bounded wait.
    assert supervisor_b.start() is False

    # The live endpoint and its registry are untouched.
    ok, err = _pane_connect(socket_path)
    assert ok, f"live endpoint destroyed by failed start: {err}"
    assert (runtime_dir / 'app-server.pid').read_text(encoding='utf-8') == pid_record_before

    # Stopping the failed supervisor must not remove the live endpoint.
    supervisor_b.stop()
    ok2, err2 = _pane_connect(socket_path)
    assert ok2, f"failed supervisor stop removed the live endpoint: {err2}"
    supervisor_a.stop()


def test_managed_app_server_stop_does_not_remove_newer_generation_socket(
    tmp_path: Path, monkeypatch
) -> None:
    """#345: the old generation's stop must never unlink a newer
    generation's endpoint."""
    runtime_dir = tmp_path / 'runtime'
    runtime_dir.mkdir()
    artifacts = codex_runtime_artifact_layout(runtime_dir)
    socket_path = artifacts.app_server_socket
    monkeypatch.setenv('CCB_CODEX_APP_SERVER_COMMAND_JSON', json.dumps(_socket_child_command(socket_path, 30.0)))
    monkeypatch.setenv('CCB_CODEX_APP_SERVER_SOCKET', str(socket_path))

    supervisor_a = ManagedCodexAppServer(runtime_dir)
    assert supervisor_a.start() is True
    supervisor_a.stop()

    # New generation starts after the old one exited.
    supervisor_b = ManagedCodexAppServer(runtime_dir)
    assert supervisor_b.start() is True

    # A late, repeated stop of the old supervisor must not touch the new
    # generation's socket.
    supervisor_a.stop()
    ok, err = _pane_connect(socket_path)
    assert ok, f"old stop removed the new generation's socket: {err}"
    assert artifacts.app_server_socket.is_socket()
    supervisor_b.stop()


def test_managed_app_server_early_exit_reports_failure(tmp_path: Path, monkeypatch) -> None:
    """#345: a child that dies before binding must report start() == False
    without claiming the PID registry."""
    runtime_dir = tmp_path / 'runtime'
    runtime_dir.mkdir()
    artifacts = codex_runtime_artifact_layout(runtime_dir)
    monkeypatch.setenv(
        'CCB_CODEX_APP_SERVER_COMMAND_JSON',
        json.dumps([sys.executable, '-c', 'raise SystemExit(9)']),
    )
    monkeypatch.setenv('CCB_CODEX_APP_SERVER_SOCKET', str(artifacts.app_server_socket))

    supervisor = ManagedCodexAppServer(runtime_dir)
    assert supervisor.start() is False
    assert not (runtime_dir / 'app-server.pid').exists()
    assert not artifacts.app_server_socket.exists()


def test_managed_app_server_delayed_readiness_still_succeeds(tmp_path: Path, monkeypatch) -> None:
    """#345: a slow-to-bind child still reaches readiness within the window."""
    runtime_dir = tmp_path / 'runtime'
    runtime_dir.mkdir()
    artifacts = codex_runtime_artifact_layout(runtime_dir)
    socket_path = artifacts.app_server_socket
    child = (
        'import socket,sys,time; '
        'time.sleep(0.5); '
        f's=socket.socket(socket.AF_UNIX); s.bind({str(socket_path)!r}); s.listen(8); '
        'time.sleep(30)'
    )
    monkeypatch.setenv('CCB_CODEX_APP_SERVER_COMMAND_JSON', json.dumps([sys.executable, '-c', child]))
    monkeypatch.setenv('CCB_CODEX_APP_SERVER_SOCKET', str(socket_path))

    supervisor = ManagedCodexAppServer(runtime_dir)
    assert supervisor.start() is True
    ok, err = _pane_connect(socket_path)
    assert ok, err
    supervisor.stop()
