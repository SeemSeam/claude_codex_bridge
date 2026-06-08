"""Init Gate state machine for provider bridge ready detection (Q3 Stage 1).

Provides a generic polling-based gate that blocks bridge startup until
the provider TUI is in a ready-to-receive state, with deadline-based
failure detection and diagnostic capture.
"""
from __future__ import annotations

import json
import os
import time
from collections import deque
from dataclasses import dataclass, field
from enum import Enum, auto
from pathlib import Path
from typing import Callable, Protocol


class InitGateState(Enum):
    """Init Gate lifecycle states."""

    LAUNCHED = auto()
    INITIALIZING = auto()
    READY = auto()
    INIT_FAIL = auto()


class InitGateProbe(Protocol):
    """Protocol for provider-specific ready detection."""

    def detect(self) -> bool:
        """Return True if the provider TUI is ready to receive input."""
        ...


@dataclass
class _ProbeAttempt:
    """Record of a single probe attempt for diagnostics."""

    t_offset_s: float
    detected: bool


@dataclass
class _CaptureEntry:
    """Pane capture entry with timestamp offset."""

    t_offset_s: float
    capture: str


@dataclass
class InitGate:
    """Generic Init Gate with segmented polling and steady-state debounce.

    Args:
        probe: Provider-specific probe implementing InitGateProbe.
        provider: Provider name (e.g., "codex", "gemini", "claude").
        runtime_dir: Directory for init_gate_failure.json output.
        capture_fn: Callable returning latest pane capture text.
        deadline_s: Total timeout for init gate (seconds).
        poll_fast_ms: Initial polling period in milliseconds (first switch_s seconds).
        poll_slow_ms: Slower polling period in milliseconds (after switch_s).
        poll_switch_s: Time threshold to switch from fast to slow polling.
        steady_count: Required consecutive positive probes before committing READY.
        bypass: If True, skip gate and return immediately (emergency override).
        clock: Monotonic time source (default: time.monotonic).
        sleep_fn: Sleep function (default: time.sleep).
        log_fn: Logging function (default: writes to stderr).
    """

    probe: InitGateProbe
    provider: str
    runtime_dir: Path
    capture_fn: Callable[[], str]
    deadline_s: float
    poll_fast_ms: int
    poll_slow_ms: int
    poll_switch_s: float
    steady_count: int
    bypass: bool
    clock: Callable[[], float] = time.monotonic
    sleep_fn: Callable[[float], None] = time.sleep
    log_fn: Callable[[str], None] = field(
        default_factory=lambda: lambda msg: print(msg, file=os.sys.stderr)
    )

    # State (initialized post-construction)
    _state: InitGateState = field(init=False, default=InitGateState.LAUNCHED)
    _last_reason: str = field(init=False, default="")
    _start_time: float = field(init=False, default=0.0)
    _probes_attempted: list[_ProbeAttempt] = field(init=False, default_factory=list)
    _recent_captures: deque[_CaptureEntry] = field(
        init=False, default_factory=lambda: deque(maxlen=3)
    )

    def __post_init__(self) -> None:
        """Initialize mutable fields."""
        object.__setattr__(self, "_state", InitGateState.LAUNCHED)
        object.__setattr__(self, "_last_reason", "")
        object.__setattr__(self, "_start_time", 0.0)
        object.__setattr__(self, "_probes_attempted", [])
        object.__setattr__(self, "_recent_captures", deque(maxlen=3))

    @property
    def last_reason(self) -> str:
        """Return the failure reason after INIT_FAIL."""
        return self._last_reason

    def wait_until_ready(self) -> bool:
        """Run the init gate until READY or INIT_FAIL.

        Returns:
            True on READY, False on INIT_FAIL.
        """
        self._state = InitGateState.INITIALIZING
        self._start_time = self.clock()

        if self.bypass:
            self._log_warn("[InitGate] BYPASS enabled — skipping ready detection")
            self._state = InitGateState.READY
            return True

        # Emit startup log with segmented polling description
        self.log_fn(
            f"[InitGate] waiting for {self.provider} TUI "
            f"(deadline: {self.deadline_s}s, "
            f"poll: {self.poll_fast_ms}ms→{self.poll_slow_ms}ms@{self.poll_switch_s}s) ..."
        )

        consecutive_positives = 0
        poll_count = 0

        while True:
            now = self.clock()
            elapsed = now - self._start_time

            # Check deadline
            if elapsed >= self.deadline_s:
                self._record_failure(
                    reason="deadline_exceeded",
                    elapsed_s=elapsed,
                    probes=self._probes_attempted,
                )
                self._state = InitGateState.INIT_FAIL
                self._last_reason = "deadline_exceeded"
                return False

            # Determine poll period
            period_s = (
                self.poll_fast_ms / 1000.0
                if elapsed < self.poll_switch_s
                else self.poll_slow_ms / 1000.0
            )

            # Probe
            try:
                detected = self.probe.detect()
            except Exception:
                detected = False

            poll_count += 1
            self._probes_attempted.append(
                _ProbeAttempt(t_offset_s=round(elapsed, 3), detected=detected)
            )

            # Capture pane for diagnostics (last 3 only)
            try:
                capture = self.capture_fn()
                self._recent_captures.append(
                    _CaptureEntry(t_offset_s=round(elapsed, 3), capture=capture)
                )
            except Exception:
                pass

            # Steady-state check
            if detected:
                consecutive_positives += 1
                if consecutive_positives >= self.steady_count:
                    self._state = InitGateState.READY
                    return True
            else:
                consecutive_positives = 0

            # Sleep until next poll
            self.sleep_fn(period_s)

    def _record_failure(
        self,
        *,
        reason: str,
        elapsed_s: float,
        probes: list[_ProbeAttempt],
    ) -> None:
        """Write init_gate_failure.json with diagnostic information."""
        failure_path = self.runtime_dir / "init_gate_failure.json"
        self.runtime_dir.mkdir(parents=True, exist_ok=True)

        failure_data = {
            "provider": self.provider,
            "reason": reason,
            "deadline_s": self.deadline_s,
            "elapsed_s": round(elapsed_s, 3),
            "init_gate_bypass": self.bypass,
            "recent_pane_captures": [
                {"t_offset_s": e.t_offset_s, "capture": e.capture}
                for e in list(self._recent_captures)
            ],
            "probes_attempted": [
                {"t_offset_s": p.t_offset_s, "detected": p.detected}
                for p in probes
            ],
            "timestamp": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        }

        try:
            with open(failure_path, "w") as f:
                json.dump(failure_data, f, indent=2)
                f.flush()
                os.fsync(f.fileno())
        except OSError as e:
            self._log_warn(f"[InitGate] failed to write failure.json: {e}")

    def _log_warn(self, msg: str) -> None:
        """Log a warning message."""
        self.log_fn(msg)


