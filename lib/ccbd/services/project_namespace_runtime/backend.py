from __future__ import annotations

from dataclasses import dataclass
import os
from pathlib import Path
import time
from typing import Callable

from terminal_runtime.mux_backend_contract import MuxCommandErrorV2
from terminal_runtime.env import tmux_history_limit
from terminal_runtime.placeholders import pane_placeholder_argv, pane_placeholder_cmd
from terminal_runtime.tmux_readiness import (
    TmuxCommandError,
    TmuxTransientServerUnavailable,
    is_tmux_absent_server_text,
    is_tmux_missing_session_text,
    is_tmux_transient_server_error_text,
    tmux_command_failure_message,
    tmux_object_ready_poll_interval_s,
    tmux_object_ready_timeout_s,
    tmux_failure_detail,
)

_MUX_NAMESPACE_REF_ATTR = '_ccb_project_namespace_ref'
_MUX_NAMESPACE_REF_ALIASES_ATTR = '_ccb_project_namespace_ref_aliases'
_MUX_PANE_REFS_ATTR = '_ccb_project_pane_refs'

_TMUX_ENVIRONMENT_KEYS = (
    'TERM',
    'TERM_PROGRAM',
    'TERM_PROGRAM_VERSION',
    'PATH',
    'SHELL',
    'BROWSER',
    'DBUS_SESSION_BUS_ADDRESS',
    'DESKTOP_SESSION',
    'DISPLAY',
    'WAYLAND_DISPLAY',
    'XAUTHORITY',
    'XDG_CURRENT_DESKTOP',
    'XDG_RUNTIME_DIR',
    'XDG_SESSION_DESKTOP',
    'XDG_SESSION_TYPE',
    'WSL_DISTRO_NAME',
    'WSL_INTEROP',
    'WSLENV',
    'WT_PROFILE_ID',
    'WT_SESSION',
    'SSH_AUTH_SOCK',
    'SSH_CONNECTION',
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
    'AGENT_ROLES_STORE',
)
_CLIPBOARD_PIPE_COMMAND = (
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


@dataclass(frozen=True)
class TmuxWindowRecord:
    window_id: str | None
    window_name: str
    active: bool = False


def build_backend(backend_factory, *, socket_path: str, namespace_state=None):
    try:
        return backend_factory(socket_path=socket_path, namespace_state=namespace_state)
    except TypeError:
        try:
            return backend_factory(socket_path=socket_path)
        except TypeError:
            return backend_factory()


def remember_namespace_state_ref(backend, state) -> None:
    if not _is_mux_backend(backend) or state is None:
        return
    namespace_ref = getattr(state, 'namespace_ref', None)
    if callable(namespace_ref):
        ref = namespace_ref()
        if not _namespace_state_matches_backend(backend, state, ref):
            return
        _remember_mux_namespace_ref(
            backend,
            ref,
            requested_session_name=getattr(state, 'tmux_session_name', None),
        )


def _namespace_state_matches_backend(backend, state, namespace: object | None = None) -> bool:
    backend_impl = str(getattr(backend, 'backend_impl', '') or '').strip()
    state_impl = str(getattr(state, 'backend_impl', '') or '').strip()
    state_family = str(getattr(state, 'namespace_backend_family', '') or '').strip()
    ref_impl_value = namespace.get('backend_impl') if isinstance(namespace, dict) else ''
    ref_family_value = namespace.get('backend_family') if isinstance(namespace, dict) else ''
    ref_impl = str(ref_impl_value or '').strip()
    ref_family = str(ref_family_value or '').strip()
    if backend_impl == 'herdr':
        if state_impl or state_family:
            return state_impl == 'herdr' or state_family == 'herdr-native'
        return ref_impl == 'herdr' or ref_family == 'herdr-native'
    if backend_impl:
        if state_impl or state_family:
            return state_impl in {'', backend_impl} and state_family != 'herdr-native'
        return ref_impl in {'', backend_impl} and ref_family != 'herdr-native'
    return state_impl not in {'herdr'} and ref_impl not in {'herdr'}


def namespace_state_fields(backend, *, session_name: str, tmux_socket_path: str) -> dict[str, object | None]:
    ref = _mux_namespace_ref_if_present(backend, session_name=session_name) if _is_mux_backend(backend) else None
    if ref is None:
        return {
            'namespace_backend_family': 'tmux-family',
            'backend_impl': 'tmux',
            'namespace_id': None,
            'namespace_session_name': None,
            'namespace_ipc_kind': None,
            'namespace_ipc_ref': None,
            'namespace_restore_token': None,
        }
    return {
        'namespace_backend_family': ref.get('backend_family'),
        'backend_impl': ref.get('backend_impl'),
        'namespace_id': ref.get('namespace_id'),
        'namespace_session_name': ref.get('session_name') or session_name,
        'namespace_ipc_kind': ref.get('ipc_kind'),
        'namespace_ipc_ref': ref.get('ipc_ref') or tmux_socket_path,
        'namespace_restore_token': ref.get('restore_token'),
    }


def prepare_server(backend, *, timeout_s: float | None = None) -> None:
    if _is_mux_backend(backend):
        _require_mux_operation(backend, operation='prepare_server', methods=('prepare_server',))
        backend.prepare_server()
        return
    _tmux_run_ready(
        backend,
        ['start-server'],
        failure_message='failed to prepare tmux server',
        timeout_s=timeout_s,
    )


def ensure_server_policy(backend, *, timeout_s: float | None = None) -> None:
    if _is_mux_backend(backend):
        policy = getattr(backend, 'ensure_server_policy', None)
        if callable(policy):
            _require_mux_operation(backend, operation='ensure_server_policy', methods=('ensure_server_policy',))
            policy()
        return
    _tmux_run_ready(
        backend,
        ['set-option', '-g', 'destroy-unattached', 'off'],
        failure_message='failed to persist tmux destroy-unattached policy',
        timeout_s=timeout_s,
    )
    _apply_optional_server_policy(backend, option='mouse', value='on', timeout_s=timeout_s)
    _apply_optional_server_policy(
        backend,
        option='history-limit',
        value=str(tmux_history_limit()),
        timeout_s=timeout_s,
    )
    _apply_optional_server_policy(backend, option='set-clipboard', value='on', timeout_s=timeout_s)
    _apply_optional_server_policy(backend, option='focus-events', value='on', timeout_s=timeout_s)
    _apply_optional_server_policy(backend, option='escape-time', value='10', timeout_s=timeout_s)
    _apply_optional_server_policy(backend, option='allow-passthrough', value='on', timeout_s=timeout_s)
    _apply_tmux_environment_policy(backend, timeout_s=timeout_s)
    _apply_optional_window_policy(backend, option='mode-keys', value='vi', timeout_s=timeout_s)
    _apply_optional_tmux_policy(
        backend,
        ['bind-key', '-T', 'copy-mode-vi', 'v', 'send-keys', '-X', 'begin-selection'],
        description='tmux copy-mode-vi begin-selection binding',
        timeout_s=timeout_s,
    )
    _apply_optional_tmux_policy(
        backend,
        ['bind-key', '-T', 'copy-mode-vi', 'C-v', 'send-keys', '-X', 'rectangle-toggle'],
        description='tmux copy-mode-vi rectangle-toggle binding',
        timeout_s=timeout_s,
    )
    for key in ('y', 'Enter', 'MouseDragEnd1Pane'):
        _apply_optional_tmux_policy(
            backend,
            ['bind-key', '-T', 'copy-mode-vi', key, 'send-keys', '-X', 'copy-pipe-and-cancel', _CLIPBOARD_PIPE_COMMAND],
            description=f'tmux copy-mode-vi clipboard binding {key}',
            timeout_s=timeout_s,
        )
    for key, direction in (('h', '-L'), ('j', '-D'), ('k', '-U'), ('l', '-R')):
        _apply_optional_tmux_policy(
            backend,
            ['bind-key', key, 'select-pane', direction],
            description=f'tmux vi pane focus binding {key}',
            timeout_s=timeout_s,
        )
    for key, direction in (('H', '-L'), ('J', '-D'), ('K', '-U'), ('L', '-R')):
        _apply_optional_tmux_policy(
            backend,
            ['bind-key', '-r', key, 'resize-pane', direction, '5'],
            description=f'tmux vi pane resize binding {key}',
            timeout_s=timeout_s,
        )


def _apply_tmux_environment_policy(backend, *, timeout_s: float | None = None) -> None:
    update_environment = ' '.join(_TMUX_ENVIRONMENT_KEYS)
    _apply_optional_tmux_policy(
        backend,
        ['set-option', '-g', 'update-environment', update_environment],
        description='tmux update-environment policy',
        timeout_s=timeout_s,
    )
    for key in _TMUX_ENVIRONMENT_KEYS:
        value = os.environ.get(key)
        if value:
            _apply_optional_tmux_policy(
                backend,
                ['set-environment', '-g', key, value],
                description=f'tmux environment {key}',
                timeout_s=timeout_s,
            )


def _apply_optional_server_policy(backend, *, option: str, value: str, timeout_s: float | None = None) -> None:
    _apply_optional_tmux_policy(
        backend,
        ['set-option', '-g', option, value],
        description=f'tmux {option} policy',
        timeout_s=timeout_s,
    )


def _apply_optional_window_policy(backend, *, option: str, value: str, timeout_s: float | None = None) -> None:
    _apply_optional_tmux_policy(
        backend,
        ['set-window-option', '-g', option, value],
        description=f'tmux {option} window policy',
        timeout_s=timeout_s,
    )


def _apply_optional_tmux_policy(
    backend,
    args: list[str],
    *,
    description: str,
    timeout_s: float | None = None,
) -> None:
    try:
        _tmux_run_ready(
            backend,
            args,
            failure_message=f'failed to persist {description}',
            timeout_s=timeout_s,
        )
    except Exception:
        return


def create_session(
    backend,
    *,
    session_name: str,
    project_root,
    window_name: str | None = None,
    terminal_size: tuple[int, int] | None = None,
    timeout_s: float | None = None,
) -> None:
    if _is_mux_backend(backend):
        _require_mux_operation(backend, operation='create_session', methods=('create_session',))
        namespace = backend.create_session(
            project_id=session_name,
            cwd=str(project_root),
            title=session_name,
        )
        _remember_mux_namespace_ref(backend, namespace, requested_session_name=session_name)
        return
    width, height = _resolved_session_size(terminal_size)
    args = [
        'new-session',
        '-d',
        '-x',
        str(width),
        '-y',
        str(height),
        '-s',
        session_name,
    ]
    if str(window_name or '').strip():
        args.extend(['-n', str(window_name).strip()])
    args.extend(
        [
            '-c',
            str(project_root),
            *pane_placeholder_argv(),
        ]
    )
    _tmux_run_ready(
        backend,
        args,
        failure_message=f'failed to create tmux session {session_name!r}',
        timeout_s=timeout_s,
    )


def _resolved_session_size(terminal_size: tuple[int, int] | None) -> tuple[int, int]:
    default = (160, 48)
    if terminal_size is None:
        return default
    try:
        width = int(terminal_size[0])
        height = int(terminal_size[1])
    except Exception:
        return default
    # 40x15 是四分屏可正常拆分的 sanity 下限：低于此宽高 tmux 无法materialize
    # 两列四 pane 布局，且这类极小值通常来自尚未初始化/已 detached 的终端脏读，
    # 一律回退到 default 而非硬塞，避免起出畸形 pane。
    if width < 40 or height < 15:
        return default
    return width, height


def session_window_target(session_name: str, window_name: str | None = None) -> str:
    session_text = str(session_name or '').strip()
    window_text = str(window_name or '').strip()
    if not session_text:
        raise ValueError('session_name cannot be empty')
    if not window_text:
        return session_text
    return f'{session_text}:{window_text}'


def list_windows(backend, session_name: str, *, timeout_s: float | None = None) -> tuple[TmuxWindowRecord, ...]:
    if _is_mux_backend(backend):
        _require_mux_operation(backend, operation='list_windows', methods=('list_windows',))
        namespace = _mux_namespace_ref(backend, session_name=session_name)
        return tuple(_mux_window_record(item) for item in backend.list_windows(namespace))
    result = _tmux_run_ready(
        backend,
        ['list-windows', '-t', session_name, '-F', '#{window_id}\t#{window_name}\t#{window_active}'],
        failure_message=f'failed to list tmux windows for session {session_name!r}',
        timeout_s=timeout_s,
    )
    windows: list[TmuxWindowRecord] = []
    for line in (result.stdout or '').splitlines():
        parts = line.split('\t')
        if len(parts) != 3:
            continue
        window_id = (parts[0] or '').strip() or None
        window_name = (parts[1] or '').strip()
        if not window_name:
            continue
        windows.append(
            TmuxWindowRecord(
                window_id=window_id,
                window_name=window_name,
                active=(parts[2] or '').strip() in {'1', 'true', 'True'},
            )
        )
    return tuple(windows)


def find_window(backend, *, session_name: str, window_name: str, timeout_s: float | None = None) -> TmuxWindowRecord | None:
    target_name = str(window_name or '').strip()
    if not target_name:
        return None
    if timeout_s is not None:
        for record in list_windows(backend, session_name, timeout_s=timeout_s):
            if record.window_name == target_name:
                return record
        return None
    for record in list_windows(backend, session_name):
        if record.window_name == target_name:
            return record
    return None


def create_window(backend, *, session_name: str, window_name: str, project_root, select: bool = False, timeout_s: float | None = None) -> TmuxWindowRecord:
    if _is_mux_backend(backend):
        creator = getattr(backend, 'create_window', None)
        if not callable(creator):
            creator = getattr(backend, 'ensure_window', None)
        _require_mux_operation(backend, operation='create_window', methods=('create_window', 'ensure_window'), require_any=True)
        namespace = _mux_namespace_ref(backend, session_name=session_name)
        record = creator(
            namespace,
            window_name=window_name,
            cwd=str(project_root),
            select=select,
        )
        return _mux_window_record(record)
    _tmux_run_ready(
        backend,
        [
            'new-window',
            '-d',
            '-t',
            session_name,
            '-n',
            window_name,
            '-c',
            str(project_root),
            *pane_placeholder_argv(),
        ],
        failure_message=f'failed to create tmux window {window_name!r} for session {session_name!r}',
        timeout_s=timeout_s,
    )
    record = wait_for_window(backend, session_name=session_name, window_name=window_name, timeout_s=timeout_s)
    if record is None:
        raise RuntimeError(f'failed to resolve tmux window {window_name!r} for session {session_name!r}')
    if select:
        select_window(
            backend,
            target=session_window_target(session_name, record.window_id or window_name),
        )
    return record


def ensure_window(backend, *, session_name: str, window_name: str, project_root, select: bool = False, timeout_s: float | None = None) -> TmuxWindowRecord:
    if _is_mux_backend(backend):
        _require_mux_operation(backend, operation='ensure_window', methods=('ensure_window',))
        namespace = _mux_namespace_ref(backend, session_name=session_name)
        record = backend.ensure_window(
            namespace,
            window_name=window_name,
            cwd=str(project_root),
            select=select,
        )
        return _mux_window_record(record)
    record = find_window(backend, session_name=session_name, window_name=window_name, timeout_s=timeout_s)
    if record is None:
        record = create_window(
            backend,
            session_name=session_name,
            window_name=window_name,
            project_root=project_root,
            select=select,
            timeout_s=timeout_s,
        )
    elif select:
        select_window(
            backend,
            target=session_window_target(session_name, record.window_id or window_name),
        )
    return record


def rename_window(backend, *, target: str, new_name: str, timeout_s: float | None = None) -> None:
    if _is_mux_backend(backend):
        _require_mux_operation(backend, operation='rename_window', methods=('rename_window',))
        namespace = _mux_namespace_ref(backend, session_name=_target_session_name(target))
        backend.rename_window(
            namespace,
            window_id=_target_window_name(target),
            target=target,
            new_name=new_name,
        )
        return
    _tmux_run_ready(
        backend,
        ['rename-window', '-t', target, new_name],
        failure_message=f'failed to rename tmux window target {target!r} to {new_name!r}',
        timeout_s=timeout_s,
    )
    session_name, _sep, _old_name = target.partition(':')
    resolved_session_name = session_name.strip()
    if resolved_session_name and wait_for_window(backend, session_name=resolved_session_name, window_name=new_name, timeout_s=timeout_s) is None:
        raise RuntimeError(f'failed to observe renamed tmux window {new_name!r} for session {resolved_session_name!r}')


def kill_window(backend, *, target: str, timeout_s: float | None = None) -> None:
    if _is_mux_backend(backend):
        _require_mux_operation(backend, operation='kill_window', methods=('kill_window',))
        namespace = _mux_namespace_ref(backend, session_name=_target_session_name(target))
        backend.kill_window(namespace, window_id=_target_window_name(target), target=target)
        return
    _tmux_run_ready(
        backend,
        ['kill-window', '-t', target],
        failure_message=f'failed to kill tmux window target {target!r}',
        timeout_s=timeout_s,
    )


def session_alive(backend, session_name: str, *, timeout_s: float | None = None) -> bool:
    if _is_mux_backend(backend):
        checker = getattr(backend, 'namespace_alive', None) or getattr(backend, 'session_alive', None)
        if not callable(checker):
            return _mux_namespace_ref_if_present(backend, session_name=session_name) is not None
        _require_mux_operation(backend, operation='session_alive', methods=('namespace_alive', 'session_alive'), require_any=True)
        return bool(checker(_mux_namespace_ref(backend, session_name=session_name)))
    runner = getattr(backend, '_tmux_run', None)
    if not callable(runner):
        checker = getattr(backend, 'is_alive', None)
        if not callable(checker):
            return False
        try:
            return bool(checker(session_name))
        except Exception:
            return False
    return bool(
        _wait_until_ready(
            lambda: _session_alive_once(backend, session_name),
            failure_message=f'failed to inspect tmux session {session_name!r}',
            timeout_s=timeout_s,
        )
    )


def session_root_pane(backend, session_name: str, *, timeout_s: float | None = None) -> str:
    return window_root_pane(backend, target_window=session_name, timeout_s=timeout_s)


def window_root_pane(backend, *, target_window: str, timeout_s: float | None = None) -> str:
    if _is_mux_backend(backend):
        _require_mux_operation(backend, operation='window_root_pane', methods=('window_root_pane',))
        namespace = _mux_namespace_ref(backend, session_name=_target_session_name(target_window))
        pane = backend.window_root_pane(
            namespace,
            window_name=_target_window_name(target_window) or _target_session_name(target_window),
        )
        return _remember_mux_pane_ref(backend, pane)
    pane_id = wait_for_root_pane(backend, target_window=target_window, timeout_s=timeout_s)
    if not pane_id.startswith('%'):
        raise RuntimeError(f'failed to resolve root pane for tmux target {target_window!r}')
    return pane_id


def split_pane(
    backend,
    *,
    target: str,
    direction: str,
    percent: int,
    project_root,
    timeout_s: float | None = None,
) -> str:
    if _is_mux_backend(backend):
        _require_mux_operation(backend, operation='split_pane', methods=('split_pane',))
        pane = _mux_pane_ref(backend, target)
        new_pane = backend.split_pane(
            pane,
            direction=direction,
            percent=max(1, min(99, int(percent))),
            command=None,
            cwd=str(project_root),
            env={},
            title='',
        )
        return _remember_mux_pane_ref(backend, new_pane)
    try:
        pane_id = backend.split_pane(
            target,
            direction=direction,
            percent=max(1, min(99, int(percent))),
            cmd=pane_placeholder_cmd(),
            cwd=str(project_root),
        )
    except TypeError:
        pane_id = backend.split_pane(
            target,
            direction,
            max(1, min(99, int(percent))),
        )
    if str(pane_id or '').startswith('%'):
        return str(pane_id)
    resolved = wait_for_root_pane(backend, target_window=target, timeout_s=timeout_s)
    if resolved.startswith('%'):
        return resolved
    raise RuntimeError(f'failed to split tmux pane from target {target!r}')


def respawn_pane(
    backend,
    *,
    pane_id: str,
    command: str,
    cwd: str,
    timeout_s: float | None = None,
) -> None:
    if _is_mux_backend(backend):
        _require_mux_operation(backend, operation='respawn_pane', methods=('respawn_pane',))
        from terminal_runtime.shell_launch import herdr_respawn_command
        backend.respawn_pane(
            _mux_pane_ref(backend, pane_id),
            command=herdr_respawn_command(
                command,
                Path(cwd),
                f'pane-{pane_id.replace(":", "_")}',
            ),
            cwd=str(cwd),
            env={},
        )
        return
    respawn = getattr(backend, 'respawn_pane', None)
    if callable(respawn):
        respawn(pane_id, cmd=command, cwd=str(cwd), remain_on_exit=True)
        return
    _tmux_run_ready(
        backend,
        ['respawn-pane', '-k', '-t', pane_id, 'sh', '-lc', command],
        failure_message=f'failed to respawn tmux pane {pane_id!r}',
        timeout_s=timeout_s,
    )


def kill_pane(backend, *, pane_id: str, timeout_s: float | None = None) -> None:
    if _is_mux_backend(backend):
        _require_mux_operation(backend, operation='kill_pane', methods=('kill_pane',))
        backend.kill_pane(_mux_pane_ref(backend, pane_id))
        return
    killer = getattr(backend, 'kill_pane', None)
    if callable(killer):
        try:
            killer(pane_id)
        except TypeError:
            killer(pane_id, timeout_s=timeout_s)
        return
    _tmux_run_ready(
        backend,
        ['kill-pane', '-t', pane_id],
        failure_message=f'failed to kill tmux pane {pane_id!r}',
        timeout_s=timeout_s,
    )


def move_pane(
    backend,
    *,
    source_pane: str,
    anchor_pane: str,
    direction: str,
    timeout_s: float | None = None,
) -> None:
    if _is_mux_backend(backend):
        _require_mux_operation(backend, operation='move_pane', methods=('move_pane',))
        backend.move_pane(
            _mux_pane_ref(backend, source_pane),
            _mux_pane_ref(backend, anchor_pane),
            direction=direction,
        )
        return
    flag = '-h' if direction == 'right' else '-v'
    _tmux_run_ready(
        backend,
        ['move-pane', flag, '-s', source_pane, '-t', anchor_pane],
        failure_message=f'failed to move tmux pane {source_pane!r}',
        timeout_s=timeout_s,
    )


def reflow_window(
    backend,
    *,
    session_name: str,
    window_name: str,
    target: str | None = None,
    timeout_s: float | None = None,
    prefer_topology_layout: bool = False,
) -> None:
    resolved_target = target or session_window_target(session_name, window_name)
    if _is_mux_backend(backend):
        _require_mux_operation(backend, operation='reflow_window', methods=('reflow_window',))
        namespace = _mux_namespace_ref(backend, session_name=session_name)
        backend.reflow_window(
            namespace,
            window_name=window_name,
            window_id=_target_window_name(resolved_target),
            target=resolved_target,
            prefer_topology_layout=prefer_topology_layout,
        )
        return
    _tmux_run_ready(
        backend,
        ['select-layout', '-E', '-t', resolved_target],
        failure_message=f'failed to reflow tmux window target {resolved_target!r}',
        timeout_s=timeout_s,
    )


def apply_pane_identity(
    backend,
    *,
    pane_id: str,
    title: str,
    agent_label: str,
    project_id: str,
    order_index: int | None = None,
    is_cmd: bool = False,
    role: str | None = None,
    slot_key: str | None = None,
    window_name: str | None = None,
    sidebar_instance: str | None = None,
    session_id: str | None = None,
    namespace_epoch: int | None = None,
    managed_by: str | None = None,
    provider_kind: str = "",
) -> None:
    if _is_mux_backend(backend):
        _require_mux_operation(backend, operation='set_pane_identity', methods=('set_pane_identity',))
        backend.set_pane_identity(
            _mux_pane_ref(backend, pane_id),
            title=title,
            agent_label=agent_label,
            project_id=project_id,
            order_index=order_index,
            is_cmd=is_cmd,
            role=role,
            slot_key=slot_key,
            window_name=window_name,
            sidebar_instance=sidebar_instance,
            session_id=session_id,
            namespace_epoch=namespace_epoch,
            managed_by=managed_by,
            provider_kind=provider_kind or None,
        )
        return
    from terminal_runtime.tmux_identity import apply_ccb_pane_identity

    apply_ccb_pane_identity(
        backend,
        pane_id,
        title=title,
        agent_label=agent_label,
        project_id=project_id,
        order_index=order_index,
        is_cmd=is_cmd,
        role=role,
        slot_key=slot_key,
        window_name=window_name,
        sidebar_instance=sidebar_instance,
        session_id=session_id,
        namespace_epoch=namespace_epoch,
        managed_by=managed_by,
    )


def kill_server(backend) -> bool:
    if _is_mux_backend(backend):
        namespace = _mux_namespace_ref_if_present(backend, session_name=None)
        if namespace is None:
            return False
        destroyer = getattr(backend, 'destroy_namespace', None)
        if callable(destroyer):
            _require_mux_operation(backend, operation='destroy_namespace', methods=('destroy_namespace',))
            destroyer(namespace)
            return True
        _require_mux_operation(backend, operation='kill_server', methods=('kill_server',))
        backend.kill_server(namespace)
        return True
    try:
        backend._tmux_run(['kill-server'], check=False, capture=True)  # type: ignore[attr-defined]
        import os
        import time
        socket_path = str(getattr(backend, '_socket_path', '') or getattr(backend, 'socket_path', '') or '').strip()
        if socket_path and os.path.exists(socket_path):
            for _ in range(30):
                if not os.path.exists(socket_path):
                    break
                time.sleep(0.1)
            try:
                if os.path.exists(socket_path):
                    os.unlink(socket_path)
            except OSError:
                pass
        return True
    except Exception:
        return False


def _is_mux_backend(backend) -> bool:
    capabilities = getattr(backend, 'capabilities', None)
    if not callable(capabilities):
        return False
    return not callable(getattr(backend, '_tmux_run', None))


def _require_mux_operation(
    backend,
    *,
    operation: str,
    methods: tuple[str, ...],
    require_any: bool = False,
) -> None:
    found = [name for name in methods if callable(getattr(backend, name, None))]
    if (require_any and not found) or (not require_any and len(found) != len(methods)):
        _raise_mux_unsupported(
            backend,
            operation=operation,
            detail=f'mux backend lacks required method for {operation}',
            evidence={'required_methods': list(methods), 'available_methods': found},
        )
    capabilities_fn = getattr(backend, 'capabilities', None)
    try:
        capabilities = capabilities_fn()
    except MuxCommandErrorV2:
        raise
    except Exception as exc:
        _raise_mux_unsupported(
            backend,
            operation=operation,
            detail=f'mux backend capabilities unavailable for {operation}: {exc}',
        )
    if not isinstance(capabilities, dict):
        _raise_mux_unsupported(backend, operation=operation, detail='mux backend capabilities must be an object')
    command_status = capabilities.get('command_status')
    semantic_status = capabilities.get('semantic_status')
    if not isinstance(command_status, dict) or not isinstance(semantic_status, dict):
        _raise_mux_unsupported(backend, operation=operation, detail='mux backend capability statuses are missing')
    keys = _capability_keys_for_operation(operation)
    missing = [key for key in keys if key not in command_status and key not in semantic_status]
    unsupported = [
        key
        for key in keys
        if str(command_status.get(key) or semantic_status.get(key) or '').strip() != 'supported'
    ]
    if missing or unsupported:
        _raise_mux_unsupported(
            backend,
            operation=operation,
            detail=f'mux backend capability unsupported for {operation}',
            evidence={'missing_capabilities': missing, 'unsupported_capabilities': unsupported},
        )


def _capability_keys_for_operation(operation: str) -> tuple[str, ...]:
    return {
        'prepare_server': ('session_attach',),
        'ensure_server_policy': ('session_attach',),
        'create_session': ('session_attach', 'workspace_create', 'workspace_metadata', 'pane_metadata'),
        'session_alive': ('session_attach', 'pane_list'),
        'list_windows': ('workspace_list', 'pane_list'),
        'create_window': (
            'workspace_list',
            'workspace_create',
            'workspace_focus',
            'pane_list',
            'workspace_metadata',
            'pane_metadata',
        ),
        'ensure_window': (
            'workspace_list',
            'workspace_create',
            'workspace_focus',
            'pane_list',
            'workspace_metadata',
            'pane_metadata',
        ),
        'window_root_pane': ('workspace_list', 'pane_list'),
        'split_pane': ('pane_list', 'pane_split', 'pane_run'),
        'kill_window': ('workspace_list', 'pane_list', 'workspace_close'),
        'rename_window': ('workspace_list', 'pane_list', 'workspace_metadata', 'pane_metadata'),
        'select_window': ('workspace_list', 'pane_list', 'workspace_focus'),
        'kill_server': ('workspace_list', 'pane_list', 'workspace_close'),
        'destroy_namespace': ('workspace_list', 'pane_list', 'workspace_close'),
        'kill_pane': ('kill_pane',),
        'move_pane': ('pane_list', 'pane_split'),
        'reflow_window': ('workspace_list', 'pane_list'),
        'respawn_pane': ('pane_list', 'pane_run'),
        'set_pane_identity': ('pane_list', 'pane_metadata'),
    }.get(operation, (operation,))


def _raise_mux_unsupported(
    backend,
    *,
    operation: str,
    detail: str,
    evidence: dict[str, object] | None = None,
) -> None:
    raise MuxCommandErrorV2(
        category='unsupported',
        backend_impl=str(getattr(backend, 'backend_impl', 'herdr') or 'herdr'),  # type: ignore[arg-type]
        operation=operation,
        detail=detail,
        evidence=evidence or {},
    )


def _remember_mux_namespace_ref(
    backend,
    namespace,
    *,
    requested_session_name: str | None = None,
) -> dict[str, object]:
    if not isinstance(namespace, dict):
        _raise_mux_unsupported(backend, operation='create_session', detail='mux create_session returned invalid namespace ref')
    ref = dict(namespace)
    setattr(backend, _MUX_NAMESPACE_REF_ATTR, ref)
    aliases: dict[str, dict[str, object]] = {}
    for name in (requested_session_name, ref.get('session_name')):
        clean_name = str(name or '').strip()
        if clean_name:
            aliases[clean_name] = dict(ref)
    if aliases:
        setattr(backend, _MUX_NAMESPACE_REF_ALIASES_ATTR, aliases)
    elif hasattr(backend, _MUX_NAMESPACE_REF_ALIASES_ATTR):
        delattr(backend, _MUX_NAMESPACE_REF_ALIASES_ATTR)
    return dict(ref)


def _mux_namespace_ref(backend, *, session_name: str) -> dict[str, object]:
    ref = _mux_namespace_ref_if_present(backend, session_name=session_name)
    if ref is not None:
        return ref
    namespace_builder = getattr(backend, 'namespace_ref', None)
    if callable(namespace_builder):
        namespace = namespace_builder(session_name, session_name)
        return _remember_mux_namespace_ref(backend, namespace, requested_session_name=session_name)
    _raise_mux_unsupported(
        backend,
        operation='namespace_ref',
        detail='mux namespace ref is missing; create_session must run before namespace operations',
        evidence={'session_name': session_name},
    )


def _mux_namespace_ref_if_present(backend, *, session_name: str | None) -> dict[str, object] | None:
    if session_name is not None:
        aliases = getattr(backend, _MUX_NAMESPACE_REF_ALIASES_ATTR, None)
        if isinstance(aliases, dict):
            alias = aliases.get(str(session_name or '').strip())
            if isinstance(alias, dict):
                return dict(alias)
    ref = getattr(backend, _MUX_NAMESPACE_REF_ATTR, None)
    if not isinstance(ref, dict):
        return None
    if session_name is not None and str(ref.get('session_name') or '').strip() != str(session_name or '').strip():
        return None
    return dict(ref)


def _mux_window_record(value) -> TmuxWindowRecord:
    if isinstance(value, TmuxWindowRecord):
        return value
    if isinstance(value, dict):
        return TmuxWindowRecord(
            window_id=_clean_optional(value.get('window_id') or value.get('id')),
            window_name=str(value.get('window_name') or value.get('name') or '').strip(),
            active=bool(value.get('active', False)),
        )
    return TmuxWindowRecord(
        window_id=_clean_optional(getattr(value, 'window_id', None) or getattr(value, 'id', None)),
        window_name=str(getattr(value, 'window_name', None) or getattr(value, 'name', '') or '').strip(),
        active=bool(getattr(value, 'active', False)),
    )


def _remember_mux_pane_ref(backend, pane) -> str:
    if isinstance(pane, dict):
        pane_id = str(pane.get('pane_id') or '').strip()
        if not pane_id:
            _raise_mux_unsupported(backend, operation='pane_ref', detail='mux pane ref is missing pane_id')
        refs = getattr(backend, _MUX_PANE_REFS_ATTR, None)
        if not isinstance(refs, dict):
            refs = {}
            setattr(backend, _MUX_PANE_REFS_ATTR, refs)
        refs[pane_id] = dict(pane)
        return pane_id
    pane_id = str(pane or '').strip()
    if not pane_id:
        _raise_mux_unsupported(backend, operation='pane_ref', detail='mux pane id cannot be empty')
    return pane_id


def _mux_pane_ref(backend, pane_id: str) -> dict[str, object]:
    refs = getattr(backend, _MUX_PANE_REFS_ATTR, None)
    if isinstance(refs, dict) and pane_id in refs and isinstance(refs[pane_id], dict):
        return dict(refs[pane_id])
    namespace = _mux_namespace_ref_if_present(backend, session_name=None)
    session_name = str(namespace.get('session_name') or '') if namespace is not None else ''
    if not session_name:
        _raise_mux_unsupported(
            backend,
            operation='pane_ref',
            detail='mux pane ref is missing; window_root_pane must run before split_pane',
            evidence={'pane_id': pane_id},
        )
    return {
        'backend_impl': str(getattr(backend, 'backend_impl', 'herdr') or 'herdr'),
        'pane_id': str(pane_id or '').strip(),
        'session_name': session_name,
        'window_name': None,
        'agent_slug': None,
    }


def _target_session_name(target: str) -> str:
    session_name, _sep, _window = str(target or '').partition(':')
    return session_name.strip()


def _target_window_name(target: str) -> str | None:
    _session, sep, window = str(target or '').partition(':')
    if not sep:
        return None
    return window.strip() or None


def _clean_optional(value: object) -> str | None:
    text = str(value or '').strip()
    return text or None


def wait_for_window(
    backend,
    *,
    session_name: str,
    window_name: str,
    timeout_s: float | None = None,
) -> TmuxWindowRecord | None:
    return _wait_until(
        lambda: find_window(backend, session_name=session_name, window_name=window_name, timeout_s=None),
        timeout_s=timeout_s,
        failure_message=f'failed to observe tmux window {window_name!r} for session {session_name!r}',
    )


def select_window(backend, *, target: str) -> None:
    if _is_mux_backend(backend):
        _require_mux_operation(backend, operation='select_window', methods=('select_window',))
        namespace = _mux_namespace_ref(backend, session_name=_target_session_name(target))
        backend.select_window(namespace, window_id=_target_window_name(target), target=target)
        return
    _wait_until_ready(
        lambda: _tmux_run_ready(
            backend,
            ['select-window', '-t', target],
            failure_message=f'failed to select tmux window target {target!r}',
            timeout_s=0.0,
        ),
        failure_message=f'failed to select tmux window target {target!r}',
    )


def wait_for_root_pane(backend, *, target_window: str, timeout_s: float | None = None) -> str:
    pane_id = _wait_until(
        lambda: _root_pane_once(backend, target_window=target_window),
        timeout_s=timeout_s,
        failure_message=f'failed to resolve root pane for tmux target {target_window!r}',
    )
    if pane_id is None:
        raise RuntimeError(f'failed to resolve root pane for tmux target {target_window!r}')
    return pane_id


def _root_pane_once(backend, *, target_window: str) -> str | None:
    result = _tmux_run_once(
        backend,
        ['list-panes', '-t', target_window, '-F', '#{pane_id}'],
    )
    if result is None:
        return None
    pane_id = ((result.stdout or '').splitlines() or [''])[0].strip()
    return pane_id or None


def _tmux_run_ready(
    backend,
    args: list[str],
    *,
    failure_message: str,
    timeout_s: float | None = None,
):
    return _wait_until_ready(
        lambda: _tmux_run_checked(backend, args),
        failure_message=failure_message,
        timeout_s=timeout_s,
    )


def _tmux_run_once(backend, args: list[str]):
    try:
        return _tmux_run_checked(backend, args)
    except TmuxTransientServerUnavailable:
        raise
    except Exception:
        return None


def _tmux_run_checked(backend, args: list[str]):
    result = backend._tmux_run(args, check=False, capture=True)  # type: ignore[attr-defined]
    if int(getattr(result, 'returncode', 1) or 0) == 0:
        return result
    detail = tmux_failure_detail(result, args)
    socket_path = str(getattr(backend, '_socket_path', '') or getattr(backend, 'socket_path', '') or '').strip() or None
    command = None
    tmux_base = getattr(backend, '_tmux_base', None)
    if callable(tmux_base):
        try:
            command = [*tmux_base(), *args]
        except Exception:
            command = None
    if is_tmux_transient_server_error_text(detail):
        raise TmuxTransientServerUnavailable(
            'tmux server unavailable',
            args=args,
            detail=detail,
            socket_path=socket_path,
            command=command,
        )
    raise TmuxCommandError(
        detail,
        args=args,
        detail=detail,
        socket_path=socket_path,
        command=command,
    )


def _wait_until(
    probe: Callable[[], object | None],
    *,
    timeout_s: float | None = None,
    failure_message: str | None = None,
):
    deadline = time.monotonic() + _tmux_object_ready_timeout_s(timeout_s)
    last_transient: TmuxTransientServerUnavailable | None = None
    while True:
        try:
            value = probe()
        except TmuxTransientServerUnavailable as exc:
            last_transient = exc
            value = None
        if value is not None:
            return value
        if time.monotonic() >= deadline:
            if last_transient is not None and failure_message:
                raise TmuxTransientServerUnavailable(failure_message) from last_transient
            return None
        time.sleep(_tmux_object_ready_poll_interval_s())


def _wait_until_ready(action: Callable[[], object], *, failure_message: str, timeout_s: float | None = None) -> object:
    deadline = time.monotonic() + _tmux_object_ready_timeout_s(timeout_s)
    last_error: Exception | None = None
    while True:
        try:
            return action()
        except Exception as exc:
            last_error = exc
        if time.monotonic() >= deadline:
            break
        time.sleep(_tmux_object_ready_poll_interval_s())
    if last_error is not None:
        detail = getattr(last_error, 'detail', None) or str(last_error)
        if isinstance(last_error, TmuxTransientServerUnavailable):
            raise TmuxTransientServerUnavailable(
                failure_message,
                detail=detail,
                socket_path=getattr(last_error, 'socket_path', None),
                command=getattr(last_error, 'command', None),
                args=getattr(last_error, 'tmux_args', None),
            ) from last_error
        raise RuntimeError(
            tmux_command_failure_message(
                failure_message,
                detail=detail,
                socket_path=getattr(last_error, 'socket_path', None),
                command=getattr(last_error, 'command', None),
                args=getattr(last_error, 'tmux_args', None),
            )
        ) from last_error
    raise RuntimeError(failure_message)


def _session_alive_once(backend, session_name: str) -> bool:
    result = backend._tmux_run(  # type: ignore[attr-defined]
        ['has-session', '-t', session_name],
        check=False,
        capture=True,
    )
    if int(getattr(result, 'returncode', 1) or 0) == 0:
        return True
    stderr = str(getattr(result, 'stderr', '') or '').strip()
    stdout = str(getattr(result, 'stdout', '') or '').strip()
    detail = stderr or stdout
    if is_tmux_absent_server_text(detail):
        return False
    if is_tmux_transient_server_error_text(detail):
        raise TmuxTransientServerUnavailable(detail)
    if not detail or is_tmux_missing_session_text(detail):
        return False
    raise RuntimeError(detail)


def _tmux_object_ready_timeout_s(timeout_s: float | None = None) -> float:
    return tmux_object_ready_timeout_s(timeout_s)


def _tmux_object_ready_poll_interval_s() -> float:
    return tmux_object_ready_poll_interval_s()


__all__ = [
    'build_backend',
    'apply_pane_identity',
    'create_session',
    'create_window',
    'ensure_server_policy',
    'ensure_window',
    'find_window',
    'kill_window',
    'kill_server',
    'kill_pane',
    'list_windows',
    'move_pane',
    'namespace_state_fields',
    'prepare_server',
    'rename_window',
    'reflow_window',
    'remember_namespace_state_ref',
    'respawn_pane',
    'session_alive',
    'session_root_pane',
    'session_window_target',
    'select_window',
    'split_pane',
    'TmuxCommandError',
    'TmuxTransientServerUnavailable',
    'TmuxWindowRecord',
    'wait_for_root_pane',
    'wait_for_window',
    'window_root_pane',
]
