"""Codex bridge runtime state (Q3 Stage 1a — Init Gate integrated)."""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING

from provider_backends.codex.runtime_artifacts import ensure_runtime_artifact_layout

from .binding import CodexBindingTracker
from .session import TerminalCodexSession

if TYPE_CHECKING:
    from provider_core.init_gate import InitGate


@dataclass(frozen=True)
class BridgePaths:
    """Paths for bridge runtime."""

    runtime_dir: Path
    input_fifo: Path
    completion_dir: Path
    history_dir: Path
    history_file: Path
    bridge_log: Path


@dataclass
class BridgeRuntimeState:
    """Mutable runtime state for the bridge process.

    Contains initialization gate and FIFO holder for lifecycle management.
    """

    paths: BridgePaths
    binding_tracker: CodexBindingTracker
    codex_session: TerminalCodexSession
    init_gate: InitGate = field(default=None)  # type: ignore[assignment]
    fifo_holder_fd: int | None = None


def build_bridge_runtime_state(
    runtime_dir: Path,
    *,
    pane_id: str,
    tmux_backend,
) -> BridgeRuntimeState:
    """Build runtime state with Init Gate constructed.

    Args:
        runtime_dir: Directory for bridge runtime files.
        pane_id: Tmux pane identifier.
        tmux_backend: Backend for tmux operations.

    Returns:
        BridgeRuntimeState with InitGate constructed.
    """
    from provider_core.init_gate import InitGate, load_init_gate_env
    from provider_backends.codex.bridge_runtime.init_probe import CodexInitProbe

    artifacts = ensure_runtime_artifact_layout(runtime_dir)
    paths = BridgePaths(
        runtime_dir=artifacts.runtime_dir,
        input_fifo=artifacts.input_fifo,
        completion_dir=artifacts.completion_dir,
        history_dir=artifacts.history_dir,
        history_file=artifacts.history_file,
        bridge_log=artifacts.bridge_log,
    )

    # Adapter: TmuxBackend._tmux_run returns subprocess.CompletedProcess,
    # but CodexInitProbe expects tmux_run_fn(args) -> str. Extract stdout
    # (decode bytes if needed) and return "" on any failure (conservative;
    # probe.detect already treats "" as not-ready).
    def _tmux_run_str(args: list[str]) -> str:
        result = tmux_backend._tmux_run(args, capture=True, timeout=2.0, check=False)
        if result is None:
            return ""
        out = getattr(result, "stdout", "")
        if isinstance(out, bytes):
            try:
                return out.decode("utf-8", errors="replace")
            except Exception:
                return ""
        return out if isinstance(out, str) else ""

    # Construct Init Gate with Codex probe
    probe = CodexInitProbe(
        pane_id=pane_id,
        tmux_run_fn=_tmux_run_str,
    )

    def capture_fn() -> str:
        """Capture visible pane for InitGate diagnostics."""
        try:
            return probe.capture_visible_for_diagnostics()
        except Exception:
            return ""

    gate_kwargs = load_init_gate_env("codex")
    init_gate = InitGate(
        probe=probe,
        provider="codex",
        runtime_dir=runtime_dir,
        capture_fn=capture_fn,
        log_fn=lambda msg: print(msg, flush=True),
        **gate_kwargs,
    )

    return BridgeRuntimeState(
        paths=paths,
        binding_tracker=CodexBindingTracker(runtime_dir),
        codex_session=TerminalCodexSession(pane_id),
        init_gate=init_gate,
    )


__all__ = [
    'BridgePaths',
    'BridgeRuntimeState',
    'build_bridge_runtime_state',
]
