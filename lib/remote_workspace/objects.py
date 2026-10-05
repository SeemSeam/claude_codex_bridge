"""A bounded Git object protocol, independent of either peer's .git metadata.

Only commit/tree/blob objects are exchanged. Trees are walked and path policy is
checked before any object is imported or worktree path is written. A bootstrap
exports one commit and its tree, not ancestor history (which may contain secrets).
"""

from __future__ import annotations

import base64
import hashlib
import os
import re
import subprocess


class SyncError(RuntimeError):
    pass


MAX_BYTES = 32 * 1024 * 1024
MAX_OBJECTS = 12000
MAX_COMMITS = 128
OID = re.compile(r'[0-9a-f]{40}\Z')
FORBIDDEN = {
    '.git',
    '.ccb',
    '.claude',
    '.codex',
    '.ssh',
    '.gnupg',
    '.aws',
    '.azure',
    '.kube',
    '.gitmodules',
    '.gitattributes',
    '.gitconfig',
    '.ccb-workspace.json',
    'ccb.config',
    '.netrc',
    '.npmrc',
    '.pypirc',
    '__pycache__',
    '.pytest_cache',
    '.mypy_cache',
    '.ruff_cache',
    '.venv',
    'node_modules',
}


def oid_ok(value):
    if not isinstance(value, str) or not OID.fullmatch(value):
        raise SyncError('invalid Git object ID')
    return value


def safe_path(value: str) -> str:
    if not isinstance(value, str) or len(value.encode('utf-8')) > 2048:
        raise SyncError('invalid path')
    parts = value.split('/')
    if value.casefold() == 'docs/plantree' or value.casefold().startswith('docs/plantree/'):
        raise SyncError('CCB plan authority is not a project-file payload')
    if (
        not value
        or len(parts) > 32
        or any(
            not p
            or p in ('.', '..')
            or p.rstrip(' .') != p
            or ':' in p
            or '\\' in p
            or any(ord(c) < 32 or ord(c) == 127 for c in p)
            for p in parts
        )
    ):
        raise SyncError(f'unsafe path: {value!r}')
    for part in parts:
        name = part.casefold()
        if (
            name in FORBIDDEN
            or name.startswith(('.env', '.git~', 'git~', '.ccb-'))
            or name.endswith(('.pem', '.key'))
        ):
            raise SyncError(f'control or credential path is excluded: {value}')
    return value


def allowed(path: str, include) -> bool:
    safe_path(path)
    return any(path == p or path.startswith(p + '/') for p in include)


def digest(kind: str, data: bytes) -> str:
    if kind not in ('commit', 'tree', 'blob'):
        raise SyncError('unsupported Git object type')
    return hashlib.sha1(kind.encode() + b' ' + str(len(data)).encode() + b'\0' + data).hexdigest()


