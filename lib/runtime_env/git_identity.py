from __future__ import annotations

from collections.abc import Mapping
import os
from pathlib import Path
import re
import subprocess

from provider_core.source_home import current_provider_source_home


GIT_IDENTITY_ENV_KEYS = frozenset(
    {
        'GIT_AUTHOR_NAME',
        'GIT_AUTHOR_EMAIL',
        'GIT_COMMITTER_NAME',
        'GIT_COMMITTER_EMAIL',
    }
)

_GITCONFIG_USER_FIELD = {
    'user.name': 'name',
    'user.email': 'email',
}


def managed_git_identity_env(
    *,
    environ: Mapping[str, object] | None = None,
    source_home: Path | None = None,
) -> dict[str, str]:
    """Fill missing Git author/committer identity for managed provider homes.

    Explicit ``GIT_*`` values in ``environ`` always win. Missing values are
    resolved from the real account/source home global Git config so isolated
    provider ``HOME`` rewrites do not fall back to username@hostname inference.
    """
    source = os.environ if environ is None else environ
    existing = {
        key: value
        for key in GIT_IDENTITY_ENV_KEYS
        if (value := str(source.get(key) or '').strip())
    }
    if len(existing) == len(GIT_IDENTITY_ENV_KEYS):
        return {}

    name = existing.get('GIT_AUTHOR_NAME') or existing.get('GIT_COMMITTER_NAME')
    email = existing.get('GIT_AUTHOR_EMAIL') or existing.get('GIT_COMMITTER_EMAIL')
    if name is None or email is None:
        home = _resolve_source_home(source_home)
        if home is not None:
            if name is None:
                name = _git_global_config_value('user.name', source_home=home)
            if email is None:
                email = _git_global_config_value('user.email', source_home=home)

    filled: dict[str, str] = {}
    if name:
        for key in ('GIT_AUTHOR_NAME', 'GIT_COMMITTER_NAME'):
            if key not in existing:
                filled[key] = name
    if email:
        for key in ('GIT_AUTHOR_EMAIL', 'GIT_COMMITTER_EMAIL'):
            if key not in existing:
                filled[key] = email
    return filled


def _resolve_source_home(source_home: Path | None) -> Path | None:
    if source_home is not None:
        try:
            return Path(source_home).expanduser()
        except Exception:
            return None
    try:
        return current_provider_source_home()
    except Exception:
        return None


def _git_global_config_value(name: str, *, source_home: Path) -> str | None:
    env = {
        str(key): str(value)
        for key, value in os.environ.items()
        if value is not None
    }
    env['HOME'] = str(source_home)
    env['GIT_CONFIG_NOSYSTEM'] = '1'
    # Avoid inheriting an ambient GIT_CONFIG_GLOBAL that points at a managed home.
    env.pop('GIT_CONFIG_GLOBAL', None)
    try:
        completed = subprocess.run(
            ['git', 'config', '--global', '--get', name],
            capture_output=True,
            text=True,
            check=False,
            timeout=5,
            env=env,
        )
    except (OSError, subprocess.SubprocessError):
        return _read_gitconfig_user_field(source_home / '.gitconfig', name)
    value = (completed.stdout or '').strip()
    if completed.returncode == 0 and value:
        return value
    return _read_gitconfig_user_field(source_home / '.gitconfig', name)


def _read_gitconfig_user_field(path: Path, name: str) -> str | None:
    field = _GITCONFIG_USER_FIELD.get(name)
    if field is None or not path.is_file():
        return None
    try:
        text = path.read_text(encoding='utf-8')
    except OSError:
        return None
    in_user = False
    pattern = re.compile(rf'^\s*{re.escape(field)}\s*=\s*(.+?)\s*$')
    for raw_line in text.splitlines():
        line = raw_line.split(';', 1)[0].split('#', 1)[0].strip()
        if not line:
            continue
        if line.startswith('[') and line.endswith(']'):
            in_user = line[1:-1].strip().lower() == 'user'
            continue
        if not in_user:
            continue
        match = pattern.match(line)
        if match:
            value = match.group(1).strip().strip('"').strip("'")
            return value or None
    return None


__all__ = ['GIT_IDENTITY_ENV_KEYS', 'managed_git_identity_env']
