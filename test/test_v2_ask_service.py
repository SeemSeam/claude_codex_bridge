from __future__ import annotations

from io import StringIO
import json
from pathlib import Path
import re
from types import SimpleNamespace

import pytest

from ccbd.api_models import DeliveryScope, JobRecord, JobStatus, MessageEnvelope
from ccbd.socket_client import CcbdClientError
from cli.context import CliContextBuilder
from cli.models import ParsedAskCommand, ParsedLoopRunnerCommand
from cli.services import ask as ask_service
from cli.services.watch_fallback import load_persisted_terminal_watch_payload
from cli.services.ask_runtime.submission import message_with_reply_guidance
from cli.services.daemon import CcbdServiceError
from completion.models import (
    CompletionConfidence,
    CompletionDecision,
    CompletionFamily,
    CompletionSnapshot,
    CompletionState,
    CompletionStatus,
)
from completion.snapshot_store import CompletionSnapshotStore
from jobs.store import JobStore
from message_bureau import AttemptRecord, AttemptState, AttemptStore, ReplyRecord, ReplyStore, ReplyTerminalStatus
from project.ids import compute_project_id
from provider_core.caller_env import caller_context_env
from storage.paths import PathLayout


@pytest.fixture(autouse=True)
def _clear_caller_project_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv('CCB_CALLER_ACTOR', raising=False)
    monkeypatch.delenv('CCB_CALLER_RUNTIME_DIR', raising=False)
    monkeypatch.delenv('CCB_CALLER_PROJECT_ROOT', raising=False)
    monkeypatch.delenv('CCB_CALLER_PROJECT_ID', raising=False)
    monkeypatch.delenv('CODEX_RUNTIME_DIR', raising=False)
    monkeypatch.delenv('CCB_SESSION_ID', raising=False)


def _build_context(project_root: Path) -> object:
    (project_root / '.ccb').mkdir(parents=True, exist_ok=True)
    (project_root / '.ccb' / 'ccb.config').write_text('cmd; agent1:codex, agent2:claude\n', encoding='utf-8')
    command = ParsedAskCommand(project=None, target='agent1', sender=None, message='hello')
    return CliContextBuilder().build(command, cwd=project_root, bootstrap_if_missing=False)


def _write_config(project_root: Path, text: str = 'cmd; agent1:codex, agent2:claude\n') -> None:
    (project_root / '.ccb').mkdir(parents=True, exist_ok=True)
    (project_root / '.ccb' / 'ccb.config').write_text(text, encoding='utf-8')


def test_submit_ask_rejects_unknown_target(tmp_path: Path) -> None:
    project_root = tmp_path / 'repo-ask-unknown-target'
    project_root.mkdir()
    context = _build_context(project_root)

    with pytest.raises(ValueError) as exc_info:
        ask_service.submit_ask(
            context,
            ParsedAskCommand(project=None, target='agent9', sender=None, message='hello'),
        )

    assert str(exc_info.value) == 'unknown agent: agent9'


