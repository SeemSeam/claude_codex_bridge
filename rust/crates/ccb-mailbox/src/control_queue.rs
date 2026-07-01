use serde_json::Value;

use crate::facade_state::{is_terminal_attempt, is_terminal_event, ControlState};
use crate::models::{
    AttemptRecord, AttemptState, InboundEventRecord, InboundEventStatus, InboundEventType,
    ReplyRecord,
};
use crate::reply_metadata::{
    reply_heartbeat_silence_seconds, reply_last_progress_at, reply_notice, reply_notice_kind,
};
use crate::reply_payloads::{delivery_job_id_from_payload, reply_id_from_payload};
use crate::targets::normalize_mailbox_target;

pub fn derive_mailbox_state(has_active: bool, queue_depth: u32) -> String {
    if has_active {
        "delivering".to_string()
    } else if queue_depth > 0 {
        "blocked".to_string()
    } else {
        "idle".to_string()
    }
}

pub fn require_mailbox_target<'a>(
    state: &'a ControlState,
    agent_name: &'a str,
) -> crate::Result<String> {
    normalize_mailbox_target(Some(agent_name), &state.known_mailboxes).ok_or_else(|| {
        crate::MailboxError::NotFound(format!("unknown mailbox target: {agent_name}"))
    })
}

pub fn summary_targets(state: &ControlState) -> Vec<String> {
    state
        .config
        .as_ref()
        .and_then(|c| c.get("agents"))
        .and_then(|v| v.as_object())
        .map(|agents| {
            let mut names: Vec<String> = agents.keys().cloned().collect();
            names.sort();
            names
        })
        .unwrap_or_default()
}

pub fn mailbox_has_activity(
    state: &ControlState,
    agent_name: &str,
    mailbox: Option<&Value>,
) -> bool {
    let record = mailbox.cloned().or_else(|| {
        state
            .mailbox_store
            .load(agent_name)
            .ok()
            .flatten()
            .map(|m| serde_json::to_value(m).unwrap_or(Value::Null))
    });
    let Some(record) = record else {
        return false;
    };
    record
        .get("active_inbound_event_id")
        .and_then(|v| v.as_str())
        .is_some_and(|s| !s.is_empty())
        || record
            .get("queue_depth")
            .and_then(|v| v.as_u64())
            .map_or(0, |n| n as u32)
            > 0
        || record
            .get("pending_reply_count")
            .and_then(|v| v.as_u64())
            .map_or(0, |n| n as u32)
            > 0
}

pub fn preview_text(value: &str, limit: usize) -> String {
    let text = value
        .replace('\r', "")
        .replace('\n', "\\n")
        .trim()
        .to_string();
    if text.chars().count() <= limit {
        text
    } else {
        let truncated: String = text.chars().take(limit).collect();
        format!("{}...", truncated.trim_end())
    }
}

pub fn queue_summary(state: &ControlState, target: &str, detail: Option<bool>) -> Value {
    let normalized = target.trim().to_lowercase();
    let normalized = if normalized.is_empty() {
        "all".to_string()
    } else {
        normalized
    };
    if normalized != "all" {
        require_mailbox_target(state, &normalized)
            .unwrap_or_else(|e| panic!("{e}"));
        let agent_summary = if detail == Some(true) {
            agent_queue_detail(state, &normalized)
        } else {
            agent_queue_summary(state, &normalized)
        };
        return serde_json::json!({
            "target": normalized,
            "agent": agent_summary,
        });
    }
    let agent_names = summary_targets(state);
    let agent_summaries: Vec<Value> = agent_names
        .iter()
        .map(|name| agent_queue_summary(state, name))
        .collect();
    serde_json::json!({
        "target": "all",
        "agent_count": agent_summaries.len(),
        "queued_agent_count": agent_summaries.iter().filter(|a| a.get("queue_depth").and_then(|v| v.as_u64()).unwrap_or(0) > 0).count(),
        "active_agent_count": agent_summaries.iter().filter(|a| a.get("active_inbound_event_id").and_then(|v| v.as_str()).is_some_and(|s| !s.is_empty())).count(),
        "total_queue_depth": agent_summaries.iter().filter_map(|a| a.get("queue_depth").and_then(|v| v.as_u64())).sum::<u64>(),
        "total_pending_reply_count": agent_summaries.iter().filter_map(|a| a.get("pending_reply_count").and_then(|v| v.as_u64())).sum::<u64>(),
        "agents": agent_summaries,
    })
}

