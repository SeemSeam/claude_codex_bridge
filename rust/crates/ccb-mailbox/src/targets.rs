use serde_json::Value;

pub const CMD_ACTOR: &str = "cmd";
pub const USER_ACTOR: &str = "user";
pub const MAINTENANCE_HEARTBEAT_ACTOR: &str = "maintenance-heartbeat";

pub fn non_mailbox_actors() -> Vec<String> {
    vec![
        USER_ACTOR.into(),
        "system".into(),
        "manual".into(),
        "email".into(),
        MAINTENANCE_HEARTBEAT_ACTOR.into(),
    ]
}

pub fn non_agent_actors() -> Vec<String> {
    let mut actors = non_mailbox_actors();
    actors.push(CMD_ACTOR.into());
    actors
}

/// Normalize an actor name. Non-mailbox actors are returned as-is; otherwise lowercased.
pub fn normalize_actor_name(actor: Option<&str>) -> crate::Result<String> {
    let normalized = actor.unwrap_or("").trim().to_lowercase();
    if normalized.is_empty() {
        return Err(crate::MailboxError::NotFound(
            "actor cannot be empty".into(),
        ));
    }
    if normalized == CMD_ACTOR || non_mailbox_actors().contains(&normalized) {
        return Ok(normalized);
    }
    Ok(normalize_agent_name(&normalized))
}

/// Normalize a mailbox owner name. Errors for non-agent actors.
pub fn normalize_mailbox_owner_name(actor: Option<&str>) -> crate::Result<String> {
    let normalized = normalize_actor_name(actor)?;
    if non_agent_actors().contains(&normalized) {
        return Err(crate::MailboxError::NotFound(format!(
            "actor {normalized:?} does not own a mailbox"
        )));
    }
    Ok(normalized)
}

/// Normalize an agent name.
pub fn normalize_agent_name(name: &str) -> String {
    name.trim().to_lowercase()
}

/// Extract known mailbox targets from a config value.
pub fn known_mailbox_targets(config: Option<&Value>) -> Vec<String> {
    let Some(config) = config else {
        return Vec::new();
    };
    let Some(agents) = config.get("agents").and_then(|v| v.as_object()) else {
        return Vec::new();
    };
    let mut targets: Vec<String> = agents.keys().map(|k| normalize_agent_name(k)).collect();
    targets.sort();
    targets.dedup();
    targets
}

/// Normalize a mailbox target against a known set.
pub fn normalize_mailbox_target(actor: Option<&str>, known_targets: &[String]) -> Option<String> {
    let normalized = actor.unwrap_or("").trim().to_lowercase();
    if normalized.is_empty() {
        return None;
    }
    let mailbox_name = normalize_mailbox_owner_name(Some(&normalized)).ok()?;
    if known_targets.contains(&mailbox_name) {
        Some(mailbox_name)
    } else {
        None
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_normalize_agent_name() {
        assert_eq!(normalize_agent_name(" Claude "), "claude");
    }

    #[test]
    fn test_normalize_actor_name_user() {
        assert_eq!(normalize_actor_name(Some("user")).unwrap(), "user");
    }

    #[test]
    fn test_known_mailbox_targets() {
        let config = serde_json::json!({
            "agents": { "Claude": {}, "Codex": {} }
        });
        let targets = known_mailbox_targets(Some(&config));
        assert_eq!(targets, vec!["claude", "codex"]);
    }
}
