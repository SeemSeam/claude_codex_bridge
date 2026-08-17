from __future__ import annotations

import json
import os
from pathlib import Path
import shutil
import stat
import subprocess

import pytest

from agents.models import AgentSpec, PermissionMode, QueuePolicy, RestoreMode, RuntimeMode, WorkspaceMode
from agents.store import AgentSpecStore
from project.resolver import bootstrap_project
from storage.paths import PathLayout
from workspace.binding import WorkspaceBindingStore
from workspace.materializer import WorkspaceMaterializer
from workspace.planner import WorkspacePlanner
from workspace.reconcile import reconcile_start_workspaces
from workspace.validator import WorkspaceValidator


def _spec(
    *,
    workspace_mode: WorkspaceMode = WorkspaceMode.GIT_WORKTREE,
    workspace_root: str | None = None,
    workspace_path: str | None = None,
    workspace_group: str | None = None,
    branch_template: str | None = None,
    name: str = 'agent1',
) -> AgentSpec:
    return AgentSpec(
        name=name,
        provider='codex',
        target='.',
        workspace_mode=workspace_mode,
        workspace_root=workspace_root,
        workspace_path=workspace_path,
        workspace_group=workspace_group,
        runtime_mode=RuntimeMode.PANE_BACKED,
        restore_default=RestoreMode.AUTO,
        permission_default=PermissionMode.MANUAL,
        queue_policy=QueuePolicy.SERIAL_PER_AGENT,
        branch_template=branch_template,
    )


def test_workspace_planner_builds_git_worktree_plan(tmp_path: Path) -> None:
    project_root = tmp_path / 'repo'
    project_root.mkdir()
    ctx = bootstrap_project(project_root)

    plan = WorkspacePlanner().plan(_spec(), ctx)
    assert plan.workspace_mode is WorkspaceMode.GIT_WORKTREE
    assert plan.workspace_path == (project_root / '.ccb' / 'workspaces' / 'agent1').resolve()
    assert plan.branch_name == 'ccb/agent1'
    assert plan.binding_path is not None
    assert plan.workspace_scope == 'agent'


def test_workspace_planner_supports_external_root_and_custom_branch_template(tmp_path: Path) -> None:
    project_root = tmp_path / 'repo'
    external = tmp_path / 'ws'
    project_root.mkdir()
    ctx = bootstrap_project(project_root)

    plan = WorkspacePlanner().plan(
        _spec(workspace_root=str(external), branch_template='ccb/{project_slug}/{agent_name}'),
        ctx,
    )
    assert external.resolve() in plan.workspace_path.parents
    assert plan.branch_name is not None
    assert 'agent1' in plan.branch_name


def test_workspace_planner_supports_exact_external_workspace_path(tmp_path: Path) -> None:
    project_root = tmp_path / 'repo'
    external = tmp_path / 'external-worktree'
    project_root.mkdir()
    ctx = bootstrap_project(project_root)

    plan = WorkspacePlanner().plan(_spec(workspace_path=str(external)), ctx)

    assert plan.workspace_path == external.resolve()
    assert plan.workspace_scope == 'external'
    assert plan.branch_name is None
    assert plan.binding_path is None


def test_workspace_planner_supports_internal_workspace_group(tmp_path: Path) -> None:
    project_root = tmp_path / 'repo'
    project_root.mkdir()
    ctx = bootstrap_project(project_root)

    plan = WorkspacePlanner().plan(_spec(workspace_group='main'), ctx)

    assert plan.workspace_path == (project_root / '.ccb' / 'workspaces' / 'groups' / 'main').resolve()
    assert plan.workspace_scope == 'group'
    assert plan.branch_name == 'ccb/group/main'
    assert plan.binding_path == plan.workspace_path / '.ccb-workspace.json'


def test_workspace_planner_inplace_uses_project_root(tmp_path: Path) -> None:
    project_root = tmp_path / 'repo'
    project_root.mkdir()
    ctx = bootstrap_project(project_root)

    plan = WorkspacePlanner().plan(_spec(workspace_mode=WorkspaceMode.INPLACE), ctx)
    assert plan.workspace_path == project_root.resolve()
    assert plan.binding_path is None
    assert plan.unsafe_shared_workspace is True


def test_workspace_planner_rejects_unknown_branch_template_var(tmp_path: Path) -> None:
    project_root = tmp_path / 'repo'
    project_root.mkdir()
    ctx = bootstrap_project(project_root)

    with pytest.raises(ValueError):
        WorkspacePlanner().plan(_spec(branch_template='ccb/{unknown}'), ctx)