pub fn agent_queue(state: &ControlState, agent_name: &str) -> Value {
    agent_queue_detail(state, agent_name)
}

pub fn agent_queue_detail(state: &ControlState, agent_name: &str) -> Value {
    let normalized =
        require_mailbox_target(state, agent_name).unwrap_or_else(|_| agent_name.to_lowercase());
    let summary_read = read_mailbox_summary(state, &normalized);
    let mailbox = summary_read
        .as_object()
        .and_then(|o| o.get("mailbox").cloned());
    let events = pending_events(state, &normalized);
    let summary = agent_queue_summary(state, &normalized);
    let active = active_event(state, &normalized, mailbox.as_ref(), &events);
    let (
        queue_depth,
        pending_reply_count,
        mailbox_state,
        active_inbound_event_id,
        last_started,
        last_finished,
    ) = if mailbox.is_none() {
        let queue_depth = events.len() as u32;
        let pending_reply_count = events
            .iter()
            .filter(|e| e.get("event_type").and_then(|v| v.as_str()) == Some("task_reply"))
            .count() as u32;
        let mailbox_state = derive_mailbox_state(active.is_some(), queue_depth);
        let active_id = active.as_ref().and_then(|e| {
            e.get("inbound_event_id")
                .and_then(|v| v.as_str())
                .map(|s| s.to_string())
        });
        let (ls, lf) = last_event_timestamps(&events);
        (
            queue_depth,
            pending_reply_count,
            mailbox_state,
            active_id,
            ls,
            lf,
        )
    } else {
        (
            summary
                .get("queue_depth")
                .and_then(|v| v.as_u64())
                .unwrap_or(0) as u32,
            summary
                .get("pending_reply_count")
                .and_then(|v| v.as_u64())
                .unwrap_or(0) as u32,
            summary
                .get("mailbox_state")
                .and_then(|v| v.as_str())
                .unwrap_or("idle")
                .to_string(),
            summary
                .get("active_inbound_event_id")
                .and_then(|v| v.as_str())
                .map(|s| s.to_string()),
            summary
                .get("last_inbound_started_at")
                .and_then(|v| v.as_str())
                .map(|s| s.to_string()),
            summary
                .get("last_inbound_finished_at")
                .and_then(|v| v.as_str())
                .map(|s| s.to_string()),
        )
    };
    serde_json::json!({
        "agent_name": normalized,
        "mailbox_id": summary.get("mailbox_id"),
        "mailbox_state": mailbox_state,
        "lease_version": summary.get("lease_version"),
        "queue_depth": queue_depth,
        "pending_reply_count": pending_reply_count,
        "active_inbound_event_id": active_inbound_event_id,
        "active": active,
        "last_inbound_started_at": last_started,
        "last_inbound_finished_at": last_finished,
        "summary_status": summary.get("summary_status"),
        "summary_error": summary.get("summary_error"),
        "queued_events": events,
    })
}

