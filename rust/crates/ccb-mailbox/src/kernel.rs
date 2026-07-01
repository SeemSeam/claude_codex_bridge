use std::sync::Arc;

use ccb_storage::paths::PathLayout;

use crate::models::*;
use crate::stores::{DeliveryLeaseStore, InboundEventStore, MailboxStore};

const TERMINAL_EVENT_STATES: &[InboundEventStatus] = &[
    InboundEventStatus::Consumed,
    InboundEventStatus::Superseded,
    InboundEventStatus::Abandoned,
];

const CLAIMABLE_EVENT_STATES: &[InboundEventStatus] =
    &[InboundEventStatus::Created, InboundEventStatus::Queued];

/// Clock function that returns an ISO-8601 UTC timestamp.
pub type Clock = Arc<dyn Fn() -> String + Send + Sync>;

fn default_clock() -> Clock {
    Arc::new(|| {
        chrono::Utc::now()
            .to_rfc3339_opts(chrono::SecondsFormat::Secs, true)
            .replace("+00:00", "Z")
    })
}

/// Mailbox kernel service: manages mailbox state and inbound events for agents.
pub struct MailboxKernelService {
    layout: PathLayout,
    clock: Clock,
    mailbox_store: MailboxStore,
    inbound_store: InboundEventStore,
    lease_store: DeliveryLeaseStore,
}

impl MailboxKernelService {
    pub fn new(layout: PathLayout) -> Self {
        Self::with_stores(layout, None, None, None, None)
    }

    pub fn with_clock(layout: PathLayout, clock: Clock) -> Self {
        Self::with_stores(layout, Some(clock), None, None, None)
    }

    pub fn with_stores(
        layout: PathLayout,
        clock: Option<Clock>,
        mailbox_store: Option<MailboxStore>,
        inbound_store: Option<InboundEventStore>,
        lease_store: Option<DeliveryLeaseStore>,
    ) -> Self {
        let mailbox_store = mailbox_store.unwrap_or_else(|| MailboxStore::new(&layout));
        let inbound_store = inbound_store.unwrap_or_else(|| InboundEventStore::new(&layout));
        let lease_store = lease_store.unwrap_or_else(|| DeliveryLeaseStore::new(&layout));
        Self {
            layout,
            clock: clock.unwrap_or_else(default_clock),
            mailbox_store,
            inbound_store,
            lease_store,
        }
    }

    pub fn layout(&self) -> &PathLayout {
        &self.layout
    }

    pub fn clock(&self) -> &Clock {
        &self.clock
    }

    pub fn mailbox_store(&self) -> &MailboxStore {
        &self.mailbox_store
    }

    pub fn inbound_store(&self) -> &InboundEventStore {
        &self.inbound_store
    }

    pub fn lease_store(&self) -> &DeliveryLeaseStore {
        &self.lease_store
    }

    fn now(&self) -> String {
        (self.clock)()
    }

    /// Get all latest events for an agent (one entry per inbound_event_id).
    pub fn latest_events(&self, agent_name: &str) -> Vec<InboundEventRecord> {
        let normalized = normalize_mailbox_owner_name(agent_name);
        let mut latest: std::collections::HashMap<String, InboundEventRecord> =
            std::collections::HashMap::new();
        let mut order: Vec<String> = Vec::new();
        for record in self.inbound_store.list_agent(&normalized) {
            if !latest.contains_key(&record.inbound_event_id) {
                order.push(record.inbound_event_id.clone());
            }
            latest.insert(record.inbound_event_id.clone(), record);
        }
        order
            .into_iter()
            .filter_map(|id| latest.remove(&id))
            .collect()
    }

    /// Get pending (non-terminal) events for an agent, optionally filtered by type.
    pub fn pending_events(
        &self,
        agent_name: &str,
        event_type: Option<InboundEventType>,
    ) -> Vec<InboundEventRecord> {
        self.latest_events(agent_name)
            .into_iter()
            .filter(|e| !is_terminal(e.status) && event_type.is_none_or(|t| e.event_type == t))
            .collect()
    }

    /// Get the first pending event for an agent.
    pub fn head_pending_event(&self, agent_name: &str) -> Option<InboundEventRecord> {
        self.pending_events(agent_name, None).into_iter().next()
    }

    /// Peek at the next claimable event.
    pub fn peek_next(
        &self,
        agent_name: &str,
        event_type: Option<InboundEventType>,
    ) -> Option<InboundEventRecord> {
        let head = self.head_pending_event(agent_name)?;
        if event_type.is_some_and(|t| head.event_type != t) {
            return None;
        }
        if !is_claimable(head.status) {
            return None;
        }
        Some(head)
    }

