#!/usr/bin/env python3
from __future__ import annotations

import argparse
from dataclasses import dataclass, field
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
import shlex
import shutil
import subprocess
import sys
from time import monotonic, sleep
from time import time as wall_time
from typing import Any

LIB_DIR = Path(__file__).resolve().parents[1] / "lib"
if str(LIB_DIR) not in sys.path:
    sys.path.insert(0, str(LIB_DIR))

from provider_pane_status.codex_pane import (
    ACTIVE_STATES,
    STATUS_CATALOG as PANE_STATUS_CATALOG,
    PaneStatus,
    normalize_screen,
    parse_codex_pane_status,
)
from provider_pane_status.codex_session import (
    CodexRuntimeStatus,
    RUNTIME_STATUS_CATALOG,
    compose_codex_runtime_status,
    read_codex_session_status,
)


DEFAULT_WORK_ROOT = Path(os.environ.get("CCB_CODEX_PANE_PROBE_WORK_ROOT", "/home/bfly/yunwei/test_ccb2/codex-pane-status-probe"))
DEFAULT_SAMPLE_INTERVAL_MS = 500
DEFAULT_ACTIVE_SAMPLE_INTERVAL_MS = 250
DEFAULT_DURATION_S = 20.0
DEFAULT_ACTIVE_HOLD_S = 1.5
DEFAULT_EMPTY_HOLD_S = 2.0
DEFAULT_INITIAL_FREE_GRACE_S = 2.0

ACTIVE_RUNTIME_STATES = frozenset({"working", "tool_running", "reconnecting"})
HARD_RUNTIME_STATES = frozenset(
    {
        "waiting_for_user",
        "auth_required",
        "auth_failed",
        "api_error",
        "config_error",
        "failed",
        "pane_dead",
    }
)

TOKEN_RE = re.compile(r"(sk-[A-Za-z0-9_-]{8,}|sess-[A-Za-z0-9_-]{8,}|Bearer\s+[A-Za-z0-9._-]+)")
STATUS_CATALOG = RUNTIME_STATUS_CATALOG


@dataclass
class ProbePaths:
    run_dir: Path
    work_dir: Path
    codex_home: Path
    home_dir: Path
    socket_path: Path
    artifacts_dir: Path
    events_path: Path
    snapshots_path: Path
    raw_log_path: Path
    normalized_log_path: Path
    run_json_path: Path
    metrics_json_path: Path


@dataclass
class ProbeRuntime:
    paths: ProbePaths
    session_name: str
    pane_id: str | None = None
    pipe_attached: bool = False
    started_at_s: float = field(default_factory=monotonic)
    last_raw_log_size: int = 0
    last_output_at_s: float | None = None
    first_output_latency_s: float | None = None
    capture_count: int = 0
    capture_duration_s: list[float] = field(default_factory=list)
    prompt_sent_at_s: float | None = None
    first_active_at_s: float | None = None
    last_active_at_s: float | None = None
    turn_terminal_at_s: float | None = None
    turn_terminal_state: str | None = None
    turn_terminal_outcome: str | None = None
    turn_terminal_reason: str | None = None
    prompt_sent_wall_time_s: float | None = None
    codex_session_root: Path | None = None
    codex_work_dir: Path | None = None
    stable_runtime_status: CodexRuntimeStatus | None = None
    stable_runtime_at_s: float | None = None
    last_active_runtime_status: CodexRuntimeStatus | None = None
    last_active_runtime_at_s: float | None = None
    empty_capture_started_at_s: float | None = None


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def redact_text(text: str) -> str:
    redacted = TOKEN_RE.sub("[REDACTED]", text or "")
    redacted = re.sub(r"(?i)(api[_-]?key|token|secret|password)\s*[:=]\s*\S+", r"\1=[REDACTED]", redacted)
    return redacted


def run_command(args: list[str], *, timeout: float = 5.0, env: dict[str, str] | None = None) -> subprocess.CompletedProcess[str]:
    return subprocess.run(args, text=True, encoding="utf-8", capture_output=True, timeout=timeout, env=env, check=False)


def tmux(socket_path: Path, args: list[str], *, timeout: float = 5.0) -> subprocess.CompletedProcess[str]:
    return run_command(["tmux", "-S", str(socket_path), *args], timeout=timeout)


def build_paths(work_root: Path) -> ProbePaths:
    base = work_root.expanduser().resolve(strict=False)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    run_dir = base / f"run-{stamp}-{os.getpid()}"
    artifacts_dir = run_dir / "artifacts"
    return ProbePaths(
        run_dir=run_dir,
        work_dir=run_dir / "work",
        codex_home=run_dir / "codex-home",
        home_dir=run_dir / "home",
        socket_path=run_dir / "tmux.sock",
        artifacts_dir=artifacts_dir,
        events_path=artifacts_dir / "events.jsonl",
        snapshots_path=artifacts_dir / "snapshots.jsonl",
        raw_log_path=artifacts_dir / "pane-output.raw.log",
        normalized_log_path=artifacts_dir / "pane-output.normalized.log",
        run_json_path=artifacts_dir / "run.json",
        metrics_json_path=artifacts_dir / "metrics.json",
    )


