use camino::Utf8PathBuf;

use crate::path_helpers::{normalize_agent_name, normalize_mailbox_owner_name};
use crate::paths::PathLayout;

impl PathLayout {
    // --- Agent paths ---

    pub fn agents_dir(&self) -> Utf8PathBuf {
        self.runtime_state_root.join("agents")
    }

    pub fn provider_profiles_dir(&self) -> Utf8PathBuf {
        self.ccb_dir().join("provider-profiles")
    }

    pub fn agent_dir(&self, agent_name: &str) -> Utf8PathBuf {
        self.agents_dir()
            .join(normalize_agent_name(agent_name).unwrap_or_else(|_| agent_name.to_lowercase()))
    }

    pub fn agent_anchor_dir(&self, agent_name: &str) -> Utf8PathBuf {
        self.ccb_dir()
            .join("agents")
            .join(normalize_agent_name(agent_name).unwrap_or_else(|_| agent_name.to_lowercase()))
    }

    pub fn agent_private_memory_path(&self, agent_name: &str) -> Utf8PathBuf {
        self.agent_anchor_dir(agent_name).join("memory.md")
    }

    pub fn agent_spec_path(&self, agent_name: &str) -> Utf8PathBuf {
        self.agent_dir(agent_name).join("agent.json")
    }

    pub fn agent_runtime_path(&self, agent_name: &str) -> Utf8PathBuf {
        self.agent_dir(agent_name).join("runtime.json")
    }

    pub fn agent_helper_path(&self, agent_name: &str) -> Utf8PathBuf {
        self.agent_dir(agent_name).join("helper.json")
    }

    pub fn agent_provider_path(&self, agent_name: &str) -> Utf8PathBuf {
        self.agent_dir(agent_name).join("provider.json")
    }

    pub fn agent_restore_path(&self, agent_name: &str) -> Utf8PathBuf {
        self.agent_dir(agent_name).join("restore.json")
    }

    pub fn agent_jobs_path(&self, agent_name: &str) -> Utf8PathBuf {
        self.agent_dir(agent_name).join("jobs.jsonl")
    }

    pub fn job_store_path(&self, agent_name: &str) -> Utf8PathBuf {
        self.agent_jobs_path(agent_name)
    }

    pub fn agent_events_path(&self, agent_name: &str) -> Utf8PathBuf {
        self.agent_dir(agent_name).join("events.jsonl")
    }

    pub fn agent_provider_runtime_dir(&self, agent_name: &str, provider: &str) -> Utf8PathBuf {
        let normalized = provider.trim().to_lowercase();
        self.agent_dir(agent_name)
            .join("provider-runtime")
            .join(normalized)
    }

    pub fn agent_provider_state_dir(&self, agent_name: &str, provider: &str) -> Utf8PathBuf {
        let normalized = provider.trim().to_lowercase();
        self.agent_dir(agent_name)
            .join("provider-state")
            .join(normalized)
    }

    pub fn agent_logs_dir(&self, agent_name: &str) -> Utf8PathBuf {
        self.agent_dir(agent_name).join("logs")
    }

    // --- Agent mailbox paths ---

    pub fn agent_mailbox_dir(&self, agent_name: &str) -> Utf8PathBuf {
        self.ccbd_mailboxes_dir().join(
            normalize_mailbox_owner_name(agent_name).unwrap_or_else(|_| agent_name.to_lowercase()),
        )
    }

    pub fn agent_mailbox_path(&self, agent_name: &str) -> Utf8PathBuf {
        self.agent_mailbox_dir(agent_name).join("mailbox.json")
    }

    pub fn agent_inbox_path(&self, agent_name: &str) -> Utf8PathBuf {
        self.agent_mailbox_dir(agent_name).join("inbox.jsonl")
    }

    pub fn agent_outbox_path(&self, agent_name: &str) -> Utf8PathBuf {
        self.agent_mailbox_dir(agent_name).join("outbox.jsonl")
    }

    pub fn mailbox_lease_path(&self, agent_name: &str) -> Utf8PathBuf {
        self.ccbd_leases_dir().join(format!(
            "{}.json",
            normalize_mailbox_owner_name(agent_name).unwrap_or_else(|_| agent_name.to_lowercase())
        ))
    }

    // --- Workspace paths ---

    pub fn workspaces_dir(&self) -> Utf8PathBuf {
        self.ccb_dir().join("workspaces")
    }

    pub fn workspace_path(&self, agent_name: &str, workspace_root: Option<&str>) -> Utf8PathBuf {
        let normalized =
            normalize_agent_name(agent_name).unwrap_or_else(|_| agent_name.to_lowercase());
        if let Some(root) = workspace_root {
            Utf8PathBuf::from(crate::paths::expand_user_path(root))
                .join(self.project_slug())
                .join(normalized)
        } else {
            self.workspaces_dir().join(normalized)
        }
    }

    pub fn workspace_group_path(&self, group_name: &str) -> Utf8PathBuf {
        self.workspaces_dir()
            .join("groups")
            .join(normalize_agent_name(group_name).unwrap_or_else(|_| group_name.to_lowercase()))
    }

    pub fn workspace_binding_path(
        &self,
        agent_name: &str,
        workspace_root: Option<&str>,
    ) -> Utf8PathBuf {
        self.workspace_path(agent_name, workspace_root)
            .join(".ccb-workspace.json")
    }

    pub fn workspace_group_binding_path(&self, group_name: &str) -> Utf8PathBuf {
        self.workspace_group_path(group_name)
            .join(".ccb-workspace.json")
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_agent_mailbox_path() {
        let layout = PathLayout::new("/project");
        assert_eq!(
            layout.agent_mailbox_path("Agent1"),
            Utf8PathBuf::from("/project/.ccb/ccbd/mailboxes/agent1/mailbox.json")
        );
    }
}
