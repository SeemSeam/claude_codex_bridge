from __future__ import annotations

import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest

from ccbd.services.project_namespace_runtime.backend import (
    apply_pane_identity,
    kill_server,
    kill_window,
    create_window,
    create_session,
    ensure_server_policy,
    ensure_window,
    find_window,
    list_windows,
    namespace_state_fields,
    prepare_server,
    remember_namespace_state_ref,
    respawn_pane,
    session_alive,
    split_pane,
    wait_for_root_pane,
    window_root_pane,
)
from terminal_runtime.mux_backend_contract import (
    MuxCommandErrorV2,
    make_capabilities,
    make_namespace_ref,
    make_pane_ref,
)
from terminal_runtime.tmux_readiness import TmuxTransientServerUnavailable

_TMUX_UPDATE_ENVIRONMENT_FOR_TEST = (
    'TERM TERM_PROGRAM TERM_PROGRAM_VERSION PATH SHELL BROWSER DBUS_SESSION_BUS_ADDRESS '
    'DESKTOP_SESSION DISPLAY WAYLAND_DISPLAY XAUTHORITY XDG_CURRENT_DESKTOP XDG_RUNTIME_DIR '
    'XDG_SESSION_DESKTOP XDG_SESSION_TYPE WSL_DISTRO_NAME WSL_INTEROP WSLENV WT_PROFILE_ID '
    'WT_SESSION SSH_AUTH_SOCK SSH_CONNECTION KITTY_WINDOW_ID '
    'WEZTERM_EXECUTABLE WEZTERM_PANE WEZTERM_UNIX_SOCKET CCB_WORKBENCH_PROFILE '
    'CCB_WORKBENCH_FORCE_RICH CCB_WORKBENCH_ROOT CCB_WORKBENCH_TERMINAL_PROGRAM '
    'CCB_WORKBENCH_TERMINAL_PROGRAM_VERSION CCB_WORKBENCH_YAZI_SAFE_CONFIG '
    'CCB_WORKBENCH_YAZI_RICH_CONFIG AGENT_ROLES_STORE'
)


def _clipboard_pipe_command_for_test() -> str:
    return (
        "sh -lc '"
        "tmp=$(mktemp \"${TMPDIR:-/tmp}/ccb-clipboard.XXXXXX\") || exit 0; "
        "cat >\"$tmp\"; "
        "if command -v wl-copy >/dev/null 2>&1 && [ -n \"${WAYLAND_DISPLAY:-}\" ]; then (wl-copy <\"$tmp\"; rm -f \"$tmp\") >/dev/null 2>&1 & "
        "elif command -v xclip >/dev/null 2>&1 && [ -n \"${DISPLAY:-}\" ]; then (xclip -selection clipboard <\"$tmp\"; rm -f \"$tmp\") >/dev/null 2>&1 & "
        "elif command -v xsel >/dev/null 2>&1 && [ -n \"${DISPLAY:-}\" ]; then (xsel --clipboard --input <\"$tmp\"; rm -f \"$tmp\") >/dev/null 2>&1 & "
        "elif command -v pbcopy >/dev/null 2>&1; then pbcopy <\"$tmp\"; rm -f \"$tmp\"; "
        "elif command -v powershell.exe >/dev/null 2>&1; then powershell.exe -NoProfile -Command \"[Console]::InputEncoding=[System.Text.UTF8Encoding]::new(); Set-Clipboard -Value ([Console]::In.ReadToEnd())\" <\"$tmp\"; rm -f \"$tmp\"; "
        "elif command -v pwsh >/dev/null 2>&1; then pwsh -NoLogo -NoProfile -Command \"[Console]::InputEncoding=[System.Text.UTF8Encoding]::new(); Set-Clipboard -Value ([Console]::In.ReadToEnd())\" <\"$tmp\"; rm -f \"$tmp\"; "
        "else rm -f \"$tmp\"; fi'"
    )


