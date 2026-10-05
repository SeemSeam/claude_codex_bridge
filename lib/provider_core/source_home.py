from __future__ import annotations

import os
from pathlib import Path

try:
    import pwd
except Exception:  # pragma: no cover - Windows fallback
    pwd = None


_MANAGED_PROVIDER_MARKERS = (
    'CCB_CALLER_ACTOR',
    'CCB_CALLER_RUNTIME_DIR',
    'CCB_SESSION_FILE',
    'CCB_SESSION_ID',
)


def current_provider_source_home() -> Path:
    explicit_provider = _env_path('CCB_PROVIDER_SOURCE_HOME')
    if explicit_provider is not None:
        return explicit_provider
    explicit = _env_path('CCB_SOURCE_HOME')
    if explicit is not None:
        return explicit

    env_home = _env_path('HOME')
    managed_process = _is_managed_provider_process()
    if (
        env_home is not None
        and not managed_process
        and not _looks_like_ccb_provider_home(env_home)
    ):
        return env_home

    account_home = _account_home_root(managed_process=managed_process)
    if account_home is not None:
        return account_home

    if env_home is not None and not managed_process:
        return env_home
    if managed_process:
        raise RuntimeError(
            'cannot resolve the source user home from a managed provider environment'
        )
    return Path.home().expanduser()


def _env_path(name: str) -> Path | None:
    raw = str(os.environ.get(name) or '').strip()
    if not raw:
        return None
    try:
        return Path(raw).expanduser()
    except Exception:
        return None


def _account_home_root(*, managed_process: bool) -> Path | None:
    if pwd is None:
        native_profile = _native_windows_profile_root()
        if native_profile is not None:
            return native_profile
        profile = _env_path('USERPROFILE')
        if (
            profile is not None
            and not managed_process
            and not _looks_like_ccb_provider_home(profile)
        ):
            return profile
        drive = str(os.environ.get('HOMEDRIVE') or '').strip()
        relative = str(os.environ.get('HOMEPATH') or '').strip()
        if drive and relative:
            try:
                return Path(f'{drive}{relative}').expanduser()
            except Exception:
                pass
        return profile if profile is not None and not managed_process else None
    try:
        raw = pwd.getpwuid(os.getuid()).pw_dir
    except Exception:
        profile = _env_path('USERPROFILE')
        if (
            profile is not None
            and not managed_process
            and not _looks_like_ccb_provider_home(profile)
        ):
            return profile
        return None
    try:
        return Path(raw).expanduser() if raw else None
    except Exception:
        return None


def _native_windows_profile_root() -> Path | None:
    if os.name != 'nt':
        return None
    try:
        import ctypes

        buffer = ctypes.create_unicode_buffer(32768)
        # CSIDL_PROFILE asks the shell for the current account's real profile
        # path and does not depend on a managed process overriding USERPROFILE.
        result = ctypes.windll.shell32.SHGetFolderPathW(
            None,
            0x0028,
            None,
            0,
            buffer,
        )
        value = str(buffer.value or '').strip()
        return Path(value).expanduser() if result == 0 and value else None
    except Exception:
        return None


def _is_managed_provider_process() -> bool:
    return any(str(os.environ.get(key) or '').strip() for key in _MANAGED_PROVIDER_MARKERS)


def _looks_like_ccb_provider_home(path: Path) -> bool:
    parts = Path(path).expanduser().parts
    for index in range(0, max(len(parts) - 4, 0)):
        if parts[index] != 'agents':
            continue
        if parts[index + 2] == 'provider-state' and parts[index + 4] == 'home':
            return True
    return False


__all__ = ['current_provider_source_home']
