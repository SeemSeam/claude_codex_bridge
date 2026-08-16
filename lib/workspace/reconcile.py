from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import shutil
import stat
import sys
import time

from agents.models import AgentRuntime, AgentSpec, AgentState, WorkspaceMode, normalize_agent_name
from agents.store import AgentRuntimeStore, AgentSpecStore
from cli.kill_runtime.processes import is_pid_alive
from project.ids import compute_project_id
from project.resolver import ProjectContext
from storage.atomic import atomic_write_json
from storage.paths import PathLayout

from .git_worktree import (
    WorkspaceBindingAuthority,
    branch_is_merged_into_head,
    delete_branch,
    is_registered_worktree,
    remove_clean_registered_worktree,
    workspace_is_dirty,
)
from .planner import WorkspacePlanner


_RMTREE_RETRY_DELAYS_S = (0.05, 0.1, 0.2)
_CLEANUP_DEFERRED_SCHEMA_VERSION = 1


@dataclass(frozen=True)
class WorktreeAlert:
    agent_name: str
    branch_name: str | None
    workspace_path: str
    dirty: bool | None
    merged: bool | None
    registered: bool
    exists: bool
    reason: str

    @property
    def needs_merge(self) -> bool:
        return self.dirty is True or self.merged is False


@dataclass(frozen=True)
class WorkspaceRetirement:
    agent_name: str
    branch_name: str | None
    workspace_path: str
    reason: str
    removed_agent_state: bool = False
    cleanup_deferred: bool = False
    cleanup_reason: str | None = None
    cleanup_deferred_persisted: bool | None = None
    cleanup_marker_cleared: bool | None = None


@dataclass(frozen=True)
class AgentStateCleanupResult:
    removed: bool
    deferred: bool = False
    reason: str | None = None
    deferred_persisted: bool | None = None
    marker_cleared: bool | None = None


@dataclass(frozen=True)
class _AgentStateCleanupDecision:
    can_remove: bool
    reason: str | None = None
    runtime_state: str | None = None
    live_pids: tuple[int, ...] = ()


@dataclass(frozen=True)
class WorkspaceGuardSummary:
    warnings: tuple[WorktreeAlert, ...] = ()
    blockers: tuple[WorktreeAlert, ...] = ()
    retired: tuple[WorkspaceRetirement, ...] = ()


def reconcile_start_workspaces(project_root: Path, config) -> WorkspaceGuardSummary:
    root = _resolve_path(project_root)
    paths = PathLayout(root)
    project_ctx = _project_context(root)
    persisted_specs = _load_persisted_specs(paths)
    desired_specs = dict(config.agents)
    deferred_cleanup = _load_deferred_cleanup(paths)

    warnings: list[WorktreeAlert] = []
    blockers: list[WorktreeAlert] = []
    pending_worktree_retirements: list[tuple[AgentSpec, str, bool, bool]] = []
    pending_state_cleanup: list[tuple[str, str]] = []
    pending_state_cleanup_names: set[str] = set()

    for agent_name, persisted_spec in persisted_specs.items():
        desired_spec = desired_specs.get(agent_name)
        if persisted_spec.workspace_mode is WorkspaceMode.GIT_WORKTREE:
            persisted_plan = WorkspacePlanner().plan(persisted_spec, project_ctx)
            reason = _retirement_reason(persisted_spec, desired_spec, project_ctx)
            if reason is None:
                continue
            if persisted_plan.workspace_scope == 'external':
                if desired_spec is None:
                    pending_state_cleanup.append((agent_name, 'removed_from_config'))
                    pending_state_cleanup_names.add(agent_name)
                continue
            alert = _inspect_worktree(root, project_ctx, persisted_spec, reason=reason)
            if alert.needs_merge:
                blockers.append(alert)
                continue
            remove_workspace = not _workspace_referenced_by_other_desired_agent(
                persisted_spec,
                desired_specs,
                project_ctx,
            )
            pending_worktree_retirements.append((persisted_spec, reason, desired_spec is None, remove_workspace))
            continue
        if desired_spec is None:
            pending_state_cleanup.append((agent_name, 'removed_from_config'))
            pending_state_cleanup_names.add(agent_name)

    for agent_name in deferred_cleanup:
        if agent_name in desired_specs or agent_name in pending_state_cleanup_names:
            continue
        pending_state_cleanup.append((agent_name, 'deferred_cleanup_retry'))
        pending_state_cleanup_names.add(agent_name)

    for spec in desired_specs.values():
        if spec.workspace_mode is not WorkspaceMode.GIT_WORKTREE:
            continue
        if WorkspacePlanner().plan(spec, project_ctx).workspace_scope == 'external':
            continue
        alert = _inspect_worktree(root, project_ctx, spec, reason='active_worktree')
        if alert.needs_merge:
            warnings.append(alert)

    if blockers:
        return WorkspaceGuardSummary(
            warnings=tuple(warnings),
            blockers=tuple(blockers),
        )

    retired: list[WorkspaceRetirement] = []
    for spec, reason, remove_agent_state, remove_workspace in pending_worktree_retirements:
        retired.append(
            _retire_worktree_spec(
                root,
                paths,
                project_ctx,
                spec,
                reason=reason,
                remove_agent_state=remove_agent_state,
                remove_workspace=remove_workspace,
            )
        )
    for agent_name, reason in pending_state_cleanup:
        cleanup = _remove_agent_state(paths, agent_name)
        retired.append(
            WorkspaceRetirement(
                agent_name=agent_name,
                branch_name=None,
                workspace_path='',
                reason=reason,
                removed_agent_state=cleanup.removed,
                cleanup_deferred=cleanup.deferred,
                cleanup_reason=cleanup.reason,
                cleanup_deferred_persisted=cleanup.deferred_persisted,
                cleanup_marker_cleared=cleanup.marker_cleared,
            )
        )

    return WorkspaceGuardSummary(
        warnings=tuple(warnings),
        blockers=(),
        retired=tuple(retired),
    )


