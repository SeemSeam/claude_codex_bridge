from __future__ import annotations

import os
import time
from pathlib import Path
from typing import Any

from ui_text import t
from provider_core.runtime_specs import provider_marker_prefix


def _required_session_info(comm):
    session_info = comm._load_session_info()
    if session_info:
        return session_info
    raise RuntimeError("❌ No active OpenCode session found. Add opencode to ccb.config and run `ccb` first")


def _log_reader(comm, *, log_reader_cls):
    return log_reader_cls(
        work_dir=Path(comm.session_info.get("work_dir") or Path.cwd()),
        project_id="global",
        session_id_filter=(str(comm.session_info.get("opencode_session_id") or "").strip() or None),
    )


def _publish_runtime_registry(comm, *, publish_registry_fn) -> None:
    publish_registry_fn(
        ccb_session_id=comm.ccb_session_id,
        session_info=comm.session_info,
        terminal=comm.terminal,
        pane_id=comm.pane_id or None,
        project_session_file=comm.project_session_file,
    )


def initialize_state(
    comm,
    *,
    get_backend_for_session_fn,
    get_pane_id_from_session_fn,
    log_reader_cls,
    publish_registry_fn,
) -> None:
    comm.session_info = _required_session_info(comm)
    comm.ccb_session_id = str(comm.session_info.get("ccb_session_id") or "").strip()
    comm.runtime_dir = Path(comm.session_info["runtime_dir"])
    comm.terminal = comm.session_info.get("terminal", os.environ.get("OPENCODE_TERMINAL", "tmux"))
    comm.pane_id = get_pane_id_from_session_fn(comm.session_info) or ""
    comm.pane_title_marker = comm.session_info.get("pane_title_marker") or ""
    comm.backend = get_backend_for_session_fn(comm.session_info)
    comm.timeout = int(os.environ.get("OPENCODE_SYNC_TIMEOUT", "30"))
    comm.marker_prefix = provider_marker_prefix("opencode")
    comm.project_session_file = comm.session_info.get("_session_file")
    comm.log_reader = _log_reader(comm, log_reader_cls=log_reader_cls)
    _publish_runtime_registry(comm, publish_registry_fn=publish_registry_fn)


def _runtime_dir_ok(comm) -> tuple[bool, str]:
    if comm.runtime_dir.exists():
        return True, ""
    return False, "Runtime directory not found"


def _pane_ok(comm, *, probe_terminal: bool) -> tuple[bool, str]:
    if not comm.pane_id:
        return False, "Session pane not found"
    if probe_terminal and comm.backend:
        pane_alive = comm.backend.is_alive(comm.pane_id)
        if not pane_alive:
            return False, f"{comm.terminal} session {comm.pane_id} not found"
    return True, ""


def _storage_ok(storage_root: Path) -> tuple[bool, str]:
    if storage_root.exists():
        return True, ""
    return False, f"OpenCode storage not found: {storage_root}"


def _ensure_session_health(comm, *, probe_terminal: bool) -> tuple[bool, str]:
    healthy, status = comm._check_session_health_impl(probe_terminal=False)
    if not healthy:
        raise RuntimeError(f"❌ Session error: {status}")
    return healthy, status


def _wait_timeout(comm, timeout: int | None) -> int:
    return comm.timeout if timeout is None else int(timeout)


def check_session_health(comm, *, probe_terminal: bool, storage_root: Path) -> tuple[bool, str]:
    try:
        for checker in (
            lambda: _runtime_dir_ok(comm),
            lambda: _pane_ok(comm, probe_terminal=probe_terminal),
            lambda: _storage_ok(storage_root),
        ):
            healthy, status = checker()
            if not healthy:
                return healthy, status
        return True, "Session OK"
    except Exception as exc:
        return False, f"Check failed: {exc}"


def ping(comm, *, display: bool = True) -> tuple[bool, str]:
    healthy, status = comm._check_session_health()
    msg = f"✅ OpenCode connection OK ({status})" if healthy else f"❌ OpenCode connection error: {status}"
    if display:
        print(msg)
    return healthy, msg


def send_message(comm, content: str) -> tuple[str, dict[str, Any]]:
    marker = comm._generate_marker()
    state = comm.log_reader.capture_state()
    comm._send_via_terminal(content)
    return marker, state


def ask_async(comm, question: str) -> bool:
    try:
        _ensure_session_health(comm, probe_terminal=False)
        comm._send_via_terminal(question)
        print("📤 Written to OpenCode, delivery unconfirmed")
        print("Hint: `ccb pend <agent|job_id>` is only a supplementary observer view, not an authoritative completion path")
        return True
    except Exception as exc:
        print(f"❌ Send failed: {exc}")
        return False


def ask_sync(comm, question: str, timeout: int | None = None) -> str | None:
    try:
        _ensure_session_health(comm, probe_terminal=False)
        print(f"🔔 {t('sending_to', provider='OpenCode')}", flush=True)
        _, state = comm._send_message(question)
        wait_timeout = _wait_timeout(comm, timeout)
        print(f"⏳ Waiting for OpenCode reply (timeout {wait_timeout}s)...")
        message, _ = comm.log_reader.wait_for_message(state, float(wait_timeout))
        if message:
            print(f"🤖 {t('reply_from', provider='OpenCode')}")
            print(message)
            return message
        print(f"⏰ {t('timeout_no_reply', provider='OpenCode')}")
        return None
    except Exception as exc:
        print(f"❌ Sync ask failed: {exc}")
        return None


__all__ = ["ask_async", "ask_sync", "check_session_health", "initialize_state", "ping", "send_message"]
