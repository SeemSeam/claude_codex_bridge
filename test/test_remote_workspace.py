from __future__ import annotations

from dataclasses import replace
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
from types import SimpleNamespace

import pytest

if sys.platform != 'linux':
    pytest.skip('remote workspace transport currently targets Linux', allow_module_level=True)

from remote_workspace.endpoint import Endpoint
from remote_workspace.files import read_json, scan
from remote_workspace.objects import Git, Objects, SyncError, safe_path
from remote_workspace.service import WorkspaceSynchronizer
from storage.paths import PathLayout
from storage.text_artifacts import write_text_artifact, artifact_stub
from ccbd.api_models import MessageEnvelope, DeliveryScope, JobStatus
from completion.models import CompletionStatus


def raw_git(path, *args):
    return subprocess.check_output(
        [
            '/usr/bin/git',
            '-c',
            'core.hooksPath=/dev/null',
            '-c',
            'user.name=Test',
            '-c',
            'user.email=test@localhost',
            '-C',
            str(path),
            *args,
        ],
        stderr=subprocess.PIPE,
    )


class LoopbackTransport:
    def __init__(self, endpoint):
        self.endpoint = endpoint
        self.calls = []
        self.fail_after = None
        self.on_call = None

    def call(self, request):
        self.calls.append(request['op'])
        if self.on_call:
            self.on_call(request)
        result = self.endpoint.handle(request)
        if request['op'] == self.fail_after:
            self.fail_after = None
            raise SyncError('simulated lost response after remote side effect')
        return result


@pytest.fixture
def sync_case(tmp_path, monkeypatch):
    project = tmp_path / 'project'
    project.mkdir()
    raw_git(project, 'init', '-q', '--initial-branch=main')
    (project / 'src').mkdir()
    (project / 'src/calc.py').write_text('def total(values):\n    return sum(values)\n')
    (project / 'README.md').write_text('fixture\n')
    raw_git(project, 'add', '.')
    raw_git(project, 'commit', '-qm', 'initial')
    workspace = project / '.ccb/workspaces/worker'
    workspace.parent.mkdir(parents=True)
    raw_git(project, 'worktree', 'add', '-b', 'ccb/worker', str(workspace), 'main')
    remote = tmp_path / 'different-remote-root'
    remote.mkdir()
    include = ['src', 'README.md', 'outputs']
    p = dict(
        host='example-host',
        remote_root=str(remote),
        local_workspace=str(workspace),
        project_root=str(project),
        agent_name='worker',
        workspace_id='fixture-identity-123',
        include=include,
        publish='commits',
    )
    profile = tmp_path / 'profiles.json'
    profile.write_text(json.dumps({'demo': p}))
    profile.chmod(0o600)
    monkeypatch.setenv('CCB_REMOTE_WORKSPACES_FILE', str(profile))
    endpoint = Endpoint(remote, {'workspace_id': p['workspace_id'], 'include': include})
    transport = LoopbackTransport(endpoint)
    config = SimpleNamespace(agents={'worker': SimpleNamespace(remote_workspace='demo')})
    layout = PathLayout(project)
    service = WorkspaceSynchronizer(layout, config, transport_factory=lambda p: transport)
    from test_v2_message_bureau_dispatcher_integration import _provider_config, _runtime, _decision
    from ccbd.services.registry import AgentRegistry
    from ccbd.services.dispatcher import JobDispatcher

    real_config = _provider_config('worker')
    real_config = replace(
        real_config,
        agents={
            'worker': replace(
                real_config.agents['worker'], remote_workspace='demo', workspace_mode='git-worktree'
            )
        },
    )
    # _provider_config uses names as providers; replace with a supported backend.
    real_config = replace(
        real_config, agents={'worker': replace(real_config.agents['worker'], provider='claude')}
    )
    registry = AgentRegistry(layout, real_config)
    registry.upsert(_runtime('worker', project_id=layout.project_id, layout=layout, pid=100))

    class Execution:
        def __init__(self):
            self.started = []

        def start(self, job, **kwargs):
            self.started.append(job)

        def finish(self, job_id):
            pass

    execution = Execution()
    dispatcher = JobDispatcher(
        layout, real_config, registry, execution_service=execution, workspace_synchronizer=service
    )

    def submit(body='implement', artifact=None):
        job = dispatcher.submit(
            MessageEnvelope(
                project_id=layout.project_id,
                to_agent='worker',
                from_actor='user',
                body=body,
                body_artifact=artifact,
                task_id='test-task',
                reply_to=None,
                message_type='ask',
                delivery_scope=DeliveryScope.SINGLE,
            )
        ).jobs[0]
        dispatcher.tick()
        return dispatcher.get(job.job_id)

    return SimpleNamespace(
        project=project,
        workspace=workspace,
        remote=remote,
        profile=profile,
        p=p,
        service=service,
        endpoint=endpoint,
        transport=transport,
        dispatcher=dispatcher,
        execution=execution,
        submit=submit,
        decision=_decision,
        layout=layout,
    )


