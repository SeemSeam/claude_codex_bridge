"""Mailbox kernel package with an env-gated Rust backend.

When the Rust ``ccb_py_mailbox.mailbox_kernel`` extension is installed and
``CCB_MAILBOX_RUST`` is not explicitly disabled, ``MailboxKernelService``
delegates its state-machine work to the Rust extension and converts results back
to the Python dataclasses that the rest of CCBD expects.  Otherwise the original
Python implementation is used.

This makes the package safe to import in source checkouts that do not have the
Rust extension built yet.
"""
from __future__ import annotations

import os
from typing import Any

from .model_enums import (
    SCHEMA_VERSION,
    InboundEventStatus,
    InboundEventType,
    LeaseState,
    MailboxState,
)
from .models import (
    DeliveryLease,
    InboundEventRecord,
    MailboxRecord,
)
from .store import (
    DeliveryLeaseStore,
    InboundEventStore,
    MailboxStore,
)

__all__ = [
    "DeliveryLease",
    "DeliveryLeaseStore",
    "InboundEventRecord",
    "InboundEventStatus",
    "InboundEventStore",
    "InboundEventType",
    "LeaseState",
    "MailboxKernelService",
    "MailboxRecord",
    "MailboxState",
    "MailboxStore",
    "SCHEMA_VERSION",
]

# Try to import the Rust extension; if it is not built, fall back to Python.
try:
    import ccb_py_mailbox as _rust
    _RUST_AVAILABLE = True
except Exception:  # pragma: no cover - Rust extension may be absent in dev
    _rust = None  # type: ignore[assignment]
    _RUST_AVAILABLE = False


_EVENT_TYPE_ORDER = (
    InboundEventType.TASK_REQUEST,
    InboundEventType.TASK_REPLY,
    InboundEventType.COMPLETION_NOTICE,
    InboundEventType.RETRY_SIGNAL,
    InboundEventType.SYSTEM_SIGNAL,
    InboundEventType.BARRIER_RELEASE,
)

_STATUS_ORDER = (
    InboundEventStatus.CREATED,
    InboundEventStatus.QUEUED,
    InboundEventStatus.DELIVERING,
    InboundEventStatus.CONSUMED,
    InboundEventStatus.SUPERSEDED,
    InboundEventStatus.ABANDONED,
)


def _event_type_to_int(value: InboundEventType | None) -> int | None:
    if value is None:
        return None
    return _EVENT_TYPE_ORDER.index(value)


def _status_to_int(value: InboundEventStatus) -> int:
    return _STATUS_ORDER.index(value)


def _event_dict_to_record(value: dict[str, Any]) -> InboundEventRecord:
    value = dict(value)
    value.setdefault("schema_version", SCHEMA_VERSION)
    value.setdefault("record_type", "inbound_event_record")
    return InboundEventRecord.from_record(value)


def _mailbox_dict_to_record(value: dict[str, Any]) -> MailboxRecord:
    value = dict(value)
    value.setdefault("schema_version", SCHEMA_VERSION)
    value.setdefault("record_type", "mailbox_record")
    return MailboxRecord.from_record(value)


def _optional_event_record(value: Any | None) -> InboundEventRecord | None:
    if value is None:
        return None
    return _event_dict_to_record(value)


def _event_record_list(value: Any) -> tuple[InboundEventRecord, ...]:
    return tuple(_event_dict_to_record(item) for item in value)


def _state_to_dict(state: Any) -> dict[str, Any]:
    if state is None:
        return {}
    return state.to_record()


def _optional_state_dict(state: Any | None) -> dict[str, Any] | None:
    if state is None:
        return None
    return state.to_record()


def _optional_event_id(value: Any) -> Any:
    """Map Python sentinel semantics for active_inbound_event_id.

    ``Ellipsis`` / absent means "keep prior"; ``None`` means "clear";
    a string means "set to this id".  The Rust kernel uses
    ``None`` = keep prior, ``Some(None)`` = clear, ``Some(Some(id))`` = set.
    Returning ``...`` lets the wrapper method omit the argument (Rust default).
    """
    if value is Ellipsis:
        return ...
    return value


