from __future__ import annotations

import asyncio
import base64
import json
import ssl
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import aiohttp
from aiohttp import web
import pytest
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ed25519, rsa
from cryptography.x509.oid import NameOID

from mobile_gateway.relay import RelayAccessGrant, issue_host_rendezvous_capability
from mobile_gateway.relay_admission import (
    RelayAdmissionSecrets,
    RelayAdmissionStore,
    generate_host_private_key,
    host_public_key_b64,
)
from mobile_gateway.relay_crypto import (
    RelayDirection,
    RelayV2Envelope,
    derive_relay_v2_key_schedule,
    host_fingerprint_for_public_key,
    key_pair_from_private_bytes,
    public_key_b64,
)
from mobile_gateway.relay_host_connector import (
    RelayHostConnector,
    RelayHostConnectorConfig,
    RelayHostConnectorError,
    _gateway_request,
)
from mobile_gateway.relay_service import ProductionRelayConfig, ProductionRelayService
from mobile_gateway.relay_stream import (
    RELAY_STREAM_INITIAL_WINDOW_BYTES,
    RELAY_STREAM_MAX_MESSAGE_BYTES,
    RelayInnerMessage,
    relay_inner_payload_size,
)


def test_relay_host_connector_requires_safe_origins() -> None:
    host_signing_key = generate_host_private_key()
    host_crypto_key = key_pair_from_private_bytes(bytes(range(101, 133)))

    with pytest.raises(ValueError, match='wss://'):
        RelayHostConnectorConfig(
            relay_origin='ws://relay.example',
            gateway_origin='http://127.0.0.1:8787',
            host_id='rhost_demo',
            host_signing_key=host_signing_key,
            host_crypto_private_key=host_crypto_key,
        )

    with pytest.raises(ValueError, match='origin'):
        RelayHostConnectorConfig(
            relay_origin='wss://relay.example/v2/host',
            gateway_origin='http://127.0.0.1:8787',
            host_id='rhost_demo',
            host_signing_key=host_signing_key,
            host_crypto_private_key=host_crypto_key,
        )

    with pytest.raises(ValueError, match='loopback'):
        RelayHostConnectorConfig(
            relay_origin='wss://relay.example',
            gateway_origin='http://gateway.example:8787',
            host_id='rhost_demo',
            host_signing_key=host_signing_key,
            host_crypto_private_key=host_crypto_key,
        )


def test_relay_host_connector_maps_provider_control_without_proxy_escape() -> None:
    read = _gateway_request(
        'get_agent_provider_control',
        {
            'project_id': 'project/demo',
            'agent': 'worker one',
            'device_token': 'must-not-enter-path',
        },
    )
    quota = _gateway_request(
        'get_agent_provider_quota',
        {'project_id': 'project/demo', 'agent': 'worker one'},
    )
    mutation = _gateway_request(
        'update_agent_provider_settings',
        {
            'project_id': 'project/demo',
            'agent': 'worker one',
            'model': 'gpt-5.6-sol',
            'thinking': 'xhigh',
            'expected_revision': 'config-r1',
            'expected_namespace_epoch': 7,
            'expected_runtime_revision': 'runtime-r1',
            'expected_provider': 'codex',
            'idempotency_key': 'provider-idempotency-0001',
            'device_token': 'must-not-forward',
            'arbitrary_path': '/etc/passwd',
        },
    )
    unbound_runtime_mutation = _gateway_request(
        'update_agent_provider_settings',
        {
            'project_id': 'project/demo',
            'agent': 'worker one',
            'model': 'gpt-5.6-sol',
            'thinking': 'medium',
            'expected_revision': 'config-r2',
            'expected_namespace_epoch': 7,
            'expected_runtime_revision': None,
            'expected_provider': 'codex',
            'idempotency_key': 'provider-idempotency-0002',
        },
    )

    assert read.method == 'GET'
    assert read.path == '/v1/projects/project%2Fdemo/agents/worker%20one/provider-control'
    assert read.query == {}
    assert quota.method == 'GET'
    assert quota.path == '/v1/projects/project%2Fdemo/agents/worker%20one/provider-quota'
    assert mutation.method == 'POST'
    assert mutation.path == read.path
    assert json.loads((mutation.body or b'{}').decode('utf-8')) == {
        'expected_namespace_epoch': 7,
        'expected_provider': 'codex',
        'expected_revision': 'config-r1',
        'expected_runtime_revision': 'runtime-r1',
        'idempotency_key': 'provider-idempotency-0001',
        'model': 'gpt-5.6-sol',
        'thinking': 'xhigh',
    }
    assert json.loads(
        (unbound_runtime_mutation.body or b'{}').decode('utf-8')
    )['expected_runtime_revision'] is None


@pytest.mark.parametrize('agent_fields', [
    {'agent': 'worker /?# one'},
    {'agent_name': 'worker /?# one'},
    {'agent': 'worker /?# one', 'agent_name': 'worker /?# one'},
])
def test_relay_submit_maps_canonical_agent_without_path_escape(agent_fields):
    request = _gateway_request('submit_agent_message', {
        'project_id': 'project/demo', **agent_fields, 'body': 'hello',
        'namespace_epoch': 4, 'idempotency_key': 'message-budget-0001',
        'expected_omp_session_id': 'native', 'expected_omp_generation': 'instance:1',
        'device_token': 'do-not-forward',
    })
    assert request.method == 'POST'
    assert request.path == '/v1/projects/project%2Fdemo/agents/worker%20%2F%3F%23%20one/messages'
    body = json.loads(request.body)
    assert body['body'] == 'hello'
    assert body['expected_omp_session_id'] == 'native'
    assert body['expected_omp_generation'] == 'instance:1'
    assert 'device_token' not in body


@pytest.mark.parametrize('agent_fields', [
    {'agent': 'one', 'agent_name': 'two'},
    {'agent': '', 'agent_name': 'two'},
    {'agent': 'one', 'agent_name': ''},
    {},
])
def test_relay_submit_rejects_conflicting_or_missing_agent(agent_fields):
    with pytest.raises(RelayHostConnectorError):
        _gateway_request('submit_agent_message', {'project_id': 'project', **agent_fields})


def test_relay_host_connector_maps_host_terminal_without_path_escape() -> None:
    opened = _gateway_request(
        'open_host_terminal',
        {
            'schema_version': 1,
            'client_session_id': 'shell-2',
            'display_name': 'Shell 2',
            'geometry': {'columns': 100, 'rows': 30},
            'device_token': 'must-not-forward',
            'arbitrary_path': '/etc/passwd',
        },
    )
    terminated = _gateway_request(
        'terminate_host_terminal',
        {
            'schema_version': 1,
            'client_session_id': 'shell-2',
            'arbitrary_path': '/etc/passwd',
        },
    )

    assert opened.method == 'POST'
    assert opened.path == '/v1/terminals'
    assert json.loads(opened.body or b'{}') == {
        'schema_version': 1,
        'client_session_id': 'shell-2',
        'display_name': 'Shell 2',
        'geometry': {'columns': 100, 'rows': 30},
    }
    assert terminated.method == 'POST'
    assert terminated.path == '/v1/terminals/terminate'
    assert json.loads(terminated.body or b'{}') == {
        'schema_version': 1,
        'client_session_id': 'shell-2',
    }


def test_relay_host_connector_proxies_encrypted_gateway_request(tmp_path: Path) -> None:
    asyncio.run(_relay_host_connector_proxies_encrypted_gateway_request(tmp_path))


def test_relay_host_connector_opens_terminal_with_top_level_project_id(
    tmp_path: Path,
) -> None:
    asyncio.run(
        _relay_host_connector_opens_terminal_with_top_level_project_id(tmp_path)
    )


