from __future__ import annotations

import hashlib
import json
from pathlib import Path
from urllib.parse import urlparse

from provider_core.one_way_inheritance import copy_regular_file
from provider_core.projected_assets import (
    copy_projected_tree_to_cache,
    seed_projected_file,
    tree_content_fingerprint,
)
from storage.atomic import atomic_write_text
from storage.paths import ensure_provider_user_cache_dir


_CACHE_PROJECTION_LABEL = 'pi-inherited-profile-asset'
_FILE_HASH_CHUNK_SIZE = 64 * 1024


def materialize_pi_config(
    source_home: Path,
    target_home: Path,
    *,
    profile=None,
    shared_cache_root: Path | None = None,
) -> None:
    """Project Pi configuration and immutable extension authority one way."""
    if not _inherits_config(profile):
        return

    source_agent = Path(source_home).expanduser() / '.pi' / 'agent'
    target_agent = Path(target_home).expanduser()
    copy_regular_file(source_agent / 'my-pi-setup.json', target_agent / 'my-pi-setup.json')

    source_settings = source_agent / 'settings.json'
    if not source_settings.is_file():
        return
    settings = _read_json_object(source_settings)
    if settings is None:
        copy_regular_file(source_settings, target_agent / 'settings.json')
        return

    cache_root = (
        Path(shared_cache_root).expanduser()
        if shared_cache_root is not None
        else ensure_provider_user_cache_dir('pi')
    )
    asset_root = cache_root / 'profile-assets'
    npm_source = source_agent / 'npm' / 'node_modules'
    npm_target = None
    if _configured_npm_package_names(settings):
        npm_target = _snapshot_tree(
            npm_source,
            cache_root=asset_root,
            category='npm-trees',
            target_name='node_modules',
            digest=_npm_install_fingerprint(source_agent / 'npm', npm_source),
        )

    package_roots = _configured_package_roots(settings, source_agent)
    direct_extensions = _snapshot_direct_extensions(
        source_agent / 'extensions',
        cache_root=asset_root,
        package_roots=package_roots,
    )
    settings['packages'] = _rewrite_packages(
        settings.get('packages'),
        source_agent=source_agent,
        cache_root=asset_root,
        npm_target=npm_target,
        direct_extensions=direct_extensions,
    )
    settings['extensions'] = _rewrite_extensions(
        settings.get('extensions'),
        source_agent=source_agent,
        cache_root=asset_root,
        direct_extensions=tuple(direct_extensions.values()),
    )
    atomic_write_text(
        target_agent / 'settings.json',
        json.dumps(settings, ensure_ascii=False, indent=2) + '\n',
    )


def _snapshot_direct_extensions(
    source_root: Path,
    *,
    cache_root: Path,
    package_roots: set[Path],
) -> dict[Path, Path]:
    projected: dict[Path, Path] = {}
    if not source_root.is_dir():
        return projected
    for source_entry in sorted(source_root.iterdir(), key=lambda path: path.name):
        if source_entry.name.startswith('.'):
            continue
        try:
            resolved_source = source_entry.resolve(strict=True)
        except (OSError, RuntimeError):
            continue
        if resolved_source in package_roots:
            continue
        if resolved_source.is_dir():
            target_entry = _snapshot_tree(
                resolved_source,
                cache_root=cache_root,
                category='direct-extensions',
            )
        elif resolved_source.is_file() and resolved_source.suffix in {'.js', '.ts'}:
            target_entry = _snapshot_file(
                resolved_source,
                cache_root=cache_root,
                category='direct-extension-files',
            )
        else:
            continue
        if target_entry is not None:
            projected[resolved_source] = target_entry
    return projected