def test_roundtrip_preserves_commits_binary_deletion_and_artifact(sync_case):
    c = sync_case
    before = Git(c.project).head()
    brief = 'exact request\n' + 'x' * 9000
    artifact = write_text_artifact(
        c.layout, text=brief, kind='ask-request', owner_id='test-request'
    )
    job = c.submit(artifact_stub(prefix='task', artifact=artifact), artifact)
    assert job.status is JobStatus.RUNNING
    sent = c.execution.started[-1]
    remote_path = c.remote / 'artifacts' / (job.job_id + '.txt')
    assert str(remote_path) in sent.request.body
    assert str(artifact['path']) not in sent.request.body
    assert remote_path.read_text() == brief
    remote = c.remote / 'workspace'
    assert (remote / '.git').is_file()  # Real remote worktree.
    (remote / 'src/calc.py').write_text('def total(values):\n    return sum(values) + 1\n')
    raw_git(remote, 'add', 'src/calc.py')
    raw_git(remote, 'commit', '-qm', 'real developer commit')
    developer_commit = Git(remote).head()
    (remote / 'README.md').unlink()
    (remote / 'outputs').mkdir()
    (remote / 'outputs/image.bin').write_bytes(bytes(range(256)))
    (remote / 'outputs/run.sh').write_text('#!/bin/sh\nexit 0\n')
    (remote / 'outputs/run.sh').chmod(0o755)
    result = c.dispatcher.complete(job.job_id, c.decision(reply='implemented'))
    assert result.status is JobStatus.COMPLETED
    sync = result.terminal_decision['diagnostics']['workspace_sync']
    assert sync['status'] == 'synced'
    assert Git(c.workspace).head() == sync['head'] == Git(remote).head()
    assert raw_git(c.project, 'cat-file', '-t', developer_commit).strip() == b'commit'
    assert scan(c.workspace, c.p['include']) == scan(remote, c.p['include'])
    assert not (c.workspace / 'README.md').exists()
    assert (c.workspace / 'outputs/image.bin').read_bytes() == bytes(range(256))
    assert Git(c.project).head() == before
    again = c.dispatcher.complete(job.job_id, c.decision(reply='duplicate'))
    assert again == result
    assert c.transport.calls.count('collect') == 1
    second = c.submit('review synchronized code')
    assert second.status is JobStatus.RUNNING
    assert (remote / 'outputs/image.bin').exists()
    c.dispatcher.complete(second.job_id, c.decision(reply='reviewed'))


@pytest.mark.parametrize('operation', ['prepare', 'collect', 'ack'])
def test_lost_response_blocks_next_task_and_recovery_is_idempotent(sync_case, operation):
    c = sync_case
    if operation == 'prepare':
        c.transport.fail_after = operation
    job = c.submit()
    if operation != 'prepare':
        (c.remote / 'workspace/src/calc.py').write_text('REMOTE_RESULT = True\n')
        c.transport.fail_after = operation
        result = c.dispatcher.complete(job.job_id, c.decision(reply='done'))
        assert result.status is JobStatus.FAILED
        assert result.terminal_decision['reason'] == 'workspace_sync_failed'
    else:
        assert job.status is JobStatus.FAILED
    starts = len(c.execution.started)
    second = c.submit('must not overtake unfinished synchronization')
    assert second.status is JobStatus.FAILED
    assert len(c.execution.started) == starts
    repaired = c.service.recover('demo', 'worker', job.job_id)
    assert repaired['status'] == 'synced'
    assert c.service.recover('demo', 'worker', job.job_id) == repaired
    next_job = c.submit('after recovery')
    assert next_job.status is JobStatus.RUNNING