def prepare_reset_workspaces(project_root: Path, *, apply: bool = True) -> WorkspaceGuardSummary:
    root = _resolve_path(project_root)
    paths = PathLayout(root)
    project_ctx = _project_context(root)

    blockers: list[WorktreeAlert] = []
    pending_retirements: list[AgentSpec] = []
    for spec in _collect_reset_worktree_specs(root, paths, project_ctx):
        alert = _inspect_worktree(root, project_ctx, spec, reason='reset_context')
        if alert.needs_merge:
            blockers.append(alert)
            continue
        pending_retirements.append(spec)

    if blockers:
        return WorkspaceGuardSummary(blockers=tuple(blockers))

    if not apply:
        return WorkspaceGuardSummary()

    retired = tuple(
        _retire_worktree_spec(
            root,
            paths,
            project_ctx,
            spec,
            reason='reset_context',
            remove_agent_state=False,
        )
        for spec in pending_retirements
    )
    return WorkspaceGuardSummary(retired=retired)


def inspect_kill_worktrees(project_root: Path) -> WorkspaceGuardSummary:
    root = _resolve_path(project_root)
    paths = PathLayout(root)
    project_ctx = _project_context(root)
    warnings = tuple(
        alert
        for spec in _load_persisted_specs(paths).values()
        if spec.workspace_mode is WorkspaceMode.GIT_WORKTREE
        if WorkspacePlanner().plan(spec, project_ctx).workspace_scope != 'external'
        for alert in (_inspect_worktree(root, project_ctx, spec, reason='kill_warning'),)
        if alert.needs_merge
    )
    return WorkspaceGuardSummary(warnings=warnings)


def format_workspace_blockers(action: str, blockers: tuple[WorktreeAlert, ...]) -> str:
    lines = [f'{action} blocked by unmerged or dirty worktree state:']
    for item in blockers:
        branch = item.branch_name or '<none>'
        dirty = _state_text(item.dirty)
        merged = _state_text(item.merged)
        lines.append(
            f'- agent={item.agent_name} reason={item.reason} branch={branch} '
            f'dirty={dirty} merged_into_head={merged} path={item.workspace_path}'
        )
    lines.append('merge or clean the listed worktree branches and retry')
    return '\n'.join(lines)


def _retirement_reason(
    persisted_spec: AgentSpec,
    desired_spec: AgentSpec | None,
    project_ctx: ProjectContext,
) -> str | None:
    if desired_spec is None:
        return 'removed_from_config'
    if desired_spec.workspace_mode is not WorkspaceMode.GIT_WORKTREE:
        return 'workspace_mode_changed'
    planner = WorkspacePlanner()
    current = planner.plan(desired_spec, project_ctx)
    persisted = planner.plan(persisted_spec, project_ctx)
    if current.workspace_path != persisted.workspace_path or current.branch_name != persisted.branch_name:
        return 'worktree_identity_changed'
    return None


