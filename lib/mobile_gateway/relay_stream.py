from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Mapping


RELAY_INNER_PROTOCOL_VERSION = 1
RELAY_STREAM_MAX_MESSAGE_BYTES = 512 * 1024
RELAY_STREAM_INITIAL_WINDOW_BYTES = RELAY_STREAM_MAX_MESSAGE_BYTES
RELAY_STREAM_MAX_WINDOW_BYTES = 2 * 1024 * 1024

_IDENTIFIER_RE = re.compile(r'^[A-Za-z0-9][A-Za-z0-9._:-]{7,127}$')
_KINDS = {
    'request',
    'response',
    'stream_open',
    'stream_data',
    'stream_window',
    'stream_close',
    'stream_cancel',
    'error',
}
RELAY_UNARY_OPERATIONS = frozenset({
    'pair_claim',
    'health',
    'device',
    'list_projects',
    'get_project_view',
    'get_agent_provider_control',
    'get_agent_provider_quota',
    'update_agent_provider_settings',
    'focus_agent',
    'focus_window',
    'terminal_history',
    'agent_conversation',
    'submit_agent_message',
    'ui_response',
    'lifecycle',
    'open_terminal',
    'open_host_terminal',
    'terminate_host_terminal',
})
RELAY_STREAM_OPERATIONS = frozenset(
    {'terminal', 'notifications', 'file_upload', 'file_download'}
)
_SAFE_ERROR_CODES = {
    'bad_request',
    'operation_not_allowed',
    'payload_too_large',
    'request_failed',
    'stream_conflict',
    'stream_limit',
    'stream_not_found',
    'stream_protocol_error',
    'stream_slow_consumer',
    'stream_upstream_error',
}


class RelayStreamProtocolError(ValueError):
    def __init__(self, code: str = 'stream_protocol_error') -> None:
        safe_code = code if code in _SAFE_ERROR_CODES else 'stream_protocol_error'
        super().__init__(safe_code)
        self.code = safe_code


@dataclass(frozen=True)
class RelayInnerMessage:
    kind: str
    payload: Mapping[str, object]
    request_id: str | None = None
    stream_id: str | None = None
    operation: str | None = None
    credit_bytes: int | None = None
    schema_version: int = RELAY_INNER_PROTOCOL_VERSION

    @classmethod
    def from_bytes(cls, value: bytes) -> 'RelayInnerMessage':
        if len(value) > RELAY_STREAM_MAX_MESSAGE_BYTES:
            raise RelayStreamProtocolError('payload_too_large')
        try:
            decoded = json.loads(value.decode('utf-8'))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise RelayStreamProtocolError('bad_request') from exc
        if not isinstance(decoded, Mapping):
            raise RelayStreamProtocolError('bad_request')
        return cls.from_json(decoded)

    @classmethod
    def from_json(cls, value: Mapping[str, object]) -> 'RelayInnerMessage':
        message = cls(
            schema_version=_positive_int(value.get('schema_version'), 'schema_version'),
            kind=_required_text(value.get('kind'), 'kind'),
            request_id=_optional_identifier(value.get('request_id'), 'request_id'),
            stream_id=_optional_identifier(value.get('stream_id'), 'stream_id'),
            operation=_optional_text(value.get('operation')),
            credit_bytes=_optional_positive_int(value.get('credit_bytes'), 'credit_bytes'),
            payload=_object_map(value.get('payload'), 'payload'),
        )
        message.validate()
        return message

    def to_json(self) -> dict[str, object]:
        self.validate()
        return {
            'schema_version': self.schema_version,
            'kind': self.kind,
            **({'request_id': self.request_id} if self.request_id is not None else {}),
            **({'stream_id': self.stream_id} if self.stream_id is not None else {}),
            **({'operation': self.operation} if self.operation is not None else {}),
            **({'credit_bytes': self.credit_bytes} if self.credit_bytes is not None else {}),
            'payload': dict(self.payload),
        }

    def to_bytes(self) -> bytes:
        encoded = json.dumps(
            self.to_json(),
            ensure_ascii=True,
            sort_keys=True,
            separators=(',', ':'),
        ).encode('utf-8')
        if len(encoded) > RELAY_STREAM_MAX_MESSAGE_BYTES:
            raise RelayStreamProtocolError('payload_too_large')
        return encoded

    def validate(self) -> None:
        if self.schema_version != RELAY_INNER_PROTOCOL_VERSION:
            raise RelayStreamProtocolError('stream_protocol_error')
        if self.kind not in _KINDS:
            raise RelayStreamProtocolError('stream_protocol_error')
        if self.kind in {'request', 'response'}:
            _require_identifier(self.request_id, 'request_id')
            if self.stream_id is not None or self.credit_bytes is not None:
                raise RelayStreamProtocolError('stream_protocol_error')
        elif self.kind in {
            'stream_open',
            'stream_data',
            'stream_window',
            'stream_close',
            'stream_cancel',
        }:
            _require_identifier(self.stream_id, 'stream_id')
            if self.request_id is not None:
                raise RelayStreamProtocolError('stream_protocol_error')
        elif self.kind == 'error':
            if (self.request_id is None) == (self.stream_id is None):
                raise RelayStreamProtocolError('stream_protocol_error')
        if self.kind == 'request':
            if self.operation not in RELAY_UNARY_OPERATIONS:
                raise RelayStreamProtocolError('operation_not_allowed')
        elif self.kind == 'stream_open':
            if self.operation not in RELAY_STREAM_OPERATIONS:
                raise RelayStreamProtocolError('operation_not_allowed')
            _window(self.credit_bytes)
        elif self.kind == 'stream_window':
            if self.operation is not None:
                raise RelayStreamProtocolError('stream_protocol_error')
            _window(self.credit_bytes)
        elif self.credit_bytes is not None:
            raise RelayStreamProtocolError('stream_protocol_error')
        elif self.operation is not None:
            raise RelayStreamProtocolError('stream_protocol_error')
        if self.kind == 'error':
            code = _required_text(self.payload.get('code'), 'error.code')
            if code not in _SAFE_ERROR_CODES:
                raise RelayStreamProtocolError('stream_protocol_error')