def test_local_edits_are_preserved_and_remote_result_retained(sync_case):
    c = sync_case
    job = c.submit()
    base = Git(c.workspace).head()
    (c.remote / 'workspace/src/calc.py').write_text('remote change\n')
    (c.workspace / 'src/calc.py').write_text('human edit\n')
    result = c.dispatcher.complete(job.job_id, c.decision(reply='done'))
    assert result.status is JobStatus.FAILED
    assert (c.workspace / 'src/calc.py').read_text() == 'human edit\n'
    assert Git(c.workspace).head() == base
    assert (c.remote / (job.job_id + '.result.json')).exists()


@pytest.mark.parametrize(
    'attack', ['symlink', 'hardlink', 'outside_allowlist', 'config', 'gitlink']
)
def test_untrusted_results_do_not_cross_file_boundary(sync_case, attack, tmp_path):
    c = sync_case
    job = c.submit()
    remote = c.remote / 'workspace'
    target = tmp_path / 'secret.txt'
    target.write_text('LOCAL_SENTINEL')
    before = (c.workspace / 'src/calc.py').read_bytes()
    if attack in ('symlink', 'hardlink'):
        (
            (remote / 'src/attack').symlink_to(target)
            if attack == 'symlink'
            else os.link(target, remote / 'src/attack')
        )
    elif attack == 'outside_allowlist':
        (remote / 'unapproved.txt').write_text('not permitted')
        raw_git(remote, 'add', 'unapproved.txt')
        raw_git(remote, 'commit', '-qm', 'bad path')
    elif attack == 'config':
        (remote / 'src/.gitconfig').write_text('[core]\nfsmonitor = malicious\n')
        raw_git(remote, 'add', '-f', 'src/.gitconfig')
        raw_git(remote, 'commit', '-qm', 'bad config')
    else:
        raw_git(
            remote,
            'update-index',
            '--add',
            '--cacheinfo',
            '160000',
            Git(remote).head(),
            'src/module',
        )
        raw_git(remote, 'commit', '-qm', 'gitlink')
    result = c.dispatcher.complete(job.job_id, c.decision(reply='attack fixture'))
    assert result.status is JobStatus.FAILED
    assert (c.workspace / 'src/calc.py').read_bytes() == before
    assert target.read_text() == 'LOCAL_SENTINEL'
    assert not (c.workspace / 'src/attack').exists()


def test_no_remote_git_configuration_or_hooks_are_imported(sync_case, tmp_path):
    c = sync_case
    job = c.submit()
    remote = c.remote / 'workspace'
    sentinel = tmp_path / 'fsmonitor-executed'
    # This harmless hook would leave evidence if any receiving Git operation
    # loaded the remote .git/config. The endpoint's plumbing also disables it.
    raw_git(remote, 'config', 'core.fsmonitor', f'touch {sentinel}')
    (remote / 'src/calc.py').write_text('safe code change\n')
    result = c.dispatcher.complete(job.job_id, c.decision(reply='done'))
    assert result.status is JobStatus.COMPLETED
    assert not sentinel.exists()
    assert b'fsmonitor' not in (c.project / '.git/config').read_bytes()


@pytest.mark.parametrize(
    'path',
    [
        '../outside',
        '/absolute',
        'src/.git/config',
        'src/.ccb/socket',
        'src/.env',
        'src/link\\target',
        'src/dir/../x',
        'src/secret.key',
        'src/.gitmodules',
        'src/a:stream',
    ],
)
def test_path_policy(path):
    with pytest.raises(SyncError):
        safe_path(path)


def test_hash_and_unrelated_objects_are_rejected(sync_case):
    c = sync_case
    git = Git(c.workspace)
    obj = git.export(git.head())
    wire = obj.wire()
    key = next(iter(wire))
    wire[key][1] = 'Zm9yZ2Vk'
    with pytest.raises(SyncError, match='hash mismatch'):
        Objects.from_wire(wire)
    obj.add('blob', b'unreferenced data')
    with pytest.raises(SyncError, match='unrelated'):
        obj.validate_bootstrap(git.head(), c.p['include'])


