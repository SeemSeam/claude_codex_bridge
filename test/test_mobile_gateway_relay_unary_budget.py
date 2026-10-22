import asyncio
from types import SimpleNamespace

import pytest

from mobile_gateway.relay_host_connector import RelayHostConnector, RelayHostConnectorError
from mobile_gateway.relay_stream import RelayInnerMessage


@pytest.mark.parametrize('close_during_error', [False, True])
def test_unary_encoder_overflow_returns_correlated_sanitized_error(close_during_error):
    async def scenario():
        connector = object.__new__(RelayHostConnector)
        connector._diagnostics = {}
        ws = SimpleNamespace(closed=False)
        session = SimpleNamespace(closed=False)
        sent = []

        async def proxy(operation, payload):
            return {'secret': 'secret capability URL 中文😀' * 100000}

        async def send(ws, session_id, session, message):
            encoded = message.to_bytes()  # Actual production encoder limit.
            if close_during_error:
                ws.closed = True
                raise RelayHostConnectorError('relay session is closed')
            sent.append((message, encoded))

        connector._proxy_gateway_operation = proxy
        connector._send_inner = send
        request = RelayInnerMessage(kind='request', request_id='original-request',
                                    operation='agent_conversation', payload={})
        await connector._handle_unary_request(ws, 'session', session, request)
        if close_during_error:
            assert sent == []
        else:
            assert len(sent) == 1
            message, encoded = sent[0]
            assert message.kind == 'error'
            assert message.request_id == request.request_id
            assert message.payload == {'code': 'payload_too_large'}
            assert b'secret' not in encoded
        assert connector._diagnostics['requests_rejected'] == 1

    asyncio.run(scenario())


def test_unary_closed_socket_does_not_attempt_recursive_error_send():
    async def scenario():
        connector = object.__new__(RelayHostConnector)
        connector._diagnostics = {}
        calls = []

        async def proxy(operation, payload):
            return {'status': 'ok'}

        async def send(*args):
            calls.append(args)
            raise RelayHostConnectorError('relay session is closed')

        connector._proxy_gateway_operation = proxy
        connector._send_inner = send
        await connector._handle_unary_request(
            SimpleNamespace(closed=True), 'session', SimpleNamespace(closed=True),
            RelayInnerMessage(kind='request', request_id='request',
                              operation='agent_conversation', payload={}))
        assert len(calls) == 1

    asyncio.run(scenario())
