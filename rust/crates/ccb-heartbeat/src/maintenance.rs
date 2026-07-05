use crate::time::seconds_between;
use serde_json::{Map, Value};

pub const HEALTH_HEALTHY: &str = "healthy";
pub const HEALTH_CONCERN: &str = "concern";
pub const HEALTH_FAILING: &str = "failing";
pub const HEALTH_UNKNOWN: &str = "unknown";

pub const RECOMMENDED_ACTION_NONE: &str = "none";
pub const RECOMMENDED_ACTION_ASSESS_LATER: &str = "assess_later";

const PENDING_ANCHOR_OBSERVATION_S: f64 = 30.0;

#[derive(Debug, Clone)]
pub struct MaintenanceHeartbeatEvaluation {
    pub health: String,
    pub source_kind: String,
    pub summary: Value,
    pub evidence: Vec<Value>,
}

impl MaintenanceHeartbeatEvaluation {
    pub fn recommended_action(&self) -> String {
        if self.health == HEALTH_HEALTHY {
            RECOMMENDED_ACTION_NONE.into()
        } else {
            RECOMMENDED_ACTION_ASSESS_LATER.into()
        }
    }

    pub fn needs_user(&self) -> bool {
        self.health == HEALTH_FAILING
    }
}

pub fn evaluate_project_view(payload: &Value) -> MaintenanceHeartbeatEvaluation {
    let view = mapping(payload.get("view")).unwrap_or_else(|| mapping(Some(payload)).unwrap());
    let ccbd = mapping(view.get("ccbd"));
    let agents = records(view.get("agents"));
    let comms = records(view.get("comms"));
    let ccbd_state = clean(ccbd.and_then(|m| m.get("state")));
    let observed_at = project_view_observed_at(payload, view);

    let mut health = HEALTH_HEALTHY.to_string();
    let mut evidence: Vec<Value> = Vec::new();
    let mut summary = serde_json::json!({
        "source_kind": "project_view",
        "ccbd_state": ccbd_state.as_deref(),
        "agent_count": agents.len() as i64,
        "active_agent_count": 0,
        "pending_agent_count": 0,
        "idle_agent_count": 0,
        "offline_agent_count": 0,
        "failed_agent_count": 0,
        "concern_agent_count": 0,
        "unknown_agent_count": 0,
        "comms_count": comms.len() as i64,
        "active_comms_count": 0,
        "concern_comms_count": 0,
        "failing_comms_count": 0,
        "suspicion_count": 0,
    });

    if let Some(state) = &ccbd_state {
        if state != "mounted" {
            health = max_health(&health, HEALTH_UNKNOWN);
            evidence.push(issue(
                HEALTH_UNKNOWN,
                "ccbd",
                &[("reason", &Value::String("ccbd_not_mounted".into()))],
                Some(&[("ccbd_state", &Value::String(state.clone()))]),
            ));
        }
    }

    let active_comms_by_target = active_comms_by_target(&comms);
    for agent in &agents {
        let _name = agent_name(agent);
        let state = clean(agent.get("activity_state"));
        increment_summary(&mut summary, &state, "_agent_count");

        if let Some(issue_value) = agent_issue(agent, ccbd_state.as_deref()) {
            let issue_health = clean(issue_value.get("health")).unwrap_or_default();
            health = max_health(&health, &issue_health);
            if issue_health == HEALTH_CONCERN {
                increment_summary_int(&mut summary, "concern_agent_count");
            } else if issue_health == HEALTH_UNKNOWN {
                increment_summary_int(&mut summary, "unknown_agent_count");
            }
            evidence.push(issue_value);
        }

        if let Some(suspicion) =
            agent_suspicion(agent, &active_comms_by_target, observed_at.as_deref())
        {
            increment_summary_int(&mut summary, "suspicion_count");
            let suspicion_health = clean(suspicion.get("health")).unwrap_or_default();
            health = max_health(&health, &suspicion_health);
            evidence.push(suspicion);
        }
    }

    for comm in &comms {
        let business_status = clean(comm.get("business_status"));
        if is_active_comms_status(business_status.as_deref()) {
            increment_summary_int(&mut summary, "active_comms_count");
        }
        if let Some(issue_value) = comms_issue(comm) {
            let issue_health = clean(issue_value.get("health")).unwrap_or_default();
            health = max_health(&health, &issue_health);
            if issue_health == HEALTH_CONCERN {
                increment_summary_int(&mut summary, "concern_comms_count");
            } else if issue_health == HEALTH_FAILING {
                increment_summary_int(&mut summary, "failing_comms_count");
            }
            evidence.push(issue_value);
        }
    }

    evidence.truncate(20);

    MaintenanceHeartbeatEvaluation {
        health,
        source_kind: "project_view".into(),
        summary,
        evidence,
    }
}

