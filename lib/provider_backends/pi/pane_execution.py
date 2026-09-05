from __future__ import annotations

import hashlib
import json
import os
import uuid
from dataclasses import replace
from datetime import datetime
from pathlib import Path

from ccbd.api_models import JobRecord
from completion.models import (
    CompletionConfidence,
    CompletionCursor,
    CompletionDecision,
    CompletionItemKind,
    CompletionSourceKind,
    CompletionStatus,
)
from provider_core.protocol import request_anchor_for_job
from provider_execution.active import ensure_active_pane_alive, prepare_active_start
from provider_execution.base import (
    ProviderPollResult,
    ProviderRuntimeContext,
    ProviderSubmission,
)
from provider_execution.common import (
    build_item,
    interrupt_and_clear_runtime_target,
    no_wrap_requested,
    send_prompt_to_runtime_target,
)
from terminal_runtime import get_backend_for_session

from provider_backends.native_cli_support.prompt import (
    clean_native_reply,
    wrap_native_prompt,
)

from .pane_events import (
    PiAssistantSnapshot,
    assistant_snapshot,
    event_matches_runtime,
    inspect_pi_runtime,
    normalized_event_type,
    read_pi_events,
)
from .session import load_project_session, persist_native_session_binding

PI_PANE_MODE = "pi_pane"
PI_EXTENSION_READY_TIMEOUT_ENV = "CCB_PI_EXTENSION_READY_TIMEOUT_S"
PI_EXTENSION_READY_TIMEOUT_DEFAULT = 30.0


