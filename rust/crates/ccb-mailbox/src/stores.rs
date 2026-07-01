use camino::Utf8PathBuf;
use ccb_storage::json::JsonStore;
use ccb_storage::jsonl::JsonlStore;
use ccb_storage::paths::PathLayout;
use serde_json::Value;

use crate::models::*;

/// Store for mailbox summary records.
#[derive(Clone)]
pub struct MailboxStore {
    json: JsonStore,
    layout: PathLayout,
}

impl MailboxStore {
    pub fn new(layout: &PathLayout) -> Self {
        Self {
            json: JsonStore::new(),
            layout: layout.clone(),
        }
    }

    pub fn load(&self, agent_name: &str) -> crate::Result<Option<MailboxRecord>> {
        let path = self.layout.agent_mailbox_path(agent_name);
        crate::record_codec::load(&self.json, &path, crate::record_codec::MAILBOX_RECORD)
    }

    pub fn save(&self, record: &MailboxRecord) -> crate::Result<()> {
        let path = self.layout.agent_mailbox_path(&record.agent_name);
        crate::record_codec::save(&self.json, &path, crate::record_codec::MAILBOX_RECORD, record)
    }

    pub fn compare_and_save(
        &self,
        record: &MailboxRecord,
        expected_summary_version: Option<u32>,
    ) -> crate::Result<bool> {
        let current = self.load(&record.agent_name)?;
        let current_version = current.map(|m| m.summary_version);
        if current_version != expected_summary_version {
            return Ok(false);
        }
        self.save(record)?;
        Ok(true)
    }

    pub fn list_all(&self) -> Vec<MailboxRecord> {
        let directory = self.layout.ccbd_mailboxes_dir();
        if !directory.exists() {
            return Vec::new();
        }
        let mut records = Vec::new();
        for path in std::fs::read_dir(&directory)
            .unwrap_or_else(|_| panic!("read_dir failed: {directory}"))
            .flatten()
            .filter_map(|e| {
                let p = e.path();
                if p.is_dir() {
                    let mailbox = p.join("mailbox.json");
                    Utf8PathBuf::from_path_buf(mailbox).ok()
                } else {
                    None
                }
            })
        {
            if let Ok(Some(record)) =
                crate::record_codec::load::<MailboxRecord>(&self.json, &path, crate::record_codec::MAILBOX_RECORD)
            {
                records.push(record);
            }
        }
        records.sort_by(|a, b| a.agent_name.cmp(&b.agent_name));
        records
    }
}

/// Store for inbound event records.
#[derive(Clone)]
pub struct InboundEventStore {
    jsonl: JsonlStore,
    layout: PathLayout,
}

impl InboundEventStore {
    pub fn new(layout: &PathLayout) -> Self {
        Self {
            jsonl: JsonlStore::new(),
            layout: layout.clone(),
        }
    }

    pub fn append(&self, record: &InboundEventRecord) -> crate::Result<()> {
        let path = self.layout.agent_inbox_path(&record.agent_name);
        crate::record_codec::append(
            &self.jsonl,
            &path,
            crate::record_codec::INBOUND_EVENT_RECORD,
            record,
        )
    }

    pub fn list_agent(&self, agent_name: &str) -> Vec<InboundEventRecord> {
        let path = self.layout.agent_inbox_path(agent_name);
        crate::record_codec::read_all(
            &self.jsonl,
            &path,
            crate::record_codec::INBOUND_EVENT_RECORD,
        )
        .unwrap_or_default()
    }

    pub fn read_since(
        &self,
        agent_name: &str,
        start_line: usize,
    ) -> (usize, Vec<InboundEventRecord>) {
        let path = self.layout.agent_inbox_path(agent_name);
        crate::record_codec::read_since(
            &self.jsonl,
            &path,
            crate::record_codec::INBOUND_EVENT_RECORD,
            start_line,
        )
        .unwrap_or_else(|_| (0, Vec::new()))
    }

