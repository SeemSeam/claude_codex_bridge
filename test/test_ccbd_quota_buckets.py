from __future__ import annotations

from pathlib import Path

import pytest

from ccbd.api_models import DeliveryScope, JobStatus, MessageEnvelope
from ccbd.services.dispatcher import JobDispatcher
from ccbd.services.dispatcher_runtime.quota_buckets import (
    QuotaBuckets,
    bucket_key,
)
from ccbd.services.registry import AgentRegistry
from ccbd.services.runtime import RuntimeService
from project.ids import compute_project_id
from project.resolver import ProjectContext
from storage.paths import PathLayout

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
from completion.models import (
    CompletionConfidence,
    CompletionCursor,
    CompletionDecision,
    CompletionSourceKind,
    CompletionState,
    CompletionStatus,
)


_CLOCK = lambda: '2026-07-01T00:00:00Z'
_FUTURE_RETRY_AFTER = '2026-07-01T01:00:00Z'
_PAST_RETRY_AFTER = '2026-06-30T23:00:00Z'


def _bootstrap_test_project(project_root: Path) -> ProjectContext:
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


def _runtime(agent_name: str, *, project_id: str, layout: PathLayout) -> AgentRuntime:
    return AgentRuntime(
        agent_name=agent_name,
        state=AgentState.IDLE,
        pid=101,
        started_at=_CLOCK(),
        last_seen_at=_CLOCK(),
        runtime_ref=f'{agent_name}-runtime',
        session_ref=f'{agent_name}-session',
        workspace_path=str(layout.workspace_path(agent_name)),
        project_id=project_id,
        backend_type='tmux',
        queue_depth=0,
        socket_path=None,
        health='healthy',
    )


def _config(*agents: str, account: dict[str, str] | None = None, model: dict[str, str] | None = None) -> ProjectConfig:
    account = account or {}
    model = model or {}
    specs: dict[str, AgentSpec] = {}
    for name in agents:
        specs[name] = AgentSpec(
            name=name,
            provider='codex',
            target='.',
            workspace_mode=WorkspaceMode.GIT_WORKTREE,
            workspace_root=None,
            runtime_mode=RuntimeMode.PANE_BACKED,
            restore_default=RestoreMode.AUTO,
            permission_default=PermissionMode.MANUAL,
            queue_policy=QueuePolicy.SERIAL_PER_AGENT,
            model=model.get(name),
            account=account.get(name),
        )
    return ProjectConfig(version=2, default_agents=tuple(agents), agents=specs)


def _ask(dispatcher, to_agent: str) -> str:
    receipt = dispatcher.submit(
        MessageEnvelope(
            project_id=dispatcher._project_id,
            to_agent=to_agent,
            from_actor='user',
            body='hello',
            task_id='task-1',
            reply_to=None,
            message_type='ask',
            delivery_scope=DeliveryScope.SINGLE,
        )
    )
    return receipt.jobs[0].job_id


def _failed_decision(*, error_kind: str, retry_after: str | None = None) -> CompletionDecision:
    diagnostics = {'error_kind': error_kind}
    if retry_after is not None:
        diagnostics['retry_after'] = retry_after
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
        source_cursor=CompletionCursor(source_kind=CompletionSourceKind.PROTOCOL_EVENT_STREAM, opaque_cursor='att-1'),
        diagnostics=diagnostics,
        finished_at=_CLOCK(),
    )


# --- unit tests for QuotaBuckets -------------------------------------------------


def test_bucket_key_groups_by_provider_model_account() -> None:
    assert bucket_key('codex', 'gpt-5.5', 'team-a') == 'codex|gpt-5.5|team-a'


def test_bucket_key_falls_back_to_provider_model_when_account_missing() -> None:
    assert bucket_key('codex', 'gpt-5.5', None) == 'codex|gpt-5.5'


def test_bucket_key_falls_back_to_provider_when_model_and_account_missing() -> None:
    assert bucket_key('codex', None, None) == 'codex'


def test_quota_bucket_mark_degraded_and_release() -> None:
    buckets = QuotaBuckets(clock=_CLOCK)
    key = bucket_key('codex', 'gpt-5.5', None)
    buckets.mark_degraded(key, _FUTURE_RETRY_AFTER)
    assert buckets.is_degraded(key) is True
    assert buckets.degraded_until(key) == _FUTURE_RETRY_AFTER


def test_quota_bucket_released_after_retry_after_passes() -> None:
    buckets = QuotaBuckets(clock=_CLOCK)
    key = bucket_key('codex', 'gpt-5.5', None)
    buckets.mark_degraded(key, _PAST_RETRY_AFTER)
    assert buckets.is_degraded(key) is False


def test_quota_bucket_is_degraded_with_explicit_now() -> None:
    buckets = QuotaBuckets(clock=_CLOCK)
    key = bucket_key('codex', 'gpt-5.5', None)
    buckets.mark_degraded(key, '2026-07-01T00:30:00Z')
    assert buckets.is_degraded(key, now_iso='2026-07-01T00:29:00Z') is True
    assert buckets.is_degraded(key, now_iso='2026-07-01T00:31:00Z') is False


# --- dispatcher integration ------------------------------------------------------


def _make_dispatcher(tmp_path: Path, *, agents: tuple[str, ...] = ('demo',), account: dict[str, str] | None = None, model: dict[str, str] | None = None) -> JobDispatcher:
    project_root = tmp_path / 'repo'
    ctx = _bootstrap_test_project(project_root)
    layout = PathLayout(project_root)
    config = _config(*agents, account=account, model=model)
    registry = AgentRegistry(layout, config)
    for agent in agents:
        registry.upsert(_runtime(agent, project_id=ctx.project_id, layout=layout))
    dispatcher = JobDispatcher(layout, config, registry, clock=_CLOCK)
    dispatcher._project_id = ctx.project_id
    return dispatcher