class PiPaneExecutionAdapter:
    provider = "pi"
    restart_resume_supported = True

    def __init__(
        self,
        *,
        provider: str = "pi",
        pane_mode: str = PI_PANE_MODE,
        session_field_prefix: str = "pi",
        session_loader=None,
        terminal_event_type: str = "agent_settled",
        terminal_authority: str = "pi_extension_agent_settled",
        extension_ready_timeout_env: str = PI_EXTENSION_READY_TIMEOUT_ENV,
        extension_ready_timeout_default: float = PI_EXTENSION_READY_TIMEOUT_DEFAULT,
        persist_native_session: bool = True,
        intermediate_stop_reasons: tuple[str, ...] = (),
    ) -> None:
        self.provider = provider
        self.pane_mode = pane_mode
        self.session_field_prefix = session_field_prefix
        self.session_loader = session_loader
        self.terminal_event_type = terminal_event_type
        self.terminal_authority = terminal_authority
        self.extension_ready_timeout_env = extension_ready_timeout_env
        self.extension_ready_timeout_default = extension_ready_timeout_default
        self.persist_native_session = persist_native_session
        self.intermediate_stop_reasons = frozenset(intermediate_stop_reasons)

    def restore_diagnostics(self) -> dict[str, object]:
        return {
            "resume_supported": True,
            "restore_mode": "exact_native_session",
            "restore_reason": f"{self.provider}_native_session_resume",
            "restore_detail": (
                f"{self.provider} pane jobs rebind to the exact live lifecycle "
                "extension instance after execution-service restore"
            ),
        }

    def start(
        self,
        job: JobRecord,
        *,
        context: ProviderRuntimeContext | None,
        now: str,
    ) -> ProviderSubmission:
        prepared = prepare_active_start(
            job,
            context=context,
            provider=self.provider,
            source_kind=CompletionSourceKind.SESSION_EVENT_LOG,
            now=now,
            missing_session_reason=f"missing_{self.provider}_session",
            load_session_fn=self._load_session,
            backend_for_session_fn=get_backend_for_session,
        )
        if isinstance(prepared, ProviderSubmission):
            return prepared

        session_data = getattr(prepared.session, "data", None)
        if not isinstance(session_data, dict):
            return _start_error(
                job,
                now=now,
                provider=self.provider,
                reason=f"{self.provider}_runtime_state_missing",
                error=f"{self.provider}_session_payload_invalid",
            )
        launch_session_id = _text(
            session_data.get("ccb_session_id")
            or session_data.get(f"{self.session_field_prefix}_session_id")
        )
        event_path = _path_value(
            session_data.get(f"{self.session_field_prefix}_completion_event_log")
        )
        dispatch_path = _path_value(
            session_data.get(f"{self.session_field_prefix}_dispatch_event_log")
        )
        runtime_dir = _path_value(session_data.get("runtime_dir"))
        path_error = _validate_runtime_paths(
            runtime_dir,
            provider=self.provider,
            event_path=event_path,
            dispatch_path=dispatch_path,
        )
        if not launch_session_id or path_error:
            return _start_error(
                job,
                now=now,
                provider=self.provider,
                reason=f"{self.provider}_completion_extension_unavailable",
                error=path_error or f"{self.provider}_launch_session_id_missing",
            )

        request_anchor = request_anchor_for_job(job.job_id)
        no_wrap = no_wrap_requested(getattr(job, "provider_options", None))
        prompt = _pane_prompt(
            job.request.body or "",
            request_anchor=request_anchor,
            no_wrap=no_wrap,
        )
        reply_delivery = (
            _text(getattr(job.request, "message_type", "")).lower()
            == "reply_delivery"
        )
        actor = _text(job.agent_name)
        state: dict[str, object] = {
            "mode": self.pane_mode,
            "provider": self.provider,
            "session_field_prefix": self.session_field_prefix,
            "terminal_authority": self.terminal_authority,
            "extension_ready_timeout_env": self.extension_ready_timeout_env,
            "extension_ready_timeout_default": self.extension_ready_timeout_default,
            "persist_native_session": self.persist_native_session,
            "backend": prepared.backend,
            "pane_id": prepared.pane_id,
            "work_dir": str(prepared.work_dir),
            "actor": actor,
            "project_id": _text(session_data.get("ccb_project_id")),
            "launch_session_id": launch_session_id,
            "runtime_instance_id": "",
            "session_file": str(getattr(prepared.session, "session_file", "") or ""),
            "session_dir": _text(
                session_data.get(f"{self.session_field_prefix}_session_dir")
            ),
            "event_path": str(event_path),
            "dispatch_path": str(dispatch_path),
            "event_offset": 0,
            "request_anchor": request_anchor,
            "started_at": now,
            "ready_wait_started_at": now,
            "prompt_sent": False,
            "prompt_sent_at": "",
            "pending_prompt": prompt,
            "prompt_sha256": hashlib.sha256(
                prompt.encode("utf-8", "replace")
            ).hexdigest(),
            "anchor_seen": False,
            "no_wrap": no_wrap,
            "reply_delivery_complete_on_dispatch": reply_delivery,
            "last_assistant_message": "",
            "last_assistant_signature": "",
            "last_outcome_reason": "",
            "last_outcome_error": "",
            "provider_turn_ref": "",
            "next_seq": 1,
        }
        submission = ProviderSubmission(
            job_id=job.job_id,
            agent_name=job.agent_name,
            provider=self.provider,
            accepted_at=now,
            ready_at=now,
            source_kind=CompletionSourceKind.SESSION_EVENT_LOG,
            reply="",
            diagnostics={
                "provider": self.provider,
                "mode": self.pane_mode,
                "workspace_path": str(prepared.work_dir),
                "pane_id": prepared.pane_id,
                "event_path": str(event_path),
                "launch_session_id": launch_session_id,
            },
            runtime_state=state,
        )
        dispatched = _dispatch_if_ready(submission, state, now=now)
        if isinstance(dispatched, ProviderPollResult):
            decision = dispatched.decision
            return _start_error(
                job,
                now=now,
                provider=self.provider,
                reason=(
                    str(decision.reason)
                    if decision is not None
                    else f"{self.provider}_pane_start_failed"
                ),
                error=str(
                    (decision.diagnostics if decision is not None else {}).get(
                        "send_error"
                    )
                    or (
                        decision.diagnostics
                        if decision is not None
                        else "pi pane start failed"
                    )
                ),
            )
        return dispatched

    def poll(
        self,
        submission: ProviderSubmission,
        *,
        now: str,
    ) -> ProviderPollResult | None:
        if _text(submission.runtime_state.get("mode")) != self.pane_mode:
            return None
        state = dict(submission.runtime_state)
        backend = state.get("backend")
        pane_id = _text(state.get("pane_id"))
        if backend is None or not pane_id:
            return _terminal_result(
                submission,
                state,
                now=now,
                status=CompletionStatus.FAILED,
                reason=f"{self.provider}_runtime_state_corrupt",
                reply="",
                confidence=CompletionConfidence.DEGRADED,
            )
        pane_dead = ensure_active_pane_alive(
            submission,
            backend=backend,
            pane_id=pane_id,
            now=now,
        )
        if pane_dead is not None:
            return pane_dead

        if not bool(state.get("prompt_sent")):
            updated = _dispatch_if_ready(submission, state, now=now)
            if isinstance(updated, ProviderSubmission):
                if updated == submission:
                    return None
                return ProviderPollResult(submission=updated)
            return updated

        if bool(state.get("reply_delivery_complete_on_dispatch")):
            return _reply_delivery_result(submission, state, now=now)

        event_path = Path(_text(state.get("event_path")))
        batch = read_pi_events(
            event_path,
            _int_value(state.get("event_offset"), 0),
        )
        if batch.protocol_error:
            return _terminal_result(
                submission,
                state,
                now=now,
                status=CompletionStatus.INCOMPLETE,
                reason=f"{self.provider}_native_protocol_invalid",
                reply="",
                confidence=CompletionConfidence.DEGRADED,
                diagnostics_extra={"protocol_error": batch.protocol_error},
            )

        items = []
        request_anchor = _text(state.get("request_anchor"))
        actor = _text(state.get("actor"))
        launch_session_id = _text(state.get("launch_session_id"))
        runtime_instance_id = _text(state.get("runtime_instance_id"))
        terminal_snapshot: PiAssistantSnapshot | None = None
        binding_error = ""
        superseded_by = ""

        for event in batch.events:
            if not event_matches_runtime(
                event,
                actor=actor,
                launch_session_id=launch_session_id,
            ):
                continue
            event_type = normalized_event_type(event)
            event_instance = _text(event.get("runtime_instance_id"))
            if event_type == "extension_ready":
                if event_instance and event_instance != runtime_instance_id:
                    return _terminal_result(
                        submission,
                        state,
                        now=now,
                        status=CompletionStatus.INCOMPLETE,
                        reason=f"{self.provider}_runtime_restarted",
                        reply="",
                        confidence=CompletionConfidence.DEGRADED,
                        diagnostics_extra={
                            "expected_runtime_instance_id": runtime_instance_id,
                            "observed_runtime_instance_id": event_instance,
                        },
                    )
                _persist_native_session_fields(
                    state,
                    native_session_id=_text(
                        event.get(f"{self.session_field_prefix}_session_id")
                    ),
                    native_session_path=_text(
                        event.get(f"{self.session_field_prefix}_session_path")
                    ),
                    observed_at=_text(event.get("timestamp")),
                )
                continue
            if event_instance != runtime_instance_id:
                continue

            event_req_id = _text(event.get("req_id"))
            event_anchor_req_id = _text(event.get("anchor_req_id"))
            event_dispatch_req_id = _text(event.get("dispatch_req_id"))
            if event_type == "binding_error" and request_anchor in {
                event_req_id,
                event_anchor_req_id,
                event_dispatch_req_id,
            }:
                binding_error = "dispatch_anchor_mismatch"
                break
            if event_type == "request_start":
                if (
                    event_req_id == request_anchor
                    or event_anchor_req_id == request_anchor
                ):
                    if not bool(event.get("dispatch_matched")):
                        binding_error = "dispatch_not_matched"
                        break
                    if event_req_id != request_anchor:
                        binding_error = "dispatch_anchor_mismatch"
                        break
                    if not bool(state.get("anchor_seen")):
                        state["anchor_seen"] = True
                        items.append(
                            build_item(
                                submission,
                                kind=CompletionItemKind.ANCHOR_SEEN,
                                timestamp=now,
                                seq=_next_seq(state),
                                payload={
                                    "turn_id": request_anchor,
                                    "source": f"{self.provider}_completion_extension",
                                    "runtime_instance_id": runtime_instance_id,
                                },
                            )
                        )
                elif event_req_id and bool(state.get("anchor_seen")):
                    superseded_by = event_req_id
                    break
                continue
            if (
                event_type == "request_superseded"
                and event_req_id == request_anchor
            ):
                superseded_by = (
                    _text(event.get("superseded_by"))
                    or "unmanaged_input"
                )
                break
            if event_req_id != request_anchor:
                continue

            snapshot = assistant_snapshot(event)
            if snapshot is not None:
                _remember_assistant(state, snapshot)
            if event_type == "tool_start":
                items.append(
                    build_item(
                        submission,
                        kind=CompletionItemKind.TOOL_CALL,
                        timestamp=now,
                        seq=_next_seq(state),
                        payload={
                            "turn_id": request_anchor,
                            "tool_call_id": _text(event.get("tool_call_id")),
                            "tool_name": _text(event.get("tool_name")),
                        },
                    )
                )
            elif event_type == "tool_end":
                items.append(
                    build_item(
                        submission,
                        kind=CompletionItemKind.TOOL_RESULT,
                        timestamp=now,
                        seq=_next_seq(state),
                        payload={
                            "turn_id": request_anchor,
                            "tool_call_id": _text(event.get("tool_call_id")),
                            "tool_name": _text(event.get("tool_name")),
                            "is_error": bool(event.get("is_error")),
                        },
                    )
                )
            elif event_type == self.terminal_event_type:
                candidate = snapshot or _snapshot_from_state(state)
                if candidate.stop_reason in self.intermediate_stop_reasons:
                    continue
                terminal_snapshot = candidate
                break

        state["event_offset"] = batch.next_offset
        state["event_trailing_partial"] = batch.trailing_partial
        updated = replace(submission, runtime_state=state)

        if binding_error:
            return _terminal_result(
                updated,
                state,
                now=now,
                status=CompletionStatus.INCOMPLETE,
                reason=f"{self.provider}_request_binding_invalid",
                reply="",
                confidence=CompletionConfidence.DEGRADED,
                items=items,
                diagnostics_extra={"binding_error": binding_error},
            )
        if superseded_by:
            return _terminal_result(
                updated,
                state,
                now=now,
                status=CompletionStatus.INCOMPLETE,
                reason=f"{self.provider}_request_superseded",
                reply="",
                confidence=CompletionConfidence.DEGRADED,
                items=items,
                diagnostics_extra={"superseded_by": superseded_by},
            )
        if terminal_snapshot is not None:
            if not bool(state.get("anchor_seen")):
                return _terminal_result(
                    updated,
                    state,
                    now=now,
                    status=CompletionStatus.INCOMPLETE,
                    reason=f"{self.provider}_native_anchor_missing",
                    reply="",
                    confidence=CompletionConfidence.DEGRADED,
                    items=items,
                )
            return _settled_result(
                updated,
                state,
                terminal_snapshot,
                items=items,
                now=now,
            )
        if items or updated != submission:
            return ProviderPollResult(
                submission=updated,
                items=tuple(items),
            )
        return None

    def cancel(self, submission: ProviderSubmission) -> None:
        if _text(submission.runtime_state.get("mode")) != self.pane_mode:
            return
        backend = submission.runtime_state.get("backend")
        pane_id = _text(submission.runtime_state.get("pane_id"))
        if backend is not None and pane_id:
            interrupt_and_clear_runtime_target(backend, pane_id)

    def export_runtime_state(
        self,
        submission: ProviderSubmission,
    ) -> dict[str, object]:
        state = dict(submission.runtime_state)
        state.pop("backend", None)
        state.pop("pending_prompt", None)
        return state

    def resume(
        self,
        job: JobRecord,
        submission: ProviderSubmission,
        *,
        context: ProviderRuntimeContext | None,
        persisted_state,
        now: str,
    ) -> ProviderSubmission | None:
        del persisted_state
        state = dict(submission.runtime_state)
        if _text(state.get("mode")) != self.pane_mode:
            return None
        if context is None or not context.workspace_path:
            return None
        session = self._load_session(
            Path(context.workspace_path).expanduser(),
            agent_name=_text(
                getattr(job, "provider_instance", None) or job.agent_name
            ),
        )
        if session is None:
            return None
        ok, pane_or_error = session.ensure_pane()
        if not ok:
            return None
        backend = get_backend_for_session(session.data)
        if backend is None:
            return None

        launch_session_id = _text(
            session.data.get("ccb_session_id")
            or session.data.get(f"{self.session_field_prefix}_session_id")
        )
        event_path = _text(
            session.data.get(f"{self.session_field_prefix}_completion_event_log")
        )
        dispatch_path = _text(
            session.data.get(f"{self.session_field_prefix}_dispatch_event_log")
        )
        if (
            launch_session_id != _text(state.get("launch_session_id"))
            or event_path != _text(state.get("event_path"))
            or dispatch_path != _text(state.get("dispatch_path"))
        ):
            return None
        observation = inspect_pi_runtime(
            Path(event_path),
            actor=_text(state.get("actor")),
            launch_session_id=launch_session_id,
        )
        if (
            observation.protocol_error
            or not observation.ready
            or observation.trailing_partial
        ):
            return None
        _persist_observed_native_session(state, observation, observed_at=now)
        prompt_sent = bool(state.get("prompt_sent"))
        if (
            prompt_sent
            and observation.runtime_instance_id
            != _text(state.get("runtime_instance_id"))
        ):
            return None
        if not prompt_sent:
            state["runtime_instance_id"] = observation.runtime_instance_id
            state["event_offset"] = observation.next_offset
            state["pending_prompt"] = _prompt_for_job(
                job,
                request_anchor=_text(state.get("request_anchor")),
                no_wrap=bool(state.get("no_wrap")),
            )
        state["backend"] = backend
        state["pane_id"] = str(pane_or_error)
        return replace(submission, runtime_state=state)

    def _load_session(self, work_dir: Path, *, agent_name: str):
        if self.session_loader is None:
            return _load_session(work_dir, agent_name=agent_name)
        return self.session_loader(work_dir, instance=agent_name)


