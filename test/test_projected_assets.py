from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
import json
from pathlib import Path
import shutil
import threading

import pytest

import provider_core.projected_assets as projected_assets


_LABEL = 'test-projection'


def _write_tree(root: Path, text: str) -> None:
    root.mkdir(parents=True, exist_ok=True)
    (root / 'asset.txt').write_text(text, encoding='utf-8')


def _marker_path(target: Path) -> Path:
    return Path(f'{target}.ccb-projection.json')


def _valid_marker(source: Path, *, label: str = _LABEL, mode: str = 'symlink') -> dict[str, object]:
    return {
        'schema_version': 1,
        'record_type': 'ccb_projected_asset',
        'label': label,
        'source': str(source),
        'mode': mode,
        'updated_at': '2026-07-21T00:00:00Z',
    }


def test_route_preserves_unmarked_directory_even_when_content_matches(tmp_path: Path) -> None:
    source = tmp_path / 'source'
    target = tmp_path / 'target'
    _write_tree(source, 'same\n')
    shutil.copytree(source, target)

    assert not projected_assets.route_projected_tree(source, target, label=_LABEL)
    assert target.is_dir() and not target.is_symlink()
    assert (target / 'asset.txt').read_text(encoding='utf-8') == 'same\n'
    assert not _marker_path(target).exists()


def test_compatibility_flag_cannot_replace_unmarked_directory_or_symlink(tmp_path: Path) -> None:
    source = tmp_path / 'source'
    target = tmp_path / 'target'
    _write_tree(source, 'source\n')
    _write_tree(target, 'user\n')

    assert not projected_assets.route_projected_tree(
        source,
        target,
        label=_LABEL,
        allow_unmarked_replace=True,
    )
    assert (target / 'asset.txt').read_text(encoding='utf-8') == 'user\n'

    shutil.rmtree(target)
    foreign = tmp_path / 'foreign'
    _write_tree(foreign, 'foreign\n')
    target.symlink_to(foreign, target_is_directory=True)
    assert not projected_assets.route_projected_tree(
        source,
        target,
        label=_LABEL,
        allow_unmarked_replace=True,
    )
    assert target.is_symlink() and target.resolve() == foreign.resolve()

    projected_assets.remove_projected_path(
        target,
        label=_LABEL,
        allow_unmarked_replace=True,
    )
    assert target.is_symlink() and target.resolve() == foreign.resolve()


def test_exact_unmarked_source_symlink_is_adopted_without_replacement(tmp_path: Path) -> None:
    source = tmp_path / 'source'
    target = tmp_path / 'target'
    _write_tree(source, 'source\n')
    target.symlink_to(source, target_is_directory=True)
    inode_before = target.lstat().st_ino

    assert projected_assets.route_projected_tree(source, target, label=_LABEL)
    assert target.is_symlink() and target.resolve() == source.resolve()
    assert target.lstat().st_ino == inode_before
    marker = json.loads(_marker_path(target).read_text(encoding='utf-8'))
    assert marker['schema_version'] == 1
    assert marker['record_type'] == 'ccb_projected_asset'
    assert marker['label'] == _LABEL
    assert marker['source'] == str(source)
    assert marker['mode'] == 'symlink'


def test_exact_unmarked_source_symlink_survives_marker_write_failure(
    tmp_path: Path,
    monkeypatch,
) -> None:
    source = tmp_path / 'source'
    target = tmp_path / 'target'
    _write_tree(source, 'source\n')
    target.symlink_to(source, target_is_directory=True)
    inode_before = target.lstat().st_ino
    monkeypatch.setattr(projected_assets, '_write_projection_marker', lambda *args, **kwargs: False)

    assert not projected_assets.route_projected_tree(source, target, label=_LABEL)
    assert target.is_symlink() and target.resolve() == source.resolve()
    assert target.lstat().st_ino == inode_before
    assert not _marker_path(target).exists()


@pytest.mark.parametrize(
    'payload',
    (
        {'schema_version': 1, 'record_type': 'user_owned', 'label': _LABEL, 'source': '/x', 'mode': 'copy'},
        {'schema_version': 2, 'record_type': 'ccb_projected_asset', 'label': _LABEL, 'source': '/x', 'mode': 'copy'},
        {'schema_version': 1, 'record_type': 'ccb_projected_asset', 'label': 'foreign', 'source': '/x', 'mode': 'copy'},
        {'schema_version': 1, 'record_type': 'ccb_projected_asset', 'label': _LABEL, 'source': '', 'mode': 'copy'},
        {'schema_version': 1, 'record_type': 'ccb_projected_asset', 'label': _LABEL, 'source': '/x', 'mode': 'foreign'},
    ),
)
def test_foreign_or_malformed_marker_preserves_target(
    tmp_path: Path,
    payload: dict[str, object],
) -> None:
    source = tmp_path / 'source'
    target = tmp_path / 'target'
    marker = _marker_path(target)
    _write_tree(source, 'source\n')
    _write_tree(target, 'user\n')
    marker.write_text(json.dumps(payload) + '\n', encoding='utf-8')
    marker_before = marker.read_bytes()

    assert not projected_assets.route_projected_tree(source, target, label=_LABEL)
    assert (target / 'asset.txt').read_text(encoding='utf-8') == 'user\n'
    assert marker.read_bytes() == marker_before