def prepare_paths(paths: ProbePaths) -> None:
    paths.work_dir.mkdir(parents=True, exist_ok=False)
    paths.codex_home.mkdir(parents=True, exist_ok=True)
    (paths.codex_home / "sessions").mkdir(parents=True, exist_ok=True)
    paths.home_dir.mkdir(parents=True, exist_ok=True)
    paths.artifacts_dir.mkdir(parents=True, exist_ok=True)
    paths.raw_log_path.touch()
    paths.events_path.touch()
    paths.snapshots_path.touch()


def provider_env(paths: ProbePaths, mode: str) -> dict[str, str]:
    env = dict(os.environ)
    if mode == "inherit":
        return env
    if mode == "test-home":
        home = Path(os.environ.get("CCB_SOURCE_HOME", "/home/bfly/yunwei/test_ccb2/source_home")).expanduser()
        env["HOME"] = str(home)
        env["CODEX_HOME"] = str(home / ".codex")
        env["CODEX_SESSION_ROOT"] = str(home / ".codex" / "sessions")
    else:
        env["HOME"] = str(paths.home_dir)
        env["CODEX_HOME"] = str(paths.codex_home)
        env["CODEX_SESSION_ROOT"] = str(paths.codex_home / "sessions")
    for key in ("CODEX_RUNTIME_DIR", "CODEX_INPUT_FIFO", "CODEX_OUTPUT_FIFO", "CODEX_TERMINAL"):
        env.pop(key, None)
    return env


def shell_env_command(env: dict[str, str], codex_bin: str) -> str:
    keys = (
        "HOME",
        "CODEX_HOME",
        "CODEX_SESSION_ROOT",
        "HTTP_PROXY",
        "HTTPS_PROXY",
        "ALL_PROXY",
        "NO_PROXY",
        "SSL_CERT_FILE",
        "NODE_EXTRA_CA_CERTS",
    )
    assignments = []
    for key in keys:
        value = env.get(key)
        if value:
            assignments.append(f"{key}={shlex.quote(value)}")
    return "env " + " ".join(assignments + [shlex.quote(codex_bin)])


def preflight(codex_bin: str) -> dict[str, object]:
    result: dict[str, object] = {"checked_at": utc_now(), "ok": True, "checks": {}}
    for name, command in (("tmux", ["tmux", "-V"]), ("codex", [codex_bin, "--version"])):
        executable = shutil.which(command[0])
        check: dict[str, object] = {"executable": executable}
        if executable is None:
            check["ok"] = False
            check["reason"] = "not_found"
            result["ok"] = False
        else:
            cp = run_command(command, timeout=5.0)
            check.update(
                {
                    "ok": cp.returncode == 0,
                    "returncode": cp.returncode,
                    "stdout": redact_text(cp.stdout.strip())[:300],
                    "stderr": redact_text(cp.stderr.strip())[:300],
                }
            )
            if cp.returncode != 0:
                result["ok"] = False
        result["checks"][name] = check
    return result


def start_codex_pane(paths: ProbePaths, *, codex_bin: str, env: dict[str, str], codex_work_dir: Path | None = None) -> ProbeRuntime:
    session_name = f"codex-pane-probe-{os.getpid()}"
    command = shell_env_command(env, codex_bin)
    pane_cwd = (codex_work_dir or paths.work_dir).expanduser().resolve(strict=False)
    pane_cwd.mkdir(parents=True, exist_ok=True)
    cp = tmux(
        paths.socket_path,
        ["new-session", "-d", "-s", session_name, "-c", str(pane_cwd), command],
        timeout=10.0,
    )
    if cp.returncode != 0:
        raise RuntimeError((cp.stderr or cp.stdout or "tmux new-session failed").strip())
    runtime = ProbeRuntime(paths=paths, session_name=session_name)
    panes = list_panes(runtime)
    runtime.pane_id = panes[0].get("pane_id") if panes else None
    if not runtime.pane_id:
        raise RuntimeError("Codex pane did not appear in dedicated tmux session")
    pipe = tmux(paths.socket_path, ["pipe-pane", "-o", "-t", runtime.pane_id, f"cat >> {shlex.quote(str(paths.raw_log_path))}"])
    runtime.pipe_attached = pipe.returncode == 0
    return runtime


def observe_existing_pane(paths: ProbePaths, *, socket_path: Path, pane_id: str, attach_pipe: bool) -> ProbeRuntime:
    paths.socket_path = socket_path.expanduser().resolve(strict=False)
    runtime = ProbeRuntime(paths=paths, session_name="", pane_id=pane_id)
    if attach_pipe:
        pipe = tmux(paths.socket_path, ["pipe-pane", "-o", "-t", runtime.pane_id, f"cat >> {shlex.quote(str(paths.raw_log_path))}"])
        runtime.pipe_attached = pipe.returncode == 0
    return runtime