    /// Claim a specific event by ID.
    pub fn claim(
        &self,
        agent_name: &str,
        inbound_event_id: &str,
        started_at: Option<&str>,
    ) -> Option<InboundEventRecord> {
        let normalized = normalize_mailbox_owner_name(agent_name);
        let timestamp = started_at.map_or_else(|| self.now(), |s| s.to_string());
        let current = self._load_claim_candidate(&normalized, inbound_event_id)?;

        if self._has_conflicting_lease(&normalized, inbound_event_id) {
            return self._refresh_none(&normalized, &timestamp);
        }
        if current.status == InboundEventStatus::Delivering {
            return self._refresh_current(&normalized, &current, &timestamp);
        }
        if !self._is_claimable_head(&normalized, &current) {
            return self._refresh_none(&normalized, &timestamp);
        }
        self._claim_current(&normalized, current, &timestamp)
    }

    /// Claim the next pending event.
    pub fn claim_next(
        &self,
        agent_name: &str,
        event_type: Option<InboundEventType>,
        started_at: Option<&str>,
    ) -> Option<InboundEventRecord> {
        let normalized = normalize_mailbox_owner_name(agent_name);
        let started_at_str = started_at.map_or_else(|| self.now(), |s| s.to_string());
        let next_event = self.peek_next(&normalized, event_type);
        if next_event.is_none() {
            let _ = self.rebuild_mailbox_summary(&normalized, Some(&started_at_str));
            return None;
        }
        self.claim(
            &normalized,
            &next_event.unwrap().inbound_event_id,
            started_at,
        )
    }

    fn _load_claim_candidate(
        &self,
        agent_name: &str,
        inbound_event_id: &str,
    ) -> Option<InboundEventRecord> {
        let current = self.inbound_store.get_latest(agent_name, inbound_event_id);
        current.filter(|c| !is_terminal(c.status))
    }

    fn _has_conflicting_lease(&self, agent_name: &str, inbound_event_id: &str) -> bool {
        let Ok(lease) = self.lease_store.load(agent_name) else {
            return false;
        };
        lease.is_some_and(|l| {
            l.lease_state == LeaseState::Acquired && l.inbound_event_id != inbound_event_id
        })
    }

    fn _is_claimable_head(&self, agent_name: &str, current: &InboundEventRecord) -> bool {
        if !is_claimable(current.status) {
            return false;
        }
        let head = self.head_pending_event(agent_name);
        head.is_some_and(|h| h.inbound_event_id == current.inbound_event_id)
    }

    fn _claim_current(
        &self,
        agent_name: &str,
        current: InboundEventRecord,
        timestamp: &str,
    ) -> Option<InboundEventRecord> {
        let updated = InboundEventRecord {
            status: InboundEventStatus::Delivering,
            started_at: Some(
                current
                    .started_at
                    .clone()
                    .unwrap_or_else(|| timestamp.to_string()),
            ),
            finished_at: None,
            ..current
        };
        let _ = self.inbound_store.append(&updated);
        let lease = DeliveryLease {
            agent_name: agent_name.to_string(),
            inbound_event_id: updated.inbound_event_id.clone(),
            lease_version: self._next_lease_version(agent_name),
            acquired_at: timestamp.to_string(),
            last_progress_at: Some(timestamp.to_string()),
            expires_at: None,
            lease_state: LeaseState::Acquired,
        };
        let _ = self.lease_store.save(&lease);

        let prior = self.mailbox_store.load(agent_name).ok().flatten();
        if prior.as_ref().is_none_or(|p| {
            p.queue_depth == 0
                || p.head_inbound_event_id.as_deref() != Some(&updated.inbound_event_id)
        }) {
            let _ = self.rebuild_mailbox_summary(agent_name, Some(timestamp));
            return Some(updated);
        }
        let prior = prior.unwrap();
        let summary_head = summary_head_from_event(&updated);
        let _ = self.apply_transition_summary_update(
            agent_name,
            prior.queue_depth,
            prior.pending_reply_count,
            Some(updated.inbound_event_id.clone()),
            latest_timestamp(
                prior.last_inbound_started_at.as_deref(),
                updated.started_at.as_deref(),
            )
            .map(|s| s.to_string()),
            prior.last_inbound_finished_at.clone(),
            Some(timestamp.to_string()),
            "transition-claim",
            Some(summary_head),
        );
        Some(updated)
    }