    pub fn get_latest(
        &self,
        agent_name: &str,
        inbound_event_id: &str,
    ) -> Option<InboundEventRecord> {
        let path = self.layout.agent_inbox_path(agent_name);
        crate::record_codec::find_last(
            &self.jsonl,
            &path,
            crate::record_codec::INBOUND_EVENT_RECORD,
            |payload: &InboundEventRecord| payload.inbound_event_id == inbound_event_id,
        )
        .unwrap_or_default()
    }

    pub fn get_latest_for_attempt(
        &self,
        agent_name: &str,
        attempt_id: &str,
    ) -> Option<InboundEventRecord> {
        let path = self.layout.agent_inbox_path(agent_name);
        crate::record_codec::find_last(
            &self.jsonl,
            &path,
            crate::record_codec::INBOUND_EVENT_RECORD,
            |payload: &InboundEventRecord| payload.attempt_id.as_deref() == Some(attempt_id),
        )
        .unwrap_or_default()
    }
}

/// Store for delivery leases.
#[derive(Clone)]
pub struct DeliveryLeaseStore {
    json: JsonStore,
    layout: PathLayout,
}

impl DeliveryLeaseStore {
    pub fn new(layout: &PathLayout) -> Self {
        Self {
            json: JsonStore::new(),
            layout: layout.clone(),
        }
    }

    pub fn load(&self, agent_name: &str) -> crate::Result<Option<DeliveryLease>> {
        let path = self.layout.mailbox_lease_path(agent_name);
        crate::record_codec::load(&self.json, &path, crate::record_codec::DELIVERY_LEASE)
    }

    pub fn save(&self, record: &DeliveryLease) -> crate::Result<()> {
        let path = self.layout.mailbox_lease_path(&record.agent_name);
        crate::record_codec::save(
            &self.json,
            &path,
            crate::record_codec::DELIVERY_LEASE,
            record,
        )
    }

    pub fn remove(&self, agent_name: &str) -> crate::Result<()> {
        let path = self.layout.mailbox_lease_path(agent_name);
        if path.exists() {
            std::fs::remove_file(&path)?;
        }
        Ok(())
    }

    pub fn list_all(&self) -> Vec<DeliveryLease> {
        let directory = self.layout.ccbd_leases_dir();
        if !directory.exists() {
            return Vec::new();
        }
        let mut leases = Vec::new();
        for path in std::fs::read_dir(&directory)
            .unwrap_or_else(|_| panic!("read_dir failed: {directory}"))
            .flatten()
            .filter_map(|e| {
                let p = e.path();
                if p.extension().map(|e| e == "json").unwrap_or(false) {
                    Utf8PathBuf::from_path_buf(p).ok()
                } else {
                    None
                }
            })
        {
            if let Ok(Some(lease)) =
                crate::record_codec::load::<DeliveryLease>(&self.json, &path, crate::record_codec::DELIVERY_LEASE)
            {
                leases.push(lease);
            }
        }
        leases.sort_by(|a, b| a.agent_name.cmp(&b.agent_name));
        leases
    }
}

/// Store for message records.
#[derive(Clone)]
pub struct MessageStore {
    jsonl: JsonlStore,
    layout: PathLayout,
}

impl MessageStore {
    pub fn new(layout: &PathLayout) -> Self {
        Self {
            jsonl: JsonlStore::new(),
            layout: layout.clone(),
        }
    }

    pub fn append(&self, record: &MessageRecord) -> crate::Result<()> {
        let path = self.layout.ccbd_messages_path();
        crate::record_codec::append(&self.jsonl, &path, crate::record_codec::MESSAGE_RECORD, record)
    }

    pub fn list_all(&self) -> Vec<MessageRecord> {
        let path = self.layout.ccbd_messages_path();
        crate::record_codec::read_all(&self.jsonl, &path, crate::record_codec::MESSAGE_RECORD)
            .unwrap_or_default()
    }

