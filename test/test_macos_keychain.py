from __future__ import annotations

import os
import plistlib
import subprocess
from pathlib import Path

import pytest

from provider_core import macos_keychain


class _Result:
    def __init__(self, returncode: int, stdout: str = '', stderr: str = '') -> None:
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr


def test_prepare_private_keychain_uses_only_managed_home_state(
    tmp_path: Path,
    monkeypatch,
) -> None:
    home = tmp_path / 'managed-home'
    calls: list[tuple[list[str], str | None]] = []

    def fake_run(argv, **kwargs):
        call = [str(part) for part in argv]
        calls.append((call, kwargs.get('env', {}).get('HOME')))
        if call[1] == 'create-keychain':
            Path(call[-1]).write_bytes(b'private-keychain')
        if call[1] == 'default-keychain':
            return _Result(0, f'"{macos_keychain.private_keychain_path(home)}"\n')
        if call[1] == 'list-keychains':
            return _Result(0, f'    "{macos_keychain.private_keychain_path(home)}"\n')
        return _Result(0)

    monkeypatch.setattr(macos_keychain, 'is_macos', lambda: True)
    monkeypatch.setattr(macos_keychain.shutil, 'which', lambda name: '/usr/bin/security')
    monkeypatch.setattr(macos_keychain.subprocess, 'run', fake_run)

    keychain = macos_keychain.prepare_private_keychain(home)

    assert keychain == macos_keychain.private_keychain_path(home)
    assert keychain.read_bytes() == b'private-keychain'
    if os.name != 'nt':
        assert keychain.stat().st_mode & 0o777 == 0o600
    assert all(call_home == str(home.resolve()) for _call, call_home in calls)
    assert [call[1] for call, _home in calls] == [
        'create-keychain',
        'unlock-keychain',
        'set-keychain-settings',
        'default-keychain',
        'list-keychains',
    ]
    assert calls[0][0][2:4] == ['-p', '']
    assert '-l' not in calls[2][0]
    preferences = plistlib.loads(macos_keychain.keychain_preferences_path(home).read_bytes())
    assert preferences['DefaultKeychain'][0]['DbName'] == str(keychain)
    assert preferences['DLDBSearchList'][0]['DbName'] == str(keychain)


def test_prepare_private_keychain_detaches_preferences_before_security_mutation(
    tmp_path: Path,
    monkeypatch,
) -> None:
    home = tmp_path / 'managed-home'
    external_preferences = tmp_path / 'external-preferences'
    external_plist = external_preferences / 'com.apple.security.plist'
    external_preferences.mkdir()
    external_plist.write_text('external\n', encoding='utf-8')
    preferences_dir = home / 'Library' / 'Preferences'
    preferences_dir.parent.mkdir(parents=True)
    preferences_dir.symlink_to(external_preferences, target_is_directory=True)
    keychain = macos_keychain.private_keychain_path(home)

    def fake_run(argv, **_kwargs):
        assert preferences_dir.is_dir()
        assert not preferences_dir.is_symlink()
        assert external_plist.read_text(encoding='utf-8') == 'external\n'
        preferences = plistlib.loads(macos_keychain.keychain_preferences_path(home).read_bytes())
        assert preferences['DefaultKeychain'][0]['DbName'] == str(keychain)
        assert preferences['DLDBSearchList'][0]['DbName'] == str(keychain)
        if argv[1] == 'create-keychain':
            keychain.write_bytes(b'private-keychain')
        if argv[1] == 'default-keychain':
            return _Result(0, f'"{keychain}"\n')
        if argv[1] == 'list-keychains':
            return _Result(0, f'    "{keychain}"\n')
        return _Result(0)

    monkeypatch.setattr(macos_keychain, 'is_macos', lambda: True)
    monkeypatch.setattr(macos_keychain.shutil, 'which', lambda name: '/usr/bin/security')
    monkeypatch.setattr(macos_keychain.subprocess, 'run', fake_run)

    assert macos_keychain.prepare_private_keychain(home) == keychain


def test_prepare_private_keychain_rejects_symlinked_database(
    tmp_path: Path,
    monkeypatch,
) -> None:
    home = tmp_path / 'managed-home'
    external = tmp_path / 'external.keychain-db'
    external.write_bytes(b'external')
    keychain = macos_keychain.private_keychain_path(home)
    keychain.parent.mkdir(parents=True)
    keychain.symlink_to(external)
    monkeypatch.setattr(macos_keychain, 'is_macos', lambda: True)

    with pytest.raises(RuntimeError, match='must be a private regular file'):
        macos_keychain.prepare_private_keychain(home)

    assert external.read_bytes() == b'external'


def test_prepare_private_keychain_rejects_hardlinked_database(
    tmp_path: Path,
    monkeypatch,
) -> None:
    home = tmp_path / 'managed-home'
    external = tmp_path / 'external.keychain-db'
    external.write_bytes(b'external')
    keychain = macos_keychain.private_keychain_path(home)
    keychain.parent.mkdir(parents=True)
    os.link(external, keychain)
    monkeypatch.setattr(macos_keychain, 'is_macos', lambda: True)

    with pytest.raises(RuntimeError, match='must be a private regular file'):
        macos_keychain.prepare_private_keychain(home)

    assert external.read_bytes() == b'external'
    assert external.stat().st_nlink == 2


def test_prepare_private_keychain_rejects_database_owned_by_another_user(
    tmp_path: Path,
    monkeypatch,
) -> None:
    home = tmp_path / 'managed-home'
    keychain = macos_keychain.private_keychain_path(home)
    keychain.parent.mkdir(parents=True)
    keychain.write_bytes(b'private-keychain')
    monkeypatch.setattr(macos_keychain, 'is_macos', lambda: True)
    monkeypatch.setattr(
        macos_keychain.os,
        'getuid',
        lambda: keychain.stat().st_uid + 1,
        raising=False,
    )

    with pytest.raises(RuntimeError, match='must be a private regular file'):
        macos_keychain.prepare_private_keychain(home)


def test_security_failure_does_not_echo_sensitive_argv(monkeypatch) -> None:
    secret = 'do-not-echo-this-secret'

    def timeout(argv, **_kwargs):
        raise subprocess.TimeoutExpired(argv, 5)

    monkeypatch.setattr(macos_keychain.subprocess, 'run', timeout)

    with pytest.raises(RuntimeError) as exc_info:
        macos_keychain._run_security(
            '/usr/bin/security',
            ['create-keychain', '-p', secret, '/tmp/private.keychain-db'],
            error='cannot create Keychain',
            home=Path('/tmp/managed-home'),
        )

    assert secret not in str(exc_info.value)


def test_remove_keychain_preferences_detaches_symlinked_parent(tmp_path: Path) -> None:
    home = tmp_path / 'managed-home'
    external_preferences = tmp_path / 'external-preferences'
    external_plist = external_preferences / 'com.apple.security.plist'
    external_preferences.mkdir()
    external_plist.write_text('external\n', encoding='utf-8')
    preferences = home / 'Library' / 'Preferences'
    preferences.parent.mkdir(parents=True)
    preferences.symlink_to(external_preferences, target_is_directory=True)

    macos_keychain.remove_keychain_preferences(home)

    assert external_plist.read_text(encoding='utf-8') == 'external\n'
    assert preferences.is_dir()
    assert not preferences.is_symlink()
