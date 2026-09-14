from __future__ import annotations

from io import StringIO
from pathlib import Path
from types import SimpleNamespace

from cli.phase2 import maybe_handle_phase2


def test_phase2_clear_sends_request_and_renders_summary(monkeypatch, tmp_path: Path) -> None:
    import cli.phase2 as phase2_module

    fake_context = SimpleNamespace(project=SimpleNamespace(project_root=tmp_path, project_id='proj-clear'))
    calls: list[tuple[str, tuple[str, ...]]] = []

    monkeypatch.setattr(phase2_module, '_build_context', lambda command, cwd, out: fake_context)
    monkeypatch.setattr(phase2_module, 'ensure_bootstrap_project_config', lambda project_root: None)

    def _clear_agent_context(context, command):
        calls.append((context.project.project_id, command.agent_names))
        return {
            'status': 'ok',
            'results': [
                {'agent': 'agent1', 'status': 'cleared', 'pane_id': '%1'},
            ],
        }

    monkeypatch.setattr(phase2_module, 'clear_agent_context', _clear_agent_context)

    stdout = StringIO()
    stderr = StringIO()
    code = maybe_handle_phase2(
        ['clear', 'agent1'],
        cwd=tmp_path,
        stdout=stdout,
        stderr=stderr,
    )

    assert code == 0
    assert calls == [('proj-clear', ('agent1',))]
    assert stdout.getvalue() == (
        'clear_status: ok\n'
        'cleared_count: 1\n'
        'skipped_count: 0\n'
        'failed_count: 0\n'
        'clear_agent: agent=agent1 status=cleared pane_id=%1\n'
    )
    assert stderr.getvalue() == ''


def test_phase2_clear_returns_nonzero_when_operation_fails(monkeypatch, tmp_path: Path) -> None:
    """#346: a failed clear (dead pane) must not exit as success."""
    import cli.phase2 as phase2_module

    fake_context = SimpleNamespace(project=SimpleNamespace(project_root=tmp_path, project_id='proj-clear-fail'))

    monkeypatch.setattr(phase2_module, '_build_context', lambda command, cwd, out: fake_context)
    monkeypatch.setattr(phase2_module, 'ensure_bootstrap_project_config', lambda project_root: None)

    def _clear_agent_context(context, command):
        return {
            'status': 'failed',
            'results': [
                {
                    'agent': 'agent1',
                    'status': 'failed',
                    'reason': 'pane_dead',
                    'pane_id': '%1',
                },
            ],
        }

    monkeypatch.setattr(phase2_module, 'clear_agent_context', _clear_agent_context)

    stdout = StringIO()
    stderr = StringIO()
    code = maybe_handle_phase2(
        ['clear', 'agent1'],
        cwd=tmp_path,
        stdout=stdout,
        stderr=stderr,
    )

    assert code == 1
    assert 'clear_status: failed' in stdout.getvalue()
    assert 'reason=pane_dead' in stdout.getvalue()


def test_phase2_compact_sends_request_and_renders_summary(monkeypatch, tmp_path: Path) -> None:
    import cli.phase2 as phase2_module

    fake_context = SimpleNamespace(project=SimpleNamespace(project_root=tmp_path, project_id='proj-compact'))
    calls: list[tuple[str, tuple[str, ...]]] = []

    monkeypatch.setattr(phase2_module, '_build_context', lambda command, cwd, out: fake_context)
    monkeypatch.setattr(phase2_module, 'ensure_bootstrap_project_config', lambda project_root: None)

    def _compact_agent_context(context, command):
        calls.append((context.project.project_id, command.agent_names))
        return {
            'status': 'ok',
            'results': [
                {'agent': 'agent1', 'status': 'compacted', 'provider': 'codex', 'command': '/compact'},
            ],
        }

    monkeypatch.setattr(phase2_module, 'compact_agent_context', _compact_agent_context)

    stdout = StringIO()
    stderr = StringIO()
    code = maybe_handle_phase2(
        ['compact', 'agent1'],
        cwd=tmp_path,
        stdout=stdout,
        stderr=stderr,
    )

    assert code == 0
    assert calls == [('proj-compact', ('agent1',))]
    assert stdout.getvalue() == (
        'compact_status: ok\n'
        'compacted_count: 1\n'
        'skipped_count: 0\n'
        'blocked_count: 0\n'
        'unsupported_count: 0\n'
        'failed_count: 0\n'
        'compact_agent: agent=agent1 status=compacted provider=codex command=/compact\n'
    )
    assert stderr.getvalue() == ''


def test_phase2_compact_returns_nonzero_when_operation_is_blocked(monkeypatch, tmp_path: Path) -> None:
    import cli.phase2 as phase2_module

    fake_context = SimpleNamespace(project=SimpleNamespace(project_root=tmp_path, project_id='proj-compact'))
    monkeypatch.setattr(phase2_module, '_build_context', lambda command, cwd, out: fake_context)
    monkeypatch.setattr(phase2_module, 'ensure_bootstrap_project_config', lambda project_root: None)
    monkeypatch.setattr(
        phase2_module,
        'compact_agent_context',
        lambda context, command: {
            'status': 'blocked',
            'results': [
                {'agent': 'agent1', 'status': 'blocked', 'reason': 'agent_has_outstanding_work'},
            ],
        },
    )

    code = maybe_handle_phase2(
        ['compact', 'agent1'],
        cwd=tmp_path,
        stdout=StringIO(),
        stderr=StringIO(),
    )

    assert code == 1
