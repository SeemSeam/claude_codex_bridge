use serde_json::Value;
use uuid::Uuid;

use crate::facade_state::{
    next_retry_index, rebuild_mailbox_summary, refresh_message_state, resolve_origin_message_id,
    set_message_state, FacadeState,
};
use crate::models::{
    AttemptRecord, AttemptState, DeliveryScope, InboundEventRecord, InboundEventStatus,
    InboundEventType, JobRecord, JobStatus, MessageEnvelope, MessageRecord, MessageState,
    ReplyRecord, ReplyTerminalStatus,
};
use crate::reply_payloads::compose_reply_payload;
use crate::targets::normalize_mailbox_target;

/// Minimal completion decision mirroring Python completion.models.CompletionDecision.
#[derive(Debug, Clone)]
pub struct CompletionDecision {
    pub terminal: bool,
    pub status: JobStatus,
    pub reason: Option<String>,
    pub reply: String,
    pub provider_turn_ref: Option<String>,
    pub diagnostics: Value,
}

impl CompletionDecision {
    pub fn completed(reply: &str) -> Self {
        Self {
            terminal: true,
            status: JobStatus::Completed,
            reason: Some("completed".into()),
            reply: reply.into(),
            provider_turn_ref: None,
            diagnostics: Value::Object(Default::default()),
        }
    }
}

pub fn new_id(prefix: &str) -> String {
    format!(
        "{}_{}",
        prefix,
        &Uuid::new_v4().to_string().replace('-', "")[..12]
    )
}

pub fn job_id_from_payload_ref(payload_ref: Option<&str>) -> Option<String> {
    let text = payload_ref.unwrap_or("").trim();
    if !text.starts_with("job:") {
        return None;
    }
    let job_id = text.split_once(':').map(|x| x.1).unwrap_or("").trim();
    if job_id.is_empty() {
        return None;
    }
    Some(job_id.to_string())
}

pub fn attempt_state_for_status(status: JobStatus) -> AttemptState {
    match status {
        JobStatus::Completed => AttemptState::Completed,
        JobStatus::Failed => AttemptState::Failed,
        JobStatus::Incomplete => AttemptState::Incomplete,
        JobStatus::Cancelled => AttemptState::Cancelled,
        _ => AttemptState::Incomplete,
    }
}

pub fn reply_status_for_job(status: JobStatus) -> ReplyTerminalStatus {
    match status {
        JobStatus::Completed => ReplyTerminalStatus::Completed,
        JobStatus::Failed => ReplyTerminalStatus::Failed,
        JobStatus::Incomplete => ReplyTerminalStatus::Incomplete,
        JobStatus::Cancelled => ReplyTerminalStatus::Cancelled,
        _ => ReplyTerminalStatus::Incomplete,
    }
}

pub fn delivered_reply_text(job: &JobRecord, decision: &CompletionDecision) -> String {
    if job.status != JobStatus::Completed {
        return decision.reply.clone();
    }
    if !job.request.silence_on_success {
        return decision.reply.clone();
    }
    let mut parts = vec![
        "CCB_COMPLETE".to_string(),
        format!("from={}", job.agent_name),
        format!("status={:?}", job.status).to_lowercase(),
        format!("job={}", job.job_id),
    ];
    let task_id = job.request.task_id.as_deref().unwrap_or("").trim();
    if !task_id.is_empty() {
        parts.push(format!("task={}", task_id));
    }
    parts.push("result=hidden".to_string());
    parts.join(" ")
}