pub fn evaluate_ps_summary(payload: &Value, error: Option<&str>) -> MaintenanceHeartbeatEvaluation {
    let ccbd_state = clean(payload.get("ccbd_state"));
    let agents = records(payload.get("agents"));
    let mut health = HEALTH_HEALTHY.to_string();
    let mut evidence: Vec<Value> = Vec::new();
    let mut summary = serde_json::json!({
        "source_kind": "local_ps",
        "ccbd_state": ccbd_state.as_deref(),
        "agent_count": agents.len() as i64,
        "failed_agent_count": 0,
        "concern_agent_count": 0,
        "unknown_agent_count": 0,
        "fallback_error": error,
    });

    if let Some(err) = error {
        health = max_health(&health, HEALTH_UNKNOWN);
        evidence.push(issue(
            HEALTH_UNKNOWN,
            "snapshot",
            &[("reason", &Value::String("project_view_unavailable".into()))],
            Some(&[("error", &Value::String(err.into()))]),
        ));
    }

    if let Some(state) = &ccbd_state {
        if state != "mounted" {
            health = max_health(&health, HEALTH_UNKNOWN);
            evidence.push(issue(
                HEALTH_UNKNOWN,
                "ccbd",
                &[("reason", &Value::String("ccbd_not_mounted".into()))],
                Some(&[("ccbd_state", &Value::String(state.clone()))]),
            ));
        }
    }

    for agent in &agents {
        let name = agent_name(agent);
        let state = clean(agent.get("state")).or_else(|| clean(agent.get("runtime_state")));
        let binding_status = clean(agent.get("binding_status"));

        if state.as_deref() == Some("failed") {
            increment_summary_int(&mut summary, "failed_agent_count");
            health = max_health(&health, HEALTH_FAILING);
            evidence.push(issue(
                HEALTH_FAILING,
                "agent_runtime",
                &[
                    ("agent", &Value::String(name.clone())),
                    ("reason", &Value::String("runtime_failed".into())),
                    (
                        "runtime_state",
                        &Value::String(state.clone().unwrap_or_default()),
                    ),
                ],
                None,
            ));
        } else if ccbd_state.as_deref() == Some("mounted")
            && matches!(
                state.as_deref(),
                Some("degraded") | Some("stopped") | Some("stopping")
            )
        {
            increment_summary_int(&mut summary, "concern_agent_count");
            health = max_health(&health, HEALTH_CONCERN);
            let reason = format!("runtime_{}", state.as_deref().unwrap_or("unknown"));
            evidence.push(issue(
                HEALTH_CONCERN,
                "agent_runtime",
                &[
                    ("agent", &Value::String(name.clone())),
                    ("reason", &Value::String(reason)),
                    (
                        "runtime_state",
                        &Value::String(state.clone().unwrap_or_default()),
                    ),
                ],
                None,
            ));
        } else if ccbd_state.as_deref() == Some("mounted")
            && matches!(state.as_deref(), Some("") | Some("unknown"))
        {
            increment_summary_int(&mut summary, "unknown_agent_count");
            health = max_health(&health, HEALTH_UNKNOWN);
            evidence.push(issue(
                HEALTH_UNKNOWN,
                "agent_runtime",
                &[
                    ("agent", &Value::String(name.clone())),
                    ("reason", &Value::String("runtime_unknown".into())),
                ],
                None,
            ));
        }

        if ccbd_state.as_deref() == Some("mounted")
            && binding_status
                .as_deref()
                .map(|s| !s.is_empty())
                .unwrap_or(false)
            && binding_status.as_deref() != Some("bound")
        {
            increment_summary_int(&mut summary, "concern_agent_count");
            health = max_health(&health, HEALTH_CONCERN);
            evidence.push(issue(
                HEALTH_CONCERN,
                "agent_binding",
                &[
                    ("agent", &Value::String(name.clone())),
                    ("reason", &Value::String("binding_not_bound".into())),
                    (
                        "binding_status",
                        &Value::String(binding_status.clone().unwrap_or_default()),
                    ),
                ],
                None,
            ));
        }
    }

    evidence.truncate(20);

    MaintenanceHeartbeatEvaluation {
        health,
        source_kind: "local_ps".into(),
        summary,
        evidence,
    }
}

