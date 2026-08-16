from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess

import pytest

from agents.models import AgentSpec, PermissionMode, QueuePolicy, RestoreMode, RuntimeMode, WorkspaceMode
from project.resolver import bootstrap_project
from workspace.binding import WorkspaceBindingStore
from workspace import git_worktree as git_worktree_runtime
from workspace.git_worktree import (
    WorkspaceBindingAuthority,
    remove_clean_registered_worktree,
    workspace_is_dirty,
)
from workspace.materializer import WorkspaceMaterializer
from workspace.planner import WorkspacePlanner


def _init_repo(root: Path) -> None:
    root.mkdir(parents=True)
    (root / 'README.md').write_text('hello\n', encoding='utf-8')
    subprocess.run(['git', 'init'], cwd=root, check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    subprocess.run(['git', 'config', 'user.email', 'test@example.com'], cwd=root, check=True)
    subprocess.run(['git', 'config', 'user.name', 'Test User'], cwd=root, check=True)
    subprocess.run(['git', 'add', '.'], cwd=root, check=True)
    subprocess.run(
        ['git', 'commit', '-m', 'init'],
        cwd=root,
        check=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )


def _managed_worktree(tmp_path: Path):
    project_root = tmp_path / 'repo'
    _init_repo(project_root)
    context = bootstrap_project(project_root)
    spec = AgentSpec(
        name='agent1',
        provider='codex',
        target='.',
        workspace_mode=WorkspaceMode.GIT_WORKTREE,
        workspace_root=None,
        runtime_mode=RuntimeMode.PANE_BACKED,
        restore_default=RestoreMode.AUTO,
        permission_default=PermissionMode.MANUAL,
        queue_policy=QueuePolicy.SERIAL_PER_AGENT,
    )
    plan = WorkspacePlanner().plan(spec, context)
    WorkspaceMaterializer().materialize(plan)
    WorkspaceBindingStore().save(plan)
    assert plan.branch_name is not None
    authority = WorkspaceBindingAuthority(
        target_project=project_root,
        project_id=context.project_id,
        workspace_path=plan.workspace_path,
        branch_name=plan.branch_name,
        agent_name=plan.agent_name,
    )
    return project_root, plan, authority


def _binding_record(plan) -> dict[str, object]:
    assert plan.binding_path is not None
    return json.loads(plan.binding_path.read_text(encoding='utf-8'))


def test_workspace_status_excludes_only_valid_owned_untracked_binding(tmp_path: Path) -> None:
    _project_root, plan, authority = _managed_worktree(tmp_path)

    assert workspace_is_dirty(plan.workspace_path) is True
    assert workspace_is_dirty(plan.workspace_path, binding_authority=authority) is False


def test_workspace_status_keeps_other_untracked_file_dirty_even_with_owned_binding(tmp_path: Path) -> None:
    _project_root, plan, authority = _managed_worktree(tmp_path)
    (plan.workspace_path / 'user-artifact.txt').write_text('keep me\n', encoding='utf-8')

    assert workspace_is_dirty(plan.workspace_path, binding_authority=authority) is True


@pytest.mark.skipif(os.name == 'nt', reason='Windows does not permit newline in file names')
def test_workspace_status_handles_unusual_untracked_filename_without_losing_it(tmp_path: Path) -> None:
    _project_root, plan, authority = _managed_worktree(tmp_path)
    (plan.workspace_path / 'user\nartifact.txt').write_text('keep me\n', encoding='utf-8')

    assert workspace_is_dirty(plan.workspace_path, binding_authority=authority) is True


@pytest.mark.parametrize('mutation', ['invalid-json', 'foreign-project', 'wrong-branch', 'wrong-agent'])
def test_workspace_status_rejects_unowned_or_invalid_binding(
    tmp_path: Path,
    mutation: str,
) -> None:
    _project_root, plan, authority = _managed_worktree(tmp_path)
    assert plan.binding_path is not None
    if mutation == 'invalid-json':
        plan.binding_path.write_text('{not-json\n', encoding='utf-8')
    else:
        record = _binding_record(plan)
        if mutation == 'foreign-project':
            record['target_project'] = str(tmp_path / 'other-project')
        elif mutation == 'wrong-branch':
            record['branch_name'] = 'ccb/other-agent'
        else:
            record['agent_name'] = 'other-agent'
        plan.binding_path.write_text(json.dumps(record) + '\n', encoding='utf-8')

    assert workspace_is_dirty(plan.workspace_path, binding_authority=authority) is True


def test_workspace_status_rejects_symlink_binding(tmp_path: Path) -> None:
    _project_root, plan, authority = _managed_worktree(tmp_path)
    assert plan.binding_path is not None
    record = _binding_record(plan)
    external = tmp_path / 'external-binding.json'
    external.write_text(json.dumps(record) + '\n', encoding='utf-8')
    plan.binding_path.unlink()
    plan.binding_path.symlink_to(external)

    assert workspace_is_dirty(plan.workspace_path, binding_authority=authority) is True


def test_workspace_status_keeps_tracked_binding_modification_dirty(tmp_path: Path) -> None:
    _project_root, plan, authority = _managed_worktree(tmp_path)
    assert plan.binding_path is not None
    subprocess.run(['git', '-C', str(plan.workspace_path), 'add', '-f', '.ccb-workspace.json'], check=True)
    subprocess.run(
        ['git', '-C', str(plan.workspace_path), 'commit', '-m', 'track marker'],
        check=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    record = _binding_record(plan)
    record['extra'] = 'modified'
    plan.binding_path.write_text(json.dumps(record) + '\n', encoding='utf-8')

    assert workspace_is_dirty(plan.workspace_path, binding_authority=authority) is True


def test_remove_clean_worktree_preserves_tracked_binding(tmp_path: Path) -> None:
    project_root, plan, authority = _managed_worktree(tmp_path)
    assert plan.binding_path is not None
    subprocess.run(['git', '-C', str(plan.workspace_path), 'add', '-f', '.ccb-workspace.json'], check=True)
    subprocess.run(
        ['git', '-C', str(plan.workspace_path), 'commit', '-m', 'track marker'],
        check=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    assert workspace_is_dirty(plan.workspace_path, binding_authority=authority) is True

    with pytest.raises(RuntimeError, match='dirty or unreadable worktree'):
        remove_clean_registered_worktree(
            project_root,
            plan.workspace_path,
            binding_authority=authority,
        )

    assert plan.binding_path.exists()
    assert plan.workspace_path.exists()


def test_remove_clean_worktree_removes_owned_marker_without_force(tmp_path: Path) -> None:
    project_root, plan, authority = _managed_worktree(tmp_path)

    removed = remove_clean_registered_worktree(
        project_root,
        plan.workspace_path,
        binding_authority=authority,
    )

    assert removed is True
    assert not plan.workspace_path.exists()


def test_remove_clean_worktree_rechecks_for_user_file_before_removal(
    monkeypatch,
    tmp_path: Path,
) -> None:
    project_root, plan, authority = _managed_worktree(tmp_path)
    original = git_worktree_runtime.workspace_is_dirty
    calls = 0

    def _racing_status(workspace_path, *, binding_authority=None):
        nonlocal calls
        calls += 1
        result = original(workspace_path, binding_authority=binding_authority)
        if calls == 1:
            (plan.workspace_path / 'late-user-artifact.txt').write_text('keep me\n', encoding='utf-8')
        return result

    monkeypatch.setattr(git_worktree_runtime, 'workspace_is_dirty', _racing_status)

    with pytest.raises(RuntimeError, match='changed during retirement'):
        remove_clean_registered_worktree(
            project_root,
            plan.workspace_path,
            binding_authority=authority,
        )

    assert plan.workspace_path.exists()
    assert (plan.workspace_path / 'late-user-artifact.txt').read_text(encoding='utf-8') == 'keep me\n'
