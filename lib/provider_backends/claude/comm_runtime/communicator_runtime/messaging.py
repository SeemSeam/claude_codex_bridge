from __future__ import annotations

import time


def ask_async(comm, question: str) -> bool:
    try:
        ensure_healthy_session(comm)
        comm._send_via_terminal(question)
        emit_async_success()
        return True
    except Exception as exc:
        print(f"❌ Send failed: {exc}")
        return False


def ask_sync(
    comm,
    question: str,
    timeout: int | None = None,
    *,
    req_id_factory,
    wrap_prompt_fn,
    is_done_text_fn,
    strip_done_text_fn,
) -> str | None:
    try:
        ensure_healthy_session(comm)
        req_id, prompt, state = sync_request_context(
            comm,
            question,
            req_id_factory=req_id_factory,
            wrap_prompt_fn=wrap_prompt_fn,
        )
        comm._send_via_terminal(prompt)
        latest, done_seen = wait_for_sync_reply(
            comm,
            state,
            req_id=req_id,
            timeout=timeout,
            is_done_text_fn=is_done_text_fn,
        )
        if done_seen:
            remember_current_session(comm)
        return cleaned_reply(
            latest,
            req_id=req_id,
            strip_done_text_fn=strip_done_text_fn,
        )
    except Exception as exc:
        print(f"❌ Send failed: {exc}")
        return None


def ping(comm, *, display: bool = True) -> tuple[bool, str]:
    healthy, msg = comm._check_session_health_impl(probe_terminal=True)
    if display:
        print(msg)
    return healthy, msg


def ensure_healthy_session(comm) -> None:
    healthy, status = comm._check_session_health_impl(probe_terminal=False)
    if not healthy:
        raise RuntimeError(f"❌ Session error: {status}")


def emit_async_success() -> None:
    print("📤 Written to Claude, delivery unconfirmed")
    print("Hint: `ccb pend <agent|job_id>` is only a supplementary observer view, not an authoritative completion path")


def sync_request_context(comm, question: str, *, req_id_factory, wrap_prompt_fn):
    req_id = req_id_factory()
    prompt = wrap_prompt_fn(question, req_id)
    state = comm.log_reader.capture_state()
    return req_id, prompt, state


def wait_for_sync_reply(
    comm,
    state,
    *,
    req_id: str,
    timeout: int | None,
    is_done_text_fn,
) -> tuple[str, bool]:
    deadline = sync_deadline(comm, timeout)
    latest = ""
    done_seen = False
    while True:
        wait_step = next_wait_step(deadline)
        if wait_step is None:
            break
        reply, state = comm.log_reader.wait_for_message(state, timeout=wait_step)
        if reply is None:
            continue
        latest = str(reply)
        if is_done_text_fn(latest, req_id):
            done_seen = True
            break
    return latest, done_seen


def sync_deadline(comm, timeout: int | None) -> float | None:
    wait_timeout = comm.timeout if timeout is None else int(timeout)
    if wait_timeout < 0:
        return None
    return time.time() + wait_timeout


def next_wait_step(deadline: float | None) -> float | None:
    if deadline is None:
        return 1.0
    remaining = deadline - time.time()
    if remaining <= 0:
        return None
    return min(remaining, 1.0)


def remember_current_session(comm) -> None:
    session_path = comm.log_reader.current_session_path()
    if session_path:
        comm._remember_claude_session(session_path)


def cleaned_reply(latest: str, *, req_id: str, strip_done_text_fn) -> str | None:
    if not latest:
        return None
    return strip_done_text_fn(latest, req_id)


__all__ = ["ask_async", "ask_sync", "ping"]
