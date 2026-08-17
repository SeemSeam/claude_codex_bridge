"""Official DSH Web carrier bridge and exact native-turn reducer."""

from __future__ import annotations

import argparse
import asyncio
from dataclasses import dataclass
import json
from pathlib import Path
import signal
import sys
from typing import Any
from urllib.parse import urlsplit, urlunsplit
import uuid

from .control import load_dsh_host_endpoint


_TERMINAL_KINDS = {
    'completed',
    'aborted',
    'blocked',
    'error',
    'max-tokens',
    'interrupted',
}


@dataclass
class DshTurnReducer:
    session_id: str
    rpc_id: str
    open_turn: int | None = None
    anchor_turn: int | None = None
    anchor_seq: int | None = None
    reply: str = ''
    reply_seq: int | None = None
    terminal_kind: str = ''
    terminal_seq: int | None = None
    completed_at: object | None = None
    protocol_error: str = ''
    external_error: str = ''

    def __post_init__(self) -> None:
        self._seen_seq: set[int] = set()

    @property
    def anchor_seen(self) -> bool:
        return self.anchor_turn is not None

    @property
    def finished(self) -> bool:
        return bool(self.terminal_kind)

    def apply(self, event: object) -> bool:
        if not isinstance(event, dict):
            self.protocol_error = self.protocol_error or 'dsh_event_not_object'
            return True
        event_type = str(event.get('type') or '').strip()
        try:
            seq = int(event.get('seq'))
        except (TypeError, ValueError):
            self.protocol_error = self.protocol_error or 'dsh_event_seq_invalid'
            return True
        if seq in self._seen_seq:
            return False
        self._seen_seq.add(seq)
        data = event.get('data')
        if not isinstance(data, dict):
            self.protocol_error = self.protocol_error or f'dsh_event_data_invalid:{event_type}'
            return True

        before = self.observation()
        if event_type == 'turn/start':
            turn = _int_or_none(data.get('turn'))
            if turn is None:
                self.protocol_error = self.protocol_error or 'dsh_turn_start_invalid'
            else:
                self.open_turn = turn
        elif event_type == 'user/message':
            self._apply_user_message(event, data, seq)
        elif event_type == 'assistant/message':
            self._apply_assistant_message(event, data, seq)
        elif event_type == 'turn/end':
            self._apply_turn_end(event, data, seq)
        return before != self.observation()

    def set_external_error(self, message: str) -> None:
        if not self.external_error:
            self.external_error = str(message or 'dsh_bridge_error')[:1000]

    def observation(self) -> dict[str, object]:
        native_error = ''
        outcome_reason = ''
        if self.terminal_kind:
            outcome_reason = 'stop' if self.terminal_kind == 'completed' else self.terminal_kind
            if self.terminal_kind != 'completed':
                native_error = f'dsh_turn_{self.terminal_kind.replace("-", "_")}'
        return {
            'type': 'dsh/observation',
            'session_id': self.session_id,
            'rpc_id': self.rpc_id,
            'anchor_seen': self.anchor_seen,
            'turn': self.anchor_turn,
            'anchor_seq': self.anchor_seq,
            'reply': self.reply,
            'reply_seq': self.reply_seq,
            'finished': self.finished,
            'finish_reason': self.terminal_kind,
            'outcome_reason': outcome_reason,
            'terminal_seq': self.terminal_seq,
            'completed_at': self.completed_at,
            'error': self.external_error or native_error,
            'protocol_error': self.protocol_error,
        }

    def _apply_user_message(self, event: dict[str, Any], data: dict[str, Any], seq: int) -> None:
        source = data.get('source')
        if not isinstance(source, dict):
            return
        if str(source.get('kind') or '') != 'user' or str(source.get('rpcId') or '') != self.rpc_id:
            return
        if self.open_turn is None:
            self.protocol_error = self.protocol_error or 'dsh_anchor_without_open_turn'
            return
        if self.anchor_turn is not None and self.anchor_turn != self.open_turn:
            self.protocol_error = self.protocol_error or 'dsh_rpc_reused_across_turns'
            return
        content = _content_text(data.get('content'))
        expected = f'CCB_REQ_ID: {self.rpc_id}'
        if not content.lstrip().startswith(expected):
            self.protocol_error = self.protocol_error or 'dsh_prompt_anchor_missing'
            return
        self.anchor_turn = self.open_turn
        self.anchor_seq = seq

    def _apply_assistant_message(self, event: dict[str, Any], data: dict[str, Any], seq: int) -> None:
        turn = _int_or_none(data.get('turn'))
        if self.anchor_turn is None or turn != self.anchor_turn:
            return
        if str(event.get('surfaceOp') or '') != 'append':
            self.protocol_error = self.protocol_error or 'dsh_assistant_message_not_committed_append'
            return
        # The official SessionEventMap stores the complete AssistantMessage in
        # data.message.  Keep the direct data.content form only as a bounded
        # compatibility fallback for pre-release carrier projections.
        message = data.get('message')
        content = message.get('content') if isinstance(message, dict) else None
        if content is None:
            content = data.get('content')
        text = _content_text(content).strip()
        if text:
            self.reply = text
            self.reply_seq = seq

    def _apply_turn_end(self, event: dict[str, Any], data: dict[str, Any], seq: int) -> None:
        turn = _int_or_none(data.get('turn'))
        if self.anchor_turn is None or turn != self.anchor_turn:
            if self.open_turn == turn:
                self.open_turn = None
            return
        reason = data.get('reason')
        kind = str(reason.get('kind') or '').strip() if isinstance(reason, dict) else ''
        if kind not in _TERMINAL_KINDS:
            self.protocol_error = self.protocol_error or f'dsh_turn_end_reason_invalid:{kind or "missing"}'
            return
        self.terminal_kind = kind
        self.terminal_seq = seq
        self.completed_at = event.get('time')
        self.open_turn = None


