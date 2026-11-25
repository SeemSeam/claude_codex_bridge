use camino::Utf8PathBuf;

use crate::path_helpers::normalized_segment;
use crate::paths::PathLayout;

impl PathLayout {
    // --- Target paths ---

    pub fn target_dir(&self, target_kind: &str, target_name: &str) -> crate::Result<Utf8PathBuf> {
        let segment = crate::path_helpers::target_segment(target_kind, target_name)?;
        if target_kind.trim().to_lowercase() == "agent" {
            Ok(self.agent_dir(&segment))
        } else {
            Ok(self.ccbd_dir().join("targets").join(segment))
        }
    }

    pub fn target_jobs_path(
        &self,
        target_kind: &str,
        target_name: &str,
    ) -> crate::Result<Utf8PathBuf> {
        Ok(self
            .target_dir(target_kind, target_name)?
            .join("jobs.jsonl"))
    }

    pub fn target_events_path(
        &self,
        target_kind: &str,
        target_name: &str,
    ) -> crate::Result<Utf8PathBuf> {
        Ok(self
            .target_dir(target_kind, target_name)?
            .join("events.jsonl"))
    }

    pub fn snapshot_path(&self, job_id: &str) -> Utf8PathBuf {
        self.ccbd_snapshots_dir().join(format!("{}.json", job_id))
    }

    pub fn cursor_path(&self, job_id: &str) -> Utf8PathBuf {
        self.ccbd_cursors_dir().join(format!("{}.json", job_id))
    }

    pub fn execution_state_path(&self, job_id: &str) -> Utf8PathBuf {
        self.ccbd_executions_dir().join(format!("{}.json", job_id))
    }

    pub fn heartbeat_subject_dir(&self, subject_kind: &str) -> crate::Result<Utf8PathBuf> {
        Ok(self
            .ccbd_heartbeats_dir()
            .join(normalized_segment(subject_kind, "subject_kind")?))
    }

    pub fn heartbeat_subject_path(
        &self,
        subject_kind: &str,
        subject_id: &str,
    ) -> crate::Result<Utf8PathBuf> {
        let normalized_id = normalized_segment(subject_id, "subject_id")?;
        Ok(self
            .heartbeat_subject_dir(subject_kind)?
            .join(format!("{}.json", normalized_id)))
    }

    pub fn provider_health_path(&self, job_id: &str) -> Utf8PathBuf {
        self.ccbd_provider_health_dir()
            .join(format!("{}.jsonl", job_id.trim()))
    }

    pub fn support_bundle_path(&self, bundle_id: &str) -> crate::Result<Utf8PathBuf> {
        let normalized = normalized_segment(bundle_id, "bundle_id")?;
        Ok(self
            .ccbd_support_dir()
            .join(format!("{}.tar.gz", normalized)))
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_target_paths() {
        let layout = PathLayout::new("/project");
        assert_eq!(
            layout.target_dir("agent", "Agent1").unwrap(),
            Utf8PathBuf::from("/project/.ccb/agents/agent1")
        );
        assert_eq!(
            layout.target_dir("service", "svc-1").unwrap(),
            Utf8PathBuf::from("/project/.ccb/ccbd/targets/svc-1")
        );
        assert_eq!(
            layout.snapshot_path("job-1"),
            Utf8PathBuf::from("/project/.ccb/ccbd/snapshots/job-1.json")
        );
        assert_eq!(
            layout.cursor_path("job-1"),
            Utf8PathBuf::from("/project/.ccb/ccbd/cursors/job-1.json")
        );
        assert_eq!(
            layout.provider_health_path("job-1"),
            Utf8PathBuf::from("/project/.ccb/ccbd/provider-health/job-1.jsonl")
        );
        assert_eq!(
            layout.support_bundle_path("bundle-1").unwrap(),
            Utf8PathBuf::from("/project/.ccb/ccbd/support/bundle-1.tar.gz")
        );
    }

    #[test]
    fn test_heartbeat_subject_path() {
        let layout = PathLayout::new("/project");
        assert_eq!(
            layout.heartbeat_subject_path("provider", "sub-1").unwrap(),
            Utf8PathBuf::from("/project/.ccb/ccbd/heartbeats/provider/sub-1.json")
        );
    }
}
