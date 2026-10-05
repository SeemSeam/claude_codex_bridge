"""Portable config tests; importing local CCB must not require Linux helpers."""

from __future__ import annotations

import builtins
from types import SimpleNamespace

import pytest

from agents.config_loader import (
    ConfigValidationError,
    load_project_config,
    render_project_config_text,
)
from remote_workspace import create_workspace_synchronizer


@pytest.mark.parametrize('platform', ['linux', 'darwin', 'win32'])
def test_local_dispatch_does_not_load_linux_transport(monkeypatch, platform):
    original_import = builtins.__import__

    def guarded_import(name, *args, **kwargs):
        if name == 'fcntl' or name.startswith('remote_workspace.service') or name == 'service':
            raise AssertionError('local dispatch imported the optional Linux transport')
        return original_import(name, *args, **kwargs)

    monkeypatch.setattr('remote_workspace.sys.platform', platform)
    monkeypatch.setattr(builtins, '__import__', guarded_import)
    config = SimpleNamespace(agents={'local': SimpleNamespace(remote_workspace=None)})
    sync = create_workspace_synchronizer(None, config)
    job, context, decision = object(), object(), object()
    assert sync.before_dispatch(job, context) is job
    assert sync.bind_context(job, context) is context
    assert sync.for_resume(job, context) is job
    assert sync.before_complete(job, decision) is decision


@pytest.mark.parametrize('platform', ['darwin', 'win32'])
def test_remote_dispatch_reports_unsupported_controller(monkeypatch, platform):
    monkeypatch.setattr('remote_workspace.sys.platform', platform)
    config = SimpleNamespace(agents={'worker': SimpleNamespace(remote_workspace='worker-vps')})
    with pytest.raises(ValueError, match='Linux controller'):
        create_workspace_synchronizer(None, config)


@pytest.mark.parametrize(
    'provider,mode,template,error',
    [
        ('codex', 'pane-backed', 'remote {command}', 'pane-backed Claude'),
        ('claude', 'headless', 'remote {command}', 'pane-backed Claude'),
        ('claude', 'pane-backed', None, 'provider_command_template'),
    ],
)
def test_remote_dispatch_requires_supported_transport(monkeypatch, provider, mode, template, error):
    monkeypatch.setattr('remote_workspace.sys.platform', 'linux')
    spec = SimpleNamespace(
        remote_workspace='worker-vps',
        provider=provider,
        runtime_mode=mode,
        provider_command_template=template,
    )
    with pytest.raises(ValueError, match=error):
        create_workspace_synchronizer(None, SimpleNamespace(agents={'worker': spec}))


def _config(project, profile='worker-vps', mode='git-worktree', extra=''):
    path = project / '.ccb' / 'ccb.config'
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        'version = 2\ndefault_agents = ["worker"]\nlayout = "worker:claude"\n'
        '[agents.worker]\nprovider = "claude"\ntarget = "."\n'
        'restore = "auto"\npermission = "manual"\n'
        f'workspace_mode = "{mode}"\nremote_workspace = "{profile}"\n{extra}',
        encoding='utf-8',
    )
    return path


def test_remote_profile_survives_config_render_and_reload(tmp_path):
    project = tmp_path / 'project'
    path = _config(project)
    config = load_project_config(project).config
    assert config.agents['worker'].remote_workspace == 'worker-vps'
    rendered = render_project_config_text(config)
    assert 'remote_workspace = "worker-vps"' in rendered
    path.write_text(rendered, encoding='utf-8')
    assert load_project_config(project).config.agents['worker'].remote_workspace == 'worker-vps'


@pytest.mark.parametrize(
    'profile,mode,extra',
    [
        ('../../other', 'git-worktree', ''),
        ('worker-vps', 'inplace', ''),
        ('worker-vps', 'copy', ''),
        ('worker-vps', 'git-worktree', 'workspace_group = "shared"\n'),
    ],
)
def test_remote_profile_cannot_choose_paths_or_share_worktrees(tmp_path, profile, mode, extra):
    project = tmp_path / 'project'
    _config(project, profile, mode, extra)
    with pytest.raises(ConfigValidationError, match='remote_workspace'):
        load_project_config(project)