def _dispatch_if_ready(
    submission: ProviderSubmission,
    state: dict[str, object],
    *,
    now: str,
) -> ProviderSubmission | ProviderPollResult:
    event_path = Path(_text(state.get("event_path")))
    actor = _text(state.get("actor"))
    launch_session_id = _text(state.get("launch_session_id"))
    observation = inspect_pi_runtime(
        event_path,
        actor=actor,
        launch_session_id=launch_session_id,
    )
    if observation.protocol_error:
        return _terminal_result(
            submission,
            state,
            now=now,
            status=CompletionStatus.INCOMPLETE,
            reason=_provider_reason(state, "native_protocol_invalid"),
            reply="",
            confidence=CompletionConfidence.DEGRADED,
            diagnostics_extra={"protocol_error": observation.protocol_error},
        )
    if observation.trailing_partial:
        return _ready_timeout_or_pending(
            submission,
            state,
            now=now,
            detail=_provider_reason(state, "completion_event_tail_partial"),
        )
    if not observation.ready or not observation.runtime_instance_id:
        return _ready_timeout_or_pending(
            submission,
            state,
            now=now,
            detail=_provider_reason(state, "completion_extension_not_ready"),
        )
    if observation.busy:
        state["runtime_instance_id"] = observation.runtime_instance_id
        state["event_offset"] = observation.next_offset
        _persist_observed_native_session(state, observation, observed_at=now)
        return replace(submission, runtime_state=state)

    prompt = _text(state.get("pending_prompt"))
    if not prompt:
        return _terminal_result(
            submission,
            state,
            now=now,
            status=CompletionStatus.FAILED,
            reason=_provider_reason(state, "runtime_state_corrupt"),
            reply="",
            confidence=CompletionConfidence.DEGRADED,
            diagnostics_extra={"missing_pending_prompt": True},
        )
    state["runtime_instance_id"] = observation.runtime_instance_id
    state["event_offset"] = observation.next_offset
    _persist_observed_native_session(state, observation, observed_at=now)
    try:
        _append_dispatch(state, prompt=prompt, now=now)
        send_prompt_to_runtime_target(
            state.get("backend"),
            _text(state.get("pane_id")),
            prompt,
        )
    except Exception as exc:
        return _terminal_result(
            submission,
            state,
            now=now,
            status=CompletionStatus.FAILED,
            reason=_provider_reason(state, "pane_send_failed"),
            reply="",
            confidence=CompletionConfidence.DEGRADED,
            diagnostics_extra={"send_error": f"{type(exc).__name__}: {exc}"},
        )
    state["prompt_sent"] = True
    state["prompt_sent_at"] = now
    state.pop("pending_prompt", None)
    return replace(submission, runtime_state=state)