async def _relay_host_connector_opens_terminal_with_top_level_project_id(
    tmp_path: Path,
) -> None:
    relay, issued = await _started_relay(tmp_path)
    gateway = await _started_gateway()
    connector = RelayHostConnector(
        RelayHostConnectorConfig(
            relay_origin=_relay_origin(relay),
            gateway_origin=gateway.origin,
            host_id=issued.host_id,
            host_signing_key=issued.private_key,
            host_crypto_private_key=key_pair_from_private_bytes(
                bytes(range(101, 133))
            ),
            tls_context=_client_ssl(),
            request_timeout_seconds=1.0,
        )
    )
    task = asyncio.create_task(connector.connect_once())
    try:
        await _wait_for(lambda: connector.diagnostics()['state'] == 'registered')
        async with aiohttp.ClientSession(raise_for_status=True) as client:
            phone = await client.ws_connect(
                relay.url('/v2/phone'),
                ssl=_client_ssl(),
            )
            phone_crypto, _ = await _open_phone_session(
                phone,
                issued=issued,
                relay_origin=issued.relay_audience,
                expected_host_public_key=public_key_b64(
                    connector.config.host_crypto_private_key
                ),
            )
            response = await _round_trip_gateway_request(
                phone,
                phone_crypto,
                session_id='relay-host-connector-session',
                outer_seq=2,
                operation='open_terminal',
                payload={
                    'request_id': 'request-open-terminal-1',
                    'schema_version': 1,
                    'project_id': 'project-demo',
                    'namespace_epoch': 7,
                    'target': {
                        'kind': 'agent',
                        'agent': 'worker1',
                    },
                    'geometry': {
                        'columns': 80,
                        'rows': 24,
                        'pixel_width': 0,
                        'pixel_height': 0,
                    },
                    'device_token': 'device-token-demo',
                },
            )

            assert response.payload['ok'] is True, response.payload
            assert response.payload['status'] == 201
            assert response.payload['body']['terminal_id'] == 'term-demo'
            assert gateway.requests == [
                ('POST', '/v1/projects/project-demo/terminals')
            ]
            assert gateway.request_bodies == [
                {
                    'schema_version': 1,
                    'project_id': 'project-demo',
                    'namespace_epoch': 7,
                    'target': {
                        'kind': 'agent',
                        'agent': 'worker1',
                    },
                    'geometry': {
                        'columns': 80,
                        'rows': 24,
                        'pixel_width': 0,
                        'pixel_height': 0,
                    },
                }
            ]
    finally:
        connector.stop()
        await asyncio.gather(task, return_exceptions=True)
        await gateway.stop()
        await relay.stop()


def test_relay_host_connector_forwards_project_view_larger_than_legacy_frame_limit(
    tmp_path: Path,
) -> None:
    asyncio.run(
        _relay_host_connector_forwards_project_view_larger_than_legacy_frame_limit(
            tmp_path
        )
    )


async def _relay_host_connector_forwards_project_view_larger_than_legacy_frame_limit(
    tmp_path: Path,
) -> None:
    relay, issued = await _started_relay(tmp_path)
    gateway = await _started_gateway(project_view_bytes=128 * 1024)
    connector = RelayHostConnector(
        RelayHostConnectorConfig(
            relay_origin=_relay_origin(relay),
            gateway_origin=gateway.origin,
            host_id=issued.host_id,
            host_signing_key=issued.private_key,
            host_crypto_private_key=key_pair_from_private_bytes(bytes(range(101, 133))),
            tls_context=_client_ssl(),
            request_timeout_seconds=2.0,
        )
    )
    task = asyncio.create_task(connector.connect_once())
    try:
        await _wait_for(lambda: connector.diagnostics()['state'] == 'registered')
        async with aiohttp.ClientSession(raise_for_status=True) as client:
            phone = await client.ws_connect(relay.url('/v2/phone'), ssl=_client_ssl())
            phone_crypto, _ = await _open_phone_session(
                phone,
                issued=issued,
                relay_origin=issued.relay_audience,
                expected_host_public_key=public_key_b64(
                    connector.config.host_crypto_private_key
                ),
            )
            response = await _round_trip_gateway_request(
                phone,
                phone_crypto,
                session_id='relay-host-connector-session',
                outer_seq=2,
                operation='get_project_view',
                payload={
                    'request_id': 'request-project-view-large-1',
                    'project_id': 'project-demo',
                    'device_token': 'device-token-demo',
                },
            )

            assert response.payload['ok'] is True, response.payload
            assert len(response.payload['body']['padding']) == 128 * 1024
            assert gateway.requests == [('GET', '/v1/projects/project-demo/view')]
    finally:
        connector.stop()
        await asyncio.gather(task, return_exceptions=True)
        await gateway.stop()
        await relay.stop()


def test_relay_pair_claim_returns_host_signed_durable_access_grant(tmp_path: Path) -> None:
    asyncio.run(_relay_pair_claim_returns_host_signed_durable_access_grant(tmp_path))


async def _relay_pair_claim_returns_host_signed_durable_access_grant(tmp_path: Path) -> None:
    relay, issued = await _started_relay(tmp_path)
    gateway = await _started_gateway()
    host_crypto_key = key_pair_from_private_bytes(bytes(range(101, 133)))
    connector = RelayHostConnector(
        RelayHostConnectorConfig(
            relay_origin=_relay_origin(relay),
            gateway_origin=gateway.origin,
            host_id=issued.host_id,
            host_signing_key=issued.private_key,
            host_crypto_private_key=host_crypto_key,
            relay_audience=issued.relay_audience,
            tls_context=_client_ssl(),
            request_timeout_seconds=1.0,
        )
    )
    task = asyncio.create_task(connector.connect_once())
    phone_auth_key = ed25519.Ed25519PrivateKey.generate()
    phone_auth_pubkey_b64 = _b64(
        phone_auth_key.public_key().public_bytes(
            serialization.Encoding.Raw,
            serialization.PublicFormat.Raw,
        )
    )
    try:
        await _wait_for(lambda: connector.diagnostics()['state'] == 'registered')
        async with aiohttp.ClientSession(raise_for_status=True) as client:
            phone = await client.ws_connect(relay.url('/v2/phone'), ssl=_client_ssl())
            phone_crypto, _ = await _open_phone_session(
                phone,
                issued=issued,
                relay_origin=issued.relay_audience,
                expected_host_public_key=public_key_b64(host_crypto_key),
            )
            response = await _round_trip_gateway_request(
                phone,
                phone_crypto,
                session_id='relay-host-connector-session',
                outer_seq=2,
                operation='pair_claim',
                payload={
                    'request_id': 'pair-request-1',
                    'pairing_code': 'pair-once',
                    'device_name': 'Android Relay',
                    'phone_auth_pubkey_b64': phone_auth_pubkey_b64,
                },
            )
            assert response.payload['ok'] is True
            body = response.payload['body']
            profile = body['host_profile']
            grant = RelayAccessGrant.from_token(profile['relay_access_grant']).verify(
                host_public_key_b64=host_public_key_b64(issued.private_key),
                host_id=issued.host_id,
                device_id='device-paired',
                audience=issued.relay_audience,
            )
            assert grant.phone_auth_pubkey_b64 == phone_auth_pubkey_b64
            assert profile['route_provider'] == 'relay'
            assert profile['websocket_url'] == issued.relay_audience
            assert 'relay_reconnect' in profile['capabilities']
            assert gateway.request_bodies[-1] == {
                'pairing_code': 'pair-once',
                'device_name': 'Android Relay',
            }
    finally:
        connector.stop()
        await asyncio.gather(task, return_exceptions=True)
        await gateway.stop()
        await relay.stop()