    fn _refresh_current(
        &self,
        agent_name: &str,
        current: &InboundEventRecord,
        timestamp: &str,
    ) -> Option<InboundEventRecord> {
        let _ = self.rebuild_mailbox_summary(agent_name, Some(timestamp));
        Some(current.clone())
    }

    fn _refresh_none(&self, agent_name: &str, timestamp: &str) -> Option<InboundEventRecord> {
        let _ = self.rebuild_mailbox_summary(agent_name, Some(timestamp));
        None
    }

    fn _next_lease_version(&self, agent_name: &str) -> u32 {
        if let Ok(Some(lease)) = self.lease_store.load(agent_name) {
            return lease.lease_version + 1;
        }
        if let Ok(Some(mailbox)) = self.mailbox_store.load(agent_name) {
            return mailbox.lease_version + 1;
        }
        1
    }

    /// Mark an event as consumed.
    pub fn consume(
        &self,
        agent_name: &str,
        inbound_event_id: &str,
        finished_at: Option<&str>,
    ) -> Option<InboundEventRecord> {
        self.mark_terminal(
            agent_name,
            inbound_event_id,
            InboundEventStatus::Consumed,
            finished_at,
        )
    }

    /// Mark an event as abandoned.
    pub fn abandon(
        &self,
        agent_name: &str,
        inbound_event_id: &str,
        finished_at: Option<&str>,
    ) -> Option<InboundEventRecord> {
        self.mark_terminal(
            agent_name,
            inbound_event_id,
            InboundEventStatus::Abandoned,
            finished_at,
        )
    }

    /// Mark an event as superseded.
    pub fn supersede(
        &self,
        agent_name: &str,
        inbound_event_id: &str,
        finished_at: Option<&str>,
    ) -> Option<InboundEventRecord> {
        self.mark_terminal(
            agent_name,
            inbound_event_id,
            InboundEventStatus::Superseded,
            finished_at,
        )
    }

    /// Generic terminal transition.
    pub fn mark_terminal(
        &self,
        agent_name: &str,
        inbound_event_id: &str,
        status: InboundEventStatus,
        finished_at: Option<&str>,
    ) -> Option<InboundEventRecord> {
        let normalized = normalize_mailbox_owner_name(agent_name);
        let timestamp = finished_at.map_or_else(|| self.now(), |s| s.to_string());
        let current = self.inbound_store.get_latest(&normalized, inbound_event_id);
        if current.is_none() {
            return self._refresh_none(&normalized, &timestamp);
        }
        let current = current.unwrap();
        if is_terminal(current.status) {
            return self._refresh_current(&normalized, &current, &timestamp);
        }
        let updated = InboundEventRecord {
            status,
            finished_at: Some(timestamp.clone()),
            ..current
        };
        let _ = self.inbound_store.append(&updated);
        self._release_matching_lease(&normalized, inbound_event_id);

        let prior = self.mailbox_store.load(&normalized).ok().flatten();
        if prior.as_ref().is_none_or(|p| p.queue_depth == 0) {
            let _ = self.rebuild_mailbox_summary(&normalized, Some(&timestamp));
            return Some(updated);
        }
        let prior = prior.unwrap();
        let queue_depth = prior.queue_depth.saturating_sub(1);
        let pending_reply_count = if updated.event_type == InboundEventType::TaskReply {
            prior.pending_reply_count.saturating_sub(1)
        } else {
            prior.pending_reply_count
        };
        let active_inbound_event_id = prior.active_inbound_event_id.as_deref().and_then(|id| {
            if id == updated.inbound_event_id {
                None
            } else {
                Some(id.to_string())
            }
        });

        let summary_head =
            if prior.head_inbound_event_id.as_deref() == Some(&updated.inbound_event_id) {
                if queue_depth == 0 {
                    empty_head()
                } else {
                    let next_head = self.head_pending_event(&normalized);
                    if next_head.is_none() {
                        let _ = self.rebuild_mailbox_summary(&normalized, Some(&timestamp));
                        return Some(updated);
                    }
                    summary_head_from_event(&next_head.unwrap())
                }
            } else {
                prior_head(&prior)
            };

        let _ = self.apply_transition_summary_update(
            &normalized,
            queue_depth,
            pending_reply_count,
            active_inbound_event_id,
            prior.last_inbound_started_at.clone(),
            latest_timestamp(prior.last_inbound_finished_at.as_deref(), Some(&timestamp))
                .map(|s| s.to_string()),
            Some(timestamp),
            "transition-terminal",
            Some(summary_head),
        );
        Some(updated)
    }

