#!/usr/bin/env python3
from __future__ import annotations

import argparse
import atexit
import hashlib
import json
import os
import re
import signal
import shlex
import sqlite3
import sys
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path

REQ_ID_RE = re.compile(r"^CCB_REQ_ID:\s*(\S+)")
DONE_RE = re.compile(r"^CCB_DONE:\s*(\S+)")
REQUEST_PATH_RE = re.compile(r"@(\S+\.md)\b")
LAUNCH_PROBE_RUN_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")
LAUNCH_PROBE_CLI_OPTIONS = {
    "--stub-launch-state-path": "STATE_PATH",
    "--stub-launch-run-id": "RUN_ID",
    "--stub-launch-fail-stage": "FAIL_STAGE",
    "--stub-launch-fail-agents": "FAIL_AGENTS",
    "--stub-launch-fail-match-indices": "FAIL_MATCH_INDICES",
    "--stub-launch-fail-release-dir": "FAIL_RELEASE_DIR",
    "--stub-launch-fail-release-timeout": "FAIL_RELEASE_TIMEOUT",
}


class _LaunchProbeExit(RuntimeError):
    def __init__(self, message: str, *, exit_code: int) -> None:
        super().__init__(message)
        self.exit_code = exit_code


def _launch_probe_env(
    provider: str,
    name: str,
    overrides: dict[str, str] | None = None,
) -> str:
    if overrides is not None:
        value = str(overrides.get(name) or "").strip()
        if value:
            return value
    for key in (f"{provider.upper()}_STUB_LAUNCH_{name}", f"STUB_LAUNCH_{name}"):
        raw = os.environ.get(key)
        if raw is not None and raw.strip():
            return raw.strip()
    return ""


def _launch_probe_float(
    provider: str,
    name: str,
    *,
    default: float = 0.0,
    overrides: dict[str, str] | None = None,
) -> float:
    raw = _launch_probe_env(provider, name, overrides)
    if not raw:
        return default
    try:
        value = float(raw)
    except (TypeError, ValueError) as exc:
        raise _LaunchProbeExit(f"invalid launch probe {name.lower()}: {raw!r}", exit_code=2) from exc
    if not (0.0 <= value < float("inf")):
        raise _LaunchProbeExit(f"invalid launch probe {name.lower()}: {raw!r}", exit_code=2)
    return value


def _launch_probe_int(
    provider: str,
    name: str,
    *,
    default: int,
    overrides: dict[str, str] | None = None,
) -> int:
    raw = _launch_probe_env(provider, name, overrides)
    if not raw:
        return default
    try:
        value = int(raw)
    except (TypeError, ValueError) as exc:
        raise _LaunchProbeExit(f"invalid launch probe {name.lower()}: {raw!r}", exit_code=2) from exc
    if not 1 <= value <= 255:
        raise _LaunchProbeExit(f"invalid launch probe {name.lower()}: {raw!r}", exit_code=2)
    return value


def _launch_probe_int_set(
    provider: str,
    name: str,
    overrides: dict[str, str] | None = None,
) -> frozenset[int]:
    raw = _launch_probe_env(provider, name, overrides)
    if not raw:
        return frozenset()
    values: list[int] = []
    for item in raw.split(","):
        text = item.strip()
        try:
            value = int(text)
        except (TypeError, ValueError) as exc:
            raise _LaunchProbeExit(
                f"invalid launch probe {name.lower()}: {raw!r}",
                exit_code=2,
            ) from exc
        if not 1 <= value <= 100_000:
            raise _LaunchProbeExit(
                f"invalid launch probe {name.lower()}: {raw!r}",
                exit_code=2,
            )
        values.append(value)
    if len(values) != len(set(values)):
        raise _LaunchProbeExit(
            f"duplicate launch probe {name.lower()}: {raw!r}",
            exit_code=2,
        )
    return frozenset(values)


def _launch_probe_path(
    provider: str,
    name: str,
    overrides: dict[str, str] | None = None,
) -> Path | None:
    raw = _launch_probe_env(provider, name, overrides)
    if not raw:
        return None
    path = Path(raw).expanduser()
    if not path.is_absolute():
        raise _LaunchProbeExit(
            f"launch probe {name.lower()} must be an absolute path: {raw!r}",
            exit_code=2,
        )
    return path


def _launch_probe_selector_matches(raw: str, value: str) -> bool:
    if not raw:
        return True
    choices = {item.strip().lower() for item in raw.split(",") if item.strip()}
    return "*" in choices or value.strip().lower() in choices


def _new_launch_probe_state(run_id: str) -> dict:
    return {
        "schema_version": 2,
        "run_id": run_id,
        "metric_scope": "injected_process_start_interval",
        "active": 0,
        "max_observed": 0,
        "active_processes": {},
        "injection_match_counts": {},
        "next_event_seq": 1,
        "events": [],
    }


def _launch_probe_state(path: Path, *, run_id: str) -> dict:
    if not path.exists():
        return _new_launch_probe_state(run_id)
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        raise _LaunchProbeExit(f"invalid launch probe state: {path}", exit_code=2) from exc
    if not isinstance(payload, dict) or payload.get("schema_version") != 2:
        raise _LaunchProbeExit(f"invalid launch probe state schema: {path}", exit_code=2)
    state_run_id = str(payload.get("run_id") or "").strip()
    if not LAUNCH_PROBE_RUN_ID_RE.fullmatch(state_run_id):
        raise _LaunchProbeExit(f"invalid launch probe state run id: {path}", exit_code=2)
    if payload.get("metric_scope") != "injected_process_start_interval":
        raise _LaunchProbeExit(f"invalid launch probe metric scope: {path}", exit_code=2)
    if not isinstance(payload.get("active_processes"), dict) or not isinstance(payload.get("events"), list):
        raise _LaunchProbeExit(f"invalid launch probe state fields: {path}", exit_code=2)
    match_counts = payload.setdefault("injection_match_counts", {})
    if not isinstance(match_counts, dict) or any(
        not isinstance(key, str)
        or isinstance(value, bool)
        or not isinstance(value, int)
        or value < 0
        for key, value in match_counts.items()
    ):
        raise _LaunchProbeExit(f"invalid launch probe injection counts: {path}", exit_code=2)
    return payload