fn agent_issue(agent: &Map<String, Value>, ccbd_state: Option<&str>) -> Option<Value> {
    let name = agent_name(agent);
    let state = clean(agent.get("activity_state"));
    let reason = clean(agent.get("activity_reason"));
    let source = clean(agent.get("activity_source"));

    if state.as_deref() == Some("failed") {
        return Some(issue(
            HEALTH_FAILING,
            "agent_activity",
            &[
                ("agent", &Value::String(name)),
                (
                    "reason",
                    &Value::String(reason.unwrap_or_else(|| "activity_failed".into())),
                ),
                ("source", &Value::String(source.unwrap_or_default())),
            ],
            None,
        ));
    }

    if state.as_deref() == Some("offline") && ccbd_state == Some("mounted") {
        return Some(issue(
            HEALTH_CONCERN,
            "agent_activity",
            &[
                ("agent", &Value::String(name)),
                (
                    "reason",
                    &Value::String(reason.unwrap_or_else(|| "agent_offline".into())),
                ),
                ("source", &Value::String(source.unwrap_or_default())),
            ],
            None,
        ));
    }

    if state.as_deref() == Some("pending") && is_concern_pending_reason(reason.as_deref()) {
        return Some(issue(
            HEALTH_CONCERN,
            "agent_activity",
            &[
                ("agent", &Value::String(name)),
                ("reason", &Value::String(reason.unwrap_or_default())),
                ("source", &Value::String(source.unwrap_or_default())),
            ],
            None,
        ));
    }

    if state.as_deref() == Some("pending") && is_unknown_pending_reason(reason.as_deref()) {
        return Some(issue(
            HEALTH_UNKNOWN,
            "agent_activity",
            &[
                ("agent", &Value::String(name)),
                ("reason", &Value::String(reason.unwrap_or_default())),
                ("source", &Value::String(source.unwrap_or_default())),
            ],
            None,
        ));
    }

    if state.as_deref() == Some("pending")
        && reason.as_deref().map(|s| !s.is_empty()).unwrap_or(false)
        && !is_benign_pending_reason(reason.as_deref())
    {
        return Some(issue(
            HEALTH_UNKNOWN,
            "agent_activity",
            &[
                ("agent", &Value::String(name)),
                ("reason", &Value::String(reason.unwrap_or_default())),
                ("source", &Value::String(source.unwrap_or_default())),
            ],
            None,
        ));
    }

    if let Some(s) = &state {
        if !KNOWN_ACTIVITY_STATES.contains(&s.as_str()) {
            return Some(issue(
                HEALTH_UNKNOWN,
                "agent_activity",
                &[
                    ("agent", &Value::String(name)),
                    ("reason", &Value::String("activity_unknown".into())),
                    ("activity_state", &Value::String(s.clone())),
                ],
                None,
            ));
        }
    }

    None
}

fn comms_issue(comm: &Map<String, Value>) -> Option<Value> {
    let business_status = clean(comm.get("business_status"));
    let status = clean(comm.get("status"));
    let job_id = comm
        .get("id")
        .and_then(|v| v.as_str())
        .unwrap_or("")
        .to_string();
    let target = comm
        .get("target")
        .and_then(|v| v.as_str())
        .unwrap_or("")
        .to_string();

    if is_failing_comms_status(business_status.as_deref())
        || matches!(status.as_deref(), Some("failed") | Some("incomplete"))
    {
        let reason = business_status
            .clone()
            .or(status.clone())
            .unwrap_or_else(|| "comms_failed".into());
        return Some(issue(
            HEALTH_FAILING,
            "comms",
            &[
                ("job_id", &Value::String(job_id)),
                ("target", &Value::String(target)),
                ("reason", &Value::String(reason)),
                ("status", &Value::String(status.unwrap_or_default())),
            ],
            None,
        ));
    }

    if is_concern_comms_status(business_status.as_deref()) {
        let block_reason = comm
            .get("block_reason")
            .and_then(|v| v.as_str())
            .map(|s| s.to_string())
            .or(business_status.clone())
            .unwrap_or_else(|| "comms_blocked".into());
        return Some(issue(
            HEALTH_CONCERN,
            "comms",
            &[
                ("job_id", &Value::String(job_id)),
                ("target", &Value::String(target)),
                ("reason", &Value::String(block_reason)),
                ("status", &Value::String(status.unwrap_or_default())),
            ],
            None,
        ));
    }

    None
}