    /// Acknowledge a reply event, claiming it if needed and then consuming it.
    pub fn ack_reply(
        &self,
        agent_name: &str,
        inbound_event_id: &str,
        started_at: Option<&str>,
        finished_at: Option<&str>,
    ) -> Option<InboundEventRecord> {
        let normalized = normalize_mailbox_owner_name(agent_name);
        let timestamp = finished_at
            .or(started_at)
            .map_or_else(|| self.now(), |s| s.to_string());
        let head = self.head_pending_event(&normalized);
        if !self._head_matches_reply(head.as_ref(), inbound_event_id) {
            let _ = self.rebuild_mailbox_summary(&normalized, Some(&timestamp));
            return None;
        }
        let current = self.inbound_store.get_latest(&normalized, inbound_event_id);
        if current.is_none() || is_terminal(current.as_ref().unwrap().status) {
            let _ = self.rebuild_mailbox_summary(&normalized, Some(&timestamp));
            return current;
        }
        let current = current.unwrap();
        if current.status == InboundEventStatus::Delivering {
            return self.consume(&normalized, inbound_event_id, Some(&timestamp));
        }
        if self
            .claim(&normalized, inbound_event_id, Some(&timestamp))
            .is_none()
        {
            let _ = self.rebuild_mailbox_summary(&normalized, Some(&timestamp));
            return None;
        }
        self.consume(&normalized, inbound_event_id, Some(&timestamp))
    }

    fn _head_matches_reply(
        &self,
        head: Option<&InboundEventRecord>,
        inbound_event_id: &str,
    ) -> bool {
        let head = match head {
            Some(h) => h,
            None => return false,
        };
        if head.inbound_event_id != inbound_event_id {
            return false;
        }
        head.event_type == InboundEventType::TaskReply
    }

    /// Rewrite the head event with a new payload_ref/status.
    pub fn rewrite_head(
        &self,
        agent_name: &str,
        inbound_event_id: &str,
        payload_ref: Option<&str>,
        status: InboundEventStatus,
        updated_at: Option<&str>,
        clear_progress: bool,
    ) -> Option<InboundEventRecord> {
        let normalized = normalize_mailbox_owner_name(agent_name);
        let timestamp = updated_at.map_or_else(|| self.now(), |s| s.to_string());
        let current = self.inbound_store.get_latest(&normalized, inbound_event_id);
        if current.is_none() || is_terminal(current.as_ref().unwrap().status) {
            let _ = self.rebuild_mailbox_summary(&normalized, Some(&timestamp));
            return None;
        }
        let current = current.unwrap();
        let updated = InboundEventRecord {
            payload_ref: payload_ref.map(|s| s.to_string()),
            status,
            started_at: if clear_progress {
                None
            } else {
                current.started_at.clone()
            },
            finished_at: if clear_progress {
                None
            } else {
                current.finished_at.clone()
            },
            ..current
        };
        let _ = self.inbound_store.append(&updated);
        self._release_matching_lease(&normalized, inbound_event_id);

        let prior = self.mailbox_store.load(&normalized).ok().flatten();
        if prior.as_ref().is_none_or(|p| {
            p.head_inbound_event_id.as_deref() != Some(inbound_event_id) || p.queue_depth == 0
        }) {
            let _ = self.rebuild_mailbox_summary(&normalized, Some(&timestamp));
            return Some(updated);
        }
        let prior = prior.unwrap();
        let active_inbound_event_id = prior.active_inbound_event_id.as_deref().and_then(|id| {
            if id == inbound_event_id {
                None
            } else {
                Some(id.to_string())
            }
        });
        let _ = self.apply_transition_summary_update(
            &normalized,
            prior.queue_depth,
            prior.pending_reply_count,
            active_inbound_event_id,
            prior.last_inbound_started_at.clone(),
            prior.last_inbound_finished_at.clone(),
            Some(timestamp),
            "transition-rewrite-head",
            Some(summary_head_from_event(&updated)),
        );
        Some(updated)
    }

    fn _release_matching_lease(&self, agent_name: &str, inbound_event_id: &str) {
        let Ok(Some(lease)) = self.lease_store.load(agent_name) else {
            return;
        };
        if lease.lease_state != LeaseState::Acquired {
            return;
        }
        if lease.inbound_event_id != inbound_event_id {
            return;
        }
        let _ = self.lease_store.remove(agent_name);
    }

