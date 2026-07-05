"""Message bureau package with an opt-in Rust backend.

The original Python implementation is used by default.  Set
``CCB_MESSAGE_BUREAU_RUST=1`` to delegate ``MessageBureauFacade`` and
``MessageBureauControlService`` to the Rust extension when it is installed.
Results are converted back to the Python dataclasses that the rest of CCBD
expects.

This keeps the package safe to import in source checkouts that do not have the
Rust extension built yet, and leaves the production default on the proven
Python backend.
"""
from __future__ import annotations

import dataclasses
import os
from typing import Any

from .callback_edges import CallbackEdgeRecord, CallbackEdgeState, CallbackEdgeStore
from .models import (
    AttemptRecord,
    AttemptState,
    MessageRecord,
    MessageState,
    ReplyRecord,
    ReplyTerminalStatus,
    SCHEMA_VERSION,
)
from .store import AttemptStore, MessageStore, ReplyStore

__all__ = [
    'AttemptRecord',
    'AttemptState',
    'AttemptStore',
    'CallbackEdgeRecord',
    'CallbackEdgeState',
    'CallbackEdgeStore',
    'MessageBureauControlService',
    'MessageBureauFacade',
    'MessageRecord',
    'MessageState',
    'MessageStore',
    'ReplyRecord',
    'ReplyStore',
    'ReplyTerminalStatus',
    'SCHEMA_VERSION',
]

# Try to import the Rust extension; if it is not built, fall back to Python.
try:
    import ccb_py_mailbox as _rust
    _RUST_AVAILABLE = True
except Exception:  # pragma: no cover - Rust extension may be absent in dev
    _rust = None  # type: ignore[assignment]
    _RUST_AVAILABLE = False


def _config_to_dict(config: Any | None) -> dict[str, Any] | None:
    """Convert a Python config object into a JSON-serializable dict for Rust."""
    if config is None:
        return None
    if isinstance(config, dict):
        return config
    if dataclasses.is_dataclass(config):
        return dataclasses.asdict(config)
    if hasattr(config, 'to_record'):
        return config.to_record()
    return vars(config)


def _to_dict(obj: Any) -> Any:
    """Best-effort conversion of Python dataclasses/namespaces into plain dicts."""
    if obj is None:
        return None
    if isinstance(obj, dict):
        return obj
    if dataclasses.is_dataclass(obj):
        return dataclasses.asdict(obj)
    if hasattr(obj, 'to_record'):
        return obj.to_record()
    return vars(obj)


def _message_dict_to_record(value: dict[str, Any] | None) -> MessageRecord | None:
    if value is None:
        return None
    value = dict(value)
    value.setdefault('schema_version', SCHEMA_VERSION)
    value.setdefault('record_type', 'message_record')
    return MessageRecord.from_record(value)


def _attempt_dict_to_record(value: dict[str, Any] | None) -> AttemptRecord | None:
    if value is None:
        return None
    value = dict(value)
    value.setdefault('schema_version', SCHEMA_VERSION)
    value.setdefault('record_type', 'attempt_record')
    return AttemptRecord.from_record(value)


def _reply_dict_to_record(value: dict[str, Any] | None) -> ReplyRecord | None:
    if value is None:
        return None
    value = dict(value)
    value.setdefault('schema_version', SCHEMA_VERSION)
    value.setdefault('record_type', 'reply_record')
    return ReplyRecord.from_record(value)


def _callback_edge_dict_to_record(value: dict[str, Any] | None) -> CallbackEdgeRecord | None:
    if value is None:
        return None
    value = dict(value)
    value.setdefault('schema_version', 1)
    value.setdefault('record_type', 'callback_edge')
    return CallbackEdgeRecord.from_record(value)


