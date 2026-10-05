"""No-follow, directory-FD-relative file access for both ends of a transaction."""

from __future__ import annotations

from contextlib import contextmanager
import json
import os
from pathlib import Path
import stat
import uuid

from .objects import MAX_BYTES, MAX_OBJECTS, SyncError, allowed, safe_path

DIR_FLAGS = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW


@contextmanager
def directory(path):
    # Walk from / rather than resolving symlinks before opening: intermediate
    # components are part of the boundary as well as the final component.
    path = Path(path).absolute()
    fd = os.open('/', DIR_FLAGS)
    try:
        for part in path.parts[1:]:
            next_fd = os.open(part, DIR_FLAGS, dir_fd=fd)
            os.close(fd)
            fd = next_fd
        yield fd
    finally:
        os.close(fd)


@contextmanager
def parent_fd(root_fd, relative, *, create=False):
    parts = relative.split('/')
    fd = os.dup(root_fd)
    try:
        for part in parts[:-1]:
            if create:
                try:
                    os.mkdir(part, 0o700, dir_fd=fd)
                except FileExistsError:
                    pass
            next_fd = os.open(part, DIR_FLAGS, dir_fd=fd)
            os.close(fd)
            fd = next_fd
        yield fd, parts[-1]
    finally:
        os.close(fd)


def read_at(fd, name, *, limit=MAX_BYTES):
    try:
        stream = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=fd)
    except FileNotFoundError:
        return None
    try:
        st = os.fstat(stream)
        if not stat.S_ISREG(st.st_mode) or st.st_nlink != 1 or st.st_size > limit:
            raise SyncError('not a bounded regular single-link file')
        with os.fdopen(os.dup(stream), 'rb') as source:
            data = source.read(limit + 1)
        after = os.fstat(stream)
        if len(data) > limit or (st.st_size, st.st_mtime_ns, st.st_ctime_ns) != (
            after.st_size,
            after.st_mtime_ns,
            after.st_ctime_ns,
        ):
            raise SyncError('file changed while being read')
        return ('100755' if st.st_mode & 0o111 else '100644'), data
    finally:
        os.close(stream)


def scan(root, include):
    files = {}
    total = 0
    with directory(root) as root_fd:

        def visit(fd, name, path):
            nonlocal total
            try:
                safe_path(path)
            except SyncError:
                return  # Always exclude control files and credential locations.
            try:
                st = os.stat(name, dir_fd=fd, follow_symlinks=False)
            except FileNotFoundError:
                return
            if stat.S_ISDIR(st.st_mode):
                child = os.open(name, DIR_FLAGS, dir_fd=fd)
                try:
                    for entry in sorted(os.listdir(child)):
                        visit(child, entry, path + '/' + entry)
                finally:
                    os.close(child)
            else:
                item = read_at(fd, name)
                if item is not None:
                    files[path] = item
                    total += len(item[1])
                    if total > MAX_BYTES or len(files) > MAX_OBJECTS:
                        raise SyncError('worktree file budget exceeded')

        for path in include:
            safe_path(path)
            try:
                with parent_fd(root_fd, path) as (fd, name):
                    visit(fd, name, path)
            except FileNotFoundError:
                pass
    return files


def check_scope(root, include):
    """Reject omitted project files without reading their contents or symlinks.

    Fixed control/credential/cache exclusions in safe_path remain excluded.
    Git ignore rules do not silently authorize discarding task output.
    """
    omitted = []
    visited = 0
    with directory(root) as root_fd:

        def visit(fd, name, path):
            nonlocal visited
            visited += 1
            if visited > MAX_OBJECTS:
                raise SyncError('scope inspection file budget exceeded')
            try:
                safe_path(path)
            except SyncError:
                return
            st = os.stat(name, dir_fd=fd, follow_symlinks=False)
            if stat.S_ISDIR(st.st_mode):
                child = os.open(name, DIR_FLAGS, dir_fd=fd)
                try:
                    for entry in sorted(os.listdir(child)):
                        visit(child, entry, path + '/' + entry)
                finally:
                    os.close(child)
            elif not allowed(path, include):
                omitted.append(path)

        for name in sorted(os.listdir(root_fd)):
            visit(root_fd, name, name)
    if omitted:
        raise SyncError(
            'files outside synchronization allowlist (not collected): '
            + ', '.join(omitted[:25])
            + (' ...' if len(omitted) > 25 else '')
            + '; move required output into approved paths before recovery'
        )


def apply_files(root, before, after, *, recovery=False):
    """Recoverable per-path writes with conflict checks before each mutation.

    The controller journals `applying` before this operation. Recovery accepts
    either the old or new version at every path; everything else is a conflict.
    The controller must quiesce other writers: a read/check/rename sequence is
    not an atomic compare-and-swap against an uncooperative concurrent process.
    """
    with directory(root) as root_fd:
        changes = []
        for path in sorted(set(before) | set(after)):
            safe_path(path)
            old, new = before.get(path), after.get(path)
            try:
                with parent_fd(root_fd, path) as (fd, name):
                    current = read_at(fd, name)
            except FileNotFoundError:
                current = None
            if current != old and not (recovery and current == new):
                raise SyncError(f'local edit conflict: {path}')
            if current != new:
                changes.append((path, old, new))
        for path, old, new in changes:
            with parent_fd(root_fd, path, create=new is not None) as (fd, name):
                current = read_at(fd, name)
                if current != old and not (recovery and current == new):
                    raise SyncError(f'concurrent edit conflict: {path}')
                if current == new:
                    continue
                if new is None:
                    os.unlink(name, dir_fd=fd)
                else:
                    temp = '.ccb-sync-' + uuid.uuid4().hex
                    handle = os.open(
                        temp,
                        os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                        0o700 if new[0] == '100755' else 0o600,
                        dir_fd=fd,
                    )
                    try:
                        with os.fdopen(handle, 'wb') as output:
                            output.write(new[1])
                            output.flush()
                            os.fsync(output.fileno())
                        os.rename(temp, name, src_dir_fd=fd, dst_dir_fd=fd)
                    finally:
                        try:
                            os.unlink(temp, dir_fd=fd)
                        except FileNotFoundError:
                            pass
                os.fsync(fd)


def read_json(path, *, limit=48 * 1024 * 1024):
    with directory(Path(path).parent) as fd:
        item = read_at(fd, Path(path).name, limit=limit)
        if item is None:
            raise FileNotFoundError(path)
        if len(item[1]) > limit:
            raise SyncError('JSON file too large')
        return json.loads(item[1])


def write_json(path, value):
    path = Path(path)
    data = json.dumps(value, sort_keys=True, separators=(',', ':')).encode()
    # Internal state is never exchanged as a directory. All names here are
    # controller generated, in a controller-owned directory outside worktrees.
    with directory(path.parent) as fd:
        temp = '.state-' + uuid.uuid4().hex
        handle = os.open(
            temp, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=fd
        )
        try:
            with os.fdopen(handle, 'wb') as output:
                output.write(data)
                output.flush()
                os.fsync(output.fileno())
            os.rename(temp, path.name, src_dir_fd=fd, dst_dir_fd=fd)
            os.fsync(fd)
        finally:
            try:
                os.unlink(temp, dir_fd=fd)
            except FileNotFoundError:
                pass
