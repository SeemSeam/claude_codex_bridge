from __future__ import annotations

import sys
from types import SimpleNamespace

import cli.kill_runtime.processes as processes
import cli.kill_runtime.zombies as zombies


def _patch_process_os_name(monkeypatch, name: str) -> None:
    os_proxy = SimpleNamespace(**vars(processes.os))
    os_proxy.name = name
    monkeypatch.setattr(processes, 'os', os_proxy)


def test_is_pid_alive_treats_procfs_zombie_as_dead(monkeypatch) -> None:
    _patch_process_os_name(monkeypatch, 'posix')
    monkeypatch.setattr(processes.os, 'kill', lambda pid, sig: None)
    monkeypatch.setattr(processes, '_proc_pid_state', lambda pid: 'Z')

    assert processes.is_pid_alive(123) is False


def test_is_pid_alive_keeps_uninterruptible_process_alive(monkeypatch) -> None:
    _patch_process_os_name(monkeypatch, 'posix')
    monkeypatch.setattr(processes.os, 'kill', lambda pid, sig: None)
    monkeypatch.setattr(processes, '_proc_pid_state', lambda pid: 'D')

    assert processes.is_pid_alive(123) is True


def test_is_pid_alive_uses_windows_exit_code(monkeypatch) -> None:
    class _FakeDWORD:
        def __init__(self) -> None:
            self.value = 0

    class _FakeKernel32:
        def OpenProcess(self, _access, _inherit, pid):
            return 99 if pid == 123 else 0

        def GetExitCodeProcess(self, _handle, code_ref):
            code_ref._obj.value = 259
            return True

        def CloseHandle(self, _handle):
            return True

    fake_wintypes = SimpleNamespace(DWORD=_FakeDWORD)
    fake_ctypes = SimpleNamespace(
        WinDLL=lambda _name, use_last_error=True: _FakeKernel32(),
        byref=lambda obj: SimpleNamespace(_obj=obj),
        wintypes=fake_wintypes,
    )

    _patch_process_os_name(monkeypatch, 'nt')
    monkeypatch.setitem(sys.modules, 'ctypes', fake_ctypes)
    monkeypatch.setitem(sys.modules, 'ctypes.wintypes', fake_wintypes)

    assert processes.is_pid_alive(123) is True
    assert processes.is_pid_alive(456) is False


def test_find_all_zombie_sessions_filters_dead_parents() -> None:
    result = zombies.find_all_zombie_sessions(
        is_pid_alive=lambda pid: pid == 456,
        list_tmux_sessions_fn=lambda: [
            'codex-123-worker',
            'claude-456-run',
            'agy-789-debugger',
            'demo-other',
        ],
    )

    assert result == [
        {
            'session': 'codex-123-worker',
            'provider': 'codex',
            'parent_pid': 123,
        },
        {
            'session': 'agy-789-debugger',
            'provider': 'agy',
            'parent_pid': 789,
        },
    ]


def test_kill_global_zombies_reports_partial_failures(capsys) -> None:
    code = zombies.kill_global_zombies(
        yes=True,
        is_pid_alive=lambda pid: False,
        find_all_zombie_sessions_fn=lambda **kwargs: [
            {'session': 'codex-123-worker', 'provider': 'codex', 'parent_pid': 123},
            {'session': 'claude-234-run', 'provider': 'claude', 'parent_pid': 234},
        ],
        kill_tmux_session_fn=lambda name: name == 'codex-123-worker',
    )

    assert code == 0
    out = capsys.readouterr().out
    assert 'Found 2 zombie session(s):' in out
    assert 'Cleaned up 1 zombie session(s), 1 failed' in out
