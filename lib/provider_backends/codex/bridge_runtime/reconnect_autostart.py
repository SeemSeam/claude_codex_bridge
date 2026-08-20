from __future__ import annotations

import os
from pathlib import Path
import subprocess
import time
from typing import Callable, Mapping

from .env import read_session_data


class CodexReconnectAutostart:
    """Arm the bundled reconnect watcher once a managed Codex thread is bound."""

    def __init__(
        self,
        runtime_dir: Path,
        *,
        environment: Mapping[str, str] | None = None,
        runner: Callable[..., subprocess.CompletedProcess[str]] = subprocess.run,
        monotonic: Callable[[], float] = time.monotonic,
        log: Callable[[str], None] | None = None,
        launcher: Path | None = None,
        retry_base_seconds: float = 5.0,
        retry_max_seconds: float = 60.0,
        command_timeout_seconds: float = 10.0,
    ) -> None:
        self.runtime_dir = Path(runtime_dir)
        self.environment = dict(os.environ if environment is None else environment)
        self.runner = runner
        self.monotonic = monotonic
        self.log = log or (lambda _message: None)
        self.launcher = Path(launcher or _bundled_launcher())
        self.retry_base_seconds = max(0.0, retry_base_seconds)
        self.retry_max_seconds = max(self.retry_base_seconds, retry_max_seconds)
        self.command_timeout_seconds = max(0.1, command_timeout_seconds)
        self._armed_threads: set[str] = set()
        self._failure_counts: dict[str, int] = {}
        self._next_attempt_at: dict[str, float] = {}

    def maybe_arm(self) -> bool:
        binding = self._current_binding()
        if binding is None:
            return False
        thread_id, environment = binding
        if thread_id in self._armed_threads:
            return False
        now = self.monotonic()
        if now < self._next_attempt_at.get(thread_id, 0.0):
            return False
        result = self._run("on", environment)
        if result is None or result.returncode != 0:
            self._record_failure(thread_id, result, now=now)
            return False
        self._armed_threads.add(thread_id)
        self._failure_counts.pop(thread_id, None)
        self._next_attempt_at.pop(thread_id, None)
        self.log(
            f"codex-reconnect automatic activation requested for thread {thread_id}"
        )
        return True

    def stop(self) -> None:
        binding = self._current_binding()
        if binding is None:
            return
        thread_id, environment = binding
        if thread_id not in self._armed_threads:
            return
        result = self._run("off", environment)
        if result is None or result.returncode != 0:
            detail = _result_error(result)
            self.log(
                f"codex-reconnect automatic shutdown failed for thread {thread_id}: {detail}"
            )
            return
        self.log(f"codex-reconnect automatic shutdown requested for thread {thread_id}")

    def _current_binding(self) -> tuple[str, dict[str, str]] | None:
        raw_session_file = str(self.environment.get("CCB_SESSION_FILE") or "").strip()
        if not raw_session_file:
            return None
        session_file = Path(raw_session_file).expanduser()
        if not session_file.is_absolute() or not session_file.is_file():
            return None
        data = read_session_data(session_file)
        if data.get("active") is not True:
            return None
        thread_id = str(data.get("codex_session_id") or "").strip()
        if not thread_id:
            return None
        environment = dict(self.environment)
        environment["CCB_SESSION_FILE"] = str(session_file)
        environment["CODEX_RUNTIME_DIR"] = str(self.runtime_dir)
        environment["CODEX_THREAD_ID"] = thread_id
        codex_home = str(
            data.get("codex_home") or environment.get("CODEX_HOME") or ""
        ).strip()
        if codex_home:
            environment["CODEX_HOME"] = codex_home
        pane_id = str(
            data.get("pane_id") or environment.get("CODEX_TMUX_SESSION") or ""
        ).strip()
        if pane_id:
            environment["CODEX_TMUX_SESSION"] = pane_id
        return thread_id, environment

    def _run(
        self,
        action: str,
        environment: Mapping[str, str],
    ) -> subprocess.CompletedProcess[str] | None:
        command = [
            str(self.launcher),
            action,
            "--state-dir",
            str(self.runtime_dir / "reconnect"),
        ]
        try:
            return self.runner(
                command,
                capture_output=True,
                text=True,
                timeout=self.command_timeout_seconds,
                check=False,
                env=dict(environment),
            )
        except (OSError, subprocess.SubprocessError) as exc:
            self.log(f"codex-reconnect {action} command failed: {exc}")
            return None

    def _record_failure(
        self,
        thread_id: str,
        result: subprocess.CompletedProcess[str] | None,
        *,
        now: float,
    ) -> None:
        failures = self._failure_counts.get(thread_id, 0) + 1
        self._failure_counts[thread_id] = failures
        delay = min(
            self.retry_max_seconds,
            self.retry_base_seconds * (2 ** min(failures - 1, 8)),
        )
        self._next_attempt_at[thread_id] = now + delay
        self.log(
            "codex-reconnect automatic activation failed for thread "
            f"{thread_id}; retrying in {delay:g}s: {_result_error(result)}"
        )


def _bundled_launcher() -> Path:
    return Path(__file__).resolve().parents[4] / "bin" / "codex-reconnect"


def _result_error(result: subprocess.CompletedProcess[str] | None) -> str:
    if result is None:
        return "command did not start"
    detail = str(result.stderr or result.stdout or "").strip().replace("\n", " ")
    if len(detail) > 500:
        detail = detail[:497] + "..."
    return detail or f"exit code {result.returncode}"


__all__ = ["CodexReconnectAutostart"]
