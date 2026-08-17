from __future__ import annotations

import asyncio
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from ccbd.api_models import DeliveryScope, JobRecord, JobStatus, MessageEnvelope
from cli.models import ParsedStartCommand
from completion.models import CompletionConfidence, CompletionItemKind, CompletionStatus
import provider_backends.dsh.bridge as dsh_bridge
from provider_backends.dsh.bridge import DshTurnReducer
import provider_backends.session_authority as session_authority
from provider_backends.dsh.control import (
    compact_dsh_session,
    load_dsh_host_endpoint,
    rotate_dsh_session,
)
from provider_backends.dsh.execution import (
    _build_command,
    build_execution_adapter,
    observe_dsh_output,
)
from provider_backends.dsh.home import materialize_dsh_home
from provider_backends.dsh.host_runtime import _owns_state
from provider_backends.dsh.launcher import (
    _dsh_web_command,
    _resume_candidate,
    build_start_cmd,
    post_launch,
)
from provider_backends.native_cli_support import NativeCliExecutionRequest
from provider_core.pathing import session_filename_for_agent
from provider_execution.base import ProviderRuntimeContext


def _event(event_type: str, seq: int, data: dict, **extra) -> dict:
    return {
        'type': event_type,
        'seq': seq,
        'time': 1_800_000_000_000 + seq,
        'data': data,
        **extra,
    }


def _job(work_dir: Path) -> JobRecord:
    return JobRecord(
        job_id='job_dsh_exact_1',
        submission_id='sub_dsh_1',
        agent_name='dsh1',
        provider='dsh',
        request=MessageEnvelope(
            project_id='project-1',
            to_agent='dsh1',
            from_actor='main',
            body='Reply exactly: DSH_OK',
            task_id=None,
            reply_to=None,
            message_type='ask',
            delivery_scope=DeliveryScope.SINGLE,
        ),
        status=JobStatus.RUNNING,
        terminal_decision=None,
        cancel_requested_at=None,
        created_at='2026-08-14T00:00:00Z',
        updated_at='2026-08-14T00:00:00Z',
        workspace_path=str(work_dir),
    )


def _session(work_dir: Path) -> Path:
    runtime = work_dir / '.ccb' / 'agents' / 'dsh1' / 'provider-runtime' / 'dsh'
    state = work_dir / '.ccb' / 'agents' / 'dsh1' / 'provider-state' / 'dsh'
    session_file = work_dir / '.ccb' / session_filename_for_agent('dsh', 'dsh1')
    endpoint_state = runtime / 'dsh-host.json'
    session_file.parent.mkdir(parents=True, exist_ok=True)
    session_file.write_text(
        json.dumps(
            {
                'active': True,
                'provider': 'dsh',
                'agent_name': 'dsh1',
                'ccb_project_id': 'project-1',
                'runtime_dir': str(runtime),
                'completion_artifact_dir': str(runtime / 'completion'),
                'work_dir': str(work_dir),
                'dsh_session_id': 'session-native-1',
                'dsh_session_file_path': str(session_file),
                'dsh_endpoint_state_path': str(endpoint_state),
                'dsh_host_instance_id': 'dsh-host-test-1',
                'dsh_home': str(state / 'home'),
                'dsh_data_dir': str(state / 'data'),
                'dsh_model_provider': 'deepseek-official',
                'dsh_model': 'deepseek-v4-flash',
                'dsh_reasoning_effort': 'high',
                'dsh_auto_permission_enabled': False,
            }
        ),
        encoding='utf-8',
    )
    return session_file


def _context(work_dir: Path) -> ProviderRuntimeContext:
    return ProviderRuntimeContext(
        agent_name='dsh1',
        workspace_path=str(work_dir),
        backend_type='pane-backed',
        runtime_ref='%1',
        session_ref=str(work_dir / '.ccb' / session_filename_for_agent('dsh', 'dsh1')),
    )