def test_completion_not_visible_until_objects_and_files_are_local(sync_case):
    c = sync_case
    job = c.submit()
    second = c.dispatcher.submit(
        MessageEnvelope(
            project_id=c.layout.project_id,
            to_agent='worker',
            from_actor='user',
            body='next task',
            task_id='task2',
            reply_to=None,
            message_type='ask',
            delivery_scope=DeliveryScope.SINGLE,
        )
    ).jobs[0]

    def observe(request):
        if request['op'] in ('collect', 'ack'):
            assert c.dispatcher.get(job.job_id).status is JobStatus.RUNNING
            c.dispatcher.tick()
            assert c.dispatcher.get(second.job_id).status is JobStatus.QUEUED

    c.transport.on_call = observe
    result = c.dispatcher.complete(job.job_id, c.decision(reply='ready'))
    assert result.status is JobStatus.COMPLETED
    c.transport.on_call = None
    c.dispatcher.tick()
    assert c.dispatcher.get(second.job_id).status is JobStatus.RUNNING


def test_native_chain_continuation_is_created_only_after_sync(sync_case):
    from test_v2_message_bureau_dispatcher_integration import _provider_config, _runtime
    from ccbd.services.registry import AgentRegistry
    from ccbd.services.dispatcher import JobDispatcher
    from message_bureau import CallbackEdgeStore, CallbackEdgeState

    c = sync_case
    config = _provider_config('codex', 'worker')
    config = replace(
        config,
        agents={
            **config.agents,
            'worker': replace(config.agents['worker'], provider='claude', remote_workspace='demo'),
        },
    )
    registry = AgentRegistry(c.layout, config)
    for name in config.agents:
        registry.upsert(_runtime(name, project_id=c.layout.project_id, layout=c.layout, pid=100))
    dispatcher = JobDispatcher(
        c.layout, config, registry, execution_service=c.execution, workspace_synchronizer=c.service
    )
    parent = dispatcher.submit(
        MessageEnvelope(
            project_id=c.layout.project_id,
            to_agent='codex',
            from_actor='user',
            body='implement then review',
            task_id='native-chain',
            reply_to=None,
            message_type='ask',
            delivery_scope=DeliveryScope.SINGLE,
        )
    ).jobs[0]
    dispatcher.tick()
    child = dispatcher.submit(
        MessageEnvelope(
            project_id=c.layout.project_id,
            to_agent='worker',
            from_actor='codex',
            body='implement child',
            task_id='native-chain',
            reply_to=None,
            message_type='ask',
            delivery_scope=DeliveryScope.SINGLE,
            route_options={'mode': 'chain'},
        )
    ).jobs[0]
    dispatcher.complete(parent.job_id, c.decision(reply='delegated'))
    dispatcher.tick()
    (c.remote / 'workspace/src/calc.py').write_text('RESULT = "native-chain"\n')

    def observe(request):
        if request['op'] in ('collect', 'ack'):
            edge = CallbackEdgeStore(c.layout).get_latest_for_child_job(child.job_id)
            assert not edge.continuation_job_id

    c.transport.on_call = observe
    dispatcher.complete(child.job_id, c.decision(reply='implemented'))
    edge = CallbackEdgeStore(c.layout).get_latest_for_child_job(child.job_id)
    assert edge.state is CallbackEdgeState.CONTINUATION_SUBMITTED
    assert (c.workspace / 'src/calc.py').read_text() == 'RESULT = "native-chain"\n'
    dispatcher.tick()
    continuation = dispatcher.get(edge.continuation_job_id)
    assert continuation.status is JobStatus.RUNNING
    assert 'CCB workspace synced' in continuation.request.body
    assert Git(c.workspace).head() in continuation.request.body
    dispatcher.complete(continuation.job_id, c.decision(reply='reviewed synchronized code'))
    assert CallbackEdgeStore(c.layout).get_latest(edge.edge_id).state is CallbackEdgeState.DONE