def _write_launch_probe_state_atomic(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.parent / f".{path.name}.tmp.{os.getpid()}.{uuid.uuid4().hex}"
    try:
        with temporary.open("x", encoding="utf-8") as handle:
            os.fchmod(handle.fileno(), 0o600)
            json.dump(payload, handle, ensure_ascii=True, sort_keys=True, separators=(",", ":"))
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        temporary.replace(path)
    finally:
        try:
            temporary.unlink()
        except FileNotFoundError:
            pass


def _pid_is_alive(pid: int) -> bool:
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


class _LaunchProbe:
    """Opt-in, injected process-start-interval probe for deterministic tests.

    The probe has no default artifact path and therefore performs no I/O unless
    a launch-injection environment variable is explicitly set.  Concurrent
    state updates use a separate flock file plus atomic replacement so readers
    never observe partial JSON.
    """

    _STAGES = {"before_active", "after_active", "after_delay", "after_barrier"}

    def __init__(
        self,
        provider: str,
        *,
        cli_overrides: dict[str, str] | None = None,
    ) -> None:
        self.provider = provider
        self.agent = (
            _launch_probe_env(provider, "AGENT")
            or (os.environ.get("CCB_CALLER_ACTOR") or "").strip()
            or (os.environ.get("CCB_AGENT_NAME") or "").strip()
            or "unknown"
        )
        self.state_path = _launch_probe_path(provider, "STATE_PATH", cli_overrides)
        self.barrier_path = _launch_probe_path(provider, "BARRIER_PATH", cli_overrides)
        self.delay_s = _launch_probe_float(
            provider,
            "DELAY",
            default=0.0,
            overrides=cli_overrides,
        )
        self.barrier_timeout_s = _launch_probe_float(
            provider,
            "BARRIER_TIMEOUT",
            default=30.0,
            overrides=cli_overrides,
        )
        self.failure_stage = _launch_probe_env(provider, "FAIL_STAGE", cli_overrides).lower()
        self.cancel_stage = _launch_probe_env(provider, "CANCEL_STAGE", cli_overrides).lower()
        self.failure_providers = _launch_probe_env(
            provider,
            "FAIL_PROVIDERS",
            cli_overrides,
        )
        self.failure_agents = _launch_probe_env(
            provider,
            "FAIL_AGENTS",
            cli_overrides,
        )
        self.failure_match_indices = _launch_probe_int_set(
            provider,
            "FAIL_MATCH_INDICES",
            cli_overrides,
        )
        self.failure_release_dir = _launch_probe_path(
            provider,
            "FAIL_RELEASE_DIR",
            cli_overrides,
        )
        self.failure_release_timeout_s = _launch_probe_float(
            provider,
            "FAIL_RELEASE_TIMEOUT",
            default=30.0,
            overrides=cli_overrides,
        )
        self.cancel_providers = _launch_probe_env(
            provider,
            "CANCEL_PROVIDERS",
            cli_overrides,
        )
        self.cancel_agents = _launch_probe_env(provider, "CANCEL_AGENTS", cli_overrides)
        self.failure_exit_code = _launch_probe_int(
            provider,
            "FAIL_EXIT_CODE",
            default=86,
            overrides=cli_overrides,
        )
        self.cancel_exit_code = _launch_probe_int(
            provider,
            "CANCEL_EXIT_CODE",
            default=130,
            overrides=cli_overrides,
        )
        self.run_id = _launch_probe_env(provider, "RUN_ID", cli_overrides) or ""
        if self.run_id and not LAUNCH_PROBE_RUN_ID_RE.fullmatch(self.run_id):
            raise _LaunchProbeExit(f"invalid launch probe run id: {self.run_id!r}", exit_code=2)
        if self.state_path is not None and not self.run_id:
            raise _LaunchProbeExit(
                "launch probe state-backed evidence requires an explicit nonempty run id",
                exit_code=2,
            )
        if self.failure_match_indices and self.state_path is None:
            raise _LaunchProbeExit(
                "launch probe fail match indices require a state path",
                exit_code=2,
            )
        if self.failure_match_indices and not self.failure_stage:
            raise _LaunchProbeExit(
                "launch probe fail match indices require a failure stage",
                exit_code=2,
            )
        if self.failure_release_dir is not None and not self.failure_match_indices:
            raise _LaunchProbeExit(
                "launch probe failure release directory requires fail match indices",
                exit_code=2,
            )
        self.token = uuid.uuid4().hex
        self.entered = False
        self.closed = False
        self.enabled = any(
            (
                self.state_path is not None,
                self.barrier_path is not None,
                self.delay_s > 0.0,
                bool(self.failure_stage),
                bool(self.cancel_stage),
            )
        )
        for stage in (self.failure_stage, self.cancel_stage):
            if stage and stage not in self._STAGES:
                raise _LaunchProbeExit(f"unknown launch probe injection stage: {stage!r}", exit_code=2)
        if self.failure_stage and self.cancel_stage and self.failure_stage == self.cancel_stage:
            raise _LaunchProbeExit(
                f"launch probe failure and cancellation target the same stage: {self.failure_stage}",
                exit_code=2,
            )

    def _update_state(self, mutate) -> None:
        if self.state_path is None:
            return
        try:
            import fcntl
        except ImportError as exc:
            raise _LaunchProbeExit("launch probe requires POSIX file locking", exit_code=2) from exc

        previous_signal_mask = None
        if hasattr(signal, "pthread_sigmask"):
            previous_signal_mask = signal.pthread_sigmask(signal.SIG_BLOCK, {signal.SIGINT, signal.SIGTERM})
        try:
            lock_path = self.state_path.parent / f".{self.state_path.name}.lock"
            lock_path.parent.mkdir(parents=True, exist_ok=True)
            with lock_path.open("a+", encoding="utf-8") as lock_handle:
                fcntl.flock(lock_handle.fileno(), fcntl.LOCK_EX)
                try:
                    state = _launch_probe_state(self.state_path, run_id=self.run_id)
                    active_processes = state["active_processes"]
                    live_run_ids: set[str] = set()
                    stale_tokens = [
                        token
                        for token, record in active_processes.items()
                        if not isinstance(record, dict) or not _pid_is_alive(int(record.get("pid") or 0))
                    ]
                    for token, record in active_processes.items():
                        if token in stale_tokens:
                            continue
                        record_run_id = str(record.get("run_id") or "").strip()
                        if not LAUNCH_PROBE_RUN_ID_RE.fullmatch(record_run_id):
                            raise _LaunchProbeExit(
                                f"launch probe state has a live process without a valid run id: {self.state_path}",
                                exit_code=2,
                            )
                        live_run_ids.add(record_run_id)
                    state_run_id = str(state.get("run_id") or "").strip()
                    if len(live_run_ids) > 1 or (live_run_ids and live_run_ids != {state_run_id}):
                        raise _LaunchProbeExit(
                            f"launch probe state has mixed active run ids: {self.state_path}",
                            exit_code=2,
                        )
                    if state_run_id != self.run_id:
                        if live_run_ids:
                            raise _LaunchProbeExit(
                                f"launch probe state is active for a different run id: {self.state_path}",
                                exit_code=2,
                            )
                        # A completed explicit run owns a separate evidence
                        # epoch.  Reusing the path for a new run must not carry
                        # historical maxima or events into the new result.
                        state = _new_launch_probe_state(self.run_id)
                        active_processes = state["active_processes"]
                        stale_tokens = []
                    for stale_token in stale_tokens:
                        stale_record = active_processes.pop(stale_token, {})
                        self._append_event(
                            state,
                            "stale_reaped",
                            pid=stale_record.get("pid"),
                            provider=stale_record.get("provider"),
                            agent=stale_record.get("agent"),
                            run_id=stale_record.get("run_id"),
                        )
                    mutate(state)
                    state["active"] = len(active_processes)
                    state["max_observed"] = max(int(state.get("max_observed") or 0), state["active"])
                    _write_launch_probe_state_atomic(self.state_path, state)
                finally:
                    fcntl.flock(lock_handle.fileno(), fcntl.LOCK_UN)
        finally:
            if previous_signal_mask is not None:
                signal.pthread_sigmask(signal.SIG_SETMASK, previous_signal_mask)

    @staticmethod
    def _append_event(state: dict, event: str, **details) -> None:
        sequence = int(state.get("next_event_seq") or 1)
        state["next_event_seq"] = sequence + 1
        payload = {
            "seq": sequence,
            "event": event,
            "timestamp_ns": time.time_ns(),
            "monotonic_ns": time.monotonic_ns(),
        }
        payload.update({key: value for key, value in details.items() if value is not None and value != ""})
        state["events"].append(payload)

    def _event(self, event: str, **details) -> None:
        def _mutate(state: dict) -> None:
            self._append_event(
                state,
                event,
                pid=os.getpid(),
                provider=self.provider,
                agent=self.agent,
                run_id=self.run_id,
                **details,
            )

        self._update_state(_mutate)

    def _matches_injection(self, *, outcome: str) -> bool:
        if outcome == "failure":
            return _launch_probe_selector_matches(self.failure_providers, self.provider) and _launch_probe_selector_matches(
                self.failure_agents, self.agent
            )
        return _launch_probe_selector_matches(self.cancel_providers, self.provider) and _launch_probe_selector_matches(
            self.cancel_agents, self.agent
        )

    def _inject_at(self, stage: str) -> None:
        if self.failure_stage == stage and self._matches_injection(outcome="failure"):
            selected, match_index = self._claim_failure_match(stage)
            if selected:
                self._wait_for_failure_release(stage=stage, match_index=match_index)
                self._event("injected_failure", stage=stage, match_index=match_index)
                raise _LaunchProbeExit(
                    f"injected launch failure for provider={self.provider} agent={self.agent} stage={stage}",
                    exit_code=self.failure_exit_code,
                )
        if self.cancel_stage == stage and self._matches_injection(outcome="cancellation"):
            self._event("injected_cancellation", stage=stage)
            raise _LaunchProbeExit(
                f"injected launch cancellation for provider={self.provider} agent={self.agent} stage={stage}",
                exit_code=self.cancel_exit_code,
            )

    def _claim_failure_match(self, stage: str) -> tuple[bool, int | None]:
        if not self.failure_match_indices:
            return True, None
        selected = False
        match_index = 0
        key = hashlib.sha256(
            f"failure:{stage}:{self.provider}:{self.agent}".encode("utf-8")
        ).hexdigest()

        def _mutate(state: dict) -> None:
            nonlocal selected, match_index
            counts = state["injection_match_counts"]
            match_index = int(counts.get(key) or 0) + 1
            counts[key] = match_index
            selected = match_index in self.failure_match_indices
            self._append_event(
                state,
                "injection_match",
                pid=os.getpid(),
                provider=self.provider,
                agent=self.agent,
                run_id=self.run_id,
                outcome="failure",
                stage=stage,
                match_index=match_index,
                selected=selected,
            )

        self._update_state(_mutate)
        return selected, match_index

    def _wait_for_failure_release(self, *, stage: str, match_index: int | None) -> None:
        if self.failure_release_dir is None:
            return
        if match_index is None:
            raise _LaunchProbeExit(
                "launch probe selected failure has no match index",
                exit_code=2,
            )
        release_path = self.failure_release_dir / f"match-{match_index:06d}.release"
        self._event("injected_failure_armed", stage=stage, match_index=match_index)
        deadline = time.monotonic() + self.failure_release_timeout_s
        while not release_path.is_file():
            if time.monotonic() >= deadline:
                self._event("injected_failure_release_timeout", stage=stage, match_index=match_index)
                raise _LaunchProbeExit(
                    f"injected launch failure release timed out at match {match_index}",
                    exit_code=124,
                )
            time.sleep(0.005)
        self._event("injected_failure_released", stage=stage, match_index=match_index)

    def _enter(self) -> None:
        # Set this before the durable mutation so a signal delivered between
        # state publication and return still runs the cleanup path.
        self.entered = True

        def _mutate(state: dict) -> None:
            state["active_processes"][self.token] = {
                "pid": os.getpid(),
                "provider": self.provider,
                "agent": self.agent,
                "run_id": self.run_id,
                "entered_ns": time.time_ns(),
            }
            self._append_event(
                state,
                "entered",
                pid=os.getpid(),
                provider=self.provider,
                agent=self.agent,
                run_id=self.run_id,
                active=len(state["active_processes"]),
            )

        self._update_state(_mutate)

    def install_signal_handlers(self) -> None:
        if not self.enabled:
            return

        def _signal_handler(signum, _frame):
            self.close(reason=f"signal:{signal.Signals(signum).name}")
            raise SystemExit(0)

        signal.signal(signal.SIGTERM, _signal_handler)
        signal.signal(signal.SIGINT, _signal_handler)

    def start(self) -> None:
        if not self.enabled:
            return
        atexit.register(self.close, "atexit")
        self.install_signal_handlers()
        self._inject_at("before_active")
        self._enter()
        self._inject_at("after_active")
        if self.delay_s:
            self._event("delay_started", duration_s=self.delay_s)
            time.sleep(self.delay_s)
            self._event("delay_finished", duration_s=self.delay_s)
        self._inject_at("after_delay")
        if self.barrier_path is not None:
            self._event("barrier_wait_started", barrier_path=str(self.barrier_path))
            deadline = time.monotonic() + self.barrier_timeout_s
            while not self.barrier_path.exists():
                if time.monotonic() >= deadline:
                    self._event("barrier_timeout", barrier_path=str(self.barrier_path))
                    raise _LaunchProbeExit(
                        f"launch barrier timed out: {self.barrier_path}",
                        exit_code=124,
                    )
                time.sleep(0.01)
            self._event("barrier_released", barrier_path=str(self.barrier_path))
        self._inject_at("after_barrier")
        self._event("startup_probe_complete")
        # Active and max_observed measure only the injected provider process-
        # start interval.  Keeping
        # a long-lived provider registered after it is ready would make a
        # completely serial launch look concurrent.
        self.close(reason="startup_probe_complete")

    def close(self, reason: str = "normal") -> None:
        if self.closed:
            return
        self.closed = True
        if not self.entered:
            return

        def _mutate(state: dict) -> None:
            record = state["active_processes"].get(self.token)
            if isinstance(record, dict) and int(record.get("pid") or 0) == os.getpid():
                state["active_processes"].pop(self.token, None)
            self._append_event(
                state,
                "exited",
                pid=os.getpid(),
                provider=self.provider,
                agent=self.agent,
                run_id=self.run_id,
                reason=reason,
                active=len(state["active_processes"]),
            )

        self._update_state(_mutate)


def _now_ms() -> int:
    return int(time.time() * 1000)


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def _delay(provider: str) -> float:
    for key in (f"{provider.upper()}_STUB_DELAY", "STUB_DELAY"):
        raw = os.environ.get(key)
        if not raw:
            continue
        try:
            return max(0.0, float(raw))
        except Exception:
            continue
    return 0.0


def _mode(provider: str) -> str:
    for key in (f"{provider.upper()}_STUB_MODE", "NATIVE_CLI_STUB_MODE", "STUB_MODE"):
        raw = os.environ.get(key)
        if raw:
            return raw.strip().lower()
    return ""


def _project_hash(path: Path) -> str:
    try:
        normalized = str(path.expanduser().absolute())
    except Exception:
        normalized = str(path)
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


def _kimi_project_hash(path: Path) -> str:
    try:
        normalized = str(path.expanduser().absolute())
    except Exception:
        normalized = str(path)
    return hashlib.md5(normalized.encode("utf-8", "surrogateescape")).hexdigest()


def _deepseek_project_code(path: Path) -> str:
    try:
        normalized = str(path.expanduser().absolute())
    except Exception:
        normalized = str(path)
    legacy = normalized.replace("\\", "-").replace("/", "-").replace(":", "")
    if len(legacy) <= 64:
        return legacy
    digest = hashlib.sha256(normalized.encode("utf-8", "surrogateescape")).hexdigest()[:16]
    basename = re.sub(r"[^A-Za-z0-9_.-]", "-", Path(normalized).name).strip("-.") or "project"
    prefix = basename[: max(1, 64 - len(digest) - 1)].rstrip("-.") or "project"
    return f"{prefix}-{digest}"


def _claude_project_key(path: Path) -> str:
    return re.sub(r"[^A-Za-z0-9]", "-", str(path))


def _write_json_atomic(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=True, indent=2) + "\n", encoding="utf-8")
    tmp.replace(path)