def list_panes(runtime: ProbeRuntime) -> list[dict[str, str]]:
    fmt = "\t".join(
        (
            "#{pane_id}",
            "#{pane_pid}",
            "#{pane_dead}",
            "#{pane_active}",
            "#{pane_current_command}",
            "#{pane_current_path}",
            "#{pane_width}",
            "#{pane_height}",
            "#{window_id}",
            "#{session_id}",
        )
    )
    command = ["list-panes", "-F", fmt]
    if runtime.session_name:
        command = ["list-panes", "-t", runtime.session_name, "-F", fmt]
    elif runtime.pane_id:
        command = ["list-panes", "-a", "-F", fmt]
    cp = tmux(runtime.paths.socket_path, command, timeout=3.0)
    if cp.returncode != 0:
        return []
    fields = (
        "pane_id",
        "pane_pid",
        "pane_dead",
        "pane_active",
        "pane_current_command",
        "pane_current_path",
        "pane_width",
        "pane_height",
        "window_id",
        "session_id",
    )
    panes = []
    for line in cp.stdout.splitlines():
        parts = line.split("\t")
        if len(parts) == len(fields):
            record = dict(zip(fields, parts))
            if runtime.session_name or not runtime.pane_id or record.get("pane_id") == runtime.pane_id:
                panes.append(record)
    return panes


def capture_screen(runtime: ProbeRuntime) -> tuple[str | None, float, str | None]:
    if not runtime.pane_id:
        return None, 0.0, "missing_pane_id"
    start = monotonic()
    cp = tmux(runtime.paths.socket_path, ["capture-pane", "-t", runtime.pane_id, "-p", "-J", "-S", "-80"], timeout=3.0)
    duration = monotonic() - start
    runtime.capture_count += 1
    runtime.capture_duration_s.append(duration)
    if cp.returncode != 0:
        return None, duration, (cp.stderr or cp.stdout or "capture-pane failed").strip()
    return cp.stdout, duration, None


def update_output_freshness(runtime: ProbeRuntime, now_s: float) -> float | None:
    try:
        size = runtime.paths.raw_log_path.stat().st_size
    except OSError:
        return None
    if size > runtime.last_raw_log_size:
        runtime.last_raw_log_size = size
        runtime.last_output_at_s = now_s
        if runtime.first_output_latency_s is None:
            runtime.first_output_latency_s = now_s - runtime.started_at_s
    if runtime.last_output_at_s is None:
        return None
    return max(0.0, now_s - runtime.last_output_at_s)


def send_prompt(
    runtime: ProbeRuntime,
    prompt: str,
    *,
    submit_key: str = "Enter",
    submit_delay_s: float = 0.1,
) -> dict[str, object]:
    if not runtime.pane_id:
        return {"ok": False, "reason": "missing_pane_id"}
    text = prompt.strip()
    if not text:
        return {"ok": False, "reason": "empty_prompt"}
    cp_text = tmux(runtime.paths.socket_path, ["send-keys", "-t", runtime.pane_id, "-l", text], timeout=5.0)
    sleep(max(0.0, submit_delay_s))
    submitted_at_s = monotonic()
    submitted_wall_time_s = wall_time()
    cp_enter = tmux(runtime.paths.socket_path, ["send-keys", "-t", runtime.pane_id, submit_key], timeout=5.0)
    result: dict[str, object] = {
        "ok": cp_text.returncode == 0 and cp_enter.returncode == 0,
        "text_returncode": cp_text.returncode,
        "enter_returncode": cp_enter.returncode,
        "submit_key": submit_key,
        "_submitted_at_s": submitted_at_s,
        "_submitted_wall_time_s": submitted_wall_time_s,
    }
    return result


