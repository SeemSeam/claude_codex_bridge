"""Native task admission; only completion events can finish the business job."""
from dataclasses import replace

from .reply_delivery import request


def dispatch(submission, state, *, now, append_dispatch):
    path = str(state.get('draft_guard_socket') or '')
    if not path:
        return None  # Legacy launch: retain the guarded terminal transport.
    actor, launch = str(state.get('actor') or ''), str(state.get('launch_session_id') or '')
    try:
        capability = request(path, actor, launch, 'task_capabilities')
    except (OSError, ValueError):
        return replace(submission, runtime_state=state)
    if capability.get('task_ingress_version') != 1:
        return None
    runtime, session = capability.get('runtime_instance_id'), capability.get('session_id')
    if not runtime or not session or runtime != state.get('runtime_instance_id'):
        return replace(submission, runtime_state=state)
    body = str(state.get('pending_prompt') or '')
    append_dispatch(state, prompt=body, now=now)
    # Set before I/O. A lost response must never fall back to terminal injection.
    state.update(prompt_sent=True, prompt_sent_at=now, task_native_transport=True,
                 task_native_session_id=session, task_admission='unknown_no_replay')
    try:
        result = request(path, actor, launch, 'task_deliver', delivery_id=submission.job_id,
                         runtime_instance_id=runtime, session_id=session, body=body)
    except (OSError, ValueError):
        result = {}
    bound = result.get('runtime_instance_id') == runtime and result.get('session_id') == session
    if bound and result.get('state') == 'blocked':
        # Native rejection guarantees that neither an intent nor a message was created.
        state.update(prompt_sent=False, task_admission='owner_busy_or_draft')
        return replace(submission, runtime_state=state)
    state['task_admission'] = str(result.get('state') or 'unknown_no_replay')
    state.pop('pending_prompt', None)
    return replace(submission, runtime_state=state)
