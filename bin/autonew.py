#!/usr/bin/env python3
"""
autonew - Send /new command to a provider's terminal pane to start a new session.

Usage:
    autonew <provider>

Providers:
    gemini, codex, opencode, droid, claude

This command directly sends '/new' to the provider's terminal pane
without any context injection or protocol wrapping.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

script_dir = Path(__file__).resolve().parent
lib_dir = script_dir.parent / "lib"
sys.path.insert(0, str(lib_dir))

from cli.output import EXIT_ERROR, EXIT_OK
from pane_registry_runtime import get_providers_map, load_registry_by_project_id
from project.identity import compute_ccb_project_id
from terminal_runtime import get_backend_for_session


# Provider to reset command mapping
PROVIDER_COMMANDS = {
    "gemini": "/clear",
    "codex": "/new",
    "opencode": "/new",
    "droid": "/new",
    "claude": "/new",
}


def _usage() -> None:
    print("Usage: autonew <provider>", file=sys.stderr)
    print("", file=sys.stderr)
    print("Providers:", file=sys.stderr)
    print("  gemini, codex, opencode, droid, claude", file=sys.stderr)
    print("", file=sys.stderr)
    print("Sends /new to the provider's pane to start a new session.", file=sys.stderr)


def main(argv: list[str]) -> int:
    if len(argv) <= 1:
        _usage()
        return EXIT_ERROR

    provider = argv[1].lower()

    if provider in ("-h", "--help"):
        _usage()
        return EXIT_OK

    if provider not in PROVIDER_COMMANDS:
        print(f"[ERROR] Unknown provider: {provider}", file=sys.stderr)
        print(f"[ERROR] Available: {', '.join(sorted(PROVIDER_COMMANDS.keys()))}", file=sys.stderr)
        return EXIT_ERROR

    # Get current project ID
    work_dir = Path(os.getcwd())
    try:
        project_id = compute_ccb_project_id(work_dir)
    except Exception as e:
        print(f"[ERROR] Failed to compute project ID: {e}", file=sys.stderr)
        return EXIT_ERROR

    # Load registry for this project and provider
    record = load_registry_by_project_id(project_id, provider)
    if not record:
        print(f"[ERROR] No active {provider} session found for this project.", file=sys.stderr)
        return EXIT_ERROR

    # Get provider's pane_id
    providers_map = get_providers_map(record)
    provider_entry = providers_map.get(provider, {})
    pane_id = str(provider_entry.get("pane_id") or "").strip()

    if not pane_id:
        print(f"[ERROR] No pane_id found for {provider}.", file=sys.stderr)
        return EXIT_ERROR

    # Get terminal backend
    backend = get_backend_for_session(record)
    if not backend:
        print("[ERROR] Terminal backend not available.", file=sys.stderr)
        return EXIT_ERROR

    # Check if pane is alive
    try:
        if not backend.is_alive(pane_id):
            print(f"[ERROR] {provider} pane {pane_id} is not alive.", file=sys.stderr)
            return EXIT_ERROR
    except Exception as e:
        print(f"[ERROR] Failed to check pane status: {e}", file=sys.stderr)
        return EXIT_ERROR

    # Send reset command to the pane
    reset_cmd = PROVIDER_COMMANDS[provider]
    try:
        backend.send_text(pane_id, reset_cmd)
        print(f"Sent {reset_cmd} to {provider} (pane: {pane_id})")
        return EXIT_OK
    except Exception as e:
        print(f"[ERROR] Failed to send {reset_cmd}: {e}", file=sys.stderr)
        return EXIT_ERROR


if __name__ == "__main__":
    sys.exit(main(sys.argv))