class _FlakyBackend:
    def __init__(self) -> None:
        self.calls: list[tuple[str, ...]] = []
        self._remaining_failures: dict[tuple[str, ...], int] = {}
        self.session_created = False
        self.require_session_for_server_policy = False
        self.missing_session_stderr: str | None = None

    def fail_once(self, *args: str) -> None:
        self._remaining_failures[tuple(args)] = 1

    def _tmux_run(self, args, *, check=False, capture=False, timeout=None):
        del check, capture, timeout
        key = tuple(str(item) for item in args)
        self.calls.append(key)
        if key[:1] == ('new-session',):
            self.session_created = True
        remaining = int(self._remaining_failures.get(key, 0))
        if remaining > 0:
            self._remaining_failures[key] = remaining - 1
            return subprocess.CompletedProcess(
                ['tmux', *key],
                1,
                stdout='',
                stderr='no server running on /tmp/ccb-runtime/test.sock\n',
            )
        if key[:2] == ('set-option', '-g') and self.require_session_for_server_policy and not self.session_created:
            return subprocess.CompletedProcess(
                ['tmux', *key],
                1,
                stdout='',
                stderr='no server running on /tmp/ccb-runtime/test.sock\n',
            )
        if key[:1] == ('list-windows',):
            return subprocess.CompletedProcess(
                ['tmux', *key],
                0,
                stdout='@1\tcmd\t1\n@2\tworkspace\t0\n',
                stderr='',
            )
        if key[:2] == ('has-session', '-t'):
            missing_stderr = self.missing_session_stderr or f"can't find session: {key[2]}\n"
            return subprocess.CompletedProcess(
                ['tmux', *key],
                0 if self.session_created else 1,
                stdout='',
                stderr='' if self.session_created else missing_stderr,
            )
        if key[:2] == ('list-panes', '-t'):
            return subprocess.CompletedProcess(
                ['tmux', *key],
                0,
                stdout='%7\n',
                stderr='',
            )
        return subprocess.CompletedProcess(['tmux', *key], 0, stdout='', stderr='')


class _FakeHerdrNamespaceBackend:
    backend_impl = 'herdr'

    def __init__(self, *, pane_spawn_status: str = 'supported') -> None:
        self.calls: list[tuple[str, object]] = []
        self.namespace: dict[str, object] | None = None
        self.windows: dict[str, dict[str, object]] = {}
        self.panes: dict[str, dict[str, object]] = {}
        self.pane_spawn_status = pane_spawn_status

    def capabilities(self) -> dict[str, object]:
        status = {
            'session_attach': 'supported',
            'pane_spawn': self.pane_spawn_status,
            'send_input': 'supported',
            'read_output': 'supported',
            'kill_pane': 'supported',
            'workspace_create': 'supported',
            'workspace_list': 'supported',
            'workspace_focus': self.pane_spawn_status,
            'workspace_close': 'supported',
            'workspace_metadata': 'supported',
            'pane_metadata': 'supported',
            'pane_list': 'supported',
            'pane_split': 'supported',
            'pane_run': 'supported',
        }
        return make_capabilities(
            backend_impl='herdr',
            command_status=status,  # type: ignore[arg-type]
            semantic_status=status,  # type: ignore[arg-type]
        )

    def prepare_server(self) -> None:
        self.calls.append(('prepare_server', None))

    def ensure_server_policy(self) -> None:
        self.calls.append(('ensure_server_policy', None))

    def create_session(self, *, project_id: str, cwd: str, title: str) -> dict[str, object]:
        self.calls.append(('create_session', {'project_id': project_id, 'cwd': cwd, 'title': title}))
        self.namespace = make_namespace_ref(
            backend_impl='herdr',
            namespace_id='workspace-1',
            session_name=title,
            ipc_kind='herdr_socket',
            ipc_ref='herdr://workspace-1',
            restore_token='restore-1',
        )
        self.windows[title] = {'window_id': 'window-control', 'window_name': title, 'active': False}
        return self.namespace

    def namespace_ref(self, session_name: str, namespace_id: str) -> dict[str, object]:
        self.calls.append(('namespace_ref', {'session_name': session_name, 'namespace_id': namespace_id}))
        return make_namespace_ref(
            backend_impl='herdr',
            namespace_id=namespace_id,
            session_name=session_name,
            ipc_kind='herdr_socket',
            ipc_ref=f'herdr://{namespace_id}',
            restore_token=f'restore-{session_name}',
        )

    def list_windows(self, namespace: dict[str, object]) -> list[dict[str, object]]:
        self.calls.append(('list_windows', namespace))
        return list(self.windows.values())

    def ensure_window(
        self,
        namespace: dict[str, object],
        *,
        window_name: str,
        cwd: str,
        select: bool,
    ) -> dict[str, object]:
        self.calls.append(('ensure_window', {'namespace': namespace, 'window_name': window_name, 'cwd': cwd, 'select': select}))
        record = self.windows.setdefault(
            window_name,
            {'window_id': f'window-{len(self.windows) + 1}', 'window_name': window_name, 'active': False},
        )
        record['active'] = bool(select)
        return record

    def window_root_pane(self, namespace: dict[str, object], *, window_name: str) -> dict[str, object]:
        self.calls.append(('window_root_pane', {'namespace': namespace, 'window_name': window_name}))
        pane = make_pane_ref(
            backend_impl='herdr',
            pane_id='herdr-pane-root',
            session_name=str(namespace['session_name']),
            window_name=window_name,
        )
        self.panes[pane['pane_id']] = pane
        return pane

    def split_pane(
        self,
        pane: dict[str, object],
        *,
        direction: str = 'right',
        percent: int = 50,
        command: list[str] | None = None,
        cwd: str = '',
        env: dict[str, str] | None = None,
        title: str = '',
    ) -> dict[str, object]:
        self.calls.append(('split_pane', {'pane': pane, 'direction': direction, 'percent': percent, 'command': command, 'cwd': cwd, 'env': env, 'title': title}))
        child = make_pane_ref(
            backend_impl='herdr',
            pane_id='herdr-pane-child',
            session_name=str(pane['session_name']),
            window_name=pane.get('window_name'),  # type: ignore[arg-type]
        )
        self.panes[child['pane_id']] = child
        return child

    def respawn_pane(
        self,
        pane: dict[str, object],
        *,
        command: list[str],
        cwd: str,
        env: dict[str, str],
    ) -> None:
        self.calls.append(('respawn_pane', {'pane': pane, 'command': command, 'cwd': cwd, 'env': env}))

    def set_pane_identity(
        self,
        pane: dict[str, object],
        *,
        title: str,
        agent_label: str,
        project_id: str,
        order_index: int | None,
        is_cmd: bool,
        role: str | None,
        slot_key: str | None,
        window_name: str | None,
        sidebar_instance: str | None,
        session_id: str | None,
        namespace_epoch: int | None,
        managed_by: str | None,
        provider_kind: str | None,
    ) -> None:
        self.calls.append(
            (
                'set_pane_identity',
                {
                    'pane': pane,
                    'title': title,
                    'agent_label': agent_label,
                    'project_id': project_id,
                    'order_index': order_index,
                    'is_cmd': is_cmd,
                    'role': role,
                    'slot_key': slot_key,
                    'window_name': window_name,
                    'sidebar_instance': sidebar_instance,
                    'session_id': session_id,
                    'namespace_epoch': namespace_epoch,
                    'managed_by': managed_by,
                    'provider_kind': provider_kind,
                },
            )
        )

    def kill_window(self, namespace: dict[str, object], *, window_id: str | None, target: str) -> None:
        self.calls.append(('kill_window', {'namespace': namespace, 'window_id': window_id, 'target': target}))

    def destroy_namespace(self, namespace: dict[str, object]) -> None:
        self.calls.append(('destroy_namespace', namespace))

    def kill_server(self, namespace: dict[str, object]) -> None:
        self.calls.append(('kill_server', namespace))

    def namespace_alive(self, namespace: dict[str, object]) -> bool:
        self.calls.append(('namespace_alive', namespace))
        if namespace.get('backend_impl') != 'herdr':
            raise MuxCommandErrorV2(
                category='invalid-request',
                backend_impl='herdr',
                operation='session_alive',
                detail='invalid Herdr namespace ref',
            )
        return True


