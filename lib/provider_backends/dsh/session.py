from __future__ import annotations

from pathlib import Path
from typing import Optional

from provider_backends.native_cli_support.session import (
    build_native_session_binding,
    compute_session_key as native_compute_session_key,
    find_project_session_file as native_find_project_session_file,
    load_native_project_session,
    make_session_class,
)


SESSION_FILENAME = '.dsh-session'
DshProjectSession = make_session_class('dsh')


def find_project_session_file(work_dir: Path, instance: Optional[str] = None) -> Optional[Path]:
    return native_find_project_session_file(
        work_dir,
        provider='dsh',
        session_filename=SESSION_FILENAME,
        instance=instance,
    )


def load_project_session(work_dir: Path, instance: Optional[str] = None):
    return load_native_project_session(
        work_dir,
        provider='dsh',
        session_filename=SESSION_FILENAME,
        instance=instance,
    )


def compute_session_key(session, instance: Optional[str] = None) -> str:
    return native_compute_session_key(session, provider='dsh', instance=instance)


def build_session_binding():
    return build_native_session_binding(provider='dsh', session_filename=SESSION_FILENAME)


__all__ = [
    'DshProjectSession',
    'SESSION_FILENAME',
    'build_session_binding',
    'compute_session_key',
    'find_project_session_file',
    'load_project_session',
]
