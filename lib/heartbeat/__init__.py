"""Heartbeat package with an env-gated Rust backend.

When the Rust ``ccb_py_heartbeat`` extension is installed and
``CCB_HEARTBEAT_RUST`` is not explicitly disabled, ``evaluate_heartbeat`` runs
inside the Rust extension module and its results are converted back to the
Python dataclasses that the rest of CCBD expects.  Otherwise the original
Python implementation is used.

This makes the package safe to import in source checkouts that do not have the
Rust extension built yet.
"""
from __future__ import annotations

import os
from typing import Any

from .models import (
    SCHEMA_VERSION,
    HeartbeatAction,
    HeartbeatDecision,
    HeartbeatPolicy,
    HeartbeatState,
)
from .store import HeartbeatStateStore

__all__ = [
    "HeartbeatAction",
    "HeartbeatDecision",
    "HeartbeatPolicy",
    "HeartbeatState",
    "HeartbeatStateStore",
    "SCHEMA_VERSION",
    "evaluate_heartbeat",
]

# Try to import the Rust extension; if it is not built, fall back to Python.
try:
    import ccb_py_heartbeat as _rust
    _RUST_AVAILABLE = True
except Exception:  # pragma: no cover - Rust extension may be absent in dev
    _rust = None  # type: ignore[assignment]
    _RUST_AVAILABLE = False


def _evaluate_heartbeat_python(
    *,
    policy: Any,
    subject_kind: str,
    subject_id: str,
    owner: str,
    observed_last_progress_at: str,
    now: str,
    state: Any | None = None,
) -> tuple[HeartbeatState, HeartbeatDecision]:
    # Local import avoids a circular reference when this module re-exports the
    # Python implementation below.
    from .engine import evaluate_heartbeat as _evaluate_heartbeat_impl
    return _evaluate_heartbeat_impl(
        policy=policy,
        subject_kind=subject_kind,
        subject_id=subject_id,
        owner=owner,
        observed_last_progress_at=observed_last_progress_at,
        now=now,
        state=state,
    )


if _RUST_AVAILABLE:
    _RUST_ACTIONS = (
        (_rust.HeartbeatAction.Idle, "idle"),
        (_rust.HeartbeatAction.Reset, "reset"),
        (_rust.HeartbeatAction.Enter, "enter"),
        (_rust.HeartbeatAction.Repeat, "repeat"),
    )

    def _rust_action_to_value(action: Any) -> str:
        for variant, value in _RUST_ACTIONS:
            if action == variant:
                return value
        raise ValueError(f"unknown rust HeartbeatAction: {action!r}")

    def _py_policy_to_rust(policy: Any) -> Any:
        if isinstance(policy, _rust.HeartbeatPolicy):
            return policy
        return _rust.HeartbeatPolicy(
            policy.silence_start_after_s,
            policy.repeat_interval_s,
            policy.max_notice_count,
        )

    def _py_state_to_rust(state: Any | None) -> Any | None:
        if state is None or isinstance(state, _rust.HeartbeatState):
            return state
        return _rust.HeartbeatState(
            state.subject_kind,
            state.subject_id,
            state.owner,
            state.last_progress_at,
            state.last_notice_at,
            state.heartbeat_started_at,
            state.notice_count,
            state.updated_at,
        )

    def _rust_state_to_py(state: Any) -> HeartbeatState:
        return HeartbeatState(
            subject_kind=state.subject_kind,
            subject_id=state.subject_id,
            owner=state.owner,
            last_progress_at=state.last_progress_at,
            last_notice_at=state.last_notice_at,
            heartbeat_started_at=state.heartbeat_started_at,
            notice_count=state.notice_count,
            updated_at=state.updated_at,
        )

    def _rust_decision_to_py(decision: Any) -> HeartbeatDecision:
        return HeartbeatDecision(
            action=HeartbeatAction(_rust_action_to_value(decision.action)),
            subject_kind=decision.subject_kind,
            subject_id=decision.subject_id,
            owner=decision.owner,
            last_progress_at=decision.last_progress_at,
            last_notice_at=decision.last_notice_at,
            silence_seconds=decision.silence_seconds,
            notice_count=decision.notice_count,
        )

    def _evaluate_heartbeat_rust(
        *,
        policy: Any,
        subject_kind: str,
        subject_id: str,
        owner: str,
        observed_last_progress_at: str,
        now: str,
        state: Any | None = None,
    ) -> tuple[HeartbeatState, HeartbeatDecision]:
        rust_policy = _py_policy_to_rust(policy)
        rust_state = _py_state_to_rust(state)
        next_state, decision = _rust.evaluate_heartbeat(
            rust_policy,
            subject_kind,
            subject_id,
            owner,
            observed_last_progress_at,
            now,
            rust_state,
        )
        return _rust_state_to_py(next_state), _rust_decision_to_py(decision)


_CCB_HEARTBEAT_RUST = os.environ.get("CCB_HEARTBEAT_RUST", "").strip().lower()
if _CCB_HEARTBEAT_RUST in {"0", "false", "no", "off"}:
    _USE_RUST = False
elif _CCB_HEARTBEAT_RUST in {"1", "true", "yes", "on"}:
    _USE_RUST = True
else:
    # Auto: use Rust when the extension is present, otherwise Python.
    _USE_RUST = _RUST_AVAILABLE

if _USE_RUST:
    if not _RUST_AVAILABLE:
        raise ImportError(
            "CCB_HEARTBEAT_RUST is enabled but ccb_py_heartbeat is not installed"
        )
    evaluate_heartbeat = _evaluate_heartbeat_rust
else:
    evaluate_heartbeat = _evaluate_heartbeat_python