class Objects:
    def __init__(self):
        self.items: dict[str, tuple[str, bytes]] = {}
        self.size = 0

    def add(self, kind, data, oid=None):
        actual = digest(kind, data)
        if oid is not None and oid_ok(oid) != actual:
            raise SyncError('Git object hash mismatch')
        if actual not in self.items:
            self.size += len(data)
            if self.size > MAX_BYTES or len(self.items) >= MAX_OBJECTS:
                raise SyncError('object budget exceeded')
            self.items[actual] = kind, data
        return actual

    def get(self, oid, kind):
        item = self.items.get(oid_ok(oid))
        if item is None or item[0] != kind:
            raise SyncError(f'missing or wrong-type {kind} object: {oid}')
        return item[1]

    def wire(self):
        return {
            oid: [kind, base64.b64encode(data).decode('ascii')]
            for oid, (kind, data) in sorted(self.items.items())
        }

    @classmethod
    def from_wire(cls, value):
        if not isinstance(value, dict) or len(value) > MAX_OBJECTS:
            raise SyncError('invalid object map')
        result = cls()
        for oid, item in value.items():
            if not isinstance(item, list) or len(item) != 2 or not isinstance(item[1], str):
                raise SyncError('invalid object record')
            try:
                data = base64.b64decode(item[1], validate=True)
            except (ValueError, TypeError) as exc:
                raise SyncError('invalid object encoding') from exc
            result.add(item[0], data, oid)
        return result

    def commit(self, oid):
        data = self.get(oid, 'commit')
        header = data.split(b'\n\n', 1)[0].splitlines()
        trees = [line[5:].decode('ascii') for line in header if line.startswith(b'tree ')]
        parents = [line[7:].decode('ascii') for line in header if line.startswith(b'parent ')]
        if len(trees) != 1 or not header[0].startswith(b'tree '):
            raise SyncError('invalid commit header')
        return oid_ok(trees[0]), [oid_ok(p) for p in parents]

    def files(self, commit, include):
        tree, _ = self.commit(commit)
        files = {}
        used = {commit}
        entries_seen = 0
        expanded_bytes = 0

        def walk(oid, prefix, depth):
            nonlocal entries_seen, expanded_bytes
            if depth > 32:
                raise SyncError('tree too deep')
            data = self.get(oid, 'tree')
            used.add(oid)
            pos = 0
            names = set()
            while pos < len(data):
                # A small DAG can expand into exponentially many paths even
                # with no blobs. Bound entries, not only unique object bytes.
                entries_seen += 1
                if entries_seen > MAX_OBJECTS:
                    raise SyncError('expanded tree entry budget exceeded')
                end = data.find(b'\0', pos)
                if end < 0 or end + 21 > len(data):
                    raise SyncError('malformed tree')
                try:
                    mode, raw_name = data[pos:end].split(b' ', 1)
                    name = raw_name.decode('utf-8')
                except (ValueError, UnicodeError) as exc:
                    raise SyncError('malformed tree entry') from exc
                if '/' in name or name in names:
                    raise SyncError('duplicate or compound tree entry')
                names.add(name)
                path = safe_path(prefix + name)
                child = data[end + 1 : end + 21].hex()
                pos = end + 21
                if mode == b'40000':
                    walk(child, path + '/', depth + 1)
                elif mode in (b'100644', b'100755'):
                    if not allowed(path, include):
                        raise SyncError(f'path outside allowlist: {path}')
                    content = self.get(child, 'blob')
                    used.add(child)
                    files[path] = (mode.decode(), content)
                    expanded_bytes += len(content)
                    if expanded_bytes > MAX_BYTES:
                        raise SyncError('expanded tree budget exceeded')
                else:
                    raise SyncError(f'symlink, gitlink or special mode refused: {path}')

        walk(tree, '', 0)
        return files, used

    def validate_bootstrap(self, base, include):
        files, used = self.files(base, include)
        if used != set(self.items):
            raise SyncError('bootstrap contains unrelated objects or history')
        return files

    def validate_result(self, base, head, include):
        oid_ok(base)
        current = oid_ok(head)
        used = set()
        final_files = None
        for _ in range(MAX_COMMITS + 1):
            files, tree_used = self.files(current, include)
            used.update(tree_used)
            if final_files is None:
                final_files = files
            if current == base:
                if used != set(self.items):
                    raise SyncError('result contains unreachable or unrelated objects')
                return final_files
            _, parents = self.commit(current)
            if len(parents) != 1:
                raise SyncError('result must be a linear descendant of the input commit')
            current = parents[0]
        raise SyncError('commit limit exceeded or input base not reached')


