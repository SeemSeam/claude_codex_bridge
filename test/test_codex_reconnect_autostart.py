from __future__ import annotations

import json
from pathlib import Path
import subprocess

from provider_backends.codex.bridge_runtime.reconnect_autostart import (
    CodexReconnectAutostart,
    _bundled_launcher,
)


def _write_session(path: Path, *, thread_id: str | None) -> None:
    payload = {
        "active": True,
        "pane_id": "%7",
        "codex_home": str(path.parent / "codex-home"),
    }
    if thread_id is not None:
        payload["codex_session_id"] = thread_id
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload), encoding="utf-8")


def test_autostart_waits_for_binding_then_arms_once_and_stops(tmp_path: Path) -> None:
    session_file = tmp_path / "session.json"
    _write_session(session_file, thread_id=None)
    calls: list[tuple[list[str], dict[str, object]]] = []

    def runner(
        command: list[str], **kwargs: object
    ) -> subprocess.CompletedProcess[str]:
        calls.append((command, kwargs))
        return subprocess.CompletedProcess(
            command,
            0,
            stdout='{"reconnect":"on","status":"arming"}\n',
            stderr="",
        )

    runtime_dir = tmp_path / "runtime"
    launcher = tmp_path / "ccb" / "bin" / "codex-reconnect"
    autostart = CodexReconnectAutostart(
        runtime_dir,
        environment={
            "CCB_SESSION_FILE": str(session_file),
            "PATH": "/usr/bin",
        },
        runner=runner,
        launcher=launcher,
    )

    assert autostart.maybe_arm() is False
    assert calls == []

    _write_session(session_file, thread_id="thread-1")
    assert autostart.maybe_arm() is True
    assert autostart.maybe_arm() is False
    assert len(calls) == 1
    command, kwargs = calls[0]
    assert command == [
        str(launcher),
        "on",
        "--state-dir",
        str(runtime_dir / "reconnect"),
    ]
    assert kwargs["check"] is False
    assert kwargs["timeout"] == 10.0
    environment = kwargs["env"]
    assert isinstance(environment, dict)
    assert environment["CODEX_THREAD_ID"] == "thread-1"
    assert environment["CODEX_RUNTIME_DIR"] == str(runtime_dir)
    assert environment["CCB_SESSION_FILE"] == str(session_file)
    assert environment["CODEX_TMUX_SESSION"] == "%7"

    autostart.stop()
    assert [call[0][1] for call in calls] == ["on", "off"]


def test_autostart_retries_failures_with_bounded_backoff(tmp_path: Path) -> None:
    session_file = tmp_path / "session.json"
    _write_session(session_file, thread_id="thread-retry")
    now = [100.0]
    calls: list[list[str]] = []
    logs: list[str] = []

    def runner(
        command: list[str], **_kwargs: object
    ) -> subprocess.CompletedProcess[str]:
        calls.append(command)
        return subprocess.CompletedProcess(
            command,
            3 if len(calls) == 1 else 0,
            stdout="",
            stderr="not ready" if len(calls) == 1 else "",
        )

    autostart = CodexReconnectAutostart(
        tmp_path / "runtime",
        environment={"CCB_SESSION_FILE": str(session_file)},
        runner=runner,
        monotonic=lambda: now[0],
        log=logs.append,
        launcher=tmp_path / "codex-reconnect",
        retry_base_seconds=5.0,
        retry_max_seconds=20.0,
    )

    assert autostart.maybe_arm() is False
    assert autostart.maybe_arm() is False
    assert len(calls) == 1
    assert "retrying in 5s" in logs[-1]

    now[0] += 5.0
    assert autostart.maybe_arm() is True
    assert autostart.maybe_arm() is False
    assert len(calls) == 2


def test_autostart_arms_a_new_bound_thread_without_rearming_old_one(
    tmp_path: Path,
) -> None:
    session_file = tmp_path / "session.json"
    _write_session(session_file, thread_id="thread-1")
    observed_threads: list[str] = []

    def runner(
        command: list[str], **kwargs: object
    ) -> subprocess.CompletedProcess[str]:
        environment = kwargs["env"]
        assert isinstance(environment, dict)
        if command[1] == "on":
            observed_threads.append(str(environment["CODEX_THREAD_ID"]))
        return subprocess.CompletedProcess(command, 0, stdout="", stderr="")

    autostart = CodexReconnectAutostart(
        tmp_path / "runtime",
        environment={"CCB_SESSION_FILE": str(session_file)},
        runner=runner,
        launcher=tmp_path / "codex-reconnect",
    )

    assert autostart.maybe_arm() is True
    assert autostart.maybe_arm() is False
    _write_session(session_file, thread_id="thread-2")
    assert autostart.maybe_arm() is True
    assert autostart.maybe_arm() is False
    assert observed_threads == ["thread-1", "thread-2"]


def test_default_launcher_is_the_python_selecting_wrapper() -> None:
    launcher = _bundled_launcher()
    assert launcher.name == "codex-reconnect"
    assert launcher.suffix == ""
