from __future__ import annotations

from .decisions import reply_delivery_failed_decision


def complete_reply_delivery_after_start(
    dispatcher,
    job,
    *,
    started_at: str,
    submission,
):
    mode = str((submission.runtime_state if submission is not None else {}).get('mode') or '').strip().lower()
    if submission is None or mode in {'error', 'passive'}:
        diagnostics = {
            'submission_mode': mode or 'missing',
        }
        if submission is not None:
            diagnostics['submission_reason'] = str(submission.runtime_state.get('reason') or submission.reason or '')
            diagnostics['submission_error'] = str(submission.runtime_state.get('error') or '')
        return dispatcher.complete(
            job.job_id,
            reply_delivery_failed_decision(
                job,
                finished_at=started_at,
                reason='reply_delivery_transport_unavailable',
                diagnostics=diagnostics,
            ),
        )

    # A successful send is transport progress, never processing completion.
    # Every delivery uses the provider's existing completion polling path,
    # just like an ordinary request. No elapsed-time delivery fallback may
    # release the target while its provider turn is still running.
    return job


__all__ = ['complete_reply_delivery_after_start']