def test_v2_mux_backend_helpers_use_namespace_refs_without_tmux_fallback(tmp_path: Path) -> None:
    backend = _FakeHerdrNamespaceBackend()

    prepare_server(backend)
    create_session(backend, session_name='ccb-herdr', project_root=tmp_path, window_name='cmd')
    ensure_server_policy(backend)
    workspace = ensure_window(
        backend,
        session_name='ccb-herdr',
        window_name='workspace',
        project_root=tmp_path,
        select=True,
    )
    windows = list_windows(backend, 'ccb-herdr')
    found = find_window(backend, session_name='ccb-herdr', window_name='workspace')
    root_pane = window_root_pane(backend, target_window='ccb-herdr:workspace')
    child_pane = split_pane(
        backend,
        target=root_pane,
        direction='right',
        percent=50,
        project_root=tmp_path,
    )
    respawn_pane(backend, pane_id=child_pane, command='echo ready', cwd=str(tmp_path))
    apply_pane_identity(
        backend,
        pane_id=child_pane,
        title='agent1',
        agent_label='agent1',
        project_id='proj-herdr',
        slot_key='agent1',
        window_name='workspace',
        namespace_epoch=1,
        managed_by='ccbd',
    )
    kill_window(backend, target='ccb-herdr:workspace')
    assert kill_server(backend) is True

    assert workspace.window_id == 'window-2'
    assert [window.window_name for window in windows] == ['ccb-herdr', 'workspace']
    assert found is not None
    assert found.window_name == 'workspace'
    assert root_pane == 'herdr-pane-root'
    assert child_pane == 'herdr-pane-child'
    assert not hasattr(backend, '_tmux_run')
    assert [call[0] for call in backend.calls] == [
        'prepare_server',
        'create_session',
        'ensure_server_policy',
        'ensure_window',
        'list_windows',
        'list_windows',
        'window_root_pane',
        'split_pane',
        'respawn_pane',
        'set_pane_identity',
        'kill_window',
        'destroy_namespace',
    ]


