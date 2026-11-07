from __future__ import annotations

from dataclasses import replace

import pytest

from cli.render_runtime.mailbox_views_runtime.queue import render_queue


def _types():
    from execution_phase import ExecutionPhaseEvidence, derive_execution_phase

    return ExecutionPhaseEvidence, derive_execution_phase


def _active_evidence(**changes):
    evidence_type, _derive = _types()
    baseline = evidence_type(
        job_id="job-1",
        job_agent="agent1",
        job_status="running",
        attempt_id="attempt-1",
        attempt_job_id="job-1",
        attempt_agent="agent1",
        attempt_state="running",
        inbound_event_id="inbound-1",
        inbound_attempt_id="attempt-1",
        inbound_agent="agent1",
        inbound_status="delivering",
        mailbox_agent="agent1",
        mailbox_state="delivering",
        mailbox_head_inbound_event_id="inbound-1",
        mailbox_active_inbound_event_id="inbound-1",
        lease_agent="agent1",
        lease_inbound_event_id="inbound-1",
        lease_state="acquired",
        completion_job_id="job-1",
        completion_agent="agent1",
        completion_anchor_seen=True,
        completion_terminal=False,
        provider_state="active",
        provider_identity_current=True,
    )
    return replace(baseline, **changes)


@pytest.mark.parametrize(
    ("evidence", "expected_phase", "expected_reason"),
    (
        (
            {
                "job_status": "queued",
                "attempt_state": "pending",
                "inbound_status": "queued",
                "mailbox_state": "blocked",
                "mailbox_active_inbound_event_id": None,
                "lease_inbound_event_id": None,
                "lease_state": None,
                "completion_job_id": None,
                "completion_agent": None,
                "completion_anchor_seen": None,
                "completion_terminal": None,
                "provider_state": None,
                "provider_identity_current": None,
            },
            "queued",
            "queued_lineage_confirmed",
        ),
        (
            {
                "completion_anchor_seen": False,
                "provider_state": None,
                "provider_identity_current": False,
            },
            "injecting",
            "request_anchor_not_seen",
        ),
        ({}, "executing", "provider_active"),
        (
            {"provider_state": "idle"},
            "provider_idle_pending_terminal",
            "provider_idle_terminal_pending",
        ),
        (
            {"provider_state": "idle", "orphan_suspected": True},
            "orphaned",
            "provider_idle_without_terminal",
        ),
        (
            {"job_status": "completed", "completion_terminal": True},
            "terminal",
            "job_completed",
        ),
        (
            {
                "job_status": "completed",
                "completion_terminal": True,
                "reply_expected": True,
            },
            "reply_queued",
            "reply_delivery_pending",
        ),
        (
            {
                "job_status": "completed",
                "completion_terminal": True,
                "reply_expected": True,
                "reply_delivery_job_id": "delivery-1",
                "reply_delivery_source_job_id": "job-1",
                "reply_delivery_status": "running",
            },
            "reply_delivering",
            "reply_delivery_running",
        ),
        (
            {
                "job_status": "completed",
                "completion_terminal": True,
                "reply_expected": True,
                "reply_delivery_job_id": "delivery-1",
                "reply_delivery_source_job_id": "job-1",
                "reply_delivery_status": "completed",
            },
            "terminal",
            "reply_delivery_completed",
        ),
    ),
)
def test_execution_phase_vocabulary(evidence, expected_phase, expected_reason) -> None:
    _evidence_type, derive = _types()

    result = derive(_active_evidence(**evidence))

    assert (result.phase, result.reason) == (expected_phase, expected_reason)


@pytest.mark.parametrize(
    ("changes", "expected_reason"),
    (
        ({"attempt_job_id": "job-other"}, "attempt_job_mismatch"),
        ({"inbound_attempt_id": "attempt-other"}, "inbound_attempt_mismatch"),
        ({"mailbox_active_inbound_event_id": "inbound-other"}, "mailbox_active_mismatch"),
        ({"mailbox_agent": "agent-other"}, "mailbox_agent_mismatch"),
        ({"mailbox_state": "blocked"}, "mailbox_state_mismatch"),
        ({"lease_inbound_event_id": "inbound-other"}, "lease_inbound_mismatch"),
        ({"lease_agent": "agent-other"}, "lease_agent_mismatch"),
        ({"lease_state": "expired"}, "lease_not_acquired"),
        ({"completion_job_id": "job-other"}, "completion_job_mismatch"),
        ({"completion_job_id": "JOB-1"}, "completion_job_mismatch"),
        ({"provider_identity_current": False}, "provider_identity_mismatch"),
        ({"provider_identity_current": None}, "provider_identity_unavailable"),
    ),
)
def test_contradictory_active_evidence_is_unknown(changes, expected_reason) -> None:
    _evidence_type, derive = _types()

    result = derive(_active_evidence(**changes))

    assert (result.phase, result.reason) == ("unknown", expected_reason)


def test_terminal_authority_wins_over_lagging_active_mailbox() -> None:
    _evidence_type, derive = _types()

    result = derive(
        _active_evidence(
            job_status="completed",
            completion_terminal=True,
            mailbox_active_inbound_event_id="stale-inbound",
            lease_inbound_event_id="stale-inbound",
        )
    )

    assert (result.phase, result.reason) == ("terminal", "job_completed")


def test_completion_terminal_wins_during_job_publication_lag() -> None:
    _evidence_type, derive = _types()

    result = derive(_active_evidence(completion_terminal=True))

    assert (result.phase, result.reason) == ("terminal", "completion_terminal")


def test_queue_cli_prefers_execution_phase_and_keeps_mailbox_state() -> None:
    lines = render_queue(
        {
            "target": "all",
            "agent_count": 1,
            "queued_agent_count": 0,
            "active_agent_count": 1,
            "total_queue_depth": 1,
            "total_pending_reply_count": 0,
            "agents": [
                {
                    "agent_name": "agent1",
                    "runtime_state": "busy",
                    "runtime_health": "healthy",
                    "mailbox_state": "delivering",
                    "execution_phase": "executing",
                    "queue_depth": 1,
                    "pending_reply_count": 0,
                    "summary_status": "ok",
                }
            ],
        }
    )

    queue_agent = next(line for line in lines if line.startswith("queue_agent:"))
    assert "phase=executing" in queue_agent
    assert "mailbox_state=delivering" in queue_agent


def test_queue_cli_falls_back_to_mailbox_state_for_older_payload() -> None:
    lines = render_queue(
        {
            "target": "agent1",
            "agent": {
                "agent_name": "agent1",
                "mailbox_id": "mbx_agent1",
                "summary_status": "ok",
                "mailbox_state": "delivering",
                "runtime_state": "busy",
                "runtime_health": "healthy",
                "lease_version": 2,
                "queue_depth": 1,
                "pending_reply_count": 0,
                "active_inbound_event_id": "inbound-1",
                "last_inbound_started_at": None,
                "last_inbound_finished_at": None,
                "queued_events": [],
            },
        }
    )

    assert "execution_phase: delivering" in lines
    assert "mailbox_state: delivering" in lines
