from __future__ import annotations

import base64
from concurrent.futures import Future
from datetime import datetime, timedelta, timezone
import json
import os
import socket
import sqlite3
import struct
import threading
import time
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request, urlopen

import pytest

import mobile_gateway.pairing as pairing_module
import mobile_gateway.service as service_module
import mobile_gateway.terminal as terminal_module
from provider_control import ProviderAccountQuota
from mobile_gateway import (
    MobileGatewayError,
    MobileGatewayPairingError,
    MobileGatewayPairingStore,
    MobileGatewayProject,
    MobileGatewayProjectRegistry,
    MobileGatewayService,
    build_mobile_gateway_server,
    parse_listen_address,
)


class _FakeCcbdClient:
    def __init__(
        self,
        *,
        project_id: str = 'proj-demo',
        project_root: str = '/srv/demo',
        display_name: str = 'demo',
        health: str = 'healthy',
        mount_state: str = 'mounted',
    ) -> None:
        self.project_id = project_id
        self.project_root = project_root
        self.display_name = display_name
        self.health = health
        self.mount_state = mount_state
        self.calls: list[tuple[object, ...]] = []

    def ping(self, target: str = 'ccbd') -> dict[str, object]:
        self.calls.append(('ping', target))
        return {
            'project_id': self.project_id,
            'mount_state': self.mount_state,
            'health': self.health,
            'namespace_epoch': 4,
            'namespace_tmux_socket_path': '/tmp/ccb-demo/tmux.sock',
            'namespace_tmux_session_name': 'ccb-demo',
            'namespace_ui_attachable': True,
        }

    def project_view(self, *, schema_version: int = 1) -> dict[str, object]:
        self.calls.append(('project_view', schema_version))
        return {
            'view': {
                'project': {
                    'id': self.project_id,
                    'root': self.project_root,
                    'display_name': self.display_name,
                },
                'namespace': {
                    'epoch': 4,
                    'socket_path': '/tmp/ccb-demo/tmux.sock',
                    'session_name': 'ccb-demo',
                    'active_window': 'main',
                    'active_pane_id': '%2',
                },
                'windows': [
                    {
                        'name': 'main',
                        'label': 'main',
                        'kind': 'agents',
                        'order': 0,
                        'active': True,
                        'agents': ['mobile'],
                    }
                ],
                'agents': [
                    {
                        'name': 'mobile',
                        'provider': 'codex',
                        'window': 'main',
                        'order': 0,
                        'pane_id': '%2',
                        'active': True,
                    }
                ],
                'content': {
                    'items': [
                        {
                            'id': 'content-1',
                            'kind': 'markdown',
                            'format': 'markdown',
                            'agent': 'mobile',
                            'title': 'Agent reply',
                            'text': 'Ready for the next task.',
                            'source': 'reply',
                        }
                    ],
                },
                'comms': [],
            },
            'cache': {'sequence': 1},
        }

    def project_focus_agent(self, *, agent: str, namespace_epoch: int | None = None) -> dict[str, object]:
        self.calls.append(('project_focus_agent', agent, namespace_epoch))
        return {
            'focused': True,
            'kind': 'agent',
            'window': 'main',
            'agent': agent,
            'namespace_epoch': namespace_epoch,
        }

    def project_focus_window(self, *, window: str, namespace_epoch: int | None = None) -> dict[str, object]:
        self.calls.append(('project_focus_window', window, namespace_epoch))
        return {
            'focused': True,
            'kind': 'window',
            'window': window,
            'agent': None,
            'namespace_epoch': namespace_epoch,
        }

    def stop_all(self, *, force: bool = False) -> dict[str, object]:
        self.calls.append(('stop_all', force))
        return {
            'stopped': True,
            'force': force,
            'summary': {'agents': ['mobile']},
        }

    def submit(self, request) -> dict[str, object]:
        record = request.to_record()
        self.calls.append(('submit', record))
        return {
            'job_id': 'job_mobile_1',
            'agent_name': record['to_agent'],
            'status': 'queued',
            'accepted_at': '2026-06-18T00:00:01Z',
        }


class _FakeFrontdeskCcbdClient(_FakeCcbdClient):
    def project_view(self, *, schema_version: int = 1) -> dict[str, object]:
        payload = super().project_view(schema_version=schema_version)
        payload['view']['windows'][0]['agents'] = ['frontdesk']
        payload['view']['agents'][0] = {
            'name': 'frontdesk',
            'provider': 'codex',
            'window': 'main',
            'order': 0,
            'active': True,
        }
        return payload


class _FakeHerdrCcbdClient(_FakeCcbdClient):
    def project_view(self, *, schema_version: int = 1) -> dict[str, object]:
        payload = super().project_view(schema_version=schema_version)
        projection = {
            'backend_impl': 'herdr',
            'capability_status': 'blocked',
            'support_tier_projection': 'experimental',
            'support_tier_projection_source': 'validation_pending',
            'beta_gaps': ['mobile-terminal-validation-pending'],
            'blocking_gaps': ['mobile-terminal-adapter-unavailable'],
            'degraded_next_action': 'collect-validation-transcript',
            'evidence_refs': {
                'namespace_ref': {
                    'backend_impl': 'herdr',
                    'namespace_id': 'workspace-1',
                    'session_name': 'ccb-herdr',
                    'ipc_kind': 'herdr_socket',
                    'ipc_ref': 'herdr://workspace-1',
                },
                'pane_ref': {'backend_impl': 'herdr', 'pane_id': 'pane-1'},
            },
        }
        payload['view']['namespace'] = {
            'epoch': 4,
            'namespace_backend_impl': 'herdr',
            'namespace_backend_family': 'herdr-native',
            'namespace_id': 'workspace-1',
            'namespace_session_name': 'ccb-herdr',
            'namespace_ipc_kind': 'herdr_socket',
            'namespace_ipc_ref': 'herdr://workspace-1',
            'herdr_surface_projection': projection,
            'active_window': 'main',
            'active_pane_id': 'pane-1',
        }
        payload['view']['windows'][0]['active_pane_id'] = 'pane-1'
        payload['view']['agents'][0] = {
            'name': 'mobile',
            'provider': 'codex',
            'window': 'main',
            'order': 0,
            'pane_id': None,
            'active': True,
            'herdr_surface_projection': projection,
        }
        return payload


class _FakeHerdrSupportedCcbdClient(_FakeHerdrCcbdClient):
    def project_view(self, *, schema_version: int = 1) -> dict[str, object]:
        payload = super().project_view(schema_version=schema_version)
        projection = dict(payload['view']['namespace']['herdr_surface_projection'])
        projection.update(
            {
                'capability_status': 'supported',
                'support_tier_projection': 'beta',
                'support_tier_projection_source': 'backend_capability',
                'beta_gaps': [],
                'blocking_gaps': [],
                'degraded_next_action': None,
            }
        )
        payload['view']['namespace']['herdr_surface_projection'] = projection
        payload['view']['agents'][0]['herdr_surface_projection'] = projection
        return payload


class _FailingCcbdClient:
    def __init__(self, message: str = 'ccbd unavailable at /tmp/private.sock') -> None:
        self.message = message
        self.calls: list[tuple[object, ...]] = []

    def ping(self, target: str = 'ccbd') -> dict[str, object]:
        self.calls.append(('ping', target))
        raise RuntimeError(self.message)

    def project_view(self, *, schema_version: int = 1) -> dict[str, object]:
        self.calls.append(('project_view', schema_version))
        raise RuntimeError(self.message)


class _FakeTerminalSession:
    def __init__(self, target) -> None:
        self.target = target
        self.outputs = [b'hello']
        self.projections = (
            [
                {
                    'history_reset': True,
                    'history': b'older history\n',
                    'screen': b'hello',
                }
            ]
            if target.target_summary.get('kind') != 'host_shell'
            else []
        )
        self.writes: list[bytes] = []
        self.pastes: list[str] = []
        self.resizes: list[object] = []
        self.closed = False
        self.viewport_geometry = target.geometry
        self.viewport_revision = 0

    def read(self, timeout_seconds: float = 0.1) -> bytes | None:
        if self.outputs:
            return self.outputs.pop(0)
        time.sleep(min(0.01, max(0.0, timeout_seconds)))
        return b''

    def write(self, data: bytes) -> None:
        self.writes.append(data)

    def take_output_projection(self) -> dict[str, object] | None:
        return self.projections.pop(0) if self.projections else None

    def paste(self, text: str) -> None:
        self.pastes.append(text)

    def resize(self, geometry) -> None:
        self.resizes.append(geometry)

    def close(self) -> None:
        self.closed = True

    def viewport_state(self) -> dict[str, object]:
        return {
            'revision': self.viewport_revision,
            'geometry': self.viewport_geometry.to_mapping(),
            'resize_policy': (
                'client'
                if self.target.target_summary.get('kind') == 'host_shell'
                else 'fixed_source'
            ),
        }


class _FakeCcbdClientWithConversationComms(_FakeCcbdClient):
    def project_view(self, *, schema_version: int = 1) -> dict[str, object]:
        payload = super().project_view(schema_version=schema_version)
        payload['view']['comms'] = [
            {
                'id': 'job_mobile_reply',
                'sender': 'user',
                'target': 'mobile',
                'status': 'completed',
                'business_status': 'replied',
                'created_at': '2026-06-18T00:00:02Z',
                'body_preview': 'question from phone',
                'attachments': [
                    {
                        'file_id': 'mobile-file-1',
                        'file_name': 'probe.txt',
                        'mime_type': 'text/plain',
                        'size_bytes': 11,
                        'kind': 'document',
                    }
                ],
            },
            {
                'id': 'job_mobile_old_reply',
                'sender': 'user',
                'target': 'mobile',
                'status': 'completed',
                'business_status': 'replied',
                'created_at': '2026-06-18T00:00:01Z',
                'body_preview': 'older question from phone',
            },
            {
                'id': 'job_other_reply',
                'sender': 'user',
                'target': 'other',
                'status': 'completed',
                'business_status': 'replied',
                'body_preview': 'wrong target',
            },
        ]
        return payload


class _FakePiCcbdClientWithConversationComms(_FakeCcbdClientWithConversationComms):
    def project_view(self, *, schema_version: int = 1) -> dict[str, object]:
        payload = super().project_view(schema_version=schema_version)
        payload['view']['agents'][0]['provider'] = 'pi'
        return payload


class _FakeOmpCcbdClientWithConversationComms(_FakeCcbdClientWithConversationComms):
    def project_view(self, *, schema_version: int = 1) -> dict[str, object]:
        payload = super().project_view(schema_version=schema_version)
        payload['view']['agents'][0]['provider'] = 'omp'
        return payload


class _FakeClaudeCcbdClient(_FakeCcbdClient):
    def project_view(self, *, schema_version: int = 1) -> dict[str, object]:
        payload = super().project_view(schema_version=schema_version)
        payload['view']['agents'][0]['provider'] = 'claude'
        return payload


class _FakePiCcbdClient(_FakeCcbdClient):
    def project_view(self, *, schema_version: int = 1) -> dict[str, object]:
        payload = super().project_view(schema_version=schema_version)
        payload['view']['agents'][0]['provider'] = 'pi'
        return payload


class _FakeOmpCcbdClient(_FakeCcbdClient):
    def project_view(self, *, schema_version: int = 1) -> dict[str, object]:
        payload = super().project_view(schema_version=schema_version)
        payload['view']['agents'][0]['provider'] = 'omp'
        return payload


class _FakeActivityCcbdClient(_FakeCcbdClient):
    def project_view(self, *, schema_version: int = 1) -> dict[str, object]:
        payload = super().project_view(schema_version=schema_version)
        payload['view']['agents'][0].update({
            'activity_state': 'active',
            'activity_reason': 'codex_working_status_line',
            'last_progress_at': '2026-07-04T09:04:00Z',
        })
        payload['view']['content']['items'][0]['completed_at'] = '2026-07-04T09:02:00Z'
        payload['view']['comms'] = [
            {
                'id': 'job_mobile_reply',
                'target': 'mobile',
                'status': 'completed',
                'updated_at': '2026-07-04T09:03:00Z',
                'body_preview': 'reply activity',
            }
        ]
        return payload


class _SlowActivityCcbdClient(_FakeActivityCcbdClient):
    def __init__(self, *, sleep_seconds: float = 0.2) -> None:
        super().__init__()
        self.sleep_seconds = sleep_seconds

    def project_view(self, *, schema_version: int = 1) -> dict[str, object]:
        self.calls.append(('project_view_start', schema_version))
        time.sleep(self.sleep_seconds)
        payload = _FakeCcbdClient.project_view(self, schema_version=schema_version)
        payload['view']['agents'][0].update({
            'activity_state': 'active',
            'activity_reason': 'codex_working_status_line',
            'last_progress_at': '2026-07-04T09:30:00Z',
        })
        return payload


class _ManualClock:
    def __init__(self, now: float = 0.0) -> None:
        self.now = now

    def monotonic(self) -> float:
        return self.now

    def text(self) -> str:
        return f'2026-06-18T00:00:{int(self.now):02d}Z'

    def advance(self, seconds: float) -> None:
        self.now += seconds


class _ManualExecutor:
    def __init__(self) -> None:
        self.pending: list[tuple[Future[object], object]] = []

    def submit(self, task) -> Future[object]:
        future: Future[object] = Future()
        self.pending.append((future, task))
        return future

    def run_next(self) -> None:
        future, task = self.pending.pop(0)
        if future.set_running_or_notify_cancel():
            try:
                future.set_result(task())
            except BaseException as exc:
                future.set_exception(exc)

    def run_all(self) -> None:
        while self.pending:
            self.run_next()


class _ToggleHealthCcbdClient(_FakeCcbdClient):
    def __init__(self, **kwargs) -> None:
        super().__init__(**kwargs)
        self.available = True

    def ping(self, target: str = 'ccbd') -> dict[str, object]:
        if not self.available:
            self.calls.append(('ping', target))
            raise RuntimeError('ccbd unavailable at /tmp/private.sock')
        return super().ping(target)


def _service(
    fake: _FakeCcbdClient,
    *,
    project_root: Path | None = None,
    mobile_dir: Path | None = None,
    project_registry: MobileGatewayProjectRegistry | None = None,
    terminal_session_factory=None,
    terminal_history_factory=None,
    terminal_message_sender=None,
    host_terminal_manager=None,
    provider_quota_service=None,
    clock=None,
) -> MobileGatewayService:
    return MobileGatewayService(
        project_id='proj-demo',
        project_root=project_root or Path('/srv/demo'),
        ccbd_client_factory=lambda: fake,
        mobile_dir=mobile_dir,
        project_registry=project_registry,
        clock=clock or (lambda: '2026-06-18T00:00:00Z'),
        terminal_session_factory=terminal_session_factory,
        terminal_history_factory=terminal_history_factory,
        terminal_message_sender=terminal_message_sender,
        host_terminal_manager=host_terminal_manager,
        provider_quota_service=provider_quota_service,
    )


def _server_registry_service(
    clients: list[_FakeCcbdClient],
    *,
    clock: _ManualClock | None = None,
    executor: _ManualExecutor | None = None,
    registry_provider=None,
    mobile_dir: Path | None = None,
) -> MobileGatewayService:
    registry = MobileGatewayProjectRegistry([
        MobileGatewayProject(
            project_id=client.project_id,
            project_root=Path(client.project_root),
            display_name=client.display_name,
            ccbd_client_factory=lambda client=client: client,
        )
        for client in clients
    ])
    controlled_clock = clock or _ManualClock()
    return MobileGatewayService(
        project_id='host-test',
        project_root=Path('/tmp/mobile-host'),
        ccbd_client_factory=registry.default_project.client,
        mobile_dir=mobile_dir,
        project_registry=registry,
        project_registry_provider=registry_provider,
        mode='loopback_server_registry',
        clock=controlled_clock.text,
        monotonic_clock=controlled_clock.monotonic,
        background_executor=executor or _ManualExecutor(),
    )


def test_parse_listen_defaults_to_loopback_only() -> None:
    assert parse_listen_address(None).text == '127.0.0.1:8787'
    assert parse_listen_address('127.0.0.1:0').text == '127.0.0.1:0'
    assert parse_listen_address('localhost:8787').text == 'localhost:8787'
    with pytest.raises(ValueError, match='loopback'):
        parse_listen_address('192.168.31.155:8787')


def test_parse_listen_accepts_specific_private_ip_for_lan() -> None:
    assert (
        parse_listen_address('192.168.31.155:8787', allow_lan=True).text
        == '192.168.31.155:8787'
    )
    assert parse_listen_address('10.0.0.7:0', allow_lan=True).text == '10.0.0.7:0'
    assert parse_listen_address('169.254.10.2:8787', allow_lan=True).text == '169.254.10.2:8787'


@pytest.mark.parametrize('host', ('0.0.0.0', '8.8.8.8', 'mobile.example.com'))
def test_parse_listen_rejects_non_specific_or_non_private_lan_host(host: str) -> None:
    with pytest.raises(ValueError, match='specific private or link-local'):
        parse_listen_address(f'{host}:8787', allow_lan=True)


def test_health_and_projects_use_ccbd_without_exposing_tmux_socket() -> None:
    fake = _FakeCcbdClient()
    service = _service(fake)

    health = service.health_payload()
    projects = service.projects_payload()

    assert health['status'] == 'ok'
    assert health['ccbd']['namespace_epoch'] == 4
    assert projects['projects'][0]['id'] == 'proj-demo'
    assert 'tmux.sock' not in json.dumps(projects)
    assert fake.calls == [('ping', 'ccbd'), ('ping', 'ccbd'), ('project_view', 1)]


def test_projects_payload_lists_registry_projects_without_exposing_tmux_socket() -> None:
    first = _FakeCcbdClient(
        project_id='proj-one',
        project_root='/srv/one',
        display_name='one',
    )
    second = _FakeCcbdClient(
        project_id='proj-two',
        project_root='/srv/two',
        display_name='two',
    )
    service = _service(
        first,
        project_registry=MobileGatewayProjectRegistry(
            [
                MobileGatewayProject(
                    project_id='proj-one',
                    project_root=Path('/srv/one'),
                    ccbd_client_factory=lambda: first,
                ),
                MobileGatewayProject(
                    project_id='proj-two',
                    project_root=Path('/srv/two'),
                    ccbd_client_factory=lambda: second,
                ),
            ]
        ),
    )

    projects = service.projects_payload()

    assert [item['id'] for item in projects['projects']] == [
        'proj-one',
        'proj-two',
    ]
    assert projects['projects'][0]['display_name'] == 'one'
    assert projects['projects'][0]['root'] == '/srv/one'
    assert projects['projects'][1]['display_name'] == 'two'
    assert projects['projects'][1]['root'] == '/srv/two'
    assert 'tmux.sock' not in json.dumps(projects)
    assert first.calls == [('ping', 'ccbd'), ('project_view', 1)]
    assert second.calls == [('ping', 'ccbd'), ('project_view', 1)]


def test_projects_payload_sorts_by_persisted_recent_activity(tmp_path: Path) -> None:
    older = _FakeCcbdClient(
        project_id='proj-older',
        project_root='/srv/older',
        display_name='older',
    )
    recent = _FakeCcbdClient(
        project_id='proj-recent',
        project_root='/srv/recent',
        display_name='recent',
    )
    service = _service(
        older,
        mobile_dir=tmp_path / 'mobile',
        project_registry=MobileGatewayProjectRegistry(
            [
                MobileGatewayProject(
                    project_id='proj-older',
                    project_root=Path('/srv/older'),
                    ccbd_client_factory=lambda: older,
                ),
                MobileGatewayProject(
                    project_id='proj-recent',
                    project_root=Path('/srv/recent'),
                    ccbd_client_factory=lambda: recent,
                ),
            ]
        ),
    )
    assert service._project_activity_store is not None
    service._project_activity_store.record_summary(
        project_id='proj-older',
        summary={'last_activity_at': '2026-07-04T09:00:00Z'},
        checked_at='2026-06-18T00:00:00Z',
    )
    service._project_activity_store.record_summary(
        project_id='proj-recent',
        summary={'last_activity_at': '2026-07-04T09:05:00Z'},
        checked_at='2026-06-18T00:00:00Z',
    )

    projects = service.projects_payload()

    assert [item['id'] for item in projects['projects']] == [
        'proj-recent',
        'proj-older',
    ]


def test_projects_payload_includes_project_activity_summary() -> None:
    fake = _FakeActivityCcbdClient()
    service = _service(fake)

    projects = service.projects_payload()

    assert projects['projects'][0]['has_working_agents'] is True
    assert projects['projects'][0]['working_agent_count'] == 1
    assert projects['projects'][0]['last_activity_at'] == '2026-07-04T09:04:00Z'
    assert fake.calls == [('ping', 'ccbd'), ('project_view', 1)]


def test_working_project_refresh_does_not_overwrite_recent_send_activity(
    tmp_path: Path,
) -> None:
    working = _FakeActivityCcbdClient(
        project_id='proj-working',
        project_root='/srv/working',
        display_name='working',
    )
    recent = _FakeCcbdClient(
        project_id='proj-recent',
        project_root='/srv/recent',
        display_name='recent',
    )
    service = _service(
        working,
        mobile_dir=tmp_path / 'mobile',
        project_registry=MobileGatewayProjectRegistry(
            [
                MobileGatewayProject(
                    project_id='proj-working',
                    project_root=Path('/srv/working'),
                    ccbd_client_factory=lambda: working,
                ),
                MobileGatewayProject(
                    project_id='proj-recent',
                    project_root=Path('/srv/recent'),
                    ccbd_client_factory=lambda: recent,
                ),
            ]
        ),
        clock=lambda: '2026-07-05T00:00:00Z',
    )
    assert service._project_activity_store is not None
    service._project_activity_store.record_summary(
        project_id='proj-recent',
        summary={'last_activity_at': '2026-07-04T09:10:00Z'},
        checked_at='2026-07-04T09:10:00Z',
    )

    projects = service.projects_payload()

    assert [item['id'] for item in projects['projects']] == [
        'proj-recent',
        'proj-working',
    ]
    working_payload = projects['projects'][1]
    assert working_payload['has_working_agents'] is True
    assert working_payload['last_activity_at'] == '2026-07-04T09:04:00Z'


def test_projects_payload_reuses_cached_project_activity_summary(tmp_path: Path) -> None:
    fake = _FakeActivityCcbdClient()
    service = _service(fake, mobile_dir=tmp_path / 'mobile')

    first = service.projects_payload()
    second = service.projects_payload()

    assert first['projects'][0]['last_activity_at'] == '2026-07-04T09:04:00Z'
    assert second['projects'][0]['last_activity_at'] == '2026-07-04T09:04:00Z'
    assert fake.calls == [
        ('ping', 'ccbd'),
        ('project_view', 1),
        ('ping', 'ccbd'),
    ]