class _RustMessageBureauFacade:
    """Adapter that delegates to ``ccb_py_mailbox.message_bureau.MessageBureauFacade``."""

    def __init__(
        self,
        layout: Any,
        *,
        config=None,
        clock: Any,
        message_store: MessageStore | None = None,
        attempt_store: AttemptStore | None = None,
        reply_store: ReplyStore | None = None,
        callback_edge_store: CallbackEdgeStore | None = None,
        mailbox_store: Any | None = None,
        inbound_store: Any | None = None,
        lease_store: Any | None = None,
        mailbox_kernel: Any | None = None,
    ) -> None:
        # The Rust service builds its own PathLayout and stores.  We keep Python
        # store instances as attributes so code that directly accesses
        # ``facade._message_store`` etc. continues to work without changes.
        self._layout = layout
        self._clock = clock
        self._message_store = message_store or MessageStore(layout)
        self._attempt_store = attempt_store or AttemptStore(layout)
        self._reply_store = reply_store or ReplyStore(layout)
        self._callback_edge_store = callback_edge_store or CallbackEdgeStore(layout)

        from mailbox_kernel import (
            DeliveryLeaseStore,
            InboundEventStore,
            MailboxKernelService,
            MailboxStore,
        )

        self._mailbox_store = mailbox_store or MailboxStore(layout)
        self._inbound_store = inbound_store or InboundEventStore(layout)
        self._lease_store = lease_store or DeliveryLeaseStore(layout)
        self._mailbox_kernel = mailbox_kernel or MailboxKernelService(
            layout,
            clock=clock,
            mailbox_store=self._mailbox_store,
            inbound_store=self._inbound_store,
            lease_store=self._lease_store,
        )
        self._inner = _rust.message_bureau.MessageBureauFacade(
            str(layout.project_root), _config_to_dict(config)
        )

    def record_submission(
        self,
        request: Any,
        jobs: tuple[Any, ...] | list[Any],
        *,
        submission_id: str | None = None,
        accepted_at: str,
        origin_message_id: str | None = None,
    ) -> str | None:
        request_dict = _to_dict(request)
        job_dicts = [_to_dict(job) for job in jobs]
        return self._inner.record_submission(
            request_dict,
            job_dicts,
            accepted_at,
            submission_id,
            origin_message_id,
        )

    def claimable_request_job_ids(self, agent_name: str) -> tuple[str, ...]:
        return tuple(self._inner.claimable_request_job_ids(agent_name))

    def get_message(self, message_id: str) -> MessageRecord | None:
        return _message_dict_to_record(self._inner.get_message(message_id))

    def all_messages(self) -> tuple[MessageRecord, ...]:
        return tuple(
            _message_dict_to_record(value) for value in self._inner.all_messages()
        )

    def mark_attempt_started(self, job: Any, *, started_at: str) -> None:
        self._inner.mark_attempt_started(_to_dict(job), started_at)

    def record_attempt_terminal(
        self, job: Any, decision: Any, *, finished_at: str
    ) -> None:
        self._inner.record_attempt_terminal(
            _to_dict(job), _to_dict(decision), finished_at
        )

    def record_reply(
        self,
        job: Any,
        decision: Any,
        *,
        finished_at: str,
        deliver_to_caller: bool = True,
    ) -> str | None:
        return self._inner.record_reply(
            _to_dict(job), _to_dict(decision), finished_at, deliver_to_caller
        )

    def record_notice(
        self,
        job: Any,
        *,
        reply: str,
        diagnostics: dict[str, Any] | None = None,
        finished_at: str,
        terminal_status: ReplyTerminalStatus = ReplyTerminalStatus.INCOMPLETE,
        deliver_to_actor: str | None = None,
    ) -> str | None:
        return self._inner.record_notice(
            _to_dict(job),
            reply,
            finished_at=finished_at,
            diagnostics=diagnostics,
            terminal_status=terminal_status,
            deliver_to_actor=deliver_to_actor,
        )

    def record_terminal(
        self,
        job: Any,
        decision: Any,
        *,
        finished_at: str,
        deliver_to_caller: bool = True,
        record_reply: bool = True,
    ) -> str | None:
        return self._inner.record_terminal(
            _to_dict(job),
            _to_dict(decision),
            finished_at,
            deliver_to_caller,
            record_reply,
        )

    def record_retry_attempt(
        self, message_id: str, job: Any, *, accepted_at: str
    ) -> str:
        return self._inner.record_retry_attempt(
            message_id, _to_dict(job), accepted_at
        )

    def set_message_state(
        self, message_id: str, next_state: Any, *, updated_at: str
    ) -> None:
        self._inner.set_message_state(message_id, next_state, updated_at)

    def record_callback_edge(self, edge: CallbackEdgeRecord) -> None:
        self._inner.record_callback_edge(_to_dict(edge))

    def callback_edge_for_child_job(
        self, child_job_id: str
    ) -> CallbackEdgeRecord | None:
        return _callback_edge_dict_to_record(
            self._inner.callback_edge_for_child_job(child_job_id)
        )

    def callback_edge_for_child_message(
        self, child_message_id: str
    ) -> CallbackEdgeRecord | None:
        return _callback_edge_dict_to_record(
            self._inner.callback_edge_for_child_message(child_message_id)
        )

    def callback_edge_for_parent_job(
        self, parent_job_id: str
    ) -> CallbackEdgeRecord | None:
        return _callback_edge_dict_to_record(
            self._inner.callback_edge_for_parent_job(parent_job_id)
        )

    def update_callback_edge(
        self, edge: CallbackEdgeRecord, **changes: Any
    ) -> CallbackEdgeRecord:
        return _callback_edge_dict_to_record(
            self._inner.update_callback_edge(_to_dict(edge), changes)
        )

    def callback_edge(self, edge_id: str) -> CallbackEdgeRecord | None:
        return _callback_edge_dict_to_record(self._inner.callback_edge(edge_id))

    def pending_callback_edges(self) -> tuple[CallbackEdgeRecord, ...]:
        return tuple(
            _callback_edge_dict_to_record(value)
            for value in self._inner.pending_callback_edges()
        )


