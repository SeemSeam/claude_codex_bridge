from __future__ import annotations

from cli.models import ParsedScreenCommand


def parse_screen(tokens, *, project, error_type):
    usage = 'screen requires <agent> [--lines 0..1000] [--json]'
    agent = None
    lines = 0
    seen = set()
    index = 0
    while index < len(tokens):
        token = tokens[index]
        if token in {'--lines', '--json'}:
            if token in seen:
                raise error_type(usage)
            seen.add(token)
            if token == '--lines':
                index += 1
                try:
                    lines = int(tokens[index])
                except (IndexError, ValueError):
                    raise error_type(usage) from None
                if not 0 <= lines <= 1000:
                    raise error_type(usage)
        elif token.startswith('-') or agent is not None or not token.strip():
            raise error_type(usage)
        else:
            agent = token
        index += 1
    if agent is None:
        raise error_type(usage)
    return ParsedScreenCommand(project, agent, lines, '--json' in seen)
