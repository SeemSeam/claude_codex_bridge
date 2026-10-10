"""Immutable Pi package graphs, isolated from the external installation."""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import shutil
import stat
import tempfile

from provider_core.projected_assets import tree_content_fingerprint, tree_symlinks_are_self_contained, write_projected_marker
from storage.locks import file_lock

_EXCLUDES = '.ccb-snapshot-exclude'


def _excluded_paths(source: Path) -> tuple[str, ...]:
    control = source / _EXCLUDES
    if not control.exists():
        return ()
    if control.is_symlink():
        raise RuntimeError('Pi snapshot exclusion file must be a regular file')
    paths = []
    for raw in control.read_text(encoding='utf-8').splitlines():
        value = raw.strip()
        if not value or value.startswith('#'):
            continue
        path = Path(value)
        if path.is_absolute() or '..' in path.parts or path.as_posix() in {'.', 'package.json', _EXCLUDES}:
            raise RuntimeError(f'invalid Pi snapshot exclusion: {value!r}')
        paths.append(path.as_posix())
    return tuple(paths)


def _ignore(source: Path, excluded: tuple[str, ...], directory: str, names: list[str]) -> set[str]:
    ignored = {'node_modules'}
    for name in names:
        relative = (Path(directory) / name).relative_to(source).as_posix()
        if any(relative == item or relative.startswith(item + '/') for item in excluded):
            ignored.add(name)
    return ignored


def payload_fingerprint(source: Path, excluded: tuple[str, ...]) -> str:
    """Hash the exact selected payload without walking installed node_modules."""
    digest = hashlib.sha256()

    def visit(path: Path) -> None:
        metadata = path.lstat()
        relative = path.relative_to(source).as_posix()
        digest.update(json.dumps([relative, stat.S_IMODE(metadata.st_mode), stat.S_IFMT(metadata.st_mode)]).encode())
        if path.is_symlink():
            link = path.readlink()
            if link.is_absolute():
                raise RuntimeError(f'Pi package {source.name!r} contains an unsafe symlink')
            try:
                path.resolve(strict=True).relative_to(source)
            except (OSError, RuntimeError, ValueError) as exc:
                raise RuntimeError(f'Pi package {source.name!r} contains an unsafe symlink') from exc
            digest.update(str(link).encode())
        elif path.is_dir():
            children = sorted(path.iterdir())
            ignored = _ignore(source, excluded, str(path), [p.name for p in children])
            for child in children:
                if child.name not in ignored:
                    visit(child)
        elif path.is_file():
            with path.open('rb') as handle:
                for chunk in iter(lambda: handle.read(65536), b''):
                    digest.update(chunk)
        else:
            raise RuntimeError(f'unsupported Pi package file: {relative}')
        digest.update(b'\0')

    visit(source)
    return digest.hexdigest()


def snapshot_package(source: Path, *, cache_root: Path, category: str, label: str) -> Path:
    # Import lazily: home owns Pi manifest and Node resolution policy.
    from .home import _read_json_object, _runtime_dependency_names, _resolve_installed_dependency, _MissingRuntimeDependency

    source = source.resolve(strict=True)
    nodes: dict[Path, dict] = {}

    def discover(path: Path) -> None:
        if path in nodes:
            return
        excluded = _excluded_paths(path)
        node = {'excluded': excluded, 'fingerprint': payload_fingerprint(path, excluded), 'edges': {}}
        nodes[path] = node
        manifest = _read_json_object(path / 'package.json') or {}
        required, optional = _runtime_dependency_names(manifest)
        for name in (*required, *optional):
            dependency = _resolve_installed_dependency(path, name)
            if dependency is None:
                if name in optional:
                    continue
                raise _MissingRuntimeDependency(f'Pi package {manifest.get("name", str(path))!r} cannot resolve runtime dependency {name!r}')
            node['edges'][name] = dependency
            discover(dependency)

    discover(source)
    description = [(str(path), node['fingerprint'], [(name, str(dep)) for name, dep in sorted(node['edges'].items())]) for path, node in sorted(nodes.items())]
    input_digest = hashlib.sha256(json.dumps(description).encode()).hexdigest()
    parent = cache_root / category / 'graph-v1'
    parent.mkdir(parents=True, exist_ok=True)
    bundle = parent / input_digest
    target = bundle / source.name

    def invalid() -> RuntimeError:
        return RuntimeError(f'failed to publish verified Pi package snapshot for {source}')

    with file_lock(parent / '.build.lock'):
        # Every graph-v1 writer holds this lock for its complete staging lifetime.
        # A killed writer releases the OS lock, so its staging tree is now orphaned.
        for abandoned in parent.glob('.ccb-build-*'):
            if abandoned.is_dir() and not abandoned.is_symlink():
                shutil.rmtree(abandoned)
        if bundle.exists() or bundle.is_symlink():
            if bundle.is_symlink() or target.is_symlink():
                raise invalid()
            receipt = _read_json_object(bundle / 'receipt.json')
            if (not receipt or receipt.get('input') != input_digest
                    or not tree_symlinks_are_self_contained(target)
                    or tree_content_fingerprint(target) != receipt.get('output')):
                raise invalid()
            return target
        with tempfile.TemporaryDirectory(prefix='.ccb-build-', dir=parent) as temporary:
            stage = Path(temporary) / 'bundle'
            stage.mkdir()
            candidate = stage / source.name
            locations: dict[Path, Path] = {}

            def copy(path: Path, destination: Path) -> None:
                destination.parent.mkdir(parents=True, exist_ok=True)
                if path in locations:
                    destination.symlink_to(os.path.relpath(locations[path], destination.parent), target_is_directory=True)
                    return
                locations[path] = destination
                node = nodes[path]
                shutil.copytree(path, destination, symlinks=True,
                                ignore=lambda directory, names: _ignore(path, node['excluded'], directory, names))
                for name, dependency in node['edges'].items():
                    copy(dependency, destination / 'node_modules' / name)

            copy(source, candidate)
            for path, node in nodes.items():
                if (payload_fingerprint(path, node['excluded']) != node['fingerprint']
                        or payload_fingerprint(locations[path], node['excluded']) != node['fingerprint']):
                    raise RuntimeError('Pi package changed during snapshot construction')
                for name, dependency in node['edges'].items():
                    if _resolve_installed_dependency(path, name) != dependency:
                        raise RuntimeError('Pi dependency resolution changed during snapshot construction')
            if not tree_symlinks_are_self_contained(candidate):
                raise invalid()
            output_digest = tree_content_fingerprint(candidate)
            if not output_digest or not write_projected_marker(candidate, label=label, mode='copy', source=source):
                raise invalid()
            (stage / 'receipt.json').write_text(json.dumps({'input': input_digest, 'output': output_digest}) + '\n', encoding='utf-8')
            stage.rename(bundle)
        return target
