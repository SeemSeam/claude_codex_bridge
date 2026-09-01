from __future__ import annotations

from pathlib import Path

from provider_core.caller_env import provider_user_session_env
from runtime_env.control_plane import control_plane_env
from runtime_env.git_identity import managed_git_identity_env
from runtime_env.user_session import user_session_transport_env


def _write_gitconfig(home: Path, *, name: str, email: str) -> None:
    home.mkdir(parents=True, exist_ok=True)
    (home / '.gitconfig').write_text(
        f'[user]\n\tname = {name}\n\temail = {email}\n',
        encoding='utf-8',
    )


def test_managed_git_identity_env_reads_source_home_gitconfig(tmp_path: Path) -> None:
    source_home = tmp_path / 'account-home'
    _write_gitconfig(source_home, name='Example User', email='user@example.com')

    env = managed_git_identity_env(
        environ={},
        source_home=source_home,
    )

    assert env == {
        'GIT_AUTHOR_NAME': 'Example User',
        'GIT_AUTHOR_EMAIL': 'user@example.com',
        'GIT_COMMITTER_NAME': 'Example User',
        'GIT_COMMITTER_EMAIL': 'user@example.com',
    }


def test_managed_git_identity_env_preserves_explicit_values(tmp_path: Path) -> None:
    source_home = tmp_path / 'account-home'
    _write_gitconfig(source_home, name='Example User', email='user@example.com')

    env = managed_git_identity_env(
        environ={
            'GIT_AUTHOR_NAME': 'Explicit Author',
            'GIT_AUTHOR_EMAIL': 'explicit@example.com',
        },
        source_home=source_home,
    )

    assert env == {
        'GIT_COMMITTER_NAME': 'Explicit Author',
        'GIT_COMMITTER_EMAIL': 'explicit@example.com',
    }


def test_managed_git_identity_env_returns_empty_without_identity(tmp_path: Path) -> None:
    source_home = tmp_path / 'empty-home'
    source_home.mkdir()

    assert managed_git_identity_env(environ={}, source_home=source_home) == {}


def test_provider_user_session_env_injects_git_identity_from_source_home(
    monkeypatch,
    tmp_path: Path,
) -> None:
    source_home = tmp_path / 'account-home'
    _write_gitconfig(source_home, name='Managed User', email='managed@example.com')
    monkeypatch.setenv('CCB_SOURCE_HOME', str(source_home))
    for key in (
        'GIT_AUTHOR_NAME',
        'GIT_AUTHOR_EMAIL',
        'GIT_COMMITTER_NAME',
        'GIT_COMMITTER_EMAIL',
    ):
        monkeypatch.delenv(key, raising=False)

    env = provider_user_session_env()

    assert env['GIT_AUTHOR_NAME'] == 'Managed User'
    assert env['GIT_AUTHOR_EMAIL'] == 'managed@example.com'
    assert env['GIT_COMMITTER_NAME'] == 'Managed User'
    assert env['GIT_COMMITTER_EMAIL'] == 'managed@example.com'


def test_provider_user_session_env_keeps_explicit_git_identity(
    monkeypatch,
    tmp_path: Path,
) -> None:
    source_home = tmp_path / 'account-home'
    _write_gitconfig(source_home, name='Managed User', email='managed@example.com')
    monkeypatch.setenv('CCB_SOURCE_HOME', str(source_home))
    monkeypatch.setenv('GIT_AUTHOR_NAME', 'Shell Author')
    monkeypatch.setenv('GIT_AUTHOR_EMAIL', 'shell@example.com')
    monkeypatch.setenv('GIT_COMMITTER_NAME', 'Shell Committer')
    monkeypatch.setenv('GIT_COMMITTER_EMAIL', 'shell-committer@example.com')

    env = provider_user_session_env()

    assert env['GIT_AUTHOR_NAME'] == 'Shell Author'
    assert env['GIT_AUTHOR_EMAIL'] == 'shell@example.com'
    assert env['GIT_COMMITTER_NAME'] == 'Shell Committer'
    assert env['GIT_COMMITTER_EMAIL'] == 'shell-committer@example.com'


