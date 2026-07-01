use serde_json::Value;

use crate::facade_recording::{
    claimable_request_job_ids as recording_claimable_request_job_ids,
    mark_attempt_started as recording_mark_attempt_started,
    record_attempt_terminal as recording_record_attempt_terminal,
    record_notice as recording_record_notice, record_reply as recording_record_reply,
    record_retry_attempt as recording_record_retry_attempt,
    record_submission as recording_record_submission, record_terminal as recording_record_terminal,
    CompletionDecision,
};
use std::sync::Arc;

use crate::facade_state::{
    refresh_message_state, set_message_state, ControlState as BureauControlState, FacadeState,
};
use crate::jobs::{JobStore, SubmissionStore};
use crate::kernel::{Clock, MailboxKernelService};
use crate::models::{
    CallbackEdgeRecord, JobRecord, MessageEnvelope, MessageRecord, MessageState,
    ReplyTerminalStatus,
};
use crate::stores::{
    AttemptStore, CallbackEdgeStore, DeliveryLeaseStore, InboundEventStore, MailboxStore,
    MessageStore, ReplyStore,
};
use crate::targets::known_mailbox_targets;

/// Message Bureau facade: high-level message lifecycle management.
pub struct MessageBureauFacade {
    state: FacadeState,
}

impl MessageBureauFacade {
    pub fn new(
        layout: ccb_storage::paths::PathLayout,
        config: Option<Value>,
        clock: Clock,
    ) -> Self {
        let known_mailboxes = known_mailbox_targets(config.as_ref());
        let known_agents = config
            .as_ref()
            .and_then(|c| c.get("agents"))
            .and_then(|v| v.as_object())
            .map(|agents| agents.keys().map(|k| k.to_lowercase()).collect())
            .unwrap_or_default();
        let mailbox_store = MailboxStore::new(&layout);
        let inbound_store = InboundEventStore::new(&layout);
        let lease_store = DeliveryLeaseStore::new(&layout);
        let mailbox_kernel = MailboxKernelService::with_stores(
            layout.clone(),
            Some(Arc::new({
                let clock = Arc::clone(&clock);
                move || clock()
            })),
            Some(mailbox_store.clone()),
            Some(inbound_store.clone()),
            Some(lease_store.clone()),
        );
        Self {
            state: FacadeState {
                layout: layout.clone(),
                clock,
                known_agents,
                known_mailboxes,
                message_store: MessageStore::new(&layout),
                attempt_store: AttemptStore::new(&layout),
                reply_store: ReplyStore::new(&layout),
                callback_edge_store: CallbackEdgeStore::new(&layout),
                mailbox_store,
                inbound_store,
                lease_store,
                mailbox_kernel,
            },
        }
    }

    pub fn state(&self) -> &FacadeState {
        &self.state
    }

    pub fn record_submission(
        &self,
        request: &MessageEnvelope,
        jobs: &[JobRecord],
        submission_id: Option<&str>,
        accepted_at: &str,
        origin_message_id: Option<&str>,
    ) -> Option<String> {
        recording_record_submission(
            &self.state,
            request,
            jobs,
            submission_id,
            accepted_at,
            origin_message_id,
        )
    }

    pub fn claimable_request_job_ids(&self, agent_name: &str) -> Vec<String> {
        recording_claimable_request_job_ids(&self.state, agent_name)
    }

    pub fn mark_attempt_started(&self, job: &JobRecord, started_at: &str) {
        recording_mark_attempt_started(&self.state, job, started_at);
    }

    pub fn record_attempt_terminal(
        &self,
        job: &JobRecord,
        decision: &CompletionDecision,
        finished_at: &str,
    ) {
        recording_record_attempt_terminal(&self.state, job, decision, finished_at);
    }

    pub fn record_reply(
        &self,
        job: &JobRecord,
        decision: &CompletionDecision,
        finished_at: &str,
        deliver_to_caller: bool,
    ) -> Option<String> {
        recording_record_reply(&self.state, job, decision, finished_at, deliver_to_caller)
    }

