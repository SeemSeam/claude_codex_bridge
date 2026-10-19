"""Read only the current default tmux composer; unknown layouts fail closed."""
from __future__ import annotations

import re
from dataclasses import dataclass

_SGR = re.compile(r'\x1b\[([0-9;]*)m')
_ANSI = re.compile(r'\x1b\[[0-?]*[ -/]*[@-~]')
_BORDER = re.compile(r'^─{8,}(?:\s\S.*\s─+)?\s*$')


@dataclass(frozen=True)
class Observation:
    state: str
    binding: str
    reason: str


def _styled_lines(text: str) -> list[list[tuple[str, bool, bool]]]:
    lines = [[]]
    dim = inverse = False
    pos = 0
    while pos < len(text):
        match = _SGR.match(text, pos)
        if match:
            codes = [int(value or 0) for value in match.group(1).split(';')]
            index = 0
            while index < len(codes):
                code = codes[index]
                if code == 0:
                    dim = inverse = False
                elif code == 2:
                    dim = True
                elif code == 22:
                    dim = False
                elif code == 7:
                    inverse = True
                elif code == 27:
                    inverse = False
                elif code in {38, 48, 58} and index+1 < len(codes):
                    index += 4 if codes[index+1] == 2 else 2
                index += 1
            pos = match.end()
            continue
        other = _ANSI.match(text, pos)
        if other:
            pos = other.end()
            continue
        char = text[pos]
        if char == '\n':
            lines.append([])
        else:
            lines[-1].append((char, dim, inverse))
        pos += 1
    return lines


def inspect_screen(provider: str, screen: dict, *, binding: str) -> Observation:
    styled = _styled_lines(screen['text'])
    lines = [''.join(char for char, _, _ in line) for line in styled]
    cursor_x, cursor_y = screen['cursor_x'], screen['cursor_y']
    def result(state, reason):
        return Observation(state, binding, reason)
    if provider == 'codex':
        arrows = [i for i, line in enumerate(lines) if line.startswith('›') and i <= cursor_y]
        if not arrows:
            return result('unknown', 'composer_missing')
        top = arrows[-1]
        if _codex_busy(lines[:top]):
            return result('unknown', 'provider_busy')
        # Default main composer has a status footer below the cursor. Selection
        # menus use the same arrow; their confirmation footer is not accepted.
        footer = _codex_footer(lines, styled, cursor_x, cursor_y)
        if footer is None:
            return result('unknown', 'composer_layout_unknown')
        if _editor_mode_in_footer(lines[footer:]):
            return result('unknown', 'unsupported_editor_mode')
        content = lines[top][2:]
        # Styled terminal rows may retain right-hand padding. This is still
        # the fixed placeholder at its initial cursor, not a typed draft.
        if (content.rstrip(' ') in _CODEX_PLACEHOLDERS
                and cursor_y == top and cursor_x == 2):
            if all(not line.strip() for line in lines[top+1:footer]):
                return result('empty', 'codex_placeholder')
        if any(line.strip() for line in [content, *lines[top+1:footer]]) or cursor_y != top or cursor_x > 2:
            return result('nonempty', 'codex_draft')
        return result('unknown', 'codex_no_placeholder')
    if provider != 'claude':
        return result('unknown', 'unsupported_provider')
    borders = [i for i, line in enumerate(lines) if _BORDER.fullmatch(line)]
    pairs = [(a, b) for a, b in zip(borders, borders[1:]) if a < cursor_y < b]
    if not pairs:
        return result('unknown', 'composer_layout_unknown')
    top, bottom = pairs[-1]
    if not lines[top+1].startswith('❯'):
        return result('unknown', 'composer_not_focused')
    # Claude may show elapsed time/tokens without an "esc to interrupt" hint.
    # Its animated flower + ellipsis is distinct from completed "Worked for"
    # status and normal assistant bullets. Never inspect the draft for status.
    if any(re.match(r'^[✢✳✶✻✽·]\s+.*(?:…|\.\.\.)', line) for line in lines[:top]):
        return result('unknown', 'provider_busy')
    if _editor_mode_in_footer(lines[bottom+1:]):
        return result('unknown', 'unsupported_editor_mode')
    content = styled[top+1][2:] + [cell for row in styled[top+2:bottom] for cell in row]
    visible = [cell for cell in content if not cell[0].isspace()]
    if not visible:
        if bottom == top+2 and cursor_y == top+1 and cursor_x == 2:
            return result('empty', 'claude_blank')
        return result('nonempty', 'claude_whitespace_draft')
    # A virtual cursor may render the first ghost character in inverse video.
    # Require the rest to be dim, and at least one actual dim glyph.
    ghost = all(dim or (index == 0 and inverse) for index, (_, dim, inverse) in enumerate(visible))
    if ghost and any(dim for _, dim, _ in visible) and cursor_x == 2 and cursor_y == top+1:
        if 'Press up to edit queued messages' in lines[top+1]:
            return result('unknown', 'provider_native_queue_pending')
        return result('empty', 'claude_ghost')
    return result('nonempty', 'claude_draft')