def test_projects_payload_returns_cached_activity_when_refresh_is_slow(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(service_module, '_PROJECT_ACTIVITY_REFRESH_TTL_SECONDS', 0)
    monkeypatch.setattr(service_module, '_PROJECT_ACTIVITY_REFRESH_BUDGET_SECONDS', 0.03)
    monkeypatch.setattr(service_module, '_PROJECT_ACTIVITY_REFRESH_PER_PROJECT_SECONDS', 0.02)
    fake = _SlowActivityCcbdClient(sleep_seconds=0.5)
    service = _service(
        fake,
        mobile_dir=tmp_path / 'mobile',
        project_registry=MobileGatewayProjectRegistry(
            [
                MobileGatewayProject(
                    project_id='proj-demo',
                    project_root=Path('/srv/demo'),
                    ccbd_client_factory=lambda: fake,
                ),
            ]
        ),
    )
    assert service._project_activity_store is not None
    service._project_activity_store.record_summary(
        project_id='proj-demo',
        summary={
            'last_activity_at': '2026-07-04T09:05:00Z',
            'has_working_agents': True,
            'working_agent_count': 1,
        },
        checked_at='2026-06-17T23:59:00Z',
    )

    started = time.monotonic()
    projects = service.projects_payload()
    elapsed = time.monotonic() - started

    assert elapsed < fake.sleep_seconds / 2
    assert projects['projects'][0]['last_activity_at'] == '2026-07-04T09:05:00Z'
    assert projects['projects'][0]['has_working_agents'] is True
    assert fake.calls[:2] == [('ping', 'ccbd'), ('project_view_start', 1)]


def test_project_view_records_last_opened_for_project_list(tmp_path: Path) -> None:
    fake = _FakeCcbdClient()
    service = _service(fake, mobile_dir=tmp_path / 'mobile')

    service.project_view_payload('proj-demo')
    projects = service.projects_payload()

    assert projects['projects'][0]['last_opened_at'] == '2026-06-18T00:00:00Z'


def test_focus_agent_records_project_activity_for_project_list(tmp_path: Path) -> None:
    fake = _FakeCcbdClient()
    service = _service(fake, mobile_dir=tmp_path / 'mobile')
    pairing = service.create_pairing_payload(
        gateway_url='http://127.0.0.1:8787',
        scopes=('view', 'focus'),
    )
    _, claim = service.dispatch_post(
        '/v1/pairing/claim',
        {'pairing_code': str(pairing['pairing_code'])},
    )

    service.dispatch_post(
        '/v1/projects/proj-demo/focus-agent',
        {'agent': 'mobile', 'namespace_epoch': 4},
        {'Authorization': f'Bearer {claim["device_token"]}'},
    )
    projects = service.projects_payload()

    assert projects['projects'][0]['last_activity_at'] == '2026-06-18T00:00:00Z'


def test_projects_payload_omits_unreachable_registry_projects() -> None:
    healthy = _FakeCcbdClient(
        project_id='proj-one',
        project_root='/srv/one',
        display_name='one',
    )
    stale = _FailingCcbdClient()
    service = _service(
        healthy,
        project_registry=MobileGatewayProjectRegistry(
            [
                MobileGatewayProject(
                    project_id='proj-one',
                    project_root=Path('/srv/one'),
                    ccbd_client_factory=lambda: healthy,
                ),
                MobileGatewayProject(
                    project_id='proj-stale',
                    project_root=Path('/srv/stale'),
                    display_name='stale',
                    ccbd_client_factory=lambda: stale,
                ),
            ]
        ),
    )

    projects = service.projects_payload()

    assert [item['id'] for item in projects['projects']] == [
        'proj-one',
    ]
    assert projects['projects'][0]['health'] == 'healthy'
    assert projects['projects'][0]['mount_state'] == 'mounted'
    assert '/tmp/private.sock' not in json.dumps(projects)
    assert healthy.calls == [('ping', 'ccbd'), ('project_view', 1)]
    assert stale.calls == [('ping', 'ccbd')]


def test_server_registry_health_does_not_scan_projects_when_cache_is_unknown() -> None:
    clients = [
        _FakeCcbdClient(
            project_id=f'proj-{index:02d}',
            project_root=f'/srv/project-{index:02d}',
            display_name=f'project-{index:02d}',
        )
        for index in range(12)
    ]
    service = _server_registry_service(clients, executor=_ManualExecutor())

    health = service.health_payload()

    assert health['status'] == 'ok'
    assert health['ccbd']['reachable'] is None
    assert health['ccbd']['project_count'] == 12
    assert health['ccbd']['available_project_count'] is None
    assert health['ccbd']['health_freshness'] == 'unknown'
    assert all(client.calls == [] for client in clients)


def test_server_projects_warm_cache_does_not_repeat_ping() -> None:
    clock = _ManualClock()
    executor = _ManualExecutor()
    client = _FakeCcbdClient(project_id='proj-one', project_root='/srv/one', display_name='one')
    service = _server_registry_service([client], clock=clock, executor=executor)

    assert service.projects_payload()['projects'] == []
    assert len(executor.pending) == 1
    executor.run_all()

    warm = service.projects_payload()['projects']

    assert [item['id'] for item in warm] == ['proj-one']
    assert warm[0]['health'] == 'healthy'
    assert warm[0]['health_freshness'] == 'fresh'
    assert client.calls == [('ping', 'ccbd')]
    assert executor.pending == []


def test_server_health_reports_cached_then_stale_without_refreshing_projects() -> None:
    clock = _ManualClock()
    executor = _ManualExecutor()
    client = _FakeCcbdClient(project_id='proj-one', project_root='/srv/one', display_name='one')
    service = _server_registry_service([client], clock=clock, executor=executor)
    service.projects_payload()
    executor.run_all()

    cached = service.health_payload()['ccbd']
    clock.advance(service_module._PROJECT_HEALTH_CACHE_TTL_SECONDS + 0.1)
    stale = service.health_payload()['ccbd']
    clock.advance(service_module._PROJECT_HEALTH_CACHE_MAX_STALE_SECONDS)
    unknown = service.health_payload()['ccbd']

    assert cached['health_freshness'] == 'cached'
    assert cached['available_project_count'] == 1
    assert stale['health_freshness'] == 'stale'
    assert stale['available_project_count'] == 1
    assert unknown['health_freshness'] == 'unknown'
    assert unknown['available_project_count'] is None
    assert client.calls == [('ping', 'ccbd')]


def test_server_projects_slow_refresh_does_not_block_list_response() -> None:
    clock = _ManualClock()
    executor = _ManualExecutor()
    client = _FakeCcbdClient(project_id='proj-slow', project_root='/srv/slow', display_name='slow')
    service = _server_registry_service([client], clock=clock, executor=executor)

    started = time.monotonic()
    payload = service.projects_payload()
    elapsed = time.monotonic() - started

    assert elapsed < 0.1
    assert payload['projects'] == []
    assert len(executor.pending) == 1
    assert client.calls == []


def test_server_projects_activity_refresh_uses_the_same_background_executor(tmp_path: Path) -> None:
    clock = _ManualClock()
    executor = _ManualExecutor()
    client = _FakeActivityCcbdClient(
        project_id='proj-one',
        project_root='/srv/one',
        display_name='one',
    )
    service = _server_registry_service(
        [client],
        clock=clock,
        executor=executor,
        mobile_dir=tmp_path / 'mobile',
    )
    service.projects_payload()
    executor.run_all()

    warm = service.projects_payload()['projects']

    assert 'has_working_agents' not in warm[0]
    assert len(executor.pending) == 1
    assert client.calls == [('ping', 'ccbd')]
    executor.run_all()
    assert service.projects_payload()['projects'][0]['has_working_agents'] is True


def test_server_projects_concurrent_requests_dedupe_refresh() -> None:
    clock = _ManualClock()
    executor = _ManualExecutor()
    client = _FakeCcbdClient(project_id='proj-one', project_root='/srv/one', display_name='one')
    service = _server_registry_service([client], clock=clock, executor=executor)

    service.projects_payload()
    service.projects_payload()

    assert len(executor.pending) == 1
    executor.run_all()
    assert client.calls == [('ping', 'ccbd')]


def test_server_projects_marks_ttl_expiry_stale_and_refreshes_asynchronously() -> None:
    clock = _ManualClock()
    executor = _ManualExecutor()
    client = _FakeCcbdClient(project_id='proj-one', project_root='/srv/one', display_name='one')
    service = _server_registry_service([client], clock=clock, executor=executor)
    service.projects_payload()
    executor.run_all()
    clock.advance(service_module._PROJECT_HEALTH_CACHE_TTL_SECONDS + 0.1)

    stale = service.projects_payload()['projects']

    assert stale[0]['health'] == 'healthy'
    assert stale[0]['health_freshness'] == 'stale'
    assert stale[0]['health_refreshing'] is True
    assert client.calls == [('ping', 'ccbd')]
    assert len(executor.pending) == 1
    executor.run_all()
    assert service.projects_payload()['projects'][0]['health_freshness'] == 'fresh'
    assert client.calls == [('ping', 'ccbd'), ('ping', 'ccbd')]


def test_server_projects_keeps_expired_health_visible_while_revalidating() -> None:
    clock = _ManualClock()
    executor = _ManualExecutor()
    client = _ToggleHealthCcbdClient(
        project_id='proj-one',
        project_root='/srv/one',
        display_name='one',
    )
    service = _server_registry_service([client], clock=clock, executor=executor)
    cold = service.projects_payload()
    assert cold['projects'] == []
    assert cold['health_warming'] is True
    assert cold['health_unknown_project_count'] == 1
    executor.run_all()
    clock.advance(service_module._PROJECT_HEALTH_CACHE_MAX_STALE_SECONDS + 0.1)

    expired = service.projects_payload()

    assert [item['id'] for item in expired['projects']] == ['proj-one']
    assert expired['projects'][0]['health_freshness'] == 'expired'
    assert expired['health_warming'] is False
    assert expired['health_unknown_project_count'] == 0
    assert len(executor.pending) == 1

    client.available = False
    executor.run_all()
    assert service.projects_payload()['projects'] == []


def test_server_projects_health_failure_and_recovery_converge() -> None:
    clock = _ManualClock()
    executor = _ManualExecutor()
    client = _ToggleHealthCcbdClient(
        project_id='proj-one',
        project_root='/srv/one',
        display_name='one',
    )
    service = _server_registry_service([client], clock=clock, executor=executor)
    service.projects_payload()
    executor.run_all()
    client.available = False
    clock.advance(service_module._PROJECT_HEALTH_CACHE_TTL_SECONDS + 0.1)

    stale = service.projects_payload()['projects']
    assert stale[0]['health_freshness'] == 'stale'
    executor.run_all()
    assert service.projects_payload()['projects'] == []

    client.available = True
    clock.advance(service_module._PROJECT_HEALTH_FAILURE_BACKOFF_SECONDS + 0.1)
    assert service.projects_payload()['projects'] == []
    executor.run_all()
    recovered = service.projects_payload()['projects']

    assert recovered[0]['id'] == 'proj-one'
    assert recovered[0]['health_freshness'] == 'fresh'
    assert client.calls == [('ping', 'ccbd'), ('ping', 'ccbd'), ('ping', 'ccbd')]


def test_projects_payload_omits_registered_projects_that_are_not_mounted_and_healthy() -> None:
    healthy = _FakeCcbdClient(
        project_id='proj-one',
        project_root='/srv/one',
        display_name='one',
    )
    unmounted = _FakeCcbdClient(
        project_id='proj-unmounted',
        project_root='/srv/unmounted',
        display_name='unmounted',
        mount_state='unmounted',
    )
    degraded = _FakeCcbdClient(
        project_id='proj-degraded',
        project_root='/srv/degraded',
        display_name='degraded',
        health='degraded',
    )
    service = _service(
        healthy,
        project_registry=MobileGatewayProjectRegistry(
            [
                MobileGatewayProject(
                    project_id='proj-one',
                    project_root=Path('/srv/one'),
                    ccbd_client_factory=lambda: healthy,
                ),
                MobileGatewayProject(
                    project_id='proj-unmounted',
                    project_root=Path('/srv/unmounted'),
                    ccbd_client_factory=lambda: unmounted,
                ),
                MobileGatewayProject(
                    project_id='proj-degraded',
                    project_root=Path('/srv/degraded'),
                    ccbd_client_factory=lambda: degraded,
                ),
            ]
        ),
    )

    projects = service.projects_payload()

    assert [item['id'] for item in projects['projects']] == ['proj-one']
    assert healthy.calls == [('ping', 'ccbd'), ('project_view', 1)]
    assert unmounted.calls == [('ping', 'ccbd')]
    assert degraded.calls == [('ping', 'ccbd')]


def test_server_projects_reconciles_registry_additions_and_removals() -> None:
    clock = _ManualClock()
    executor = _ManualExecutor()
    first = _FakeCcbdClient(project_id='proj-one', project_root='/srv/one', display_name='one')
    second = _FakeCcbdClient(project_id='proj-two', project_root='/srv/two', display_name='two')
    registry_clients = [first]

    def registry_provider() -> MobileGatewayProjectRegistry:
        return MobileGatewayProjectRegistry([
            MobileGatewayProject(
                project_id=client.project_id,
                project_root=Path(client.project_root),
                display_name=client.display_name,
                ccbd_client_factory=lambda client=client: client,
            )
            for client in registry_clients
        ])

    service = _server_registry_service(
        [first],
        clock=clock,
        executor=executor,
        registry_provider=registry_provider,
    )
    service.projects_payload()
    executor.run_all()
    registry_clients.append(second)

    added = service.projects_payload()['projects']
    assert [item['id'] for item in added] == ['proj-one']
    executor.run_all()
    assert [item['id'] for item in service.projects_payload()['projects']] == ['proj-one', 'proj-two']

    registry_clients[:] = [second]
    assert [item['id'] for item in service.projects_payload()['projects']] == ['proj-two']
    health = service.health_payload()
    assert health['ccbd']['project_count'] == 1
    assert 'proj-one' not in service._project_health_cache._entries  # type: ignore[union-attr]


def test_server_projects_mix_fresh_and_stale_health_summaries() -> None:
    clock = _ManualClock()
    executor = _ManualExecutor()
    first = _FakeCcbdClient(project_id='proj-one', project_root='/srv/one', display_name='one')
    second = _FakeCcbdClient(project_id='proj-two', project_root='/srv/two', display_name='two')
    service = _server_registry_service([first, second], clock=clock, executor=executor)
    service.projects_payload()
    executor.run_all()
    clock.advance(service_module._PROJECT_HEALTH_CACHE_TTL_SECONDS + 0.1)

    service.projects_payload()
    executor.run_next()
    mixed = service.projects_payload()['projects']
    freshness = {item['id']: item['health_freshness'] for item in mixed}

    assert freshness == {'proj-one': 'fresh', 'proj-two': 'stale'}
    executor.run_all()


def test_server_project_health_refresh_budget_and_cache_size_are_bounded(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(service_module, '_PROJECT_HEALTH_REFRESH_BUDGET', 1)
    monkeypatch.setattr(service_module, '_PROJECT_HEALTH_CACHE_MAX_ENTRIES', 1)
    clock = _ManualClock()
    executor = _ManualExecutor()
    clients = [
        _FakeCcbdClient(project_id='proj-one', project_root='/srv/one', display_name='one'),
        _FakeCcbdClient(project_id='proj-two', project_root='/srv/two', display_name='two'),
    ]
    service = _server_registry_service(clients, clock=clock, executor=executor)

    service.projects_payload()

    assert len(executor.pending) == 1
    executor.run_all()
    assert len(service._project_health_cache._entries) == 1  # type: ignore[union-attr]


def test_project_view_redacts_server_tmux_evidence() -> None:
    fake = _FakeCcbdClient()
    payload = _service(fake).project_view_payload('proj-demo')
    namespace = payload['view']['namespace']

    assert namespace['epoch'] == 4
    assert namespace['active_pane_id'] == '%2'
    assert 'socket_path' not in namespace
    assert 'session_name' not in namespace
    assert 'tmux.sock' not in json.dumps(payload)
    assert 'ccb-demo' not in json.dumps(payload)
    assert fake.calls == [('project_view', 1)]


def test_project_view_preserves_correlated_execution_phase_fields() -> None:
    class _PhaseCcbdClient(_FakeCcbdClient):
        def project_view(self, *, schema_version: int = 1) -> dict[str, object]:
            payload = super().project_view(schema_version=schema_version)
            payload['view']['comms'] = [
                {
                    'id': 'job-phase',
                    'status': 'running',
                    'execution_phase': 'executing',
                    'execution_phase_reason': 'provider_active',
                    'execution_evidence': {
                        'job_id': 'job-phase',
                        'completion_anchor_seen': True,
                    },
                }
            ]
            return payload

    payload = _service(_PhaseCcbdClient()).project_view_payload('proj-demo')

    comm = payload['view']['comms'][0]
    assert comm['execution_phase'] == 'executing'
    assert comm['execution_phase_reason'] == 'provider_active'
    assert comm['execution_evidence'] == {
        'job_id': 'job-phase',
        'completion_anchor_seen': True,
    }


def test_project_view_rejects_unknown_project() -> None:
    with pytest.raises(MobileGatewayError, match='unknown project') as excinfo:
        _service(_FakeCcbdClient()).project_view_payload('other')
    assert excinfo.value.status_code == 404


def test_agent_provider_control_returns_runtime_and_matching_catalog(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class _ProviderControlClient(_FakeCcbdClient):
        def project_view(self, *, schema_version: int = 1) -> dict[str, object]:
            payload = super().project_view(schema_version=schema_version)
            payload['view']['agents'][0]['provider_control'] = {
                'schema_version': 1,
                'provider': 'codex',
                'configured_model': 'gpt-5.6-sol',
                'active_model': 'gpt-5.6-sol',
                'mutation_mode': 'restart_required',
                'usage': {'total_tokens': 123, 'scope': 'current_session_tail'},
            }
            return payload

    catalog_calls: list[dict[str, list[str]] | None] = []
    real_capabilities = service_module.config_ui_provider_capabilities

    def capabilities_without_unrelated_cli_probes(**kwargs):
        catalog_calls.append(kwargs.get('cli_models'))
        return real_capabilities(**kwargs)

    monkeypatch.setattr(
        service_module,
        'config_ui_provider_capabilities',
        capabilities_without_unrelated_cli_probes,
    )
    service = _service(
        _ProviderControlClient(),
        project_root=tmp_path / 'project',
        mobile_dir=tmp_path / 'mobile',
    )
    pairing = service.create_pairing_payload(gateway_url='http://127.0.0.1:8787')
    _, claim = service.dispatch_post(
        '/v1/pairing/claim',
        {'pairing_code': str(pairing['pairing_code']), 'device_name': 'Provider test'},
    )

    status, payload = service.dispatch_get(
        '/v1/projects/proj-demo/agents/mobile/provider-control',
        {'Authorization': f'Bearer {claim["device_token"]}'},
    )

    assert status == 200
    assert payload['provider_control']['active_model'] == 'gpt-5.6-sol'
    assert payload['provider_control']['usage']['total_tokens'] == 123
    assert payload['provider_catalog']['id'] == 'codex'
    assert payload['provider_catalog']['model_shortcut'] is True
    assert 'account_usage' not in payload
    assert catalog_calls == [{}]


def test_agent_provider_quota_is_loaded_on_separate_route(tmp_path: Path) -> None:
    class _QuotaService:
        def __init__(self) -> None:
            self.calls: list[tuple[Path, str, str]] = []

        def account_quota(self, *, project_root: Path, agent: str, provider: str):
            self.calls.append((project_root, agent, provider))
            return ProviderAccountQuota(
                provider_id=provider,
                display_name='Codex',
                status='available',
                fetched_at='2026-08-11T00:00:00Z',
                next_refresh_at='2026-08-11T00:05:00Z',
            )

    quota = _QuotaService()
    project_root = tmp_path / 'project'
    service = _service(
        _FakeCcbdClient(),
        project_root=project_root,
        mobile_dir=tmp_path / 'mobile',
        provider_quota_service=quota,
    )
    pairing = service.create_pairing_payload(gateway_url='http://127.0.0.1:8787')
    _, claim = service.dispatch_post(
        '/v1/pairing/claim',
        {'pairing_code': str(pairing['pairing_code']), 'device_name': 'Provider quota'},
    )
    headers = {'Authorization': f'Bearer {claim["device_token"]}'}

    status, payload = service.dispatch_get(
        '/v1/projects/proj-demo/agents/mobile/provider-quota',
        headers,
    )

    assert status == 200
    assert payload['account_usage']['status'] == 'available'
    assert quota.calls == [(project_root, 'mobile', 'codex')]


def test_agent_provider_control_requires_view_scope(tmp_path: Path) -> None:
    service = _service(_FakeCcbdClient(), mobile_dir=tmp_path / 'mobile')

    with pytest.raises(MobileGatewayError) as missing_auth:
        service.dispatch_get('/v1/projects/proj-demo/agents/mobile/provider-control')

    assert missing_auth.value.status_code == 401


def test_agent_provider_settings_are_fenced_and_idempotent(tmp_path: Path) -> None:
    class _ProviderControlClient(_FakeCcbdClient):
        def project_view(self, *, schema_version: int = 1) -> dict[str, object]:
            payload = super().project_view(schema_version=schema_version)
            payload['view']['agents'][0]['provider_control'] = {
                'provider': 'codex',
                'active_model': 'gpt-5.5',
                'active_thinking': 'medium',
                'runtime_revision': 'runtime-r1',
                'mutation_mode': 'restart_required',
            }
            return payload

    project_root = tmp_path / 'project'
    config = project_root / '.ccb' / 'ccb.config'
    config.parent.mkdir(parents=True)
    config.write_text(
        '''version = 2
entry_window = "main"

[windows]
main = "mobile:codex"

[agents.mobile]
model = "gpt-5.5"
thinking = "medium"
''',
        encoding='utf-8',
    )
    service = _service(
        _ProviderControlClient(),
        project_root=project_root,
        mobile_dir=tmp_path / 'mobile',
    )
    pairing = service.create_pairing_payload(gateway_url='http://127.0.0.1:8787')
    _, claim = service.dispatch_post(
        '/v1/pairing/claim',
        {'pairing_code': str(pairing['pairing_code']), 'device_name': 'Provider test'},
    )
    headers = {'Authorization': f'Bearer {claim["device_token"]}'}
    _, details = service.dispatch_get(
        '/v1/projects/proj-demo/agents/mobile/provider-control',
        headers,
    )
    request = {
        'model': 'gpt-5.6-sol',
        'thinking': 'xhigh',
        'expected_revision': details['config_revision'],
        'expected_namespace_epoch': details['namespace_epoch'],
        'expected_provider': 'codex',
        'expected_runtime_revision': 'runtime-r1',
        'idempotency_key': 'provider-change-0001',
    }

    status, first = service.dispatch_post(
        '/v1/projects/proj-demo/agents/mobile/provider-control',
        request,
        headers,
    )
    replay_status, replay = service.dispatch_post(
        '/v1/projects/proj-demo/agents/mobile/provider-control',
        request,
        headers,
    )

    assert status == replay_status == 200
    assert replay == first
    assert first['status'] == 'pending_restart'
    assert first['namespace_epoch'] == 4
    assert first['runtime_revision'] == 'runtime-r1'
    assert 'backup_path' not in first
    assert 'model = "gpt-5.6-sol"' in config.read_text(encoding='utf-8')

    with pytest.raises(MobileGatewayError, match='another request') as cross_agent:
        service.dispatch_post(
            '/v1/projects/proj-demo/agents/other/provider-control',
            request,
            headers,
        )
    assert cross_agent.value.status_code == 409

    with pytest.raises(MobileGatewayError, match='another request') as reused:
        service.dispatch_post(
            '/v1/projects/proj-demo/agents/mobile/provider-control',
            {**request, 'model': 'gpt-5.5'},
            headers,
        )
    assert reused.value.status_code == 409


def test_agent_provider_settings_remain_pending_when_runtime_model_is_unknown(
    tmp_path: Path,
) -> None:
    class _ProviderControlClient(_FakeCcbdClient):
        def project_view(self, *, schema_version: int = 1) -> dict[str, object]:
            payload = super().project_view(schema_version=schema_version)
            payload['view']['agents'][0]['provider_control'] = {
                'provider': 'codex',
                'active_model': None,
                'active_thinking': None,
                'runtime_revision': None,
                'mutation_mode': 'restart_required',
            }
            return payload

    project_root = tmp_path / 'project'
    config = project_root / '.ccb' / 'ccb.config'
    config.parent.mkdir(parents=True)
    config.write_text(
        '''version = 2
entry_window = "main"

[windows]
main = "mobile:codex"

[agents.mobile]
model = "gpt-5.5"
thinking = "medium"
''',
        encoding='utf-8',
    )
    service = _service(
        _ProviderControlClient(),
        project_root=project_root,
        mobile_dir=tmp_path / 'mobile',
    )
    pairing = service.create_pairing_payload(gateway_url='http://127.0.0.1:8787')
    _, claim = service.dispatch_post(
        '/v1/pairing/claim',
        {'pairing_code': str(pairing['pairing_code']), 'device_name': 'Provider test'},
    )
    headers = {'Authorization': f'Bearer {claim["device_token"]}'}
    _, before = service.dispatch_get(
        '/v1/projects/proj-demo/agents/mobile/provider-control',
        headers,
    )

    service.dispatch_post(
        '/v1/projects/proj-demo/agents/mobile/provider-control',
        {
            'model': 'gpt-5.6-sol',
            'thinking': 'xhigh',
            'expected_revision': before['config_revision'],
            'expected_namespace_epoch': before['namespace_epoch'],
            'expected_provider': 'codex',
            'expected_runtime_revision': None,
            'idempotency_key': 'provider-change-unknown-runtime',
        },
        headers,
    )
    _, after = service.dispatch_get(
        '/v1/projects/proj-demo/agents/mobile/provider-control',
        headers,
    )

    assert after['provider_control']['active_model'] is None
    assert after['provider_control']['restart_pending'] is True
    assert after['provider_control']['pending_model'] == 'gpt-5.6-sol'
    assert after['provider_control']['pending_thinking'] == 'xhigh'


def test_agent_provider_settings_require_dedicated_scope_and_current_identity(
    tmp_path: Path,
) -> None:
    class _ProviderControlClient(_FakeCcbdClient):
        def project_view(self, *, schema_version: int = 1) -> dict[str, object]:
            payload = super().project_view(schema_version=schema_version)
            payload['view']['agents'][0]['provider_control'] = {
                'provider': 'codex',
                'runtime_revision': 'runtime-r1',
            }
            return payload

    project_root = tmp_path / 'project'
    config = project_root / '.ccb' / 'ccb.config'
    config.parent.mkdir(parents=True)
    config.write_text(
        'version = 2\nentry_window = "main"\n\n[windows]\nmain = "mobile:codex"\n',
        encoding='utf-8',
    )
    service = _service(
        _ProviderControlClient(),
        project_root=project_root,
        mobile_dir=tmp_path / 'mobile',
    )
    pairing = service.create_pairing_payload(
        gateway_url='http://127.0.0.1:8787',
        scopes=('view', 'lifecycle'),
    )
    _, claim = service.dispatch_post(
        '/v1/pairing/claim',
        {'pairing_code': str(pairing['pairing_code'])},
    )
    request = {
        'model': 'gpt-5.6-sol',
        'expected_revision': service_module.project_config_revision(project_root),
        'expected_namespace_epoch': 4,
        'expected_provider': 'codex',
        'expected_runtime_revision': 'runtime-r1',
        'idempotency_key': 'provider-change-0002',
    }

    with pytest.raises(MobileGatewayError) as denied:
        service.dispatch_post(
            '/v1/projects/proj-demo/agents/mobile/provider-control',
            request,
            {'Authorization': f'Bearer {claim["device_token"]}'},
        )
    assert denied.value.status_code == 403

    allowed_pairing = service.create_pairing_payload(
        gateway_url='http://127.0.0.1:8787',
        scopes=('view', 'provider_settings'),
    )
    _, allowed_claim = service.dispatch_post(
        '/v1/pairing/claim',
        {'pairing_code': str(allowed_pairing['pairing_code'])},
    )
    allowed_headers = {'Authorization': f'Bearer {allowed_claim["device_token"]}'}
    with pytest.raises(MobileGatewayError, match='stale project namespace') as stale:
        service.dispatch_post(
            '/v1/projects/proj-demo/agents/mobile/provider-control',
            {**request, 'expected_namespace_epoch': 3},
            allowed_headers,
        )
    assert stale.value.status_code == 409

    with pytest.raises(MobileGatewayError, match='stale provider runtime') as stale_runtime:
        service.dispatch_post(
            '/v1/projects/proj-demo/agents/mobile/provider-control',
            {
                **request,
                'idempotency_key': 'provider-change-0003',
                'expected_runtime_revision': 'runtime-r0',
            },
            allowed_headers,
        )
    assert stale_runtime.value.status_code == 409


def test_project_view_routes_to_matching_registry_project() -> None:
    first = _FakeCcbdClient(
        project_id='proj-one',
        project_root='/srv/one',
        display_name='one',
    )
    second = _FakeCcbdClient(
        project_id='proj-two',
        project_root='/srv/two',
        display_name='two',
    )
    service = _service(
        first,
        project_registry=MobileGatewayProjectRegistry(
            [
                MobileGatewayProject(
                    project_id='proj-one',
                    project_root=Path('/srv/one'),
                    ccbd_client_factory=lambda: first,
                ),
                MobileGatewayProject(
                    project_id='proj-two',
                    project_root=Path('/srv/two'),
                    ccbd_client_factory=lambda: second,
                ),
            ]
        ),
    )

    payload = service.project_view_payload('proj-two')

    assert payload['view']['project']['id'] == 'proj-two'
    assert payload['view']['project']['root'] == '/srv/two'
    assert 'socket_path' not in payload['view']['namespace']
    assert first.calls == []
    assert second.calls == [('project_view', 1)]


def test_terminal_history_reads_selected_agent_scrollback_without_leaking_tmux_evidence(tmp_path: Path) -> None:
    targets = []

    def history_factory(target):
        targets.append(target)
        return {
            'history_scope': 'tmux_scrollback',
            'source_pane_id': target.pane_id,
            'blocks': [
                {
                    'id': 'history-1',
                    'type': 'command',
                    'title': 'Command',
                    'text': '$ flutter test',
                },
                {
                    'id': 'history-2',
                    'type': 'log',
                    'title': 'Log',
                    'text': '54 tests passed',
                },
            ],
        }

    service = _service(
        _FakeCcbdClient(),
        mobile_dir=tmp_path / 'mobile',
        terminal_history_factory=history_factory,
    )
    pairing = service.create_pairing_payload(gateway_url='http://127.0.0.1:8787')
    _, claim = service.dispatch_post(
        '/v1/pairing/claim',
        {'pairing_code': str(pairing['pairing_code']), 'device_name': 'Pixel Fold'},
    )

    status, payload = service.dispatch_get(
        '/v1/projects/proj-demo/terminal-history?agent=mobile&namespace_epoch=4&max_lines=120',
        {'Authorization': f'Bearer {claim["device_token"]}'},
    )

    assert status == 200
    assert payload['status'] == 'ok'
    history = payload['terminal_history']
    assert history['agent'] == 'mobile'
    assert history['history_scope'] == 'tmux_scrollback'
    assert history['source_pane_id'] == '%2'
    assert history['generated_at'] == '2026-06-18T00:00:00Z'
    assert history['blocks'][0]['type'] == 'command'
    assert targets[0].project_id == 'proj-demo'
    assert targets[0].namespace_epoch == 4
    assert targets[0].agent == 'mobile'
    assert targets[0].window == 'main'
    assert targets[0].pane_id == '%2'
    assert targets[0].socket_path == '/tmp/ccb-demo/tmux.sock'
    assert targets[0].session_name == 'ccb-demo'
    assert targets[0].max_lines == 120
    public_json = json.dumps(payload)
    assert '/tmp/ccb-demo/tmux.sock' not in public_json
    assert 'ccb-demo' not in public_json


def test_terminal_history_returns_herdr_blocked_payload(tmp_path: Path) -> None:
    called = False

    def history_factory(_target):
        nonlocal called
        called = True
        return {}

    service = _service(
        _FakeHerdrCcbdClient(),
        mobile_dir=tmp_path / 'mobile',
        terminal_history_factory=history_factory,
    )
    pairing = service.create_pairing_payload(gateway_url='http://127.0.0.1:8787')
    _, claim = service.dispatch_post(
        '/v1/pairing/claim',
        {'pairing_code': str(pairing['pairing_code']), 'device_name': 'Pixel Fold'},
    )

    status, payload = service.dispatch_get(
        '/v1/projects/proj-demo/terminal-history?agent=mobile&namespace_epoch=4&max_lines=120',
        {'Authorization': f'Bearer {claim["device_token"]}'},
    )

    assert status == 409
    assert payload['status'] == 'blocked'
    blocked = payload['terminal_blocked']
    assert blocked['code'] == 'history_unsupported'
    assert blocked['backend_impl'] == 'herdr'
    assert blocked['capability_status'] == 'blocked'
    assert blocked['degraded_next_action'] == 'collect-validation-transcript'
    assert blocked['beta_gaps'] == ['mobile-terminal-validation-pending']
    assert blocked['blocking_gaps'] == ['mobile-terminal-adapter-unavailable']
    assert called is False
    assert 'herdr://workspace-1' not in json.dumps(payload)


def test_terminal_history_blocks_contradictory_herdr_supported_projection(tmp_path: Path) -> None:
    class _ContradictoryHerdrClient(_FakeHerdrSupportedCcbdClient):
        def __init__(self, projection_delta: dict[str, object]) -> None:
            super().__init__()
            self.projection_delta = projection_delta

        def project_view(self, *, schema_version: int = 1) -> dict[str, object]:
            payload = super().project_view(schema_version=schema_version)
            projection = dict(payload['view']['namespace']['herdr_surface_projection'])
            projection.update({'capability_status': 'supported'})
            projection.update(self.projection_delta)
            payload['view']['namespace']['herdr_surface_projection'] = projection
            payload['view']['agents'][0]['herdr_surface_projection'] = projection
            return payload

    contradictions = [
        ({'beta_gaps': ['mobile-terminal-validation-pending']}, 'beta_gaps'),
        ({'support_tier_projection': 'experimental'}, 'support_tier_projection'),
        ({'support_tier_projection_source': 'validation_pending'}, 'support_tier_projection_source'),
    ]

    for delta, case in contradictions:
        called = False

        def history_factory(_target):
            nonlocal called
            called = True
            return {}

        service = _service(
            _ContradictoryHerdrClient(delta),
            mobile_dir=tmp_path / f'mobile-{case}',
            terminal_history_factory=history_factory,
        )
        pairing = service.create_pairing_payload(gateway_url='http://127.0.0.1:8787')
        _, claim = service.dispatch_post('/v1/pairing/claim', {'pairing_code': str(pairing['pairing_code'])})

        status, payload = service.dispatch_get(
            '/v1/projects/proj-demo/terminal-history?agent=mobile&namespace_epoch=4&max_lines=120',
            {'Authorization': f'Bearer {claim["device_token"]}'},
        )

        assert status == 409
        assert payload['status'] == 'blocked'
        assert payload['terminal_blocked']['backend_impl'] == 'herdr'
        assert called is False


def test_terminal_history_uses_herdr_backend_neutral_target(tmp_path: Path) -> None:
    targets = []

    def history_factory(target):
        targets.append(target)
        return {
            'history_scope': 'herdr_pane_history',
            'source_pane_id': target.pane_id,
            'blocks': [{'id': 'history-1', 'type': 'log', 'title': 'Log', 'text': 'Herdr output'}],
        }

    service = _service(
        _FakeHerdrSupportedCcbdClient(),
        mobile_dir=tmp_path / 'mobile',
        terminal_history_factory=history_factory,
    )
    pairing = service.create_pairing_payload(gateway_url='http://127.0.0.1:8787')
    _, claim = service.dispatch_post('/v1/pairing/claim', {'pairing_code': str(pairing['pairing_code'])})

    status, payload = service.dispatch_get(
        '/v1/projects/proj-demo/terminal-history?agent=mobile&namespace_epoch=4&max_lines=120',
        {'Authorization': f'Bearer {claim["device_token"]}'},
    )

    assert status == 200
    assert payload['status'] == 'ok'
    assert payload['terminal_history']['history_scope'] == 'herdr_pane_history'
    assert targets[0].backend_impl == 'herdr'
    assert targets[0].socket_path == ''
    assert targets[0].session_name == 'ccb-herdr'
    assert targets[0].pane_ref == {'backend_impl': 'herdr', 'pane_id': 'pane-1'}
    assert targets[0].namespace_ref['namespace_id'] == 'workspace-1'
    assert targets[0].history_supported is True
    assert 'herdr://workspace-1' not in json.dumps(payload)


def test_terminal_history_default_herdr_adapter_missing_returns_blocked(tmp_path: Path) -> None:
    service = _service(_FakeHerdrSupportedCcbdClient(), mobile_dir=tmp_path / 'mobile')
    pairing = service.create_pairing_payload(gateway_url='http://127.0.0.1:8787')
    _, claim = service.dispatch_post('/v1/pairing/claim', {'pairing_code': str(pairing['pairing_code'])})

    status, payload = service.dispatch_get(
        '/v1/projects/proj-demo/terminal-history?agent=mobile&namespace_epoch=4&max_lines=120',
        {'Authorization': f'Bearer {claim["device_token"]}'},
    )

    assert status == 409
    assert payload['status'] == 'blocked'
    assert payload['terminal_blocked']['code'] == 'target_not_attachable'
    assert payload['terminal_blocked']['blocking_gaps'] == ['herdr-terminal-adapter-unavailable']
    assert 'tmux' not in json.dumps(payload)


def test_terminal_history_requires_view_auth_and_fresh_epoch(tmp_path: Path) -> None:
    service = _service(_FakeCcbdClient(), mobile_dir=tmp_path / 'mobile')

    with pytest.raises(MobileGatewayError) as missing_auth:
        service.dispatch_get('/v1/projects/proj-demo/terminal-history?agent=mobile&namespace_epoch=4')
    assert missing_auth.value.status_code == 401

    pairing = service.create_pairing_payload(gateway_url='http://127.0.0.1:8787')
    _, claim = service.dispatch_post(
        '/v1/pairing/claim',
        {'pairing_code': str(pairing['pairing_code'])},
    )
    token = str(claim['device_token'])
    with pytest.raises(MobileGatewayError) as stale:
        service.dispatch_get(
            '/v1/projects/proj-demo/terminal-history?agent=mobile&namespace_epoch=3',
            {'Authorization': f'Bearer {token}'},
        )
    assert stale.value.status_code == 409

    with pytest.raises(MobileGatewayError) as bad_epoch:
        service.dispatch_get(
            '/v1/projects/proj-demo/terminal-history?agent=mobile&namespace_epoch=bad',
            {'Authorization': f'Bearer {token}'},
        )
    assert bad_epoch.value.status_code == 400


def test_agent_conversation_reads_project_view_without_terminal_scope(tmp_path: Path) -> None:
    fake = _FakeCcbdClient()
    service = _service(fake, mobile_dir=tmp_path / 'mobile')
    pairing = service.create_pairing_payload(
        gateway_url='http://127.0.0.1:8787',
        scopes=('view',),
    )
    _, claim = service.dispatch_post('/v1/pairing/claim', {'pairing_code': str(pairing['pairing_code'])})

    status, payload = service.dispatch_get(
        '/v1/projects/proj-demo/agents/mobile/conversation?namespace_epoch=4&limit=20',
        {'Authorization': f'Bearer {claim["device_token"]}'},
    )

    assert status == 200
    conversation = payload['conversation']
    assert conversation['project_id'] == 'proj-demo'
    assert conversation['agent'] == 'mobile'
    assert conversation['namespace_epoch'] == 4
    assert conversation['items'] == []
    public_json = json.dumps(payload)
    assert 'terminal_input' not in public_json
    assert 'tmux.sock' not in public_json
    assert 'ccb-demo' not in public_json
    assert fake.calls == [('project_view', 1)]


def test_agent_conversation_includes_completed_comms_reply_preview(tmp_path: Path) -> None:
    project_root = tmp_path / 'repo'
    snapshot_dir = project_root / '.ccb' / 'ccbd' / 'snapshots'
    snapshot_dir.mkdir(parents=True)
    (snapshot_dir / 'job_mobile_reply.json').write_text(
        json.dumps(
            {
                'latest_decision': {
                    'reply': 'answer from mobile_probe',
                },
                'latest_reply_preview': 'preview fallback',
            }
        ),
        encoding='utf-8',
    )
    (snapshot_dir / 'job_mobile_old_reply.json').write_text(
        json.dumps(
            {
                'latest_decision': {
                    'reply': 'older answer from mobile_probe',
                },
            }
        ),
        encoding='utf-8',
    )
    service = _service(
        _FakeCcbdClientWithConversationComms(),
        project_root=project_root,
        mobile_dir=tmp_path / 'mobile',
    )
    pairing = service.create_pairing_payload(
        gateway_url='http://127.0.0.1:8787',
        scopes=('view',),
    )
    _, claim = service.dispatch_post(
        '/v1/pairing/claim',
        {'pairing_code': str(pairing['pairing_code'])},
    )

    status, payload = service.dispatch_get(
        '/v1/projects/proj-demo/agents/mobile/conversation?namespace_epoch=4&limit=20',
        {'Authorization': f'Bearer {claim["device_token"]}'},
    )

    assert status == 200
    items = payload['conversation']['items']
    assert items == []
    assert 'wrong target' not in json.dumps(payload)


@pytest.mark.parametrize('provider', ['kimi', 'opencode', 'unknown-provider'])
def test_non_native_provider_keeps_safe_structured_conversation_fallback(
    tmp_path: Path,
    provider: str,
) -> None:
    project_root = tmp_path / 'repo'
    snapshot_dir = project_root / '.ccb' / 'ccbd' / 'snapshots'
    snapshot_dir.mkdir(parents=True)
    (snapshot_dir / 'job_mobile_reply.json').write_text(
        json.dumps({'latest_decision': {'reply': 'structured completion'}}),
        encoding='utf-8',
    )
    (snapshot_dir / 'job_mobile_old_reply.json').write_text(
        json.dumps({'latest_decision': {'reply': 'older structured completion'}}),
        encoding='utf-8',
    )

    class _ProviderClient(_FakeCcbdClientWithConversationComms):
        def project_view(self, *, schema_version: int = 1) -> dict[str, object]:
            payload = super().project_view(schema_version=schema_version)
            payload['view']['agents'][0]['provider'] = provider
            return payload

    service = _service(_ProviderClient(), project_root=project_root, mobile_dir=tmp_path / 'mobile')
    pairing = service.create_pairing_payload(gateway_url='http://127.0.0.1:8787', scopes=('view',))
    _, claim = service.dispatch_post('/v1/pairing/claim', {'pairing_code': str(pairing['pairing_code'])})
    _, payload = service.dispatch_get(
        '/v1/projects/proj-demo/agents/mobile/conversation?namespace_epoch=4&limit=20',
        {'Authorization': f'Bearer {claim["device_token"]}'},
    )

    items = payload['conversation']['items']
    assert [item['kind'] for item in items] == [
        'user_message', 'agent_reply', 'user_message', 'agent_reply',
    ]
    assert [item['body'] for item in items] == [
        'older question from phone', 'older structured completion',
        'question from phone', 'structured completion',
    ]
    assert all('terminal' not in str(item.get('source') or '') for item in items)
    assert all(item.get('kind') not in {'log', 'diff'} for item in items)


def test_agent_conversation_prefers_terminal_scrollback_over_comms(tmp_path: Path) -> None:
    project_root = tmp_path / 'repo'
    snapshot_dir = project_root / '.ccb' / 'ccbd' / 'snapshots'
    snapshot_dir.mkdir(parents=True)
    (snapshot_dir / 'job_mobile_reply.json').write_text(
        json.dumps({'latest_decision': {'reply': 'stale CCB_REPLY answer'}}),
        encoding='utf-8',
    )

    def history_factory(target) -> dict[str, object]:
        assert target.agent == 'mobile'
        assert target.namespace_epoch == 4
        return {
            'agent': 'mobile',
            'history_scope': 'tmux_scrollback',
            'source_pane_id': '%2',
            'blocks': [
                {
                    'id': 'pane-1',
                    'type': 'log',
                    'title': 'Log',
                    'text': 'real pane assistant response',
                },
                {
                    'id': 'pane-2',
                    'type': 'command',
                    'title': 'Command',
                    'text': '› real pane input',
                },
            ],
        }

    service = _service(
        _FakeCcbdClientWithConversationComms(),
        project_root=project_root,
        mobile_dir=tmp_path / 'mobile',
        terminal_history_factory=history_factory,
    )
    pairing = service.create_pairing_payload(
        gateway_url='http://127.0.0.1:8787',
        scopes=('view',),
    )
    _, claim = service.dispatch_post(
        '/v1/pairing/claim',
        {'pairing_code': str(pairing['pairing_code'])},
    )

    status, payload = service.dispatch_get(
        '/v1/projects/proj-demo/agents/mobile/conversation?namespace_epoch=4&limit=20',
        {'Authorization': f'Bearer {claim["device_token"]}'},
    )

    assert status == 200
    items = payload['conversation']['items']
    assert items == []
    public_json = json.dumps(payload)
    assert 'stale CCB_REPLY answer' not in public_json
    assert 'question from phone' not in public_json


def test_agent_conversation_prefers_codex_native_transcript(tmp_path: Path) -> None:
    project_root = tmp_path / 'repo'
    snapshot_dir = project_root / '.ccb' / 'ccbd' / 'snapshots'
    snapshot_dir.mkdir(parents=True)
    (snapshot_dir / 'job_mobile_probe.json').write_text(
        json.dumps({'latest_decision': {'reply': 'stale ask snapshot'}}),
        encoding='utf-8',
    )
    jobs_dir = project_root / '.ccb' / 'agents' / 'mobile'
    jobs_dir.mkdir(parents=True)
    (jobs_dir / 'jobs.jsonl').write_text(
        json.dumps(
            {
                'job_id': 'job_mobile_probe',
                'status': 'completed',
                'agent_name': 'mobile',
                'created_at': '2026-06-25T12:00:00Z',
                'request': {
                    'body': 'stale ask prompt',
                    'route_options': {'source': 'mobile_gateway'},
                },
            }
        )
        + '\n',
        encoding='utf-8',
    )
    _write_codex_rollout(
        project_root,
        agent='mobile',
        thread_id='thread-native',
        records=[
            {
                'type': 'response_item',
                'payload': {
                    'type': 'message',
                    'role': 'developer',
                    'content': [{'type': 'input_text', 'text': 'hidden developer'}],
                },
            },
            {
                'type': 'response_item',
                'payload': {
                    'type': 'message',
                    'role': 'user',
                    'content': [{'type': 'input_text', 'text': 'hidden context'}],
                },
            },
            {
                'timestamp': '2026-06-25T12:00:00.000Z',
                'type': 'event_msg',
                'payload': {
                    'type': 'user_message',
                    'message': 'native question',
                },
            },
            {
                'timestamp': '2026-06-25T12:00:01.000Z',
                'type': 'event_msg',
                'payload': {
                    'type': 'agent_message',
                    'message': 'native answer',
                },
            },
            {
                'timestamp': '2026-06-25T12:00:02.000Z',
                'type': 'event_msg',
                'payload': {
                    'type': 'user_message',
                    'message': (
                        'CCB_REQ_ID: job_mobile_probe\n\n'
                        'clean prompt\n\n'
                        'CCB_REPLY_MODE: compact\n\n'
                        'CCB reply guidance:\n'
                        '- Answer directly and concisely.\n'
                        '- Avoid raw logs.'
                    ),
                },
            },
        ],
    )

    def history_factory(target):
        return {
            'history_scope': 'tmux_scrollback',
            'source_pane_id': target.pane_id,
            'blocks': [
                {
                    'id': 'old-input',
                    'type': 'command',
                    'title': 'Command',
                    'text': 'stale pane prompt',
                },
                {
                    'id': 'old-output',
                    'type': 'log',
                    'title': 'Terminal output',
                    'text': 'stale pane answer',
                },
            ],
        }

    service = _service(
        _FakeCcbdClient(),
        project_root=project_root,
        mobile_dir=tmp_path / 'mobile',
        terminal_history_factory=history_factory,
    )
    pairing = service.create_pairing_payload(
        gateway_url='http://127.0.0.1:8787',
        scopes=('view',),
    )
    _, claim = service.dispatch_post(
        '/v1/pairing/claim',
        {'pairing_code': str(pairing['pairing_code'])},
    )

    status, payload = service.dispatch_get(
        '/v1/projects/proj-demo/agents/mobile/conversation?namespace_epoch=4&limit=20',
        {'Authorization': f'Bearer {claim["device_token"]}'},
    )

    assert status == 200
    items = payload['conversation']['items']
    assert [item.get('source') for item in items] == [
        'provider_native/codex',
        'provider_native/codex',
        'provider_native/codex',
    ]
    assert [(item['kind'], item['body']) for item in items] == [
        ('user_message', 'native question'),
        ('agent_reply', 'native answer'),
        ('user_message', 'clean prompt'),
    ]
    public_json = json.dumps(payload)
    assert 'Ready for the next task.' not in public_json
    assert 'reply-content-1' not in public_json
    assert 'hidden developer' not in public_json
    assert 'hidden context' not in public_json
    assert 'CCB_REQ_ID' not in public_json
    assert 'CCB_REPLY_MODE' not in public_json
    assert 'CCB reply guidance' not in public_json
    assert 'stale ask prompt' not in public_json
    assert 'stale ask snapshot' not in public_json
    assert 'stale pane prompt' not in public_json
    assert 'stale pane answer' not in public_json


def test_agent_conversation_skips_codex_local_controls_and_backfills_real_thread(
    tmp_path: Path,
) -> None:
    project_root = tmp_path / 'repo'
    _write_codex_rollout(
        project_root,
        agent='mobile',
        thread_id='real-thread',
        created_at=1782350000,
        updated_at=1782350001,
        records=[
            {
                'timestamp': '2026-06-25T12:00:00.000Z',
                'type': 'event_msg',
                'payload': {
                    'type': 'user_message',
                    'message': 'real question before local controls',
                },
            },
            {
                'timestamp': '2026-06-25T12:00:01.000Z',
                'type': 'event_msg',
                'payload': {
                    'type': 'agent_message',
                    'message': 'real answer before local controls',
                },
            },
        ],
    )
    _write_codex_rollout(
        project_root,
        agent='mobile',
        thread_id='clear-thread',
        created_at=1782350010,
        updated_at=1782350011,
        records=[
            {
                'timestamp': '2026-06-25T12:01:00.000Z',
                'type': 'event_msg',
                'payload': {
                    'type': 'user_message',
                    'message': (
                        '<command-name>/clear</command-name>\n'
                        '<command-message>clear</command-message> '
                        '<command-args></command-args>'
                    ),
                },
            },
        ],
    )
    _write_codex_rollout(
        project_root,
        agent='mobile',
        thread_id='caveat-thread',
        created_at=1782350020,
        updated_at=1782350021,
        records=[
            {
                'timestamp': '2026-06-25T12:02:00.000Z',
                'type': 'event_msg',
                'payload': {
                    'type': 'user_message',
                    'message': (
                        '<local-command-caveat>Caveat: local command metadata. '
                        'Do not respond.</local-command-caveat>'
                    ),
                },
            },
        ],
    )

    service = _service(
        _FakeCcbdClient(),
        project_root=project_root,
        mobile_dir=tmp_path / 'mobile',
    )
    pairing = service.create_pairing_payload(
        gateway_url='http://127.0.0.1:8787',
        scopes=('view',),
    )
    _, claim = service.dispatch_post(
        '/v1/pairing/claim',
        {'pairing_code': str(pairing['pairing_code'])},
    )

    status, payload = service.dispatch_get(
        '/v1/projects/proj-demo/agents/mobile/conversation?namespace_epoch=4&limit=20',
        {'Authorization': f'Bearer {claim["device_token"]}'},
    )

    assert status == 200
    items = payload['conversation']['items']
    assert [(item['kind'], item['body']) for item in items] == [
        ('user_message', 'real question before local controls'),
        ('agent_reply', 'real answer before local controls'),
    ]
    assert '<command-name>' not in json.dumps(payload)
    assert '<local-command-caveat>' not in json.dumps(payload)


def test_agent_conversation_keeps_codex_response_assistant_with_event_user(
    tmp_path: Path,
) -> None:
    project_root = tmp_path / 'repo'
    _write_codex_rollout(
        project_root,
        agent='mobile',
        thread_id='thread-native',
        records=[
            {
                'timestamp': '2026-06-25T12:00:00.000Z',
                'type': 'response_item',
                'payload': {
                    'type': 'message',
                    'role': 'user',
                    'content': [{'type': 'input_text', 'text': 'hidden context'}],
                },
            },
            {
                'timestamp': '2026-06-25T12:00:01.000Z',
                'type': 'event_msg',
                'payload': {
                    'type': 'user_message',
                    'message': 'visible prompt',
                },
            },
            {
                'timestamp': '2026-06-25T12:00:02.000Z',
                'type': 'response_item',
                'payload': {
                    'type': 'message',
                    'role': 'assistant',
                    'content': [{'type': 'output_text', 'text': 'live partial answer'}],
                },
            },
        ],
    )
    service = _service(
        _FakeCcbdClient(),
        project_root=project_root,
        mobile_dir=tmp_path / 'mobile',
    )
    pairing = service.create_pairing_payload(
        gateway_url='http://127.0.0.1:8787',
        scopes=('view',),
    )
    _, claim = service.dispatch_post(
        '/v1/pairing/claim',
        {'pairing_code': str(pairing['pairing_code'])},
    )

    status, payload = service.dispatch_get(
        '/v1/projects/proj-demo/agents/mobile/conversation?namespace_epoch=4&limit=20',
        {'Authorization': f'Bearer {claim["device_token"]}'},
    )

    assert status == 200
    native_items = [
        item for item in payload['conversation']['items']
        if item.get('source') == 'provider_native/codex'
    ]
    assert [(item['kind'], item['body']) for item in native_items] == [
        ('user_message', 'visible prompt'),
        ('agent_reply', 'live partial answer'),
    ]
    assert native_items[1]['sent_at'] == '2026-06-25T12:00:02.000Z'
    assert 'completed_at' not in native_items[1]
    assert 'hidden context' not in json.dumps(payload)


def test_codex_native_user_file_link_has_download_attachment(tmp_path: Path) -> None:
    project_root = tmp_path / 'repo'
    release_path = tmp_path / 'downloads' / 'ccb-mobile-release.apk'
    release_path.parent.mkdir()
    release_path.write_bytes(b'release apk body\n')
    _write_codex_rollout(
        project_root,
        agent='mobile',
        thread_id='thread-native-file',
        records=[
            {
                'timestamp': '2026-06-25T12:00:00.000Z',
                'type': 'event_msg',
                'payload': {
                    'type': 'user_message',
                    'message': f'[release]({release_path.as_uri()})',
                },
            },
        ],
    )
    service = _service(
        _FakeCcbdClient(),
        project_root=project_root,
        mobile_dir=tmp_path / 'mobile',
    )
    pairing = service.create_pairing_payload(
        gateway_url='http://127.0.0.1:8787',
        scopes=('view',),
    )
    _, claim = service.dispatch_post(
        '/v1/pairing/claim',
        {'pairing_code': str(pairing['pairing_code'])},
    )

    _, payload = service.dispatch_get(
        '/v1/projects/proj-demo/agents/mobile/conversation?namespace_epoch=4&limit=20',
        {'Authorization': f'Bearer {claim["device_token"]}'},
    )

    item = payload['conversation']['items'][0]
    assert item['body'].startswith('[release](ccb-artifact://')
    assert [attachment['file_name'] for attachment in item['attachments']] == [
        'ccb-mobile-release.apk',
    ]


def test_codex_native_agent_workspace_file_link_is_downloadable(tmp_path: Path) -> None:
    project_root = tmp_path / 'repo'
    report_path = (
        project_root
        / '.ccb'
        / 'workspaces'
        / 'mobile'
        / 'tmp'
        / 'workspace-report.pdf'
    )
    report_path.parent.mkdir(parents=True)
    report_path.write_bytes(b'workspace pdf body\n')
    _write_codex_rollout(
        project_root,
        agent='mobile',
        thread_id='thread-native-workspace-file',
        records=[
            {
                'timestamp': '2026-06-25T12:00:01.000Z',
                'type': 'response_item',
                'payload': {
                    'type': 'message',
                    'role': 'assistant',
                    'content': [
                        {
                            'type': 'output_text',
                            'text': f'[workspace report]({report_path})',
                        }
                    ],
                },
            },
        ],
    )
    service = _service(
        _FakeCcbdClient(),
        project_root=project_root,
        mobile_dir=tmp_path / 'mobile',
    )
    pairing = service.create_pairing_payload(
        gateway_url='http://127.0.0.1:8787',
        scopes=('view', 'file_download'),
    )
    _, claim = service.dispatch_post(
        '/v1/pairing/claim',
        {'pairing_code': str(pairing['pairing_code'])},
    )

    _, payload = service.dispatch_get(
        '/v1/projects/proj-demo/agents/mobile/conversation?namespace_epoch=4&limit=20',
        {'Authorization': f'Bearer {claim["device_token"]}'},
    )

    item = payload['conversation']['items'][0]
    assert item['kind'] == 'agent_reply'
    assert item['body'].startswith('[workspace report](ccb-artifact://')
    assert [attachment['file_name'] for attachment in item['attachments']] == [
        'workspace-report.pdf',
    ]
    attachment = item['attachments'][0]
    status, content, headers = service.dispatch_file_download(
        f'/v1/projects/proj-demo/agents/mobile/files/{attachment["file_id"]}',
        {'Authorization': f'Bearer {claim["device_token"]}'},
    )
    assert status == 200
    assert content == b'workspace pdf body\n'
    assert headers['content-type'] == 'application/pdf'


def test_agent_conversation_completes_codex_response_assistant_from_task_marker(
    tmp_path: Path,
) -> None:
    project_root = tmp_path / 'repo'
    _write_codex_rollout(
        project_root,
        agent='mobile',
        thread_id='thread-native',
        records=[
            {
                'timestamp': '2026-06-25T12:00:00.000Z',
                'type': 'event_msg',
                'payload': {
                    'type': 'user_message',
                    'message': 'visible prompt',
                },
            },
            {
                'timestamp': '2026-06-25T12:00:01.000Z',
                'type': 'response_item',
                'payload': {
                    'type': 'message',
                    'role': 'assistant',
                    'content': [{'type': 'output_text', 'text': 'completed answer'}],
                },
            },
            {
                'timestamp': '2026-06-25T12:00:05.000Z',
                'type': 'event_msg',
                'payload': {'type': 'task_complete'},
            },
        ],
    )
    service = _service(
        _FakeCcbdClient(),
        project_root=project_root,
        mobile_dir=tmp_path / 'mobile',
    )
    pairing = service.create_pairing_payload(
        gateway_url='http://127.0.0.1:8787',
        scopes=('view',),
    )
    _, claim = service.dispatch_post(
        '/v1/pairing/claim',
        {'pairing_code': str(pairing['pairing_code'])},
    )

    status, payload = service.dispatch_get(
        '/v1/projects/proj-demo/agents/mobile/conversation?namespace_epoch=4&limit=20',
        {'Authorization': f'Bearer {claim["device_token"]}'},
    )

    assert status == 200
    native_items = [
        item for item in payload['conversation']['items']
        if item.get('source') == 'provider_native/codex'
    ]
    assert [(item['kind'], item['body']) for item in native_items] == [
        ('user_message', 'visible prompt'),
        ('agent_reply', 'completed answer'),
    ]
    assert native_items[1]['started_at'] == '2026-06-25T12:00:01.000Z'
    assert native_items[1]['completed_at'] == '2026-06-25T12:00:05.000Z'
    assert native_items[1]['sent_at'] == '2026-06-25T12:00:05.000Z'
    assert native_items[1]['duration_ms'] == 4000


def test_agent_conversation_prefers_pi_native_transcript_and_refreshes_cache(
    tmp_path: Path,
) -> None:
    project_root = tmp_path / 'repo'
    agent_work_dir = project_root / '.ccb' / 'workspaces' / 'mobile'
    agent_work_dir.mkdir(parents=True)
    jobs_dir = project_root / '.ccb' / 'agents' / 'mobile'
    jobs_dir.mkdir(parents=True)
    (jobs_dir / 'jobs.jsonl').write_text(
        json.dumps(
            {
                'job_id': 'stale-pi-job',
                'status': 'completed',
                'agent_name': 'mobile',
                'request': {'body': 'stale structured prompt'},
            }
        )
        + '\n',
        encoding='utf-8',
    )
    transcript_path = _write_pi_transcript(
        project_root,
        agent='mobile',
        session_id='pi-session-native',
        cwd=agent_work_dir,
        records=[
            {
                'type': 'model_change',
                'id': 'pi-config',
                'parentId': None,
                'timestamp': '2026-06-25T12:00:00.000Z',
            },
            {
                'type': 'message',
                'id': 'pi-user-1',
                'parentId': 'pi-config',
                'timestamp': '2026-06-25T12:00:01.000Z',
                'message': {
                    'role': 'user',
                    'content': [{'type': 'text', 'text': 'pi native question'}],
                },
            },
            {
                'type': 'message',
                'id': 'pi-assistant-1',
                'parentId': 'pi-user-1',
                'timestamp': '2026-06-25T12:00:02.000Z',
                'message': {
                    'role': 'assistant',
                    'content': [
                        {'type': 'thinking', 'thinking': 'hidden pi thinking'},
                        {'type': 'text', 'text': 'pi native answer'},
                        {
                            'type': 'toolCall',
                            'id': 'call-hidden',
                            'name': 'bash',
                            'arguments': {'command': 'hidden tool command'},
                        },
                    ],
                },
            },
            {
                'type': 'message',
                'id': 'pi-branch-user',
                'parentId': 'pi-user-1',
                'timestamp': '2026-06-25T12:00:03.000Z',
                'message': {
                    'role': 'user',
                    'content': [{'type': 'text', 'text': 'inactive branch question'}],
                },
            },
            {
                'type': 'message',
                'id': 'pi-branch-assistant',
                'parentId': 'pi-branch-user',
                'timestamp': '2026-06-25T12:00:04.000Z',
                'message': {
                    'role': 'assistant',
                    'content': [{'type': 'text', 'text': 'inactive branch answer'}],
                },
            },
            {
                'type': 'message',
                'id': 'pi-user-2',
                'parentId': 'pi-assistant-1',
                'timestamp': '2026-06-25T12:00:05.000Z',
                'message': {
                    'role': 'user',
                    'content': [
                        {
                            'type': 'text',
                            'text': (
                                'CCB_REQ_ID: pi-request\n\n'
                                'clean pi prompt\n\n'
                                'CCB reply guidance:\n'
                                '- Answer directly and concisely.\n'
                            ),
                        }
                    ],
                },
            },
            {
                'type': 'message',
                'id': 'pi-assistant-2a',
                'parentId': 'pi-user-2',
                'timestamp': '2026-06-25T12:00:06.000Z',
                'message': {
                    'role': 'assistant',
                    'content': [{'type': 'text', 'text': 'step one'}],
                },
            },
            {
                'type': 'message',
                'id': 'pi-tool-result',
                'parentId': 'pi-assistant-2a',
                'timestamp': '2026-06-25T12:00:07.000Z',
                'message': {
                    'role': 'toolResult',
                    'content': [{'type': 'text', 'text': 'hidden tool result'}],
                },
            },
            {
                'type': 'message',
                'id': 'pi-assistant-2b',
                'parentId': 'pi-tool-result',
                'timestamp': '2026-06-25T12:00:08.000Z',
                'message': {
                    'role': 'assistant',
                    'content': [{'type': 'text', 'text': 'step two'}],
                },
            },
        ],
        partial_tail='{"type":"message","id":"partial"',
    )
    _write_pi_transcript(
        project_root,
        agent='mobile',
        session_id='wrong-project',
        cwd=tmp_path / 'other-repo',
        write_binding=False,
        records=[
            {
                'type': 'message',
                'id': 'wrong-user',
                'parentId': None,
                'timestamp': '2026-06-25T11:00:01.000Z',
                'message': {
                    'role': 'user',
                    'content': [{'type': 'text', 'text': 'wrong project history'}],
                },
            },
        ],
    )

    service = _service(
        _FakePiCcbdClient(),
        project_root=project_root,
        mobile_dir=tmp_path / 'mobile',
    )
    pairing = service.create_pairing_payload(
        gateway_url='http://127.0.0.1:8787',
        scopes=('view',),
    )
    _, claim = service.dispatch_post(
        '/v1/pairing/claim',
        {'pairing_code': str(pairing['pairing_code'])},
    )
    route = '/v1/projects/proj-demo/agents/mobile/conversation?namespace_epoch=4&limit=20'
    headers = {'Authorization': f'Bearer {claim["device_token"]}'}

    status, payload = service.dispatch_get(route, headers)

    assert status == 200
    items = payload['conversation']['items']
    assert [item.get('source') for item in items] == ['provider_native/pi'] * 4
    assert [(item['kind'], item['body']) for item in items] == [
        ('user_message', 'pi native question'),
        ('agent_reply', 'pi native answer'),
        ('user_message', 'clean pi prompt'),
        ('agent_reply', 'step one\n\nstep two'),
    ]
    assert {item['session_id'] for item in items} == {'pi-session-native'}
    assert items[0]['sent_at'] == '2026-06-25T12:00:01.000Z'
    assert items[1]['completed_at'] == '2026-06-25T12:00:02.000Z'
    public_json = json.dumps(payload)
    for hidden in (
        'hidden pi thinking',
        'hidden tool command',
        'hidden tool result',
        'inactive branch question',
        'inactive branch answer',
        'wrong project history',
        'CCB_REQ_ID',
        'CCB reply guidance',
        'stale structured prompt',
    ):
        assert hidden not in public_json

    with transcript_path.open('a', encoding='utf-8') as stream:
        stream.write('\n')
        stream.write(json.dumps({
            'type': 'message',
            'id': 'pi-user-3',
            'parentId': 'pi-assistant-2b',
            'timestamp': '2026-06-25T12:00:09.000Z',
            'message': {
                'role': 'user',
                'content': [{'type': 'text', 'text': 'new pi question'}],
            },
        }) + '\n')
        stream.write(json.dumps({
            'type': 'message',
            'id': 'pi-assistant-3',
            'parentId': 'pi-user-3',
            'timestamp': '2026-06-25T12:00:10.000Z',
            'message': {
                'role': 'assistant',
                'content': [{'type': 'text', 'text': 'new pi answer'}],
            },
        }) + '\n')

    _, refreshed = service.dispatch_get(route, headers)
    assert [(item['kind'], item['body']) for item in refreshed['conversation']['items'][-2:]] == [
        ('user_message', 'new pi question'),
        ('agent_reply', 'new pi answer'),
    ]


def test_agent_conversation_preserves_pi_session_boundaries(tmp_path: Path) -> None:
    project_root = tmp_path / 'repo'
    work_dir = project_root / '.ccb' / 'workspaces' / 'mobile'
    work_dir.mkdir(parents=True)
    _write_pi_transcript(
        project_root,
        agent='mobile',
        session_id='pi-session-1',
        cwd=work_dir,
        timestamp='2026-06-25T11:00:00.000Z',
        records=[_pi_message_record(
            record_id='first-user',
            parent_id=None,
            role='user',
            text='first session question',
            timestamp='2026-06-25T11:00:01.000Z',
        )],
    )
    _write_pi_transcript(
        project_root,
        agent='mobile',
        session_id='pi-session-2',
        cwd=work_dir,
        timestamp='2026-06-25T12:00:00.000Z',
        records=[_pi_message_record(
            record_id='second-user',
            parent_id=None,
            role='user',
            text='second session question',
            timestamp='2026-06-25T12:00:01.000Z',
        )],
    )

    items = _conversation_items_for(
        _service(_FakePiCcbdClient(), project_root=project_root, mobile_dir=tmp_path / 'mobile')
    )

    assert [(item['body'], item['session_id']) for item in items] == [
        ('first session question', 'pi-session-1'),
        ('second session question', 'pi-session-2'),
    ]


def test_agent_conversation_reads_omp_native_transcript_and_refreshes_cache(
    tmp_path: Path,
) -> None:
    project_root = tmp_path / 'repo'
    work_dir = project_root / '.ccb' / 'workspaces' / 'mobile'
    work_dir.mkdir(parents=True)
    transcript_path = _write_pi_family_transcript(
        project_root,
        provider='omp',
        agent='mobile',
        session_id='omp-session-native',
        cwd=work_dir,
        records=[
            _pi_message_record(
                record_id='omp-user',
                parent_id=None,
                role='user',
                text='omp native question',
                timestamp='2026-06-25T12:00:01.000Z',
            ),
            {
                'type': 'message',
                'id': 'omp-answer',
                'parentId': 'omp-user',
                'timestamp': '2026-06-25T12:00:02.000Z',
                'message': {
                    'role': 'assistant',
                    'content': [
                        {'type': 'thinking', 'thinking': 'hidden omp thinking'},
                        {'type': 'text', 'text': 'omp native answer'},
                        {'type': 'toolCall', 'name': 'bash', 'arguments': {'command': 'hidden'}},
                    ],
                },
            },
            _pi_message_record(
                record_id='inactive-user',
                parent_id='omp-user',
                role='user',
                text='inactive omp branch',
                timestamp='2026-06-25T12:00:03.000Z',
            ),
            _pi_message_record(
                record_id='omp-user-2',
                parent_id='omp-answer',
                role='user',
                text='omp follow-up',
                timestamp='2026-06-25T12:00:04.000Z',
            ),
        ],
        partial_tail='{"type":"message","id":"partial"',
    )
    wrong_path = _write_pi_family_transcript(
        project_root,
        provider='omp',
        agent='mobile',
        session_id='wrong-work-dir',
        cwd=tmp_path / 'other',
        records=[_pi_message_record(
            record_id='wrong-user',
            parent_id=None,
            role='user',
            text='wrong omp work dir',
            timestamp='2026-06-25T10:00:00.000Z',
        )],
        write_binding=False,
    )
    (wrong_path.parent / 'symlink.jsonl').symlink_to(transcript_path)
    service = _service(
        _FakeOmpCcbdClient(),
        project_root=project_root,
        mobile_dir=tmp_path / 'mobile',
    )

    items = _conversation_items_for(service)

    assert [(item['kind'], item['body']) for item in items] == [
        ('user_message', 'omp native question'),
        ('agent_reply', 'omp native answer'),
        ('user_message', 'omp follow-up'),
    ]
    assert [item['source'] for item in items] == ['provider_native/omp'] * 3
    assert {item['session_id'] for item in items} == {'omp-session-native'}
    assert 'hidden omp thinking' not in json.dumps(items)
    assert 'inactive omp branch' not in json.dumps(items)
    assert 'wrong omp work dir' not in json.dumps(items)

    with transcript_path.open('a', encoding='utf-8') as stream:
        stream.write('\n' + json.dumps(_pi_message_record(
            record_id='omp-answer-2',
            parent_id='omp-user-2',
            role='assistant',
            text='omp refreshed answer',
            timestamp='2026-06-25T12:00:05.000Z',
        )) + '\n')

    refreshed = _conversation_items_for(service)
    assert refreshed[-1]['body'] == 'omp refreshed answer'


def test_pi_native_conversation_rejects_out_of_tree_bound_session(tmp_path: Path) -> None:
    project_root = tmp_path / 'repo'
    work_dir = project_root / '.ccb' / 'workspaces' / 'mobile'
    work_dir.mkdir(parents=True)
    _write_pi_transcript(
        project_root,
        agent='mobile',
        session_id='managed-session',
        cwd=work_dir,
        records=[_pi_message_record(
            record_id='managed-user',
            parent_id=None,
            role='user',
            text='must stay hidden after invalid binding',
            timestamp='2026-06-25T12:00:01.000Z',
        )],
    )
    outside = tmp_path / 'outside.jsonl'
    outside.write_text('{}\n', encoding='utf-8')
    binding_path = project_root / '.ccb' / '.pi-mobile-session'
    binding = json.loads(binding_path.read_text(encoding='utf-8'))
    binding['pi_session_path'] = str(outside)
    binding_path.write_text(json.dumps(binding), encoding='utf-8')

    items = _conversation_items_for(
        _service(_FakePiCcbdClient(), project_root=project_root, mobile_dir=tmp_path / 'mobile')
    )

    assert items == []


@pytest.mark.parametrize(
    ('client', 'provider'),
    (
        (_FakePiCcbdClientWithConversationComms(), 'pi'),
        (_FakeOmpCcbdClientWithConversationComms(), 'omp'),
    ),
)
def test_agent_conversation_does_not_fallback_without_native_transcript(
    tmp_path: Path,
    client: _FakeCcbdClient,
    provider: str,
) -> None:
    project_root = tmp_path / 'repo'
    snapshot_dir = project_root / '.ccb' / 'ccbd' / 'snapshots'
    snapshot_dir.mkdir(parents=True)
    (snapshot_dir / 'job_mobile_reply.json').write_text(
        json.dumps({'latest_decision': {'reply': f'stale {provider} completion'}}),
        encoding='utf-8',
    )
    jobs_dir = project_root / '.ccb' / 'agents' / 'mobile'
    jobs_dir.mkdir(parents=True)
    (jobs_dir / 'jobs.jsonl').write_text(
        json.dumps({
            'job_id': f'stale-{provider}-job',
            'status': 'completed',
            'agent_name': 'mobile',
            'request': {'body': f'stale {provider} job prompt'},
        }) + '\n',
        encoding='utf-8',
    )
    service = _service(
        client,
        project_root=project_root,
        mobile_dir=tmp_path / 'mobile',
    )
    pairing = service.create_pairing_payload(
        gateway_url='http://127.0.0.1:8787',
        scopes=('view',),
    )
    _, claim = service.dispatch_post(
        '/v1/pairing/claim',
        {'pairing_code': str(pairing['pairing_code'])},
    )

    _, payload = service.dispatch_get(
        '/v1/projects/proj-demo/agents/mobile/conversation?namespace_epoch=4&limit=20',
        {'Authorization': f'Bearer {claim["device_token"]}'},
    )

    assert payload['conversation']['items'] == []
    assert f'stale {provider} completion' not in json.dumps(payload)
    assert f'stale {provider} job prompt' not in json.dumps(payload)
    assert 'question from phone' not in json.dumps(payload)


def test_agent_conversation_prefers_claude_native_transcript(tmp_path: Path) -> None:
    project_root = tmp_path / 'repo'
    snapshot_dir = project_root / '.ccb' / 'ccbd' / 'snapshots'
    snapshot_dir.mkdir(parents=True)
    (snapshot_dir / 'job_mobile_probe.json').write_text(
        json.dumps({'latest_decision': {'reply': 'stale ask snapshot'}}),
        encoding='utf-8',
    )
    _write_claude_transcript(
        project_root,
        agent='mobile',
        session_id='claude-session-native',
        records=[
            {
                'type': 'system',
                'timestamp': '2026-06-25T11:59:59.000Z',
                'content': 'hidden system prompt',
            },
            {
                'uuid': 'user-1',
                'timestamp': '2026-06-25T12:00:00.000Z',
                'type': 'user',
                'message': {
                    'role': 'user',
                    'content': [{'type': 'text', 'text': 'claude native question'}],
                },
            },
            {
                'uuid': 'assistant-1',
                'timestamp': '2026-06-25T12:00:01.000Z',
                'type': 'assistant',
                'message': {
                    'id': 'msg_1',
                    'role': 'assistant',
                    'content': [
                        {'type': 'thinking', 'text': 'hidden thinking'},
                        {'type': 'text', 'text': 'claude native answer'},
                    ],
                },
            },
            {
                'uuid': 'user-2',
                'timestamp': '2026-06-25T12:00:02.000Z',
                'type': 'user',
                'message': {
                    'role': 'user',
                    'content': [
                        {
                            'type': 'text',
                            'text': (
                                'CCB_REQ_ID: job_mobile_probe\n\n'
                                'clean claude prompt\n\n'
                                'CCB_REPLY_MODE: silent\n\n'
                                'CCB reply guidance:\n'
                                '- Answer directly and concisely.\n'
                            ),
                        }
                    ],
                },
            },
            {
                'uuid': 'local-clear',
                'timestamp': '2026-06-25T12:00:03.000Z',
                'type': 'user',
                'message': {
                    'role': 'user',
                    'content': (
                        '<command-name>/clear</command-name>\n'
                        '<command-message>clear</command-message> '
                        '<command-args></command-args>'
                    ),
                },
            },
            {
                'uuid': 'local-caveat',
                'timestamp': '2026-06-25T12:00:04.000Z',
                'type': 'user',
                'message': {
                    'role': 'user',
                    'content': (
                        '<local-command-caveat>Local command metadata. '
                        'Do not respond.</local-command-caveat>'
                    ),
                },
            },
        ],
    )

    def history_factory(target):
        return {
            'history_scope': 'tmux_scrollback',
            'source_pane_id': target.pane_id,
            'blocks': [
                {
                    'id': 'old-input',
                    'type': 'command',
                    'title': 'Command',
                    'text': 'stale claude pane prompt',
                },
                {
                    'id': 'old-output',
                    'type': 'log',
                    'title': 'Terminal output',
                    'text': 'stale claude pane answer',
                },
            ],
        }

    service = _service(
        _FakeClaudeCcbdClient(),
        project_root=project_root,
        mobile_dir=tmp_path / 'mobile',
        terminal_history_factory=history_factory,
    )
    pairing = service.create_pairing_payload(
        gateway_url='http://127.0.0.1:8787',
        scopes=('view',),
    )
    _, claim = service.dispatch_post(
        '/v1/pairing/claim',
        {'pairing_code': str(pairing['pairing_code'])},
    )

    status, payload = service.dispatch_get(
        '/v1/projects/proj-demo/agents/mobile/conversation?namespace_epoch=4&limit=20',
        {'Authorization': f'Bearer {claim["device_token"]}'},
    )

    assert status == 200
    items = payload['conversation']['items']
    assert [item.get('source') for item in items] == [
        'provider_native/claude',
        'provider_native/claude',
        'provider_native/claude',
    ]
    assert [(item['kind'], item['body']) for item in items] == [
        ('user_message', 'claude native question'),
        ('agent_reply', 'claude native answer'),
        ('user_message', 'clean claude prompt'),
    ]
    assert {item['session_id'] for item in items} == {'claude-session-native'}
    assert items[0]['sent_at'] == '2026-06-25T12:00:00.000Z'
    assert items[1]['completed_at'] == '2026-06-25T12:00:01.000Z'
    public_json = json.dumps(payload)
    assert 'hidden system prompt' not in public_json
    assert 'hidden thinking' not in public_json
    assert 'CCB_REQ_ID' not in public_json
    assert 'CCB_REPLY_MODE' not in public_json
    assert 'CCB reply guidance' not in public_json
    assert '<command-name>' not in public_json
    assert '<local-command-caveat>' not in public_json
    assert 'stale ask snapshot' not in public_json
    assert 'stale claude pane prompt' not in public_json
    assert 'stale claude pane answer' not in public_json


def test_agent_conversation_discovers_claude_native_transcript_from_projects_root(
    tmp_path: Path,
) -> None:
    project_root = tmp_path / 'repo'
    _write_claude_transcript(
        project_root,
        agent='mobile',
        session_id='claude-session-discovered',
        records=[
            {
                'uuid': 'user-1',
                'timestamp': '2026-06-25T12:00:00.000Z',
                'type': 'user',
                'message': {
                    'role': 'user',
                    'content': [{'type': 'text', 'text': 'discovered question'}],
                },
            },
            {
                'uuid': 'assistant-1',
                'timestamp': '2026-06-25T12:00:01.000Z',
                'type': 'assistant',
                'message': {
                    'role': 'assistant',
                    'content': [{'type': 'text', 'text': 'discovered answer'}],
                },
            },
        ],
        bind_session_path=False,
        use_project_dir=True,
    )

    def history_factory(target):
        return {
            'history_scope': 'tmux_scrollback',
            'source_pane_id': target.pane_id,
            'blocks': [
                {
                    'id': 'stale-output',
                    'type': 'log',
                    'title': 'Terminal output',
                    'text': 'stale fallback output',
                },
            ],
        }

    service = _service(
        _FakeClaudeCcbdClient(),
        project_root=project_root,
        mobile_dir=tmp_path / 'mobile',
        terminal_history_factory=history_factory,
    )
    pairing = service.create_pairing_payload(
        gateway_url='http://127.0.0.1:8787',
        scopes=('view',),
    )
    _, claim = service.dispatch_post(
        '/v1/pairing/claim',
        {'pairing_code': str(pairing['pairing_code'])},
    )

    status, payload = service.dispatch_get(
        '/v1/projects/proj-demo/agents/mobile/conversation?namespace_epoch=4&limit=20',
        {'Authorization': f'Bearer {claim["device_token"]}'},
    )

    assert status == 200
    items = payload['conversation']['items']
    assert [item.get('source') for item in items] == [
        'provider_native/claude',
        'provider_native/claude',
    ]
    assert [(item['kind'], item['body']) for item in items] == [
        ('user_message', 'discovered question'),
        ('agent_reply', 'discovered answer'),
    ]
    assert 'stale fallback output' not in json.dumps(payload)


def test_agent_conversation_does_not_use_terminal_history_for_claude_without_native_transcript(
    tmp_path: Path,
) -> None:
    project_root = tmp_path / 'repo'

    def history_factory(target):
        return {
            'history_scope': 'tmux_scrollback',
            'source_pane_id': target.pane_id,
            'blocks': [
                {
                    'id': 'stale-output',
                    'type': 'log',
                    'title': 'Terminal output',
                    'text': 'stale tmux fallback output',
                },
            ],
        }

    service = _service(
        _FakeClaudeCcbdClient(),
        project_root=project_root,
        mobile_dir=tmp_path / 'mobile',
        terminal_history_factory=history_factory,
    )
    pairing = service.create_pairing_payload(
        gateway_url='http://127.0.0.1:8787',
        scopes=('view',),
    )
    _, claim = service.dispatch_post(
        '/v1/pairing/claim',
        {'pairing_code': str(pairing['pairing_code'])},
    )

    status, payload = service.dispatch_get(
        '/v1/projects/proj-demo/agents/mobile/conversation?namespace_epoch=4&limit=20',
        {'Authorization': f'Bearer {claim["device_token"]}'},
    )

    assert status == 200
    items = payload['conversation']['items']
    assert all('tmux_scrollback' not in str(item.get('source')) for item in items)
    assert items == []
    assert 'stale tmux fallback output' not in json.dumps(payload)


def test_agent_conversation_groups_consecutive_codex_native_agent_messages(
    tmp_path: Path,
) -> None:
    project_root = tmp_path / 'repo'
    _write_codex_rollout(
        project_root,
        agent='mobile',
        thread_id='thread-native',
        records=[
            {
                'timestamp': '2026-06-25T12:00:00.000Z',
                'type': 'event_msg',
                'payload': {
                    'type': 'user_message',
                    'message': 'start long task',
                },
            },
            {
                'timestamp': '2026-06-25T12:00:01.000Z',
                'type': 'event_msg',
                'payload': {
                    'type': 'agent_message',
                    'message': 'step one complete',
                },
            },
            {
                'timestamp': '2026-06-25T12:00:02.000Z',
                'type': 'event_msg',
                'payload': {
                    'type': 'agent_message',
                    'message': 'step two complete',
                },
            },
            {
                'timestamp': '2026-06-25T12:00:03.000Z',
                'type': 'event_msg',
                'payload': {
                    'type': 'agent_message',
                    'message': 'final result',
                },
            },
            {
                'timestamp': '2026-06-25T12:00:03.000Z',
                'type': 'event_msg',
                'payload': {'type': 'task_complete'},
            },
        ],
    )
    service = _service(
        _FakeCcbdClient(),
        project_root=project_root,
        mobile_dir=tmp_path / 'mobile',
    )
    pairing = service.create_pairing_payload(
        gateway_url='http://127.0.0.1:8787',
        scopes=('view',),
    )
    _, claim = service.dispatch_post(
        '/v1/pairing/claim',
        {'pairing_code': str(pairing['pairing_code'])},
    )

    _, payload = service.dispatch_get(
        '/v1/projects/proj-demo/agents/mobile/conversation?namespace_epoch=4&limit=20',
        {'Authorization': f'Bearer {claim["device_token"]}'},
    )

    native_items = [
        item for item in payload['conversation']['items']
        if item.get('source') == 'provider_native/codex'
    ]
    assert [(item['kind'], item['body']) for item in native_items] == [
        ('user_message', 'start long task'),
        ('agent_reply', 'step one complete\n\nstep two complete\n\nfinal result'),
    ]
    assert native_items[0]['sent_at'] == '2026-06-25T12:00:00.000Z'
    assert native_items[1]['started_at'] == '2026-06-25T12:00:01.000Z'
    assert native_items[1]['completed_at'] == '2026-06-25T12:00:03.000Z'
    assert native_items[1]['sent_at'] == '2026-06-25T12:00:03.000Z'
    assert native_items[1]['duration_ms'] == 2000


def test_agent_conversation_starts_new_codex_agent_group_after_user_message(
    tmp_path: Path,
) -> None:
    project_root = tmp_path / 'repo'
    _write_codex_rollout(
        project_root,
        agent='mobile',
        thread_id='thread-native',
        records=[
            {
                'type': 'event_msg',
                'payload': {
                    'type': 'user_message',
                    'message': 'first prompt',
                },
            },
            {
                'type': 'event_msg',
                'payload': {
                    'type': 'agent_message',
                    'message': 'first step',
                },
            },
            {
                'type': 'event_msg',
                'payload': {
                    'type': 'agent_message',
                    'message': 'first done',
                },
            },
            {
                'type': 'event_msg',
                'payload': {
                    'type': 'user_message',
                    'message': 'second prompt',
                },
            },
            {
                'type': 'event_msg',
                'payload': {
                    'type': 'agent_message',
                    'message': 'second done',
                },
            },
        ],
    )
    service = _service(
        _FakeCcbdClient(),
        project_root=project_root,
        mobile_dir=tmp_path / 'mobile',
    )
    pairing = service.create_pairing_payload(
        gateway_url='http://127.0.0.1:8787',
        scopes=('view',),
    )
    _, claim = service.dispatch_post(
        '/v1/pairing/claim',
        {'pairing_code': str(pairing['pairing_code'])},
    )

    _, payload = service.dispatch_get(
        '/v1/projects/proj-demo/agents/mobile/conversation?namespace_epoch=4&limit=20',
        {'Authorization': f'Bearer {claim["device_token"]}'},
    )

    native_items = [
        item for item in payload['conversation']['items']
        if item.get('source') == 'provider_native/codex'
    ]
    assert [(item['kind'], item['body']) for item in native_items] == [
        ('user_message', 'first prompt'),
        ('agent_reply', 'first step\n\nfirst done'),
        ('user_message', 'second prompt'),
        ('agent_reply', 'second done'),
    ]


def test_agent_conversation_pages_latest_then_older_items(tmp_path: Path) -> None:
    project_root = tmp_path / 'repo'
    snapshot_dir = project_root / '.ccb' / 'ccbd' / 'snapshots'
    snapshot_dir.mkdir(parents=True)
    (snapshot_dir / 'job_mobile_reply.json').write_text(
        json.dumps({'latest_decision': {'reply': 'answer from mobile_probe'}}),
        encoding='utf-8',
    )
    (snapshot_dir / 'job_mobile_old_reply.json').write_text(
        json.dumps({'latest_decision': {'reply': 'older answer from mobile_probe'}}),
        encoding='utf-8',
    )
    service = _service(
        _FakeCcbdClientWithConversationComms(),
        project_root=project_root,
        mobile_dir=tmp_path / 'mobile',
    )
    pairing = service.create_pairing_payload(
        gateway_url='http://127.0.0.1:8787',
        scopes=('view',),
    )
    _, claim = service.dispatch_post(
        '/v1/pairing/claim',
        {'pairing_code': str(pairing['pairing_code'])},
    )
    headers = {'Authorization': f'Bearer {claim["device_token"]}'}

    _, latest = service.dispatch_get(
        '/v1/projects/proj-demo/agents/mobile/conversation?namespace_epoch=4&limit=2',
        headers,
    )
    latest_conversation = latest['conversation']

    assert latest_conversation['items'] == []
    assert 'next_cursor' not in latest_conversation


def test_agent_conversation_pages_codex_native_by_record_timestamp_across_threads(
    tmp_path: Path,
) -> None:
    project_root = tmp_path / 'repo'
    _write_codex_rollout(
        project_root,
        agent='mobile',
        thread_id='newer-pane-thread-created-first',
        created_at=1782350000,
        updated_at=1782350100,
        records=[
            {
                'timestamp': '2026-06-25T12:02:00.000Z',
                'type': 'event_msg',
                'payload': {
                    'type': 'user_message',
                    'message': 'fresh pane question',
                },
            },
            {
                'timestamp': '2026-06-25T12:02:01.000Z',
                'type': 'event_msg',
                'payload': {
                    'type': 'agent_message',
                    'message': 'fresh pane answer',
                },
            },
        ],
    )
    _write_codex_rollout(
        project_root,
        agent='mobile',
        thread_id='older-backfill-thread-created-second',
        created_at=1782350001,
        updated_at=1782350002,
        records=[
            {
                'timestamp': '2026-06-25T12:00:00.000Z',
                'type': 'event_msg',
                'payload': {
                    'type': 'user_message',
                    'message': 'older backfill question',
                },
            },
            {
                'timestamp': '2026-06-25T12:00:01.000Z',
                'type': 'event_msg',
                'payload': {
                    'type': 'agent_message',
                    'message': 'older backfill answer',
                },
            },
        ],
    )
    service = _service(
        _FakeCcbdClientWithConversationComms(),
        project_root=project_root,
        mobile_dir=tmp_path / 'mobile',
    )
    pairing = service.create_pairing_payload(
        gateway_url='http://127.0.0.1:8787',
        scopes=('view',),
    )
    _, claim = service.dispatch_post(
        '/v1/pairing/claim',
        {'pairing_code': str(pairing['pairing_code'])},
    )
    headers = {'Authorization': f'Bearer {claim["device_token"]}'}

    _, latest = service.dispatch_get(
        '/v1/projects/proj-demo/agents/mobile/conversation?namespace_epoch=4&limit=2',
        headers,
    )
    latest_items = latest['conversation']['items']

    assert [item['body'] for item in latest_items] == [
        'fresh pane question',
        'fresh pane answer',
    ]
    assert {item['session_id'] for item in latest_items} == {
        'newer-pane-thread-created-first'
    }
    assert [item['sent_at'] for item in latest_items] == [
        '2026-06-25T12:02:00.000Z',
        '2026-06-25T12:02:01.000Z',
    ]
    assert latest['conversation']['next_cursor'] == '2'

    _, older = service.dispatch_get(
        '/v1/projects/proj-demo/agents/mobile/conversation?namespace_epoch=4'
        f'&limit=2&cursor={latest["conversation"]["next_cursor"]}',
        headers,
    )
    assert [item['body'] for item in older['conversation']['items']] == [
        'older backfill question',
        'older backfill answer',
    ]
    assert {
        item['session_id'] for item in older['conversation']['items']
    } == {'older-backfill-thread-created-second'}


def test_agent_conversation_tails_large_codex_rollout_without_parsing_head(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project_root = tmp_path / 'repo'
    sentinel = 'must not parse old rollout head'
    records: list[dict[str, object]] = [
        {
            'timestamp': '2026-06-25T11:00:00.000Z',
            'type': 'event_msg',
            'payload': {'type': 'user_message', 'message': sentinel},
        }
    ]
    records.extend(
        {
            'timestamp': f'2026-06-25T11:10:{index % 60:02d}.000Z',
            'type': 'rollout_noise',
            'payload': {'ignored': 'x' * 96},
        }
        for index in range(7000)
    )
    records.extend(
        [
            {
                'timestamp': '2026-06-25T12:00:00.000Z',
                'type': 'event_msg',
                'payload': {
                    'type': 'user_message',
                    'message': 'latest large rollout question',
                },
            },
            {
                'timestamp': '2026-06-25T12:00:01.000Z',
                'type': 'event_msg',
                'payload': {
                    'type': 'agent_message',
                    'message': 'latest large rollout step one',
                },
            },
            {
                'timestamp': '2026-06-25T12:00:02.000Z',
                'type': 'event_msg',
                'payload': {
                    'type': 'agent_message',
                    'message': 'latest large rollout final',
                },
            },
        ]
    )
    _write_codex_rollout(
        project_root,
        agent='mobile',
        thread_id='large-thread',
        records=records,
    )
    original_event_parser = service_module._codex_event_message_conversation_item

    def fail_if_head_is_parsed(payload: dict[str, object], **kwargs):
        if payload.get('message') == sentinel:
            raise AssertionError('old rollout head was parsed')
        return original_event_parser(payload, **kwargs)

    monkeypatch.setattr(
        service_module,
        '_codex_event_message_conversation_item',
        fail_if_head_is_parsed,
    )
    service = _service(
        _FakeCcbdClientWithConversationComms(),
        project_root=project_root,
        mobile_dir=tmp_path / 'mobile',
    )
    pairing = service.create_pairing_payload(
        gateway_url='http://127.0.0.1:8787',
        scopes=('view',),
    )
    _, claim = service.dispatch_post(
        '/v1/pairing/claim',
        {'pairing_code': str(pairing['pairing_code'])},
    )

    status, payload = service.dispatch_get(
        '/v1/projects/proj-demo/agents/mobile/conversation?namespace_epoch=4&limit=2',
        {'Authorization': f'Bearer {claim["device_token"]}'},
    )

    assert status == 200
    conversation = payload['conversation']
    assert conversation['next_cursor'].startswith('codex-before:')
    assert [(item['kind'], item['body']) for item in conversation['items']] == [
        ('user_message', 'latest large rollout question'),
        (
            'agent_reply',
            'latest large rollout step one\n\nlatest large rollout final',
        ),
    ]
    assert sentinel not in json.dumps(payload)


def test_agent_conversation_pages_older_items_from_codex_tail_cursor(
    tmp_path: Path,
) -> None:
    project_root = tmp_path / 'repo'
    records: list[dict[str, object]] = [
        {
            'timestamp': '2026-06-25T10:00:00.000Z',
            'type': 'event_msg',
            'payload': {
                'type': 'user_message',
                'message': 'older large rollout question',
            },
        },
        {
            'timestamp': '2026-06-25T10:00:01.000Z',
            'type': 'event_msg',
            'payload': {
                'type': 'agent_message',
                'message': 'older large rollout answer',
            },
        },
    ]
    records.extend(
        {
            'timestamp': f'2026-06-25T11:20:{index % 60:02d}.000Z',
            'type': 'rollout_noise',
            'payload': {'ignored': 'x' * 96},
        }
        for index in range(1300)
    )
    records.extend(
        [
            {
                'timestamp': '2026-06-25T12:00:00.000Z',
                'type': 'event_msg',
                'payload': {
                    'type': 'user_message',
                    'message': 'latest large rollout question',
                },
            },
            {
                'timestamp': '2026-06-25T12:00:01.000Z',
                'type': 'event_msg',
                'payload': {
                    'type': 'agent_message',
                    'message': 'latest large rollout answer',
                },
            },
        ]
    )
    _write_codex_rollout(
        project_root,
        agent='mobile',
        thread_id='large-thread',
        records=records,
    )
    service = _service(
        _FakeCcbdClientWithConversationComms(),
        project_root=project_root,
        mobile_dir=tmp_path / 'mobile',
    )
    pairing = service.create_pairing_payload(
        gateway_url='http://127.0.0.1:8787',
        scopes=('view',),
    )
    _, claim = service.dispatch_post(
        '/v1/pairing/claim',
        {'pairing_code': str(pairing['pairing_code'])},
    )
    headers = {'Authorization': f'Bearer {claim["device_token"]}'}
    _, latest = service.dispatch_get(
        '/v1/projects/proj-demo/agents/mobile/conversation?namespace_epoch=4&limit=2',
        headers,
    )

    _, older = service.dispatch_get(
        '/v1/projects/proj-demo/agents/mobile/conversation?namespace_epoch=4'
        f'&limit=2&cursor={latest["conversation"]["next_cursor"]}',
        headers,
    )

    assert [(item['kind'], item['body']) for item in older['conversation']['items']] == [
        ('user_message', 'older large rollout question'),
        ('agent_reply', 'older large rollout answer'),
    ]
    assert 'next_cursor' not in older['conversation']


def test_agent_conversation_caches_codex_latest_page_until_rollout_changes(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project_root = tmp_path / 'repo'
    records: list[dict[str, object]] = [
        {
            'timestamp': '2026-06-25T10:00:00.000Z',
            'type': 'event_msg',
            'payload': {
                'type': 'user_message',
                'message': 'older cached question',
            },
        },
        {
            'timestamp': '2026-06-25T10:00:01.000Z',
            'type': 'event_msg',
            'payload': {
                'type': 'agent_message',
                'message': 'older cached answer',
            },
        },
    ]
    records.extend(
        {
            'timestamp': f'2026-06-25T11:20:{index % 60:02d}.000Z',
            'type': 'rollout_noise',
            'payload': {'ignored': 'x' * 96},
        }
        for index in range(1300)
    )
    records.extend(
        [
            {
                'timestamp': '2026-06-25T12:00:00.000Z',
                'type': 'event_msg',
                'payload': {
                    'type': 'user_message',
                    'message': 'cached latest question',
                },
            },
            {
                'timestamp': '2026-06-25T12:00:01.000Z',
                'type': 'event_msg',
                'payload': {
                    'type': 'agent_message',
                    'message': 'cached latest answer',
                },
            },
        ]
    )
    _write_codex_rollout(
        project_root,
        agent='mobile',
        thread_id='large-cache-thread',
        records=records,
    )
    service = _service(
        _FakeCcbdClientWithConversationComms(),
        project_root=project_root,
        mobile_dir=tmp_path / 'mobile',
    )
    pairing = service.create_pairing_payload(
        gateway_url='http://127.0.0.1:8787',
        scopes=('view',),
    )
    _, claim = service.dispatch_post(
        '/v1/pairing/claim',
        {'pairing_code': str(pairing['pairing_code'])},
    )
    headers = {'Authorization': f'Bearer {claim["device_token"]}'}

    _, first = service.dispatch_get(
        '/v1/projects/proj-demo/agents/mobile/conversation?namespace_epoch=4&limit=2',
        headers,
    )
    assert [item['body'] for item in first['conversation']['items']] == [
        'cached latest question',
        'cached latest answer',
    ]

    original_rollout_parser = service_module._codex_rollout_conversation_items

    def fail_if_rollout_is_parsed(*args, **kwargs):
        raise AssertionError('cached latest page should not parse rollout again')

    monkeypatch.setattr(
        service_module,
        '_codex_rollout_conversation_items',
        fail_if_rollout_is_parsed,
    )
    _, second = service.dispatch_get(
        '/v1/projects/proj-demo/agents/mobile/conversation?namespace_epoch=4&limit=2',
        headers,
    )
    assert [item['body'] for item in second['conversation']['items']] == [
        'cached latest question',
        'cached latest answer',
    ]

    monkeypatch.setattr(
        service_module,
        '_codex_rollout_conversation_items',
        original_rollout_parser,
    )
    rollout_path = (
        project_root
        / '.ccb'
        / 'agents'
        / 'mobile'
        / 'provider-state'
        / 'codex'
        / 'home'
        / 'sessions'
        / '2026'
        / '06'
        / '25'
        / 'rollout-large-cache-thread.jsonl'
    )
    with rollout_path.open('a', encoding='utf-8') as handle:
        for record in [
            {
                'timestamp': '2026-06-25T12:01:00.000Z',
                'type': 'event_msg',
                'payload': {
                    'type': 'user_message',
                    'message': 'fresh changed question',
                },
            },
            {
                'timestamp': '2026-06-25T12:01:01.000Z',
                'type': 'event_msg',
                'payload': {
                    'type': 'agent_message',
                    'message': 'fresh changed answer',
                },
            },
        ]:
            handle.write(f'{json.dumps(record)}\n')

    _, changed = service.dispatch_get(
        '/v1/projects/proj-demo/agents/mobile/conversation?namespace_epoch=4&limit=2',
        headers,
    )
    assert [item['body'] for item in changed['conversation']['items']] == [
        'fresh changed question',
        'fresh changed answer',
    ]


def test_agent_conversation_cache_keeps_codex_limit_and_cursor_separate(
    tmp_path: Path,
) -> None:
    project_root = tmp_path / 'repo'
    records: list[dict[str, object]] = [
        {
            'timestamp': '2026-06-25T10:00:00.000Z',
            'type': 'event_msg',
            'payload': {'type': 'user_message', 'message': 'turn one question'},
        },
        {
            'timestamp': '2026-06-25T10:00:01.000Z',
            'type': 'event_msg',
            'payload': {'type': 'agent_message', 'message': 'turn one answer'},
        },
    ]
    records.extend(
        {
            'timestamp': f'2026-06-25T11:00:{index % 60:02d}.000Z',
            'type': 'rollout_noise',
            'payload': {'ignored': 'x' * 96},
        }
        for index in range(1300)
    )
    records.extend(
        [
            {
                'timestamp': '2026-06-25T12:00:00.000Z',
                'type': 'event_msg',
                'payload': {'type': 'user_message', 'message': 'turn two question'},
            },
            {
                'timestamp': '2026-06-25T12:00:01.000Z',
                'type': 'event_msg',
                'payload': {'type': 'agent_message', 'message': 'turn two answer'},
            },
        ]
    )
    _write_codex_rollout(
        project_root,
        agent='mobile',
        thread_id='large-limit-cursor-thread',
        records=records,
    )
    service = _service(
        _FakeCcbdClientWithConversationComms(),
        project_root=project_root,
        mobile_dir=tmp_path / 'mobile',
    )
    pairing = service.create_pairing_payload(
        gateway_url='http://127.0.0.1:8787',
        scopes=('view',),
    )
    _, claim = service.dispatch_post(
        '/v1/pairing/claim',
        {'pairing_code': str(pairing['pairing_code'])},
    )
    headers = {'Authorization': f'Bearer {claim["device_token"]}'}

    _, limit_one = service.dispatch_get(
        '/v1/projects/proj-demo/agents/mobile/conversation?namespace_epoch=4&limit=1',
        headers,
    )
    assert [item['body'] for item in limit_one['conversation']['items']] == [
        'turn two answer',
    ]

    _, latest = service.dispatch_get(
        '/v1/projects/proj-demo/agents/mobile/conversation?namespace_epoch=4&limit=2',
        headers,
    )
    assert [item['body'] for item in latest['conversation']['items']] == [
        'turn two question',
        'turn two answer',
    ]
    assert latest['conversation']['next_cursor'].startswith('codex-before:')

    _, older = service.dispatch_get(
        '/v1/projects/proj-demo/agents/mobile/conversation?namespace_epoch=4'
        f'&limit=2&cursor={latest["conversation"]["next_cursor"]}',
        headers,
    )
    assert [item['body'] for item in older['conversation']['items']] == [
        'turn one question',
        'turn one answer',
    ]


def test_agent_conversation_pages_completed_job_history_beyond_project_view_limit(tmp_path: Path) -> None:
    project_root = tmp_path / 'repo'
    jobs_path = project_root / '.ccb' / 'agents' / 'mobile' / 'jobs.jsonl'
    jobs_path.parent.mkdir(parents=True)
    records = []
    for index in range(56):
        job_id = f'job_history_{index:02d}'
        records.append(
            {
                'schema_version': 2,
                'record_type': 'job_record',
                'job_id': job_id,
                'agent_name': 'mobile',
                'target_name': 'mobile',
                'provider': 'fake',
                'request': {
                    'project_id': 'proj-demo',
                    'to_agent': 'mobile',
                    'from_actor': 'user',
                    'body': f'history question {index:02d}',
                    'message_type': 'ask',
                    'route_options': {},
                },
                'status': 'completed',
                'terminal_decision': {
                    'reply': f'history answer {index:02d}',
                },
                'created_at': f'2026-06-18T00:{index:02d}:00Z',
                'updated_at': f'2026-06-18T00:{index:02d}:01Z',
            }
        )
    jobs_path.write_text(
        ''.join(f'{json.dumps(record)}\n' for record in records),
        encoding='utf-8',
    )
    service = _service(
        _FakeCcbdClient(),
        project_root=project_root,
        mobile_dir=tmp_path / 'mobile',
    )
    pairing = service.create_pairing_payload(
        gateway_url='http://127.0.0.1:8787',
        scopes=('view',),
    )
    _, claim = service.dispatch_post(
        '/v1/pairing/claim',
        {'pairing_code': str(pairing['pairing_code'])},
    )
    headers = {'Authorization': f'Bearer {claim["device_token"]}'}

    _, latest = service.dispatch_get(
        '/v1/projects/proj-demo/agents/mobile/conversation?namespace_epoch=4&limit=50',
        headers,
    )
    latest_conversation = latest['conversation']

    assert latest_conversation['items'] == []
    assert 'next_cursor' not in latest_conversation


def test_agent_conversation_maps_artifact_links_to_download_attachments(tmp_path: Path) -> None:
    project_root = tmp_path / 'repo'
    snapshot_dir = project_root / '.ccb' / 'ccbd' / 'snapshots'
    snapshot_dir.mkdir(parents=True)
    file_id = 'mobile-file-backend-artifact'
    (snapshot_dir / 'job_mobile_reply.json').write_text(
        json.dumps(
            {
                'latest_decision': {
                    'reply': (
                        'Generated files:\n'
                        f'- [artifact.txt](ccb-artifact://{file_id})'
                    ),
                },
            }
        ),
        encoding='utf-8',
    )
    file_dir = (
        project_root
        / '.ccb'
        / 'ccbd'
        / 'mobile'
        / 'files'
        / 'proj-demo'
        / 'mobile'
        / file_id
    )
    file_dir.mkdir(parents=True)
    (file_dir / 'metadata.json').write_text(
        json.dumps(
            {
                'file_id': file_id,
                'file_name': 'artifact.txt',
                'mime_type': 'text/plain',
                'size_bytes': 12,
            }
        ),
        encoding='utf-8',
    )
    service = _service(
        _FakeCcbdClientWithConversationComms(),
        project_root=project_root,
        mobile_dir=tmp_path / 'mobile',
    )
    pairing = service.create_pairing_payload(
        gateway_url='http://127.0.0.1:8787',
        scopes=('view',),
    )
    _, claim = service.dispatch_post(
        '/v1/pairing/claim',
        {'pairing_code': str(pairing['pairing_code'])},
    )

    status, payload = service.dispatch_get(
        '/v1/projects/proj-demo/agents/mobile/conversation?namespace_epoch=4&limit=20',
        {'Authorization': f'Bearer {claim["device_token"]}'},
    )

    assert status == 200
    assert payload['conversation']['items'] == []


def test_agent_conversation_maps_artifact_links_from_gateway_file_store(tmp_path: Path) -> None:
    project_root = tmp_path / 'repo'
    snapshot_dir = project_root / '.ccb' / 'ccbd' / 'snapshots'
    snapshot_dir.mkdir(parents=True)
    jobs_dir = project_root / '.ccb' / 'agents' / 'mobile'
    jobs_dir.mkdir(parents=True)
    file_store = tmp_path / 'server-mobile' / 'files'
    file_id = 'mobile-file-shared-artifact'
    (snapshot_dir / 'job_mobile_reply.json').write_text(
        json.dumps(
            {
                'latest_decision': {
                    'reply': (
                        'Generated files:\n'
                        f'- [artifact.txt](ccb-artifact://{file_id})'
                    ),
                },
            }
        ),
        encoding='utf-8',
    )
    (jobs_dir / 'jobs.jsonl').write_text(
        json.dumps(
            {
                'job_id': 'job_mobile_reply',
                'request': {
                    'route_options': {
                        'mobile_files_dir': str(file_store),
                    },
                },
            }
        )
        + '\n',
        encoding='utf-8',
    )
    file_dir = file_store / 'proj-demo' / 'mobile' / file_id
    file_dir.mkdir(parents=True)
    (file_dir / 'metadata.json').write_text(
        json.dumps(
            {
                'file_id': file_id,
                'file_name': 'artifact.txt',
                'mime_type': 'text/plain',
                'size_bytes': 12,
            }
        ),
        encoding='utf-8',
    )
    service = _service(
        _FakeCcbdClientWithConversationComms(),
        project_root=project_root,
        mobile_dir=tmp_path / 'mobile',
    )
    pairing = service.create_pairing_payload(
        gateway_url='http://127.0.0.1:8787',
        scopes=('view',),
    )
    _, claim = service.dispatch_post(
        '/v1/pairing/claim',
        {'pairing_code': str(pairing['pairing_code'])},
    )

    status, payload = service.dispatch_get(
        '/v1/projects/proj-demo/agents/mobile/conversation?namespace_epoch=4&limit=20',
        {'Authorization': f'Bearer {claim["device_token"]}'},
    )

    assert status == 200
    assert payload['conversation']['items'] == []


def test_non_native_conversation_resolves_project_file_links(tmp_path: Path) -> None:
    project_root = tmp_path / 'repo'
    outside_dir = tmp_path / 'downloads'
    snapshot_dir = project_root / '.ccb' / 'ccbd' / 'snapshots'
    jobs_dir = project_root / '.ccb' / 'agents' / 'mobile'
    agent_workspace_dir = project_root / '.ccb' / 'workspaces' / 'mobile'
    other_workspace_dir = project_root / '.ccb' / 'workspaces' / 'other'
    docs_dir = project_root / 'docs'
    snapshot_dir.mkdir(parents=True)
    jobs_dir.mkdir(parents=True)
    agent_workspace_dir.mkdir(parents=True)
    other_workspace_dir.mkdir(parents=True)
    docs_dir.mkdir(parents=True)
    outside_dir.mkdir()
    report_path = docs_dir / 'report.txt'
    workspace_report_path = agent_workspace_dir / 'tmp' / 'workspace-report.pdf'
    other_workspace_report_path = other_workspace_dir / 'tmp' / 'other-report.pdf'
    workspace_metadata_path = agent_workspace_dir / '.git'
    workspace_hidden_path = agent_workspace_dir / 'tmp' / '.private' / 'state.json'
    release_path = outside_dir / 'ccb-mobile-release.apk'
    notes_path = outside_dir / 'release-notes.txt'
    oversized_path = outside_dir / 'oversized.bin'
    hidden_path = project_root / '.ccb' / 'secret.txt'
    encoded_percent_path = docs_dir / 'a%2Fb.txt'
    report_path.write_text('report body\n', encoding='utf-8')
    workspace_report_path.parent.mkdir()
    workspace_report_path.write_bytes(b'workspace pdf body\n')
    other_workspace_report_path.parent.mkdir()
    other_workspace_report_path.write_bytes(b'other workspace pdf body\n')
    workspace_metadata_path.write_text('gitdir: private\n', encoding='utf-8')
    workspace_hidden_path.parent.mkdir()
    workspace_hidden_path.write_text('{"secret": true}\n', encoding='utf-8')
    release_path.write_bytes(b'release apk body\n')
    notes_path.write_text('release notes\n', encoding='utf-8')
    encoded_percent_path.write_text('percent path\n', encoding='utf-8')
    with oversized_path.open('wb') as oversized_file:
        oversized_file.truncate(129 * 1024 * 1024)
    hidden_path.write_text('hidden\n', encoding='utf-8')
    (jobs_dir / 'jobs.jsonl').write_text(
        json.dumps(
            {
                'job_id': 'job_mobile_reply',
                'status': 'completed',
                'agent_name': 'mobile',
                'request': {'body': 'create report'},
            }
        )
        + '\n',
        encoding='utf-8',
    )
    (snapshot_dir / 'job_mobile_reply.json').write_text(
        json.dumps(
            {
                'latest_decision': {
                    'reply': (
                        'Generated files:\n'
                        '- [report](docs/report.txt)\n'
                        f'- [workspace report]({workspace_report_path})\n'
                        f'- [other workspace report]({other_workspace_report_path})\n'
                        f'- [workspace metadata]({workspace_metadata_path})\n'
                        f'- [workspace hidden state]({workspace_hidden_path})\n'
                        '- [hidden](.ccb/secret.txt)\n'
                        f'- [outside]({release_path})\n'
                        f'- [file URI]({notes_path.as_uri()})\n'
                        f'- [encoded percent file URI]({encoded_percent_path.as_uri()})\n'
                        f'- [directory]({outside_dir})\n'
                        f'- [oversized]({oversized_path})\n'
                        '- [remote file URI](file://remote-host/etc/hosts)\n'
                    ),
                },
            }
        ),
        encoding='utf-8',
    )

    class _KimiClient(_FakeCcbdClientWithConversationComms):
        def project_view(self, *, schema_version: int = 1) -> dict[str, object]:
            payload = super().project_view(schema_version=schema_version)
            payload['view']['agents'][0]['provider'] = 'kimi'
            return payload

    service = _service(
        _KimiClient(),
        project_root=project_root,
        mobile_dir=tmp_path / 'mobile',
    )
    pairing = service.create_pairing_payload(
        gateway_url='http://127.0.0.1:8787',
        scopes=('view', 'file_download'),
    )
    _, claim = service.dispatch_post(
        '/v1/pairing/claim',
        {'pairing_code': str(pairing['pairing_code'])},
    )

    _, payload = service.dispatch_get(
        '/v1/projects/proj-demo/agents/mobile/conversation?namespace_epoch=4&limit=20',
        {'Authorization': f'Bearer {claim["device_token"]}'},
    )

    reply = next(
        item
        for item in payload['conversation']['items']
        if item['id'] == 'reply-job_mobile_reply'
    )
    assert 'ccb-artifact://' in reply['body']
    assert '[hidden](.ccb/secret.txt)' in reply['body']
    assert f'[other workspace report]({other_workspace_report_path})' in reply['body']
    assert f'[workspace metadata]({workspace_metadata_path})' in reply['body']
    assert f'[workspace hidden state]({workspace_hidden_path})' in reply['body']
    assert '[directory]' in reply['body']
    assert f'[oversized]({oversized_path})' in reply['body']
    assert '[remote file URI](file://remote-host/etc/hosts)' in reply['body']
    assert [item['file_name'] for item in reply['attachments']] == [
        'report.txt',
        'workspace-report.pdf',
        'ccb-mobile-release.apk',
        'release-notes.txt',
        'a%2Fb.txt',
    ]

    workspace_report = next(
        item
        for item in reply['attachments']
        if item['file_name'] == 'workspace-report.pdf'
    )
    status, content, headers = service.dispatch_file_download(
        f'/v1/projects/proj-demo/agents/mobile/files/{workspace_report["file_id"]}',
        {'Authorization': f'Bearer {claim["device_token"]}'},
    )
    assert status == 200
    assert content == b'workspace pdf body\n'
    assert headers['x-ccb-file-name'] == 'workspace-report.pdf'

    release = next(
        item
        for item in reply['attachments']
        if item['file_name'] == 'ccb-mobile-release.apk'
    )
    status, content, headers = service.dispatch_file_download(
        f'/v1/projects/proj-demo/agents/mobile/files/{release["file_id"]}',
        {'Authorization': f'Bearer {claim["device_token"]}'},
    )
    assert status == 200
    assert content == b'release apk body\n'
    assert headers['x-ccb-file-name'] == 'ccb-mobile-release.apk'


def test_agent_conversation_requires_view_auth_and_fresh_epoch(tmp_path: Path) -> None:
    service = _service(_FakeCcbdClient(), mobile_dir=tmp_path / 'mobile')
    pairing = service.create_pairing_payload(
        gateway_url='http://127.0.0.1:8787',
        scopes=('message_submit',),
    )
    _, claim = service.dispatch_post(
        '/v1/pairing/claim',
        {'pairing_code': str(pairing['pairing_code'])},
    )

    with pytest.raises(MobileGatewayError) as missing_auth:
        service.dispatch_get('/v1/projects/proj-demo/agents/mobile/conversation?namespace_epoch=4')
    assert missing_auth.value.status_code == 401

    with pytest.raises(MobileGatewayError) as denied:
        service.dispatch_get(
            '/v1/projects/proj-demo/agents/mobile/conversation?namespace_epoch=4',
            {'Authorization': f'Bearer {claim["device_token"]}'},
        )
    assert denied.value.status_code == 403

    view_pairing = service.create_pairing_payload(
        gateway_url='http://127.0.0.1:8787',
        scopes=('view',),
    )
    _, view_claim = service.dispatch_post(
        '/v1/pairing/claim',
        {'pairing_code': str(view_pairing['pairing_code'])},
    )
    with pytest.raises(MobileGatewayError) as stale:
        service.dispatch_get(
            '/v1/projects/proj-demo/agents/mobile/conversation?namespace_epoch=3',
            {'Authorization': f'Bearer {view_claim["device_token"]}'},
        )
    assert stale.value.status_code == 409


def test_agent_message_submit_sends_plain_text_to_agent_pane(tmp_path: Path) -> None:
    fake = _FakeCcbdClient()
    sent: list[tuple[object, str]] = []
    service = _service(
        fake,
        mobile_dir=tmp_path / 'mobile',
        terminal_message_sender=lambda target, text: sent.append((target, text)) or {},
    )
    pairing = service.create_pairing_payload(
        gateway_url='http://127.0.0.1:8787',
        scopes=('view', 'message_submit'),
    )
    _, claim = service.dispatch_post(
        '/v1/pairing/claim',
        {'pairing_code': str(pairing['pairing_code'])},
    )

    status, payload = service.dispatch_post(
        '/v1/projects/proj-demo/agents/mobile/messages',
        {
            'schema_version': 1,
            'project_id': 'proj-demo',
            'agent': 'mobile',
            'namespace_epoch': 4,
            'idempotency_key': 'mobile-msg-1',
            'body': 'continue with the next step',
            'format': 'markdown',
        },
        {'Authorization': f'Bearer {claim["device_token"]}'},
    )

    assert status == 202
    result = payload['message_submit']
    assert result['accepted'] is True
    assert result['idempotency_key'] == 'mobile-msg-1'
    assert result['job_id'] is None
    assert result['message_id'] == 'mobile-msg-1'
    assert result['state'] == 'sent'
    assert len(sent) == 1
    target, text = sent[0]
    assert text == 'continue with the next step'
    assert target.project_id == 'proj-demo'
    assert target.agent == 'mobile'
    assert target.window == 'main'
    assert target.pane_id == '%2'
    assert target.socket_path == '/tmp/ccb-demo/tmux.sock'
    assert target.session_name == 'ccb-demo'
    assert not any(call[0] == 'submit' for call in fake.calls)
    response_json = json.dumps(payload)
    assert 'terminal_input' not in response_json
    assert 'tmux.sock' not in response_json
    projects = service.projects_payload()
    assert projects['projects'][0]['last_activity_at'] == '2026-06-18T00:00:00Z'


def test_agent_message_submit_returns_herdr_input_blocked_payload(tmp_path: Path) -> None:
    sent: list[tuple[object, str]] = []
    service = _service(
        _FakeHerdrCcbdClient(),
        mobile_dir=tmp_path / 'mobile',
        terminal_message_sender=lambda target, text: sent.append((target, text)) or {},
    )
    pairing = service.create_pairing_payload(
        gateway_url='http://127.0.0.1:8787',
        scopes=('view', 'message_submit'),
    )
    _, claim = service.dispatch_post(
        '/v1/pairing/claim',
        {'pairing_code': str(pairing['pairing_code'])},
    )

    status, payload = service.dispatch_post(
        '/v1/projects/proj-demo/agents/mobile/messages',
        {
            'schema_version': 1,
            'project_id': 'proj-demo',
            'agent': 'mobile',
            'namespace_epoch': 4,
            'idempotency_key': 'mobile-msg-herdr-blocked',
            'body': 'continue with the next step',
            'format': 'markdown',
        },
        {'Authorization': f'Bearer {claim["device_token"]}'},
    )

    assert status == 409
    assert payload['status'] == 'blocked'
    blocked = payload['terminal_blocked']
    assert blocked['code'] == 'input_unsupported'
    assert blocked['backend_impl'] == 'herdr'
    assert blocked['degraded_next_action'] == 'collect-validation-transcript'
    assert sent == []
    assert 'herdr://workspace-1' not in json.dumps(payload)


def test_agent_message_submit_uses_herdr_backend_neutral_target(tmp_path: Path) -> None:
    sent: list[tuple[object, str]] = []
    service = _service(
        _FakeHerdrSupportedCcbdClient(),
        mobile_dir=tmp_path / 'mobile',
        terminal_message_sender=lambda target, text: sent.append((target, text)) or {},
    )
    pairing = service.create_pairing_payload(
        gateway_url='http://127.0.0.1:8787',
        scopes=('view', 'message_submit'),
    )
    _, claim = service.dispatch_post('/v1/pairing/claim', {'pairing_code': str(pairing['pairing_code'])})

    status, payload = service.dispatch_post(
        '/v1/projects/proj-demo/agents/mobile/messages',
        {
            'schema_version': 1,
            'project_id': 'proj-demo',
            'agent': 'mobile',
            'namespace_epoch': 4,
            'idempotency_key': 'mobile-msg-herdr-supported',
            'body': 'continue with the next step',
            'format': 'markdown',
        },
        {'Authorization': f'Bearer {claim["device_token"]}'},
    )

    assert status == 202
    assert payload['status'] == 'ok'
    target, text = sent[0]
    assert text == 'continue with the next step'
    assert target.backend_impl == 'herdr'
    assert target.socket_path == ''
    assert target.session_name == 'ccb-herdr'
    assert target.pane_ref == {'backend_impl': 'herdr', 'pane_id': 'pane-1'}
    assert target.namespace_ref['namespace_id'] == 'workspace-1'
    assert target.input_supported is True
    assert 'herdr://workspace-1' not in json.dumps(payload)


def test_agent_message_submit_default_herdr_adapter_missing_returns_blocked(tmp_path: Path) -> None:
    service = _service(_FakeHerdrSupportedCcbdClient(), mobile_dir=tmp_path / 'mobile')
    pairing = service.create_pairing_payload(
        gateway_url='http://127.0.0.1:8787',
        scopes=('view', 'message_submit'),
    )
    _, claim = service.dispatch_post('/v1/pairing/claim', {'pairing_code': str(pairing['pairing_code'])})

    status, payload = service.dispatch_post(
        '/v1/projects/proj-demo/agents/mobile/messages',
        {
            'schema_version': 1,
            'project_id': 'proj-demo',
            'agent': 'mobile',
            'namespace_epoch': 4,
            'idempotency_key': 'mobile-msg-herdr-adapter-missing',
            'body': 'continue with the next step',
            'format': 'markdown',
        },
        {'Authorization': f'Bearer {claim["device_token"]}'},
    )

    assert status == 409
    assert payload['status'] == 'blocked'
    assert payload['terminal_blocked']['code'] == 'target_not_attachable'
    assert payload['terminal_blocked']['blocking_gaps'] == ['herdr-terminal-adapter-unavailable']
    assert 'tmux' not in json.dumps(payload)


def test_terminal_attach_target_raises_herdr_attach_blocked_payload(tmp_path: Path) -> None:
    service = _service(_FakeHerdrCcbdClient(), mobile_dir=tmp_path / 'mobile')

    with pytest.raises(MobileGatewayError) as blocked:
        service._terminal_attach_target(
            {
                'terminal_id': 'term-herdr',
                'project_id': 'proj-demo',
                'target_epoch': 4,
                'target_summary': {
                    'project_id': 'proj-demo',
                    'agent': 'mobile',
                    'window': 'main',
                },
                'geometry': {},
            },
            include_history=True,
        )

    assert blocked.value.status_code == 409
    payload = getattr(blocked.value, 'terminal_blocked', None)
    assert payload['code'] == 'attach_unsupported'
    assert payload['backend_impl'] == 'herdr'
    assert payload['degraded_next_action'] == 'collect-validation-transcript'
    assert 'herdr://workspace-1' not in json.dumps(payload)


def test_frontdesk_message_submit_uses_ccbd_ask_job_not_pane(tmp_path: Path) -> None:
    fake = _FakeFrontdeskCcbdClient()
    sent: list[tuple[object, str]] = []
    service = _service(
        fake,
        mobile_dir=tmp_path / 'mobile',
        terminal_message_sender=lambda target, text: sent.append((target, text)) or {},
    )
    pairing = service.create_pairing_payload(
        gateway_url='http://127.0.0.1:8787',
        scopes=('view', 'message_submit'),
    )
    _, claim = service.dispatch_post(
        '/v1/pairing/claim',
        {'pairing_code': str(pairing['pairing_code'])},
    )

    status, payload = service.dispatch_post(
        '/v1/projects/proj-demo/agents/frontdesk/messages',
        {
            'schema_version': 1,
            'project_id': 'proj-demo',
            'agent': 'frontdesk',
            'namespace_epoch': 4,
            'idempotency_key': 'frontdesk-intake-1',
            'body': 'Build a small Python utility with focused tests.',
            'format': 'markdown',
        },
        {'Authorization': f'Bearer {claim["device_token"]}'},
    )

    assert status == 202
    result = payload['message_submit']
    assert result['accepted'] is True
    assert result['idempotency_key'] == 'frontdesk-intake-1'
    assert result['job_id'] == 'job_mobile_1'
    assert result['message_id'] == 'frontdesk-intake-1'
    assert result['state'] == 'queued'
    assert result['message']['state'] == 'queued'
    assert result['message']['agent'] == 'frontdesk'
    assert sent == []
    submit_calls = [call for call in fake.calls if call[0] == 'submit']
    assert len(submit_calls) == 1
    record = submit_calls[0][1]
    assert record['to_agent'] == 'frontdesk'
    assert record['from_actor'] == 'user'
    assert record['message_type'] == 'ask'
    assert record['delivery_scope'] == 'single'
    assert record['task_id'] == 'frontdesk-intake-1'
    assert record['route_options']['entry'] == 'frontdesk_message_submit'
    assert record['route_options']['source'] == 'mobile_gateway'


def test_agent_message_submit_accepts_attachment_only_message(tmp_path: Path) -> None:
    fake = _FakeCcbdClient()
    sent: list[tuple[object, str]] = []
    service = _service(
        fake,
        mobile_dir=tmp_path / 'mobile',
        terminal_message_sender=lambda target, text: sent.append((target, text)) or {},
    )
    pairing = service.create_pairing_payload(
        gateway_url='http://127.0.0.1:8787',
        scopes=('view', 'message_submit'),
    )
    _, claim = service.dispatch_post(
        '/v1/pairing/claim',
        {'pairing_code': str(pairing['pairing_code'])},
    )

    status, payload = service.dispatch_post(
        '/v1/projects/proj-demo/agents/mobile/messages',
        {
            'schema_version': 1,
            'project_id': 'proj-demo',
            'agent': 'mobile',
            'namespace_epoch': 4,
            'idempotency_key': 'mobile-file-msg-1',
            'body': '',
            'format': 'markdown',
            'attachments': [
                {
                    'file_id': 'mobile-file-1',
                    'file_name': 'probe.txt',
                    'mime_type': 'text/plain',
                    'size_bytes': 11,
                    'kind': 'document',
                }
            ],
        },
        {'Authorization': f'Bearer {claim["device_token"]}'},
    )

    assert status == 202
    message = payload['message_submit']['message']
    assert message['body'] == ''
    assert message['attachments'][0]['file_id'] == 'mobile-file-1'
    assert sent[0][1] == (
        'Attached files:\n'
        '- probe.txt (text/plain, 11 bytes, file id: mobile-file-1)'
    )
    assert not any(call[0] == 'submit' for call in fake.calls)


def test_message_submit_body_keeps_text_and_project_attachment_link() -> None:
    assert service_module._message_submit_body(
        'review this',
        [
            {
                'file_id': 'mobile-file-1',
                'file_name': 'probe.txt',
                'mime_type': 'text/plain',
                'size_bytes': 11,
                'project_relative_path': '.ccb/mobile/uploads/mobile/mobile-file-1-probe.txt',
            }
        ],
    ) == (
        'review this\n\nAttached files:\n'
        '- [probe.txt](.ccb/mobile/uploads/mobile/mobile-file-1-probe.txt) '
        '(text/plain, 11 bytes, file id: mobile-file-1)'
    )


def test_agent_message_submit_requires_agent_pane_evidence(tmp_path: Path) -> None:
    class NoPaneClient(_FakeCcbdClient):
        def project_view(self, *, schema_version: int = 1) -> dict[str, object]:
            payload = super().project_view(schema_version=schema_version)
            del payload['view']['agents'][0]['pane_id']
            return payload

    sent: list[tuple[object, str]] = []
    service = _service(
        NoPaneClient(),
        mobile_dir=tmp_path / 'mobile',
        terminal_message_sender=lambda target, text: sent.append((target, text)) or {},
    )
    pairing = service.create_pairing_payload(
        gateway_url='http://127.0.0.1:8787',
        scopes=('view', 'message_submit'),
    )
    _, claim = service.dispatch_post(
        '/v1/pairing/claim',
        {'pairing_code': str(pairing['pairing_code'])},
    )

    with pytest.raises(MobileGatewayError) as missing_pane:
        service.dispatch_post(
            '/v1/projects/proj-demo/agents/mobile/messages',
            {
                'project_id': 'proj-demo',
                'agent': 'mobile',
                'namespace_epoch': 4,
                'idempotency_key': 'mobile-msg-no-pane',
                'body': 'continue',
            },
            {'Authorization': f'Bearer {claim["device_token"]}'},
        )
    assert missing_pane.value.status_code == 409
    assert str(missing_pane.value) == 'message target has no pane evidence'
    assert sent == []


def test_agent_message_submit_requires_message_submit_scope(tmp_path: Path) -> None:
    service = _service(_FakeCcbdClient(), mobile_dir=tmp_path / 'mobile')
    pairing = service.create_pairing_payload(
        gateway_url='http://127.0.0.1:8787',
        scopes=('view', 'terminal_input'),
    )
    _, claim = service.dispatch_post(
        '/v1/pairing/claim',
        {'pairing_code': str(pairing['pairing_code'])},
    )

    with pytest.raises(MobileGatewayError) as denied:
        service.dispatch_post(
            '/v1/projects/proj-demo/agents/mobile/messages',
            {
                'project_id': 'proj-demo',
                'agent': 'mobile',
                'namespace_epoch': 4,
                'idempotency_key': 'mobile-msg-1',
                'body': 'continue',
            },
            {'Authorization': f'Bearer {claim["device_token"]}'},
        )
    assert denied.value.status_code == 403

    ask_pairing = service.create_pairing_payload(
        gateway_url='http://127.0.0.1:8787',
        scopes=('view', 'ask'),
    )
    _, ask_claim = service.dispatch_post(
        '/v1/pairing/claim',
        {'pairing_code': str(ask_pairing['pairing_code'])},
    )
    with pytest.raises(MobileGatewayError) as ask_denied:
        service.dispatch_post(
            '/v1/projects/proj-demo/agents/mobile/messages',
            {
                'project_id': 'proj-demo',
                'agent': 'mobile',
                'namespace_epoch': 4,
                'idempotency_key': 'mobile-msg-2',
                'body': 'continue',
            },
            {'Authorization': f'Bearer {ask_claim["device_token"]}'},
        )
    assert ask_denied.value.status_code == 403


def test_agent_file_upload_download_round_trips_bytes_over_http(tmp_path: Path) -> None:
    project_root = tmp_path / 'repo'
    service = _service(
        _FakeCcbdClient(),
        project_root=project_root,
        mobile_dir=tmp_path / 'mobile',
    )
    pairing = service.create_pairing_payload(gateway_url='http://127.0.0.1:8787')
    _, claim = service.dispatch_post(
        '/v1/pairing/claim',
        {'pairing_code': str(pairing['pairing_code'])},
    )
    token = str(claim['device_token'])
    server = build_mobile_gateway_server(parse_listen_address('127.0.0.1:0'), service)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    try:
        thread.start()
        host, port = server.server_address[:2]
        base = f'http://{host}:{port}'
        data = b'hello from mobile file route\n'
        upload_request = Request(
            f'{base}/v1/projects/proj-demo/agents/mobile/files',
            data=data,
            method='POST',
            headers={
                'Authorization': f'Bearer {token}',
                'Content-Type': 'text/plain',
                'X-Ccb-File-Name': 'probe%20file.txt',
                'Accept': 'application/json',
            },
        )

        with urlopen(upload_request) as response:
            upload = json.loads(response.read().decode('utf-8'))
        file_id = upload['file_id']
        assert upload['file_name'] == 'probe file.txt'
        assert upload['mime_type'] == 'text/plain'
        assert upload['size_bytes'] == len(data)
        assert upload['project_relative_path'].startswith(
            '.ccb/mobile/uploads/mobile/'
        )
        assert (project_root / upload['project_relative_path']).read_bytes() == data

        download_request = Request(
            f'{base}/v1/projects/proj-demo/agents/mobile/files/{file_id}',
            headers={'Authorization': f'Bearer {token}', 'Accept': '*/*'},
        )
        with urlopen(download_request) as response:
            downloaded = response.read()
            content_type = response.headers.get('content-type')
            file_name = response.headers.get('x-ccb-file-name')

        assert downloaded == data
        assert content_type == 'text/plain'
        assert file_name == 'probe file.txt'
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


def test_agent_file_routes_use_registry_project_id(tmp_path: Path) -> None:
    first_root = tmp_path / 'one'
    second_root = tmp_path / 'two'
    first_root.mkdir()
    second_root.mkdir()
    first = _FakeCcbdClient(
        project_id='proj-one',
        project_root=str(first_root),
        display_name='one',
    )
    second = _FakeCcbdClient(
        project_id='proj-two',
        project_root=str(second_root),
        display_name='two',
    )
    service = _service(
        first,
        mobile_dir=tmp_path / 'mobile',
        project_registry=MobileGatewayProjectRegistry(
            [
                MobileGatewayProject(
                    project_id='proj-one',
                    project_root=first_root,
                    ccbd_client_factory=lambda: first,
                ),
                MobileGatewayProject(
                    project_id='proj-two',
                    project_root=second_root,
                    ccbd_client_factory=lambda: second,
                ),
            ]
        ),
    )
    pairing = service.create_pairing_payload(gateway_url='http://127.0.0.1:8787')
    _, claim = service.dispatch_post(
        '/v1/pairing/claim',
        {'pairing_code': str(pairing['pairing_code'])},
    )
    token = str(claim['device_token'])
    data = b'server-wide file route\n'

    status, upload = service.dispatch_file_upload(
        '/v1/projects/proj-two/agents/mobile/files',
        data,
        {
            'Authorization': f'Bearer {token}',
            'Content-Type': 'text/plain',
            'X-Ccb-File-Name': 'server-wide.txt',
        },
    )

    assert status == 201
    file_id = str(upload['file_id'])
    metadata_path = (
        tmp_path
        / 'mobile'
        / 'files'
        / 'proj-two'
        / 'mobile'
        / file_id
        / 'metadata.json'
    )
    metadata = json.loads(metadata_path.read_text(encoding='utf-8'))
    assert metadata['project_id'] == 'proj-two'
    assert (second_root / metadata['project_relative_path']).read_bytes() == data
    assert not (tmp_path / 'mobile' / 'files' / 'proj-demo' / 'mobile' / file_id).exists()

    download_status, downloaded, headers = service.dispatch_file_download(
        f'/v1/projects/proj-two/agents/mobile/files/{file_id}',
        {'Authorization': f'Bearer {token}'},
    )

    assert download_status == 200
    assert downloaded == data
    assert headers['x-ccb-file-name'] == 'server-wide.txt'
    with pytest.raises(MobileGatewayError) as wrong_project:
        service.dispatch_file_download(
            f'/v1/projects/proj-one/agents/mobile/files/{file_id}',
            {'Authorization': f'Bearer {token}'},
        )
    assert wrong_project.value.status_code == 404
    assert first.calls == []
    assert second.calls == [('project_view', 1)]


def test_agent_file_routes_require_file_scopes(tmp_path: Path) -> None:
    service = _service(
        _FakeCcbdClient(project_root=str(tmp_path / 'repo')),
        project_root=tmp_path / 'repo',
        mobile_dir=tmp_path / 'mobile',
    )
    pairing = service.create_pairing_payload(
        gateway_url='http://127.0.0.1:8787',
        scopes=('view',),
    )
    _, claim = service.dispatch_post(
        '/v1/pairing/claim',
        {'pairing_code': str(pairing['pairing_code'])},
    )
    token = str(claim['device_token'])

    with pytest.raises(MobileGatewayError) as denied:
        service.dispatch_file_upload(
            '/v1/projects/proj-demo/agents/mobile/files',
            b'hello',
            {
                'Authorization': f'Bearer {token}',
                'Content-Type': 'text/plain',
                'X-Ccb-File-Name': 'probe.txt',
            },
        )
    assert denied.value.status_code == 403

    upload_pairing = service.create_pairing_payload(
        gateway_url='http://127.0.0.1:8787',
        scopes=('file_upload',),
    )
    _, upload_claim = service.dispatch_post(
        '/v1/pairing/claim',
        {'pairing_code': str(upload_pairing['pairing_code'])},
    )
    _, upload = service.dispatch_file_upload(
        '/v1/projects/proj-demo/agents/mobile/files',
        b'host file',
        {
            'Authorization': f'Bearer {upload_claim["device_token"]}',
            'X-Ccb-File-Name': 'host.txt',
        },
    )
    with pytest.raises(MobileGatewayError) as download_denied:
        service.dispatch_file_download(
            f'/v1/projects/proj-demo/agents/mobile/files/{upload["file_id"]}',
            {'Authorization': f'Bearer {token}'},
        )
    assert download_denied.value.status_code == 403


def test_pairing_claim_creates_hashed_device_records_and_audit(tmp_path: Path) -> None:
    service = _service(_FakeCcbdClient(), mobile_dir=tmp_path / 'mobile')
    pairing = service.create_pairing_payload(
        gateway_url='https://mobile.example.com',
        route_provider='cloudflare_tunnel',
    )
    pairing_code = str(pairing['pairing_code'])

    status, claim = service.dispatch_post(
        '/v1/pairing/claim',
        {
            'pairing_code': pairing_code,
            'device_name': 'Pixel Fold',
        },
    )
    device_token = str(claim['device_token'])
    device_id = str(claim['device']['device_id'])

    assert status == 201
    assert claim['host_profile']['device_id'] == device_id
    assert claim['host_profile']['scopes'] == [
        'ask',
        'content',
        'file_download',
        'file_upload',
        'focus',
        'host_terminal',
        'lifecycle',
        'message_submit',
        'notify',
        'provider_settings',
        'terminal_input',
        'view',
    ]
    assert claim['host_profile']['route_provider'] == 'cloudflare_tunnel'
    assert claim['host_profile']['gateway_url'] == 'https://mobile.example.com'

    status, me = service.dispatch_get('/v1/devices/me', {'Authorization': f'Bearer {device_token}'})
    assert status == 200
    assert me['device']['name'] == 'Pixel Fold'
    assert me['device']['revoked'] is False

    stored_pairings = (tmp_path / 'mobile' / 'pairing-tokens.jsonl').read_text(encoding='utf-8')
    stored_devices = (tmp_path / 'mobile' / 'devices.json').read_text(encoding='utf-8')
    stored_audit = (tmp_path / 'mobile' / 'audit.jsonl').read_text(encoding='utf-8')
    assert pairing_code not in stored_pairings
    assert pairing_code not in stored_audit
    assert device_token not in stored_devices
    assert device_token not in stored_audit
    assert 'sha256:' in stored_pairings
    assert 'sha256:' in stored_devices

    with pytest.raises(MobileGatewayError) as duplicate:
        service.dispatch_post('/v1/pairing/claim', {'pairing_code': pairing_code})
    assert duplicate.value.status_code == 409

    status, revoked = service.dispatch_post(
        f'/v1/devices/{device_id}/revoke',
        {},
        {'Authorization': f'Bearer {device_token}'},
    )
    assert status == 200
    assert revoked['device']['revoked'] is True
    with pytest.raises(MobileGatewayError) as denied:
        service.dispatch_get('/v1/devices/me', {'Authorization': f'Bearer {device_token}'})
    assert denied.value.status_code == 401
    with pytest.raises(MobileGatewayError) as denied_view:
        service.dispatch_get(
            '/v1/projects/proj-demo/view',
            {'Authorization': f'Bearer {device_token}'},
        )
    assert denied_view.value.status_code == 401


def test_reusable_pairing_can_claim_multiple_devices_until_revoked(tmp_path: Path) -> None:
    store = MobileGatewayPairingStore(tmp_path / 'mobile')
    pairing = store.create_pairing_payload(
        project_id='host-demo',
        gateway_url='https://mobile.example.com',
        route_provider='tailnet',
        scopes=('view', 'notify'),
        expires_seconds=None,
        reusable_claims=True,
    )
    pairing_code = str(pairing['pairing_code'])

    first = store.claim_pairing(pairing_code=pairing_code, device_name='Phone A')
    second = store.claim_pairing(pairing_code=pairing_code, device_name='Phone B')

    assert first['device']['device_id'] != second['device']['device_id']
    assert store.pairing_code_is_claimable(pairing_code)
    store.revoke_pairing(str(pairing['pairing_id']), reason='mobile_update_refreshed')
    assert not store.pairing_code_is_claimable(pairing_code)
    with pytest.raises(MobileGatewayPairingError) as denied:
        store.claim_pairing(pairing_code=pairing_code, device_name='Phone C')
    assert denied.value.reason == 'revoked'


def test_reusable_handoff_survives_restart_and_manual_rotation_keeps_old_devices(tmp_path: Path) -> None:
    mobile_dir = tmp_path / 'mobile'
    store = MobileGatewayPairingStore(mobile_dir)
    handoff = store.ensure_reusable_pairing_payload(
        project_id='host-demo',
        gateway_url='https://mobile.example.com',
        route_provider='tailnet',
        scopes=('view', 'notify'),
    )
    code = str(handoff['pairing_code'])
    claims = [
        store.claim_pairing(pairing_code=code, device_name=f'Phone {index}')
        for index in range(4)
    ]
    old_token = str(claims[0]['device_token'])

    restarted = MobileGatewayPairingStore(mobile_dir).ensure_reusable_pairing_payload(
        project_id='host-demo',
        gateway_url='https://mobile.example.com',
        route_provider='tailnet',
        scopes=('view', 'notify'),
    )
    assert restarted['pairing_code'] == code
    assert restarted['generation'] == 1
    if os.name != 'nt':
        assert (mobile_dir / 'pairing-handoff.json').stat().st_mode & 0o777 == 0o600

    rotated = MobileGatewayPairingStore(mobile_dir).rotate_reusable_pairing_payload(
        project_id='host-demo',
        gateway_url='https://mobile.example.com',
        route_provider='tailnet',
        scopes=('view', 'notify'),
    )
    assert rotated['pairing_code'] != code
    assert rotated['generation'] == 2
    with pytest.raises(MobileGatewayPairingError) as denied:
        store.claim_pairing(pairing_code=code, device_name='Old QR')
    assert denied.value.reason == 'revoked'
    assert store.authenticate_device(old_token, required_scopes=('view',)).device_id == claims[0]['device']['device_id']

    audit = (mobile_dir / 'audit.jsonl').read_text(encoding='utf-8')
    assert code not in audit
    assert str(rotated['pairing_code']) not in audit
    assert old_token not in audit


def test_gateway_service_restart_reuses_persistent_handoff(tmp_path: Path) -> None:
    first = _service(_FakeCcbdClient(), mobile_dir=tmp_path / 'mobile')
    handoff = first.ensure_reusable_pairing_payload(
        gateway_url='https://mobile.example.com',
        route_provider='tailnet',
    )
    restarted = _service(_FakeCcbdClient(), mobile_dir=tmp_path / 'mobile')

    restored = restarted.ensure_reusable_pairing_payload(
        gateway_url='https://changed.example.com',
        route_provider='lan',
    )

    assert restored['pairing_code'] == handoff['pairing_code']
    assert restored['generation'] == handoff['generation']
    assert restored['gateway_url'] == 'https://mobile.example.com'


def test_device_presence_is_redacted_and_heartbeat_does_not_change_user_activity(tmp_path: Path) -> None:
    service = _service(_FakeCcbdClient(), mobile_dir=tmp_path / 'mobile')
    pairing = service.create_pairing_payload(gateway_url='http://127.0.0.1:8787')
    _, claim = service.dispatch_post('/v1/pairing/claim', {'pairing_code': pairing['pairing_code']})
    token = str(claim['device_token'])
    headers = {'Authorization': f'Bearer {token}'}

    _, first = service.dispatch_post('/v1/devices/me/presence', {
        'visible': True,
        'focused_project_id': 'proj-demo',
        'focused_agent': 'mobile',
        'terminal_id': 'term-demo',
        'user_activity': True,
    }, headers)
    _, heartbeat = service.dispatch_post('/v1/devices/me/presence', {
        'visible': True,
        'focused_project_id': 'proj-demo',
    }, headers)

    assert first['presence']['last_user_activity_at'] == heartbeat['presence']['last_user_activity_at']
    _, me = service.dispatch_get('/v1/devices/me', headers)
    assert me['presence']['focused_agent'] == 'mobile'
    stored = (tmp_path / 'mobile' / 'audit.jsonl').read_text(encoding='utf-8')
    for secret in (token, str(pairing['pairing_code']), 'term-demo'):
        assert secret not in stored


def test_presence_expires_authoritatively_and_revoke_isolated_to_one_device(tmp_path: Path) -> None:
    now = [datetime(2026, 7, 13, tzinfo=timezone.utc)]
    store = MobileGatewayPairingStore(
        tmp_path / 'mobile',
        clock=lambda: now[0],
        presence_ttl_seconds=90,
    )
    pairing = store.create_pairing_payload(
        project_id='host-demo',
        gateway_url='https://mobile.example.com',
        route_provider='tailnet',
        scopes=('view',),
        expires_seconds=None,
        reusable_claims=True,
    )
    first = store.claim_pairing(pairing_code=str(pairing['pairing_code']), device_name='Phone A')
    second = store.claim_pairing(pairing_code=str(pairing['pairing_code']), device_name='Phone B')
    first_id = str(first['device']['device_id'])
    second_id = str(second['device']['device_id'])
    first_presence = store.record_presence(
        device_id=first_id,
        visible=True,
        focused_project_id='project-a',
        focused_agent='agent-a',
        user_activity=True,
    )
    now[0] += timedelta(seconds=1)
    heartbeat = store.record_presence(device_id=first_id, visible=True)
    assert heartbeat['last_user_activity_at'] == first_presence['last_user_activity_at']
    store.record_presence(device_id=second_id, visible=True, focused_project_id='project-b')
    now[0] += timedelta(seconds=91)

    stale = store.presence_for_device(first_id)
    assert stale is not None
    assert stale['freshness'] == 'stale'
    assert stale['visible'] is False
    assert stale['focused_project_id'] == ''

    store.revoke_device_locally(device_id=first_id)
    assert store.presence_for_device(first_id) is None
    second_presence = store.presence_for_device(second_id)
    assert second_presence is not None
    assert second_presence['freshness'] == 'stale'


def test_host_local_device_revoke_lists_devices_and_revokes_terminal_handles(tmp_path: Path) -> None:
    store = MobileGatewayPairingStore(tmp_path / 'mobile')
    pairing = store.create_pairing_payload(
        project_id='proj-demo',
        gateway_url='https://mobile.example.com',
        route_provider='cloudflare_tunnel',
        scopes=('view', 'terminal_input'),
    )
    claim = store.claim_pairing(
        pairing_code=str(pairing['pairing_code']),
        device_name='Lost phone',
    )
    device_id = str(claim['device']['device_id'])
    handle = store.create_terminal_handle(
        project_id='proj-demo',
        device_id=device_id,
        target_epoch=4,
        target_summary={'project_id': 'proj-demo', 'agent': 'mobile'},
        geometry={'columns': 80, 'rows': 24},
    )

    assert store.list_devices()[0]['device_id'] == device_id

    result = store.revoke_device_locally(device_id=device_id)

    assert result['device']['revoked'] is True
    assert result['revoked_terminal_count'] == 1
    with pytest.raises(MobileGatewayPairingError) as denied:
        store.authenticate_terminal_token(
            terminal_id=str(handle['terminal_id']),
            terminal_token=str(handle['terminal_token']),
        )
    assert denied.value.reason == 'revoked'
    audit = (tmp_path / 'mobile' / 'audit.jsonl').read_text(encoding='utf-8')
    assert 'device_revoked' in audit
    assert 'terminal_revoked' in audit
    assert str(handle['terminal_token']) not in audit


def test_terminal_replacement_handle_rejects_previous_handle_resume_cursor(tmp_path: Path) -> None:
    store = MobileGatewayPairingStore(tmp_path / 'mobile')
    first = store.create_terminal_handle(
        project_id='proj-demo',
        device_id='dev-demo',
        target_epoch=4,
        target_summary={'project_id': 'proj-demo', 'agent': 'mobile'},
        geometry={'columns': 80, 'rows': 24},
    )
    store.record_terminal_output_sequence(
        terminal_id=str(first['terminal_id']),
        terminal_token=str(first['terminal_token']),
        sequence=7,
    )
    replacement = store.create_terminal_handle(
        project_id='proj-demo',
        device_id='dev-demo',
        target_epoch=4,
        target_summary={'project_id': 'proj-demo', 'agent': 'mobile'},
        geometry={'columns': 80, 'rows': 24},
    )

    with pytest.raises(MobileGatewayPairingError) as denied:
        store.authenticate_terminal_token(
            terminal_id=str(replacement['terminal_id']),
            terminal_token=str(replacement['terminal_token']),
            resume_cursor=7,
        )

    assert denied.value.reason == 'stale_resume_cursor'


def test_terminal_state_index_reads_only_appended_jsonl_tail(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    reads: list[int] = []
    original = pairing_module._read_jsonl_from_offset

    def recording_read(path: Path, offset: int):
        reads.append(offset)
        return original(path, offset)

    monkeypatch.setattr(pairing_module, '_read_jsonl_from_offset', recording_read)
    store = MobileGatewayPairingStore(tmp_path / 'mobile')
    handle = store.create_terminal_handle(
        project_id='proj-demo',
        device_id='dev-demo',
        target_epoch=4,
        target_summary={'project_id': 'proj-demo', 'agent': 'mobile'},
        geometry={'columns': 80, 'rows': 24},
    )

    store.record_terminal_input_sequence(
        terminal_id=str(handle['terminal_id']),
        terminal_token=str(handle['terminal_token']),
        sequence=1,
    )
    store.record_terminal_output_sequence(
        terminal_id=str(handle['terminal_id']),
        terminal_token=str(handle['terminal_token']),
        sequence=1,
    )
    store.record_terminal_input_sequence(
        terminal_id=str(handle['terminal_id']),
        terminal_token=str(handle['terminal_token']),
        sequence=2,
    )

    assert reads[0] == 0
    assert reads[1] > 0
    assert reads[2] > reads[1]
    assert reads[2] - reads[1] < 2048


def test_terminal_state_index_observes_external_appends(tmp_path: Path) -> None:
    mobile_dir = tmp_path / 'mobile'
    first_store = MobileGatewayPairingStore(mobile_dir)
    handle = first_store.create_terminal_handle(
        project_id='proj-demo',
        device_id='dev-demo',
        target_epoch=4,
        target_summary={'project_id': 'proj-demo', 'agent': 'mobile'},
        geometry={'columns': 80, 'rows': 24},
    )
    first_store.record_terminal_input_sequence(
        terminal_id=str(handle['terminal_id']),
        terminal_token=str(handle['terminal_token']),
        sequence=1,
    )

    second_store = MobileGatewayPairingStore(mobile_dir)
    second_store.record_terminal_input_sequence(
        terminal_id=str(handle['terminal_id']),
        terminal_token=str(handle['terminal_token']),
        sequence=2,
    )

    record = first_store.authenticate_terminal_token(
        terminal_id=str(handle['terminal_id']),
        terminal_token=str(handle['terminal_token']),
    )
    assert record['last_input_seq'] == 2


def test_terminal_state_log_compacts_to_latest_handle_records(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(pairing_module, '_TERMINAL_LOG_COMPACT_BYTES', 1)
    store = MobileGatewayPairingStore(tmp_path / 'mobile')
    handle = store.create_terminal_handle(
        project_id='proj-demo',
        device_id='dev-demo',
        target_epoch=4,
        target_summary={'project_id': 'proj-demo', 'agent': 'mobile'},
        geometry={'columns': 80, 'rows': 24},
    )
    store.record_terminal_input_sequence(
        terminal_id=str(handle['terminal_id']),
        terminal_token=str(handle['terminal_token']),
        sequence=3,
    )
    store.record_terminal_output_sequence(
        terminal_id=str(handle['terminal_id']),
        terminal_token=str(handle['terminal_token']),
        sequence=4,
    )

    record = store.authenticate_terminal_token(
        terminal_id=str(handle['terminal_id']),
        terminal_token=str(handle['terminal_token']),
    )
    lines = store.terminal_tokens_path.read_text(encoding='utf-8').splitlines()

    assert len(lines) == 1
    assert record['last_input_seq'] == 3
    assert record['last_output_seq'] == 4


def test_terminal_open_requires_terminal_scope_and_mints_hashed_token(tmp_path: Path) -> None:
    service = _service(_FakeCcbdClient(), mobile_dir=tmp_path / 'mobile')
    pairing = service.create_pairing_payload(gateway_url='http://127.0.0.1:8787')
    _, claim = service.dispatch_post(
        '/v1/pairing/claim',
        {
            'pairing_code': str(pairing['pairing_code']),
            'device_name': 'Pixel Fold',
        },
    )
    token = str(claim['device_token'])

    status, handle = service.dispatch_post(
        '/v1/projects/proj-demo/terminals',
        {
            'schema_version': 1,
            'project_id': 'proj-demo',
            'namespace_epoch': 4,
            'target': {
                'kind': 'agent',
                'agent': 'mobile',
                'window': 'main',
                'pane_id': '%2',
            },
            'geometry': {
                'columns': 100,
                'rows': 30,
                'pixel_width': 960,
                'pixel_height': 640,
            },
        },
        {
            'Authorization': f'Bearer {token}',
            'Host': '127.0.0.1:8787',
        },
    )

    assert status == 201
    assert str(handle['terminal_id']).startswith('term_')
    assert handle['terminal_token']
    assert handle['expires_at']
    assert handle['websocket_url'] == f'ws://127.0.0.1:8787/v1/terminals/{handle["terminal_id"]}'
    assert handle['target_epoch'] == 4
    assert handle['target_summary'] == {
        'project_id': 'proj-demo',
        'agent': 'mobile',
        'window': 'main',
        'pane_id': '%2',
    }
    assert 'tmux.sock' not in json.dumps(handle)
    assert 'ccb-demo' not in json.dumps(handle)

    stored_tokens = (tmp_path / 'mobile' / 'terminal-tokens.jsonl').read_text(encoding='utf-8')
    stored_audit = (tmp_path / 'mobile' / 'audit.jsonl').read_text(encoding='utf-8')
    assert str(handle['terminal_token']) not in stored_tokens
    assert str(handle['terminal_token']) not in stored_audit
    assert 'sha256:' in stored_tokens
    assert '"last_input_seq": 0' in stored_tokens


def test_host_terminal_open_and_terminate_are_device_scoped(tmp_path: Path) -> None:
    class RecordingHostTerminalManager:
        def __init__(self) -> None:
            self.attach_calls: list[dict[str, object]] = []
            self.terminate_calls: list[dict[str, str]] = []

        def attach_target(self, **kwargs):
            self.attach_calls.append(dict(kwargs))
            slot = str(kwargs['client_session_id'])
            return terminal_module.TerminalAttachTarget(
                terminal_id=str(kwargs['terminal_id']),
                socket_path='/tmp/private-host-terminal.sock',
                session_name=f'private-{slot}',
                pane_id='%9',
                geometry=kwargs['geometry'],
                target_summary={
                    'kind': 'host_shell',
                    'project_id': '@host',
                    'client_session_id': slot,
                    'display_name': str(kwargs['display_name']),
                    'working_directory': '~',
                },
                include_history=bool(kwargs['include_history']),
            )

        def terminate(self, **kwargs):
            self.terminate_calls.append({key: str(value) for key, value in kwargs.items()})
            return True

    manager = RecordingHostTerminalManager()
    service = _service(
        _FakeCcbdClient(),
        mobile_dir=tmp_path / 'mobile',
        host_terminal_manager=manager,
    )
    pairing = service.create_pairing_payload(
        gateway_url='http://127.0.0.1:8787',
        scopes=('view', 'host_terminal'),
    )
    _, claim = service.dispatch_post(
        '/v1/pairing/claim',
        {'pairing_code': str(pairing['pairing_code']), 'device_name': 'Phone A'},
    )
    headers = {
        'Authorization': f'Bearer {claim["device_token"]}',
        'Host': '127.0.0.1:8787',
    }

    status, handle = service.dispatch_post(
        '/v1/terminals',
        {
            'schema_version': 1,
            'client_session_id': 'shell-2',
            'display_name': 'Shell 2',
            'geometry': {'columns': 100, 'rows': 30},
        },
        headers,
    )

    assert status == 201
    assert handle['target_epoch'] == 0
    assert handle['target_summary'] == {
        'kind': 'host_shell',
        'project_id': '@host',
        'client_session_id': 'shell-2',
        'display_name': 'Shell 2',
        'working_directory': '~',
    }
    assert '/tmp/private-host-terminal.sock' not in json.dumps(handle)
    assert manager.attach_calls[0]['device_id'] == claim['device']['device_id']

    status, terminated = service.dispatch_post(
        '/v1/terminals/terminate',
        {'client_session_id': 'shell-2'},
        headers,
    )
    assert status == 200
    assert terminated['terminated'] is True
    assert terminated['revoked_terminal_count'] == 1
    assert manager.terminate_calls == [
        {
            'device_id': str(claim['device']['device_id']),
            'client_session_id': 'shell-2',
        }
    ]
    with pytest.raises(MobileGatewayPairingError) as revoked:
        MobileGatewayPairingStore(tmp_path / 'mobile').authenticate_terminal_token(
            terminal_id=str(handle['terminal_id']),
            terminal_token=str(handle['terminal_token']),
        )
    assert revoked.value.reason == 'revoked'


def test_host_terminal_requires_dedicated_scope(tmp_path: Path) -> None:
    service = _service(_FakeCcbdClient(), mobile_dir=tmp_path / 'mobile')
    pairing = service.create_pairing_payload(
        gateway_url='http://127.0.0.1:8787',
        scopes=('view', 'terminal_input'),
    )
    _, claim = service.dispatch_post(
        '/v1/pairing/claim',
        {'pairing_code': str(pairing['pairing_code'])},
    )

    with pytest.raises(MobileGatewayError) as denied:
        service.dispatch_post(
            '/v1/terminals',
            {'client_session_id': 'shell-1', 'display_name': 'Shell 1'},
            {'Authorization': f'Bearer {claim["device_token"]}'},
        )

    assert denied.value.status_code == 403


@pytest.mark.parametrize(
    'target',
    [
        {'kind': 'agent', 'agent': 'mobile', 'window': 'main'},
        {'kind': 'window_active_pane', 'window': 'main'},
        {'kind': 'pane_evidence', 'window': 'main', 'pane_id': 'pane-1'},
    ],
)
def test_terminal_open_herdr_non_agent_target_uses_backend_neutral_attach(
    tmp_path: Path,
    target: dict[str, object],
) -> None:
    service = _service(_FakeHerdrSupportedCcbdClient(), mobile_dir=tmp_path / 'mobile')
    pairing = service.create_pairing_payload(gateway_url='http://127.0.0.1:8787')
    _, claim = service.dispatch_post('/v1/pairing/claim', {'pairing_code': str(pairing['pairing_code'])})

    status, handle = service.dispatch_post(
        '/v1/projects/proj-demo/terminals',
        {
            'project_id': 'proj-demo',
            'namespace_epoch': 4,
            'target': target,
            'geometry': {'columns': 100, 'rows': 30},
        },
        {'Authorization': f'Bearer {claim["device_token"]}'},
    )
    record = service._require_pairing_store().authenticate_terminal_token(
        terminal_id=str(handle['terminal_id']),
        terminal_token=str(handle['terminal_token']),
    )
    attach_target = service._terminal_attach_target(record, include_history=True)

    assert status == 201
    assert attach_target.backend_impl == 'herdr'
    assert attach_target.socket_path == ''
    assert attach_target.session_name == 'ccb-herdr'
    assert attach_target.namespace_ref['namespace_id'] == 'workspace-1'
    assert attach_target.pane_ref == {'backend_impl': 'herdr', 'pane_id': 'pane-1'}
    assert attach_target.attach_supported is True


@pytest.mark.parametrize(
    'target',
    [
        {'kind': 'window_active_pane', 'window': 'main'},
        {'kind': 'pane_evidence', 'window': 'main', 'pane_id': 'pane-1'},
    ],
)
def test_terminal_open_herdr_non_agent_target_returns_structured_blocked(
    tmp_path: Path,
    target: dict[str, object],
) -> None:
    service = _service(_FakeHerdrCcbdClient(), mobile_dir=tmp_path / 'mobile')
    pairing = service.create_pairing_payload(gateway_url='http://127.0.0.1:8787')
    _, claim = service.dispatch_post('/v1/pairing/claim', {'pairing_code': str(pairing['pairing_code'])})

    with pytest.raises(MobileGatewayError) as exc_info:
        service.dispatch_post(
            '/v1/projects/proj-demo/terminals',
            {
                'project_id': 'proj-demo',
                'namespace_epoch': 4,
                'target': target,
                'geometry': {'columns': 100, 'rows': 30},
            },
            {'Authorization': f'Bearer {claim["device_token"]}'},
        )

    assert exc_info.value.status_code == 409
    blocked = getattr(exc_info.value, 'terminal_blocked')
    assert blocked['code'] == 'attach_unsupported'
    assert blocked['backend_impl'] == 'herdr'
    assert blocked['degraded_next_action'] == 'collect-validation-transcript'


def test_terminal_open_herdr_agent_target_blocks_contradictory_projection(tmp_path: Path) -> None:
    class _ContradictoryHerdrClient(_FakeHerdrSupportedCcbdClient):
        def project_view(self, *, schema_version: int = 1) -> dict[str, object]:
            payload = super().project_view(schema_version=schema_version)
            projection = dict(payload['view']['namespace']['herdr_surface_projection'])
            projection.update({'capability_status': 'supported', 'beta_gaps': ['mobile-terminal-validation-pending']})
            payload['view']['namespace']['herdr_surface_projection'] = projection
            payload['view']['agents'][0]['herdr_surface_projection'] = projection
            return payload

    service = _service(_ContradictoryHerdrClient(), mobile_dir=tmp_path / 'mobile')
    pairing = service.create_pairing_payload(gateway_url='http://127.0.0.1:8787')
    _, claim = service.dispatch_post('/v1/pairing/claim', {'pairing_code': str(pairing['pairing_code'])})

    with pytest.raises(MobileGatewayError) as exc_info:
        service.dispatch_post(
            '/v1/projects/proj-demo/terminals',
            {
                'project_id': 'proj-demo',
                'namespace_epoch': 4,
                'target': {'kind': 'agent', 'agent': 'mobile', 'window': 'main'},
                'geometry': {'columns': 100, 'rows': 30},
            },
            {'Authorization': f'Bearer {claim["device_token"]}'},
        )

    assert exc_info.value.status_code == 409
    blocked = getattr(exc_info.value, 'terminal_blocked')
    assert blocked['code'] == 'attach_unsupported'
    assert blocked['backend_impl'] == 'herdr'
    assert blocked['beta_gaps'] == ['mobile-terminal-validation-pending']


def test_terminal_open_http_returns_herdr_blocked_payload(tmp_path: Path) -> None:
    service = _service(_FakeHerdrCcbdClient(), mobile_dir=tmp_path / 'mobile')
    pairing = service.create_pairing_payload(gateway_url='http://127.0.0.1:8787')
    server = build_mobile_gateway_server(parse_listen_address('127.0.0.1:0'), service)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    try:
        thread.start()
        host, port = server.server_address[:2]
        base = f'http://{host}:{port}'
        claim_request = Request(
            f'{base}/v1/pairing/claim',
            data=json.dumps({'pairing_code': pairing['pairing_code']}).encode('utf-8'),
            method='POST',
            headers={'Content-Type': 'application/json'},
        )
        with urlopen(claim_request) as response:
            claim = json.loads(response.read().decode('utf-8'))
        terminal_request = Request(
            f'{base}/v1/projects/proj-demo/terminals',
            data=json.dumps(
                {
                    'project_id': 'proj-demo',
                    'namespace_epoch': 4,
                    'target': {'kind': 'agent', 'agent': 'mobile', 'window': 'main'},
                    'geometry': {'columns': 100, 'rows': 30},
                }
            ).encode('utf-8'),
            method='POST',
            headers={
                'Authorization': f'Bearer {claim["device_token"]}',
                'Content-Type': 'application/json',
            },
        )

        with pytest.raises(HTTPError) as exc_info:
            urlopen(terminal_request)
        payload = json.loads(exc_info.value.read().decode('utf-8'))
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)

    assert exc_info.value.code == 409
    assert payload['status'] == 'blocked'
    assert payload['terminal_blocked']['code'] == 'attach_unsupported'
    assert payload['terminal_blocked']['backend_impl'] == 'herdr'


def test_terminal_websocket_open_returns_herdr_blocked_payload(tmp_path: Path) -> None:
    class _SwitchingHerdrClient(_FakeHerdrSupportedCcbdClient):
        blocked = False

        def project_view(self, *, schema_version: int = 1) -> dict[str, object]:
            if self.blocked:
                return _FakeHerdrCcbdClient.project_view(self, schema_version=schema_version)
            return super().project_view(schema_version=schema_version)

    client = _SwitchingHerdrClient()
    service = _service(client, mobile_dir=tmp_path / 'mobile')
    pairing = service.create_pairing_payload(gateway_url='http://127.0.0.1:8787')
    _, claim = service.dispatch_post('/v1/pairing/claim', {'pairing_code': str(pairing['pairing_code'])})
    _, handle = service.dispatch_post(
        '/v1/projects/proj-demo/terminals',
        {
            'project_id': 'proj-demo',
            'namespace_epoch': 4,
            'target': {'kind': 'agent', 'agent': 'mobile', 'window': 'main'},
            'geometry': {'columns': 100, 'rows': 30},
        },
        {'Authorization': f'Bearer {claim["device_token"]}'},
    )
    client.blocked = True
    server = build_mobile_gateway_server(parse_listen_address('127.0.0.1:0'), service)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    sock = None
    try:
        thread.start()
        host, port = server.server_address[:2]
        sock = _websocket_connect(host, port, f'/v1/terminals/{handle["terminal_id"]}')
        _websocket_send_json(
            sock,
            {
                'type': 'open',
                'terminal_id': handle['terminal_id'],
                'token': handle['terminal_token'],
            },
        )

        error = _websocket_read_until(sock, 'error')
    finally:
        if sock is not None:
            sock.close()
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)

    assert error['code'] == 'attach_unsupported'
    assert error['terminal_blocked']['code'] == 'attach_unsupported'
    assert error['terminal_blocked']['backend_impl'] == 'herdr'


def test_terminal_websocket_default_herdr_adapter_missing_returns_blocked(tmp_path: Path) -> None:
    service = _service(_FakeHerdrSupportedCcbdClient(), mobile_dir=tmp_path / 'mobile')
    pairing = service.create_pairing_payload(gateway_url='http://127.0.0.1:8787')
    _, claim = service.dispatch_post('/v1/pairing/claim', {'pairing_code': str(pairing['pairing_code'])})
    _, handle = service.dispatch_post(
        '/v1/projects/proj-demo/terminals',
        {
            'project_id': 'proj-demo',
            'namespace_epoch': 4,
            'target': {'kind': 'agent', 'agent': 'mobile', 'window': 'main'},
            'geometry': {'columns': 100, 'rows': 30},
        },
        {'Authorization': f'Bearer {claim["device_token"]}'},
    )
    server = build_mobile_gateway_server(parse_listen_address('127.0.0.1:0'), service)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    sock = None
    try:
        thread.start()
        host, port = server.server_address[:2]
        sock = _websocket_connect(host, port, f'/v1/terminals/{handle["terminal_id"]}')
        _websocket_send_json(
            sock,
            {
                'type': 'open',
                'terminal_id': handle['terminal_id'],
                'token': handle['terminal_token'],
            },
        )

        error = _websocket_read_until(sock, 'error')
    finally:
        if sock is not None:
            sock.close()
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)

    assert error['code'] == 'target_not_attachable'
    assert error['terminal_blocked']['code'] == 'target_not_attachable'
    assert error['terminal_blocked']['blocking_gaps'] == ['herdr-terminal-adapter-unavailable']
    assert 'tmux' not in error['message']


@pytest.mark.parametrize(
    'target,expected_gap',
    [
        ({'kind': 'window_active_pane', 'window': 'logs'}, 'window-active-pane-does-not-match-herdr-pane'),
        ({'kind': 'pane_evidence', 'window': 'main', 'pane_id': 'pane-2'}, 'pane-evidence-does-not-match-herdr-pane'),
    ],
)
def test_terminal_open_herdr_supported_target_mismatch_fails_closed(
    tmp_path: Path,
    target: dict[str, object],
    expected_gap: str,
) -> None:
    class _MismatchedHerdrClient(_FakeHerdrSupportedCcbdClient):
        def project_view(self, *, schema_version: int = 1) -> dict[str, object]:
            payload = super().project_view(schema_version=schema_version)
            payload['view']['windows'].append(
                {
                    'name': 'logs',
                    'label': 'logs',
                    'kind': 'logs',
                    'order': 1,
                    'active': False,
                    'agents': [],
                    'active_pane_id': 'pane-2',
                }
            )
            return payload

    service = _service(_MismatchedHerdrClient(), mobile_dir=tmp_path / 'mobile')
    pairing = service.create_pairing_payload(gateway_url='http://127.0.0.1:8787')
    _, claim = service.dispatch_post('/v1/pairing/claim', {'pairing_code': str(pairing['pairing_code'])})

    with pytest.raises(MobileGatewayError) as exc_info:
        service.dispatch_post(
            '/v1/projects/proj-demo/terminals',
            {
                'project_id': 'proj-demo',
                'namespace_epoch': 4,
                'target': target,
                'geometry': {'columns': 100, 'rows': 30},
            },
            {'Authorization': f'Bearer {claim["device_token"]}'},
        )

    assert exc_info.value.status_code == 409
    blocked = getattr(exc_info.value, 'terminal_blocked')
    assert blocked['code'] == 'target_not_attachable'
    assert blocked['blocking_gaps'] == [expected_gap]


def test_terminal_open_rejects_missing_scope_and_stale_epoch(tmp_path: Path) -> None:
    service = _service(_FakeCcbdClient(), mobile_dir=tmp_path / 'mobile')
    pairing = service.create_pairing_payload(
        gateway_url='http://127.0.0.1:8787',
        scopes=('view', 'focus'),
    )
    _, claim = service.dispatch_post(
        '/v1/pairing/claim',
        {
            'pairing_code': str(pairing['pairing_code']),
            'device_name': 'Pixel Fold',
        },
    )
    view_only_token = str(claim['device_token'])
    request = {
        'schema_version': 1,
        'project_id': 'proj-demo',
        'namespace_epoch': 4,
        'target': {
            'kind': 'agent',
            'agent': 'mobile',
            'window': 'main',
            'pane_id': '%2',
        },
        'geometry': {
            'columns': 100,
            'rows': 30,
        },
    }

    with pytest.raises(MobileGatewayError) as missing:
        service.dispatch_post('/v1/projects/proj-demo/terminals', request)
    assert missing.value.status_code == 401

    with pytest.raises(MobileGatewayError) as denied:
        service.dispatch_post(
            '/v1/projects/proj-demo/terminals',
            request,
            {'Authorization': f'Bearer {view_only_token}'},
        )
    assert denied.value.status_code == 403

    terminal_pairing = service.create_pairing_payload(gateway_url='http://127.0.0.1:8787')
    _, terminal_claim = service.dispatch_post(
        '/v1/pairing/claim',
        {
            'pairing_code': str(terminal_pairing['pairing_code']),
            'device_name': 'iPad',
        },
    )
    terminal_token = str(terminal_claim['device_token'])
    stale_request = dict(request)
    stale_request['namespace_epoch'] = 3
    with pytest.raises(MobileGatewayError) as stale:
        service.dispatch_post(
            '/v1/projects/proj-demo/terminals',
            stale_request,
            {'Authorization': f'Bearer {terminal_token}'},
        )
    assert stale.value.status_code == 409


def test_terminal_websocket_streams_frames_and_rejects_replayed_input(tmp_path: Path) -> None:
    sessions: list[_FakeTerminalSession] = []

    def session_factory(target):
        session = _FakeTerminalSession(target)
        sessions.append(session)
        return session

    service = _service(
        _FakeCcbdClient(),
        mobile_dir=tmp_path / 'mobile',
        terminal_session_factory=session_factory,
    )
    pairing = service.create_pairing_payload(gateway_url='http://127.0.0.1:8787')
    _, claim = service.dispatch_post(
        '/v1/pairing/claim',
        {
            'pairing_code': str(pairing['pairing_code']),
            'device_name': 'Pixel Fold',
        },
    )
    _, handle = service.dispatch_post(
        '/v1/projects/proj-demo/terminals',
        {
            'project_id': 'proj-demo',
            'namespace_epoch': 4,
            'target': {
                'kind': 'agent',
                'agent': 'mobile',
                'window': 'main',
            },
            'geometry': {
                'columns': 100,
                'rows': 30,
            },
        },
        {'Authorization': f'Bearer {claim["device_token"]}'},
    )
    server = build_mobile_gateway_server(parse_listen_address('127.0.0.1:0'), service)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    sock = None
    try:
        thread.start()
        host, port = server.server_address[:2]
        sock = _websocket_connect(host, port, f'/v1/terminals/{handle["terminal_id"]}')
        _websocket_send_json(
            sock,
            {
                'type': 'open',
                'terminal_id': handle['terminal_id'],
                'token': handle['terminal_token'],
            },
        )

        open_ack = _websocket_read_until(sock, 'open')
        assert open_ack['geometry'] == {
            'columns': 100,
            'rows': 30,
            'pixel_width': 0,
            'pixel_height': 0,
        }
        assert open_ack['resize_policy'] == 'fixed_source'
        output = _websocket_read_until(sock, 'output')
        assert output['seq'] == 1
        assert base64.b64decode(str(output['bytes_b64'])) == b'hello'
        assert output['render_mode'] == 'replace_snapshot'
        assert output['history_reset'] is True
        assert base64.b64decode(str(output['history_b64'])) == b'older history\n'
        assert base64.b64decode(str(output['screen_b64'])) == b'hello'
        assert sessions
        assert sessions[0].target.socket_path == '/tmp/ccb-demo/tmux.sock'
        assert sessions[0].target.session_name == 'ccb-demo'
        assert sessions[0].target.pane_id == '%2'
        assert sessions[0].target.command == [
            'tmux',
            '-S',
            '/tmp/ccb-demo/tmux.sock',
            'capture-pane',
            '-p',
            '-e',
            '-t',
            '%2',
            '-S',
            '-1000',
        ]
        assert 'attach-session' not in sessions[0].target.command
        assert sessions[0].target.geometry.columns == 100
        assert sessions[0].target.geometry.rows == 30
        assert sessions[0].target.include_history is True

        sessions[0].viewport_geometry = terminal_module.TerminalGeometry(
            columns=164,
            rows=47,
        )
        sessions[0].viewport_revision += 1
        geometry = _websocket_read_until(sock, 'geometry')
        assert geometry == {
            'type': 'geometry',
            'columns': 164,
            'rows': 47,
            'pixel_width': 0,
            'pixel_height': 0,
            'resize_policy': 'fixed_source',
            'revision': 1,
        }

        _websocket_send_json(sock, {'type': 'input', 'seq': 1, 'bytes_b64': base64.b64encode(b'a').decode('ascii')})
        _wait_for(lambda: sessions[0].writes == [b'a'])
        _websocket_send_json(sock, {'type': 'paste', 'seq': 2, 'text': 'hello paste'})
        _wait_for(lambda: sessions[0].pastes == ['hello paste'])
        projects = service.projects_payload()
        assert projects['projects'][0]['last_activity_at'] == '2026-06-18T00:00:00Z'
        _websocket_send_json(sock, {'type': 'resize', 'columns': 120, 'rows': 36})
        time.sleep(0.05)
        assert sessions[0].resizes == []

        _websocket_send_json(sock, {'type': 'input', 'seq': 2, 'bytes_b64': base64.b64encode(b'b').decode('ascii')})
        error = _websocket_read_until(sock, 'error')
        assert error['code'] == 'replayed_sequence'
        closed = _websocket_read_until(sock, 'closed')
        assert closed['reason'] == 'replayed_sequence'
        _wait_for(lambda: sessions[0].closed)

        stored_tokens = (tmp_path / 'mobile' / 'terminal-tokens.jsonl').read_text(encoding='utf-8')
        assert str(handle['terminal_token']) not in stored_tokens
        assert '"last_input_seq": 2' in stored_tokens
        assert '"closed_reason": "replayed_sequence"' in stored_tokens
    finally:
        if sock is not None:
            sock.close()
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


def test_gateway_close_closes_active_terminal_sessions(tmp_path: Path) -> None:
    sessions: list[_FakeTerminalSession] = []

    def session_factory(target):
        session = _FakeTerminalSession(target)
        sessions.append(session)
        return session

    service = _service(
        _FakeCcbdClient(),
        mobile_dir=tmp_path / 'mobile',
        terminal_session_factory=session_factory,
    )
    pairing = service.create_pairing_payload(gateway_url='http://127.0.0.1:8787')
    _, claim = service.dispatch_post(
        '/v1/pairing/claim',
        {
            'pairing_code': str(pairing['pairing_code']),
            'device_name': 'Pixel Fold',
        },
    )
    _, handle = service.dispatch_post(
        '/v1/projects/proj-demo/terminals',
        {
            'project_id': 'proj-demo',
            'namespace_epoch': 4,
            'target': {
                'kind': 'agent',
                'agent': 'mobile',
                'window': 'main',
            },
            'geometry': {'columns': 100, 'rows': 30},
        },
        {'Authorization': f'Bearer {claim["device_token"]}'},
    )
    server = build_mobile_gateway_server(parse_listen_address('127.0.0.1:0'), service)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    sock = None
    try:
        thread.start()
        host, port = server.server_address[:2]
        sock = _websocket_connect(host, port, f'/v1/terminals/{handle["terminal_id"]}')
        _websocket_send_json(
            sock,
            {
                'type': 'open',
                'terminal_id': handle['terminal_id'],
                'token': handle['terminal_token'],
            },
        )
        _websocket_read_until(sock, 'open')
        _wait_for(lambda: bool(sessions))

        service.close()

        _wait_for(lambda: sessions[0].closed)
    finally:
        if sock is not None:
            sock.close()
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


def test_terminal_websocket_delivers_text_and_enter_in_one_input_frame(
    tmp_path: Path,
    monkeypatch,
) -> None:
    tmux_calls: list[list[str]] = []

    def fake_tmux_run(command, **kwargs):
        call = list(command)
        tmux_calls.append(call)
        if 'display-message' in call:
            output = (
                b'0\n'
                if '#{history_size}' in call
                else '@0\t113\t30\t100\t30\tlayout-token'
            )
        else:
            output = b'prompt$ ' if 'capture-pane' in call else b''
        return type('Completed', (), {'returncode': 0, 'stdout': output, 'stderr': b''})()

    monkeypatch.setattr(terminal_module.subprocess, 'run', fake_tmux_run)
    service = _service(
        _FakeCcbdClient(),
        mobile_dir=tmp_path / 'mobile',
        terminal_session_factory=terminal_module.TmuxTerminalSession,
    )
    pairing = service.create_pairing_payload(gateway_url='http://127.0.0.1:8787')
    _, claim = service.dispatch_post(
        '/v1/pairing/claim',
        {'pairing_code': str(pairing['pairing_code'])},
    )
    _, handle = service.dispatch_post(
        '/v1/projects/proj-demo/terminals',
        {
            'project_id': 'proj-demo',
            'namespace_epoch': 4,
            'target': {'kind': 'agent', 'agent': 'mobile', 'window': 'main'},
        },
        {'Authorization': f'Bearer {claim["device_token"]}'},
    )
    server = build_mobile_gateway_server(parse_listen_address('127.0.0.1:0'), service)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    sock = None
    try:
        thread.start()
        host, port = server.server_address[:2]
        sock = _websocket_connect(host, port, f'/v1/terminals/{handle["terminal_id"]}')
        _websocket_send_json(
            sock,
            {
                'type': 'open',
                'terminal_id': handle['terminal_id'],
                'token': handle['terminal_token'],
            },
        )
        _websocket_read_until(sock, 'output')

        _websocket_send_json(
            sock,
            {
                'type': 'input',
                'seq': 1,
                'bytes_b64': base64.b64encode(b'test2\r').decode('ascii'),
            },
        )
        expected = [
            ['tmux', '-S', '/tmp/ccb-demo/tmux.sock', 'send-keys', '-t', '%2', '-l', 'test2'],
            ['tmux', '-S', '/tmp/ccb-demo/tmux.sock', 'send-keys', '-t', '%2', 'Enter'],
        ]
        _wait_for(lambda: all(call in tmux_calls for call in expected))

        _websocket_send_json(
            sock,
            {
                'type': 'input',
                'seq': 2,
                'bytes_b64': base64.b64encode(b'test3\r').decode('ascii'),
            },
        )
        _wait_for(
            lambda: sum(call[-1:] == ['Enter'] for call in tmux_calls) == 2,
        )
    finally:
        if sock is not None:
            sock.close()
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


def test_terminal_websocket_uses_herdr_backend_neutral_target(tmp_path: Path) -> None:
    sessions: list[_FakeTerminalSession] = []

    def session_factory(target):
        session = _FakeTerminalSession(target)
        sessions.append(session)
        return session

    service = _service(
        _FakeHerdrSupportedCcbdClient(),
        mobile_dir=tmp_path / 'mobile',
        terminal_session_factory=session_factory,
    )
    pairing = service.create_pairing_payload(gateway_url='http://127.0.0.1:8787')
    _, claim = service.dispatch_post('/v1/pairing/claim', {'pairing_code': str(pairing['pairing_code'])})
    _, handle = service.dispatch_post(
        '/v1/projects/proj-demo/terminals',
        {
            'project_id': 'proj-demo',
            'namespace_epoch': 4,
            'target': {'kind': 'agent', 'agent': 'mobile', 'window': 'main'},
            'geometry': {'columns': 100, 'rows': 30},
        },
        {'Authorization': f'Bearer {claim["device_token"]}'},
    )
    server = build_mobile_gateway_server(parse_listen_address('127.0.0.1:0'), service)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    sock = None
    try:
        thread.start()
        host, port = server.server_address[:2]
        sock = _websocket_connect(host, port, f'/v1/terminals/{handle["terminal_id"]}')
        _websocket_send_json(
            sock,
            {
                'type': 'open',
                'terminal_id': handle['terminal_id'],
                'token': handle['terminal_token'],
            },
        )

        output = _websocket_read_until(sock, 'output')
        assert base64.b64decode(str(output['bytes_b64'])) == b'hello'
        assert sessions
        target = sessions[0].target
        assert target.backend_impl == 'herdr'
        assert target.socket_path == ''
        assert target.session_name == 'ccb-herdr'
        assert target.pane_ref == {'backend_impl': 'herdr', 'pane_id': 'pane-1'}
        assert target.namespace_ref['namespace_id'] == 'workspace-1'
        assert target.attach_supported is True
        _websocket_send_json(sock, {'type': 'input', 'seq': 1, 'bytes_b64': base64.b64encode(b'a').decode('ascii')})
        _wait_for(lambda: sessions[0].writes == [b'a'])
    finally:
        if sock is not None:
            sock.close()
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


def test_terminal_websocket_resumes_after_transport_disconnect_with_matching_cursor(tmp_path: Path) -> None:
    sessions: list[_FakeTerminalSession] = []

    def session_factory(target):
        session = _FakeTerminalSession(target)
        sessions.append(session)
        return session

    service = _service(
        _FakeCcbdClient(),
        mobile_dir=tmp_path / 'mobile',
        terminal_session_factory=session_factory,
    )
    pairing = service.create_pairing_payload(gateway_url='http://127.0.0.1:8787')
    _, claim = service.dispatch_post('/v1/pairing/claim', {'pairing_code': str(pairing['pairing_code'])})
    _, handle = service.dispatch_post(
        '/v1/projects/proj-demo/terminals',
        {
            'project_id': 'proj-demo',
            'namespace_epoch': 4,
            'target': {'kind': 'agent', 'agent': 'mobile', 'window': 'main'},
        },
        {'Authorization': f'Bearer {claim["device_token"]}'},
    )
    server = build_mobile_gateway_server(parse_listen_address('127.0.0.1:0'), service)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    sock = None
    resumed_sock = None
    try:
        thread.start()
        host, port = server.server_address[:2]
        sock = _websocket_connect(host, port, f'/v1/terminals/{handle["terminal_id"]}')
        _websocket_send_json(
            sock,
            {
                'type': 'open',
                'terminal_id': handle['terminal_id'],
                'token': handle['terminal_token'],
            },
        )
        first_output = _websocket_read_until(sock, 'output')
        assert first_output['seq'] == 1
        sock.close()
        sock = None
        _wait_for(lambda: sessions[0].closed)
        stored_after_disconnect = (tmp_path / 'mobile' / 'terminal-tokens.jsonl').read_text(encoding='utf-8')
        assert '"last_output_seq": 1' in stored_after_disconnect
        assert '"disconnected_reason": "transport_disconnected"' in stored_after_disconnect
        assert '"closed_reason"' not in stored_after_disconnect

        resumed_sock = _websocket_connect(host, port, f'/v1/terminals/{handle["terminal_id"]}')
        _websocket_send_json(
            resumed_sock,
            {
                'type': 'open',
                'terminal_id': handle['terminal_id'],
                'token': handle['terminal_token'],
                'resume_cursor': 1,
            },
        )
        open_ack = _websocket_read_until(resumed_sock, 'open')
        assert open_ack['resume_cursor'] == 1
        assert open_ack['last_input_seq'] == 0
        second_output = _websocket_read_until(resumed_sock, 'output')
        assert second_output['seq'] == 2
        assert len(sessions) == 2
        assert sessions[1].target.include_history is False
        _wait_for(
            lambda: '"last_output_seq": 2'
            in (tmp_path / 'mobile' / 'terminal-tokens.jsonl').read_text(encoding='utf-8')
        )

        _websocket_send_json(resumed_sock, {'type': 'closed', 'reason': 'client_closed'})
        closed = _websocket_read_until(resumed_sock, 'closed')
        assert closed['reason'] == 'client_closed'
        _wait_for(lambda: sessions[1].closed)
        stored_after_close = (tmp_path / 'mobile' / 'terminal-tokens.jsonl').read_text(encoding='utf-8')
        assert '"last_output_seq": 2' in stored_after_close
        assert '"closed_reason": "client_closed"' in stored_after_close
    finally:
        if sock is not None:
            sock.close()
        if resumed_sock is not None:
            resumed_sock.close()
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


def test_terminal_websocket_accepts_stale_output_resume_cursor(tmp_path: Path) -> None:
    sessions: list[_FakeTerminalSession] = []
    service = _service(
        _FakeCcbdClient(),
        mobile_dir=tmp_path / 'mobile',
        terminal_session_factory=lambda target: sessions.append(_FakeTerminalSession(target)) or sessions[-1],
    )
    pairing = service.create_pairing_payload(gateway_url='http://127.0.0.1:8787')
    _, claim = service.dispatch_post('/v1/pairing/claim', {'pairing_code': str(pairing['pairing_code'])})
    _, handle = service.dispatch_post(
        '/v1/projects/proj-demo/terminals',
        {
            'project_id': 'proj-demo',
            'namespace_epoch': 4,
            'target': {'kind': 'agent', 'agent': 'mobile', 'window': 'main'},
        },
        {'Authorization': f'Bearer {claim["device_token"]}'},
    )
    server = build_mobile_gateway_server(parse_listen_address('127.0.0.1:0'), service)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    sock = None
    stale_sock = None
    try:
        thread.start()
        host, port = server.server_address[:2]
        sock = _websocket_connect(host, port, f'/v1/terminals/{handle["terminal_id"]}')
        _websocket_send_json(
            sock,
            {
                'type': 'open',
                'terminal_id': handle['terminal_id'],
                'token': handle['terminal_token'],
            },
        )
        assert _websocket_read_until(sock, 'output')['seq'] == 1
        sock.close()
        sock = None
        _wait_for(lambda: sessions[0].closed)

        stored_after_disconnect = (tmp_path / 'mobile' / 'terminal-tokens.jsonl').read_text(encoding='utf-8')
        assert '"last_output_seq": 1' in stored_after_disconnect

        stale_sock = _websocket_connect(host, port, f'/v1/terminals/{handle["terminal_id"]}')
        _websocket_send_json(
            stale_sock,
            {
                'type': 'open',
                'terminal_id': handle['terminal_id'],
                'token': handle['terminal_token'],
                'resume_cursor': 0,
            },
        )
        open_ack = _websocket_read_until(stale_sock, 'open')
        assert open_ack['resume_cursor'] == 1
        output = _websocket_read_until(stale_sock, 'output')
        assert output['seq'] == 2
        assert len(sessions) == 2
        assert sessions[1].target.include_history is False
        stored_after_resume = (tmp_path / 'mobile' / 'terminal-tokens.jsonl').read_text(encoding='utf-8')
        assert '"last_resume_cursor": 0' in stored_after_resume
        assert '"last_resume_gap": 1' in stored_after_resume
    finally:
        if sock is not None:
            sock.close()
        if stale_sock is not None:
            stale_sock.close()
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


def test_terminal_websocket_rejects_future_resume_cursor(tmp_path: Path) -> None:
    sessions: list[_FakeTerminalSession] = []
    service = _service(
        _FakeCcbdClient(),
        mobile_dir=tmp_path / 'mobile',
        terminal_session_factory=lambda target: sessions.append(_FakeTerminalSession(target)) or sessions[-1],
    )
    pairing = service.create_pairing_payload(gateway_url='http://127.0.0.1:8787')
    _, claim = service.dispatch_post('/v1/pairing/claim', {'pairing_code': str(pairing['pairing_code'])})
    _, handle = service.dispatch_post(
        '/v1/projects/proj-demo/terminals',
        {
            'project_id': 'proj-demo',
            'namespace_epoch': 4,
            'target': {'kind': 'agent', 'agent': 'mobile', 'window': 'main'},
        },
        {'Authorization': f'Bearer {claim["device_token"]}'},
    )
    server = build_mobile_gateway_server(parse_listen_address('127.0.0.1:0'), service)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    sock = None
    future_sock = None
    try:
        thread.start()
        host, port = server.server_address[:2]
        sock = _websocket_connect(host, port, f'/v1/terminals/{handle["terminal_id"]}')
        _websocket_send_json(
            sock,
            {
                'type': 'open',
                'terminal_id': handle['terminal_id'],
                'token': handle['terminal_token'],
            },
        )
        assert _websocket_read_until(sock, 'output')['seq'] == 1
        sock.close()
        sock = None
        _wait_for(lambda: sessions[0].closed)

        future_sock = _websocket_connect(host, port, f'/v1/terminals/{handle["terminal_id"]}')
        _websocket_send_json(
            future_sock,
            {
                'type': 'open',
                'terminal_id': handle['terminal_id'],
                'token': handle['terminal_token'],
                'resume_cursor': 99,
            },
        )
        error = _websocket_read_until(future_sock, 'error')
        assert error['code'] == 'stale_resume_cursor'
        closed = _websocket_read_until(future_sock, 'closed')
        assert closed['reason'] == 'stale_resume_cursor'
        assert len(sessions) == 1
    finally:
        if sock is not None:
            sock.close()
        if future_sock is not None:
            future_sock.close()
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


def test_terminal_websocket_rejects_invalid_open_token(tmp_path: Path) -> None:
    sessions: list[_FakeTerminalSession] = []
    service = _service(
        _FakeCcbdClient(),
        mobile_dir=tmp_path / 'mobile',
        terminal_session_factory=lambda target: sessions.append(_FakeTerminalSession(target)) or sessions[-1],
    )
    pairing = service.create_pairing_payload(gateway_url='http://127.0.0.1:8787')
    _, claim = service.dispatch_post('/v1/pairing/claim', {'pairing_code': str(pairing['pairing_code'])})
    _, handle = service.dispatch_post(
        '/v1/projects/proj-demo/terminals',
        {
            'project_id': 'proj-demo',
            'namespace_epoch': 4,
            'target': {'kind': 'agent', 'agent': 'mobile', 'window': 'main'},
        },
        {'Authorization': f'Bearer {claim["device_token"]}'},
    )
    server = build_mobile_gateway_server(parse_listen_address('127.0.0.1:0'), service)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    sock = None
    try:
        thread.start()
        host, port = server.server_address[:2]
        sock = _websocket_connect(host, port, f'/v1/terminals/{handle["terminal_id"]}')
        _websocket_send_json(
            sock,
            {
                'type': 'open',
                'terminal_id': handle['terminal_id'],
                'token': 'wrong-token',
            },
        )
        error = _websocket_read_until(sock, 'error')
        assert error['code'] == 'invalid_token'
        assert sessions == []
    finally:
        if sock is not None:
            sock.close()
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


def test_focus_routes_require_focus_scope_and_return_redacted_project_view(tmp_path: Path) -> None:
    fake = _FakeCcbdClient()
    service = _service(fake, mobile_dir=tmp_path / 'mobile')
    pairing = service.create_pairing_payload(gateway_url='http://127.0.0.1:8787')
    _, claim = service.dispatch_post(
        '/v1/pairing/claim',
        {
            'pairing_code': str(pairing['pairing_code']),
            'device_name': 'Pixel Fold',
        },
    )
    token = str(claim['device_token'])

    status, focused = service.dispatch_post(
        '/v1/projects/proj-demo/focus-agent',
        {
            'agent': 'mobile',
            'namespace_epoch': 4,
        },
        {'Authorization': f'Bearer {token}'},
    )
    assert status == 200
    assert focused['focus']['focused'] is True
    assert focused['focus']['agent'] == 'mobile'
    assert focused['view']['namespace']['epoch'] == 4
    assert 'socket_path' not in focused['view']['namespace']
    assert 'session_name' not in focused['view']['namespace']

    status, focused = service.dispatch_post(
        '/v1/projects/proj-demo/focus-window',
        {
            'window': 'main',
            'namespace_epoch': 4,
        },
        {'Authorization': f'Bearer {token}'},
    )
    assert status == 200
    assert focused['focus']['kind'] == 'window'
    assert ('project_focus_agent', 'mobile', 4) in fake.calls
    assert ('project_focus_window', 'main', 4) in fake.calls


def test_focus_routes_reject_missing_or_view_only_device_scope(tmp_path: Path) -> None:
    service = _service(_FakeCcbdClient(), mobile_dir=tmp_path / 'mobile')
    pairing = service.create_pairing_payload(
        gateway_url='http://127.0.0.1:8787',
        scopes=('view',),
    )
    _, claim = service.dispatch_post(
        '/v1/pairing/claim',
        {
            'pairing_code': str(pairing['pairing_code']),
            'device_name': 'Pixel Fold',
        },
    )
    token = str(claim['device_token'])

    with pytest.raises(MobileGatewayError) as missing:
        service.dispatch_post(
            '/v1/projects/proj-demo/focus-agent',
            {'agent': 'mobile', 'namespace_epoch': 4},
        )
    assert missing.value.status_code == 401

    with pytest.raises(MobileGatewayError) as denied:
        service.dispatch_post(
            '/v1/projects/proj-demo/focus-agent',
            {'agent': 'mobile', 'namespace_epoch': 4},
            {'Authorization': f'Bearer {token}'},
        )
    assert denied.value.status_code == 403


def test_lifecycle_route_uses_lifecycle_scope_and_ccbd_stop_authority(tmp_path: Path) -> None:
    fake = _FakeCcbdClient()
    service = _service(fake, mobile_dir=tmp_path / 'mobile')
    pairing = service.create_pairing_payload(gateway_url='http://127.0.0.1:8787')
    _, claim = service.dispatch_post(
        '/v1/pairing/claim',
        {
            'pairing_code': str(pairing['pairing_code']),
            'device_name': 'Pixel Fold',
        },
    )
    token = str(claim['device_token'])

    status, opened = service.dispatch_post(
        '/v1/projects/proj-demo/lifecycle',
        {'action': 'open', 'project_id': 'proj-demo'},
        {'Authorization': f'Bearer {token}'},
    )
    assert status == 200
    assert opened['lifecycle']['action'] == 'open'
    assert opened['lifecycle']['effect'] == 'opened'
    assert opened['lifecycle']['ccb_authority'] is True
    assert opened['lifecycle']['tmux_kill_server'] is False
    assert opened['view']['namespace']['epoch'] == 4
    assert 'socket_path' not in opened['view']['namespace']

    status, closed = service.dispatch_post(
        '/v1/projects/proj-demo/lifecycle',
        {'action': 'close'},
        {'Authorization': f'Bearer {token}'},
    )
    assert status == 200
    assert closed['lifecycle']['effect'] == 'mobile_view_closed'
    assert ('stop_all', False) not in fake.calls

    status, stopped = service.dispatch_post(
        '/v1/projects/proj-demo/lifecycle',
        {'action': 'stop'},
        {'Authorization': f'Bearer {token}'},
    )
    assert status == 200
    assert stopped['lifecycle']['effect'] == 'ccbd_stop_requested'
    assert stopped['lifecycle']['forced'] is False
    assert stopped['lifecycle']['result']['force'] is False
    assert stopped['lifecycle']['tmux_kill_server'] is False
    assert ('stop_all', False) in fake.calls


def test_lifecycle_route_rejects_missing_scope_and_force_stop(tmp_path: Path) -> None:
    service = _service(_FakeCcbdClient(), mobile_dir=tmp_path / 'mobile')
    pairing = service.create_pairing_payload(
        gateway_url='http://127.0.0.1:8787',
        scopes=('view', 'focus', 'terminal_input'),
    )
    _, claim = service.dispatch_post(
        '/v1/pairing/claim',
        {
            'pairing_code': str(pairing['pairing_code']),
            'device_name': 'Pixel Fold',
        },
    )
    token = str(claim['device_token'])

    with pytest.raises(MobileGatewayError) as denied:
        service.dispatch_post(
            '/v1/projects/proj-demo/lifecycle',
            {'action': 'open'},
            {'Authorization': f'Bearer {token}'},
        )
    assert denied.value.status_code == 403

    pairing = service.create_pairing_payload(
        gateway_url='http://127.0.0.1:8787',
        scopes=('view', 'lifecycle'),
    )
    _, claim = service.dispatch_post(
        '/v1/pairing/claim',
        {
            'pairing_code': str(pairing['pairing_code']),
            'device_name': 'Pixel Fold Admin',
        },
    )
    lifecycle_token = str(claim['device_token'])
    with pytest.raises(MobileGatewayError) as unsupported:
        service.dispatch_post(
            '/v1/projects/proj-demo/lifecycle',
            {'action': 'force_stop'},
            {'Authorization': f'Bearer {lifecycle_token}'},
        )
    assert unsupported.value.status_code == 400


def test_http_server_exposes_g1_get_endpoints(tmp_path: Path) -> None:
    fake = _FakeCcbdClient()
    service = _service(fake, mobile_dir=tmp_path / 'mobile')
    pairing = service.create_pairing_payload(gateway_url='http://127.0.0.1:8787')
    server = build_mobile_gateway_server(parse_listen_address('127.0.0.1:0'), service)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    try:
        thread.start()
        host, port = server.server_address[:2]
        base = f'http://{host}:{port}'

        with urlopen(f'{base}/v1/health') as response:
            health = json.loads(response.read().decode('utf-8'))
        with urlopen(f'{base}/v1/projects') as response:
            projects = json.loads(response.read().decode('utf-8'))
        claim_request = Request(
            f'{base}/v1/pairing/claim',
            data=json.dumps({'pairing_code': pairing['pairing_code']}).encode('utf-8'),
            method='POST',
            headers={'Content-Type': 'application/json'},
        )
        with urlopen(claim_request) as response:
            claim = json.loads(response.read().decode('utf-8'))
        token = str(claim['device_token'])
        view_request = Request(
            f'{base}/v1/projects/proj-demo/view',
            headers={'Authorization': f'Bearer {token}'},
        )
        with urlopen(view_request) as response:
            view = json.loads(response.read().decode('utf-8'))

        assert health['status'] == 'ok'
        assert projects['projects'][0]['id'] == 'proj-demo'
        assert 'socket_path' not in view['view']['namespace']
        with pytest.raises(HTTPError) as excinfo:
            urlopen(
                Request(
                    f'{base}/v1/projects/other/view',
                    headers={'Authorization': f'Bearer {token}'},
                )
            )
        assert excinfo.value.code == 404
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


def _write_pi_transcript(
    project_root: Path,
    *,
    agent: str,
    session_id: str,
    records: list[dict[str, object]],
    cwd: Path | None = None,
    partial_tail: str | None = None,
    timestamp: str = '2026-06-25T12:00:00.000Z',
    write_binding: bool = True,
) -> Path:
    return _write_pi_family_transcript(
        project_root,
        provider='pi',
        agent=agent,
        session_id=session_id,
        records=records,
        cwd=cwd,
        partial_tail=partial_tail,
        timestamp=timestamp,
        write_binding=write_binding,
    )


def _write_pi_family_transcript(
    project_root: Path,
    *,
    provider: str,
    agent: str,
    session_id: str,
    records: list[dict[str, object]],
    cwd: Path | None = None,
    partial_tail: str | None = None,
    timestamp: str = '2026-06-25T12:00:00.000Z',
    write_binding: bool = True,
) -> Path:
    work_dir = cwd or project_root
    session_dir = (
        project_root
        / '.ccb'
        / 'agents'
        / agent
        / 'provider-state'
        / provider
        / 'sessions'
    )
    timestamp_prefix = timestamp.replace(':', '-').replace('.', '-')
    transcript_path = session_dir / f'{timestamp_prefix}_{session_id}.jsonl'
    transcript_path.parent.mkdir(parents=True, exist_ok=True)
    header = {
        'type': 'session',
        'version': 3,
        'id': session_id,
        'timestamp': timestamp,
        'cwd': str(work_dir),
    }
    prefix_records = ({'type': 'title', 'title': 'OMP session'},) if provider == 'omp' else ()
    content = ''.join(
        f'{json.dumps(record)}\n'
        for record in (*prefix_records, header, *records)
    )
    if partial_tail is not None:
        content += partial_tail
    transcript_path.write_text(content, encoding='utf-8')
    if write_binding:
        binding = {
            'active': True,
            'agent_name': agent,
            'ccb_session_id': f'ccb-{provider}-{agent}',
            'work_dir': str(work_dir),
            'workspace_path': str(work_dir),
        }
        if provider == 'pi':
            binding.update({
                'pi_session_id': session_id,
                'pi_session_path': str(transcript_path),
                'pi_session_work_dir_norm': str(work_dir),
            })
        binding_path = project_root / '.ccb' / f'.{provider}-{agent}-session'
        binding_path.write_text(json.dumps(binding), encoding='utf-8')
    return transcript_path


def _pi_message_record(
    *,
    record_id: str,
    parent_id: str | None,
    role: str,
    text: str,
    timestamp: str,
) -> dict[str, object]:
    return {
        'type': 'message',
        'id': record_id,
        'parentId': parent_id,
        'timestamp': timestamp,
        'message': {
            'role': role,
            'content': [{'type': 'text', 'text': text}],
        },
    }


def _conversation_items_for(service: MobileGatewayService) -> list[dict[str, object]]:
    pairing = service.create_pairing_payload(
        gateway_url='http://127.0.0.1:8787',
        scopes=('view',),
    )
    _, claim = service.dispatch_post(
        '/v1/pairing/claim',
        {'pairing_code': str(pairing['pairing_code'])},
    )
    status, payload = service.dispatch_get(
        '/v1/projects/proj-demo/agents/mobile/conversation?namespace_epoch=4&limit=50',
        {'Authorization': f'Bearer {claim["device_token"]}'},
    )
    assert status == 200
    return [dict(item) for item in payload['conversation']['items']]


def _write_codex_rollout(
    project_root: Path,
    *,
    agent: str,
    thread_id: str,
    records: list[dict[str, object]],
    created_at: int = 1782350000,
    updated_at: int = 1782350001,
) -> None:
    home = project_root / '.ccb' / 'agents' / agent / 'provider-state' / 'codex' / 'home'
    rollout_path = home / 'sessions' / '2026' / '06' / '25' / f'rollout-{thread_id}.jsonl'
    rollout_path.parent.mkdir(parents=True, exist_ok=True)
    rollout_path.write_text(
        ''.join(f'{json.dumps(record)}\n' for record in records),
        encoding='utf-8',
    )
    state_path = home / 'state_5.sqlite'
    connection = sqlite3.connect(state_path)
    try:
        connection.execute(
            'create table if not exists threads ('
            'id text primary key, '
            'rollout_path text, '
            'created_at integer, '
            'updated_at integer, '
            'title text, '
            'first_user_message text, '
            'preview text)'
        )
        connection.execute(
            'insert into threads '
            '(id, rollout_path, created_at, updated_at, title, first_user_message, preview) '
            'values (?, ?, ?, ?, ?, ?, ?)',
            (
                thread_id,
                str(rollout_path),
                created_at,
                updated_at,
                'native transcript',
                'native question',
                'native preview',
            ),
        )
        connection.commit()
    finally:
        connection.close()


def _write_claude_transcript(
    project_root: Path,
    *,
    agent: str,
    session_id: str,
    records: list[dict[str, object]],
    bind_session_path: bool = True,
    use_project_dir: bool = False,
) -> None:
    if use_project_dir:
        from provider_backends.claude.registry_support.pathing import (
            project_key_for_path,
        )

        discovery_work_dir = Path('C:/ccb-mobile-test/repo') if os.name == 'nt' else project_root
        session_parent = project_key_for_path(discovery_work_dir)
    else:
        discovery_work_dir = project_root
        session_parent = ''
    projects_root = (
        project_root
        / '.ccb'
        / 'agents'
        / agent
        / 'provider-state'
        / 'claude'
        / 'home'
        / '.claude'
        / 'projects'
    )
    transcript_path = (
        projects_root
        / session_parent
        / f'{session_id}.jsonl'
    )
    transcript_path.parent.mkdir(parents=True, exist_ok=True)
    transcript_path.write_text(
        ''.join(f'{json.dumps(record)}\n' for record in records),
        encoding='utf-8',
    )
    session_file = project_root / '.ccb' / f'.claude-{agent}-session'
    session_file.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        'active': True,
        'work_dir': str(discovery_work_dir),
        'claude_session_id': session_id,
        'claude_projects_root': str(projects_root),
    }
    if bind_session_path:
        payload['claude_session_path'] = str(transcript_path)
    session_file.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding='utf-8',
    )


def _websocket_connect(host: str, port: int, path: str) -> socket.socket:
    sock = socket.create_connection((host, port), timeout=2)
    key = base64.b64encode(os.urandom(16)).decode('ascii')
    request = (
        f'GET {path} HTTP/1.1\r\n'
        f'Host: {host}:{port}\r\n'
        'Upgrade: websocket\r\n'
        'Connection: Upgrade\r\n'
        f'Sec-WebSocket-Key: {key}\r\n'
        'Sec-WebSocket-Version: 13\r\n'
        '\r\n'
    )
    sock.sendall(request.encode('ascii'))
    response = b''
    while b'\r\n\r\n' not in response:
        response += sock.recv(4096)
    assert b' 101 ' in response.split(b'\r\n', 1)[0]
    return sock


def _websocket_send_json(sock: socket.socket, payload: dict[str, object]) -> None:
    body = json.dumps(payload).encode('utf-8')
    header = bytearray([0x81])
    length = len(body)
    if length < 126:
        header.append(0x80 | length)
    elif length <= 0xFFFF:
        header.append(0x80 | 126)
        header.extend(struct.pack('!H', length))
    else:
        header.append(0x80 | 127)
        header.extend(struct.pack('!Q', length))
    mask = b'\x01\x02\x03\x04'
    encoded = bytes(byte ^ mask[index % 4] for index, byte in enumerate(body))
    sock.sendall(bytes(header) + mask + encoded)


def _websocket_read_json(sock: socket.socket) -> dict[str, object]:
    sock.settimeout(2)
    first = _recv_exact(sock, 2)
    opcode = first[0] & 0x0F
    length = first[1] & 0x7F
    if length == 126:
        length = struct.unpack('!H', _recv_exact(sock, 2))[0]
    elif length == 127:
        length = struct.unpack('!Q', _recv_exact(sock, 8))[0]
    payload = _recv_exact(sock, length)
    if opcode == 0x8:
        return {'type': 'closed', 'reason': 'websocket_closed'}
    decoded = json.loads(payload.decode('utf-8'))
    assert isinstance(decoded, dict)
    return {str(key): value for key, value in decoded.items()}


def _websocket_read_until(sock: socket.socket, frame_type: str) -> dict[str, object]:
    deadline = time.time() + 2
    while time.time() < deadline:
        frame = _websocket_read_json(sock)
        if frame.get('type') == frame_type:
            return frame
    raise AssertionError(f'websocket frame not received: {frame_type}')


def _recv_exact(sock: socket.socket, length: int) -> bytes:
    data = b''
    while len(data) < length:
        chunk = sock.recv(length - len(data))
        if not chunk:
            raise AssertionError('socket closed before expected bytes')
        data += chunk
    return data


def _wait_for(predicate) -> None:
    deadline = time.time() + 2
    while time.time() < deadline:
        if predicate():
            return
        time.sleep(0.01)
    raise AssertionError('condition was not reached')
