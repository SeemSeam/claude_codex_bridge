from __future__ import annotations

from collections.abc import Mapping

from ccbd.no_reply_reason import NoReplyReason, describe_reason


def render_why(payload: Mapping[str, object]) -> tuple[str, ...]:
    job = payload.get("job")
    if not isinstance(job, Mapping):
        return ("why_status: error", "error: job record not available")

    status = str(job.get("status") or "")
    no_reply_reason = str(job.get("no_reply_reason") or "").strip()
    terminal_decision = job.get("terminal_decision")

    lines = [
        "why_status: ok",
        f"job_id: {job.get('job_id')}",
        f"agent: {job.get('agent_name')}",
        f"status: {status}",
    ]

    if not no_reply_reason and isinstance(terminal_decision, Mapping):
        diagnostics = terminal_decision.get("diagnostics") or {}
        if isinstance(diagnostics, Mapping):
            no_reply_reason = str(diagnostics.get("no_reply_reason") or "").strip()

    if not no_reply_reason:
        if status == "completed":
            lines.append("no_reply_reason: (job completed successfully)")
        else:
            lines.append("no_reply_reason: (not classified)")
        return tuple(lines)

    try:
        reason = NoReplyReason(no_reply_reason)
        label, triage = describe_reason(reason)
    except ValueError:
        label = "未知分类 / unknown classification"
        triage = "请联系运维 / contact operator."

    lines.extend(
        [
            f"no_reply_reason: {no_reply_reason}",
            f"description: {label}",
            f"triage: {triage}",
        ]
    )
    detail = job.get("no_reply_detail")
    if isinstance(detail, Mapping) and detail:
        lines.append(f"detail: {detail}")
    return tuple(lines)


__all__ = ["render_why"]
