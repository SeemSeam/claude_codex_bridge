use serde_json::Value;

use crate::kernel::Clock;
use crate::kernel::MailboxKernelService;
use crate::models::{
    AttemptRecord, AttemptState, CallbackEdgeRecord, CallbackEdgeState, InboundEventRecord,
    InboundEventStatus, MessageRecord, MessageState, ReplyRecord, ReplyTerminalStatus,
};
use crate::stores::{
    AttemptStore, CallbackEdgeStore, DeliveryLeaseStore, InboundEventStore, MailboxStore,
    MessageStore, ReplyStore,
};
use crate::targets::normalize_agent_name;

const TERMINAL_ATTEMPT_STATES: &[AttemptState] = &[
    AttemptState::Completed,
    AttemptState::Incomplete,
    AttemptState::Failed,
    AttemptState::Cancelled,
    AttemptState::Superseded,
    AttemptState::DeadLetter,
];

const TERMINAL_INBOUND_STATUSES: &[InboundEventStatus] = &[
    InboundEventStatus::Consumed,
    InboundEventStatus::Superseded,
    InboundEventStatus::Abandoned,
];

/// Shared state container for the message bureau facade.
pub struct FacadeState {
    pub layout: ccb_storage::paths::PathLayout,
    pub clock: Clock,
    pub known_agents: Vec<String>,
    pub known_mailboxes: Vec<String>,
    pub message_store: MessageStore,
    pub attempt_store: AttemptStore,
    pub reply_store: ReplyStore,
    pub callback_edge_store: CallbackEdgeStore,
    pub mailbox_store: MailboxStore,
    pub inbound_store: InboundEventStore,
    pub lease_store: DeliveryLeaseStore,
    pub mailbox_kernel: MailboxKernelService,
}

impl FacadeState {
    pub fn now(&self) -> String {
        (self.clock)()
    }
}

/// Resolve the origin message id from a reply_to reference.
pub fn resolve_origin_message_id(state: &FacadeState, reply_to: Option<&str>) -> Option<String> {
    let key = reply_to.unwrap_or("").trim();
    if key.is_empty() {
        return None;
    }
    if let Some(attempt) = state.attempt_store.get_latest(key) {
        return Some(attempt.message_id);
    }
    if let Some(attempt) = state.attempt_store.get_latest_by_job_id(key) {
        return Some(attempt.message_id);
    }
    if let Some(message) = state.message_store.get_latest(key) {
        return Some(message.message_id);
    }
    None
}

/// Refresh message state based on attempts and replies.
pub fn refresh_message_state(state: &FacadeState, message_id: &str, updated_at: &str) {
    let Some(_message) = state.message_store.get_latest(message_id) else {
        return;
    };
    let attempts = latest_attempts_for_message(state, message_id);
    if attempts.is_empty() {
        return;
    }
    let active = active_attempts(&attempts);
    let replies = state.reply_store.list_message(message_id);
    let next_state = next_message_state(&active, &attempts, &replies);
    set_message_state(state, message_id, next_state, updated_at);
}

/// Set message state to next_state if different.
pub fn set_message_state(
    state: &FacadeState,
    message_id: &str,
    next_state: MessageState,
    updated_at: &str,
) {
    let Some(current) = state.message_store.get_latest(message_id) else {
        return;
    };
    if current.message_state == next_state {
        return;
    }
    let updated = MessageRecord {
        updated_at: updated_at.to_string(),
        message_state: next_state,
        ..current
    };
    let _ = state.message_store.append(&updated);
}

/// Get latest attempts for a message (one per attempt_id).
pub fn latest_attempts_for_message(state: &FacadeState, message_id: &str) -> Vec<AttemptRecord> {
    let mut latest: std::collections::HashMap<String, AttemptRecord> =
        std::collections::HashMap::new();
    for record in state.attempt_store.list_message(message_id) {
        latest.insert(record.attempt_id.clone(), record);
    }
    latest.into_values().collect()
}

/// Compute the next retry index for an agent on a message.
pub fn next_retry_index(state: &FacadeState, message_id: &str, agent_name: &str) -> u32 {
    let normalized = normalize_agent_name(agent_name);
    let mut latest: i32 = -1;
    for record in state.attempt_store.list_message(message_id) {
        if record.agent_name != normalized {
            continue;
        }
        latest = latest.max(record.retry_index as i32);
    }
    (latest + 1) as u32
}

/// Rebuild mailbox summary for an agent.
pub fn rebuild_mailbox_summary(
    state: &FacadeState,
    agent_name: &str,
    updated_at: &str,
) -> crate::Result<()> {
    state
        .mailbox_kernel
        .rebuild_mailbox_summary(agent_name, Some(updated_at))?;
    Ok(())
}