pub fn record_submission(
    state: &FacadeState,
    request: &MessageEnvelope,
    jobs: &[JobRecord],
    submission_id: Option<&str>,
    accepted_at: &str,
    origin_message_id: Option<&str>,
) -> Option<String> {
    if jobs.is_empty() {
        return None;
    }
    let message_id = new_id("msg");
    let message = MessageRecord {
        message_id: message_id.clone(),
        origin_message_id: origin_message_id
            .map(|s| s.to_string())
            .or_else(|| resolve_origin_message_id(state, request.reply_to.as_deref())),
        from_actor: request.from_actor.clone(),
        target_scope: match request.delivery_scope {
            DeliveryScope::Agent => "single".to_string(),
            DeliveryScope::Group => "group".to_string(),
            DeliveryScope::Broadcast => "broadcast".to_string(),
        },
        target_agents: jobs.iter().map(|j| j.agent_name.clone()).collect(),
        message_class: request.message_type.clone(),
        reply_policy: serde_json::json!({
            "mode": if jobs.len() > 1 { "all" } else { "single" },
            "expected_reply_count": jobs.len(),
            "silence_on_success": request.silence_on_success,
        }),
        retry_policy: serde_json::json!({
            "mode": "auto",
            "max_attempts": 3,
            "retryable_reasons": ["api_error", "transport_error"],
            "retry_runtime_when_resume_supported": true,
            "retryable_runtime_reasons": ["pane_dead", "pane_unavailable"],
        }),
        priority: 100,
        payload_ref: None,
        submission_id: submission_id.map(|s| s.to_string()),
        created_at: accepted_at.to_string(),
        updated_at: accepted_at.to_string(),
        message_state: MessageState::Queued,
    };
    let _ = state.message_store.append(&message);

    for job in jobs {
        let attempt_id = new_id("att");
        let attempt = AttemptRecord {
            attempt_id: attempt_id.clone(),
            message_id: message_id.clone(),
            agent_name: job.agent_name.clone(),
            provider: job.provider.clone(),
            job_id: job.job_id.clone(),
            retry_index: 0,
            health_snapshot_ref: None,
            started_at: accepted_at.to_string(),
            updated_at: accepted_at.to_string(),
            attempt_state: AttemptState::Pending,
        };
        let _ = state.attempt_store.append(&attempt);
        let event = InboundEventRecord {
            inbound_event_id: new_id("iev"),
            agent_name: job.agent_name.clone(),
            event_type: InboundEventType::TaskRequest,
            message_id: message_id.clone(),
            attempt_id: Some(attempt_id.clone()),
            payload_ref: Some(format!("job:{}", job.job_id)),
            priority: 100,
            status: InboundEventStatus::Queued,
            created_at: accepted_at.to_string(),
            started_at: None,
            finished_at: None,
        };
        let _ = state.inbound_store.append(&event);
        let _ = state.mailbox_kernel.apply_incremental_summary_update(
            &job.agent_name,
            1,
            0,
            None,
            None,
            None,
            Some(accepted_at),
        );
    }
    Some(message_id)
}

pub fn claimable_request_job_ids(state: &FacadeState, agent_name: &str) -> Vec<String> {
    let Some(event) = state
        .mailbox_kernel
        .peek_next(agent_name, Some(InboundEventType::TaskRequest))
    else {
        return Vec::new();
    };
    job_id_from_payload_ref(event.payload_ref.as_deref())
        .map(|id| vec![id])
        .unwrap_or_default()
}

pub fn record_retry_attempt(
    state: &FacadeState,
    message_id: &str,
    job: &JobRecord,
    accepted_at: &str,
) -> crate::Result<String> {
    let Some(message) = state.message_store.get_latest(message_id) else {
        return Err(crate::MailboxError::NotFound(format!(
            "message not found: {message_id}"
        )));
    };
    let retry_index = next_retry_index(state, message_id, &job.agent_name);
    let attempt_id = new_id("att");
    let attempt = AttemptRecord {
        attempt_id: attempt_id.clone(),
        message_id: message_id.to_string(),
        agent_name: job.agent_name.clone(),
        provider: job.provider.clone(),
        job_id: job.job_id.clone(),
        retry_index,
        health_snapshot_ref: None,
        started_at: accepted_at.to_string(),
        updated_at: accepted_at.to_string(),
        attempt_state: AttemptState::Pending,
    };
    let _ = state.attempt_store.append(&attempt);
    let event = InboundEventRecord {
        inbound_event_id: new_id("iev"),
        agent_name: job.agent_name.clone(),
        event_type: InboundEventType::TaskRequest,
        message_id: message_id.to_string(),
        attempt_id: Some(attempt_id.clone()),
        payload_ref: Some(format!("job:{}", job.job_id)),
        priority: 100,
        status: InboundEventStatus::Queued,
        created_at: accepted_at.to_string(),
        started_at: None,
        finished_at: None,
    };
    let _ = state.inbound_store.append(&event);
    let _ = state.mailbox_kernel.apply_incremental_summary_update(
        &job.agent_name,
        1,
        0,
        None,
        None,
        None,
        Some(accepted_at),
    );
    set_message_state(
        state,
        &message.message_id,
        MessageState::Queued,
        accepted_at,
    );
    Ok(attempt_id)
}