    pub fn record_notice(
        &self,
        job: &JobRecord,
        reply: &str,
        diagnostics: Option<Value>,
        finished_at: &str,
        terminal_status: ReplyTerminalStatus,
        deliver_to_actor: Option<&str>,
    ) -> Option<String> {
        recording_record_notice(
            &self.state,
            job,
            reply,
            diagnostics,
            finished_at,
            terminal_status,
            deliver_to_actor,
        )
    }

    pub fn record_terminal(
        &self,
        job: &JobRecord,
        decision: &CompletionDecision,
        finished_at: &str,
        deliver_to_caller: bool,
        record_reply_enabled: bool,
    ) -> Option<String> {
        recording_record_terminal(
            &self.state,
            job,
            decision,
            finished_at,
            deliver_to_caller,
            record_reply_enabled,
        )
    }

    pub fn record_retry_attempt(
        &self,
        message_id: &str,
        job: &JobRecord,
        accepted_at: &str,
    ) -> crate::Result<String> {
        recording_record_retry_attempt(&self.state, message_id, job, accepted_at)
    }

    pub fn set_message_state(&self, message_id: &str, next_state: MessageState, updated_at: &str) {
        set_message_state(&self.state, message_id, next_state, updated_at);
    }

    pub fn refresh_message_state(&self, message_id: &str, updated_at: &str) {
        refresh_message_state(&self.state, message_id, updated_at);
    }

    pub fn record_callback_edge(&self, edge: &CallbackEdgeRecord) -> crate::Result<()> {
        self.state.callback_edge_store.append(edge)
    }

    pub fn callback_edge_for_child_job(&self, child_job_id: &str) -> Option<CallbackEdgeRecord> {
        self.state
            .callback_edge_store
            .get_latest_for_child_job(child_job_id)
    }

    pub fn callback_edge_for_child_message(
        &self,
        child_message_id: &str,
    ) -> Option<CallbackEdgeRecord> {
        self.state
            .callback_edge_store
            .get_latest_for_child_message(child_message_id)
    }

    pub fn callback_edge_for_parent_job(&self, parent_job_id: &str) -> Option<CallbackEdgeRecord> {
        self.state
            .callback_edge_store
            .get_latest_for_parent_job(parent_job_id)
    }

    pub fn callback_edge(&self, edge_id: &str) -> Option<CallbackEdgeRecord> {
        self.state.callback_edge_store.get_latest(edge_id)
    }

    pub fn pending_callback_edges(&self) -> Vec<CallbackEdgeRecord> {
        crate::facade_state::pending_callback_edges(&self.state.callback_edge_store)
    }

    /// Update an existing callback edge by appending a new record with the
    /// requested changes. Mirrors Python `MessageBureauFacade.update_callback_edge`.
    pub fn update_callback_edge(
        &self,
        edge: &CallbackEdgeRecord,
        changes: crate::stores::CallbackEdgeChanges,
    ) -> CallbackEdgeRecord {
        self.state.callback_edge_store.update(edge, changes)
    }

    pub fn all_messages(&self) -> Vec<MessageRecord> {
        self.state.message_store.list_all()
    }

    pub fn get_message(&self, message_id: &str) -> Option<MessageRecord> {
        self.state.message_store.get_latest(message_id)
    }

    pub fn all_attempts(&self) -> Vec<crate::models::AttemptRecord> {
        self.state.attempt_store.list_all()
    }

    pub fn replies_for_message(&self, message_id: &str) -> Vec<crate::models::ReplyRecord> {
        self.state.reply_store.list_message(message_id)
    }
}

/// Control service for queue inspection and management.
pub struct MessageBureauControlService {
    state: BureauControlState,
}