fn active_attempts(attempts: &[AttemptRecord]) -> Vec<AttemptRecord> {
    attempts
        .iter()
        .filter(|a| !TERMINAL_ATTEMPT_STATES.contains(&a.attempt_state))
        .cloned()
        .collect()
}

fn next_message_state(
    active: &[AttemptRecord],
    attempts: &[AttemptRecord],
    replies: &[ReplyRecord],
) -> MessageState {
    if !active.is_empty() {
        return if replies.is_empty() {
            MessageState::Running
        } else {
            MessageState::PartiallyReplied
        };
    }
    let non_notice: Vec<_> = replies.iter().filter(|r| !is_notice(r)).collect();
    let terminal_replies = if non_notice.is_empty() {
        replies.iter().collect()
    } else {
        non_notice
    };
    if !terminal_replies.is_empty() {
        return reply_terminal_state(
            &terminal_replies
                .iter()
                .map(|r| r.terminal_status)
                .collect::<Vec<_>>(),
        );
    }
    attempt_terminal_state(&attempts.iter().map(|a| a.attempt_state).collect::<Vec<_>>())
}

fn is_notice(reply: &ReplyRecord) -> bool {
    reply
        .diagnostics
        .get("notice")
        .and_then(|v| v.as_bool())
        .unwrap_or(false)
}

fn reply_terminal_state(statuses: &[ReplyTerminalStatus]) -> MessageState {
    let set: std::collections::HashSet<_> = statuses.iter().copied().collect();
    match set.len() {
        1 => match statuses[0] {
            ReplyTerminalStatus::Completed => MessageState::Completed,
            ReplyTerminalStatus::Cancelled => MessageState::Cancelled,
            ReplyTerminalStatus::Failed => MessageState::Failed,
            ReplyTerminalStatus::Incomplete => MessageState::Incomplete,
        },
        _ => MessageState::Incomplete,
    }
}

fn attempt_terminal_state(statuses: &[AttemptState]) -> MessageState {
    let set: std::collections::HashSet<_> = statuses.iter().copied().collect();
    match set.len() {
        1 => match statuses[0] {
            AttemptState::Completed => MessageState::Completed,
            AttemptState::Cancelled => MessageState::Cancelled,
            AttemptState::Failed => MessageState::Failed,
            AttemptState::Incomplete => MessageState::Incomplete,
            _ => MessageState::Incomplete,
        },
        _ => MessageState::Incomplete,
    }
}

/// State container for the message bureau control service.
pub struct ControlState {
    pub layout: ccb_storage::paths::PathLayout,
    pub config: Option<Value>,
    pub known_mailboxes: Vec<String>,
    pub clock: Clock,
    pub mailbox_store: MailboxStore,
    pub inbound_store: InboundEventStore,
    pub lease_store: DeliveryLeaseStore,
    pub message_store: MessageStore,
    pub attempt_store: AttemptStore,
    pub reply_store: ReplyStore,
    pub job_store: crate::jobs::JobStore,
    pub submission_store: crate::jobs::SubmissionStore,
    pub mailbox_kernel: MailboxKernelService,
}

impl ControlState {
    pub fn now(&self) -> String {
        (self.clock)()
    }
}

/// Determine whether an inbound event is terminal.
pub fn is_terminal_event(status: InboundEventStatus) -> bool {
    TERMINAL_INBOUND_STATUSES.contains(&status)
}

/// Determine whether an attempt is in a terminal state.
pub fn is_terminal_attempt(state: AttemptState) -> bool {
    TERMINAL_ATTEMPT_STATES.contains(&state)
}

/// Build a summary head from an event record.
pub fn mailbox_head_payload(record: Option<&InboundEventRecord>) -> Option<Value> {
    let record = record?;
    Some(serde_json::json!({
        "inbound_event_id": record.inbound_event_id,
        "event_type": record.event_type.to_string(),
        "status": format!("{:?}", record.status).to_lowercase(),
        "message_id": record.message_id,
        "attempt_id": record.attempt_id,
        "payload_ref": record.payload_ref,
    }))
}

/// Pending callback edges (state is Pending or ChildCompleted).
pub fn pending_callback_edges(store: &CallbackEdgeStore) -> Vec<CallbackEdgeRecord> {
    store
        .list_all()
        .into_iter()
        .filter(|e| {
            e.state == CallbackEdgeState::Pending || e.state == CallbackEdgeState::ChildCompleted
        })
        .collect()
}