def test_workspace_binding_and_validator_roundtrip(tmp_path: Path) -> None:
    project_root = tmp_path / 'repo'
    project_root.mkdir()
    ctx = bootstrap_project(project_root)
    plan = WorkspacePlanner().plan(_spec(), ctx)
    plan.workspace_path.mkdir(parents=True)

    binding_path = WorkspaceBindingStore().save(plan)
    assert binding_path is not None and binding_path.exists()
    result = WorkspaceValidator().validate(plan)
    assert result.ok is True
    assert result.errors == ()


def test_workspace_group_binding_allows_multiple_agents(tmp_path: Path) -> None:
    project_root = tmp_path / 'repo'
    project_root.mkdir()
    ctx = bootstrap_project(project_root)
    planner = WorkspacePlanner()
    plan1 = planner.plan(_spec(name='agent1', workspace_group='main'), ctx)
    plan2 = planner.plan(_spec(name='agent2', workspace_group='main'), ctx)
    plan1.workspace_path.mkdir(parents=True)

    WorkspaceBindingStore().save(plan2)

    result = WorkspaceValidator().validate(plan1)

    assert plan1.workspace_path == plan2.workspace_path
    assert result.ok is True
    assert result.errors == ()


def test_workspace_group_binding_can_target_controller_owned_worktree(tmp_path: Path) -> None:
    project_root = tmp_path / 'repo'
    project_root.mkdir()
    ctx = bootstrap_project(project_root)
    controller_path = tmp_path / 'controller-node-worktree'
    binding_path = PathLayout(project_root).workspace_group_binding_path('compact-node-001')
    WorkspaceBindingStore().bind_controller_worktree(
        binding_path,
        target_project=project_root,
        project_id=ctx.project_id,
        workspace_group='compact-node-001',
        workspace_path=controller_path,
        branch_name='ccb/workgroup/tx/node-001',
    )

    worker = WorkspacePlanner().plan(
        _spec(name='worker', workspace_group='compact-node-001'),
        ctx,
    )
    reviewer = WorkspacePlanner().plan(
        _spec(name='reviewer', workspace_group='compact-node-001'),
        ctx,
    )

    assert worker.workspace_path == controller_path.resolve()
    assert reviewer.workspace_path == controller_path.resolve()
    assert worker.branch_name == 'ccb/workgroup/tx/node-001'
    assert reviewer.branch_name == worker.branch_name
    local_binding = controller_path / '.ccb-workspace.json'
    assert local_binding.exists()
    record = json.loads(local_binding.read_text(encoding='utf-8'))
    assert record['target_project'] == str(project_root.resolve())
    assert record['workspace_path'] == str(controller_path.resolve())


def test_workspace_validator_reports_missing_binding(tmp_path: Path) -> None:
    project_root = tmp_path / 'repo'
    project_root.mkdir()
    ctx = bootstrap_project(project_root)
    plan = WorkspacePlanner().plan(_spec(), ctx)
    plan.workspace_path.mkdir(parents=True)

    result = WorkspaceValidator().validate(plan)
    assert result.ok is True
    assert result.warnings == ('workspace binding file is missing',)


