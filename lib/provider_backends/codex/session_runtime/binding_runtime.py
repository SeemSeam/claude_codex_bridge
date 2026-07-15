from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from provider_sessions.files import safe_write_session
from storage.locks import file_lock

from ..session_authority import remember_bound_session_authority
from ..start_cmd import build_resume_start_cmd
from ..start_cmd_runtime.fields_runtime import normalized_session_value, resume_template_command
from .pathing import now_str


@dataclass(frozen=True)
class CurrentBindingState:
    path: str
    session_id: str
    start_cmd: str
    codex_start_cmd: str


@dataclass(frozen=True)
class RequestedBinding:
    path: str
    session_id: str


@dataclass(frozen=True)
class BindingChange:
    old_path: str
    old_id: str
    new_path: str
    new_id: str
    resume_start_cmd: str | None


class SessionWriteError(RuntimeError):
    pass


def update_codex_log_binding(
    session,
    *,
    log_path: str | None,
    session_id: str | None,
    post_write_validate: Callable[[], bool] | None = None,
) -> bool:
    expected_binding = binding_identity(session.data)
    with file_lock(_binding_lock_path(session.session_file)):
        persisted = _read_persisted_session_data(session.session_file)
        if persisted is not None and binding_identity(persisted) != expected_binding:
            raise SessionWriteError("codex session binding changed concurrently")
        before = dict(persisted if persisted is not None else session.data)
        change = binding_change_for_data(before, log_path=log_path, session_id=session_id)
        if change is None:
            _replace_session_data(session, before)
            return False

        updated = dict(before)
        record_binding_change(updated, change)
        updated["updated_at"] = now_str()
        mark_active(updated)
        _write_session_data(session, updated)
        if post_write_validate is not None and not post_write_validate():
            try:
                _restore_persisted_session_data(session, before)
            finally:
                _replace_session_data(session, before)
            raise SessionWriteError("codex session binding failed post-write validation")
        _replace_session_data(session, updated)
    trigger_transfer_if_needed(session, change)
    return True


def binding_change(session, *, log_path: str | None, session_id: str | None) -> BindingChange | None:
    return binding_change_for_data(session.data, log_path=log_path, session_id=session_id)


def binding_change_for_data(
    data: dict[str, object],
    *,
    log_path: str | None,
    session_id: str | None,
) -> BindingChange | None:
    current = current_binding_state_for_data(data)
    requested = requested_binding(log_path=log_path, session_id=session_id)
    resume_start_cmd = resume_start_cmd_for(data, session_id)
    if not should_record_binding_change(
        data,
        current,
        requested,
        session_id=session_id,
        resume_start_cmd=resume_start_cmd,
    ):
        return None
    return BindingChange(
        old_path=current.path,
        old_id=current.session_id,
        new_path=requested.path,
        new_id=requested.session_id,
        resume_start_cmd=resume_start_cmd,
    )


def current_binding_state(session) -> CurrentBindingState:
    return current_binding_state_for_data(session.data)


def current_binding_state_for_data(data: dict[str, object]) -> CurrentBindingState:
    return CurrentBindingState(
        path=str(data.get("codex_session_path") or "").strip(),
        session_id=str(data.get("codex_session_id") or "").strip(),
        start_cmd=str(data.get("start_cmd") or "").strip(),
        codex_start_cmd=str(data.get("codex_start_cmd") or "").strip(),
    )


def requested_binding(*, log_path: str | None, session_id: str | None) -> RequestedBinding:
    path = str(log_path or "").strip()
    return RequestedBinding(
        path=path,
        session_id=normalized_session_id(session_id, log_path_str=path),
    )


def should_record_binding_change(
    data: dict[str, object],
    current: CurrentBindingState,
    requested: RequestedBinding,
    *,
    session_id: str | None,
    resume_start_cmd: str | None,
) -> bool:
    return any(
        (
            path_changed(data, requested.path),
            id_changed(data, session_id),
            resume_command_changed(current, resume_start_cmd),
        )
    )


def path_changed(data: dict[str, object], new_path: str) -> bool:
    return bool(new_path and data.get("codex_session_path") != new_path)