pub fn mark_attempt_started(state: &FacadeState, job: &JobRecord, started_at: &str) {
    let Some(attempt) = state.attempt_store.get_latest_by_job_id(&job.job_id) else {
        return;
    };
    let updated = AttemptRecord {
        started_at: Some(attempt.started_at.clone())
            .filter(|s| !s.is_empty())
            .unwrap_or_else(|| started_at.to_string()),
        updated_at: started_at.to_string(),
        attempt_state: AttemptState::Running,
        ..attempt.clone()
    };
    let _ = state.attempt_store.append(&updated);

    let inbound = resolve_inbound_for_attempt_start(state, job, &attempt.attempt_id);
    let mut mailbox_updated = false;
    if let Some(inbound) = inbound {
        if !crate::facade_state::is_terminal_event(inbound.status) {
            if inbound.status == InboundEventStatus::Delivering {
                mailbox_updated = true;
            } else {
                let _ = state.mailbox_kernel.claim(
                    &job.agent_name,
                    &inbound.inbound_event_id,
                    Some(started_at),
                );
                mailbox_updated = true;
            }
        }
    }
    set_message_state(
        state,
        &attempt.message_id,
        MessageState::Running,
        started_at,
    );
    if !mailbox_updated {
        let _ = rebuild_mailbox_summary(state, &job.agent_name, started_at);
    }
}

pub fn record_attempt_terminal(
    state: &FacadeState,
    job: &JobRecord,
    _decision: &CompletionDecision,
    finished_at: &str,
) {
    let Some(attempt) = state.attempt_store.get_latest_by_job_id(&job.job_id) else {
        return;
    };
    let updated = AttemptRecord {
        updated_at: finished_at.to_string(),
        attempt_state: attempt_state_for_status(job.status),
        ..attempt.clone()
    };
    let _ = state.attempt_store.append(&updated);

    let inbound = state
        .inbound_store
        .get_latest_for_attempt(&job.agent_name, &attempt.attempt_id);
    if let Some(inbound) = inbound {
        if !crate::facade_state::is_terminal_event(inbound.status) {
            if matches!(
                inbound.status,
                InboundEventStatus::Created | InboundEventStatus::Queued
            ) && job.status == JobStatus::Cancelled
            {
                let _ = state.mailbox_kernel.abandon(
                    &job.agent_name,
                    &inbound.inbound_event_id,
                    Some(finished_at),
                );
            } else {
                let _ = state.mailbox_kernel.consume(
                    &job.agent_name,
                    &inbound.inbound_event_id,
                    Some(finished_at),
                );
            }
        }
    } else {
        let _ = rebuild_mailbox_summary(state, &job.agent_name, finished_at);
    }
    refresh_message_state(state, &attempt.message_id, finished_at);
}

fn resolve_inbound_for_attempt_start(
    state: &FacadeState,
    job: &JobRecord,
    attempt_id: &str,
) -> Option<InboundEventRecord> {
    let is_reply_delivery = job.request.message_type.to_lowercase() == "reply_delivery"
        || job
            .provider_options
            .get("reply_delivery")
            .and_then(|v| v.as_bool())
            .unwrap_or(false);
    if is_reply_delivery {
        let inbound_event_id = job
            .provider_options
            .get("reply_delivery_inbound_event_id")
            .and_then(|v| v.as_str())
            .unwrap_or("")
            .trim();
        if !inbound_event_id.is_empty() {
            if let Some(inbound) = state
                .inbound_store
                .get_latest(&job.agent_name, inbound_event_id)
            {
                return Some(inbound);
            }
        }
    }
    state
        .inbound_store
        .get_latest_for_attempt(&job.agent_name, attempt_id)
}

