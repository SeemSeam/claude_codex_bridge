from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
import uuid

from agents.models import ProviderProfileSpec
from completion.models import CompletionItemKind, CompletionSourceKind, CompletionStatus
from project.ids import compute_project_id
from provider_backends.grok import home as grok_home
from provider_backends.grok import pane_execution
from provider_backends.grok.execution import _build_command, _build_env, observe_grok_output
from provider_backends.grok.pane_execution import GrokPaneExecutionAdapter
from provider_backends.grok.skills import grok_ccb_skills_ready, materialize_grok_skills
from provider_backends.native_cli_support import NativeCliExecutionRequest
from provider_backends.native_cli_support.prompt import wrap_native_prompt
from provider_execution.base import ProviderRuntimeContext


def _req(prompt: str = 'review this', work_dir: str = '/tmp/wd', session_data: dict | None = None) -> NativeCliExecutionRequest:
    return NativeCliExecutionRequest(
        provider='grok',
        job=SimpleNamespace(job_id='job_grok_1'),
        work_dir=Path(work_dir),
        session_data=session_data or {},
        prompt=prompt,
        request_anchor='job_grok_1',
    )


def test_grok_command_builds_headless_streaming_invocation(monkeypatch) -> None:
    monkeypatch.delenv('CCB_GROK_MODEL', raising=False)
    monkeypatch.delenv('CCB_GROK_EFFORT', raising=False)

    cmd = _build_command(_req(prompt='hello', work_dir='/tmp/repo'))

    assert '-p' in cmd and cmd[cmd.index('-p') + 1] == 'hello'
    assert cmd[cmd.index('--cwd') + 1] == '/tmp/repo'
    assert cmd[cmd.index('--output-format') + 1] == 'streaming-json'
    session_id = cmd[cmd.index('--session-id') + 1]
    assert str(uuid.UUID(session_id)) == session_id
    assert '-m' not in cmd
    assert '--reasoning-effort' not in cmd


def test_grok_command_injects_model_and_effort_from_env(monkeypatch) -> None:
    monkeypatch.setenv('CCB_GROK_MODEL', 'grok-composer-2.5-fast')
    monkeypatch.setenv('CCB_GROK_EFFORT', 'low')

    cmd = _build_command(_req())

    assert cmd[cmd.index('-m') + 1] == 'grok-composer-2.5-fast'
    assert cmd[cmd.index('--reasoning-effort') + 1] == 'low'


def test_grok_command_session_data_overrides_env(monkeypatch) -> None:
    monkeypatch.setenv('CCB_GROK_MODEL', 'grok-composer-2.5-fast')
    monkeypatch.setenv('CCB_GROK_EFFORT', 'low')

    cmd = _build_command(_req(session_data={'grok_model': 'grok-4.5', 'grok_effort': 'xhigh'}))

    assert cmd[cmd.index('-m') + 1] == 'grok-4.5'
    assert cmd[cmd.index('--reasoning-effort') + 1] == 'xhigh'


def test_grok_skills_project_per_home_even_when_optional_inheritance_is_disabled(tmp_path: Path) -> None:
    home = tmp_path / 'managed-home'
    bundled = home / '.grok' / 'skills' / 'help' / 'SKILL.md'
    bundled.parent.mkdir(parents=True)
    bundled.write_text('provider help\n', encoding='utf-8')

    active = materialize_grok_skills(home, profile=ProviderProfileSpec(inherit_skills=True))
    repeated = materialize_grok_skills(home, profile=ProviderProfileSpec(inherit_skills=True))

    assert active == ('ask', 'ccb-clear', 'ccb-compact', 'ccb-diagnose')
    assert repeated == active
    assert grok_ccb_skills_ready(home) is True
    for skill_name in active:
        assert (home / '.grok' / 'skills' / skill_name / 'SKILL.md').is_file()
        assert (home / '.grok' / 'skills' / f'{skill_name}.ccb-projection.json').is_file()
    assert bundled.read_text(encoding='utf-8') == 'provider help\n'

    disabled = materialize_grok_skills(home, profile=ProviderProfileSpec(inherit_skills=False))

    assert disabled == ('ask', 'ccb-clear', 'ccb-compact', 'ccb-diagnose')
    assert grok_ccb_skills_ready(home) is True
    assert (home / '.grok' / 'skills' / 'ask' / 'SKILL.md').is_file()
    assert (home / '.grok' / 'skills' / 'ccb-clear' / 'SKILL.md').is_file()
    assert bundled.read_text(encoding='utf-8') == 'provider help\n'