def test_native_review_and_integration_accept_returned_commit(sync_case):
    from test_workgroup_git_integration import _kernel, _node, _node_record

    c = sync_case
    (c.project / '.git/info/exclude').write_text('.ccb/\n')
    node = _node(1, allowed_paths=('src/calc.py',))
    kernel = _kernel(c.project, (node,))
    kernel.preflight()
    kernel.prepare_integration()
    kernel.prepare_node(node.node_id)
    workspace = Path(_node_record(kernel, node.node_id)['worktree_path'])
    c.p['local_workspace'] = str(workspace)
    c.p['publish'] = 'worktree'
    c.profile.write_text(json.dumps({'demo': c.p}))
    current = c.dispatcher._registry.get('worker')
    c.dispatcher._registry.upsert(
        replace(current, workspace_path=str(workspace)), authority_write=True
    )
    original = Git(c.project).head()
    job = c.submit('native integration test')
    assert job.status is JobStatus.RUNNING
    (c.remote / 'workspace/src/calc.py').write_text(
        'def total(values):\n    return sum(values) * 2\n'
    )
    completed = c.dispatcher.complete(job.job_id, c.decision(reply='implemented'))
    assert completed.status is JobStatus.COMPLETED
    assert Git(c.project).head() == original
    review = kernel.capture_review_input(node.node_id, worker_job_id=job.job_id)
    # Deterministic reviewer for plumbing verification, not a claimed LLM review.
    assert 'return sum(values) * 2' in (workspace / 'src/calc.py').read_text()
    kernel.record_review(
        node.node_id,
        reviewer_job_id='deterministic-review',
        result='pass',
        input_digest=review['input_digest'],
        tree_digest=review['tree_digest'],
    )
    kernel.finalize_node(node.node_id)
    kernel.integrate_ready()
    kernel.promote()
    kernel.verify_root()
    accepted = kernel.accept()
    assert accepted['status'] == 'accepted'
    assert (c.project / 'src/calc.py').read_text().endswith('return sum(values) * 2\n')


def test_profile_config_roundtrips_and_bad_modes_are_rejected(sync_case):
    from agents.models import AgentValidationError
    from agents.store import AgentSpecStore
    from agents.config_loader_runtime.defaults_runtime.rendering_runtime.serialization import (
        agent_spec_to_config_dict,
    )
    from agents.config_loader_runtime.parsing_runtime.agent_specs import build_agent_spec

    c = sync_case
    spec = c.dispatcher._config.agents['worker']
    store = AgentSpecStore(c.layout)
    store.save(spec)
    assert store.load('worker').remote_workspace == 'demo'
    assert build_agent_spec('worker', agent_spec_to_config_dict(spec)) == spec
    with pytest.raises(AgentValidationError):
        replace(spec, workspace_mode='inplace')
    with pytest.raises(AgentValidationError):
        replace(spec, remote_workspace='../../bad')


def test_interrupted_apply_recovers_without_duplicate_commits(sync_case, monkeypatch):
    import remote_workspace.service as service_module

    c = sync_case
    job = c.submit()
    (c.remote / 'workspace/src/calc.py').write_text('CHANGED = True\n')
    original_apply = service_module.apply_files

    def interrupt_after_files(*args, **kwargs):
        original_apply(*args, **kwargs)
        raise SyncError('simulated interruption after files, before branch update')

    monkeypatch.setattr(service_module, 'apply_files', interrupt_after_files)
    assert c.dispatcher.complete(job.job_id, c.decision(reply='done')).status is JobStatus.FAILED
    monkeypatch.setattr(service_module, 'apply_files', original_apply)
    receipt = c.service.recover('demo', 'worker', job.job_id)
    assert Git(c.workspace).head() == receipt['head']
    assert (c.workspace / 'src/calc.py').read_text() == 'CHANGED = True\n'


def test_remote_receipt_survives_crash_before_state_update(sync_case, monkeypatch):
    import remote_workspace.endpoint as endpoint_module

    c = sync_case
    job = c.submit()
    original_write = endpoint_module.write_json

    def crash(path, value):
        if Path(path) == c.endpoint.state_path and value.get('phase') == 'collected':
            raise SyncError('crash after result receipt was persisted')
        return original_write(path, value)

    monkeypatch.setattr(endpoint_module, 'write_json', crash)
    assert c.dispatcher.complete(job.job_id, c.decision(reply='done')).status is JobStatus.FAILED
    monkeypatch.setattr(endpoint_module, 'write_json', original_write)
    assert c.service.recover('demo', 'worker', job.job_id)['status'] == 'synced'


def test_restored_job_keeps_remote_artifact_mapping_without_redispatch(sync_case):
    from provider_execution.base import ProviderRuntimeContext

    c = sync_case
    artifact = write_text_artifact(
        c.layout, text='exact request', kind='ask-request', owner_id='restore'
    )
    job = c.submit(artifact_stub(prefix='task', artifact=artifact), artifact)
    context = ProviderRuntimeContext(
        agent_name='worker',
        workspace_path=str(c.workspace),
        backend_type='pane-backed',
        runtime_ref='tmux:1',
        session_ref='session',
    )
    count = len(c.transport.calls)
    resumed = c.service.for_resume(job, context)
    assert str(c.remote / 'artifacts' / (job.job_id + '.txt')) in resumed.request.body
    assert len(c.transport.calls) == count