def test_dsh_reducer_requires_exact_rpc_turn_committed_reply_and_completed_end() -> None:
    reducer = DshTurnReducer(session_id='session-1', rpc_id='job-1')

    reducer.apply(_event('turn/start', 1, {'turn': 4}))
    reducer.apply(
        _event(
            'user/message',
            2,
            {
                'content': [{'type': 'text', 'text': 'CCB_REQ_ID: other\n\nold'}],
                'source': {'kind': 'user', 'rpcId': 'other'},
            },
            surfaceOp='append',
        )
    )
    assert reducer.anchor_seen is False

    reducer.apply(
        _event(
            'user/message',
            3,
            {
                'content': [{'type': 'text', 'text': 'CCB_REQ_ID: job-1\n\nwork'}],
                'source': {'kind': 'user', 'rpcId': 'job-1'},
            },
            surfaceOp='append',
        )
    )
    reducer.apply(
        _event(
            'assistant/message',
            4,
            {
                'turn': 4,
                'step': 1,
                'message': {
                    'id': 'assistant-1',
                    'role': 'assistant',
                    'content': [{'type': 'text', 'text': 'DSH_OK'}],
                    'source': {
                        'kind': 'model',
                        'provider': 'deepseek-official',
                        'model': 'deepseek-v4-flash',
                    },
                },
            },
            surfaceOp='append',
        )
    )
    reducer.apply(_event('turn/end', 5, {'turn': 4, 'reason': {'kind': 'completed'}}))

    observed = reducer.observation()
    assert observed['anchor_seen'] is True
    assert observed['reply'] == 'DSH_OK'
    assert observed['finished'] is True
    assert observed['finish_reason'] == 'completed'
    assert observed['outcome_reason'] == 'stop'
    assert observed['error'] == ''


def test_dsh_reducer_fails_closed_for_noncompleted_native_terminal() -> None:
    reducer = DshTurnReducer(session_id='session-1', rpc_id='job-1')
    reducer.apply(_event('turn/start', 1, {'turn': 1}))
    reducer.apply(
        _event(
            'user/message',
            2,
            {
                'content': [{'type': 'text', 'text': 'CCB_REQ_ID: job-1\n\nwork'}],
                'source': {'kind': 'user', 'rpcId': 'job-1'},
            },
            surfaceOp='append',
        )
    )
    reducer.apply(_event('turn/end', 3, {'turn': 1, 'reason': {'kind': 'max-tokens'}}))

    observed = reducer.observation()
    assert observed['finished'] is True
    assert observed['finish_reason'] == 'max-tokens'
    assert observed['error'] == 'dsh_turn_max_tokens'


@pytest.mark.parametrize(
    'terminal_kind',
    ('aborted', 'blocked', 'error', 'max-tokens', 'interrupted'),
)
def test_dsh_reducer_rejects_every_native_failure_terminal(
    terminal_kind: str,
) -> None:
    reducer = DshTurnReducer(session_id='session-1', rpc_id='job-1')
    reducer.apply(_event('turn/start', 1, {'turn': 1}))
    reducer.apply(
        _event(
            'user/message',
            2,
            {
                'content': [{'type': 'text', 'text': 'CCB_REQ_ID: job-1\n\nwork'}],
                'source': {'kind': 'user', 'rpcId': 'job-1'},
            },
            surfaceOp='append',
        )
    )
    reducer.apply(
        _event(
            'turn/end',
            3,
            {'turn': 1, 'reason': {'kind': terminal_kind}},
        )
    )

    observed = reducer.observation()
    assert observed['finished'] is True
    assert observed['finish_reason'] == terminal_kind
    assert observed['outcome_reason'] == terminal_kind
    assert observed['error'] == f'dsh_turn_{terminal_kind.replace("-", "_")}'


def test_dsh_reducer_rejects_uncommitted_assistant_projection() -> None:
    reducer = DshTurnReducer(session_id='session-1', rpc_id='job-1')
    reducer.apply(_event('turn/start', 1, {'turn': 1}))
    reducer.apply(
        _event(
            'user/message',
            2,
            {
                'content': [{'type': 'text', 'text': 'CCB_REQ_ID: job-1\n\nwork'}],
                'source': {'kind': 'user', 'rpcId': 'job-1'},
            },
            surfaceOp='append',
        )
    )
    reducer.apply(
        _event(
            'assistant/message',
            3,
            {'turn': 1, 'content': [{'type': 'text', 'text': 'not committed'}]},
            surfaceOp='replace',
        )
    )

    assert reducer.protocol_error == 'dsh_assistant_message_not_committed_append'
    assert reducer.reply == ''