    /// Rebuild the mailbox summary from history.
    pub fn rebuild_mailbox_summary(
        &self,
        agent_name: &str,
        updated_at: Option<&str>,
    ) -> crate::Result<MailboxRecord> {
        let normalized = normalize_mailbox_owner_name(agent_name);
        let prior = self.mailbox_store.load(&normalized)?;
        let expected_summary_version = prior.as_ref().map(|p| p.summary_version);
        let record = self.project_mailbox_summary(
            &normalized,
            updated_at,
            prior.as_ref(),
            "history-refresh",
        )?;
        if self
            .mailbox_store
            .compare_and_save(&record, expected_summary_version)?
        {
            return Ok(record);
        }
        Ok(self.mailbox_store.load(&normalized)?.unwrap_or(record))
    }

    /// Project a mailbox summary without persisting it.
    pub fn project_mailbox_summary(
        &self,
        agent_name: &str,
        updated_at: Option<&str>,
        prior: Option<&MailboxRecord>,
        summary_source: &str,
    ) -> crate::Result<MailboxRecord> {
        let normalized = normalize_mailbox_owner_name(agent_name);
        let timestamp = updated_at.map_or_else(|| self.now(), |s| s.to_string());
        let lease = self.lease_store.load(&normalized)?;
        let events = self.pending_events(&normalized, None);
        let queue_depth = events.len() as u32;
        let pending_reply_count = events
            .iter()
            .filter(|e| e.event_type == InboundEventType::TaskReply)
            .count() as u32;
        let (last_started, last_finished) = self._latest_activity(&normalized, prior);
        let active_inbound_event_id = self._active_inbound_event_id(lease.as_ref());
        let summary_head = if let Some(head) = self.head_pending_event(&normalized) {
            summary_head_from_event(&head)
        } else {
            empty_head()
        };
        Ok(build_mailbox_summary_record(
            &normalized,
            prior,
            lease.as_ref(),
            queue_depth,
            pending_reply_count,
            active_inbound_event_id,
            last_started,
            last_finished,
            timestamp,
            summary_source,
            Some(summary_head),
        ))
    }

    fn _active_inbound_event_id(&self, lease: Option<&DeliveryLease>) -> Option<String> {
        lease
            .filter(|l| l.lease_state == LeaseState::Acquired && !l.inbound_event_id.is_empty())
            .map(|l| l.inbound_event_id.clone())
    }

    fn _latest_activity(
        &self,
        agent_name: &str,
        prior: Option<&MailboxRecord>,
    ) -> (Option<String>, Option<String>) {
        let mut last_started = prior.and_then(|p| p.last_inbound_started_at.clone());
        let mut last_finished = prior.and_then(|p| p.last_inbound_finished_at.clone());
        for event in self.latest_events(agent_name) {
            last_started = latest_timestamp(last_started.as_deref(), event.started_at.as_deref())
                .map(|s| s.to_string());
            last_finished =
                latest_timestamp(last_finished.as_deref(), event.finished_at.as_deref())
                    .map(|s| s.to_string());
        }
        (last_started, last_finished)
    }

    /// Apply an incremental summary update.
    #[allow(clippy::too_many_arguments)]
    pub fn apply_incremental_summary_update(
        &self,
        agent_name: &str,
        queue_delta: i32,
        pending_reply_delta: i32,
        active_inbound_event_id: Option<Option<String>>,
        last_started_at: Option<&str>,
        last_finished_at: Option<&str>,
        updated_at: Option<&str>,
    ) -> crate::Result<MailboxRecord> {
        let normalized = normalize_mailbox_owner_name(agent_name);
        let timestamp = updated_at.map_or_else(|| self.now(), |s| s.to_string());
        let prior = self.mailbox_store.load(&normalized)?;
        let expected_summary_version = prior.as_ref().map(|p| p.summary_version);
        let lease = self.lease_store.load(&normalized)?;
        let prior_queue_depth = prior.as_ref().map_or(0, |p| p.queue_depth as i32);
        let queue_depth = (prior_queue_depth + queue_delta).max(0) as u32;
        let prior_pending_reply_count = prior.as_ref().map_or(0, |p| p.pending_reply_count as i32);
        let pending_reply_count = (prior_pending_reply_count + pending_reply_delta).max(0) as u32;
        let current_active = match active_inbound_event_id {
            Some(Some(id)) => Some(id),
            Some(None) => None,
            None => prior
                .as_ref()
                .and_then(|p| p.active_inbound_event_id.clone()),
        };
        let last_started = latest_timestamp(
            prior
                .as_ref()
                .and_then(|p| p.last_inbound_started_at.as_deref()),
            last_started_at,
        )
        .map(|s| s.to_string());
        let last_finished = latest_timestamp(
            prior
                .as_ref()
                .and_then(|p| p.last_inbound_finished_at.as_deref()),
            last_finished_at,
        )
        .map(|s| s.to_string());
        let summary_head = self
            .head_pending_event(&normalized)
            .map(|head| summary_head_from_event(&head));
        let record = build_mailbox_summary_record(
            &normalized,
            prior.as_ref(),
            lease.as_ref(),
            queue_depth,
            pending_reply_count,
            current_active,
            last_started,
            last_finished,
            timestamp,
            "incremental-upsert",
            summary_head,
        );
        if self
            .mailbox_store
            .compare_and_save(&record, expected_summary_version)?
        {
            return Ok(record);
        }
        Ok(self.mailbox_store.load(&normalized)?.unwrap_or(record))
    }

