from __future__ import annotations

from pathlib import Path
import shutil
import subprocess
import uuid

from agents.config_loader import load_project_config
from cli.context import CliContext
from cli.models import ParsedStartCommand
import cli.services.tmux_start_layout as tmux_start_layout
from project.resolver import bootstrap_project
from storage.paths import PathLayout
from terminal_runtime import TmuxBackend
from terminal_runtime.placeholders import pane_placeholder_cmd
from terminal_runtime.tmux_identity import pane_visual
import pytest


def _context(project_root: Path) -> CliContext:
    project_root.mkdir(parents=True, exist_ok=True)
    (project_root / '.ccb').mkdir(parents=True, exist_ok=True)
    project = bootstrap_project(project_root)
    command = ParsedStartCommand(project=None, agent_names=(), restore=False, auto_permission=False)
    return CliContext(command=command, cwd=project_root, project=project, paths=PathLayout(project_root))


def test_prepare_tmux_start_layout_uses_current_pane_as_cmd_anchor(monkeypatch, tmp_path: Path) -> None:
    project_root = tmp_path / 'repo-layout'
    project_root.mkdir(parents=True, exist_ok=True)
    (project_root / '.ccb').mkdir(parents=True, exist_ok=True)
    (project_root / '.ccb' / 'ccb.config').write_text('cmd, agent1:codex; agent2:codex, agent3:claude\n', encoding='utf-8')
    ctx = _context(project_root)
    config = load_project_config(project_root).config
    calls: list[tuple[str, str, str]] = []

    class FakeTmuxBackend:
        def get_current_pane_id(self) -> str:
            return '%0'

        def set_pane_title(self, pane_id: str, title: str) -> None:
            calls.append(('title', pane_id, title))

        def set_pane_user_option(self, pane_id: str, name: str, value: str) -> None:
            calls.append((name, pane_id, value))

        def split_pane(
            self,
            parent_pane_id: str,
            direction: str,
            percent: int,
            cmd: str | None = None,
            cwd: str | None = None,
        ) -> str:
            mapping = {
                ('right', '%0'): '%1',
                ('bottom', '%0'): '%2',
                ('bottom', '%1'): '%3',
            }
            return mapping[(direction, str(parent_pane_id))]

    monkeypatch.setattr(tmux_start_layout, 'make_terminal_backend', FakeTmuxBackend)

    layout = tmux_start_layout.prepare_tmux_start_layout(
        ctx,
        config=config,
        targets=('agent1', 'agent2', 'agent3'),
    )

    assert layout.cmd_pane_id == '%0'
    assert layout.agent_panes == {'agent1': '%2', 'agent2': '%1', 'agent3': '%3'}
    assert ('title', '%0', 'cmd') in calls
    assert ('title', '%2', 'agent1') in calls
    assert ('title', '%1', 'agent2') in calls
    assert ('title', '%3', 'agent3') in calls


def test_prepare_tmux_start_layout_uses_explicit_percent_hint(monkeypatch, tmp_path: Path) -> None:
    project_root = tmp_path / 'repo-layout-percent'
    project_root.mkdir(parents=True, exist_ok=True)
    (project_root / '.ccb').mkdir(parents=True, exist_ok=True)
    (project_root / '.ccb' / 'ccb.config').write_text('cmd; agent1:codex@35\n', encoding='utf-8')
    ctx = _context(project_root)
    config = load_project_config(project_root).config
    split_calls: list[tuple[str, str, int]] = []

    class FakeTmuxBackend:
        def get_current_pane_id(self) -> str:
            return '%0'

        def set_pane_title(self, pane_id: str, title: str) -> None:
            return None

        def set_pane_user_option(self, pane_id: str, name: str, value: str) -> None:
            return None

        def split_pane(
            self,
            parent_pane_id: str,
            direction: str,
            percent: int,
            cmd: str | None = None,
            cwd: str | None = None,
        ) -> str:
            split_calls.append((parent_pane_id, direction, percent))
            return '%1'

    monkeypatch.setattr(tmux_start_layout, 'make_terminal_backend', FakeTmuxBackend)

    layout = tmux_start_layout.prepare_tmux_start_layout(
        ctx,
        config=config,
        targets=('agent1',),
    )

    assert layout.agent_panes == {'agent1': '%1'}
    assert split_calls == [('%0', 'right', 35)]


