#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

script_dir = Path(__file__).resolve().parent
lib_dir = script_dir.parent / 'lib'
sys.path.insert(0, str(lib_dir))

from provider_hooks.artifacts import current_turn_req_id_from_transcript, extract_req_id, write_event


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description='Write exact provider completion artifacts for Claude/Gemini hooks.')
    parser.add_argument('--provider', required=True)
    parser.add_argument('--completion-dir', required=True)
    parser.add_argument('--agent-name', required=True)
    parser.add_argument('--workspace', required=True)
    return parser.parse_args()


def _read_stdin_payload() -> dict:
    raw = sys.stdin.read()
    if not raw.strip():
        return {}
    try:
        data = json.loads(raw)
    except Exception:
        return {}
    return dict(data) if isinstance(data, dict) else {}


def _lookup_path(payload: dict[str, Any], path: str) -> Any:
    current: Any = payload
    for part in path.split('.'):
        if not isinstance(current, dict):
            return None
        current = current.get(part)
    return current


def _first_value(payload: dict[str, Any], *paths: str) -> Any:
    for path in paths:
        value = _lookup_path(payload, path)
        if value is None:
            continue
        if isinstance(value, str) and not value.strip():
            continue
        return value
    return None


def _first_text(payload: dict[str, Any], *paths: str) -> str:
    value = _first_value(payload, *paths)
    if value is None:
        return ''
    return str(value).strip()


def _normalize_status_token(value: object) -> str | None:
    token = ''.join(ch for ch in str(value or '').strip().lower() if ch.isalnum() or ch in {'_', '-'})
    if not token:
        return None
    if token in {'completed', 'complete', 'success', 'succeeded', 'ok', 'stop', 'stopped', 'finished', 'done'}:
        return 'completed'
    if token in {'failed', 'failure', 'error', 'errored'}:
        return 'failed'
    if token in {'cancelled', 'canceled', 'cancel', 'aborted', 'abort', 'interrupted'}:
        return 'cancelled'
    if token in {'incomplete', 'timeout', 'timedout', 'max_tokens', 'maxtokens', 'length'}:
        return 'incomplete'
    return None


def _gemini_text_failure_details(text: str) -> dict[str, str] | None:
    normalized = str(text or '').strip().lower()
    if not normalized:
        return None
    markers = (
        ('LoginRequired', 'code assist login required'),
        ('LoginRequired', 'login required'),
        ('NotLoggedIn', 'not logged in'),
        ('AuthenticationFailed', 'authentication failed'),
        ('PermissionDenied', 'permission denied'),
        ('AccessDenied', 'access denied'),
        ('Forbidden', 'forbidden'),
        ('Unauthorized', 'unauthorized'),
        ('InsufficientQuota', 'insufficient quota'),
        ('QuotaExceeded', 'quota exceeded'),
        ('PaymentRequired', 'payment required'),
        ('InsufficientBalance', 'insufficient balance'),
        ('CreditBalanceTooLow', 'credit balance too low'),
    )
    for error_code, marker in markers:
        if marker in normalized:
            return {
                'error_code': error_code,
                'error_message': str(text).strip(),
            }
    return None


def _empty_reply_diagnostics(*, reason: str) -> dict[str, Any]:
    diagnosis = (
        'Provider completion hook fired without assistant reply text; inspect '
        'the provider transcript, pane state, and authentication/API output.'
    )
    return {
        'reason': reason,
        'empty_reply': True,
        'error_type': 'empty_provider_reply',
        'message': diagnosis,
        'diagnosis': diagnosis,
    }


