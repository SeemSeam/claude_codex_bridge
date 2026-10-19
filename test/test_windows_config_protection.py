from pathlib import Path
import json
import os
import shutil
import subprocess

import pytest

PS = shutil.which('powershell')
pytestmark = pytest.mark.skipif(not PS, reason='Windows PowerShell 5.1 required')
HELPER = Path(__file__).resolve().parents[1] / 'platforms/windows/installer/config_io.ps1'


def literal(value):
    return "'" + str(value).replace("'", "''") + "'"


def run(tmp_path, path, body):
    script = tmp_path / 'exercise.ps1'
    script.write_text("$ErrorActionPreference='Stop'\n. " + literal(HELPER) +
                      '\n$target = ' + literal(path) + '\n' + body, encoding='utf-8-sig')
    return subprocess.run([PS, '-NoProfile', '-NonInteractive', '-ExecutionPolicy', 'Bypass',
                           '-File', str(script)], capture_output=True, timeout=30)


def test_chinese_deep_configuration_and_exact_backup(tmp_path):
    path = tmp_path / '中文配置.json'
    nested = {'value': '项目😀'}
    for _ in range(20):
        nested = {'nested': nested}
    original = {'hooks': nested, 'permissions': {'allow': ['Read'], 'deny': ['Write']}}
    raw = json.dumps(original, ensure_ascii=False).encode('utf-8')
    path.write_bytes(raw)
    result = run(tmp_path, path, "$value=Read-CCBSettings $target\n$value.permissions.allow += 'Bash(ccb ask *)'\nWrite-CCBSettings $target $value")
    assert result.returncode == 0, result.stderr
    actual = json.loads(path.read_bytes())
    assert actual['hooks'] == original['hooks']
    assert actual['permissions']['deny'] == ['Write']
    assert not path.read_bytes().startswith(b'\xef\xbb\xbf')
    assert list(tmp_path.glob('*.ccb-backup-*'))[0].read_bytes() == raw


@pytest.mark.parametrize('raw', [b'{broken', b'null', b'[]', b'', b'\xff',
                                  b'{"permissions":"bad"}', b'{"permissions":{"allow":"bad"}}'])
def test_bad_configuration_fails_without_writing(tmp_path, raw):
    path = tmp_path / 'settings.json'
    path.write_bytes(raw)
    result = run(tmp_path, path, '$value=Read-CCBSettings $target\nWrite-CCBSettings $target $value')
    assert result.returncode != 0
    assert path.read_bytes() == raw
    assert not list(tmp_path.glob('*.ccb-backup-*'))


def test_concurrent_change_is_preserved(tmp_path):
    path = tmp_path / 'settings.json'
    path.write_text('{}')
    result = run(tmp_path, path, "$value=Read-CCBSettings $target\n[IO.File]::WriteAllText($target, '{\"edited\":true}')\nWrite-CCBSettings $target $value")
    assert result.returncode != 0
    assert json.loads(path.read_bytes()) == {'edited': True}


def test_new_file_and_bom_input(tmp_path):
    path = tmp_path / 'settings.json'
    for raw in [None, b'\xef\xbb\xbf{}']:
        if raw is not None:
            path.write_bytes(raw)
        result = run(tmp_path, path, '$value=Read-CCBSettings $target\nWrite-CCBSettings $target $value')
        assert result.returncode == 0, result.stderr
        assert json.loads(path.read_bytes()) == {}
        assert not path.read_bytes().startswith(b'\xef\xbb\xbf')


def test_real_installer_permission_roundtrip(tmp_path):
    installer = (HELPER.parent / 'install.ps1').read_text(encoding='utf-8-sig')
    function = 'function Install-ClaudeConfig {' + installer.split('function Install-ClaudeConfig {', 1)[1].split('function Remove-MarkedMemoryBlock', 1)[0]
    removal = installer.split('  # 5. Remove settings.json permissions', 1)[1].split('  # 6. Remove Codex skills', 1)[0]
    profile = tmp_path / 'profile'
    target = profile / '.claude/settings.json'
    target.parent.mkdir(parents=True)
    original = dict(hooks={'command': 'D:/中文目录/工具.js'}, permissions={'allow': ['Read']})
    target.write_text(json.dumps(original, ensure_ascii=False), encoding='utf-8')
    script = tmp_path / 'installer-permissions.ps1'
    script.write_text("$ErrorActionPreference='Stop'\n. " + literal(HELPER) +
                      '\n$repoRoot = ' + literal(tmp_path / 'empty-source') +
                      '\nfunction Remove-CCBMemoryInjections {}\n' + function +
                      '\nInstall-ClaudeConfig\n$settings = Read-CCBSettings ' + literal(target) +
                      '\n' + removal, encoding='utf-8-sig')
    result = subprocess.run([PS, '-NoProfile', '-NonInteractive', '-ExecutionPolicy', 'Bypass',
                             '-File', str(script)], env=dict(os.environ, USERPROFILE=str(profile)),
                            capture_output=True, timeout=30)
    assert result.returncode == 0, result.stderr
    assert json.loads(target.read_bytes()) == original
    assert len(list(target.parent.glob('settings.json.ccb-backup-*'))) == 2