def test_prepare_tmux_start_layout_assigns_slot_stable_styles(monkeypatch, tmp_path: Path) -> None:
    project_root = tmp_path / 'repo-layout-styles'
    project_root.mkdir(parents=True, exist_ok=True)
    (project_root / '.ccb').mkdir(parents=True, exist_ok=True)
    (project_root / '.ccb' / 'ccb.config').write_text('cmd, agent1:codex; agent2:codex, agent3:claude\n', encoding='utf-8')
    ctx = _context(project_root)
    config = load_project_config(project_root).config
    options: dict[tuple[str, str], str] = {}
    styles: dict[str, tuple[str | None, str | None]] = {}

    class FakeTmuxBackend:
        def get_current_pane_id(self) -> str:
            return '%0'

        def set_pane_title(self, pane_id: str, title: str) -> None:
            return None

        def set_pane_user_option(self, pane_id: str, name: str, value: str) -> None:
            options[(pane_id, name)] = value

        def set_pane_style(
            self,
            pane_id: str,
            *,
            border_style: str | None = None,
            active_border_style: str | None = None,
        ) -> None:
            styles[pane_id] = (border_style, active_border_style)

        def split_pane(
            self,
            parent_pane_id: str,
            direction: str,
            percent: int,
            cmd: str | None = None,
            cwd: str | None = None,
        ) -> str:
            mapping = {
                ('right', '%0'): '%1',
                ('bottom', '%0'): '%2',
                ('bottom', '%1'): '%3',
            }
            return mapping[(direction, str(parent_pane_id))]

    monkeypatch.setattr(tmux_start_layout, 'make_terminal_backend', FakeTmuxBackend)

    layout = tmux_start_layout.prepare_tmux_start_layout(
        ctx,
        config=config,
        targets=('agent1', 'agent2', 'agent3'),
    )

    assert layout.agent_panes == {'agent1': '%2', 'agent2': '%1', 'agent3': '%3'}
    cmd_visual = pane_visual(project_id=ctx.project.project_id, slot_key='cmd', is_cmd=True)
    agent1_visual = pane_visual(project_id=ctx.project.project_id, slot_key='agent1', order_index=0)
    agent2_visual = pane_visual(project_id=ctx.project.project_id, slot_key='agent2', order_index=1)
    agent3_visual = pane_visual(project_id=ctx.project.project_id, slot_key='agent3', order_index=2)
    assert options[('%0', '@ccb_label_style')] == cmd_visual.label_style
    assert options[('%2', '@ccb_label_style')] == agent1_visual.label_style
    assert options[('%1', '@ccb_label_style')] == agent2_visual.label_style
    assert options[('%3', '@ccb_label_style')] == agent3_visual.label_style
    assert styles['%2'] == (agent1_visual.border_style, agent1_visual.active_border_style)
    assert styles['%1'] == (agent2_visual.border_style, agent2_visual.active_border_style)
    assert styles['%3'] == (agent3_visual.border_style, agent3_visual.active_border_style)


def test_prepare_tmux_start_layout_uses_root_pane_for_first_agent_when_cmd_disabled(monkeypatch, tmp_path: Path) -> None:
    project_root = tmp_path / 'repo-layout-no-cmd'
    project_root.mkdir(parents=True, exist_ok=True)
    (project_root / '.ccb').mkdir(parents=True, exist_ok=True)
    (project_root / '.ccb' / 'ccb.config').write_text('demo:codex\n', encoding='utf-8')
    ctx = _context(project_root)
    config = load_project_config(project_root).config
    calls: list[tuple[str, str, str]] = []

    class FakeTmuxBackend:
        def get_current_pane_id(self) -> str:
            return '%0'

        def set_pane_title(self, pane_id: str, title: str) -> None:
            calls.append(('title', pane_id, title))

        def set_pane_user_option(self, pane_id: str, name: str, value: str) -> None:
            calls.append((name, pane_id, value))

        def split_pane(
            self,
            parent_pane_id: str,
            direction: str,
            percent: int,
            cmd: str | None = None,
            cwd: str | None = None,
        ) -> str:
            raise AssertionError('single-agent no-cmd layout should reuse root pane')

    monkeypatch.setattr(tmux_start_layout, 'make_terminal_backend', FakeTmuxBackend)

    layout = tmux_start_layout.prepare_tmux_start_layout(
        ctx,
        config=config,
        targets=('demo',),
    )

    assert layout.cmd_pane_id is None
    assert layout.agent_panes == {'demo': '%0'}
    assert ('title', '%0', 'demo') in calls


