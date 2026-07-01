use crate::models::{HeartbeatAction, HeartbeatDecision, HeartbeatPolicy, HeartbeatState};
use crate::time::seconds_between;

pub fn evaluate_heartbeat(
    policy: &HeartbeatPolicy,
    subject_kind: &str,
    subject_id: &str,
    owner: &str,
    observed_last_progress_at: &str,
    now: &str,
    state: Option<&HeartbeatState>,
) -> (HeartbeatState, HeartbeatDecision) {
    let progress_at = progress_timestamp(observed_last_progress_at, now);
    let current = current_state(state, subject_kind, subject_id, owner, &progress_at, now);
    let mut base = current.clone();
    base.subject_kind = subject_kind.to_string();
    base.subject_id = subject_id.to_string();
    base.owner = owner.to_string();

    if progress_advanced(&progress_at, &current.last_progress_at) && heartbeat_active(&current) {
        let next_state = reset_state(&base, &progress_at, now);
        let decision = decision(HeartbeatAction::Reset, &next_state, 0.0);
        return (next_state, decision);
    }

    base.last_progress_at = progress_at.clone();
    let silence_seconds = silence_seconds(&progress_at, now);

    if silence_seconds < policy.silence_start_after_s {
        return (
            base.clone(),
            decision(HeartbeatAction::Idle, &base, silence_seconds),
        );
    }

    if should_enter_heartbeat(&base) {
        let next_state = enter_state(&base, now);
        return (
            next_state.clone(),
            decision(HeartbeatAction::Enter, &next_state, silence_seconds),
        );
    }

    if notice_limit_reached(&base, policy) {
        return (
            base.clone(),
            decision(HeartbeatAction::Idle, &base, silence_seconds),
        );
    }

    if repeat_interval_not_elapsed(&base, policy, now) {
        return (
            base.clone(),
            decision(HeartbeatAction::Idle, &base, silence_seconds),
        );
    }

    let next_state = repeat_state(&base, now);
    (
        next_state.clone(),
        decision(HeartbeatAction::Repeat, &next_state, silence_seconds),
    )
}

fn progress_timestamp(observed_last_progress_at: &str, now: &str) -> String {
    let trimmed = observed_last_progress_at.trim();
    if trimmed.is_empty() {
        let trimmed_now = now.trim();
        if trimmed_now.is_empty() {
            panic!("observed_last_progress_at cannot be empty");
        }
        return trimmed_now.to_string();
    }
    trimmed.to_string()
}

fn current_state(
    state: Option<&HeartbeatState>,
    subject_kind: &str,
    subject_id: &str,
    owner: &str,
    progress_at: &str,
    now: &str,
) -> HeartbeatState {
    match state {
        Some(s) => s.clone(),
        None => HeartbeatState {
            subject_kind: subject_kind.to_string(),
            subject_id: subject_id.to_string(),
            owner: owner.to_string(),
            last_progress_at: progress_at.to_string(),
            last_notice_at: None,
            heartbeat_started_at: None,
            notice_count: 0,
            updated_at: now.to_string(),
        },
    }
}

fn heartbeat_active(state: &HeartbeatState) -> bool {
    state.notice_count > 0 || state.last_notice_at.is_some()
}

fn reset_state(base: &HeartbeatState, progress_at: &str, now: &str) -> HeartbeatState {
    HeartbeatState {
        subject_kind: base.subject_kind.clone(),
        subject_id: base.subject_id.clone(),
        owner: base.owner.clone(),
        last_progress_at: progress_at.to_string(),
        last_notice_at: None,
        heartbeat_started_at: None,
        notice_count: 0,
        updated_at: now.to_string(),
    }
}

fn enter_state(base: &HeartbeatState, now: &str) -> HeartbeatState {
    HeartbeatState {
        subject_kind: base.subject_kind.clone(),
        subject_id: base.subject_id.clone(),
        owner: base.owner.clone(),
        last_progress_at: base.last_progress_at.clone(),
        last_notice_at: Some(now.to_string()),
        heartbeat_started_at: base.heartbeat_started_at.clone().or(Some(now.to_string())),
        notice_count: 1,
        updated_at: now.to_string(),
    }
}

fn repeat_state(base: &HeartbeatState, now: &str) -> HeartbeatState {
    HeartbeatState {
        subject_kind: base.subject_kind.clone(),
        subject_id: base.subject_id.clone(),
        owner: base.owner.clone(),
        last_progress_at: base.last_progress_at.clone(),
        last_notice_at: Some(now.to_string()),
        heartbeat_started_at: base.heartbeat_started_at.clone().or(Some(now.to_string())),
        notice_count: base.notice_count + 1,
        updated_at: now.to_string(),
    }
}

fn silence_seconds(progress_at: &str, now: &str) -> f64 {
    seconds_between(progress_at, now)
        .map(|s| s.max(0.0))
        .unwrap_or(0.0)
}

fn should_enter_heartbeat(state: &HeartbeatState) -> bool {
    state.last_notice_at.is_none() || state.notice_count == 0
}

fn notice_limit_reached(state: &HeartbeatState, policy: &HeartbeatPolicy) -> bool {
    policy
        .max_notice_count
        .map(|limit| state.notice_count >= limit)
        .unwrap_or(false)
}

fn repeat_interval_not_elapsed(
    state: &HeartbeatState,
    policy: &HeartbeatPolicy,
    now: &str,
) -> bool {
    since_last_notice_seconds(state, now) < policy.repeat_interval_s
}

fn since_last_notice_seconds(state: &HeartbeatState, now: &str) -> f64 {
    match &state.last_notice_at {
        Some(last_notice) => seconds_between(last_notice, now)
            .map(|s| s.max(0.0))
            .unwrap_or(0.0),
        None => 0.0,
    }
}

fn decision(
    action: HeartbeatAction,
    state: &HeartbeatState,
    silence_seconds: f64,
) -> HeartbeatDecision {
    HeartbeatDecision {
        action,
        subject_kind: state.subject_kind.clone(),
        subject_id: state.subject_id.clone(),
        owner: state.owner.clone(),
        last_progress_at: state.last_progress_at.clone(),
        last_notice_at: state.last_notice_at.clone(),
        silence_seconds,
        notice_count: state.notice_count,
    }
}

fn progress_advanced(observed: &str, recorded: &str) -> bool {
    if observed == recorded {
        return false;
    }
    seconds_between(recorded, observed)
        .map(|s| s > 0.0)
        .unwrap_or(true)
}
