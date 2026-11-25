use ccb_storage::{jsonl::JsonlStore, paths::PathLayout};
use serde::Serialize;
use std::collections::{HashMap, HashSet};
use std::io::{BufRead, BufReader};

use crate::models::{
    JobEvent, JobRecord, JobStatus, ProjectViewJobSummary, SubmissionRecord, TargetKind,
};
use crate::Result;

const JOB_EVENT_RECORD_TYPE: &str = "job_event";

const SCHEMA_VERSION: i32 = 2;

#[derive(Serialize)]
struct Record<'a, T: Serialize> {
    schema_version: i32,
    record_type: &'a str,
    #[serde(flatten)]
    payload: &'a T,
}

impl<'a, T: Serialize> Record<'a, T> {
    fn new(record_type: &'a str, payload: &'a T) -> Self {
        Self {
            schema_version: SCHEMA_VERSION,
            record_type,
            payload,
        }
    }
}

/// Store for job records, persisted per target.
#[derive(Clone)]
pub struct JobStore {
    layout: PathLayout,
    jsonl: JsonlStore,
}

impl JobStore {
    pub fn new(layout: &PathLayout) -> Self {
        Self {
            layout: layout.clone(),
            jsonl: JsonlStore::new(),
        }
    }

    pub fn append(&self, record: &JobRecord) -> Result<()> {
        let path = self.layout.target_jobs_path(
            &format!("{:?}", record.target_kind).to_lowercase(),
            &record.target_name,
        )?;
        self.jsonl
            .append(&path, &Record::new("job_record", record))
            .map_err(Into::into)
    }

    pub fn list_agent(&self, agent_name: &str) -> Vec<JobRecord> {
        self.list_target(TargetKind::Agent, agent_name)
    }

    pub fn list_target(&self, target_kind: TargetKind, target_name: &str) -> Vec<JobRecord> {
        let Ok(path) = self
            .layout
            .target_jobs_path(&format!("{:?}", target_kind).to_lowercase(), target_name)
        else {
            return Vec::new();
        };
        self.jsonl.read_all(&path).unwrap_or_default()
    }

    pub fn list_agent_tail(&self, agent_name: &str, limit: usize) -> Vec<JobRecord> {
        self.list_target_tail(TargetKind::Agent, agent_name, limit)
    }

    pub fn list_target_tail(
        &self,
        target_kind: TargetKind,
        target_name: &str,
        limit: usize,
    ) -> Vec<JobRecord> {
        let Ok(path) = self
            .layout
            .target_jobs_path(&format!("{:?}", target_kind).to_lowercase(), target_name)
        else {
            return Vec::new();
        };
        self.jsonl.read_tail(&path, limit).unwrap_or_default()
    }

    pub fn get_latest(&self, agent_name: &str, job_id: &str) -> Option<JobRecord> {
        self.get_latest_target(TargetKind::Agent, agent_name, job_id)
    }

    pub fn get_latest_target(
        &self,
        target_kind: TargetKind,
        target_name: &str,
        job_id: &str,
    ) -> Option<JobRecord> {
        let Ok(path) = self
            .layout
            .target_jobs_path(&format!("{:?}", target_kind).to_lowercase(), target_name)
        else {
            return None;
        };
        self.jsonl
            .find_last(&path, |payload: &JobRecord| payload.job_id == job_id)
            .unwrap_or_default()
    }

    /// Returns the last `limit` job records for each requested agent.
    pub fn list_agent_tails_batch(
        &self,
        agent_names: &[String],
        limit: usize,
    ) -> HashMap<String, Vec<JobRecord>> {
        agent_names
            .iter()
            .map(|name| (name.clone(), self.list_agent_tail(name, limit)))
            .collect()
    }

    /// Returns the last `limit` job summaries for each requested agent.
    pub fn list_agent_tail_summaries_batch(
        &self,
        agent_names: &[String],
        limit: usize,
    ) -> HashMap<String, Vec<ProjectViewJobSummary>> {
        self.list_agent_tails_batch(agent_names, limit)
            .into_iter()
            .map(|(name, records)| {
                (
                    name,
                    records
                        .iter()
                        .map(ProjectViewJobSummary::from)
                        .collect::<Vec<_>>(),
                )
            })
            .collect()
    }

