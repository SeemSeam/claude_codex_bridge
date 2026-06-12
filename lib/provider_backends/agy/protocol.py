from __future__ import annotations

import re

from provider_core.protocol import (
    ANY_DONE_LINE_RE,
    DONE_PREFIX,
    REQ_ID_PREFIX,
    is_done_text,
    make_req_id,
    strip_done_text,
)


def wrap_agy_prompt(message: str, req_id: str) -> str:
    rendered = (message or '').rstrip()
    return (
        f'{REQ_ID_PREFIX} {req_id}\n\n'
        f'{rendered}\n\n'
        'IMPORTANT: when you finish answering, write this exact line on its '
        'own line as the final line of your reply (no quoting, no code fence):\n'
        f'{DONE_PREFIX} {req_id}\n'
    )


_LINE_PREFIX_RE = re.compile(r'^[\s>$#❯]+')
_BANNER_KEYWORDS = ('CCB_REQ_ID:', 'CCB_DONE:')
_BANNER_INSTRUCTIONS = (
    'IMPORTANT: when you finish',
    'IMPORTANT:',
    'on its own line as the final line',
    'no quoting, no code fence',
)


def _req_anchor_re(req_id: str) -> re.Pattern[str]:
    # Not line-anchored: pane TUIs often prefix echoed input with `> ` or similar.
    return re.compile(rf'{re.escape(REQ_ID_PREFIX)}\s*{re.escape(req_id)}')


def pane_contains_req_anchor(text: str, req_id: str) -> bool:
    if not text or not req_id:
        return False
    return _req_anchor_re(req_id).search(text) is not None


def _done_anywhere_re(req_id: str) -> re.Pattern[str]:
    return re.compile(rf'{re.escape(DONE_PREFIX)}\s*{re.escape(req_id)}')


def extract_reply_for_req(text: str, req_id: str) -> tuple[str, bool]:
    """Return (reply, done_seen) extracted from a pane snapshot.

    Antigravity's TUI renders both the echoed prompt and the model response
    with the same 2-space indentation, so echo-DONE and model-DONE are
    indistinguishable by line prefix. We rely on order instead: the prompt
    instructs the model to write CCB_DONE as the final line, so the LAST
    CCB_DONE occurrence is the model's; the one before it (if any) is the
    echoed prompt's tail.

    Algorithm:
    1. Find the LAST `CCB_REQ_ID: <id>` anchor.
    2. In the after-anchor window, find all `CCB_DONE: <id>` occurrences.
    3. Decide by count:
       - 0 -> model still thinking; reply='', done_seen=False.
       - 1 -> only the echoed prompt DONE is visible; reply='',
              done_seen=False.
       - >=2 -> last is model DONE, second-to-last is echo DONE.
              reply slice = (end of echo-DONE line) -> (start of model-DONE
              line). done_seen=True.
    4. Clean: drop banner/echo lines, normalize whitespace.
    5. Sentinel guard: if the cleaned reply still contains banner instruction
       fragments, the slice landed in the echo region; drop it.
    """
    if not text or not req_id:
        return '', False

    text = text.replace('\r\n', '\n').replace('\r', '\n')

    anchor_matches = list(_req_anchor_re(req_id).finditer(text))
    if not anchor_matches:
        return '', False

    after_anchor = text[anchor_matches[-1].end():]

    done_matches = list(_done_anywhere_re(req_id).finditer(after_anchor))
    if len(done_matches) < 2:
        return '', False

    _, echo_line_end = _line_bounds(after_anchor, done_matches[-2].start())
    model_line_start, _ = _line_bounds(after_anchor, done_matches[-1].start())

    reply_start = echo_line_end + 1 if echo_line_end < len(after_anchor) else echo_line_end
    body = after_anchor[reply_start:model_line_start]

    cleaned = _clean_body(body, req_id)
    if _contains_banner_fragment(cleaned):
        return '', False

    return cleaned, True


def _line_bounds(text: str, pos: int) -> tuple[int, int]:
    start = text.rfind('\n', 0, pos) + 1
    end = text.find('\n', pos)
    if end == -1:
        end = len(text)
    return start, end


def _contains_banner_fragment(text: str) -> bool:
    blob = text or ''
    for marker in _BANNER_INSTRUCTIONS:
        if marker in blob:
            return True
    for marker in _BANNER_KEYWORDS:
        if marker in blob:
            return True
    return False


def _clean_body(body: str, req_id: str) -> str:
    text = (body or '').replace('\r\n', '\n').replace('\r', '\n')
    try:
        text = strip_done_text(text, req_id)
    except Exception:
        pass
    text = ANY_DONE_LINE_RE.sub('', text)

    cleaned_lines: list[str] = []
    for raw in text.split('\n'):
        stripped = _LINE_PREFIX_RE.sub('', raw).rstrip()
        if _is_banner_line(stripped):
            continue
        cleaned_lines.append(stripped)

    while cleaned_lines and not cleaned_lines[0].strip():
        cleaned_lines.pop(0)
    while cleaned_lines and not cleaned_lines[-1].strip():
        cleaned_lines.pop()

    return '\n'.join(cleaned_lines).strip()


def _is_banner_line(line: str) -> bool:
    text = (line or '').strip()
    if not text:
        return False
    for marker in _BANNER_KEYWORDS:
        if marker in text:
            return True
    for marker in _BANNER_INSTRUCTIONS:
        if marker in text:
            return True
    return False


__all__ = [
    'ANY_DONE_LINE_RE',
    'DONE_PREFIX',
    'REQ_ID_PREFIX',
    'extract_reply_for_req',
    'is_done_text',
    'make_req_id',
    'pane_contains_req_anchor',
    'strip_done_text',
    'wrap_agy_prompt',
]
