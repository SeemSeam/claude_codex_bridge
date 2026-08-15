from __future__ import annotations

import sys
from pathlib import Path
from typing import TextIO
import json

from cli.ask_usage import write_ask_usage
from cli.auxiliary import cmd_droid_subcommand
from cli.management import cmd_install, cmd_reinstall, cmd_uninstall, cmd_update, cmd_version
from cli.management_runtime.commands_runtime.update import maybe_handle_post_update_command
from cli.management_runtime.startup_update import (
    maybe_handle_background_update_refresh_command,
    maybe_handle_startup_release_update,
)
from cli.phase2 import maybe_handle_phase2
from cli.parser_runtime.constants import SUBCOMMANDS
from cli.router import dispatch_auxiliary_command, dispatch_management_command, print_command_help, print_kill_help, print_start_help
from cli.roles_runtime import cmd_roles
from cli.services.theme import cmd_theme
from cli.services.mobile_host import maybe_handle_mobile_host_serve_command
from cli.sidebar_click import maybe_handle_sidebar_click_command
from cli.sidebar_resize_sync import maybe_handle_sidebar_resize_sync_command
from cli.tools_runtime import cmd_tools
from cli.tools_runtime.workbench import (
    cmd_rich,
    disable_workbench,
    launch_rich_ccb,
    print_workbench_status,
    rich_auto_start_allowed,
    uninstall_workbench,
)
from ccbd.desktop_api import build_discovery


def _should_print_version(tokens: list[str]) -> bool:
    return "--print-version" in tokens


def _is_ask_help(tokens: list[str]) -> bool:
    visible = _strip_global_project_tokens(tokens)
    return len(visible) >= 2 and visible[0] == "ask" and visible[1] in {"-h", "--help", "help"}


def _is_kill_help(tokens: list[str]) -> bool:
    visible = _strip_global_project_tokens(tokens)
    return len(visible) >= 2 and visible[0] == "kill" and visible[1] in {"-h", "--help", "help"}


def _is_start_help(tokens: list[str]) -> bool:
    tokens = _strip_global_project_tokens(tokens)
    if not tokens:
        return False
    if tokens[0] in {"-h", "--help", "help"}:
        return True
    if tokens[0] in SUBCOMMANDS or tokens[0] in {"install", "version", "update", "uninstall", "reinstall", "droid", "tools", "roles", "mail", "provider", "up", "rich", "rich-install", "theme"}:
        return False
    return any(token in {"-h", "--help", "help"} for token in tokens)


def _command_help_name(tokens: list[str]) -> str | None:
    visible = _strip_global_project_tokens(tokens)
    if not visible:
        return None
    help_tokens = {"-h", "--help", "help"}
    if not any(token in help_tokens for token in visible[1:]):
        return None
    if len(visible) >= 2 and visible[1] in help_tokens:
        return visible[0]
    if len(visible) >= 2:
        if visible[0] == "doctor" and visible[1] in {"ps", "--runtime"}:
            return "doctor-ps"
        if visible[0] == "doctor" and visible[1] in {"logs", "--logs"}:
            return "doctor-logs"
        if visible[0] == "doctor" and visible[1] == "storage":
            return "doctor-storage"
        if visible[0] == "repair" and visible[1] == "ack":
            return "repair-ack"
        if visible[0] == "repair" and visible[1] == "retry":
            return "repair-retry"
        if visible[0] == "repair" and visible[1] == "resubmit":
            return "repair-resubmit"
    return visible[0]


def _strip_global_project_tokens(tokens: list[str]) -> list[str]:
    remaining = list(tokens)
    while remaining[:1] == ["--project"] and len(remaining) >= 2:
        remaining = remaining[2:]
    return remaining


def _rewrite_version_alias(tokens: list[str]) -> list[str]:
    if tokens and tokens[0] in {"-v", "--version"}:
        return ["version"]
    return tokens


def _write_removed_command_error(stderr: TextIO, *, command: str, guidance: str) -> int:
    print(f"❌ `ccb {command}` has been removed.", file=stderr)
    print(guidance, file=stderr)
    return 2


def _handle_help(tokens: list[str], *, stdout: TextIO) -> int | None:
    if _is_ask_help(tokens):
        write_ask_usage(stdout, command_name="ccb ask")
        return 0
    if _is_kill_help(tokens):
        print_kill_help(file=stdout)
        return 0
    command_name = _command_help_name(tokens)
    if command_name is not None and print_command_help(command_name, file=stdout):
        return 0
    if _is_start_help(tokens):
        print_start_help(file=stdout)
        return 0
    return None


