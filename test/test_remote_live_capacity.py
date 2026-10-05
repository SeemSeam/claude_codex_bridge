"""Opt-in real SSH tests; model execution is a fixture, not a Claude run."""

import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import shlex
import subprocess
import sys
import threading
import time
import uuid

import pytest

if sys.platform != 'linux':
    pytest.skip(
        'remote capacity tests require Linux process and file primitives', allow_module_level=True
    )

from ccbd.api_models import JobStatus
from remote_workspace.deploy import deploy
from remote_workspace.files import write_json
from remote_workspace.transport import SshTransport, ssh_argv
from test_remote_workspace import sync_case

SETTINGS = os.environ.get('CCB_LIVE_CAPACITY_SETTINGS')
pytestmark = pytest.mark.skipif(
    not SETTINGS, reason='explicit disposable VPS capacity settings required'
)


def capacity_profile(base, settings, run_id):
    parent = PurePosixPath(settings['remote_parent'])
    if not parent.is_absolute() or '..' in parent.parts or str(parent) == '/':
        raise ValueError('remote_parent must be an absolute non-root directory without traversal')
    profile = {
        **base,
        'host': settings['host'],
        'remote_root': str(parent / ('ccb-capacity-' + run_id)),
        'workspace_id': 'capacity-' + run_id,
        'timeout_seconds': 60,
        'python': settings.get('python', '/usr/bin/python3'),
    }
    profile.pop('ssh_config', None)
    if settings.get('ssh_config'):
        profile['ssh_config'] = settings['ssh_config']
    return profile


@pytest.mark.parametrize('mib,timeout_failure', [(16, False), (31.5, False), (16, True)])
def test_real_ssh_capacity_and_bounded_failure(sync_case, mib, timeout_failure):
    c = sync_case
    settings = json.loads(Path(SETTINGS).read_text())
    profile = capacity_profile(c.p, settings, uuid.uuid4().hex)
    remote = profile['remote_root']
    c.profile.write_text(json.dumps({'demo': profile}))
    env = settings.get('env', {})
    deploy(
        profile,
        {
            'session_id': str(uuid.uuid4()),
            'claude': '/bin/false',
            'tmux': settings.get('tmux', '/usr/bin/tmux'),
            'env': env,
        },
        git=settings.get('git', '/usr/bin/git'),
        git_env=env,
    )
    c.service.transport_factory = SshTransport
    job = c.submit('controlled binary synchronization fixture; zero model calls')
    assert job.status is JobStatus.RUNNING
    # Generate only controller-owned disposable data and a per-test SSH stdout
    # throttle. No global qdisc, route, firewall or service is modified.
    proxy = '#!' + profile['python'] + '''
import subprocess,sys,time
p=subprocess.run([sys.executable,*sys.argv[1:]],input=sys.stdin.buffer.read(),stdout=subprocess.PIPE)
try:
 for i in range(0,len(p.stdout),262144):
  sys.stdout.buffer.write(p.stdout[i:i+262144]);sys.stdout.buffer.flush();time.sleep(0.0625)
except BrokenPipeError: pass
raise SystemExit(p.returncode)
'''
    size = int(mib * 1024 * 1024)
    script = (
        'import hashlib,json,pathlib\n'
        f'root=pathlib.Path({remote!r})\n'
        "(root/'workspace/outputs').mkdir(exist_ok=True)\n"
        f"data=(bytes(range(256))*({size}//256+1))[:{size}]\n"
        "(root/'workspace/outputs/result.bin').write_bytes(data)\n"
        f"(root/'slow-python').write_text({proxy!r})\n"
        "(root/'slow-python').chmod(0o700)\n"
        "print(json.dumps({'sha256':hashlib.sha256(data).hexdigest()}))\n"
    )
    generated = subprocess.run(
        [*ssh_argv(profile), profile['host'], shlex.join([profile['python'], '-I', '-'])],
        input=script,
        text=True,
        capture_output=True,
        check=True,
        timeout=30,
    )
    expected = json.loads(generated.stdout)['sha256']
    collecting = threading.Event()
    timings = {}

    class MeasuredTransport(SshTransport):
        def call(self, request):
            if request['op'] != 'collect':
                return super().call(request)
            collecting.set()
            start = time.monotonic()
            try:
                return SshTransport(
                    {
                        **profile,
                        'python': remote + '/slow-python',
                        'timeout_seconds': 2 if timeout_failure else 60,
                        'retry_attempts': 1,
                    }
                ).call(request)
            finally:
                timings['collect_seconds'] = time.monotonic() - start

    c.service.transport_factory = MeasuredTransport

    def wait_for_lock():
        assert collecting.wait(30)
        start = time.monotonic()
        with c.dispatcher._chain_transition_lock:
            timings['same_project_lock_wait_seconds'] = time.monotonic() - start

    waiter = threading.Thread(target=wait_for_lock)
    waiter.start()
    start = time.monotonic()
    result = c.dispatcher.complete(job.job_id, c.decision(reply='binary fixture ready'))
    timings['completion_seconds'] = time.monotonic() - start
    waiter.join(5)
    assert not waiter.is_alive()
    if timeout_failure:
        assert result.status is JobStatus.FAILED
        assert result.terminal_decision['reason'] == 'workspace_sync_failed'
        c.service.transport_factory = SshTransport
        start = time.monotonic()
        receipt = c.service.recover('demo', 'worker', job.job_id)
        timings['recovery_seconds'] = time.monotonic() - start
        assert receipt['status'] == 'synced'
        assert c.dispatcher.get(job.job_id).status is JobStatus.FAILED
    else:
        assert result.status is JobStatus.COMPLETED
    actual = hashlib.sha256((c.workspace / 'outputs/result.bin').read_bytes()).hexdigest()
    assert actual == expected
    assert len(c.execution.started) == 1
    write_json(
        Path(settings['evidence']) / f'capacity-{mib}-{timeout_failure}.json',
        {
            'bytes': size,
            'ok': True,
            'job_id': job.job_id,
            'remote_root': remote,
            'model_calls': 0,
            'sha256': actual,
            'throttle_mib_per_second': 4,
            'timeout_fixture': timeout_failure,
            'failed_job_stays_failed': timeout_failure,
            **timings,
        },
    )