async def run_bridge(request_path: Path, *, observe_only: bool = False) -> int:
    request = _load_request(request_path)
    session_id = _required_text(request, 'session_id')
    rpc_id = _required_text(request, 'rpc_id')
    endpoint_state = Path(_required_text(request, 'endpoint_state_path')).expanduser()
    host_instance_id = _required_text(request, 'host_instance_id')
    reducer = DshTurnReducer(session_id=session_id, rpc_id=rpc_id)
    cancelled = asyncio.Event()
    _install_async_signal_handlers(cancelled)
    submitted = bool(observe_only)
    reconnects = 0

    while not cancelled.is_set():
        endpoint = await _wait_for_endpoint(
            endpoint_state,
            cancelled,
            expected_instance_id=host_instance_id,
        )
        try:
            import aiohttp
        except Exception as exc:  # pragma: no cover - launcher dependency gate
            reducer.set_external_error(f'dsh_bridge_dependency_missing:{type(exc).__name__}')
            _emit(reducer.observation())
            return 1

        try:
            timeout = aiohttp.ClientTimeout(total=None, connect=10, sock_connect=10)
            async with aiohttp.ClientSession(timeout=timeout, trust_env=False) as client:
                ws = await client.ws_connect(_websocket_url(endpoint), heartbeat=20, autoping=True)
                async with ws:
                    await _rpc(client, endpoint, 'host.describe', {}, rpc_id=f'{rpc_id}:describe:{reconnects}')
                    await _rpc(
                        client,
                        endpoint,
                        'session.create',
                        {'sessionId': session_id, 'cwd': _required_text(request, 'work_dir')},
                        rpc_id=f'{rpc_id}:create:{reconnects}',
                    )
                    if not submitted:
                        await _select_model(client, endpoint, request, rpc_id=rpc_id)
                        await _rpc(
                            client,
                            endpoint,
                            'session.prompt',
                            {
                                'sessionId': session_id,
                                'mode': 'queue',
                                'content': [{'type': 'text', 'text': _required_text(request, 'prompt')}],
                            },
                            rpc_id=rpc_id,
                        )
                        submitted = True

                    history = await _history_events(client, endpoint, session_id, rpc_id=rpc_id)
                    changed = False
                    for event in history:
                        changed = reducer.apply(event) or changed
                    if changed or reducer.anchor_seen or reducer.protocol_error:
                        _emit(reducer.observation())
                    if reducer.finished or reducer.protocol_error:
                        return 0
                    if observe_only and not reducer.anchor_seen:
                        reducer.set_external_error('dsh_restore_rpc_not_found')
                        _emit(reducer.observation())
                        return 0

                    stream_result = await _consume_stream(
                        client,
                        ws,
                        endpoint,
                        request,
                        reducer,
                        cancelled,
                    )
                    if stream_result == 'terminal':
                        return 0
                    if stream_result == 'cancelled':
                        await _cancel_turn(client, endpoint, session_id, rpc_id=rpc_id)
                        reducer.set_external_error('dsh_cancelled_by_ccb')
                        _emit(reducer.observation())
                        return 0
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            if cancelled.is_set():
                break
            reconnects += 1
            print(
                f'ccb dsh bridge reconnect {reconnects}: {type(exc).__name__}: {exc}',
                file=sys.stderr,
                flush=True,
            )
            await _cancel_aware_sleep(cancelled, min(2.0, 0.1 * (2 ** min(reconnects, 5))))

    try:
        endpoint = load_dsh_host_endpoint(
            endpoint_state,
            expected_instance_id=host_instance_id,
        )
        import aiohttp

        async with aiohttp.ClientSession(trust_env=False) as client:
            await _cancel_turn(client, endpoint, session_id, rpc_id=rpc_id)
    except Exception:
        pass
    reducer.set_external_error('dsh_cancelled_by_ccb')
    _emit(reducer.observation())
    return 0