def run_probe(args: argparse.Namespace) -> dict[str, object]:
    paths = build_paths(args.work_root)
    prepare_paths(paths)
    summary: dict[str, object] = {
        "schema_version": 1,
        "status": "started",
        "started_at": utc_now(),
        "work_root": str(paths.run_dir),
        "artifacts": {
            "run_json": str(paths.run_json_path),
            "events_jsonl": str(paths.events_path),
            "snapshots_jsonl": str(paths.snapshots_path),
            "raw_log": str(paths.raw_log_path),
            "normalized_log": str(paths.normalized_log_path),
            "metrics_json": str(paths.metrics_json_path),
        },
    }
    runtime: ProbeRuntime | None = None
    try:
        preflight_result = preflight(args.codex_bin)
        summary["preflight"] = preflight_result
        if not preflight_result["ok"]:
            summary["status"] = "skipped"
            summary["reason"] = "preflight_failed"
            return summary

        if args.tmux_socket and args.pane_id:
            summary["observe_existing"] = True
            summary["tmux_socket"] = str(args.tmux_socket)
            runtime = observe_existing_pane(
                paths,
                socket_path=args.tmux_socket,
                pane_id=args.pane_id,
                attach_pipe=not args.no_pipe,
            )
            if args.codex_session_root:
                runtime.codex_session_root = args.codex_session_root.expanduser().resolve(strict=False)
            if args.codex_work_dir:
                runtime.codex_work_dir = args.codex_work_dir.expanduser().resolve(strict=False)
        else:
            env = provider_env(paths, args.provider_home_mode)
            summary["provider_home_mode"] = args.provider_home_mode
            summary["codex_home"] = env.get("CODEX_HOME")
            summary["codex_session_root"] = env.get("CODEX_SESSION_ROOT")
            runtime = start_codex_pane(paths, codex_bin=args.codex_bin, env=env, codex_work_dir=args.codex_work_dir)
            session_root = env.get("CODEX_SESSION_ROOT")
            if session_root:
                runtime.codex_session_root = Path(session_root).expanduser().resolve(strict=False)
            runtime.codex_work_dir = (args.codex_work_dir or paths.work_dir).expanduser().resolve(strict=False)
            summary["codex_work_dir"] = str(runtime.codex_work_dir)
        summary["pane_id"] = runtime.pane_id
        summary["pipe_attached"] = runtime.pipe_attached

        prompt_event = maybe_send_prompt(runtime, args)
        if prompt_event is not None:
            append_jsonl(paths.events_path, prompt_event)

        end_at = monotonic() + args.duration_s
        observed_pane_states: list[str] = []
        observed_runtime_states: list[str] = []
        observed_raw_runtime_states: list[str] = []
        pane_state_records: list[dict[str, object]] = []
        runtime_state_records: list[dict[str, object]] = []
        raw_runtime_state_records: list[dict[str, object]] = []
        last_status_event: dict[str, object] | None = None
        while monotonic() < end_at:
            now_s = monotonic()
            update_output_freshness(runtime, now_s)
            panes = list_panes(runtime)
            pane_facts = panes[0] if panes else {}
            pane_dead = str(pane_facts.get("pane_dead") or "1") == "1"
            screen, capture_duration_s, capture_error = capture_screen(runtime)
            status = parse_codex_pane_status(screen, pane_dead=pane_dead)
            session_status = read_codex_session_status(
                runtime.codex_session_root,
                work_dir=runtime.codex_work_dir,
                min_mtime_s=runtime.prompt_sent_wall_time_s,
            )
            raw_runtime_status = compose_codex_runtime_status(status, session_status)
            runtime_status = stabilize_runtime_status(runtime, raw_runtime_status, now_s=now_s)
            turn_timing = update_turn_timing(
                runtime,
                status=status,
                runtime_status=runtime_status,
                now_s=now_s,
            )
            observed_pane_states.append(status.state)
            observed_raw_runtime_states.append(raw_runtime_status.state)
            observed_runtime_states.append(runtime_status.state)
            pane_state_record = {
                "elapsed_s": round(now_s - runtime.started_at_s, 3),
                "state": status.state,
                "reason": status.reason,
                "terminal_outcome": status.terminal_outcome,
            }
            runtime_state_record = {
                "elapsed_s": round(now_s - runtime.started_at_s, 3),
                "state": runtime_status.state,
                "reason": runtime_status.reason,
                "terminal_outcome": status.terminal_outcome,
            }
            raw_runtime_state_record = {
                "elapsed_s": round(now_s - runtime.started_at_s, 3),
                "state": raw_runtime_status.state,
                "reason": raw_runtime_status.reason,
                "terminal_outcome": status.terminal_outcome,
            }
            pane_state_records.append(pane_state_record)
            runtime_state_records.append(runtime_state_record)
            raw_runtime_state_records.append(raw_runtime_state_record)
            snapshot = {
                "ts": utc_now(),
                "elapsed_s": round(now_s - runtime.started_at_s, 3),
                "pane": pane_facts,
                "capture_duration_s": round(capture_duration_s, 4),
                "capture_error": capture_error,
                "status": status.to_record(),
                "session_status": session_status.to_record(),
                "runtime_status": runtime_status.to_record(),
                "raw_runtime_status": raw_runtime_status.to_record(),
                "turn_timing": turn_timing,
                "screen_tail": redact_text(normalize_screen(screen or "")[-2000:]),
            }
            append_jsonl(paths.snapshots_path, snapshot)
            status_event = {
                "ts": snapshot["ts"],
                "kind": "status",
                "state": runtime_status.state,
                "reason": runtime_status.reason,
                "pane_state": status.state,
                "pane_reason": status.reason,
                "session_state": session_status.state,
                "session_reason": session_status.reason,
                "raw_state": raw_runtime_status.state,
                "raw_reason": raw_runtime_status.reason,
                "turn_timing": turn_timing,
                "terminal_outcome": status.terminal_outcome,
            }
            if should_emit_status_event(args.event_mode, last_status_event, status_event):
                append_jsonl(paths.events_path, status_event)
                last_status_event = status_event
            sleep(next_sample_delay_s(args, status=status))

        write_normalized_log(paths)
        metrics = build_metrics(
            runtime,
            observed_runtime_states,
            state_records=runtime_state_records,
            flicker_window_s=args.flicker_window_s,
        )
        if pane_state_records:
            metrics["pane_state_stability"] = build_state_stability(
                pane_state_records,
                flicker_window_s=args.flicker_window_s,
            )
        if raw_runtime_state_records:
            metrics["raw_runtime_state_stability"] = build_state_stability(
                raw_runtime_state_records,
                flicker_window_s=args.flicker_window_s,
            )
        write_json(paths.metrics_json_path, metrics)
        summary["status"] = "completed"
        summary["finished_at"] = utc_now()
        summary["event_mode"] = args.event_mode
        summary["observed_states"] = sorted(set(observed_runtime_states))
        summary["observed_raw_states"] = sorted(set(observed_raw_runtime_states))
        summary["observed_pane_states"] = sorted(set(observed_pane_states))
        summary["final_state"] = observed_runtime_states[-1] if observed_runtime_states else "unknown"
        summary["final_raw_state"] = observed_raw_runtime_states[-1] if observed_raw_runtime_states else "unknown"
        summary["final_pane_state"] = observed_pane_states[-1] if observed_pane_states else "unknown"
        summary["metrics"] = metrics
        summary["turn_timing"] = build_turn_timing_summary(runtime)
        return summary
    except Exception as exc:
        summary["status"] = "failed"
        summary["reason"] = type(exc).__name__
        summary["detail"] = redact_text(str(exc))
        return summary
    finally:
        if runtime is not None and not args.keep_tmux and not args.tmux_socket:
            tmux(paths.socket_path, ["kill-server"], timeout=5.0)
        write_json(paths.run_json_path, summary)


