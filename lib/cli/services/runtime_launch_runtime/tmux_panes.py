from __future__ import annotations

import os
import time
from pathlib import Path

from terminal_runtime.env import tmux_history_limit
from terminal_runtime.placeholders import pane_placeholder_argv

_TMUX_ENVIRONMENT_KEYS = (
    'TERM',
    'TERM_PROGRAM',
    'TERM_PROGRAM_VERSION',
    'DISPLAY',
    'WAYLAND_DISPLAY',
    'XDG_RUNTIME_DIR',
    'WSL_DISTRO_NAME',
    'WSL_INTEROP',
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
_PREPARED_DETACHED_TMUX_SERVER_KEYS: set[tuple[object, ...]] = set()
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


def launch_pane(
    backend,
    *,
    spec_name: str,
    assigned_pane_id: str | None,
    start_cmd: str,
    run_cwd: Path,
    create_detached_tmux_pane_fn,
    pane_meets_minimum_size_fn,
    best_effort_kill_tmux_pane_fn,
    allow_detached_fallback: bool,
) -> str:
    if assigned_pane_id:
        pane_id = str(assigned_pane_id)
        backend.respawn_pane(
            pane_id,
            cmd=start_cmd,
            cwd=str(run_cwd),
            remain_on_exit=True,
        )
        return pane_id
    if not allow_detached_fallback:
        raise RuntimeError(
            f'project namespace launch requires assigned tmux pane for {spec_name}'
        )
    return allocate_fresh_pane(
        backend,
        spec_name=spec_name,
        start_cmd=start_cmd,
        run_cwd=run_cwd,
        create_detached_tmux_pane_fn=create_detached_tmux_pane_fn,
        pane_meets_minimum_size_fn=pane_meets_minimum_size_fn,
        best_effort_kill_tmux_pane_fn=best_effort_kill_tmux_pane_fn,
        allow_detached_fallback=allow_detached_fallback,
    )


def allocate_fresh_pane(
    backend,
    *,
    spec_name: str,
    start_cmd: str,
    run_cwd: Path,
    create_detached_tmux_pane_fn,
    pane_meets_minimum_size_fn,
    best_effort_kill_tmux_pane_fn,
    allow_detached_fallback: bool,
) -> str:
    try:
        pane_id = backend.create_pane(start_cmd, str(run_cwd))
    except Exception as exc:
        if not should_fallback_to_detached_session(exc):
            raise
        return detached_pane(
            backend,
            spec_name=spec_name,
            start_cmd=start_cmd,
            run_cwd=run_cwd,
            create_detached_tmux_pane_fn=create_detached_tmux_pane_fn,
        )
    if pane_meets_minimum_size_fn(backend, pane_id):
        return pane_id
    best_effort_kill_tmux_pane_fn(backend, pane_id)
    if not allow_detached_fallback:
        raise RuntimeError(
            f'project namespace launch could not allocate stable tmux pane for {spec_name}'
        )
    return detached_pane(
        backend,
        spec_name=spec_name,
        start_cmd=start_cmd,
        run_cwd=run_cwd,
        create_detached_tmux_pane_fn=create_detached_tmux_pane_fn,
    )


def detached_pane(
    backend,
    *,
    spec_name: str,
    start_cmd: str,
    run_cwd: Path,
    create_detached_tmux_pane_fn,
) -> str:
    return create_detached_tmux_pane_fn(
        backend,
        cmd=start_cmd,
        cwd=run_cwd,
        session_name=f'ccb-{spec_name}',
    )


def prepare_detached_tmux_server(backend) -> None:
    cache_key = _detached_tmux_server_prepare_key(backend)
    if cache_key in _PREPARED_DETACHED_TMUX_SERVER_KEYS:
        return

    prepared = True
    prepared = best_effort_tmux_run(backend, ['set-option', '-g', 'destroy-unattached', 'off']) and prepared
    prepared = best_effort_tmux_run(backend, ['set-option', '-g', 'mouse', 'on']) and prepared
    prepared = best_effort_tmux_run(
        backend,
        ['set-option', '-g', 'history-limit', str(tmux_history_limit())],
    ) and prepared
    prepared = best_effort_tmux_run(backend, ['set-option', '-g', 'set-clipboard', 'on']) and prepared
    prepared = best_effort_tmux_run(backend, ['set-option', '-g', 'focus-events', 'on']) and prepared
    prepared = best_effort_tmux_run(backend, ['set-option', '-g', 'escape-time', '10']) and prepared
    prepared = best_effort_tmux_run(backend, ['set-option', '-g', 'allow-passthrough', 'on']) and prepared
    prepared = _best_effort_tmux_environment_policy(backend) and prepared
    prepared = best_effort_tmux_run(backend, ['set-window-option', '-g', 'mode-keys', 'vi']) and prepared
    prepared = best_effort_tmux_run(backend, ['bind-key', '-T', 'copy-mode-vi', 'v', 'send-keys', '-X', 'begin-selection']) and prepared
    prepared = best_effort_tmux_run(backend, ['bind-key', '-T', 'copy-mode-vi', 'C-v', 'send-keys', '-X', 'rectangle-toggle']) and prepared
    for key in ('y', 'Enter', 'MouseDragEnd1Pane'):
        prepared = best_effort_tmux_run(
            backend,
            ['bind-key', '-T', 'copy-mode-vi', key, 'send-keys', '-X', 'copy-pipe-and-cancel', _CLIPBOARD_PIPE_COMMAND],
        ) and prepared
    for key, direction in (('h', '-L'), ('j', '-D'), ('k', '-U'), ('l', '-R')):
        prepared = best_effort_tmux_run(backend, ['bind-key', key, 'select-pane', direction]) and prepared
    for key, direction in (('H', '-L'), ('J', '-D'), ('K', '-U'), ('L', '-R')):
        prepared = best_effort_tmux_run(backend, ['bind-key', '-r', key, 'resize-pane', direction, '5']) and prepared


    if prepared:
        _PREPARED_DETACHED_TMUX_SERVER_KEYS.add(cache_key)


def _detached_tmux_server_prepare_key(backend) -> tuple[object, ...]:
    socket_path = str(getattr(backend, 'socket_path', '') or '').strip()
    socket_name = str(getattr(backend, 'socket_name', '') or '').strip()
    socket_key = ('path', socket_path) if socket_path else ('name', socket_name or '<default>')
    env_key = tuple((key, os.environ.get(key) or '') for key in _TMUX_ENVIRONMENT_KEYS)
    return (*socket_key, env_key, ('history-limit', tmux_history_limit()))


def best_effort_tmux_run(backend, argv: list[str]) -> bool:
    try:
        result = backend._tmux_run(argv, check=False)  # type: ignore[attr-defined]
    except Exception:
        return False
    return int(getattr(result, 'returncode', 0) or 0) == 0


def _best_effort_tmux_environment_policy(backend) -> bool:
    prepared = best_effort_tmux_run(backend, ['set-option', '-g', 'update-environment', ' '.join(_TMUX_ENVIRONMENT_KEYS)])
    for key in _TMUX_ENVIRONMENT_KEYS:
        value = os.environ.get(key)
        if value:
            prepared = best_effort_tmux_run(backend, ['set-environment', '-g', key, value]) and prepared
    return prepared


def create_detached_tmux_pane(backend, *, cmd: str, cwd: Path, session_name: str) -> str:
    target_session = f'{session_name}-{int(time.time() * 1000)}-{os.getpid()}'
    backend._tmux_run(  # type: ignore[attr-defined]
        ['new-session', '-d', '-x', '160', '-y', '48', '-s', target_session, '-c', str(cwd), *pane_placeholder_argv()],
        check=True,
    )
    prepare_detached_tmux_server(backend)
    result = backend._tmux_run(  # type: ignore[attr-defined]
        ['list-panes', '-t', target_session, '-F', '#{pane_id}'],
        capture=True,
        check=True,
    )
    pane_id = ((result.stdout or '').splitlines() or [''])[0].strip()
    if not pane_id:
        raise RuntimeError(
            f'failed to create detached tmux pane for session {target_session}'
        )
    backend.respawn_pane(pane_id, cmd=cmd, cwd=str(cwd), remain_on_exit=True)
    return pane_id


def pane_meets_minimum_size(
    backend,
    pane_id: str,
    *,
    min_width: int = 20,
    min_height: int = 8,
) -> bool:
    dimensions = pane_dimensions(backend, pane_id)
    if dimensions is None:
        return True
    width, height = dimensions
    return width >= min_width and height >= min_height


def pane_dimensions(backend, pane_id: str) -> tuple[int, int] | None:
    try:
        result = backend._tmux_run(  # type: ignore[attr-defined]
            ['display-message', '-p', '-t', pane_id, '#{pane_width}x#{pane_height}'],
            capture=True,
            check=True,
        )
    except Exception:
        return None
    raw = (result.stdout or '').strip().lower()
    try:
        width_text, height_text = raw.split('x', 1)
        width = int(width_text)
        height = int(height_text)
    except Exception:
        return None
    return width, height


def best_effort_kill_tmux_pane(backend, pane_id: str) -> None:
    try:
        backend.kill_tmux_pane(pane_id)
        return
    except Exception:
        pass
    try:
        backend._tmux_run(['kill-pane', '-t', pane_id], check=False)  # type: ignore[attr-defined]
    except Exception:
        pass


def should_fallback_to_detached_session(exc: Exception) -> bool:
    text = str(exc).strip().lower()
    return 'split-window failed' in text or 'no space for new pane' in text


__all__ = [
    'best_effort_kill_tmux_pane',
    'create_detached_tmux_pane',
    'launch_pane',
    'pane_meets_minimum_size',
    'prepare_detached_tmux_server',
]