async def _consume_stream(client, ws, endpoint: str, request: dict[str, object], reducer: DshTurnReducer, cancelled: asyncio.Event) -> str:
    import aiohttp

    while not cancelled.is_set():
        receive = asyncio.create_task(ws.receive())
        stop = asyncio.create_task(cancelled.wait())
        done, pending = await asyncio.wait({receive, stop}, return_when=asyncio.FIRST_COMPLETED)
        for task in pending:
            task.cancel()
        if pending:
            await asyncio.gather(*pending, return_exceptions=True)
        if stop in done and cancelled.is_set():
            return 'cancelled'
        message = receive.result()
        if message.type == aiohttp.WSMsgType.TEXT:
            envelope = _json_object(message.data, label='dsh mux frame')
            payload = envelope.get('payload')
            if not isinstance(payload, dict):
                raise RuntimeError('dsh mux frame omitted payload')
            frame_type = str(payload.get('type') or '')
            if frame_type == 'session/event' and str(payload.get('sessionId') or '') == reducer.session_id:
                if reducer.apply(payload.get('event')):
                    _emit(reducer.observation())
                if reducer.finished or reducer.protocol_error:
                    return 'terminal'
            elif frame_type == 'approval/requested' and str(payload.get('sessionId') or '') == reducer.session_id:
                result = await _handle_approval(client, endpoint, envelope, payload, request, reducer)
                if result == 'blocked':
                    return 'terminal'
            elif frame_type == 'question/requested' and str(payload.get('sessionId') or '') == reducer.session_id:
                reducer.set_external_error('dsh_interactive_question_required')
                _emit(reducer.observation())
                await _respond_cancelled(
                    client,
                    endpoint,
                    rpc_id=_required_text(envelope, 'rpcId'),
                    message='CCB cannot answer an interactive DSH question',
                )
                await _cancel_turn(client, endpoint, reducer.session_id, rpc_id=reducer.rpc_id)
                return 'terminal'
            elif frame_type == 'stream/error':
                error = payload.get('error')
                raise RuntimeError(f'dsh mux stream error: {error}')
        elif message.type in {aiohttp.WSMsgType.CLOSED, aiohttp.WSMsgType.CLOSE, aiohttp.WSMsgType.ERROR}:
            return 'reconnect'
    return 'cancelled'


