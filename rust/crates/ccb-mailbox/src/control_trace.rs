use serde_json::Value;

use crate::control_queue::preview_text;
use crate::jobs::{JobStore, SubmissionStore};
use crate::models::{AttemptRecord, MessageRecord, ReplyRecord, SubmissionRecord};
use crate::reply_metadata::{
    reply_heartbeat_silence_seconds, reply_last_progress_at, reply_notice, reply_notice_kind,
};
use crate::stores::{AttemptStore, InboundEventStore, MailboxStore, MessageStore, ReplyStore};

pub struct TraceState {
    pub config: Option<Value>,
    pub mailbox_store: MailboxStore,
    pub inbound_store: InboundEventStore,
    pub message_store: MessageStore,
    pub attempt_store: AttemptStore,
    pub reply_store: ReplyStore,
    pub job_store: JobStore,
    pub submission_store: SubmissionStore,
}

pub fn trace(state: &TraceState, target: &str) -> Value {
    let identifier = target.trim();
    if identifier.is_empty() {
        panic!("trace requires target");
    }
    if identifier.starts_with("sub_") {
        return trace_submission(state, identifier);
    }
    if identifier.starts_with("msg_") {
        return trace_message(state, identifier, "message");
    }
    if identifier.starts_with("att_") {
        return trace_attempt(state, identifier);
    }
    if identifier.starts_with("rep_") {
        return trace_reply(state, identifier);
    }
    if identifier.starts_with("job_") {
        return trace_job(state, identifier);
    }
    panic!("trace requires <submission_id|message_id|attempt_id|reply_id|job_id>");
}

fn trace_submission(state: &TraceState, submission_id: &str) -> Value {
    let submission = state
        .submission_store
        .get_latest(submission_id)
        .unwrap_or_else(|| panic!("submission not found: {submission_id}"));
    let messages = latest_messages_for_submission(state, submission_id);
    let attempts = attempt_summaries_for_messages(state, &messages);
    let replies = reply_summaries_for_messages(state, &messages);
    let jobs: Vec<Value> = submission
        .job_ids
        .iter()
        .map(|job_id| job_summary(state, job_id, None))
        .collect();
    let events = event_summaries_for_messages(state, &messages);
    trace_payload(
        state,
        submission_id,
        "submission",
        Some(submission_summary(&submission)),
        None,
        None,
        None,
        None,
        &messages,
        &attempts,
        &replies,
        &events,
        &jobs,
    )
}

fn trace_message(state: &TraceState, message_id: &str, resolved_kind: &str) -> Value {
    let message = state
        .message_store
        .get_latest(message_id)
        .unwrap_or_else(|| panic!("message not found: {message_id}"));
    let attempts = attempt_summaries_for_records(&latest_attempts_for_message(state, message_id));
    let replies = reply_summaries_for_records(&state.reply_store.list_message(message_id));
    let events = event_summaries_for_messages(state, std::slice::from_ref(&message));
    let jobs: Vec<Value> = attempts
        .iter()
        .filter_map(|item| {
            item.get("job_id")
                .and_then(|v| v.as_str())
                .map(|s| s.to_string())
        })
        .map(|job_id| {
            job_summary(
                state,
                &job_id,
                Some(&message.target_agents.first().cloned().unwrap_or_default()),
            )
        })
        .collect();
    let submission = submission_summary_by_id(state, message.submission_id.as_deref());
    trace_payload(
        state,
        message_id,
        resolved_kind,
        submission,
        Some(message_summary(&message)),
        None,
        None,
        None,
        std::slice::from_ref(&message),
        &attempts,
        &replies,
        &events,
        &jobs,
    )
}

fn trace_attempt(state: &TraceState, attempt_id: &str) -> Value {
    let attempt = state
        .attempt_store
        .get_latest(attempt_id)
        .unwrap_or_else(|| panic!("attempt not found: {attempt_id}"));
    let message = state
        .message_store
        .get_latest(&attempt.message_id)
        .unwrap_or_else(|| panic!("message not found for attempt: {attempt_id}"));
    let replies: Vec<ReplyRecord> = state
        .reply_store
        .list_message(&message.message_id)
        .into_iter()
        .filter(|r| r.attempt_id == attempt_id)
        .collect();
    trace_payload(
        state,
        attempt_id,
        "attempt",
        submission_summary_by_id(state, message.submission_id.as_deref()),
        Some(message_summary(&message)),
        Some(attempt_summary(&attempt)),
        None,
        None,
        &[message],
        &[attempt_summary(&attempt)],
        &reply_summaries_for_records(&replies),
        &event_summaries_for_attempt(state, &attempt),
        &[job_summary(
            state,
            &attempt.job_id,
            Some(&attempt.agent_name),
        )],
    )
}

