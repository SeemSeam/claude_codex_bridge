from __future__ import annotations

import re

from .nodes import LayoutLeaf, LayoutNode

_LEAF_TOKEN_RE = re.compile(
    r'(?P<name>[A-Za-z][A-Za-z0-9_.-]{0,63})'
    r'(?:\s*:\s*(?P<provider>[A-Za-z0-9_-]+)'
    r'(?:\s*\(\s*(?P<workspace_mode>worktree)\s*\))?'
    r')?'
    r'(?:\s*@\s*(?P<percent>\d+))?$'
)


class LayoutParseError(ValueError):
    pass


class _LayoutParser:
    def __init__(self, text: str):
        self._tokens = tokenize(text)
        self._index = 0

    def parse(self) -> LayoutNode:
        if not self._tokens:
            raise LayoutParseError('layout is empty')
        node = self._parse_horizontal()
        if self._peek() is not None:
            raise LayoutParseError(f'unexpected token {self._peek()!r}')
        return node

    def _parse_horizontal(self) -> LayoutNode:
        node = self._parse_vertical()
        while self._peek() == ';':
            self._consume(';')
            rhs = self._parse_vertical()
            node = LayoutNode(kind='horizontal', left=node, right=rhs)
        return node

    def _parse_vertical(self) -> LayoutNode:
        node = self._parse_primary()
        while self._peek() == ',':
            self._consume(',')
            rhs = self._parse_primary()
            node = LayoutNode(kind='vertical', left=node, right=rhs)
        return node

    def _parse_primary(self) -> LayoutNode:
        token = self._peek()
        if token is None:
            raise LayoutParseError('unexpected end of layout')
        if token == '(':
            self._consume('(')
            node = self._parse_horizontal()
            self._consume(')')
            return node
        if token in {')', ';', ','}:
            raise LayoutParseError(f'unexpected token {token!r}')
        return self._parse_leaf(self._consume_any())

    def _parse_leaf(self, token: str) -> LayoutNode:
        match = _LEAF_TOKEN_RE.fullmatch(token)
        if match is None:
            raise LayoutParseError(
                f"invalid layout token {token!r}; expected 'cmd', 'agent', 'agent:provider', or 'agent:provider(worktree)'"
            )
        pct_str = match.group('percent')
        pct = int(pct_str) if pct_str is not None else None
        return LayoutNode(
            kind='leaf',
            leaf=LayoutLeaf(
                name=match.group('name').strip(),
                provider=(match.group('provider') or None),
                workspace_mode=(match.group('workspace_mode') or None),
                percent=pct,
            ),
        )

    def _peek(self) -> str | None:
        if self._index >= len(self._tokens):
            return None
        return self._tokens[self._index]

    def _consume(self, expected: str) -> str:
        token = self._peek()
        if token != expected:
            raise LayoutParseError(f'expected {expected!r}, found {token!r}')
        self._index += 1
        return token

    def _consume_any(self) -> str:
        token = self._peek()
        if token is None:
            raise LayoutParseError('unexpected end of layout')
        self._index += 1
        return token


def tokenize(text: str) -> tuple[str, ...]:
    tokens: list[str] = []
    buf: list[str] = []
    for raw_line in str(text or '').splitlines():
        line = raw_line.split('#', 1)[0].split('//', 1)[0]
        index = 0
        while index < len(line):
            char = line[index]
            if char == '(' and ''.join(buf).strip():
                close = line.find(')', index + 1)
                if close != -1:
                    buf.append(line[index : close + 1])
                    index = close + 1
                    continue
            if char in {'(', ')', ';', ','}:
                append_leaf_token(tokens, buf)
                tokens.append(char)
                index += 1
                continue
            buf.append(char)
            index += 1
        append_leaf_token(tokens, buf)
    return tuple(token for token in tokens if token)


def append_leaf_token(tokens: list[str], buf: list[str]) -> None:
    leaf = ''.join(buf).strip()
    if leaf:
        tokens.append(leaf)
    buf.clear()


def parse_layout_spec(text: str) -> LayoutNode:
    return _LayoutParser(text).parse()


__all__ = ['LayoutParseError', 'parse_layout_spec']
