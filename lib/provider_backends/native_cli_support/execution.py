from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime
import hashlib
import json
import os
from pathlib import Path
import signal
import subprocess
from typing import Callable, Any

from ccbd.api_models import JobRecord
from completion.models import (
    CompletionConfidence,
    CompletionCursor,
    CompletionDecision,
    CompletionItemKind,
    CompletionSourceKind,
    CompletionStatus,
)
from provider_core.instance_resolution import named_agent_instance
from provider_backends.native_cli_support.home import (
    build_native_private_env,
    ensure_native_provider_storage_isolation,
)
from provider_core.protocol import request_anchor_for_job
from provider_execution.active_runtime.polling_runtime.result import runtime_error_result
from provider_execution.base import ProviderPollResult, ProviderRuntimeContext, ProviderSubmission
from provider_execution.common import build_item, error_submission, no_wrap_requested

from .prompt import clean_native_reply, wrap_native_prompt
from .session import load_native_project_session


_RUN_PROCS: dict[str, subprocess.Popen] = {}
_MAX_STDERR_CHARS = 4000
_STOP_REASONS = {
    "stop",
    "end_turn",
    "turn_end",
    "agent_settled",
    "agent_end_terminal",
    "completed",
    "complete",
    "done",
    "finished",
    "success",
    "ok",
}
_FAILED_OUTCOME_REASONS = {"error", "failed", "failure"}
_SUCCESS_OUTCOME_REASONS = {"stop", "end_turn", "yield"}


@dataclass(frozen=True)
class NativeCliObservation:
    text: str = ""
    # Some providers can prove that the provider itself durably accepted the
    # exact CCB request.  This is deliberately separate from process launch or
    # prompt submission: exact adapters must not synthesize anchor evidence.
    anchor_seen: bool = False
    finished: bool = False
    finish_reason: str = ""
    turn_ref: str | None = None
    completed_at: object | None = None
    error: str = ""
    intermediate: bool = False
    outcome_reason: str = ""
    protocol_error: str = ""


@dataclass(frozen=True)
class NativeCliExecutionRequest:
    provider: str
    job: JobRecord
    work_dir: Path
    session_data: dict[str, object]
    prompt: str
    request_anchor: str


CommandBuilder = Callable[[NativeCliExecutionRequest], list[str]]
EnvBuilder = Callable[[NativeCliExecutionRequest], dict[str, str]]
Observer = Callable[[Path], NativeCliObservation]
ResumeCommandBuilder = Callable[[dict[str, object]], list[str]]


@dataclass(frozen=True)
class NativeCliExecutionConfig:
    provider: str
    session_filename: str
    command_builder: CommandBuilder
    env_builder: EnvBuilder | None = None
    private_path_env_names: tuple[str, ...] = ()
    private_raw_env_names: tuple[str, ...] = ()
    observer: Observer | None = None
    output_kind: str = "jsonl"
    mode: str = "native_cli_run"
    start_failed_reason: str = ""
    failed_reason: str = ""
    empty_reason: str = ""
    run_error_reason: str = ""
    complete_reason: str = ""
    process_exit_complete_reason: str = ""
    missing_terminal_reason: str = ""
    timeout_reason: str = ""
    invalid_protocol_reason: str = ""
    missing_outcome_reason: str = ""
    run_timeout_s: float = 900.0
    terminal_on_process_exit: bool = True
    terminal_requires_process_exit: bool = False
    require_outcome_reason: bool = False
    require_native_anchor: bool = False
    # The provider has a durable, correlated terminal protocol rather than a
    # heuristic interpretation of one-shot CLI output.
    exact_native_terminal: bool = False
    # Optional observer-only process used after a ccbd restart.  It must never
    # submit the user prompt again; it may only recover provider-native state
    # for the exact persisted request.
    resume_command_builder: ResumeCommandBuilder | None = None

    def reason(self, name: str) -> str:
        explicit = str(getattr(self, name) or "").strip()
        if explicit:
            return explicit
        stem = name.removesuffix("_reason")
        return f"{self.provider}_{stem}"