fn trace_reply(state: &TraceState, reply_id: &str) -> Value {
    let reply = state
        .reply_store
        .get_latest(reply_id)
        .unwrap_or_else(|| panic!("reply not found: {reply_id}"));
    let attempt = state
        .attempt_store
        .get_latest(&reply.attempt_id)
        .unwrap_or_else(|| panic!("attempt not found for reply: {reply_id}"));
    let message = state
        .message_store
        .get_latest(&reply.message_id)
        .unwrap_or_else(|| panic!("message not found for reply: {reply_id}"));
    trace_payload(
        state,
        reply_id,
        "reply",
        submission_summary_by_id(state, message.submission_id.as_deref()),
        Some(message_summary(&message)),
        Some(attempt_summary(&attempt)),
        Some(reply_summary(&reply)),
        None,
        &[message],
        &[attempt_summary(&attempt)],
        &[reply_summary(&reply)],
        &event_summaries_for_attempt(state, &attempt),
        &[job_summary(
            state,
            &attempt.job_id,
            Some(&attempt.agent_name),
        )],
    )
}

fn trace_job(state: &TraceState, job_id: &str) -> Value {
    let attempt = state
        .attempt_store
        .get_latest_by_job_id(job_id)
        .unwrap_or_else(|| panic!("job not found in message bureau: {job_id}"));
    let message = state
        .message_store
        .get_latest(&attempt.message_id)
        .unwrap_or_else(|| panic!("message not found for job: {job_id}"));
    let replies: Vec<ReplyRecord> = state
        .reply_store
        .list_message(&message.message_id)
        .into_iter()
        .filter(|r| r.attempt_id == attempt.attempt_id)
        .collect();
    let job = job_summary(state, job_id, Some(&attempt.agent_name));
    trace_payload(
        state,
        job_id,
        "job",
        submission_summary_by_id(state, message.submission_id.as_deref()),
        Some(message_summary(&message)),
        Some(attempt_summary(&attempt)),
        None,
        Some(job.clone()),
        &[message],
        &[attempt_summary(&attempt)],
        &reply_summaries_for_records(&replies),
        &event_summaries_for_attempt(state, &attempt),
        &[job],
    )
}

fn latest_messages_for_submission(state: &TraceState, submission_id: &str) -> Vec<MessageRecord> {
    latest_records(
        state.message_store.list_submission(submission_id),
        |record| record.message_id.clone(),
    )
}

fn latest_attempts_for_message(state: &TraceState, message_id: &str) -> Vec<AttemptRecord> {
    latest_records(state.attempt_store.list_message(message_id), |record| {
        record.attempt_id.clone()
    })
}

fn attempt_summaries_for_messages(state: &TraceState, messages: &[MessageRecord]) -> Vec<Value> {
    let mut items = Vec::new();
    for message in messages {
        let attempts = latest_attempts_for_message(state, &message.message_id);
        items.extend(attempt_summaries_for_records(&attempts));
    }
    items
}

fn attempt_summaries_for_records(attempts: &[AttemptRecord]) -> Vec<Value> {
    let mut ordered = attempts.to_vec();
    ordered.sort_by(|a, b| {
        a.retry_index
            .cmp(&b.retry_index)
            .then_with(|| a.started_at.cmp(&b.started_at))
            .then_with(|| a.attempt_id.cmp(&b.attempt_id))
    });
    ordered.iter().map(attempt_summary).collect()
}

fn reply_summaries_for_messages(state: &TraceState, messages: &[MessageRecord]) -> Vec<Value> {
    let mut items = Vec::new();
    for message in messages {
        let replies = state.reply_store.list_message(&message.message_id);
        items.extend(reply_summaries_for_records(&replies));
    }
    items
}

fn reply_summaries_for_records(replies: &[ReplyRecord]) -> Vec<Value> {
    let mut ordered = replies.to_vec();
    ordered.sort_by(|a, b| {
        a.finished_at
            .cmp(&b.finished_at)
            .then_with(|| a.reply_id.cmp(&b.reply_id))
    });
    ordered.iter().map(reply_summary).collect()
}

