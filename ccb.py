#!/usr/bin/env python3
"""
ccb (Claude Code Bridge) - Unified AI Launcher
Supports Claude + Codex / Claude + Gemini / all three simultaneously
Uses tmux as the project UI backend
"""

import time

_CCB_PROCESS_ENTRY_NS = time.perf_counter_ns()

import sys
import os
from pathlib import Path

script_dir = Path(__file__).resolve().parent
sys.path.insert(0, str(script_dir / "lib"))
from cli.startup_process_trace import capture_source_wrapper_trace, mark_ccb_main

capture_source_wrapper_trace(_CCB_PROCESS_ENTRY_NS)

from stdio_runtime import setup_windows_encoding
from terminal_runtime.backend_env import get_backend_env
from platforms.windows.os_platform import (
    detect_os_platform,
    detect_os_platform_string,
    platform_needs_herdr,
    check_herdr_ready,
    is_native_windows,
)
from cli.entrypoint import run_cli_entrypoint

setup_windows_encoding()

# ── OS platform detection ─────────────────────────────────────────────────
# Detect early so downstream code can branch on platform.

_detected_platform = detect_os_platform()
_detected_platform_str = detect_os_platform_string()
os.environ["CCB_OS_PLATFORM"] = _detected_platform_str

# ── Native Windows: Herdr gate ────────────────────────────────────────────
# On native Windows, CCB requires Herdr as the terminal multiplexer backend.
# Probe early for actionable diagnostics; the actual blocking gate runs in
# main() so it can respect safe-introspection (--help/--version/etc.).

_herdr_ok: bool = True
_herdr_message: str = ""
if is_native_windows():
    if os.environ.get("CCB_SKIP_HERDR_CHECK"):
        print("[CCB] Herdr 检查已跳过 (CCB_SKIP_HERDR_CHECK=1)", file=sys.stderr)
    else:
        _herdr_ok, _herdr_message, _ = check_herdr_ready()
        if not _herdr_ok:
            print(f"[CCB] OS 平台: {_detected_platform_str}", file=sys.stderr)
            print(f"[CCB] {_herdr_message}", file=sys.stderr)
            print(
                "[CCB] 提示: 设置 CCB_SKIP_HERDR_CHECK=1 可跳过此检查（仅限诊断）",
                file=sys.stderr,
            )

backend_env = get_backend_env()
if backend_env and not os.environ.get("CCB_BACKEND_ENV"):
    os.environ["CCB_BACKEND_ENV"] = backend_env

VERSION = "8.6.10"
GIT_COMMIT = "release"
GIT_DATE = "2026-08-18"


def _is_source_checkout(root: Path) -> bool:
    return (root / ".git").exists()


def _is_safe_introspection(argv: list[str]) -> bool:
    if not argv:
        return False
    first = argv[0]
    return first in {"--help", "-h", "--print-version", "version"} or "--help" in argv or "-h" in argv


def _split_roots(value: str) -> list[Path]:
    roots: list[Path] = []
    for item in value.split(os.pathsep):
        text = item.strip()
        if text:
            roots.append(Path(text).expanduser())
    return roots


def _default_source_allowed_roots(root: Path) -> list[Path]:
    parent = root.parent
    return [parent / "test_ccb2"]


def _path_is_under(path: Path, root: Path) -> bool:
    try:
        resolved_path = path.resolve()
        resolved_root = root.resolve()
    except Exception:
        resolved_path = path.absolute()
        resolved_root = root.absolute()
    return resolved_path == resolved_root or resolved_root in resolved_path.parents


def _project_arg_paths(argv: list[str], cwd: Path) -> list[Path]:
    paths: list[Path] = []
    index = 0
    while index < len(argv):
        item = argv[index]
        value: str | None = None
        if item == "--project" and index + 1 < len(argv):
            value = argv[index + 1]
            index += 1
        elif item.startswith("--project="):
            value = item.split("=", 1)[1]
        if value:
            project_path = Path(value).expanduser()
            if not project_path.is_absolute():
                project_path = cwd / project_path
            paths.append(project_path)
        index += 1
    return paths


def _source_runtime_allowed(root: Path, cwd: Path, argv: list[str]) -> tuple[bool, str]:
    if not _is_source_checkout(root):
        return True, ""
    if _is_safe_introspection(argv):
        return True, ""
    if os.environ.get("CCB_SOURCE_RUNTIME_OK") == "1":
        return True, ""
    if os.environ.get("PYTEST_CURRENT_TEST"):
        return True, ""

    configured = os.environ.get("CCB_SOURCE_ALLOWED_ROOTS", "")
    allowed_roots = _split_roots(configured) if configured.strip() else _default_source_allowed_roots(root)
    for project_path in _project_arg_paths(argv, cwd):
        for allowed_root in allowed_roots:
            if _path_is_under(project_path, allowed_root):
                return True, ""
    for allowed_root in allowed_roots:
        if _path_is_under(cwd, allowed_root):
            return True, ""

    rendered = ", ".join(str(path) for path in allowed_roots)
    return (
        False,
        "Refusing to run the CCB source checkout outside an allowed test project.\n"
        "Use the installed release `ccb` for normal project/work-environment commands.\n"
        "Use `/home/bfly/yunwei/ccb_source/ccb_test` from "
        "`/home/bfly/yunwei/test_ccb2` for source-change validation.\n"
        f"Current directory: {cwd}\n"
        f"Allowed source roots: {rendered}\n"
        "Override only for explicit diagnostics with CCB_SOURCE_RUNTIME_OK=1.",
    )


def main():
    mark_ccb_main(time.perf_counter_ns())
    allowed, reason = _source_runtime_allowed(script_dir, Path.cwd(), sys.argv[1:])
    if not allowed:
        print(reason, file=sys.stderr)
        return 1
    # Herdr gate: on native Windows without Herdr, block non-introspection commands.
    if not _herdr_ok and not _is_safe_introspection(sys.argv[1:]):
        print(
            "[CCB] Herdr 未就绪，无法启动。请先安装并配置 Herdr 后重试。",
            file=sys.stderr,
        )
        return 1
    return run_cli_entrypoint(
        sys.argv[1:],
        version=VERSION,
        script_root=script_dir,
        cwd=Path.cwd(),
        stdout=sys.stdout,
        stderr=sys.stderr,
    )


if __name__ == "__main__":
    sys.exit(main())