def maybe_send_prompt(runtime: ProbeRuntime, args: argparse.Namespace) -> dict[str, object] | None:
    prompt = args.prompt.strip()
    if not prompt:
        return None
    sleep(max(0.0, args.initial_wait_s))
    now_s = monotonic()
    update_output_freshness(runtime, now_s)
    panes = list_panes(runtime)
    pane_dead = str((panes[0] if panes else {}).get("pane_dead") or "1") == "1"
    screen, _, capture_error = capture_screen(runtime)
    status = parse_codex_pane_status(screen, pane_dead=pane_dead)
    if not prompt_can_be_sent(status):
        return {
            "ts": utc_now(),
            "kind": "prompt_skipped",
            "reason": f"status_{status.state}",
            "status": status.to_record(),
            "capture_error": capture_error,
        }
    result = send_prompt(
        runtime,
        prompt,
        submit_key=args.submit_key,
        submit_delay_s=args.submit_delay_ms / 1000.0,
    )
    submitted_at_s = result.pop("_submitted_at_s", None)
    submitted_wall_time_s = result.pop("_submitted_wall_time_s", None)
    if result.get("ok"):
        runtime.prompt_sent_at_s = submitted_at_s if isinstance(submitted_at_s, float) else monotonic()
        runtime.prompt_sent_wall_time_s = submitted_wall_time_s if isinstance(submitted_wall_time_s, float) else wall_time()
    event = {"ts": utc_now(), "kind": "prompt_sent", "result": result}
    if runtime.prompt_sent_at_s is not None:
        event["submitted_elapsed_s"] = round(runtime.prompt_sent_at_s - runtime.started_at_s, 3)
    return event


def prompt_can_be_sent(status: PaneStatus) -> bool:
    return status.state not in {
        *ACTIVE_STATES,
        "waiting_for_user",
        "auth_required",
        "auth_failed",
        "api_error",
        "config_error",
        "failed",
        "pane_dead",
    }


def next_sample_delay_s(
    args: argparse.Namespace,
    *,
    status: PaneStatus,
) -> float:
    if status.state in ACTIVE_STATES:
        interval_ms = args.active_sample_interval_ms
    else:
        interval_ms = args.sample_interval_ms
    return max(0.05, interval_ms / 1000.0)


def stabilize_runtime_status(
    runtime: ProbeRuntime,
    raw_status: CodexRuntimeStatus,
    *,
    now_s: float,
) -> CodexRuntimeStatus:
    if raw_status.pane_reason != "empty_capture":
        runtime.empty_capture_started_at_s = None

    if raw_status.state in ACTIVE_RUNTIME_STATES:
        runtime.last_active_runtime_status = raw_status
        runtime.last_active_runtime_at_s = now_s
        return record_stable_runtime_status(runtime, raw_status, now_s)

    if raw_status.state in HARD_RUNTIME_STATES:
        return record_stable_runtime_status(runtime, raw_status, now_s)

    active_hold = held_active_status(runtime, raw_status, now_s)
    if active_hold is not None:
        return record_stable_runtime_status(runtime, active_hold, now_s)

    submitted_start = submitted_start_status(runtime, raw_status, now_s)
    if submitted_start is not None:
        return record_stable_runtime_status(runtime, submitted_start, now_s)

    startup_hold = held_startup_free_status(runtime, raw_status, now_s)
    if startup_hold is not None:
        return record_stable_runtime_status(runtime, startup_hold, now_s)

    empty_hold = held_empty_capture_status(runtime, raw_status, now_s)
    if empty_hold is not None:
        return record_stable_runtime_status(runtime, empty_hold, now_s)

    return record_stable_runtime_status(runtime, raw_status, now_s)


def record_stable_runtime_status(
    runtime: ProbeRuntime,
    status: CodexRuntimeStatus,
    now_s: float,
) -> CodexRuntimeStatus:
    runtime.stable_runtime_status = status
    runtime.stable_runtime_at_s = now_s
    return status


