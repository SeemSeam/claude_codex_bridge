from __future__ import annotations

import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import patch

import pytest

from provider_backends.pi.home import _snapshot_package_tree
from provider_backends.pi import package_snapshot


def package(path, name, deps=(), code='module.exports = {};', version='1.0.0'):
    path.mkdir(parents=True)
    (path / 'package.json').write_text(json.dumps({'name': name, 'version': version, 'dependencies': dict.fromkeys(deps, '*')}))
    (path / 'index.js').write_text(code)
    return path


def diamond(tmp_path):
    source = package(tmp_path / 'plugin', 'plugin', ['a', 'b'], 'module.exports=[require("a"),require("b")];')
    package(source / 'node_modules/a', 'a', ['x'], 'module.exports=require("x");')
    package(source / 'node_modules/b', 'b', ['x'], 'module.exports=require("x");')
    package(source / 'node_modules/x', 'x', [], 'module.exports={value:42};')
    return source


def snapshot(source, tmp_path):
    return _snapshot_package_tree(source, cache_root=tmp_path / 'cache', category='local-packages')


def node(script, path):
    if not shutil.which('node'):
        pytest.skip('requires Node.js')
    return subprocess.check_output(['node', '-e', script, str(path)], text=True).strip()


def test_shared_dependency_retains_node_identity_and_warm_cache_skips_copy(tmp_path):
    source = diamond(tmp_path)
    first = snapshot(source, tmp_path)
    assert node('let a=require(process.argv[1]);console.log(a[0]===a[1],a[0].value)', first) == 'true 42'
    assert len({p.resolve() for p in first.rglob('x/package.json')}) == 1
    assert (first / 'node_modules/b/node_modules/x').is_symlink()
    with patch.object(package_snapshot.shutil, 'copytree', side_effect=AssertionError('warm cache copied')):
        assert snapshot(source, tmp_path) == first
    (first / 'node_modules/a/node_modules/x/index.js').write_text('tampered')
    with pytest.raises(RuntimeError, match='verified Pi package snapshot'):
        snapshot(source, tmp_path)


def test_same_size_same_mtime_changes_invalidate(tmp_path):
    source = diamond(tmp_path)
    first = snapshot(source, tmp_path)
    dep = source / 'node_modules/x/index.js'
    before = dep.stat()
    dep.write_text(dep.read_text().replace('42', '43'))
    os.utime(dep, ns=(before.st_atime_ns, before.st_mtime_ns))
    second = snapshot(source, tmp_path)
    assert first != second
    assert node('console.log(require(process.argv[1])[0].value)', second) == '43'


def test_same_name_version_distinct_installs_are_not_merged(tmp_path):
    source = diamond(tmp_path)
    package(source / 'node_modules/b/node_modules/x', 'x', [], 'module.exports={value:99};')
    target = snapshot(source, tmp_path)
    assert node('console.log(JSON.stringify(require(process.argv[1])))', target) == '[{"value":42},{"value":99}]'


def test_exclusions_are_explicit_and_preserve_gitignored_build(tmp_path):
    source = package(tmp_path / 'plugin', 'plugin')
    (source / '.gitignore').write_text('dist/\nbackup/\n')
    (source / 'dist').mkdir(); (source / 'dist/index.js').write_text('runtime')
    (source / 'backup').mkdir(); (source / 'backup/blob').write_text('unneeded')
    (source / '.ccb-snapshot-exclude').write_text('# literal paths\nbackup/\n')
    target = snapshot(source, tmp_path)
    assert (target / 'dist/index.js').read_text() == 'runtime'
    assert not (target / 'backup').exists()
    (source / 'backup/blob').write_text('changed excluded file')
    assert snapshot(source, tmp_path) == target


@pytest.mark.parametrize('value', ['../outside', '/', 'package.json', '.ccb-snapshot-exclude', '.'])
def test_invalid_exclusions_fail(tmp_path, value):
    source = package(tmp_path / 'plugin', 'plugin')
    (source / '.ccb-snapshot-exclude').write_text(value)
    with pytest.raises(RuntimeError, match='invalid Pi snapshot exclusion'):
        snapshot(source, tmp_path)


def test_concurrent_builders_publish_one_bundle(tmp_path):
    source = diamond(tmp_path)
    with ThreadPoolExecutor(max_workers=4) as pool:
        targets = list(pool.map(lambda _: snapshot(source, tmp_path), range(4)))
    assert len(set(targets)) == 1
    assert node('console.log(require(process.argv[1])[0].value)', targets[0]) == '42'
    assert not list((tmp_path / 'cache/local-packages/graph-v1').glob('.ccb-build-*'))


def test_killed_builder_staging_is_reclaimed(tmp_path):
    source = diamond(tmp_path)
    script = '''
import sys,os
from pathlib import Path
from provider_backends.pi import package_snapshot as p
from provider_backends.pi.home import _snapshot_package_tree
original=p.shutil.copytree
def crash(*args,**kwargs):
    original(*args,**kwargs)
    os._exit(77)
p.shutil.copytree=crash
_snapshot_package_tree(Path(sys.argv[1]),cache_root=Path(sys.argv[2]),category='local-packages')
'''
    env = {**os.environ, 'PYTHONPATH': str(Path(__file__).resolve().parents[1] / 'lib')}
    result = subprocess.run([sys.executable, '-c', script, str(source), str(tmp_path / 'cache')], env=env)
    assert result.returncode == 77
    parent = tmp_path / 'cache/local-packages/graph-v1'
    assert list(parent.glob('.ccb-build-*'))
    target = snapshot(source, tmp_path)
    assert not list(parent.glob('.ccb-build-*'))
    assert node('console.log(require(process.argv[1])[0].value)', target) == '42'


def test_source_mutation_during_copy_is_rejected(tmp_path):
    source = diamond(tmp_path)
    original = package_snapshot.shutil.copytree
    def changed(src, dst, **kwargs):
        result = original(src, dst, **kwargs)
        if Path(src) == source:
            (source / 'index.js').write_text('changed')
        return result
    with patch.object(package_snapshot.shutil, 'copytree', side_effect=changed):
        with pytest.raises(RuntimeError, match='changed during snapshot'):
            snapshot(source, tmp_path)