def id_changed(data: dict[str, object], session_id: str | None) -> bool:
    return bool(session_id and data.get("codex_session_id") != session_id)


def resume_command_changed(current: CurrentBindingState, resume_start_cmd: str | None) -> bool:
    if resume_start_cmd is None:
        return False
    return current.start_cmd != resume_start_cmd or current.codex_start_cmd != resume_start_cmd


def resume_start_cmd_for(data: dict[str, object], session_id: object) -> str | None:
    normalized_session_id = normalized_session_value(session_id)
    if not normalized_session_id:
        return None
    return build_resume_start_cmd(resume_template_command(data), normalized_session_id)


def normalized_session_id(session_id: str | None, *, log_path_str: str) -> str:
    new_id = str(session_id or "").strip()
    if new_id or not log_path_str:
        return new_id
    try:
        return Path(log_path_str).stem
    except Exception:
        return ""


def record_binding_change(data: dict[str, object], change: BindingChange) -> None:
    apply_current_binding(data, change)
    mark_old_binding(
        data,
        old_path=change.old_path,
        old_id=change.old_id,
        new_path=change.new_path,
        new_id=change.new_id,
    )


def apply_current_binding(data: dict[str, object], change: BindingChange) -> None:
    if change.new_path:
        data["codex_session_path"] = change.new_path
    if change.new_id:
        data["codex_session_id"] = change.new_id
    if change.resume_start_cmd:
        data["codex_start_cmd"] = change.resume_start_cmd
        data["start_cmd"] = change.resume_start_cmd
    remember_bound_session_authority(data)


def mark_old_binding(data: dict[str, object], *, old_path: str, old_id: str, new_path: str, new_id: str) -> None:
    if old_id and old_id != new_id:
        data["old_codex_session_id"] = old_id
    if old_path and (old_path != new_path or (old_id and old_id != new_id)):
        data["old_codex_session_path"] = old_path
    if old_path or old_id:
        data["old_updated_at"] = now_str()


def trigger_transfer_if_needed(session, change: BindingChange) -> None:
    if not change.old_path and not change.old_id:
        return
    try:
        from memory.transfer_runtime import maybe_auto_transfer

        old_path_obj = expanded_old_path(change.old_path)
        maybe_auto_transfer(
            provider="codex",
            work_dir=Path(session.work_dir),
            session_path=old_path_obj,
            session_id=change.old_id or None,
        )
    except Exception:
        pass


def mark_active(data: dict[str, object]) -> None:
    if data.get("active") is False:
        data["active"] = True


def _write_session_data(session, data: dict[str, object]) -> None:
    payload = json.dumps(data, ensure_ascii=False, indent=2) + "\n"
    ok, error = safe_write_session(session.session_file, payload)
    if not ok:
        raise SessionWriteError(str(error or "failed to write codex session binding"))


def _restore_persisted_session_data(session, data: dict[str, object]) -> None:
    _write_session_data(session, data)


def _replace_session_data(session, data: dict[str, object]) -> None:
    session.data.clear()
    session.data.update(data)


def binding_identity(data: dict[str, object]) -> tuple[str, str]:
    return (
        str(data.get("codex_session_path") or "").strip(),
        str(data.get("codex_session_id") or "").strip(),
    )


def _binding_lock_path(session_file: Path) -> Path:
    path = Path(session_file)
    return path.with_name(path.name + ".binding.lock")


def _read_persisted_session_data(session_file: Path) -> dict[str, object] | None:
    path = Path(session_file)
    if not path.exists():
        return None
    try:
        value = json.loads(path.read_text(encoding="utf-8-sig"))
    except Exception as exc:
        raise SessionWriteError(f"cannot read codex session binding: {exc}") from exc
    if not isinstance(value, dict):
        raise SessionWriteError("codex session binding is not a JSON object")
    return value


def expanded_old_path(old_path: str) -> Path | None:
    if not old_path:
        return None
    try:
        return Path(old_path).expanduser()
    except Exception:
        return None


__all__ = ["SessionWriteError", "update_codex_log_binding"]
