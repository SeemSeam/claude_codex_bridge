"""Native entry handlers. Shared CLI parsing and source guards remain authoritative."""
from __future__ import annotations

import os
import sys

from cli import phase2
from cli.phase2_runtime import handlers_start as shared_start
from cli.phase2_runtime.handlers_start import (
    _ccbd_herdr_session_name, _print_herdr_daemon_conflict,
    _stream_is_tty, _env_truthy,
)
from cli.services import start_foreground as foreground
from cli.services.start_foreground import ForegroundAttachError
from platforms.windows.herdr.cli_entrypoint import run_native_cli_entrypoint


def _native_phase2(argv, *, cwd=None, stdout=None, stderr=None):
    out, err = stdout or sys.stdout, stderr or sys.stderr
    command = phase2.parse_phase2_command(
        argv, config_command=phase2._looks_like_config_validate(argv), err=err)
    if command is None:
        return 2
    if command.kind not in {'herdr-open', 'start'}:
        return phase2.maybe_handle_phase2(argv, cwd=cwd, stdout=out, stderr=err)
    try:
        if (not getattr(command, 'no_attach', False) and not _env_truthy('CCB_NO_ATTACH')
                and _stream_is_tty(sys.stdin) and _stream_is_tty(out)):
            from platforms.windows.terminal_health import check_before_attach
            check_before_attach(err)
        context = phase2._build_context(command, cwd=cwd, out=out)
        phase2.ensure_bootstrap_project_config(context.project.project_root)
        return _native_dispatch(context, command, out)
    except Exception as exc:
        return phase2.handle_phase2_exception(err, command_kind=command.kind, exc=exc)


def _native_dispatch(context, command, out):
    services = phase2._dispatch_services()
    if command.kind == 'herdr-open':
        return handle_herdr_open(context, command, out, services)
    if command.kind == 'start':
        return handle_start(context, command, out, services)
    return phase2._dispatch_impl(context, command, out, services)


def handle_start(context, command, out, services):
    shared_start._ensure_project_commands_approved(context, out, services)
    shared_start._ensure_herdr_runtime_evidence(context)
    interactive = (not _env_truthy('CCB_NO_ATTACH')
                   and _stream_is_tty(sys.stdin) and _stream_is_tty(out))
    size = shared_start._terminal_size_for_streams(out, sys.stdin) if interactive else None
    if size is not None:
        summary = services.start_agents(context, command, terminal_size=size)
    else:
        summary = services.start_agents(context, command)
    if interactive:
        attach_started_project_namespace(context)
    else:
        services.write_lines(out, services.render_start(summary))
    return 0


def attach_started_project_namespace(context):
    client = foreground._foreground_attach_client(context)
    payload = foreground._wait_for_attach_target(client, env=foreground._attach_env())
    if foreground._payload_backend_impl(payload) != 'herdr':
        return foreground.attach_started_project_namespace(context)
    return _attach_herdr_project_namespace(context, payload)


def _attach_herdr_project_namespace(context, payload):
    namespace_ref = foreground._herdr_namespace_ref_from_payload(payload)
    backend_selection = foreground._herdr_backend_selection_from_payload(payload)
    projection_detail = foreground._herdr_surface_projection_detail(payload)
    backend = foreground._build_herdr_attach_backend(
        namespace_ref=namespace_ref,
        backend_selection=backend_selection,
    )
    attach = getattr(backend, 'attach_namespace', None)
    if not callable(attach):
        raise ForegroundAttachError(
            'foreground attach failed: Herdr backend does not support attach_namespace '
            f'(backend_impl={backend_selection.get("backend_impl")}, ipc_kind={namespace_ref.get("ipc_kind")})'
            f'{projection_detail}'
        )
    # Reuse the invoking terminal. A second detached UI both hides errors
    # and leaves the original foreground connection racing for input.
    try:
        attach(namespace_ref, window_name=foreground._clean_optional_payload_text(payload.get('namespace_workspace_window_name')))
    except Exception as exc:
        raise ForegroundAttachError(
            'foreground attach failed: Herdr attach_namespace failed '
            f'(backend_impl={backend_selection.get("backend_impl")}, ipc_kind={namespace_ref.get("ipc_kind")}, '
            f'ipc_ref_present={bool(namespace_ref.get("ipc_ref"))}, detail={exc})'
            f'{projection_detail}'
        ) from exc
    return foreground._foreground_attach_summary_from_payload(context, payload)


def handle_herdr_open(context, command, out, services) -> int:
    # Serialize check/start across repeated clicks, but never hold the lock
    # for the lifetime of the interactive terminal.
    from platforms.windows.herdr.bootstrap import project_open_lock

    project_root = getattr(getattr(context, 'project', None), 'project_root', None)
    with project_open_lock(project_root):
        rc = _prepare_herdr_open(context, command, out, services)
    if rc == 0 and not command.no_attach and not _env_truthy('CCB_NO_ATTACH'):
        if _stream_is_tty(sys.stdin) and _stream_is_tty(out):
            attach_started_project_namespace(context)
    return rc


def _open_session(context, requested, *, running):
    requested = str(requested or '').strip() or None
    if not running:
        session = _ccbd_herdr_session_name(context)
        if requested and requested != session:
            raise RuntimeError(
                f'This native project requires Herdr session {session!r}; '
                'custom session names are not supported by the daemon. '
                'Omit --herdr-session to use the project session.'
            )
        return session
    from ccbd.services.project_namespace_state_runtime.stores import ProjectNamespaceStateStore

    state = ProjectNamespaceStateStore(context.paths).load()
    recorded = str(getattr(state, 'namespace_session_name', '') or '').strip()
    if not recorded:
        raise RuntimeError('Cannot reconnect: the live daemon has no recorded Herdr session.')
    if requested and requested != recorded:
        raise RuntimeError(
            f'This project already uses Herdr session {recorded!r}; '
            'omit --herdr-session to reconnect to that session.'
        )
    return recorded