def test_v2_mux_backend_helpers_rebuild_namespace_ref_for_requested_session(tmp_path: Path) -> None:
    backend = _FakeHerdrNamespaceBackend()
    create_session(backend, session_name='ccb-old', project_root=tmp_path, window_name='cmd')

    ensure_window(
        backend,
        session_name='ccb-new',
        window_name='workspace',
        project_root=tmp_path,
        select=True,
    )

    ensure_call = backend.calls[-1]
    assert ensure_call[0] == 'ensure_window'
    assert ensure_call[1]['namespace']['session_name'] == 'ccb-new'  # type: ignore[index]
    assert ('namespace_ref', {'session_name': 'ccb-new', 'namespace_id': 'ccb-new'}) in backend.calls


def test_namespace_state_fields_rejects_cached_namespace_ref_for_different_session(tmp_path: Path) -> None:
    backend = _FakeHerdrNamespaceBackend()
    create_session(backend, session_name='ccb-old', project_root=tmp_path, window_name='cmd')

    fields = namespace_state_fields(
        backend,
        session_name='ccb-new',
        tmux_socket_path='',
    )

    assert fields['namespace_backend_family'] == 'tmux-family'
    assert fields['namespace_session_name'] is None
    assert fields['namespace_restore_token'] is None


def test_namespace_ref_aliases_do_not_retain_replaced_session(tmp_path: Path) -> None:
    backend = _FakeHerdrNamespaceBackend()
    create_session(backend, session_name='ccb-old', project_root=tmp_path, window_name='cmd')
    create_session(backend, session_name='ccb-new', project_root=tmp_path, window_name='cmd')

    old_fields = namespace_state_fields(
        backend,
        session_name='ccb-old',
        tmux_socket_path='',
    )
    new_fields = namespace_state_fields(
        backend,
        session_name='ccb-new',
        tmux_socket_path='',
    )

    assert old_fields['namespace_restore_token'] is None
    assert new_fields['namespace_session_name'] == 'ccb-new'
    assert new_fields['namespace_restore_token'] == 'restore-1'


def test_blank_namespace_ref_clears_previous_aliases(tmp_path: Path) -> None:
    backend = _FakeHerdrNamespaceBackend()
    create_session(backend, session_name='ccb-old', project_root=tmp_path, window_name='cmd')

    remember_namespace_state_ref(
        backend,
        SimpleNamespace(
            tmux_session_name='',
            namespace_ref=lambda: {
                'backend_family': 'herdr-native',
                'backend_impl': 'herdr',
                'namespace_id': 'w-blank',
                'session_name': '',
                'ipc_kind': 'herdr_socket',
                'ipc_ref': 'herdr://blank',
                'restore_token': 'blank-token',
            },
        ),
    )
    fields = namespace_state_fields(
        backend,
        session_name='ccb-old',
        tmux_socket_path='',
    )

    assert fields['namespace_session_name'] is None
    assert fields['namespace_restore_token'] is None


def test_herdr_backend_ignores_stale_tmux_namespace_state(tmp_path: Path) -> None:
    backend = _FakeHerdrNamespaceBackend()
    remember_namespace_state_ref(
        backend,
        SimpleNamespace(
            tmux_session_name='ccb-proj',
            namespace_backend_family='tmux-family',
            backend_impl='tmux',
            namespace_ref=lambda: {
                'backend_family': 'tmux-family',
                'backend_impl': 'tmux',
                'namespace_id': 'ccb-proj',
                'session_name': 'ccb-proj',
                'ipc_kind': 'psmux',
                'ipc_ref': str(tmp_path / 'tmux.sock'),
                'restore_token': None,
            },
        ),
    )

    assert session_alive(backend, 'ccb-proj') is True
    assert ('namespace_ref', {'session_name': 'ccb-proj', 'namespace_id': 'ccb-proj'}) in backend.calls
    alive_call = backend.calls[-1]
    assert alive_call[0] == 'namespace_alive'
    assert alive_call[1]['backend_impl'] == 'herdr'  # type: ignore[index]


def test_v2_mux_backend_helper_capability_gap_fails_closed(tmp_path: Path) -> None:
    backend = _FakeHerdrNamespaceBackend(pane_spawn_status='unsupported')
    create_session(backend, session_name='ccb-herdr', project_root=tmp_path, window_name='cmd')

    with pytest.raises(MuxCommandErrorV2) as exc_info:
        ensure_window(
            backend,
            session_name='ccb-herdr',
            window_name='workspace',
            project_root=tmp_path,
            select=True,
        )

    assert exc_info.value.category == 'unsupported'
    assert exc_info.value.operation == 'ensure_window'
    assert exc_info.value.evidence['unsupported_capabilities'] == ['workspace_focus']
    assert [call[0] for call in backend.calls] == ['create_session']


