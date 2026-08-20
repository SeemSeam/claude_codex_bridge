from __future__ import annotations

import argparse
import json
from pathlib import Path
import subprocess
import sys


def droid_server_path(*, script_root: Path) -> Path:
    return script_root / "mcp" / "ccb-delegation" / "server.py"


def cmd_droid_setup_delegation(args, *, script_root: Path) -> int:
    server_path = droid_server_path(script_root=script_root)
    if not server_path.exists():
        print(f"❌ MCP server not found: {server_path}", file=sys.stderr)
        return 2

    if args.force:
        subprocess.run(["droid", "mcp", "remove", "ccb-delegation"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    cmd = [
        "droid",
        "mcp",
        "add",
        "ccb-delegation",
        "--type",
        "stdio",
        sys.executable,
        str(server_path),
    ]
    try:
        rc = subprocess.run(cmd).returncode
    except FileNotFoundError:
        print("❌ `droid` not found in PATH.", file=sys.stderr)
        return 2
    if rc != 0:
        print("❌ Failed to register MCP server. Ensure `droid` is installed and on PATH.", file=sys.stderr)
        return rc or 1
    print("✅ Registered MCP server: ccb-delegation")
    print("Next: run `ccb droid test-delegation` to verify tools are visible.")
    return 0


def extract_tool_name(tool: dict) -> str:
    for key in ("id", "name", "toolName"):
        value = tool.get(key)
        if isinstance(value, str) and value:
            return value
    return ""


def cmd_droid_test_delegation(_args) -> int:
    cmd = ["droid", "exec", "--list-tools", "--output-format", "json"]
    try:
        res = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8")
    except FileNotFoundError:
        print("❌ `droid` not found in PATH.", file=sys.stderr)
        return 2
    if res.returncode != 0:
        err = res.stderr.strip() or res.stdout.strip()
        print(f"❌ droid exec --list-tools failed: {err}", file=sys.stderr)
        return res.returncode or 1

    try:
        tools = json.loads(res.stdout)
    except Exception:
        print("❌ Failed to parse tool list JSON from droid.", file=sys.stderr)
        return 2

    if not isinstance(tools, list):
        print("❌ Tool list JSON is not an array.", file=sys.stderr)
        return 2

    names = {name for tool in tools if (name := extract_tool_name(tool))}
    required = {
        "ccb_ask_agent",
        "ccb_pend_agent",
        "ccb_ping_agent",
    }
    missing = sorted(required - names)
    if missing:
        print("❌ MCP delegation tools missing:", ", ".join(missing), file=sys.stderr)
        print("Hint: run `ccb droid setup-delegation`.", file=sys.stderr)
        return 2

    print("✅ MCP delegation tools detected.")
    return 0


def cmd_droid_subcommand(argv: list[str], *, script_root: Path) -> int:
    parser = argparse.ArgumentParser(prog="ccb droid", description="Droid-specific commands")
    subparsers = parser.add_subparsers(dest="subcommand", help="Droid subcommands")

    setup_parser = subparsers.add_parser("setup-delegation", help="Register MCP delegation tools for Droid")
    setup_parser.add_argument("--force", action="store_true", help="Re-register MCP server")

    subparsers.add_parser("test-delegation", help="Verify MCP delegation tools are available")

    args = parser.parse_args(argv)
    if args.subcommand == "setup-delegation":
        return cmd_droid_setup_delegation(args, script_root=script_root)
    if args.subcommand == "test-delegation":
        return cmd_droid_test_delegation(args)
    parser.print_help()
    return 1
