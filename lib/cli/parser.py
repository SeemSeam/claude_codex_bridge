from __future__ import annotations

import sys
from typing import Iterable

from stdio_runtime import read_stdin_text
from cli.models import ParsedCommand, ParsedStartCommand

from .parser_runtime import (
    SUBCOMMANDS,
    WAIT_COMMAND_TO_MODE,
    parse_ack,
    parse_agent,
    parse_ask,
    parse_cancel,
    parse_clear,
    parse_cleanup,
    parse_config,
    parse_doctor,
    parse_fault,
    parse_global_options,
    parse_identity,
    parse_inbox,
    parse_kill,
    parse_layout,
    parse_logs,
    parse_loop,
    parse_maintenance,
    parse_mobile,
    parse_plan,
    parse_pend,
    parse_ping,
    parse_probe,
    parse_ps,
    parse_question,
    parse_queue,
    parse_repair,
    parse_reload,
    parse_restart,
    parse_resubmit,
    parse_retry,
    parse_start,
    parse_trace,
    parse_wait,
    parse_watch,
    parse_why,
)


class CliUsageError(ValueError):
    pass


_COMMAND_PARSERS = {
    'cancel': parse_cancel,
    'clear': parse_clear,
    'cleanup': parse_cleanup,
    'kill': parse_kill,
    'layout': parse_layout,
    'ps': parse_ps,
    'ping': parse_ping,
    'watch': parse_watch,
    'pend': parse_pend,
    'queue': parse_queue,
    'repair': parse_repair,
    'trace': parse_trace,
    'why': parse_why,
    'resubmit': parse_resubmit,
    'retry': parse_retry,
    'inbox': parse_inbox,
    'ack': parse_ack,
    'agent': parse_agent,
    'logs': parse_logs,
    'loop': parse_loop,
    'plan': parse_plan,
    'question': parse_question,
    'maintenance': parse_maintenance,
    'mobile': parse_mobile,
    'doctor': parse_doctor,
    'identity': parse_identity,
    'probe': parse_probe,
    'config': parse_config,
    'fault': parse_fault,
    'reload': parse_reload,
    'restart': parse_restart,
}


class CliParser:
    def parse(self, argv: Iterable[str]) -> ParsedCommand:
        tokens = list(argv)
        project, tokens = parse_global_options(tokens, error_type=CliUsageError)
        if not tokens:
            return ParsedStartCommand(project=project, agent_names=(), restore=True, auto_permission=True)
        command = tokens[0]
        if command not in SUBCOMMANDS:
            return parse_start(tokens, project=project, error_type=CliUsageError)

        rest = tokens[1:]
        if command == 'ask':
            return parse_ask(
                rest,
                project=project,
                read_optional_stdin=self._read_optional_stdin,
                error_type=CliUsageError,
            )
        if command in WAIT_COMMAND_TO_MODE:
            return parse_wait(command, rest, project=project, error_type=CliUsageError)
        parser_fn = _COMMAND_PARSERS.get(command)
        if parser_fn is not None:
            return parser_fn(rest, project=project, error_type=CliUsageError)
        raise CliUsageError(f'unknown command: {command}')

    def _read_optional_stdin(self) -> str:
        if sys.stdin.isatty():
            return ''
        try:
            return read_stdin_text()
        except OSError:
            return ''