def test_grok_skill_projection_repairs_unmarked_control_skill_conflict(tmp_path: Path) -> None:
    home = tmp_path / 'managed-home'
    conflict = home / '.grok' / 'skills' / 'ask' / 'SKILL.md'
    conflict.parent.mkdir(parents=True)
    conflict.write_text('user owned ask\n', encoding='utf-8')

    active = materialize_grok_skills(home, profile=ProviderProfileSpec(inherit_skills=True))

    assert active == ('ask', 'ccb-clear', 'ccb-compact', 'ccb-diagnose')
    assert 'name: ask' in conflict.read_text(encoding='utf-8')
    assert (home / '.grok' / 'skills' / 'ask.ccb-projection.json').is_file()
    assert grok_ccb_skills_ready(home) is True


def test_grok_headless_command_and_env_use_managed_skills_and_exact_caller(
    monkeypatch,
    tmp_path: Path,
) -> None:
    source_home = tmp_path / 'source-home'
    (source_home / '.grok').mkdir(parents=True)
    monkeypatch.setattr(grok_home, 'current_provider_source_home', lambda: source_home)
    project = tmp_path / 'repo'
    runtime_dir = project / '.ccb' / 'agents' / 'grok1' / 'provider-runtime' / 'grok'
    home = project / '.ccb' / 'agents' / 'grok1' / 'provider-state' / 'grok' / 'home'
    session_data = {
        'agent_name': 'grok1',
        'runtime_dir': str(runtime_dir),
        'grok_home': str(home),
        'ccb_session_id': 'ccb-grok1-test',
        'grok_skill_permissions_enabled': True,
    }
    request = _req(work_dir=str(project), session_data=session_data)

    cmd = _build_command(request)
    env = _build_env(request)

    assert cmd.count('--allow') == 15
    assert 'Bash(command ask *)' in cmd
    assert 'Bash(command ccb clear*)' in cmd
    assert 'Bash(command ccb ping *)' in cmd
    assert 'Bash(command ccb repair *)' in cmd
    assert 'Bash(command ccb kill *)' not in cmd
    assert 'Bash(command tmux -S * capture-pane *)' in cmd
    assert 'Bash(command tmux -S * send-keys *)' not in cmd
    assert env['HOME'] == str(home)
    assert env['CCB_CALLER_ACTOR'] == 'grok1'
    assert env['CCB_CALLER_RUNTIME_DIR'] == str(runtime_dir)
    assert env['CCB_CALLER_PROJECT_ROOT'] == str(project)
    assert env['CCB_CALLER_PROJECT_ID'] == compute_project_id(project)
    assert env['CCB_SESSION_ID'] == 'ccb-grok1-test'


def test_grok_headless_command_does_not_allow_skill_commands_without_session_policy(
    monkeypatch,
    tmp_path: Path,
) -> None:
    source_home = tmp_path / 'source-home'
    (source_home / '.grok').mkdir(parents=True)
    monkeypatch.setattr(grok_home, 'current_provider_source_home', lambda: source_home)
    home = tmp_path / 'managed-home'

    cmd = _build_command(_req(session_data={'grok_home': str(home)}))

    assert '--allow' not in cmd


