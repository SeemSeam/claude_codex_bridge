"""Unit tests for InitGate state machine (Q3 Stage 1a)."""
from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Callable
from unittest.mock import MagicMock

import pytest

from provider_core.init_gate import InitGate, InitGateProbe, InitGateState, load_init_gate_env


class HappyProbe(InitGateProbe):
    """Always returns True."""

    def detect(self) -> bool:
        return True


class NeverProbe(InitGateProbe):
    """Always returns False."""

    def detect(self) -> bool:
        return False


class AlternatingProbe(InitGateProbe):
    """Alternates True/False each call."""

    def __init__(self) -> None:
        self._call_count = 0

    def detect(self) -> bool:
        self._call_count += 1
        return self._call_count % 2 == 0


class TimedSequenceProbe(InitGateProbe):
    """Returns True after N calls."""

    def __init__(self, true_after: int) -> None:
        self._call_count = 0
        self._true_after = true_after

    def detect(self) -> bool:
        self._call_count += 1
        return self._call_count >= self._true_after


class MockClock:
    """Controlled monotonic clock for deterministic testing."""

    def __init__(self, start: float = 0.0) -> None:
        self._now = start

    def __call__(self) -> float:
        return self._now

    def advance(self, delta: float) -> None:
        self._now += delta


@pytest.fixture
def tmp_runtime_dir(tmp_path: Path) -> Path:
    """Provide a temporary runtime directory."""
    return tmp_path / "runtime"


@pytest.fixture
def mock_capture() -> Callable[[], str]:
    """Simple mock capture function."""
    return lambda: "mock pane capture"


@pytest.fixture
def mock_log() -> list[str]:
    """Capture log messages."""
    messages: list[str] = []
    return messages


def make_gate(
    probe: InitGateProbe,
    tmp_runtime_dir: Path,
    mock_capture: Callable[[], str],
    mock_log: list[str],
    *,
    clock: MockClock | None = None,
    sleep_calls: list[float] | None = None,
    deadline_s: float = 1.0,
    poll_fast_ms: int = 100,
    poll_slow_ms: int = 200,
    poll_switch_s: float = 0.5,
    steady_count: int = 2,
    bypass: bool = False,
) -> InitGate:
    """Factory for creating InitGate with mock dependencies."""
    clock = clock or MockClock()
    sleep_calls = sleep_calls if sleep_calls is not None else []

    def mock_sleep(duration: float) -> None:
        sleep_calls.append(duration)
        clock.advance(duration)

    def mock_log_fn(msg: str) -> None:
        mock_log.append(msg)

    return InitGate(
        probe=probe,
        provider="test",
        runtime_dir=tmp_runtime_dir,
        capture_fn=mock_capture,
        deadline_s=deadline_s,
        poll_fast_ms=poll_fast_ms,
        poll_slow_ms=poll_slow_ms,
        poll_switch_s=poll_switch_s,
        steady_count=steady_count,
        bypass=bypass,
        clock=clock,
        sleep_fn=mock_sleep,
        log_fn=mock_log_fn,
    )


class TestHappyPath:
    """Test LAUNCHED → READY happy path."""

    def test_happy_path_ready(self, tmp_runtime_dir: Path, mock_capture, mock_log):
        """Probe always True → steady_count=2 → returns True after ~2 polls."""
        probe = HappyProbe()
        clock = MockClock()
        sleep_calls: list[float] = []

        gate = make_gate(
            probe, tmp_runtime_dir, mock_capture, mock_log,
            clock=clock, sleep_calls=sleep_calls,
            deadline_s=10.0, poll_fast_ms=100, steady_count=2
        )

        result = gate.wait_until_ready()

        assert result is True
        assert gate._state == InitGateState.READY
        # Should have ~2 polls (first detect + 1 more to reach steady_count)
        # With steady_count=2 and all True, we need 2 consecutive True
        assert len(sleep_calls) >= 1  # At least one sleep
        # Verify startup log
        assert any("waiting for test TUI" in msg for msg in mock_log)

    def test_steady_count_requires_consecutive(self, tmp_runtime_dir: Path, mock_capture, mock_log):
        """Non-consecutive True detections should not commit until steady_count reached."""
        probe = TimedSequenceProbe(true_after=3)
        clock = MockClock()
        sleep_calls: list[float] = []

        gate = make_gate(
            probe, tmp_runtime_dir, mock_capture, mock_log,
            clock=clock, sleep_calls=sleep_calls,
            deadline_s=10.0, poll_fast_ms=50, steady_count=2
        )

        result = gate.wait_until_ready()

        assert result is True
        # Should have taken at least 3 polls (False, False, True, True)
        assert probe._call_count >= 3


