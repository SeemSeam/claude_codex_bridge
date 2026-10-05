"""Failure boundaries use real process death, not catchable exceptions."""

from dataclasses import replace
import json
import os
from pathlib import Path
import signal
import sys

import pytest

if sys.platform != 'linux':
    pytest.skip(
        'remote hardening tests require Linux process and file primitives', allow_module_level=True
    )

from ccbd.api_models import DeliveryScope, JobStatus, MessageEnvelope
from ccbd.services.dispatcher import JobDispatcher
from ccbd.services.registry import AgentRegistry
from message_bureau import CallbackEdgeStore, CallbackEdgeState
from remote_workspace import preflight
from remote_workspace.files import read_json
from remote_workspace.objects import Git, SyncError
from test_remote_workspace import sync_case
from test_v2_message_bureau_dispatcher_integration import _provider_config, _runtime


def die():
    os.kill(os.getpid(), signal.SIGKILL)


def run_crashing_child(work):
    pid = os.fork()
    if pid == 0:
        try:
            work()
        finally:
            os._exit(99)
    _, status = os.waitpid(pid, 0)
    assert os.WIFSIGNALED(status) and os.WTERMSIG(status) == signal.SIGKILL


def restored(c, config=None):
    config = config or c.dispatcher._config
    return JobDispatcher(
        c.layout,
        config,
        AgentRegistry(c.layout, config),
        execution_service=c.execution,
        workspace_synchronizer=c.service,
    )


@pytest.mark.parametrize(
    'boundary',
    [
        'remote_receipt',
        'local_result',
        'partial_apply',
        'local_apply',
        'remote_ack',
        'local_receipt',
    ],
)
def test_sigkill_recovers_same_result_without_task_replay(sync_case, boundary):
    c = sync_case
    job = c.submit()
    (c.remote / 'workspace/src/calc.py').write_text('RECOVERED = True\n')
    (c.remote / 'workspace/README.md').write_text('second changed file\n')

    def work():
        import remote_workspace.endpoint as endpoint
        import remote_workspace.service as service

        if boundary in ('remote_receipt', 'remote_ack'):
            original = endpoint.write_json

            def write(path, value):
                original(path, value)
                if (
                    boundary == 'remote_receipt'
                    and str(path).endswith('.result.json')
                    or boundary == 'remote_ack'
                    and value.get('phase') == 'acked'
                ):
                    die()

            endpoint.write_json = write
        elif boundary == 'partial_apply':
            original = os.rename

            def rename(src, dst, *args, **kwargs):
                original(src, dst, *args, **kwargs)
                if str(src).startswith('.ccb-sync-') and dst == 'README.md':
                    die()

            os.rename = rename
        elif boundary == 'local_apply':
            original = service.apply_files

            def apply(*args, **kwargs):
                original(*args, **kwargs)
                die()

            service.apply_files = apply
        else:
            original = service.write_json

            def write(path, value):
                original(path, value)
                if (
                    boundary == 'local_result'
                    and str(path).endswith('.result.json')
                    or boundary == 'local_receipt'
                    and Path(path).name == 'state.json'
                    and value.get('phase') == 'done'
                ):
                    die()

            service.write_json = write
        c.dispatcher.complete(job.job_id, c.decision(reply='same result'))

    run_crashing_child(work)
    receipt = c.service.recover('demo', 'worker', job.job_id)
    assert c.service.recover('demo', 'worker', job.job_id) == receipt
    dispatcher = restored(c)
    result = dispatcher.complete(job.job_id, c.decision(reply='same result'))
    assert result.status is JobStatus.COMPLETED
    assert Git(c.workspace).head() == receipt['head']
    assert (c.workspace / 'src/calc.py').read_text() == 'RECOVERED = True\n'
    assert (c.workspace / 'README.md').read_text() == 'second changed file\n'
    assert len(c.execution.started) == 1
    assert read_json(c.remote / 'state.json')['phase'] == 'acked'