def _inspect_worktree(
    project_root: Path,
    project_ctx: ProjectContext,
    spec: AgentSpec,
    *,
    reason: str,
) -> WorktreeAlert:
    plan = WorkspacePlanner().plan(spec, project_ctx)
    branch_name = plan.branch_name
    merged = branch_is_merged_into_head(project_root, branch_name) if branch_name else None
    binding_authority = _workspace_binding_authority(plan)
    return WorktreeAlert(
        agent_name=spec.name,
        branch_name=branch_name,
        workspace_path=str(plan.workspace_path),
        dirty=workspace_is_dirty(plan.workspace_path, binding_authority=binding_authority),
        merged=merged,
        registered=is_registered_worktree(project_root, plan.workspace_path),
        exists=plan.workspace_path.exists(),
        reason=reason,
    )


def _workspace_referenced_by_other_desired_agent(
    retired_spec: AgentSpec,
    desired_specs: dict[str, AgentSpec],
    project_ctx: ProjectContext,
) -> bool:
    planner = WorkspacePlanner()
    retired_plan = planner.plan(retired_spec, project_ctx)
    retired_identity = _workspace_identity(retired_plan)
    for desired_spec in desired_specs.values():
        if desired_spec.name == retired_spec.name:
            continue
        if desired_spec.workspace_mode is not WorkspaceMode.GIT_WORKTREE:
            continue
        desired_plan = planner.plan(desired_spec, project_ctx)
        if desired_plan.workspace_scope == 'external':
            continue
        if _workspace_identity(desired_plan) == retired_identity:
            return True
    return False


def _workspace_identity(plan) -> tuple[str, str]:
    return (str(_resolve_path(plan.workspace_path)), str(plan.branch_name or ''))


def _retire_worktree_spec(
    project_root: Path,
    paths: PathLayout,
    project_ctx: ProjectContext,
    spec: AgentSpec,
    *,
    reason: str,
    remove_agent_state: bool,
    remove_workspace: bool = True,
) -> WorkspaceRetirement:
    plan = WorkspacePlanner().plan(spec, project_ctx)
    if remove_workspace and plan.workspace_scope != 'external':
        removed = remove_clean_registered_worktree(
            project_root,
            plan.workspace_path,
            binding_authority=_workspace_binding_authority(plan),
        )
        if not removed and _path_within(plan.workspace_path, paths.workspaces_dir) and plan.workspace_path.exists():
            raise RuntimeError(f'refusing to recursively remove unregistered workspace path: {plan.workspace_path}')
        if plan.branch_name:
            delete_branch(project_root, plan.branch_name)
    if remove_agent_state:
        cleanup = _remove_agent_state(paths, spec.name)
    else:
        cleanup = AgentStateCleanupResult(removed=False)
    return WorkspaceRetirement(
        agent_name=spec.name,
        branch_name=plan.branch_name,
        workspace_path=str(plan.workspace_path),
        reason=reason,
        removed_agent_state=cleanup.removed,
        cleanup_deferred=cleanup.deferred,
        cleanup_reason=cleanup.reason,
        cleanup_deferred_persisted=cleanup.deferred_persisted,
        cleanup_marker_cleared=cleanup.marker_cleared,
    )


def _remove_agent_state(paths: PathLayout, agent_name: str) -> AgentStateCleanupResult:
    decision = _agent_state_cleanup_decision(paths, agent_name)
    if not decision.can_remove:
        deferred_persisted = _write_cleanup_deferred(paths, agent_name, decision)
        return AgentStateCleanupResult(
            removed=False,
            deferred=True,
            reason=decision.reason,
            deferred_persisted=deferred_persisted,
        )

    try:
        for target in (paths.agent_dir(agent_name), paths.agent_mailbox_dir(agent_name)):
            if target.is_symlink() or target.is_file():
                target.unlink()
                continue
            if target.is_dir():
                _rmtree_agent_state(target)
    except PermissionError as exc:
        if not _is_file_in_use_error(exc):
            raise
        decision = _AgentStateCleanupDecision(
            can_remove=False,
            reason='file_in_use',
        )
        deferred_persisted = _write_cleanup_deferred(paths, agent_name, decision)
        return AgentStateCleanupResult(
            removed=False,
            deferred=True,
            reason=decision.reason,
            deferred_persisted=deferred_persisted,
        )

    marker_cleared = _clear_cleanup_deferred(paths, agent_name)
    return AgentStateCleanupResult(
        removed=True,
        reason=None if marker_cleared else 'deferred_marker_clear_failed',
        marker_cleared=marker_cleared,
    )