pub fn record_reply(
    state: &FacadeState,
    job: &JobRecord,
    decision: &CompletionDecision,
    finished_at: &str,
    deliver_to_caller: bool,
) -> Option<String> {
    let attempt = state.attempt_store.get_latest_by_job_id(&job.job_id)?;
    let reply_text = delivered_reply_text(job, decision);
    let reply_artifact = decision
        .diagnostics
        .get("reply_artifact")
        .and_then(|v| v.as_object())
        .map(|obj| Value::Object(obj.clone()));
    let reply_id = new_id("rep");
    let reply = ReplyRecord {
        reply_id: reply_id.clone(),
        message_id: attempt.message_id.clone(),
        attempt_id: attempt.attempt_id.clone(),
        agent_name: job.agent_name.clone(),
        terminal_status: reply_status_for_job(job.status),
        reply: reply_text,
        reply_artifact,
        diagnostics: serde_json::json!({
            "reason": decision.reason,
            "status": format!("{:?}", job.status).to_lowercase(),
            "provider_turn_ref": decision.provider_turn_ref,
            "decision_diagnostics": decision.diagnostics,
            "silence_on_success": job.request.silence_on_success,
        }),
        finished_at: finished_at.to_string(),
    };
    let _ = state.reply_store.append(&reply);

    if deliver_to_caller {
        if let Some(caller_mailbox) = mailbox_actor(state, &job.request.from_actor) {
            queue_reply_delivery(
                state,
                &caller_mailbox,
                &attempt.message_id,
                &attempt.attempt_id,
                &reply_id,
                finished_at,
            );
        }
    }
    refresh_message_state(state, &attempt.message_id, finished_at);
    Some(reply_id)
}

pub fn record_notice(
    state: &FacadeState,
    job: &JobRecord,
    reply: &str,
    diagnostics: Option<Value>,
    finished_at: &str,
    terminal_status: ReplyTerminalStatus,
    deliver_to_actor: Option<&str>,
) -> Option<String> {
    let attempt = state.attempt_store.get_latest_by_job_id(&job.job_id)?;
    let reply_id = new_id("rep");
    let mut payload = diagnostics.unwrap_or_else(|| Value::Object(Default::default()));
    if let Some(obj) = payload.as_object_mut() {
        obj.entry("status".to_string())
            .or_insert_with(|| format!("{:?}", job.status).to_lowercase().into());
        obj.entry("notice".to_string())
            .or_insert_with(|| true.into());
    }
    let reply_text = reply.to_string();
    let reply = ReplyRecord {
        reply_id: reply_id.clone(),
        message_id: attempt.message_id.clone(),
        attempt_id: attempt.attempt_id.clone(),
        agent_name: job.agent_name.clone(),
        terminal_status,
        reply: reply_text,
        reply_artifact: None,
        diagnostics: payload,
        finished_at: finished_at.to_string(),
    };
    let _ = state.reply_store.append(&reply);

    let target_actor = deliver_to_actor.unwrap_or(&job.request.from_actor);
    if let Some(caller_mailbox) = mailbox_actor(state, target_actor) {
        queue_reply_delivery(
            state,
            &caller_mailbox,
            &attempt.message_id,
            &attempt.attempt_id,
            &reply_id,
            finished_at,
        );
    }
    refresh_message_state(state, &attempt.message_id, finished_at);
    Some(reply_id)
}

pub fn record_terminal(
    state: &FacadeState,
    job: &JobRecord,
    decision: &CompletionDecision,
    finished_at: &str,
    deliver_to_caller: bool,
    record_reply_enabled: bool,
) -> Option<String> {
    record_attempt_terminal(state, job, decision, finished_at);
    if !record_reply_enabled {
        return None;
    }
    record_reply(state, job, decision, finished_at, deliver_to_caller)
}

fn queue_reply_delivery(
    state: &FacadeState,
    caller_mailbox: &str,
    message_id: &str,
    attempt_id: &str,
    reply_id: &str,
    finished_at: &str,
) {
    let event = InboundEventRecord {
        inbound_event_id: new_id("iev"),
        agent_name: caller_mailbox.to_string(),
        event_type: InboundEventType::TaskReply,
        message_id: message_id.to_string(),
        attempt_id: Some(attempt_id.to_string()),
        payload_ref: Some(compose_reply_payload(reply_id, None)),
        priority: 10,
        status: InboundEventStatus::Queued,
        created_at: finished_at.to_string(),
        started_at: None,
        finished_at: None,
    };
    let _ = state.inbound_store.append(&event);
    let _ = state.mailbox_kernel.apply_incremental_summary_update(
        caller_mailbox,
        1,
        1,
        None,
        None,
        None,
        Some(finished_at),
    );
}

fn mailbox_actor(state: &FacadeState, actor: &str) -> Option<String> {
    normalize_mailbox_target(Some(actor), &state.known_mailboxes)
}