def _gemini_event_status_and_diagnostics(payload: dict[str, Any], reply: str) -> tuple[str, dict[str, Any]]:
    diagnostics: dict[str, Any] = {
        'hook_event_name': _first_text(payload, 'hook_event_name') or 'AfterAgent',
    }
    finish_reason = _first_text(payload, 'finishReason', 'finish_reason', 'result.finishReason', 'result.finish_reason')
    if finish_reason:
        diagnostics['finish_reason'] = finish_reason

    raw_error_value = _first_value(
        payload,
        'error',
        'result.error',
        'response.error',
        'agent.error',
        'failure',
        'exception',
        'cause',
    )
    error_payload = dict(raw_error_value) if isinstance(raw_error_value, dict) else {}
    error_code = _first_text(
        payload,
        'error.code',
        'error.error_code',
        'error.errorCode',
        'result.error.code',
        'response.error.code',
        'agent.error.code',
        'failure.code',
        'exception.code',
        'cause.code',
        'error_code',
        'errorCode',
    )
    error_type = _first_text(
        payload,
        'error.type',
        'error.error_type',
        'error.errorType',
        'result.error.type',
        'response.error.type',
        'agent.error.type',
        'failure.type',
        'exception.type',
        'cause.type',
        'error_type',
        'errorType',
    )
    error_message = _first_text(
        payload,
        'error.message',
        'error.error_message',
        'error.errorMessage',
        'result.error.message',
        'response.error.message',
        'agent.error.message',
        'failure.message',
        'exception.message',
        'cause.message',
        'error_message',
        'errorMessage',
    )
    if not error_message and raw_error_value is not None and not isinstance(raw_error_value, dict):
        error_message = str(raw_error_value).strip()

    explicit_status = _normalize_status_token(
        _first_value(
            payload,
            'status',
            'result.status',
            'response.status',
            'agent.status',
            'state',
            'result.state',
            'response.state',
        )
    )
    finish_status = _normalize_status_token(finish_reason)
    text_failure = _gemini_text_failure_details(reply)
    has_explicit_error = bool(error_code or error_type or error_message or raw_error_value)

    if text_failure:
        error_code = error_code or text_failure['error_code']
        error_message = error_message or text_failure['error_message']

    status = explicit_status or ('failed' if has_explicit_error or text_failure else None) or finish_status or 'completed'
    if status == 'completed' and (has_explicit_error or text_failure):
        status = 'failed'

    if status == 'failed':
        diagnostics['error_type'] = error_type or 'provider_api_error'
        diagnostics['reason'] = 'api_error'
    elif error_type:
        diagnostics['error_type'] = error_type

    if error_code:
        diagnostics['error_code'] = error_code
    if error_message:
        diagnostics['error_message'] = error_message
        diagnostics['text'] = error_message
    return status, diagnostics


def _handle_claude(*, payload: dict, completion_dir: Path, agent_name: str, workspace_path: str) -> int:
    event_name = str(payload.get('hook_event_name') or '').strip() or 'Stop'
    transcript_path = str(payload.get('transcript_path') or '').strip()
    reply = str(payload.get('last_assistant_message') or '')
    req_id = current_turn_req_id_from_transcript(transcript_path, assistant_reply=reply)
    if not req_id:
        return 0
    status = 'completed' if event_name == 'Stop' else 'failed'
    diagnostics = {
        'hook_event_name': event_name,
        'stop_hook_active': bool(payload.get('stop_hook_active', False)),
    }
    if status == 'completed' and not reply.strip():
        status = 'incomplete'
        diagnostics.update(_empty_reply_diagnostics(reason='hook_stop_empty_reply'))
    write_event(
        provider='claude',
        completion_dir=completion_dir,
        agent_name=agent_name,
        workspace_path=workspace_path,
        req_id=req_id,
        status=status,
        reply=reply,
        session_id=str(payload.get('session_id') or '').strip() or None,
        hook_event_name=event_name,
        transcript_path=transcript_path or None,
        diagnostics=diagnostics,
    )
    return 0


def _handle_gemini(*, payload: dict, completion_dir: Path, agent_name: str, workspace_path: str) -> int:
    prompt = _first_text(payload, 'prompt', 'request.prompt', 'input.prompt')
    req_id = extract_req_id(prompt)
    if not req_id:
        return 0
    raw_reply = _first_value(
        payload,
        'prompt_response',
        'response',
        'result.response',
        'reply',
        'message',
    )
    reply = '' if raw_reply is None else str(raw_reply).strip()
    status, diagnostics = _gemini_event_status_and_diagnostics(payload, reply)
    if status == 'completed' and not reply:
        status = 'incomplete'
        diagnostics.update(_empty_reply_diagnostics(reason='hook_after_agent_incomplete'))
    session_id = _first_text(payload, 'session_id', 'sessionId', 'session.id') or None
    hook_event_name = _first_text(payload, 'hook_event_name') or 'AfterAgent'
    write_event(
        provider='gemini',
        completion_dir=completion_dir,
        agent_name=agent_name,
        workspace_path=workspace_path,
        req_id=req_id,
        status=status,
        reply=reply,
        session_id=session_id,
        hook_event_name=hook_event_name,
        transcript_path=None,
        diagnostics=diagnostics,
    )
    return 0


def main() -> int:
    args = _parse_args()
    payload = _read_stdin_payload()
    completion_dir = Path(args.completion_dir).expanduser()
    provider = str(args.provider or '').strip().lower()
    if provider == 'claude':
        return _handle_claude(
            payload=payload,
            completion_dir=completion_dir,
            agent_name=str(args.agent_name),
            workspace_path=str(args.workspace),
        )
    if provider == 'gemini':
        return _handle_gemini(
            payload=payload,
            completion_dir=completion_dir,
            agent_name=str(args.agent_name),
            workspace_path=str(args.workspace),
        )
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