impl MessageBureauControlService {
    pub fn new(
        layout: ccb_storage::paths::PathLayout,
        config: Option<Value>,
        clock: Option<Clock>,
    ) -> Self {
        let resolved_clock = clock.unwrap_or_else(|| {
            Arc::new(|| {
                chrono::Utc::now()
                    .to_rfc3339_opts(chrono::SecondsFormat::Secs, true)
                    .replace("+00:00", "Z")
            })
        });
        let known_mailboxes = known_mailbox_targets(config.as_ref());
        let mailbox_store = MailboxStore::new(&layout);
        let inbound_store = InboundEventStore::new(&layout);
        let lease_store = DeliveryLeaseStore::new(&layout);
        let mailbox_kernel = MailboxKernelService::with_stores(
            layout.clone(),
            Some(Arc::new({
                let clock = Arc::clone(&resolved_clock);
                move || clock()
            })),
            Some(mailbox_store.clone()),
            Some(inbound_store.clone()),
            Some(lease_store.clone()),
        );
        Self {
            state: BureauControlState {
                layout: layout.clone(),
                config: config.clone(),
                known_mailboxes,
                clock: resolved_clock,
                mailbox_store,
                inbound_store,
                lease_store,
                message_store: MessageStore::new(&layout),
                attempt_store: AttemptStore::new(&layout),
                reply_store: ReplyStore::new(&layout),
                job_store: JobStore::new(&layout),
                submission_store: SubmissionStore::new(&layout),
                mailbox_kernel,
            },
        }
    }

    /// Build a control service that shares persistent stores with an existing
    /// bureau facade. This keeps the mailbox inspection layer consistent with
    /// the facade that records submissions and completions.
    pub fn from_facade(
        facade: &MessageBureauFacade,
        config: Option<Value>,
        job_store: Option<JobStore>,
        submission_store: Option<SubmissionStore>,
    ) -> Self {
        let state = facade.state();
        let known_mailboxes = known_mailbox_targets(config.as_ref());
        let mailbox_kernel = MailboxKernelService::with_stores(
            state.layout.clone(),
            Some(Arc::clone(&state.clock)),
            Some(state.mailbox_store.clone()),
            Some(state.inbound_store.clone()),
            Some(state.lease_store.clone()),
        );
        Self {
            state: BureauControlState {
                layout: state.layout.clone(),
                config: config.clone(),
                known_mailboxes,
                clock: Arc::clone(&state.clock),
                mailbox_store: state.mailbox_store.clone(),
                inbound_store: state.inbound_store.clone(),
                lease_store: state.lease_store.clone(),
                message_store: state.message_store.clone(),
                attempt_store: state.attempt_store.clone(),
                reply_store: state.reply_store.clone(),
                job_store: job_store.unwrap_or_else(|| JobStore::new(&state.layout)),
                submission_store: submission_store
                    .unwrap_or_else(|| SubmissionStore::new(&state.layout)),
                mailbox_kernel,
            },
        }
    }

    pub fn queue_summary(&self, target: &str, detail: Option<bool>) -> Value {
        crate::control_queue::queue_summary(&self.state, target, detail)
    }

    pub fn agent_queue(&self, agent_name: &str) -> Value {
        crate::control_queue::agent_queue(&self.state, agent_name)
    }

    pub fn trace(&self, target: &str) -> Value {
        let trace_state = crate::control_trace::TraceState {
            config: self.state.config.clone(),
            mailbox_store: self.state.mailbox_store.clone(),
            inbound_store: self.state.inbound_store.clone(),
            message_store: self.state.message_store.clone(),
            attempt_store: self.state.attempt_store.clone(),
            reply_store: self.state.reply_store.clone(),
            job_store: self.state.job_store.clone(),
            submission_store: self.state.submission_store.clone(),
        };
        crate::control_trace::trace(&trace_state, target)
    }

    pub fn inbox(&self, agent_name: &str, detail: Option<bool>) -> Value {
        crate::control_queue::inbox(&self.state, agent_name, detail)
    }