class NativeCliSubprocessAdapter:
    def __init__(self, config: NativeCliExecutionConfig) -> None:
        self.config = config
        self.provider = str(config.provider or "").strip().lower()

    @property
    def restart_resume_supported(self) -> bool:
        return self.config.resume_command_builder is not None

    def restore_diagnostics(self) -> dict[str, object]:
        if self.config.resume_command_builder is not None:
            return {
                "resume_supported": True,
                "restore_mode": "exact_native_observer",
                "restore_reason": "provider_native_history_resume",
                "restore_detail": (
                    f"{self.provider} resumes the exact persisted native request "
                    "through an observer-only process without reposting the prompt"
                ),
            }
        return {
            "resume_supported": False,
            "restore_mode": "resubmit_required",
            "restore_reason": "provider_resume_unsupported",
            "restore_detail": (
                f"{self.provider} jobs run through per-job native CLI subprocesses; "
                "completed stdout/stderr artifacts can be inspected after restart, "
                "but interrupted in-flight jobs should be resubmitted"
            ),
        }

    def start(
        self,
        job: JobRecord,
        *,
        context: ProviderRuntimeContext | None,
        now: str,
    ) -> ProviderSubmission:
        return _start_submission(self.config, job, context=context, now=now)

    def poll(self, submission: ProviderSubmission, *, now: str) -> ProviderPollResult | None:
        return _poll_submission(self.config, submission, now=now)

    def cancel(self, submission: ProviderSubmission) -> None:
        _terminate_process(submission.runtime_state, grace=False)

    def export_runtime_state(self, submission: ProviderSubmission) -> dict[str, object]:
        return _serializable_state(submission.runtime_state)

    def resume(
        self,
        job: JobRecord,
        submission: ProviderSubmission,
        *,
        context: ProviderRuntimeContext | None,
        persisted_state,
        now: str,
    ) -> ProviderSubmission | None:
        del context, persisted_state
        return _resume_submission(self.config, job, submission, now=now)


def _resume_submission(
    config: NativeCliExecutionConfig,
    job: JobRecord,
    submission: ProviderSubmission,
    *,
    now: str,
) -> ProviderSubmission | None:
    builder = config.resume_command_builder
    state = dict(submission.runtime_state or {})
    if builder is None:
        return None
    if (
        submission.job_id != job.job_id
        or submission.provider != config.provider
        or str(state.get("provider") or "") != config.provider
        or str(state.get("job_id") or "") != job.job_id
        or str(state.get("mode") or "") != config.mode
    ):
        return None

    stdout_path = _path_from_state(state, "stdout_path")
    stderr_path = _path_from_state(state, "stderr_path")
    work_dir = _path_from_state(state, "work_dir")
    if stdout_path is None or stderr_path is None or work_dir is None:
        return None

    # A surviving observer keeps writing the same append-only artifact.  Do
    # not create a duplicate observer (and, critically, do not repost work).
    if _pid_is_live(_state_int(state, "pid", 0)):
        state["last_poll_at"] = now
        return replace(submission, runtime_state=state)

    try:
        command = builder(state)
        if not command or not all(str(part) for part in command):
            return None
        stdout_path.parent.mkdir(parents=True, exist_ok=True)
        with stdout_path.open("a", encoding="utf-8") as stdout, stderr_path.open(
            "a", encoding="utf-8"
        ) as stderr:
            proc = subprocess.Popen(
                [str(part) for part in command],
                cwd=str(work_dir),
                env=dict(os.environ),
                stdout=stdout,
                stderr=stderr,
                text=True,
                start_new_session=True,
            )
    except Exception:
        return None

    _RUN_PROCS[_proc_key(config.provider, job.job_id)] = proc
    state.update(
        {
            "pid": proc.pid,
            "returncode": None,
            "last_poll_at": now,
            "resumed_at": now,
            "resume_mode": "observe_only",
        }
    )
    return replace(submission, runtime_state=state)