def test_symlinked_marker_and_target_absent_foreign_marker_block_projection(tmp_path: Path) -> None:
    source = tmp_path / 'source'
    target = tmp_path / 'target'
    marker = _marker_path(target)
    foreign_marker = tmp_path / 'foreign-marker.json'
    _write_tree(source, 'source\n')
    foreign_marker.write_text(json.dumps(_valid_marker(source)) + '\n', encoding='utf-8')
    marker.symlink_to(foreign_marker)

    assert not projected_assets.route_projected_tree(source, target, label=_LABEL)
    assert not target.exists()
    assert marker.is_symlink()

    marker.unlink()
    marker.write_text('{malformed\n', encoding='utf-8')
    assert not projected_assets.route_projected_tree(source, target, label=_LABEL)
    assert not target.exists()
    assert marker.read_text(encoding='utf-8') == '{malformed\n'


def test_target_absent_marker_for_another_consumer_blocks_projection(tmp_path: Path) -> None:
    source = tmp_path / 'source'
    target = tmp_path / 'target'
    marker = _marker_path(target)
    _write_tree(source, 'source\n')
    marker.write_text(json.dumps(_valid_marker(source, label='foreign')) + '\n', encoding='utf-8')
    marker_before = marker.read_bytes()

    assert not projected_assets.route_projected_tree(source, target, label=_LABEL)
    assert not target.exists() and not target.is_symlink()
    assert marker.read_bytes() == marker_before


def test_foreign_marker_blocks_exact_source_symlink_adoption(tmp_path: Path) -> None:
    source = tmp_path / 'source'
    target = tmp_path / 'target'
    marker = _marker_path(target)
    _write_tree(source, 'source\n')
    target.symlink_to(source, target_is_directory=True)
    marker.write_text(json.dumps(_valid_marker(source, label='foreign')) + '\n', encoding='utf-8')
    inode_before = target.lstat().st_ino

    assert not projected_assets.route_projected_tree(source, target, label=_LABEL)
    assert target.is_symlink() and target.resolve() == source.resolve()
    assert target.lstat().st_ino == inode_before
    assert json.loads(marker.read_text(encoding='utf-8'))['label'] == 'foreign'


def test_new_projection_rolls_back_when_marker_cannot_be_written(
    tmp_path: Path,
    monkeypatch,
) -> None:
    source = tmp_path / 'source'
    target = tmp_path / 'target'
    _write_tree(source, 'source\n')
    monkeypatch.setattr(projected_assets, '_write_projection_marker', lambda *args, **kwargs: False)

    assert not projected_assets.route_projected_tree(source, target, label=_LABEL)
    assert not target.exists() and not target.is_symlink()
    assert not _marker_path(target).exists()


def test_valid_owned_marker_allows_refresh_and_owned_cleanup(tmp_path: Path) -> None:
    first_source = tmp_path / 'first-source'
    second_source = tmp_path / 'second-source'
    target = tmp_path / 'target'
    marker = _marker_path(target)
    _write_tree(first_source, 'first\n')
    _write_tree(second_source, 'second\n')

    assert projected_assets.route_projected_tree(first_source, target, label=_LABEL)
    assert projected_assets.route_projected_tree(second_source, target, label=_LABEL)
    assert target.is_symlink() and target.resolve() == second_source.resolve()
    assert json.loads(marker.read_text(encoding='utf-8'))['source'] == str(second_source)

    assert not projected_assets.route_projected_tree(
        second_source,
        target,
        enabled=False,
        label=_LABEL,
    )
    assert not target.exists() and not target.is_symlink()
    assert not marker.exists()


def test_missing_source_removes_only_valid_owned_target(tmp_path: Path) -> None:
    source = tmp_path / 'source'
    target = tmp_path / 'target'
    _write_tree(source, 'source\n')
    assert projected_assets.route_projected_tree(source, target, label=_LABEL)
    shutil.rmtree(source)

    assert not projected_assets.route_projected_tree(source, target, label=_LABEL)
    assert not target.exists() and not target.is_symlink()
    assert not _marker_path(target).exists()

    _write_tree(target, 'user\n')
    assert not projected_assets.route_projected_tree(source, target, label=_LABEL)
    assert (target / 'asset.txt').read_text(encoding='utf-8') == 'user\n'


