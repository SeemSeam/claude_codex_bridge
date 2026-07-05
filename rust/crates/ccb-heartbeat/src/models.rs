use serde::{Deserialize, Serialize};

pub const SCHEMA_VERSION: u32 = 1;
pub const HEARTBEAT_STATE_RECORD_TYPE: &str = "heartbeat_state";
pub const MAINTENANCE_HEARTBEAT_SCHEDULE_RECORD_TYPE: &str = "maintenance_heartbeat_schedule";
pub const MAINTENANCE_HEARTBEAT_STATUS_RECORD_TYPE: &str = "maintenance_heartbeat_status";
pub const MAINTENANCE_HEARTBEAT_ACTIVATION_RECORD_TYPE: &str = "maintenance_heartbeat_activation";
pub const MAINTENANCE_HEARTBEAT_RUNNER_RECORD_TYPE: &str = "maintenance_heartbeat_runner";

// Python-public aliases for the maintenance heartbeat record types.
pub const SCHEDULE_RECORD_TYPE: &str = MAINTENANCE_HEARTBEAT_SCHEDULE_RECORD_TYPE;
pub const STATUS_RECORD_TYPE: &str = MAINTENANCE_HEARTBEAT_STATUS_RECORD_TYPE;
pub const ACTIVATION_RECORD_TYPE: &str = MAINTENANCE_HEARTBEAT_ACTIVATION_RECORD_TYPE;
pub const RUNNER_RECORD_TYPE: &str = MAINTENANCE_HEARTBEAT_RUNNER_RECORD_TYPE;

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "lowercase")]
pub enum HeartbeatAction {
    Idle,
    Reset,
    Enter,
    Repeat,
}

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq)]
pub struct HeartbeatPolicy {
    pub silence_start_after_s: f64,
    pub repeat_interval_s: f64,
    pub max_notice_count: Option<u32>,
}

impl HeartbeatPolicy {
    pub fn new(
        silence_start_after_s: f64,
        repeat_interval_s: f64,
        max_notice_count: Option<u32>,
    ) -> Result<Self, String> {
        if silence_start_after_s < 0.0 {
            return Err("silence_start_after_s cannot be negative".into());
        }
        if repeat_interval_s <= 0.0 {
            return Err("repeat_interval_s must be positive".into());
        }
        if max_notice_count == Some(0) {
            return Err("max_notice_count must be positive when set".into());
        }
        Ok(Self {
            silence_start_after_s,
            repeat_interval_s,
            max_notice_count,
        })
    }
}

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq)]
pub struct HeartbeatState {
    pub subject_kind: String,
    pub subject_id: String,
    pub owner: String,
    pub last_progress_at: String,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub last_notice_at: Option<String>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub heartbeat_started_at: Option<String>,
    #[serde(default)]
    pub notice_count: u32,
    pub updated_at: String,
}

impl HeartbeatState {
    #[allow(clippy::too_many_arguments)]
    pub fn new(
        subject_kind: impl Into<String>,
        subject_id: impl Into<String>,
        owner: impl Into<String>,
        last_progress_at: impl Into<String>,
        last_notice_at: Option<String>,
        heartbeat_started_at: Option<String>,
        notice_count: u32,
        updated_at: impl Into<String>,
    ) -> crate::Result<Self> {
        let subject_kind = subject_kind.into();
        let subject_id = subject_id.into();
        let owner = owner.into();
        let last_progress_at = last_progress_at.into();
        let updated_at = updated_at.into();

        require_non_empty("subject_kind", &subject_kind)?;
        require_non_empty("subject_id", &subject_id)?;
        require_non_empty("owner", &owner)?;
        require_non_empty("last_progress_at", &last_progress_at)?;
        require_non_empty("updated_at", &updated_at)?;

        Ok(Self {
            subject_kind,
            subject_id,
            owner,
            last_progress_at,
            last_notice_at,
            heartbeat_started_at,
            notice_count,
            updated_at,
        })
    }

    pub fn to_record(&self) -> serde_json::Value {
        serde_json::json!({
            "schema_version": SCHEMA_VERSION,
            "record_type": HEARTBEAT_STATE_RECORD_TYPE,
            "subject_kind": self.subject_kind,
            "subject_id": self.subject_id,
            "owner": self.owner,
            "last_progress_at": self.last_progress_at,
            "last_notice_at": self.last_notice_at,
            "heartbeat_started_at": self.heartbeat_started_at,
            "notice_count": self.notice_count,
            "updated_at": self.updated_at,
        })
    }

