import base64
import hashlib
import importlib.util
import json
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

if sys.platform != 'linux':
    pytest.skip('remote deployment currently targets Linux', allow_module_level=True)

from remote_workspace.deploy import deployment_payload
from remote_workspace.files import write_json
from remote_workspace.installer import install
from remote_workspace.objects import SyncError
from test_remote_workspace import sync_case


def changed(payload):
    payload = json.loads(json.dumps(payload))
    name = 'remote_workspace/provider_remote.py'
    payload['files'][name] = base64.b64encode(
        base64.b64decode(payload['files'][name]) + b'\n# new release\n'
    ).decode()
    hashes = {
        k: hashlib.sha256(base64.b64decode(v)).hexdigest()
        for k, v in payload['files'].items()
        if k != 'deployment.json'
    }
    version = hashlib.sha256(json.dumps(hashes, sort_keys=True).encode()).hexdigest()
    payload['version'] = version
    payload['files']['deployment.json'] = base64.b64encode(
        json.dumps({'protocol': 1, 'version': version, 'hashes': hashes}).encode()
    ).decode()
    payload['update'] = True
    return payload


@pytest.fixture
def release(tmp_path):
    root = tmp_path / 'endpoint'
    payload = deployment_payload(
        {'remote_root': str(root), 'workspace_id': 'fixture-id', 'include': ['src']},
        {'session_id': 'session', 'claude': '/bin/false', 'tmux': '/bin/false'},
    )
    assert install(payload)['ok']
    write_json(root / 'state.json', {'phase': 'acked', 'job_id': 'fixture'})
    return root, payload


def test_deployment_update_verify_and_explicit_rollback(release):
    root, old = release
    new = changed(old)
    result = install(new)
    assert result['previous'] == old['version']
    assert install({'root': str(root), 'action': 'verify'})['backups'] == [old['version']]
    rolled = install({'root': str(root), 'action': 'rollback', 'version': old['version']})
    assert rolled['rollback'] and rolled['version'] == old['version']
    assert install({'root': str(root), 'action': 'verify'})['version'] == old['version']


def test_update_rejects_active_session_and_unfinished_job(release, monkeypatch):
    root, old = release
    new = changed(old)
    write_json(root / 'state.json', {'phase': 'prepared'})
    with pytest.raises(ValueError, match='unfinished'):
        install(new)
    write_json(root / 'state.json', {'phase': 'acked'})
    monkeypatch.setattr(
        'remote_workspace.installer.subprocess.run', lambda *a, **k: SimpleNamespace(returncode=0)
    )
    with pytest.raises(ValueError, match='stop owned'):
        install(new)
    assert install({'root': str(root), 'action': 'verify'})['version'] == old['version']


def test_corrupt_payload_has_no_partial_mutation(release):
    root, old = release
    bad = changed(old)
    bad['files']['remote_workspace/endpoint.py'] = base64.b64encode(b'bad').decode()
    with pytest.raises(ValueError, match='checksum'):
        install(bad)
    assert install({'root': str(root), 'action': 'verify'})['version'] == old['version']


def test_interrupted_update_refuses_execution_until_rollback(release, monkeypatch):
    import subprocess, sys
    from remote_workspace import installer

    root, old = release
    new = changed(old)
    real_write = installer.write

    def interrupted(path, data):
        if path == root / 'remote_workspace/endpoint.py':
            raise OSError('power loss fixture')
        return real_write(path, data)

    monkeypatch.setattr(installer, 'write', interrupted)
    with pytest.raises(OSError):
        install(new)
    assert (root / 'deployment-pending.json').exists()
    run = subprocess.run(
        [sys.executable, str(root / 'endpoint.py')], capture_output=True, text=True
    )
    assert run.returncode and 'deployment incomplete' in run.stderr
    with pytest.raises(ValueError, match='interrupted'):
        install({'root': str(root), 'action': 'verify'})
    monkeypatch.setattr(installer, 'write', real_write)
    assert install({'root': str(root), 'action': 'rollback', 'version': old['version']})['ok']
    assert not (root / 'deployment-pending.json').exists()


def test_profiles_are_published_only_after_validation(sync_case):
    c = sync_case
    spec = importlib.util.spec_from_file_location(
        'remote_setup', Path(__file__).parents[1] / 'tools/remote_workspace_setup.py'
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    path = c.profile.parent / 'new-profiles.json'
    with pytest.raises(SyncError):
        module.register_profile(path, 'new', {**c.p, 'host': 'invalid host'})
    assert not path.exists()
    module.register_profile(path, 'new', c.p)
    before = path.read_bytes()
    with pytest.raises(ValueError, match='owns'):
        module.register_profile(path, 'duplicate', c.p)
    assert path.read_bytes() == before


def test_bridge_restart_preserves_unfinished_runtime_binding(tmp_path):
    from remote_workspace.provider_client import ProviderBridge

    write_json(tmp_path / 'state.json', {'phase': 'prepared'})
    write_json(
        tmp_path / 'transport.json', {'session_id': 'abc', 'boot_id': 'boot1', 'generation': 'run1'}
    )
    bridge = ProviderBridge({'terminal': {'session_id': 'abc'}}, tmp_path)
    assert bridge.expected_runtime == {'boot_id': 'boot1', 'generation': 'run1'}
    with pytest.raises(SyncError):
        ProviderBridge({'terminal': {'session_id': 'other'}}, tmp_path)
    write_json(tmp_path / 'state.json', {'phase': 'done'})
    assert not ProviderBridge({'terminal': {'session_id': 'abc'}}, tmp_path).expected_runtime


def test_diagnostic_retention_is_bounded(tmp_path):
    from remote_workspace.provider_client import ProviderBridge

    bridge = ProviderBridge({'terminal': {'session_id': 'abc'}}, tmp_path)
    with (tmp_path / 'errors').open('a+b', buffering=0) as stream:
        stream.write(b'x' * (2 * 1024 * 1024) + b'last error')
        bridge.errors = stream
        bridge.trim_errors()
        assert (tmp_path / 'errors').stat().st_size < 300 * 1024
        assert (tmp_path / 'errors').read_bytes().endswith(b'last error')