fn agent_suspicion(
    agent: &Map<String, Value>,
    active_comms_by_target: &std::collections::HashMap<String, Vec<&Map<String, Value>>>,
    observed_at: Option<&str>,
) -> Option<Value> {
    let name = agent_name(agent);
    let state = clean(agent.get("activity_state"));
    let source = clean(agent.get("activity_source"));
    let reason = clean(agent.get("activity_reason"));
    let current_job_id = agent
        .get("current_job_id")
        .and_then(|v| v.as_str())
        .unwrap_or("")
        .to_string();
    let active_comms = active_comms_by_target
        .get(&name)
        .map(|v| v.as_slice())
        .unwrap_or(&[]);
    let provider_runtime = mapping(agent.get("provider_runtime"));
    let runtime_state = provider_runtime.and_then(|m| mapping(m.get("runtime_state")));

    if provider_runtime.is_some() && current_job_id.is_empty() {
        return Some(suspicion_envelope(
            "provider_runtime_without_control_job",
            agent,
            "provider_runtime_without_control_job",
            source.as_deref().unwrap_or("provider_runtime"),
            active_comms,
            HEALTH_CONCERN,
        ));
    }

    if provider_delivery_pending_anchor(runtime_state, observed_at) {
        return Some(suspicion_envelope(
            "provider_delivery_pending_anchor",
            agent,
            "provider_delivery_pending_anchor",
            source.as_deref().unwrap_or("provider_runtime"),
            active_comms,
            HEALTH_CONCERN,
        ));
    }

    if state.as_deref() == Some("active")
        && is_provider_work_source(source.as_deref())
        && current_job_id.is_empty()
        && active_comms.is_empty()
    {
        return Some(suspicion_envelope(
            "provider_work_without_control_work",
            agent,
            "provider_work_without_control_work",
            source.as_deref().unwrap_or("unknown"),
            active_comms,
            HEALTH_CONCERN,
        ));
    }

    if matches!(state.as_deref(), Some("active") | Some("pending"))
        && (source.as_deref().map(|s| s.is_empty()).unwrap_or(true)
            || reason.as_deref().map(|s| s.is_empty()).unwrap_or(true))
    {
        return Some(suspicion_envelope(
            "degraded_activity_evidence",
            agent,
            "degraded_activity_evidence",
            source.as_deref().unwrap_or("unknown"),
            active_comms,
            HEALTH_UNKNOWN,
        ));
    }

    None
}

fn suspicion_envelope(
    condition_kind: &str,
    agent: &Map<String, Value>,
    reason: &str,
    source: &str,
    active_comms: &[&Map<String, Value>],
    health: &str,
) -> Value {
    let name = agent_name(agent);
    let current_job_id = agent
        .get("current_job_id")
        .and_then(|v| v.as_str())
        .unwrap_or("")
        .to_string();
    let active_comm_ids: Vec<Value> = active_comms
        .iter()
        .filter_map(|comm| {
            comm.get("id")
                .and_then(|v| v.as_str())
                .filter(|s| !s.is_empty())
                .map(|s| Value::String(s.to_string()))
        })
        .collect();

    issue(
        health,
        "suspicion_envelope",
        &[
            ("agent", &Value::String(name)),
            ("reason", &Value::String(reason.into())),
            ("source", &Value::String(source.into())),
            ("condition_kind", &Value::String(condition_kind.into())),
            ("confidence", &Value::String("needs_self_assessment".into())),
        ],
        Some(&[
            (
                "control_state",
                &serde_json::json!({
                    "activity_state": agent.get("activity_state"),
                    "runtime_state": agent.get("runtime_state"),
                    "runtime_health": agent.get("runtime_health"),
                    "current_job_id": if current_job_id.is_empty() { Value::Null } else { Value::String(current_job_id.clone()) },
                    "queue_depth": agent.get("queue_depth"),
                    "active_comms_count": active_comms.len() as i64,
                }),
            ),
            (
                "provider_state",
                &serde_json::json!({
                    "activity_source": agent.get("activity_source"),
                    "activity_reason": agent.get("activity_reason"),
                    "last_progress_at": agent.get("last_progress_at"),
                    "provider_runtime": provider_runtime_evidence(agent.get("provider_runtime")),
                }),
            ),
            (
                "pane_ref",
                &serde_json::json!({
                    "pane_id": agent.get("pane_id"),
                    "window": agent.get("window"),
                }),
            ),
            (
                "evidence_refs",
                &serde_json::json!({
                    "current_job_id": if current_job_id.is_empty() { Value::Null } else { Value::String(current_job_id) },
                    "active_comms_job_ids": active_comm_ids,
                }),
            ),
            (
                "allowed_actions",
                &Value::Array(
                    SELF_DIAGNOSE_ACTIONS
                        .iter()
                        .map(|s| Value::String((*s).into()))
                        .collect(),
                ),
            ),
        ]),
    )
}