def _append_jsonl(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(payload, ensure_ascii=True) + "\n")


def _load_json(path: Path) -> dict:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}
    return payload if isinstance(payload, dict) else {}


def _iter_hook_commands(provider: str, workspace: Path) -> list[str]:
    if provider == "gemini":
        settings = _load_json(workspace / ".gemini" / "settings.json")
        hooks = settings.get("hooks")
        groups = hooks.get("AfterAgent") if isinstance(hooks, dict) else None
        if isinstance(groups, list):
            return _extract_hook_commands(groups)
        return []
    if provider == "claude":
        settings = _load_json(workspace / ".claude" / "settings.local.json")
        hooks = settings.get("hooks")
        groups = hooks.get("Stop") if isinstance(hooks, dict) else None
        if isinstance(groups, list):
            return _extract_hook_commands(groups)
        return []
    return []


def _extract_hook_commands(groups: list[object]) -> list[str]:
    commands: list[str] = []
    for group in groups:
        if not isinstance(group, dict):
            continue
        hooks = group.get("hooks")
        if not isinstance(hooks, list):
            continue
        for hook in hooks:
            if not isinstance(hook, dict):
                continue
            if str(hook.get("type") or "").strip().lower() != "command":
                continue
            command = str(hook.get("command") or "").strip()
            if command:
                commands.append(command)
    return commands