def held_active_status(
    runtime: ProbeRuntime,
    raw_status: CodexRuntimeStatus,
    now_s: float,
) -> CodexRuntimeStatus | None:
    if raw_status.state not in {"free", "unknown"}:
        return None
    if runtime.last_active_runtime_at_s is None:
        return None
    elapsed_s = now_s - runtime.last_active_runtime_at_s
    if elapsed_s > DEFAULT_ACTIVE_HOLD_S:
        return None
    active = runtime.last_active_runtime_status
    held_state = active.state if active is not None and active.state in ACTIVE_RUNTIME_STATES else "working"
    return held_runtime_status(
        raw_status,
        state=held_state,
        reason="active_hold_after_recent_work",
        note=f"active_hold_s={round(DEFAULT_ACTIVE_HOLD_S, 3)}",
    )


def held_startup_free_status(
    runtime: ProbeRuntime,
    raw_status: CodexRuntimeStatus,
    now_s: float,
) -> CodexRuntimeStatus | None:
    if raw_status.state != "free" or runtime.prompt_sent_at_s is not None:
        return None
    if now_s - runtime.started_at_s > DEFAULT_INITIAL_FREE_GRACE_S:
        return None
    previous = runtime.stable_runtime_status
    if previous is not None and previous.state not in {"free"}:
        state = previous.state
        reason = previous.reason
    else:
        state = "unknown"
        reason = "startup_free_grace"
    return held_runtime_status(
        raw_status,
        state=state,
        reason=reason,
        note=f"initial_free_grace_s={round(DEFAULT_INITIAL_FREE_GRACE_S, 3)}",
    )


def submitted_start_status(
    runtime: ProbeRuntime,
    raw_status: CodexRuntimeStatus,
    now_s: float,
) -> CodexRuntimeStatus | None:
    if runtime.prompt_sent_at_s is None:
        return None
    if runtime.first_active_at_s is not None or runtime.turn_terminal_at_s is not None:
        return None
    if raw_status.state != "unknown":
        return None
    return held_runtime_status(
        raw_status,
        state="start",
        reason="prompt_submitted_waiting_for_first_signal",
        note=f"submitted_elapsed_s={round(max(0.0, now_s - runtime.prompt_sent_at_s), 3)}",
    )


def held_empty_capture_status(
    runtime: ProbeRuntime,
    raw_status: CodexRuntimeStatus,
    now_s: float,
) -> CodexRuntimeStatus | None:
    previous = runtime.stable_runtime_status
    if raw_status.pane_reason != "empty_capture" or previous is None:
        return None
    if previous.state in HARD_RUNTIME_STATES:
        return None
    if previous.state not in {"free", "working", "tool_running", "reconnecting"}:
        return None
    if runtime.empty_capture_started_at_s is None:
        runtime.empty_capture_started_at_s = now_s
    if now_s - runtime.empty_capture_started_at_s > DEFAULT_EMPTY_HOLD_S:
        return None
    return held_runtime_status(
        raw_status,
        state=previous.state,
        reason="empty_capture_hold_previous",
        note=f"empty_hold_s={round(DEFAULT_EMPTY_HOLD_S, 3)}",
    )


def held_runtime_status(
    raw_status: CodexRuntimeStatus,
    *,
    state: str,
    reason: str,
    note: str,
) -> CodexRuntimeStatus:
    return CodexRuntimeStatus(
        state,
        reason,
        "stabilizer",
        raw_status.pane_state,
        raw_status.pane_reason,
        raw_status.session_state,
        raw_status.session_reason,
        notes=(
            *raw_status.notes,
            note,
            f"raw_state={raw_status.state}",
            f"raw_reason={raw_status.reason}",
        ),
    )


def update_turn_timing(
    runtime: ProbeRuntime,
    *,
    status: PaneStatus,
    runtime_status: CodexRuntimeStatus | None = None,
    now_s: float,
) -> dict[str, object]:
    if runtime.prompt_sent_at_s is None:
        return {"phase": "no_turn"}

    if status.state in ACTIVE_STATES:
        if runtime.first_active_at_s is None:
            runtime.first_active_at_s = now_s
        runtime.last_active_at_s = now_s

    if status.terminal_outcome is not None and runtime.turn_terminal_outcome is None:
        runtime.turn_terminal_outcome = status.terminal_outcome

    if runtime.turn_terminal_at_s is None:
        terminal_state: str | None = None
        terminal_reason: str | None = None
        if status.terminal_outcome == "completed":
            terminal_state = "completed"
            terminal_reason = "codex_worked_for_summary"
        elif runtime_status is not None and runtime_status.state == "free" and runtime_status.reason in {
            "codex_session_task_complete",
            "codex_pane_completed",
        }:
            terminal_state = "free"
            terminal_reason = runtime_status.reason
        elif status.state in {"failed", "auth_failed", "api_error", "config_error", "pane_dead"}:
            terminal_state = status.state
            terminal_reason = status.reason
        if terminal_state is not None:
            runtime.turn_terminal_at_s = now_s
            runtime.turn_terminal_state = terminal_state
            runtime.turn_terminal_reason = terminal_reason or terminal_state
            if runtime.turn_terminal_outcome is None:
                runtime.turn_terminal_outcome = status.terminal_outcome or (
                    "completed" if terminal_state in {"completed", "free"} else terminal_state
                )

    turn_elapsed_s = (runtime.turn_terminal_at_s or now_s) - runtime.prompt_sent_at_s
    display_elapsed_s = turn_elapsed_s

    if runtime.turn_terminal_at_s is not None:
        phase = "terminal"
    elif status.state in ACTIVE_STATES:
        phase = "active"
    elif runtime.first_active_at_s is None:
        phase = "submitted_waiting_for_active"
    else:
        phase = status.state

    record: dict[str, object] = {
        "phase": phase,
        "turn_elapsed_s": round(turn_elapsed_s, 3),
        "display_elapsed_s": round(display_elapsed_s, 3),
        "display_elapsed_source": "probe_submit_clock",
    }
    if runtime.turn_terminal_reason is not None:
        record["terminal_reason"] = runtime.turn_terminal_reason
    if runtime.first_active_at_s is not None:
        record["first_active_latency_s"] = round(runtime.first_active_at_s - runtime.prompt_sent_at_s, 3)
    if runtime.last_active_at_s is not None:
        record["last_active_elapsed_s"] = round(runtime.last_active_at_s - runtime.prompt_sent_at_s, 3)
    if runtime.turn_terminal_at_s is not None:
        record["terminal_elapsed_s"] = round(runtime.turn_terminal_at_s - runtime.prompt_sent_at_s, 3)
        record["terminal_state"] = runtime.turn_terminal_state
        record["terminal_outcome"] = runtime.turn_terminal_outcome
    elif runtime.turn_terminal_outcome is not None:
        record["terminal_outcome"] = runtime.turn_terminal_outcome
    return record