async def _handle_approval(client, endpoint: str, envelope: dict[str, object], payload: dict[str, object], request: dict[str, object], reducer: DshTurnReducer) -> str:
    if not reducer.anchor_seen:
        return 'ignored'
    allowed = bool(request.get('auto_permission'))
    outcome = 'allowed-once' if allowed else 'rejected'
    await _respond(
        client,
        endpoint,
        rpc_id=_required_text(envelope, 'rpcId'),
        value={
            'sessionId': reducer.session_id,
            'approvalId': _required_text(payload, 'approvalId'),
            'outcome': outcome,
        },
    )
    if allowed:
        _emit(
            {
                'type': 'dsh/approval',
                'session_id': reducer.session_id,
                'rpc_id': reducer.rpc_id,
                'approval_id': str(payload.get('approvalId') or ''),
                'tool_name': str(payload.get('toolName') or ''),
                'outcome': outcome,
            }
        )
        return 'allowed'
    reducer.set_external_error(f'dsh_approval_required:{str(payload.get("toolName") or "tool")}')
    _emit(reducer.observation())
    return 'blocked'


async def _select_model(client, endpoint: str, request: dict[str, object], *, rpc_id: str) -> None:
    model = str(request.get('model') or '').strip()
    reasoning = str(request.get('reasoning_effort') or '').strip()
    if not model and not reasoning:
        return
    provider = str(request.get('model_provider') or '').strip()
    if not model:
        current = await _rpc(
            client,
            endpoint,
            'session.models',
            {'sessionId': _required_text(request, 'session_id')},
            rpc_id=f'{rpc_id}:models',
        )
        selected = current.get('current')
        if not isinstance(selected, dict):
            raise RuntimeError('dsh session.models omitted current selection')
        provider = str(selected.get('provider') or '').strip()
        model = str(selected.get('model') or '').strip()
        if not provider or not model:
            raise RuntimeError('dsh session.models returned an invalid current selection')
    payload: dict[str, object] = {
        'sessionId': _required_text(request, 'session_id'),
        'provider': provider or 'deepseek-official',
        'model': model,
    }
    if reasoning:
        payload['reasoningEffort'] = reasoning
    await _rpc(client, endpoint, 'session.selectModel', payload, rpc_id=f'{rpc_id}:model')


async def _history_events(client, endpoint: str, session_id: str, *, rpc_id: str) -> list[dict[str, object]]:
    before_seq: int | None = None
    events: dict[int, dict[str, object]] = {}
    for page_no in range(200):
        payload: dict[str, object] = {'sessionId': session_id, 'maxMessages': 100}
        if before_seq is not None:
            payload['beforeSeq'] = before_seq
        value = await _rpc(
            client,
            endpoint,
            'session.history',
            payload,
            rpc_id=f'{rpc_id}:history:{page_no}',
        )
        entries = value.get('events')
        if not isinstance(entries, list):
            raise RuntimeError('dsh session.history omitted events')
        page_seqs: list[int] = []
        found_rpc = False
        for entry in entries:
            if not isinstance(entry, dict) or not isinstance(entry.get('event'), dict):
                raise RuntimeError('dsh session.history returned an invalid entry')
            event = dict(entry['event'])
            try:
                seq = int(event.get('seq'))
            except (TypeError, ValueError) as exc:
                raise RuntimeError('dsh session.history returned an invalid seq') from exc
            events[seq] = event
            page_seqs.append(seq)
            if _event_rpc_id(event) == rpc_id:
                found_rpc = True
        if not bool(value.get('hasMore')):
            break
        if not page_seqs:
            raise RuntimeError('dsh session.history pagination made no progress')
        before_seq = min(page_seqs)
        # Pages are whole-message aligned.  Once the exact user message is in
        # the collected suffix, all later assistant and turn/end events are
        # already present, while its owning turn/start is on the same page.
        if found_rpc:
            break
    else:
        raise RuntimeError('dsh session.history exceeded the bounded page scan')
    return [events[key] for key in sorted(events)]


