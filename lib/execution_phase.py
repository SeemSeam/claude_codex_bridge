from __future__ import annotations

from dataclasses import asdict, dataclass


_PENDING_JOB_STATUSES = frozenset({"accepted", "queued"})
_TERMINAL_JOB_STATUSES = frozenset({"completed", "cancelled", "failed", "incomplete"})
_PENDING_REPLY_STATUSES = frozenset({"accepted", "queued"})
_TERMINAL_REPLY_STATUSES = frozenset({"completed", "cancelled", "failed", "incomplete"})


@dataclass(frozen=True)
class ExecutionPhaseEvidence:
    job_id: str | None = None
    job_agent: str | None = None
    job_status: str | None = None
    attempt_id: str | None = None
    attempt_job_id: str | None = None
    attempt_agent: str | None = None
    attempt_state: str | None = None
    inbound_event_id: str | None = None
    inbound_attempt_id: str | None = None
    inbound_agent: str | None = None
    inbound_status: str | None = None
    mailbox_agent: str | None = None
    mailbox_state: str | None = None
    mailbox_head_inbound_event_id: str | None = None
    mailbox_active_inbound_event_id: str | None = None
    lease_agent: str | None = None
    lease_inbound_event_id: str | None = None
    lease_state: str | None = None
    completion_job_id: str | None = None
    completion_agent: str | None = None
    completion_anchor_seen: bool | None = None
    completion_terminal: bool | None = None
    provider_state: str | None = None
    provider_identity_current: bool | None = None
    orphan_suspected: bool = False
    reply_expected: bool = False
    reply_delivery_job_id: str | None = None
    reply_delivery_source_job_id: str | None = None
    reply_delivery_status: str | None = None

    def to_record(self) -> dict[str, object]:
        return {
            key: value
            for key, value in asdict(self).items()
            if value is not None and value is not False
        }


@dataclass(frozen=True)
class ExecutionPhaseResult:
    phase: str
    reason: str
    evidence: ExecutionPhaseEvidence

    def to_record(self) -> dict[str, object]:
        return {
            "execution_phase": self.phase,
            "execution_phase_reason": self.reason,
            "execution_evidence": self.evidence.to_record(),
        }


def derive_execution_phase(evidence: ExecutionPhaseEvidence) -> ExecutionPhaseResult:
    job_status = _clean(evidence.job_status)
    completion_terminal = evidence.completion_terminal is True

    if job_status == "completed" and evidence.reply_expected:
        return _reply_phase(evidence)
    if job_status in _TERMINAL_JOB_STATUSES:
        return _result(evidence, "terminal", f"job_{job_status}")
    if completion_terminal:
        return _result(evidence, "terminal", "completion_terminal")
    if job_status in _PENDING_JOB_STATUSES:
        reason = _queued_lineage_error(evidence)
        if reason:
            return _result(evidence, "unknown", reason)
        return _result(evidence, "queued", "queued_lineage_confirmed")
    if job_status != "running":
        return _result(evidence, "unknown", "job_status_unknown")

    reason = _active_lineage_error(evidence)
    if reason:
        return _result(evidence, "unknown", reason)
    if evidence.completion_anchor_seen is False:
        return _result(evidence, "injecting", "request_anchor_not_seen")
    if evidence.completion_anchor_seen is not True:
        return _result(evidence, "unknown", "completion_anchor_unknown")
    if evidence.provider_identity_current is None:
        return _result(evidence, "unknown", "provider_identity_unavailable")
    if evidence.provider_identity_current is not True:
        return _result(evidence, "unknown", "provider_identity_mismatch")

    provider_state = _clean(evidence.provider_state)
    if provider_state in {"active", "pending"}:
        return _result(evidence, "executing", "provider_active")
    if provider_state == "idle":
        if evidence.orphan_suspected:
            return _result(evidence, "orphaned", "provider_idle_without_terminal")
        return _result(evidence, "provider_idle_pending_terminal", "provider_idle_terminal_pending")
    return _result(evidence, "unknown", "provider_state_unknown")