def relay_inner_payload_size(payload: Mapping[str, object]) -> int:
    try:
        return len(
            json.dumps(
                dict(payload),
                ensure_ascii=True,
                sort_keys=True,
                separators=(',', ':'),
            ).encode('utf-8')
        )
    except (TypeError, ValueError) as exc:
        raise RelayStreamProtocolError('bad_request') from exc


def _window(value: int | None) -> int:
    if value is None or value <= 0 or value > RELAY_STREAM_MAX_WINDOW_BYTES:
        raise RelayStreamProtocolError('stream_protocol_error')
    return value


def _object_map(value: object, name: str) -> dict[str, object]:
    if value is None:
        return {}
    if isinstance(value, Mapping):
        return {str(key): item for key, item in value.items()}
    raise RelayStreamProtocolError('bad_request')


def _required_text(value: object, name: str) -> str:
    text = str(value or '').strip()
    if not text:
        raise RelayStreamProtocolError('bad_request')
    return text


def _optional_text(value: object) -> str | None:
    text = str(value or '').strip()
    return text or None


def _require_identifier(value: str | None, name: str) -> str:
    if value is None or _IDENTIFIER_RE.fullmatch(value) is None:
        raise RelayStreamProtocolError('stream_protocol_error')
    return value


def _optional_identifier(value: object, name: str) -> str | None:
    if value is None:
        return None
    return _require_identifier(str(value).strip(), name)


def _positive_int(value: object, name: str) -> int:
    if isinstance(value, bool):
        raise RelayStreamProtocolError('bad_request')
    if isinstance(value, float):
        raise RelayStreamProtocolError('bad_request')
    try:
        parsed = int(value)
    except (TypeError, ValueError) as exc:
        raise RelayStreamProtocolError('bad_request') from exc
    if parsed <= 0:
        raise RelayStreamProtocolError('bad_request')
    return parsed


def _optional_positive_int(value: object, name: str) -> int | None:
    if value is None:
        return None
    return _positive_int(value, name)


__all__ = [
    'RELAY_INNER_PROTOCOL_VERSION',
    'RELAY_STREAM_INITIAL_WINDOW_BYTES',
    'RELAY_STREAM_MAX_MESSAGE_BYTES',
    'RELAY_STREAM_MAX_WINDOW_BYTES',
    'RelayInnerMessage',
    'RelayStreamProtocolError',
    'relay_inner_payload_size',
]
