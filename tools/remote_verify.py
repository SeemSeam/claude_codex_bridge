#!/usr/bin/env python3
"""Run returned project code in a disposable, offline Linux filesystem view.

The worktree is read-only. No host home, /mnt, /run, /init, Git control data,
agent socket or credential environment is present. Failure never falls back to
running a test directly on the controller.
"""

import argparse
import ctypes
import errno
import os
from pathlib import Path
import sys


def seccomp_fd():
    lib = ctypes.CDLL('libseccomp.so.2', use_errno=True)
    lib.seccomp_init.argtypes = [ctypes.c_uint32]
    lib.seccomp_init.restype = ctypes.c_void_p
    lib.seccomp_syscall_resolve_name.argtypes = [ctypes.c_char_p]
    lib.seccomp_syscall_resolve_name.restype = ctypes.c_int
    lib.seccomp_rule_add.argtypes = [ctypes.c_void_p, ctypes.c_uint32, ctypes.c_int, ctypes.c_uint]
    lib.seccomp_export_bpf.argtypes = [ctypes.c_void_p, ctypes.c_int]
    lib.seccomp_release.argtypes = [ctypes.c_void_p]
    ctx = lib.seccomp_init(0x7FFF0000)
    if not ctx:
        raise RuntimeError('seccomp initialization failed')
    fd = os.memfd_create('ccb-verification-seccomp')
    try:
        names = (
            'mount',
            'umount2',
            'setns',
            'unshare',
            'bpf',
            'ptrace',
            'process_vm_readv',
            'process_vm_writev',
            'keyctl',
            'add_key',
            'request_key',
            'perf_event_open',
            'userfaultfd',
            'kexec_load',
            'init_module',
            'finit_module',
            'delete_module',
            'reboot',
            'open_by_handle_at',
            'io_uring_setup',
        )
        for name in names:
            number = lib.seccomp_syscall_resolve_name(name.encode())
            if number >= 0 and lib.seccomp_rule_add(ctx, 0x00050000 | errno.EPERM, number, 0):
                raise RuntimeError('seccomp rule failed: ' + name)
        number = lib.seccomp_syscall_resolve_name(b'clone3')
        if number >= 0 and lib.seccomp_rule_add(ctx, 0x00050000 | errno.ENOSYS, number, 0):
            raise RuntimeError('seccomp clone3 rule failed')
        if lib.seccomp_export_bpf(ctx, fd):
            raise RuntimeError('seccomp export failed')
        os.lseek(fd, 0, os.SEEK_SET)
        os.set_inheritable(fd, True)
        return fd
    finally:
        lib.seccomp_release(ctx)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--workspace', type=Path, default=Path.cwd())
    parser.add_argument('command', nargs=argparse.REMAINDER)
    args = parser.parse_args()
    command = args.command[1:] if args.command[:1] == ['--'] else args.command
    if not command or not command[0].startswith('/usr/bin/'):
        parser.error('use an explicit /usr/bin executable and an argv, without a shell wrapper')
    workspace = args.workspace.resolve(strict=True)
    if workspace == Path('/') or not workspace.is_dir():
        parser.error('a project workspace is required')
    fd = seccomp_fd()
    argv = [
        '/usr/bin/bwrap',
        '--unshare-user',
        '--unshare-all',
        '--disable-userns',
        '--die-with-parent',
        '--new-session',
        '--cap-drop',
        'ALL',
        '--clearenv',
        '--ro-bind',
        '/usr',
        '/usr',
        '--symlink',
        'usr/bin',
        '/bin',
        '--symlink',
        'usr/sbin',
        '/sbin',
    ]
    for path in ('/lib', '/lib64'):
        if Path(path).exists():
            argv += ['--ro-bind', path, path]
    argv += [
        '--proc',
        '/proc',
        '--dev',
        '/dev',
        '--tmpfs',
        '/tmp',
        '--tmpfs',
        '/run',
        '--dir',
        '/home/sandbox',
        '--dir',
        '/etc',
        '--ro-bind',
        str(workspace),
        '/work',
    ]
    for name in ('.git', '.ccb', '.claude', '.codex', '.ssh'):
        path = workspace / name
        if path.is_dir():
            argv += ['--tmpfs', '/work/' + name]
        elif path.exists() or path.is_symlink():
            argv += ['--ro-bind', '/dev/null', '/work/' + name]
    argv += [
        '--chdir',
        '/work',
        '--setenv',
        'HOME',
        '/home/sandbox',
        '--setenv',
        'PATH',
        '/usr/bin:/bin',
        '--setenv',
        'LANG',
        'C.UTF-8',
        '--setenv',
        'TZ',
        'UTC',
        '--setenv',
        'PYTHONDONTWRITEBYTECODE',
        '1',
        '--seccomp',
        str(fd),
        '--',
        *command,
    ]
    libc = ctypes.CDLL(None, use_errno=True)
    if libc.prctl(38, 1, 0, 0, 0):
        raise RuntimeError('NoNewPrivs failed')
    os.execve(argv[0], argv, {'PATH': '/usr/bin:/bin', 'LANG': 'C.UTF-8'})


if __name__ == '__main__':
    main()
