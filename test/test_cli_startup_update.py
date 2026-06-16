from __future__ import annotations

import json
from io import StringIO
from pathlib import Path
import time

from cli.management_runtime import startup_update as startup_update_runtime
from cli.management_runtime import startup_update_flow
from cli.management_runtime import startup_update_refresh


class _TtyStringIO(StringIO):
    def isatty(self) -> bool:  # pragma: no cover - trivial
        return True


class _TtyInput(StringIO):
    def isatty(self) -> bool:  # pragma: no cover - trivial
        return True


def _release_install(tmp_path: Path, *, version: str = "6.0.10") -> Path:
    (tmp_path / "install.sh").write_text("#!/usr/bin/env bash\n", encoding="utf-8")
    (tmp_path / "ccb.py").write_text(
        f'#!/usr/bin/env python3\nVERSION = "{version}"\nGIT_COMMIT = "abc1234"\nGIT_DATE = "2026-04-24"\n',
        encoding="utf-8",
    )
    (tmp_path / "BUILD_INFO.json").write_text(
        json.dumps(
            {
                "version": version,
                "commit": "abc1234",
                "date": "2026-04-24",
                "build_time": "2026-04-24T07:44:20Z",
                "platform": "linux",
                "arch": "x86_64",
                "channel": "stable",
                "source_kind": "release",
                "install_mode": "release",
            }
        ),
        encoding="utf-8",
    )
    return tmp_path


def _fresh_update_state(*, current: str = "6.0.10", latest: str = "6.0.11") -> dict[str, object]:
    now = time.time()
    return {
        "schema_version": 1,
        "checked_at": "2026-04-24T07:44:20Z",
        "checked_at_epoch": now,
        "current_version": current,
        "latest_version": latest,
        "update_available": True,
        "muted_version": None,
        "deferred_version": None,
        "deferred_until_epoch": None,
    }


def _source_install(tmp_path: Path, *, version: str = "6.0.10") -> Path:
    (tmp_path / "install.sh").write_text("#!/usr/bin/env bash\n", encoding="utf-8")
    (tmp_path / "ccb.py").write_text(
        f'#!/usr/bin/env python3\nVERSION = "{version}"\nGIT_COMMIT = "abc1234"\nGIT_DATE = "2026-04-24"\n',
        encoding="utf-8",
    )
    (tmp_path / "BUILD_INFO.json").write_text(
        json.dumps(
            {
                "version": version,
                "commit": "abc1234",
                "date": "2026-04-24",
                "build_time": "2026-04-24T07:44:20Z",
                "platform": "linux",
                "arch": "x86_64",
                "channel": "dev",
                "source_kind": "source",
                "install_mode": "source",
            }
        ),
        encoding="utf-8",
    )
    return tmp_path


def test_maybe_handle_startup_release_update_schedules_background_refresh_when_cache_missing(tmp_path: Path) -> None:
    install_dir = _release_install(tmp_path)
    calls: list[tuple[Path, Path]] = []

    code = startup_update_runtime.maybe_handle_startup_release_update(
        [],
        script_root=install_dir,
        cwd=install_dir,
        stdout=_TtyStringIO(),
        stderr=StringIO(),
        stdin=_TtyInput(""),
        schedule_refresh_fn=lambda *, script_root, install_dir: calls.append((script_root, install_dir)) or True,
    )

    assert code is None
    assert calls == [(install_dir, install_dir)]


def test_maybe_handle_startup_release_update_supports_macos_release(tmp_path: Path, monkeypatch) -> None:
    install_dir = _release_install(tmp_path)
    calls: list[tuple[Path, Path]] = []
    monkeypatch.setattr(startup_update_flow.platform, "system", lambda: "Darwin")

    code = startup_update_runtime.maybe_handle_startup_release_update(
        [],
        script_root=install_dir,
        cwd=install_dir,
        stdout=_TtyStringIO(),
        stderr=StringIO(),
        stdin=_TtyInput(""),
        schedule_refresh_fn=lambda *, script_root, install_dir: calls.append((script_root, install_dir)) or True,
    )

    assert code is None
    assert calls == [(install_dir, install_dir)]


def test_maybe_handle_startup_release_update_defers_prompt_on_default_continue(tmp_path: Path) -> None:
    install_dir = _release_install(tmp_path)
    state = _fresh_update_state()
    startup_update_runtime.write_update_check_state(install_dir, state)
    stdout = _TtyStringIO()

    code = startup_update_runtime.maybe_handle_startup_release_update(
        [],
        script_root=install_dir,
        cwd=install_dir,
        stdout=stdout,
        stderr=StringIO(),
        stdin=_TtyInput("\n"),
        schedule_refresh_fn=lambda **_: (_ for _ in ()).throw(AssertionError("refresh should not run")),
    )

    saved = startup_update_runtime.load_update_check_state(install_dir)
    assert code is None
    assert saved is not None
    assert saved["deferred_version"] == "6.0.11"
    assert float(saved["deferred_until_epoch"] or 0.0) > float(saved["checked_at_epoch"] or 0.0)
    assert "Release update available: v6.0.11" in stdout.getvalue()


def test_maybe_handle_startup_release_update_silences_prompt_for_current_version(tmp_path: Path) -> None:
    install_dir = _release_install(tmp_path)
    startup_update_runtime.write_update_check_state(install_dir, _fresh_update_state())

    code = startup_update_runtime.maybe_handle_startup_release_update(
        [],
        script_root=install_dir,
        cwd=install_dir,
        stdout=_TtyStringIO(),
        stderr=StringIO(),
        stdin=_TtyInput("s\n"),
        schedule_refresh_fn=lambda **_: (_ for _ in ()).throw(AssertionError("refresh should not run")),
    )

    saved = startup_update_runtime.load_update_check_state(install_dir)
    assert code is None
    assert saved is not None
    assert saved["muted_version"] == "6.0.11"
    assert saved["deferred_version"] is None