async def _relay_host_connector_proxies_encrypted_gateway_request(tmp_path: Path) -> None:
    relay, issued = await _started_relay(tmp_path)
    gateway = await _started_gateway()
    host_crypto_key = key_pair_from_private_bytes(bytes(range(101, 133)))
    connector = RelayHostConnector(
        RelayHostConnectorConfig(
            relay_origin=_relay_origin(relay),
            gateway_origin=gateway.origin,
            host_id=issued.host_id,
            host_signing_key=issued.private_key,
            host_crypto_private_key=host_crypto_key,
            tls_context=_client_ssl(),
            request_timeout_seconds=1.0,
        )
    )
    task = asyncio.create_task(connector.connect_once())
    try:
        await _wait_for(lambda: connector.diagnostics()['state'] == 'registered')
        async with aiohttp.ClientSession(raise_for_status=True) as client:
            phone = await client.ws_connect(relay.url('/v2/phone'), ssl=_client_ssl())
            phone_crypto, host_hello = await _open_phone_session(
                phone,
                issued=issued,
                relay_origin=issued.relay_audience,
                expected_host_public_key=public_key_b64(host_crypto_key),
            )
            assert host_hello['payload']['server_fingerprint'] == host_fingerprint_for_public_key(
                public_key_b64(host_crypto_key)
            )
            assert (
                'get_agent_provider_control'
                in host_hello['payload']['unary_operations']
            )
            assert (
                'update_agent_provider_settings'
                in host_hello['payload']['unary_operations']
            )
            assert 'terminal' in host_hello['payload']['stream_operations']

            response = await _round_trip_gateway_request(
                phone,
                phone_crypto,
                session_id='relay-host-connector-session',
                outer_seq=2,
                operation='health',
                payload={'request_id': 'req-health-1'},
            )

            assert response.kind == 'response'
            assert response.request_id == 'req-health-1'
            assert response.payload['ok'] is True
            assert response.payload['status'] == 200
            assert response.payload['body']['status'] == 'ok'
            assert response.payload['body']['served_by'] == 'loopback-gateway'
            assert gateway.requests == [('GET', '/v1/health')]
            assert connector.diagnostics()['requests_proxied'] == 1
    finally:
        connector.stop()
        await asyncio.wait_for(task, timeout=2)
        await gateway.stop()
        await relay.stop()


def test_relay_host_connector_rejects_unallowlisted_gateway_request(tmp_path: Path) -> None:
    asyncio.run(_relay_host_connector_rejects_unallowlisted_gateway_request(tmp_path))


async def _relay_host_connector_rejects_unallowlisted_gateway_request(tmp_path: Path) -> None:
    relay, issued = await _started_relay(tmp_path)
    gateway = await _started_gateway()
    connector = RelayHostConnector(
        RelayHostConnectorConfig(
            relay_origin=_relay_origin(relay),
            gateway_origin=gateway.origin,
            host_id=issued.host_id,
            host_signing_key=issued.private_key,
            host_crypto_private_key=key_pair_from_private_bytes(bytes(range(101, 133))),
            tls_context=_client_ssl(),
            request_timeout_seconds=1.0,
        )
    )
    task = asyncio.create_task(connector.connect_once())
    try:
        await _wait_for(lambda: connector.diagnostics()['state'] == 'registered')
        async with aiohttp.ClientSession(raise_for_status=True) as client:
            phone = await client.ws_connect(relay.url('/v2/phone'), ssl=_client_ssl())
            phone_crypto, _host_hello = await _open_phone_session(
                phone,
                issued=issued,
                relay_origin=issued.relay_audience,
                expected_host_public_key=public_key_b64(connector.config.host_crypto_private_key),
            )
            response = await _round_trip_gateway_request(
                phone,
                phone_crypto,
                session_id='relay-host-connector-session',
                outer_seq=2,
                operation='raw_request',
                payload={
                    'method': 'GET',
                    'path': '/v1/projects/../../secrets',
                    'device_token': 'secret-token',
                },
            )

            assert response.kind == 'error'
            assert response.payload == {'code': 'operation_not_allowed'}
            assert gateway.requests == []
            assert connector.diagnostics()['requests_rejected'] == 1
    finally:
        connector.stop()
        await asyncio.wait_for(task, timeout=2)
        await gateway.stop()
        await relay.stop()


def test_relay_host_connector_keeps_device_token_out_of_mutation_body(tmp_path: Path) -> None:
    asyncio.run(_relay_host_connector_keeps_device_token_out_of_mutation_body(tmp_path))


async def _relay_host_connector_keeps_device_token_out_of_mutation_body(tmp_path: Path) -> None:
    relay, issued = await _started_relay(tmp_path)
    gateway = await _started_gateway()
    connector = RelayHostConnector(
        RelayHostConnectorConfig(
            relay_origin=_relay_origin(relay),
            gateway_origin=gateway.origin,
            host_id=issued.host_id,
            host_signing_key=issued.private_key,
            host_crypto_private_key=key_pair_from_private_bytes(bytes(range(101, 133))),
            tls_context=_client_ssl(),
            request_timeout_seconds=1.0,
        )
    )
    task = asyncio.create_task(connector.connect_once())
    try:
        await _wait_for(lambda: connector.diagnostics()['state'] == 'registered')
        async with aiohttp.ClientSession(raise_for_status=True) as client:
            phone = await client.ws_connect(relay.url('/v2/phone'), ssl=_client_ssl())
            phone_crypto, _ = await _open_phone_session(
                phone,
                issued=issued,
                relay_origin=issued.relay_audience,
                expected_host_public_key=public_key_b64(connector.config.host_crypto_private_key),
            )
            response = await _round_trip_gateway_request(
                phone,
                phone_crypto,
                session_id='relay-host-connector-session',
                outer_seq=2,
                operation='submit_agent_message',
                payload={
                    'request_id': 'request-submit-demo-1',
                    'project_id': 'project-demo',
                    'agent_name': 'worker1',
                    'namespace_epoch': 7,
                    'message': 'relay message',
                    'device_token': 'device-token-demo',
                },
            )

            assert response.kind == 'response'
            assert response.payload['ok'] is True
            assert gateway.request_bodies == [
                {
                    'project_id': 'project-demo',
                    'agent_name': 'worker1',
                    'namespace_epoch': 7,
                    'message': 'relay message',
                }
            ]
    finally:
        connector.stop()
        await asyncio.wait_for(task, timeout=2)
        await gateway.stop()
        await relay.stop()


def test_relay_host_connector_demultiplexes_concurrent_requests(tmp_path: Path) -> None:
    asyncio.run(_relay_host_connector_demultiplexes_concurrent_requests(tmp_path))


async def _relay_host_connector_demultiplexes_concurrent_requests(tmp_path: Path) -> None:
    relay, issued = await _started_relay(tmp_path)
    gateway = await _started_gateway()
    connector = RelayHostConnector(
        RelayHostConnectorConfig(
            relay_origin=_relay_origin(relay),
            gateway_origin=gateway.origin,
            host_id=issued.host_id,
            host_signing_key=issued.private_key,
            host_crypto_private_key=key_pair_from_private_bytes(bytes(range(101, 133))),
            tls_context=_client_ssl(),
            request_timeout_seconds=1.0,
        )
    )
    task = asyncio.create_task(connector.connect_once())
    try:
        await _wait_for(lambda: connector.diagnostics()['state'] == 'registered')
        async with aiohttp.ClientSession(raise_for_status=True) as client:
            phone = await client.ws_connect(relay.url('/v2/phone'), ssl=_client_ssl())
            phone_crypto, _ = await _open_phone_session(
                phone,
                issued=issued,
                relay_origin=issued.relay_audience,
                expected_host_public_key=public_key_b64(connector.config.host_crypto_private_key),
            )
            await _send_phone_inner(
                phone,
                phone_crypto,
                outer_seq=2,
                message=RelayInnerMessage(
                    kind='request',
                    request_id='request-health-slow-1',
                    operation='health',
                    payload={},
                ),
            )
            await _send_phone_inner(
                phone,
                phone_crypto,
                outer_seq=3,
                message=RelayInnerMessage(
                    kind='request',
                    request_id='request-device-fast-1',
                    operation='device',
                    payload={'device_token': 'device-token-demo'},
                ),
            )

            first = await _receive_phone_inner(phone, phone_crypto)
            second = await _receive_phone_inner(phone, phone_crypto)

            assert first.request_id == 'request-device-fast-1'
            assert first.payload['body']['device']['device_id'] == 'device-demo'
            assert second.request_id == 'request-health-slow-1'
            assert second.payload['body']['status'] == 'ok'
            assert set(gateway.requests) == {
                ('GET', '/v1/devices/me'),
                ('GET', '/v1/health'),
            }
    finally:
        connector.stop()
        await asyncio.wait_for(task, timeout=2)
        await gateway.stop()
        await relay.stop()


def test_relay_host_connector_rejects_reused_request_identity(tmp_path: Path) -> None:
    asyncio.run(_relay_host_connector_rejects_reused_request_identity(tmp_path))