    pub fn from_record(record: serde_json::Value) -> Result<Self, String> {
        validate_header(&record, HEARTBEAT_STATE_RECORD_TYPE)?;
        let mut state: HeartbeatState =
            serde_json::from_value(record).map_err(|e| format!("invalid heartbeat state: {e}"))?;
        state.last_notice_at = normalize_optional_text(state.last_notice_at);
        state.heartbeat_started_at = normalize_optional_text(state.heartbeat_started_at);
        Ok(state)
    }
}

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq)]
pub struct HeartbeatDecision {
    pub action: HeartbeatAction,
    pub subject_kind: String,
    pub subject_id: String,
    pub owner: String,
    pub last_progress_at: String,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub last_notice_at: Option<String>,
    pub silence_seconds: f64,
    pub notice_count: u32,
}

impl HeartbeatDecision {
    pub fn notice_due(&self) -> bool {
        matches!(
            self.action,
            HeartbeatAction::Enter | HeartbeatAction::Repeat
        )
    }
}

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq)]
pub struct MaintenanceHeartbeatSchedule {
    pub project_id: String,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub next_run_at: Option<String>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub reason: Option<String>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub updated_at: Option<String>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub updated_by: Option<String>,
}

impl MaintenanceHeartbeatSchedule {
    pub fn new(
        project_id: impl Into<String>,
        next_run_at: Option<String>,
        reason: Option<String>,
        updated_at: Option<String>,
        updated_by: Option<String>,
    ) -> crate::Result<Self> {
        let project_id = project_id.into();
        require_non_empty("project_id", &project_id)?;

        Ok(Self {
            project_id,
            next_run_at,
            reason,
            updated_at,
            updated_by,
        })
    }

    pub fn to_record(&self) -> serde_json::Value {
        serde_json::json!({
            "schema_version": SCHEMA_VERSION,
            "record_type": MAINTENANCE_HEARTBEAT_SCHEDULE_RECORD_TYPE,
            "project_id": self.project_id,
            "next_run_at": self.next_run_at,
            "reason": self.reason,
            "updated_at": self.updated_at,
            "updated_by": self.updated_by,
        })
    }

    pub fn from_record(record: serde_json::Value) -> Result<Self, String> {
        validate_header(&record, MAINTENANCE_HEARTBEAT_SCHEDULE_RECORD_TYPE)?;
        let mut value: MaintenanceHeartbeatSchedule = serde_json::from_value(record)
            .map_err(|e| format!("invalid maintenance schedule: {e}"))?;
        value.next_run_at = normalize_optional_text(value.next_run_at);
        value.reason = normalize_optional_text(value.reason);
        value.updated_at = normalize_optional_text(value.updated_at);
        value.updated_by = normalize_optional_text(value.updated_by);
        Ok(value)
    }
}

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq)]
pub struct MaintenanceHeartbeatStatus {
    pub project_id: String,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub last_tick_status: Option<String>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub last_tick_at: Option<String>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub last_ok_at: Option<String>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub last_error: Option<String>,
    #[serde(default)]
    pub unknown_streak: u32,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub updated_at: Option<String>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub source_kind: Option<String>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub recommended_action: Option<String>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub next_heartbeat_after_s: Option<u32>,
    #[serde(default)]
    pub needs_user: bool,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub summary: Option<serde_json::Value>,
    #[serde(default, skip_serializing_if = "Vec::is_empty")]
    pub evidence: Vec<serde_json::Value>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub last_activation_status: Option<String>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub last_activation_id: Option<String>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub last_activation_job_id: Option<String>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub last_activation_target: Option<String>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub last_activation_dedup_key: Option<String>,
}

impl MaintenanceHeartbeatStatus {
    #[allow(clippy::too_many_arguments)]
    pub fn new(
        project_id: impl Into<String>,
        last_tick_status: Option<String>,
        last_tick_at: Option<String>,
        last_ok_at: Option<String>,
        last_error: Option<String>,
        unknown_streak: u32,
        updated_at: Option<String>,
        source_kind: Option<String>,
        recommended_action: Option<String>,
        next_heartbeat_after_s: Option<u32>,
        needs_user: bool,
        summary: Option<serde_json::Value>,
        evidence: Vec<serde_json::Value>,
        last_activation_status: Option<String>,
        last_activation_id: Option<String>,
        last_activation_job_id: Option<String>,
        last_activation_target: Option<String>,
        last_activation_dedup_key: Option<String>,
    ) -> crate::Result<Self> {
        let project_id = project_id.into();
        require_non_empty("project_id", &project_id)?;

        if let Some(secs) = next_heartbeat_after_s {
            if secs == 0 {
                return Err(crate::HeartbeatError::Validation(
                    "next_heartbeat_after_s must be positive".into(),
                ));
            }
        }

        if let Some(value) = &summary {
            if !value.is_object() {
                return Err(crate::HeartbeatError::Validation(
                    "summary must be an object".into(),
                ));
            }
        }

        for (i, item) in evidence.iter().enumerate() {
            if !item.is_object() {
                return Err(crate::HeartbeatError::Validation(format!(
                    "evidence[{i}] must be an object"
                )));
            }
        }

        Ok(Self {
            project_id,
            last_tick_status,
            last_tick_at,
            last_ok_at,
            last_error,
            unknown_streak,
            updated_at,
            source_kind,
            recommended_action,
            next_heartbeat_after_s,
            needs_user,
            summary,
            evidence,
            last_activation_status,
            last_activation_id,
            last_activation_job_id,
            last_activation_target,
            last_activation_dedup_key,
        })
    }