async def _rpc(client, endpoint: str, method: str, payload: dict[str, object], *, rpc_id: str) -> dict[str, object]:
    envelope = {
        'type': 'client-request',
        'rpcId': rpc_id,
        'method': method,
        'payload': payload,
    }
    async with client.post(f'{endpoint}/api/{method}', json=envelope) as response:
        body = await response.text()
        if response.status != 200:
            raise RuntimeError(f'dsh RPC {method} returned HTTP {response.status}: {body[:300]}')
    result = _json_object(body, label=f'dsh RPC {method}')
    if result.get('type') != 'server-response' or str(result.get('rpcId') or '') != rpc_id:
        raise RuntimeError(f'dsh RPC {method} returned an invalid correlation envelope')
    outcome = result.get('result')
    if not isinstance(outcome, dict):
        raise RuntimeError(f'dsh RPC {method} omitted result')
    if outcome.get('ok') is not True:
        error = outcome.get('error')
        if isinstance(error, dict):
            raise RuntimeError(
                f'dsh RPC {method} rejected: {str(error.get("code") or "unknown")}: '
                f'{str(error.get("message") or "")}'
            )
        raise RuntimeError(f'dsh RPC {method} rejected')
    value = outcome.get('value')
    return dict(value) if isinstance(value, dict) else {}


async def _respond(client, endpoint: str, *, rpc_id: str, value: dict[str, object]) -> None:
    envelope = {
        'type': 'client-response',
        'rpcId': rpc_id,
        'result': {'ok': True, 'value': value},
    }
    await _post_response(client, endpoint, envelope)


async def _respond_cancelled(client, endpoint: str, *, rpc_id: str, message: str) -> None:
    envelope = {
        'type': 'client-response',
        'rpcId': rpc_id,
        'result': {
            'ok': False,
            'error': {
                'code': 'cancelled',
                'message': str(message or 'cancelled'),
                'details': {},
            },
        },
    }
    await _post_response(client, endpoint, envelope)


async def _post_response(client, endpoint: str, envelope: dict[str, object]) -> None:
    async with client.post(f'{endpoint}/api/respond', json=envelope) as response:
        body = await response.text()
        if response.status != 200:
            raise RuntimeError(f'dsh respond returned HTTP {response.status}: {body[:300]}')
    receipt = _json_object(body, label='dsh respond receipt')
    if receipt.get('accepted') is not True:
        raise RuntimeError(f'dsh respond rejected: {receipt}')


async def _cancel_turn(client, endpoint: str, session_id: str, *, rpc_id: str) -> None:
    try:
        await _rpc(
            client,
            endpoint,
            'session.cancel',
            {'sessionId': session_id},
            rpc_id=f'{rpc_id}:cancel:{uuid.uuid4()}',
        )
    except Exception:
        return


async def _wait_for_endpoint(
    path: Path,
    cancelled: asyncio.Event,
    *,
    expected_instance_id: str,
) -> str:
    last_error = 'state unavailable'
    for _attempt in range(300):
        if cancelled.is_set():
            raise asyncio.CancelledError
        try:
            return load_dsh_host_endpoint(
                path,
                expected_instance_id=expected_instance_id,
            )
        except Exception as exc:
            last_error = str(exc)
        await _cancel_aware_sleep(cancelled, 0.1)
    raise RuntimeError(f'dsh host endpoint unavailable: {last_error}')