async def _relay_host_connector_rejects_reused_request_identity(tmp_path: Path) -> None:
    relay, issued = await _started_relay(tmp_path)
    gateway = await _started_gateway()
    connector = RelayHostConnector(
        RelayHostConnectorConfig(
            relay_origin=_relay_origin(relay),
            gateway_origin=gateway.origin,
            host_id=issued.host_id,
            host_signing_key=issued.private_key,
            host_crypto_private_key=key_pair_from_private_bytes(bytes(range(101, 133))),
            tls_context=_client_ssl(),
            request_timeout_seconds=1.0,
        )
    )
    task = asyncio.create_task(connector.connect_once())
    try:
        await _wait_for(lambda: connector.diagnostics()['state'] == 'registered')
        async with aiohttp.ClientSession(raise_for_status=True) as client:
            phone = await client.ws_connect(relay.url('/v2/phone'), ssl=_client_ssl())
            phone_crypto, _ = await _open_phone_session(
                phone,
                issued=issued,
                relay_origin=issued.relay_audience,
                expected_host_public_key=public_key_b64(connector.config.host_crypto_private_key),
            )
            request_id = 'request-reused-demo-1'
            await _send_phone_inner(
                phone,
                phone_crypto,
                outer_seq=2,
                message=RelayInnerMessage(
                    kind='request',
                    request_id=request_id,
                    operation='health',
                    payload={},
                ),
            )
            await _send_phone_inner(
                phone,
                phone_crypto,
                outer_seq=3,
                message=RelayInnerMessage(
                    kind='request',
                    request_id=request_id,
                    operation='device',
                    payload={'device_token': 'device-token-demo'},
                ),
            )

            messages = [
                await _receive_phone_inner(phone, phone_crypto),
                await _receive_phone_inner(phone, phone_crypto),
            ]
            response = next(message for message in messages if message.kind == 'response')
            rejection = next(message for message in messages if message.kind == 'error')
            assert response.request_id == request_id
            assert response.payload['body']['status'] == 'ok'
            assert rejection.payload == {'code': 'bad_request'}
            assert gateway.requests == [('GET', '/v1/health')]
    finally:
        connector.stop()
        await asyncio.wait_for(task, timeout=2)
        await gateway.stop()
        await relay.stop()


def test_relay_host_connector_rejects_reused_stream_identity(tmp_path: Path) -> None:
    asyncio.run(_relay_host_connector_rejects_reused_stream_identity(tmp_path))


async def _relay_host_connector_rejects_reused_stream_identity(tmp_path: Path) -> None:
    relay, issued = await _started_relay(tmp_path)
    gateway = await _started_gateway()
    connector = RelayHostConnector(
        RelayHostConnectorConfig(
            relay_origin=_relay_origin(relay),
            gateway_origin=gateway.origin,
            host_id=issued.host_id,
            host_signing_key=issued.private_key,
            host_crypto_private_key=key_pair_from_private_bytes(bytes(range(101, 133))),
            tls_context=_client_ssl(),
            request_timeout_seconds=1.0,
        )
    )
    task = asyncio.create_task(connector.connect_once())
    try:
        await _wait_for(lambda: connector.diagnostics()['state'] == 'registered')
        async with aiohttp.ClientSession(raise_for_status=True) as client:
            phone = await client.ws_connect(relay.url('/v2/phone'), ssl=_client_ssl())
            phone_crypto, _ = await _open_phone_session(
                phone,
                issued=issued,
                relay_origin=issued.relay_audience,
                expected_host_public_key=public_key_b64(connector.config.host_crypto_private_key),
            )
            stream_id = 'stream-reused-demo-1'
            opened = RelayInnerMessage(
                kind='stream_open',
                stream_id=stream_id,
                operation='terminal',
                credit_bytes=RELAY_STREAM_INITIAL_WINDOW_BYTES,
                payload={
                    'terminal_id': 'term-demo',
                    'terminal_token': 'terminal-token-demo',
                },
            )
            await _send_phone_inner(phone, phone_crypto, outer_seq=2, message=opened)
            await _send_phone_inner(phone, phone_crypto, outer_seq=3, message=opened)

            rejection = await _receive_until_kind(phone, phone_crypto, 'error')
            assert rejection.stream_id == stream_id
            assert rejection.payload == {'code': 'stream_conflict'}
            await _wait_for(
                lambda: gateway.requests.count(('GET', '/v1/terminals/term-demo')) == 1
            )
            await _send_phone_inner(
                phone,
                phone_crypto,
                outer_seq=4,
                message=RelayInnerMessage(
                    kind='stream_cancel',
                    stream_id=stream_id,
                    payload={},
                ),
            )
    finally:
        connector.stop()
        await asyncio.wait_for(task, timeout=2)
        await gateway.stop()
        await relay.stop()


def test_relay_host_connector_revoked_host_reports_auth_diagnostic(tmp_path: Path) -> None:
    asyncio.run(_relay_host_connector_revoked_host_reports_auth_diagnostic(tmp_path))


def test_relay_host_connector_reconnects_after_transient_socket_loss(tmp_path: Path) -> None:
    asyncio.run(_relay_host_connector_reconnects_after_transient_socket_loss(tmp_path))


async def _relay_host_connector_reconnects_after_transient_socket_loss(
    tmp_path: Path,
) -> None:
    relay, issued = await _started_relay(tmp_path)
    connector = RelayHostConnector(
        RelayHostConnectorConfig(
            relay_origin=_relay_origin(relay),
            gateway_origin='http://127.0.0.1:8787',
            host_id=issued.host_id,
            host_signing_key=issued.private_key,
            host_crypto_private_key=key_pair_from_private_bytes(bytes(range(101, 133))),
            tls_context=_client_ssl(),
            request_timeout_seconds=1.0,
            min_reconnect_delay_seconds=0.05,
            max_reconnect_delay_seconds=0.1,
        )
    )
    task = asyncio.create_task(connector.run_forever())
    try:
        await _wait_for(lambda: connector.diagnostics()['state'] == 'registered')
        first_connections = int(relay.metrics_snapshot()['host_connections'])
        endpoint = relay._hosts[issued.host_id]
        await endpoint.close(code=1012, message='test_transient_loss')
        await _wait_for(lambda: relay.metrics_snapshot()['active_hosts'] == 0)
        await _wait_for(
            lambda: connector.diagnostics()['state'] == 'registered'
            and int(relay.metrics_snapshot()['host_connections']) > first_connections,
            timeout=3.0,
        )
        assert not task.done()
    finally:
        connector.stop()
        await asyncio.gather(task, return_exceptions=True)
        await relay.stop()


async def _relay_host_connector_revoked_host_reports_auth_diagnostic(tmp_path: Path) -> None:
    relay, issued = await _started_relay(tmp_path)
    issued.store.revoke_host(issued.host_id, reason='test revoke before connect')
    connector = RelayHostConnector(
        RelayHostConnectorConfig(
            relay_origin=_relay_origin(relay),
            gateway_origin='http://127.0.0.1:8787',
            host_id=issued.host_id,
            host_signing_key=issued.private_key,
            host_crypto_private_key=key_pair_from_private_bytes(bytes(range(101, 133))),
            tls_context=_client_ssl(),
            request_timeout_seconds=1.0,
        )
    )
    try:
        with pytest.raises(
            RelayHostConnectorError,
            match='authentication rejected',
        ):
            await connector.connect_once()
        assert connector.diagnostics()['state'] == 'auth_rejected'
        assert connector.diagnostics()['last_error_code'] == 'relay_auth_rejected'
    finally:
        connector.stop()
        await relay.stop()


def test_relay_host_connector_streams_terminal_without_replaying_input(tmp_path: Path) -> None:
    asyncio.run(_relay_host_connector_streams_terminal_without_replaying_input(tmp_path))


def test_relay_host_connector_streams_terminal_frame_above_legacy_window(
    tmp_path: Path,
) -> None:
    asyncio.run(
        _relay_host_connector_streams_terminal_frame_above_legacy_window(tmp_path)
    )