def load_init_gate_env(provider: str) -> dict:
    """Load InitGate constructor kwargs from environment variables.

    Generic vars: CCB_INIT_GATE_DEADLINE_S, CCB_INIT_GATE_POLL_FAST_MS, etc.
    Per-provider vars: CCB_<PROVIDER>_INIT_DEADLINE_S, etc.

    Per-provider overrides generic.

    Args:
        provider: Provider name (e.g., "codex").

    Returns:
        Dict of kwargs suitable for InitGate construction.
    """
    provider_upper = provider.upper()

    def get_env(key: str, default: str) -> str:
        """Check per-provider first, then generic."""
        provider_key = f"CCB_{provider_upper}_INIT_{key.upper()}"
        generic_key = f"CCB_INIT_GATE_{key.upper()}"
        return os.environ.get(provider_key, os.environ.get(generic_key, default))

    return {
        "deadline_s": float(get_env("DEADLINE_S", "30")),
        "poll_fast_ms": int(get_env("POLL_FAST_MS", "200")),
        "poll_slow_ms": int(get_env("POLL_SLOW_MS", "500")),
        "poll_switch_s": float(get_env("POLL_SWITCH_S", "5")),
        "steady_count": int(get_env("STEADY_COUNT", "2")),
        "bypass": get_env("BYPASS", "0").strip().lower() in ("1", "true", "yes", "on"),
    }