def build_metrics(
    runtime: ProbeRuntime,
    observed_states: list[str],
    *,
    state_records: list[dict[str, object]] | None = None,
    flicker_window_s: float = 1.0,
) -> dict[str, object]:
    durations = runtime.capture_duration_s
    records = state_records or []
    metrics: dict[str, object] = {
        "capture_count": runtime.capture_count,
        "capture_duration_avg_s": round(sum(durations) / len(durations), 4) if durations else None,
        "capture_duration_max_s": round(max(durations), 4) if durations else None,
        "capture_duration_p95_s": round(percentile(durations, 0.95), 4) if durations else None,
        "first_output_latency_s": round(runtime.first_output_latency_s, 3) if runtime.first_output_latency_s is not None else None,
        "observed_state_counts": {state: observed_states.count(state) for state in sorted(set(observed_states))},
        "raw_log_bytes": runtime.last_raw_log_size,
    }
    if records:
        metrics.update(build_state_stability(records, flicker_window_s=flicker_window_s))
    return metrics


def percentile(values: list[float], quantile: float) -> float:
    if not values:
        raise ValueError("percentile requires at least one value")
    ordered = sorted(values)
    bounded = min(1.0, max(0.0, quantile))
    index = int((len(ordered) - 1) * bounded)
    return ordered[index]


def build_state_stability(records: list[dict[str, object]], *, flicker_window_s: float) -> dict[str, object]:
    transitions = transition_sequence(records)
    flickers = flicker_transitions(transitions, flicker_window_s=flicker_window_s)
    duration_s = sample_duration_s(records)
    sample_count = len(records)
    return {
        "sample_count": sample_count,
        "samples_per_s": round(sample_count / duration_s, 3) if duration_s > 0 else None,
        "transition_count": max(0, len(transitions) - 1),
        "flicker_transition_count": len(flickers),
        "flicker_window_s": flicker_window_s,
        "state_dwell_s": state_dwell_s(records),
        "transition_sequence": transitions,
        "flicker_transitions": flickers,
    }


def sample_duration_s(records: list[dict[str, object]]) -> float:
    if len(records) < 2:
        return 0.0
    first = float(records[0].get("elapsed_s") or 0.0)
    last = float(records[-1].get("elapsed_s") or first)
    return max(0.0, last - first)


def transition_sequence(records: list[dict[str, object]]) -> list[dict[str, object]]:
    transitions: list[dict[str, object]] = []
    previous_state: object = object()
    for record in records:
        state = record.get("state")
        if state == previous_state:
            continue
        transition: dict[str, object] = {
            "elapsed_s": record.get("elapsed_s"),
            "state": state,
        }
        reason = record.get("reason")
        if reason is not None:
            transition["reason"] = reason
        terminal_outcome = record.get("terminal_outcome")
        if terminal_outcome is not None:
            transition["terminal_outcome"] = terminal_outcome
        transitions.append(transition)
        previous_state = state
    return transitions


def flicker_transitions(transitions: list[dict[str, object]], *, flicker_window_s: float) -> list[dict[str, object]]:
    flickers: list[dict[str, object]] = []
    for index in range(2, len(transitions)):
        before = transitions[index - 2]
        middle = transitions[index - 1]
        after = transitions[index]
        before_state = before.get("state")
        middle_state = middle.get("state")
        after_state = after.get("state")
        if before_state != after_state or before_state == middle_state:
            continue
        start_s = float(before.get("elapsed_s") or 0.0)
        end_s = float(after.get("elapsed_s") or start_s)
        if end_s - start_s <= flicker_window_s:
            flickers.append(
                {
                    "from_elapsed_s": before.get("elapsed_s"),
                    "to_elapsed_s": after.get("elapsed_s"),
                    "states": [before_state, middle_state, after_state],
                    "duration_s": round(max(0.0, end_s - start_s), 3),
                }
            )
    return flickers


