from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import shutil
import tempfile

_HASH_CHUNK_SIZE = 64 * 1024


def route_projected_tree(
    source: Path,
    target: Path,
    *,
    enabled: bool = True,
    label: str = 'projected-tree',
    marker_path: Path | None = None,
    allow_unmarked_replace: bool = False,
) -> bool:
    source = Path(source).expanduser()
    target = Path(target).expanduser()
    marker = marker_path or _default_marker_path(target)
    marker_present = marker.exists() or marker.is_symlink()
    marker_owned = _marker_matches(marker, label=label, source=None)

    if marker_present and not marker_owned:
        return False

    if not enabled or not source.is_dir():
        _remove_projected_target(target, marker, label=label)
        return False
    if _same_path(source, target) and not target.is_symlink():
        return True
    target.parent.mkdir(parents=True, exist_ok=True)
    if _projection_points_to(target, source):
        return _write_projection_marker(marker, source=source, mode='symlink', label=label)
    if target.exists() or target.is_symlink():
        if not _can_replace_projected_target(
            target,
            marker,
            label=label,
            allow_unmarked_replace=allow_unmarked_replace,
        ):
            return False
        _remove_path(target)
    try:
        target.symlink_to(source, target_is_directory=True)
        if _write_projection_marker(marker, source=source, mode='symlink', label=label):
            return True
    except Exception:
        pass
    _remove_path(target)
    try:
        shutil.copytree(source, target)
        if _write_projection_marker(marker, source=source, mode='copy', label=label):
            return True
    except Exception:
        pass
    _remove_path(target)
    return False


def copy_projected_tree_to_cache(source: Path, bundle_root: Path, *, label: str = 'projected-tree') -> bool:
    source = Path(source).expanduser()
    bundle_root = Path(bundle_root).expanduser()
    if not source.is_dir():
        return False
    if _tree_has_required_entries(source, bundle_root):
        return write_projected_marker(bundle_root, label=label, mode='copy', source=source)
    bundle_root.parent.mkdir(parents=True, exist_ok=True)
    staging_root = Path(
        tempfile.mkdtemp(
            prefix=f'.{bundle_root.name}.ccb-cache-',
            dir=bundle_root.parent,
        )
    )
    candidate = staging_root / 'candidate'
    try:
        shutil.copytree(source, candidate)
        try:
            candidate.rename(bundle_root)
        except OSError:
            # Another materializer may have published the same content-addressed
            # bundle first. Accept only a complete winning tree.
            if not _tree_has_required_entries(source, bundle_root):
                return False
        if not write_projected_marker(bundle_root, label=label, mode='copy', source=source):
            raise OSError(f'failed to write projection marker: {bundle_root}')
    except Exception:
        return False
    finally:
        _remove_path(staging_root)
    return True


def seed_projected_tree(
    source: Path,
    target: Path,
    *,
    enabled: bool = True,
    label: str = 'projected-tree',
    marker_path: Path | None = None,
) -> bool:
    """Seed a writable local tree without replacing user-owned target data."""
    source = Path(source).expanduser()
    target = Path(target).expanduser()
    marker = marker_path or _default_marker_path(target)

    if not enabled:
        remove_projected_path(target, label=label, marker_path=marker)
        return False
    if not source.is_dir():
        return False
    if _same_path(source, target) and not target.is_symlink():
        return True

    source_fingerprint = tree_metadata_fingerprint(source)
    if not source_fingerprint:
        return False
    owned = _marker_matches(marker, label=label, source=None)
    target_present = target.exists() or target.is_symlink()
    if target_present and not owned:
        return False
    if marker.exists() and not owned:
        return False

    marker_payload = _read_projection_marker(marker) if owned else {}
    if (
        target.is_dir()
        and not target.is_symlink()
        and _marker_matches(marker, label=label, source=source)
        and marker_payload.get('mode') == 'copy-seed'
        and marker_payload.get('source_fingerprint') == source_fingerprint
    ):
        return True

    target.parent.mkdir(parents=True, exist_ok=True)
    staging_root = Path(tempfile.mkdtemp(prefix=f'.{target.name}.ccb-seed-', dir=target.parent))
    staged_target = staging_root / 'candidate'
    previous_target = staging_root / 'previous'
    moved_previous = False
    installed_candidate = False
    try:
        shutil.copytree(source, staged_target)
        if target_present:
            target.rename(previous_target)
            moved_previous = True
        staged_target.rename(target)
        installed_candidate = True
        if not _write_projection_marker(
            marker,
            source=source,
            mode='copy-seed',
            label=label,
            source_fingerprint=source_fingerprint,
        ):
            raise OSError(f'failed to write projection marker: {marker}')
        _remove_path(previous_target)
        return True
    except Exception:
        if installed_candidate:
            _remove_path(target)
        if moved_previous and (previous_target.exists() or previous_target.is_symlink()):
            try:
                previous_target.rename(target)
            except Exception:
                pass
        return False
    finally:
        _remove_path(staging_root)


