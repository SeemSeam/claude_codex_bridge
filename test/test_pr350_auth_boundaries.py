"""Regression coverage for the PR350 integration; credentials are synthetic."""
from __future__ import annotations

import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest

from provider_backends.agy import launcher as agy
from provider_backends.claude.launcher_runtime import home as claude
from provider_core import macos_keychain as kc


@pytest.fixture(autouse=True)
def _no_native_agy_keychain(monkeypatch):
    monkeypatch.setattr(agy, 'is_macos', lambda: False)


def _inherit_files(source, managed):
    agy._materialize_private_credentials(
        source, managed, profile=SimpleNamespace(inherit_auth=True, inherit_config=False),
    )


def test_claude_credential_lifecycle_pins_private_database(tmp_path, monkeypatch):
    layout = claude.claude_layout_for_home(tmp_path / 'managed')
    database = kc.private_keychain_path(layout.home_root)
    calls = []
    monkeypatch.setattr(claude.platform, 'system', lambda: 'Darwin')
    monkeypatch.setattr(claude, '_macos_keychain_account', lambda: 'synthetic-user')
    monkeypatch.setattr(claude.shutil, 'which', lambda _: '/usr/bin/security')

    def run(argv, **kwargs):
        calls.append(argv)
        assert argv[-1] == str(database)
        assert kwargs['env']['HOME'] == str(layout.home_root)
        return subprocess.CompletedProcess(argv, 44 if argv[1] == 'find-generic-password' else 0, '', '')

    monkeypatch.setattr(claude.subprocess, 'run', run)
    claude._sync_managed_macos_keychain_auth(layout, {'synthetic': 'token'})
    claude._remove_managed_macos_keychain_auth(layout)
    assert [argv[1] for argv in calls] == [
        'find-generic-password', 'add-generic-password', 'delete-generic-password',
    ]


def test_claude_write_timeout_does_not_disclose_credential(tmp_path, monkeypatch):
    secret = 'synthetic-secret-not-for-errors'
    monkeypatch.setattr(claude.platform, 'system', lambda: 'Darwin')
    monkeypatch.setattr(claude, '_macos_keychain_account', lambda: 'synthetic-user')
    monkeypatch.setattr(claude.shutil, 'which', lambda _: '/usr/bin/security')

    def run(argv, **kwargs):
        if argv[1] == 'find-generic-password':
            return subprocess.CompletedProcess(argv, 44, '', '')
        raise subprocess.TimeoutExpired(argv, 5)

    monkeypatch.setattr(claude.subprocess, 'run', run)
    with pytest.raises(RuntimeError) as error:
        claude._sync_managed_macos_keychain_auth(
            claude.claude_layout_for_home(tmp_path / 'managed'), {'token': secret},
        )
    assert secret not in str(error.value)
    assert error.value.__suppress_context__


def test_agy_source_logout_removes_only_recorded_projections(tmp_path):
    source, managed = tmp_path / 'source', tmp_path / 'managed'
    relative = agy._AGY_AUTH_FILES[0]
    external = source / relative
    external.parent.mkdir(parents=True)
    external.write_text('source-token')
    _inherit_files(source, managed)
    private = managed / agy._AGY_AUTH_FILES[1]
    private.write_text('agent-private-token')
    external.unlink()
    _inherit_files(source, managed)
    assert not (managed / relative).exists()
    assert private.read_text() == 'agent-private-token'


def test_agy_source_error_preserves_prior_projection(tmp_path, monkeypatch):
    source, managed = tmp_path / 'source', tmp_path / 'managed'
    external = source / agy._AGY_AUTH_FILES[0]
    external.parent.mkdir(parents=True)
    external.write_text('original')
    _inherit_files(source, managed)
    external.write_text('new')
    failing = source / agy._AGY_AUTH_FILES[1]
    failing.write_text('unreadable')
    read = Path.read_text
    copy = agy.shutil.copy2

    def read_text(path, *args, **kwargs):
        if path == failing:
            raise PermissionError('denied')
        return read(path, *args, **kwargs)

    def copy_file(path, *args, **kwargs):
        if Path(path) == failing:
            raise PermissionError('denied')
        return copy(path, *args, **kwargs)

    monkeypatch.setattr(Path, 'read_text', read_text)
    monkeypatch.setattr(agy.shutil, 'copy2', copy_file)
    with pytest.raises((OSError, RuntimeError)):
        _inherit_files(source, managed)
    assert (managed / agy._AGY_AUTH_FILES[0]).read_text() == 'original'


