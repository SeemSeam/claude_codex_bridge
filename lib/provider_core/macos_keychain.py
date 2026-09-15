from __future__ import annotations

import os
import plistlib
import shutil
import stat
import subprocess
from pathlib import Path

from provider_core.one_way_inheritance import ensure_private_descendant_directory
from provider_core.platform_info import is_macos
from storage.atomic import atomic_write_text

_KEYCHAIN_REL = Path('Library') / 'Keychains' / 'ccb-provider.keychain-db'
_PREFERENCES_REL = Path('Library') / 'Preferences' / 'com.apple.security.plist'
_LOGIN_KEYCHAIN_GUID = '{87191ca3-0fc9-11d4-849a-000502b52122}'


def private_keychain_path(home: Path) -> Path:
    return Path(home).expanduser() / _KEYCHAIN_REL


def keychain_preferences_path(home: Path) -> Path:
    return Path(home).expanduser() / _PREFERENCES_REL


def prepare_private_keychain(home: Path) -> Path | None:
    if not is_macos():
        return None
    home = Path(home).expanduser().resolve()
    keychain_dir = ensure_private_descendant_directory(home, _KEYCHAIN_REL.parent)
    keychain = keychain_dir / _KEYCHAIN_REL.name
    _require_private_regular_file(keychain)
    # Security.framework may update this plist during create-keychain, so
    # detach any inherited Preferences alias before the first security write.
    _write_preferences(home, keychain)
    security = shutil.which('security') or '/usr/bin/security'
    if not keychain.exists():
        _run_security(
            security,
            ['create-keychain', '-p', '', str(keychain)],
            home=home,
            error='cannot create agent-private macOS Keychain',
        )
        _require_private_regular_file(keychain)
    os.chmod(keychain, 0o600)
    _run_security(
        security,
        ['unlock-keychain', '-p', '', str(keychain)],
        home=home,
        error='cannot unlock agent-private macOS Keychain',
    )
    _run_security(
        security,
        ['set-keychain-settings', str(keychain)],
        home=home,
        error='cannot configure agent-private macOS Keychain',
    )
    default_result = _run_security(
        security,
        ['default-keychain', '-d', 'user'],
        home=home,
        error='managed HOME cannot resolve agent-private macOS Keychain',
    )
    if _resolved_keychain(default_result.stdout) != keychain.resolve():
        raise RuntimeError('managed HOME resolved an unexpected default macOS Keychain')
    search_result = _run_security(
        security,
        ['list-keychains', '-d', 'user'],
        home=home,
        error='managed HOME cannot resolve agent-private macOS Keychain search list',
    )
    search_list = [
        resolved
        for line in search_result.stdout.splitlines()
        if (resolved := _resolved_keychain(line)) is not None
    ]
    if search_list != [keychain.resolve()]:
        raise RuntimeError('managed HOME resolved an unexpected macOS Keychain search list')
    return keychain


def remove_keychain_preferences(home: Path) -> None:
    preferences_dir = ensure_private_descendant_directory(
        Path(home).expanduser(),
        _PREFERENCES_REL.parent,
    )
    target = preferences_dir / _PREFERENCES_REL.name
    if target.is_symlink() or target.is_file():
        target.unlink()


def _require_private_regular_file(path: Path) -> None:
    try:
        metadata = path.lstat()
    except FileNotFoundError:
        return
    getuid = getattr(os, 'getuid', None)
    current_uid = getuid() if callable(getuid) else None
    if (
        stat.S_ISLNK(metadata.st_mode)
        or not stat.S_ISREG(metadata.st_mode)
        or metadata.st_nlink != 1
        or (current_uid is not None and metadata.st_uid != current_uid)
    ):
        raise RuntimeError(f'agent-private macOS Keychain path must be a private regular file: {path}')


def _write_preferences(home: Path, keychain: Path) -> None:
    preferences_dir = ensure_private_descendant_directory(home, _PREFERENCES_REL.parent)
    entry = {
        'DbName': str(keychain),
        'GUID': _LOGIN_KEYCHAIN_GUID,
        'SubserviceType': 6,
    }
    atomic_write_text(
        preferences_dir / _PREFERENCES_REL.name,
        plistlib.dumps(
            {
                'DefaultKeychain': [dict(entry)],
                'DLDBSearchList': [dict(entry)],
            },
            fmt=plistlib.FMT_XML,
        ).decode('utf-8'),
    )


def _run_security(
    security: str,
    args: list[str],
    *,
    error: str,
    home: Path,
):
    env = dict(os.environ)
    env['HOME'] = str(home)
    try:
        result = subprocess.run(
            [security, *args],
            check=False,
            capture_output=True,
            text=True,
            timeout=5,
            env=env,
        )
    except Exception as exc:
        raise RuntimeError(f'{error}: {type(exc).__name__}') from None
    if result.returncode != 0:
        raise RuntimeError(f'{error}: security exited {result.returncode}')
    return result


def _resolved_keychain(value: str) -> Path | None:
    raw = _unquote(str(value or '').strip())
    return Path(raw).expanduser().resolve() if raw else None


def _unquote(value: str) -> str:
    return value[1:-1] if len(value) >= 2 and value[0] == value[-1] == '"' else value


__all__ = [
    'keychain_preferences_path',
    'prepare_private_keychain',
    'private_keychain_path',
    'remove_keychain_preferences',
]