def test_usage_limit_on_one_agent_degrades_bucket_for_same_model_account(tmp_path: Path) -> None:
    dispatcher = _make_dispatcher(
        tmp_path,
        agents=('agent-a', 'agent-b'),
        model={'agent-a': 'gpt-5.5', 'agent-b': 'gpt-5.5'},
    )
    job_a = _ask(dispatcher, 'agent-a')
    dispatcher.tick()

    dispatcher.complete(job_a, _failed_decision(error_kind='provider_usage_limit', retry_after=_FUTURE_RETRY_AFTER))

    key = bucket_key('codex', 'gpt-5.5', None)
    assert dispatcher._quota_buckets.is_degraded(key) is True


def test_degraded_bucket_skips_starting_jobs_for_same_bucket(tmp_path: Path) -> None:
    dispatcher = _make_dispatcher(
        tmp_path,
        agents=('agent-a', 'agent-b'),
        model={'agent-a': 'gpt-5.5', 'agent-b': 'gpt-5.5'},
    )
    job_a = _ask(dispatcher, 'agent-a')
    dispatcher.tick()
    dispatcher.complete(job_a, _failed_decision(error_kind='provider_usage_limit', retry_after=_FUTURE_RETRY_AFTER))

    job_b = _ask(dispatcher, 'agent-b')
    dispatcher.tick()

    current_b = dispatcher.get(job_b)
    assert current_b is not None
    assert current_b.status in {JobStatus.QUEUED, JobStatus.ACCEPTED}


def test_bucket_release_allows_job_to_start(tmp_path: Path) -> None:
    dispatcher = _make_dispatcher(
        tmp_path,
        agents=('agent-a', 'agent-b'),
        model={'agent-a': 'gpt-5.5', 'agent-b': 'gpt-5.5'},
    )
    job_a = _ask(dispatcher, 'agent-a')
    dispatcher.tick()
    dispatcher.complete(job_a, _failed_decision(error_kind='provider_usage_limit', retry_after=_FUTURE_RETRY_AFTER))

    # Move clock past retry_after and rebuild dispatcher state so the new tick sees the time.
    late_clock = lambda: '2026-07-02T00:00:00Z'
    dispatcher._runtime_state.clock = late_clock
    dispatcher._quota_buckets._clock = late_clock

    job_b = _ask(dispatcher, 'agent-b')
    dispatcher.tick()

    current_b = dispatcher.get(job_b)
    assert current_b is not None
    assert current_b.status is JobStatus.RUNNING


def test_different_model_not_affected_by_bucket_degradation(tmp_path: Path) -> None:
    dispatcher = _make_dispatcher(
        tmp_path,
        agents=('agent-a', 'agent-b'),
        model={'agent-a': 'gpt-5.5', 'agent-b': 'gpt-4'},
    )
    job_a = _ask(dispatcher, 'agent-a')
    dispatcher.tick()
    dispatcher.complete(job_a, _failed_decision(error_kind='provider_usage_limit', retry_after=_FUTURE_RETRY_AFTER))

    job_b = _ask(dispatcher, 'agent-b')
    dispatcher.tick()

    current_b = dispatcher.get(job_b)
    assert current_b is not None
    assert current_b.status is JobStatus.RUNNING


def test_account_splits_buckets(tmp_path: Path) -> None:
    dispatcher = _make_dispatcher(
        tmp_path,
        agents=('agent-a', 'agent-b'),
        model={'agent-a': 'gpt-5.5', 'agent-b': 'gpt-5.5'},
        account={'agent-a': 'acct-1', 'agent-b': 'acct-2'},
    )
    job_a = _ask(dispatcher, 'agent-a')
    dispatcher.tick()
    dispatcher.complete(job_a, _failed_decision(error_kind='provider_usage_limit', retry_after=_FUTURE_RETRY_AFTER))

    job_b = _ask(dispatcher, 'agent-b')
    dispatcher.tick()

    current_b = dispatcher.get(job_b)
    assert current_b is not None
    assert current_b.status is JobStatus.RUNNING


def test_agent_spec_account_is_optional_and_normalized() -> None:
    spec = AgentSpec(
        name='demo',
        provider='codex',
        target='.',
        workspace_mode=WorkspaceMode.GIT_WORKTREE,
        workspace_root=None,
        runtime_mode=RuntimeMode.PANE_BACKED,
        restore_default=RestoreMode.AUTO,
        permission_default=PermissionMode.MANUAL,
        queue_policy=QueuePolicy.SERIAL_PER_AGENT,
    )
    assert spec.account is None

    spec_with_account = AgentSpec(
        name='demo',
        provider='codex',
        target='.',
        workspace_mode=WorkspaceMode.GIT_WORKTREE,
        workspace_root=None,
        runtime_mode=RuntimeMode.PANE_BACKED,
        restore_default=RestoreMode.AUTO,
        permission_default=PermissionMode.MANUAL,
        queue_policy=QueuePolicy.SERIAL_PER_AGENT,
        account='  Team-A  ',
    )
    assert spec_with_account.account == 'Team-A'


def test_build_agent_spec_parses_account() -> None:
    from agents.config_loader_runtime.parsing_runtime.agent_specs import build_agent_spec

    raw = {
        'provider': 'codex',
        'target': '.',
        'workspace_mode': 'git-worktree',
        'restore': 'auto',
        'permission': 'manual',
        'model': 'gpt-5.5',
        'account': 'team-a',
    }
    spec = build_agent_spec('demo', raw)
    assert spec.account == 'team-a'
    assert spec.model == 'gpt-5.5'