async def _relay_host_connector_streams_terminal_frame_above_legacy_window(
    tmp_path: Path,
) -> None:
    relay, issued = await _started_relay(tmp_path)
    gateway = await _started_gateway(terminal_output_bytes=220 * 1024)
    connector = RelayHostConnector(
        RelayHostConnectorConfig(
            relay_origin=_relay_origin(relay),
            gateway_origin=gateway.origin,
            host_id=issued.host_id,
            host_signing_key=issued.private_key,
            host_crypto_private_key=key_pair_from_private_bytes(bytes(range(101, 133))),
            tls_context=_client_ssl(),
            request_timeout_seconds=1.0,
            stream_write_timeout_seconds=0.5,
        )
    )
    task = asyncio.create_task(connector.connect_once())
    try:
        await _wait_for(lambda: connector.diagnostics()['state'] == 'registered')
        async with aiohttp.ClientSession(raise_for_status=True) as client:
            phone = await client.ws_connect(relay.url('/v2/phone'), ssl=_client_ssl())
            phone_crypto, _ = await _open_phone_session(
                phone,
                issued=issued,
                relay_origin=issued.relay_audience,
                expected_host_public_key=public_key_b64(
                    connector.config.host_crypto_private_key
                ),
            )
            stream_id = 'terminal-large-history-1'
            await _send_phone_inner(
                phone,
                phone_crypto,
                outer_seq=2,
                message=RelayInnerMessage(
                    kind='stream_open',
                    stream_id=stream_id,
                    operation='terminal',
                    credit_bytes=RELAY_STREAM_INITIAL_WINDOW_BYTES,
                    payload={
                        'terminal_id': 'term-demo',
                        'terminal_token': 'terminal-token-demo',
                    },
                ),
            )

            opening = await _receive_until_kind(phone, phone_crypto, 'stream_data')
            output = await _receive_until_kind(phone, phone_crypto, 'stream_data')
            assert opening.payload['frame']['type'] == 'open'
            assert output.payload['frame']['type'] == 'output'
            output_size = relay_inner_payload_size(output.payload)
            assert output_size > 256 * 1024
            assert output_size <= RELAY_STREAM_MAX_MESSAGE_BYTES
            assert len(_b64decode(str(output.payload['frame']['bytes_b64']))) == 220 * 1024
    finally:
        connector.stop()
        await asyncio.wait_for(task, timeout=2)
        await gateway.stop()
        await relay.stop()


async def _relay_host_connector_streams_terminal_without_replaying_input(tmp_path: Path) -> None:
    relay, issued = await _started_relay(tmp_path)
    gateway = await _started_gateway()
    connector = RelayHostConnector(
        RelayHostConnectorConfig(
            relay_origin=_relay_origin(relay),
            gateway_origin=gateway.origin,
            host_id=issued.host_id,
            host_signing_key=issued.private_key,
            host_crypto_private_key=key_pair_from_private_bytes(bytes(range(101, 133))),
            tls_context=_client_ssl(),
            request_timeout_seconds=1.0,
        )
    )
    task = asyncio.create_task(connector.connect_once())
    try:
        await _wait_for(lambda: connector.diagnostics()['state'] == 'registered')
        async with aiohttp.ClientSession(raise_for_status=True) as client:
            phone = await client.ws_connect(relay.url('/v2/phone'), ssl=_client_ssl())
            phone_crypto, _ = await _open_phone_session(
                phone,
                issued=issued,
                relay_origin=issued.relay_audience,
                expected_host_public_key=public_key_b64(connector.config.host_crypto_private_key),
            )
            stream_id = 'terminal-stream-demo-1'
            opening_payload = {
                'frame': {
                    'type': 'open',
                    'terminal_id': 'term-demo',
                    'resume_cursor': 0,
                    'last_input_seq': 0,
                }
            }
            opening_credit = relay_inner_payload_size(opening_payload)
            await _send_phone_inner(
                phone,
                phone_crypto,
                outer_seq=2,
                message=RelayInnerMessage(
                    kind='stream_open',
                    stream_id=stream_id,
                    operation='terminal',
                    credit_bytes=opening_credit,
                    payload={
                        'terminal_id': 'term-demo',
                        'terminal_token': 'terminal-token-demo',
                    },
                ),
            )
            opening = await _receive_until_kind(phone, phone_crypto, 'stream_data')
            assert opening.stream_id == stream_id
            assert opening.payload['frame']['type'] == 'open'
            await _receive_until_kind(phone, phone_crypto, 'stream_window')

            input_frame = {
                'type': 'input',
                'seq': 1,
                'data_b64': _b64(b'MOBILE_STREAM_INPUT'),
            }
            await _send_phone_inner(
                phone,
                phone_crypto,
                outer_seq=3,
                message=RelayInnerMessage(
                    kind='stream_data',
                    stream_id=stream_id,
                    payload={'frame': input_frame},
                ),
            )
            await _wait_for(lambda: gateway.terminal_inputs == [input_frame])
            output_payload = {
                'frame': {
                    'type': 'output',
                    'seq': 1,
                    'data_b64': input_frame['data_b64'],
                }
            }
            await _send_phone_inner(
                phone,
                phone_crypto,
                outer_seq=4,
                message=RelayInnerMessage(
                    kind='stream_window',
                    stream_id=stream_id,
                    credit_bytes=relay_inner_payload_size(output_payload),
                    payload={},
                ),
            )
            output = await _receive_until_kind(phone, phone_crypto, 'stream_data')
            assert output.payload['frame']['type'] == 'output'
            assert base64.urlsafe_b64decode(
                str(output.payload['frame']['data_b64']) + '=='
            ) == b'MOBILE_STREAM_INPUT'
            resize_frame = {
                'type': 'resize',
                'seq': 2,
                'cols': 100,
                'rows': 32,
            }
            await _send_phone_inner(
                phone,
                phone_crypto,
                outer_seq=5,
                message=RelayInnerMessage(
                    kind='stream_data',
                    stream_id=stream_id,
                    payload={'frame': resize_frame},
                ),
            )
            await _wait_for(
                lambda: gateway.terminal_inputs == [input_frame, resize_frame]
            )
            await _send_phone_inner(
                phone,
                phone_crypto,
                outer_seq=6,
                message=RelayInnerMessage(
                    kind='stream_cancel',
                    stream_id=stream_id,
                    payload={},
                ),
            )
            await _receive_until_kind(phone, phone_crypto, 'stream_close')
            assert gateway.terminal_inputs == [input_frame, resize_frame]
    finally:
        connector.stop()
        await asyncio.wait_for(task, timeout=2)
        await gateway.stop()
        await relay.stop()
    _assert_canary_not_persisted(tmp_path, 'MOBILE_STREAM_INPUT')


def test_relay_host_connector_notification_stream_resumes_without_duplicates(tmp_path: Path) -> None:
    asyncio.run(_relay_host_connector_notification_stream_resumes_without_duplicates(tmp_path))


@pytest.mark.parametrize(
    'content_size',
    (220 * 1024, 25 * 1024 * 1024),
    ids=('multi-frame', 'configured-upload-limit'),
)
def test_relay_host_connector_streams_files_beyond_frame_limit(
    tmp_path: Path,
    content_size: int,
) -> None:
    asyncio.run(
        _relay_host_connector_streams_files_beyond_frame_limit(
            tmp_path,
            content_size=content_size,
        )
    )


def test_relay_host_connector_limits_buffered_uploads_per_phone_session(
    tmp_path: Path,
) -> None:
    asyncio.run(_relay_host_connector_limits_buffered_uploads_per_phone_session(tmp_path))