def _agent_state_cleanup_decision(
    paths: PathLayout,
    agent_name: str,
) -> _AgentStateCleanupDecision:
    runtime_path = paths.agent_runtime_path(agent_name)
    if not runtime_path.exists():
        return _AgentStateCleanupDecision(can_remove=True)

    try:
        runtime = AgentRuntimeStore(paths).load(agent_name)
    except Exception:
        return _AgentStateCleanupDecision(
            can_remove=False,
            reason='runtime_unreadable',
        )
    if runtime is None:
        return _AgentStateCleanupDecision(
            can_remove=False,
            reason='runtime_unknown',
        )

    live_pids = _live_runtime_pids(runtime)
    if live_pids:
        return _AgentStateCleanupDecision(
            can_remove=False,
            reason='runtime_process_alive',
            runtime_state=runtime.state.value,
            live_pids=live_pids,
        )
    if runtime.state is not AgentState.STOPPED:
        return _AgentStateCleanupDecision(
            can_remove=False,
            reason=f'runtime_state_{runtime.state.value}',
            runtime_state=runtime.state.value,
        )
    return _AgentStateCleanupDecision(
        can_remove=True,
        runtime_state=runtime.state.value,
    )


def _live_runtime_pids(runtime: AgentRuntime) -> tuple[int, ...]:
    live: list[int] = []
    for value in (runtime.pid, runtime.runtime_pid):
        try:
            pid = int(value or 0)
        except (TypeError, ValueError):
            continue
        if pid <= 0 or pid in live:
            continue
        if is_pid_alive(pid):
            live.append(pid)
    return tuple(live)


def _write_cleanup_deferred(
    paths: PathLayout,
    agent_name: str,
    decision: _AgentStateCleanupDecision,
) -> bool:
    payload = {
        'schema_version': _CLEANUP_DEFERRED_SCHEMA_VERSION,
        'record_type': 'agent_cleanup_deferred',
        'agent_name': agent_name,
        'reason': decision.reason or 'unknown',
        'runtime_state': decision.runtime_state,
        'live_pids': list(decision.live_pids),
        'created_at': _utc_now(),
    }
    try:
        atomic_write_json(paths.agent_cleanup_deferred_path(agent_name), payload)
    except OSError:
        # Cleanup must remain non-destructive even if the marker cannot be written.
        return False
    return True


def _clear_cleanup_deferred(paths: PathLayout, agent_name: str) -> bool:
    try:
        paths.agent_cleanup_deferred_path(agent_name).unlink()
    except FileNotFoundError:
        return True
    except OSError:
        return False
    return True


def _load_deferred_cleanup(paths: PathLayout) -> dict[str, str]:
    root = paths.runtime_state_root / 'state' / 'agent-cleanup'
    if not root.is_dir():
        return {}
    deferred: dict[str, str] = {}
    for marker in sorted(root.glob('*.json')):
        try:
            payload = json.loads(marker.read_text(encoding='utf-8'))
            if not isinstance(payload, dict):
                continue
            if payload.get('schema_version') != _CLEANUP_DEFERRED_SCHEMA_VERSION:
                continue
            agent_name = normalize_agent_name(str(payload.get('agent_name') or marker.stem))
            if agent_name != marker.stem:
                continue
            if payload.get('record_type') != 'agent_cleanup_deferred':
                continue
        except (OSError, UnicodeError, ValueError, TypeError):
            continue
        deferred[agent_name] = str(payload.get('reason') or 'deferred_cleanup_retry')
    return deferred


def _rmtree_agent_state(target: Path) -> None:
    attempts = len(_RMTREE_RETRY_DELAYS_S) + 1
    for attempt in range(attempts):
        try:
            if sys.version_info >= (3, 12):
                shutil.rmtree(target, onexc=_retry_remove_readonly)
            else:
                def onerror(function, path, exc_info) -> None:
                    _retry_remove_readonly(function, path, exc_info[1])

                shutil.rmtree(target, onerror=onerror)
            return
        except PermissionError as exc:
            if not _is_file_in_use_error(exc) or attempt >= attempts - 1:
                raise
            time.sleep(_RMTREE_RETRY_DELAYS_S[attempt])


