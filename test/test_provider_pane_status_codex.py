from __future__ import annotations

from pathlib import Path

from provider_pane_status.codex_pane import (
    USAGE_LIMIT_MARKERS,
    parse_codex_pane_status,
    parse_retry_after,
)
from provider_pane_status.models import (
    PaneCompletionEvidence,
    ProviderPaneStatusSignal,
    SOURCE_STATUS_ERROR,
    SOURCE_STATUS_OK,
)


def test_codex_parser_direct_module_keeps_body_text_unknown() -> None:
    status = parse_codex_pane_status(
        "\n".join(
            [
                "› Explain this status text",
                "",
                "The UI can show • Working (9m 47s • esc to interrupt).",
                "",
                "› Use /skills to list available skills",
            ]
        )
    )

    assert status.state == "unknown"
    assert status.reason == "no_known_status_pattern"
    assert status.completion_evidence is None


def test_codex_parser_direct_module_exposes_worked_for_as_observation_only() -> None:
    status = parse_codex_pane_status("• Worked for 4s\n")
    record = status.to_record()

    assert status.state == "completed"
    assert status.terminal_outcome == "completed"
    assert isinstance(status.completion_evidence, PaneCompletionEvidence)
    assert status.completion_evidence.__not_a_job_terminator__() is None
    assert record["completion_evidence"] == {
        "outcome": "completed",
        "reason": "codex_worked_for_terminal_summary",
        "source": "codex_pane",
    }


def test_provider_signal_separates_capture_error_from_parse_unknown() -> None:
    parsed_unknown = ProviderPaneStatusSignal(
        provider="codex",
        source_status=SOURCE_STATUS_OK,
        parsed_state="unknown",
        reason="no_known_status_pattern",
    )
    source_error = ProviderPaneStatusSignal(
        provider="codex",
        source_status=SOURCE_STATUS_ERROR,
        parsed_state="unknown",
        reason="tmux_capture_failed",
    )

    assert parsed_unknown.to_record()["source_status"] == "ok"
    assert source_error.to_record()["source_status"] == "error"
    assert parsed_unknown.to_record()["parsed_state"] == "unknown"
    assert source_error.to_record()["parsed_state"] == "unknown"


def test_probe_script_imports_shared_parser_without_local_codex_regex() -> None:
    script_path = Path(__file__).resolve().parents[1] / "scripts" / "probe_codex_pane_status.py"
    source = script_path.read_text(encoding="utf-8")

    assert "from provider_pane_status.codex_pane import" in source
    assert "STATUS_MARKER_RE" not in source
    assert "CODEX_WORKING_LINE_RE" not in source
    assert "CODEX_RECONNECT_LINE_RE" not in source
    assert "CODEX_TOOL_LINE_RE" not in source


# --- usage-limit / quota banner detection ---------------------------------


_USAGE_LIMIT_BANNER = (
    "You've hit your usage limit. Visit https://chatgpt.com/#pricing to "
    "purchase more credits or try again at Jul 2nd, 2026 10:21 AM."
)


def test_codex_parser_detects_real_usage_limit_banner_with_iso_retry_after() -> None:
    status = parse_codex_pane_status(_USAGE_LIMIT_BANNER)

    assert status.state == "usage_limit"
    assert status.reason == "provider_usage_limit"
    assert status.terminal_outcome == "failed"
    assert status.retry_after == "2026-07-02T10:21:00"
    assert isinstance(status.completion_evidence, PaneCompletionEvidence)
    assert status.completion_evidence.outcome == "failed"
    assert status.completion_evidence.source == "codex_pane"
    assert status.completion_evidence.reason == "provider_usage_limit"
    # matched_patterns should reference the banner markers actually present.
    joined = " ".join(status.matched_patterns)
    assert "usage limit" in joined or "try again at" in joined


def test_codex_parser_usage_limit_to_record_carries_retry_after() -> None:
    record = parse_codex_pane_status(_USAGE_LIMIT_BANNER).to_record()

    assert record["state"] == "usage_limit"
    assert record["retry_after"] == "2026-07-02T10:21:00"
    assert record["terminal_outcome"] == "failed"


def test_codex_parser_usage_limit_without_reset_date_leaves_retry_after_none() -> None:
    status = parse_codex_pane_status(
        "You have hit your usage limit. Please upgrade your plan to continue."
    )

    assert status.state == "usage_limit"
    assert status.retry_after is None


def test_codex_parser_usage_limit_handles_different_month_and_ordinal() -> None:
    status = parse_codex_pane_status(
        "Plan limit reached. You ran out of credits. try again at September 23rd, 2026 3:05 PM"
    )

    assert status.state == "usage_limit"
    assert status.retry_after == "2026-09-23T15:05:00"


def test_codex_parser_completed_still_wins_over_usage_limit_text() -> None:
    # A genuine terminal summary must not be misread as a usage-limit banner.
    status = parse_codex_pane_status("• Worked for 4s\nyou hit your usage limit in body only")

    assert status.state == "completed"
    assert status.terminal_outcome == "completed"


def test_parse_retry_after_helper_round_trips_and_returns_none_on_miss() -> None:
    assert parse_retry_after(
        "try again at Jul 2nd, 2026 10:21 AM"
    ) == "2026-07-02T10:21:00"
    assert parse_retry_after("no reset time here") is None
    assert parse_retry_after("") is None


def test_usage_limit_markers_tuple_is_present_and_nonempty() -> None:
    assert isinstance(USAGE_LIMIT_MARKERS, tuple)
    assert USAGE_LIMIT_MARKERS
    assert "try again at" in USAGE_LIMIT_MARKERS