def test_grok_prompt_uses_request_anchor_without_semantic_done_marker() -> None:
    prompt = wrap_native_prompt('review this', 'job_grok_native_end')

    assert 'CCB_REQ_ID: job_grok_native_end' in prompt
    assert 'CCB_DONE' not in prompt
    assert 'CCB reply guidance:' not in prompt


class _FakeGrokSession:
    def __init__(self, home: Path) -> None:
        self.data = {'grok_home': str(home)}

    def ensure_pane(self) -> tuple[bool, str]:
        return True, '%9'


class _FakeGrokBackend:
    def __init__(self) -> None:
        self.sent: list[tuple[str, str]] = []

    def send_text_to_pane(self, pane_id: str, text: str) -> None:
        self.sent.append((pane_id, text))

    def is_tmux_pane_alive(self, pane_id: str) -> bool:
        return pane_id == '%9'


def _pane_job(*, message_type: str = 'ask', no_wrap: bool = False):
    return SimpleNamespace(
        job_id='job_grok_pane_1',
        agent_name='grok1',
        provider='grok',
        provider_instance=None,
        provider_options={'no_wrap': True} if no_wrap else {},
        request=SimpleNamespace(body='visible request', message_type=message_type),
    )


def _pane_context(tmp_path: Path) -> ProviderRuntimeContext:
    return ProviderRuntimeContext(
        agent_name='grok1',
        workspace_path=str(tmp_path),
        backend_type='pane-backed',
        runtime_ref='%9',
        session_ref=str(tmp_path / '.ccb' / '.grok-grok1-session'),
    )


def _write_pane_events(home: Path, events: list[dict]) -> None:
    path = home / '.grok' / 'sessions' / 'repo' / 'session-visible' / 'updates.jsonl'
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        ''.join(json.dumps(event, ensure_ascii=True) + '\n' for event in events),
        encoding='utf-8',
    )


def test_grok_pane_adapter_sends_to_visible_pane_and_finishes_from_native_turn_event(
    monkeypatch,
    tmp_path: Path,
) -> None:
    home = tmp_path / 'managed-home'
    backend = _FakeGrokBackend()
    session = _FakeGrokSession(home)
    monkeypatch.setattr(pane_execution, '_load_session', lambda work_dir, agent_name: session)
    monkeypatch.setattr(pane_execution, 'get_backend_for_session', lambda data: backend)
    adapter = GrokPaneExecutionAdapter()

    submission = adapter.start(
        _pane_job(),
        context=_pane_context(tmp_path),
        now='2026-07-13T00:00:00Z',
    )

    assert submission.source_kind is CompletionSourceKind.SESSION_EVENT_LOG
    assert submission.runtime_state['mode'] == 'grok_pane'
    assert backend.sent[0][0] == '%9'
    assert 'CCB_REQ_ID: job_grok_pane_1' in backend.sent[0][1]
    assert 'CCB_DONE' not in backend.sent[0][1]

    _write_pane_events(
        home,
        [
            {
                'method': 'session/update',
                'params': {
                    'sessionId': 'session-visible',
                    'update': {
                        'sessionUpdate': 'user_message_chunk',
                        'content': {'type': 'text', 'text': backend.sent[0][1]},
                    },
                },
            },
            {
                'method': 'session/update',
                'params': {
                    'sessionId': 'session-visible',
                    'update': {
                        'sessionUpdate': 'agent_message_chunk',
                        'content': {'type': 'text', 'text': 'visible reply'},
                    },
                },
                '_meta': {'promptId': 'prompt-visible'},
            },
            {
                'method': '_x.ai/session/update',
                'params': {
                    'sessionId': 'session-visible',
                    'update': {
                        'sessionUpdate': 'turn_completed',
                        'prompt_id': 'prompt-visible',
                        'stop_reason': 'end_turn',
                    },
                },
            },
        ],
    )

    result = adapter.poll(submission, now='2026-07-13T00:00:05Z')

    assert result is not None and result.decision is not None
    assert result.decision.status is CompletionStatus.COMPLETED
    assert result.decision.reason == 'grok_run_stop'
    assert result.decision.reply == 'visible reply'
    assert [item.kind for item in result.items] == [
        CompletionItemKind.ANCHOR_SEEN,
        CompletionItemKind.ASSISTANT_FINAL,
        CompletionItemKind.TURN_BOUNDARY,
    ]