def test_dsh_reducer_does_not_treat_reasoning_as_a_reply() -> None:
    reducer = DshTurnReducer(session_id='session-1', rpc_id='job-1')
    reducer.apply(_event('turn/start', 1, {'turn': 1}))
    reducer.apply(
        _event(
            'user/message',
            2,
            {
                'content': [{'type': 'text', 'text': 'CCB_REQ_ID: job-1\n\nwork'}],
                'source': {'kind': 'user', 'rpcId': 'job-1'},
            },
            surfaceOp='append',
        )
    )
    reducer.apply(
        _event(
            'assistant/message',
            3,
            {
                'turn': 1,
                'step': 1,
                'message': {
                    'id': 'assistant-1',
                    'role': 'assistant',
                    'content': [{'type': 'reasoning', 'text': 'private reasoning'}],
                    'source': {'kind': 'model', 'provider': 'p', 'model': 'm'},
                },
            },
            surfaceOp='append',
        )
    )
    reducer.apply(_event('turn/end', 4, {'turn': 1, 'reason': {'kind': 'completed'}}))

    observed = reducer.observation()
    assert observed['finished'] is True
    assert observed['reply'] == ''


def test_dsh_command_writes_owner_only_exact_request(tmp_path: Path) -> None:
    work_dir = tmp_path / 'repo'
    work_dir.mkdir()
    session_file = _session(work_dir)
    session_data = json.loads(session_file.read_text(encoding='utf-8'))
    request = NativeCliExecutionRequest(
        provider='dsh',
        job=_job(work_dir),
        work_dir=work_dir,
        session_data=session_data,
        prompt='ignored generic wrapper',
        request_anchor='job_dsh_exact_1',
    )

    command = _build_command(request)

    request_path = Path(command[-1])
    payload = json.loads(request_path.read_text(encoding='utf-8'))
    assert command[-3:-1] == ['provider_backends.dsh.bridge', '--request']
    assert payload['rpc_id'] == 'job_dsh_exact_1'
    assert payload['session_id'] == 'session-native-1'
    assert payload['prompt'].startswith('CCB_REQ_ID: job_dsh_exact_1\n\n')
    assert payload['prompt'].endswith('Reply exactly: DSH_OK\n')
    assert request_path.stat().st_mode & 0o077 == 0


