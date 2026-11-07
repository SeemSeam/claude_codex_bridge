"""Protect managed runtime data from OpenCode's source snapshot recursion."""
from __future__ import annotations

import os
from pathlib import Path
import subprocess

# PathLayout's volatile trees; config, project memory and provider profiles stay visible.
VOLATILE_TREES = ('agents', 'ccbd', 'runtime', 'state', 'shared-cache', 'workspaces')


def _git(root: Path, *args: str):
    env = dict(os.environ)
    for key in ('GIT_DIR', 'GIT_WORK_TREE', 'GIT_COMMON_DIR', 'GIT_INDEX_FILE'):
        env.pop(key, None)
    return subprocess.run(['git', '-C', str(root), *args], env=env,
                          capture_output=True, timeout=3, check=False)


def ensure_runtime_excludes(directory: Path) -> tuple[str, ...]:
    """Run at managed launch, never during read-only project resolution.

    Returns sanitized warnings; never changes the index, tracked files or Provider
    configuration. Git resolves linked-worktree metadata instead of guessing .git/.
    """
    lock = None
    owned_lock = False
    try:
        root = Path(directory).resolve()
        result = _git(root, 'rev-parse', '--show-toplevel')
        if result.returncode:
            return ()  # Non-Git workspace.
        toplevel = Path(os.fsdecode(result.stdout).rstrip('\n')).resolve()
        relative = root.relative_to(toplevel)
        runtime = (relative / '.ccb').as_posix()
        if any(char in runtime for char in '\n\r'):
            return ('OpenCode snapshot protection: unsupported Git path; configure local exclusions manually.',)
        paths = [runtime + '/' + name for name in VOLATILE_TREES]
        def escaped(value):
            return ''.join('\\' + char if char in '\\*?[]#! ' else char for char in value)
        patterns = ['/' + escaped(path) + '/' for path in paths]
        result = _git(root, 'rev-parse', '--git-path', 'info/exclude')
        if result.returncode:
            raise OSError()
        exclude = Path(os.fsdecode(result.stdout).rstrip('\n'))
        if not exclude.is_absolute():
            exclude = root / exclude
        if exclude.is_symlink():
            raise OSError()
        exclude.parent.mkdir(parents=True, exist_ok=True)
        lock = exclude.with_name(exclude.name + '.lock')
        descriptor = os.open(lock, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        owned_lock = True
        try:
            previous = exclude.read_bytes() if exclude.exists() else b''
            existing = previous.decode('utf-8', errors='surrogateescape').splitlines()
            # Keep existing lines byte for byte. Add only missing patterns.
            missing = [pattern for pattern in patterns if pattern not in existing]
            if missing:
                addition = ('\n' if previous and not previous.endswith(b'\n') else '')
                addition += '# CCB volatile runtime (OpenCode snapshot protection)\n' + '\n'.join(missing) + '\n'
                with os.fdopen(descriptor, 'wb') as output:
                    descriptor = None
                    output.write(previous + addition.encode('utf-8', errors='surrogateescape'))
                if exclude.exists():
                    os.chmod(lock, exclude.stat().st_mode & 0o777)
                os.replace(lock, exclude)
            probes = [path + '/ccb-runtime-probe' for path in paths]
            checked = _git(toplevel, 'check-ignore', '--no-index', '--', *probes)
            ignored = os.fsdecode(checked.stdout).splitlines()
            warnings = []
            if checked.returncode != 0 or len(ignored) != len(probes):
                warnings.append('OpenCode snapshot protection is overridden by Git ignore rules; exclude CCB volatile runtime paths locally.')
            tracked = _git(toplevel, 'ls-files', '-z', '--', *(':(literal)' + path for path in paths))
            if tracked.stdout:
                warnings.append('CCB runtime files are already Git-tracked; local exclusions do not remove them from the index. Review tracked runtime files manually.')
            return tuple(warnings)
        finally:
            if descriptor is not None:
                os.close(descriptor)
    except (OSError, ValueError, subprocess.SubprocessError):
        return ('OpenCode snapshot protection could not update Git info/exclude; exclude CCB volatile runtime paths locally before running tasks.',)
    finally:
        if owned_lock and lock is not None:
            lock.unlink(missing_ok=True)