    pub fn get_latest(&self, message_id: &str) -> Option<MessageRecord> {
        let path = self.layout.ccbd_messages_path();
        crate::record_codec::find_last(
            &self.jsonl,
            &path,
            crate::record_codec::MESSAGE_RECORD,
            |payload: &MessageRecord| payload.message_id == message_id,
        )
        .unwrap_or_default()
    }

    pub fn list_submission(&self, submission_id: &str) -> Vec<MessageRecord> {
        self.list_all()
            .into_iter()
            .filter(|m| m.submission_id.as_deref() == Some(submission_id))
            .collect()
    }
}

/// Store for attempt records.
#[derive(Clone)]
pub struct AttemptStore {
    jsonl: JsonlStore,
    layout: PathLayout,
}

impl AttemptStore {
    pub fn new(layout: &PathLayout) -> Self {
        Self {
            jsonl: JsonlStore::new(),
            layout: layout.clone(),
        }
    }

    pub fn append(&self, record: &AttemptRecord) -> crate::Result<()> {
        let path = self.layout.ccbd_attempts_path();
        crate::record_codec::append(&self.jsonl, &path, crate::record_codec::ATTEMPT_RECORD, record)
    }

    pub fn list_all(&self) -> Vec<AttemptRecord> {
        let path = self.layout.ccbd_attempts_path();
        crate::record_codec::read_all(&self.jsonl, &path, crate::record_codec::ATTEMPT_RECORD)
            .unwrap_or_default()
    }

    pub fn get_latest(&self, attempt_id: &str) -> Option<AttemptRecord> {
        let path = self.layout.ccbd_attempts_path();
        crate::record_codec::find_last(
            &self.jsonl,
            &path,
            crate::record_codec::ATTEMPT_RECORD,
            |payload: &AttemptRecord| payload.attempt_id == attempt_id,
        )
        .unwrap_or_default()
    }

    pub fn get_latest_by_job_id(&self, job_id: &str) -> Option<AttemptRecord> {
        let path = self.layout.ccbd_attempts_path();
        crate::record_codec::find_last(
            &self.jsonl,
            &path,
            crate::record_codec::ATTEMPT_RECORD,
            |payload: &AttemptRecord| payload.job_id == job_id,
        )
        .unwrap_or_default()
    }

    pub fn get_latest_by_message_id(
        &self,
        message_id: &str,
        exclude_job_id: Option<&str>,
    ) -> Option<AttemptRecord> {
        let path = self.layout.ccbd_attempts_path();
        crate::record_codec::find_last(
            &self.jsonl,
            &path,
            crate::record_codec::ATTEMPT_RECORD,
            |payload: &AttemptRecord| {
                payload.message_id == message_id
                    && exclude_job_id.is_none_or(|ex| payload.job_id != ex)
            },
        )
        .unwrap_or_default()
    }

    pub fn get_latest_by_message_agent(
        &self,
        message_id: &str,
        agent_name: &str,
    ) -> Option<AttemptRecord> {
        let path = self.layout.ccbd_attempts_path();
        crate::record_codec::find_last(
            &self.jsonl,
            &path,
            crate::record_codec::ATTEMPT_RECORD,
            |payload: &AttemptRecord| {
                payload.message_id == message_id && payload.agent_name == agent_name
            },
        )
        .unwrap_or_default()
    }

    pub fn list_message(&self, message_id: &str) -> Vec<AttemptRecord> {
        self.list_all()
            .into_iter()
            .filter(|a| a.message_id == message_id)
            .collect()
    }

    pub fn list_agent(&self, agent_name: &str) -> Vec<AttemptRecord> {
        self.list_all()
            .into_iter()
            .filter(|a| a.agent_name == agent_name)
            .collect()
    }
}

/// Store for reply records.
#[derive(Clone)]
pub struct ReplyStore {
    jsonl: JsonlStore,
    layout: PathLayout,
}