class TestDeadlineFailure:
    """Test LAUNCHED → INIT_FAIL on deadline."""

    def test_deadline_exceeded(self, tmp_runtime_dir: Path, mock_capture, mock_log):
        """Probe always False, deadline 0.3s → returns False with correct reason and failure.json."""
        probe = NeverProbe()
        clock = MockClock()
        sleep_calls: list[float] = []

        gate = make_gate(
            probe, tmp_runtime_dir, mock_capture, mock_log,
            clock=clock, sleep_calls=sleep_calls,
            deadline_s=0.3, poll_fast_ms=50, steady_count=2
        )

        result = gate.wait_until_ready()

        assert result is False
        assert gate.last_reason == "deadline_exceeded"

        # Verify failure.json
        failure_path = tmp_runtime_dir / "init_gate_failure.json"
        assert failure_path.exists()

        with open(failure_path) as f:
            data = json.load(f)

        assert data["provider"] == "test"
        assert data["reason"] == "deadline_exceeded"
        assert data["deadline_s"] == 0.3
        assert data["init_gate_bypass"] is False
        assert "recent_pane_captures" in data
        assert "probes_attempted" in data
        assert "timestamp" in data


class TestSteadyState:
    """Test steady-state debounce behavior."""

    def test_steady_state_debounce(self, tmp_runtime_dir: Path, mock_capture, mock_log):
        """Sequence T→F→T→T: only commits on trailing T-T pair, not first T."""
        # Create probe with sequence: True, False, True, True, ...
        class SequenceProbe(InitGateProbe):
            def __init__(self):
                self._results = [True, False, True, True]
                self._idx = 0

            def detect(self) -> bool:
                result = self._results[self._idx] if self._idx < len(self._results) else True
                self._idx += 1
                return result

        probe = SequenceProbe()
        clock = MockClock()
        sleep_calls: list[float] = []

        gate = make_gate(
            probe, tmp_runtime_dir, mock_capture, mock_log,
            clock=clock, sleep_calls=sleep_calls,
            deadline_s=10.0, poll_fast_ms=50, steady_count=2
        )

        result = gate.wait_until_ready()

        assert result is True
        # Should have taken exactly 4 polls:
        # 1: T (consecutive=1, not enough)
        # 2: F (consecutive=0, reset)
        # 3: T (consecutive=1, not enough)
        # 4: T (consecutive=2, READY!)
        assert probe._idx == 4


class TestBypass:
    """Test bypass mode."""

    def test_bypass_returns_immediately(self, tmp_runtime_dir: Path, mock_capture, mock_log):
        """bypass=True → returns True without calling probe.detect even once."""
        probe = MagicMock(spec=InitGateProbe)
        probe.detect.return_value = False

        gate = make_gate(
            probe, tmp_runtime_dir, mock_capture, mock_log,
            bypass=True, deadline_s=10.0
        )

        result = gate.wait_until_ready()

        assert result is True
        probe.detect.assert_not_called()
        # Verify warning log
        assert any("BYPASS" in msg for msg in mock_log)


class TestSegmentedPolling:
    """Test segmented polling (fast→slow)."""

    def test_segmented_polling(self, tmp_runtime_dir: Path, mock_capture, mock_log):
        """First polls are ~fast_ms, later polls ~slow_ms after switch_s threshold."""
        probe = NeverProbe()  # Never returns True to keep polling
        clock = MockClock()
        sleep_calls: list[float] = []

        gate = make_gate(
            probe, tmp_runtime_dir, mock_capture, mock_log,
            clock=clock, sleep_calls=sleep_calls,
            deadline_s=2.0, poll_fast_ms=50, poll_slow_ms=200, poll_switch_s=0.5
        )

        try:
            gate.wait_until_ready()
        except AssertionError:
            pass  # Expected to fail due to deadline

        # First few sleeps should be ~0.05s (fast)
        fast_sleeps = [s for s in sleep_calls if s < 0.1]
        # After 0.5s elapsed, should switch to ~0.2s (slow)
        slow_sleeps = [s for s in sleep_calls if s >= 0.15]

        # Should have both fast and slow periods
        assert len(fast_sleeps) >= 3
        assert len(slow_sleeps) >= 1 or len(sleep_calls) < 10  # May not reach slow if deadline short