@pytest.mark.parametrize('boundary', ['terminal_record', 'reply_record', 'callback_record'])
def test_sigkill_between_result_and_callback_does_not_lose_or_duplicate_chain(sync_case, boundary):
    c = sync_case
    config = _provider_config('codex', 'worker')
    config = replace(
        config,
        agents={
            **config.agents,
            'worker': replace(
                config.agents['worker'],
                provider='claude',
                remote_workspace='demo',
                workspace_mode='git-worktree',
            ),
        },
    )
    registry = AgentRegistry(c.layout, config)
    for name in config.agents:
        registry.upsert(_runtime(name, project_id=c.layout.project_id, layout=c.layout, pid=100))
    dispatcher = JobDispatcher(
        c.layout, config, registry, execution_service=c.execution, workspace_synchronizer=c.service
    )

    def submit(agent, sender, body, options=None):
        return dispatcher.submit(
            MessageEnvelope(
                project_id=c.layout.project_id,
                to_agent=agent,
                from_actor=sender,
                body=body,
                task_id='crash-chain',
                reply_to=None,
                message_type='ask',
                delivery_scope=DeliveryScope.SINGLE,
                route_options=options,
            )
        ).jobs[0]

    parent = submit('codex', 'user', 'implement then review')
    dispatcher.tick()
    child = submit('worker', 'codex', 'implement once', {'mode': 'chain'})
    dispatcher.complete(parent.job_id, c.decision(reply='delegated'))
    dispatcher.tick()
    (c.remote / 'workspace/src/calc.py').write_text('CHAIN_RESULT = True\n')

    def work():
        from ccbd.services.dispatcher_runtime.finalization_runtime import service

        target = dispatcher._message_bureau if boundary == 'reply_record' else service
        name = (
            'record_reply'
            if boundary == 'reply_record'
            else (
                'persist_terminal_completion'
                if boundary == 'terminal_record'
                else 'record_message_bureau_completion'
            )
        )
        original = getattr(target, name)

        def crash(*args, **kwargs):
            result = original(*args, **kwargs)
            die()
            return result

        setattr(target, name, crash)
        dispatcher.complete(child.job_id, c.decision(reply='implemented exactly once'))

    run_crashing_child(work)
    dispatcher = restored(c, config)
    dispatcher.tick()
    edge = CallbackEdgeStore(c.layout).get_latest_for_child_job(child.job_id)
    assert edge.state is CallbackEdgeState.CONTINUATION_SUBMITTED
    continuation_id = edge.continuation_job_id
    assert continuation_id
    assert dispatcher.get(child.job_id).status is JobStatus.COMPLETED
    assert (c.workspace / 'src/calc.py').read_text() == 'CHAIN_RESULT = True\n'
    dispatcher.tick()
    assert (
        CallbackEdgeStore(c.layout).get_latest_for_child_job(child.job_id).continuation_job_id
        == continuation_id
    )
    assert sum(j.job_id == child.job_id for j in c.execution.started) == 1
    replies = dispatcher._message_bureau._reply_store.list_message(edge.child_message_id)
    assert len([reply for reply in replies if not reply.diagnostics.get('notice')]) == 1


def test_reply_recovery_does_not_promote_unsynchronized_or_failed_job(sync_case):
    from remote_workspace.callback_recovery import restore_missing_reply

    c = sync_case
    job = c.submit()
    assert restore_missing_reply(c.dispatcher, job) is None
    terminal = replace(job, status=JobStatus.COMPLETED, terminal_decision={'diagnostics': {}})
    assert restore_missing_reply(c.dispatcher, terminal) is None
    terminal = replace(
        terminal,
        status=JobStatus.FAILED,
        terminal_decision={'diagnostics': {'workspace_sync': {'status': 'synced'}}},
    )
    assert restore_missing_reply(c.dispatcher, terminal) is None


def test_untracked_omitted_result_blocks_completion_then_recovers(sync_case):
    c = sync_case
    job = c.submit()
    outside = c.remote / 'workspace/new_module.py'
    outside.write_text('REQUIRED = True\n')
    result = c.dispatcher.complete(job.job_id, c.decision(reply='looks complete'))
    assert result.status is JobStatus.FAILED
    assert 'new_module.py' in read_json(c.service.state_root('demo') / 'state.json')['error']
    assert not (c.remote / (job.job_id + '.result.json')).exists()
    outside.rename(c.remote / 'workspace/src/new_module.py')
    assert c.service.recover('demo', 'worker', job.job_id)['status'] == 'synced'
    assert (c.workspace / 'src/new_module.py').read_text() == 'REQUIRED = True\n'