@pytest.mark.parametrize('kind', ['symlink', 'hardlink'])
def test_agy_refresh_detaches_destination_alias_without_external_writes(tmp_path, kind):
    source, managed = tmp_path / 'source', tmp_path / 'managed'
    relative = agy._AGY_AUTH_FILES[0]
    external, target = source / relative, managed / relative
    external.parent.mkdir(parents=True)
    target.parent.mkdir(parents=True)
    external.write_text('source-token')
    before = external.stat()
    if kind == 'symlink':
        target.symlink_to(external)
    else:
        target.hardlink_to(external)
    _inherit_files(source, managed)
    target.write_text('managed-refresh')
    assert external.read_text() == 'source-token'
    assert external.stat().st_mtime_ns == before.st_mtime_ns
    assert external.stat().st_mode == before.st_mode


@pytest.mark.parametrize('body', ['broken', '{"schema_version":1,"files":["../../external"]}'])
def test_agy_invalid_provenance_blocks_cleanup(tmp_path, body):
    source, managed = tmp_path / 'source', tmp_path / 'managed'
    path = managed / agy._AGY_AUTH_PROJECTION_REL
    path.parent.mkdir(parents=True)
    path.write_text(body)
    existing = managed / agy._AGY_AUTH_FILES[0]
    existing.write_text('keep')
    with pytest.raises(RuntimeError):
        _inherit_files(source, managed)
    assert existing.read_text() == 'keep'


def test_agy_independent_to_inherited_transition_preserves_private_login(tmp_path):
    source, managed = tmp_path / 'source', tmp_path / 'managed'
    profile = SimpleNamespace(inherit_auth=False, inherit_config=False)
    agy._materialize_private_credentials(source, managed, profile=profile)
    target = managed / agy._AGY_AUTH_FILES[0]
    target.write_text('private-login')
    profile.inherit_auth = True
    with pytest.raises(RuntimeError, match='Agent-private'):
        agy._materialize_private_credentials(source, managed, profile=profile)
    assert target.read_text() == 'private-login'


def test_agy_missing_mode_record_cannot_adopt_inherited_keychain(tmp_path, monkeypatch):
    monkeypatch.setattr(agy, '_managed_macos_keychain_auth_exists', lambda _: True)
    with pytest.raises(RuntimeError, match='remove or move'):
        agy._materialize_private_credentials(
            tmp_path / 'source', tmp_path / 'managed',
            profile=SimpleNamespace(inherit_auth=False, inherit_config=False),
        )


def test_agy_source_keychain_error_blocks_launch_without_file_fallback(tmp_path, monkeypatch):
    from cli.models import ParsedStartCommand

    managed, source = tmp_path / 'managed', tmp_path / 'source'
    source.mkdir()
    monkeypatch.setattr(agy, '_resolve_managed_home', lambda _: managed)
    monkeypatch.setattr(agy, '_resolve_credential_source_home', lambda: source)
    monkeypatch.setattr(agy, 'load_resolved_provider_profile', lambda _: None)
    monkeypatch.setattr(agy, '_prepare_macos_agent_private_keychain', lambda _: kc.private_keychain_path(managed))

    def read(*args):
        raise RuntimeError('source locked')

    monkeypatch.setattr(agy, '_project_macos_inherited_keychain_auth', read)
    with pytest.raises(RuntimeError, match='source locked'):
        agy.build_start_cmd(
            ParsedStartCommand(project=None, agent_names=('a',), restore=False, auto_permission=False),
            SimpleNamespace(name='a', provider='agy', startup_args=(), provider_command_template=None, env={}),
            tmp_path / 'runtime', 'launch',
        )
    assert not (managed / agy._AGY_KEYRING_BYPASS_MARKER_REL).exists()