def test_prepare_tmux_start_layout_creates_split_panes_with_placeholder(monkeypatch, tmp_path: Path) -> None:
    project_root = tmp_path / 'repo-layout-placeholder'
    ctx = _context(project_root)
    (project_root / '.ccb' / 'ccb.config').write_text(
        'agent1:codex; agent2:codex, agent3:claude\n',
        encoding='utf-8',
    )
    config = load_project_config(project_root).config
    created: list[tuple[str, str | None]] = []
    live_panes = {'%0'}

    class FakeTmuxBackend:
        def get_current_pane_id(self) -> str:
            return '%0'

        def set_pane_title(self, pane_id: str, title: str) -> None:
            return None

        def set_pane_user_option(self, pane_id: str, name: str, value: str) -> None:
            return None

        def set_pane_style(
            self,
            pane_id: str,
            *,
            border_style: str | None = None,
            active_border_style: str | None = None,
        ) -> None:
            return None

        def split_pane(
            self,
            parent_pane_id: str,
            *,
            direction: str = 'right',
            percent: int = 50,
            cmd: str | None = None,
            cwd: str | None = None,
        ) -> str:
            if parent_pane_id not in live_panes:
                raise RuntimeError(f'Cannot split: pane {parent_pane_id} does not exist')
            if not cmd:
                live_panes.discard(parent_pane_id)
            pane_id = f'%{len(created) + 1}'
            created.append((cmd or '', parent_pane_id))
            live_panes.add(pane_id)
            return pane_id

    monkeypatch.setattr(tmux_start_layout, 'make_terminal_backend', FakeTmuxBackend)

    layout = tmux_start_layout.prepare_tmux_start_layout(
        ctx,
        config=config,
        targets=('agent1', 'agent2', 'agent3'),
    )

    assert layout.agent_panes == {'agent1': '%0', 'agent2': '%1', 'agent3': '%2'}
    assert created
    assert all(cmd == pane_placeholder_cmd() for cmd, _parent in created)


def test_prepare_tmux_start_layout_survives_exiting_default_command(tmp_path: Path) -> None:
    if shutil.which('tmux') is None:
        pytest.skip('tmux is not installed')

    project_root = tmp_path / 'repo-layout-stress'
    ctx = _context(project_root)
    (project_root / '.ccb' / 'ccb.config').write_text(
        'agent1:codex; agent2:codex, agent3:claude\n',
        encoding='utf-8',
    )
    config = load_project_config(project_root).config
    socket_path = Path('/tmp') / f'ccb-{uuid.uuid4().hex[:12]}.sock'
    backend = TmuxBackend(socket_path=str(socket_path))

    try:
        for _ in range(3):
            session_name = f'ccb-layout-{uuid.uuid4().hex[:8]}'
            backend._tmux_run(
                ['new-session', '-d', '-s', session_name, '-c', str(project_root), 'sh', '-lc', pane_placeholder_cmd()],
                check=True,
            )
            backend._tmux_run(['set-option', '-t', session_name, 'default-command', 'false'], check=True)
            root = (
                backend._tmux_run(['list-panes', '-t', session_name, '-F', '#{pane_id}'], capture=True, check=True).stdout
                or ''
            ).splitlines()[0].strip()

            layout = tmux_start_layout.prepare_tmux_start_layout(
                ctx,
                config=config,
                targets=('agent1', 'agent2', 'agent3'),
                tmux_backend=backend,
                root_pane_id=root,
            )
            assert set(layout.agent_panes) == {'agent1', 'agent2', 'agent3'}
            for pane_id in layout.agent_panes.values():
                assert backend.pane_exists(pane_id)
            backend._tmux_run(['kill-session', '-t', session_name], check=False)
    finally:
        subprocess.run(['tmux', '-S', str(socket_path), 'kill-server'], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        socket_path.unlink(missing_ok=True)
