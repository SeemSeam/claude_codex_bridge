"""Isolated production TLS CCB Relay for the Android OMP integration harness."""
from __future__ import annotations

import asyncio
import base64
from datetime import datetime, timedelta, timezone
import ipaddress
import socket
from pathlib import Path
import ssl
import threading
import time

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ed25519, rsa, x25519
from cryptography.x509.oid import NameOID

from mobile_gateway.relay import issue_host_access_grant
from mobile_gateway.relay_admission import (RelayAdmissionSecrets, RelayAdmissionStore,
    generate_host_private_key, host_public_key_b64)
from mobile_gateway.relay_crypto import host_fingerprint_for_public_key, public_key_b64
from mobile_gateway.relay_host_connector import RelayHostConnector, RelayHostConnectorConfig
from mobile_gateway.relay_service import ProductionRelayConfig, ProductionRelayService
from mobile_gateway.relay_stream import RELAY_STREAM_MAX_MESSAGE_BYTES


def _b64(value):
    return base64.urlsafe_b64encode(value).decode('ascii').rstrip('=')


class AndroidFixtureRelay:
    def __init__(self, directory: Path, gateway_origin: str, device_id: str):
        self.directory = directory
        self.gateway_origin = gateway_origin
        self.device_id = device_id
        self.loop = asyncio.new_event_loop()
        self.thread = threading.Thread(target=self.loop.run_forever, daemon=True)
        self.thread.start()
        try:
            self.defines = asyncio.run_coroutine_threadsafe(self._start(), self.loop).result(30)
        except BaseException:
            self.loop.call_soon_threadsafe(self.loop.stop)
            self.thread.join(timeout=5)
            raise

    async def _start(self):
        root = self.directory
        root.mkdir(mode=0o700)
        key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        subject = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, 'localhost')])
        cert = (x509.CertificateBuilder().subject_name(subject).issuer_name(subject)
            .public_key(key.public_key()).serial_number(x509.random_serial_number())
            .not_valid_before(datetime.now(timezone.utc) - timedelta(minutes=1))
            .not_valid_after(datetime.now(timezone.utc) + timedelta(days=1))
            .add_extension(x509.SubjectAlternativeName([x509.DNSName('localhost'),
                x509.IPAddress(ipaddress.ip_address('127.0.0.1'))]), critical=False)
            .sign(key, hashes.SHA256()))
        cert_file, key_file = root/'cert.pem', root/'key.pem'
        cert_file.write_bytes(cert.public_bytes(serialization.Encoding.PEM))
        key_file.write_bytes(key.private_bytes(serialization.Encoding.PEM,
            serialization.PrivateFormat.TraditionalOpenSSL, serialization.NoEncryption()))
        cert_file.chmod(0o600)
        key_file.chmod(0o600)
        signing = generate_host_private_key()
        store = RelayAdmissionStore(root/'relay.sqlite3',
            admission_secrets=RelayAdmissionSecrets.generate_for_testing())
        self.store = store
        invitation = store.issue_invitation(ttl_seconds=3600, max_sessions=8)
        credential = store.claim_invitation(invitation.invitation,
            host_public_key_b64=host_public_key_b64(signing))
        # Admission validates the signed grant audience against public_origin,
        # not the bound URL. Choose the loopback port before constructing config.
        with socket.socket() as listener:
            listener.bind(('127.0.0.1', 0))
            port = listener.getsockname()[1]
        origin = f'wss://127.0.0.1:{port}'
        self.service = ProductionRelayService(ProductionRelayConfig(
            listen_host='127.0.0.1', listen_port=port, admin_host='127.0.0.1', admin_port=0,
            public_origin=origin,
            tls_cert_file=cert_file, tls_key_file=key_file,
            admission_db_path=root/'relay.sqlite3', state_dir=root/'state',
            handshake_timeout=30, idle_timeout=3600, write_timeout=30), admission_store=store)
        await self.service.start()
        origin = self.service.url('/').rstrip('/')
        self.port = int(origin.rsplit(':', 1)[1])
        crypto = x25519.X25519PrivateKey.generate()
        self.connector = RelayHostConnector(RelayHostConnectorConfig(
            relay_origin=origin, gateway_origin=self.gateway_origin, host_id=credential.host_id,
            host_signing_key=signing, host_crypto_private_key=crypto,
            tls_context=ssl.create_default_context(cafile=str(cert_file)),
            request_timeout_seconds=30))
        self.task = asyncio.create_task(self.connector.run_forever())
        deadline = time.monotonic() + 20
        while self.connector.diagnostics()['state'] != 'registered':
            if self.task.done():
                await self.task
                raise RuntimeError('Relay connector stopped during startup')
            if time.monotonic() > deadline:
                raise RuntimeError('Relay connector registration timed out')
            await asyncio.sleep(.05)
        phone = ed25519.Ed25519PrivateKey.generate()
        seed = phone.private_bytes(serialization.Encoding.Raw,
            serialization.PrivateFormat.Raw, serialization.NoEncryption())
        grant = issue_host_access_grant(signing, host_id=credential.host_id,
            device_id=self.device_id, phone_auth_pubkey_b64=host_public_key_b64(phone),
            audience=origin, scopes=('view', 'message_submit', 'provider_settings'),
            expires_at=int(time.time()) + 3600)
        assert RELAY_STREAM_MAX_MESSAGE_BYTES == 512 * 1024
        return {'OMP_RELAY_URL': origin, 'OMP_RELAY_HOST': credential.host_id,
            'OMP_RELAY_DEVICE': self.device_id, 'OMP_RELAY_GRANT': grant,
            'OMP_RELAY_PHONE_KEY': _b64(seed),
            'OMP_RELAY_FINGERPRINT': host_fingerprint_for_public_key(public_key_b64(crypto)),
            'OMP_RELAY_CA_B64': base64.b64encode(cert_file.read_bytes()).decode('ascii')}

    def diagnostics(self):
        async def snapshot():
            return {'connector': self.connector.diagnostics(),
                'service': self.service.metrics_snapshot(),
                'admission_audit': self.store.audit_records(),
                'public_origin': self.service.config.public_origin,
                'inner_message_limit_bytes': RELAY_STREAM_MAX_MESSAGE_BYTES}
        return asyncio.run_coroutine_threadsafe(snapshot(), self.loop).result(10)

    def close(self):
        async def stop():
            self.connector.stop()
            await asyncio.gather(self.task, return_exceptions=True)
            await self.service.stop()
        try:
            asyncio.run_coroutine_threadsafe(stop(), self.loop).result(20)
        finally:
            self.loop.call_soon_threadsafe(self.loop.stop)
            self.thread.join(timeout=5)
            self.loop.close()