def _hook_context(provider: str, workspace: Path) -> tuple[Path, str, Path] | None:
    for command in _iter_hook_commands(provider, workspace):
        try:
            parts = shlex.split(command)
        except Exception:
            continue
        completion_dir = ""
        agent_name = ""
        workspace_path = ""
        index = 0
        while index < len(parts):
            token = parts[index]
            if token == "--completion-dir" and index + 1 < len(parts):
                completion_dir = parts[index + 1]
                index += 2
                continue
            if token == "--agent-name" and index + 1 < len(parts):
                agent_name = parts[index + 1]
                index += 2
                continue
            if token == "--workspace" and index + 1 < len(parts):
                workspace_path = parts[index + 1]
                index += 2
                continue
            index += 1
        if completion_dir and agent_name and workspace_path:
            return (
                Path(completion_dir).expanduser(),
                agent_name,
                Path(workspace_path).expanduser(),
            )
    return None


def _write_hook_event(provider: str, workspace: Path, req_id: str, reply: str) -> None:
    context = _hook_context(provider, workspace)
    if context is None:
        return
    completion_dir, agent_name, workspace_path = context
    try:
        repo_root = Path(__file__).resolve().parents[2]
        sys.path.insert(0, str(repo_root / "lib"))
        from provider_hooks.artifacts import write_event
    except Exception:
        return

    try:
        write_event(
            provider=provider,
            completion_dir=completion_dir,
            agent_name=agent_name,
            workspace_path=str(workspace_path),
            req_id=req_id,
            status="completed",
            reply=reply,
            session_id=f"stub-{provider}-{req_id}",
            hook_event_name="AfterAgent" if provider == "gemini" else "Stop",
            diagnostics={"source": "provider_stub"},
        )
    except Exception:
        return


def _request_message(prompt: str) -> str:
    raw = str(prompt or "").strip()
    if not raw:
        return ""
    match = REQUEST_PATH_RE.search(raw)
    if not match:
        lines = raw.splitlines()
        body = [line for line in lines[1:] if line.strip()]
        return "\n".join(body).strip() or raw
    request_path = Path(match.group(1)).expanduser()
    try:
        return request_path.read_text(encoding="utf-8").strip()
    except Exception:
        return raw


def _sync_prompt_buffer_request(
    line: str,
    current_lines: list[str],
    current_req: str,
) -> tuple[list[str], str]:
    match = REQ_ID_RE.match(line)
    if not match:
        return current_lines, current_req

    next_req = match.group(1).strip()
    # Exact-turn prompts can complete before optional guidance has drained from
    # stdin. A new outer anchor starts a fresh request, so drop that stale tail.
    if current_lines and current_req != next_req:
        current_lines = []
    return current_lines, next_req


def _looks_like_exact_turn_prompt(provider: str, line: str, current_lines: list[str], current_req: str) -> bool:
    if not current_req:
        return False
    if provider == "codex":
        prefix = f"CCB_REQ_ID: {current_req}"
        if line.startswith(prefix) and line[len(prefix) :].strip():
            return True
        if len(current_lines) >= 3 and not current_lines[1].strip() and line.strip():
            return True
    if provider == "gemini":
        return "Execute the full request from @" in line
    if provider in {"claude", "codex"}:
        if line.strip():
            return False
        body_lines = [item for item in current_lines[1:] if item.strip()]
        return bool(body_lines)
    if provider in {"agy", "kimi", "deepseek"}:
        return line.strip() == "- Avoid raw logs and background unless explicitly requested."
    if provider == "mimo":
        return bool(len(current_lines) >= 3 and line.strip())
    return False


def _codex_log_path() -> Path:
    explicit = (os.environ.get("CODEX_LOG_PATH") or "").strip()
    if explicit:
        return Path(explicit).expanduser()
    root = Path(os.environ.get("CODEX_SESSION_ROOT") or (Path.home() / ".codex" / "sessions")).expanduser()
    sid = (os.environ.get("CCB_SESSION_ID") or "").strip() or f"stub-{uuid.uuid4().hex}"
    return root / sid / f"{sid}.jsonl"


def _ensure_codex_meta(path: Path, cwd: str) -> None:
    try:
        if path.exists() and path.stat().st_size > 0:
            return
    except OSError:
        return
    meta = {"type": "session_meta", "payload": {"cwd": cwd}}
    _append_jsonl(path, meta)


def _handle_codex(req_id: str, prompt: str, delay_s: float) -> None:
    log_path = _codex_log_path()
    _ensure_codex_meta(log_path, os.getcwd())
    turn_id = f"turn-{req_id}"
    task_id = f"task-{req_id}"
    reply = f"stub reply for {req_id}"
    user_entry = {
        "timestamp": _now_iso(),
        "type": "response_item",
        "payload": {
            "type": "message",
            "role": "user",
            "turn_id": turn_id,
            "task_id": task_id,
            "content": [{"type": "input_text", "text": prompt}],
        },
    }
    _append_jsonl(log_path, user_entry)
    if delay_s:
        time.sleep(delay_s)
    assistant_entry = {
        "timestamp": _now_iso(),
        "type": "event_msg",
        "payload": {
            "type": "agent_message",
            "role": "assistant",
            "turn_id": turn_id,
            "task_id": task_id,
            "phase": "final_answer",
            "message": reply,
        },
    }
    terminal_entry = {
        "timestamp": _now_iso(),
        "type": "event_msg",
        "payload": {
            "type": "task_complete",
            "turn_id": turn_id,
            "task_id": task_id,
            "reason": "task_complete",
            "last_agent_message": reply,
        },
    }
    _append_jsonl(log_path, assistant_entry)
    _append_jsonl(log_path, terminal_entry)


def _gemini_session_path() -> Path:
    explicit = (os.environ.get("GEMINI_SESSION_PATH") or "").strip()
    if explicit:
        return Path(explicit).expanduser()
    root = Path(os.environ.get("GEMINI_ROOT") or (Path.home() / ".gemini" / "tmp")).expanduser()
    project_hash = _project_hash(Path.cwd())
    sid = (os.environ.get("CCB_SESSION_ID") or "").strip() or f"stub-{uuid.uuid4().hex}"
    return root / project_hash / "chats" / f"session-{sid}.json"


def _load_gemini_messages(path: Path) -> list[dict]:
    if not path.exists():
        return []
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return []
    messages = data.get("messages") if isinstance(data, dict) else None
    return messages if isinstance(messages, list) else []


def _write_gemini_session(path: Path, session_id: str, messages: list[dict]) -> None:
    payload = {"sessionId": session_id, "messages": messages}
    _write_json_atomic(path, payload)


def _claude_session_path() -> Path:
    explicit = (os.environ.get("CLAUDE_SESSION_PATH") or "").strip()
    if explicit:
        return Path(explicit).expanduser()
    root = Path(os.environ.get("CLAUDE_PROJECTS_ROOT") or (Path.home() / ".claude" / "projects")).expanduser()
    key = _claude_project_key(Path.cwd())
    sid = (os.environ.get("CLAUDE_SESSION_ID") or "").strip() or f"stub-{uuid.uuid4().hex}"
    return root / key / f"{sid}.jsonl"


def _handle_claude(req_id: str, prompt: str, delay_s: float, session_path: Path) -> None:
    user_uuid = f"user-{uuid.uuid4().hex}"
    user_entry = {
        "type": "user",
        "uuid": user_uuid,
        "message": {
            "role": "user",
            "content": [{"type": "text", "text": prompt}],
        },
    }
    _append_jsonl(session_path, user_entry)
    if delay_s:
        time.sleep(delay_s)
    reply = f"stub reply for {req_id}\nCCB_DONE: {req_id}"
    assistant_uuid = f"assistant-{uuid.uuid4().hex}"
    assistant_entry = {
        "type": "assistant",
        "uuid": assistant_uuid,
        "parentUuid": user_uuid,
        "message": {
            "role": "assistant",
            "stop_reason": "end_turn",
            "content": [{"type": "text", "text": reply}],
        },
    }
    _append_jsonl(session_path, assistant_entry)
    _append_jsonl(
        session_path,
        {
            "type": "system",
            "subtype": "turn_duration",
            "parentUuid": assistant_uuid,
            "durationMs": max(1, int(delay_s * 1000)),
        },
    )