    /// Apply a transition summary update.
    #[allow(clippy::too_many_arguments)]
    pub fn apply_transition_summary_update(
        &self,
        agent_name: &str,
        queue_depth: u32,
        pending_reply_count: u32,
        active_inbound_event_id: Option<String>,
        last_started_at: Option<String>,
        last_finished_at: Option<String>,
        updated_at: Option<String>,
        summary_source: &str,
        summary_head: Option<std::collections::HashMap<String, Option<String>>>,
    ) -> crate::Result<MailboxRecord> {
        let normalized = normalize_mailbox_owner_name(agent_name);
        let timestamp = updated_at.unwrap_or_else(|| self.now());
        let prior = self.mailbox_store.load(&normalized)?;
        let expected_summary_version = prior.as_ref().map(|p| p.summary_version);
        let lease = self.lease_store.load(&normalized)?;
        let record = build_mailbox_summary_record(
            &normalized,
            prior.as_ref(),
            lease.as_ref(),
            queue_depth,
            pending_reply_count,
            active_inbound_event_id,
            last_started_at,
            last_finished_at,
            timestamp,
            summary_source,
            summary_head,
        );
        if self
            .mailbox_store
            .compare_and_save(&record, expected_summary_version)?
        {
            return Ok(record);
        }
        Ok(self.mailbox_store.load(&normalized)?.unwrap_or(record))
    }

    /// Convenience alias for rebuild_mailbox_summary.
    pub fn refresh_mailbox(
        &self,
        agent_name: &str,
        updated_at: Option<&str>,
    ) -> crate::Result<MailboxRecord> {
        self.rebuild_mailbox_summary(agent_name, updated_at)
    }

    /// Convenience alias for apply_incremental_summary_update.
    #[allow(clippy::too_many_arguments)]
    pub fn upsert_mailbox_summary(
        &self,
        agent_name: &str,
        queue_delta: i32,
        pending_reply_delta: i32,
        active_inbound_event_id: Option<Option<String>>,
        last_started_at: Option<&str>,
        last_finished_at: Option<&str>,
        updated_at: Option<&str>,
    ) -> crate::Result<MailboxRecord> {
        self.apply_incremental_summary_update(
            agent_name,
            queue_delta,
            pending_reply_delta,
            active_inbound_event_id,
            last_started_at,
            last_finished_at,
            updated_at,
        )
    }
}

fn is_terminal(status: InboundEventStatus) -> bool {
    TERMINAL_EVENT_STATES.contains(&status)
}

fn is_claimable(status: InboundEventStatus) -> bool {
    CLAIMABLE_EVENT_STATES.contains(&status)
}

fn latest_timestamp<'a>(current: Option<&'a str>, candidate: Option<&'a str>) -> Option<&'a str> {
    match (current, candidate) {
        (_, None) => current,
        (None, Some(c)) => Some(c),
        (Some(a), Some(b)) => Some(if b > a { b } else { a }),
    }
}

fn normalize_mailbox_owner_name(name: &str) -> String {
    name.trim().to_lowercase()
}

fn next_summary_version(prior: Option<&MailboxRecord>) -> u32 {
    prior.map_or(1, |p| p.summary_version + 1)
}

fn derive_mailbox_state(has_active: bool, queue_depth: u32) -> MailboxState {
    if has_active {
        MailboxState::Delivering
    } else if queue_depth > 0 {
        MailboxState::Blocked
    } else {
        MailboxState::Idle
    }
}

