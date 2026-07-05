use serde_json::Value;

use crate::models::ReplyRecord;

/// Determine whether a reply is a notice.
pub fn reply_notice(reply: &ReplyRecord) -> bool {
    let diagnostics = reply_diagnostics(reply);
    if diagnostics
        .get("notice")
        .and_then(|v| v.as_bool())
        .unwrap_or(false)
    {
        return true;
    }
    reply_notice_kind(reply).is_some()
}

/// Get the notice kind if any.
pub fn reply_notice_kind(reply: &ReplyRecord) -> Option<String> {
    let diagnostics = reply_diagnostics(reply);
    let value = diagnostics
        .get("notice_kind")
        .and_then(|v| v.as_str())
        .unwrap_or("")
        .trim()
        .to_lowercase();
    if value.is_empty() {
        None
    } else {
        Some(value)
    }
}

/// Get the last progress timestamp from reply diagnostics.
pub fn reply_last_progress_at(reply: &ReplyRecord) -> Option<String> {
    let diagnostics = reply_diagnostics(reply);
    let value = diagnostics
        .get("last_progress_at")
        .and_then(|v| v.as_str())
        .unwrap_or("")
        .trim();
    if value.is_empty() {
        None
    } else {
        Some(value.to_string())
    }
}

/// Get heartbeat silence seconds from reply diagnostics.
pub fn reply_heartbeat_silence_seconds(reply: &ReplyRecord) -> Option<f64> {
    let diagnostics = reply_diagnostics(reply);
    diagnostics
        .get("heartbeat_silence_seconds")
        .and_then(|v| v.as_f64())
}

fn reply_diagnostics(reply: &ReplyRecord) -> Value {
    if let Some(obj) = reply.diagnostics.as_object() {
        return Value::Object(obj.clone());
    }
    Value::Object(Default::default())
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::models::ReplyTerminalStatus;

    fn make_reply(diagnostics: Value) -> ReplyRecord {
        ReplyRecord {
            reply_id: "r1".into(),
            message_id: "m1".into(),
            attempt_id: "a1".into(),
            agent_name: "claude".into(),
            terminal_status: ReplyTerminalStatus::Completed,
            reply: "ok".into(),
            reply_artifact: None,
            diagnostics,
            finished_at: "2025-01-01T00:00:00Z".into(),
        }
    }

    #[test]
    fn test_reply_notice() {
        let reply = make_reply(serde_json::json!({ "notice": true }));
        assert!(reply_notice(&reply));
    }
}