    /// Returns up to `result_limit` recent project-view job summaries across the
    /// requested agents, using an adaptive scan bounded by `per_agent_limit`.
    ///
    /// Mirrors Python `JobStore.list_project_view_recent_jobs` from v8.0.4.
    pub fn list_project_view_recent_jobs(
        &self,
        agent_names: &[String],
        per_agent_limit: usize,
        result_limit: usize,
        statuses: Option<&[JobStatus]>,
        per_agent_initial_limit: Option<usize>,
    ) -> Vec<ProjectViewJobSummary> {
        let status_set: HashSet<JobStatus> = statuses
            .map(|s| s.iter().copied().collect())
            .unwrap_or_else(|| {
                crate::models::PROJECT_VIEW_RECENT_JOB_STATUSES
                    .iter()
                    .copied()
                    .collect()
            });
        let initial_limit = per_agent_initial_limit
            .unwrap_or(per_agent_limit)
            .clamp(1, per_agent_limit.max(1));

        let mut collected: HashMap<String, HashSet<String>> = HashMap::new();
        let mut summaries: Vec<ProjectViewJobSummary> = Vec::new();

        let mut current_limit = initial_limit;
        loop {
            let mut added_this_round = 0usize;
            for agent_name in agent_names {
                let seen = collected.entry(agent_name.clone()).or_default();
                let tail = self.list_agent_tail(agent_name, current_limit);
                for record in tail {
                    if !status_set.contains(&record.status) {
                        continue;
                    }
                    if seen.insert(record.job_id.clone()) {
                        summaries.push(ProjectViewJobSummary::from(&record));
                        added_this_round += 1;
                    }
                }
            }

            // If we have enough results, or we already scanned the maximum,
            // stop expanding.
            if summaries.len() >= result_limit || current_limit >= per_agent_limit {
                break;
            }
            // If this round added nothing, further expansion won't help.
            if added_this_round == 0 {
                break;
            }
            current_limit = (current_limit * 2).clamp(1, per_agent_limit);
        }

        summaries.sort_by(|a, b| b.updated_at.cmp(&a.updated_at));
        summaries.truncate(result_limit);
        summaries
    }
}

/// Store for job events.
#[derive(Clone)]
pub struct JobEventStore {
    layout: PathLayout,
    jsonl: JsonlStore,
}

impl JobEventStore {
    pub fn new(layout: &PathLayout) -> Self {
        Self {
            layout: layout.clone(),
            jsonl: JsonlStore::new(),
        }
    }

    pub fn append(&self, event: &JobEvent) -> Result<()> {
        let path = self.layout.target_events_path(
            &format!("{:?}", event.target_kind).to_lowercase(),
            &event.target_name,
        )?;
        self.jsonl
            .append(&path, &Record::new("job_event", event))
            .map_err(Into::into)
    }

    pub fn read_since(&self, agent_name: &str, start_line: usize) -> (usize, Vec<JobEvent>) {
        self.read_since_target(TargetKind::Agent, agent_name, start_line)
    }

    pub fn read_since_target(
        &self,
        target_kind: TargetKind,
        target_name: &str,
        start_line: usize,
    ) -> (usize, Vec<JobEvent>) {
        let Ok(path) = self
            .layout
            .target_events_path(&format!("{:?}", target_kind).to_lowercase(), target_name)
        else {
            return (0, Vec::new());
        };
        let Ok(file) = std::fs::File::open(&path) else {
            return (0, Vec::new());
        };
        let reader = BufReader::new(file);
        let mut events = Vec::new();
        let mut current = 0usize;
        for line in reader.lines() {
            let Ok(line) = line else {
                continue;
            };
            let trimmed = line.trim();
            if trimmed.is_empty() {
                continue;
            }
            current += 1;
            if current <= start_line {
                continue;
            }
            let Ok(value) = serde_json::from_str::<serde_json::Value>(trimmed) else {
                continue;
            };
            let record_type = value
                .get("record_type")
                .and_then(|v| v.as_str())
                .unwrap_or("");
            if record_type != JOB_EVENT_RECORD_TYPE {
                continue;
            }
            if let Ok(event) = serde_json::from_value::<JobEvent>(value) {
                events.push(event);
            }
        }
        (current, events)
    }
}

/// Store for submission records.
#[derive(Clone)]
pub struct SubmissionStore {
    layout: PathLayout,
    jsonl: JsonlStore,
}

impl SubmissionStore {
    pub fn new(layout: &PathLayout) -> Self {
        Self {
            layout: layout.clone(),
            jsonl: JsonlStore::new(),
        }
    }

    pub fn append(&self, record: &SubmissionRecord) -> Result<()> {
        let path = self.layout.ccbd_submissions_path();
        self.jsonl
            .append(&path, &Record::new("submission_record", record))
            .map_err(Into::into)
    }

    pub fn list_all(&self) -> Vec<SubmissionRecord> {
        let path = self.layout.ccbd_submissions_path();
        self.jsonl.read_all(&path).unwrap_or_default()
    }

    pub fn get_latest(&self, submission_id: &str) -> Option<SubmissionRecord> {
        let path = self.layout.ccbd_submissions_path();
        self.jsonl
            .find_last(&path, |payload: &SubmissionRecord| {
                payload.submission_id == submission_id
            })
            .unwrap_or_default()
    }
}
