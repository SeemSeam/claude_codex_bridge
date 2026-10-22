"""A persisted OMP receipt releases only delivery, allowing the next reply head."""
from ccbd.api_models import JobStatus
from ccbd.services.dispatcher import JobDispatcher
from ccbd.services.registry import AgentRegistry
from provider_execution.base import ProviderSubmission
from provider_execution.service_runtime.models import ExecutionUpdate
from provider_backends.omp import reply_delivery
from storage.paths import PathLayout
from test_unified_message_fifo import (
    HoldingExecutionService, _bootstrap_test_project, _provider_config, _runtime,
    _ask, _turn_end_decision, _active_delivery_job,
)


def test_second_reply_received_while_native_business_turn_has_not_settled(tmp_path, monkeypatch):
    ctx = _bootstrap_test_project(tmp_path / 'project')
    layout = PathLayout(ctx.project_root)
    config = _provider_config('omp', 'claude', 'gemini')
    registry = AgentRegistry(layout, config)
    for i, name in enumerate(config.agents):
        registry.upsert(_runtime(name, project_id=ctx.project_id, layout=layout, pid=100+i))
    execution = HoldingExecutionService(provider='omp')
    dispatcher = JobDispatcher(layout, config, registry, execution_service=execution,
        auto_reply_delivery_on_complete=True, clock=lambda: '2026-10-22T00:00:00Z')
    sources = []
    for name in ('claude', 'gemini'):
        sources.append(dispatcher.submit(_ask(name, 'omp', name, project_id=ctx.project_id, task_id=name)).jobs[0])
    dispatcher.tick()
    for source in sources:
        dispatcher.complete(source.job_id, _turn_end_decision(reply=source.agent_name))
    dispatcher.tick()
    first = _active_delivery_job(dispatcher, 'omp')
    assert first and dispatcher.inbox('omp')['item_count'] == 2

    def received(job):
        state = {'reply_receipt_transport': True, 'draft_guard_socket': '/unused',
            'actor': 'omp', 'launch_session_id': 'launch', 'runtime_instance_id': 'runtime',
            'reply_receipt_session_id': 'native', 'reply_body_sha256': 'hash'}
        def request(*args, **kwargs):
            return {'state': 'received', 'runtime_instance_id': 'runtime', 'session_id': 'native',
                'delivery_id': job.job_id, 'body_sha256': 'hash'}
        monkeypatch.setattr(reply_delivery, 'request', request)
        submission = execution._active[job.job_id]
        result = reply_delivery.poll_receipt(submission, state, now='2026-10-22T00:00:01Z')
        execution._armed[job.job_id] = result.decision

    received(first)
    dispatcher.poll_completions()
    assert dispatcher.get(first.job_id).status is JobStatus.COMPLETED
    assert dispatcher.inbox('omp')['item_count'] == 1
    dispatcher.tick()
    second = _active_delivery_job(dispatcher, 'omp')
    assert second and second.job_id != first.job_id
    received(second)
    dispatcher.poll_completions()
    assert dispatcher.inbox('omp')['item_count'] == 0
    assert dispatcher.get(second.job_id).terminal_decision['diagnostics']['business_processing_complete'] is False