fn event_summaries_for_messages(state: &TraceState, messages: &[MessageRecord]) -> Vec<Value> {
    let message_ids: std::collections::HashSet<_> =
        messages.iter().map(|m| m.message_id.clone()).collect();
    let mut events = Vec::new();
    if let Some(config) = state
        .config
        .as_ref()
        .and_then(|c| c.get("agents"))
        .and_then(|v| v.as_object())
    {
        for agent_name in config.keys() {
            for record in state.inbound_store.list_agent(agent_name) {
                if message_ids.contains(&record.message_id) {
                    events.push(record);
                }
            }
        }
    }
    events = latest_records(events, |record| record.inbound_event_id.clone());
    events.sort_by(|a, b| {
        a.created_at
            .cmp(&b.created_at)
            .then_with(|| a.inbound_event_id.cmp(&b.inbound_event_id))
    });
    events.iter().map(|e| event_summary(state, e)).collect()
}

fn event_summaries_for_attempt(state: &TraceState, attempt: &AttemptRecord) -> Vec<Value> {
    let Some(message) = state.message_store.get_latest(&attempt.message_id) else {
        return Vec::new();
    };
    event_summaries_for_messages(state, &[message])
        .into_iter()
        .filter(|event| {
            event.get("attempt_id").and_then(|v| v.as_str()) == Some(&attempt.attempt_id)
        })
        .collect()
}

fn submission_summary_by_id(state: &TraceState, submission_id: Option<&str>) -> Option<Value> {
    let submission_id = submission_id?;
    state
        .submission_store
        .get_latest(submission_id)
        .map(|s| submission_summary(&s))
}

#[allow(clippy::too_many_arguments)]
fn trace_payload(
    _state: &TraceState,
    target: &str,
    resolved_kind: &str,
    submission: Option<Value>,
    message: Option<Value>,
    attempt: Option<Value>,
    reply: Option<Value>,
    job: Option<Value>,
    messages: &[MessageRecord],
    attempts: &[Value],
    replies: &[Value],
    events: &[Value],
    jobs: &[Value],
) -> Value {
    let message_items: Vec<Value> = messages.iter().map(message_summary).collect();
    let attempt_items = attempts.to_vec();
    let reply_items = replies.to_vec();
    let event_items = events.to_vec();
    let job_items: Vec<Value> = jobs.iter().filter(|&j| !j.is_null()).cloned().collect();
    let message_id = message
        .as_ref()
        .and_then(|m| m.get("message_id").cloned())
        .or_else(|| {
            message_items
                .first()
                .and_then(|m| m.get("message_id").cloned())
        });
    serde_json::json!({
        "target": target,
        "resolved_kind": resolved_kind,
        "submission_id": submission.as_ref().and_then(|s| s.get("submission_id").cloned()),
        "message_id": message_id,
        "attempt_id": attempt.as_ref().and_then(|a| a.get("attempt_id").cloned()),
        "reply_id": reply.as_ref().and_then(|r| r.get("reply_id").cloned()),
        "job_id": job.as_ref().and_then(|j| j.get("job_id").cloned()),
        "submission": submission,
        "message": message,
        "attempt": attempt,
        "reply": reply,
        "job": job,
        "message_count": message_items.len(),
        "attempt_count": attempt_items.len(),
        "reply_count": reply_items.len(),
        "event_count": event_items.len(),
        "job_count": job_items.len(),
        "messages": message_items,
        "attempts": attempt_items,
        "replies": reply_items,
        "events": event_items,
        "jobs": job_items,
    })
}

fn latest_records<T>(records: Vec<T>, key_fn: impl Fn(&T) -> String) -> Vec<T> {
    let mut latest: std::collections::HashMap<String, T> = std::collections::HashMap::new();
    let mut order: Vec<String> = Vec::new();
    for record in records {
        let key = key_fn(&record);
        if !latest.contains_key(&key) {
            order.push(key.clone());
        }
        latest.insert(key, record);
    }
    order
        .into_iter()
        .filter_map(|key| latest.remove(&key))
        .collect()
}

fn submission_summary(submission: &SubmissionRecord) -> Value {
    serde_json::json!({
        "submission_id": submission.submission_id,
        "from_actor": submission.from_actor,
        "target_scope": submission.target_scope,
        "task_id": submission.task_id,
        "job_ids": submission.job_ids,
        "created_at": submission.created_at,
        "updated_at": submission.updated_at,
    })
}