def test_daemon_environment_keeps_only_the_profile_location():
    from runtime_env.control_plane import control_plane_env

    env = control_plane_env(
        environ={
            'CCB_REMOTE_WORKSPACES_FILE': '/trusted/profiles.json',
            'UNRELATED_SECRET': 'sentinel',
        }
    )
    assert env['CCB_REMOTE_WORKSPACES_FILE'] == '/trusted/profiles.json'
    assert 'UNRELATED_SECRET' not in env


def test_uncommitted_input_and_second_round_keep_controller_head(sync_case):
    c = sync_case
    c.p['publish'] = 'worktree'
    c.profile.write_text(json.dumps({'demo': c.p}))
    head = Git(c.workspace).head()
    (c.workspace / 'README.md').write_text('local uncommitted input\n')
    job = c.submit()
    assert Git(c.workspace).head() == head
    assert (c.remote / 'workspace/README.md').read_text() == 'local uncommitted input\n'
    (c.remote / 'workspace/src/calc.py').write_text('ROUND = 1\n')
    c.dispatcher.complete(job.job_id, c.decision(reply='done'))
    assert Git(c.workspace).head() == head
    next_job = c.submit('review')
    assert next_job.status is JobStatus.RUNNING
    assert (c.remote / 'workspace/src/calc.py').read_text() == 'ROUND = 1\n'
    assert Git(c.workspace).head() == head
    c.dispatcher.complete(next_job.job_id, c.decision(reply='reviewed'))


def test_bootstrap_does_not_export_deleted_history_secrets(sync_case):
    c = sync_case
    git = Git(c.project)
    old = git.head()
    files = git.export(old).validate_bootstrap(old, c.p['include'])
    secret_commit = git.snapshot(
        {**files, 'README.md': ('100644', b'HISTORICAL_SENTINEL')},
        parent=old,
        message='historical private content',
    )
    current = git.snapshot(files, parent=secret_commit, message='remove historical content')
    objects = git.export(current)
    objects.validate_bootstrap(current, c.p['include'])
    assert all(b'HISTORICAL_SENTINEL' not in data for kind, data in objects.items.values())
    assert secret_commit not in objects.items


def test_local_symlink_parent_cannot_redirect_results(sync_case, tmp_path):
    c = sync_case
    job = c.submit()
    (c.remote / 'workspace/src/calc.py').write_text('would overwrite\n')
    original = c.workspace / 'src'
    original.rename(c.workspace / 'src-preserved')
    outside = tmp_path / 'outside'
    outside.mkdir()
    (outside / 'calc.py').write_text('LOCAL_SENTINEL\n')
    original.symlink_to(outside)
    assert c.dispatcher.complete(job.job_id, c.decision(reply='done')).status is JobStatus.FAILED
    assert (outside / 'calc.py').read_text() == 'LOCAL_SENTINEL\n'


def test_shared_empty_tree_dag_has_bounded_expansion():
    # Fewer than 20 objects can encode over 100,000 empty directories. The
    # importer must stop traversal even though file bytes/count both stay zero.
    objects = Objects()
    tree = objects.add('tree', b'')
    for _ in range(17):
        tree = objects.add(
            'tree', b'40000 a\0' + bytes.fromhex(tree) + b'40000 b\0' + bytes.fromhex(tree)
        )
    head = objects.add(
        'commit',
        f'tree {tree}\nauthor T <t@localhost> 0 +0000\ncommitter T <t@localhost> 0 +0000\n\nfixture\n'.encode(),
    )
    with pytest.raises(SyncError, match='entry budget'):
        objects.validate_bootstrap(head, ['a', 'b'])