def _handle_removed_commands(tokens: list[str], *, stderr: TextIO) -> int | None:
    if tokens and tokens[0] == "open":
        print("❌ The standalone attach command has been removed.", file=stderr)
        print("💡 Use: ccb", file=stderr)
        return 2

    if tokens and tokens[0] == "up":
        print("❌ `ccb up` is no longer supported.", file=stderr)
        print("💡 Use: ccb  (agents are configured by .ccb/ccb.config)", file=stderr)
        return 2

    if tokens and tokens[0] in {"mail", "provider"}:
        return _write_removed_command_error(
            stderr,
            command=tokens[0],
            guidance="💡 Use `ccb ask` for task submission/results, `ccb doctor` for diagnostics, and `ccb trace` for lineage details.",
        )
    if tokens and tokens[0] == "rich-install":
        return _write_removed_command_error(
            stderr,
            command="rich-install",
            guidance="💡 Use: ccb update rich",
        )
    return None


def _dispatch_desktop_discover(
    tokens: list[str],
    *,
    version: str,
    stdout: TextIO,
    stderr: TextIO,
) -> int | None:
    if not tokens or tokens[0] != 'desktop-discover':
        return None
    project: str | None = None
    json_requested = False
    index = 1
    while index < len(tokens):
        token = tokens[index]
        if token == '--json':
            if json_requested:
                print('desktop-discover: duplicate --json', file=stderr)
                return 2
            json_requested = True
            index += 1
            continue
        if token == '--project' and index + 1 < len(tokens):
            if project is not None:
                print('desktop-discover: duplicate --project', file=stderr)
                return 2
            project = tokens[index + 1]
            index += 2
            continue
        if token.startswith('--project=') and project is None:
            project = token.split('=', 1)[1]
            index += 1
            continue
        print('desktop-discover: expected --project <absolute-root> --json', file=stderr)
        return 2
    if not json_requested or project is None:
        print('usage: ccb desktop-discover --project <canonical-root> --json', file=stderr)
        return 2
    payload = build_discovery(project, ccb_version=version)
    print(json.dumps(payload, ensure_ascii=False, sort_keys=True), file=stdout)
    # A valid project with a stopped/degraded runtime is still a successful
    # discovery result; the structured diagnostics tell Desktop why its
    # endpoint/capabilities are unavailable.  Invalid project identity remains
    # a command failure.
    return 0 if payload.get('project_id') else 1


def _dispatch_auxiliary(tokens: list[str], *, script_root: Path) -> int | None:
    return dispatch_auxiliary_command(
        tokens,
        droid_handler=lambda args: cmd_droid_subcommand(list(args), script_root=script_root),
    )


def _dispatch_management(tokens: list[str], *, script_root: Path) -> int | None:
    if not (tokens and tokens[0] in {"install", "version", "update", "uninstall", "reinstall"}):
        return None

    return dispatch_management_command(
        tokens,
        install_handler=lambda args: cmd_install(args, script_root=script_root),
        update_handler=lambda args: cmd_update(args, script_root=script_root),
        version_handler=lambda args: cmd_version(args, script_root=script_root),
        uninstall_handler=lambda args: cmd_uninstall(args, script_root=script_root),
        reinstall_handler=lambda args: cmd_reinstall(args, script_root=script_root),
    )


def _dispatch_tools(tokens: list[str], *, script_root: Path, stdout: TextIO, stderr: TextIO) -> int | None:
    if not (tokens and tokens[0] == 'tools'):
        return None
    return cmd_tools(tokens[1:], script_root=script_root, stdout=stdout, stderr=stderr)


def _dispatch_theme(tokens: list[str], *, stdout: TextIO, stderr: TextIO) -> int | None:
    if not (tokens and tokens[0] == 'theme'):
        return None
    return cmd_theme(tokens[1:], stdout=stdout, stderr=stderr)


def _dispatch_rich(tokens: list[str], *, script_root: Path, cwd: Path, stdout: TextIO, stderr: TextIO) -> int | None:
    if not (tokens and tokens[0] == 'rich'):
        return None
    if len(tokens) > 1:
        action = tokens[1]
        if action in {'-h', '--help', 'help'}:
            _print_rich_usage(stdout)
            return 0
        if action in {'uninstall', 'remove'} and len(tokens) == 2:
            result = uninstall_workbench(profile='rich', remove_cache=False)
            print_workbench_status(result, stdout)
            return 0 if result.get('status') in {'ok', 'missing'} else 1
        if action in {'disable', 'off'} and len(tokens) == 2:
            result = disable_workbench(profile='rich', close=True)
            print_workbench_status(result, stdout)
            return 0 if result.get('status') in {'ok', 'degraded', 'missing'} else 1
        _print_rich_usage(stdout)
        return 2
    return cmd_rich(script_root=script_root, cwd=cwd, stdout=stdout, stderr=stderr)