def _start_submission(
    config: NativeCliExecutionConfig,
    job: JobRecord,
    *,
    context: ProviderRuntimeContext | None,
    now: str,
) -> ProviderSubmission:
    provider = str(config.provider or "").strip().lower()
    work_dir = _resolve_work_dir(job, context)
    if work_dir is None:
        return error_submission(
            job,
            provider=provider,
            now=now,
            source_kind=CompletionSourceKind.STRUCTURED_RESULT_STREAM,
            reason="runtime_unavailable",
            error="work_dir_missing",
        )

    session = _load_session_for_job(provider, config.session_filename, work_dir, job)
    if session is None:
        return error_submission(
            job,
            provider=provider,
            now=now,
            source_kind=CompletionSourceKind.STRUCTURED_RESULT_STREAM,
            reason="runtime_unavailable",
            error=f"{provider}_session_file_missing",
        )

    runtime_dir = _path_from_session(session.data, "runtime_dir")
    completion_dir = _path_from_session(session.data, "completion_artifact_dir")
    if completion_dir is None:
        completion_dir = (runtime_dir or (work_dir / ".ccb" / "runtime" / provider)) / "completion"
    completion_dir.mkdir(parents=True, exist_ok=True)

    output_suffix = "jsonl" if config.output_kind == "jsonl" else "out"
    stdout_path = completion_dir / f"{job.job_id}.{provider}-run.{output_suffix}"
    stderr_path = completion_dir / f"{job.job_id}.{provider}-run.stderr.log"
    request_anchor = request_anchor_for_job(job.job_id)
    no_wrap = no_wrap_requested(job)
    prompt = job.request.body if no_wrap else wrap_native_prompt(job.request.body or "", request_anchor)
    request = NativeCliExecutionRequest(
        provider=provider,
        job=job,
        work_dir=work_dir,
        session_data=session.data,
        prompt=prompt,
        request_anchor=request_anchor,
    )
    cmd = config.command_builder(request)
    env = _native_cli_env(config, request)

    try:
        with stdout_path.open("w", encoding="utf-8") as stdout, stderr_path.open("w", encoding="utf-8") as stderr:
            proc = subprocess.Popen(
                cmd,
                cwd=str(work_dir),
                env=env,
                stdout=stdout,
                stderr=stderr,
                text=True,
                start_new_session=True,
            )
    except Exception as exc:
        return error_submission(
            job,
            provider=provider,
            now=now,
            source_kind=CompletionSourceKind.STRUCTURED_RESULT_STREAM,
            reason=config.reason("start_failed_reason"),
            error=f"{type(exc).__name__}: {exc}",
        )

    _RUN_PROCS[_proc_key(provider, job.job_id)] = proc
    state = {
        "mode": config.mode,
        "provider": provider,
        "job_id": job.job_id,
        "request_anchor": request_anchor,
        "work_dir": str(work_dir),
        "started_at": now,
        "last_poll_at": now,
        "next_seq": 1,
        "anchor_emitted": bool(no_wrap and not config.require_native_anchor),
        "no_wrap": bool(no_wrap),
        "reply_buffer": "",
        "stdout_path": str(stdout_path),
        "stderr_path": str(stderr_path),
        "pid": proc.pid,
        "returncode": None,
        "run_timeout_s": _effective_run_timeout_s(config),
        "prompt_sha256": hashlib.sha256(prompt.encode("utf-8", "replace")).hexdigest(),
    }
    return ProviderSubmission(
        job_id=job.job_id,
        agent_name=job.agent_name,
        provider=provider,
        accepted_at=now,
        ready_at=now,
        source_kind=CompletionSourceKind.STRUCTURED_RESULT_STREAM,
        reply="",
        diagnostics={
            "provider": provider,
            "mode": config.mode,
            "workspace_path": str(work_dir),
            "stdout_path": str(stdout_path),
            "stderr_path": str(stderr_path),
            "pid": proc.pid,
        },
        runtime_state=state,
    )


def _poll_submission(
    config: NativeCliExecutionConfig,
    submission: ProviderSubmission,
    *,
    now: str,
) -> ProviderPollResult | None:
    mode = str(submission.runtime_state.get("mode") or "")
    if mode in {"passive", "error"}:
        return runtime_error_result(
            submission,
            now=now,
            reason=str(submission.runtime_state.get("reason") or "runtime_unavailable"),
            error=str(submission.runtime_state.get("error") or ""),
        )
    if mode != config.mode:
        return runtime_error_result(submission, now=now, reason="runtime_state_corrupt")

    state = dict(submission.runtime_state)
    provider = str(state.get("provider") or config.provider)
    state["last_poll_at"] = now
    state["next_seq"] = _state_int(state, "next_seq", 1)

    proc = _RUN_PROCS.get(_proc_key(provider, submission.job_id))
    if proc is not None:
        state["returncode"] = proc.poll()
        if state["returncode"] is not None:
            _RUN_PROCS.pop(_proc_key(provider, submission.job_id), None)

    observer = config.observer or observe_jsonl_output
    observation = observer(Path(str(state.get("stdout_path") or "")))
    items = []
    if not bool(state.get("anchor_emitted")) and (
        not config.require_native_anchor or observation.anchor_seen
    ):
        items.append(
            build_item(
                submission,
                kind=CompletionItemKind.ANCHOR_SEEN,
                timestamp=now,
                seq=_next_seq(state),
                payload={
                    "turn_id": str(state.get("request_anchor") or submission.job_id),
                    "source": f"{provider}_native_cli_prompt_submitted",
                },
            )
        )
        state["anchor_emitted"] = True

    request_anchor = str(state.get("request_anchor") or submission.job_id)
    reply = clean_native_reply(observation.text.strip(), request_anchor)
    if reply and reply != str(state.get("reply_buffer") or ""):
        state["reply_buffer"] = reply
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
                    "provider_turn_ref": observation.turn_ref,
                    "completed_at": observation.completed_at,
                    "finish_reason": observation.finish_reason,
                },
            )
        )

    terminal = _terminal_result_if_ready(
        config,
        submission,
        state,
        observation=observation,
        returncode=_coerce_returncode(state.get("returncode")),
        items=items,
        now=now,
    )
    if terminal is not None:
        return terminal

    updated = replace(submission, reply=str(state.get("reply_buffer") or ""), runtime_state=state)
    if items or updated != submission:
        return ProviderPollResult(submission=updated, items=tuple(items), decision=None)
    return None