def test_seed_projected_file_refreshes_and_removes_only_owned_target(tmp_path: Path) -> None:
    source = tmp_path / 'source.ts'
    target = tmp_path / 'target.ts'
    source.write_text('first\n', encoding='utf-8')

    assert projected_assets.seed_projected_file(source, target, label=_LABEL)
    assert target.read_text(encoding='utf-8') == 'first\n'

    source.write_text('second version\n', encoding='utf-8')
    assert projected_assets.seed_projected_file(source, target, label=_LABEL)
    assert target.read_text(encoding='utf-8') == 'second version\n'

    assert not projected_assets.seed_projected_file(source, target, enabled=False, label=_LABEL)
    assert not target.exists()
    assert not _marker_path(target).exists()


def test_seed_projected_file_preserves_unowned_target(tmp_path: Path) -> None:
    source = tmp_path / 'source.ts'
    target = tmp_path / 'target.ts'
    source.write_text('source\n', encoding='utf-8')
    target.write_text('user-owned\n', encoding='utf-8')

    assert not projected_assets.seed_projected_file(source, target, label=_LABEL)
    assert target.read_text(encoding='utf-8') == 'user-owned\n'
    assert not _marker_path(target).exists()


def test_seed_projected_file_rolls_back_when_marker_write_fails(
    tmp_path: Path,
    monkeypatch,
) -> None:
    source = tmp_path / 'source.ts'
    target = tmp_path / 'target.ts'
    source.write_text('source\n', encoding='utf-8')
    monkeypatch.setattr(projected_assets, '_write_projection_marker', lambda *args, **kwargs: False)

    assert not projected_assets.seed_projected_file(source, target, label=_LABEL)
    assert not target.exists()
    assert not _marker_path(target).exists()


def test_copy_projected_tree_to_cache_handles_concurrent_first_publish(
    tmp_path: Path,
    monkeypatch,
) -> None:
    source = tmp_path / 'source'
    target = tmp_path / 'cache' / 'digest' / 'bundle'
    _write_tree(source, 'shared\n')
    copy_barrier = threading.Barrier(2)
    original_copytree = shutil.copytree

    def synchronized_copytree(*args, **kwargs):
        result = original_copytree(*args, **kwargs)
        copy_barrier.wait(timeout=5)
        return result

    monkeypatch.setattr(projected_assets.shutil, 'copytree', synchronized_copytree)

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(
            executor.map(
                lambda _: projected_assets.copy_projected_tree_to_cache(
                    source,
                    target,
                    label=_LABEL,
                ),
                range(2),
            )
        )

    assert results == [True, True]
    assert (target / 'asset.txt').read_text(encoding='utf-8') == 'shared\n'
    assert json.loads(_marker_path(target).read_text(encoding='utf-8'))['label'] == _LABEL
    assert not list(target.parent.glob(f'.{target.name}.ccb-cache-*'))


def test_copy_projected_tree_to_cache_records_explicit_marker_source(tmp_path: Path) -> None:
    staged_source = tmp_path / 'staged-source'
    authority_source = tmp_path / 'authority-source'
    target = tmp_path / 'cache' / 'digest' / 'bundle'
    _write_tree(staged_source, 'normalized\n')
    _write_tree(authority_source, 'original\n')

    assert projected_assets.copy_projected_tree_to_cache(
        staged_source,
        target,
        label=_LABEL,
        marker_source=authority_source,
    )

    marker = json.loads(_marker_path(target).read_text(encoding='utf-8'))
    assert marker['source'] == str(authority_source)
    assert (target / 'asset.txt').read_text(encoding='utf-8') == 'normalized\n'


def test_copy_projected_tree_to_cache_rejects_tampered_cache_hit(tmp_path: Path) -> None:
    source = tmp_path / 'source'
    target = tmp_path / 'cache' / 'digest' / 'bundle'
    _write_tree(source, 'trusted\n')

    assert projected_assets.copy_projected_tree_to_cache(source, target, label=_LABEL)
    (target / 'asset.txt').write_text('tampered\n', encoding='utf-8')

    assert not projected_assets.copy_projected_tree_to_cache(source, target, label=_LABEL)
    assert (target / 'asset.txt').read_text(encoding='utf-8') == 'tampered\n'