def _rewrite_packages(
    raw_packages: object,
    *,
    source_agent: Path,
    cache_root: Path,
    npm_target: Path | None,
    direct_extensions: dict[Path, Path],
) -> list[object]:
    if not isinstance(raw_packages, list):
        return []
    rewritten: list[object] = []
    for entry in raw_packages:
        source = (
            entry
            if isinstance(entry, str)
            else entry.get('source')
            if isinstance(entry, dict)
            else None
        )
        if not isinstance(source, str) or not source.strip():
            rewritten.append(_clone_json_value(entry))
            continue
        projected_source = _project_package_source(
            source,
            source_agent=source_agent,
            cache_root=cache_root,
            npm_target=npm_target,
            direct_extensions=direct_extensions,
        )
        if isinstance(entry, str):
            rewritten.append(projected_source)
        else:
            updated = _clone_json_value(entry)
            if isinstance(updated, dict):
                updated['source'] = projected_source
            rewritten.append(updated)
    return rewritten


def _project_package_source(
    source: str,
    *,
    source_agent: Path,
    cache_root: Path,
    npm_target: Path | None,
    direct_extensions: dict[Path, Path],
) -> str:
    npm_name = _npm_package_name(source)
    if npm_name:
        candidate = npm_target / npm_name if npm_target is not None else None
        return str(candidate) if candidate is not None and candidate.exists() else source

    git_parts = _git_package_parts(source)
    if git_parts:
        installed = (source_agent / 'git' / git_parts[0] / git_parts[1]).resolve(strict=False)
        target = _snapshot_tree(installed, cache_root=cache_root, category='git-packages')
        return str(target) if target is not None else source

    resolved = _resolve_source_path(source, source_agent)
    direct = direct_extensions.get(resolved)
    if direct is not None:
        return str(direct)
    if resolved.is_dir():
        target = _snapshot_tree(resolved, cache_root=cache_root, category='local-packages')
    elif resolved.is_file():
        target = _snapshot_file(resolved, cache_root=cache_root, category='local-package-files')
    else:
        return source
    return str(target) if target is not None else source


def _rewrite_extensions(
    raw_extensions: object,
    *,
    source_agent: Path,
    cache_root: Path,
    direct_extensions: tuple[Path, ...],
) -> list[object]:
    entries: list[object] = []
    raw = raw_extensions if isinstance(raw_extensions, list) else []
    for entry in raw:
        if not isinstance(entry, str) or _is_resource_pattern(entry):
            entries.append(_clone_json_value(entry))
            continue
        resolved = _resolve_source_path(entry, source_agent)
        if resolved.is_dir():
            target = _snapshot_tree(resolved, cache_root=cache_root, category='explicit-extensions')
        elif resolved.is_file():
            target = _snapshot_file(resolved, cache_root=cache_root, category='explicit-extension-files')
        else:
            target = None
        entries.append(str(target) if target is not None else entry)
    if '!extensions/**' not in entries:
        entries.append('!extensions/**')
    for path in direct_extensions:
        value = str(path)
        if value not in entries:
            entries.append(value)
    return entries


def _snapshot_tree(
    source: Path,
    *,
    cache_root: Path,
    category: str,
    target_name: str | None = None,
    digest: str | None = None,
) -> Path | None:
    source_path = Path(source).expanduser()
    if not source_path.is_dir():
        return None
    content_digest = digest or tree_content_fingerprint(source_path)
    if not content_digest:
        return None
    target = cache_root / category / content_digest / (target_name or source_path.name)
    if not copy_projected_tree_to_cache(source_path, target, label=_CACHE_PROJECTION_LABEL):
        return None
    return target


def _snapshot_file(source: Path, *, cache_root: Path, category: str) -> Path | None:
    source_path = Path(source).expanduser()
    digest = _file_content_fingerprint(source_path)
    if not digest:
        return None
    target = cache_root / category / digest / source_path.name
    if not seed_projected_file(source_path, target, label=_CACHE_PROJECTION_LABEL):
        return None
    return target


def _file_content_fingerprint(path: Path) -> str:
    digest = hashlib.sha256()
    try:
        with path.open('rb') as handle:
            for chunk in iter(lambda: handle.read(_FILE_HASH_CHUNK_SIZE), b''):
                digest.update(chunk)
    except OSError:
        return ''
    return digest.hexdigest()