    /// Read access to the inbound event store (mirrors Python
    /// `_message_bureau_control._inbound_store`). Additive API exposed for
    /// daemon comms-recovery parity; wraps the existing pub store, no behavior
    /// change.
    pub fn inbound_store(&self) -> &crate::stores::InboundEventStore {
        &self.state.inbound_store
    }

    /// Read access to the delivery lease store.
    pub fn lease_store(&self) -> &crate::stores::DeliveryLeaseStore {
        &self.state.lease_store
    }

    /// Read access to the mailbox kernel service (head/abandon/refresh).
    pub fn mailbox_kernel(&self) -> &crate::kernel::MailboxKernelService {
        &self.state.mailbox_kernel
    }

    /// Read access to the attempt store.
    pub fn attempt_store(&self) -> &crate::stores::AttemptStore {
        &self.state.attempt_store
    }

    /// Read access to the reply store.
    pub fn reply_store(&self) -> &crate::stores::ReplyStore {
        &self.state.reply_store
    }

    /// Read access to the message store.
    pub fn message_store(&self) -> &crate::stores::MessageStore {
        &self.state.message_store
    }

    pub fn mailbox_head(&self, agent_name: &str) -> Value {
        crate::control_queue::mailbox_head(&self.state, agent_name)
    }

    pub fn ack_reply(&self, agent_name: &str, inbound_event_id: Option<&str>) -> Value {
        crate::control_queue::ack_reply(&self.state, agent_name, inbound_event_id)
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::models::{DeliveryScope, JobStatus, TargetKind};
    use tempfile::TempDir;

    fn make_envelope(from_actor: &str) -> MessageEnvelope {
        MessageEnvelope {
            project_id: "p1".into(),
            to_agent: "claude".into(),
            from_actor: from_actor.into(),
            body: "hello".into(),
            task_id: None,
            reply_to: None,
            message_type: "task_request".into(),
            delivery_scope: DeliveryScope::Agent,
            silence_on_success: false,
            route_options: Value::Object(Default::default()),
            body_artifact: None,
        }
    }

    fn make_job(job_id: &str, agent_name: &str) -> JobRecord {
        JobRecord {
            job_id: job_id.into(),
            submission_id: None,
            agent_name: agent_name.into(),
            provider: "claude".into(),
            request: make_envelope("user"),
            status: JobStatus::Accepted,
            terminal_decision: None,
            cancel_requested_at: None,
            created_at: "2025-01-01T00:00:00Z".into(),
            updated_at: "2025-01-01T00:00:00Z".into(),
            workspace_path: None,
            target_kind: TargetKind::Agent,
            target_name: agent_name.into(),
            provider_instance: None,
            provider_options: Value::Object(Default::default()),
        }
    }

    #[test]
    fn test_record_submission() {
        let dir = TempDir::new().unwrap();
        let p = camino::Utf8Path::from_path(dir.path()).unwrap();
        let layout = ccb_storage::paths::PathLayout::new(p);
        let config = serde_json::json!({ "agents": { "claude": {} } });
        let facade = MessageBureauFacade::new(
            layout,
            Some(config),
            Arc::new(|| "2025-01-01T00:00:00Z".into()),
        );
        let jobs = vec![make_job("job1", "claude")];
        let message_id = facade.record_submission(
            &make_envelope("user"),
            &jobs,
            Some("sub1"),
            "2025-01-01T00:00:00Z",
            None,
        );
        assert!(message_id.is_some());
        assert_eq!(facade.all_messages().len(), 1);
    }

    #[test]
    fn test_queue_summary() {
        let dir = TempDir::new().unwrap();
        let p = camino::Utf8Path::from_path(dir.path()).unwrap();
        let layout = ccb_storage::paths::PathLayout::new(p);
        let config = serde_json::json!({ "agents": { "claude": {} } });
        let control = MessageBureauControlService::new(layout, Some(config), None);
        let summary = control.queue_summary("all", None);
        assert_eq!(summary.get("agent_count").and_then(|v| v.as_u64()), Some(1));
    }
}