def test_grok_reply_delivery_dispatches_and_holds_until_anchored_turn_end(
    monkeypatch,
    tmp_path: Path,
) -> None:
    home = tmp_path / 'managed-home'
    backend = _FakeGrokBackend()
    session = _FakeGrokSession(home)
    monkeypatch.setattr(pane_execution, '_load_session', lambda work_dir, agent_name: session)
    monkeypatch.setattr(pane_execution, 'get_backend_for_session', lambda data: backend)
    adapter = GrokPaneExecutionAdapter()

    submission = adapter.start(
        _pane_job(message_type='reply_delivery'),
        context=_pane_context(tmp_path),
        now='2026-07-13T00:00:00Z',
    )
    result = adapter.poll(submission, now='2026-07-13T00:00:01Z')

    assert backend.sent[0][0] == '%9'
    assert 'CCB_REQ_ID: job_grok_pane_1' in backend.sent[0][1]
    # Sending is transport only: no terminal decision at dispatch.
    assert result is None or result.decision is None

    _write_pane_events(
        home,
        [
            {
                'method': 'session/update',
                'params': {
                    'sessionId': 'session-visible',
                    'update': {
                        'sessionUpdate': 'user_message_chunk',
                        'content': {'type': 'text', 'text': backend.sent[0][1]},
                    },
                },
            },
            {
                'method': 'session/update',
                'params': {
                    'sessionId': 'session-visible',
                    'update': {
                        'sessionUpdate': 'agent_message_chunk',
                        'content': {'type': 'text', 'text': 'delivered seen'},
                    },
                },
                '_meta': {'promptId': 'prompt-visible'},
            },
            {
                'method': '_x.ai/session/update',
                'params': {
                    'sessionId': 'session-visible',
                    'update': {
                        'sessionUpdate': 'turn_completed',
                        'prompt_id': 'prompt-visible',
                        'stop_reason': 'end_turn',
                    },
                },
            },
        ],
    )

    held_submission = result.submission if result is not None else submission
    finished = adapter.poll(held_submission, now='2026-07-13T00:00:05Z')

    assert finished is not None and finished.decision is not None
    assert finished.decision.status is CompletionStatus.COMPLETED
    assert finished.decision.reason == 'grok_run_stop'


def test_grok_pane_adapter_preserves_compact_reply_mode_without_static_guidance(
    monkeypatch,
    tmp_path: Path,
) -> None:
    home = tmp_path / 'managed-home'
    backend = _FakeGrokBackend()
    session = _FakeGrokSession(home)
    monkeypatch.setattr(pane_execution, '_load_session', lambda work_dir, agent_name: session)
    monkeypatch.setattr(pane_execution, 'get_backend_for_session', lambda data: backend)
    job = _pane_job()
    job.request.body = 'visible request\n\nCCB_REPLY_MODE: compact'

    GrokPaneExecutionAdapter().start(
        job,
        context=_pane_context(tmp_path),
        now='2026-07-13T00:00:00Z',
    )

    assert backend.sent[0][1].count('CCB_REPLY_MODE: compact') == 1
    assert 'CCB reply guidance:' not in backend.sent[0][1]