def execution_phase_evidence_from_records(
    *,
    job,
    attempt=None,
    inbound=None,
    mailbox=None,
    lease=None,
    completion=None,
    provider_state: str | None = None,
    provider_identity_current: bool | None = None,
    orphan_suspected: bool = False,
    reply_expected: bool = False,
    reply_delivery=None,
    reply_delivery_source_job_id: str | None = None,
) -> ExecutionPhaseEvidence:
    completion_state = getattr(completion, "state", None)
    completion_decision = getattr(completion, "latest_decision", None)
    return ExecutionPhaseEvidence(
        job_id=_attr(job, "job_id"),
        job_agent=_attr(job, "agent_name"),
        job_status=_enum_attr(job, "status"),
        attempt_id=_attr(attempt, "attempt_id"),
        attempt_job_id=_attr(attempt, "job_id"),
        attempt_agent=_attr(attempt, "agent_name"),
        attempt_state=_enum_attr(attempt, "attempt_state"),
        inbound_event_id=_attr(inbound, "inbound_event_id"),
        inbound_attempt_id=_attr(inbound, "attempt_id"),
        inbound_agent=_attr(inbound, "agent_name"),
        inbound_status=_enum_attr(inbound, "status"),
        mailbox_agent=_attr(mailbox, "agent_name"),
        mailbox_state=_enum_attr(mailbox, "mailbox_state"),
        mailbox_head_inbound_event_id=_attr(mailbox, "head_inbound_event_id"),
        mailbox_active_inbound_event_id=_attr(mailbox, "active_inbound_event_id"),
        lease_agent=_attr(lease, "agent_name"),
        lease_inbound_event_id=_attr(lease, "inbound_event_id"),
        lease_state=_enum_attr(lease, "lease_state"),
        completion_job_id=_attr(completion, "job_id"),
        completion_agent=_attr(completion, "agent_name"),
        completion_anchor_seen=_optional_bool(getattr(completion_state, "anchor_seen", None)),
        completion_terminal=_optional_bool(
            getattr(completion_state, "terminal", None)
            if completion_state is not None
            else getattr(completion_decision, "terminal", None)
        ),
        provider_state=provider_state,
        provider_identity_current=provider_identity_current,
        orphan_suspected=bool(orphan_suspected),
        reply_expected=bool(reply_expected),
        reply_delivery_job_id=_attr(reply_delivery, "job_id"),
        reply_delivery_source_job_id=reply_delivery_source_job_id,
        reply_delivery_status=_enum_attr(reply_delivery, "status"),
    )


def _reply_phase(evidence: ExecutionPhaseEvidence) -> ExecutionPhaseResult:
    delivery_id = _identity(evidence.reply_delivery_job_id)
    source_id = _identity(evidence.reply_delivery_source_job_id)
    source_job_id = _identity(evidence.job_id)
    status = _clean(evidence.reply_delivery_status)
    if not delivery_id and not status:
        return _result(evidence, "reply_queued", "reply_delivery_pending")
    if not delivery_id:
        return _result(evidence, "unknown", "reply_delivery_job_missing")
    if source_id != source_job_id:
        return _result(evidence, "unknown", "reply_delivery_source_mismatch")
    if status in _PENDING_REPLY_STATUSES:
        return _result(evidence, "reply_queued", "reply_delivery_pending")
    if status == "running":
        return _result(evidence, "reply_delivering", "reply_delivery_running")
    if status in _TERMINAL_REPLY_STATUSES:
        return _result(evidence, "terminal", f"reply_delivery_{status}")
    return _result(evidence, "unknown", "reply_delivery_status_unknown")