def _retry_remove_readonly(function, path, excinfo) -> None:
    if not isinstance(excinfo, PermissionError):
        raise excinfo
    if _is_file_in_use_error(excinfo):
        _retry_file_in_use(function, path, excinfo)
        return
    os.chmod(path, stat.S_IREAD | stat.S_IWRITE | stat.S_IEXEC)
    function(path)


def _retry_file_in_use(function, path, exc: PermissionError) -> None:
    last_error = exc
    for delay in _RMTREE_RETRY_DELAYS_S:
        time.sleep(delay)
        try:
            function(path)
            return
        except PermissionError as retry_error:
            if not _is_file_in_use_error(retry_error):
                raise
            last_error = retry_error
    raise last_error


def _is_file_in_use_error(exc: PermissionError) -> bool:
    if int(getattr(exc, 'winerror', 0) or 0) == 32:
        return True
    message = str(exc).lower()
    return 'being used' in message or 'used by another process' in message


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace('+00:00', 'Z')


def _workspace_binding_authority(plan) -> WorkspaceBindingAuthority:
    if not plan.branch_name:
        raise RuntimeError(f'managed git worktree is missing branch authority: {plan.workspace_path}')
    return WorkspaceBindingAuthority(
        target_project=plan.project_root,
        project_id=plan.project_id,
        workspace_path=plan.workspace_path,
        branch_name=plan.branch_name,
        agent_name=None if plan.workspace_scope == 'group' else plan.agent_name,
    )


def _load_persisted_specs(paths: PathLayout) -> dict[str, AgentSpec]:
    store = AgentSpecStore(paths)
    specs: dict[str, AgentSpec] = {}
    if not paths.agents_dir.is_dir():
        return specs
    for child in sorted(paths.agents_dir.iterdir()):
        if not child.is_dir():
            continue
        try:
            spec = store.load(child.name)
        except Exception:
            continue
        if spec is None:
            continue
        specs[spec.name] = spec
    return specs


def _collect_reset_worktree_specs(
    project_root: Path,
    paths: PathLayout,
    project_ctx: ProjectContext,
) -> tuple[AgentSpec, ...]:
    specs: list[AgentSpec] = []
    seen: set[tuple[str, str]] = set()

    def _append(spec: AgentSpec) -> None:
        if spec.workspace_mode is not WorkspaceMode.GIT_WORKTREE:
            return
        plan = WorkspacePlanner().plan(spec, project_ctx)
        if plan.workspace_scope == 'external':
            return
        identity = (str(_resolve_path(plan.workspace_path)), plan.branch_name or '')
        if identity in seen:
            return
        seen.add(identity)
        specs.append(spec)

    for spec in _load_persisted_specs(paths).values():
        _append(spec)
    for spec in _load_current_config_specs(project_root):
        _append(spec)
    return tuple(specs)


def _load_current_config_specs(project_root: Path) -> tuple[AgentSpec, ...]:
    try:
        from agents.config_loader import load_project_config

        config = load_project_config(project_root).config
    except Exception:
        return ()
    return tuple(config.agents.values())


def _project_context(project_root: Path) -> ProjectContext:
    root = _resolve_path(project_root)
    return ProjectContext(
        cwd=root,
        project_root=root,
        config_dir=root / '.ccb',
        project_id=compute_project_id(root),
        source='workspace-reconcile',
    )


def _path_within(path: Path, parent: Path) -> bool:
    try:
        _resolve_path(path).relative_to(_resolve_path(parent))
    except ValueError:
        return False
    return True


def _resolve_path(path: Path) -> Path:
    candidate = Path(path).expanduser()
    try:
        return candidate.resolve()
    except Exception:
        return candidate.absolute()


def _state_text(value: bool | None) -> str:
    if value is True:
        return 'true'
    if value is False:
        return 'false'
    return 'unknown'


__all__ = [
    'AgentStateCleanupResult',
    'WorkspaceGuardSummary',
    'WorkspaceRetirement',
    'WorktreeAlert',
    'format_workspace_blockers',
    'inspect_kill_worktrees',
    'prepare_reset_workspaces',
    'reconcile_start_workspaces',
]