def _terminal_result_if_ready(
    config: NativeCliExecutionConfig,
    submission: ProviderSubmission,
    state: dict[str, object],
    *,
    observation: NativeCliObservation,
    returncode: int | None,
    items: list,
    now: str,
) -> ProviderPollResult | None:
    provider = str(config.provider or submission.provider)
    reply = str(state.get("reply_buffer") or clean_native_reply(observation.text or "", str(state.get("request_anchor") or submission.job_id))).strip()
    wait_for_process_exit = config.terminal_requires_process_exit and returncode is None
    if observation.error and not wait_for_process_exit and not (
        config.exact_native_terminal and observation.finished
    ):
        return _terminal(
            config,
            submission,
            state,
            items,
            now,
            status=CompletionStatus.FAILED,
            reason=config.reason("run_error_reason"),
            reply=reply,
            confidence=CompletionConfidence.DEGRADED,
            diagnostics_extra={
                "error": observation.error,
                "finish_reason": observation.finish_reason,
                "outcome_reason": observation.outcome_reason,
                "returncode": returncode,
            },
        )

    timeout_s = _state_float(state, "run_timeout_s", config.run_timeout_s)
    if returncode is None and _run_timeout_elapsed(str(state.get("started_at") or ""), now=now, timeout_s=timeout_s):
        return _terminal(
            config,
            submission,
            state,
            items,
            now,
            status=CompletionStatus.INCOMPLETE,
            reason=config.reason("timeout_reason"),
            reply=reply,
            confidence=CompletionConfidence.DEGRADED,
            diagnostics_extra={
                "run_timeout_s": timeout_s,
                "run_timeout_started_at": str(state.get("started_at") or ""),
                "stdout_path": str(state.get("stdout_path") or ""),
                "stderr_path": str(state.get("stderr_path") or ""),
                "stderr_tail": _stderr_tail(Path(str(state.get("stderr_path") or ""))),
            },
            terminate_grace=False,
        )

    if returncode is not None and returncode != 0:
        return _terminal(
            config,
            submission,
            state,
            items,
            now,
            status=CompletionStatus.FAILED,
            reason=config.reason("failed_reason"),
            reply=reply,
            confidence=CompletionConfidence.DEGRADED,
            diagnostics_extra={
                "returncode": returncode,
                "stderr_tail": _stderr_tail(Path(str(state.get("stderr_path") or ""))),
            },
        )

    if wait_for_process_exit:
        return None

    if observation.protocol_error:
        return _terminal(
            config,
            submission,
            state,
            items,
            now,
            status=CompletionStatus.INCOMPLETE,
            reason=config.reason("invalid_protocol_reason"),
            reply=reply,
            confidence=CompletionConfidence.DEGRADED,
            diagnostics_extra={
                "protocol_error": observation.protocol_error,
                "finish_reason": observation.finish_reason,
                "outcome_reason": observation.outcome_reason,
                "returncode": returncode,
                "stdout_path": str(state.get("stdout_path") or ""),
                "stderr_path": str(state.get("stderr_path") or ""),
            },
        )

    if observation.finished and config.require_native_anchor and not bool(state.get("anchor_emitted")):
        return _terminal(
            config,
            submission,
            state,
            items,
            now,
            status=CompletionStatus.INCOMPLETE,
            reason=config.reason("invalid_protocol_reason"),
            reply=reply,
            confidence=CompletionConfidence.DEGRADED,
            diagnostics_extra={
                "protocol_error": "provider_native_anchor_missing",
                "finish_reason": observation.finish_reason,
                "outcome_reason": observation.outcome_reason,
                "returncode": returncode,
            },
        )

    if observation.finished:
        reason = _normalized_reason(observation.finish_reason)
        outcome_reason = _normalized_reason(observation.outcome_reason)
        if config.require_outcome_reason and not outcome_reason:
            status = CompletionStatus.INCOMPLETE
            terminal_reason = config.reason("missing_outcome_reason")
            confidence = CompletionConfidence.DEGRADED
        elif observation.error:
            status = CompletionStatus.FAILED
            terminal_reason = config.reason("run_error_reason")
            confidence = (
                CompletionConfidence.EXACT
                if config.exact_native_terminal
                else CompletionConfidence.OBSERVED
            )
        elif outcome_reason in _FAILED_OUTCOME_REASONS:
            status = CompletionStatus.FAILED
            terminal_reason = config.reason("run_error_reason")
            confidence = CompletionConfidence.OBSERVED
        elif outcome_reason and outcome_reason not in _SUCCESS_OUTCOME_REASONS:
            status = CompletionStatus.INCOMPLETE
            terminal_reason = f"{provider}_run_finished:{outcome_reason}"
            confidence = CompletionConfidence.OBSERVED
        elif reason and reason not in _STOP_REASONS:
            status = CompletionStatus.INCOMPLETE
            terminal_reason = f"{provider}_run_finished:{reason}"
            confidence = CompletionConfidence.OBSERVED
        elif not reply:
            status = CompletionStatus.INCOMPLETE
            terminal_reason = config.reason("empty_reason")
            confidence = CompletionConfidence.DEGRADED
        else:
            status = CompletionStatus.COMPLETED
            terminal_reason = config.reason("complete_reason")
            confidence = (
                CompletionConfidence.EXACT
                if config.exact_native_terminal
                else CompletionConfidence.OBSERVED
            )
        _append_turn_boundary(config, submission, state, items, now, reason=terminal_reason, reply=reply, observation=observation)
        return _terminal(
            config,
            submission,
            state,
            items,
            now,
            status=status,
            reason=terminal_reason,
            reply=reply,
            confidence=confidence,
            diagnostics_extra={
                "finish_reason": observation.finish_reason,
                "outcome_reason": observation.outcome_reason,
                "error": observation.error,
                "stdout_path": str(state.get("stdout_path") or ""),
                "stderr_path": str(state.get("stderr_path") or ""),
                "returncode": returncode,
            },
        )

    if returncode == 0 and not config.terminal_on_process_exit:
        return _terminal(
            config,
            submission,
            state,
            items,
            now,
            status=CompletionStatus.INCOMPLETE,
            reason=config.reason("missing_terminal_reason"),
            reply=reply,
            confidence=CompletionConfidence.DEGRADED,
            diagnostics_extra={
                "finish_reason": observation.finish_reason,
                "returncode": returncode,
                "stdout_path": str(state.get("stdout_path") or ""),
                "stderr_path": str(state.get("stderr_path") or ""),
            },
        )

    if returncode == 0 and config.terminal_on_process_exit:
        if observation.finish_reason:
            reason = _normalized_reason(observation.finish_reason)
            if reason and reason not in _STOP_REASONS:
                _append_turn_boundary(
                    config,
                    submission,
                    state,
                    items,
                    now,
                    reason=f"{provider}_run_finished:{reason}",
                    reply=reply,
                    observation=observation,
                )
                return _terminal(
                    config,
                    submission,
                    state,
                    items,
                    now,
                    status=CompletionStatus.INCOMPLETE,
                    reason=f"{provider}_run_finished:{reason}",
                    reply=reply,
                    confidence=CompletionConfidence.DEGRADED,
                    diagnostics_extra={"finish_reason": observation.finish_reason, "returncode": returncode},
                )
        if not reply:
            return _terminal(
                config,
                submission,
                state,
                items,
                now,
                status=CompletionStatus.INCOMPLETE,
                reason=config.reason("empty_reason"),
                reply="",
                confidence=CompletionConfidence.DEGRADED,
                diagnostics_extra={"returncode": returncode},
            )
        _append_turn_boundary(
            config,
            submission,
            state,
            items,
            now,
            reason=config.reason("process_exit_complete_reason"),
            reply=reply,
            observation=observation,
        )
        return _terminal(
            config,
            submission,
            state,
            items,
            now,
            status=CompletionStatus.COMPLETED,
            reason=config.reason("process_exit_complete_reason"),
            reply=reply,
            confidence=CompletionConfidence.OBSERVED,
            diagnostics_extra={"returncode": returncode},
        )
    return None