@pytest.mark.parametrize('owned', [True, False])
def test_agy_keychain_absence_cleans_only_owned_item(tmp_path, monkeypatch, owned):
    source, managed = tmp_path / 'source', tmp_path / 'managed'
    database = kc.private_keychain_path(managed)
    if owned:
        agy._write_auth_projection(managed, {'keychain_projected': True})
    calls = []

    def run(argv, **kwargs):
        calls.append(argv)
        if argv[1] == 'delete-generic-password':
            assert argv[-1] == str(database)
            assert kwargs['env']['HOME'] == str(managed)
        return subprocess.CompletedProcess(argv, 44, '', '')

    monkeypatch.setattr(agy.subprocess, 'run', run)
    monkeypatch.setattr(agy.shutil, 'which', lambda _: '/usr/bin/security')
    assert not agy._project_macos_inherited_keychain_auth(source, managed, database)
    assert len(calls) == (2 if owned else 1)


@pytest.mark.parametrize('code', [1, 36, 128])
def test_agy_keychain_read_error_preserves_projection(tmp_path, monkeypatch, code):
    source, managed = tmp_path / 'source', tmp_path / 'managed'
    agy._write_auth_projection(managed, {'keychain_projected': True})
    path = managed / agy._AGY_AUTH_PROJECTION_REL
    before = path.read_bytes()
    calls = []

    def run(argv, **kwargs):
        calls.append(argv)
        return subprocess.CompletedProcess(argv, code, '', '')

    monkeypatch.setattr(agy.subprocess, 'run', run)
    monkeypatch.setattr(agy.shutil, 'which', lambda _: '/usr/bin/security')
    with pytest.raises(RuntimeError):
        agy._project_macos_inherited_keychain_auth(source, managed, kc.private_keychain_path(managed))
    assert path.read_bytes() == before
    assert len(calls) == 1


def test_existing_locked_keychain_is_unlocked_without_recreation(tmp_path, monkeypatch):
    home = tmp_path / 'managed'
    database = kc.private_keychain_path(home)
    database.parent.mkdir(parents=True)
    database.write_bytes(b'existing-items')
    locked = True

    def run(argv, **kwargs):
        nonlocal locked
        assert argv[1] != 'create-keychain'
        if argv[1] == 'unlock-keychain':
            assert argv[2:] == ['-p', '', str(database)]
            locked = False
        else:
            assert not locked
        if argv[1] == 'set-keychain-settings':
            assert argv[2:] == [str(database)]
        return subprocess.CompletedProcess(argv, 0, f'"{database}"\n', '')

    monkeypatch.setattr(kc, 'is_macos', lambda: True)
    monkeypatch.setattr(kc.subprocess, 'run', run)
    monkeypatch.setattr(kc.shutil, 'which', lambda _: '/usr/bin/security')
    kc.prepare_private_keychain(home)
    assert not locked
    assert database.read_bytes() == b'existing-items'


@pytest.mark.parametrize('command', ['default-keychain', 'list-keychains'])
def test_unexpected_external_search_domain_blocks_preparation(tmp_path, monkeypatch, command):
    home = tmp_path / 'managed'
    database = kc.private_keychain_path(home)

    def run(argv, **kwargs):
        if argv[1] == 'create-keychain':
            database.write_bytes(b'private')
        resolved = tmp_path / 'external.keychain-db' if argv[1] == command else database
        return subprocess.CompletedProcess(argv, 0, f'"{resolved}"\n', '')

    monkeypatch.setattr(kc, 'is_macos', lambda: True)
    monkeypatch.setattr(kc.subprocess, 'run', run)
    monkeypatch.setattr(kc.shutil, 'which', lambda _: '/usr/bin/security')
    with pytest.raises(RuntimeError, match='unexpected'):
        kc.prepare_private_keychain(home)
