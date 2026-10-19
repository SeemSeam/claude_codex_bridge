import hashlib
import importlib.util
import json
import os
from pathlib import Path
import sys
import zipfile

import pytest

SPEC = importlib.util.spec_from_file_location('windows_maintenance',
    Path(__file__).resolve().parents[1] / 'platforms/windows/tools/manage_install.py')
maintenance = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(maintenance)
pytestmark = pytest.mark.skipif(os.name != 'nt', reason='native Windows transactions')


def payload(path, version):
    (path / 'bin').mkdir(parents=True)
    (path / 'bin/ccb.exe').write_bytes(b'MZfake-test-executable')
    (path / 'platforms/windows').mkdir(parents=True)
    (path / 'platforms/windows/ccb.py').write_text('# fixture')
    (path / 'lib').mkdir()
    (path / 'config').mkdir()
    (path / 'VERSION').write_text(version)
    (path / 'WINDOWS_MANIFEST.json').write_text(json.dumps(dict(platform='windows', arch='x86_64', version=version)))
    return path


@pytest.fixture
def installation(tmp_path, monkeypatch):
    root = payload(tmp_path / '安装 runtime', '1.0.0')
    (root / 'config/local.txt').write_text('用户配置', encoding='utf-8')
    monkeypatch.setattr(maintenance, 'require_stopped', lambda root: None)
    monkeypatch.setattr(maintenance, 'smoke', lambda root, python: None)
    return maintenance.Installation(root, Path(sys.executable))


def archive(tmp_path):
    source = payload(tmp_path / 'next', '2.0.0')
    target = tmp_path / 'candidate.zip'
    with zipfile.ZipFile(target, 'w') as bundle:
        for file in source.rglob('*'):
            bundle.write(file, 'ccb-windows-x86_64/' + file.relative_to(source).as_posix())
    return target, hashlib.sha256(target.read_bytes()).hexdigest()


def test_update_and_rollback_preserve_local_edits(installation, tmp_path):
    package, digest = archive(tmp_path)
    with installation.lock():
        result = installation.update(package, digest)
    assert result['status'] == 'updated'
    assert maintenance.metadata(installation.root)['version'] == '2.0.0'
    config = installation.root / 'config/local.txt'
    assert config.read_text(encoding='utf-8') == '用户配置'
    config.write_text('edited after update')
    installation.rollback()
    assert maintenance.metadata(installation.root)['version'] == '1.0.0'
    assert config.read_text() == 'edited after update'


def test_bad_checksum_does_not_change_install(installation, tmp_path):
    package, _ = archive(tmp_path)
    with pytest.raises(ValueError, match='SHA256 mismatch'):
        installation.update(package, '0' * 64)
    assert maintenance.metadata(installation.root)['version'] == '1.0.0'
    assert installation.transaction() is None


def test_post_switch_failure_restores_original(installation, tmp_path, monkeypatch):
    package, digest = archive(tmp_path)
    def smoke(root, python):
        if root == installation.root and maintenance.metadata(root)['version'] == '2.0.0':
            raise RuntimeError('simulated post-switch smoke failure')
    monkeypatch.setattr(maintenance, 'smoke', smoke)
    with pytest.raises(RuntimeError, match='post-switch'):
        installation.update(package, digest)
    assert maintenance.metadata(installation.root)['version'] == '1.0.0'
    assert installation.transaction()['phase'] == 'recovered'
    assert list(installation.storage.glob('failed-*'))


@pytest.mark.parametrize('cut', ['before-old-move', 'after-old-move', 'after-new-move'])
def test_explicit_recovery_after_interrupted_process(installation, tmp_path, monkeypatch, cut):
    package, digest = archive(tmp_path)
    original = Path.rename
    def interrupted(path, target):
        if cut == 'before-old-move' and path == installation.root:
            raise KeyboardInterrupt()
        result = original(path, target)
        if ((cut == 'after-old-move' and path == installation.root) or
                (cut == 'after-new-move' and target == installation.root)):
            raise KeyboardInterrupt()
        return result
    monkeypatch.setattr(Path, 'rename', interrupted)
    with pytest.raises(KeyboardInterrupt):
        installation.update(package, digest)
    monkeypatch.setattr(Path, 'rename', original)
    assert installation.transaction()['phase'] == 'prepared'
    assert installation.recover()['status'] == 'recovered'
    assert maintenance.metadata(installation.root)['version'] == '1.0.0'


def test_active_runtime_blocks_before_staging(installation, tmp_path, monkeypatch):
    package, digest = archive(tmp_path)
    def active(root):
        raise RuntimeError('still running')
    monkeypatch.setattr(maintenance, 'require_stopped', active)
    with pytest.raises(RuntimeError, match='still running'):
        installation.update(package, digest)
    assert not list(installation.storage.glob('candidate-*'))


def test_concurrent_maintenance_lock(installation):
    with installation.lock():
        with pytest.raises(RuntimeError, match='Another Windows'):
            with installation.lock():
                pytest.fail('lock must exclude another caller')


@pytest.mark.parametrize('entry', ['../escape.txt', '/absolute.txt', 'pkg/a:stream', 'pkg/CON.txt',
                                  'pkg/name.', 'pkg/dir\\escape', 'pkg/a/../../escape'])
def test_unsafe_archive_rejected_before_extraction(tmp_path, entry):
    package = tmp_path / 'bad.zip'
    with zipfile.ZipFile(package, 'w') as bundle:
        info = zipfile.ZipInfo('placeholder')
        info.filename = entry  # ZipInfo otherwise normalizes backslashes on Windows.
        bundle.writestr(info, 'bad')
    with pytest.raises(ValueError, match='Unsafe'):
        maintenance.extract(package, hashlib.sha256(package.read_bytes()).hexdigest(), tmp_path / 'out')
    assert not (tmp_path / 'out').exists()


def test_journal_cannot_escape_storage(installation):
    maintenance.save(installation.journal, dict(root=str(installation.root), backup='../other', phase='prepared'))
    with pytest.raises(ValueError, match='journal path'):
        installation.recover()


def test_project_root_is_rejected(tmp_path):
    root = payload(tmp_path / 'project', '1.0.0')
    (root / '.ccb').mkdir()
    with pytest.raises(ValueError, match='project'):
        maintenance.Installation(root, Path(sys.executable))