pub fn agent_queue_summary(state: &ControlState, agent_name: &str) -> Value {
    let normalized =
        require_mailbox_target(state, agent_name).unwrap_or_else(|_| agent_name.to_lowercase());
    let summary_read = read_mailbox_summary(state, &normalized);
    let mailbox = summary_read
        .as_object()
        .and_then(|o| o.get("mailbox").cloned());
    if mailbox.is_none() {
        return serde_json::json!({
            "agent_name": normalized,
            "mailbox_id": format!("mbx_{}", normalized),
            "mailbox_state": None::<String>,
            "lease_version": 0,
            "queue_depth": 0,
            "pending_reply_count": 0,
            "active_inbound_event_id": None::<String>,
            "last_inbound_started_at": None::<String>,
            "last_inbound_finished_at": None::<String>,
            "summary_status": summary_read.get("status"),
            "summary_error": summary_read.get("error"),
        });
    }
    let mailbox = mailbox.unwrap();
    serde_json::json!({
        "agent_name": normalized,
        "mailbox_id": mailbox.get("mailbox_id"),
        "mailbox_state": mailbox.get("mailbox_state").and_then(|v| v.as_str()),
        "lease_version": mailbox.get("lease_version").and_then(|v| v.as_u64()).unwrap_or(0),
        "queue_depth": mailbox.get("queue_depth").and_then(|v| v.as_u64()).unwrap_or(0),
        "pending_reply_count": mailbox.get("pending_reply_count").and_then(|v| v.as_u64()).unwrap_or(0),
        "active_inbound_event_id": mailbox.get("active_inbound_event_id").and_then(|v| v.as_str()),
        "last_inbound_started_at": mailbox.get("last_inbound_started_at").and_then(|v| v.as_str()),
        "last_inbound_finished_at": mailbox.get("last_inbound_finished_at").and_then(|v| v.as_str()),
        "summary_status": summary_read.get("status"),
        "summary_error": summary_read.get("error"),
    })
}

fn read_mailbox_summary(state: &ControlState, agent_name: &str) -> Value {
    match state.mailbox_store.load(agent_name) {
        Ok(Some(mailbox)) => serde_json::json!({
            "mailbox": serde_json::to_value(mailbox).unwrap_or(Value::Null),
            "status": "ok",
            "error": None::<String>,
        }),
        Ok(None) => serde_json::json!({
            "mailbox": None::<Value>,
            "status": "missing",
            "error": None::<String>,
        }),
        Err(e) => serde_json::json!({
            "mailbox": None::<Value>,
            "status": "error",
            "error": e.to_string(),
        }),
    }
}

fn active_event(
    state: &ControlState,
    agent_name: &str,
    mailbox: Option<&Value>,
    events: &[Value],
) -> Option<Value> {
    let active_id = mailbox.and_then(|m| m.get("active_inbound_event_id").and_then(|v| v.as_str()));
    if let Some(id) = active_id {
        if let Some(record) = state.inbound_store.get_latest(agent_name, id) {
            return Some(event_payload(state, &record));
        }
    }
    active_event_from_events(events)
}

fn active_event_from_events(events: &[Value]) -> Option<Value> {
    for event in events {
        if event.get("status").and_then(|v| v.as_str()) == Some("delivering") {
            return Some(event.clone());
        }
    }
    events.first().cloned()
}

fn event_payload(state: &ControlState, record: &InboundEventRecord) -> Value {
    let attempt = record
        .attempt_id
        .as_deref()
        .and_then(|id| state.attempt_store.get_latest(id));
    let message = state.message_store.get_latest(&record.message_id);
    let mut item = serde_json::json!({
        "position": 1,
        "inbound_event_id": record.inbound_event_id,
        "event_type": record.event_type.to_string(),
        "status": format!("{:?}", record.status).to_lowercase(),
        "priority": record.priority,
        "message_id": record.message_id,
        "message_state": message.as_ref().map(|m| format!("{:?}", m.message_state).to_lowercase()),
        "attempt_id": record.attempt_id,
        "attempt_state": attempt.as_ref().map(|a| format!("{:?}", a.attempt_state).to_lowercase()),
        "job_id": attempt.as_ref().map(|a| a.job_id.clone()),
        "created_at": record.created_at,
        "started_at": record.started_at,
        "finished_at": record.finished_at,
    });
    if record.event_type != InboundEventType::TaskReply {
        return item;
    }
    let Some(reply_id) = reply_id_from_payload(record.payload_ref.as_deref()) else {
        return item;
    };
    let Some(reply) = state.reply_store.get_latest(&reply_id) else {
        return item;
    };
    if let Some(obj) = item.as_object_mut() {
        obj.insert("reply_id".to_string(), reply.reply_id.clone().into());
        obj.insert("source_actor".to_string(), reply.agent_name.clone().into());
        obj.insert(
            "reply_terminal_status".to_string(),
            format!("{:?}", reply.terminal_status).to_lowercase().into(),
        );
        obj.insert("reply_notice".to_string(), reply_notice(&reply).into());
        obj.insert(
            "reply_notice_kind".to_string(),
            reply_notice_kind(&reply).unwrap_or_default().into(),
        );
        obj.insert(
            "reply_finished_at".to_string(),
            reply.finished_at.clone().into(),
        );
        obj.insert(
            "reply_last_progress_at".to_string(),
            reply_last_progress_at(&reply).unwrap_or_default().into(),
        );
        obj.insert(
            "reply_heartbeat_silence_seconds".to_string(),
            reply_heartbeat_silence_seconds(&reply)
                .unwrap_or_default()
                .into(),
        );
    }
    item
}