def seed_projected_file(
    source: Path,
    target: Path,
    *,
    enabled: bool = True,
    label: str = 'projected-file',
    marker_path: Path | None = None,
) -> bool:
    """Seed a writable local file without replacing user-owned target data."""
    source = Path(source).expanduser()
    target = Path(target).expanduser()
    marker = marker_path or _default_marker_path(target)

    if not enabled:
        remove_projected_path(target, label=label, marker_path=marker)
        return False
    if not source.is_file() or source.is_symlink():
        return False
    if _same_path(source, target) and not target.is_symlink():
        return True

    source_fingerprint = _file_metadata_fingerprint(source)
    if not source_fingerprint:
        return False
    owned = _marker_matches(marker, label=label, source=None)
    target_present = target.exists() or target.is_symlink()
    if target_present and not owned:
        return False
    if marker.exists() and not owned:
        return False

    marker_payload = _read_projection_marker(marker) if owned else {}
    if (
        target.is_file()
        and not target.is_symlink()
        and _marker_matches(marker, label=label, source=source)
        and marker_payload.get('mode') == 'copy-seed'
        and marker_payload.get('source_fingerprint') == source_fingerprint
    ):
        return True

    target.parent.mkdir(parents=True, exist_ok=True)
    staging_root = Path(tempfile.mkdtemp(prefix=f'.{target.name}.ccb-seed-', dir=target.parent))
    staged_target = staging_root / 'candidate'
    previous_target = staging_root / 'previous'
    moved_previous = False
    installed_candidate = False
    try:
        shutil.copy2(source, staged_target, follow_symlinks=False)
        if target_present:
            target.rename(previous_target)
            moved_previous = True
        staged_target.rename(target)
        installed_candidate = True
        if not _write_projection_marker(
            marker,
            source=source,
            mode='copy-seed',
            label=label,
            source_fingerprint=source_fingerprint,
        ):
            raise OSError(f'failed to write projection marker: {marker}')
        _remove_path(previous_target)
        return True
    except Exception:
        if installed_candidate:
            _remove_path(target)
        if moved_previous and (previous_target.exists() or previous_target.is_symlink()):
            try:
                previous_target.rename(target)
            except Exception:
                pass
        return False
    finally:
        _remove_path(staging_root)


def ensure_shared_tree_bundle(source: Path, bundle_root: Path) -> Path | None:
    return bundle_root if copy_projected_tree_to_cache(source, bundle_root) else None


def remove_projected_path(
    target: Path,
    *,
    label: str = 'projected-tree',
    source: Path | None = None,
    marker_path: Path | None = None,
    allow_unmarked_replace: bool = False,
) -> None:
    del allow_unmarked_replace
    target = Path(target).expanduser()
    marker = marker_path or _default_marker_path(target)
    if not _marker_matches(marker, label=label, source=source):
        return
    _remove_projected_target(target, marker, label=label)


def projected_path_is_owned(
    target: Path,
    *,
    label: str = 'projected-tree',
    source: Path | None = None,
    marker_path: Path | None = None,
) -> bool:
    target = Path(target).expanduser()
    marker = marker_path or _default_marker_path(target)
    return _marker_matches(marker, label=label, source=source)


def remove_projected_tree(
    target: Path,
    *,
    marker_path: Path | None = None,
    allow_unmarked_replace: bool = False,
) -> None:
    remove_projected_path(target, marker_path=marker_path, allow_unmarked_replace=allow_unmarked_replace)


def write_projected_marker(target: Path, *, label: str, mode: str, source: Path) -> bool:
    return _write_projection_marker(
        _default_marker_path(Path(target).expanduser()),
        source=source,
        mode=mode,
        label=label,
    )


def tree_content_fingerprint(root: Path) -> str:
    root = Path(root).expanduser()
    digest = hashlib.sha256()
    try:
        for entry in sorted(root.rglob('*')):
            relative = entry.relative_to(root)
            kind = 'd' if entry.is_dir() else 'f' if entry.is_file() else 'l' if entry.is_symlink() else 'o'
            digest.update(kind.encode('utf-8'))
            digest.update(b'\0')
            digest.update(str(relative).encode('utf-8', errors='ignore'))
            digest.update(b'\0')
            if entry.is_file():
                with entry.open('rb') as handle:
                    for chunk in iter(lambda: handle.read(_HASH_CHUNK_SIZE), b''):
                        digest.update(chunk)
            elif entry.is_symlink():
                digest.update(str(entry.readlink()).encode('utf-8', errors='ignore'))
            digest.update(b'\0')
    except Exception:
        return ''
    return digest.hexdigest()


def tree_metadata_fingerprint(root: Path) -> str:
    root = Path(root).expanduser()
    digest = hashlib.sha256()
    try:
        for entry in sorted(root.rglob('*')):
            relative = entry.relative_to(root)
            if entry.is_symlink():
                kind = 'l'
            elif entry.is_dir():
                kind = 'd'
            elif entry.is_file():
                kind = 'f'
            else:
                kind = 'o'
            digest.update(kind.encode('utf-8'))
            digest.update(b'\0')
            digest.update(str(relative).encode('utf-8', errors='ignore'))
            digest.update(b'\0')
            if entry.is_symlink():
                digest.update(str(entry.readlink()).encode('utf-8', errors='ignore'))
            elif entry.is_file():
                stat = entry.stat()
                digest.update(str(stat.st_size).encode('utf-8'))
                digest.update(b'\0')
                digest.update(str(stat.st_mtime_ns).encode('utf-8'))
            digest.update(b'\0')
    except Exception:
        return ''
    return digest.hexdigest()


