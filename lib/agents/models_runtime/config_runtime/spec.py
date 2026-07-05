from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from provider_model_shortcuts import provider_model_startup_args, startup_args_contain_model_flag
from provider_profiles.models import ProviderProfileSpec
from role_aliases import canonical_role_id

from .api import AgentApiSpec
from ..enums import PermissionMode, QueuePolicy, RestoreMode, RuntimeMode, WorkspaceMode, normalize_runtime_mode
from ..names import SCHEMA_VERSION, AgentValidationError, normalize_agent_name


_PROVIDER_COMMAND_PLACEHOLDER = '{command}'


def normalize_agent_api(api) -> AgentApiSpec:
    if isinstance(api, AgentApiSpec):
        return api
    try:
        return AgentApiSpec(**dict(api or {}))
    except Exception as exc:  # pragma: no cover - defensive normalization
        raise AgentValidationError(f'invalid api: {exc}') from exc


def normalize_provider_profile(profile) -> ProviderProfileSpec:
    if isinstance(profile, ProviderProfileSpec):
        return profile
    try:
        return ProviderProfileSpec(**dict(profile or {}))
    except Exception as exc:  # pragma: no cover - defensive normalization
        raise AgentValidationError(f'invalid provider_profile: {exc}') from exc


@dataclass(frozen=True)
class AgentSpec:
    name: str
    provider: str
    target: str
    workspace_mode: WorkspaceMode
    workspace_root: str | None
    runtime_mode: RuntimeMode
    restore_default: RestoreMode
    permission_default: PermissionMode
    queue_policy: QueuePolicy
    workspace_path: str | None = None
    workspace_group: str | None = None
    provider_command_template: str | None = None
    model: str | None = None
    startup_args: tuple[str, ...] = field(default_factory=tuple)
    env: dict[str, str] = field(default_factory=dict)
    api: AgentApiSpec = field(default_factory=AgentApiSpec)
    provider_profile: ProviderProfileSpec = field(default_factory=ProviderProfileSpec)
    branch_template: str | None = None
    labels: tuple[str, ...] = field(default_factory=tuple)
    description: str | None = None
    role: str | None = None
    watch_paths: tuple[str, ...] = field(default_factory=tuple)
    dispatch_disabled: bool = False
    account: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, 'name', normalize_agent_name(self.name))
        provider = str(self.provider or '').strip().lower()
        if not provider:
            raise AgentValidationError('provider cannot be empty')
        target = str(self.target or '').strip()
        if not target:
            raise AgentValidationError('target cannot be empty')
        object.__setattr__(self, 'provider', provider)
        object.__setattr__(self, 'target', target)
        workspace_mode = WorkspaceMode(self.workspace_mode)
        object.__setattr__(self, 'workspace_mode', workspace_mode)
        workspace_root = self._normalize_optional_string(self.workspace_root, field_name='workspace_root')
        workspace_path = self._normalize_optional_string(self.workspace_path, field_name='workspace_path')
        workspace_group = self._normalize_workspace_group()
        provider_command_template = self._normalize_provider_command_template()
        self._validate_workspace_overrides(
            workspace_mode=workspace_mode,
            workspace_root=workspace_root,
            workspace_path=workspace_path,
            workspace_group=workspace_group,
            branch_template=self.branch_template,
        )
        object.__setattr__(self, 'workspace_root', workspace_root)
        object.__setattr__(self, 'workspace_path', workspace_path)
        object.__setattr__(self, 'workspace_group', workspace_group)
        object.__setattr__(self, 'provider_command_template', provider_command_template)
        object.__setattr__(self, 'runtime_mode', normalize_runtime_mode(self.runtime_mode))
        model = self._normalize_model()
        startup_args = tuple(str(item) for item in self.startup_args)
        object.__setattr__(self, 'model', model)
        object.__setattr__(self, 'startup_args', self._normalize_startup_args(provider=provider, model=model, startup_args=startup_args))
        object.__setattr__(self, 'labels', tuple(str(item) for item in self.labels))
        object.__setattr__(self, 'role', self._normalize_role())
        object.__setattr__(self, 'watch_paths', tuple(str(item) for item in self.watch_paths))
        object.__setattr__(self, 'dispatch_disabled', bool(self.dispatch_disabled))
        object.__setattr__(self, 'account', self._normalize_optional_string(self.account, field_name='account'))
        object.__setattr__(self, 'env', {str(key): str(value) for key, value in dict(self.env).items()})
        object.__setattr__(self, 'api', normalize_agent_api(self.api))
        object.__setattr__(self, 'provider_profile', normalize_provider_profile(self.provider_profile))

    def _normalize_model(self) -> str | None:
        if self.model is None:
            return None
        normalized = str(self.model).strip()
        if not normalized:
            raise AgentValidationError('model cannot be empty')
        return canonical_role_id(normalized)

    def _normalize_role(self) -> str | None:
        if self.role is None:
            return None
        normalized = str(self.role).strip().lower()
        if not normalized:
            raise AgentValidationError('role cannot be empty')
        allowed = set('abcdefghijklmnopqrstuvwxyz0123456789._-')
        if any(ch not in allowed for ch in normalized) or '.' not in normalized:
            raise AgentValidationError('role must use publisher.role form, for example ccb.archi')
        return canonical_role_id(normalized)

    def _normalize_optional_string(self, value: str | None, *, field_name: str) -> str | None:
        if value is None:
            return None
        normalized = str(value).strip()
        if not normalized:
            raise AgentValidationError(f'{field_name} cannot be empty')
        return normalized

    def _normalize_workspace_group(self) -> str | None:
        if self.workspace_group is None:
            return None
        try:
            return normalize_agent_name(str(self.workspace_group))
        except AgentValidationError as exc:
            raise AgentValidationError(f'workspace_group is invalid: {exc}') from exc

    def _validate_workspace_overrides(
        self,
        *,
        workspace_mode: WorkspaceMode,
        workspace_root: str | None,
        workspace_path: str | None,
        workspace_group: str | None,
        branch_template: str | None,
    ) -> None:
        if workspace_path is not None and workspace_group is not None:
            raise AgentValidationError('workspace_path and workspace_group are mutually exclusive')
        if workspace_path is not None and workspace_root is not None:
            raise AgentValidationError('workspace_path cannot be combined with workspace_root')
        if workspace_group is not None and workspace_root is not None:
            raise AgentValidationError('workspace_group cannot be combined with workspace_root')
        if (workspace_path is not None or workspace_group is not None) and workspace_mode is not WorkspaceMode.GIT_WORKTREE:
            raise AgentValidationError('workspace_path and workspace_group require workspace_mode="git-worktree"')
        if (workspace_path is not None or workspace_group is not None) and branch_template is not None:
            raise AgentValidationError('workspace_path and workspace_group cannot be combined with branch_template')

    def _normalize_provider_command_template(self) -> str | None:
        value = self._normalize_optional_string(
            self.provider_command_template,
            field_name='provider_command_template',
        )
        if value is None:
            return None
        if value.count(_PROVIDER_COMMAND_PLACEHOLDER) != 1:
            raise AgentValidationError(
                f'provider_command_template must contain exactly one {_PROVIDER_COMMAND_PLACEHOLDER}'
            )
        return value

    def _normalize_startup_args(
        self,
        *,
        provider: str,
        model: str | None,
        startup_args: tuple[str, ...],
    ) -> tuple[str, ...]:
        if model is None:
            return startup_args
        try:
            compiled_model_args = provider_model_startup_args(provider, model=model)
        except ValueError as exc:
            raise AgentValidationError(str(exc)) from exc
        if startup_args[: len(compiled_model_args)] == compiled_model_args:
            remaining_args = startup_args[len(compiled_model_args) :]
            if startup_args_contain_model_flag(provider, remaining_args):
                raise AgentValidationError(
                    f'model cannot be combined with startup_args model flags for provider {provider}'
                )
            return startup_args
        if startup_args_contain_model_flag(provider, startup_args):
            raise AgentValidationError(
                f'model cannot be combined with startup_args model flags for provider {provider}'
            )
        return compiled_model_args + startup_args

    def to_record(self) -> dict[str, Any]:
        return {
            'schema_version': SCHEMA_VERSION,
            'record_type': 'agent_spec',
            'name': self.name,
            'provider': self.provider,
            'target': self.target,
            'workspace_mode': self.workspace_mode.value,
            'workspace_root': self.workspace_root,
            'workspace_path': self.workspace_path,
            'workspace_group': self.workspace_group,
            'provider_command_template': self.provider_command_template,
            'runtime_mode': self.runtime_mode.value,
            'restore_default': self.restore_default.value,
            'permission_default': self.permission_default.value,
            'queue_policy': self.queue_policy.value,
            'model': self.model,
            'startup_args': list(self.startup_args),
            'env': dict(self.env),
            'api': self.api.to_record(),
            'provider_profile': self.provider_profile.to_record(),
            'branch_template': self.branch_template,
            'labels': list(self.labels),
            'description': self.description,
            'role': self.role,
            'watch_paths': list(self.watch_paths),
            'dispatch_disabled': bool(self.dispatch_disabled),
            'account': self.account,
        }


__all__ = ['AgentSpec', 'normalize_agent_api', 'normalize_provider_profile']