def test_maybe_handle_startup_release_update_updates_and_relaunches_on_yes(tmp_path: Path) -> None:
    install_dir = _release_install(tmp_path)
    startup_update_runtime.write_update_check_state(install_dir, _fresh_update_state())
    relaunched: dict[str, object] = {}

    def _relaunch(tokens, *, script_root: Path, cwd: Path, env: dict[str, str]) -> int:
        relaunched["tokens"] = list(tokens)
        relaunched["script_root"] = script_root
        relaunched["cwd"] = cwd
        relaunched["env"] = dict(env)
        return 17

    code = startup_update_runtime.maybe_handle_startup_release_update(
        ["--project", str(install_dir), "-s"],
        script_root=install_dir,
        cwd=install_dir,
        stdout=_TtyStringIO(),
        stderr=StringIO(),
        stdin=_TtyInput("y\n"),
        update_fn=lambda args, *, script_root: 0,
        relaunch_fn=_relaunch,
        schedule_refresh_fn=lambda **_: (_ for _ in ()).throw(AssertionError("refresh should not run")),
    )

    assert code == 17
    assert relaunched["tokens"] == ["--project", str(install_dir), "-s"]
    assert relaunched["script_root"] == install_dir
    assert relaunched["cwd"] == install_dir


def test_maybe_handle_startup_release_update_skips_non_start_commands(tmp_path: Path) -> None:
    install_dir = _release_install(tmp_path)

    code = startup_update_runtime.maybe_handle_startup_release_update(
        ["ping", "ccbd"],
        script_root=install_dir,
        cwd=install_dir,
        stdout=_TtyStringIO(),
        stderr=StringIO(),
        stdin=_TtyInput("y\n"),
        schedule_refresh_fn=lambda **_: (_ for _ in ()).throw(AssertionError("refresh should not run")),
    )

    assert code is None
    assert startup_update_runtime.load_update_check_state(install_dir) is None


def test_maybe_handle_startup_release_update_schedules_refresh_for_stale_cache_without_prompt(tmp_path: Path) -> None:
    install_dir = _release_install(tmp_path)
    stale = _fresh_update_state()
    stale["checked_at_epoch"] = 1.0
    startup_update_runtime.write_update_check_state(install_dir, stale)
    calls: list[bool] = []
    stdout = _TtyStringIO()

    code = startup_update_runtime.maybe_handle_startup_release_update(
        [],
        script_root=install_dir,
        cwd=install_dir,
        stdout=stdout,
        stderr=StringIO(),
        stdin=_TtyInput("y\n"),
        schedule_refresh_fn=lambda *, script_root, install_dir: calls.append(True) or True,
    )

    assert code is None
    assert calls == [True]
    assert "Release update available" not in stdout.getvalue()


def test_maybe_handle_startup_release_update_skips_source_install(tmp_path: Path) -> None:
    install_dir = _source_install(tmp_path)
    calls: list[bool] = []

    code = startup_update_runtime.maybe_handle_startup_release_update(
        [],
        script_root=install_dir,
        cwd=install_dir,
        stdout=_TtyStringIO(),
        stderr=StringIO(),
        stdin=_TtyInput("y\n"),
        schedule_refresh_fn=lambda **_: calls.append(True) or True,
    )

    assert code is None
    assert calls == []
    assert startup_update_runtime.load_update_check_state(install_dir) is None


def test_schedule_background_update_refresh_creates_lock_and_spawns_internal_command(monkeypatch, tmp_path: Path) -> None:
    install_dir = _release_install(tmp_path)
    captured: dict[str, object] = {}

    class _DummyProcess:
        def __init__(self):
            self.pid = 123

    def _fake_popen(command, **kwargs):
        captured["command"] = list(command)
        captured["kwargs"] = dict(kwargs)
        return _DummyProcess()

    monkeypatch.setattr(startup_update_refresh.subprocess, "Popen", _fake_popen)

    assert startup_update_runtime.schedule_background_update_refresh(script_root=install_dir, install_dir=install_dir) is True
    assert captured["command"] == [
        startup_update_refresh.sys.executable,
        str(install_dir / "ccb.py"),
        startup_update_runtime.BACKGROUND_REFRESH_COMMAND,
    ]
    assert startup_update_runtime.update_check_lock_path(install_dir).exists()


def test_background_update_refresh_command_updates_cache_and_releases_lock(monkeypatch, tmp_path: Path) -> None:
    install_dir = _release_install(tmp_path)
    lock_path = startup_update_runtime.update_check_lock_path(install_dir)
    lock_path.write_text("locked\n", encoding="utf-8")
    monkeypatch.setenv("CCB_UPDATE_REFRESH_LOCK", str(lock_path))
    monkeypatch.setattr(
        startup_update_refresh,
        "get_available_versions",
        lambda **_: ["6.0.9", "6.0.10", "6.0.11"],
    )

    code = startup_update_runtime.maybe_handle_background_update_refresh_command(
        [startup_update_runtime.BACKGROUND_REFRESH_COMMAND],
        script_root=install_dir,
    )

    saved = startup_update_runtime.load_update_check_state(install_dir)
    assert code == 0
    assert saved is not None
    assert saved["latest_version"] == "6.0.11"
    assert saved["update_available"] is True
    assert not lock_path.exists()