fn provider_runtime_evidence(value: Option<&Value>) -> Value {
    let runtime = match value {
        Some(Value::Object(m)) => m.clone(),
        _ => return Value::Null,
    };
    let mut result = runtime.clone();
    if let Some(Value::Object(state)) = runtime.get("runtime_state") {
        result.insert("runtime_state".into(), Value::Object(state.clone()));
    }
    Value::Object(result)
}

fn provider_delivery_pending_anchor(
    runtime_state: Option<&Map<String, Value>>,
    observed_at: Option<&str>,
) -> bool {
    let runtime_state = match runtime_state {
        Some(m) => m,
        None => return false,
    };
    let delivery_state = clean(runtime_state.get("delivery_state"));
    if delivery_state.as_deref() != Some("pending_anchor") {
        return false;
    }
    if truthy(runtime_state.get("anchor_seen")) {
        return false;
    }
    let age = match age_seconds(observed_at, runtime_state.get("delivery_started_at")) {
        Some(a) => a,
        None => return false,
    };
    let timeout = float_or_none(runtime_state.get("delivery_timeout_s"));
    let mut threshold = PENDING_ANCHOR_OBSERVATION_S;
    if let Some(t) = timeout {
        if t > 0.0 {
            threshold = threshold.min(t);
        }
    }
    age >= threshold
}

fn project_view_observed_at(payload: &Value, view: &Map<String, Value>) -> Option<String> {
    if let Some(text) = view.get("generated_at").and_then(|v| v.as_str()) {
        let trimmed = text.trim();
        if !trimmed.is_empty() {
            return Some(trimmed.to_string());
        }
    }
    let cache = mapping(payload.get("cache"))?;
    cache
        .get("generated_at")
        .and_then(|v| v.as_str())
        .map(|s| s.trim().to_string())
        .filter(|s| !s.is_empty())
}

fn age_seconds(now: Option<&str>, timestamp: Option<&Value>) -> Option<f64> {
    let now = now?;
    let text = timestamp.and_then(|v| v.as_str()).unwrap_or("").trim();
    if text.is_empty() {
        return None;
    }
    seconds_between(text, now).map(|s| s.max(0.0))
}

fn truthy(value: Option<&Value>) -> bool {
    match value {
        Some(Value::Bool(b)) => *b,
        Some(v) => {
            let text = match v.as_str() {
                Some(s) => s.trim().to_lowercase(),
                None => v.to_string().trim().to_lowercase(),
            };
            matches!(text.as_str(), "1" | "true" | "yes" | "on")
        }
        None => false,
    }
}

fn float_or_none(value: Option<&Value>) -> Option<f64> {
    match value {
        Some(Value::Number(n)) => n.as_f64(),
        Some(Value::String(s)) => s.trim().parse().ok(),
        _ => None,
    }
}

fn active_comms_by_target<'a>(
    comms: &'a [&'a Map<String, Value>],
) -> std::collections::HashMap<String, Vec<&'a Map<String, Value>>> {
    let mut grouped: std::collections::HashMap<String, Vec<&Map<String, Value>>> =
        std::collections::HashMap::new();
    for comm in comms {
        let target = comm
            .get("target")
            .and_then(|v| v.as_str())
            .unwrap_or("")
            .trim()
            .to_string();
        if target.is_empty() {
            continue;
        }
        let business_status = clean(comm.get("business_status"));
        if !is_active_comms_status(business_status.as_deref()) {
            continue;
        }
        grouped.entry(target).or_default().push(*comm);
    }
    grouped
}