def test_user_session_transport_env_keeps_git_identity_keys() -> None:
    env = user_session_transport_env(
        {
            'GIT_AUTHOR_NAME': 'Transport User',
            'GIT_AUTHOR_EMAIL': 'transport@example.com',
            'CODEX_HOME': '/tmp/global-codex-home',
        }
    )

    assert env == {
        'GIT_AUTHOR_NAME': 'Transport User',
        'GIT_AUTHOR_EMAIL': 'transport@example.com',
    }


def test_control_plane_env_keeps_git_identity(monkeypatch) -> None:
    monkeypatch.setenv('GIT_AUTHOR_NAME', 'Control User')
    monkeypatch.setenv('GIT_AUTHOR_EMAIL', 'control@example.com')
    monkeypatch.setenv('GIT_COMMITTER_NAME', 'Control Committer')
    monkeypatch.setenv('GIT_COMMITTER_EMAIL', 'control-committer@example.com')

    env = control_plane_env()

    assert env['GIT_AUTHOR_NAME'] == 'Control User'
    assert env['GIT_AUTHOR_EMAIL'] == 'control@example.com'
    assert env['GIT_COMMITTER_NAME'] == 'Control Committer'
    assert env['GIT_COMMITTER_EMAIL'] == 'control-committer@example.com'


def test_native_headless_env_injects_git_identity_after_home_rewrite(
    monkeypatch,
    tmp_path: Path,
) -> None:
    from ccbd.api_models import DeliveryScope, JobRecord, JobStatus, MessageEnvelope
    from provider_backends.native_cli_support.execution import (
        NativeCliExecutionConfig,
        NativeCliExecutionRequest,
        _native_cli_env,
    )

    source_home = tmp_path / 'account-home'
    _write_gitconfig(source_home, name='Headless User', email='headless@example.com')
    monkeypatch.setenv('CCB_SOURCE_HOME', str(source_home))
    for key in (
        'GIT_AUTHOR_NAME',
        'GIT_AUTHOR_EMAIL',
        'GIT_COMMITTER_NAME',
        'GIT_COMMITTER_EMAIL',
    ):
        monkeypatch.delenv(key, raising=False)

    work_dir = tmp_path / 'repo'
    work_dir.mkdir()
    managed_home = tmp_path / 'managed-home'
    request = NativeCliExecutionRequest(
        provider='qwen',
        job=JobRecord(
            job_id='job_git_identity',
            submission_id='sub_git_identity',
            agent_name='qwen1',
            provider='qwen',
            request=MessageEnvelope(
                project_id='proj',
                to_agent='qwen1',
                from_actor='main',
                body='test',
                task_id=None,
                reply_to=None,
                message_type='ask',
                delivery_scope=DeliveryScope.SINGLE,
            ),
            status=JobStatus.RUNNING,
            terminal_decision=None,
            cancel_requested_at=None,
            created_at='2026-06-13T00:00:00Z',
            updated_at='2026-06-13T00:00:00Z',
            workspace_path=str(work_dir),
        ),
        work_dir=work_dir,
        session_data={'qwen_home': str(managed_home)},
        prompt='test prompt',
        request_anchor='anchor',
    )
    config = NativeCliExecutionConfig(
        provider='qwen',
        session_filename='.qwen-session',
        command_builder=lambda _request: ['qwen'],
        env_builder=lambda _request: {'HOME': str(tmp_path / 'global-home')},
    )

    env = _native_cli_env(config, request)

    assert env['HOME'] == str(managed_home)
    assert env['GIT_AUTHOR_NAME'] == 'Headless User'
    assert env['GIT_AUTHOR_EMAIL'] == 'headless@example.com'
    assert env['GIT_COMMITTER_NAME'] == 'Headless User'
    assert env['GIT_COMMITTER_EMAIL'] == 'headless@example.com'