def state_dwell_s(records: list[dict[str, object]]) -> dict[str, float]:
    dwell: dict[str, float] = {}
    for index, record in enumerate(records[:-1]):
        state = str(record.get("state") or "unknown")
        now_s = float(record.get("elapsed_s") or 0.0)
        next_s = float(records[index + 1].get("elapsed_s") or now_s)
        dwell[state] = dwell.get(state, 0.0) + max(0.0, next_s - now_s)
    if records:
        last_state = str(records[-1].get("state") or "unknown")
        dwell.setdefault(last_state, 0.0)
    return {state: round(seconds, 3) for state, seconds in sorted(dwell.items())}


def should_emit_status_event(
    event_mode: str,
    previous_event: dict[str, object] | None,
    current_event: dict[str, object],
) -> bool:
    if event_mode == "all" or previous_event is None:
        return True
    keys = ("state", "reason", "pane_state", "session_state", "terminal_outcome")
    return any(previous_event.get(key) != current_event.get(key) for key in keys)


def build_turn_timing_summary(runtime: ProbeRuntime) -> dict[str, object] | None:
    if runtime.prompt_sent_at_s is None:
        return None
    end_s = runtime.turn_terminal_at_s or runtime.last_active_at_s or monotonic()
    summary: dict[str, object] = {
        "submitted_elapsed_s": round(runtime.prompt_sent_at_s - runtime.started_at_s, 3),
        "elapsed_s": round(end_s - runtime.prompt_sent_at_s, 3),
        "terminal_state": runtime.turn_terminal_state,
        "terminal_outcome": runtime.turn_terminal_outcome,
        "terminal_reason": runtime.turn_terminal_reason,
    }
    if runtime.first_active_at_s is not None:
        summary["first_active_latency_s"] = round(runtime.first_active_at_s - runtime.prompt_sent_at_s, 3)
    if runtime.last_active_at_s is not None:
        summary["last_active_elapsed_s"] = round(runtime.last_active_at_s - runtime.prompt_sent_at_s, 3)
    if runtime.turn_terminal_at_s is not None:
        summary["terminal_elapsed_s"] = round(runtime.turn_terminal_at_s - runtime.prompt_sent_at_s, 3)
    return summary


def write_normalized_log(paths: ProbePaths) -> None:
    try:
        raw = paths.raw_log_path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        raw = ""
    paths.normalized_log_path.write_text(redact_text(normalize_screen(raw)), encoding="utf-8")


def append_jsonl(path: Path, payload: dict[str, object]) -> None:
    with path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(payload, ensure_ascii=False, sort_keys=True) + "\n")


def write_json(path: Path, payload: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Probe Codex CLI state using only its tmux pane.")
    parser.add_argument("--print-status-catalog", action="store_true", help="Print known pane status states and exit.")
    parser.add_argument("--work-root", type=Path, default=DEFAULT_WORK_ROOT)
    parser.add_argument("--prompt", default="")
    parser.add_argument("--codex-bin", default="codex")
    parser.add_argument("--codex-work-dir", type=Path, default=None)
    parser.add_argument("--provider-home-mode", choices=("isolated", "test-home", "inherit"), default="isolated")
    parser.add_argument("--duration-s", type=float, default=DEFAULT_DURATION_S)
    parser.add_argument("--sample-interval-ms", type=int, default=DEFAULT_SAMPLE_INTERVAL_MS)
    parser.add_argument("--active-sample-interval-ms", type=int, default=DEFAULT_ACTIVE_SAMPLE_INTERVAL_MS)
    parser.add_argument("--initial-wait-s", type=float, default=3.0)
    parser.add_argument("--event-mode", choices=("all", "transitions"), default="all")
    parser.add_argument("--flicker-window-s", type=float, default=1.0)
    parser.add_argument("--submit-key", default="Enter")
    parser.add_argument("--submit-delay-ms", type=int, default=100)
    parser.add_argument("--keep-tmux", action="store_true")
    parser.add_argument("--tmux-socket", type=Path, default=None, help="Observe an existing tmux server instead of launching Codex.")
    parser.add_argument("--pane-id", default="", help="Existing pane id to observe when --tmux-socket is set.")
    parser.add_argument("--codex-session-root", type=Path, default=None, help="Codex sessions root for existing pane observation.")
    parser.add_argument("--no-pipe", action="store_true", help="Do not attach pipe-pane for existing pane observation.")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(sys.argv[1:] if argv is None else argv)
    if args.print_status_catalog:
        print(
            json.dumps(
                {
                    "schema_version": 1,
                    "pane_states": PANE_STATUS_CATALOG,
                    "runtime_states": STATUS_CATALOG,
                },
                ensure_ascii=False,
                indent=2,
                sort_keys=True,
            )
        )
        return 0
    summary = run_probe(args)
    print(json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True))
    return 0 if summary.get("status") in {"completed", "skipped"} else 1


if __name__ == "__main__":
    raise SystemExit(main())