class _RustMessageBureauControlService:
    """Adapter that delegates to ``ccb_py_mailbox.message_bureau.MessageBureauControlService``."""

    def __init__(
        self,
        layout: Any,
        config: Any,
        *,
        mailbox_store: Any | None = None,
        inbound_store: Any | None = None,
        lease_store: Any | None = None,
        message_store: MessageStore | None = None,
        attempt_store: AttemptStore | None = None,
        reply_store: ReplyStore | None = None,
        job_store: Any | None = None,
        submission_store: Any | None = None,
        mailbox_kernel: Any | None = None,
        clock: Any | None = None,
    ) -> None:
        self._layout = layout
        self._config = config
        self._clock = clock

        from mailbox_kernel import (
            DeliveryLeaseStore,
            InboundEventStore,
            MailboxKernelService,
            MailboxStore,
        )
        from jobs.store import JobStore, SubmissionStore

        self._mailbox_store = mailbox_store or MailboxStore(layout)
        self._inbound_store = inbound_store or InboundEventStore(layout)
        self._lease_store = lease_store or DeliveryLeaseStore(layout)
        self._message_store = message_store or MessageStore(layout)
        self._attempt_store = attempt_store or AttemptStore(layout)
        self._reply_store = reply_store or ReplyStore(layout)
        self._job_store = job_store or JobStore(layout)
        self._submission_store = submission_store or SubmissionStore(layout)
        self._mailbox_kernel = mailbox_kernel or MailboxKernelService(
            layout,
            clock=clock,
            mailbox_store=self._mailbox_store,
            inbound_store=self._inbound_store,
            lease_store=self._lease_store,
        )
        self._inner = _rust.message_bureau.MessageBureauControlService(
            str(layout.project_root), _config_to_dict(config)
        )

    def queue_summary(
        self, target: str = 'all', *, detail: bool | None = None
    ) -> dict[str, Any]:
        return self._inner.queue_summary(target, detail)

    def agent_queue(self, agent_name: str) -> dict[str, Any]:
        return self._inner.agent_queue(agent_name)

    def trace(self, target: str) -> dict[str, Any]:
        return self._inner.trace(target)

    def inbox(
        self, agent_name: str, *, detail: bool | None = None
    ) -> dict[str, Any]:
        return self._inner.inbox(agent_name, detail)

    def mailbox_head(self, agent_name: str) -> dict[str, Any]:
        return self._inner.mailbox_head(agent_name)

    def ack_reply(
        self, agent_name: str, inbound_event_id: str | None = None
    ) -> dict[str, Any]:
        return self._inner.ack_reply(agent_name, inbound_event_id)


_CCB_MESSAGE_BUREAU_RUST = (
    os.environ.get('CCB_MESSAGE_BUREAU_RUST', '').strip().lower()
)
# Default to the proven Python backend.  Rust backend is opt-in only.
if _CCB_MESSAGE_BUREAU_RUST in {'1', 'true', 'yes', 'on'}:
    _USE_RUST = True
else:
    _USE_RUST = False

if _USE_RUST:
    if not _RUST_AVAILABLE:
        raise ImportError(
            'CCB_MESSAGE_BUREAU_RUST=1 but ccb_py_mailbox is not installed'
        )
    MessageBureauFacade = _RustMessageBureauFacade
    MessageBureauControlService = _RustMessageBureauControlService
else:
    from .facade import MessageBureauFacade
    from .control import MessageBureauControlService
