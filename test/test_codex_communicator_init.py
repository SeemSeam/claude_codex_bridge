"""Tests for Codex bridge Init Gate integration (Q3-S1a.3)."""
from __future__ import annotations

import os
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from provider_backends.codex.bridge_runtime.runtime_state import (
    BridgeRuntimeState,
    build_bridge_runtime_state,
)


class TestDualBridgeInit:
    """Test DualBridge __init__ and Init Gate integration."""

    def test_dualbridge_init_opens_fifo_holder(self, tmp_path: Path, monkeypatch):
        """FIFO holder fd is opened in __init__."""
        import tempfile

        # Need to mock before importing DualBridge
        monkeypatch.setenv('CODEX_TMUX_SESSION', '%3')

        with tempfile.NamedTemporaryFile(mode='w', delete=False) as fifo:
            fifo_path = fifo.name

        try:
            from provider_backends.codex.bridge_runtime.service import DualBridge

            # Mock os.open to capture the call
            open_calls = []
            original_os_open = os.open

            def mock_os_open(path, flags):
                open_calls.append((path, flags))
                # Return a real fd we can close later
                return original_os_open('/dev/null', os.O_RDONLY)

            monkeypatch.setattr(os, 'open', mock_os_open)

            # Create runtime dir with pre-existing fifo
            runtime_dir = tmp_path / 'runtime'
            runtime_dir.mkdir()

            bridge = DualBridge(runtime_dir)

            # Verify os.open was called with correct args
            assert len(open_calls) >= 1
            fifo_calls = [c for c in open_calls if 'input.fifo' in str(c[0])]
            assert len(fifo_calls) >= 1

            # Check flags include NONBLOCK and access mode is RDONLY.
            # Note: os.O_RDONLY == 0 on POSIX, so it cannot be detected via
            # bitmask AND. Use the access-mode bits (lower 2 bits) instead.
            path, flags = fifo_calls[0]
            assert (flags & os.O_ACCMODE) == os.O_RDONLY
            assert flags & os.O_NONBLOCK

        finally:
            os.unlink(fifo_path)

    def test_dualbridge_run_blocks_on_init_gate_false(self, tmp_path: Path, monkeypatch):
        """run() returns 3 when InitGate returns False."""
        monkeypatch.setenv('CODEX_TMUX_SESSION', '%3')

        from provider_backends.codex.bridge_runtime.service import DualBridge

        # Mock build_bridge_runtime_state to inject a fake init_gate
        fake_gate = MagicMock()
        fake_gate.wait_until_ready.return_value = False
        fake_gate.last_reason = "deadline_exceeded"

        with patch(
            'provider_backends.codex.bridge_runtime.service.build_bridge_runtime_state'
        ) as mock_build:
            mock_state = MagicMock()
            mock_state.init_gate = fake_gate
            mock_state.fifo_holder_fd = None
            mock_state.paths.input_fifo = tmp_path / 'input.fifo'
            mock_build.return_value = mock_state

            bridge = DualBridge(tmp_path)
            exit_code = bridge.run()

            assert exit_code == 3
            fake_gate.wait_until_ready.assert_called_once()

    def test_dualbridge_run_success_enters_main_loop(self, tmp_path: Path, monkeypatch):
        """run() enters main loop when InitGate passes."""
        monkeypatch.setenv('CODEX_TMUX_SESSION', '%3')

        from provider_backends.codex.bridge_runtime.service import DualBridge

        # Fake gate that passes immediately
        fake_gate = MagicMock()
        fake_gate.wait_until_ready.return_value = True

        # Fake tracker
        fake_tracker = MagicMock()
        fake_tracker.start = MagicMock()

        with patch(
            'provider_backends.codex.bridge_runtime.service.build_bridge_runtime_state'
        ) as mock_build:
            mock_state = MagicMock()
            mock_state.init_gate = fake_gate
            mock_state.fifo_holder_fd = None
            mock_state.paths.input_fifo = tmp_path / 'fifo'
            mock_state.binding_tracker = fake_tracker
            mock_build.return_value = mock_state

            # Mock _read_request to exit loop immediately. _running is an
            # instance attribute, so we cannot patch it at class level — set
            # it on the bridge instance after construction.
            with patch.object(DualBridge, '_read_request', return_value=None):
                bridge = DualBridge(tmp_path)
                bridge._running = False  # makes main loop exit immediately
                exit_code = bridge.run()

                assert exit_code == 0
                fake_tracker.start.assert_called_once()