    pub fn to_record(&self) -> serde_json::Value {
        serde_json::json!({
            "schema_version": SCHEMA_VERSION,
            "record_type": MAINTENANCE_HEARTBEAT_STATUS_RECORD_TYPE,
            "project_id": self.project_id,
            "last_tick_status": self.last_tick_status,
            "last_tick_at": self.last_tick_at,
            "last_ok_at": self.last_ok_at,
            "last_error": self.last_error,
            "unknown_streak": self.unknown_streak,
            "updated_at": self.updated_at,
            "source_kind": self.source_kind,
            "recommended_action": self.recommended_action,
            "next_heartbeat_after_s": self.next_heartbeat_after_s,
            "needs_user": self.needs_user,
            "summary": self.summary,
            "evidence": self.evidence,
            "last_activation_status": self.last_activation_status,
            "last_activation_id": self.last_activation_id,
            "last_activation_job_id": self.last_activation_job_id,
            "last_activation_target": self.last_activation_target,
            "last_activation_dedup_key": self.last_activation_dedup_key,
        })
    }

    pub fn from_record(record: serde_json::Value) -> Result<Self, String> {
        validate_header(&record, MAINTENANCE_HEARTBEAT_STATUS_RECORD_TYPE)?;
        let mut value: MaintenanceHeartbeatStatus = serde_json::from_value(record)
            .map_err(|e| format!("invalid maintenance status: {e}"))?;
        value.last_tick_status = normalize_optional_text(value.last_tick_status);
        value.last_tick_at = normalize_optional_text(value.last_tick_at);
        value.last_ok_at = normalize_optional_text(value.last_ok_at);
        value.last_error = normalize_optional_text(value.last_error);
        value.updated_at = normalize_optional_text(value.updated_at);
        value.source_kind = normalize_optional_text(value.source_kind);
        value.recommended_action = normalize_optional_text(value.recommended_action);
        value.last_activation_status = normalize_optional_text(value.last_activation_status);
        value.last_activation_id = normalize_optional_text(value.last_activation_id);
        value.last_activation_job_id = normalize_optional_text(value.last_activation_job_id);
        value.last_activation_target = normalize_optional_text(value.last_activation_target);
        value.last_activation_dedup_key = normalize_optional_text(value.last_activation_dedup_key);
        Ok(value)
    }
}

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq)]
pub struct MaintenanceHeartbeatRunner {
    pub project_id: String,
    pub runner_id: String,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub pid: Option<u32>,
    #[serde(default = "default_state")]
    pub state: String,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub source: Option<String>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub started_at: Option<String>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub last_seen_at: Option<String>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub last_wake_at: Option<String>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub last_tick_at: Option<String>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub last_tick_status: Option<String>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub observed_next_run_at: Option<String>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub sleep_until: Option<String>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub exit_reason: Option<String>,
}

fn default_state() -> String {
    "unknown".into()
}

impl MaintenanceHeartbeatRunner {
    #[allow(clippy::too_many_arguments)]
    pub fn new(
        project_id: impl Into<String>,
        runner_id: impl Into<String>,
        pid: Option<u32>,
        state: impl Into<String>,
        source: Option<String>,
        started_at: Option<String>,
        last_seen_at: Option<String>,
        last_wake_at: Option<String>,
        last_tick_at: Option<String>,
        last_tick_status: Option<String>,
        observed_next_run_at: Option<String>,
        sleep_until: Option<String>,
        exit_reason: Option<String>,
    ) -> crate::Result<Self> {
        let project_id = project_id.into();
        let runner_id = runner_id.into();
        let state = state.into();

        require_non_empty("project_id", &project_id)?;
        require_non_empty("runner_id", &runner_id)?;
        require_non_empty("state", &state)?;

        if let Some(pid) = pid {
            if pid == 0 {
                return Err(crate::HeartbeatError::Validation(
                    "pid must be positive".into(),
                ));
            }
        }

        Ok(Self {
            project_id,
            runner_id,
            pid,
            state,
            source,
            started_at,
            last_seen_at,
            last_wake_at,
            last_tick_at,
            last_tick_status,
            observed_next_run_at,
            sleep_until,
            exit_reason,
        })
    }

