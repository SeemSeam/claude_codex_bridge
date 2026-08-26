from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import shutil
import subprocess

from agents.models import WorkspaceMode
from workspace.git_worktree import can_use_git_worktree, has_missing_registered_worktree, prune_missing_worktrees_under
from workspace.models import WorkspacePlan

_COPY_IGNORE_PATTERNS = shutil.ignore_patterns('.git', '.ccb', '__pycache__', '.pytest_cache')


@dataclass(frozen=True)
class MaterializationResult:
    workspace_path: Path
    created: bool
    mode: str


class WorkspaceMaterializer:
    def materialize(self, plan: WorkspacePlan) -> MaterializationResult:
        if plan.workspace_mode is WorkspaceMode.INPLACE:
            plan.workspace_path.mkdir(parents=True, exist_ok=True)
            return MaterializationResult(workspace_path=plan.workspace_path, created=False, mode=plan.workspace_mode.value)
        if plan.workspace_mode is WorkspaceMode.COPY:
            return self._materialize_copy(plan)
        if plan.workspace_mode is WorkspaceMode.GIT_WORKTREE:
            return self._materialize_git_worktree(plan)
        raise ValueError(f'unsupported workspace_mode: {plan.workspace_mode}')

    def _materialize_copy(self, plan: WorkspacePlan) -> MaterializationResult:
        if self._is_placeholder_workspace(plan):
            self._clear_placeholder_workspace(plan)
        if plan.workspace_path.exists():
            return MaterializationResult(workspace_path=plan.workspace_path, created=False, mode=plan.workspace_mode.value)
        plan.workspace_path.parent.mkdir(parents=True, exist_ok=True)
        shutil.copytree(plan.source_root, plan.workspace_path, ignore=_COPY_IGNORE_PATTERNS)
        return MaterializationResult(workspace_path=plan.workspace_path, created=True, mode=plan.workspace_mode.value)

    def _materialize_git_worktree(self, plan: WorkspacePlan) -> MaterializationResult:
        if plan.branch_name is None:
            if plan.workspace_scope != 'external':
                raise ValueError('git-worktree workspace requires branch_name')
        if not can_use_git_worktree(plan.project_root):
            raise RuntimeError(
                'git-worktree workspace requires a git repository: '
                f'{plan.project_root}; use workspace_mode="copy" for an explicit directory copy'
            )
        if plan.workspace_scope == 'external':
            self._validate_external_git_workspace(plan)
            return MaterializationResult(workspace_path=plan.workspace_path, created=False, mode=plan.workspace_mode.value)

        if self._is_existing_git_workspace(plan.workspace_path):
            self._validate_existing_git_workspace(plan)
            return MaterializationResult(workspace_path=plan.workspace_path, created=False, mode=plan.workspace_mode.value)

        if self._is_placeholder_workspace(plan):
            self._clear_placeholder_workspace(plan)
        elif plan.workspace_path.exists() and any(plan.workspace_path.iterdir()):
            raise RuntimeError(f'workspace path is not empty and is not a git worktree: {plan.workspace_path}')

        self._prune_stale_worktree_registration(plan)
        plan.workspace_path.parent.mkdir(parents=True, exist_ok=True)
        if has_missing_registered_worktree(plan.project_root, plan.workspace_path):
            prune_missing_worktrees_under(plan.project_root, plan.workspace_path.parent)

        try:
            self._run(self._git_worktree_add_args(plan), error=f'failed to materialize git worktree for {plan.agent_name}')
        except RuntimeError:
            self._prune_stale_worktree_registration(plan)
            if not has_missing_registered_worktree(plan.project_root, plan.workspace_path):
                raise
            prune_missing_worktrees_under(plan.project_root, plan.workspace_path.parent)
            self._run(
                self._git_worktree_add_args(plan, force=True),
                error=f'failed to materialize git worktree for {plan.agent_name}',
            )
        self._validate_existing_git_workspace(plan)
        return MaterializationResult(workspace_path=plan.workspace_path, created=True, mode=plan.workspace_mode.value)

    def _git_worktree_add_args(self, plan: WorkspacePlan, *, force: bool = False) -> list[str]:
        assert plan.branch_name is not None
        branch_exists = self._branch_exists(plan.project_root, plan.branch_name)
        args = ['git', '-C', str(plan.project_root), 'worktree', 'add']
        if force:
            args.append('-f')
        if branch_exists:
            args.extend([str(plan.workspace_path), plan.branch_name])
        else:
            args.extend(
                ['-b', plan.branch_name, str(plan.workspace_path), plan.base_commit or 'HEAD']
            )
        return args

    def _prune_stale_worktree_registration(self, plan: WorkspacePlan) -> None:
        if plan.workspace_path.exists():
            return
        registered, prunable = self._worktree_registration_status(plan.project_root, plan.workspace_path)
        if not registered or not prunable:
            return
        subprocess.run(
            ['git', '-C', str(plan.project_root), 'worktree', 'prune'],
            check=False,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )

    def _worktree_registration_status(self, repo_root: Path, workspace_path: Path) -> tuple[bool, bool]:
        result = subprocess.run(
            ['git', '-C', str(repo_root), 'worktree', 'list', '--porcelain'],
            check=False,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        if result.returncode != 0:
            return False, False
        target = workspace_path.expanduser().resolve()
        registered = False
        prunable = False
        for block in (chunk.strip() for chunk in (result.stdout or '').split('\n\n') if chunk.strip()):
            lines = block.splitlines()
            if not lines or not lines[0].startswith('worktree '):
                continue
            raw_path = lines[0][len('worktree ') :].strip()
            try:
                current = Path(raw_path).expanduser().resolve()
            except Exception:
                current = Path(raw_path).expanduser().absolute()
            if current != target:
                continue
            registered = True
            prunable = any(line.startswith('prunable ') for line in lines[1:])
            break
        return registered, prunable

    def _validate_existing_git_workspace(self, plan: WorkspacePlan) -> None:
        top_level = self._git_output(plan.workspace_path, ['rev-parse', '--show-toplevel'])
        if Path(top_level).expanduser().resolve() != plan.workspace_path:
            raise RuntimeError(f'workspace path is not the git worktree root: {plan.workspace_path}')
        if plan.branch_name:
            current_branch = self._git_output(plan.workspace_path, ['branch', '--show-current'])
            if current_branch and current_branch != plan.branch_name:
                raise RuntimeError(
                    f'workspace branch mismatch for {plan.agent_name}: expected {plan.branch_name}, got {current_branch}'
                )

    def _validate_external_git_workspace(self, plan: WorkspacePlan) -> None:
        if plan.workspace_path == plan.project_root:
            raise RuntimeError(
                f'external workspace_path must not equal the project root; use workspace_mode="inplace": {plan.workspace_path}'
            )
        if not plan.workspace_path.exists():
            raise RuntimeError(f'external workspace_path does not exist: {plan.workspace_path}')
        if not self._is_existing_git_workspace(plan.workspace_path):
            raise RuntimeError(f'external workspace_path is not a git workspace root: {plan.workspace_path}')
        self._validate_existing_git_workspace(plan)
        project_common = self._git_common_dir(plan.project_root)
        workspace_common = self._git_common_dir(plan.workspace_path)
        if project_common != workspace_common:
            raise RuntimeError(
                f'external workspace_path is not from the project git repository: {plan.workspace_path}'
            )

    def _is_existing_git_workspace(self, path: Path) -> bool:
        git_dir = path / '.git'
        if not git_dir.exists():
            return False
        try:
            self._git_output(path, ['rev-parse', '--show-toplevel'])
        except RuntimeError:
            return False
        return True

    def _is_placeholder_workspace(self, plan: WorkspacePlan) -> bool:
        if not plan.workspace_path.exists() or not plan.workspace_path.is_dir():
            return False
        allowed = {plan.binding_path.name} if plan.binding_path is not None else set()
        entries = list(plan.workspace_path.iterdir())
        return all(entry.name in allowed for entry in entries)

    def _clear_placeholder_workspace(self, plan: WorkspacePlan) -> None:
        if plan.binding_path is not None and plan.binding_path.exists():
            plan.binding_path.unlink()
        try:
            plan.workspace_path.rmdir()
        except OSError:
            pass

    def _branch_exists(self, repo_root: Path, branch_name: str) -> bool:
        result = subprocess.run(
            ['git', '-C', str(repo_root), 'show-ref', '--verify', '--quiet', f'refs/heads/{branch_name}'],
            check=False,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        return result.returncode == 0

    def _git_output(self, cwd: Path, args: list[str]) -> str:
        result = subprocess.run(
            ['git', '-C', str(cwd), *args],
            check=False,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        if result.returncode != 0:
            raise RuntimeError((result.stderr or result.stdout or 'git command failed').strip())
        return (result.stdout or '').strip()

    def _git_common_dir(self, cwd: Path) -> Path:
        raw = self._git_output(cwd, ['rev-parse', '--git-common-dir'])
        path = Path(raw).expanduser()
        if not path.is_absolute():
            path = Path(cwd) / path
        return path.resolve()

    def _run(self, args: list[str], *, error: str) -> None:
        result = subprocess.run(args, check=False, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, encoding="utf-8")
        if result.returncode != 0:
            detail = (result.stderr or result.stdout or '').strip()
            raise RuntimeError(f'{error}: {detail}')
