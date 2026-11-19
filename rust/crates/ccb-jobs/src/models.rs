use serde::{Deserialize, Serialize};
use serde_json::Value;

/// Delivery scope for a message envelope.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
#[derive(Default)]
pub enum DeliveryScope {
    #[default]
    Agent,
    Group,
    Broadcast,
}

/// A message envelope submitted to the daemon.
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct MessageEnvelope {
    pub project_id: String,
    pub to_agent: String,
    pub from_actor: String,
    pub body: String,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub task_id: Option<String>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub reply_to: Option<String>,
    pub message_type: String,
    pub delivery_scope: DeliveryScope,
    #[serde(default)]
    pub silence_on_success: bool,
    #[serde(default)]
    pub route_options: Value,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub body_artifact: Option<Value>,
}

/// Job status enum.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Hash, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
#[derive(Default)]
pub enum JobStatus {
    #[default]
    Accepted,
    Running,
    Completed,
    Failed,
    Incomplete,
    Cancelled,
}

/// Target kind for job routing.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
#[derive(Default)]
pub enum TargetKind {
    #[default]
    Agent,
    Group,
}

/// A job record persisted per target.
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct JobRecord {
    pub job_id: String,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub submission_id: Option<String>,
    #[serde(default)]
    pub agent_name: String,
    pub provider: String,
    pub request: MessageEnvelope,
    pub status: JobStatus,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub terminal_decision: Option<Value>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub cancel_requested_at: Option<String>,
    pub created_at: String,
    pub updated_at: String,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub workspace_path: Option<String>,
    #[serde(default)]
    pub target_kind: TargetKind,
    #[serde(default)]
    pub target_name: String,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub provider_instance: Option<String>,
    #[serde(default)]
    pub provider_options: Value,
}

/// A job event persisted per target.
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct JobEvent {
    pub event_id: String,
    pub job_id: String,
    #[serde(default)]
    pub agent_name: String,
    #[serde(default)]
    pub target_kind: TargetKind,
    #[serde(default)]
    pub target_name: String,
    #[serde(rename = "type")]
    pub event_type: String,
    #[serde(default)]
    pub payload: Value,
    pub timestamp: String,
}

/// A submission record.
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct SubmissionRecord {
    pub submission_id: String,
    pub project_id: String,
    pub from_actor: String,
    pub target_scope: String,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub task_id: Option<String>,
    #[serde(default)]
    pub job_ids: Vec<String>,
    #[serde(default)]
    pub created_at: String,
    #[serde(default)]
    pub updated_at: String,
}

/// Statuses that `list_project_view_recent_jobs` considers by default.
pub const PROJECT_VIEW_RECENT_JOB_STATUSES: &[JobStatus] = &[
    JobStatus::Completed,
    JobStatus::Cancelled,
    JobStatus::Failed,
    JobStatus::Incomplete,
];

/// Summary projection of a message envelope used by project view.
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct ProjectViewMessageSummary {
    pub project_id: String,
    pub to_agent: String,
    pub from_actor: String,
    pub body: String,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub task_id: Option<String>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub reply_to: Option<String>,
    pub message_type: String,
    pub delivery_scope: String,
    #[serde(default)]
    pub silence_on_success: bool,
    #[serde(default)]
    pub route_options: Value,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub body_artifact: Option<Value>,
}

impl From<&MessageEnvelope> for ProjectViewMessageSummary {
    fn from(envelope: &MessageEnvelope) -> Self {
        Self {
            project_id: envelope.project_id.clone(),
            to_agent: envelope.to_agent.clone(),
            from_actor: envelope.from_actor.clone(),
            body: envelope.body.clone(),
            task_id: envelope.task_id.clone(),
            reply_to: envelope.reply_to.clone(),
            message_type: envelope.message_type.clone(),
            delivery_scope: serde_json::to_value(envelope.delivery_scope)
                .ok()
                .and_then(|v| v.as_str().map(String::from))
                .unwrap_or_else(|| "agent".to_string()),
            silence_on_success: envelope.silence_on_success,
            route_options: envelope.route_options.clone(),
            body_artifact: envelope.body_artifact.clone(),
        }
    }
}

/// Summary projection of a job record used by project view.
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct ProjectViewJobSummary {
    pub job_id: String,
    pub agent_name: String,
    pub provider: String,
    pub request: ProjectViewMessageSummary,
    pub status: JobStatus,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub terminal_decision: Option<Value>,
    pub created_at: String,
    pub updated_at: String,
    #[serde(default)]
    pub target_kind: TargetKind,
    #[serde(default)]
    pub target_name: String,
    #[serde(default)]
    pub provider_options: Value,
}

impl From<&JobRecord> for ProjectViewJobSummary {
    fn from(job: &JobRecord) -> Self {
        Self {
            job_id: job.job_id.clone(),
            agent_name: job.agent_name.clone(),
            provider: job.provider.clone(),
            request: ProjectViewMessageSummary::from(&job.request),
            status: job.status,
            terminal_decision: job.terminal_decision.clone(),
            created_at: job.created_at.clone(),
            updated_at: job.updated_at.clone(),
            target_kind: job.target_kind,
            target_name: if job.target_name.is_empty() {
                job.agent_name.clone()
            } else {
                job.target_name.clone()
            },
            provider_options: job.provider_options.clone(),
        }
    }
}
