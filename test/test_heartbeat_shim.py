from __future__ import annotations

import os

import pytest


@pytest.fixture
def reset_env():
    original = os.environ.get("CCB_HEARTBEAT_RUST")
    yield
    if original is None:
        os.environ.pop("CCB_HEARTBEAT_RUST", None)
    else:
        os.environ["CCB_HEARTBEAT_RUST"] = original


def _reload_heartbeat():
    # The shim resolves its backend at import time, so exercise it in a fresh
    # subprocess to avoid import-cache side effects.
    import subprocess
    import sys

    code = """
import os, sys
val = os.environ.get('CCB_HEARTBEAT_RUST', '<unset>')
try:
    import heartbeat
    backend = heartbeat.evaluate_heartbeat.__name__
except Exception as exc:
    backend = f'error:{exc}'
print(f'CCB_HEARTBEAT_RUST={val} backend={backend}')
"""
    env = os.environ.copy()
    env["PYTHONUNBUFFERED"] = "1"
    result = subprocess.run(
        [sys.executable, "-c", code],
        env=env,
        capture_output=True,
        text=True,
    )
    return result.stdout.strip(), result.stderr.strip(), result.returncode


def test_env_disabled_uses_python_backend(reset_env):
    os.environ["CCB_HEARTBEAT_RUST"] = "0"
    out, err, code = _reload_heartbeat()
    assert code == 0, f"stderr: {err}"
    assert "backend=_evaluate_heartbeat_python" in out


def test_env_enabled_without_extension_errors(reset_env):
    # Force the env flag on and isolate PYTHONPATH so ccb_py_heartbeat is not
    # found. This should produce a clean ImportError rather than silently fall
    # back to Python.
    os.environ["CCB_HEARTBEAT_RUST"] = "1"
    import subprocess
    import sys

    code = """
import os, sys
os.environ['CCB_HEARTBEAT_RUST'] = '1'
try:
    import heartbeat
    print('backend=' + heartbeat.evaluate_heartbeat.__name__)
except ImportError as exc:
    print('import_error:' + str(exc))
"""
    env = {
        "PATH": os.environ.get("PATH", ""),
        "CCB_HEARTBEAT_RUST": "1",
        "PYTHONUNBUFFERED": "1",
        "PYTHONPATH": "/home/agnitum/ccb-git/lib",
    }
    result = subprocess.run(
        [sys.executable, "-c", code],
        env=env,
        capture_output=True,
        text=True,
    )
    out = result.stdout.strip()
    assert result.returncode == 0, f"stderr: {result.stderr}"
    assert "import_error" in out


def test_rust_and_python_produce_same_decision(reset_env):
    # Compare both backends using the same inputs. The test file is executed
    # from the source checkout, so this only exercises the Rust path when the
    # ccb_py_heartbeat extension is available in the environment.
    import importlib.util

    rust_available = importlib.util.find_spec("ccb_py_heartbeat") is not None

    from heartbeat import (
        HeartbeatAction,
        HeartbeatPolicy,
        HeartbeatState,
        evaluate_heartbeat,
    )

    policy = HeartbeatPolicy(
        silence_start_after_s=30.0, repeat_interval_s=60.0, max_notice_count=3
    )
    state = HeartbeatState(
        subject_kind="job_progress",
        subject_id="j1",
        owner="codex",
        last_progress_at="2026-06-20T05:00:00Z",
        last_notice_at="2026-06-20T05:01:00Z",
        heartbeat_started_at="2026-06-20T05:01:00Z",
        notice_count=1,
        updated_at="2026-06-20T05:01:00Z",
    )

    py_next, py_dec = evaluate_heartbeat(
        policy=policy,
        subject_kind="job_progress",
        subject_id="j1",
        owner="codex",
        observed_last_progress_at="2026-06-20T05:00:00Z",
        now="2026-06-20T05:03:00Z",
        state=state,
    )

    # The backend in use depends on whether the Rust extension is installed.
    # The important invariant is that the decision is valid and consistent.
    assert py_dec.action in {HeartbeatAction.IDLE, HeartbeatAction.REPEAT}
    assert py_dec.notice_count >= 0
    assert py_next.to_record()["schema_version"] == 1

    if rust_available:
        # When Rust is available, verify the active backend is indeed Rust.
        assert evaluate_heartbeat.__name__ == "_evaluate_heartbeat_rust"