fn last_event_timestamps(events: &[Value]) -> (Option<String>, Option<String>) {
    let mut last_started = None;
    let mut last_finished = None;
    for event in events {
        if let Some(started_at) = event.get("started_at").and_then(|v| v.as_str()) {
            if last_started
                .as_deref()
                .is_none_or(|current| started_at > current)
            {
                last_started = Some(started_at.to_string());
            }
        }
        if let Some(finished_at) = event.get("finished_at").and_then(|v| v.as_str()) {
            if last_finished
                .as_deref()
                .is_none_or(|current| finished_at > current)
            {
                last_finished = Some(finished_at.to_string());
            }
        }
    }
    (last_started, last_finished)
}

pub fn inbox(state: &ControlState, agent_name: &str, detail: Option<bool>) -> Value {
    let normalized =
        require_mailbox_target(state, agent_name).unwrap_or_else(|_| agent_name.to_lowercase());
    let mailbox_payload = agent_queue_summary(state, &normalized);
    let summary_read = read_mailbox_summary(state, &normalized);
    let mailbox = summary_read
        .as_object()
        .and_then(|o| o.get("mailbox").cloned());
    if detail != Some(true) {
        return serde_json::json!({
            "target": normalized,
            "summary_status": summary_read.get("status"),
            "summary_error": summary_read.get("error"),
            "agent": mailbox_payload,
            "item_count": mailbox_payload.get("queue_depth").and_then(|v| v.as_u64()).unwrap_or(0),
            "head": enrich_mailbox_head(state, &normalized, mailbox_summary_head_payload(mailbox.as_ref())),
            "items": Vec::<Value>::new(),
        });
    }
    let records = pending_event_records(state, &normalized);
    let items: Vec<Value> = records
        .iter()
        .enumerate()
        .map(|(index, record)| inbox_item_summary(state, record, index + 1))
        .collect();
    let head = enrich_mailbox_head(
        state,
        &normalized,
        mailbox_summary_head_payload(mailbox.as_ref()),
    )
    .or_else(|| items.first().cloned());
    serde_json::json!({
        "target": normalized,
        "summary_status": summary_read.get("status"),
        "summary_error": summary_read.get("error"),
        "agent": mailbox_payload,
        "item_count": items.len(),
        "head": head,
        "items": items,
    })
}

pub fn mailbox_head(state: &ControlState, agent_name: &str) -> Value {
    let normalized =
        require_mailbox_target(state, agent_name).unwrap_or_else(|_| agent_name.to_lowercase());
    let summary_read = read_mailbox_summary(state, &normalized);
    let mailbox = summary_read
        .as_object()
        .and_then(|o| o.get("mailbox").cloned());
    serde_json::json!({
        "target": normalized,
        "summary_status": summary_read.get("status"),
        "summary_error": summary_read.get("error"),
        "head": enrich_mailbox_head(state, &normalized, mailbox_summary_head_payload(mailbox.as_ref())),
    })
}

fn mailbox_summary_head_payload(mailbox: Option<&Value>) -> Option<Value> {
    let mailbox = mailbox?;
    let obj = mailbox.as_object()?;
    let inbound_event_id = obj.get("head_inbound_event_id").and_then(|v| v.as_str())?;
    Some(serde_json::json!({
        "inbound_event_id": inbound_event_id,
        "event_type": obj.get("head_event_type").and_then(|v| v.as_str()),
        "status": obj.get("head_status").and_then(|v| v.as_str()),
        "message_id": obj.get("head_message_id").and_then(|v| v.as_str()),
        "attempt_id": obj.get("head_attempt_id").and_then(|v| v.as_str()),
        "payload_ref": obj.get("head_payload_ref").and_then(|v| v.as_str()),
    }))
}