def _codex_busy(lines: list[str]) -> bool:
    from provider_pane_status.codex_pane import (
        CODEX_RECONNECT_LINE_RE, CODEX_TOOL_LINE_RE, CODEX_WORKING_LINE_RE,
        WORKED_FOR_RE,
    )

    active = completed = -1
    for index, line in enumerate(lines):
        # A wrapped native row may continue on indented lines, never on the
        # next assistant bullet or a user prompt. Reuse the status vocabulary
        # rather than treating arbitrary quoted interrupt hints as activity.
        parts = [line]
        for continuation in lines[index+1:index+3]:
            if not continuation.strip() or not continuation.startswith('  '):
                break
            parts.append(continuation.strip())
        status = ' '.join(parts)
        # Fullscreen Codex 0.159.2 renders this native summary with two-space
        # indentation instead of a bullet. Keep the same timer grammar.
        completed_status = re.sub(r'^  (?=worked\s+for\b)', '• ', status, flags=re.I)
        if WORKED_FOR_RE.match(completed_status):
            completed = index
        if any(pattern.match(status) for pattern in (
                CODEX_RECONNECT_LINE_RE, CODEX_TOOL_LINE_RE, CODEX_WORKING_LINE_RE)):
            active = index
    return active > completed


def _codex_footer(lines, styled, cursor_x: int, cursor_y: int) -> int | None:
    # A contiguous native status/hint region follows the editor's blank
    # separator. Model/status labels remain opaque; extra rows must be native
    # hints, so arbitrary indented draft continuation cannot become a footer.
    footer = next((i for i in range(len(lines)-1, cursor_y, -1) if lines[i].strip()), None)
    if footer is None:
        return None
    bottom = footer
    while footer > cursor_y+1 and lines[footer-1].strip():
        footer -= 1
    if footer <= cursor_y+1 or lines[footer-1].strip():
        return None
    if any(not re.match(r'^  \? for shortcuts\b', row)
           for row in lines[footer+1:bottom+1]):
        return None
    row = lines[footer]
    if not row.startswith('  '):
        return None
    if re.match(r'^  (?:\d+% [Cc]ontext\b|\? for shortcuts\b)', row):
        return footer
    if re.fullmatch(r'  Context (?:100|[1-9]?\d)% left *', row):
        return footer
    # Custom status bars separate fields with a middle dot. Require the
    # rendering attribute as well as spacing; ordinary draft prose is not a
    # status bar just because it contains a dot or a model-like word.
    if any(char == '·' and dim and 0 < i < len(row)-1
           and row[i-1:i+2] == ' · '
           for i, (char, dim, _) in enumerate(styled[footer])):
        return footer
    # Newer builds may stop dimming the composer placeholder and status fields
    # alike. Authorize only the EMPTY native composer in that layout: a known
    # placeholder text at the initial cursor, no continuation text. Plain
    # draft prose and arbitrary model labels cannot establish a boundary for
    # clearing nonempty input. Unknown draft layouts still fail closed.
    if (_codex_native_placeholder(lines, cursor_x, cursor_y)
            and all(not line.strip() for line in lines[cursor_y+1:footer])):
        return footer
    return None


_CODEX_PLACEHOLDERS = ('Ask Codex to do anything', 'Implement {feature}')


def _codex_native_placeholder(lines, cursor_x: int, cursor_y: int) -> bool:
    # The native empty-composer placeholder. Newer builds may drop the dim
    # attribute and change the wording, so accept the known placeholder texts
    # regardless of attribute; anything else still fails closed.
    if cursor_x != 2 or cursor_y >= len(lines):
        return False
    content = lines[cursor_y].rstrip(' ')
    return content.startswith('› ') and content[2:] in _CODEX_PLACEHOLDERS


def _editor_mode_in_footer(lines: list[str]) -> bool:
    return any(re.search(r'\b(?:INSERT|NORMAL|VISUAL)\b', line) for line in lines)
