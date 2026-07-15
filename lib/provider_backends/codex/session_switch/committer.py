from __future__ import annotations

from pathlib import Path

from provider_backends.codex.session import CodexProjectSession

from .diagnostics import write_rebound
from .models import SwitchCandidate


def commit_rebind(
    *,
    session_file: Path,
    session_data: dict[str, object],
    candidate: SwitchCandidate,
    runtime_dir: Path | None,
    reason: str,
) -> bool:
    old_session_id = str(session_data.get("codex_session_id") or "").strip()
    old_session_path = str(session_data.get("codex_session_path") or "").strip()
    session = CodexProjectSession(session_file=session_file, data=dict(session_data))
    before = _snapshot(session.data)
    try:
        committed = session.update_codex_log_binding(
            log_path=str(candidate.path),
            session_id=candidate.session_id,
            post_write_validate=lambda: candidate.path.is_file(),
        )
    except Exception:
        return False
    if not committed:
        return False
    changed = before != _snapshot(session.data)
    if changed:
        write_rebound(
            runtime_dir,
            candidate=candidate,
            old_session_id=old_session_id,
            old_session_path=old_session_path,
            reason=reason,
        )
    return changed


def _snapshot(data: dict[str, object]) -> tuple[str, str, str, str]:
    return (
        str(data.get("codex_session_path") or "").strip(),
        str(data.get("codex_session_id") or "").strip(),
        str(data.get("codex_start_cmd") or "").strip(),
        str(data.get("start_cmd") or "").strip(),
    )


__all__ = ["commit_rebind"]