fn enrich_mailbox_head(
    state: &ControlState,
    agent_name: &str,
    head: Option<Value>,
) -> Option<Value> {
    let head = head?;
    let obj = head.as_object()?;
    if obj
        .get("event_type")
        .and_then(|v| v.as_str())
        .unwrap_or("")
        .to_lowercase()
        != "task_reply"
    {
        return Some(head);
    }
    let attempt_id = obj.get("attempt_id").and_then(|v| v.as_str());
    let attempt = attempt_id.and_then(|id| state.attempt_store.get_latest(id));
    let mut reply_id = reply_id_from_payload(obj.get("payload_ref").and_then(|v| v.as_str()));
    if reply_id.is_none() {
        if let Some(inbound_event_id) = obj.get("inbound_event_id").and_then(|v| v.as_str()) {
            if let Some(record) = state.inbound_store.get_latest(agent_name, inbound_event_id) {
                reply_id = reply_id_from_payload(record.payload_ref.as_deref());
            }
        }
    }
    let Some(reply) = reply_id.and_then(|id| state.reply_store.get_latest(&id)) else {
        return Some(head);
    };
    let mut enriched = obj.clone();
    enriched.insert("reply_id".to_string(), reply.reply_id.clone().into());
    enriched.insert("source_actor".to_string(), reply.agent_name.clone().into());
    enriched.insert(
        "reply_terminal_status".to_string(),
        format!("{:?}", reply.terminal_status).to_lowercase().into(),
    );
    enriched.insert("reply_notice".to_string(), reply_notice(&reply).into());
    enriched.insert(
        "reply_notice_kind".to_string(),
        reply_notice_kind(&reply).unwrap_or_default().into(),
    );
    enriched.insert(
        "job_id".to_string(),
        attempt
            .as_ref()
            .map(|a| a.job_id.clone())
            .unwrap_or_default()
            .into(),
    );
    enriched.insert(
        "reply_finished_at".to_string(),
        reply.finished_at.clone().into(),
    );
    enriched.insert(
        "reply_last_progress_at".to_string(),
        reply_last_progress_at(&reply).unwrap_or_default().into(),
    );
    enriched.insert(
        "reply_heartbeat_silence_seconds".to_string(),
        reply_heartbeat_silence_seconds(&reply)
            .unwrap_or_default()
            .into(),
    );
    enriched.insert("reply".to_string(), reply.reply.clone().into());
    Some(Value::Object(enriched))
}

pub fn pending_event_records(state: &ControlState, agent_name: &str) -> Vec<InboundEventRecord> {
    discard_stale_head_events(state, agent_name);
    let mut latest_by_id: std::collections::HashMap<String, InboundEventRecord> =
        std::collections::HashMap::new();
    let mut order: Vec<String> = Vec::new();
    for record in state.inbound_store.list_agent(agent_name) {
        if !latest_by_id.contains_key(&record.inbound_event_id) {
            order.push(record.inbound_event_id.clone());
        }
        latest_by_id.insert(record.inbound_event_id.clone(), record);
    }
    order
        .into_iter()
        .filter_map(|id| latest_by_id.remove(&id))
        .filter(|record| !is_terminal_event(record.status) && event_is_live(state, record))
        .collect()
}

fn discard_stale_head_events(state: &ControlState, agent_name: &str) {
    loop {
        let head = state.mailbox_kernel.head_pending_event(agent_name);
        let Some(head) = head else {
            return;
        };
        if event_is_live(state, &head) {
            return;
        }
        let timestamp = state.now();
        let _ = state
            .mailbox_kernel
            .abandon(agent_name, &head.inbound_event_id, Some(&timestamp));
    }
}