def test_cache_and_control_exclusions_stay_uncollected(sync_case):
    c = sync_case
    job = c.submit()
    cache = c.remote / 'workspace/__pycache__'
    cache.mkdir()
    (cache / 'test.pyc').write_bytes(b'cache')
    (c.remote / 'workspace/.env').write_text('DO_NOT_COPY=sentinel\n')
    assert c.dispatcher.complete(job.job_id, c.decision(reply='done')).status is JobStatus.COMPLETED
    assert not (c.workspace / '.env').exists()
    assert not (c.workspace / '__pycache__').exists()


def test_preflight_enrollment_is_explicit_and_drift_blocks(tmp_path, monkeypatch):
    project = tmp_path / 'project'
    project.mkdir()
    profiles = tmp_path / 'profiles.json'
    value = {'schema': 1, 'project': str(project), 'tools': {'codex': 'version1'}}
    monkeypatch.setattr(preflight, 'inspect', lambda *args: value.copy())
    with pytest.raises(SyncError, match='missing'):
        preflight.check(project, profiles, tmp_path, {})
    preflight.check(project, profiles, tmp_path, {}, record=True)
    assert preflight.check(project, profiles, tmp_path, {})['ok']
    with pytest.raises(SyncError, match='older launch baseline'):
        preflight.check(project, profiles, tmp_path, {'CCB_REMOTE_PREFLIGHT_BASELINE_HASH': 'old'})
    with pytest.raises(SyncError, match='exists'):
        preflight.check(project, profiles, tmp_path, {}, record=True)
    value['tools'] = {'codex': 'version2'}
    with pytest.raises(SyncError, match='drift: tools'):
        preflight.check(project, profiles, tmp_path, {})


def test_preflight_source_reaches_daemon_without_unrelated_secrets():
    from runtime_env.control_plane import control_plane_env

    env = control_plane_env(
        environ={'CCB_REMOTE_PREFLIGHT_SOURCE': '/trusted/ccb', 'SECRET': 'hidden'}
    )
    assert env['CCB_REMOTE_PREFLIGHT_SOURCE'] == '/trusted/ccb'
    assert 'SECRET' not in env


def test_preflight_ssh_cannot_consume_piped_ask_input(tmp_path, monkeypatch):
    from types import SimpleNamespace
    import subprocess

    def run(argv, **kwargs):
        assert kwargs['stdin'] == subprocess.DEVNULL
        return SimpleNamespace(
            returncode=0,
            stdout=json.dumps(
                {
                    'workspace_id': 'fixture',
                    'session_id': 'session',
                    'hashes': {},
                    'version': 'test',
                }
            ),
        )

    monkeypatch.setattr(preflight.subprocess, 'run', run)
    profile = {
        'host': 'fixture',
        'remote_root': '/home/test/fixture',
        'workspace_id': 'fixture',
        'terminal': {'session_id': 'session'},
    }
    assert preflight.remote_identity(profile, tmp_path)['version'] == 'test'


def test_launch_environment_ignores_cwd_and_matches_nonlogin_socket(tmp_path):
    project = tmp_path / 'project'
    project.mkdir()
    env = preflight.launch_environment(
        {'PATH': f'.:{project}/bin:/usr/bin', 'XDG_RUNTIME_DIR': '/bad'},
        tmp_path,
        tmp_path / 'state',
        project,
    )
    assert '.' not in env['PATH'].split(':')
    assert str(project / 'bin') not in env['PATH'].split(':')
    assert env['XDG_RUNTIME_DIR'] != '/bad'
    again = preflight.launch_environment(
        {'PATH': '/usr/bin'}, tmp_path, tmp_path / 'state', project
    )
    assert again['XDG_RUNTIME_DIR'] == env['XDG_RUNTIME_DIR']