fn issue(
    health: &str,
    kind: &str,
    required: &[(&str, &Value)],
    optional: Option<&[(&str, &Value)]>,
) -> Value {
    let mut record = Map::new();
    record.insert("health".into(), Value::String(health.into()));
    record.insert("kind".into(), Value::String(kind.into()));
    for (key, value) in required {
        record.insert((*key).into(), (*value).clone());
    }
    if let Some(fields) = optional {
        for (key, value) in fields {
            match *value {
                Value::Null => {}
                Value::String(s) if s.is_empty() => {}
                _ => {
                    record.insert((*key).into(), (*value).clone());
                }
            }
        }
    }
    Value::Object(record)
}

fn records(value: Option<&Value>) -> Vec<&Map<String, Value>> {
    match value {
        Some(Value::Array(items)) => items.iter().filter_map(|item| item.as_object()).collect(),
        _ => Vec::new(),
    }
}

fn mapping(value: Option<&Value>) -> Option<&Map<String, Value>> {
    value.and_then(|v| v.as_object())
}

fn clean(value: Option<&Value>) -> Option<String> {
    value
        .map(|v| match v {
            Value::String(s) => s.trim().to_lowercase(),
            Value::Null => String::new(),
            _ => v.to_string().trim().to_lowercase(),
        })
        .filter(|s| !s.is_empty())
}

fn agent_name(agent: &Map<String, Value>) -> String {
    agent
        .get("name")
        .or_else(|| agent.get("agent_name"))
        .and_then(|v| v.as_str())
        .unwrap_or("")
        .trim()
        .to_string()
}

fn max_health(current: &str, candidate: &str) -> String {
    let rank_current = health_rank(current);
    let rank_candidate = health_rank(candidate);
    if rank_candidate > rank_current {
        candidate.to_string()
    } else {
        current.to_string()
    }
}

fn health_rank(health: &str) -> i32 {
    match health {
        HEALTH_HEALTHY => 0,
        HEALTH_UNKNOWN => 1,
        HEALTH_CONCERN => 2,
        HEALTH_FAILING => 3,
        _ => 0,
    }
}

fn increment_summary(summary: &mut Value, state: &Option<String>, suffix: &str) {
    let key = match state.as_deref() {
        Some("active") => format!("active{suffix}"),
        Some("pending") => format!("pending{suffix}"),
        Some("idle") => format!("idle{suffix}"),
        Some("offline") => format!("offline{suffix}"),
        Some("failed") => format!("failed{suffix}"),
        _ => return,
    };
    increment_summary_int(summary, &key);
}

fn increment_summary_int(summary: &mut Value, key: &str) {
    if let Some(obj) = summary.as_object_mut() {
        let current = obj.get(key).and_then(|v| v.as_i64()).unwrap_or(0);
        obj.insert(key.into(), Value::Number((current + 1).into()));
    }
}

fn is_active_comms_status(status: Option<&str>) -> bool {
    matches!(
        status,
        Some("delivering") | Some("replying") | Some("sending")
    )
}

fn is_failing_comms_status(status: Option<&str>) -> bool {
    matches!(
        status,
        Some("delivery_failed") | Some("failed") | Some("incomplete")
    )
}

fn is_concern_comms_status(status: Option<&str>) -> bool {
    matches!(status, Some("blocked"))
}

fn is_provider_work_source(source: Option<&str>) -> bool {
    matches!(
        source,
        Some("codex_hook")
            | Some("claude_hook")
            | Some("gemini_hook")
            | Some("opencode_hook")
            | Some("provider_activity")
            | Some("provider_pane")
    )
}

fn is_benign_pending_reason(reason: Option<&str>) -> bool {
    matches!(
        reason,
        Some("job_queued") | Some("reconcile_active") | Some("pane_missing_recovering")
    )
}

fn is_concern_pending_reason(reason: Option<&str>) -> bool {
    matches!(
        reason,
        Some("callback_child_completed")
            | Some("callback_waiting_child")
            | Some("job_running_stale")
            | Some("provider_prompt_idle")
            | Some("provider_prompt_input_stuck")
            | Some("provider_waiting_for_user")
    )
}

fn is_unknown_pending_reason(reason: Option<&str>) -> bool {
    matches!(reason, Some("health_unknown") | Some("runtime_unknown"))
}

const KNOWN_ACTIVITY_STATES: &[&str] = &["active", "failed", "idle", "offline", "pending"];
const SELF_DIAGNOSE_ACTIONS: &[&str] = &[
    "diagnose",
    "capture_pane_readonly",
    "inspect_logs_readonly",
    "schedule_followup",
    "ask_user",
];