def _persist_observed_native_session(
    state: dict[str, object],
    observation,
    *,
    observed_at: str,
) -> None:
    _persist_native_session_fields(
        state,
        native_session_id=_text(getattr(observation, "native_session_id", "")),
        native_session_path=_text(getattr(observation, "native_session_path", "")),
        observed_at=_text(getattr(observation, "native_session_observed_at", "")) or observed_at,
    )


def _persist_native_session_fields(
    state: dict[str, object],
    *,
    native_session_id: str,
    native_session_path: str,
    observed_at: str,
) -> None:
    if not bool(state.get("persist_native_session")):
        return
    session_file = _text(state.get("session_file"))
    session_dir = _text(state.get("session_dir"))
    if not native_session_id or not native_session_path or not session_file or not session_dir:
        return
    if (
        native_session_id == _text(state.get("native_session_id"))
        and native_session_path == _text(state.get("native_session_path"))
    ):
        return
    ok, error = persist_native_session_binding(
        Path(session_file),
        expected_ccb_session_id=_text(state.get("launch_session_id")),
        agent_name=_text(state.get("actor")),
        project_id=_text(state.get("project_id")),
        work_dir=Path(_text(state.get("work_dir"))),
        session_dir=Path(session_dir),
        native_session_id=native_session_id,
        native_session_path=Path(native_session_path),
        observed_at=observed_at,
    )
    if ok:
        state["native_session_id"] = native_session_id
        state["native_session_path"] = native_session_path
        state.pop("native_session_binding_error", None)
    else:
        state["native_session_binding_error"] = error or "binding_persist_failed"