class _RustMailboxKernelService:
    """Adapter that delegates to ``ccb_py_mailbox.mailbox_kernel.MailboxKernelService``."""

    def __init__(
        self,
        layout: Any,
        *,
        clock: Any,
        mailbox_store: MailboxStore | None = None,
        inbound_store: InboundEventStore | None = None,
        lease_store: DeliveryLeaseStore | None = None,
    ) -> None:
        # The Rust service builds its own PathLayout from the project root and
        # its own stores.  We keep the Python store instances as attributes so
        # code that directly accesses ``_mailbox_kernel._inbound_store`` etc.
        # continues to work without changes.
        self._layout = layout
        self._clock = clock
        self._mailbox_store = mailbox_store or MailboxStore(layout)
        self._inbound_store = inbound_store or InboundEventStore(layout)
        self._lease_store = lease_store or DeliveryLeaseStore(layout)
        self._inner = _rust.mailbox_kernel.MailboxKernelService(str(layout.project_root))

        # Expose the same internal attributes the Python runtime and tests use,
        # so callers can keep using ``service._mailbox_record_cls``,
        # ``service._normalize_agent_name``, etc.
        from mailbox_runtime.targets import normalize_mailbox_owner_name

        self._mailbox_record_cls = MailboxRecord
        self._delivery_lease_cls = DeliveryLease
        self._reply_event_type = InboundEventType.TASK_REPLY
        self._lease_state_acquired = LeaseState.ACQUIRED
        self._mailbox_state_delivering = MailboxState.DELIVERING
        self._mailbox_state_blocked = MailboxState.BLOCKED
        self._mailbox_state_idle = MailboxState.IDLE
        self._status_delivering = InboundEventStatus.DELIVERING
        self._status_consumed = InboundEventStatus.CONSUMED
        self._terminal_event_states = frozenset(
            {
                InboundEventStatus.CONSUMED,
                InboundEventStatus.SUPERSEDED,
                InboundEventStatus.ABANDONED,
            }
        )
        self._claimable_event_states = frozenset(
            {InboundEventStatus.CREATED, InboundEventStatus.QUEUED}
        )
        self._normalize_agent_name = normalize_mailbox_owner_name

    def latest_events(self, agent_name: str) -> tuple[InboundEventRecord, ...]:
        return _event_record_list(self._inner.latest_events(agent_name))

    def pending_events(
        self,
        agent_name: str,
        *,
        event_type: InboundEventType | None = None,
    ) -> tuple[InboundEventRecord, ...]:
        return _event_record_list(
            self._inner.pending_events(agent_name, _event_type_to_int(event_type))
        )

    def head_pending_event(self, agent_name: str) -> InboundEventRecord | None:
        return _optional_event_record(self._inner.head_pending_event(agent_name))

    def peek_next(
        self,
        agent_name: str,
        *,
        event_type: InboundEventType | None = None,
    ) -> InboundEventRecord | None:
        return _optional_event_record(
            self._inner.peek_next(agent_name, _event_type_to_int(event_type))
        )

    def claim(
        self,
        agent_name: str,
        inbound_event_id: str,
        *,
        started_at: str | None = None,
    ) -> InboundEventRecord | None:
        return _optional_event_record(
            self._inner.claim(agent_name, inbound_event_id, started_at)
        )

    def claim_next(
        self,
        agent_name: str,
        *,
        event_type: InboundEventType | None = None,
        started_at: str | None = None,
    ) -> InboundEventRecord | None:
        return _optional_event_record(
            self._inner.claim_next(agent_name, _event_type_to_int(event_type), started_at)
        )

    def ack_reply(
        self,
        agent_name: str,
        inbound_event_id: str,
        *,
        started_at: str | None = None,
        finished_at: str | None = None,
    ) -> InboundEventRecord | None:
        return _optional_event_record(
            self._inner.ack_reply(agent_name, inbound_event_id, started_at, finished_at)
        )

    def consume(
        self,
        agent_name: str,
        inbound_event_id: str,
        *,
        finished_at: str | None = None,
    ) -> InboundEventRecord | None:
        return _optional_event_record(
            self._inner.consume(agent_name, inbound_event_id, finished_at)
        )

    def abandon(
        self,
        agent_name: str,
        inbound_event_id: str,
        *,
        finished_at: str | None = None,
    ) -> InboundEventRecord | None:
        return _optional_event_record(
            self._inner.abandon(agent_name, inbound_event_id, finished_at)
        )

    def supersede(
        self,
        agent_name: str,
        inbound_event_id: str,
        *,
        finished_at: str | None = None,
    ) -> InboundEventRecord | None:
        return _optional_event_record(
            self._inner.supersede(agent_name, inbound_event_id, finished_at)
        )

    def rebuild_mailbox_summary(
        self,
        agent_name: str,
        *,
        updated_at: str | None = None,
    ) -> MailboxRecord:
        return _mailbox_dict_to_record(
            self._inner.rebuild_mailbox_summary(agent_name, updated_at)
        )

    def project_mailbox_summary(
        self,
        agent_name: str,
        *,
        updated_at: str | None = None,
        prior=Ellipsis,
        summary_source: str = "projection",
    ) -> MailboxRecord:
        if prior is Ellipsis:
            prior_record = self._mailbox_store.load(agent_name)
        else:
            prior_record = prior
        return _mailbox_dict_to_record(
            self._inner.project_mailbox_summary(
                agent_name,
                updated_at,
                _optional_state_dict(prior_record),
                summary_source,
            )
        )

    def refresh_mailbox(
        self,
        agent_name: str,
        *,
        updated_at: str | None = None,
    ) -> MailboxRecord:
        return _mailbox_dict_to_record(
            self._inner.refresh_mailbox(agent_name, updated_at)
        )

    def apply_incremental_summary_update(
        self,
        agent_name: str,
        *,
        queue_delta: int = 0,
        pending_reply_delta: int = 0,
        active_inbound_event_id=Ellipsis,
        last_started_at: str | None = None,
        last_finished_at: str | None = None,
        updated_at: str | None = None,
    ) -> MailboxRecord:
        active = _optional_event_id(active_inbound_event_id)
        return _mailbox_dict_to_record(
            self._inner.apply_incremental_summary_update(
                agent_name,
                queue_delta,
                pending_reply_delta,
                active if active is not ... else None,
                last_started_at,
                last_finished_at,
                updated_at,
            )
        )

    def upsert_mailbox_summary(
        self,
        agent_name: str,
        *,
        queue_delta: int = 0,
        pending_reply_delta: int = 0,
        active_inbound_event_id=Ellipsis,
        last_started_at: str | None = None,
        last_finished_at: str | None = None,
        updated_at: str | None = None,
    ) -> MailboxRecord:
        return self.apply_incremental_summary_update(
            agent_name,
            queue_delta=queue_delta,
            pending_reply_delta=pending_reply_delta,
            active_inbound_event_id=active_inbound_event_id,
            last_started_at=last_started_at,
            last_finished_at=last_finished_at,
            updated_at=updated_at,
        )

    def rewrite_head(
        self,
        agent_name: str,
        inbound_event_id: str,
        *,
        payload_ref: str | None,
        status,
        updated_at: str | None = None,
        clear_progress: bool = False,
    ) -> InboundEventRecord | None:
        return _optional_event_record(
            self._inner.rewrite_head(
                agent_name,
                inbound_event_id,
                payload_ref,
                _status_to_int(status),
                updated_at,
                clear_progress,
            )
        )


_CCB_MAILBOX_RUST = os.environ.get("CCB_MAILBOX_RUST", "").strip().lower()
if _CCB_MAILBOX_RUST in {"0", "false", "no", "off"}:
    _USE_RUST = False
elif _CCB_MAILBOX_RUST in {"1", "true", "yes", "on"}:
    _USE_RUST = True
else:
    _USE_RUST = _RUST_AVAILABLE

if _USE_RUST:
    if not _RUST_AVAILABLE:
        raise ImportError(
            "CCB_MAILBOX_RUST is enabled but ccb_py_mailbox is not installed"
        )
    MailboxKernelService = _RustMailboxKernelService
else:
    from .service import MailboxKernelService