def _opencode_storage_root() -> Path:
    return Path(os.environ.get("OPENCODE_STORAGE_ROOT") or (Path.home() / ".opencode" / "storage")).expanduser()


def _opencode_ids() -> tuple[str, str]:
    project_id = (os.environ.get("OPENCODE_PROJECT_ID") or "").strip()
    if not project_id:
        project_id = f"proj-{_project_hash(Path.cwd())[:12]}"
    session_id = (os.environ.get("CCB_SESSION_ID") or "").strip()
    if not session_id:
        session_id = f"ses_{project_id}"
    return project_id, session_id


def _write_opencode_storage(root: Path, project_id: str, session_id: str, reply: str, msg_index: int) -> None:
    root = root.expanduser()
    now = _now_ms()
    work_dir = str(Path.cwd())

    project_payload = {"id": project_id, "worktree": work_dir, "time": {"updated": now}}
    session_payload = {"id": session_id, "directory": work_dir, "time": {"updated": now}}

    msg_id = f"msg_{msg_index}"
    part_id = f"prt_{msg_index}"
    msg_payload = {"id": msg_id, "sessionID": session_id, "role": "assistant", "time": {"created": now, "completed": now}}
    part_payload = {"id": part_id, "messageID": msg_id, "type": "text", "text": reply, "time": {"start": now}}

    (root / "project").mkdir(parents=True, exist_ok=True)
    (root / "session" / project_id).mkdir(parents=True, exist_ok=True)
    (root / "message" / session_id).mkdir(parents=True, exist_ok=True)
    (root / "part" / msg_id).mkdir(parents=True, exist_ok=True)

    (root / "project" / f"{project_id}.json").write_text(json.dumps(project_payload, ensure_ascii=True, indent=2) + "\n", encoding="utf-8")
    (root / "session" / project_id / f"{session_id}.json").write_text(
        json.dumps(session_payload, ensure_ascii=True, indent=2) + "\n", encoding="utf-8"
    )
    (root / "message" / session_id / f"{msg_id}.json").write_text(
        json.dumps(msg_payload, ensure_ascii=True, indent=2) + "\n", encoding="utf-8"
    )
    (root / "part" / msg_id / f"{part_id}.json").write_text(
        json.dumps(part_payload, ensure_ascii=True, indent=2) + "\n", encoding="utf-8"
    )


def _handle_opencode(req_id: str, delay_s: float, state: dict) -> None:
    if delay_s:
        time.sleep(delay_s)
    reply = f"stub reply for {req_id}\nCCB_DONE: {req_id}"
    state["msg_index"] += 1
    root = state["storage_root"]
    project_id = state["project_id"]
    session_id = state["session_id"]
    _write_opencode_storage(root, project_id, session_id, reply, state["msg_index"])


def _mimo_storage_root() -> Path:
    home = (os.environ.get("MIMOCODE_HOME") or "").strip()
    if home:
        return Path(home).expanduser() / "data" / "storage"
    return Path.home() / ".local" / "share" / "mimocode" / "storage"


def _mimo_ids() -> tuple[str, str]:
    project_id = (os.environ.get("MIMOCODE_PROJECT_ID") or "").strip()
    if not project_id:
        project_id = f"proj-{_project_hash(Path.cwd())[:12]}"
    session_id = (os.environ.get("CCB_SESSION_ID") or "").strip()
    if not session_id:
        session_id = f"ses_{project_id}"
    return project_id, session_id