def _file_metadata_fingerprint(path: Path) -> str:
    try:
        metadata = path.stat(follow_symlinks=False)
    except OSError:
        return ''
    digest = hashlib.sha256()
    digest.update(str(metadata.st_size).encode('utf-8'))
    digest.update(b'\0')
    digest.update(str(metadata.st_mtime_ns).encode('utf-8'))
    return digest.hexdigest()


def _remove_projected_target(target: Path, marker: Path, *, label: str) -> None:
    if _marker_matches(marker, label=label, source=None):
        _remove_path(target)
        marker.unlink(missing_ok=True)


def _projection_points_to(target: Path, source: Path) -> bool:
    if not target.is_symlink():
        return False
    try:
        return target.resolve() == source.resolve()
    except Exception:
        try:
            return target.readlink() == source
        except Exception:
            return False


def _can_replace_projected_target(
    target: Path,
    marker: Path,
    *,
    label: str,
    allow_unmarked_replace: bool,
) -> bool:
    del allow_unmarked_replace
    if _marker_matches(marker, label=label, source=None):
        return True
    if marker.exists() or marker.is_symlink():
        return False
    return not target.exists() and not target.is_symlink()


def _tree_has_required_entries(source: Path, candidate: Path) -> bool:
    if not candidate.is_dir():
        return False
    try:
        for entry in source.rglob('*'):
            relative = entry.relative_to(source)
            projected = candidate / relative
            if entry.is_dir() and not projected.is_dir():
                return False
            if entry.is_file() and not projected.is_file():
                return False
            if entry.is_symlink() and not projected.exists() and not projected.is_symlink():
                return False
    except Exception:
        return False
    return True


def _default_marker_path(target: Path) -> Path:
    return Path(f'{target}.ccb-projection.json')


def _marker_matches(marker: Path, *, label: str, source: Path | None) -> bool:
    if marker.is_symlink() or not marker.is_file():
        return False
    payload = _read_projection_marker(marker)
    if payload.get('schema_version') != 1:
        return False
    if payload.get('record_type') != 'ccb_projected_asset':
        return False
    if str(payload.get('label') or '') != label:
        return False
    marker_source = str(payload.get('source') or '').strip()
    if not marker_source:
        return False
    if str(payload.get('mode') or '') not in {'symlink', 'copy', 'copy-seed'}:
        return False
    if source is None:
        return True
    try:
        return Path(marker_source).expanduser().resolve() == Path(source).expanduser().resolve()
    except Exception:
        return marker_source == str(source)


def _read_projection_marker(path: Path) -> dict[str, object]:
    try:
        payload = json.loads(path.read_text(encoding='utf-8'))
    except Exception:
        return {}
    return payload if isinstance(payload, dict) else {}


def _write_projection_marker(
    path: Path,
    *,
    source: Path,
    mode: str,
    label: str,
    source_fingerprint: str | None = None,
) -> bool:
    payload = {
        'schema_version': 1,
        'record_type': 'ccb_projected_asset',
        'label': label,
        'source': str(source),
        'mode': mode,
        'updated_at': datetime.now(timezone.utc).isoformat().replace('+00:00', 'Z'),
    }
    if source_fingerprint:
        payload['source_fingerprint'] = source_fingerprint
    descriptor: int | None = None
    tmp_path: Path | None = None
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        descriptor, tmp_name = tempfile.mkstemp(prefix=f'.{path.name}.', suffix='.tmp', dir=path.parent)
        tmp_path = Path(tmp_name)
        with os.fdopen(descriptor, 'w', encoding='utf-8') as handle:
            handle.write(json.dumps(payload, ensure_ascii=True, sort_keys=True) + '\n')
        descriptor = None
        os.replace(tmp_path, path)
        return True
    except Exception:
        if descriptor is not None:
            try:
                os.close(descriptor)
            except OSError:
                pass
        if tmp_path is not None:
            tmp_path.unlink(missing_ok=True)
        return False


def _remove_path(path: Path) -> None:
    if path.is_symlink() or path.is_file():
        path.unlink(missing_ok=True)
        return
    if path.is_dir():
        shutil.rmtree(path, ignore_errors=True)


def _same_path(left: Path, right: Path) -> bool:
    try:
        return left.resolve() == right.resolve()
    except Exception:
        return left == right


__all__ = [
    'copy_projected_tree_to_cache',
    'ensure_shared_tree_bundle',
    'projected_path_is_owned',
    'remove_projected_path',
    'remove_projected_tree',
    'route_projected_tree',
    'seed_projected_file',
    'seed_projected_tree',
    'tree_content_fingerprint',
    'tree_metadata_fingerprint',
    'write_projected_marker',
]