def _append_turn_boundary(
    config: NativeCliExecutionConfig,
    submission: ProviderSubmission,
    state: dict[str, object],
    items: list,
    now: str,
    *,
    reason: str,
    reply: str,
    observation: NativeCliObservation,
) -> None:
    del config
    if bool(state.get("turn_boundary_emitted")):
        return
    items.append(
        build_item(
            submission,
            kind=CompletionItemKind.TURN_BOUNDARY,
            timestamp=now,
            seq=_next_seq(state),
            payload={
                "reason": reason,
                "last_agent_message": reply,
                "turn_id": str(state.get("request_anchor") or submission.job_id),
                "provider_turn_ref": observation.turn_ref,
                "finish_reason": observation.finish_reason,
                "completed_at": observation.completed_at,
            },
        )
    )
    state["turn_boundary_emitted"] = True


def _terminal(
    config: NativeCliExecutionConfig,
    submission: ProviderSubmission,
    state: dict[str, object],
    items: list,
    now: str,
    *,
    status: CompletionStatus,
    reason: str,
    reply: str,
    confidence: CompletionConfidence,
    diagnostics_extra: dict[str, object] | None = None,
    terminate_grace: bool = True,
) -> ProviderPollResult:
    state["returncode"] = _coerce_returncode(state.get("returncode"))
    updated = replace(
        submission,
        runtime_state=state,
        status=status,
        reason=reason,
        reply=reply,
        confidence=confidence,
    )
    cursor = _last_cursor(items) or CompletionCursor(
        source_kind=submission.source_kind,
        event_seq=_state_int(state, "next_seq", 1),
        updated_at=now,
    )
    diagnostics = {
        "mode": config.mode,
        "anchor_seen": bool(state.get("anchor_emitted")),
        "reply_chars": len(reply or ""),
    }
    diagnostics.update(diagnostics_extra or {})
    decision = CompletionDecision(
        terminal=True,
        status=status,
        reason=reason,
        confidence=confidence,
        reply=reply or "",
        anchor_seen=bool(state.get("anchor_emitted")),
        reply_started=bool(reply),
        reply_stable=bool(reply) and status is CompletionStatus.COMPLETED,
        provider_turn_ref=str(state.get("request_anchor") or submission.job_id),
        source_cursor=cursor,
        finished_at=now,
        diagnostics=diagnostics,
    )
    _terminate_process(state, grace=terminate_grace)
    return ProviderPollResult(submission=updated, items=tuple(items), decision=decision)