impl ReplyStore {
    pub fn new(layout: &PathLayout) -> Self {
        Self {
            jsonl: JsonlStore::new(),
            layout: layout.clone(),
        }
    }

    pub fn append(&self, record: &ReplyRecord) -> crate::Result<()> {
        let path = self.layout.ccbd_replies_path();
        crate::record_codec::append(&self.jsonl, &path, crate::record_codec::REPLY_RECORD, record)
    }

    pub fn list_all(&self) -> Vec<ReplyRecord> {
        let path = self.layout.ccbd_replies_path();
        crate::record_codec::read_all(&self.jsonl, &path, crate::record_codec::REPLY_RECORD)
            .unwrap_or_default()
    }

    pub fn get_latest(&self, reply_id: &str) -> Option<ReplyRecord> {
        let path = self.layout.ccbd_replies_path();
        crate::record_codec::find_last(
            &self.jsonl,
            &path,
            crate::record_codec::REPLY_RECORD,
            |payload: &ReplyRecord| payload.reply_id == reply_id,
        )
        .unwrap_or_default()
    }

    pub fn list_message(&self, message_id: &str) -> Vec<ReplyRecord> {
        let path = self.layout.ccbd_replies_path();
        crate::record_codec::read_all::<ReplyRecord>(&self.jsonl, &path, crate::record_codec::REPLY_RECORD)
            .unwrap_or_default()
            .into_iter()
            .filter(|r| r.message_id == message_id)
            .collect()
    }
}

/// Store for callback edge records.
#[derive(Clone)]
pub struct CallbackEdgeStore {
    jsonl: JsonlStore,
    layout: PathLayout,
}

impl CallbackEdgeStore {
    pub fn new(layout: &PathLayout) -> Self {
        Self {
            jsonl: JsonlStore::new(),
            layout: layout.clone(),
        }
    }

    pub fn append(&self, record: &CallbackEdgeRecord) -> crate::Result<()> {
        let path = self.layout.ccbd_callback_edges_path();
        crate::record_codec::append(
            &self.jsonl,
            &path,
            crate::record_codec::CALLBACK_EDGE_RECORD,
            record,
        )
    }

    pub fn list_all(&self) -> Vec<CallbackEdgeRecord> {
        let path = self.layout.ccbd_callback_edges_path();
        crate::record_codec::read_all(
            &self.jsonl,
            &path,
            crate::record_codec::CALLBACK_EDGE_RECORD,
        )
        .unwrap_or_default()
    }

    pub fn get_latest(&self, edge_id: &str) -> Option<CallbackEdgeRecord> {
        let path = self.layout.ccbd_callback_edges_path();
        crate::record_codec::find_last(
            &self.jsonl,
            &path,
            crate::record_codec::CALLBACK_EDGE_RECORD,
            |payload: &CallbackEdgeRecord| payload.edge_id == edge_id,
        )
        .unwrap_or_default()
    }

    pub fn get_latest_for_child_job(&self, child_job_id: &str) -> Option<CallbackEdgeRecord> {
        let path = self.layout.ccbd_callback_edges_path();
        crate::record_codec::find_last(
            &self.jsonl,
            &path,
            crate::record_codec::CALLBACK_EDGE_RECORD,
            |payload: &CallbackEdgeRecord| payload.child_job_id == child_job_id,
        )
        .unwrap_or_default()
    }

    pub fn get_latest_for_child_message(
        &self,
        child_message_id: &str,
    ) -> Option<CallbackEdgeRecord> {
        let path = self.layout.ccbd_callback_edges_path();
        crate::record_codec::find_last(
            &self.jsonl,
            &path,
            crate::record_codec::CALLBACK_EDGE_RECORD,
            |payload: &CallbackEdgeRecord| payload.child_message_id == child_message_id,
        )
        .unwrap_or_default()
    }

