#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import sys
from typing import Any

script_dir = Path(__file__).resolve().parent
lib_dir = script_dir.parent / 'lib'
sys.path.insert(0, str(lib_dir))

from provider_hooks.activity import write_activity


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description='Write CCB provider activity status artifacts.')
    parser.add_argument('--provider', required=True)
    parser.add_argument('--project-id', required=True)
    parser.add_argument('--agent-name')
    parser.add_argument('--runtime-dir')
    parser.add_argument('--workspace')
    parser.add_argument('--state')
    return parser.parse_args()


def _read_stdin_payload() -> dict[str, Any]:
    raw = sys.stdin.read()
    if not raw.strip():
        return {}
    try:
        payload = json.loads(raw)
    except Exception:
        return {}
    return payload if isinstance(payload, dict) else {}


def _first_text(payload: dict[str, Any], *paths: str) -> str:
    for path in paths:
        value = _lookup(payload, path)
        if value is None:
            continue
        text = str(value).strip()
        if text:
            return text
    return ''


def _lookup(payload: dict[str, Any], path: str) -> Any:
    current: Any = payload
    for part in path.split('.'):
        if not isinstance(current, dict):
            return None
        current = current.get(part)
    return current


def _activity_state(provider: str, payload: dict[str, Any], explicit_state: str | None) -> str | None:
    if explicit_state:
        return explicit_state
    if _has_error(payload):
        return 'failed'
    event = _event_name(payload)
    normalized_event = event.lower().replace('-', '').replace('_', '')
    if normalized_event in {'userpromptsubmit', 'posttooluse'}:
        return 'active'
    if normalized_event in {'pretooluse'}:
        return 'tool'
    if normalized_event in {'permissionrequest', 'notification'}:
        if provider == 'claude' and normalized_event == 'notification' and not _claude_notification_waiting(payload):
            return None
        return 'waiting'
    if normalized_event in {'sessionstart', 'stop'}:
        if normalized_event == 'stop' and _background_tasks_running(payload):
            return 'active'
        return 'idle'
    return None


def _event_name(payload: dict[str, Any]) -> str:
    return _first_text(payload, 'hook_event_name', 'event_name', 'event', 'type', 'name') or 'unknown'


def _provider_session_id(payload: dict[str, Any]) -> str | None:
    return _first_text(payload, 'session_id', 'sessionId', 'session.id') or None


def _provider_turn_id(payload: dict[str, Any]) -> str | None:
    return _first_text(payload, 'turn_id', 'turnId', 'turn.id', 'request_id', 'requestId') or None


def _model(payload: dict[str, Any]) -> str | None:
    return _first_text(payload, 'model', 'model_id', 'modelId', 'request.model') or None


def _diagnostics(payload: dict[str, Any]) -> dict[str, object]:
    result: dict[str, object] = {}
    tool_name = _first_text(payload, 'tool_name', 'tool.name', 'toolName')
    if tool_name:
        result['tool_name'] = tool_name
    error_type = _first_text(payload, 'error.type', 'error_type', 'errorType')
    if error_type:
        result['error_type'] = error_type
        result['reason'] = 'api_error'
    error_code = _first_text(payload, 'error.code', 'error_code', 'errorCode')
    if error_code:
        result['error_code'] = error_code
    error_message = _first_text(payload, 'error.message', 'error_message', 'errorMessage')
    if error_message:
        result['error_message_preview'] = error_message[:300]
    return result


def _has_error(payload: dict[str, Any]) -> bool:
    if any(_first_text(payload, path) for path in ('error', 'error.message', 'error_type', 'errorType')):
        return True
    status = _first_text(payload, 'status', 'state').lower()
    return status in {'failed', 'failure', 'error', 'errored'}


def _background_tasks_running(payload: dict[str, Any]) -> bool:
    value = _lookup(payload, 'background_tasks')
    if isinstance(value, list):
        return len(value) > 0
    if isinstance(value, dict):
        running = value.get('running')
        if isinstance(running, list):
            return len(running) > 0
        if isinstance(running, bool):
            return running
    return bool(payload.get('background_tasks_running'))


def _claude_notification_waiting(payload: dict[str, Any]) -> bool:
    text = ' '.join(
        _first_text(payload, path).lower()
        for path in ('message', 'notification.message', 'title', 'notification.title', 'reason')
    )
    return any(marker in text for marker in ('permission', 'approve', 'input', 'waiting', 'blocked'))


def main() -> int:
    args = _parse_args()
    payload = _read_stdin_payload()
    provider = str(args.provider or '').strip().lower()
    state = _activity_state(provider, payload, args.state)
    runtime_dir = str(args.runtime_dir or os.environ.get('CCB_CALLER_RUNTIME_DIR') or '').strip()
    agent_name = str(args.agent_name or os.environ.get('CCB_CALLER_ACTOR') or '').strip()
    if not state or not runtime_dir or not agent_name:
        return 0
    try:
        write_activity(
            provider=provider,
            project_id=str(args.project_id),
            agent_name=agent_name,
            runtime_dir=runtime_dir,
            state=state,
            source=f'{provider}_hook',
            event_name=_event_name(payload),
            ccb_session_id=os.environ.get('CCB_SESSION_ID'),
            pane_id=os.environ.get('TMUX_PANE'),
            workspace_path=args.workspace,
            provider_session_id=_provider_session_id(payload),
            provider_turn_id=_provider_turn_id(payload),
            model=_model(payload),
            diagnostics=_diagnostics(payload),
        )
    except Exception:
        return 0
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