def _ensure_mimo_db(db_path: Path) -> None:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(db_path) as conn:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS session (
                id TEXT PRIMARY KEY,
                directory TEXT NOT NULL,
                time_updated INTEGER NOT NULL
            );
            CREATE TABLE IF NOT EXISTS message (
                id TEXT PRIMARY KEY,
                session_id TEXT NOT NULL,
                time_created INTEGER NOT NULL,
                time_updated INTEGER NOT NULL,
                data TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS part (
                id TEXT PRIMARY KEY,
                message_id TEXT NOT NULL,
                session_id TEXT NOT NULL,
                time_created INTEGER NOT NULL,
                time_updated INTEGER NOT NULL,
                data TEXT NOT NULL
            );
            """
        )


def _write_mimo_storage(root: Path, session_id: str, prompt: str, reply: str, msg_index: int) -> None:
    root = root.expanduser()
    root.mkdir(parents=True, exist_ok=True)
    now = _now_ms()
    db_path = root.parent / "mimocode.db"
    _ensure_mimo_db(db_path)
    user_msg_id = f"msg_user_{msg_index}"
    assistant_msg_id = f"msg_assistant_{msg_index}"
    user_part_id = f"prt_user_{msg_index}"
    assistant_part_id = f"prt_assistant_{msg_index}"
    user_payload = {
        "id": user_msg_id,
        "sessionID": session_id,
        "role": "user",
        "time": {"created": now, "completed": now},
    }
    assistant_payload = {
        "id": assistant_msg_id,
        "sessionID": session_id,
        "parentID": user_msg_id,
        "role": "assistant",
        "time": {"created": now + 1, "completed": now + 2},
        "finish": "stop",
    }
    user_part = {"id": user_part_id, "messageID": user_msg_id, "type": "text", "text": prompt, "time": {"start": now, "end": now}}
    assistant_part = {
        "id": assistant_part_id,
        "messageID": assistant_msg_id,
        "type": "text",
        "text": reply,
        "time": {"start": now + 1, "end": now + 2},
    }
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            "INSERT OR REPLACE INTO session (id, directory, time_updated) VALUES (?, ?, ?)",
            (session_id, str(Path.cwd()), now + 2),
        )
        conn.execute(
            "INSERT OR REPLACE INTO message (id, session_id, time_created, time_updated, data) VALUES (?, ?, ?, ?, ?)",
            (user_msg_id, session_id, now, now, json.dumps(user_payload, ensure_ascii=True)),
        )
        conn.execute(
            "INSERT OR REPLACE INTO message (id, session_id, time_created, time_updated, data) VALUES (?, ?, ?, ?, ?)",
            (assistant_msg_id, session_id, now + 1, now + 2, json.dumps(assistant_payload, ensure_ascii=True)),
        )
        conn.execute(
            "INSERT OR REPLACE INTO part (id, message_id, session_id, time_created, time_updated, data) VALUES (?, ?, ?, ?, ?, ?)",
            (user_part_id, user_msg_id, session_id, now, now, json.dumps(user_part, ensure_ascii=True)),
        )
        conn.execute(
            "INSERT OR REPLACE INTO part (id, message_id, session_id, time_created, time_updated, data) VALUES (?, ?, ?, ?, ?, ?)",
            (assistant_part_id, assistant_msg_id, session_id, now + 1, now + 2, json.dumps(assistant_part, ensure_ascii=True)),
        )
        conn.commit()


def _handle_mimo(req_id: str, prompt: str, delay_s: float, state: dict) -> None:
    if delay_s:
        time.sleep(delay_s)
    reply = f"stub reply for {req_id}"
    state["msg_index"] += 1
    _write_mimo_storage(state["storage_root"], state["session_id"], prompt, reply, state["msg_index"])


def _mimo_run_prompt(argv: list[str]) -> str | None:
    if "run" not in argv:
        return None
    index = argv.index("run") + 1
    message_parts: list[str] = []
    options_with_values = {
        "--agent",
        "--dir",
        "--format",
        "--model",
        "--session",
    }
    while index < len(argv):
        token = argv[index]
        if token in options_with_values:
            index += 2
            continue
        if token.startswith("--"):
            index += 1
            continue
        message_parts.append(token)
        index += 1
    return " ".join(message_parts).strip()


def _handle_mimo_run_cli(argv: list[str], delay_s: float) -> int:
    prompt = _mimo_run_prompt(argv) or ""
    req_match = REQ_ID_RE.search(prompt)
    req_id = req_match.group(1).strip() if req_match else "job_mimo_run"
    if delay_s:
        time.sleep(delay_s)
    reply = f"stub reply for {req_id}"
    print(
        json.dumps(
            {
                "type": "text",
                "sessionID": f"ses_{req_id}",
                "part": {
                    "id": f"prt-{req_id}",
                    "messageID": f"msg-{req_id}",
                    "sessionID": f"ses_{req_id}",
                    "type": "text",
                    "text": reply,
                },
            },
            ensure_ascii=True,
        ),
        flush=True,
    )
    print(
        json.dumps(
            {
                "type": "step_finish",
                "sessionID": f"ses_{req_id}",
                "timestamp": _now_iso(),
                "part": {
                    "id": f"step-{req_id}",
                    "messageID": f"msg-{req_id}",
                    "sessionID": f"ses_{req_id}",
                    "type": "step-finish",
                    "reason": "stop",
                },
            },
            ensure_ascii=True,
        ),
        flush=True,
    )
    return 0


def _native_cli_prompt(provider: str, argv: list[str]) -> str | None:
    if provider == "mimo":
        return _mimo_run_prompt(argv)
    if provider == "qwen" and "--bare" in argv:
        return _last_positional(argv, options_with_values={"--output-format", "--session-id", "--model"})
    if provider in {"qoder", "qodercn"} and ("-p" in argv or "--print" in argv):
        return _last_positional(
            argv,
            options_with_values={
                "--config-dir",
                "--output-format",
                "--permission-mode",
                "--session-id",
                "--model",
                "-w",
            },
        )
    if provider == "cursor" and "--print" in argv:
        return _last_positional(argv, options_with_values={"--output-format", "--workspace", "--model"})
    if provider == "copilot" and "-p" in argv:
        index = argv.index("-p")
        return argv[index + 1] if index + 1 < len(argv) else ""
    if provider == "grok" and "-p" in argv:
        index = argv.index("-p")
        return argv[index + 1] if index + 1 < len(argv) else ""
    if provider == "crush" and "run" in argv:
        return _last_positional(argv, options_with_values={"--data-dir", "--cwd", "--model"})
    if provider == "kiro" and "chat" in argv and "--no-interactive" in argv:
        return _last_positional(argv, options_with_values={"--wrap", "--model"})
    if provider in {"pi", "omp"} and "--mode" in argv and "json" in argv:
        return _last_positional(
            argv,
            options_with_values={
                "--api-key",
                "--append-system-prompt",
                "--exclude-tools",
                "--extension",
                "--model",
                "--models",
                "--mode",
                "--name",
                "--approval-mode",
                "--provider",
                "--session",
                "--session-dir",
                "--skill",
                "--system-prompt",
                "--theme",
                "--thinking",
                "--tools",
            },
        )
    if provider == "zai" and "--prompt" in argv:
        index = argv.index("--prompt")
        return argv[index + 1] if index + 1 < len(argv) else ""
    return None


def _last_positional(argv: list[str], *, options_with_values: set[str]) -> str:
    positional: list[str] = []
    index = 0
    while index < len(argv):
        token = argv[index]
        if token in options_with_values:
            index += 2
            continue
        if token.startswith("-"):
            index += 1
            continue
        positional.append(token)
        index += 1
    return positional[-1] if positional else ""


def _handle_native_cli_run(provider: str, argv: list[str], delay_s: float) -> int:
    prompt = _native_cli_prompt(provider, argv) or ""
    req_match = REQ_ID_RE.search(prompt)
    req_id = req_match.group(1).strip() if req_match else f"job_{provider}_run"
    mode = _mode(provider)
    if delay_s:
        time.sleep(delay_s)
    if mode in {"permission", "denied", "error"}:
        print(f"{provider} permission denied for {req_id}", file=sys.stderr, flush=True)
        return 13
    if mode == "timeout":
        time.sleep(float(os.environ.get("STUB_TIMEOUT_SLEEP", "5")))
        return 0
    reply = "" if mode == "empty" else f"stub reply for {req_id}"
    if provider in {"pi", "omp"}:
        print(
            json.dumps(
                {
                    "type": "session",
                    "version": 3,
                    "id": f"ses-{provider}-{req_id}",
                    "timestamp": _now_iso(),
                    "cwd": os.getcwd(),
                },
                ensure_ascii=True,
            ),
            flush=True,
        )
        if mode in {"tool", "tool_then_final"}:
            print(
                json.dumps(
                    {
                        "type": "tool_execution_start",
                        "toolCallId": f"tool-{req_id}",
                        "toolName": "stub_tool",
                        "args": {},
                    },
                    ensure_ascii=True,
                ),
                flush=True,
            )
        if reply:
            print(
                json.dumps(
                    {
                        "type": "message_update",
                        "message": {
                            "id": f"msg-{req_id}",
                            "role": "assistant",
                            "content": [{"type": "text", "text": reply}],
                        },
                        "assistantMessageEvent": {"type": "text_delta", "delta": reply},
                    },
                    ensure_ascii=True,
                ),
                flush=True,
            )
        print(
            json.dumps(
                {
                    "type": "turn_end",
                    "message": {
                        "id": f"msg-{req_id}",
                        "role": "assistant",
                        "content": ([{"type": "text", "text": reply}] if reply else []),
                    },
                    "toolResults": [],
                    "timestamp": _now_iso(),
                },
                ensure_ascii=True,
            ),
            flush=True,
        )
        print(
            json.dumps(
                {
                    "type": "agent_end",
                    "messages": [
                        {
                            "id": f"msg-{req_id}",
                            "role": "assistant",
                            "content": ([{"type": "text", "text": reply}] if reply else []),
                        }
                    ],
                },
                ensure_ascii=True,
            ),
            flush=True,
        )
        return 0
    if provider == "zai":
        print(json.dumps({"role": "user", "content": prompt}, ensure_ascii=True), flush=True)
        if reply:
            print(json.dumps({"role": "assistant", "content": reply}, ensure_ascii=True), flush=True)
        return 0
    if provider in {"crush", "kiro"}:
        if reply:
            print(reply, flush=True)
        return 0
    if provider == "grok":
        if mode == "cancelled":
            reply = ""
        if reply:
            midpoint = max(1, len(reply) // 2)
            print(json.dumps({"type": "text", "data": reply[:midpoint]}, ensure_ascii=True), flush=True)
            print(json.dumps({"type": "text", "data": reply[midpoint:]}, ensure_ascii=True), flush=True)
        if mode == "no_terminal":
            return 0
        print(
            json.dumps(
                {
                    "type": "end",
                    "stopReason": "Cancelled" if mode == "cancelled" else "EndTurn",
                    "sessionId": f"ses-grok-{req_id}",
                    "requestId": f"req-grok-{req_id}",
                },
                ensure_ascii=True,
            ),
            flush=True,
        )
        return 0
    if provider in {"qoder", "qodercn"}:
        session_id = f"11111111-1111-5111-8111-{abs(hash(req_id)) % 10**12:012d}"
        print(
            json.dumps(
                {
                    "type": "system",
                    "subtype": "init",
                    "session_id": session_id,
                    "permissionMode": "dontAsk",
                },
                ensure_ascii=True,
            ),
            flush=True,
        )
        if reply:
            print(
                json.dumps(
                    {
                        "type": "assistant",
                        "message": {
                            "role": "assistant",
                            "content": [{"type": "text", "text": reply}],
                        },
                        "session_id": session_id,
                    },
                    ensure_ascii=True,
                ),
                flush=True,
            )
        if mode == "no_terminal":
            return 0
        print(
            json.dumps(
                {
                    "type": "result",
                    "subtype": "success",
                    "is_error": False,
                    "result": reply,
                    "stop_reason": "end_turn",
                    "session_id": session_id,
                },
                ensure_ascii=True,
            ),
            flush=True,
        )
        return 0
    if mode in {"tool", "tool_then_final"}:
        print(
            json.dumps(
                {
                    "type": "tool_call",
                    "role": "assistant",
                    "request_id": req_id,
                    "name": "stub_tool",
                    "status": "tool_calls",
                },
                ensure_ascii=True,
            ),
            flush=True,
        )
    payload = {
        "type": "result",
        "role": "assistant",
        "request_id": req_id,
        "session_id": f"ses-{provider}-{req_id}",
        "finish_reason": "stop",
        "completed_at": _now_iso(),
    }
    if reply:
        payload["text"] = reply
    print(json.dumps(payload, ensure_ascii=True), flush=True)
    return 0


def _droid_sessions_root() -> Path:
    root = (os.environ.get("DROID_SESSIONS_ROOT") or os.environ.get("FACTORY_SESSIONS_ROOT") or "").strip()
    if root:
        return Path(root).expanduser()
    return (Path.home() / ".factory" / "sessions").expanduser()


def _droid_slug(path: Path) -> str:
    return re.sub(r"[^A-Za-z0-9]", "-", str(path))


def _droid_session_path() -> Path:
    explicit = (os.environ.get("DROID_SESSION_PATH") or "").strip()
    if explicit:
        return Path(explicit).expanduser()
    root = _droid_sessions_root()
    slug = _droid_slug(Path.cwd())
    sid = (os.environ.get("CCB_SESSION_ID") or "").strip() or f"stub-{uuid.uuid4().hex}"
    return root / slug / f"{sid}.jsonl"


def _ensure_droid_session_start(path: Path, session_id: str, cwd: str) -> None:
    try:
        if path.exists() and path.stat().st_size > 0:
            return
    except OSError:
        return
    entry = {"type": "session_start", "id": session_id, "cwd": cwd}
    _append_jsonl(path, entry)


def _handle_droid(req_id: str, prompt: str, delay_s: float, session_path: Path, session_id: str) -> None:
    _ensure_droid_session_start(session_path, session_id, os.getcwd())
    user_entry = {
        "type": "message",
        "id": f"msg-{uuid.uuid4().hex}",
        "message": {"role": "user", "content": [{"type": "text", "text": prompt}]},
    }
    _append_jsonl(session_path, user_entry)
    if delay_s:
        time.sleep(delay_s)
    reply = f"stub reply for {req_id}\nCCB_DONE: {req_id}"
    assistant_entry = {
        "type": "message",
        "id": f"msg-{uuid.uuid4().hex}",
        "message": {"role": "assistant", "content": [{"type": "text", "text": reply}]},
    }
    _append_jsonl(session_path, assistant_entry)


def _handle_pane_quiet(req_id: str, delay_s: float) -> None:
    if delay_s:
        time.sleep(delay_s)
    print(f"stub reply for {req_id}", flush=True)
    print(f"CCB_DONE: {req_id}", flush=True)


def _handle_kimi(req_id: str, prompt: str, delay_s: float) -> None:
    if delay_s:
        time.sleep(delay_s)
    reply = f"stub reply for {req_id}"
    sid = (os.environ.get("CCB_SESSION_ID") or "").strip() or "stub-kimi"
    wire = Path.home() / ".kimi" / "sessions" / _kimi_project_hash(Path.cwd()) / sid / "wire.jsonl"
    _append_jsonl(
        wire,
        {
            "timestamp": _now_iso(),
            "message": {
                "type": "TurnBegin",
                "payload": {"user_input": [{"type": "text", "text": prompt}]},
            },
        },
    )
    _append_jsonl(
        wire,
        {
            "timestamp": _now_iso(),
            "message": {"type": "ContentPart", "payload": {"type": "text", "text": reply}},
        },
    )
    _append_jsonl(
        wire,
        {
            "timestamp": _now_iso(),
            "message": {"type": "StatusUpdate", "payload": {"message_id": f"msg-{req_id}"}},
        },
    )
    _append_jsonl(wire, {"timestamp": _now_iso(), "message": {"type": "TurnEnd", "payload": {}}})
    print(reply, flush=True)


def _handle_deepseek(req_id: str, prompt: str, delay_s: float) -> None:
    if delay_s:
        time.sleep(delay_s)
    reply = f"stub reply for {req_id}"
    sid = (os.environ.get("CCB_SESSION_ID") or "").strip() or "stub-deepseek"
    root = Path.home() / ".deepcode" / "projects" / _deepseek_project_code(Path.cwd())
    index_path = root / "sessions-index.json"
    session_path = root / f"{sid}.jsonl"
    _write_json_atomic(
        index_path,
        {"sessions": [{"id": sid, "status": "completed", "assistantReply": reply, "updateTime": _now_iso()}]},
    )
    _append_jsonl(session_path, {"id": f"user-{req_id}", "role": "user", "content": prompt})
    _append_jsonl(session_path, {"id": f"assistant-{req_id}", "role": "assistant", "content": reply})
    print(reply, flush=True)


def _handle_agy(req_id: str, prompt: str, delay_s: float) -> None:
    if delay_s:
        time.sleep(delay_s)
    reply = f"stub reply for {req_id}"
    cid = (os.environ.get("CCB_SESSION_ID") or "").strip() or "stub-agy"
    transcript = (
        Path.home()
        / ".gemini"
        / "antigravity-cli"
        / "brain"
        / cid
        / ".system_generated"
        / "logs"
        / "transcript.jsonl"
    )
    _append_jsonl(
        transcript,
        {
            "step_index": 1,
            "source": "USER_EXPLICIT",
            "type": "USER_INPUT",
            "status": "DONE",
            "created_at": _now_iso(),
            "content": prompt,
        },
    )
    _append_jsonl(
        transcript,
        {
            "step_index": 2,
            "source": "MODEL",
            "type": "PLANNER_RESPONSE",
            "status": "DONE",
            "created_at": _now_iso(),
            "content": reply,
        },
    )
    print(reply, flush=True)
    _print_agy_ready_prompt()


def _print_agy_ready_prompt() -> None:
    print("────────────────────────────────────────────────────────────", flush=True)
    print(">", flush=True)
    print("────────────────────────────────────────────────────────────", flush=True)
    print("? for shortcuts                                                   Gemini 3.1 Pro (High)", flush=True)


def _without_launch_probe_cli_options(arguments: list[str]) -> list[str]:
    remaining: list[str] = []
    index = 0
    while index < len(arguments):
        item = arguments[index]
        option = item.partition("=")[0]
        if option not in LAUNCH_PROBE_CLI_OPTIONS:
            remaining.append(item)
            index += 1
            continue
        if "=" in item:
            index += 1
            continue
        # argparse has already rejected a missing value at this point.
        index += 2
    return remaining


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(add_help=False, allow_abbrev=False)
    parser.add_argument("--provider", default="")
    for option in LAUNCH_PROBE_CLI_OPTIONS:
        parser.add_argument(option, default="")
    args, _unknown = parser.parse_known_args(argv[1:])
    launch_probe_overrides = {
        probe_name: str(getattr(args, option[2:].replace("-", "_"), "") or "").strip()
        for option, probe_name in LAUNCH_PROBE_CLI_OPTIONS.items()
    }
    provider_arguments = _without_launch_probe_cli_options(argv[1:])

    provider = (args.provider or Path(argv[0]).name).strip().lower()
    if provider not in (
        "codex",
        "gemini",
        "claude",
        "opencode",
        "droid",
        "agy",
        "kimi",
        "deepseek",
        "mimo",
        "copilot",
        "codebuddy",
        "qwen",
        "qoder",
        "qodercn",
        "cursor",
        "crush",
        "grok",
        "kiro",
        "pi",
        "omp",
        "zai",
    ):
        print(f"[stub] unknown provider: {provider}", file=sys.stderr)
        return 2

    launch_probe: _LaunchProbe | None = None
    try:
        launch_probe = _LaunchProbe(provider, cli_overrides=launch_probe_overrides)
        launch_probe.start()
    except _LaunchProbeExit as exc:
        if launch_probe is not None:
            try:
                launch_probe.close(reason="startup_probe_exit")
            except _LaunchProbeExit:
                # Preserve the first fail-closed reason.  A state-authority
                # conflict can make both entry and cleanup reject the same
                # mutation, and cleanup must not mask that original evidence.
                pass
        print(f"[stub] {exc}", file=sys.stderr, flush=True)
        return exc.exit_code

    delay_s = _delay(provider)

    if provider == "mimo" and _mimo_run_prompt(provider_arguments) is not None:
        return _handle_mimo_run_cli(provider_arguments, delay_s)
    if provider in {"qwen", "qoder", "qodercn", "cursor", "copilot", "crush", "grok", "kiro", "pi", "omp", "zai"} and _native_cli_prompt(provider, provider_arguments) is not None:
        return _handle_native_cli_run(provider, provider_arguments, delay_s)

    # Provider-specific initialization.
    gemini_messages: list[dict] = []
    gemini_session_id = ""
    gemini_session_path = None
    claude_session_path = None
    opencode_state: dict | None = None
    mimo_state: dict | None = None
    droid_session_path: Path | None = None
    droid_session_id = ""
    copilot_session_path: Path | None = None
    copilot_session_id = ""
    codebuddy_session_path: Path | None = None
    codebuddy_session_id = ""
    qwen_session_path: Path | None = None
    qwen_session_id = ""

    if provider == "gemini":
        gemini_session_path = _gemini_session_path()
        gemini_session_id = (os.environ.get("CCB_SESSION_ID") or "").strip() or f"stub-{uuid.uuid4().hex}"
        gemini_messages = _load_gemini_messages(gemini_session_path)
        _write_gemini_session(gemini_session_path, gemini_session_id, gemini_messages)
    elif provider == "claude":
        claude_session_path = _claude_session_path()
        claude_session_path.parent.mkdir(parents=True, exist_ok=True)
        if not claude_session_path.exists():
            claude_session_path.write_text("", encoding="utf-8")
    elif provider == "opencode":
        project_id, session_id = _opencode_ids()
        opencode_state = {
            "storage_root": _opencode_storage_root(),
            "project_id": project_id,
            "session_id": session_id,
            "msg_index": 0,
        }
    elif provider == "mimo":
        _project_id, session_id = _mimo_ids()
        mimo_state = {
            "storage_root": _mimo_storage_root(),
            "session_id": session_id,
            "msg_index": 0,
        }
    elif provider == "droid":
        droid_session_path = _droid_session_path()
        droid_session_id = (os.environ.get("CCB_SESSION_ID") or "").strip() or f"stub-{uuid.uuid4().hex}"
        _ensure_droid_session_start(droid_session_path, droid_session_id, os.getcwd())
    elif provider == "copilot":
        copilot_session_id = (os.environ.get("COPILOT_SESSION_ID") or "").strip() or f"stub-{uuid.uuid4().hex}"
        explicit = (os.environ.get("COPILOT_SESSION_PATH") or "").strip()
        if explicit:
            copilot_session_path = Path(explicit).expanduser()
        else:
            root = _droid_sessions_root()
            slug = _droid_slug(Path.cwd())
            copilot_session_path = root / slug / f"copilot-{copilot_session_id}.jsonl"
        _ensure_droid_session_start(copilot_session_path, copilot_session_id, os.getcwd())
    elif provider == "codebuddy":
        codebuddy_session_id = (os.environ.get("CODEBUDDY_SESSION_ID") or "").strip() or f"stub-{uuid.uuid4().hex}"
        explicit = (os.environ.get("CODEBUDDY_SESSION_PATH") or "").strip()
        if explicit:
            codebuddy_session_path = Path(explicit).expanduser()
        else:
            root = _droid_sessions_root()
            slug = _droid_slug(Path.cwd())
            codebuddy_session_path = root / slug / f"codebuddy-{codebuddy_session_id}.jsonl"
        _ensure_droid_session_start(codebuddy_session_path, codebuddy_session_id, os.getcwd())
    elif provider == "qwen":
        qwen_session_id = (os.environ.get("QWEN_SESSION_ID") or "").strip() or f"stub-{uuid.uuid4().hex}"
        explicit = (os.environ.get("QWEN_SESSION_PATH") or "").strip()
        if explicit:
            qwen_session_path = Path(explicit).expanduser()
        else:
            root = _droid_sessions_root()
            slug = _droid_slug(Path.cwd())
            qwen_session_path = root / slug / f"qwen-{qwen_session_id}.jsonl"
        _ensure_droid_session_start(qwen_session_path, qwen_session_id, os.getcwd())

    if provider == "kimi":
        print("Welcome to Kimi Code CLI!", flush=True)
        print("── input ─────────", flush=True)
        print("agent (stub-kimi ○)", flush=True)
    if provider == "agy":
        _print_agy_ready_prompt()

    def _handle_request(req_id: str, prompt: str) -> None:
        if provider == "codex":
            _handle_codex(req_id, prompt, delay_s)
            return
        if provider == "gemini":
            if delay_s:
                time.sleep(delay_s)
            reply = f"stub reply for {req_id}\nCCB_DONE: {req_id}"
            assert gemini_session_path is not None
            gemini_messages.append({"type": "user", "content": _request_message(prompt) or prompt})
            gemini_messages.append({"type": "gemini", "content": reply, "id": f"stub-{len(gemini_messages)}"})
            _write_gemini_session(gemini_session_path, gemini_session_id, gemini_messages)
            _write_hook_event(provider, Path.cwd(), req_id, f"stub reply for {req_id}")
            return
        if provider == "claude":
            assert claude_session_path is not None
            _handle_claude(req_id, prompt, delay_s, claude_session_path)
            _write_hook_event(provider, Path.cwd(), req_id, f"stub reply for {req_id}")
            return
        if provider == "opencode":
            assert opencode_state is not None
            _handle_opencode(req_id, delay_s, opencode_state)
            return
        if provider == "mimo":
            assert mimo_state is not None
            _handle_mimo(req_id, prompt, delay_s, mimo_state)
            return
        if provider == "droid":
            assert droid_session_path is not None
            _handle_droid(req_id, prompt, delay_s, droid_session_path, droid_session_id)
            return
        if provider == "agy":
            _handle_agy(req_id, prompt, delay_s)
            return
        if provider == "kimi":
            _handle_kimi(req_id, prompt, delay_s)
            return
        if provider == "deepseek":
            _handle_deepseek(req_id, prompt, delay_s)
            return
        if provider == "copilot":
            assert copilot_session_path is not None
            _handle_droid(req_id, prompt, delay_s, copilot_session_path, copilot_session_id)
            return
        if provider == "codebuddy":
            assert codebuddy_session_path is not None
            _handle_droid(req_id, prompt, delay_s, codebuddy_session_path, codebuddy_session_id)
            return
        if provider == "qwen":
            assert qwen_session_path is not None
            _handle_droid(req_id, prompt, delay_s, qwen_session_path, qwen_session_id)
            return

    def _signal_handler(_signum, _frame):
        if launch_probe is not None:
            launch_probe.close(reason=f"signal:{signal.Signals(_signum).name}")
        raise SystemExit(0)

    signal.signal(signal.SIGTERM, _signal_handler)
    signal.signal(signal.SIGINT, _signal_handler)

    current_lines: list[str] = []
    current_req = ""

    while True:
        line = sys.stdin.readline()
        if line == "":
            time.sleep(0.05)
            continue
        line = line.rstrip("\n")
        if not line and not current_lines:
            continue

        current_lines, current_req = _sync_prompt_buffer_request(line, current_lines, current_req)

        current_lines.append(line)

        m_done = DONE_RE.match(line)
        if m_done:
            if not current_req:
                current_req = m_done.group(1).strip()
            req_id = current_req or m_done.group(1).strip()
            prompt = "\n".join(current_lines).strip()
            _handle_request(req_id, prompt)
            current_lines = []
            current_req = ""
            continue

        if _looks_like_exact_turn_prompt(provider, line, current_lines, current_req):
            prompt = "\n".join(current_lines).strip()
            _handle_request(current_req, prompt)
            current_lines = []
            current_req = ""

    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