    pub fn to_record(&self) -> serde_json::Value {
        serde_json::json!({
            "schema_version": SCHEMA_VERSION,
            "record_type": MAINTENANCE_HEARTBEAT_RUNNER_RECORD_TYPE,
            "project_id": self.project_id,
            "runner_id": self.runner_id,
            "pid": self.pid,
            "state": self.state,
            "source": self.source,
            "started_at": self.started_at,
            "last_seen_at": self.last_seen_at,
            "last_wake_at": self.last_wake_at,
            "last_tick_at": self.last_tick_at,
            "last_tick_status": self.last_tick_status,
            "observed_next_run_at": self.observed_next_run_at,
            "sleep_until": self.sleep_until,
            "exit_reason": self.exit_reason,
        })
    }

    pub fn from_record(record: serde_json::Value) -> Result<Self, String> {
        validate_header(&record, MAINTENANCE_HEARTBEAT_RUNNER_RECORD_TYPE)?;
        let mut value: MaintenanceHeartbeatRunner = serde_json::from_value(record)
            .map_err(|e| format!("invalid maintenance runner: {e}"))?;
        value.source = normalize_optional_text(value.source);
        value.started_at = normalize_optional_text(value.started_at);
        value.last_seen_at = normalize_optional_text(value.last_seen_at);
        value.last_wake_at = normalize_optional_text(value.last_wake_at);
        value.last_tick_at = normalize_optional_text(value.last_tick_at);
        value.last_tick_status = normalize_optional_text(value.last_tick_status);
        value.observed_next_run_at = normalize_optional_text(value.observed_next_run_at);
        value.sleep_until = normalize_optional_text(value.sleep_until);
        value.exit_reason = normalize_optional_text(value.exit_reason);
        Ok(value)
    }
}

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq)]
pub struct MaintenanceHeartbeatActivation {
    pub project_id: String,
    pub activation_id: String,
    pub status: String,
    pub condition_kind: String,
    pub trigger_kind: String,
    pub source: String,
    pub observed_at: String,
    pub target_agent: String,
    pub delivery_mode: String,
    pub payload_kind: String,
    pub dedup_key: String,
    pub reason: String,
    #[serde(default = "default_created_by")]
    pub created_by: String,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub not_before: Option<String>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub expires_at: Option<String>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub job_id: Option<String>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub submitted_at: Option<String>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub suppressed_reason: Option<String>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub error: Option<String>,
    #[serde(default)]
    pub repeat_count: u32,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub payload_summary: Option<serde_json::Value>,
    #[serde(default, skip_serializing_if = "Vec::is_empty")]
    pub evidence: Vec<serde_json::Value>,
}

fn default_created_by() -> String {
    "maintenance-heartbeat".into()
}