fn event_is_live(state: &ControlState, event: &InboundEventRecord) -> bool {
    let Some(_message) = state.message_store.get_latest(&event.message_id) else {
        return false;
    };
    let attempt = event
        .attempt_id
        .as_deref()
        .and_then(|id| state.attempt_store.get_latest(id));
    if event.attempt_id.is_some() && attempt.is_none() {
        return false;
    }
    if event.event_type == InboundEventType::TaskRequest {
        if let Some(attempt) = attempt {
            return !is_terminal_attempt(attempt.attempt_state);
        }
        return false;
    }
    if event.event_type == InboundEventType::TaskReply {
        return reply_for_event(state, event).is_some();
    }
    true
}

pub fn reply_for_event(state: &ControlState, event: &InboundEventRecord) -> Option<ReplyRecord> {
    if event.event_type != InboundEventType::TaskReply {
        return None;
    }
    let reply_id = reply_id_from_payload(event.payload_ref.as_deref())?;
    state.reply_store.get_latest(&reply_id)
}

pub fn pending_events(state: &ControlState, agent_name: &str) -> Vec<Value> {
    pending_event_records(state, agent_name)
        .iter()
        .enumerate()
        .map(|(position, record)| {
            let attempt = record
                .attempt_id
                .as_deref()
                .and_then(|id| state.attempt_store.get_latest(id));
            let message = state.message_store.get_latest(&record.message_id);
            let replies = state.reply_store.list_message(&record.message_id);
            serde_json::json!({
                "position": position + 1,
                "inbound_event_id": record.inbound_event_id,
                "event_type": record.event_type.to_string(),
                "status": format!("{:?}", record.status).to_lowercase(),
                "priority": record.priority,
                "message_id": record.message_id,
                "message_state": message.as_ref().map(|m| format!("{:?}", m.message_state).to_lowercase()),
                "attempt_id": record.attempt_id,
                "attempt_state": attempt.as_ref().map(|a| format!("{:?}", a.attempt_state).to_lowercase()),
                "job_id": attempt.as_ref().map(|a| a.job_id.clone()),
                "reply_count": replies.len(),
                "created_at": record.created_at,
                "started_at": record.started_at,
                "finished_at": record.finished_at,
            })
        })
        .collect()
}

pub fn inbox_item_summary(
    state: &ControlState,
    event: &InboundEventRecord,
    position: usize,
) -> Value {
    let attempt = event
        .attempt_id
        .as_deref()
        .and_then(|id| state.attempt_store.get_latest(id));
    let message = state.message_store.get_latest(&event.message_id);
    let reply = reply_for_event(state, event);
    let mut item = serde_json::json!({
        "position": position,
        "inbound_event_id": event.inbound_event_id,
        "event_type": event.event_type.to_string(),
        "status": format!("{:?}", event.status).to_lowercase(),
        "priority": event.priority,
        "message_id": event.message_id,
        "message_state": message.as_ref().map(|m| format!("{:?}", m.message_state).to_lowercase()),
        "attempt_id": event.attempt_id,
        "attempt_state": attempt.as_ref().map(|a| format!("{:?}", a.attempt_state).to_lowercase()),
        "job_id": attempt.as_ref().map(|a| a.job_id.clone()),
        "source_actor": reply.as_ref().map(|r| r.agent_name.clone()).or_else(|| message.as_ref().map(|m| m.from_actor.clone())),
        "created_at": event.created_at,
        "started_at": event.started_at,
        "finished_at": event.finished_at,
    });
    if let Some(reply) = reply {
        if let Some(obj) = item.as_object_mut() {
            obj.insert("reply_id".to_string(), reply.reply_id.clone().into());
            obj.insert(
                "reply_terminal_status".to_string(),
                format!("{:?}", reply.terminal_status).to_lowercase().into(),
            );
            obj.insert(
                "reply_finished_at".to_string(),
                reply.finished_at.clone().into(),
            );
            obj.insert(
                "reply_preview".to_string(),
                preview_text(&reply.reply, 120).into(),
            );
            obj.insert("reply_notice".to_string(), reply_notice(&reply).into());
            obj.insert(
                "reply_notice_kind".to_string(),
                reply_notice_kind(&reply).unwrap_or_default().into(),
            );
            obj.insert(
                "reply_last_progress_at".to_string(),
                reply_last_progress_at(&reply).unwrap_or_default().into(),
            );
            obj.insert(
                "reply_heartbeat_silence_seconds".to_string(),
                reply_heartbeat_silence_seconds(&reply)
                    .unwrap_or_default()
                    .into(),
            );
            if position == 1 {
                obj.insert("reply".to_string(), reply.reply.clone().into());
                if let Some(artifact) = reply.reply_artifact {
                    obj.insert("reply_artifact".to_string(), artifact);
                }
            }
        }
    }
    item
}