def test_workspace_materializer_creates_real_git_worktree(tmp_path: Path) -> None:
    project_root = tmp_path / 'repo'
    project_root.mkdir()
    (project_root / 'README.md').write_text('hello\n', encoding='utf-8')
    subprocess.run(['git', 'init'], cwd=project_root, check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    subprocess.run(['git', 'config', 'user.email', 'test@example.com'], cwd=project_root, check=True)
    subprocess.run(['git', 'config', 'user.name', 'Test User'], cwd=project_root, check=True)
    subprocess.run(['git', 'add', '.'], cwd=project_root, check=True)
    subprocess.run(['git', 'commit', '-m', 'init'], cwd=project_root, check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)

    ctx = bootstrap_project(project_root)
    plan = WorkspacePlanner().plan(_spec(), ctx)

    result = WorkspaceMaterializer().materialize(plan)

    assert result.created is True
    assert (plan.workspace_path / '.git').exists()
    assert (plan.workspace_path / 'README.md').read_text(encoding='utf-8') == 'hello\n'
    branch = subprocess.run(
        ['git', '-C', str(plan.workspace_path), 'branch', '--show-current'],
        check=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    ).stdout.strip()
    assert branch == 'ccb/agent1'


def test_workspace_materializer_reuses_internal_group_worktree(tmp_path: Path) -> None:
    project_root = tmp_path / 'repo'
    _init_git_repo(project_root)
    ctx = bootstrap_project(project_root)
    planner = WorkspacePlanner()
    plan1 = planner.plan(_spec(name='agent1', workspace_group='main'), ctx)
    plan2 = planner.plan(_spec(name='agent2', workspace_group='main'), ctx)
    materializer = WorkspaceMaterializer()

    first = materializer.materialize(plan1)
    second = materializer.materialize(plan2)

    assert first.created is True
    assert second.created is False
    assert plan1.workspace_path == plan2.workspace_path
    branch = subprocess.run(
        ['git', '-C', str(plan1.workspace_path), 'branch', '--show-current'],
        check=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    ).stdout.strip()
    assert branch == 'ccb/group/main'


def test_workspace_materializer_validates_external_workspace_path_without_creating(tmp_path: Path) -> None:
    project_root = tmp_path / 'repo'
    external = tmp_path / 'external-worktree'
    _init_git_repo(project_root)
    subprocess.run(
        ['git', '-C', str(project_root), 'worktree', 'add', '-b', 'manual/shared', str(external), 'HEAD'],
        check=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    ctx = bootstrap_project(project_root)
    plan = WorkspacePlanner().plan(_spec(workspace_path=str(external)), ctx)

    result = WorkspaceMaterializer().materialize(plan)

    assert result.created is False
    assert plan.binding_path is None
    branch = subprocess.run(
        ['git', '-C', str(external), 'branch', '--show-current'],
        check=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    ).stdout.strip()
    assert branch == 'manual/shared'


def test_workspace_materializer_rejects_missing_external_workspace_path(tmp_path: Path) -> None:
    project_root = tmp_path / 'repo'
    external = tmp_path / 'missing-worktree'
    _init_git_repo(project_root)
    ctx = bootstrap_project(project_root)
    plan = WorkspacePlanner().plan(_spec(workspace_path=str(external)), ctx)

    with pytest.raises(RuntimeError, match='external workspace_path does not exist'):
        WorkspaceMaterializer().materialize(plan)


def test_workspace_materializer_rejects_external_workspace_path_equal_to_project_root(tmp_path: Path) -> None:
    project_root = tmp_path / 'repo'
    _init_git_repo(project_root)
    ctx = bootstrap_project(project_root)
    plan = WorkspacePlanner().plan(_spec(workspace_path=str(project_root)), ctx)

    with pytest.raises(RuntimeError, match='external workspace_path must not equal the project root'):
        WorkspaceMaterializer().materialize(plan)


def test_workspace_materializer_rejects_git_worktree_for_non_git_project(tmp_path: Path) -> None:
    project_root = tmp_path / 'repo'
    project_root.mkdir()
    (project_root / 'README.md').write_text('should-not-copy\n', encoding='utf-8')
    ctx = bootstrap_project(project_root)
    plan = WorkspacePlanner().plan(_spec(), ctx)

    with pytest.raises(RuntimeError, match='git-worktree workspace requires a git repository'):
        WorkspaceMaterializer().materialize(plan)

    assert plan.workspace_path.exists() is False


def test_workspace_materializer_allows_explicit_copy_for_non_git_project(tmp_path: Path) -> None:
    project_root = tmp_path / 'repo'
    project_root.mkdir()
    (project_root / 'README.md').write_text('copy\n', encoding='utf-8')
    ctx = bootstrap_project(project_root)
    plan = WorkspacePlanner().plan(_spec(workspace_mode=WorkspaceMode.COPY), ctx)

    result = WorkspaceMaterializer().materialize(plan)

    assert result.created is True
    assert (plan.workspace_path / 'README.md').read_text(encoding='utf-8') == 'copy\n'
    assert not (plan.workspace_path / '.git').exists()


def test_workspace_materializer_clears_placeholder_binding_before_worktree_add(tmp_path: Path) -> None:
    project_root = tmp_path / 'repo'
    project_root.mkdir()
    (project_root / 'README.md').write_text('hello\n', encoding='utf-8')
    subprocess.run(['git', 'init'], cwd=project_root, check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    subprocess.run(['git', 'config', 'user.email', 'test@example.com'], cwd=project_root, check=True)
    subprocess.run(['git', 'config', 'user.name', 'Test User'], cwd=project_root, check=True)
    subprocess.run(['git', 'add', '.'], cwd=project_root, check=True)
    subprocess.run(['git', 'commit', '-m', 'init'], cwd=project_root, check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)

    ctx = bootstrap_project(project_root)
    plan = WorkspacePlanner().plan(_spec(), ctx)
    plan.workspace_path.mkdir(parents=True)
    assert plan.binding_path is not None
    plan.binding_path.write_text('{}\n', encoding='utf-8')

    WorkspaceMaterializer().materialize(plan)

    assert (plan.workspace_path / '.git').exists()
    assert not plan.binding_path.exists()


def test_workspace_materializer_recovers_missing_registered_git_worktree(tmp_path: Path) -> None:
    project_root = tmp_path / 'repo'
    project_root.mkdir()
    (project_root / 'README.md').write_text('hello\n', encoding='utf-8')
    subprocess.run(['git', 'init'], cwd=project_root, check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    subprocess.run(['git', 'config', 'user.email', 'test@example.com'], cwd=project_root, check=True)
    subprocess.run(['git', 'config', 'user.name', 'Test User'], cwd=project_root, check=True)
    subprocess.run(['git', 'add', '.'], cwd=project_root, check=True)
    subprocess.run(['git', 'commit', '-m', 'init'], cwd=project_root, check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)

    ctx = bootstrap_project(project_root)
    plan = WorkspacePlanner().plan(_spec(), ctx)
    materializer = WorkspaceMaterializer()

    materializer.materialize(plan)
    shutil.rmtree(plan.workspace_path)

    listing_before = subprocess.run(
        ['git', '-C', str(project_root), 'worktree', 'list', '--porcelain'],
        check=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    ).stdout
    assert str(plan.workspace_path) in listing_before
    assert 'prunable ' in listing_before

    result = materializer.materialize(plan)

    assert result.created is True
    assert (plan.workspace_path / '.git').exists()
    assert (plan.workspace_path / 'README.md').read_text(encoding='utf-8') == 'hello\n'


def test_reconcile_does_not_remove_group_worktree_still_referenced(tmp_path: Path) -> None:
    project_root = tmp_path / 'repo'
    _init_git_repo(project_root)
    ctx = bootstrap_project(project_root)
    paths = PathLayout(project_root)
    spec1 = _spec(name='agent1', workspace_group='main')
    spec2 = _spec(name='agent2', workspace_group='main')
    store = AgentSpecStore(paths)
    store.save(spec1)
    store.save(spec2)
    plan = WorkspacePlanner().plan(spec1, ctx)
    WorkspaceMaterializer().materialize(plan)
    WorkspaceBindingStore().save(WorkspacePlanner().plan(spec2, ctx))

    summary = reconcile_start_workspaces(project_root, type('Config', (), {'agents': {'agent2': spec2}})())

    assert plan.workspace_path.exists() is True
    assert paths.agent_dir('agent1').exists() is False
    assert paths.agent_dir('agent2').exists() is True
    assert len(summary.retired) == 1
    assert summary.retired[0].agent_name == 'agent1'


def test_reconcile_keeps_user_untracked_file_as_retirement_blocker(tmp_path: Path) -> None:
    project_root = tmp_path / 'repo'
    _init_git_repo(project_root)
    context = bootstrap_project(project_root)
    paths = PathLayout(project_root)
    spec = _spec(name='agent1')
    AgentSpecStore(paths).save(spec)
    plan = WorkspacePlanner().plan(spec, context)
    WorkspaceMaterializer().materialize(plan)
    WorkspaceBindingStore().save(plan)
    user_artifact = plan.workspace_path / 'user-artifact.txt'
    user_artifact.write_text('keep me\n', encoding='utf-8')

    summary = reconcile_start_workspaces(project_root, type('Config', (), {'agents': {}})())

    assert len(summary.blockers) == 1
    assert summary.blockers[0].dirty is True
    assert summary.retired == ()
    assert user_artifact.read_text(encoding='utf-8') == 'keep me\n'
    assert plan.workspace_path.exists()


def test_reconcile_removes_retired_agent_state_with_readonly_files(tmp_path: Path) -> None:
    project_root = tmp_path / 'repo'
    project_root.mkdir()
    paths = PathLayout(project_root)
    spec = _spec(name='retired', workspace_mode=WorkspaceMode.INPLACE)
    AgentSpecStore(paths).save(spec)
    readonly_file = paths.agent_dir('retired') / 'provider-state' / 'claude' / 'home' / '.git' / 'objects' / 'pack' / 'pack.idx'
    readonly_file.parent.mkdir(parents=True)
    readonly_file.write_text('readonly\n', encoding='utf-8')
    readonly_file.chmod(stat.S_IREAD)

    try:
        summary = reconcile_start_workspaces(project_root, type('Config', (), {'agents': {}})())
    finally:
        if readonly_file.exists():
            os.chmod(readonly_file, stat.S_IREAD | stat.S_IWRITE)

    assert paths.agent_dir('retired').exists() is False
    assert len(summary.retired) == 1
    assert summary.retired[0].agent_name == 'retired'
    assert summary.retired[0].removed_agent_state is True


def _init_git_repo(project_root: Path) -> None:
    project_root.mkdir(parents=True, exist_ok=True)
    (project_root / 'README.md').write_text('hello\n', encoding='utf-8')
    subprocess.run(['git', 'init'], cwd=project_root, check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    subprocess.run(['git', 'config', 'user.email', 'test@example.com'], cwd=project_root, check=True)
    subprocess.run(['git', 'config', 'user.name', 'Test User'], cwd=project_root, check=True)
    subprocess.run(['git', 'add', '.'], cwd=project_root, check=True)
    subprocess.run(['git', 'commit', '-m', 'init'], cwd=project_root, check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