@pytest.mark.parametrize('local_provider', [None, 'codex', 'gemini', 'unknown-provider'])
def test_preflight_requires_only_configured_local_provider_tools(
    tmp_path, monkeypatch, local_provider
):
    project = tmp_path / 'project'
    (project / '.ccb').mkdir(parents=True)
    local = (
        ''
        if local_provider is None
        else (
            f'\n[agents.reviewer]\nprovider = "{local_provider}"\ntarget = "."\n'
            'workspace_mode = "inplace"\n'
            'restore = "auto"\npermission = "manual"\n'
        )
    )
    layout = (
        'worker:claude' if local_provider is None else f'reviewer:{local_provider}; worker:claude'
    )
    default_agents = ['worker'] if local_provider is None else ['reviewer', 'worker']
    (project / '.ccb/ccb.config').write_text(
        f'version = 2\nlayout = "{layout}"\ndefault_agents = {json.dumps(default_agents)}\n'
        '[agents.worker]\nprovider = "claude"\ntarget = "."\n'
        'restore = "auto"\npermission = "manual"\n'
        'workspace_mode = "git-worktree"\nremote_workspace = "demo"\n' + local
    )
    profiles = tmp_path / 'profiles.json'
    profile = {'project_root': str(project)}
    profiles.write_text(json.dumps({'demo': profile}))
    monkeypatch.setattr('remote_workspace.provider_client.load_profile', lambda *a: ({}, profile))
    monkeypatch.setattr(preflight, 'remote_identity', lambda *a: {'version': 'verified'})
    monkeypatch.setattr(preflight, 'source_identity', lambda *a: {'sha256': 'source'})
    monkeypatch.setattr(
        preflight, 'tool_identity', lambda path, env: {'path': str(path), 'version': 'test'}
    )
    required = {'git', 'ssh', 'tmux'} | ({local_provider} if local_provider else set())
    looked_up = []

    def which(name, *, path):
        executable = Path(name).name
        looked_up.append(executable)
        assert executable in required, f'unconfigured local executable queried: {name}'
        if executable in ('git', 'ssh'):
            assert name == '/usr/bin/' + executable
        return '/usr/bin/' + executable

    monkeypatch.setattr(preflight.shutil, 'which', which)
    if local_provider == 'unknown-provider':
        with pytest.raises(SyncError, match='cannot identify native CLI'):
            preflight.inspect(
                project, profiles, tmp_path, {'PATH': '/usr/bin', 'XDG_RUNTIME_DIR': str(tmp_path)}
            )
        assert looked_up == []
        return
    result = preflight.inspect(
        project, profiles, tmp_path, {'PATH': '/usr/bin', 'XDG_RUNTIME_DIR': str(tmp_path)}
    )
    assert set(result['tools']) == required | {'python'}
    assert set(looked_up) == required


def test_version_probe_cannot_consume_piped_ask_input(tmp_path, monkeypatch):
    from types import SimpleNamespace
    import subprocess

    executable = tmp_path / 'ssh'
    executable.touch()
    calls = []

    def run(argv, **kwargs):
        calls.append(argv)
        assert kwargs['stdin'] == subprocess.DEVNULL
        return SimpleNamespace(returncode=int(argv[-1] == '--version'), stdout='version', stderr='')

    monkeypatch.setattr(preflight.subprocess, 'run', run)
    assert preflight.tool_identity(executable, {})['version'] == 'version'
    assert [cmd[-1] for cmd in calls] == ['--version', '-V']


def test_live_capacity_uses_explicit_host_paths_and_tools(tmp_path):
    from test_remote_live_capacity import capacity_profile

    settings = {
        'host': 'disposable-worker',
        'remote_parent': '/srv/acceptance',
        'ssh_config': str(tmp_path / 'ssh_config'),
        'python': '/opt/runtime/python3',
    }
    profile = capacity_profile({'host': 'old', 'ssh_config': '/old/config'}, settings, 'fixture')
    assert profile['host'] == 'disposable-worker'
    assert profile['remote_root'] == '/srv/acceptance/ccb-capacity-fixture'
    assert profile['python'] == '/opt/runtime/python3'
    assert profile['ssh_config'] == settings['ssh_config']
    del settings['ssh_config']
    assert 'ssh_config' not in capacity_profile(profile, settings, 'next')


@pytest.mark.parametrize('parent', ['/', '.', 'relative/path', '/srv/../etc'])
def test_live_capacity_rejects_ambiguous_remote_parent(parent):
    from test_remote_live_capacity import capacity_profile

    with pytest.raises(ValueError, match='remote_parent'):
        capacity_profile({}, {'host': 'fixture', 'remote_parent': parent}, 'fixture')


def test_scope_check_does_not_follow_omitted_symlink(sync_case):
    c = sync_case
    job = c.submit()
    (c.remote / 'workspace/omitted-link').symlink_to('/nonexistent-private-target')
    result = c.dispatcher.complete(job.job_id, c.decision(reply='done'))
    assert result.status is JobStatus.FAILED
    assert 'omitted-link' in read_json(c.service.state_root('demo') / 'state.json')['error']