fn message_summary(message: &MessageRecord) -> Value {
    serde_json::json!({
        "message_id": message.message_id,
        "origin_message_id": message.origin_message_id,
        "submission_id": message.submission_id,
        "from_actor": message.from_actor,
        "target_scope": message.target_scope,
        "target_agents": message.target_agents,
        "message_class": message.message_class,
        "message_state": format!("{:?}", message.message_state).to_lowercase(),
        "priority": message.priority,
        "reply_mode": message.reply_policy.get("mode"),
        "expected_reply_count": message.reply_policy.get("expected_reply_count"),
        "silence_on_success": message.reply_policy.get("silence_on_success").and_then(|v| v.as_bool()).unwrap_or(false),
        "retry_mode": message.retry_policy.get("mode"),
        "created_at": message.created_at,
        "updated_at": message.updated_at,
    })
}

fn attempt_summary(attempt: &AttemptRecord) -> Value {
    serde_json::json!({
        "attempt_id": attempt.attempt_id,
        "message_id": attempt.message_id,
        "agent_name": attempt.agent_name,
        "provider": attempt.provider,
        "job_id": attempt.job_id,
        "retry_index": attempt.retry_index,
        "attempt_state": format!("{:?}", attempt.attempt_state).to_lowercase(),
        "health_snapshot_ref": attempt.health_snapshot_ref,
        "started_at": attempt.started_at,
        "updated_at": attempt.updated_at,
    })
}

fn reply_summary(reply: &ReplyRecord) -> Value {
    serde_json::json!({
        "reply_id": reply.reply_id,
        "message_id": reply.message_id,
        "attempt_id": reply.attempt_id,
        "agent_name": reply.agent_name,
        "terminal_status": format!("{:?}", reply.terminal_status).to_lowercase(),
        "reply": reply.reply,
        "reply_preview": preview_text(&reply.reply, 120),
        "reply_size": reply.reply.len(),
        "notice": reply_notice(reply),
        "notice_kind": reply_notice_kind(reply).unwrap_or_default(),
        "last_progress_at": reply_last_progress_at(reply).unwrap_or_default(),
        "heartbeat_silence_seconds": reply_heartbeat_silence_seconds(reply).unwrap_or_default(),
        "reason": reply.diagnostics.get("reason"),
        "status": reply.diagnostics.get("status"),
        "silence_on_success": reply.diagnostics.get("silence_on_success").and_then(|v| v.as_bool()).unwrap_or(false),
        "provider_turn_ref": reply.diagnostics.get("provider_turn_ref"),
        "finished_at": reply.finished_at,
    })
}

fn event_summary(state: &TraceState, event: &crate::models::InboundEventRecord) -> Value {
    let mailbox = state.mailbox_store.load(&event.agent_name).ok().flatten();
    serde_json::json!({
        "inbound_event_id": event.inbound_event_id,
        "agent_name": event.agent_name,
        "event_type": event.event_type.to_string(),
        "message_id": event.message_id,
        "attempt_id": event.attempt_id,
        "payload_ref": event.payload_ref,
        "priority": event.priority,
        "status": format!("{:?}", event.status).to_lowercase(),
        "mailbox_state": mailbox.as_ref().map(|m| format!("{:?}", m.mailbox_state).to_lowercase()),
        "mailbox_active": mailbox.as_ref().and_then(|m| m.active_inbound_event_id.as_deref()) == Some(&event.inbound_event_id),
        "created_at": event.created_at,
        "started_at": event.started_at,
        "finished_at": event.finished_at,
    })
}

fn job_summary(state: &TraceState, job_id: &str, hint_agent: Option<&str>) -> Value {
    let mut job = None;
    if let Some(agent_name) = hint_agent {
        job = state.job_store.get_latest(agent_name, job_id);
    }
    if job.is_none() {
        if let Some(config) = state
            .config
            .as_ref()
            .and_then(|c| c.get("agents"))
            .and_then(|v| v.as_object())
        {
            for agent_name in config.keys() {
                if Some(agent_name.as_str()) == hint_agent {
                    continue;
                }
                if let Some(found) = state.job_store.get_latest(agent_name, job_id) {
                    job = Some(found);
                    break;
                }
            }
        }
    }
    job.map(|j| {
        serde_json::json!({
            "job_id": j.job_id,
            "agent_name": j.agent_name,
            "provider": j.provider,
            "status": format!("{:?}", j.status).to_lowercase(),
            "submission_id": j.submission_id,
            "created_at": j.created_at,
            "updated_at": j.updated_at,
        })
    })
    .unwrap_or(Value::Null)
}