async def _cancel_aware_sleep(cancelled: asyncio.Event, seconds: float) -> None:
    try:
        await asyncio.wait_for(cancelled.wait(), timeout=max(0.01, seconds))
    except asyncio.TimeoutError:
        return


def _install_async_signal_handlers(cancelled: asyncio.Event) -> None:
    loop = asyncio.get_running_loop()
    for signum in (signal.SIGTERM, signal.SIGINT, signal.SIGHUP):
        try:
            loop.add_signal_handler(signum, cancelled.set)
        except (AttributeError, NotImplementedError, RuntimeError, ValueError):
            continue


def _websocket_url(endpoint: str) -> str:
    parsed = urlsplit(endpoint)
    scheme = 'wss' if parsed.scheme == 'https' else 'ws'
    return urlunsplit((scheme, parsed.netloc, '/api/events.mux', '', ''))


def _load_request(path: Path) -> dict[str, object]:
    try:
        payload = json.loads(Path(path).read_text(encoding='utf-8'))
    except Exception as exc:
        raise RuntimeError(f'dsh bridge request unavailable: {path}') from exc
    if not isinstance(payload, dict) or payload.get('record_type') != 'dsh_bridge_request':
        raise RuntimeError('dsh bridge request is invalid')
    return payload


def _event_rpc_id(event: dict[str, object]) -> str:
    if str(event.get('type') or '') != 'user/message':
        return ''
    data = event.get('data')
    source = data.get('source') if isinstance(data, dict) else None
    return str(source.get('rpcId') or '') if isinstance(source, dict) else ''


def _content_text(value: object) -> str:
    if isinstance(value, str):
        return value
    if isinstance(value, list):
        return ''.join(_content_text(item) for item in value)
    if not isinstance(value, dict):
        return ''
    block_type = str(value.get('type') or '').strip()
    # Reasoning is deliberately excluded: it is not the assistant's reply and
    # must never turn an otherwise empty final response into CCB success.
    if block_type in {'text', 'output_text'} and isinstance(value.get('text'), str):
        return str(value['text'])
    if block_type:
        return ''
    for key in ('content', 'text', 'message'):
        nested = value.get(key)
        if isinstance(nested, (dict, list, str)):
            text = _content_text(nested)
            if text:
                return text
    return ''


def _required_text(payload: dict[str, object], key: str) -> str:
    value = str(payload.get(key) or '').strip()
    if not value:
        raise RuntimeError(f'dsh bridge request omitted {key}')
    return value


def _json_object(value: object, *, label: str) -> dict[str, object]:
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except json.JSONDecodeError as exc:
            raise RuntimeError(f'{label} returned invalid JSON') from exc
    if not isinstance(value, dict):
        raise RuntimeError(f'{label} is not an object')
    return value


def _int_or_none(value: object) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _emit(payload: dict[str, object]) -> None:
    print(json.dumps(payload, ensure_ascii=False, separators=(',', ':')), flush=True)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog='ccb-dsh-bridge')
    parser.add_argument('--request', required=True)
    parser.add_argument('--observe-only', action='store_true')
    args = parser.parse_args(argv)
    try:
        return asyncio.run(run_bridge(Path(args.request), observe_only=bool(args.observe_only)))
    except KeyboardInterrupt:
        return 130
    except Exception as exc:
        print(f'ccb dsh bridge failed: {type(exc).__name__}: {exc}', file=sys.stderr, flush=True)
        _emit(
            {
                'type': 'dsh/observation',
                'session_id': '',
                'rpc_id': '',
                'anchor_seen': False,
                'finished': False,
                'reply': '',
                'error': f'dsh_bridge_failed:{type(exc).__name__}:{exc}',
                'protocol_error': '',
            }
        )
        return 1


if __name__ == '__main__':  # pragma: no cover - subprocess entrypoint
    raise SystemExit(main())


__all__ = ['DshTurnReducer', 'main', 'run_bridge']