async def _relay_host_connector_limits_buffered_uploads_per_phone_session(
    tmp_path: Path,
) -> None:
    relay, issued = await _started_relay(tmp_path)
    gateway = await _started_gateway()
    host_crypto_key = key_pair_from_private_bytes(bytes(range(101, 133)))
    connector = RelayHostConnector(
        RelayHostConnectorConfig(
            relay_origin=_relay_origin(relay),
            gateway_origin=gateway.origin,
            host_id=issued.host_id,
            host_signing_key=issued.private_key,
            host_crypto_private_key=host_crypto_key,
            tls_context=_client_ssl(),
            request_timeout_seconds=1.0,
        )
    )
    task = asyncio.create_task(connector.connect_once())
    try:
        await _wait_for(lambda: connector.diagnostics()['state'] == 'registered')
        async with aiohttp.ClientSession(raise_for_status=True) as client:
            phone = await client.ws_connect(relay.url('/v2/phone'), ssl=_client_ssl())
            phone_crypto, _ = await _open_phone_session(
                phone,
                issued=issued,
                relay_origin=issued.relay_audience,
                expected_host_public_key=public_key_b64(host_crypto_key),
            )
            metadata = {
                'project_id': 'project-demo',
                'agent': 'worker1',
                'file_name': 'bounded.bin',
                'mime_type': 'application/octet-stream',
                'device_token': 'device-token-demo',
            }
            await _send_phone_inner(
                phone,
                phone_crypto,
                outer_seq=2,
                message=RelayInnerMessage(
                    kind='stream_open',
                    stream_id='upload-buffered-1',
                    operation='file_upload',
                    credit_bytes=RELAY_STREAM_INITIAL_WINDOW_BYTES,
                    payload=metadata,
                ),
            )
            assert (await _receive_phone_inner(phone, phone_crypto)).kind == 'stream_window'
            await _send_phone_inner(
                phone,
                phone_crypto,
                outer_seq=3,
                message=RelayInnerMessage(
                    kind='stream_open',
                    stream_id='upload-buffered-2',
                    operation='file_upload',
                    credit_bytes=RELAY_STREAM_INITIAL_WINDOW_BYTES,
                    payload=metadata,
                ),
            )
            conflict = await _receive_phone_inner(phone, phone_crypto)
            assert conflict.kind == 'error'
            assert conflict.stream_id == 'upload-buffered-2'
            assert conflict.payload == {'code': 'stream_conflict'}
            await _send_phone_inner(
                phone,
                phone_crypto,
                outer_seq=4,
                message=RelayInnerMessage(
                    kind='stream_cancel',
                    stream_id='upload-buffered-1',
                    payload={},
                ),
            )
            assert (await _receive_phone_inner(phone, phone_crypto)).kind == 'stream_close'
    finally:
        connector.stop()
        await asyncio.gather(task, return_exceptions=True)
        await gateway.stop()
        await relay.stop()


async def _relay_host_connector_streams_files_beyond_frame_limit(
    tmp_path: Path,
    *,
    content_size: int,
) -> None:
    relay, issued = await _started_relay(tmp_path)
    gateway = await _started_gateway()
    host_crypto_key = key_pair_from_private_bytes(bytes(range(101, 133)))
    connector = RelayHostConnector(
        RelayHostConnectorConfig(
            relay_origin=_relay_origin(relay),
            gateway_origin=gateway.origin,
            host_id=issued.host_id,
            host_signing_key=issued.private_key,
            host_crypto_private_key=host_crypto_key,
            tls_context=_client_ssl(),
            request_timeout_seconds=2.0,
        )
    )
    task = asyncio.create_task(connector.connect_once())
    content = bytes((index % 251 for index in range(content_size)))
    try:
        await _wait_for(lambda: connector.diagnostics()['state'] == 'registered')
        async with aiohttp.ClientSession(raise_for_status=True) as client:
            phone = await client.ws_connect(relay.url('/v2/phone'), ssl=_client_ssl())
            phone_crypto, _ = await _open_phone_session(
                phone,
                issued=issued,
                relay_origin=issued.relay_audience,
                expected_host_public_key=public_key_b64(host_crypto_key),
            )

            async def receive_stream_message() -> RelayInnerMessage:
                # This is a capacity and integrity test, not a latency test. A
                # loaded WSL runner can need more than the two-second default
                # for one of the hundreds of frames in the 25 MiB case.
                return await _receive_phone_inner(
                    phone,
                    phone_crypto,
                    timeout=10.0,
                )

            outer_seq = 2
            upload_id = 'upload-stream-demo'
            await _send_phone_inner(
                phone,
                phone_crypto,
                outer_seq=outer_seq,
                message=RelayInnerMessage(
                    kind='stream_open',
                    stream_id=upload_id,
                    operation='file_upload',
                    credit_bytes=RELAY_STREAM_INITIAL_WINDOW_BYTES,
                    payload={
                        'project_id': 'project-demo',
                        'agent': 'worker1',
                        'file_name': 'large.bin',
                        'mime_type': 'application/octet-stream',
                        'device_token': 'device-token-demo',
                    },
                ),
            )
            outer_seq += 1
            assert (await receive_stream_message()).kind == 'stream_window'
            for offset in range(0, len(content), 32 * 1024):
                payload = {'chunk_b64': _b64(content[offset : offset + 32 * 1024])}
                await _send_phone_inner(
                    phone,
                    phone_crypto,
                    outer_seq=outer_seq,
                    message=RelayInnerMessage(
                        kind='stream_data',
                        stream_id=upload_id,
                        payload=payload,
                    ),
                )
                outer_seq += 1
                assert (await receive_stream_message()).kind == 'stream_window'
            await _send_phone_inner(
                phone,
                phone_crypto,
                outer_seq=outer_seq,
                message=RelayInnerMessage(
                    kind='stream_data',
                    stream_id=upload_id,
                    payload={'eof': True},
                ),
            )
            outer_seq += 1
            assert (await receive_stream_message()).kind == 'stream_window'
            upload_result = await receive_stream_message()
            assert upload_result.payload['result']['ok'] is True, upload_result.payload
            assert (await receive_stream_message()).kind == 'stream_close'
            assert gateway.uploaded_files['file-demo'] == content

            download_id = 'download-stream-demo'
            await _send_phone_inner(
                phone,
                phone_crypto,
                outer_seq=outer_seq,
                message=RelayInnerMessage(
                    kind='stream_open',
                    stream_id=download_id,
                    operation='file_download',
                    credit_bytes=RELAY_STREAM_INITIAL_WINDOW_BYTES,
                    payload={
                        'project_id': 'project-demo',
                        'agent': 'worker1',
                        'file_id': 'file-demo',
                        'device_token': 'device-token-demo',
                    },
                ),
            )
            outer_seq += 1
            downloaded = bytearray()
            while True:
                message = await receive_stream_message()
                if message.kind == 'stream_close':
                    break
                assert message.kind == 'stream_data'
                if chunk := message.payload.get('chunk_b64'):
                    downloaded.extend(_b64decode(str(chunk)))
                await _send_phone_inner(
                    phone,
                    phone_crypto,
                    outer_seq=outer_seq,
                    message=RelayInnerMessage(
                        kind='stream_window',
                        stream_id=download_id,
                        credit_bytes=relay_inner_payload_size(message.payload),
                        payload={},
                    ),
                )
                outer_seq += 1
            assert bytes(downloaded) == content
    finally:
        connector.stop()
        await asyncio.gather(task, return_exceptions=True)
        await gateway.stop()
        await relay.stop()


async def _relay_host_connector_notification_stream_resumes_without_duplicates(tmp_path: Path) -> None:
    relay, issued = await _started_relay(tmp_path)
    gateway = await _started_gateway()
    connector = RelayHostConnector(
        RelayHostConnectorConfig(
            relay_origin=_relay_origin(relay),
            gateway_origin=gateway.origin,
            host_id=issued.host_id,
            host_signing_key=issued.private_key,
            host_crypto_private_key=key_pair_from_private_bytes(bytes(range(101, 133))),
            tls_context=_client_ssl(),
            request_timeout_seconds=1.0,
        )
    )
    cursor_before_delivery: list[str | None] = []
    send_stream_payload = connector._send_stream_payload

    async def track_cursor_before_delivery(
        ws,
        session_id,
        session,
        state,
        payload,
    ) -> None:
        if 'event' in payload:
            cursor_before_delivery.append(state.last_event_id)
        await send_stream_payload(ws, session_id, session, state, payload)

    connector._send_stream_payload = track_cursor_before_delivery
    task = asyncio.create_task(connector.connect_once())
    try:
        await _wait_for(lambda: connector.diagnostics()['state'] == 'registered')
        async with aiohttp.ClientSession(raise_for_status=True) as client:
            phone = await client.ws_connect(relay.url('/v2/phone'), ssl=_client_ssl())
            phone_crypto, _ = await _open_phone_session(
                phone,
                issued=issued,
                relay_origin=issued.relay_audience,
                expected_host_public_key=public_key_b64(connector.config.host_crypto_private_key),
            )
            stream_id = 'notification-stream-demo-1'
            await _send_phone_inner(
                phone,
                phone_crypto,
                outer_seq=2,
                message=RelayInnerMessage(
                    kind='stream_open',
                    stream_id=stream_id,
                    operation='notifications',
                    credit_bytes=RELAY_STREAM_INITIAL_WINDOW_BYTES,
                    payload={'device_token': 'device-token-demo'},
                ),
            )
            received_ids: list[str] = []
            while len(received_ids) < 2:
                message = await _receive_phone_inner(phone, phone_crypto, timeout=3.0)
                if message.kind != 'stream_data':
                    continue
                event = message.payload['event']
                received_ids.append(str(event['id']))
                await _send_phone_inner(
                    phone,
                    phone_crypto,
                    outer_seq=2 + len(received_ids),
                    message=RelayInnerMessage(
                        kind='stream_window',
                        stream_id=stream_id,
                        credit_bytes=relay_inner_payload_size(message.payload),
                        payload={},
                    ),
                )
            assert received_ids == ['evt-1', 'evt-2']
            assert gateway.notification_cursors[:3] == [None, 'evt-1', 'evt-1']
            assert cursor_before_delivery == [None, 'evt-1']
            await _send_phone_inner(
                phone,
                phone_crypto,
                outer_seq=5,
                message=RelayInnerMessage(
                    kind='stream_cancel',
                    stream_id=stream_id,
                    payload={},
                ),
            )
    finally:
        connector.stop()
        await asyncio.wait_for(task, timeout=2)
        await gateway.stop()
        await relay.stop()