class Git:
    """Plumbing only: no checkout, filters, external diff, fetched config or hooks."""

    def __init__(self, path, *, executable='/usr/bin/git', env=None):
        self.path = str(path)
        self.executable = executable
        self.env = {k: v for k, v in os.environ.items() if not k.startswith('GIT_')}
        self.env.update(
            GIT_CONFIG_NOSYSTEM='1',
            GIT_CONFIG_GLOBAL='/dev/null',
            GIT_NO_REPLACE_OBJECTS='1',
            GIT_TERMINAL_PROMPT='0',
        )
        self.env.update(env or {})

    def run(self, *args, data=None):
        result = subprocess.run(
            [
                self.executable,
                '-c',
                'core.hooksPath=/dev/null',
                '-c',
                'core.fsmonitor=false',
                '-c',
                'core.attributesFile=/dev/null',
                '-c',
                'gc.auto=0',
                '-c',
                'maintenance.auto=false',
                '-c',
                'core.autocrlf=false',
                '-C',
                self.path,
                *args,
            ],
            input=data,
            capture_output=True,
            env=self.env,
            timeout=30,
        )
        if result.returncode:
            raise SyncError(f'Git {args[0]} failed: {result.stderr.decode(errors="replace")[:600]}')
        if len(result.stdout) > MAX_BYTES:
            raise SyncError('Git output exceeded limit')
        return result.stdout

    def head(self):
        return oid_ok(self.run('rev-parse', 'HEAD').decode().strip())

    def branch(self):
        ref = self.run('symbolic-ref', 'HEAD').decode().strip()
        if not ref.startswith('refs/heads/'):
            raise SyncError('a worker branch is required')
        return ref

    def export(self, head, base=None):
        objects = Objects()
        seen = set()

        def read(oid, kind):
            if oid in seen:
                return
            seen.add(oid)
            size = int(self.run('cat-file', '-s', oid_ok(oid)))
            if size > MAX_BYTES or objects.size + size > MAX_BYTES:
                raise SyncError('object budget exceeded')
            data = self.run('cat-file', kind, oid)
            objects.add(kind, data, oid)
            if kind == 'tree':
                pos = 0
                while pos < len(data):
                    end = data.index(b'\0', pos)
                    mode = data[pos:end].split(b' ', 1)[0]
                    child = data[end + 1 : end + 21].hex()
                    read(child, 'tree' if mode == b'40000' else 'blob')
                    pos = end + 21

        current = oid_ok(head)
        for _ in range(MAX_COMMITS + 1):
            read(current, 'commit')
            tree, parents = objects.commit(current)
            read(tree, 'tree')
            if base is None or current == base:
                return objects
            if len(parents) != 1:
                raise SyncError('only linear worker history is supported')
            current = parents[0]
        raise SyncError('commit limit exceeded')

    def import_objects(self, objects):
        for oid, (kind, data) in objects.items.items():
            actual = (
                self.run('hash-object', '-w', '-t', kind, '--stdin', data=data).decode().strip()
            )
            if actual != oid:
                raise SyncError('Git rejected an object hash')

    def snapshot(self, files, *, parent, message):
        # Build trees directly, avoiding Git's index, attributes and clean filters.
        tree = {}
        for path, (mode, content) in files.items():
            current = tree
            parts = safe_path(path).split('/')
            for part in parts[:-1]:
                current = current.setdefault(part, {})
            current[parts[-1]] = (mode, content)

        def write(kind, content):
            return (
                self.run('hash-object', '-w', '-t', kind, '--stdin', data=content).decode().strip()
            )

        def emit(entries):
            rows = []
            for name, item in entries.items():
                directory = isinstance(item, dict)
                mode, oid = (
                    ('40000', emit(item)) if directory else (item[0], write('blob', item[1]))
                )
                rows.append(
                    (
                        (name + ('/' if directory else '')).encode(),
                        mode.encode() + b' ' + name.encode() + b'\0' + bytes.fromhex(oid),
                    )
                )
            return write('tree', b''.join(row for _, row in sorted(rows)))

        root = emit(tree)
        old_tree = self.run('rev-parse', parent + '^{tree}').decode().strip()
        if root == old_tree:
            return parent
        # A controller-owned snapshot has deterministic content/identity. User
        # commits remain byte-identical objects; no history is rewritten.
        data = (
            f'tree {root}\nparent {parent}\nauthor CCB Sync <sync@localhost> 0 +0000\n'
            f'committer CCB Sync <sync@localhost> 0 +0000\n\n{message}\n'
        ).encode()
        return write('commit', data)