def test_tree_content_fingerprint_and_cache_track_executable_mode(tmp_path: Path) -> None:
    source = tmp_path / 'source'
    _write_tree(source, 'script\n')
    asset = source / 'asset.txt'
    asset.chmod(0o644)
    first_digest = projected_assets.tree_content_fingerprint(source)
    first_target = tmp_path / 'cache' / first_digest / 'bundle'
    assert projected_assets.copy_projected_tree_to_cache(source, first_target, label=_LABEL)

    asset.chmod(0o755)
    second_digest = projected_assets.tree_content_fingerprint(source)
    second_target = tmp_path / 'cache' / second_digest / 'bundle'
    assert second_digest != first_digest
    assert projected_assets.copy_projected_tree_to_cache(source, second_target, label=_LABEL)
    assert second_target.joinpath('asset.txt').stat().st_mode & 0o777 == 0o755


def test_copy_projected_tree_to_cache_rejects_symlinked_cache_root(tmp_path: Path) -> None:
    source = tmp_path / 'source'
    external = tmp_path / 'external'
    target = tmp_path / 'cache' / 'digest' / 'bundle'
    _write_tree(source, 'same\n')
    _write_tree(external, 'same\n')
    target.parent.mkdir(parents=True)
    target.symlink_to(external, target_is_directory=True)

    assert not projected_assets.copy_projected_tree_to_cache(source, target, label=_LABEL)
    assert target.is_symlink()
    assert not _marker_path(target).exists()


def test_tree_content_fingerprint_tracks_root_directory_mode(tmp_path: Path) -> None:
    source = tmp_path / 'source'
    _write_tree(source, 'same\n')
    source.chmod(0o700)
    first_digest = projected_assets.tree_content_fingerprint(source)

    source.chmod(0o755)

    assert projected_assets.tree_content_fingerprint(source) != first_digest


def test_copy_projected_tree_to_cache_preserves_internal_directory_symlink(
    tmp_path: Path,
) -> None:
    source = tmp_path / 'source'
    target = tmp_path / 'cache' / 'digest' / 'bundle'
    nested = source / 'nested'
    _write_tree(nested, 'linked\n')
    (source / 'alias').symlink_to('nested', target_is_directory=True)

    assert projected_assets.copy_projected_tree_to_cache(source, target, label=_LABEL)
    assert (target / 'alias').is_symlink()
    assert (target / 'alias').readlink() == Path('nested')
    assert (target / 'alias' / 'asset.txt').read_text(encoding='utf-8') == 'linked\n'


def test_copy_projected_tree_to_cache_rejects_external_source_symlink(tmp_path: Path) -> None:
    source = tmp_path / 'source'
    external = tmp_path / 'external'
    target = tmp_path / 'cache' / 'digest' / 'bundle'
    source.mkdir()
    _write_tree(external, 'external\n')
    (source / 'escape').symlink_to(external, target_is_directory=True)

    assert not projected_assets.copy_projected_tree_to_cache(source, target, label=_LABEL)
    assert not target.exists()


def test_copy_projected_tree_to_cache_rejects_absolute_internal_symlink(tmp_path: Path) -> None:
    source = tmp_path / 'source'
    target = tmp_path / 'cache' / 'digest' / 'bundle'
    nested = source / 'nested'
    _write_tree(nested, 'internal\n')
    (source / 'absolute').symlink_to(nested, target_is_directory=True)

    assert not projected_assets.copy_projected_tree_to_cache(source, target, label=_LABEL)
    assert not target.exists()


@pytest.mark.parametrize('link_target', ['missing', 'loop'])
def test_copy_projected_tree_to_cache_rejects_unresolvable_internal_symlink(
    tmp_path: Path,
    link_target: str,
) -> None:
    source = tmp_path / 'source'
    target = tmp_path / 'cache' / 'digest' / 'bundle'
    source.mkdir()
    (source / 'loop').symlink_to(link_target)

    assert not projected_assets.copy_projected_tree_to_cache(source, target, label=_LABEL)
    assert not target.exists()


def test_copy_projected_tree_to_cache_rejects_symlinked_concurrent_winner(
    tmp_path: Path,
    monkeypatch,
) -> None:
    source = tmp_path / 'source'
    external = tmp_path / 'external'
    target = tmp_path / 'cache' / 'digest' / 'bundle'
    _write_tree(source, 'same\n')
    _write_tree(external, 'same\n')
    original_copytree = shutil.copytree

    def copytree_with_symlinked_winner(*args, **kwargs):
        result = original_copytree(*args, **kwargs)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.symlink_to(external, target_is_directory=True)
        return result

    monkeypatch.setattr(projected_assets.shutil, 'copytree', copytree_with_symlinked_winner)

    assert not projected_assets.copy_projected_tree_to_cache(source, target, label=_LABEL)
    assert target.is_symlink()
    assert not _marker_path(target).exists()