def test_dsh_launcher_starts_loopback_web_service_without_prompt_input(
    monkeypatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setattr(
        'provider_backends.dsh.launcher.provider_start_parts',
        lambda provider: ('dsh-test-bin',),
    )
    home = tmp_path / 'managed-home'
    data = tmp_path / 'managed-data'
    runtime = tmp_path / 'runtime'
    endpoint_state = runtime / 'dsh-host.json'
    for path in (home, data, runtime):
        path.mkdir(parents=True)
    prepared = {
        'dsh_home': str(home),
        'dsh_data_dir': str(data),
        'dsh_endpoint_state_path': str(endpoint_state),
        'dsh_host_instance_id': 'dsh-host-launch-test-1',
    }
    command = ParsedStartCommand(
        project=None,
        agent_names=('dsh1',),
        restore=True,
        auto_permission=False,
    )
    spec = SimpleNamespace(
        name='dsh1',
        startup_args=(),
        provider_command_template=None,
        env={},
    )

    rendered = build_start_cmd(
        command,
        spec,
        runtime,
        'ccb-launch-1',
        prepared_state=prepared,
    )

    assert 'provider_backends.dsh.host_runtime' in rendered
    assert '--instance-id dsh-host-launch-test-1' in rendered
    assert 'dsh-test-bin' in rendered
    assert '\"web\"' in rendered
    assert '\"--host\"' in rendered and '\"127.0.0.1\"' in rendered
    assert '\"--port\"' in rendered and '\"0\"' in rendered
    assert str(home) in rendered
    assert str(data / 'agents-home') in rendered
    assert prepared['dsh_agents_home'] == str(data / 'agents-home')
    assert f'PYTHONPATH={Path(__file__).resolve().parents[1] / "lib"}' in rendered
    assert 'CCB_REQ_ID' not in rendered
    assert 'session.prompt' not in rendered
    assert prepared['dsh_session_id'].startswith('session-')


def test_dsh_post_launch_fails_immediately_when_service_carrier_exits(
    monkeypatch,
    tmp_path: Path,
) -> None:
    state = tmp_path / 'dsh-host.json'
    state.write_text(
        json.dumps(
            {
                'record_type': 'dsh_host_state',
                'provider': 'dsh',
                'status': 'pending_launch',
                'host_instance_id': 'dsh-host-dead-test-1',
                'endpoint': None,
            }
        ),
        encoding='utf-8',
    )
    monkeypatch.setattr(
        'provider_backends.dsh.launcher.load_dsh_host_endpoint',
        lambda _path, **_kwargs: (_ for _ in ()).throw(RuntimeError('pending')),
    )
    backend = SimpleNamespace(is_pane_alive=lambda _pane: False)

    with pytest.raises(
        RuntimeError,
        match='dsh host carrier exited before readiness: pending',
    ):
        post_launch(
            backend,
            '%1',
            tmp_path,
            'launch-1',
            {
                'dsh_endpoint_state_path': str(state),
                'dsh_host_instance_id': 'dsh-host-dead-test-1',
            },
        )


def test_dsh_web_command_preserves_explicit_loopback_options() -> None:
    command = _dsh_web_command(
        ('dsh', 'web', '--host=127.0.0.1', '--port', '43125')
    )

    assert command == [
        'dsh',
        'web',
        '--host=127.0.0.1',
        '--port',
        '43125',
    ]


@pytest.mark.parametrize(
    'parts',
    (
        ('dsh', 'web', '--host', '0.0.0.0'),
        ('dsh', 'web', '--host=localhost'),
        ('dsh', 'web', '--host=::1'),
        ('dsh', 'web', '--host='),
    ),
)
def test_dsh_web_command_rejects_noncanonical_host_binding(parts) -> None:
    with pytest.raises(ValueError, match='127\\.0\\.0\\.1|requires a value'):
        _dsh_web_command(parts)


def test_dsh_restore_accepts_only_exact_authority_binding(tmp_path: Path) -> None:
    workspace = tmp_path / 'workspace'
    workspace.mkdir()
    session = tmp_path / '.dsh-session'
    session.write_text(
        json.dumps(
            {
                'provider': 'dsh',
                'ccb_project_id': 'project-1',
                'agent_name': 'dsh1',
                'work_dir': str(workspace),
                'dsh_provider_authority_fingerprint': 'authority-1',
                'dsh_session_id': 'session-native-1',
                'dsh_context_generation': 3,
            }
        ),
        encoding='utf-8',
    )

    exact = _resume_candidate(
        session,
        project_id='project-1',
        agent_name='dsh1',
        workspace_path=workspace,
        authority_fingerprint='authority-1',
    )

    assert exact == ('session-native-1', 3)
    assert _resume_candidate(
        session,
        project_id='project-other',
        agent_name='dsh1',
        workspace_path=workspace,
        authority_fingerprint='authority-1',
    ) is None
    assert _resume_candidate(
        session,
        project_id='project-1',
        agent_name='dsh-other',
        workspace_path=workspace,
        authority_fingerprint='authority-1',
    ) is None
    assert _resume_candidate(
        session,
        project_id='project-1',
        agent_name='dsh1',
        workspace_path=tmp_path / 'other-workspace',
        authority_fingerprint='authority-1',
    ) is None
    assert _resume_candidate(
        session,
        project_id='project-1',
        agent_name='dsh1',
        workspace_path=workspace,
        authority_fingerprint='authority-other',
    ) is None


def test_dsh_model_selection_resolves_current_model_for_reasoning_only(
    monkeypatch,
) -> None:
    calls: list[tuple[str, dict[str, object], str]] = []

    async def rpc(_client, _endpoint, method, payload, *, rpc_id):
        calls.append((method, dict(payload), rpc_id))
        if method == 'session.models':
            return {
                'current': {
                    'provider': 'deepseek-official',
                    'model': 'deepseek-v4-flash',
                }
            }
        return {}

    monkeypatch.setattr(dsh_bridge, '_rpc', rpc)
    asyncio.run(
        dsh_bridge._select_model(
            object(),
            'http://127.0.0.1:43125',
            {
                'session_id': 'session-native-1',
                'model_provider': 'deepseek-official',
                'model': '',
                'reasoning_effort': 'max',
            },
            rpc_id='job-1',
        )
    )

    assert [call[0] for call in calls] == ['session.models', 'session.selectModel']
    assert calls[-1][1] == {
        'sessionId': 'session-native-1',
        'provider': 'deepseek-official',
        'model': 'deepseek-v4-flash',
        'reasoningEffort': 'max',
    }


def test_dsh_interactive_question_uses_native_cancelled_response() -> None:
    calls: list[tuple[str, dict[str, object]]] = []

    class Response:
        status = 200

        async def __aenter__(self):
            return self

        async def __aexit__(self, _exc_type, _exc, _tb):
            return False

        async def text(self):
            return '{"accepted":true}'

    class Client:
        def post(self, url, *, json):
            calls.append((url, dict(json)))
            return Response()

    asyncio.run(
        dsh_bridge._respond_cancelled(
            Client(),
            'http://127.0.0.1:43125',
            rpc_id='question-rpc-1',
            message='CCB cannot answer an interactive DSH question',
        )
    )

    assert calls == [
        (
            'http://127.0.0.1:43125/api/respond',
            {
                'type': 'client-response',
                'rpcId': 'question-rpc-1',
                'result': {
                    'ok': False,
                    'error': {
                        'code': 'cancelled',
                        'message': 'CCB cannot answer an interactive DSH question',
                        'details': {},
                    },
                },
            },
        )
    ]


def test_dsh_execution_completes_only_from_native_observation(monkeypatch, tmp_path: Path) -> None:
    work_dir = tmp_path / 'repo'
    work_dir.mkdir()
    _session(work_dir)

    class Proc:
        pid = 812345
        returncode = None

        def poll(self):
            return self.returncode

        def terminate(self):
            self.returncode = 0

        def kill(self):
            self.returncode = -9

    proc = Proc()
    monkeypatch.setattr(
        'provider_backends.native_cli_support.execution.subprocess.Popen',
        lambda *args, **kwargs: proc,
    )
    adapter = build_execution_adapter()
    submission = adapter.start(
        _job(work_dir),
        context=_context(work_dir),
        now='2026-08-14T00:00:00Z',
    )
    stdout = Path(str(submission.runtime_state['stdout_path']))
    stdout.write_text(
        json.dumps(
            {
                'type': 'dsh/observation',
                'session_id': 'session-native-1',
                'rpc_id': 'job_dsh_exact_1',
                'anchor_seen': True,
                'turn': 2,
                'reply': 'DSH_OK',
                'finished': True,
                'finish_reason': 'completed',
                'outcome_reason': 'stop',
                'completed_at': 1_800_000_000_000,
                'error': '',
                'protocol_error': '',
            }
        )
        + '\n',
        encoding='utf-8',
    )

    result = adapter.poll(submission, now='2026-08-14T00:00:01Z')

    assert result is not None
    assert result.decision is not None
    assert result.decision.status is CompletionStatus.COMPLETED
    assert result.decision.confidence is CompletionConfidence.EXACT
    assert result.decision.reason == 'dsh_native_turn_completed'
    assert result.decision.reply == 'DSH_OK'
    assert [item.kind for item in result.items] == [
        CompletionItemKind.ANCHOR_SEEN,
        CompletionItemKind.ASSISTANT_FINAL,
        CompletionItemKind.TURN_BOUNDARY,
    ]


def test_dsh_native_failure_terminal_is_exact_and_emits_turn_boundary(
    monkeypatch,
    tmp_path: Path,
) -> None:
    work_dir = tmp_path / 'repo'
    work_dir.mkdir()
    _session(work_dir)

    class Proc:
        pid = 812348
        returncode = None

        def poll(self):
            return self.returncode

        def terminate(self):
            self.returncode = 0

        def kill(self):
            self.returncode = -9

    monkeypatch.setattr(
        'provider_backends.native_cli_support.execution.subprocess.Popen',
        lambda *args, **kwargs: Proc(),
    )
    adapter = build_execution_adapter()
    submission = adapter.start(
        _job(work_dir),
        context=_context(work_dir),
        now='2026-08-14T00:00:00Z',
    )
    Path(str(submission.runtime_state['stdout_path'])).write_text(
        json.dumps(
            {
                'type': 'dsh/observation',
                'session_id': 'session-native-1',
                'rpc_id': 'job_dsh_exact_1',
                'anchor_seen': True,
                'turn': 2,
                'reply': '',
                'finished': True,
                'finish_reason': 'error',
                'outcome_reason': 'error',
                'error': 'dsh_turn_error',
                'protocol_error': '',
            }
        )
        + '\n',
        encoding='utf-8',
    )

    result = adapter.poll(submission, now='2026-08-14T00:00:01Z')

    assert result is not None and result.decision is not None
    assert result.decision.status is CompletionStatus.FAILED
    assert result.decision.confidence is CompletionConfidence.EXACT
    assert result.decision.reason == 'dsh_native_turn_failed'
    assert [item.kind for item in result.items] == [
        CompletionItemKind.ANCHOR_SEEN,
        CompletionItemKind.TURN_BOUNDARY,
    ]


def test_dsh_process_exit_with_reply_is_not_completion(monkeypatch, tmp_path: Path) -> None:
    work_dir = tmp_path / 'repo'
    work_dir.mkdir()
    _session(work_dir)

    class Proc:
        pid = 812346
        returncode = 0

        def poll(self):
            return self.returncode

        def terminate(self):
            return None

        def kill(self):
            return None

    monkeypatch.setattr(
        'provider_backends.native_cli_support.execution.subprocess.Popen',
        lambda *args, **kwargs: Proc(),
    )
    adapter = build_execution_adapter()
    submission = adapter.start(
        _job(work_dir),
        context=_context(work_dir),
        now='2026-08-14T00:00:00Z',
    )
    stdout = Path(str(submission.runtime_state['stdout_path']))
    stdout.write_text(
        json.dumps(
            {
                'type': 'dsh/observation',
                'session_id': 'session-native-1',
                'rpc_id': 'job_dsh_exact_1',
                'anchor_seen': True,
                'turn': 2,
                'reply': 'text without turn/end',
                'finished': False,
                'finish_reason': '',
                'outcome_reason': '',
                'error': '',
                'protocol_error': '',
            }
        )
        + '\n',
        encoding='utf-8',
    )

    result = adapter.poll(submission, now='2026-08-14T00:00:01Z')

    assert result is not None and result.decision is not None
    assert result.decision.status is CompletionStatus.INCOMPLETE
    assert result.decision.reason == 'dsh_native_turn_end_missing'
    assert result.decision.reply == 'text without turn/end'


def test_dsh_completed_turn_without_native_anchor_is_not_completion(
    monkeypatch,
    tmp_path: Path,
) -> None:
    work_dir = tmp_path / 'repo'
    work_dir.mkdir()
    _session(work_dir)

    class Proc:
        pid = 812347
        returncode = None

        def poll(self):
            return self.returncode

        def terminate(self):
            self.returncode = 0

        def kill(self):
            self.returncode = -9

    monkeypatch.setattr(
        'provider_backends.native_cli_support.execution.subprocess.Popen',
        lambda *args, **kwargs: Proc(),
    )
    adapter = build_execution_adapter()
    submission = adapter.start(
        _job(work_dir),
        context=_context(work_dir),
        now='2026-08-14T00:00:00Z',
    )
    stdout = Path(str(submission.runtime_state['stdout_path']))
    stdout.write_text(
        json.dumps(
            {
                'type': 'dsh/observation',
                'session_id': 'session-native-1',
                'rpc_id': 'job_dsh_exact_1',
                'anchor_seen': False,
                'turn': 2,
                'reply': 'unbound reply',
                'finished': True,
                'finish_reason': 'completed',
                'outcome_reason': 'stop',
                'error': '',
                'protocol_error': '',
            }
        )
        + '\n',
        encoding='utf-8',
    )

    result = adapter.poll(submission, now='2026-08-14T00:00:01Z')

    assert result is not None and result.decision is not None
    assert result.decision.status is CompletionStatus.INCOMPLETE
    assert result.decision.reason == 'dsh_native_protocol_invalid'
    assert result.decision.diagnostics['protocol_error'] == 'provider_native_anchor_missing'


def test_dsh_resume_starts_observer_only_without_reposting(monkeypatch, tmp_path: Path) -> None:
    work_dir = tmp_path / 'repo'
    work_dir.mkdir()
    _session(work_dir)
    commands: list[list[str]] = []

    class Proc:
        next_pid = 900000

        def __init__(self):
            type(self).next_pid += 1
            self.pid = type(self).next_pid
            self.returncode = None

        def poll(self):
            return self.returncode

        def terminate(self):
            self.returncode = 0

        def kill(self):
            self.returncode = -9

    def popen(command, *args, **kwargs):
        del args, kwargs
        commands.append(list(command))
        return Proc()

    monkeypatch.setattr(
        'provider_backends.native_cli_support.execution.subprocess.Popen',
        popen,
    )
    monkeypatch.setattr(
        'provider_backends.native_cli_support.execution._pid_is_live',
        lambda pid: False,
    )
    adapter = build_execution_adapter()
    job = _job(work_dir)
    submission = adapter.start(job, context=_context(work_dir), now='2026-08-14T00:00:00Z')

    resumed = adapter.resume(
        job,
        submission,
        context=_context(work_dir),
        persisted_state=SimpleNamespace(),
        now='2026-08-14T00:00:02Z',
    )

    assert resumed is not None
    assert len(commands) == 2
    assert '--observe-only' not in commands[0]
    assert commands[1][-1] == '--observe-only'
    assert resumed.runtime_state['resume_mode'] == 'observe_only'
    adapter.cancel(resumed)


def test_observe_dsh_output_rejects_identityless_fallback(tmp_path: Path) -> None:
    output = tmp_path / 'out.jsonl'
    output.write_text(
        json.dumps(
            {
                'type': 'dsh/observation',
                'session_id': '',
                'rpc_id': '',
                'anchor_seen': False,
                'finished': False,
                'reply': '',
                'error': 'dsh_bridge_failed:RuntimeError',
            }
        )
        + '\n',
        encoding='utf-8',
    )

    observed = observe_dsh_output(output)

    assert observed.error.startswith('dsh_bridge_failed')
    assert observed.protocol_error == 'dsh_observation_identity_missing'


def test_dsh_home_projection_is_allowlisted_and_one_way(tmp_path: Path) -> None:
    source = tmp_path / 'source-dsh'
    target = tmp_path / 'managed-dsh'
    (source / 'sessions').mkdir(parents=True)
    (source / '.credentials.yaml').write_text('DEEPSEEK_API_KEY: secret\n', encoding='utf-8')
    (source / '.env').write_text('DEEPSEEK_BASE_URL=https://example.test\n', encoding='utf-8')
    (source / 'settings.yaml').write_text('generation: 1\n', encoding='utf-8')
    (source / 'sessions' / 'old.jsonl').write_text('{}\n', encoding='utf-8')

    materialize_dsh_home(target, source_home=source)

    assert (target / '.credentials.yaml').is_file()
    assert (target / '.env').is_file()
    assert (target / 'settings.yaml').is_file()
    assert not (target / 'sessions').exists()
    assert all((target / 'skills' / name / 'SKILL.md').is_file() for name in (
        'ask', 'ccb-clear', 'ccb-compact', 'ccb-diagnose'
    ))
    (target / '.credentials.yaml').write_text('managed\n', encoding='utf-8')
    assert source.joinpath('.credentials.yaml').read_text(encoding='utf-8').startswith('DEEPSEEK')


def test_dsh_authority_fingerprint_tracks_nested_auth_and_api_only(
    monkeypatch,
    tmp_path: Path,
) -> None:
    source_home = tmp_path / 'source-home'
    dsh_home = source_home / '.dsh'
    dsh_home.mkdir(parents=True)
    credentials = dsh_home / '.credentials.yaml'
    dotenv = dsh_home / '.env'
    settings = dsh_home / 'settings.yaml'
    credentials.write_text('DEEPSEEK_API_KEY: key-a\n', encoding='utf-8')
    dotenv.write_text('DEEPSEEK_BASE_URL=https://route-a.example\n', encoding='utf-8')
    settings.write_text('theme: dark\n', encoding='utf-8')
    runtime = (
        tmp_path
        / 'repo'
        / '.ccb'
        / 'agents'
        / 'dsh1'
        / 'provider-runtime'
        / 'dsh'
    )
    runtime.mkdir(parents=True)
    monkeypatch.setattr(
        session_authority,
        'current_provider_source_home',
        lambda: source_home,
    )

    first = session_authority.current_provider_authority_fingerprint(
        'dsh',
        None,
        runtime,
    )
    settings.write_text('theme: light\n', encoding='utf-8')
    config_only = session_authority.current_provider_authority_fingerprint(
        'dsh',
        None,
        runtime,
    )
    dotenv.write_text('DEEPSEEK_BASE_URL=https://route-b.example\n', encoding='utf-8')
    route_changed = session_authority.current_provider_authority_fingerprint(
        'dsh',
        None,
        runtime,
    )
    credentials.write_text('DEEPSEEK_API_KEY: key-b\n', encoding='utf-8')
    auth_changed = session_authority.current_provider_authority_fingerprint(
        'dsh',
        None,
        runtime,
    )

    assert config_only == first
    assert route_changed != first
    assert auth_changed != route_changed


def test_dsh_control_validates_loopback_rotates_and_compacts(monkeypatch, tmp_path: Path) -> None:
    state = tmp_path / 'dsh-host.json'
    state.write_text(
        json.dumps(
            {
                'record_type': 'dsh_host_state',
                'status': 'ready',
                'host_instance_id': 'dsh-host-control-test-1',
                'endpoint': 'http://127.0.0.1:45160',
            }
        ),
        encoding='utf-8',
    )
    session = tmp_path / '.dsh-session'
    session.write_text(
        json.dumps(
            {
                'provider': 'dsh',
                'dsh_session_id': 'session-old',
                'dsh_context_generation': 2,
                'dsh_endpoint_state_path': str(state),
                'dsh_host_instance_id': 'dsh-host-control-test-1',
                'work_dir': str(tmp_path),
            }
        ),
        encoding='utf-8',
    )
    calls: list[tuple[str, dict]] = []

    def rpc(endpoint, method, payload, **kwargs):
        del endpoint, kwargs
        calls.append((method, payload))
        if method == 'commands/execute':
            return {
                'commandId': 'cmd-native-compact-1',
                'result': {'kind': 'success', 'text': 'Compacted.'},
            }
        return {'sessionId': 'session-old'}

    monkeypatch.setattr('provider_backends.dsh.control.dsh_rpc', rpc)

    assert load_dsh_host_endpoint(state) == 'http://127.0.0.1:45160'
    assert load_dsh_host_endpoint(
        state,
        expected_instance_id='dsh-host-control-test-1',
    ) == 'http://127.0.0.1:45160'
    with pytest.raises(RuntimeError, match='another launch instance'):
        load_dsh_host_endpoint(
            state,
            expected_instance_id='dsh-host-stale-test-1',
        )
    compacted = compact_dsh_session(session)
    rotated = rotate_dsh_session(session)

    assert compacted == {
        'session_id': 'session-old',
        'command': '/compact',
        'command_id': 'cmd-native-compact-1',
        'detail': 'Compacted.',
    }
    assert calls[-1] == (
        'commands/execute',
        {'args': {'agentId': 'session-old', 'line': '/compact'}},
    )
    assert rotated['old_session_id'] == 'session-old'
    assert rotated['session_id'] != 'session-old'
    assert rotated['context_generation'] == 3
    persisted = json.loads(session.read_text(encoding='utf-8'))
    assert persisted['dsh_session_id'] == rotated['session_id']
    assert persisted['dsh_context_generation'] == 3
    assert persisted['dsh_resume_status'] == 'context_rotated'


def test_dsh_replaced_host_wrapper_cannot_overwrite_new_instance_state(
    tmp_path: Path,
) -> None:
    state = tmp_path / 'dsh-host.json'
    state.write_text(
        json.dumps(
            {
                'record_type': 'dsh_host_state',
                'host_instance_id': 'dsh-host-current-1',
                'status': 'pending_launch',
            }
        ),
        encoding='utf-8',
    )

    assert _owns_state(state, 'dsh-host-current-1') is True
    assert _owns_state(state, 'dsh-host-replaced-1') is False


@pytest.mark.parametrize(
    'endpoint',
    (
        'http://example.com:45160',
        'https://127.0.0.1:45160',
        'http://user:password@127.0.0.1:45160',
        'http://127.0.0.1:45160/api',
        'http://127.0.0.1',
    ),
)
def test_dsh_control_rejects_non_loopback_or_ambiguous_endpoint(
    endpoint: str,
    tmp_path: Path,
) -> None:
    state = tmp_path / 'dsh-host.json'
    state.write_text(
        json.dumps(
            {
                'record_type': 'dsh_host_state',
                'status': 'ready',
                'endpoint': endpoint,
            }
        ),
        encoding='utf-8',
    )

    with pytest.raises(RuntimeError):
        load_dsh_host_endpoint(state)