def observe_jsonl_output(path: Path) -> NativeCliObservation:
    helper = _observe_jsonl_output_with_rust_helper(path)
    if helper is not None:
        return helper
    return _observe_jsonl_output_python(path)


def _observe_jsonl_output_with_rust_helper(path: Path) -> NativeCliObservation | None:
    mode = str(os.environ.get("CCB_RUST_NATIVE_OUTPUT") or "").strip().lower()
    global_mode = str(os.environ.get("CCB_RUST_HELPERS") or "").strip().lower()
    if not mode and global_mode not in {"0", "false", "no", "off", "disabled"}:
        mode = "auto"
    if mode not in {"1", "auto", "required"}:
        return None
    required = mode == "required"
    try:
        from rust_helpers_native_output import observe_native_jsonl_output
    except Exception as exc:
        if required:
            raise RuntimeError(
                "native.output.observe requires ccb-rs-helper; no Python fallback is available for this path"
            ) from exc
        return None
    result = observe_native_jsonl_output(path)
    value = result.value
    if not isinstance(value, dict):
        return None
    return NativeCliObservation(
        text=str(value.get("text") or ""),
        anchor_seen=bool(value.get("anchor_seen")),
        finished=bool(value.get("finished")),
        finish_reason=str(value.get("finish_reason") or ""),
        turn_ref=value.get("turn_ref") if isinstance(value.get("turn_ref"), str) else None,
        completed_at=value.get("completed_at"),
        error=str(value.get("error") or ""),
        intermediate=bool(value.get("intermediate")),
    )


def _observe_jsonl_output_python(path: Path) -> NativeCliObservation:
    if not path or not path.is_file():
        return NativeCliObservation()
    chunks: list[str] = []
    finished = False
    finish_reason = ""
    turn_ref: str | None = None
    completed_at: object | None = None
    error = ""
    intermediate = False
    try:
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError as exc:
        return NativeCliObservation(error=f"read_stdout_failed:{exc}")
    for line in lines:
        stripped = line.strip()
        if not stripped:
            continue
        try:
            event = json.loads(stripped)
        except json.JSONDecodeError:
            continue
        if not isinstance(event, dict):
            continue
        if _is_error_event(event):
            error = _event_text(event) or _event_reason(event) or "native_cli_error"
            continue
        if _is_tool_event(event):
            intermediate = True
            reason = _event_reason(event)
            if reason:
                finish_reason = reason
            continue
        text = _assistant_text(event)
        if text:
            chunks.append(text)
            turn_ref = turn_ref or _event_ref(event)
            completed_at = completed_at or _event_time(event)
        if _is_final_event(event):
            finished = True
            finish_reason = _event_reason(event) or finish_reason or "completed"
            turn_ref = turn_ref or _event_ref(event)
            completed_at = completed_at or _event_time(event)
    return NativeCliObservation(
        text="".join(chunks),
        finished=finished,
        finish_reason=finish_reason,
        turn_ref=turn_ref,
        completed_at=completed_at,
        error=error,
        intermediate=intermediate,
    )


def observe_stdout_output(path: Path) -> NativeCliObservation:
    if not path or not path.is_file():
        return NativeCliObservation()
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError as exc:
        return NativeCliObservation(error=f"read_stdout_failed:{exc}")
    return NativeCliObservation(text=text)


def _assistant_text(event: dict[str, Any]) -> str:
    if _is_user_event(event):
        return ""
    if not (_is_assistant_event(event) or _is_final_event(event)):
        return ""
    return _event_text(event)


def _is_user_event(event: dict[str, Any]) -> bool:
    return _nested_text_value(event, ("role", "sender", "author")).strip().lower() == "user"