def _queued_lineage_error(evidence: ExecutionPhaseEvidence) -> str | None:
    common = _common_identity_error(evidence)
    if common:
        return common
    if _clean(evidence.attempt_state) not in {"pending", "delivering"}:
        return "attempt_not_pending"
    if _clean(evidence.inbound_status) not in {"created", "queued"}:
        return "inbound_not_queued"
    if _clean(evidence.mailbox_state) != "blocked":
        return "mailbox_state_mismatch"
    inbound_id = _identity(evidence.inbound_event_id)
    if _identity(evidence.mailbox_head_inbound_event_id) != inbound_id:
        return "mailbox_head_mismatch"
    if _identity(evidence.mailbox_active_inbound_event_id):
        return "mailbox_active_conflict"
    if _clean(evidence.lease_state) == "acquired" or _identity(evidence.lease_inbound_event_id):
        return "queued_lease_conflict"
    return None


def _active_lineage_error(evidence: ExecutionPhaseEvidence) -> str | None:
    common = _common_identity_error(evidence)
    if common:
        return common
    if _clean(evidence.attempt_state) not in {"delivering", "running", "waiting_completion"}:
        return "attempt_not_active"
    if _clean(evidence.inbound_status) != "delivering":
        return "inbound_not_delivering"
    if _clean(evidence.mailbox_state) != "delivering":
        return "mailbox_state_mismatch"
    inbound_id = _identity(evidence.inbound_event_id)
    if _identity(evidence.mailbox_active_inbound_event_id) != inbound_id:
        return "mailbox_active_mismatch"
    if _identity(evidence.lease_inbound_event_id) != inbound_id:
        return "lease_inbound_mismatch"
    if _identity(evidence.lease_agent) != _identity(evidence.job_agent):
        return "lease_agent_mismatch"
    if _clean(evidence.lease_state) != "acquired":
        return "lease_not_acquired"
    if _identity(evidence.completion_job_id) != _identity(evidence.job_id):
        return "completion_job_mismatch"
    if _identity(evidence.completion_agent) != _identity(evidence.job_agent):
        return "completion_agent_mismatch"
    return None


def _common_identity_error(evidence: ExecutionPhaseEvidence) -> str | None:
    job_id = _identity(evidence.job_id)
    job_agent = _identity(evidence.job_agent)
    attempt_id = _identity(evidence.attempt_id)
    inbound_id = _identity(evidence.inbound_event_id)
    if not job_id:
        return "job_id_missing"
    if not job_agent:
        return "job_agent_missing"
    if not attempt_id:
        return "attempt_missing"
    if _identity(evidence.attempt_job_id) != job_id:
        return "attempt_job_mismatch"
    if _identity(evidence.attempt_agent) != job_agent:
        return "attempt_agent_mismatch"
    if not inbound_id:
        return "inbound_missing"
    if _identity(evidence.inbound_attempt_id) != attempt_id:
        return "inbound_attempt_mismatch"
    if _identity(evidence.inbound_agent) != job_agent:
        return "inbound_agent_mismatch"
    if _identity(evidence.mailbox_agent) != job_agent:
        return "mailbox_agent_mismatch"
    return None


def _result(evidence: ExecutionPhaseEvidence, phase: str, reason: str) -> ExecutionPhaseResult:
    return ExecutionPhaseResult(phase=phase, reason=reason, evidence=evidence)


def _clean(value: object) -> str:
    return str(value or "").strip().lower()


def _identity(value: object) -> str:
    return str(value or "").strip()


def _attr(record, name: str) -> str | None:
    value = getattr(record, name, None) if record is not None else None
    text = str(value or "").strip()
    return text or None


def _enum_attr(record, name: str) -> str | None:
    value = getattr(record, name, None) if record is not None else None
    value = getattr(value, "value", value)
    text = str(value or "").strip().lower()
    return text or None


def _optional_bool(value: object) -> bool | None:
    return value if isinstance(value, bool) else None


__all__ = [
    "ExecutionPhaseEvidence",
    "ExecutionPhaseResult",
    "derive_execution_phase",
    "execution_phase_evidence_from_records",
]
