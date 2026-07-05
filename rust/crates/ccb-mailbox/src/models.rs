use serde::{Deserialize, Serialize};
use serde_json::Value;

// Job-store models now live in `ccb-jobs`; re-export them so existing
// `ccb-mailbox` callers keep compiling.
pub use ccb_jobs::models::{
    DeliveryScope, JobEvent, JobRecord, JobStatus, MessageEnvelope, SubmissionRecord, TargetKind,
};

pub const SCHEMA_VERSION: u32 = 1;

// --- Mailbox Kernel Enums ---

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
#[derive(Default)]
pub enum MailboxState {
    #[default]
    Idle,
    Delivering,
    Blocked,
    Recovering,
    Degraded,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum InboundEventType {
    TaskRequest,
    TaskReply,
    CompletionNotice,
    RetrySignal,
    SystemSignal,
    BarrierRelease,
}

impl std::fmt::Display for InboundEventType {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        let serialized = serde_json::to_string(self).map_err(|_| std::fmt::Error)?;
        f.write_str(serialized.trim_matches('"'))
    }
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
#[derive(Default)]
pub enum InboundEventStatus {
    #[default]
    Created,
    Queued,
    Delivering,
    Consumed,
    Superseded,
    Abandoned,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
#[derive(Default)]
pub enum LeaseState {
    #[default]
    Acquired,
    Released,
    Expired,
    Orphaned,
}

// --- Message Bureau Enums ---

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
#[derive(Default)]
pub enum MessageState {
    #[default]
    Created,
    Queued,
    Dispatching,
    Running,
    PartiallyReplied,
    Completed,
    Incomplete,
    Failed,
    Cancelled,
    DeadLetter,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Hash, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
#[derive(Default)]
pub enum AttemptState {
    #[default]
    Pending,
    Delivering,
    Running,
    WaitingCompletion,
    ReplyReady,
    Stalled,
    RuntimeDead,
    Failed,
    Incomplete,
    Cancelled,
    Superseded,
    DeadLetter,
    Completed,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Hash, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
#[derive(Default)]
pub enum ReplyTerminalStatus {
    #[default]
    Completed,
    Incomplete,
    Failed,
    Cancelled,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
#[derive(Default)]
pub enum CallbackEdgeState {
    #[default]
    Pending,
    ChildCompleted,
    ContinuationSubmitted,
    Done,
    Failed,
    TimedOut,
}

// --- Mailbox Kernel Models ---

/// Mailbox state for an agent.
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct MailboxRecord {
    pub mailbox_id: String,
    pub agent_name: String,
    pub summary_version: u32,
    pub summary_source: String,
    pub summary_refreshed_at: String,
    pub active_inbound_event_id: Option<String>,
    pub queue_depth: u32,
    pub pending_reply_count: u32,
    pub head_inbound_event_id: Option<String>,
    pub head_event_type: Option<String>,
    pub head_status: Option<String>,
    pub head_message_id: Option<String>,
    pub head_attempt_id: Option<String>,
    pub head_payload_ref: Option<String>,
    pub last_inbound_started_at: Option<String>,
    pub last_inbound_finished_at: Option<String>,
    pub mailbox_state: MailboxState,
    pub lease_version: u32,
    pub updated_at: String,
}

impl MailboxRecord {
    pub fn new(agent_name: impl Into<String>) -> Self {
        let agent_name = agent_name.into();
        Self {
            mailbox_id: format!("mbx_{}", agent_name),
            agent_name,
            summary_version: 1,
            summary_source: "initial".into(),
            summary_refreshed_at: String::new(),
            active_inbound_event_id: None,
            queue_depth: 0,
            pending_reply_count: 0,
            head_inbound_event_id: None,
            head_event_type: None,
            head_status: None,
            head_message_id: None,
            head_attempt_id: None,
            head_payload_ref: None,
            last_inbound_started_at: None,
            last_inbound_finished_at: None,
            mailbox_state: MailboxState::Idle,
            lease_version: 0,
            updated_at: String::new(),
        }
    }
}

/// An inbound event in a mailbox.
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct InboundEventRecord {
    pub inbound_event_id: String,
    pub agent_name: String,
    pub event_type: InboundEventType,
    pub message_id: String,
    pub attempt_id: Option<String>,
    pub payload_ref: Option<String>,
    pub priority: u32,
    pub status: InboundEventStatus,
    pub created_at: String,
    pub started_at: Option<String>,
    pub finished_at: Option<String>,
}

/// A delivery lease for an inbound event.
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct DeliveryLease {
    pub agent_name: String,
    pub inbound_event_id: String,
    pub lease_version: u32,
    pub acquired_at: String,
    pub last_progress_at: Option<String>,
    pub expires_at: Option<String>,
    pub lease_state: LeaseState,
}

// --- Message Bureau Models ---

/// A message in the bureau.
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct MessageRecord {
    pub message_id: String,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub origin_message_id: Option<String>,
    pub from_actor: String,
    pub target_scope: String,
    #[serde(default)]
    pub target_agents: Vec<String>,
    #[serde(default = "default_message_class")]
    pub message_class: String,
    #[serde(default)]
    pub reply_policy: Value,
    #[serde(default)]
    pub retry_policy: Value,
    #[serde(default = "default_priority")]
    pub priority: u32,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub payload_ref: Option<String>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub submission_id: Option<String>,
    #[serde(default)]
    pub created_at: String,
    #[serde(default)]
    pub updated_at: String,
    #[serde(default)]
    pub message_state: MessageState,
}

fn default_message_class() -> String {
    "task_request".to_string()
}

fn default_priority() -> u32 {
    100
}

/// An attempt to deliver a message.
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct AttemptRecord {
    pub attempt_id: String,
    pub message_id: String,
    pub agent_name: String,
    pub provider: String,
    pub job_id: String,
    pub retry_index: u32,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub health_snapshot_ref: Option<String>,
    pub started_at: String,
    pub updated_at: String,
    pub attempt_state: AttemptState,
}

/// A reply to a message.
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct ReplyRecord {
    pub reply_id: String,
    pub message_id: String,
    pub attempt_id: String,
    pub agent_name: String,
    pub terminal_status: ReplyTerminalStatus,
    pub reply: String,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub reply_artifact: Option<Value>,
    #[serde(default)]
    pub diagnostics: Value,
    #[serde(default)]
    pub finished_at: String,
}

/// A callback edge linking parent and child jobs.
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct CallbackEdgeRecord {
    pub edge_id: String,
    pub parent_job_id: String,
    pub parent_message_id: String,
    pub parent_agent: String,
    pub child_job_id: String,
    pub child_message_id: String,
    pub callback_target_agent: String,
    pub original_caller: String,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub original_task_id: Option<String>,
    #[serde(default)]
    pub state: CallbackEdgeState,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub child_reply_id: Option<String>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub child_status: Option<String>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub continuation_job_id: Option<String>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub continuation_message_id: Option<String>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub timeout_at: Option<String>,
    #[serde(default)]
    pub created_at: String,
    #[serde(default)]
    pub updated_at: String,
    #[serde(default)]
    pub diagnostics: Value,
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_mailbox_state_default() {
        assert_eq!(MailboxState::default(), MailboxState::Idle);
    }

    #[test]
    fn test_message_state_default() {
        assert_eq!(MessageState::default(), MessageState::Created);
    }

    #[test]
    fn test_message_record_serde() {
        let msg = MessageRecord {
            message_id: "m1".into(),
            origin_message_id: None,
            from_actor: "user".into(),
            target_scope: "agent".into(),
            target_agents: vec!["claude".into()],
            message_class: "task_request".into(),
            reply_policy: Value::Object(Default::default()),
            retry_policy: Value::Object(Default::default()),
            priority: 100,
            payload_ref: None,
            submission_id: None,
            created_at: "2025-01-01T00:00:00Z".into(),
            updated_at: "2025-01-01T00:00:00Z".into(),
            message_state: MessageState::Created,
        };
        let json = serde_json::to_string(&msg).unwrap();
        let deserialized: MessageRecord = serde_json::from_str(&json).unwrap();
        assert_eq!(deserialized.message_id, "m1");
        assert_eq!(deserialized.target_agents, vec!["claude"]);
    }

    #[test]
    fn test_inbound_event_serde() {
        let event = InboundEventRecord {
            inbound_event_id: "e1".into(),
            agent_name: "agent-a".into(),
            event_type: InboundEventType::TaskRequest,
            message_id: "m1".into(),
            attempt_id: None,
            payload_ref: None,
            priority: 100,
            status: InboundEventStatus::Queued,
            created_at: "2025-01-01T00:00:00Z".into(),
            started_at: None,
            finished_at: None,
        };
        let json = serde_json::to_string(&event).unwrap();
        let deserialized: InboundEventRecord = serde_json::from_str(&json).unwrap();
        assert_eq!(deserialized.event_type, InboundEventType::TaskRequest);
    }

    #[test]
    fn test_callback_edge_state_default() {
        assert_eq!(CallbackEdgeState::default(), CallbackEdgeState::Pending);
    }

    #[test]
    fn test_event_type_display_is_snake_case() {
        assert_eq!(InboundEventType::TaskRequest.to_string(), "task_request");
        assert_eq!(InboundEventType::TaskReply.to_string(), "task_reply");
        assert_eq!(
            InboundEventType::CompletionNotice.to_string(),
            "completion_notice"
        );
        assert_eq!(InboundEventType::SystemSignal.to_string(), "system_signal");
        assert_eq!(
            InboundEventType::BarrierRelease.to_string(),
            "barrier_release"
        );
    }
}