def _is_assistant_event(event: dict[str, Any]) -> bool:
    role = _nested_text_value(event, ("role", "sender", "author")).strip().lower()
    if role in {"assistant", "agent", "model"}:
        return True
    event_type = _event_type(event)
    return any(token in event_type for token in ("assistant", "agent_message", "message_delta", "content_delta", "text"))


def _is_final_event(event: dict[str, Any]) -> bool:
    if _is_tool_event(event):
        return False
    haystack = " ".join(
        item
        for item in (
            _event_type(event),
            _event_reason(event).strip().lower().replace("-", "_"),
            _nested_text_value(event, ("status", "state")).strip().lower().replace("-", "_"),
        )
        if item
    )
    if not haystack:
        return False
    return any(token in haystack for token in ("final", "result", "completion", "completed", "done", "finished", "turn_end", "end_turn"))


def _is_tool_event(event: dict[str, Any]) -> bool:
    haystack = " ".join(
        item
        for item in (
            _event_type(event),
            _event_reason(event).strip().lower().replace("-", "_"),
            _nested_text_value(event, ("role", "status", "state", "name")).strip().lower().replace("-", "_"),
        )
        if item
    )
    return "tool" in haystack or "permission" in haystack or "function_call" in haystack


def _is_error_event(event: dict[str, Any]) -> bool:
    haystack = " ".join(
        item
        for item in (
            _event_type(event),
            _event_reason(event).strip().lower().replace("-", "_"),
            _nested_text_value(event, ("status", "state")).strip().lower().replace("-", "_"),
        )
        if item
    )
    return any(token in haystack for token in ("error", "failed", "failure", "permission_denied", "unauthorized", "auth_failed"))


def _event_type(event: dict[str, Any]) -> str:
    return _nested_text_value(event, ("type", "event", "kind", "name")).strip().lower().replace("-", "_")


def _event_text(event: Any) -> str:
    if isinstance(event, str):
        return event
    if isinstance(event, list):
        return "".join(_event_text(item) for item in event)
    if not isinstance(event, dict):
        return ""
    for key in ("merged_text", "final_answer", "answer", "reply", "text", "output", "response"):
        value = event.get(key)
        if isinstance(value, str) and value:
            return value
        if isinstance(value, (dict, list)):
            text = _event_text(value)
            if text:
                return text
    value = event.get("content")
    if isinstance(value, str) and value:
        return value
    if isinstance(value, (dict, list)):
        text = _event_text(value)
        if text:
            return text
    for key in ("payload", "message", "delta", "part", "result", "data"):
        value = event.get(key)
        if isinstance(value, (dict, list, str)):
            text = _event_text(value)
            if text:
                return text
    return ""


def _event_reason(event: dict[str, Any]) -> str:
    for key in ("reason", "finish_reason", "stop_reason", "status", "state"):
        value = event.get(key)
        if isinstance(value, str) and value:
            return value.strip()
    for key in ("payload", "properties", "part", "message", "result", "data"):
        nested = event.get(key)
        if isinstance(nested, dict):
            reason = _event_reason(nested)
            if reason:
                return reason
    return ""


def _event_ref(event: dict[str, Any]) -> str | None:
    for key in ("id", "message_id", "messageID", "session_id", "sessionID", "turn_id", "request_id"):
        value = event.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    for key in ("payload", "message", "part", "result", "data"):
        nested = event.get(key)
        if isinstance(nested, dict):
            ref = _event_ref(nested)
            if ref:
                return ref
    return None


def _event_time(event: dict[str, Any]) -> object | None:
    for key in ("completed_at", "time", "timestamp", "created_at", "updated_at"):
        value = event.get(key)
        if value:
            return value
    for key in ("payload", "message", "part", "result", "data"):
        nested = event.get(key)
        if isinstance(nested, dict):
            value = _event_time(nested)
            if value:
                return value
    return None


def _nested_text_value(event: Any, keys: tuple[str, ...]) -> str:
    if isinstance(event, list):
        for item in event:
            value = _nested_text_value(item, keys)
            if value:
                return value
        return ""
    if not isinstance(event, dict):
        return ""
    for key in keys:
        value = event.get(key)
        if isinstance(value, str) and value:
            return value
    for key in ("payload", "message", "part", "result", "data"):
        value = event.get(key)
        if isinstance(value, (dict, list)):
            nested = _nested_text_value(value, keys)
            if nested:
                return nested
    return ""


def _native_cli_env(config: NativeCliExecutionConfig, request: NativeCliExecutionRequest) -> dict[str, str]:
    ensure_native_provider_storage_isolation(request.provider)
    env = dict(os.environ)
    if config.env_builder is not None:
        env.update(config.env_builder(request))
    raw_home = str(request.session_data.get(f"{request.provider}_home") or "").strip()
    if raw_home:
        managed_home = Path(raw_home).expanduser()
        raw_data = str(request.session_data.get(f"{request.provider}_data_dir") or "").strip()
        managed_data = (
            Path(raw_data).expanduser()
            if raw_data
            else managed_home.parent / "data"
        )
        env.update(
            build_native_private_env(
                managed_home,
                data_dir=managed_data,
                extra_path_env_names=config.private_path_env_names,
                extra_raw_env_names=config.private_raw_env_names,
            )
        )
    return env


