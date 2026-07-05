from __future__ import annotations

from io import StringIO
from pathlib import Path
from types import SimpleNamespace

from cli.phase2 import maybe_handle_phase2
from cli.services.ask import AskSummary


def test_phase2_ask_submit_renders_async_summary(monkeypatch, tmp_path: Path) -> None:
    import cli.phase2 as phase2_module

    fake_context = SimpleNamespace(project=SimpleNamespace(project_root=tmp_path, project_id='proj-1'))

    monkeypatch.setattr(phase2_module, '_build_context', lambda command, cwd, out: fake_context)
    monkeypatch.setattr(phase2_module, 'ensure_bootstrap_project_config', lambda project_root: None)
    monkeypatch.setattr(
        phase2_module,
        'submit_ask',
        lambda context, command: AskSummary(
            project_id='proj-1',
            submission_id=None,
            jobs=({'job_id': 'job_1', 'target_name': 'agent1', 'status': 'accepted'},),
        ),
    )
    stdout = StringIO()
    stderr = StringIO()
    code = maybe_handle_phase2(
        ['ask', 'agent1', 'hello'],
        cwd=tmp_path,
        stdout=stdout,
        stderr=stderr,
    )

    assert code == 0
    assert stdout.getvalue() == 'accepted job=job_1 target=agent1\n[CCB_ASYNC_SUBMITTED job=job_1 target=agent1]\n'
    assert stderr.getvalue() == ''


def test_phase2_ask_silence_flag_reaches_submission(monkeypatch, tmp_path: Path) -> None:
    import cli.phase2 as phase2_module

    fake_context = SimpleNamespace(project=SimpleNamespace(project_root=tmp_path, project_id='proj-1'))
    captured: dict[str, object] = {}

    def _capture_submit(context, command):
        captured['silence'] = command.silence
        return AskSummary(
            project_id='proj-1',
            submission_id=None,
            jobs=({'job_id': 'job_2', 'target_name': 'agent1', 'status': 'accepted'},),
        )

    monkeypatch.setattr(phase2_module, '_build_context', lambda command, cwd, out: fake_context)
    monkeypatch.setattr(phase2_module, 'ensure_bootstrap_project_config', lambda project_root: None)
    monkeypatch.setattr(phase2_module, 'submit_ask', _capture_submit)

    stdout = StringIO()
    stderr = StringIO()
    code = maybe_handle_phase2(
        ['ask', '--silence', 'agent1', 'hello'],
        cwd=tmp_path,
        stdout=stdout,
        stderr=stderr,
    )

    assert code == 0
    assert captured.get('silence') is True
