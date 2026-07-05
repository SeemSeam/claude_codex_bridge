from __future__ import annotations

from cli.context import CliContext
from cli.models import ParsedWhyCommand

from .daemon import invoke_mounted_daemon


def why_target(context: CliContext, command: ParsedWhyCommand) -> dict:
    return invoke_mounted_daemon(
        context,
        allow_restart_stale=False,
        request_fn=lambda client: client.get({"job_id": command.job_id}),
    )


__all__ = ["why_target"]