def _print_rich_usage(stdout: TextIO) -> None:
    print('usage: ccb rich [uninstall|disable]', file=stdout)


def _tokens_are_start_command(tokens: list[str]) -> bool:
    visible = _strip_global_project_tokens(tokens)
    if not visible:
        return True
    allowed = {'-s', '--safe', '-n', '--new-context'}
    return all(token in allowed for token in visible)


def _dispatch_auto_rich_start(tokens: list[str], *, script_root: Path, cwd: Path, stdout: TextIO, stderr: TextIO) -> int | None:
    if not _tokens_are_start_command(tokens):
        return None
    if not rich_auto_start_allowed():
        return None
    result = launch_rich_ccb(script_root=script_root, cwd=cwd, start_args=tokens)
    print_workbench_status(result, stdout)
    if result.get('status') not in {'ok', 'degraded'}:
        if result.get('reason'):
            print(f"ERROR: {result['reason']}", file=stderr)
        return 1
    return 0 if result.get('launch_status') == 'started' else 1


def _dispatch_roles(tokens: list[str], *, script_root: Path, cwd: Path, stdout: TextIO, stderr: TextIO) -> int | None:
    if not (tokens and tokens[0] == 'roles'):
        return None
    return cmd_roles(tokens[1:], script_root=script_root, cwd=cwd, stdout=stdout, stderr=stderr)


def _log_received_argv(tokens: list[str], *, stderr: TextIO) -> None:
    import os
    if os.environ.get('CCB_DEBUG_ARGV') not in {'1', 'true', 'yes', 'on'}:
        return
    print(f'[ccb8 argv] {tokens!r}', file=stderr)


def run_cli_entrypoint(
    argv: list[str],
    *,
    version: str,
    script_root: Path,
    cwd: Path,
    stdout: TextIO,
    stderr: TextIO,
) -> int:
    tokens = list(argv or [])
    _log_received_argv(tokens, stderr=stderr)
    if _should_print_version(tokens):
        print(f"v{version}", file=stdout)
        return 0
    sidebar_click_result = maybe_handle_sidebar_click_command(tokens, stderr=stderr)
    if sidebar_click_result is not None:
        return sidebar_click_result
    sidebar_resize_sync_result = maybe_handle_sidebar_resize_sync_command(tokens, stderr=stderr)
    if sidebar_resize_sync_result is not None:
        return sidebar_resize_sync_result
    internal_result = maybe_handle_background_update_refresh_command(tokens, script_root=script_root)
    if internal_result is not None:
        return internal_result
    internal_result = maybe_handle_mobile_host_serve_command(tokens, script_root=script_root)
    if internal_result is not None:
        return internal_result
    internal_result = maybe_handle_post_update_command(tokens, script_root=script_root)
    if internal_result is not None:
        return internal_result

    help_result = _handle_help(tokens, stdout=stdout)
    if help_result is not None:
        return help_result

    tokens = _rewrite_version_alias(tokens)

    desktop_discover_result = _dispatch_desktop_discover(
        tokens,
        version=version,
        stdout=stdout,
        stderr=stderr,
    )
    if desktop_discover_result is not None:
        return desktop_discover_result

    removed_result = _handle_removed_commands(tokens, stderr=stderr)
    if removed_result is not None:
        return removed_result

    auxiliary_result = _dispatch_auxiliary(tokens, script_root=script_root)
    if auxiliary_result is not None:
        return auxiliary_result

    management_result = _dispatch_management(tokens, script_root=script_root)
    if management_result is not None:
        return management_result

    rich_result = _dispatch_rich(tokens, script_root=script_root, cwd=cwd, stdout=stdout, stderr=stderr)
    if rich_result is not None:
        return rich_result

    tools_result = _dispatch_tools(tokens, script_root=script_root, stdout=stdout, stderr=stderr)
    if tools_result is not None:
        return tools_result

    theme_result = _dispatch_theme(tokens, stdout=stdout, stderr=stderr)
    if theme_result is not None:
        return theme_result

    roles_result = _dispatch_roles(tokens, script_root=script_root, cwd=cwd, stdout=stdout, stderr=stderr)
    if roles_result is not None:
        return roles_result

    auto_rich_result = _dispatch_auto_rich_start(tokens, script_root=script_root, cwd=cwd, stdout=stdout, stderr=stderr)
    if auto_rich_result is not None:
        return auto_rich_result

    startup_update_result = maybe_handle_startup_release_update(
        tokens,
        script_root=script_root,
        cwd=cwd,
        stdout=stdout,
        stderr=stderr,
        stdin=sys.stdin,
    )
    if startup_update_result is not None:
        return startup_update_result

    return maybe_handle_phase2(tokens, cwd=cwd, stdout=stdout, stderr=stderr)
__all__ = ["run_cli_entrypoint"]