def test_prepare_server_then_create_session_and_server_policy_retry_transient_tmux_failures(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv('CCB_TMUX_OBJECT_READY_POLL_INTERVAL_S', '0')
    monkeypatch.setenv('DISPLAY', ':99')
    monkeypatch.setenv('BROWSER', 'wslview')
    monkeypatch.setenv('DBUS_SESSION_BUS_ADDRESS', 'unix:path=/run/user/1000/bus')
    monkeypatch.setenv('WT_SESSION', 'windows-terminal-session')
    monkeypatch.setenv('AGENT_ROLES_STORE', '/home/demo/.roles')
    backend = _FlakyBackend()
    backend.fail_once('start-server')
    backend.fail_once('set-option', '-g', 'destroy-unattached', 'off')
    backend.fail_once(
        'new-session',
        '-d',
        '-x',
        '160',
        '-y',
        '48',
        '-s',
        'ccb-proj',
        '-n',
        'cmd',
        '-c',
        str(tmp_path),
        'sh',
        '-lc',
        'while :; do sleep 3600; done',
    )

    prepare_server(backend)
    create_session(backend, session_name='ccb-proj', project_root=tmp_path, window_name='cmd')
    ensure_server_policy(backend)

    assert backend.calls.count(('start-server',)) == 2
    assert backend.calls.count(('set-option', '-g', 'destroy-unattached', 'off')) == 2
    assert backend.calls.count(('set-option', '-g', 'mouse', 'on')) == 1
    assert backend.calls.count(('set-option', '-g', 'history-limit', '10000')) == 1
    assert backend.calls.count(('set-option', '-g', 'set-clipboard', 'on')) == 1
    assert backend.calls.count(('set-option', '-g', 'focus-events', 'on')) == 1
    assert backend.calls.count(('set-option', '-g', 'escape-time', '10')) == 1
    assert backend.calls.count(('set-option', '-g', 'allow-passthrough', 'on')) == 1
    assert backend.calls.count(('set-window-option', '-g', 'mode-keys', 'vi')) == 1
    assert backend.calls.count(('bind-key', '-T', 'copy-mode-vi', 'v', 'send-keys', '-X', 'begin-selection')) == 1
    assert ('bind-key', '-T', 'copy-mode-vi', 'y', 'send-keys', '-X', 'copy-selection-and-cancel') not in backend.calls
    assert any(
        call[:7] == ('bind-key', '-T', 'copy-mode-vi', 'y', 'send-keys', '-X', 'copy-pipe-and-cancel')
        and 'xclip -selection clipboard <"$tmp"' in call[7]
        and 'exec xclip' not in call[7]
        for call in backend.calls
    )
    assert ('set-environment', '-g', 'DISPLAY', ':99') in backend.calls
    assert ('set-environment', '-g', 'BROWSER', 'wslview') in backend.calls
    assert (
        'set-environment',
        '-g',
        'DBUS_SESSION_BUS_ADDRESS',
        'unix:path=/run/user/1000/bus',
    ) in backend.calls
    assert ('set-environment', '-g', 'WT_SESSION', 'windows-terminal-session') in backend.calls
    assert ('set-environment', '-g', 'AGENT_ROLES_STORE', '/home/demo/.roles') in backend.calls
    assert backend.calls.count(('bind-key', 'h', 'select-pane', '-L')) == 1
    assert backend.calls.count(('bind-key', '-r', 'L', 'resize-pane', '-R', '5')) == 1
    assert backend.calls.count(
        (
            'new-session',
            '-d',
            '-x',
            '160',
            '-y',
            '48',
            '-s',
            'ccb-proj',
            '-n',
            'cmd',
            '-c',
            str(tmp_path),
            'sh',
            '-lc',
            'while :; do sleep 3600; done',
        )
    ) == 2


def test_prepare_server_accepts_fast_probe_timeout(monkeypatch) -> None:
    monkeypatch.setenv('CCB_TMUX_OBJECT_READY_POLL_INTERVAL_S', '0')
    backend = _FlakyBackend()

    prepare_server(backend, timeout_s=0.0)

    assert backend.calls == [('start-server',)]


def test_fresh_namespace_creates_session_before_server_policy(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv('CCB_TMUX_OBJECT_READY_POLL_INTERVAL_S', '0')
    monkeypatch.delenv('DISPLAY', raising=False)
    monkeypatch.delenv('WAYLAND_DISPLAY', raising=False)
    monkeypatch.delenv('XDG_RUNTIME_DIR', raising=False)
    monkeypatch.delenv('WSL_DISTRO_NAME', raising=False)
    monkeypatch.delenv('WSL_INTEROP', raising=False)
    monkeypatch.delenv('SSH_AUTH_SOCK', raising=False)
    monkeypatch.delenv('SSH_CONNECTION', raising=False)
    for key in (
        'TERM',
        'TERM_PROGRAM',
        'TERM_PROGRAM_VERSION',
        'PATH',
        'SHELL',
        'BROWSER',
        'DBUS_SESSION_BUS_ADDRESS',
        'DESKTOP_SESSION',
        'XAUTHORITY',
        'XDG_CURRENT_DESKTOP',
        'XDG_SESSION_DESKTOP',
        'XDG_SESSION_TYPE',
        'WSLENV',
        'WT_PROFILE_ID',
        'WT_SESSION',
        'KITTY_WINDOW_ID',
        'WEZTERM_EXECUTABLE',
        'WEZTERM_PANE',
        'WEZTERM_UNIX_SOCKET',
        'CCB_WORKBENCH_PROFILE',
        'CCB_WORKBENCH_FORCE_RICH',
        'CCB_WORKBENCH_ROOT',
        'CCB_WORKBENCH_TERMINAL_PROGRAM',
        'CCB_WORKBENCH_TERMINAL_PROGRAM_VERSION',
        'CCB_WORKBENCH_YAZI_SAFE_CONFIG',
        'CCB_WORKBENCH_YAZI_RICH_CONFIG',
    ):
        monkeypatch.delenv(key, raising=False)
    backend = _FlakyBackend()
    backend.require_session_for_server_policy = True

    create_session(backend, session_name='ccb-proj', project_root=tmp_path, window_name='cmd')
    ensure_server_policy(backend)

    assert backend.calls[0][:1] == ('new-session',)
    assert ('start-server',) not in backend.calls
    assert ('set-option', '-g', 'destroy-unattached', 'off') not in backend.calls[:1]
    assert ('set-option', '-g', 'mouse', 'on') not in backend.calls[:1]
    assert ('set-option', '-g', 'history-limit', '10000') not in backend.calls[:1]
    assert ('set-option', '-g', 'set-clipboard', 'on') not in backend.calls[:1]
    assert ('set-option', '-g', 'focus-events', 'on') not in backend.calls[:1]
    assert ('set-option', '-g', 'escape-time', '10') not in backend.calls[:1]
    expected_policy_calls = [
        ('set-option', '-g', 'destroy-unattached', 'off'),
        ('set-option', '-g', 'mouse', 'on'),
        ('set-option', '-g', 'history-limit', '10000'),
        ('set-option', '-g', 'set-clipboard', 'on'),
        ('set-option', '-g', 'focus-events', 'on'),
        ('set-option', '-g', 'escape-time', '10'),
        ('set-option', '-g', 'allow-passthrough', 'on'),
        ('set-option', '-g', 'update-environment', _TMUX_UPDATE_ENVIRONMENT_FOR_TEST),
        ('set-window-option', '-g', 'mode-keys', 'vi'),
        ('bind-key', '-T', 'copy-mode-vi', 'v', 'send-keys', '-X', 'begin-selection'),
        ('bind-key', '-T', 'copy-mode-vi', 'C-v', 'send-keys', '-X', 'rectangle-toggle'),
        (
            'bind-key',
            '-T',
            'copy-mode-vi',
            'y',
            'send-keys',
            '-X',
            'copy-pipe-and-cancel',
            _clipboard_pipe_command_for_test(),
        ),
        (
            'bind-key',
            '-T',
            'copy-mode-vi',
            'Enter',
            'send-keys',
            '-X',
            'copy-pipe-and-cancel',
            _clipboard_pipe_command_for_test(),
        ),
        (
            'bind-key',
            '-T',
            'copy-mode-vi',
            'MouseDragEnd1Pane',
            'send-keys',
            '-X',
            'copy-pipe-and-cancel',
            _clipboard_pipe_command_for_test(),
        ),
        ('bind-key', 'h', 'select-pane', '-L'),
        ('bind-key', 'j', 'select-pane', '-D'),
        ('bind-key', 'k', 'select-pane', '-U'),
        ('bind-key', 'l', 'select-pane', '-R'),
        ('bind-key', '-r', 'H', 'resize-pane', '-L', '5'),
        ('bind-key', '-r', 'J', 'resize-pane', '-D', '5'),
        ('bind-key', '-r', 'K', 'resize-pane', '-U', '5'),
        ('bind-key', '-r', 'L', 'resize-pane', '-R', '5'),
    ]
    assert backend.calls[-len(expected_policy_calls):] == expected_policy_calls


def test_list_windows_retries_transient_tmux_failures(monkeypatch) -> None:
    monkeypatch.setenv('CCB_TMUX_OBJECT_READY_POLL_INTERVAL_S', '0')
    backend = _FlakyBackend()
    backend.fail_once('list-windows', '-t', 'ccb-proj', '-F', '#{window_id}\t#{window_name}\t#{window_active}')

    windows = list_windows(backend, 'ccb-proj')

    assert [(window.window_id, window.window_name, window.active) for window in windows] == [
        ('@1', 'cmd', True),
        ('@2', 'workspace', False),
    ]
    assert backend.calls.count(('list-windows', '-t', 'ccb-proj', '-F', '#{window_id}\t#{window_name}\t#{window_active}')) == 2


def test_session_alive_retries_transient_tmux_failures(monkeypatch) -> None:
    monkeypatch.setenv('CCB_TMUX_OBJECT_READY_POLL_INTERVAL_S', '0')
    backend = _FlakyBackend()
    backend.session_created = True

    original_tmux_run = backend._tmux_run
    state = {'remaining': 1}

    def _tmux_run(args, *, check=False, capture=False, timeout=None):
        if tuple(str(item) for item in args) == ('has-session', '-t', 'ccb-proj') and state['remaining'] > 0:
            state['remaining'] -= 1
            backend.calls.append(tuple(str(item) for item in args))
            return subprocess.CompletedProcess(
                ['tmux', *args],
                1,
                stdout='',
                stderr='fork failed: resource temporarily unavailable\n',
            )
        return original_tmux_run(args, check=check, capture=capture, timeout=timeout)

    backend._tmux_run = _tmux_run  # type: ignore[method-assign]

    assert session_alive(backend, 'ccb-proj') is True
    assert backend.calls.count(('has-session', '-t', 'ccb-proj')) == 2


def test_session_alive_treats_absent_project_server_as_missing_namespace(monkeypatch) -> None:
    monkeypatch.setenv('CCB_TMUX_OBJECT_READY_POLL_INTERVAL_S', '0')
    backend = _FlakyBackend()
    backend.missing_session_stderr = 'no server running on /tmp/ccb-runtime/test.sock\n'

    assert session_alive(backend, 'ccb-proj') is False
    assert backend.calls.count(('has-session', '-t', 'ccb-proj')) == 1


def test_session_alive_treats_missing_project_socket_as_missing_namespace(monkeypatch) -> None:
    monkeypatch.setenv('CCB_TMUX_OBJECT_READY_POLL_INTERVAL_S', '0')
    backend = _FlakyBackend()
    backend.missing_session_stderr = (
        'error connecting to /tmp/ccb-runtime/test.sock (No such file or directory)\n'
    )

    assert session_alive(backend, 'ccb-proj') is False
    assert backend.calls.count(('has-session', '-t', 'ccb-proj')) == 1


def test_wait_for_root_pane_raises_transient_unavailable_for_fast_probe(monkeypatch) -> None:
    monkeypatch.setenv('CCB_TMUX_OBJECT_READY_POLL_INTERVAL_S', '0')
    backend = _FlakyBackend()
    backend.fail_once('list-panes', '-t', 'ccb-proj:workspace', '-F', '#{pane_id}')

    with pytest.raises(TmuxTransientServerUnavailable):
        wait_for_root_pane(backend, target_window='ccb-proj:workspace', timeout_s=0.0)


def test_find_window_uses_fast_probe_timeout_when_provided(monkeypatch) -> None:
    monkeypatch.setenv('CCB_TMUX_OBJECT_READY_POLL_INTERVAL_S', '0')
    backend = _FlakyBackend()
    backend.fail_once('list-windows', '-t', 'ccb-proj', '-F', '#{window_id}\t#{window_name}\t#{window_active}')

    with pytest.raises(TmuxTransientServerUnavailable):
        find_window(backend, session_name='ccb-proj', window_name='workspace', timeout_s=0.0)
    assert backend.calls.count(('list-windows', '-t', 'ccb-proj', '-F', '#{window_id}\t#{window_name}\t#{window_active}')) == 1


def test_create_window_uses_fast_probe_timeout_when_provided(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv('CCB_TMUX_OBJECT_READY_POLL_INTERVAL_S', '0')
    backend = _FlakyBackend()
    backend.fail_once('list-windows', '-t', 'ccb-proj', '-F', '#{window_id}\t#{window_name}\t#{window_active}')

    record = create_window(
        backend,
        session_name='ccb-proj',
        window_name='workspace',
        project_root=tmp_path,
        timeout_s=0.0,
    )
    assert record.window_name == 'workspace'
    assert backend.calls.count(('list-windows', '-t', 'ccb-proj', '-F', '#{window_id}\t#{window_name}\t#{window_active}')) == 2


def test_ensure_window_uses_fast_probe_timeout_when_provided(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv('CCB_TMUX_OBJECT_READY_POLL_INTERVAL_S', '0')
    backend = _FlakyBackend()
    backend.fail_once('list-windows', '-t', 'ccb-proj', '-F', '#{window_id}\t#{window_name}\t#{window_active}')

    with pytest.raises(TmuxTransientServerUnavailable):
        ensure_window(
            backend,
            session_name='ccb-proj',
            window_name='workspace',
            project_root=tmp_path,
            timeout_s=0.0,
        )
    assert backend.calls.count(('list-windows', '-t', 'ccb-proj', '-F', '#{window_id}\t#{window_name}\t#{window_active}')) == 1


def test_create_session_uses_terminal_size_hint_when_provided(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv('CCB_TMUX_OBJECT_READY_POLL_INTERVAL_S', '0')
    backend = _FlakyBackend()

    create_session(
        backend,
        session_name='ccb-proj',
        project_root=tmp_path,
        window_name='cmd',
        terminal_size=(233, 61),
    )

    assert backend.calls == [
        (
            'new-session',
            '-d',
            '-x',
            '233',
            '-y',
            '61',
            '-s',
            'ccb-proj',
            '-n',
            'cmd',
            '-c',
            str(tmp_path),
            'sh',
            '-lc',
            'while :; do sleep 3600; done',
        )
    ]


def test_create_session_falls_back_to_default_size_when_terminal_size_too_small(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv('CCB_TMUX_OBJECT_READY_POLL_INTERVAL_S', '0')
    backend = _FlakyBackend()

    create_session(
        backend,
        session_name='ccb-proj',
        project_root=tmp_path,
        window_name='cmd',
        terminal_size=(10, 5),
    )

    assert backend.calls == [
        (
            'new-session',
            '-d',
            '-x',
            '160',
            '-y',
            '48',
            '-s',
            'ccb-proj',
            '-n',
            'cmd',
            '-c',
            str(tmp_path),
            'sh',
            '-lc',
            'while :; do sleep 3600; done',
        )
    ]


def test_create_session_accepts_fast_probe_timeout(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv('CCB_TMUX_OBJECT_READY_POLL_INTERVAL_S', '0')
    backend = _FlakyBackend()

    create_session(
        backend,
        session_name='ccb-proj',
        project_root=tmp_path,
        window_name='cmd',
        timeout_s=0.0,
    )

    assert backend.calls[0][:2] == ('new-session', '-d')


def test_ensure_server_policy_accepts_fast_probe_timeout(monkeypatch) -> None:
    monkeypatch.setenv('CCB_TMUX_OBJECT_READY_POLL_INTERVAL_S', '0')
    backend = _FlakyBackend()

    ensure_server_policy(backend, timeout_s=0.0)

    assert backend.calls[:7] == [
        ('set-option', '-g', 'destroy-unattached', 'off'),
        ('set-option', '-g', 'mouse', 'on'),
        ('set-option', '-g', 'history-limit', '10000'),
        ('set-option', '-g', 'set-clipboard', 'on'),
        ('set-option', '-g', 'focus-events', 'on'),
        ('set-option', '-g', 'escape-time', '10'),
        ('set-option', '-g', 'allow-passthrough', 'on'),
    ]
    assert ('set-option', '-g', 'update-environment', _TMUX_UPDATE_ENVIRONMENT_FOR_TEST) in backend.calls
    assert (
        'bind-key',
        '-T',
        'copy-mode-vi',
        'MouseDragEnd1Pane',
        'send-keys',
        '-X',
        'copy-pipe-and-cancel',
        _clipboard_pipe_command_for_test(),
    ) in backend.calls
    assert backend.calls[-14:] == [
        ('set-window-option', '-g', 'mode-keys', 'vi'),
        ('bind-key', '-T', 'copy-mode-vi', 'v', 'send-keys', '-X', 'begin-selection'),
        ('bind-key', '-T', 'copy-mode-vi', 'C-v', 'send-keys', '-X', 'rectangle-toggle'),
        ('bind-key', '-T', 'copy-mode-vi', 'y', 'send-keys', '-X', 'copy-pipe-and-cancel', _clipboard_pipe_command_for_test()),
        ('bind-key', '-T', 'copy-mode-vi', 'Enter', 'send-keys', '-X', 'copy-pipe-and-cancel', _clipboard_pipe_command_for_test()),
        ('bind-key', '-T', 'copy-mode-vi', 'MouseDragEnd1Pane', 'send-keys', '-X', 'copy-pipe-and-cancel', _clipboard_pipe_command_for_test()),
        ('bind-key', 'h', 'select-pane', '-L'),
        ('bind-key', 'j', 'select-pane', '-D'),
        ('bind-key', 'k', 'select-pane', '-U'),
        ('bind-key', 'l', 'select-pane', '-R'),
        ('bind-key', '-r', 'H', 'resize-pane', '-L', '5'),
        ('bind-key', '-r', 'J', 'resize-pane', '-D', '5'),
        ('bind-key', '-r', 'K', 'resize-pane', '-U', '5'),
        ('bind-key', '-r', 'L', 'resize-pane', '-R', '5'),
    ]


def test_kill_window_accepts_fast_probe_timeout(monkeypatch) -> None:
    monkeypatch.setenv('CCB_TMUX_OBJECT_READY_POLL_INTERVAL_S', '0')
    backend = _FlakyBackend()

    from ccbd.services.project_namespace_runtime.backend import kill_window

    kill_window(backend, target='ccb-proj:@1', timeout_s=0.0)

    assert backend.calls == [('kill-window', '-t', 'ccb-proj:@1')]