def _ready_timeout_or_pending(
    submission: ProviderSubmission,
    state: dict[str, object],
    *,
    now: str,
    detail: str,
) -> ProviderSubmission | ProviderPollResult:
    elapsed = _seconds_between(
        _text(state.get("ready_wait_started_at")) or submission.accepted_at,
        now,
    )
    timeout_s = _extension_ready_timeout_s(state)
    state["extension_ready_wait_s"] = elapsed
    state["extension_ready_detail"] = detail
    if elapsed < timeout_s:
        return replace(submission, runtime_state=state)
    return _terminal_result(
        submission,
        state,
        now=now,
        status=CompletionStatus.INCOMPLETE,
        reason=_provider_reason(state, "completion_extension_not_ready"),
        reply="",
        confidence=CompletionConfidence.DEGRADED,
        diagnostics_extra={
            "extension_ready_wait_s": elapsed,
            "extension_ready_timeout_s": timeout_s,
            "extension_ready_detail": detail,
            "prompt_sent": False,
        },
    )


def _append_dispatch(
    state: dict[str, object],
    *,
    prompt: str,
    now: str,
) -> None:
    path = Path(_text(state.get("dispatch_path")))
    record = {
        "schema_version": 1,
        "type": "dispatch",
        "dispatch_id": uuid.uuid4().hex,
        "actor": _text(state.get("actor")),
        "launch_session_id": _text(state.get("launch_session_id")),
        "runtime_instance_id": _text(state.get("runtime_instance_id")),
        "req_id": _text(state.get("request_anchor")),
        "prompt_sha256": hashlib.sha256(
            prompt.encode("utf-8", "replace")
        ).hexdigest(),
        "timestamp": now,
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as stream:
        stream.write(
            json.dumps(record, ensure_ascii=True, separators=(",", ":"))
            + "\n"
        )
    try:
        os.chmod(path, 0o600)
    except OSError:
        pass


def _settled_result(
    submission: ProviderSubmission,
    state: dict[str, object],
    snapshot: PiAssistantSnapshot,
    *,
    items: list,
    now: str,
) -> ProviderPollResult:
    request_anchor = _text(state.get("request_anchor"))
    reply = clean_native_reply(snapshot.text, request_anchor)
    outcome = snapshot.stop_reason
    state["last_outcome_reason"] = outcome
    state["last_outcome_error"] = snapshot.error
    state["provider_turn_ref"] = snapshot.response_id or request_anchor

    if outcome == "stop" and reply:
        status = CompletionStatus.COMPLETED
        reason = _provider_reason(state, "run_stop")
        confidence = CompletionConfidence.EXACT
        item_kind = CompletionItemKind.TURN_BOUNDARY
    elif outcome == "error":
        status = CompletionStatus.FAILED
        reason = _provider_reason(state, "run_error")
        confidence = CompletionConfidence.EXACT
        item_kind = CompletionItemKind.ERROR
    elif outcome == "stop":
        status = CompletionStatus.INCOMPLETE
        reason = _provider_reason(state, "empty_reply")
        confidence = CompletionConfidence.DEGRADED
        item_kind = CompletionItemKind.TURN_ABORTED
    elif not outcome:
        status = CompletionStatus.INCOMPLETE
        reason = _provider_reason(state, "native_outcome_missing")
        confidence = CompletionConfidence.DEGRADED
        item_kind = CompletionItemKind.TURN_ABORTED
    else:
        status = CompletionStatus.INCOMPLETE
        reason = f"{_provider_reason(state, 'run_finished')}:{outcome}"
        confidence = CompletionConfidence.EXACT
        item_kind = CompletionItemKind.TURN_ABORTED

    if reply:
        items.append(
            build_item(
                submission,
                kind=CompletionItemKind.ASSISTANT_FINAL,
                timestamp=now,
                seq=_next_seq(state),
                payload={
                    "text": reply,
                    "reply": reply,
                    "final_answer": reply,
                    "turn_id": request_anchor,
                    "provider_turn_ref": state["provider_turn_ref"],
                    "finish_reason": outcome,
                },
            )
        )
    items.append(
        build_item(
            submission,
            kind=item_kind,
            timestamp=now,
            seq=_next_seq(state),
            payload={
                "reason": reason,
                "turn_id": request_anchor,
                "provider_turn_ref": state["provider_turn_ref"],
                "finish_reason": outcome,
                "error": snapshot.error,
            },
        )
    )
    return _terminal_result(
        submission,
        state,
        now=now,
        status=status,
        reason=reason,
        reply=reply,
        confidence=confidence,
        items=items,
        diagnostics_extra={
            "outcome_reason": outcome,
            "outcome_error": snapshot.error,
            "runtime_instance_id": _text(state.get("runtime_instance_id")),
            "terminal_authority": _text(state.get("terminal_authority")),
        },
    )


def _terminal_result(
    submission: ProviderSubmission,
    state: dict[str, object],
    *,
    now: str,
    status: CompletionStatus,
    reason: str,
    reply: str,
    confidence: CompletionConfidence,
    items: list | tuple | None = None,
    diagnostics_extra: dict[str, object] | None = None,
) -> ProviderPollResult:
    event_items = tuple(items or ())
    updated = replace(
        submission,
        runtime_state=state,
        status=status,
        reason=reason,
        reply=reply,
        confidence=confidence,
    )
    cursor = (
        event_items[-1].cursor
        if event_items
        else CompletionCursor(
            source_kind=submission.source_kind,
            event_seq=_int_value(state.get("next_seq"), 1),
            updated_at=now,
        )
    )
    diagnostics = {
        **dict(submission.diagnostics or {}),
        "mode": _text(state.get("mode")),
        "anchor_seen": bool(state.get("anchor_seen")),
        "prompt_sent": bool(state.get("prompt_sent")),
        "reply_chars": len(reply),
    }
    diagnostics.update(diagnostics_extra or {})
    decision = CompletionDecision(
        terminal=True,
        status=status,
        reason=reason,
        confidence=confidence,
        reply=reply,
        anchor_seen=bool(state.get("anchor_seen")),
        reply_started=bool(reply),
        reply_stable=status is CompletionStatus.COMPLETED,
        provider_turn_ref=_text(state.get("provider_turn_ref"))
        or _text(state.get("request_anchor"))
        or submission.job_id,
        source_cursor=cursor,
        finished_at=now,
        diagnostics=diagnostics,
    )
    return ProviderPollResult(
        submission=updated,
        items=event_items,
        decision=decision,
    )


def _reply_delivery_result(
    submission: ProviderSubmission,
    state: dict[str, object],
    *,
    now: str,
) -> ProviderPollResult:
    state["anchor_seen"] = True
    return _terminal_result(
        submission,
        state,
        now=now,
        status=CompletionStatus.COMPLETED,
        reason="reply_delivery_sent",
        reply="",
        confidence=CompletionConfidence.OBSERVED,
        diagnostics_extra={
            "reply_delivery": True,
            "delivery_status": "sent",
            "submission_mode": _text(state.get("mode")),
        },
    )


def _remember_assistant(
    state: dict[str, object],
    snapshot: PiAssistantSnapshot,
) -> None:
    state["last_assistant_message"] = snapshot.text
    state["last_outcome_reason"] = snapshot.stop_reason
    state["last_outcome_error"] = snapshot.error
    state["provider_turn_ref"] = snapshot.response_id
    signature_payload = "\0".join(
        (
            snapshot.response_id,
            snapshot.stop_reason,
            snapshot.error,
            snapshot.text,
        )
    )
    state["last_assistant_signature"] = hashlib.sha256(
        signature_payload.encode("utf-8", "replace")
    ).hexdigest()


def _snapshot_from_state(state: dict[str, object]) -> PiAssistantSnapshot:
    return PiAssistantSnapshot(
        text=_text(state.get("last_assistant_message")),
        stop_reason=_text(state.get("last_outcome_reason")),
        error=_text(state.get("last_outcome_error")),
        response_id=_text(state.get("provider_turn_ref")),
    )


def _start_error(
    job: JobRecord,
    *,
    now: str,
    provider: str,
    reason: str,
    error: str,
) -> ProviderSubmission:
    return ProviderSubmission(
        job_id=job.job_id,
        agent_name=job.agent_name,
        provider=provider,
        accepted_at=now,
        ready_at=now,
        source_kind=CompletionSourceKind.SESSION_EVENT_LOG,
        reply="",
        diagnostics={
            "provider": provider,
            "mode": "error",
            "reason": reason,
            "error": error,
        },
        runtime_state={
            "mode": "error",
            "reason": reason,
            "error": error,
            "next_seq": 1,
        },
    )


def _validate_runtime_paths(
    runtime_dir: Path | None,
    *,
    provider: str,
    event_path: Path | None,
    dispatch_path: Path | None,
) -> str:
    if runtime_dir is None:
        return f"{provider}_runtime_dir_missing"
    if event_path is None:
        return f"{provider}_completion_event_log_missing"
    if dispatch_path is None:
        return f"{provider}_dispatch_event_log_missing"
    completion_dir = (runtime_dir / "completion").resolve()
    for label, path in (
        (f"{provider}_completion_event_log", event_path),
        (f"{provider}_dispatch_event_log", dispatch_path),
    ):
        try:
            resolved = path.resolve()
            if not resolved.is_relative_to(completion_dir):
                return f"{label}_outside_runtime"
        except (OSError, RuntimeError, ValueError):
            return f"{label}_invalid"
    return ""


def _prompt_for_job(
    job: JobRecord,
    *,
    request_anchor: str,
    no_wrap: bool,
) -> str:
    return _pane_prompt(
        job.request.body or "",
        request_anchor=request_anchor,
        no_wrap=no_wrap,
    )


def _pane_prompt(
    body: str,
    *,
    request_anchor: str,
    no_wrap: bool,
) -> str:
    if no_wrap:
        return body
    return wrap_native_prompt(body, request_anchor)


def _load_session(work_dir: Path, *, agent_name: str):
    return load_project_session(work_dir, instance=agent_name)


def _path_value(value: object) -> Path | None:
    raw = _text(value)
    return Path(raw).expanduser() if raw else None


def _next_seq(state: dict[str, object]) -> int:
    value = _int_value(state.get("next_seq"), 1)
    state["next_seq"] = value + 1
    return value


def _int_value(value: object, default: int) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _extension_ready_timeout_s(state: dict[str, object]) -> float:
    timeout_env = _text(state.get("extension_ready_timeout_env"))
    timeout_default = _float_value(
        state.get("extension_ready_timeout_default"),
        PI_EXTENSION_READY_TIMEOUT_DEFAULT,
    )
    raw = _text(os.environ.get(timeout_env)) if timeout_env else ""
    if not raw:
        return timeout_default
    try:
        return max(1.0, float(raw))
    except ValueError:
        return timeout_default


def _float_value(value: object, default: float) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _provider_reason(state: dict[str, object], suffix: str) -> str:
    provider = _text(state.get("provider")) or "pi"
    return f"{provider}_{suffix}"


def _seconds_between(start: str, end: str) -> float:
    try:
        start_dt = datetime.fromisoformat(start.replace("Z", "+00:00"))
        end_dt = datetime.fromisoformat(end.replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return 0.0
    return max(0.0, (end_dt - start_dt).total_seconds())


def _text(value: object) -> str:
    return str(value or "").strip()


__all__ = [
    "PI_EXTENSION_READY_TIMEOUT_DEFAULT",
    "PI_EXTENSION_READY_TIMEOUT_ENV",
    "PI_PANE_MODE",
    "PiPaneExecutionAdapter",
]