pub(crate) fn summary_head_from_event(
    record: &InboundEventRecord,
) -> std::collections::HashMap<String, Option<String>> {
    let mut head = std::collections::HashMap::new();
    head.insert(
        "head_inbound_event_id".into(),
        Some(record.inbound_event_id.clone()),
    );
    head.insert(
        "head_event_type".into(),
        Some(record.event_type.to_string()),
    );
    head.insert(
        "head_status".into(),
        Some(format!("{:?}", record.status).to_lowercase()),
    );
    head.insert("head_message_id".into(), Some(record.message_id.clone()));
    head.insert("head_attempt_id".into(), record.attempt_id.clone());
    head.insert("head_payload_ref".into(), record.payload_ref.clone());
    head
}

fn empty_head() -> std::collections::HashMap<String, Option<String>> {
    let mut head = std::collections::HashMap::new();
    head.insert("head_inbound_event_id".into(), None);
    head.insert("head_event_type".into(), None);
    head.insert("head_status".into(), None);
    head.insert("head_message_id".into(), None);
    head.insert("head_attempt_id".into(), None);
    head.insert("head_payload_ref".into(), None);
    head
}

fn prior_head(prior: &MailboxRecord) -> std::collections::HashMap<String, Option<String>> {
    let mut head = std::collections::HashMap::new();
    head.insert(
        "head_inbound_event_id".into(),
        prior.head_inbound_event_id.clone(),
    );
    head.insert("head_event_type".into(), prior.head_event_type.clone());
    head.insert("head_status".into(), prior.head_status.clone());
    head.insert("head_message_id".into(), prior.head_message_id.clone());
    head.insert("head_attempt_id".into(), prior.head_attempt_id.clone());
    head.insert("head_payload_ref".into(), prior.head_payload_ref.clone());
    head
}

#[allow(clippy::too_many_arguments)]
fn build_mailbox_summary_record(
    agent_name: &str,
    prior: Option<&MailboxRecord>,
    lease: Option<&DeliveryLease>,
    queue_depth: u32,
    pending_reply_count: u32,
    active_inbound_event_id: Option<String>,
    last_started_at: Option<String>,
    last_finished_at: Option<String>,
    updated_at: String,
    summary_source: &str,
    summary_head: Option<std::collections::HashMap<String, Option<String>>>,
) -> MailboxRecord {
    let summary_head = summary_head.unwrap_or_else(|| {
        if let Some(p) = prior {
            prior_head(p)
        } else {
            empty_head()
        }
    });
    let mailbox_id = prior.map_or_else(|| format!("mbx_{}", agent_name), |p| p.mailbox_id.clone());
    let lease_version =
        lease.map_or_else(|| prior.map_or(0, |p| p.lease_version), |l| l.lease_version);
    let has_active = lease
        .map(|l| l.lease_state == LeaseState::Acquired && !l.inbound_event_id.is_empty())
        .unwrap_or(false)
        || active_inbound_event_id
            .as_deref()
            .is_some_and(|s| !s.is_empty());
    let mailbox_state = derive_mailbox_state(has_active, queue_depth);

    let summary_version = summary_version_for_record(
        prior,
        queue_depth,
        pending_reply_count,
        active_inbound_event_id.as_deref(),
        &summary_head,
        last_started_at.as_deref(),
        last_finished_at.as_deref(),
        mailbox_state,
        lease_version,
    );

    MailboxRecord {
        mailbox_id,
        agent_name: agent_name.to_string(),
        summary_version,
        summary_source: summary_source.to_string(),
        summary_refreshed_at: updated_at.clone(),
        active_inbound_event_id,
        queue_depth,
        pending_reply_count,
        head_inbound_event_id: summary_head.get("head_inbound_event_id").cloned().flatten(),
        head_event_type: summary_head.get("head_event_type").cloned().flatten(),
        head_status: summary_head.get("head_status").cloned().flatten(),
        head_message_id: summary_head.get("head_message_id").cloned().flatten(),
        head_attempt_id: summary_head.get("head_attempt_id").cloned().flatten(),
        head_payload_ref: summary_head.get("head_payload_ref").cloned().flatten(),
        last_inbound_started_at: last_started_at,
        last_inbound_finished_at: last_finished_at,
        mailbox_state,
        lease_version,
        updated_at,
    }
}

