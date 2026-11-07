"""Local Git hygiene without changing user config, index or Provider files."""
from pathlib import Path
import subprocess

import pytest

from opencode_runtime.git_excludes import ensure_runtime_excludes, VOLATILE_TREES


def git(root, *args):
    return subprocess.run(['git', '-C', str(root), *args], capture_output=True, check=False)


@pytest.fixture
def repo(tmp_path):
    git(tmp_path, 'init')
    return tmp_path


@pytest.mark.parametrize('subdir', ['', 'nested project[1]'])
def test_excludes_runtime_but_keeps_config_and_memory(repo, subdir):
    root = repo / subdir
    root.mkdir(exist_ok=True)
    (root / '.ccb').mkdir()
    (root / '.ccb/ccb.config').write_text('config')
    (root / '.ccb/ccb_memory.md').write_text('memory')
    git(repo, 'add', '.')
    index_before = (repo / '.git/index').read_bytes()
    exclude = repo / '.git/info/exclude'
    exclude.write_bytes(b'# user rule\n*.old')
    assert ensure_runtime_excludes(root) == ()
    once = exclude.read_bytes()
    assert once.startswith(b'# user rule\n*.old\n')
    assert ensure_runtime_excludes(root) == () and exclude.read_bytes() == once
    for name in VOLATILE_TREES:
        probe = (root / '.ccb' / name / 'probe').relative_to(repo).as_posix()
        assert git(repo, 'check-ignore', '--no-index', '--', probe).returncode == 0
    for name in ('ccb.config', 'ccb_memory.md'):
        probe = (root / '.ccb' / name).relative_to(repo).as_posix()
        assert git(repo, 'check-ignore', '--no-index', '--', probe).returncode == 1
    assert (repo / '.git/index').read_bytes() == index_before


def test_linked_worktree_and_tracked_runtime_are_not_untracked_automatically(repo, tmp_path):
    (repo / 'source.txt').write_text('fixture')
    git(repo, 'add', '.')
    git(repo, '-c', 'user.name=Fixture', '-c', 'user.email=fixture@example.invalid', 'commit', '-m', 'fixture')
    worktree = tmp_path / 'linked'
    assert git(repo, 'worktree', 'add', '-b', 'fixture', str(worktree)).returncode == 0
    runtime = worktree / '.ccb/agents/worker/provider-state'
    runtime.mkdir(parents=True)
    (runtime / 'fixture.db').write_text('fixture')
    git(worktree, 'add', '.')
    before = git(worktree, 'ls-files', '-z').stdout
    warnings = ensure_runtime_excludes(worktree)
    assert len(warnings) == 1 and 'already Git-tracked' in warnings[0]
    assert git(worktree, 'ls-files', '-z').stdout == before
    assert (worktree / '.git').is_file()
    assert not (worktree / '.git/info').exists()


def test_non_git_skip_and_failed_write_preserves_user_rules(repo, tmp_path, monkeypatch):
    outside = tmp_path.parent / (tmp_path.name + '-outside')
    outside.mkdir()
    assert ensure_runtime_excludes(outside) == ()
    exclude = repo / '.git/info/exclude'
    before = exclude.read_bytes()
    monkeypatch.setattr('opencode_runtime.git_excludes.os.replace', lambda *a: (_ for _ in ()).throw(PermissionError()))
    warnings = ensure_runtime_excludes(repo)
    assert 'could not update' in warnings[0]
    assert exclude.read_bytes() == before
    assert not exclude.with_name('exclude.lock').exists()


def test_existing_lock_and_symlink_are_preserved(repo, tmp_path):
    exclude = repo / '.git/info/exclude'
    lock = exclude.with_name('exclude.lock')
    lock.write_text('other owner')
    assert ensure_runtime_excludes(repo)
    assert lock.read_text() == 'other owner'
    lock.unlink()
    target = tmp_path / 'external'
    target.write_text('external rule')
    exclude.unlink()
    exclude.symlink_to(target)
    assert ensure_runtime_excludes(repo)
    assert target.read_text() == 'external rule'


def test_project_ignore_negation_is_reported(repo):
    (repo / '.gitignore').write_text('!/.ccb/agents/\n!/.ccb/agents/**\n')
    warnings = ensure_runtime_excludes(repo)
    assert any('overridden' in warning for warning in warnings)