pub fn ack_reply(state: &ControlState, agent_name: &str, inbound_event_id: Option<&str>) -> Value {
    let normalized =
        require_mailbox_target(state, agent_name).unwrap_or_else(|_| agent_name.to_lowercase());
    let head = head_event(state, &normalized, inbound_event_id);
    if head.event_type == InboundEventType::TaskRequest {
        return ack_terminal_task_request(state, &normalized, &head);
    }
    let reply = reply_for_event(state, &head);
    if reply.is_none() {
        panic!(
            "reply record missing for inbound event: {}",
            head.inbound_event_id
        );
    }
    let reply = reply.unwrap();
    let attempt = head
        .attempt_id
        .as_deref()
        .and_then(|id| state.attempt_store.get_latest(id));
    let timestamp = state.now();
    let consumed = state.mailbox_kernel.ack_reply(
        &normalized,
        &head.inbound_event_id,
        Some(&timestamp),
        Some(&timestamp),
    );
    if consumed.is_none() {
        panic!("failed to ack reply event: {}", head.inbound_event_id);
    }
    let consumed = consumed.unwrap();
    let mailbox_payload = agent_queue(state, &normalized);
    let next_records = pending_event_records(state, &normalized);
    let mut next_head = state.mailbox_kernel.head_pending_event(&normalized);
    if next_head.is_none()
        || next_records
            .iter()
            .all(|record| record.inbound_event_id != next_head.as_ref().unwrap().inbound_event_id)
    {
        next_head = next_records.into_iter().next();
    }
    ack_payload(
        &normalized,
        &consumed,
        attempt.as_ref(),
        &reply,
        next_head.as_ref(),
        &mailbox_payload,
    )
}

fn head_event(
    state: &ControlState,
    agent_name: &str,
    inbound_event_id: Option<&str>,
) -> InboundEventRecord {
    let direct_head = state.mailbox_kernel.head_pending_event(agent_name);
    let requested_event_id = inbound_event_id.map(|s| s.to_string()).unwrap_or_else(|| {
        direct_head
            .as_ref()
            .map(|h| h.inbound_event_id.clone())
            .unwrap_or_default()
    });
    if let Some(ref direct_head) = direct_head {
        if direct_head.inbound_event_id == requested_event_id {
            if direct_head.event_type == InboundEventType::TaskReply {
                return validate_reply_head(direct_head);
            }
            if direct_head.event_type == InboundEventType::TaskRequest {
                return direct_head.clone();
            }
        }
    }
    let records = pending_event_records(state, agent_name);
    let head = records
        .first()
        .cloned()
        .unwrap_or_else(|| panic!("inbox is empty for agent: {agent_name}"));
    let requested_event_id = if requested_event_id.is_empty() {
        head.inbound_event_id.clone()
    } else {
        requested_event_id
    };
    if head.inbound_event_id != requested_event_id {
        panic!("ack requires head event: {}", head.inbound_event_id);
    }
    validate_reply_head(&head)
}

fn validate_reply_head(head: &InboundEventRecord) -> InboundEventRecord {
    if head.event_type != InboundEventType::TaskReply {
        panic!(
            "ack only supports task_reply or terminal task_request head events; found: {:?}",
            head.event_type
        );
    }
    if let Some(job_id) = delivery_job_id_from_payload(head.payload_ref.as_deref()) {
        panic!("ack is not allowed after automatic reply delivery has been scheduled: {job_id}");
    }
    head.clone()
}