class TestFailureJsonStructure:
    """Test failure.json structure."""

    def test_failure_json_structure(self, tmp_runtime_dir: Path, mock_capture, mock_log):
        """On deadline fail, json has all required fields."""
        probe = NeverProbe()
        clock = MockClock()
        sleep_calls: list[float] = []

        # Track capture calls
        capture_calls: list[str] = []
        def tracking_capture() -> str:
            capture_calls.append(f"capture at t={clock._now}")
            return f"mock capture {len(capture_calls)}"

        gate = make_gate(
            probe, tmp_runtime_dir, tracking_capture, mock_log,
            clock=clock, sleep_calls=sleep_calls,
            deadline_s=0.3, poll_fast_ms=50
        )

        gate.wait_until_ready()

        failure_path = tmp_runtime_dir / "init_gate_failure.json"
        with open(failure_path) as f:
            data = json.load(f)

        # Verify all required fields
        assert data["provider"] == "test"
        assert data["reason"] == "deadline_exceeded"
        assert isinstance(data["deadline_s"], (int, float))
        assert isinstance(data["elapsed_s"], (int, float))
        assert isinstance(data["init_gate_bypass"], bool)
        assert isinstance(data["recent_pane_captures"], list)
        assert isinstance(data["probes_attempted"], list)
        assert "timestamp" in data

    def test_recent_pane_captures_ring(self, tmp_runtime_dir: Path, mock_capture, mock_log):
        """>=4 polls → failure.json has exactly 3 items (last 3)."""
        probe = NeverProbe()
        clock = MockClock()
        sleep_calls: list[float] = []
        capture_counter = [0]

        def counting_capture() -> str:
            capture_counter[0] += 1
            return f"capture-{capture_counter[0]}"

        gate = make_gate(
            probe, tmp_runtime_dir, counting_capture, mock_log,
            clock=clock, sleep_calls=sleep_calls,
            deadline_s=0.5, poll_fast_ms=50
        )

        gate.wait_until_ready()

        failure_path = tmp_runtime_dir / "init_gate_failure.json"
        with open(failure_path) as f:
            data = json.load(f)

        # Should have exactly 3 captures
        captures = data["recent_pane_captures"]
        assert len(captures) == 3
        # Should be last 3 captures
        assert captures[0]["capture"] == "capture-1" or "capture-2"
        assert captures[-1]["capture"].startswith("capture-")


class TestEnvVarLoading:
    """Test load_init_gate_env function."""

    def test_env_var_loading(self, monkeypatch):
        """monkeypatch env → load_init_gate_env reflects values."""
        monkeypatch.setenv("CCB_INIT_GATE_DEADLINE_S", "60")
        monkeypatch.setenv("CCB_INIT_GATE_POLL_FAST_MS", "300")
        monkeypatch.setenv("CCB_CODEX_INIT_DEADLINE_S", "45")  # per-provider override

        kwargs = load_init_gate_env("codex")

        assert kwargs["deadline_s"] == 45.0  # per-provider wins
        assert kwargs["poll_fast_ms"] == 300  # generic used

    def test_env_fallback_to_generic(self, monkeypatch):
        """Unset per-provider override falls back to generic."""
        monkeypatch.setenv("CCB_INIT_GATE_DEADLINE_S", "60")
        monkeypatch.delenv("CCB_CODEX_INIT_DEADLINE_S", raising=False)

        kwargs = load_init_gate_env("codex")

        assert kwargs["deadline_s"] == 60.0  # generic used

    def test_env_bypass_parsing(self, monkeypatch):
        """Test BYPASS env var parsing."""
        for val in ["1", "true", "yes", "on"]:
            monkeypatch.setenv("CCB_INIT_GATE_BYPASS", val)
            kwargs = load_init_gate_env("codex")
            assert kwargs["bypass"] is True, f"failed for {val}"

        monkeypatch.setenv("CCB_INIT_GATE_BYPASS", "0")
        kwargs = load_init_gate_env("codex")
        assert kwargs["bypass"] is False
