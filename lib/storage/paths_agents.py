from __future__ import annotations

from pathlib import Path

from agents.models import normalize_agent_name
from mailbox_runtime.targets import normalize_mailbox_owner_name
from project.discovery import WORKSPACE_BINDING_FILENAME


class AgentRuntimePathMixin:
    @property
    def agents_dir(self):
        return self.runtime_state_root / 'agents'

    @property
    def provider_profiles_dir(self):
        return self.ccb_dir / 'provider-profiles'

    def agent_dir(self, agent_name: str) -> Path:
        return self.agents_dir / normalize_agent_name(agent_name)

    def agent_anchor_dir(self, agent_name: str) -> Path:
        return self.ccb_dir / 'agents' / normalize_agent_name(agent_name)

    def agent_private_memory_path(self, agent_name: str) -> Path:
        return self.agent_anchor_dir(agent_name) / 'memory.md'

    def agent_spec_path(self, agent_name: str) -> Path:
        return self.agent_dir(agent_name) / 'agent.json'

    def agent_runtime_path(self, agent_name: str) -> Path:
        return self.agent_dir(agent_name) / 'runtime.json'

    def agent_helper_path(self, agent_name: str) -> Path:
        return self.agent_dir(agent_name) / 'helper.json'

    def agent_provider_path(self, agent_name: str) -> Path:
        return self.agent_dir(agent_name) / 'provider.json'

    def agent_restore_path(self, agent_name: str) -> Path:
        return self.agent_dir(agent_name) / 'restore.json'

    def agent_jobs_path(self, agent_name: str) -> Path:
        return self.agent_dir(agent_name) / 'jobs.jsonl'

    def job_store_path(self, agent_name: str) -> Path:
        return self.agent_jobs_path(agent_name)

    def agent_events_path(self, agent_name: str) -> Path:
        return self.agent_dir(agent_name) / 'events.jsonl'

    def agent_provider_runtime_dir(self, agent_name: str, provider: str) -> Path:
        normalized_provider = str(provider or '').strip().lower()
        if not normalized_provider:
            raise ValueError('provider cannot be empty')
        return self.agent_dir(agent_name) / 'provider-runtime' / normalized_provider

    def agent_provider_state_dir(self, agent_name: str, provider: str) -> Path:
        normalized_provider = str(provider or '').strip().lower()
        if not normalized_provider:
            raise ValueError('provider cannot be empty')
        return self.agent_dir(agent_name) / 'provider-state' / normalized_provider

    def agent_logs_dir(self, agent_name: str) -> Path:
        return self.agent_dir(agent_name) / 'logs'


class AgentMailboxPathMixin:
    def agent_mailbox_dir(self, agent_name: str) -> Path:
        return self.ccbd_mailboxes_dir / normalize_mailbox_owner_name(agent_name)

    def agent_mailbox_path(self, agent_name: str) -> Path:
        return self.agent_mailbox_dir(agent_name) / 'mailbox.json'

    def agent_inbox_path(self, agent_name: str) -> Path:
        return self.agent_mailbox_dir(agent_name) / 'inbox.jsonl'

    def agent_outbox_path(self, agent_name: str) -> Path:
        return self.agent_mailbox_dir(agent_name) / 'outbox.jsonl'

    def mailbox_lease_path(self, agent_name: str) -> Path:
        return self.ccbd_leases_dir / f'{normalize_mailbox_owner_name(agent_name)}.json'


class WorkspacePathMixin:
    @property
    def workspaces_dir(self):
        return self.ccb_dir / 'workspaces'

    def workspace_path(self, agent_name: str, workspace_root: str | None = None) -> Path:
        normalized = normalize_agent_name(agent_name)
        if workspace_root:
            base = Path(workspace_root).expanduser()
            return base / self.project_slug / normalized
        return self.workspaces_dir / normalized

    def workspace_group_path(self, group_name: str) -> Path:
        normalized = normalize_agent_name(group_name)
        return self.workspaces_dir / 'groups' / normalized

    def workspace_binding_path(
        self,
        agent_name: str,
        workspace_root: str | None = None,
    ) -> Path:
        return self.workspace_path(
            agent_name,
            workspace_root=workspace_root,
        ) / WORKSPACE_BINDING_FILENAME

    def workspace_group_binding_path(self, group_name: str) -> Path:
        return self.workspace_group_path(group_name) / WORKSPACE_BINDING_FILENAME


__all__ = [
    'AgentMailboxPathMixin',
    'AgentRuntimePathMixin',
    'WorkspacePathMixin',
]