async def _round_trip_gateway_request(
    phone: aiohttp.ClientWebSocketResponse,
    phone_crypto,
    *,
    session_id: str,
    outer_seq: int,
    operation: str,
    payload: dict[str, object],
) -> RelayInnerMessage:
    inner = {
        'schema_version': 1,
        'kind': 'request',
        'request_id': str(payload.pop('request_id', 'request-demo-1')),
        'operation': operation,
        'payload': payload,
    }
    envelope = phone_crypto.seal(
        op='relay.inner.v1',
        plaintext=json.dumps(inner, sort_keys=True, separators=(',', ':')).encode('utf-8'),
    )
    await phone.send_json(
        {
            'schema_version': 2,
            'session_id': session_id,
            'seq': outer_seq,
            'kind': 'gateway_envelope',
            'payload': {'envelope': envelope.to_json()},
        }
    )
    response_frame = await phone.receive_json()
    assert response_frame['kind'] == 'gateway_envelope'
    response_envelope = RelayV2Envelope.from_json(response_frame['payload']['envelope'])
    plaintext = phone_crypto.open(response_envelope)
    return RelayInnerMessage.from_bytes(plaintext)


async def _send_phone_inner(
    phone: aiohttp.ClientWebSocketResponse,
    phone_crypto,
    *,
    outer_seq: int,
    message: RelayInnerMessage,
) -> None:
    envelope = phone_crypto.seal(op='relay.inner.v1', plaintext=message.to_bytes())
    await phone.send_json(
        {
            'schema_version': 2,
            'session_id': 'relay-host-connector-session',
            'seq': outer_seq,
            'kind': 'gateway_envelope',
            'payload': {'envelope': envelope.to_json()},
        }
    )


async def _receive_phone_inner(
    phone: aiohttp.ClientWebSocketResponse,
    phone_crypto,
    *,
    timeout: float = 2.0,
) -> RelayInnerMessage:
    response_frame = await asyncio.wait_for(phone.receive_json(), timeout=timeout)
    assert response_frame['kind'] == 'gateway_envelope'
    envelope = RelayV2Envelope.from_json(response_frame['payload']['envelope'])
    assert envelope.op == 'relay.inner.v1'
    return RelayInnerMessage.from_bytes(phone_crypto.open(envelope))


async def _receive_until_kind(
    phone: aiohttp.ClientWebSocketResponse,
    phone_crypto,
    kind: str,
) -> RelayInnerMessage:
    for _ in range(8):
        message = await _receive_phone_inner(phone, phone_crypto)
        if message.kind == kind:
            return message
    raise AssertionError(f'relay inner message kind not received: {kind}')


async def _open_phone_session(
    phone: aiohttp.ClientWebSocketResponse,
    *,
    issued: '_IssuedHost',
    relay_origin: str,
    expected_host_public_key: str,
):
    session_id = 'relay-host-connector-session'
    client_private = key_pair_from_private_bytes(bytes(range(1, 33)))
    client_public = public_key_b64(client_private)
    phone_nonce_b64 = _b64(b'fresh phone nonce for host connector test')
    rendezvous = issue_host_rendezvous_capability(
        issued.private_key,
        host_id=issued.host_id,
        session_id=session_id,
        client_pubkey_b64=client_public,
        phone_nonce_b64=phone_nonce_b64,
        audience=relay_origin,
        expires_at=int(time.time()) + 30,
    )
    await phone.send_json(
        {
            'schema_version': 2,
            'session_id': session_id,
            'seq': 1,
            'kind': 'client_hello',
            'payload': {
                'host_id': issued.host_id,
                'device_id': 'device-relay-host-connector',
                'client_pubkey_b64': client_public,
                'phone_nonce_b64': phone_nonce_b64,
                'supported_versions': [2],
                'rendezvous_capability': rendezvous,
            },
        }
    )
    host_hello = await phone.receive_json()
    assert host_hello['kind'] == 'host_hello'
    assert host_hello['payload']['host_pubkey_b64'] == expected_host_public_key
    schedule = derive_relay_v2_key_schedule(
        local_private_key=client_private,
        peer_public_key_b64=host_hello['payload']['host_pubkey_b64'],
        role='phone',
        session_id=session_id,
        client_public_key_b64=client_public,
        host_public_key_b64=host_hello['payload']['host_pubkey_b64'],
        expected_host_fingerprint=host_hello['payload']['server_fingerprint'],
    )
    return schedule.session(role='phone'), host_hello


@dataclass
class _IssuedHost:
    store: RelayAdmissionStore
    host_id: str
    private_key: Any
    relay_audience: str = 'wss://relay.seemlab.top'


async def _started_relay(tmp_path: Path) -> tuple[ProductionRelayService, _IssuedHost]:
    cert_path, key_path = _write_self_signed_cert(tmp_path)
    store = RelayAdmissionStore(
        tmp_path / 'relay.sqlite3',
        admission_secrets=RelayAdmissionSecrets.generate_for_testing(),
    )
    host_private = generate_host_private_key()
    invitation = store.issue_invitation(ttl_seconds=600, max_sessions=4)
    credential = store.claim_invitation(
        invitation.invitation,
        host_public_key_b64=host_public_key_b64(host_private),
    )
    service = ProductionRelayService(
        ProductionRelayConfig(
            listen_host='127.0.0.1',
            listen_port=0,
            admin_host='127.0.0.1',
            admin_port=0,
            tls_cert_file=cert_path,
            tls_key_file=key_path,
            admission_db_path=tmp_path / 'relay.sqlite3',
            state_dir=tmp_path / 'state',
            handshake_timeout=2.0,
            idle_timeout=5.0,
            write_timeout=1.0,
        ),
        admission_store=store,
    )
    await service.start()
    return service, _IssuedHost(store=store, host_id=credential.host_id, private_key=host_private)


@dataclass
class _GatewayStub:
    origin: str
    runner: web.AppRunner
    site: web.TCPSite
    requests: list[tuple[str, str]]
    request_bodies: list[dict[str, object]]
    terminal_inputs: list[dict[str, object]]
    notification_cursors: list[str | None]
    uploaded_files: dict[str, bytes]

    async def stop(self) -> None:
        await self.runner.cleanup()


