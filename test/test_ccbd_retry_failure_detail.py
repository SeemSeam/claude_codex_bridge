from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from ccbd.services.dispatcher import JobDispatcher
from ccbd.services.dispatcher_runtime.failure_policy import nonretryable_api_failure_kind
from ccbd.services.dispatcher_runtime.finalization_retry_runtime.details import retry_failure_detail
from ccbd.services.dispatcher_runtime.lifecycle import _raise_if_non_retryable
from ccbd.services.registry import AgentRegistry
from ccbd.services.runtime import RuntimeService
from project.ids import compute_project_id
from project.resolver import ProjectContext
from storage.paths import PathLayout

_LIB = Path(__file__).resolve().parents[1] / 'lib'
if str(_LIB) not in sys.path:
    sys.path.insert(0, str(_LIB))

from agents.models import (
    AgentRuntime,
    AgentSpec,
    AgentState,
    PermissionMode,
    ProjectConfig,
    QueuePolicy,
    RestoreMode,
    RuntimeMode,
    WorkspaceMode,
)
from ccbd.api_models import DeliveryScope, MessageEnvelope
from completion.models import (
    CompletionConfidence,
    CompletionCursor,
    CompletionDecision,
    CompletionSourceKind,
    CompletionStatus,
)


def _load_finish_hook_module():
    """Load bin/ccb-provider-finish-hook.py as a module (it has no package)."""
    hook_path = Path(__file__).resolve().parents[1] / 'bin' / 'ccb-provider-finish-hook.py'
    spec = importlib.util.spec_from_file_location('ccb_provider_finish_hook_test', hook_path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def test_retry_failure_detail_collects_reason_and_diagnostics() -> None:
    decision = SimpleNamespace(
        reason="api_error",
        diagnostics={
            "error_type": "timeout",
            "error_code": "408",
            "error_message": "request timed out",
            "fault_rule_id": "rule-1",
        },
    )

    detail = retry_failure_detail(decision)

    assert detail == (
        "reason=api_error, error_type=timeout, error_code=408, "
        "error_message=request timed out, fault_rule_id=rule-1"
    )


def test_retry_failure_detail_falls_back_to_default_reason() -> None:
    decision = SimpleNamespace(reason="", diagnostics={})

    assert retry_failure_detail(decision) == "reason=api_error"


# --- provider content-error diagnostics on empty/error turns -------------


def test_codex_delivery_pane_signal_tags_usage_limit_with_pane_tail_and_retry_after() -> None:
    from provider_backends.codex.execution import _delivery_pane_signal

    banner = (
        "You've hit your usage limit. Visit https://chatgpt.com to purchase "
        "more credits or try again at Jul 2nd, 2026 10:21 AM."
    )

    class _Backend:
        def get_pane_content(self, pane_id, lines=120):
            return banner

    state = {'backend': _Backend(), 'pane_id': '%9'}
    signal = _delivery_pane_signal(state)

    assert signal is not None
    assert signal['error_kind'] == 'provider_usage_limit'
    assert signal['pane_signal_state'] == 'usage_limit'
    assert signal['retry_after'] == '2026-07-02T10:21:00'
    assert 'pane_tail' in signal
    assert 'usage limit' in signal['pane_tail'].lower()


def test_codex_delivery_pane_signal_returns_none_when_no_content_error() -> None:
    from provider_backends.codex.execution import _delivery_pane_signal

    class _Backend:
        def get_pane_content(self, pane_id, lines=120):
            return "openai codex\n› ready prompt"

    state = {'backend': _Backend(), 'pane_id': '%9'}
    assert _delivery_pane_signal(state) is None


def test_codex_delivery_pane_signal_returns_none_without_backend_seam() -> None:
    from provider_backends.codex.execution import _delivery_pane_signal

    # No get_pane_content on the backend -> nothing to tag.
    state = {'backend': SimpleNamespace(), 'pane_id': '%9'}
    assert _delivery_pane_signal(state) is None
    # No pane id at all.
    state2 = {'backend': object(), 'pane_id': ''}
    assert _delivery_pane_signal(state2) is None


def test_finish_hook_empty_reply_diagnostics_infers_usage_limit_error_kind() -> None:
    hook = _load_finish_hook_module()

    diagnostics = hook._empty_reply_diagnostics(
        reason='hook_after_agent_incomplete',
        context_text="You've hit your usage limit. try again at Jul 2nd, 2026 10:21 AM.",
    )

    assert diagnostics['empty_reply'] is True
    assert diagnostics['error_type'] == 'empty_provider_reply'
    assert diagnostics['error_kind'] == 'provider_usage_limit'


def test_finish_hook_empty_reply_diagnostics_has_no_error_kind_for_bare_empty_reply() -> None:
    hook = _load_finish_hook_module()

    diagnostics = hook._empty_reply_diagnostics(reason='hook_stop_empty_reply', context_text='')

    assert diagnostics['empty_reply'] is True
    assert 'error_kind' not in diagnostics


def test_finish_hook_empty_reply_diagnostics_maps_auth_and_api_markers() -> None:
    hook = _load_finish_hook_module()

    auth = hook._empty_reply_diagnostics(
        reason='hook_stop_empty_reply', context_text='Authentication failed: unauthorized'
    )
    assert auth['error_kind'] == 'provider_auth_failed'

    api = hook._empty_reply_diagnostics(
        reason='hook_stop_empty_reply', context_text='rate limit exceeded, too many requests'
    )
    assert api['error_kind'] == 'provider_api_error'


def test_nonretryable_api_failure_kind_classifies_incomplete_usage_limit() -> None:
    decision = SimpleNamespace(
        status=SimpleNamespace(value='incomplete'),
        reason='model_empty_output',
        diagnostics={'error_kind': 'provider_usage_limit'},
    )

    assert nonretryable_api_failure_kind(decision) == 'billing'


def test_nonretryable_api_failure_kind_ignores_incomplete_without_error_kind() -> None:
    decision = SimpleNamespace(
        status=SimpleNamespace(value='incomplete'),
        reason='timeout',
        diagnostics={},
    )

    assert nonretryable_api_failure_kind(decision) is None


# --- manual retry fast-fail on non-retryable provider error kinds ---------------


def _bootstrap_project(project_root: Path) -> ProjectContext:
    project_root.mkdir()
    config_dir = project_root / '.ccb'
    config_dir.mkdir(exist_ok=True)
    (config_dir / 'ccb.config').write_text('cmd; demo:fake\n', encoding='utf-8')
    return ProjectContext(
        cwd=project_root,
        project_root=project_root,
        config_dir=config_dir,
        project_id=compute_project_id(project_root),
        source='test',
    )


def _config_for_retry(agent_name: str = 'demo') -> ProjectConfig:
    spec = AgentSpec(
        name=agent_name,
        provider='codex',
        target='.',
        workspace_mode=WorkspaceMode.GIT_WORKTREE,
        workspace_root=None,
        runtime_mode=RuntimeMode.PANE_BACKED,
        restore_default=RestoreMode.AUTO,
        permission_default=PermissionMode.MANUAL,
        queue_policy=QueuePolicy.SERIAL_PER_AGENT,
    )
    return ProjectConfig(version=2, default_agents=(agent_name,), agents={agent_name: spec})


def _runtime(agent_name: str, *, project_id: str, layout: PathLayout) -> AgentRuntime:
    return AgentRuntime(
        agent_name=agent_name,
        state=AgentState.IDLE,
        pid=101,
        started_at='2026-07-01T00:00:00Z',
        last_seen_at='2026-07-01T00:00:00Z',
        runtime_ref=f'{agent_name}-runtime',
        session_ref=f'{agent_name}-session',
        workspace_path=str(layout.workspace_path(agent_name)),
        project_id=project_id,
        backend_type='tmux',
        queue_depth=0,
        socket_path=None,
        health='healthy',
    )


def _failed_decision_with_error_kind(error_kind: str) -> CompletionDecision:
    return CompletionDecision(
        terminal=True,
        status=CompletionStatus.FAILED,
        reason='api_error',
        reply='',
        confidence=CompletionConfidence.EXACT,
        anchor_seen=False,
        reply_started=False,
        reply_stable=False,
        provider_turn_ref=None,
        source_cursor=CompletionCursor(source_kind=CompletionSourceKind.PROTOCOL_EVENT_STREAM),
        diagnostics={'error_kind': error_kind},
        finished_at='2026-07-01T00:00:00Z',
    )


@pytest.mark.parametrize(
    'error_kind',
    [
        'provider_usage_limit',
        'provider_auth_failed',
        'provider_auth_required',
        'provider_config_error',
    ],
)
def test_manual_retry_rejects_non_retryable_error_kind(tmp_path: Path, error_kind: str) -> None:
    project_root = tmp_path / 'repo'
    ctx = _bootstrap_project(project_root)
    layout = PathLayout(project_root)
    config = _config_for_retry('demo')
    registry = AgentRegistry(layout, config)
    registry.upsert(_runtime('demo', project_id=ctx.project_id, layout=layout))
    dispatcher = JobDispatcher(layout, config, registry, clock=lambda: '2026-07-01T00:00:00Z')
    dispatcher._project_id = ctx.project_id

    receipt = dispatcher.submit(
        MessageEnvelope(
            project_id=ctx.project_id,
            to_agent='demo',
            from_actor='user',
            body='hello',
            task_id='task-1',
            reply_to=None,
            message_type='ask',
            delivery_scope=DeliveryScope.SINGLE,
        )
    )
    job_id = receipt.jobs[0].job_id
    dispatcher.tick()
    dispatcher.complete(job_id, _failed_decision_with_error_kind(error_kind))

    with pytest.raises(Exception) as exc_info:
        dispatcher.retry(job_id)

    assert 'non-retryable' in str(exc_info.value).lower()
    assert error_kind in str(exc_info.value)


def test_raise_if_non_retryable_allows_retryable_error_kind() -> None:
    class _Attempt:
        attempt_id = 'att-1'
        message_id = 'msg-1'
        agent_name = 'demo'
        attempt_state = object()

    class _Dispatcher:
        _layout = SimpleNamespace()

        def _dispatch_error(self, msg: str):
            return RuntimeError(msg)

    dispatcher = _Dispatcher()
    # provider_api_error is retryable; should not raise.
    _raise_if_non_retryable(dispatcher, _Attempt())


def test_raise_if_non_retryable_blocks_non_retryable_error_kind() -> None:
    class _Attempt:
        attempt_id = 'att-1'
        message_id = 'msg-1'
        agent_name = 'demo'
        attempt_state = object()

    class _Dispatcher:
        _layout = SimpleNamespace()

        def _dispatch_error(self, msg: str):
            return RuntimeError(msg)

    class _Reply:
        attempt_id = 'att-1'
        diagnostics = {'error_kind': 'provider_usage_limit'}

    class _Store:
        def list_message(self, message_id: str):
            return [_Reply()]

    dispatcher = _Dispatcher()
    dispatcher._layout = SimpleNamespace()  # will be replaced by monkeypatch below
    # Monkeypatch ReplyStore to return our synthetic reply.
    from message_bureau import ReplyStore

    original_init = ReplyStore.__init__
    original_list_message = ReplyStore.list_message

    def _fake_init(self, layout):
        self._layout = layout

    def _fake_list_message(self, message_id: str):
        return [_Reply()]

    ReplyStore.__init__ = _fake_init
    ReplyStore.list_message = _fake_list_message
    try:
        with pytest.raises(RuntimeError, match='non-retryable: provider_usage_limit'):
            _raise_if_non_retryable(dispatcher, _Attempt())
    finally:
        ReplyStore.__init__ = original_init
        ReplyStore.list_message = original_list_message