    pub fn get_latest_for_parent_job(&self, parent_job_id: &str) -> Option<CallbackEdgeRecord> {
        let path = self.layout.ccbd_callback_edges_path();
        crate::record_codec::find_last(
            &self.jsonl,
            &path,
            crate::record_codec::CALLBACK_EDGE_RECORD,
            |payload: &CallbackEdgeRecord| payload.parent_job_id == parent_job_id,
        )
        .unwrap_or_default()
    }

    pub fn get_latest_continuation_for_edge(&self, edge_id: &str) -> Option<CallbackEdgeRecord> {
        let path = self.layout.ccbd_callback_edges_path();
        crate::record_codec::find_last(
            &self.jsonl,
            &path,
            crate::record_codec::CALLBACK_EDGE_RECORD,
            |payload: &CallbackEdgeRecord| {
                payload.edge_id == edge_id
                    && payload
                        .continuation_job_id
                        .as_deref()
                        .is_some_and(|s| !s.is_empty())
            },
        )
        .unwrap_or_default()
    }

    pub fn update(
        &self,
        record: &CallbackEdgeRecord,
        changes: CallbackEdgeChanges,
    ) -> CallbackEdgeRecord {
        let mut updated = record.clone();
        if let Some(state) = changes.state {
            updated.state = state;
        }
        if let Some(child_reply_id) = changes.child_reply_id {
            updated.child_reply_id = Some(child_reply_id);
        }
        if let Some(child_status) = changes.child_status {
            updated.child_status = Some(child_status);
        }
        if let Some(continuation_job_id) = changes.continuation_job_id {
            updated.continuation_job_id = Some(continuation_job_id);
        }
        if let Some(continuation_message_id) = changes.continuation_message_id {
            updated.continuation_message_id = Some(continuation_message_id);
        }
        if let Some(timeout_at) = changes.timeout_at {
            updated.timeout_at = timeout_at;
        }
        if let Some(diagnostics) = changes.diagnostics {
            updated.diagnostics = diagnostics;
        }
        if let Some(updated_at) = changes.updated_at {
            updated.updated_at = updated_at;
        }
        let _ = self.append(&updated);
        updated
    }
}

/// Changes to apply in a callback edge update.
pub struct CallbackEdgeChanges {
    pub state: Option<CallbackEdgeState>,
    pub child_reply_id: Option<String>,
    pub child_status: Option<String>,
    pub continuation_job_id: Option<String>,
    pub continuation_message_id: Option<String>,
    /// `None` means "do not change"; `Some(None)` clears the timeout;
    /// `Some(Some(ts))` sets it to `ts`.
    pub timeout_at: Option<Option<String>>,
    pub diagnostics: Option<Value>,
    pub updated_at: Option<String>,
}

impl CallbackEdgeChanges {
    pub fn new() -> Self {
        Self {
            state: None,
            child_reply_id: None,
            child_status: None,
            continuation_job_id: None,
            continuation_message_id: None,
            timeout_at: None,
            diagnostics: None,
            updated_at: None,
        }
    }
}

impl Default for CallbackEdgeChanges {
    fn default() -> Self {
        Self::new()
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use tempfile::TempDir;

    #[test]
    fn test_message_store_round_trip() {
        let dir = TempDir::new().unwrap();
        let p = camino::Utf8Path::from_path(dir.path()).unwrap();
        let layout = PathLayout::new(p);
        let store = MessageStore::new(&layout);
        let msg = MessageRecord {
            message_id: "m1".into(),
            origin_message_id: None,
            from_actor: "user".into(),
            target_scope: "agent".into(),
            target_agents: vec![],
            message_class: "task_request".into(),
            reply_policy: Value::Null,
            retry_policy: Value::Null,
            priority: 100,
            payload_ref: None,
            submission_id: None,
            created_at: String::new(),
            updated_at: String::new(),
            message_state: MessageState::default(),
        };
        store.append(&msg).unwrap();
        let all = store.list_all();
        assert_eq!(all.len(), 1);
        assert_eq!(all[0].message_id, "m1");
    }
}