async def _started_gateway(
    *,
    project_view_bytes: int = 0,
    terminal_output_bytes: int = 0,
) -> _GatewayStub:
    requests: list[tuple[str, str]] = []
    request_bodies: list[dict[str, object]] = []
    terminal_inputs: list[dict[str, object]] = []
    notification_cursors: list[str | None] = []
    uploaded_files: dict[str, bytes] = {}

    async def health(request: web.Request) -> web.Response:
        await asyncio.sleep(0.05)
        requests.append((request.method, request.path))
        return web.json_response({'schema_version': 1, 'status': 'ok', 'served_by': 'loopback-gateway'})

    async def device(request: web.Request) -> web.Response:
        requests.append((request.method, request.path))
        assert request.headers['authorization'] == 'Bearer device-token-demo'
        return web.json_response(
            {
                'schema_version': 1,
                'device': {
                    'device_id': 'device-demo',
                    'project_id': 'project-demo',
                    'route_provider': 'relay',
                    'scopes': ['view'],
                },
            }
        )

    async def submit_message(request: web.Request) -> web.Response:
        requests.append((request.method, request.path))
        assert request.headers['authorization'] == 'Bearer device-token-demo'
        body = await request.json()
        assert isinstance(body, dict)
        request_bodies.append({str(key): value for key, value in body.items()})
        return web.json_response(
            {
                'schema_version': 1,
                'accepted': True,
                'project_id': 'project-demo',
                'agent': 'worker1',
            }
        )

    async def project_view(request: web.Request) -> web.Response:
        requests.append((request.method, request.path))
        assert request.headers['authorization'] == 'Bearer device-token-demo'
        return web.json_response(
            {
                'schema_version': 1,
                'project': {'id': 'project-demo'},
                'padding': 'x' * project_view_bytes,
            }
        )

    async def pair_claim(request: web.Request) -> web.Response:
        requests.append((request.method, request.path))
        body = await request.json()
        assert isinstance(body, dict)
        request_bodies.append({str(key): value for key, value in body.items()})
        return web.json_response(
            {
                'schema_version': 1,
                'device_token': 'paired-device-secret',
                'device': {
                    'device_id': 'device-paired',
                    'project_id': 'project-demo',
                    'scopes': ['view', 'notify', 'terminal_input'],
                },
                'host_profile': {
                    'project_id': 'project-demo',
                    'device_id': 'device-paired',
                    'scopes': ['view', 'notify', 'terminal_input'],
                },
            },
            status=201,
        )

    async def open_terminal(request: web.Request) -> web.Response:
        requests.append((request.method, request.path))
        assert request.headers['authorization'] == 'Bearer device-token-demo'
        body = await request.json()
        assert isinstance(body, dict)
        request_bodies.append({str(key): value for key, value in body.items()})
        return web.json_response(
            {
                'terminal_id': 'term-demo',
                'terminal_token': 'terminal-token-demo',
                'expires_at': '2026-07-24T01:00:00Z',
                'websocket_url': 'ws://loopback.invalid/v1/terminals/term-demo',
                'target_epoch': 7,
                'target_summary': {
                    'project_id': 'project-demo',
                    'agent': 'worker1',
                    'window': 'main',
                },
            },
            status=201,
        )

    async def terminal(request: web.Request) -> web.WebSocketResponse:
        requests.append((request.method, request.path))
        websocket = web.WebSocketResponse(max_msg_size=512 * 1024)
        await websocket.prepare(request)
        opening = await websocket.receive_json()
        assert opening == {
            'type': 'open',
            'terminal_id': 'term-demo',
            'token': 'terminal-token-demo',
        }
        await websocket.send_json(
            {
                'type': 'open',
                'terminal_id': 'term-demo',
                'resume_cursor': 0,
                'last_input_seq': 0,
            }
        )
        if terminal_output_bytes > 0:
            await websocket.send_json(
                {
                    'type': 'output',
                    'seq': 1,
                    'bytes_b64': _b64(b'h' * terminal_output_bytes),
                }
            )
        async for incoming in websocket:
            if incoming.type != aiohttp.WSMsgType.TEXT:
                continue
            frame = json.loads(incoming.data)
            assert isinstance(frame, dict)
            terminal_inputs.append(frame)
            if frame.get('type') == 'input':
                await websocket.send_json(
                    {
                        'type': 'output',
                        'seq': 1,
                        'data_b64': frame['data_b64'],
                    }
                )
        return websocket

    async def notifications(request: web.Request) -> web.StreamResponse:
        cursor = request.headers.get('Last-Event-ID')
        attempt = len(notification_cursors)
        notification_cursors.append(cursor)
        event_id = 'evt-1' if attempt < 2 else 'evt-2'
        response = web.StreamResponse(
            status=200,
            headers={'Content-Type': 'text/event-stream'},
        )
        await response.prepare(request)
        await response.write(b'retry: 10\n\n')
        first = '{"kind":"task_completed",'
        second = f'"event_id":"{event_id}"}}'
        await response.write(f'id: {event_id}\n'.encode())
        await response.write(b'event: task_completed\n')
        await response.write(b'retry: 10\n')
        await response.write(f'data: {first}\n'.encode())
        await response.write(f'data: {second}\n\n'.encode())
        await response.write_eof()
        return response

    async def upload_file(request: web.Request) -> web.Response:
        requests.append((request.method, request.path))
        assert request.headers['authorization'] == 'Bearer device-token-demo'
        body = await request.read()
        uploaded_files['file-demo'] = body
        return web.json_response(
            {
                'file_id': 'file-demo',
                'file_name': request.headers['X-Ccb-File-Name'],
                'mime_type': request.content_type,
                'size_bytes': len(body),
            },
            status=201,
        )

    async def download_file(request: web.Request) -> web.Response:
        requests.append((request.method, request.path))
        assert request.headers['authorization'] == 'Bearer device-token-demo'
        return web.Response(
            body=uploaded_files['file-demo'],
            content_type='application/octet-stream',
        )

    app = web.Application(client_max_size=(25 * 1024 * 1024) + 1)
    app.router.add_get('/v1/health', health)
    app.router.add_post('/v1/pairing/claim', pair_claim)
    app.router.add_get('/v1/devices/me', device)
    app.router.add_post(
        '/v1/projects/project-demo/agents/worker1/messages',
        submit_message,
    )
    app.router.add_get('/v1/projects/project-demo/view', project_view)
    app.router.add_post(
        '/v1/projects/project-demo/terminals',
        open_terminal,
    )
    app.router.add_get('/v1/terminals/term-demo', terminal)
    app.router.add_get('/v1/mobile/notifications', notifications)
    app.router.add_post(
        '/v1/projects/project-demo/agents/worker1/files',
        upload_file,
    )
    app.router.add_get(
        '/v1/projects/project-demo/agents/worker1/files/file-demo',
        download_file,
    )
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, '127.0.0.1', 0)
    await site.start()
    sockets = site._server.sockets
    assert sockets
    port = sockets[0].getsockname()[1]
    return _GatewayStub(
        origin=f'http://127.0.0.1:{port}',
        runner=runner,
        site=site,
        requests=requests,
        request_bodies=request_bodies,
        terminal_inputs=terminal_inputs,
        notification_cursors=notification_cursors,
        uploaded_files=uploaded_files,
    )


async def _wait_for(predicate, *, timeout: float = 5.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return
        await asyncio.sleep(0.02)
    assert predicate()


def _relay_origin(service: ProductionRelayService) -> str:
    return service.url('/').rstrip('/')


def _client_ssl() -> ssl.SSLContext:
    context = ssl.create_default_context()
    context.check_hostname = False
    context.verify_mode = ssl.CERT_NONE
    return context


def _assert_canary_not_persisted(root: Path, canary: str) -> None:
    needle = canary.encode('utf-8')
    for path in root.rglob('*'):
        if not path.is_file():
            continue
        assert needle not in path.read_bytes(), f'relay persisted payload canary in {path}'


def _write_self_signed_cert(tmp_path: Path) -> tuple[Path, Path]:
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    subject = issuer = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, 'localhost')])
    cert = (
        x509.CertificateBuilder()
        .subject_name(subject)
        .issuer_name(issuer)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(datetime.now(timezone.utc) - timedelta(minutes=1))
        .not_valid_after(datetime.now(timezone.utc) + timedelta(days=1))
        .add_extension(
            x509.SubjectAlternativeName([x509.DNSName('localhost'), x509.DNSName('127.0.0.1')]),
            critical=False,
        )
        .sign(key, hashes.SHA256())
    )
    cert_path = tmp_path / 'relay-cert.pem'
    key_path = tmp_path / 'relay-key.pem'
    cert_path.write_bytes(cert.public_bytes(serialization.Encoding.PEM))
    key_path.write_bytes(
        key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.TraditionalOpenSSL,
            serialization.NoEncryption(),
        )
    )
    cert_path.chmod(0o600)
    key_path.chmod(0o600)
    return cert_path, key_path


def _b64(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).decode('ascii').rstrip('=')


def _b64decode(value: str) -> bytes:
    return base64.urlsafe_b64decode(value + '=' * (-len(value) % 4))