impl MaintenanceHeartbeatActivation {
    #[allow(clippy::too_many_arguments)]
    pub fn new(
        project_id: impl Into<String>,
        activation_id: impl Into<String>,
        status: impl Into<String>,
        condition_kind: impl Into<String>,
        trigger_kind: impl Into<String>,
        source: impl Into<String>,
        observed_at: impl Into<String>,
        target_agent: impl Into<String>,
        delivery_mode: impl Into<String>,
        payload_kind: impl Into<String>,
        dedup_key: impl Into<String>,
        reason: impl Into<String>,
        created_by: impl Into<String>,
        not_before: Option<String>,
        expires_at: Option<String>,
        job_id: Option<String>,
        submitted_at: Option<String>,
        suppressed_reason: Option<String>,
        error: Option<String>,
        repeat_count: u32,
        payload_summary: Option<serde_json::Value>,
        evidence: Vec<serde_json::Value>,
    ) -> crate::Result<Self> {
        let project_id = project_id.into();
        let activation_id = activation_id.into();
        let status = status.into();
        let condition_kind = condition_kind.into();
        let trigger_kind = trigger_kind.into();
        let source = source.into();
        let observed_at = observed_at.into();
        let target_agent = target_agent.into();
        let delivery_mode = delivery_mode.into();
        let payload_kind = payload_kind.into();
        let dedup_key = dedup_key.into();
        let reason = reason.into();
        let created_by = created_by.into();

        require_non_empty("project_id", &project_id)?;
        require_non_empty("activation_id", &activation_id)?;
        require_non_empty("status", &status)?;
        require_non_empty("condition_kind", &condition_kind)?;
        require_non_empty("trigger_kind", &trigger_kind)?;
        require_non_empty("source", &source)?;
        require_non_empty("observed_at", &observed_at)?;
        require_non_empty("target_agent", &target_agent)?;
        require_non_empty("delivery_mode", &delivery_mode)?;
        require_non_empty("payload_kind", &payload_kind)?;
        require_non_empty("dedup_key", &dedup_key)?;
        require_non_empty("reason", &reason)?;
        require_non_empty("created_by", &created_by)?;

        if let Some(value) = &payload_summary {
            if !value.is_object() {
                return Err(crate::HeartbeatError::Validation(
                    "payload_summary must be an object".into(),
                ));
            }
        }

        for (i, item) in evidence.iter().enumerate() {
            if !item.is_object() {
                return Err(crate::HeartbeatError::Validation(format!(
                    "evidence[{i}] must be an object"
                )));
            }
        }

        Ok(Self {
            project_id,
            activation_id,
            status,
            condition_kind,
            trigger_kind,
            source,
            observed_at,
            target_agent,
            delivery_mode,
            payload_kind,
            dedup_key,
            reason,
            created_by,
            not_before,
            expires_at,
            job_id,
            submitted_at,
            suppressed_reason,
            error,
            repeat_count,
            payload_summary,
            evidence,
        })
    }

    pub fn to_record(&self) -> serde_json::Value {
        serde_json::json!({
            "schema_version": SCHEMA_VERSION,
            "record_type": MAINTENANCE_HEARTBEAT_ACTIVATION_RECORD_TYPE,
            "project_id": self.project_id,
            "activation_id": self.activation_id,
            "status": self.status,
            "condition_kind": self.condition_kind,
            "trigger_kind": self.trigger_kind,
            "source": self.source,
            "observed_at": self.observed_at,
            "target_agent": self.target_agent,
            "delivery_mode": self.delivery_mode,
            "payload_kind": self.payload_kind,
            "dedup_key": self.dedup_key,
            "reason": self.reason,
            "created_by": self.created_by,
            "not_before": self.not_before,
            "expires_at": self.expires_at,
            "job_id": self.job_id,
            "submitted_at": self.submitted_at,
            "suppressed_reason": self.suppressed_reason,
            "error": self.error,
            "repeat_count": self.repeat_count,
            "payload_summary": self.payload_summary,
            "evidence": self.evidence,
        })
    }

    pub fn from_record(record: serde_json::Value) -> Result<Self, String> {
        validate_header(&record, MAINTENANCE_HEARTBEAT_ACTIVATION_RECORD_TYPE)?;
        let mut value: MaintenanceHeartbeatActivation = serde_json::from_value(record)
            .map_err(|e| format!("invalid maintenance activation: {e}"))?;
        value.not_before = normalize_optional_text(value.not_before);
        value.expires_at = normalize_optional_text(value.expires_at);
        value.job_id = normalize_optional_text(value.job_id);
        value.submitted_at = normalize_optional_text(value.submitted_at);
        value.suppressed_reason = normalize_optional_text(value.suppressed_reason);
        value.error = normalize_optional_text(value.error);
        Ok(value)
    }
}

fn validate_header(record: &serde_json::Value, record_type: &str) -> Result<(), String> {
    let schema_version = record
        .get("schema_version")
        .and_then(|v| v.as_u64())
        .ok_or("missing schema_version")? as u32;
    if schema_version != SCHEMA_VERSION {
        return Err(format!("schema_version must be {SCHEMA_VERSION}"));
    }
    let actual = record
        .get("record_type")
        .and_then(|v| v.as_str())
        .ok_or("missing record_type")?;
    if actual != record_type {
        return Err(format!("record_type must be '{record_type}'"));
    }
    Ok(())
}

fn normalize_optional_text(value: Option<String>) -> Option<String> {
    value.and_then(|s| {
        let trimmed = s.trim();
        if trimmed.is_empty() {
            None
        } else {
            Some(trimmed.to_string())
        }
    })
}

fn require_non_empty(name: &str, value: &str) -> crate::Result<()> {
    if value.trim().is_empty() {
        return Err(crate::HeartbeatError::Validation(format!(
            "{name} cannot be empty"
        )));
    }
    Ok(())
}