def _npm_install_fingerprint(npm_root: Path, node_modules: Path) -> str:
    if not node_modules.is_dir():
        return ''
    digest = hashlib.sha256()
    found = False
    for path in (
        npm_root / 'package.json',
        npm_root / 'package-lock.json',
        node_modules / '.package-lock.json',
    ):
        if not path.is_file() or path.is_symlink():
            continue
        found = True
        digest.update(path.name.encode('utf-8'))
        digest.update(b'\0')
        try:
            with path.open('rb') as handle:
                for chunk in iter(lambda: handle.read(_FILE_HASH_CHUNK_SIZE), b''):
                    digest.update(chunk)
        except OSError:
            return ''
        digest.update(b'\0')
    return digest.hexdigest() if found else ''


def _is_resource_pattern(value: str) -> bool:
    return value.startswith(('!', '+', '-')) or '*' in value or '?' in value


def _configured_package_roots(settings: dict[str, object], source_agent: Path) -> set[Path]:
    roots: set[Path] = set()
    packages = settings.get('packages')
    if not isinstance(packages, list):
        return roots
    for entry in packages:
        source = (
            entry
            if isinstance(entry, str)
            else entry.get('source')
            if isinstance(entry, dict)
            else None
        )
        if not isinstance(source, str):
            continue
        npm_name = _npm_package_name(source)
        if npm_name:
            roots.add((source_agent / 'npm' / 'node_modules' / npm_name).resolve(strict=False))
            continue
        git_parts = _git_package_parts(source)
        if git_parts:
            roots.add((source_agent / 'git' / git_parts[0] / git_parts[1]).resolve(strict=False))
            continue
        roots.add(_resolve_source_path(source, source_agent))
    return roots


def _configured_npm_package_names(settings: dict[str, object]) -> set[str]:
    packages = settings.get('packages')
    if not isinstance(packages, list):
        return set()
    names: set[str] = set()
    for entry in packages:
        source = (
            entry
            if isinstance(entry, str)
            else entry.get('source')
            if isinstance(entry, dict)
            else None
        )
        if isinstance(source, str) and (name := _npm_package_name(source)):
            names.add(name)
    return names


def _npm_package_name(source: str) -> str | None:
    if not source.startswith('npm:'):
        return None
    spec = source[4:].strip()
    if not spec:
        return None
    if spec.startswith('@'):
        slash = spec.find('/')
        if slash < 2:
            return None
        version = spec.find('@', slash)
        return spec if version < 0 else spec[:version]
    return spec.split('@', 1)[0] or None


def _git_package_parts(source: str) -> tuple[str, Path] | None:
    value = source.strip()
    if value.startswith('git:'):
        value = value[4:].strip()
    elif not value.startswith(('http://', 'https://', 'ssh://', 'git://')):
        return None
    if value.startswith('git@') and ':' in value:
        host, path = value[4:].split(':', 1)
    elif '://' in value:
        parsed = urlparse(value)
        host, path = parsed.hostname or '', parsed.path.lstrip('/')
    else:
        host, separator, path = value.partition('/')
        if not separator:
            return None
    if '@' in path:
        path = path.split('@', 1)[0]
    path = path.removesuffix('.git').strip('/')
    if not host or len(Path(path).parts) < 2 or '..' in Path(path).parts:
        return None
    return host, Path(path)


def _resolve_source_path(source: str, source_agent: Path) -> Path:
    candidate = Path(source).expanduser()
    if not candidate.is_absolute():
        candidate = source_agent / candidate
    return candidate.resolve(strict=False)


def _read_json_object(path: Path) -> dict[str, object] | None:
    try:
        payload = json.loads(path.read_text(encoding='utf-8'))
    except Exception:
        return None
    return payload if isinstance(payload, dict) else None


def _clone_json_value(value: object) -> object:
    try:
        return json.loads(json.dumps(value, ensure_ascii=False))
    except Exception:
        return value


def _inherits_config(profile) -> bool:
    return True if profile is None else bool(getattr(profile, 'inherit_config', True))


__all__ = ['materialize_pi_config']