def _prepare_herdr_open(context, command, out, services) -> int:
    """``ccb herdr open`` — WezTerm-launched Herdr managed startup bootstrap.

    Locates Herdr, starts the project server for a cold open, injects
    the herdr runtime env, then starts agents through the herdr backend
    (managed mode; CCB stays the provider/recovery authority). Foreground
    attach by default; ``--no-attach`` starts headless.  ``--wait-ready`` blocks
    until ccbd is mounted (replacing the ``ccb8.ps1`` lifecycle.json poll).
    """
    from cli.models_start import ParsedStartCommand
    from platforms.windows.herdr.bootstrap import ensure_herdr_bootstrap_env

    running, backend = _daemon_running_and_backend(context)
    if running and backend != 'herdr':
        _print_herdr_daemon_conflict(backend)
        return 1
    # The daemon creates its namespace in the project-derived session. A live
    # daemon must reconnect to its recorded server without creating a new one.
    session = _open_session(context, command.herdr_session, running=running)
    result = ensure_herdr_bootstrap_env(
        herdr_exe=command.herdr_exe,
        herdr_session=session,
        auto_start_server=not running,
        start_session=session,
    )
    if result.get('ok') is not True:
        print(str(result.get('reason') or 'herdr open failed'), file=sys.stderr)
        return 1
    for warning in (result.get('warnings') or ()):
        print(f'Warning: {warning}', file=sys.stderr)
    start_command = ParsedStartCommand(
        project=command.project,
        agent_names=(),
        restore=True,
        auto_permission=True,
    )
    previous_no_attach = os.environ.get('CCB_NO_ATTACH')
    os.environ['CCB_NO_ATTACH'] = '1'
    try:
        if running:
            print('mode: reconnect (existing agents preserved)', file=out)
            rc = 0
        else:
            rc = handle_start(context, start_command, out, services)
    finally:
        if previous_no_attach is None:
            os.environ.pop('CCB_NO_ATTACH', None)
        else:
            os.environ['CCB_NO_ATTACH'] = previous_no_attach
    if rc == 0 and (command.wait_ready or running):
        ready, phase = _wait_for_ccbd_mounted(context)
        if not ready:
            print(
                f'ccb herdr open: ccbd not ready after waiting (phase={phase}); '
                'check `ccb ping` and the keeper/lifecycle state.',
                file=sys.stderr,
            )
            return 1
    return rc


def _wait_for_ccbd_mounted(
    context,
    *,
    timeout_s: float = 90.0,
    sleep_s: float = 2.0,
) -> tuple[bool, str]:
    """Require a live, responsive namespace; a mounted state file is insufficient."""
    import time

    from ccbd.services.lifecycle import CcbdLifecycleStore
    from ccbd.socket_client import CcbdClient, CcbdClientError

    deadline = time.monotonic() + timeout_s
    lifecycle = None
    while time.monotonic() < deadline:
        try:
            lifecycle = CcbdLifecycleStore(context.paths).load()
        except Exception:
            lifecycle = None
        if lifecycle is not None and lifecycle.phase == 'mounted':
            try:
                remaining = max(0.1, deadline - time.monotonic())
                payload = CcbdClient(context.paths.ccbd_socket_path, timeout_s=min(3.0, remaining)).ping('ccbd')
                if (payload.get('mount_state') == 'mounted'
                        and payload.get('namespace_backend_impl') == 'herdr'
                        and payload.get('namespace_ui_attachable') is True):
                    return True, 'mounted'
            except (CcbdClientError, OSError):
                pass
        time.sleep(sleep_s)
    phase = str(getattr(lifecycle, 'phase', '') or '').strip() or 'unknown'
    return False, phase


def _daemon_running_and_backend(context):
    """Return ``(running, backend_impl)`` for the current project's daemon.

    ``running`` reflects a live daemon PID, even while its worker is busy;
    ``backend_impl`` is the recorded namespace backend (``herdr``/``tmux``) or
    None when unknown or when no namespace state exists.
    """
    try:
        from cli.services.daemon import inspect_daemon

        _manager, _guard, inspection = inspect_daemon(context)
    except (ImportError, ModuleNotFoundError):
        # herdr/daemon module genuinely unavailable — safe to say "not running"
        return False, None
    except Exception as exc:
        # Inspection raised unexpectedly — daemon state unknown.
        # DEC-3: fail-closed.  Treat as a potential daemon conflict so the
        # user is warned rather than silently proceeding into a collision.
        import sys
        print(
            f"ccb herdr open: daemon inspection failed ({exc}); "
            "treating as potential conflict — stop any running CCB session "
            "(`ccb kill`) before retrying.",
            file=sys.stderr,
        )
        return True, None
    # A temporary socket timeout must not authorize another start/restore.
    # Reconnect waits for a responsive namespace before reporting success.
    running = bool(getattr(inspection, 'pid_alive', False))
    if not running:
        return False, None
    try:
        from ccbd.services.project_namespace_state_runtime.stores import (
            ProjectNamespaceStateStore,
        )

        state = ProjectNamespaceStateStore(context.paths).load()
    except Exception:
        return True, None
    if state is None:
        return True, None
    return True, str(getattr(state, 'backend_impl', '') or '').strip() or None