fn ack_terminal_task_request(
    state: &ControlState,
    agent_name: &str,
    head: &InboundEventRecord,
) -> Value {
    let attempt = head
        .attempt_id
        .as_deref()
        .and_then(|id| state.attempt_store.get_latest(id));
    if let Some(ref attempt) = attempt {
        if !is_terminal_attempt(attempt.attempt_state) {
            panic!(
                "ack only supports terminal task_request head events; found attempt_state={:?}",
                attempt.attempt_state
            );
        }
    }
    let timestamp = state.now();
    let consumed = if attempt
        .as_ref()
        .is_some_and(|a| a.attempt_state == AttemptState::Cancelled)
        && matches!(
            head.status,
            InboundEventStatus::Created | InboundEventStatus::Queued
        ) {
        state
            .mailbox_kernel
            .abandon(agent_name, &head.inbound_event_id, Some(&timestamp))
    } else {
        state
            .mailbox_kernel
            .consume(agent_name, &head.inbound_event_id, Some(&timestamp))
    };
    if consumed.is_none() {
        panic!(
            "failed to ack task_request event: {}",
            head.inbound_event_id
        );
    }
    let consumed = consumed.unwrap();
    let mailbox_payload = agent_queue(state, agent_name);
    let next_records = pending_event_records(state, agent_name);
    let mut next_head = state.mailbox_kernel.head_pending_event(agent_name);
    if next_head.is_none()
        || next_records
            .iter()
            .all(|record| record.inbound_event_id != next_head.as_ref().unwrap().inbound_event_id)
    {
        next_head = next_records.into_iter().next();
    }
    serde_json::json!({
        "target": agent_name,
        "agent_name": agent_name,
        "acknowledged_inbound_event_id": consumed.inbound_event_id,
        "acknowledged_event_type": consumed.event_type.to_string(),
        "message_id": consumed.message_id,
        "attempt_id": consumed.attempt_id,
        "job_id": attempt.as_ref().map(|a| a.job_id.clone()),
        "attempt_state": attempt.as_ref().map(|a| format!("{:?}", a.attempt_state).to_lowercase()),
        "next_inbound_event_id": next_head.as_ref().map(|h| h.inbound_event_id.clone()),
        "next_event_type": next_head.as_ref().map(|h| h.event_type.to_string()),
        "mailbox": mailbox_payload,
        "reply": "",
    })
}

fn ack_payload(
    agent_name: &str,
    consumed: &InboundEventRecord,
    attempt: Option<&AttemptRecord>,
    reply: &ReplyRecord,
    next_head: Option<&InboundEventRecord>,
    mailbox_payload: &Value,
) -> Value {
    serde_json::json!({
        "target": agent_name,
        "agent_name": agent_name,
        "acknowledged_inbound_event_id": consumed.inbound_event_id,
        "acknowledged_event_type": consumed.event_type.to_string(),
        "message_id": consumed.message_id,
        "attempt_id": consumed.attempt_id,
        "job_id": attempt.map(|a| a.job_id.clone()),
        "reply_id": reply.reply_id,
        "reply_from_agent": reply.agent_name,
        "reply_terminal_status": format!("{:?}", reply.terminal_status).to_lowercase(),
        "reply_finished_at": reply.finished_at,
        "reply_notice": reply_notice(reply),
        "reply_notice_kind": reply_notice_kind(reply).unwrap_or_default(),
        "reply_last_progress_at": reply_last_progress_at(reply).unwrap_or_default(),
        "reply_heartbeat_silence_seconds": reply_heartbeat_silence_seconds(reply).unwrap_or_default(),
        "next_inbound_event_id": next_head.map(|h| h.inbound_event_id.clone()),
        "next_event_type": next_head.map(|h| h.event_type.to_string()),
        "mailbox": mailbox_payload,
        "reply": reply.reply,
    })
}

#[cfg(test)]
mod tests {
    use super::preview_text;

    #[test]
    fn test_preview_text_ascii() {
        assert_eq!(preview_text("hello world", 20), "hello world");
        assert_eq!(preview_text("hello world", 5), "hello...");
    }

    #[test]
    fn test_preview_text_multibyte() {
        let text = "────────────────────────────────────────";
        let out = preview_text(text, 10);
        assert!(out.ends_with("..."));
        assert!(out.chars().count() <= 13); // 10 chars + "..."
                                            // Must not panic and must be valid UTF-8.
        assert!(!out.is_empty());
    }
}
