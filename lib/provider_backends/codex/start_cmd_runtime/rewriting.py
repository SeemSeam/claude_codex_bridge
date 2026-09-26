from __future__ import annotations

import shlex
import re

from .parsing import find_codex_token_index

# `build_resume_start_cmd` means "make this command resume <session_id>".
# Both `resume` and `fork` are continuation subcommands; keeping an existing
# `fork <A>` and appending `resume <B>` produces an unparseable CLI.
_CONTINUATION_SUBCOMMANDS = frozenset({'resume', 'fork'})


def build_resume_start_cmd(command: object, session_id: object) -> str:
    normalized_session_id = str(session_id or '').strip()
    if not normalized_session_id:
        return str(command or '').strip()
    raw = str(command or '').strip()
    if not raw:
        return f'codex resume {shlex.quote(normalized_session_id)}'
    if 'CCB_CODEX_MANAGED_REMOTE=1' in raw:
        return _replace_managed_resume_id(raw, normalized_session_id)
    shell_prefix, codex_segment = split_last_shell_segment(raw)
    rebuilt_segment = rewrite_codex_segment(codex_segment, normalized_session_id)
    if not rebuilt_segment:
        rebuilt_segment = f'codex resume {shlex.quote(normalized_session_id)}'
    if shell_prefix:
        return f'{shell_prefix}; {rebuilt_segment}'
    return rebuilt_segment


def strip_resume_start_cmd(command: object) -> str:
    raw = str(command or '').strip()
    if not raw:
        return ''
    if 'CCB_CODEX_MANAGED_REMOTE=1' in raw:
        return _replace_managed_resume_id(raw, '')
    shell_prefix, codex_segment = split_last_shell_segment(raw)
    stripped_segment = strip_resume_from_codex_segment(codex_segment)
    if stripped_segment is None:
        stripped_segment = codex_segment
    if shell_prefix:
        return f'{shell_prefix}; {stripped_segment}'
    return stripped_segment


def split_last_shell_segment(command: str) -> tuple[str, str]:
    prefix, separator, tail = str(command or '').rpartition(';')
    if not separator:
        return '', str(command or '').strip()
    return prefix.strip(), tail.strip()


def strip_resume_from_codex_segment(segment: str) -> str | None:
    try:
        tokens = shlex.split(segment)
    except Exception:
        return None
    if not tokens:
        return None
    codex_index = find_codex_token_index(tokens)
    if codex_index is None:
        return None
    continuation_index = _continuation_subcommand_index(tokens, codex_index)
    base_tokens = tokens[:continuation_index] if continuation_index is not None else list(tokens)
    if not base_tokens:
        return None
    return ' '.join(shlex.quote(str(token)) for token in base_tokens)


def rewrite_codex_segment(segment: str, session_id: str) -> str | None:
    try:
        tokens = shlex.split(segment)
    except Exception:
        return None
    if not tokens:
        return None
    codex_index = find_codex_token_index(tokens)
    if codex_index is None:
        return None
    continuation_index = _continuation_subcommand_index(tokens, codex_index)
    base_tokens = tokens[:continuation_index] if continuation_index is not None else list(tokens)
    base_tokens.extend(['resume', session_id])
    return ' '.join(shlex.quote(str(token)) for token in base_tokens)


def _continuation_subcommand_index(tokens: list[str], codex_index: int) -> int | None:
    value_options = {'-c', '--config', '-m', '--model', '-p', '--profile',
                     '-s', '--sandbox', '-a', '--ask-for-approval', '-C', '--cd',
                     '-i', '--image', '--add-dir', '--enable', '--disable',
                     '--local-provider', '--remote'}
    flags = {'--oss', '--full-auto', '--dangerously-bypass-approvals-and-sandbox',
             '--dangerously-bypass-hook-trust',
             '--search', '--no-alt-screen', '-h', '--help', '-V', '--version'}
    index = codex_index + 1
    while index < len(tokens):
        token = tokens[index]
        if token in value_options:
            index += 2
        elif token in flags or any(token.startswith(option + '=') for option in value_options):
            index += 1
        elif any(token.startswith(option) and len(token) > 2 for option in value_options if len(option) == 2):
            index += 1
        else:
            # Only the first positional token can be the continuation command.
            return index if token in _CONTINUATION_SUBCOMMANDS else None
    return None


_MANAGED_RESUME_ASSIGNMENT_RE = re.compile(
    r'(?P<prefix>\bCCB_CODEX_RESUME_ID=)(?:\'[^\']*\'|"[^"]*"|[^;\s]*)'
)


def _replace_managed_resume_id(command: str, session_id: str) -> str:
    replacement = rf'\g<prefix>{shlex.quote(session_id)}'
    return _MANAGED_RESUME_ASSIGNMENT_RE.sub(replacement, command, count=1)


__all__ = [
    'build_resume_start_cmd',
    'rewrite_codex_segment',
    'split_last_shell_segment',
    'strip_resume_from_codex_segment',
    'strip_resume_start_cmd',
]