#[allow(clippy::too_many_arguments)]
fn summary_version_for_record(
    prior: Option<&MailboxRecord>,
    queue_depth: u32,
    pending_reply_count: u32,
    active_inbound_event_id: Option<&str>,
    summary_head: &std::collections::HashMap<String, Option<String>>,
    last_started_at: Option<&str>,
    last_finished_at: Option<&str>,
    mailbox_state: MailboxState,
    lease_version: u32,
) -> u32 {
    let prior = match prior {
        Some(p) => p,
        None => return 1,
    };
    let unchanged = prior.active_inbound_event_id.as_deref() == active_inbound_event_id
        && prior.queue_depth == queue_depth
        && prior.pending_reply_count == pending_reply_count
        && prior.head_inbound_event_id
            == summary_head.get("head_inbound_event_id").cloned().flatten()
        && prior.head_event_type == summary_head.get("head_event_type").cloned().flatten()
        && prior.head_status == summary_head.get("head_status").cloned().flatten()
        && prior.head_message_id == summary_head.get("head_message_id").cloned().flatten()
        && prior.head_attempt_id == summary_head.get("head_attempt_id").cloned().flatten()
        && prior.head_payload_ref == summary_head.get("head_payload_ref").cloned().flatten()
        && prior.last_inbound_started_at.as_deref() == last_started_at
        && prior.last_inbound_finished_at.as_deref() == last_finished_at
        && prior.mailbox_state == mailbox_state
        && prior.lease_version == lease_version;
    if unchanged {
        prior.summary_version
    } else {
        next_summary_version(Some(prior))
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use tempfile::TempDir;

    fn setup() -> (TempDir, MailboxKernelService) {
        let dir = TempDir::new().unwrap();
        let p = camino::Utf8Path::from_path(dir.path()).unwrap();
        let layout = PathLayout::new(p);
        let svc = MailboxKernelService::new(layout);
        (dir, svc)
    }

    fn make_event(id: &str, agent: &str) -> InboundEventRecord {
        InboundEventRecord {
            inbound_event_id: id.into(),
            agent_name: agent.into(),
            event_type: InboundEventType::TaskRequest,
            message_id: "m1".into(),
            attempt_id: None,
            payload_ref: None,
            priority: 100,
            status: InboundEventStatus::Queued,
            created_at: "2025-01-01T00:00:00Z".into(),
            started_at: None,
            finished_at: None,
        }
    }

    #[test]
    fn test_append_and_read() {
        let (_dir, svc) = setup();
        let event = make_event("e1", "agent-a");
        svc.inbound_store().append(&event).unwrap();
        let events = svc.latest_events("agent-a");
        assert_eq!(events.len(), 1);
    }

    #[test]
    fn test_pending_events() {
        let (_dir, svc) = setup();
        svc.inbound_store()
            .append(&make_event("e1", "agent-a"))
            .unwrap();
        let mut e2 = make_event("e2", "agent-a");
        e2.status = InboundEventStatus::Consumed;
        svc.inbound_store().append(&e2).unwrap();
        let pending = svc.pending_events("agent-a", None);
        assert_eq!(pending.len(), 1);
        assert_eq!(pending[0].inbound_event_id, "e1");
    }

    #[test]
    fn test_claim() {
        let (_dir, svc) = setup();
        svc.inbound_store()
            .append(&make_event("e1", "agent-a"))
            .unwrap();
        let claimed = svc
            .claim("agent-a", "e1", Some("2025-01-01T00:01:00Z"))
            .unwrap();
        assert_eq!(claimed.status, InboundEventStatus::Delivering);
        assert!(claimed.started_at.is_some());
    }

    #[test]
    fn test_claim_next() {
        let (_dir, svc) = setup();
        svc.inbound_store()
            .append(&make_event("e1", "agent-a"))
            .unwrap();
        let claimed = svc.claim_next("agent-a", None, None).unwrap();
        assert_eq!(claimed.inbound_event_id, "e1");
    }

    #[test]
    fn test_consume_updates_summary() {
        let (_dir, svc) = setup();
        svc.inbound_store()
            .append(&make_event("e1", "agent-a"))
            .unwrap();
        svc.rebuild_mailbox_summary("agent-a", Some("2025-01-01T00:00:00Z"))
            .unwrap();
        svc.consume("agent-a", "e1", Some("2025-01-01T00:02:00Z"));
        let summary = svc.mailbox_store().load("agent-a").unwrap().unwrap();
        assert_eq!(summary.queue_depth, 0);
    }
}