def test_worktree_mode_exports_only_selected_paths_from_full_local_tree(sync_case):
    c = sync_case
    c.p['publish'] = 'worktree'
    c.profile.write_text(json.dumps({'demo': c.p}))
    (c.workspace / 'private.txt').write_text('EXCLUDED_SENTINEL')
    (c.workspace / '.gitattributes').write_text('*.py filter=unsafe\n')
    raw_git(c.workspace, 'add', 'private.txt', '.gitattributes')
    raw_git(c.workspace, 'commit', '-qm', 'controller-only local tree')
    local_head = Git(c.workspace).head()
    job = c.submit()
    assert job.status is JobStatus.RUNNING
    assert not (c.remote / 'workspace/private.txt').exists()
    assert not (c.remote / 'workspace/.gitattributes').exists()
    request = read_json(c.service.state_root('demo') / (job.job_id + '.request.json'))
    transferred = Objects.from_wire(request['objects'])
    assert all(b'EXCLUDED_SENTINEL' not in data for _, data in transferred.items.values())
    (c.remote / 'workspace/src/calc.py').write_text('RESULT = 5\n')
    result = c.dispatcher.complete(job.job_id, c.decision(reply='done'))
    assert result.status is JobStatus.COMPLETED
    assert Git(c.workspace).head() == local_head
    assert (c.workspace / 'private.txt').read_text() == 'EXCLUDED_SENTINEL'
    assert (c.workspace / '.gitattributes').read_text() == '*.py filter=unsafe\n'


@pytest.mark.parametrize('location', ['project', 'writable', 'symlink'])
def test_profile_rejects_untrusted_ssh_config(sync_case, tmp_path, location):
    c = sync_case
    path = (c.project if location == 'project' else tmp_path) / 'ssh-config'
    if location == 'symlink':
        target = tmp_path / 'real-ssh-config'
        target.write_text('Host example-host\n HostName 127.0.0.1\n')
        path.symlink_to(target)
    else:
        path.write_text('Host example-host\n HostName 127.0.0.1\n')
        path.chmod(0o666 if location == 'writable' else 0o600)
    c.p['ssh_config'] = str(path)
    c.profile.write_text(json.dumps({'demo': c.p}))
    with pytest.raises(SyncError, match='SSH config'):
        c.service.profile('demo', 'worker')


@pytest.mark.parametrize('field', ['profiles', 'ssh_config', 'local_workspace'])
def test_profile_paths_cannot_use_parent_traversal(sync_case, monkeypatch, field):
    c = sync_case
    (c.project.parent / 'unused').mkdir()
    if field == 'profiles':
        # Lexically outside the project, but resolves into project-controlled data.
        nested = c.project / 'profiles.json'
        nested.write_text(c.profile.read_text())
        nested.chmod(0o600)
        ambiguous = c.project.parent / 'unused' / '..' / c.project.name / nested.name
        monkeypatch.setenv('CCB_REMOTE_WORKSPACES_FILE', str(ambiguous))
    elif field == 'ssh_config':
        target = c.project / 'ssh-config'
        target.write_text('Host example-host\n HostName 127.0.0.1\n')
        target.chmod(0o600)
        c.p[field] = str(c.project.parent / 'unused' / '..' / c.project.name / 'ssh-config')
        c.profile.write_text(json.dumps({'demo': c.p}))
    else:
        c.p[field] = str(c.project / '..' / 'outside-workspace')
        c.profile.write_text(json.dumps({'demo': c.p}))
    with pytest.raises(SyncError):
        c.service.profile('demo', 'worker')


def test_ssh_transport_uses_pinned_config_and_disables_forwarding(monkeypatch):
    from remote_workspace.transport import SshTransport

    captured = []

    class Process:
        returncode = 0

        def __init__(self, argv, **kwargs):
            captured.extend(argv)
            kwargs['stdout'].write(b'{"ok":true}')
            kwargs['stdout'].flush()

        def poll(self):
            return 0

        def wait(self):
            return 0

    monkeypatch.setattr('remote_workspace.transport.subprocess.Popen', Process)
    assert SshTransport(
        {
            'host': 'vps-test',
            'remote_root': '/home/test/endpoint',
            'ssh_config': '/home/controller/private-ssh-config',
        }
    ).call({'op': 'probe'}) == {'ok': True}
    assert captured[captured.index('-F') + 1] == '/home/controller/private-ssh-config'
    for option in [
        'ForwardAgent=no',
        'ClearAllForwardings=yes',
        'SendEnv=-*',
        'StrictHostKeyChecking=yes',
        'ControlPath=none',
        'PermitLocalCommand=no',
    ]:
        assert option in captured
