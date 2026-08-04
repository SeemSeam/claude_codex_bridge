from __future__ import annotations

import json
from pathlib import Path
import os
import shutil

_LEGACY_BIN_DIR = Path.home() / '.local' / 'bin'


def build_tmux_backend(socket_path: str):
    try:
        from terminal_runtime.backend_selection import make_terminal_backend

        return make_terminal_backend(socket_path=socket_path)
    except Exception:
        return None


def current_install_root() -> Path:
    return Path(__file__).resolve().parents[4]


def _candidate_roots() -> tuple[Path, ...]:
    roots: list[Path] = []
    runtime_root = current_install_root()
    roots.append(runtime_root)

    ccb_path = resolve_ccb_executable()
    if ccb_path is not None:
        roots.append(ccb_path.parent)

    deduped: list[Path] = []
    seen: set[Path] = set()
    for root in roots:
        resolved = root.resolve()
        if resolved in seen:
            continue
        seen.add(resolved)
        deduped.append(resolved)
    return tuple(deduped)


def resolve_ccb_executable() -> Path | None:
    runtime_ccb = current_install_root() / 'ccb'
    if runtime_ccb.is_file():
        return runtime_ccb

    installed = shutil.which('ccb')
    if installed:
        path = Path(installed).expanduser()
        if path.is_file():
            return path.resolve()

    legacy = _LEGACY_BIN_DIR / 'ccb'
    if legacy.is_file():
        return legacy
    return None


def script_path(script_name: str) -> str | None:
    for root in _candidate_roots():
        runtime_copy = root / 'config' / script_name
        if runtime_copy.is_file():
            return str(runtime_copy)

    installed = shutil.which(script_name)
    if installed:
        path = Path(installed).expanduser()
        if path.is_file():
            return str(path.resolve())

    legacy = _LEGACY_BIN_DIR / script_name
    if legacy.is_file():
        return str(legacy)

    repo_copy = current_install_root() / 'config' / script_name
    if repo_copy.is_file():
        return str(repo_copy)
    return None


def detect_ccb_version() -> str:
    env_version = str(os.environ.get('CCB_VERSION') or '').strip()
    if env_version:
        return env_version

    for root in _candidate_roots():
        version = _read_local_version(root)
        if version:
            return version
    return '?'


def _read_local_version(root: Path) -> str:
    build_info_version = _read_build_info_version(root / 'BUILD_INFO.json')
    if build_info_version:
        return build_info_version
    version_file_value = _read_version_file(root / 'VERSION')
    if version_file_value:
        return version_file_value
    return _read_embedded_ccb_version(root / 'ccb')


def _read_build_info_version(path: Path) -> str:
    try:
        payload = json.loads(path.read_text(encoding='utf-8', errors='replace'))
    except Exception:
        return ''
    if not isinstance(payload, dict):
        return ''
    return str(payload.get('version') or '').strip()


def _read_version_file(path: Path) -> str:
    try:
        return path.read_text(encoding='utf-8', errors='replace').strip()
    except Exception:
        return ''


def _read_embedded_ccb_version(path: Path) -> str:
    try:
        lines = path.read_text(encoding='utf-8', errors='replace').splitlines()[:60]
    except Exception:
        return ''
    for line in lines:
        text = line.strip()
        if not text.startswith('VERSION') or '=' not in text:
            continue
        return text.split('=', 1)[1].strip().strip('"').strip("'")
    return ''


__all__ = [
    'build_tmux_backend',
    'current_install_root',
    'detect_ccb_version',
    'resolve_ccb_executable',
    'script_path',
]