def _resolve_work_dir(job: JobRecord, context: ProviderRuntimeContext | None) -> Path | None:
    value = (context.workspace_path if context else None) or job.workspace_path
    if not value:
        return None
    try:
        return Path(str(value)).expanduser()
    except Exception:
        return None


def _load_session_for_job(provider: str, session_filename: str, work_dir: Path, job: JobRecord):
    agent_name = str(job.provider_instance or job.agent_name or provider)
    instance = named_agent_instance(agent_name, primary_agent=provider)
    if instance is not None:
        session = load_native_project_session(work_dir, provider=provider, session_filename=session_filename, instance=instance)
        if session is not None:
            return session
        return None
    return (
        load_native_project_session(work_dir, provider=provider, session_filename=session_filename, instance=agent_name)
        or load_native_project_session(work_dir, provider=provider, session_filename=session_filename)
    )


def _path_from_session(session_data: dict[str, object], key: str) -> Path | None:
    value = str(session_data.get(key) or "").strip()
    if not value:
        return None
    try:
        return Path(value).expanduser()
    except Exception:
        return None


def _path_from_state(state: dict[str, object], key: str) -> Path | None:
    return _path_from_session(state, key)


def _pid_is_live(pid: int) -> bool:
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError:
        return False
    return True


def _next_seq(state: dict[str, object]) -> int:
    seq = _state_int(state, "next_seq", 1)
    state["next_seq"] = seq + 1
    return seq


def _state_int(state: dict[str, object], key: str, default: int) -> int:
    try:
        return int(state.get(key, default))
    except (TypeError, ValueError):
        return default


def _state_float(state: dict[str, object], key: str, default: float) -> float:
    try:
        return max(0.0, float(state.get(key, default)))
    except (TypeError, ValueError):
        return max(0.0, float(default))


def _coerce_returncode(value: object) -> int | None:
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _normalized_reason(reason: str) -> str:
    return str(reason or "").strip().lower().replace("-", "_")


def _effective_run_timeout_s(config: NativeCliExecutionConfig) -> float:
    env_name = f"CCB_{str(config.provider or '').strip().upper().replace('-', '_')}_RUN_TIMEOUT_S"
    raw = str(os.environ.get(env_name) or "").strip()
    if raw:
        try:
            return max(0.0, float(raw))
        except Exception:
            pass
    return max(0.0, float(config.run_timeout_s))


def _run_timeout_elapsed(started_at: str, *, now: str, timeout_s: float) -> bool:
    if timeout_s <= 0 or not started_at or not now:
        return False
    try:
        started = _parse_timestamp(started_at)
        current = _parse_timestamp(now)
    except Exception:
        return False
    return (current - started).total_seconds() >= max(0.0, timeout_s)


def _parse_timestamp(value: str) -> datetime:
    normalized = str(value or "").strip()
    if normalized.endswith("Z"):
        normalized = normalized[:-1] + "+00:00"
    return datetime.fromisoformat(normalized)


def _last_cursor(items: list) -> CompletionCursor | None:
    if not items:
        return None
    return items[-1].cursor


def _stderr_tail(path: Path) -> str:
    if not path or not path.is_file():
        return ""
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError as exc:
        return f"read_stderr_failed:{exc}"
    return text[-_MAX_STDERR_CHARS:]


def _terminate_process(state: dict[str, object], *, grace: bool) -> None:
    provider = str(state.get("provider") or "")
    job_id = str(state.get("job_id") or "")
    proc = _RUN_PROCS.pop(_proc_key(provider, job_id), None)
    if proc is None:
        return
    if proc.poll() is not None:
        return
    try:
        if grace:
            proc.terminate()
        else:
            os.killpg(proc.pid, signal.SIGTERM)
    except Exception:
        try:
            proc.kill()
        except Exception:
            pass


def _serializable_state(state: dict[str, object]) -> dict[str, object]:
    return {
        key: value
        for key, value in dict(state or {}).items()
        if isinstance(value, (str, int, float, bool, type(None), list, tuple, dict))
    }


def _proc_key(provider: str, job_id: str) -> str:
    return f"{provider}:{job_id}"


__all__ = [
    "NativeCliExecutionConfig",
    "NativeCliExecutionRequest",
    "NativeCliObservation",
    "NativeCliSubprocessAdapter",
    "observe_jsonl_output",
    "observe_stdout_output",
]