def test_submit_ask_rejects_explicit_cross_project_target(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project_root = tmp_path / 'repo'
    other_project = tmp_path / 'other'
    project_root.mkdir()
    other_project.mkdir()
    _write_config(project_root)
    _write_config(other_project)
    monkeypatch.delenv('CCB_CALLER_PROJECT_ROOT', raising=False)
    monkeypatch.delenv('CCB_CALLER_PROJECT_ID', raising=False)
    command = ParsedAskCommand(project=str(other_project), target='agent1', sender=None, message='hello')
    context = CliContextBuilder().build(command, cwd=project_root, bootstrap_if_missing=False)

    with pytest.raises(ValueError, match='ask is project-local'):
        ask_service.submit_ask(context, command)


def test_submit_ask_rejects_unanchored_explicit_project_without_source_test_env(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project_root = tmp_path / 'target'
    unanchored_cwd = tmp_path / 'unanchored'
    project_root.mkdir()
    unanchored_cwd.mkdir()
    _write_config(project_root)
    monkeypatch.delenv('CCB_TEST_ENTRYPOINT', raising=False)
    monkeypatch.delenv('CCB_SOURCE_ALLOWED_ROOTS', raising=False)
    monkeypatch.delenv('CCB_TEST_ROOTS', raising=False)
    command = ParsedAskCommand(project=str(project_root), target='agent1', sender=None, message='hello')
    context = CliContextBuilder().build(command, cwd=unanchored_cwd, bootstrap_if_missing=False)

    with pytest.raises(ValueError, match='cannot select a CCB project from outside'):
        ask_service.submit_ask(context, command)


def test_submit_ask_allows_unanchored_source_test_explicit_project_from_allowed_root(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    test_root = tmp_path / 'test-root'
    project_root = test_root / 'target'
    unanchored_cwd = test_root / 'unanchored'
    project_root.mkdir(parents=True)
    unanchored_cwd.mkdir()
    _write_config(project_root)
    monkeypatch.setenv('CCB_TEST_ENTRYPOINT', '1')
    monkeypatch.setenv('CCB_SOURCE_ALLOWED_ROOTS', str(test_root))
    command = ParsedAskCommand(
        project=str(project_root),
        target='agent1',
        sender=None,
        message='hello',
        callback=True,
        allowed_chain_targets=('reviewer',),
        bind_chain_workspace_tree=True,
    )
    context = CliContextBuilder().build(command, cwd=unanchored_cwd, bootstrap_if_missing=False)
    captured: dict[str, object] = {}

    class _FakeClient:
        def submit(self, envelope) -> dict:
            captured['project_id'] = envelope.project_id
            captured['to_agent'] = envelope.to_agent
            captured['from_actor'] = envelope.from_actor
            captured['route_options'] = envelope.route_options
            return {
                'job_id': 'job_1',
                'agent_name': envelope.to_agent,
                'target_name': envelope.to_agent,
                'status': 'accepted',
            }

    monkeypatch.setattr(
        ask_service,
        'invoke_mounted_daemon',
        lambda context, allow_restart_stale, request_fn: request_fn(_FakeClient()),
    )

    summary = ask_service.submit_ask(context, command)

    assert context.project.source == 'explicit'
    assert context.cwd == unanchored_cwd
    assert captured == {
        'project_id': context.project.project_id,
        'to_agent': 'agent1',
        'from_actor': 'user',
        'route_options': {
            'mode': 'chain',
            'allowed_chain_targets': ['reviewer'],
            'bind_chain_workspace_tree': True,
        },
    }
    assert summary.jobs[0]['job_id'] == 'job_1'


def test_submit_ask_allows_internal_explicit_project_context_from_outer_project(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project_root = tmp_path / 'repo'
    outer_project = tmp_path / 'outer'
    project_root.mkdir()
    outer_project.mkdir()
    _write_config(project_root)
    _write_config(outer_project)
    context = CliContextBuilder().build(
        ParsedLoopRunnerCommand(project=str(project_root), once=True, json_output=True),
        cwd=outer_project,
        bootstrap_if_missing=False,
    )
    captured: dict[str, object] = {}

    class _FakeClient:
        def submit(self, envelope) -> dict:
            captured['project_id'] = envelope.project_id
            captured['to_agent'] = envelope.to_agent
            captured['from_actor'] = envelope.from_actor
            return {
                'job_id': 'job_1',
                'agent_name': envelope.to_agent,
                'target_name': envelope.to_agent,
                'status': 'accepted',
            }

    monkeypatch.setattr(
        ask_service,
        'invoke_mounted_daemon',
        lambda context, allow_restart_stale, request_fn: request_fn(_FakeClient()),
    )

    summary = ask_service.submit_ask(
        context,
        ParsedAskCommand(project=None, target='agent1', sender='system', message='hello'),
    )

    assert context.project.source == 'explicit'
    assert context.cwd == outer_project
    assert captured == {
        'project_id': context.project.project_id,
        'to_agent': 'agent1',
        'from_actor': 'system',
    }
    assert summary.jobs[0]['job_id'] == 'job_1'


def test_submit_ask_allows_source_test_explicit_project_from_allowed_test_root(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    test_root = tmp_path / 'test-root'
    outer_project = test_root / 'outer'
    project_root = test_root / 'target'
    outer_project.mkdir(parents=True)
    project_root.mkdir(parents=True)
    _write_config(outer_project)
    _write_config(project_root)
    monkeypatch.setenv('CCB_TEST_ENTRYPOINT', '1')
    monkeypatch.setenv('CCB_SOURCE_ALLOWED_ROOTS', str(test_root))
    command = ParsedAskCommand(project=str(project_root), target='agent1', sender=None, message='hello')
    context = CliContextBuilder().build(command, cwd=outer_project, bootstrap_if_missing=False)
    captured: dict[str, object] = {}

    class _FakeClient:
        def submit(self, envelope) -> dict:
            captured['project_id'] = envelope.project_id
            captured['to_agent'] = envelope.to_agent
            return {
                'job_id': 'job_1',
                'agent_name': envelope.to_agent,
                'target_name': envelope.to_agent,
                'status': 'accepted',
            }

    monkeypatch.setattr(
        ask_service,
        'invoke_mounted_daemon',
        lambda context, allow_restart_stale, request_fn: request_fn(_FakeClient()),
    )

    summary = ask_service.submit_ask(context, command)

    assert context.project.source == 'explicit'
    assert context.cwd == outer_project
    assert captured == {'project_id': context.project.project_id, 'to_agent': 'agent1'}
    assert summary.jobs[0]['job_id'] == 'job_1'


def test_submit_ask_rejects_workspace_binding_that_escapes_current_project(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project_root = tmp_path / 'repo'
    workspace = project_root / 'subdir'
    other_project = tmp_path / 'other'
    workspace.mkdir(parents=True)
    other_project.mkdir()
    _write_config(project_root)
    _write_config(other_project)
    (workspace / '.ccb-workspace.json').write_text(
        json.dumps({'target_project': str(other_project)}),
        encoding='utf-8',
    )
    monkeypatch.delenv('CCB_CALLER_PROJECT_ROOT', raising=False)
    monkeypatch.delenv('CCB_CALLER_PROJECT_ID', raising=False)
    command = ParsedAskCommand(project=None, target='agent1', sender=None, message='hello')
    context = CliContextBuilder().build(command, cwd=workspace, bootstrap_if_missing=False)
    assert context.project.project_root == other_project.resolve()
    assert context.project.source == 'workspace-binding'

    with pytest.raises(ValueError, match='ask is project-local'):
        ask_service.submit_ask(context, command)


def test_submit_ask_rejects_stale_same_name_caller_runtime_project(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    current_project = tmp_path / 'current'
    stale_project = tmp_path / 'stale'
    current_project.mkdir()
    stale_project.mkdir()
    _write_config(current_project)
    _write_config(stale_project)
    stale_runtime_dir = stale_project / '.ccb' / 'agents' / 'agent1' / 'provider-runtime' / 'codex'
    stale_runtime_dir.mkdir(parents=True, exist_ok=True)
    command = ParsedAskCommand(project=None, target='agent2', sender=None, message='hello')
    context = CliContextBuilder().build(command, cwd=current_project, bootstrap_if_missing=False)
    monkeypatch.setenv('CCB_CALLER_ACTOR', 'agent1')
    monkeypatch.delenv('CCB_CALLER_RUNTIME_DIR', raising=False)
    monkeypatch.setenv('CODEX_RUNTIME_DIR', str(stale_runtime_dir))

    with pytest.raises(ValueError, match='caller runtime belongs to another'):
        ask_service.submit_ask(context, command)


def test_submit_ask_allows_removed_target_when_reload_drain_active(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    project_root = tmp_path / 'repo-ask-reload-drain-target'
    project_root.mkdir()
    context = _build_context(project_root)
    captured: dict[str, object] = {}

    class _FakeClient:
        def project_view(self, *, schema_version: int = 1) -> dict:
            captured['project_view_schema_version'] = schema_version
            return {
                'view': {
                    'reload_drains': {
                        'active_count': 1,
                        'active_records': [{'agent': 'agent2', 'intent_kind': 'unload'}],
                    },
                    'agents': [
                        {'name': 'agent1', 'dispatch_blocked_by_reload_drain': False},
                        {'name': 'agent2', 'dispatch_blocked_by_reload_drain': True},
                    ],
                }
            }

        def submit(self, envelope) -> dict:
            captured['to_agent'] = envelope.to_agent
            return {
                'job_id': 'job_2',
                'agent_name': envelope.to_agent,
                'target_name': envelope.to_agent,
                'status': 'accepted',
            }

    monkeypatch.setattr(
        ask_service,
        'load_project_config',
        lambda project_root: SimpleNamespace(config=SimpleNamespace(agents={'agent1': object()})),
    )
    monkeypatch.setattr(ask_service, 'resolve_ask_sender', lambda context, sender: 'user')
    monkeypatch.setattr(
        ask_service,
        'invoke_mounted_daemon',
        lambda context, allow_restart_stale, request_fn: request_fn(_FakeClient()),
    )

    summary = ask_service.submit_ask(
        context,
        ParsedAskCommand(project=None, target='agent2', sender=None, message='hello'),
    )

    assert captured == {'project_view_schema_version': 1, 'to_agent': 'agent2'}
    assert summary.jobs[0]['agent_name'] == 'agent2'


def test_submit_ask_resolves_unique_role_id_alias(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    project_root = tmp_path / 'repo-ask-role-alias'
    project_root.mkdir()
    context = _build_context(project_root)
    captured: dict[str, object] = {}

    class _Spec:
        role = 'agentroles.archi'

    class _FakeClient:
        def submit(self, envelope) -> dict:
            captured['to_agent'] = envelope.to_agent
            captured['delivery_scope'] = envelope.delivery_scope
            return {
                'job_id': 'job_1',
                'agent_name': 'archi',
                'target_name': 'archi',
                'status': 'accepted',
            }

    monkeypatch.setattr(
        ask_service,
        'load_project_config',
        lambda project_root: SimpleNamespace(config=SimpleNamespace(agents={'agent1': object(), 'archi': _Spec()})),
    )
    monkeypatch.setattr(ask_service, 'resolve_ask_sender', lambda context, sender: 'agent1')
    monkeypatch.setattr(
        ask_service,
        'invoke_mounted_daemon',
        lambda context, allow_restart_stale, request_fn: request_fn(_FakeClient()),
    )

    summary = ask_service.submit_ask(
        context,
        ParsedAskCommand(project=None, target='agentroles.archi', sender=None, message='review'),
    )

    assert captured == {'to_agent': 'archi', 'delivery_scope': DeliveryScope.SINGLE}
    assert summary.jobs[0]['agent_name'] == 'archi'


def test_submit_ask_resolves_legacy_role_id_alias(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    project_root = tmp_path / 'repo-ask-legacy-role-alias'
    project_root.mkdir()
    context = _build_context(project_root)
    captured: dict[str, object] = {}

    class _Spec:
        role = 'agentroles.archi'

    class _FakeClient:
        def submit(self, envelope) -> dict:
            captured['to_agent'] = envelope.to_agent
            return {
                'job_id': 'job_1',
                'agent_name': 'archi',
                'target_name': 'archi',
                'status': 'accepted',
            }

    monkeypatch.setattr(
        ask_service,
        'load_project_config',
        lambda project_root: SimpleNamespace(config=SimpleNamespace(agents={'agent1': object(), 'archi': _Spec()})),
    )
    monkeypatch.setattr(ask_service, 'resolve_ask_sender', lambda context, sender: 'agent1')
    monkeypatch.setattr(
        ask_service,
        'invoke_mounted_daemon',
        lambda context, allow_restart_stale, request_fn: request_fn(_FakeClient()),
    )

    summary = ask_service.submit_ask(
        context,
        ParsedAskCommand(project=None, target='ccb.archi', sender=None, message='review'),
    )

    assert captured == {'to_agent': 'archi'}
    assert summary.jobs[0]['agent_name'] == 'archi'


def test_submit_ask_role_id_alias_requires_binding(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    project_root = tmp_path / 'repo-ask-role-alias-missing'
    project_root.mkdir()
    context = _build_context(project_root)

    monkeypatch.setattr(
        ask_service,
        'load_project_config',
        lambda project_root: SimpleNamespace(config=SimpleNamespace(agents={'agent1': object()})),
    )

    with pytest.raises(ValueError, match='role agentroles.archi is not bound to any configured agent'):
        ask_service.submit_ask(
            context,
            ParsedAskCommand(project=None, target='agentroles.archi', sender=None, message='review'),
        )


def test_submit_ask_role_id_alias_rejects_multiple_bindings(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    project_root = tmp_path / 'repo-ask-role-alias-multiple'
    project_root.mkdir()
    context = _build_context(project_root)

    class _Spec:
        role = 'agentroles.archi'

    monkeypatch.setattr(
        ask_service,
        'load_project_config',
        lambda project_root: SimpleNamespace(
            config=SimpleNamespace(agents={'archi': _Spec(), 'archi_review': _Spec()})
        ),
    )

    with pytest.raises(ValueError, match='role agentroles.archi is bound to multiple agents: archi, archi_review'):
        ask_service.submit_ask(
            context,
            ParsedAskCommand(project=None, target='agentroles.archi', sender=None, message='review'),
        )


def test_submit_ask_maps_broadcast_payload_and_submission(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    project_root = tmp_path / 'repo-ask-broadcast'
    project_root.mkdir()
    context = _build_context(project_root)
    captured: dict[str, object] = {}

    class _FakeClient:
        def submit(self, envelope) -> dict:
            captured['project_id'] = envelope.project_id
            captured['to_agent'] = envelope.to_agent
            captured['from_actor'] = envelope.from_actor
            captured['body'] = envelope.body
            captured['body_artifact'] = envelope.body_artifact
            captured['reply_to'] = envelope.reply_to
            captured['message_type'] = envelope.message_type
            captured['delivery_scope'] = envelope.delivery_scope
            captured['silence_on_success'] = envelope.silence_on_success
            captured['route_options'] = envelope.route_options
            return {
                'submission_id': 'sub_1',
                'jobs': [
                    {'job_id': 'job_1', 'agent_name': 'agent1', 'target_name': 'agent1', 'status': 'accepted'},
                    {'job_id': 'job_2', 'agent_name': 'agent2', 'target_name': 'agent2', 'status': 'accepted'},
                ],
            }

    monkeypatch.setattr(
        ask_service,
        'load_project_config',
        lambda project_root: SimpleNamespace(config=SimpleNamespace(agents={'agent1': {}, 'agent2': {}})),
    )
    monkeypatch.setattr(ask_service, 'resolve_ask_sender', lambda context, sender: 'agent1')
    monkeypatch.setattr(
        ask_service,
        'invoke_mounted_daemon',
        lambda context, allow_restart_stale, request_fn: request_fn(_FakeClient()),
    )

    summary = ask_service.submit_ask(
        context,
        ParsedAskCommand(
            project=None,
            target='all',
            sender=None,
            message='ship it',
            reply_to='msg_1',
            mode='notify',
            silence=True,
        ),
    )

    assert summary.project_id == context.project.project_id
    assert summary.submission_id == 'sub_1'
    assert [job['job_id'] for job in summary.jobs] == ['job_1', 'job_2']
    assert captured == {
        'project_id': context.project.project_id,
        'to_agent': 'all',
        'from_actor': 'agent1',
        'body': 'ship it',
        'body_artifact': None,
        'reply_to': 'msg_1',
        'message_type': 'notify',
        'delivery_scope': DeliveryScope.BROADCAST,
        'silence_on_success': True,
        'route_options': {},
    }


def test_submit_ask_maps_chain_route_options(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    project_root = tmp_path / 'repo-ask-chain'
    project_root.mkdir()
    context = _build_context(project_root)
    captured: dict[str, object] = {}

    class _FakeClient:
        def submit(self, envelope) -> dict:
            captured['route_options'] = envelope.route_options
            return {
                'job_id': 'job_1',
                'agent_name': 'agent2',
                'target_name': 'agent2',
                'status': 'accepted',
            }

    monkeypatch.setattr(
        ask_service,
        'load_project_config',
        lambda project_root: SimpleNamespace(config=SimpleNamespace(agents={'agent1': {}, 'agent2': {}})),
    )
    monkeypatch.setattr(ask_service, 'resolve_ask_sender', lambda context, sender: 'agent1')
    monkeypatch.setattr(
        ask_service,
        'invoke_mounted_daemon',
        lambda context, allow_restart_stale, request_fn: request_fn(_FakeClient()),
    )

    summary = ask_service.submit_ask(
        context,
        ParsedAskCommand(project=None, target='agent2', sender=None, message='collect evidence', callback=True),
    )

    assert summary.jobs[0]['job_id'] == 'job_1'
    assert captured['route_options'] == {'mode': 'chain'}


def test_submit_ask_maps_artifact_route_options(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    project_root = tmp_path / 'repo-ask-artifact-routes'
    project_root.mkdir()
    context = _build_context(project_root)
    captured: dict[str, object] = {}

    class _FakeClient:
        def submit(self, envelope) -> dict:
            captured['route_options'] = envelope.route_options
            return {
                'job_id': 'job_1',
                'agent_name': 'agent2',
                'target_name': 'agent2',
                'status': 'accepted',
            }

    monkeypatch.setattr(
        ask_service,
        'load_project_config',
        lambda project_root: SimpleNamespace(config=SimpleNamespace(agents={'agent1': {}, 'agent2': {}})),
    )
    monkeypatch.setattr(ask_service, 'resolve_ask_sender', lambda context, sender: 'agent1')
    monkeypatch.setattr(
        ask_service,
        'invoke_mounted_daemon',
        lambda context, allow_restart_stale, request_fn: request_fn(_FakeClient()),
    )

    summary = ask_service.submit_ask(
        context,
        ParsedAskCommand(
            project=None,
            target='agent2',
            sender=None,
            message='collect evidence',
            callback=True,
            artifact_request=True,
            artifact_reply=True,
        ),
    )

    assert summary.jobs[0]['job_id'] == 'job_1'
    assert captured['route_options'] == {'mode': 'chain', 'artifact_request': True, 'artifact_reply': True}


def test_submit_ask_keeps_inline_request_body_at_daemon_submission(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    project_root = tmp_path / 'repo-ask-inline-request'
    project_root.mkdir()
    context = _build_context(project_root)
    captured: dict[str, object] = {}

    class _FakeClient:
        def submit(self, envelope) -> dict:
            captured['body'] = envelope.body
            captured['body_artifact'] = envelope.body_artifact
            return {
                'job_id': 'job_1',
                'agent_name': 'agent2',
                'target_name': 'agent2',
                'status': 'accepted',
            }

    monkeypatch.setattr(
        ask_service,
        'load_project_config',
        lambda project_root: SimpleNamespace(config=SimpleNamespace(agents={'agent1': {}, 'agent2': {}})),
    )
    monkeypatch.setattr(ask_service, 'resolve_ask_sender', lambda context, sender: 'agent1')
    monkeypatch.setattr(
        ask_service,
        'invoke_mounted_daemon',
        lambda context, allow_restart_stale, request_fn: request_fn(_FakeClient()),
    )

    summary = ask_service.submit_ask(
        context,
        ParsedAskCommand(
            project=None,
            target='agent2',
            sender=None,
            message='inline task',
            artifact_request=True,
            inline_request=True,
        ),
    )

    assert summary.jobs[0]['job_id'] == 'job_1'
    assert str(captured['body']).startswith('inline task')
    assert captured['body_artifact'] is None


def test_submit_ask_maps_restricted_chain_workspace_binding(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    project_root = tmp_path / 'repo-ask-restricted-chain'
    project_root.mkdir()
    context = _build_context(project_root)
    captured: dict[str, object] = {}

    class _FakeClient:
        def submit(self, envelope) -> dict:
            captured['route_options'] = envelope.route_options
            return {
                'job_id': 'job_1',
                'agent_name': 'agent2',
                'target_name': 'agent2',
                'status': 'accepted',
            }

    monkeypatch.setattr(
        ask_service,
        'load_project_config',
        lambda project_root: SimpleNamespace(config=SimpleNamespace(agents={'agent1': {}, 'agent2': {}})),
    )
    monkeypatch.setattr(ask_service, 'resolve_ask_sender', lambda context, sender: 'agent1')
    monkeypatch.setattr(
        ask_service,
        'invoke_mounted_daemon',
        lambda context, allow_restart_stale, request_fn: request_fn(_FakeClient()),
    )

    summary = ask_service.submit_ask(
        context,
        ParsedAskCommand(
            project=None,
            target='agent2',
            sender=None,
            message='review task',
            callback=True,
            allowed_chain_targets=('Reviewer', 'reviewer', 'other-reviewer', 'Reviewer'),
            bind_chain_workspace_tree=True,
        ),
    )

    assert summary.jobs[0]['job_id'] == 'job_1'
    assert captured['route_options'] == {
        'mode': 'chain',
        'allowed_chain_targets': ['reviewer', 'other-reviewer'],
        'bind_chain_workspace_tree': True,
    }


def test_submit_ask_spills_large_body_before_daemon_submit(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    project_root = tmp_path / 'repo-ask-large-body'
    project_root.mkdir()
    context = _build_context(project_root)
    captured: dict[str, object] = {}
    large_message = 'alpha-' + ('x' * 5000) + '-omega'

    class _FakeClient:
        def submit(self, envelope) -> dict:
            captured['body'] = envelope.body
            captured['body_artifact'] = envelope.body_artifact
            return {
                'job_id': 'job_1',
                'agent_name': 'agent2',
                'target_name': 'agent2',
                'status': 'accepted',
            }

    monkeypatch.setattr(
        ask_service,
        'load_project_config',
        lambda project_root: SimpleNamespace(config=SimpleNamespace(agents={'agent1': {}, 'agent2': {}})),
    )
    monkeypatch.setattr(ask_service, 'resolve_ask_sender', lambda context, sender: 'agent1')
    monkeypatch.setattr(
        ask_service,
        'invoke_mounted_daemon',
        lambda context, allow_restart_stale, request_fn: request_fn(_FakeClient()),
    )

    summary = ask_service.submit_ask(
        context,
        ParsedAskCommand(project=None, target='agent2', sender=None, message=large_message),
    )

    assert summary.jobs[0]['job_id'] == 'job_1'
    body = str(captured['body'])
    artifact = captured['body_artifact']
    assert len(body.encode('utf-8')) <= 4096
    assert 'larger than 4 KiB' in body
    assert isinstance(artifact, dict)
    artifact_path = Path(str(artifact['path']))
    assert artifact_path.exists()
    artifact_text = artifact_path.read_text(encoding='utf-8')
    assert artifact_text.startswith('alpha-')
    assert 'omega' in artifact_text
    assert 'CCB reply guidance:' not in artifact_text
    assert 'CCB_REPLY_MODE:' not in artifact_text


def test_submit_ask_forces_small_body_artifact(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    project_root = tmp_path / 'repo-ask-forced-body-artifact'
    project_root.mkdir()
    context = _build_context(project_root)
    captured: dict[str, object] = {}

    class _FakeClient:
        def submit(self, envelope) -> dict:
            captured['body'] = envelope.body
            captured['body_artifact'] = envelope.body_artifact
            return {
                'job_id': 'job_1',
                'agent_name': 'agent2',
                'target_name': 'agent2',
                'status': 'accepted',
            }

    monkeypatch.setattr(
        ask_service,
        'load_project_config',
        lambda project_root: SimpleNamespace(config=SimpleNamespace(agents={'agent1': {}, 'agent2': {}})),
    )
    monkeypatch.setattr(ask_service, 'resolve_ask_sender', lambda context, sender: 'agent1')
    monkeypatch.setattr(
        ask_service,
        'invoke_mounted_daemon',
        lambda context, allow_restart_stale, request_fn: request_fn(_FakeClient()),
    )

    summary = ask_service.submit_ask(
        context,
        ParsedAskCommand(
            project=None,
            target='agent2',
            sender=None,
            message='short task',
            artifact_request=True,
        ),
    )

    assert summary.jobs[0]['job_id'] == 'job_1'
    body = str(captured['body'])
    artifact = captured['body_artifact']
    assert 'stored as an artifact by --artifact-request' in body
    assert 'Preview:' not in body
    assert 'short task' not in body
    assert isinstance(artifact, dict)
    artifact_path = Path(str(artifact['path']))
    assert artifact_path.exists()
    artifact_text = artifact_path.read_text(encoding='utf-8')
    assert artifact_text == 'short task'


def test_message_with_reply_guidance_leaves_default_ask_unchanged() -> None:
    body = message_with_reply_guidance('review the diff', message_type='ask')

    assert body == 'review the diff'


def test_message_with_reply_guidance_appends_only_compact_mode_metadata() -> None:
    body = message_with_reply_guidance('review the diff', message_type='ask', compact=True)

    assert body == 'review the diff\n\nCCB_REPLY_MODE: compact'
    assert 'CCB reply guidance:' not in body


def test_message_with_reply_guidance_respects_explicit_output_requirements() -> None:
    body = message_with_reply_guidance(
        'review the diff\n\nOutput requirements:\n- Write a full report.',
        message_type='ask',
    )

    assert body == 'review the diff\n\nOutput requirements:\n- Write a full report.'


def test_message_with_reply_guidance_respects_chinese_explicit_output_requirements() -> None:
    body = message_with_reply_guidance(
        '\u8bf7\u5b8c\u6574\u8f93\u51fa\u6d4b\u8bd5\u65e5\u5fd7\uff0c\u4e0d\u8981\u603b\u7ed3\u3002',
        message_type='ask',
        compact=True,
    )

    assert body == '\u8bf7\u5b8c\u6574\u8f93\u51fa\u6d4b\u8bd5\u65e5\u5fd7\uff0c\u4e0d\u8981\u603b\u7ed3\u3002'


def test_message_with_reply_guidance_respects_additional_english_output_requirements() -> None:
    body = message_with_reply_guidance(
        'Run the audit. Include everything and leave nothing out.',
        message_type='ask',
        compact=True,
    )

    assert body == 'Run the audit. Include everything and leave nothing out.'


def test_message_with_reply_guidance_appends_only_silent_mode_metadata() -> None:
    body = message_with_reply_guidance('run smoke test', message_type='ask', silence_on_success=True)

    assert body == 'run smoke test\n\nCCB_REPLY_MODE: silent'
    assert 'CCB reply guidance:' not in body


def test_message_with_reply_guidance_does_not_duplicate_existing_reply_mode() -> None:
    body = message_with_reply_guidance(
        'run smoke test\n\nCCB_REPLY_MODE: compact',
        message_type='ask',
        compact=True,
    )

    assert body == 'run smoke test\n\nCCB_REPLY_MODE: compact'


def test_ask_guidance_source_has_no_literal_chinese_characters() -> None:
    source = (Path(__file__).resolve().parents[1] / 'lib' / 'cli' / 'services' / 'ask_runtime' / 'submission.py').read_text(encoding='utf-8')
    assert re.search(r'[\u4e00-\u9fff]', source) is None


def test_message_with_reply_guidance_skips_non_ask_modes() -> None:
    assert message_with_reply_guidance('ship it', message_type='notify') == 'ship it'


def test_submit_ask_rejects_explicit_cmd_sender(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    project_root = tmp_path / 'repo-ask-explicit-cmd'
    project_root.mkdir()
    context = _build_context(project_root)

    monkeypatch.setattr(
        ask_service,
        'load_project_config',
        lambda project_root: SimpleNamespace(config=SimpleNamespace(agents={'agent1': {}, 'agent2': {}})),
    )

    with pytest.raises(ValueError, match='unknown sender agent: cmd'):
        ask_service.submit_ask(
            context,
            ParsedAskCommand(project=None, target='agent1', sender='cmd', message='hello'),
        )


def test_submit_ask_translates_client_reset_during_shutdown(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    project_root = tmp_path / 'repo-ask-stopping'
    project_root.mkdir()
    context = _build_context(project_root)

    class _FlakyClient:
        def submit(self, envelope) -> dict:
            del envelope
            raise CcbdClientError('socket closed')

    monkeypatch.setattr(
        ask_service,
        'load_project_config',
        lambda project_root: SimpleNamespace(config=SimpleNamespace(agents={'agent1': {}, 'agent2': {}})),
    )
    monkeypatch.setattr(ask_service, 'resolve_ask_sender', lambda context, sender: 'agent1')
    monkeypatch.setattr(
        'cli.services.daemon.connect_mounted_daemon',
        lambda context, allow_restart_stale: SimpleNamespace(client=_FlakyClient()),
    )
    monkeypatch.setattr(
        'cli.services.daemon.inspect_daemon',
        lambda context: (
            None,
            None,
            SimpleNamespace(phase='stopping', desired_state='stopped'),
        ),
    )

    with pytest.raises(CcbdServiceError, match='project ccbd is stopping; wait for shutdown to finish'):
        ask_service.submit_ask(
            context,
            ParsedAskCommand(project=None, target='agent1', sender=None, message='hello'),
        )


def test_resolve_ask_sender_defaults_to_user_for_project_root(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    project_root = tmp_path / 'repo-ask-default-cmd'
    project_root.mkdir()
    context = _build_context(project_root)

    for env_name in ('CCB_CALLER_ACTOR', 'CCB_CALLER_RUNTIME_DIR', 'CODEX_RUNTIME_DIR', 'CCB_SESSION_ID'):
        monkeypatch.delenv(env_name, raising=False)

    assert ask_service.resolve_ask_sender(context, None) == 'user'


def test_resolve_ask_sender_prefers_runtime_dir_actor(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    project_root = tmp_path / 'repo-ask-runtime-actor'
    project_root.mkdir()
    context = _build_context(project_root)
    runtime_dir = project_root / '.ccb' / 'agents' / 'agent1' / 'provider-runtime' / 'codex'
    runtime_dir.mkdir(parents=True, exist_ok=True)

    monkeypatch.delenv('CCB_CALLER_ACTOR', raising=False)
    monkeypatch.delenv('CCB_CALLER_RUNTIME_DIR', raising=False)
    monkeypatch.setenv('CODEX_RUNTIME_DIR', str(runtime_dir))
    monkeypatch.setenv('CCB_SESSION_ID', 'legacy-session-without-actor')

    assert ask_service.resolve_ask_sender(context, None) == 'agent1'


def test_resolve_ask_sender_ignores_stale_runtime_dir_actor(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    project_root = tmp_path / 'repo-ask-current'
    stale_project = tmp_path / 'repo-ask-stale'
    project_root.mkdir()
    stale_project.mkdir()
    context = _build_context(project_root)
    stale_runtime_dir = stale_project / '.ccb' / 'agents' / 'agent1' / 'provider-runtime' / 'codex'
    stale_runtime_dir.mkdir(parents=True, exist_ok=True)

    monkeypatch.setenv('CCB_CALLER_ACTOR', 'agent1')
    monkeypatch.delenv('CCB_CALLER_RUNTIME_DIR', raising=False)
    monkeypatch.setenv('CODEX_RUNTIME_DIR', str(stale_runtime_dir))
    monkeypatch.setenv('CCB_SESSION_ID', 'ccb-agent1-stale')

    assert ask_service.resolve_ask_sender(context, None) == 'user'


def test_resolve_ask_sender_prefers_relocated_runtime_dir_actor(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    project_root = tmp_path / 'repo-ask-relocated-runtime-actor'
    (project_root / '.ccb').mkdir(parents=True, exist_ok=True)
    (project_root / '.ccb' / 'ccb.config').write_text('agent1:codex\n', encoding='utf-8')
    relocated_root = tmp_path / 'state-root'
    project_id = compute_project_id(project_root)
    (project_root / '.ccb' / 'runtime-root-ref.json').write_text(
        json.dumps(
            {
                'schema_version': 1,
                'record_type': 'ccb_runtime_root_ref',
                'project_id': project_id,
                'runtime_state_root': str(relocated_root),
                'created_at': '2026-05-07T00:00:00Z',
            }
        ),
        encoding='utf-8',
    )
    context = CliContextBuilder().build(
        ParsedAskCommand(project=None, target='agent1', sender=None, message='hello'),
        cwd=project_root,
        bootstrap_if_missing=False,
    )
    runtime_dir = context.paths.agents_dir / 'agent1' / 'provider-runtime' / 'codex'
    runtime_dir.mkdir(parents=True, exist_ok=True)

    monkeypatch.delenv('CCB_CALLER_ACTOR', raising=False)
    monkeypatch.delenv('CCB_CALLER_RUNTIME_DIR', raising=False)
    monkeypatch.setenv('CODEX_RUNTIME_DIR', str(runtime_dir))
    monkeypatch.setenv('CCB_SESSION_ID', 'legacy-session-without-actor')

    assert context.paths.runtime_state_root == relocated_root
    assert ask_service.resolve_ask_sender(context, None) == 'agent1'


def test_caller_context_env_includes_project_identity(tmp_path: Path) -> None:
    project_root = tmp_path / 'repo-caller-env-project'
    runtime_dir = project_root / '.ccb' / 'agents' / 'agent1' / 'provider-runtime' / 'codex'
    runtime_dir.mkdir(parents=True)

    env = caller_context_env(actor='agent1', runtime_dir=runtime_dir, launch_session_id='ccb-agent1-1')

    assert env['CCB_CALLER_PROJECT_ROOT'] == str(project_root.resolve())
    assert env['CCB_CALLER_PROJECT_ID'] == compute_project_id(project_root)


@pytest.mark.parametrize('relocated', [False, True])
def test_omp_python_ask_recovers_sender_from_managed_agent_dir(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, relocated: bool,
) -> None:
    project_root = tmp_path / 'omp-project'
    _write_config(project_root, 'frontdesk:omp, executor:omp\n')
    if relocated:
        (project_root / '.ccb' / 'runtime-root-ref.json').write_text(json.dumps({
            'schema_version': 1,
            'record_type': 'ccb_runtime_root_ref',
            'project_id': compute_project_id(project_root),
            'runtime_state_root': str(tmp_path / 'relocated-state'),
            'created_at': '2026-10-22T00:00:00Z',
        }), encoding='utf-8')
    command = ParsedAskCommand(project=None, target='executor', sender=None, message='review')
    context = CliContextBuilder().build(command, cwd=project_root)
    # OMP's Python allowlist preserves PI_* but drops CCB_* and CODEX_*.
    if relocated:
        assert context.paths.runtime_state_root == tmp_path / 'relocated-state'
    monkeypatch.setenv('PI_CODING_AGENT_DIR', str(
        context.paths.agent_provider_state_dir('frontdesk', 'omp') / 'home' / '.omp' / 'agent'
    ))

    assert ask_service.resolve_ask_sender(context, None) == 'frontdesk'
    assert ask_service.resolve_ask_sender(context, 'user') == 'user'


@pytest.mark.parametrize('agent_dir', [
    'other/.ccb/agents/frontdesk/provider-state/omp/home/.omp/agent',
    'project/.ccb/agents/unknown/provider-state/omp/home/.omp/agent',
    'project/.ccb/agents/frontdesk/provider-state/pi/home/.omp/agent',
    'project/.ccb/agents/frontdesk/provider-state/omp/home/.omp/agent/child',
    'project/.ccb/agents/frontdesk/provider-runtime/omp',
    'external/.omp/agent',
])
def test_omp_python_ask_does_not_infer_sender_from_unbound_agent_dir(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, agent_dir: str,
) -> None:
    project_root = tmp_path / 'project'
    _write_config(project_root, 'frontdesk:omp, executor:omp\n')
    command = ParsedAskCommand(project=None, target='executor', sender=None, message='review')
    context = CliContextBuilder().build(command, cwd=project_root)
    monkeypatch.setenv('PI_CODING_AGENT_DIR', str(tmp_path / agent_dir))

    assert ask_service.resolve_ask_sender(context, None) == 'user'


def test_omp_python_sender_does_not_follow_managed_dir_symlink_outside_project(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    project_root = tmp_path / 'project'
    _write_config(project_root, 'frontdesk:omp, executor:omp\n')
    command = ParsedAskCommand(project=None, target='executor', sender=None, message='review')
    context = CliContextBuilder().build(command, cwd=project_root)
    external = tmp_path / 'external-agent-dir'
    external.mkdir()
    agent_dir = context.paths.agent_provider_state_dir('frontdesk', 'omp') / 'home' / '.omp' / 'agent'
    agent_dir.parent.mkdir(parents=True)
    agent_dir.symlink_to(external, target_is_directory=True)
    monkeypatch.setenv('PI_CODING_AGENT_DIR', str(agent_dir))

    assert ask_service.resolve_ask_sender(context, None) == 'user'


@pytest.mark.parametrize('identity', ['runtime', 'actor', 'session', 'foreign-runtime', 'non-omp'])
def test_omp_python_sender_fallback_preserves_existing_identity_authority(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, identity: str,
) -> None:
    project_root = tmp_path / 'project'
    provider = 'codex' if identity == 'non-omp' else 'omp'
    _write_config(project_root, f'frontdesk:{provider}, executor:omp\n')
    command = ParsedAskCommand(project=None, target='executor', sender=None, message='review')
    context = CliContextBuilder().build(command, cwd=project_root)
    monkeypatch.setenv('PI_CODING_AGENT_DIR', str(
        context.paths.agent_provider_state_dir('frontdesk', 'omp') / 'home' / '.omp' / 'agent'
    ))
    if identity == 'runtime':
        monkeypatch.setenv('CCB_CALLER_RUNTIME_DIR', str(
            context.paths.agent_provider_runtime_dir('executor', 'omp')
        ))
    elif identity == 'actor':
        monkeypatch.setenv('CCB_CALLER_ACTOR', 'executor')
    elif identity == 'session':
        monkeypatch.setenv('CCB_SESSION_ID', 'ccb-executor-current')
    elif identity == 'foreign-runtime':
        monkeypatch.setenv('CCB_CALLER_RUNTIME_DIR', str(
            tmp_path / 'other' / '.ccb' / 'agents' / 'frontdesk' / 'provider-runtime' / 'omp'
        ))

    expected = 'user' if identity in {'foreign-runtime', 'non-omp'} else 'executor'
    assert ask_service.resolve_ask_sender(context, None) == expected


def test_watch_ask_job_reconnects_and_preserves_cursor(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    project_root = tmp_path / 'repo-ask-watch'
    project_root.mkdir()
    context = _build_context(project_root)
    rendered: list[tuple[str, ...]] = []

    class _FlakyClient:
        def __init__(self) -> None:
            self.calls: list[int] = []

        def watch(self, job_id: str, *, cursor: int = 0) -> dict:
            assert job_id == 'job_1'
            self.calls.append(cursor)
            if cursor == 0:
                return {
                    'job_id': 'job_1',
                    'agent_name': 'agent1',
                    'target_name': 'agent1',
                    'cursor': 2,
                    'generation': 1,
                    'terminal': False,
                    'status': 'running',
                    'reply': 'partial',
                    'events': [
                        {'event_id': 'evt_1', 'job_id': 'job_1', 'agent_name': 'agent1', 'type': 'job_started', 'timestamp': '2026-04-06T00:00:01Z'},
                    ],
                }
            raise CcbdClientError('socket closed')

    class _StableClient:
        def __init__(self) -> None:
            self.calls: list[int] = []

        def watch(self, job_id: str, *, cursor: int = 0) -> dict:
            assert job_id == 'job_1'
            self.calls.append(cursor)
            return {
                'job_id': 'job_1',
                'agent_name': 'agent1',
                'target_name': 'agent1',
                'cursor': 4,
                'generation': 2,
                'terminal': True,
                'status': 'completed',
                'reply': 'done',
                'events': [],
            }

    flaky = _FlakyClient()
    stable = _StableClient()
    handles = iter([SimpleNamespace(client=flaky), SimpleNamespace(client=stable)])
    seen: list[bool] = []

    def _connect(context, allow_restart_stale):
        seen.append(allow_restart_stale)
        return next(handles)

    monkeypatch.setattr(ask_service, 'connect_mounted_daemon', _connect)
    monkeypatch.setattr(ask_service, 'watch_timeout_seconds', lambda: 1.0)
    monkeypatch.setattr(ask_service, 'watch_poll_interval_seconds', lambda: 0.0)
    monkeypatch.setattr(ask_service, 'render_watch_batch', lambda batch: (f'{batch.job_id}:{batch.cursor}:{batch.terminal}',))
    monkeypatch.setattr(ask_service, 'write_lines', lambda out, lines: rendered.append(lines))
    monkeypatch.setattr(ask_service.time, 'sleep', lambda seconds: None)

    batch = ask_service.watch_ask_job(context, 'job_1', StringIO(), timeout=None, emit_output=True)

    assert batch.cursor == 4
    assert batch.generation == 2
    assert batch.reply == 'done'
    assert flaky.calls == [0, 2]
    assert stable.calls == [2]
    assert rendered == [('job_1:2:False',), ('job_1:4:True',)]
    assert seen == [False, False]


def test_watch_ask_job_times_out_after_reconnect_failures(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    project_root = tmp_path / 'repo-ask-timeout'
    project_root.mkdir()
    context = _build_context(project_root)
    clock = iter([0.0, 0.5, 1.5])

    class _FlakyClient:
        def watch(self, job_id: str, *, cursor: int = 0) -> dict:
            del job_id, cursor
            raise CcbdClientError('socket closed')

    seen: list[bool] = []

    def _connect(context, allow_restart_stale):
        seen.append(allow_restart_stale)
        return SimpleNamespace(client=_FlakyClient())

    monkeypatch.setattr(
        ask_service,
        'connect_mounted_daemon',
        _connect,
    )
    monkeypatch.setattr(ask_service, 'watch_timeout_seconds', lambda: 1.0)
    monkeypatch.setattr(ask_service, 'watch_poll_interval_seconds', lambda: 0.0)
    monkeypatch.setattr(ask_service.time, 'monotonic', lambda: next(clock))
    monkeypatch.setattr(ask_service.time, 'sleep', lambda seconds: None)

    with pytest.raises(RuntimeError) as exc_info:
        ask_service.watch_ask_job(context, 'job_1', StringIO(), timeout=None, emit_output=False)

    assert str(exc_info.value) == 'watch timed out for job_1'
    assert seen == [False, False]


def test_watch_ask_job_retries_when_reconnect_attempt_temporarily_fails(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    project_root = tmp_path / 'repo-ask-reconnect-step-fail'
    project_root.mkdir()
    context = _build_context(project_root)
    clock = iter([0.0, 0.1, 0.2, 0.3, 0.4, 0.5])

    class _FlakyClient:
        def __init__(self) -> None:
            self.calls: list[int] = []

        def watch(self, job_id: str, *, cursor: int = 0) -> dict:
            assert job_id == 'job_1'
            self.calls.append(cursor)
            raise CcbdClientError('socket closed')

    class _StableClient:
        def __init__(self) -> None:
            self.calls: list[int] = []

        def watch(self, job_id: str, *, cursor: int = 0) -> dict:
            assert job_id == 'job_1'
            self.calls.append(cursor)
            return {
                'job_id': 'job_1',
                'agent_name': 'agent1',
                'target_name': 'agent1',
                'cursor': 1,
                'generation': 2,
                'terminal': True,
                'status': 'completed',
                'reply': 'done',
                'events': [],
            }

    flaky = _FlakyClient()
    stable = _StableClient()
    connects = {'count': 0}
    seen: list[bool] = []

    def _connect(context, allow_restart_stale):
        del context
        seen.append(allow_restart_stale)
        connects['count'] += 1
        if connects['count'] == 1:
            return SimpleNamespace(client=flaky)
        if connects['count'] == 2:
            raise CcbdServiceError('daemon restarting')
        return SimpleNamespace(client=stable)

    monkeypatch.setattr(ask_service, 'connect_mounted_daemon', _connect)
    monkeypatch.setattr(ask_service, 'watch_timeout_seconds', lambda: 1.0)
    monkeypatch.setattr(ask_service, 'watch_poll_interval_seconds', lambda: 0.0)
    monkeypatch.setattr(ask_service.time, 'monotonic', lambda: next(clock))
    monkeypatch.setattr(ask_service.time, 'sleep', lambda seconds: None)

    batch = ask_service.watch_ask_job(context, 'job_1', StringIO(), timeout=None, emit_output=False)

    assert batch.terminal is True
    assert batch.reply == 'done'
    assert flaky.calls == [0]
    assert stable.calls == [0]
    assert seen == [False, False, False]


def test_persisted_watch_fallback_resolves_callback_root_final_reply(tmp_path: Path) -> None:
    project_root = tmp_path / 'repo-ask-watch-callback-fallback'
    project_root.mkdir()
    context = _build_context(project_root)
    layout = PathLayout(project_root)
    finished_at = '2026-03-18T00:00:10Z'
    request = MessageEnvelope(
        project_id=context.project.project_id,
        to_agent='agent1',
        from_actor='user',
        body='delegate and finish',
        task_id='task-callback',
        reply_to=None,
        message_type='ask',
        delivery_scope=DeliveryScope.SINGLE,
    )
    JobStore(layout).append(
        JobRecord(
            job_id='job_callback_root',
            submission_id=None,
            agent_name='agent1',
            target_kind='agent',
            target_name='agent1',
            provider='codex',
            provider_instance=None,
            provider_options={},
            request=request,
            status=JobStatus.COMPLETED,
            terminal_decision={
                'schema_version': 2,
                'record_type': 'completion_decision',
                'terminal': True,
                'status': 'completed',
                'reason': 'task_complete',
                'confidence': 'exact',
                'reply': 'delegated to child',
                'anchor_seen': True,
                'reply_started': True,
                'reply_stable': True,
                'provider_turn_ref': 'turn-1',
                'source_cursor': None,
                'finished_at': finished_at,
                'diagnostics': {},
                'delegated': True,
                'suppress_reply': True,
                'chain_edge_id': 'cb_1',
                'chain_child_job_id': 'job_child',
            },
            cancel_requested_at=None,
            created_at='2026-03-18T00:00:00Z',
            updated_at=finished_at,
        )
    )
    AttemptStore(layout).append(
        AttemptRecord(
            attempt_id='att_root',
            message_id='msg_root',
            agent_name='agent1',
            provider='codex',
            job_id='job_callback_root',
            retry_index=0,
            health_snapshot_ref=None,
            started_at='2026-03-18T00:00:00Z',
            updated_at=finished_at,
            attempt_state=AttemptState.COMPLETED,
        )
    )
    ReplyStore(layout).append(
        ReplyRecord(
            reply_id='rep_final',
            message_id='msg_root',
            attempt_id='att_continuation',
            agent_name='agent1',
            terminal_status=ReplyTerminalStatus.COMPLETED,
            reply='FINAL CALLBACK RESULT',
            diagnostics={'reason': 'task_complete'},
            finished_at='2026-03-18T00:00:20Z',
        )
    )
    CompletionSnapshotStore(layout).save(
        CompletionSnapshot(
            job_id='job_callback_root',
            agent_name='agent1',
            profile_family=CompletionFamily.PROTOCOL_TURN,
            state=CompletionState(terminal=True),
            latest_decision=CompletionDecision(
                terminal=True,
                status=CompletionStatus.COMPLETED,
                reason='task_complete',
                confidence=CompletionConfidence.EXACT,
                reply='delegated to child',
                anchor_seen=True,
                reply_started=True,
                reply_stable=True,
                provider_turn_ref='turn-1',
                source_cursor=None,
                finished_at=finished_at,
                diagnostics={},
            ),
            latest_reply_preview='delegated to child',
            updated_at=finished_at,
        )
    )

    payload = load_persisted_terminal_watch_payload(context, 'job_callback_root')

    assert payload is not None
    assert payload['reply'] == 'FINAL CALLBACK RESULT'
    assert payload['visible_reply_source'] == 'message_bureau_reply'


def test_write_ask_output_appends_newline(tmp_path: Path) -> None:
    path = tmp_path / 'reply.txt'

    ask_service.write_ask_output(path, 'done')

    assert path.read_text(encoding='utf-8') == 'done\n'


def test_exit_code_for_ask_status_prefers_no_reply_exit_for_incomplete_with_reply() -> None:
    assert ask_service.exit_code_for_ask_status('incomplete', reply='partial') == 2
    assert ask_service.exit_code_for_ask_status('completed', reply='done') == 0
    assert ask_service.exit_code_for_ask_status('failed', reply='') == 1
