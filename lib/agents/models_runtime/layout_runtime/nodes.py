from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class LayoutLeaf:
    name: str
    provider: str | None = None
    workspace_mode: str | None = None
    percent: int | None = None


@dataclass(frozen=True)
class LayoutNode:
    kind: str
    leaf: 'LayoutLeaf | None' = None
    left: 'LayoutNode | None' = None
    right: 'LayoutNode | None' = None

    def __post_init__(self) -> None:
        kind = str(self.kind or '').strip().lower()
        if kind not in {'leaf', 'horizontal', 'vertical'}:
            raise ValueError(f'unsupported layout node kind: {self.kind!r}')
        object.__setattr__(self, 'kind', kind)
        if kind == 'leaf':
            if self.leaf is None or self.left is not None or self.right is not None:
                raise ValueError('leaf layout node requires only leaf payload')
            return
        if self.leaf is not None or self.left is None or self.right is None:
            raise ValueError('branch layout node requires left/right children')

    @property
    def leaf_count(self) -> int:
        if self.kind == 'leaf':
            return 1
        assert self.left is not None
        assert self.right is not None
        return self.left.leaf_count + self.right.leaf_count

    def iter_leaves(self) -> tuple[LayoutLeaf, ...]:
        if self.kind == 'leaf':
            assert self.leaf is not None
            return (self.leaf,)
        assert self.left is not None
        assert self.right is not None
        return (*self.left.iter_leaves(), *self.right.iter_leaves())

    def render(self) -> str:
        if self.kind == 'leaf':
            assert self.leaf is not None
            parts = []
            if self.leaf.provider:
                if str(self.leaf.workspace_mode or '').strip() == 'worktree':
                    parts.append(f'{self.leaf.name}:{self.leaf.provider}(worktree)')
                else:
                    parts.append(f'{self.leaf.name}:{self.leaf.provider}')
            else:
                parts.append(self.leaf.name)
            if self.leaf.percent is not None:
                parts.append(f'@{self.leaf.percent}')
            return ''.join(parts)
        assert self.left is not None
        assert self.right is not None
        sep = ';' if self.kind == 'horizontal' else ','
        left = render_child(self.left, parent_kind=self.kind)
        right = render_child(self.right, parent_kind=self.kind)
        return f'{left}{sep} {right}'


def render_child(node: LayoutNode, *, parent_kind: str) -> str:
    text = node.render()
    if node.kind == 'leaf':
        return text
    child_rank = precedence(node.kind)
    parent_rank = precedence(parent_kind)
    if child_rank < parent_rank:
        return f'({text})'
    if child_rank == parent_rank:
        return text
    if parent_kind == 'vertical' and node.kind == 'horizontal':
        return f'({text})'
    return text


def precedence(kind: str) -> int:
    return {'horizontal': 1, 'vertical': 2, 'leaf': 3}[kind]


def iter_layout_names(node: LayoutNode) -> tuple[str, ...]:
    return tuple(leaf.name for leaf in node.iter_leaves())


__all__ = ['LayoutLeaf', 'LayoutNode', 'iter_layout_names']
