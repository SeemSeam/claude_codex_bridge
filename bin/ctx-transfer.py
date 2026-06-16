#!/usr/bin/env python3
"""
ctx-transfer - Transfer conversation context between CCB agents.

Usage:
    ctx-transfer [OPTIONS]

Examples:
    ctx-transfer --last 5 --agent agent1 --send  # Transfer last 5 conversations
    ctx-transfer --dry-run                   # Preview without sending
    ctx-transfer --output context.md         # Write to file
    ctx-transfer --from auto                 # Auto-detect source provider
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

script_dir = Path(__file__).resolve().parent
lib_dir = script_dir.parent / "lib"
sys.path.insert(0, str(lib_dir))

from memory import (
    ContextTransfer,
    SessionNotFoundError,
    SessionParseError,
)


def parse_args(argv: list[str]) -> argparse.Namespace:
    """Parse command line arguments."""
    parser = argparse.ArgumentParser(
        prog="ctx-transfer",
        description="Transfer conversation context between CCB agents.",
    )
    parser.add_argument(
        "-n", "--last",
        type=int,
        default=3,
        help="Number of conversation pairs to transfer (default: 3)",
    )
    parser.add_argument(
        "--from",
        "--source",
        dest="source_provider",
        default="auto",
        choices=["auto", "claude", "codex", "gemini", "opencode", "droid"],
        help="Source provider (default: auto)",
    )
    parser.add_argument(
        "--agent",
        dest="agent_name",
        help="Target agent name (required with --send)",
    )
    parser.add_argument(
        "--send",
        action="store_true",
        help="Send to agent via ask (default: disabled)",
    )
    parser.add_argument(
        "-d", "--dry-run",
        action="store_true",
        help="Preview output without sending",
    )
    parser.add_argument(
        "-o", "--output",
        type=Path,
        help="Write output to file instead of sending",
    )
    parser.add_argument(
        "--session-path",
        type=Path,
        help="Explicit session JSONL path",
    )
    parser.add_argument(
        "--max-tokens",
        type=int,
        default=8000,
        help="Maximum tokens to transfer (default: 8000)",
    )
    parser.add_argument(
        "-f", "--format",
        dest="fmt",
        default="markdown",
        choices=["markdown", "plain", "json"],
        help="Output format (default: markdown)",
    )
    parser.add_argument(
        "-q", "--quiet",
        action="store_true",
        help="Suppress informational output",
    )
    parser.add_argument(
        "-s", "--save",
        action="store_true",
        help="Save transfer to ./.ccb/history/ (auto-enabled when sending)",
    )
    parser.add_argument(
        "--no-save",
        action="store_true",
        help="Disable auto-save when sending to agent",
    )
    parser.add_argument(
        "--detailed",
        action="store_true",
        help="Output detailed tool executions with full results",
    )
    args = parser.parse_args(argv[1:])
    if args.send and not str(args.agent_name or "").strip():
        parser.error("--send requires --agent <agent_name>")
    return args


def main(argv: list[str]) -> int:
    """Main entry point."""
    args = parse_args(argv)

    try:
        transfer = ContextTransfer(
            max_tokens=args.max_tokens,
            work_dir=Path.cwd(),
        )

        # Extract conversations
        context = transfer.extract_conversations(
            session_path=args.session_path,
            last_n=args.last,
            source_provider=args.source_provider,
        )

        if not context.conversations:
            print("No conversations found in session.", file=sys.stderr)
            return 1

        if not args.quiet:
            provider_label = (context.source_provider or "auto").strip().lower() or "auto"
            print(
                f"Extracted {len(context.conversations)} conversation(s) "
                f"(~{context.token_estimate} tokens) from {provider_label}",
                file=sys.stderr,
            )

        # Format output
        formatted = transfer.format_output(context, args.fmt, detailed=args.detailed)

        # Handle output modes
        if args.dry_run:
            print(formatted)
            return 0

        if args.output:
            args.output.parent.mkdir(parents=True, exist_ok=True)
            args.output.write_text(formatted, encoding="utf-8")
            if not args.quiet:
                print(f"Written to {args.output}", file=sys.stderr)
            return 0

        if not args.send:
            # Default: write to history dir when no --output/--dry-run
            if not args.output:
                saved_path = transfer.save_transfer(context, args.fmt, target_agent=None)
                if not args.quiet:
                    print(f"Saved to {saved_path}", file=sys.stderr)
            return 0

        # Save if requested or sending to agent (unless --no-save)
        should_save = args.save or (not args.no_save)
        saved_path = None
        if should_save:
            saved_path = transfer.save_transfer(context, args.fmt, args.agent_name)
            if not args.quiet:
                print(f"Saved to {saved_path}", file=sys.stderr)

        # Send to agent
        success, result = transfer.send_to_agent(context, args.agent_name, args.fmt)
        if success:
            if not args.quiet:
                print(f"Sent to {args.agent_name}", file=sys.stderr)
            if result:
                print(result)
            return 0
        else:
            print(f"Failed to send: {result}", file=sys.stderr)
            return 1

    except SessionNotFoundError as e:
        print(f"Session not found: {e}", file=sys.stderr)
        print("Hints:", file=sys.stderr)
        print("  - Ensure a CCB-supported CLI is running in this directory", file=sys.stderr)
        print("  - Use --from to select a specific provider", file=sys.stderr)
        print("  - Use --session-path to specify a Claude session file", file=sys.stderr)
        return 1
    except SessionParseError as e:
        print(f"Session parse error: {e}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        return 130
    except Exception as e:
        print(f"Error: {e}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