def test_grok_observer_extracts_text_from_aggregated_json(tmp_path: Path) -> None:
    out = tmp_path / 'grok.out'
    out.write_text(
        json.dumps(
            {
                'text': 'alpha beta gamma',
                'stopReason': 'EndTurn',
                'sessionId': 'ses-1',
                'requestId': 'req-1',
                'thought': 'internal reasoning that must not leak into the reply',
            }
        ),
        encoding='utf-8',
    )

    obs = observe_grok_output(out)

    assert obs.error == ''
    assert obs.text == 'alpha beta gamma'
    assert obs.finished is True
    assert obs.finish_reason == 'end_turn'
    assert obs.turn_ref == 'req-1'


def test_grok_observer_preserves_multiline_text(tmp_path: Path) -> None:
    out = tmp_path / 'grok.out'
    out.write_text(
        json.dumps({'text': 'Apple\nBanana\nOrange', 'stopReason': 'EndTurn', 'requestId': 'r'}),
        encoding='utf-8',
    )

    obs = observe_grok_output(out)

    assert obs.text == 'Apple\nBanana\nOrange'


def test_grok_observer_empty_text_is_not_error(tmp_path: Path) -> None:
    out = tmp_path / 'grok.out'
    out.write_text(json.dumps({'text': '', 'stopReason': 'EndTurn', 'requestId': 'r'}), encoding='utf-8')

    obs = observe_grok_output(out)

    assert obs.text == ''
    assert obs.error == ''


def test_grok_observer_partial_json_waits_without_error(tmp_path: Path) -> None:
    out = tmp_path / 'grok.out'
    out.write_text('{"text": "half', encoding='utf-8')

    obs = observe_grok_output(out)

    assert obs.text == ''
    assert obs.error == ''


def test_grok_observer_extracts_native_streaming_json(tmp_path: Path) -> None:
    out = tmp_path / 'grok.jsonl'
    out.write_text(
        '\n'.join(
            [
                json.dumps({'type': 'thought', 'data': 'hidden'}, ensure_ascii=True),
                json.dumps({'type': 'text', 'data': 'alpha '}, ensure_ascii=True),
                json.dumps({'type': 'text', 'data': 'beta'}, ensure_ascii=True),
                json.dumps(
                    {
                        'type': 'end',
                        'stopReason': 'EndTurn',
                        'sessionId': 'ses-stream',
                        'requestId': 'req-stream',
                    },
                    ensure_ascii=True,
                ),
            ]
        )
        + '\n',
        encoding='utf-8',
    )

    obs = observe_grok_output(out)

    assert obs.error == ''
    assert obs.text == 'alpha beta'
    assert obs.finished is True
    assert obs.finish_reason == 'end_turn'
    assert obs.turn_ref == 'req-stream'


def test_grok_observer_treats_native_cancelled_end_as_terminal(tmp_path: Path) -> None:
    out = tmp_path / 'grok-cancelled.jsonl'
    out.write_text(
        json.dumps(
            {
                'type': 'end',
                'stopReason': 'Cancelled',
                'sessionId': 'ses-cancelled',
                'requestId': 'req-cancelled',
            },
            ensure_ascii=True,
        )
        + '\n',
        encoding='utf-8',
    )

    obs = observe_grok_output(out)

    assert obs.error == ''
    assert obs.text == ''
    assert obs.finished is True
    assert obs.finish_reason == 'cancelled'
    assert obs.turn_ref == 'req-cancelled'


def test_grok_observer_aggregated_cancelled_end_is_not_parser_error(tmp_path: Path) -> None:
    out = tmp_path / 'grok-cancelled.json'
    out.write_text(
        json.dumps({'text': '', 'stopReason': 'Cancelled', 'requestId': 'req-cancelled'}),
        encoding='utf-8',
    )

    obs = observe_grok_output(out)

    assert obs.error == ''
    assert obs.finished is True
    assert obs.finish_reason == 'cancelled'


def test_grok_observer_missing_file_is_empty(tmp_path: Path) -> None:
    obs = observe_grok_output(tmp_path / 'does-not-exist.out')

    assert obs.text == ''
    assert obs.error == ''
