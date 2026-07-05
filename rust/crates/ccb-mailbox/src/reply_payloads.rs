const REPLY_PREFIX: &str = "reply:";
const DELIVERY_PREFIX: &str = "delivery:";

/// Compose a reply payload reference from a reply id and optional delivery job id.
pub fn compose_reply_payload(reply_id: &str, delivery_job_id: Option<&str>) -> String {
    let normalized_reply_id = reply_id.trim();
    if normalized_reply_id.is_empty() {
        panic!("reply_id cannot be empty");
    }
    let mut parts = vec![format!("{REPLY_PREFIX}{normalized_reply_id}")];
    if let Some(job_id) = delivery_job_id {
        let normalized = job_id.trim();
        if !normalized.is_empty() {
            parts.push(format!("{DELIVERY_PREFIX}{normalized}"));
        }
    }
    parts.join(" ")
}

/// Extract reply id from a payload reference.
pub fn reply_id_from_payload(payload_ref: Option<&str>) -> Option<String> {
    value_for_prefix(payload_ref, REPLY_PREFIX)
}

/// Extract delivery job id from a payload reference.
pub fn delivery_job_id_from_payload(payload_ref: Option<&str>) -> Option<String> {
    value_for_prefix(payload_ref, DELIVERY_PREFIX)
}

fn value_for_prefix(payload_ref: Option<&str>, prefix: &str) -> Option<String> {
    let text = payload_ref.unwrap_or("").trim();
    if text.is_empty() {
        return None;
    }
    for token in text.replace(';', " ").split_whitespace() {
        if let Some(value) = token.strip_prefix(prefix) {
            let trimmed = value.trim();
            if !trimmed.is_empty() {
                return Some(trimmed.to_string());
            }
        }
    }
    None
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_compose_and_parse() {
        let payload = compose_reply_payload("rep_1", Some("job_2"));
        assert_eq!(reply_id_from_payload(Some(&payload)), Some("rep_1".into()));
        assert_eq!(
            delivery_job_id_from_payload(Some(&payload)),
            Some("job_2".into())
        );
    }
}
