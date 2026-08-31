from __future__ import annotations

import base64
from collections import OrderedDict
from concurrent.futures import Future, TimeoutError as FutureTimeoutError
from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import ipaddress
import json
import mimetypes
import os
from pathlib import Path
from queue import Empty, Full, Queue
import re
import select
import shutil
import socket
import sqlite3
import stat
import threading
import time
from typing import Callable, Mapping
from uuid import uuid4
from urllib.parse import parse_qs, unquote, urlparse
from urllib.request import url2pathname

from agents.config_loader import load_project_config
from ccbd.api_models import DeliveryScope, MessageEnvelope
from platforms.windows.herdr.ccbd_surface_projection import herdr_surface_projection_passes_gate
from ccbd.socket_client import CcbdClientError
from cli.services.config_ui import config_ui_provider_capabilities
from project.identity import normalize_work_dir
from provider_backends.pi.session import validate_native_session_binding
from provider_core.pathing import session_filename_for_agent
from provider_control import (
    ProviderQuotaService,
    ProviderSettingsError,
    ProviderSettingsStore,
    project_config_revision,
    provider_restart_pending_agents,
    resolve_provider_session_path,
)
from provider_pane_status.control_messages import (
    clean_provider_local_control_message,
)
from .activity_watch import (
    MobileAgentActivityProbe,
    probe_mobile_agent_activity,
)
from .notifications import (
    MobileInvalidationSnapshot,
    MobileNotificationSnapshot,
    MobileNotificationStore,
    encode_sse_event,
)
from .pairing import MobileGatewayPairingError, MobileGatewayPairingStore
from .push import MobilePushDispatcher, PushSender
from .project_activity import MobileGatewayProjectActivityStore
from .project_registry import MobileGatewayProject, MobileGatewayProjectRegistry
from .terminal import (
    HostTerminalManager,
    TerminalAttachTarget,
    TerminalGeometry,
    TerminalHistoryTarget,
    PaneMessageTarget,
    create_tmux_terminal_history,
    create_tmux_terminal_session,
    send_tmux_pane_message,
)
from .websocket import WebSocketConnection, WebSocketProtocolError, accept_websocket, is_websocket_upgrade

_DEFAULT_HOST = '127.0.0.1'
_DEFAULT_PORT = 8787
_SCHEMA_VERSION = 1
_BASE_CAPABILITIES = ('http_json', 'project_view')
_PAIRING_CAPABILITIES = (
    'pairing',
    'device_tokens',
    'lifecycle',
    'focus',
    'terminal_open',
    'websocket_terminal',
    'terminal_history',
    'file_upload',
    'file_download',
    'notifications',
    'invalidation_stream',
    'event_cursor_resume',
    'device_presence',
    'push_notifications',
    'provider_control',
    'host_terminal',
)
_REDACTED_NAMESPACE_KEYS = ('socket_path', 'session_name')
_DEFAULT_ROUTE_PROVIDER = 'lan'
_PROJECT_HEALTH_CACHE_TTL_SECONDS = 5.0
_PROJECT_HEALTH_CACHE_MAX_STALE_SECONDS = 30.0
_PROJECT_HEALTH_CACHE_MAX_ENTRIES = 256
_PROJECT_HEALTH_REFRESH_WORKERS = 4
_PROJECT_HEALTH_REFRESH_QUEUE_MAX = 256
_PROJECT_HEALTH_REFRESH_BUDGET = 256
_PROJECT_HEALTH_FAILURE_BACKOFF_SECONDS = 2.0
_PROJECT_HEALTH_FAILURE_BACKOFF_MAX_SECONDS = 30.0
_DEFAULT_PAIRING_SCOPES = (
    'view',
    'content',
    'focus',
    'ask',
    'message_submit',
    'file_upload',
    'file_download',
    'notify',
    'terminal_input',
    'host_terminal',
    'lifecycle',
    'provider_settings',
)
_PROVIDER_MUTATION_CACHE_MAX_ENTRIES = 256
_MAX_MOBILE_UPLOAD_FILE_BYTES = 25 * 1024 * 1024
_MAX_MOBILE_DOWNLOAD_FILE_BYTES = 128 * 1024 * 1024
_NOTIFICATION_STREAM_POLL_SECONDS = 1.0
_INVALIDATION_WATCH_MIN_INTERVAL_SECONDS = 0.75
_INVALIDATION_WATCH_TARGET_LIMIT = 32
_INVALIDATION_WATCH_TARGET_TTL_SECONDS = 45.0
_CODEX_NATIVE_TAIL_FILE_BYTES = 64 * 1024
_CODEX_NATIVE_TAIL_LINE_LIMIT = 120
_CODEX_NATIVE_TAIL_THREAD_LIMIT = 2
_CODEX_NATIVE_TAIL_CHUNK_LIMIT = 48
_CODEX_NATIVE_TAIL_READ_BLOCK_BYTES = 256 * 1024
_CODEX_NATIVE_CURSOR_PREFIX = 'codex-before:'
_PROJECT_ACTIVITY_REFRESH_LIMIT = 3
_PROJECT_ACTIVITY_REFRESH_TTL_SECONDS = 10
_PROJECT_ACTIVITY_REFRESH_BUDGET_SECONDS = 0.75
_PROJECT_ACTIVITY_REFRESH_PER_PROJECT_SECONDS = 0.25


def _socket_peer_is_closed(connection: socket.socket) -> bool:
    """Return true when a streaming HTTP peer has closed its read side."""
    try:
        readable, _, _ = select.select((connection,), (), (), 0)
        if not readable:
            return False
        return connection.recv(1, socket.MSG_PEEK) == b''
    except (OSError, ValueError):
        return True
_CONVERSATION_PAGE_CACHE_MAX_ENTRIES = 64
_CONVERSATION_PAGE_CACHE_MAX_BYTES = 8 * 1024 * 1024
_MOBILE_PROJECT_UPLOAD_DIR = ('.ccb', 'mobile', 'uploads')


@dataclass(frozen=True)
class ListenAddress:
    host: str = _DEFAULT_HOST
    port: int = _DEFAULT_PORT

    @property
    def text(self) -> str:
        return f'{self.host}:{self.port}'


@dataclass(frozen=True)
class _ConversationItemsResult:
    items: list[dict[str, object]]
    already_paged: bool = False
    next_cursor: str | None = None


@dataclass(frozen=True)
class _ConversationPageCacheKey:
    project_id: str
    agent: str
    namespace_epoch: int
    limit: int
    cursor: str | None


@dataclass(frozen=True)
class _ConversationPageCacheEntry:
    fingerprint: tuple[tuple[str, int, int], ...]
    page: dict[str, object]
    byte_size: int


@dataclass(frozen=True)
class _InvalidationWatchTarget:
    project_id: str
    project_short_name: str
    agent: str
    namespace_epoch: int | None
    provider: str | None
    expires_monotonic: float


@dataclass(frozen=True)
class _ProjectHealthCacheEntry:
    payload: dict[str, object]
    checked_at: str
    checked_monotonic: float
    next_refresh_monotonic: float
    failure_count: int


class _BoundedDaemonExecutor:
    """Small daemon executor used by the gateway's non-request refresh work."""

    def __init__(self, *, workers: int, max_pending: int) -> None:
        self._queue: Queue[tuple[Future[object], Callable[[], object]] | None] = Queue(
            maxsize=max(1, int(max_pending))
        )
        self._closed = threading.Event()
        self._threads = [
            threading.Thread(
                target=self._run,
                name=f'ccb-mobile-refresh-{index}',
                daemon=True,
            )
            for index in range(max(1, int(workers)))
        ]
        for thread in self._threads:
            thread.start()

    def submit(self, task: Callable[[], object]) -> Future[object] | None:
        if self._closed.is_set():
            return None
        future: Future[object] = Future()
        try:
            self._queue.put_nowait((future, task))
        except Full:
            return None
        return future

    def close(self) -> None:
        self._closed.set()
        for _thread in self._threads:
            try:
                self._queue.put_nowait(None)
            except Full:
                break

    def _run(self) -> None:
        while not self._closed.is_set() or not self._queue.empty():
            try:
                item = self._queue.get(timeout=0.1)
            except Empty:
                continue
            if item is None:
                return
            future, task = item
            if not future.set_running_or_notify_cancel():
                continue
            try:
                future.set_result(task())
            except BaseException as exc:
                future.set_exception(exc)


class _ProjectHealthCache:
    """Per-project ping cache with bounded stale-while-revalidate scheduling."""

    def __init__(
        self,
        *,
        clock: Callable[[], str],
        monotonic_clock: Callable[[], float],
        submit: Callable[[Callable[[], object]], Future[object] | None],
        ping: Callable[[MobileGatewayProject], dict[str, object]],
    ) -> None:
        self._clock = clock
        self._monotonic_clock = monotonic_clock
        self._submit = submit
        self._ping = ping
        self._entries: OrderedDict[str, _ProjectHealthCacheEntry] = OrderedDict()
        self._registry_ids: tuple[str, ...] = ()
        self._active_ids: set[str] = set()
        self._refreshing: set[str] = set()
        self._observed_ids: set[str] = set()
        self._available_ids: set[str] = set()
        self._observed_active_count = 0
        self._overview_updated_monotonic: float | None = None
        self._overview_available_project_ids: tuple[str, ...] = ()
        self._lock = threading.Lock()
        self._closed = False

    def prime(self, projects: tuple[MobileGatewayProject, ...]) -> None:
        """Record registry identity without pinging it, so host health stays O(1)."""
        with self._lock:
            self._reconcile_locked(projects)

    def close(self) -> None:
        with self._lock:
            self._closed = True
            self._refreshing.clear()

    def health_by_project(
        self,
        projects: tuple[MobileGatewayProject, ...],
    ) -> dict[str, dict[str, object]]:
        now = self._monotonic_clock()
        candidates: list[MobileGatewayProject] = []
        results: dict[str, dict[str, object]] = {}
        with self._lock:
            self._reconcile_locked(projects)
            for project in projects:
                entry = self._entries.get(project.project_id)
                if (
                    len(candidates) < _PROJECT_HEALTH_REFRESH_BUDGET
                    and self._needs_refresh_locked(project.project_id, entry, now=now)
                ):
                    self._refreshing.add(project.project_id)
                    candidates.append(project)
                results[project.project_id] = self._snapshot_locked(
                    project.project_id,
                    entry,
                    now=now,
                )
        for project in candidates:
            future = self._submit(lambda project=project: self._refresh(project))
            if future is None:
                with self._lock:
                    self._refreshing.discard(project.project_id)
        return results

    def overview(self) -> dict[str, object]:
        """Return the last observed project health without registry work or pings."""
        now = self._monotonic_clock()
        with self._lock:
            project_count = len(self._registry_ids)
            complete = self._observed_active_count == project_count
            updated_at = self._overview_updated_monotonic
            available_count = len(self._available_ids)
            available_ids = self._overview_available_project_ids
        if not complete or updated_at is None:
            freshness = 'unknown'
        elif now - updated_at <= _PROJECT_HEALTH_CACHE_TTL_SECONDS:
            freshness = 'cached'
        elif now - updated_at <= _PROJECT_HEALTH_CACHE_MAX_STALE_SECONDS:
            freshness = 'stale'
        else:
            freshness = 'unknown'
        return {
            'project_count': project_count,
            'available_project_count': available_count if freshness != 'unknown' else None,
            'available_project_ids': list(available_ids) if freshness != 'unknown' else [],
            'health_freshness': freshness,
        }

    def _reconcile_locked(self, projects: tuple[MobileGatewayProject, ...]) -> None:
        project_ids = tuple(project.project_id for project in projects)
        active_ids = set(project_ids)
        if active_ids != self._active_ids:
            self._overview_updated_monotonic = None
        self._registry_ids = project_ids
        self._active_ids = active_ids
        for project_id in tuple(self._entries):
            if project_id not in active_ids:
                self._entries.pop(project_id, None)
        self._observed_ids.intersection_update(active_ids)
        self._available_ids.intersection_update(active_ids)
        self._observed_active_count = len(self._observed_ids)
        self._overview_available_project_ids = tuple(sorted(self._available_ids))[:10]
        self._refreshing.intersection_update(active_ids)

    def _snapshot_locked(
        self,
        project_id: str,
        entry: _ProjectHealthCacheEntry | None,
        *,
        now: float,
    ) -> dict[str, object]:
        if entry is None:
            return {
                'health': 'unknown',
                'mount_state': 'unknown',
                'health_freshness': 'unknown',
                'health_refreshing': project_id in self._refreshing,
            }
        age = max(0.0, now - entry.checked_monotonic)
        if age <= _PROJECT_HEALTH_CACHE_TTL_SECONDS:
            freshness = 'fresh'
        elif age <= _PROJECT_HEALTH_CACHE_MAX_STALE_SECONDS:
            freshness = 'stale'
        else:
            # Keep the last observed state visible while it is revalidated.
            # Returning unknown here makes every idle cache interval briefly
            # collapse the mobile project list to zero entries.
            freshness = 'expired'
        return {
            **entry.payload,
            'health_freshness': freshness,
            'health_checked_at': entry.checked_at,
            'health_refreshing': project_id in self._refreshing,
        }

    def _needs_refresh_locked(
        self,
        project_id: str,
        entry: _ProjectHealthCacheEntry | None,
        *,
        now: float,
    ) -> bool:
        return (
            not self._closed
            and project_id not in self._refreshing
            and (entry is None or now >= entry.next_refresh_monotonic)
        )

    def _refresh(self, project: MobileGatewayProject) -> None:
        now = self._monotonic_clock()
        try:
            payload = dict(self._ping(project) or {})
        except Exception:
            payload = {
                'health': 'unreachable',
                'mount_state': 'unavailable',
                'error': 'project unavailable',
            }
        failure = _project_health_refresh_failed(payload)
        with self._lock:
            prior = self._entries.get(project.project_id)
            failure_count = (prior.failure_count if prior is not None else 0) + 1 if failure else 0
            if failure:
                delay = min(
                    _PROJECT_HEALTH_FAILURE_BACKOFF_MAX_SECONDS,
                    _PROJECT_HEALTH_FAILURE_BACKOFF_SECONDS * (2 ** max(0, failure_count - 1)),
                )
            else:
                delay = _PROJECT_HEALTH_CACHE_TTL_SECONDS
            if not self._closed and project.project_id in self._active_ids:
                if project.project_id not in self._observed_ids:
                    self._observed_ids.add(project.project_id)
                    self._observed_active_count += 1
                if _project_available_for_mobile_list(payload):
                    self._available_ids.add(project.project_id)
                else:
                    self._available_ids.discard(project.project_id)
                self._entries[project.project_id] = _ProjectHealthCacheEntry(
                    payload=payload,
                    checked_at=self._clock(),
                    checked_monotonic=now,
                    next_refresh_monotonic=now + delay,
                    failure_count=failure_count,
                )
                self._entries.move_to_end(project.project_id)
                while len(self._entries) > _PROJECT_HEALTH_CACHE_MAX_ENTRIES:
                    evicted_project_id, _entry = self._entries.popitem(last=False)
                    if evicted_project_id in self._observed_ids:
                        self._observed_ids.discard(evicted_project_id)
                        self._observed_active_count -= 1
                    self._available_ids.discard(evicted_project_id)
                if self._observed_active_count == len(self._registry_ids):
                    self._overview_updated_monotonic = now
                    self._overview_available_project_ids = tuple(sorted(self._available_ids))[:10]
            self._refreshing.discard(project.project_id)


class MobileGatewayError(RuntimeError):
    def __init__(self, message: str, *, status_code: int = 400) -> None:
        super().__init__(message)
        self.status_code = int(status_code)


class _TerminalBlockedError(MobileGatewayError):
    def __init__(self, payload: dict[str, object]) -> None:
        super().__init__(str(payload.get('message') or 'terminal target is blocked'), status_code=409)
        self.terminal_blocked = payload


def _default_terminal_session_factory(target: TerminalAttachTarget):
    if target.backend_impl == 'herdr':
        raise _TerminalBlockedError(_herdr_terminal_target_blocked_payload('herdr-terminal-adapter-unavailable'))
    return create_tmux_terminal_session(target)


def _default_terminal_history_factory(target: TerminalHistoryTarget) -> dict[str, object]:
    if target.backend_impl == 'herdr':
        raise _TerminalBlockedError(_herdr_terminal_target_blocked_payload('herdr-terminal-adapter-unavailable'))
    return create_tmux_terminal_history(target)


def _default_terminal_message_sender(target: PaneMessageTarget, text: str) -> dict[str, object]:
    if target.backend_impl == 'herdr':
        raise _TerminalBlockedError(_herdr_terminal_target_blocked_payload('herdr-terminal-adapter-unavailable'))
    return send_tmux_pane_message(target, text)


class MobileGatewayService:
    def __init__(
        self,
        *,
        project_id: str,
        project_root: Path,
        ccbd_client_factory: Callable[[], object],
        mobile_dir: Path | None = None,
        pairing_store: MobileGatewayPairingStore | None = None,
        project_registry: MobileGatewayProjectRegistry | None = None,
        project_registry_provider: Callable[[], MobileGatewayProjectRegistry] | None = None,
        mode: str = 'loopback_current_project',
        clock: Callable[[], str] | None = None,
        monotonic_clock: Callable[[], float] | None = None,
        background_executor: object | None = None,
        terminal_session_factory: Callable[[TerminalAttachTarget], object] | None = None,
        terminal_history_factory: Callable[[TerminalHistoryTarget], dict[str, object]] | None = None,
        terminal_message_sender: Callable[[PaneMessageTarget, str], dict[str, object]] | None = None,
        host_terminal_manager: HostTerminalManager | None = None,
        push_sender: PushSender | None = None,
        push_sender_timeout_seconds: float = 2.0,
        push_sender_max_workers: int = 4,
        push_diagnostic: Mapping[str, object] | None = None,
        agent_activity_probe: Callable[..., MobileAgentActivityProbe] | None = None,
        provider_quota_service: ProviderQuotaService | None = None,
    ) -> None:
        self._project_id = str(project_id)
        self._project_root = Path(project_root)
        self._ccbd_client_factory = ccbd_client_factory
        self._project_registry = project_registry or MobileGatewayProjectRegistry.current_project(
            project_id=self._project_id,
            project_root=self._project_root,
            ccbd_client_factory=self._ccbd_client_factory,
        )
        self._project_registry_provider = project_registry_provider
        self._mode = str(mode or 'loopback_current_project').strip() or 'loopback_current_project'
        self._clock = clock or _utc_now
        self._monotonic_clock = monotonic_clock or time.monotonic
        self._background_executor = background_executor
        self._owns_background_executor = False
        self._closed = False
        self._terminal_sessions_lock = threading.Lock()
        self._terminal_sessions: dict[int, object] = {}
        self._terminal_session_factory = terminal_session_factory or _default_terminal_session_factory
        self._terminal_history_factory = terminal_history_factory or _default_terminal_history_factory
        self._terminal_message_sender = terminal_message_sender or _default_terminal_message_sender
        self._agent_activity_probe = (
            agent_activity_probe or probe_mobile_agent_activity
        )
        self._push_diagnostic = dict(push_diagnostic or {})
        self._mobile_dir = Path(mobile_dir) if mobile_dir is not None else None
        self._host_terminal_manager = host_terminal_manager
        if self._host_terminal_manager is None and self._mobile_dir is not None:
            self._host_terminal_manager = HostTerminalManager(self._mobile_dir)
        self._pairing_store = pairing_store
        if self._pairing_store is None and mobile_dir is not None:
            self._pairing_store = MobileGatewayPairingStore(self._mobile_dir)
        self._push_dispatcher = (
            MobilePushDispatcher(
                pairing_store=self._pairing_store,
                host_id=self._project_id,
                sender=push_sender,
                timeout_seconds=push_sender_timeout_seconds,
                max_workers=push_sender_max_workers,
            )
            if self._pairing_store is not None else None
        )
        self._notification_store = MobileNotificationStore(self._mobile_dir) if mobile_dir is not None else None
        self._project_activity_store = (
            MobileGatewayProjectActivityStore(self._mobile_dir) if mobile_dir is not None else None
        )
        self._conversation_page_cache: OrderedDict[
            _ConversationPageCacheKey,
            _ConversationPageCacheEntry,
        ] = OrderedDict()
        self._conversation_page_cache_bytes = 0
        self._conversation_page_cache_lock = threading.Lock()
        self._provider_settings_store = ProviderSettingsStore()
        self._provider_quota_service = provider_quota_service or ProviderQuotaService()
        self._provider_mutation_lock = threading.Lock()
        self._provider_mutation_results: OrderedDict[
            tuple[str, str], tuple[str, dict[str, object]]
        ] = OrderedDict()
        self._project_activity_refreshing: set[str] = set()
        self._project_activity_refresh_lock = threading.Lock()
        # One bounded watcher/cache serves every SSE client.  It tracks only
        # selected app subscriptions and never turns an SSE keepalive into a
        # registry/project-view sweep.
        self._invalidation_watch_lock = threading.Lock()
        self._invalidation_watch_refreshing = False
        self._invalidation_watch_last_refresh = float('-inf')
        self._invalidation_watch_targets: OrderedDict[
            tuple[str, str, int | None], _InvalidationWatchTarget
        ] = OrderedDict()
        self._invalidation_audit = {
            'watch_refreshes': 0,
            'watch_targets_checked': 0,
            'watch_activity_probes': 0,
            'watch_activity_probe_failures': 0,
            'watch_project_view_calls': 0,
            'watch_conversation_requests': 0,
            'ccbd_project_view_requests': 0,
            'mobile_conversation_requests': 0,
        }
        self._project_health_cache: _ProjectHealthCache | None = None
        if self._mode == 'loopback_server_registry':
            self._project_health_cache = _ProjectHealthCache(
                clock=self._clock,
                monotonic_clock=self._monotonic_clock,
                submit=self._submit_background,
                ping=self._project_list_health,
            )
            self._project_health_cache.prime(self._project_registry.projects())

    @property
    def project_id(self) -> str:
        return self._project_id

    def health_payload(self) -> dict[str, object]:
        if self._mode == 'loopback_server_registry':
            return self._server_registry_health_payload()
        try:
            ccbd = self._client().ping('ccbd')
        except Exception as exc:
            return {
                'schema_version': _SCHEMA_VERSION,
                'status': 'degraded',
                'server_time': self._clock(),
                'mode': self._mode,
                'project_id': self._project_id,
                'capabilities': self._capabilities(),
                'ccbd': {
                    'reachable': False,
                    'error': _error_text(exc),
                },
            }
        return {
            'schema_version': _SCHEMA_VERSION,
            'status': 'ok',
            'server_time': self._clock(),
            'mode': self._mode,
            'project_id': self._project_id,
            'capabilities': self._capabilities(),
            'ccbd': _ccbd_health_summary(ccbd),
        }

    def _server_registry_health_payload(self) -> dict[str, object]:
        cache = self._project_health_cache
        overview = cache.overview() if cache is not None else {
            'project_count': 0,
            'available_project_count': None,
            'available_project_ids': [],
            'health_freshness': 'unknown',
        }
        return {
            'schema_version': _SCHEMA_VERSION,
            'status': 'ok',
            'server_time': self._clock(),
            'mode': self._mode,
            'project_id': self._project_id,
            'capabilities': self._capabilities(),
            'ccbd': {
                'reachable': None,
                **overview,
            },
        }

    def projects_payload(self) -> dict[str, object]:
        projects: list[dict[str, object]] = []
        registry_projects = self._registry_projects(refresh=True)
        if self._project_health_cache is not None:
            health_by_project = self._project_health_cache.health_by_project(registry_projects)
        else:
            health_by_project = self._project_list_health_by_project(registry_projects)
        unknown_project_count = sum(
            1
            for health in health_by_project.values()
            if str(health.get('health_freshness') or 'unknown') == 'unknown'
        )
        capabilities = self._capabilities()
        activity_refreshes_remaining = _PROJECT_ACTIVITY_REFRESH_LIMIT
        activity_deadline = time.monotonic() + _PROJECT_ACTIVITY_REFRESH_BUDGET_SECONDS
        for project in registry_projects:
            ccbd = health_by_project[project.project_id]
            if not _project_available_for_mobile_list(ccbd):
                continue
            item = {
                'id': project.project_id,
                'display_name': project.public_display_name,
                'root': project.project_root.as_posix(),
                'health': str(ccbd.get('health') or 'unknown'),
                'mount_state': str(ccbd.get('mount_state') or ''),
                'health_freshness': str(ccbd.get('health_freshness') or 'unknown'),
                'capabilities': capabilities,
            }
            if ccbd.get('health_checked_at'):
                item['health_checked_at'] = str(ccbd.get('health_checked_at') or '')
            if ccbd.get('health_refreshing'):
                item['health_refreshing'] = True
            allow_activity_refresh = (
                activity_refreshes_remaining > 0
                and time.monotonic() < activity_deadline
            )
            activity_summary, attempted_activity_refresh = self._project_activity_summary(
                project,
                allow_refresh=allow_activity_refresh,
                deadline=activity_deadline,
            )
            if attempted_activity_refresh:
                activity_refreshes_remaining -= 1
            item.update(activity_summary)
            if ccbd.get('error'):
                item['error'] = str(ccbd.get('error') or '')
            projects.append(item)
        projects = _sort_project_payloads_by_recent_activity(projects)
        return {
            'schema_version': _SCHEMA_VERSION,
            'projects': projects,
            'registry_project_count': len(registry_projects),
            'health_warming': unknown_project_count > 0,
            'health_unknown_project_count': unknown_project_count,
        }

    def project_view_payload(self, project_id: str) -> dict[str, object]:
        project = self._require_project(project_id)
        payload = self._request_project_view(project)
        self._observe_project_view_for_notifications(project, payload)
        self._record_project_opened(project.project_id)
        return _redact_project_view_payload(payload)

    def agent_provider_control_payload(
        self,
        project_id: str,
        *,
        agent: str,
        headers: Mapping[str, object] | None,
    ) -> dict[str, object]:
        self._authenticate(headers, required_scopes=('view',))
        project = self._require_project(project_id)
        view_payload = _redact_project_view_payload(self._request_project_view(project))
        view = view_payload.get('view') if isinstance(view_payload.get('view'), dict) else view_payload
        agent_record = next(
            (
                dict(item)
                for item in tuple(view.get('agents') or ())
                if isinstance(item, dict) and str(item.get('name') or '').strip() == str(agent).strip()
            ),
            None,
        )
        if agent_record is None:
            raise MobileGatewayError('unknown agent', status_code=404)
        provider = str(agent_record.get('provider') or '').strip().lower()
        catalog = config_ui_provider_capabilities(
            project_root=project.project_root,
            cli_models=None if provider in {'opencode', 'mimo'} else {},
            roles=(),
        )
        provider_entry = next(
            (
                dict(item)
                for item in tuple(catalog.get('providers') or ())
                if isinstance(item, dict) and str(item.get('id') or '').strip().lower() == provider
            ),
            {
                'id': provider,
                'model_shortcut': False,
                'model_source': 'none',
                'models': [],
                'custom_model': False,
                'static_thinking': False,
            },
        )
        config_revision = project_config_revision(project.project_root)
        try:
            configured_spec = load_project_config(project.project_root).config.agents.get(str(agent).strip())
        except Exception:
            configured_spec = None
        provider_control = dict(agent_record.get('provider_control') or {})
        restart_pending = (
            str(agent).strip().lower()
            in provider_restart_pending_agents(project.project_root)
        )
        provider_control['restart_pending'] = restart_pending
        if configured_spec is not None:
            configured_model = str(getattr(configured_spec, 'model', '') or '').strip() or None
            configured_thinking = str(getattr(configured_spec, 'thinking', '') or '').strip() or None
            provider_control['configured_model'] = configured_model
            provider_control['configured_thinking'] = configured_thinking
            active_model = str(provider_control.get('active_model') or '').strip() or None
            active_thinking = str(provider_control.get('active_thinking') or '').strip() or None
            provider_control['pending_model'] = (
                configured_model
                if configured_model is not None
                and (
                    restart_pending
                    or (active_model is not None and configured_model != active_model)
                )
                else None
            )
            provider_control['pending_thinking'] = (
                configured_thinking
                if configured_thinking is not None
                and (
                    restart_pending
                    or (
                        active_thinking is not None
                        and configured_thinking != active_thinking
                    )
                )
                else None
            )
        return {
            'schema_version': 1,
            'status': 'ok',
            'project_id': project.project_id,
            'agent': str(agent_record.get('name') or agent),
            'namespace_epoch': _optional_int(
                (view.get('namespace') or {}).get('epoch')
                if isinstance(view.get('namespace'), dict)
                else None
            ),
            'provider_control': provider_control,
            'provider_catalog': provider_entry,
            'config_revision': config_revision,
        }

    def agent_provider_quota_payload(
        self,
        project_id: str,
        *,
        agent: str,
        headers: Mapping[str, object] | None,
    ) -> dict[str, object]:
        """Load optional account quota without delaying provider controls."""
        self._authenticate(headers, required_scopes=('view',))
        project = self._require_project(project_id)
        view_payload = _redact_project_view_payload(self._request_project_view(project))
        view = view_payload.get('view') if isinstance(view_payload.get('view'), dict) else view_payload
        agent_record = next(
            (
                dict(item)
                for item in tuple(view.get('agents') or ())
                if isinstance(item, dict)
                and str(item.get('name') or '').strip() == str(agent).strip()
            ),
            None,
        )
        if agent_record is None:
            raise MobileGatewayError('unknown agent', status_code=404)
        provider = str(agent_record.get('provider') or '').strip().lower()
        account_usage = self._provider_quota_service.account_quota(
            project_root=project.project_root,
            agent=str(agent_record.get('name') or agent),
            provider=provider,
        )
        return {
            'schema_version': 1,
            'status': 'ok',
            'project_id': project.project_id,
            'agent': str(agent_record.get('name') or agent),
            'account_usage': account_usage.to_record(),
        }

    def update_agent_provider_settings(
        self,
        project_id: str,
        *,
        agent: str,
        payload: Mapping[str, object],
        headers: Mapping[str, object] | None,
    ) -> dict[str, object]:
        auth = self._authenticate(headers, required_scopes=('provider_settings',))
        project = self._require_project(project_id)
        idempotency_key = str(payload.get('idempotency_key') or '').strip()
        if re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9._:-]{7,127}', idempotency_key) is None:
            raise MobileGatewayError('valid idempotency_key is required', status_code=400)
        fingerprint = hashlib.sha256(
            json.dumps(
                {
                    'project_id': project.project_id,
                    'agent': str(agent).strip(),
                    'payload': dict(payload),
                },
                ensure_ascii=True,
                sort_keys=True,
                separators=(',', ':'),
            ).encode('utf-8')
        ).hexdigest()
        cache_key = (auth.device_id, idempotency_key)
        with self._provider_mutation_lock:
            cached = self._provider_mutation_results.get(cache_key)
            if cached is not None:
                if cached[0] != fingerprint:
                    raise MobileGatewayError(
                        'idempotency_key was already used for another request',
                        status_code=409,
                    )
                self._provider_mutation_results.move_to_end(cache_key)
                return dict(cached[1])

            view_payload = _redact_project_view_payload(self._request_project_view(project))
            view = view_payload.get('view') if isinstance(view_payload.get('view'), dict) else view_payload
            namespace = view.get('namespace') if isinstance(view.get('namespace'), dict) else {}
            current_epoch = _optional_int(namespace.get('epoch'))
            expected_epoch = _optional_int(payload.get('expected_namespace_epoch'))
            if current_epoch is None or expected_epoch != current_epoch:
                raise MobileGatewayError('stale project namespace; refresh before saving', status_code=409)
            agent_record = next(
                (
                    dict(item)
                    for item in tuple(view.get('agents') or ())
                    if isinstance(item, dict)
                    and str(item.get('name') or '').strip() == str(agent).strip()
                ),
                None,
            )
            if agent_record is None:
                raise MobileGatewayError('unknown agent', status_code=404)
            provider = str(agent_record.get('provider') or '').strip().lower()
            expected_provider = str(payload.get('expected_provider') or '').strip().lower()
            if not expected_provider or expected_provider != provider:
                raise MobileGatewayError('stale provider identity; refresh before saving', status_code=409)
            provider_control = (
                dict(agent_record.get('provider_control') or {})
                if isinstance(agent_record.get('provider_control'), dict)
                else {}
            )
            if 'expected_runtime_revision' not in payload:
                raise MobileGatewayError('expected_runtime_revision is required', status_code=400)
            expected_runtime_revision = _optional_text(payload.get('expected_runtime_revision'))
            current_runtime_revision = _optional_text(provider_control.get('runtime_revision'))
            if expected_runtime_revision != current_runtime_revision:
                raise MobileGatewayError('stale provider runtime; refresh before saving', status_code=409)
            catalog = config_ui_provider_capabilities(
                project_root=project.project_root,
                cli_models=None if provider in {'opencode', 'mimo'} else {},
                roles=(),
            )
            provider_entry = next(
                (
                    dict(item)
                    for item in tuple(catalog.get('providers') or ())
                    if isinstance(item, dict)
                    and str(item.get('id') or '').strip().lower() == provider
                ),
                None,
            )
            if not provider_entry or not provider_entry.get('model_shortcut'):
                raise MobileGatewayError('provider model selection is unsupported', status_code=409)
            models = {
                str(item.get('id') or '').strip(): dict(item)
                for item in tuple(provider_entry.get('models') or ())
                if isinstance(item, dict) and str(item.get('id') or '').strip()
            }
            model = str(payload.get('model') or '').strip()
            selected = models.get(model)
            if selected is None:
                raise MobileGatewayError('model is not selectable for this provider', status_code=422)
            allowed_thinking = {
                str(item).strip().lower()
                for item in tuple(selected.get('reasoning_levels') or ())
                if str(item).strip()
            }
            try:
                result = self._provider_settings_store.apply(
                    project_root=project.project_root,
                    agent=str(agent).strip(),
                    model=model,
                    thinking=_optional_text(payload.get('thinking')),
                    expected_revision=str(payload.get('expected_revision') or '').strip(),
                    allowed_models=set(models),
                    allowed_thinking=allowed_thinking,
                )
            except ProviderSettingsError as exc:
                raise MobileGatewayError(str(exc), status_code=exc.status_code) from exc
            record = result.to_record()
            record.update(
                {
                    'idempotency_key': idempotency_key,
                    'namespace_epoch': current_epoch,
                    'runtime_revision': current_runtime_revision,
                }
            )
            self._provider_mutation_results[cache_key] = (fingerprint, dict(record))
            self._provider_mutation_results.move_to_end(cache_key)
            while len(self._provider_mutation_results) > _PROVIDER_MUTATION_CACHE_MAX_ENTRIES:
                self._provider_mutation_results.popitem(last=False)
            self._record_project_activity(project.project_id)
            return record

    def invalidation_audit_payload(self) -> dict[str, int]:
        """Test/diagnostic-only counters for proving idle stream cost."""
        with self._invalidation_watch_lock:
            return {key: int(value) for key, value in self._invalidation_audit.items()}

    def push_audit_payload(self) -> dict[str, object]:
        dispatcher = self._push_dispatcher
        audit = dispatcher.audit_payload() if dispatcher is not None else {'enabled': False}
        if self._push_diagnostic:
            audit['sender'] = dict(self._push_diagnostic)
        return audit

    def create_pairing_payload(
        self,
        *,
        gateway_url: str,
        route_provider: str = _DEFAULT_ROUTE_PROVIDER,
        scopes: tuple[str, ...] = _DEFAULT_PAIRING_SCOPES,
        expires_seconds: int | None = 10 * 60,
        reusable_claims: bool = False,
    ) -> dict[str, object]:
        store = self._require_pairing_store()
        store.write_gateway_state(
            project_id=self._project_id,
            gateway_url=gateway_url,
            route_provider=route_provider,
            capabilities=self._capabilities(),
        )
        return store.create_pairing_payload(
            project_id=self._project_id,
            gateway_url=gateway_url,
            route_provider=route_provider,
            scopes=scopes,
            expires_seconds=expires_seconds,
            reusable_claims=reusable_claims,
        )

    def ensure_reusable_pairing_payload(
        self,
        *,
        gateway_url: str,
        route_provider: str,
        scopes: tuple[str, ...] = _DEFAULT_PAIRING_SCOPES,
    ) -> dict[str, object]:
        store = self._require_pairing_store()
        store.write_gateway_state(
            project_id=self._project_id,
            gateway_url=gateway_url,
            route_provider=route_provider,
            capabilities=self._capabilities(),
        )
        return store.ensure_reusable_pairing_payload(
            project_id=self._project_id,
            gateway_url=gateway_url,
            route_provider=route_provider,
            scopes=scopes,
        )

    def rotate_reusable_pairing_payload(
        self,
        *,
        gateway_url: str,
        route_provider: str,
        scopes: tuple[str, ...] = _DEFAULT_PAIRING_SCOPES,
    ) -> dict[str, object]:
        return self._require_pairing_store().rotate_reusable_pairing_payload(
            project_id=self._project_id,
            gateway_url=gateway_url,
            route_provider=route_provider,
            scopes=scopes,
        )

    def dispatch_get(self, path: str, headers: Mapping[str, object] | None = None) -> tuple[int, dict[str, object]]:
        parsed = urlparse(path)
        route = parsed.path.rstrip('/') or '/'
        if route == '/v1/health':
            status = 200
            payload = self.health_payload()
            if payload.get('status') == 'degraded':
                status = 503
            return status, payload
        if route == '/v1/projects':
            return 200, self.projects_payload()
        if route == '/v1/mobile/notifications':
            return 200, {
                'schema_version': _SCHEMA_VERSION,
                'status': 'ok',
                'events': self.notification_events_since(path, headers),
            }
        if route == '/v1/mobile/notifications/audit':
            self._authenticate(headers, required_scopes=('notify',))
            return 200, {
                'schema_version': _SCHEMA_VERSION,
                'status': 'ok',
                'audit': self.invalidation_audit_payload(),
            }
        if route == '/v1/mobile/push/audit':
            self._authenticate(headers, required_scopes=('notify',))
            return 200, {
                'schema_version': _SCHEMA_VERSION,
                'status': 'ok',
                'audit': self.push_audit_payload(),
            }
        prefix = '/v1/projects/'
        suffix = '/view'
        if route.startswith(prefix) and route.endswith(suffix):
            project_id = unquote(route[len(prefix):-len(suffix)].strip('/'))
            self._authenticate(headers, required_scopes=('view',))
            return 200, self.project_view_payload(project_id)
        history_suffix = '/terminal-history'
        if route.startswith(prefix) and route.endswith(history_suffix):
            project_id = unquote(route[len(prefix):-len(history_suffix)].strip('/'))
            history_payload = self.terminal_history_payload(
                project_id,
                query=parse_qs(parsed.query, keep_blank_values=True),
                headers=headers,
            )
            return (409 if history_payload.get('status') == 'blocked' else 200), history_payload
        conversation_route = _parse_project_agent_route(route, suffix='conversation')
        if conversation_route is not None:
            project_id, agent = conversation_route
            return 200, self.agent_conversation_payload(
                project_id,
                agent=agent,
                query=parse_qs(parsed.query, keep_blank_values=True),
                headers=headers,
            )
        provider_control_route = _parse_project_agent_route(route, suffix='provider-control')
        if provider_control_route is not None:
            project_id, agent = provider_control_route
            return 200, self.agent_provider_control_payload(
                project_id,
                agent=agent,
                headers=headers,
            )
        provider_quota_route = _parse_project_agent_route(route, suffix='provider-quota')
        if provider_quota_route is not None:
            project_id, agent = provider_quota_route
            return 200, self.agent_provider_quota_payload(
                project_id,
                agent=agent,
                headers=headers,
            )
        if route == '/v1/devices/me':
            device = self._authenticate(headers, required_scopes=('view',))
            payload = {
                'schema_version': _SCHEMA_VERSION,
                'status': 'ok',
                'device': device.public_payload(),
            }
            presence = self._require_pairing_store().presence_for_device(device.device_id)
            if presence is not None:
                payload['presence'] = presence
            return 200, payload
        raise MobileGatewayError('not found', status_code=404)

    def notification_stream_target_from_path(self, path: str) -> bool:
        parsed = urlparse(path)
        route = parsed.path.rstrip('/') or '/'
        return route == '/v1/mobile/notifications'

    def notification_stream_once_from_path(self, path: str) -> bool:
        parsed = urlparse(path)
        query = parse_qs(parsed.query, keep_blank_values=True)
        once = str(_query_text(query, 'once') or '').strip().lower()
        return once in {'1', 'true', 'yes'}

    def notification_events_since(
        self,
        path: str = '/v1/mobile/notifications',
        headers: Mapping[str, object] | None = None,
        *,
        last_event_id: str | None = None,
    ) -> list[dict[str, object]]:
        if not self.notification_stream_target_from_path(path):
            raise MobileGatewayError('not found', status_code=404)
        parsed = urlparse(path)
        query = parse_qs(parsed.query, keep_blank_values=True)
        self._authenticate(headers, required_scopes=('notify',))
        store = self._require_notification_store()
        self._register_invalidation_watch(query)
        self._refresh_notification_stream_if_due(
            store,
            force=self.notification_stream_once_from_path(path),
        )
        cursor = (
            last_event_id
            if last_event_id is not None
            else _query_text(query, 'last_event_id') or _header_value(headers, 'last-event-id')
        )
        return [event.to_payload() for event in store.events_since(cursor)]

    def _refresh_notification_stream_if_due(
        self,
        store: MobileNotificationStore,
        *,
        force: bool = False,
    ) -> None:
        """Refresh the shared selected-agent provider-evidence watcher.

        This has no ccbd RPC path: it reads only CCB-owned binding/activity
        records, a bounded provider pane tail, and native transcript metadata
        for subscribed targets. Project view/conversation remains an explicit
        REST action, so idle SSE clients do not create hidden project scans.
        """
        now = self._monotonic_clock()
        with self._invalidation_watch_lock:
            if (
                self._invalidation_watch_refreshing
                or (
                    not force
                    and now - self._invalidation_watch_last_refresh
                    < _INVALIDATION_WATCH_MIN_INTERVAL_SECONDS
                )
            ):
                return
            self._invalidation_watch_refreshing = True
        try:
            snapshots = self._selected_invalidation_watch_snapshots()
            if snapshots:
                store.sync_invalidations(snapshots)
        finally:
            with self._invalidation_watch_lock:
                self._invalidation_watch_last_refresh = self._monotonic_clock()
                self._invalidation_watch_refreshing = False

    def terminal_history_payload(
        self,
        project_id: str,
        *,
        query: Mapping[str, object],
        headers: Mapping[str, object] | None = None,
    ) -> dict[str, object]:
        project = self._require_project(project_id)
        self._authenticate(headers, required_scopes=('view',))
        view_payload = self._request_project_view(project)
        blocked = _terminal_operation_blocked_payload(
            view_payload,
            agent=_query_text(query, 'agent'),
            namespace_epoch=_query_int(query, 'namespace_epoch'),
            operation='history',
        )
        if blocked is not None:
            return {
                'schema_version': _SCHEMA_VERSION,
                'status': 'blocked',
                'project_id': project.project_id,
                'terminal_blocked': blocked,
            }
        target = _terminal_history_target(
            project_id=project.project_id,
            view_payload=view_payload,
            agent=_query_text(query, 'agent'),
            namespace_epoch=_query_int(query, 'namespace_epoch'),
            max_lines=_query_int(query, 'max_lines') or 200,
        )
        try:
            history = dict(self._terminal_history_factory(target) or {})
        except _TerminalBlockedError as exc:
            return {
                'schema_version': _SCHEMA_VERSION,
                'status': 'blocked',
                'project_id': project.project_id,
                'terminal_blocked': exc.terminal_blocked,
            }
        except Exception as exc:
            raise MobileGatewayError(_error_text(exc), status_code=503) from exc
        history.setdefault('agent', target.agent)
        history.setdefault('history_scope', 'tmux_scrollback')
        history.setdefault('source_pane_id', target.pane_id)
        history.setdefault('generated_at', self._clock())
        history.setdefault('stale', False)
        history.setdefault('blocks', [])
        return {
            'schema_version': _SCHEMA_VERSION,
            'status': 'ok',
            'project_id': project.project_id,
            'terminal_history': history,
        }

    def agent_conversation_payload(
        self,
        project_id: str,
        *,
        agent: str,
        query: Mapping[str, object],
        headers: Mapping[str, object] | None = None,
    ) -> dict[str, object]:
        with self._invalidation_watch_lock:
            self._invalidation_audit['mobile_conversation_requests'] += 1
        project = self._require_project(project_id)
        self._authenticate(headers, required_scopes=('view',))
        view_payload = self._request_project_view(project)
        self._observe_project_view_for_notifications(project, view_payload)
        target = _validate_agent_conversation_target(
            project_id=project.project_id,
            view_payload=view_payload,
            agent=agent,
            namespace_epoch=_query_int(query, 'namespace_epoch'),
        )
        limit = min(200, max(1, _query_int(query, 'limit') or 50))
        cursor = _query_text(query, 'cursor')
        agent_record = _map(target.get('agent_record'))
        provider_key = (_optional_text(agent_record.get('provider')) or '').strip().lower()
        cache_key = _ConversationPageCacheKey(
            project_id=project.project_id,
            agent=str(target['agent']),
            namespace_epoch=int(target['namespace_epoch']),
            limit=limit,
            cursor=cursor,
        )
        cache_fingerprint = _agent_native_conversation_cache_fingerprint(
            project.project_root,
            agent=str(target['agent']),
            provider=provider_key,
        )
        if cache_fingerprint:
            cached_page = self._conversation_page_cache_get(cache_key, cache_fingerprint)
            if cached_page is not None:
                return self._agent_conversation_response(
                    project_id=project.project_id,
                    agent=str(target['agent']),
                    namespace_epoch=int(target['namespace_epoch']),
                    page=cached_page,
                )
        conversation_items = _agent_conversation_items(
            view_payload,
            project_id=project.project_id,
            agent=target['agent'],
            namespace_epoch=int(target['namespace_epoch']),
            project_root=project.project_root,
            mobile_files_dir=self._mobile_files_dir(),
            limit=limit,
            cursor=cursor,
        )
        if conversation_items.already_paged:
            page = {
                'items': conversation_items.items,
                'next_cursor': conversation_items.next_cursor,
            }
        else:
            page = _agent_conversation_page(
                conversation_items.items,
                limit=limit,
                cursor=cursor,
            )
        if cache_fingerprint and _conversation_page_has_provider_native_items(page):
            self._conversation_page_cache_put(cache_key, cache_fingerprint, page)
        return self._agent_conversation_response(
            project_id=project.project_id,
            agent=str(target['agent']),
            namespace_epoch=int(target['namespace_epoch']),
            page=page,
        )

    def _agent_conversation_response(
        self,
        *,
        project_id: str,
        agent: str,
        namespace_epoch: int,
        page: dict[str, object],
    ) -> dict[str, object]:
        conversation: dict[str, object] = {
            'project_id': project_id,
            'agent': agent,
            'namespace_epoch': namespace_epoch,
            'generated_at': self._clock(),
            'items': page['items'],
        }
        if page.get('next_cursor') is not None:
            conversation['next_cursor'] = page['next_cursor']
        return {
            'schema_version': _SCHEMA_VERSION,
            'status': 'ok',
            'conversation': conversation,
        }

    def _conversation_page_cache_get(
        self,
        key: _ConversationPageCacheKey,
        fingerprint: tuple[tuple[str, int, int], ...],
    ) -> dict[str, object] | None:
        with self._conversation_page_cache_lock:
            entry = self._conversation_page_cache.get(key)
            if entry is None:
                return None
            if entry.fingerprint != fingerprint:
                self._conversation_page_cache_bytes -= entry.byte_size
                self._conversation_page_cache.pop(key, None)
                return None
            self._conversation_page_cache.move_to_end(key)
            return _copy_conversation_page(entry.page)

    def _conversation_page_cache_put(
        self,
        key: _ConversationPageCacheKey,
        fingerprint: tuple[tuple[str, int, int], ...],
        page: dict[str, object],
    ) -> None:
        page_copy = _copy_conversation_page(page)
        byte_size = _conversation_page_byte_size(page_copy)
        if byte_size > _CONVERSATION_PAGE_CACHE_MAX_BYTES:
            return
        with self._conversation_page_cache_lock:
            old_entry = self._conversation_page_cache.pop(key, None)
            if old_entry is not None:
                self._conversation_page_cache_bytes -= old_entry.byte_size
            self._conversation_page_cache[key] = _ConversationPageCacheEntry(
                fingerprint=fingerprint,
                page=page_copy,
                byte_size=byte_size,
            )
            self._conversation_page_cache_bytes += byte_size
            self._conversation_page_cache.move_to_end(key)
            while (
                len(self._conversation_page_cache) > _CONVERSATION_PAGE_CACHE_MAX_ENTRIES
                or self._conversation_page_cache_bytes > _CONVERSATION_PAGE_CACHE_MAX_BYTES
            ):
                _, evicted = self._conversation_page_cache.popitem(last=False)
                self._conversation_page_cache_bytes -= evicted.byte_size

    def file_upload_target_from_path(self, path: str) -> tuple[str, str] | None:
        parsed = urlparse(path)
        route = parsed.path.rstrip('/') or '/'
        return _parse_project_agent_files_route(route)

    def file_download_target_from_path(self, path: str) -> tuple[str, str, str] | None:
        parsed = urlparse(path)
        route = parsed.path.rstrip('/') or '/'
        return _parse_project_agent_file_route(route)

    def dispatch_file_upload(
        self,
        path: str,
        body: bytes,
        headers: Mapping[str, object] | None = None,
    ) -> tuple[int, dict[str, object]]:
        target = self.file_upload_target_from_path(path)
        if target is None:
            raise MobileGatewayError('not found', status_code=404)
        project_id, agent = target
        project = self._require_project(project_id)
        auth = self._authenticate_any_scope(
            headers,
            allowed_scopes=('file_upload', 'message_submit', 'ask'),
        )
        if len(body) > _MAX_MOBILE_UPLOAD_FILE_BYTES:
            raise MobileGatewayError('file too large', status_code=413)
        view_payload = self._request_project_view(project)
        view = _map(view_payload.get('view'))
        namespace = _map(view.get('namespace'))
        target_record = _validate_agent_conversation_target(
            project_id=project.project_id,
            view_payload=view_payload,
            agent=agent,
            namespace_epoch=_optional_int(namespace.get('epoch')),
        )
        file_name = _header_file_name(headers)
        mime_type = _header_text(headers, 'content-type') or 'application/octet-stream'
        file_id = f'mobile-file-{uuid4().hex[:16]}'
        digest = hashlib.sha256(body).hexdigest()
        record = {
            'schema_version': _SCHEMA_VERSION,
            'file_id': file_id,
            'project_id': project.project_id,
            'agent': target_record['agent'],
            'device_id': auth.device_id,
            'file_name': file_name,
            'mime_type': mime_type,
            'size_bytes': len(body),
            'sha256': digest,
            'created_at': self._clock(),
        }
        directory = self._mobile_file_dir(project.project_id, str(target_record['agent']), file_id)
        directory.mkdir(parents=True, exist_ok=False)
        (directory / 'content.bin').write_bytes(body)
        project_relative_path = _mobile_project_upload_relative_path(
            agent=str(target_record['agent']),
            file_id=file_id,
            file_name=file_name,
        )
        project_path = (project.project_root / project_relative_path).resolve(strict=False)
        project_path.parent.mkdir(parents=True, exist_ok=True)
        project_path.write_bytes(body)
        record['project_relative_path'] = project_relative_path.as_posix()
        record['project_path'] = str(project_path)
        (directory / 'metadata.json').write_text(
            json.dumps(record, ensure_ascii=False, sort_keys=True),
            encoding='utf-8',
        )
        return 201, {
            'schema_version': _SCHEMA_VERSION,
            'status': 'ok',
            'file_id': file_id,
            'file_name': file_name,
            'mime_type': mime_type,
            'size_bytes': len(body),
            'sha256': digest,
            'project_relative_path': project_relative_path.as_posix(),
            'project_path': str(project_path),
        }

    def dispatch_file_download(
        self,
        path: str,
        headers: Mapping[str, object] | None = None,
    ) -> tuple[int, bytes, dict[str, str]]:
        target = self.file_download_target_from_path(path)
        if target is None:
            raise MobileGatewayError('not found', status_code=404)
        project_id, agent, file_id = target
        project = self._require_project(project_id)
        self._authenticate(headers, required_scopes=('file_download',))
        directory = self._mobile_file_dir(project.project_id, agent, file_id)
        metadata = _read_file_metadata(directory)
        if not metadata:
            raise MobileGatewayError('unknown file', status_code=404)
        if str(metadata.get('project_id') or '') != project.project_id:
            raise MobileGatewayError('unknown file', status_code=404)
        if str(metadata.get('agent') or '') != agent:
            raise MobileGatewayError('unknown file', status_code=404)
        content_path = directory / 'content.bin'
        try:
            body = content_path.read_bytes()
        except FileNotFoundError as exc:
            raise MobileGatewayError('unknown file', status_code=404) from exc
        digest = hashlib.sha256(body).hexdigest()
        if digest != str(metadata.get('sha256') or ''):
            raise MobileGatewayError('file checksum mismatch', status_code=500)
        return 200, body, {
            'content-type': str(metadata.get('mime_type') or 'application/octet-stream'),
            'x-ccb-file-name': str(metadata.get('file_name') or 'attachment'),
            'x-ccb-file-sha256': digest,
        }

    def dispatch_post(
        self,
        path: str,
        body: Mapping[str, object] | None,
        headers: Mapping[str, object] | None = None,
    ) -> tuple[int, dict[str, object]]:
        parsed = urlparse(path)
        route = parsed.path.rstrip('/') or '/'
        payload = body if isinstance(body, Mapping) else {}
        if route == '/v1/pairing/claim':
            try:
                result = self._require_pairing_store().claim_pairing(
                    pairing_code=str(payload.get('pairing_code') or ''),
                    device_name=str(payload.get('device_name') or ''),
                    requested_device_id=_optional_text(payload.get('device_id')),
                )
            except MobileGatewayPairingError as exc:
                raise MobileGatewayError(str(exc), status_code=exc.status_code) from exc
            return 201, result
        if route == '/v1/devices/me/presence':
            device = self._authenticate(headers, required_scopes=('view',))
            try:
                presence = self._require_pairing_store().record_presence(
                    device_id=device.device_id,
                    visible=bool(payload.get('visible')),
                    focused_project_id=_optional_text(payload.get('focused_project_id')),
                    focused_agent=_optional_text(payload.get('focused_agent')),
                    terminal_id=_optional_text(payload.get('terminal_id')),
                    user_activity=bool(payload.get('user_activity')),
                )
            except MobileGatewayPairingError as exc:
                raise MobileGatewayError(str(exc), status_code=exc.status_code) from exc
            return 200, {'schema_version': _SCHEMA_VERSION, 'status': 'ok', 'presence': presence}
        if route == '/v1/terminals':
            return 201, self._open_host_terminal(payload=payload, headers=headers)
        if route == '/v1/terminals/terminate':
            return 200, self._terminate_host_terminal(payload=payload, headers=headers)
        project_route = _parse_project_action_route(route)
        if project_route is not None:
            project_id, action = project_route
            if action == 'focus-agent':
                return 200, self._focus_agent(
                    project_id=project_id,
                    agent=str(payload.get('agent') or ''),
                    namespace_epoch=_optional_int(payload.get('namespace_epoch')),
                    headers=headers,
                )
            if action == 'focus-window':
                return 200, self._focus_window(
                    project_id=project_id,
                    window=str(payload.get('window') or ''),
                    namespace_epoch=_optional_int(payload.get('namespace_epoch')),
                    headers=headers,
                )
            if action == 'lifecycle':
                return 200, self._project_lifecycle(
                    project_id=project_id,
                    payload=payload,
                    headers=headers,
                )
            if action == 'terminals':
                return 201, self._open_terminal(
                    project_id=project_id,
                    payload=payload,
                    headers=headers,
                )
        message_route = _parse_project_agent_route(route, suffix='messages')
        if message_route is not None:
            project_id, agent = message_route
            message_payload = self._submit_agent_message(
                project_id=project_id,
                agent=agent,
                payload=payload,
                headers=headers,
            )
            return (409 if message_payload.get('status') == 'blocked' else 202), message_payload
        provider_control_route = _parse_project_agent_route(route, suffix='provider-control')
        if provider_control_route is not None:
            project_id, agent = provider_control_route
            return 200, self.update_agent_provider_settings(
                project_id,
                agent=agent,
                payload=payload,
                headers=headers,
            )
        prefix = '/v1/devices/'
        suffix = '/revoke'
        if route.startswith(prefix) and route.endswith(suffix):
            device_id = unquote(route[len(prefix):-len(suffix)].strip('/'))
            try:
                result = self._require_pairing_store().revoke_device(
                    device_id=device_id,
                    device_token=_bearer_token(headers),
                )
            except MobileGatewayPairingError as exc:
                raise MobileGatewayError(str(exc), status_code=exc.status_code) from exc
            return 200, result
        raise MobileGatewayError('not found', status_code=404)

    def dispatch_put(
        self,
        path: str,
        body: Mapping[str, object] | None,
        headers: Mapping[str, object] | None = None,
    ) -> tuple[int, dict[str, object]]:
        route = urlparse(path).path.rstrip('/') or '/'
        if route != '/v1/devices/me/push-token':
            raise MobileGatewayError('not found', status_code=404)
        device = self._authenticate(headers, required_scopes=('notify',))
        try:
            self._require_pairing_store().register_push_token(
                device_id=device.device_id,
                token=str((body or {}).get('token') or ''),
            )
        except MobileGatewayPairingError as exc:
            raise MobileGatewayError(str(exc), status_code=exc.status_code) from exc
        return 200, {'schema_version': _SCHEMA_VERSION, 'status': 'ok'}

    def dispatch_delete(
        self,
        path: str,
        headers: Mapping[str, object] | None = None,
    ) -> tuple[int, dict[str, object]]:
        route = urlparse(path).path.rstrip('/') or '/'
        if route != '/v1/devices/me/push-token':
            raise MobileGatewayError('not found', status_code=404)
        device = self._authenticate(headers, required_scopes=('notify',))
        self._require_pairing_store().delete_push_token(device_id=device.device_id)
        return 200, {'schema_version': _SCHEMA_VERSION, 'status': 'ok'}

    def _submit_agent_message(
        self,
        *,
        project_id: str,
        agent: str,
        payload: Mapping[str, object],
        headers: Mapping[str, object] | None,
    ) -> dict[str, object]:
        project = self._require_project(project_id)
        self._authenticate_any_scope(headers, allowed_scopes=('message_submit',))
        body_project_id = str(payload.get('project_id') or '').strip()
        if body_project_id and body_project_id != project.project_id:
            raise MobileGatewayError('request project_id does not match route', status_code=400)
        body_agent = str(payload.get('agent') or '').strip()
        if body_agent and body_agent != agent:
            raise MobileGatewayError('request agent does not match route', status_code=400)
        idempotency_key = str(payload.get('idempotency_key') or '').strip()
        if not idempotency_key:
            raise MobileGatewayError('idempotency_key is required', status_code=400)
        body = str(payload.get('body') or '').strip()
        attachments = _attachment_records(payload.get('attachments'))
        if not body and not attachments:
            raise MobileGatewayError('body or attachments are required', status_code=400)
        submit_body = _message_submit_body(body, attachments)
        message_format = str(payload.get('format') or 'markdown').strip() or 'markdown'
        view_payload = self._request_project_view(project)
        target = _validate_agent_conversation_target(
            project_id=project.project_id,
            view_payload=view_payload,
            agent=agent,
            namespace_epoch=_optional_int(payload.get('namespace_epoch')),
        )
        if str(target['agent']) == 'frontdesk':
            receipt = self._submit_frontdesk_message(
                project=project,
                body=submit_body,
                idempotency_key=idempotency_key,
            )
            job_id = _optional_text(receipt.get('job_id'))
            if not job_id:
                raise MobileGatewayError('ccbd submit did not return job_id', status_code=503)
            state = _optional_text(receipt.get('status')) or 'accepted'
            created_at = _optional_text(receipt.get('accepted_at')) or self._clock()
            message_id = idempotency_key
            self._record_project_activity(project.project_id, activity_at=created_at)
            return {
                'schema_version': _SCHEMA_VERSION,
                'status': 'ok',
                'project_id': project.project_id,
                'agent': target['agent'],
                'message_submit': {
                    'accepted': True,
                    'idempotency_key': idempotency_key,
                    'message_id': message_id,
                    'job_id': job_id,
                    'state': state,
                    'created_at': created_at,
                    'message': {
                        'id': message_id,
                        'agent': target['agent'],
                        'kind': 'user_message',
                        'title': 'You',
                        'body': body,
                        'format': message_format,
                        'state': state,
                        'source': 'mobile',
                        'attachments': attachments,
                    },
                },
            }
        blocked = _terminal_operation_blocked_payload(
            view_payload,
            agent=str(target['agent']),
            namespace_epoch=int(target['namespace_epoch']),
            operation='input',
        )
        if blocked is not None:
            return {
                'schema_version': _SCHEMA_VERSION,
                'status': 'blocked',
                'project_id': project.project_id,
                'agent': target['agent'],
                'terminal_blocked': blocked,
            }
        message_target = _pane_message_target(
            project_id=project.project_id,
            view_payload=view_payload,
            target=target,
        )
        try:
            self._terminal_message_sender(message_target, submit_body)
        except _TerminalBlockedError as exc:
            return {
                'schema_version': _SCHEMA_VERSION,
                'status': 'blocked',
                'project_id': project.project_id,
                'agent': target['agent'],
                'terminal_blocked': exc.terminal_blocked,
            }
        except Exception as exc:
            raise MobileGatewayError(_error_text(exc), status_code=503) from exc
        message_id = idempotency_key
        created_at = self._clock()
        self._record_project_activity(project.project_id, activity_at=created_at)
        return {
            'schema_version': _SCHEMA_VERSION,
            'status': 'ok',
            'project_id': project.project_id,
            'agent': target['agent'],
            'message_submit': {
                'accepted': True,
                'idempotency_key': idempotency_key,
                'message_id': message_id,
                'job_id': None,
                'state': 'sent',
                'created_at': created_at,
                'message': {
                    'id': message_id,
                    'agent': target['agent'],
                    'kind': 'user_message',
                    'title': 'You',
                    'body': body,
                    'format': message_format,
                    'state': 'sent',
                    'source': 'mobile',
                    'attachments': attachments,
                },
            },
        }

    def _submit_frontdesk_message(
        self,
        *,
        project: MobileGatewayProject,
        body: str,
        idempotency_key: str,
    ) -> dict[str, object]:
        request = MessageEnvelope(
            project_id=project.project_id,
            to_agent='frontdesk',
            from_actor='user',
            body=body,
            task_id=idempotency_key,
            reply_to=None,
            message_type='ask',
            delivery_scope=DeliveryScope.SINGLE,
            silence_on_success=False,
            route_options={
                'source': 'mobile_gateway',
                'entry': 'frontdesk_message_submit',
                'idempotency_key': idempotency_key,
            },
        )
        try:
            receipt = project.client().submit(request)
        except CcbdClientError as exc:
            raise MobileGatewayError(str(exc), status_code=503) from exc
        except Exception as exc:
            raise MobileGatewayError(_error_text(exc), status_code=503) from exc
        if not isinstance(receipt, Mapping):
            raise MobileGatewayError('ccbd submit returned invalid receipt', status_code=503)
        return dict(receipt)

    def terminal_id_from_path(self, path: str) -> str | None:
        parsed = urlparse(path)
        route = parsed.path.rstrip('/') or '/'
        prefix = '/v1/terminals/'
        if not route.startswith(prefix):
            return None
        terminal_id = unquote(route[len(prefix):].strip('/'))
        return terminal_id or None

    def handle_terminal_websocket(self, terminal_id: str, connection: WebSocketConnection) -> None:
        store = self._require_pairing_store()
        terminal_token = ''
        close_reason = 'transport_disconnected'
        session = None
        output_stop = threading.Event()
        output_thread: threading.Thread | None = None
        close_state: dict[str, str] = {}
        close_handle = False
        try:
            open_frame = connection.read_json()
            if open_frame is None:
                return
            if str(open_frame.get('type') or '') != 'open':
                connection.send_json({'type': 'error', 'code': 'terminal_open_required'})
                close_reason = 'invalid_open'
                return
            if str(open_frame.get('terminal_id') or '') != terminal_id:
                connection.send_json({'type': 'error', 'code': 'terminal_id_mismatch'})
                close_reason = 'invalid_open'
                return
            terminal_token = str(open_frame.get('token') or '')
            resume_cursor = _optional_int(open_frame.get('resume_cursor'))
            record = store.authenticate_terminal_token(
                terminal_id=terminal_id,
                terminal_token=terminal_token,
                resume_cursor=resume_cursor,
            )
            attach_target = self._terminal_attach_target(
                record,
                include_history=resume_cursor is None,
            )
            session = self._terminal_session_factory(attach_target)
            self._register_terminal_session(session)
            viewport = _terminal_viewport_state(session, fallback=attach_target)
            connection.send_json(
                {
                    'type': 'open',
                    'terminal_id': terminal_id,
                    'resume_cursor': _int(record.get('last_output_seq'), 0),
                    'last_input_seq': _int(record.get('last_input_seq'), 0),
                    'geometry': viewport['geometry'],
                    'resize_policy': viewport['resize_policy'],
                    'geometry_revision': viewport['revision'],
                }
            )
            output_thread = threading.Thread(
                target=_pump_terminal_output,
                args=(
                    connection,
                    session,
                    output_stop,
                    close_state,
                    store,
                    terminal_id,
                    terminal_token,
                    _int(record.get('last_output_seq'), 0),
                    _int(viewport.get('revision'), 0),
                ),
                daemon=True,
            )
            output_thread.start()
            while not output_stop.is_set():
                frame = connection.read_json()
                if frame is None:
                    close_reason = close_state.get('reason') or 'transport_disconnected'
                    break
                close_reason = self._handle_terminal_frame(
                    connection=connection,
                    session=session,
                    terminal_id=terminal_id,
                    terminal_token=terminal_token,
                    frame=frame,
                )
                if close_reason:
                    close_handle = True
                    break
            if output_stop.is_set() and not close_handle:
                close_reason = close_state.get('reason') or close_reason
                close_handle = close_reason != 'transport_disconnected'
        except MobileGatewayPairingError as exc:
            close_reason = str(exc.reason or 'terminal_token_denied')
            close_handle = True
            _safe_send_json(connection, {'type': 'error', 'code': close_reason})
        except MobileGatewayError as exc:
            close_reason = _terminal_error_code(exc)
            close_handle = True
            _safe_send_json(connection, _terminal_error_frame(exc))
        except WebSocketProtocolError as exc:
            close_reason = 'protocol_error'
            close_handle = True
            _safe_send_json(connection, {'type': 'error', 'code': 'protocol_error', 'message': _error_text(exc)})
        except Exception as exc:
            close_reason = 'terminal_stream_error'
            close_handle = True
            _safe_send_json(connection, {'type': 'error', 'code': 'terminal_stream_error', 'message': _error_text(exc)})
        finally:
            output_stop.set()
            if terminal_token:
                try:
                    if close_handle:
                        store.close_terminal_handle(
                            terminal_id=terminal_id,
                            terminal_token=terminal_token,
                            reason=close_reason or 'client_closed',
                        )
                    else:
                        store.mark_terminal_disconnected(
                            terminal_id=terminal_id,
                            terminal_token=terminal_token,
                            reason=close_reason or 'transport_disconnected',
                        )
                except MobileGatewayPairingError:
                    pass
            if session is not None:
                self._unregister_terminal_session(session)
                try:
                    session.close()
                except Exception:
                    pass
            _safe_send_json(connection, {'type': 'closed', 'reason': close_reason or 'client_closed'})
            connection.close()
            if output_thread is not None:
                output_thread.join(timeout=1)

    def _client(self):
        return self._ccbd_client_factory()

    def _focus_agent(
        self,
        *,
        project_id: str,
        agent: str,
        namespace_epoch: int | None,
        headers: Mapping[str, object] | None,
    ) -> dict[str, object]:
        project = self._require_project(project_id)
        self._authenticate(headers, required_scopes=('focus',))
        if not str(agent or '').strip():
            raise MobileGatewayError('agent is required', status_code=400)
        try:
            focus = project.client().project_focus_agent(agent=agent, namespace_epoch=namespace_epoch)
        except CcbdClientError as exc:
            raise MobileGatewayError(str(exc), status_code=_ccbd_focus_status(exc)) from exc
        except Exception as exc:
            raise MobileGatewayError(_error_text(exc), status_code=503) from exc
        payload = self._focused_project_view_payload(project, focus)
        self._record_project_activity(project.project_id)
        return payload

    def _project_lifecycle(
        self,
        *,
        project_id: str,
        payload: Mapping[str, object],
        headers: Mapping[str, object] | None,
    ) -> dict[str, object]:
        project = self._require_project(project_id)
        self._authenticate(headers, required_scopes=('lifecycle',))
        body_project_id = str(payload.get('project_id') or '').strip()
        if body_project_id and body_project_id != project.project_id:
            raise MobileGatewayError('request project_id does not match route', status_code=400)
        action = str(payload.get('action') or '').strip().lower()
        if action not in {'wake', 'open', 'close', 'stop'}:
            raise MobileGatewayError('unsupported lifecycle action', status_code=400)
        if action in {'wake', 'open'}:
            result = self._lifecycle_result(
                action=action,
                state='running',
                effect='already_running' if action == 'wake' else 'opened',
                ccb_authority=True,
            )
            response = _redact_project_view_payload(self._request_project_view(project))
            response.update({
                'schema_version': _SCHEMA_VERSION,
                'status': 'ok',
                'project_id': project.project_id,
                'lifecycle': result,
            })
            return response
        if action == 'close':
            return {
                'schema_version': _SCHEMA_VERSION,
                'status': 'ok',
                'project_id': project.project_id,
                'lifecycle': self._lifecycle_result(
                    action='close',
                    state='running',
                    effect='mobile_view_closed',
                    ccb_authority=True,
                ),
            }
        try:
            stop_result = project.client().stop_all(force=False)
        except CcbdClientError as exc:
            raise MobileGatewayError(str(exc), status_code=503) from exc
        except Exception as exc:
            raise MobileGatewayError(_error_text(exc), status_code=503) from exc
        return {
            'schema_version': _SCHEMA_VERSION,
            'status': 'ok',
            'project_id': project.project_id,
            'lifecycle': self._lifecycle_result(
                action='stop',
                state='stopping',
                effect='ccbd_stop_requested',
                ccb_authority=True,
                forced=False,
                result=dict(stop_result or {}) if isinstance(stop_result, Mapping) else {},
            ),
        }

    def _lifecycle_result(
        self,
        *,
        action: str,
        state: str,
        effect: str,
        ccb_authority: bool,
        forced: bool = False,
        result: dict[str, object] | None = None,
    ) -> dict[str, object]:
        return {
            'action': action,
            'state': state,
            'effect': effect,
            'forced': forced,
            'ccb_authority': ccb_authority,
            'tmux_kill_server': False,
            'updated_at': self._clock(),
            **({'result': result} if result is not None else {}),
        }

    def _focus_window(
        self,
        *,
        project_id: str,
        window: str,
        namespace_epoch: int | None,
        headers: Mapping[str, object] | None,
    ) -> dict[str, object]:
        project = self._require_project(project_id)
        self._authenticate(headers, required_scopes=('focus',))
        if not str(window or '').strip():
            raise MobileGatewayError('window is required', status_code=400)
        try:
            focus = project.client().project_focus_window(window=window, namespace_epoch=namespace_epoch)
        except CcbdClientError as exc:
            raise MobileGatewayError(str(exc), status_code=_ccbd_focus_status(exc)) from exc
        except Exception as exc:
            raise MobileGatewayError(_error_text(exc), status_code=503) from exc
        payload = self._focused_project_view_payload(project, focus)
        self._record_project_activity(project.project_id)
        return payload

    def _open_terminal(
        self,
        *,
        project_id: str,
        payload: Mapping[str, object],
        headers: Mapping[str, object] | None,
    ) -> dict[str, object]:
        project = self._require_project(project_id)
        auth = self._authenticate(headers, required_scopes=('terminal_input',))
        body_project_id = str(payload.get('project_id') or '').strip()
        if body_project_id and body_project_id != project.project_id:
            raise MobileGatewayError('request project_id does not match route', status_code=400)
        target = _map(payload.get('target'))
        geometry = _map(payload.get('geometry'))
        view_payload = self._request_project_view(project)
        target_payload = _validate_terminal_target(
            project.project_id,
            view_payload,
            target=target,
            namespace_epoch=_optional_int(payload.get('namespace_epoch')),
        )
        handle = self._require_pairing_store().create_terminal_handle(
            project_id=project.project_id,
            device_id=auth.device_id,
            target_epoch=int(target_payload['target_epoch']),
            target_summary=target_payload['target_summary'],
            geometry=geometry,
        )
        terminal_id = str(handle.get('terminal_id') or '')
        handle['websocket_url'] = _terminal_websocket_url(headers, terminal_id=terminal_id)
        return handle

    def _open_host_terminal(
        self,
        *,
        payload: Mapping[str, object],
        headers: Mapping[str, object] | None,
    ) -> dict[str, object]:
        auth = self._authenticate(headers, required_scopes=('host_terminal',))
        client_session_id = str(payload.get('client_session_id') or '').strip()
        display_name = str(payload.get('display_name') or '').strip()
        geometry_payload = _map(payload.get('geometry'))
        geometry = TerminalGeometry.from_mapping(geometry_payload)
        try:
            attach_target = self._require_host_terminal_manager().attach_target(
                terminal_id='pending',
                device_id=auth.device_id,
                client_session_id=client_session_id,
                display_name=display_name,
                geometry=geometry,
                include_history=True,
            )
        except RuntimeError as exc:
            status_code = 400 if 'slot must be' in str(exc) else 503
            raise MobileGatewayError(str(exc), status_code=status_code) from exc
        handle = self._require_pairing_store().create_terminal_handle(
            project_id='@host',
            device_id=auth.device_id,
            target_epoch=0,
            target_summary=attach_target.target_summary,
            geometry=geometry_payload,
        )
        terminal_id = str(handle.get('terminal_id') or '')
        handle['websocket_url'] = _terminal_websocket_url(headers, terminal_id=terminal_id)
        return handle

    def _terminate_host_terminal(
        self,
        *,
        payload: Mapping[str, object],
        headers: Mapping[str, object] | None,
    ) -> dict[str, object]:
        auth = self._authenticate(headers, required_scopes=('host_terminal',))
        client_session_id = str(payload.get('client_session_id') or '').strip()
        revoked_terminal_count = self._require_pairing_store().revoke_host_terminal_handles(
            device_id=auth.device_id,
            client_session_id=client_session_id,
        )
        try:
            terminated = self._require_host_terminal_manager().terminate(
                device_id=auth.device_id,
                client_session_id=client_session_id,
            )
        except RuntimeError as exc:
            status_code = 400 if 'slot must be' in str(exc) else 503
            raise MobileGatewayError(str(exc), status_code=status_code) from exc
        return {
            'schema_version': _SCHEMA_VERSION,
            'status': 'ok',
            'client_session_id': client_session_id,
            'terminated': terminated,
            'revoked_terminal_count': revoked_terminal_count,
        }

    def _focused_project_view_payload(
        self,
        project: MobileGatewayProject,
        focus: dict[str, object],
    ) -> dict[str, object]:
        payload = self._request_project_view(project)
        redacted = _redact_project_view_payload(payload)
        redacted['focus'] = dict(focus or {}) if isinstance(focus, dict) else {}
        return redacted

    def close(self) -> None:
        with self._terminal_sessions_lock:
            if self._closed:
                return
            self._closed = True
            terminal_sessions = tuple(self._terminal_sessions.values())
            self._terminal_sessions.clear()
        for session in terminal_sessions:
            close = getattr(session, 'close', None)
            if callable(close):
                try:
                    close()
                except Exception:
                    pass
        if self._project_health_cache is not None:
            self._project_health_cache.close()
        if self._push_dispatcher is not None:
            self._push_dispatcher.close()
        if self._owns_background_executor and self._background_executor is not None:
            close = getattr(self._background_executor, 'close', None)
            if callable(close):
                close()

    def _register_terminal_session(self, session: object) -> None:
        with self._terminal_sessions_lock:
            if not self._closed:
                self._terminal_sessions[id(session)] = session
                return
        close = getattr(session, 'close', None)
        if callable(close):
            try:
                close()
            except Exception:
                pass
        raise MobileGatewayError('mobile gateway is shutting down', status_code=503)

    def _unregister_terminal_session(self, session: object) -> None:
        with self._terminal_sessions_lock:
            self._terminal_sessions.pop(id(session), None)

    def _registry_projects(self, *, refresh: bool = False) -> tuple[MobileGatewayProject, ...]:
        if refresh and self._project_registry_provider is not None:
            try:
                self._project_registry = self._project_registry_provider()
            except Exception:
                pass
        return self._project_registry.projects()

    def _submit_background(self, task: Callable[[], object]) -> Future[object] | None:
        if self._closed:
            return None
        executor = self._background_executor
        if executor is None:
            executor = _BoundedDaemonExecutor(
                workers=_PROJECT_HEALTH_REFRESH_WORKERS,
                max_pending=_PROJECT_HEALTH_REFRESH_QUEUE_MAX,
            )
            self._background_executor = executor
            self._owns_background_executor = True
        submit = getattr(executor, 'submit', None)
        if not callable(submit):
            return None
        return submit(task)

    def _require_project(self, project_id: str) -> MobileGatewayProject:
        requested = str(project_id or '').strip()
        if self._project_registry_provider is not None:
            self._registry_projects(refresh=True)
        project = self._project_registry.get(requested)
        if project is None:
            raise MobileGatewayError('unknown project', status_code=404)
        return project

    def _require_pairing_store(self) -> MobileGatewayPairingStore:
        if self._pairing_store is None:
            raise MobileGatewayError('mobile pairing store is not configured', status_code=503)
        return self._pairing_store

    def _require_host_terminal_manager(self) -> HostTerminalManager:
        if self._host_terminal_manager is None:
            raise MobileGatewayError('host terminal service is not configured', status_code=503)
        return self._host_terminal_manager

    def _require_notification_store(self) -> MobileNotificationStore:
        if self._notification_store is None:
            raise MobileGatewayError('mobile notification store is not configured', status_code=503)
        return self._notification_store

    def _observe_project_view_for_notifications(
        self,
        project: MobileGatewayProject,
        payload: dict[str, object],
    ) -> None:
        """Use an already-authorized ProjectView as the activity source.

        This is deliberately called only from an explicit view/conversation
        request, never from the SSE loop. It keeps completion detection precise
        without an extra ccbd request and establishes watcher baselines.
        """
        store = self._notification_store
        if store is None:
            return
        observed_at = self._clock()
        invalidations = _invalidation_snapshots_for_project(project, payload, observed_at=observed_at)
        store.sync_invalidations(invalidations)
        emitted = store.sync_snapshots(
            _notification_snapshots_for_project(project, payload, observed_at=observed_at)
        )
        for event in emitted:
            self._record_project_activity(event.project_id, activity_at=event.completed_at)
            if self._push_dispatcher is not None:
                self._push_dispatcher.deliver(event)

    def _register_invalidation_watch(self, query: Mapping[str, object]) -> None:
        project_id = _query_text(query, 'watch_project_id')
        agent = _query_text(query, 'watch_agent')
        if not project_id or not agent:
            return
        # Do not refresh a dynamic registry for each SSE tick. A selected
        # project was already resolved by the app's normal REST load; unknown
        # targets are ignored rather than inducing a registry scan.
        project = self._project_registry.get(project_id)
        if project is None:
            return
        epoch = _query_int(query, 'watch_namespace_epoch')
        key = (project.project_id, agent, epoch)
        now = self._monotonic_clock()
        target = _InvalidationWatchTarget(
            project_id=project.project_id,
            project_short_name=project.public_display_name,
            agent=agent,
            namespace_epoch=epoch,
            provider=_query_text(query, 'watch_provider'),
            expires_monotonic=now + _INVALIDATION_WATCH_TARGET_TTL_SECONDS,
        )
        with self._invalidation_watch_lock:
            self._invalidation_watch_targets[key] = target
            self._invalidation_watch_targets.move_to_end(key)
            while len(self._invalidation_watch_targets) > _INVALIDATION_WATCH_TARGET_LIMIT:
                self._invalidation_watch_targets.popitem(last=False)

    def _selected_invalidation_watch_snapshots(self) -> list[MobileInvalidationSnapshot]:
        now = self._monotonic_clock()
        with self._invalidation_watch_lock:
            expired = [
                key for key, target in self._invalidation_watch_targets.items()
                if target.expires_monotonic < now
            ]
            for key in expired:
                self._invalidation_watch_targets.pop(key, None)
            targets = list(self._invalidation_watch_targets.values())
            self._invalidation_audit['watch_refreshes'] += 1
        snapshots: list[MobileInvalidationSnapshot] = []
        for target in targets:
            project = self._project_registry.get(target.project_id)
            if project is None:
                continue
            activity_state = 'unknown'
            try:
                probe = self._agent_activity_probe(
                    project_root=project.project_root,
                    project_id=target.project_id,
                    agent=target.agent,
                    provider=target.provider,
                    namespace_epoch=target.namespace_epoch,
                    now=self._clock(),
                )
                activity_state = str(
                    getattr(probe, 'activity_state', '') or 'unknown'
                ).strip().lower()
                with self._invalidation_watch_lock:
                    self._invalidation_audit['watch_activity_probes'] += 1
            except Exception:
                with self._invalidation_watch_lock:
                    self._invalidation_audit['watch_activity_probe_failures'] += 1
            # Native fingerprints use stat/read of provider-owned local files;
            # no ccbd project_view or mobile conversation HTTP/RPC occurs here.
            fingerprint = _agent_native_conversation_cache_fingerprint(
                project.project_root, agent=target.agent, provider=target.provider
            )
            digest = hashlib.sha256(repr(fingerprint).encode('utf-8')).hexdigest()[:24]
            snapshots.append(MobileInvalidationSnapshot(
                project_id=target.project_id,
                project_short_name=target.project_short_name,
                namespace_epoch=target.namespace_epoch,
                agent=target.agent,
                activity_state=activity_state,
                conversation_fingerprint=digest,
                observed_at=self._clock(),
            ))
        with self._invalidation_watch_lock:
            self._invalidation_audit['watch_targets_checked'] += len(snapshots)
        return snapshots

    def _mobile_file_dir(self, project_id: str, agent: str, file_id: str) -> Path:
        return (
            self._mobile_files_dir()
            / _safe_path_segment(project_id)
            / _safe_path_segment(agent)
            / _safe_path_segment(file_id)
        )

    def _mobile_files_dir(self) -> Path:
        root = (
            self._mobile_dir
            if self._mobile_dir is not None
            else self._project_root / '.ccb' / 'ccbd' / 'mobile'
        )
        return root / 'files'

    def _capabilities(self) -> list[str]:
        values = list(_BASE_CAPABILITIES)
        if self._pairing_store is not None:
            values.extend(_PAIRING_CAPABILITIES)
        return values

    def _authenticate(self, headers: Mapping[str, object] | None, *, required_scopes: tuple[str, ...]):
        try:
            return self._require_pairing_store().authenticate_device(
                _bearer_token(headers),
                required_scopes=required_scopes,
            )
        except MobileGatewayPairingError as exc:
            raise MobileGatewayError(str(exc), status_code=exc.status_code) from exc

    def _authenticate_any_scope(
        self,
        headers: Mapping[str, object] | None,
        *,
        allowed_scopes: tuple[str, ...],
    ):
        auth = self._authenticate(headers, required_scopes=())
        if auth.scopes.intersection({str(scope) for scope in allowed_scopes}):
            return auth
        raise MobileGatewayError('device scope denied', status_code=403)

    def _ping_or_unavailable(self, project: MobileGatewayProject) -> dict[str, object]:
        try:
            payload = project.client().ping('ccbd')
        except CcbdClientError as exc:
            raise MobileGatewayError(str(exc), status_code=503) from exc
        except Exception as exc:
            raise MobileGatewayError(_error_text(exc), status_code=503) from exc
        return dict(payload or {}) if isinstance(payload, dict) else {}

    def _project_list_health_by_project(
        self,
        projects: tuple[MobileGatewayProject, ...],
    ) -> dict[str, dict[str, object]]:
        return {
            project.project_id: self._project_list_health(project)
            for project in projects
        }

    def _project_list_health(self, project: MobileGatewayProject) -> dict[str, object]:
        try:
            return self._ping_or_unavailable(project)
        except MobileGatewayError:
            return {
                'health': 'unreachable',
                'mount_state': 'unavailable',
                'error': 'project unavailable',
            }

    def _project_activity_summary(
        self,
        project: MobileGatewayProject,
        *,
        allow_refresh: bool,
        deadline: float,
    ) -> tuple[dict[str, object], bool]:
        if self._mode == 'loopback_server_registry':
            return self._server_project_activity_summary(
                project,
                allow_refresh=allow_refresh,
            )
        store_record = (
            self._project_activity_store.project(project.project_id)
            if self._project_activity_store is not None
            else {}
        )
        summary = _project_activity_summary_from_record(store_record)
        if not allow_refresh:
            return summary, False
        if self._project_activity_store is not None and not _project_activity_record_stale(
            store_record,
            now_text=self._clock(),
            max_age_seconds=_PROJECT_ACTIVITY_REFRESH_TTL_SECONDS,
        ):
            return summary, False
        if time.monotonic() >= deadline:
            return summary, False

        attempted_refresh = True
        timeout_seconds = min(
            _PROJECT_ACTIVITY_REFRESH_PER_PROJECT_SECONDS,
            max(0.01, deadline - time.monotonic()),
        )
        try:
            view_payload = self._request_project_view_with_timeout(
                project,
                timeout_seconds=timeout_seconds,
            )
        except MobileGatewayError:
            return summary, attempted_refresh
        except Exception:
            return summary, attempted_refresh
        fresh_summary = _project_activity_summary_from_view(view_payload)
        checked_at = self._clock()
        if self._project_activity_store is not None:
            try:
                self._project_activity_store.record_summary(
                    project_id=project.project_id,
                    summary=fresh_summary,
                    checked_at=checked_at,
                )
            except Exception:
                pass
        merged = dict(summary)
        merged.update(fresh_summary)
        return merged, attempted_refresh

    def _server_project_activity_summary(
        self,
        project: MobileGatewayProject,
        *,
        allow_refresh: bool,
    ) -> tuple[dict[str, object], bool]:
        if self._project_activity_store is None:
            return {}, False
        store_record = self._project_activity_store.project(project.project_id)
        summary = _project_activity_summary_from_record(store_record)
        if not allow_refresh or not _project_activity_record_stale(
            store_record,
            now_text=self._clock(),
            max_age_seconds=_PROJECT_ACTIVITY_REFRESH_TTL_SECONDS,
        ):
            return summary, False
        with self._project_activity_refresh_lock:
            if project.project_id in self._project_activity_refreshing:
                return summary, False
            self._project_activity_refreshing.add(project.project_id)
        future = self._submit_background(lambda: self._refresh_server_project_activity(project))
        if future is None:
            with self._project_activity_refresh_lock:
                self._project_activity_refreshing.discard(project.project_id)
            return summary, False
        return summary, True

    def _refresh_server_project_activity(self, project: MobileGatewayProject) -> None:
        try:
            view_payload = self._request_project_view(project)
            fresh_summary = _project_activity_summary_from_view(view_payload)
            if self._project_activity_store is not None:
                self._project_activity_store.record_summary(
                    project_id=project.project_id,
                    summary=fresh_summary,
                    checked_at=self._clock(),
                )
        except Exception:
            pass
        finally:
            with self._project_activity_refresh_lock:
                self._project_activity_refreshing.discard(project.project_id)

    def _record_project_opened(self, project_id: str) -> None:
        if self._project_activity_store is None:
            return
        try:
            self._project_activity_store.record_opened(
                project_id=project_id,
                opened_at=self._clock(),
            )
        except Exception:
            pass

    def _record_project_activity(self, project_id: str, *, activity_at: str | None = None) -> None:
        if self._project_activity_store is None:
            return
        try:
            self._project_activity_store.record_activity(
                project_id=project_id,
                activity_at=activity_at or self._clock(),
            )
        except Exception:
            pass

    def _request_project_view(self, project: MobileGatewayProject) -> dict[str, object]:
        with self._invalidation_watch_lock:
            self._invalidation_audit['ccbd_project_view_requests'] += 1
        try:
            payload = project.client().project_view(schema_version=1)
        except CcbdClientError as exc:
            raise MobileGatewayError(str(exc), status_code=503) from exc
        except Exception as exc:
            raise MobileGatewayError(_error_text(exc), status_code=503) from exc
        return dict(payload or {}) if isinstance(payload, dict) else {}

    def _request_project_view_with_timeout(
        self,
        project: MobileGatewayProject,
        *,
        timeout_seconds: float,
    ) -> dict[str, object]:
        if self._project_activity_store is None:
            return self._request_project_view(project)
        future = self._submit_background(lambda: self._request_project_view(project))
        if future is None:
            raise MobileGatewayError('project activity refresh is busy', status_code=503)
        try:
            return future.result(timeout=max(0.01, timeout_seconds))
        except FutureTimeoutError as exc:
            future.cancel()
            raise MobileGatewayError('project activity unavailable', status_code=503) from exc

    def _terminal_attach_target(
        self,
        record: dict[str, object],
        *,
        include_history: bool,
    ) -> TerminalAttachTarget:
        target_summary = _map(record.get('target_summary'))
        if target_summary.get('kind') == 'host_shell':
            try:
                return self._require_host_terminal_manager().attach_target(
                    terminal_id=str(record.get('terminal_id') or ''),
                    device_id=str(record.get('device_id') or ''),
                    client_session_id=str(target_summary.get('client_session_id') or ''),
                    display_name=str(target_summary.get('display_name') or ''),
                    geometry=TerminalGeometry.from_mapping(record.get('geometry')),
                    include_history=include_history,
                )
            except RuntimeError as exc:
                raise MobileGatewayError(str(exc), status_code=503) from exc
        project = self._require_project(str(record.get('project_id') or ''))
        view_payload = self._request_project_view(project)
        view = _map(view_payload.get('view'))
        namespace = _map(view.get('namespace'))
        actual_epoch = _optional_int(namespace.get('epoch'))
        target_epoch = _optional_int(record.get('target_epoch'))
        if actual_epoch is None or target_epoch is None or actual_epoch != target_epoch:
            raise MobileGatewayError('stale namespace epoch', status_code=409)
        blocked = _terminal_operation_blocked_payload(
            view_payload,
            agent=_optional_text(_map(record.get('target_summary')).get('agent')),
            namespace_epoch=target_epoch,
            operation='attach',
        )
        if blocked is not None:
            raise _TerminalBlockedError(blocked)
        herdr_summary = _herdr_terminal_target_summary(
            view,
            agent=_optional_text(target_summary.get('agent')),
            operation='attach',
        )
        socket_path = _optional_text(namespace.get('socket_path'))
        session_name = _optional_text(namespace.get('session_name'))
        _validate_terminal_summary(record, view, herdr_summary=herdr_summary)
        if not socket_path or not session_name:
            if herdr_summary is None:
                raise MobileGatewayError('ProjectView tmux evidence is not attachable', status_code=409)
        pane_id = _terminal_summary_pane_id(target_summary, view)
        if not pane_id and herdr_summary is not None:
            pane_id = _optional_text(herdr_summary.get('pane_id'))
        if not pane_id:
            raise MobileGatewayError('terminal target pane evidence is required', status_code=409)
        return TerminalAttachTarget(
            terminal_id=str(record.get('terminal_id') or ''),
            socket_path=socket_path or '',
            session_name=session_name or _optional_text(_map(herdr_summary).get('session_name')) or '',
            pane_id=pane_id,
            geometry=TerminalGeometry.from_mapping(record.get('geometry')),
            target_summary=target_summary,
            include_history=include_history,
            **_terminal_target_kwargs(herdr_summary),
        )

    def _handle_terminal_frame(
        self,
        *,
        connection: WebSocketConnection,
        session,
        terminal_id: str,
        terminal_token: str,
        frame: Mapping[str, object],
    ) -> str:
        frame_type = str(frame.get('type') or '').strip()
        if frame_type == 'input':
            seq = _required_positive_int(frame.get('seq'), 'seq')
            data = base64.b64decode(str(frame.get('bytes_b64') or ''), validate=True)
            record = self._require_pairing_store().record_terminal_input_sequence(
                terminal_id=terminal_id,
                terminal_token=terminal_token,
                sequence=seq,
            )
            session.write(data)
            project_id = str(record.get('project_id') or '')
            if project_id != '@host':
                self._record_project_activity(project_id)
            return ''
        if frame_type == 'paste':
            seq = _required_positive_int(frame.get('seq'), 'seq')
            record = self._require_pairing_store().record_terminal_input_sequence(
                terminal_id=terminal_id,
                terminal_token=terminal_token,
                sequence=seq,
            )
            session.paste(str(frame.get('text') or ''))
            project_id = str(record.get('project_id') or '')
            if project_id != '@host':
                self._record_project_activity(project_id)
            return ''
        if frame_type == 'resize':
            viewport = _terminal_viewport_state(session)
            if viewport is not None and viewport.get('resize_policy') != 'client':
                return ''
            session.resize(TerminalGeometry.from_mapping(frame))
            return ''
        if frame_type == 'closed':
            return str(frame.get('reason') or 'client_closed')
        connection.send_json({'type': 'error', 'code': 'unsupported_terminal_frame'})
        return 'unsupported_terminal_frame'


def parse_listen_address(value: str | None, *, allow_lan: bool = False) -> ListenAddress:
    text = str(value or '').strip()
    if not text:
        return ListenAddress()
    if text.count(':') != 1:
        raise ValueError('listen address must be HOST:PORT')
    host, port_text = (item.strip() for item in text.rsplit(':', 1))
    if not host:
        host = _DEFAULT_HOST
    try:
        port = int(port_text)
    except ValueError as exc:
        raise ValueError('listen port must be an integer') from exc
    if port < 0 or port > 65535:
        raise ValueError('listen port must be between 0 and 65535')
    if not _is_loopback_host(host):
        if not allow_lan:
            raise ValueError('mobile gateway only supports loopback listen addresses for this route provider')
        if not _is_private_lan_host(host):
            raise ValueError(
                'LAN mobile gateway listen address must be a specific private or link-local IP address'
            )
    return ListenAddress(host=host, port=port)


def build_mobile_gateway_server(listen: ListenAddress, service: MobileGatewayService) -> ThreadingHTTPServer:
    class _Handler(BaseHTTPRequestHandler):
        server_version = 'CCBMobileGateway/1'

        def do_GET(self) -> None:  # noqa: N802 - stdlib hook
            if service.notification_stream_target_from_path(self.path):
                self._send_notification_stream()
                return
            terminal_id = service.terminal_id_from_path(self.path)
            if terminal_id is not None and is_websocket_upgrade(self.headers):
                try:
                    connection = accept_websocket(self)
                except WebSocketProtocolError as exc:
                    self._send_json(400, {
                        'schema_version': _SCHEMA_VERSION,
                        'status': 'error',
                        'error': _error_text(exc),
                    })
                    return
                service.handle_terminal_websocket(terminal_id, connection)
                return
            if service.file_download_target_from_path(self.path) is not None:
                try:
                    status, body, headers = service.dispatch_file_download(self.path, self.headers)
                except MobileGatewayError as exc:
                    self._send_json(exc.status_code, _mobile_error_payload(exc))
                    return
                self._send_bytes(status, body, headers)
                return
            try:
                status, payload = service.dispatch_get(self.path, self.headers)
            except MobileGatewayError as exc:
                status = exc.status_code
                payload = _mobile_error_payload(exc)
            self._send_json(status, payload)

        def do_POST(self) -> None:  # noqa: N802 - stdlib hook
            try:
                if service.file_upload_target_from_path(self.path) is not None:
                    status, payload = service.dispatch_file_upload(
                        self.path,
                        self._read_raw_body(max_bytes=_MAX_MOBILE_UPLOAD_FILE_BYTES),
                        self.headers,
                    )
                else:
                    status, payload = service.dispatch_post(self.path, self._read_json_body(), self.headers)
            except MobileGatewayError as exc:
                status = exc.status_code
                payload = _mobile_error_payload(exc)
            except ValueError as exc:
                status = 400
                payload = {
                    'schema_version': _SCHEMA_VERSION,
                    'status': 'error',
                    'error': _error_text(exc),
                }
            self._send_json(status, payload)

        def do_PUT(self) -> None:  # noqa: N802 - stdlib hook
            try:
                status, payload = service.dispatch_put(self.path, self._read_json_body(), self.headers)
            except MobileGatewayError as exc:
                status = exc.status_code
                payload = _mobile_error_payload(exc)
            except ValueError as exc:
                status = 400
                payload = {'schema_version': _SCHEMA_VERSION, 'status': 'error', 'error': _error_text(exc)}
            self._send_json(status, payload)

        def do_DELETE(self) -> None:  # noqa: N802 - stdlib hook
            try:
                status, payload = service.dispatch_delete(self.path, self.headers)
            except MobileGatewayError as exc:
                status = exc.status_code
                payload = _mobile_error_payload(exc)
            self._send_json(status, payload)

        def log_message(self, format: str, *args) -> None:  # noqa: A002 - stdlib signature
            return

        def _send_json(self, status: int, payload: dict[str, object]) -> None:
            body = json.dumps(payload, ensure_ascii=False, sort_keys=True).encode('utf-8')
            self.send_response(status)
            self.send_header('content-type', 'application/json; charset=utf-8')
            self.send_header('content-length', str(len(body)))
            self.end_headers()
            try:
                self.wfile.write(body)
            except (BrokenPipeError, ConnectionResetError):
                return

        def _send_notification_stream(self) -> None:
            once = service.notification_stream_once_from_path(self.path)
            try:
                events = service.notification_events_since(self.path, self.headers)
            except MobileGatewayError as exc:
                self._send_json(exc.status_code, _mobile_error_payload(exc))
                return
            self.send_response(200)
            self.send_header('content-type', 'text/event-stream; charset=utf-8')
            self.send_header('cache-control', 'no-cache')
            self.send_header('connection', 'close' if once else 'keep-alive')
            self.end_headers()
            last_event_id = self._write_notification_events(events)
            if once:
                self.close_connection = True
                return
            while True:
                try:
                    time.sleep(_NOTIFICATION_STREAM_POLL_SECONDS)
                    if _socket_peer_is_closed(self.connection):
                        return
                    events = service.notification_events_since(
                        self.path,
                        self.headers,
                        last_event_id=last_event_id,
                    )
                    next_id = self._write_notification_events(events)
                    if next_id is not None:
                        last_event_id = next_id
                    elif not self._write_sse_bytes(b': keepalive\n\n'):
                        return
                except (BrokenPipeError, ConnectionError, OSError):
                    return

        def _write_notification_events(self, events: list[dict[str, object]]) -> str | None:
            last_event_id = None
            for event in events:
                if not self._write_sse_bytes(encode_sse_event(event)):
                    return last_event_id
                last_event_id = str(event.get('id') or '') or last_event_id
            return last_event_id

        def _write_sse_bytes(self, body: bytes) -> bool:
            try:
                self.wfile.write(body)
                self.wfile.flush()
                return True
            except (BrokenPipeError, ConnectionError, OSError):
                return False

        def _send_bytes(self, status: int, body: bytes, headers: dict[str, str]) -> None:
            self.send_response(status)
            for key, value in headers.items():
                self.send_header(key, value)
            self.send_header('content-length', str(len(body)))
            self.end_headers()
            try:
                self.wfile.write(body)
            except (BrokenPipeError, ConnectionResetError):
                return

        def _read_json_body(self) -> dict[str, object]:
            length_text = self.headers.get('content-length') or '0'
            try:
                length = int(length_text)
            except ValueError as exc:
                raise ValueError('invalid content-length') from exc
            if length < 0 or length > 65536:
                raise ValueError('request body too large')
            raw = self.rfile.read(length) if length else b'{}'
            if not raw:
                return {}
            decoded = json.loads(raw.decode('utf-8'))
            if isinstance(decoded, dict):
                return {str(key): value for key, value in decoded.items()}
            raise ValueError('request body must be a JSON object')

        def _read_raw_body(self, *, max_bytes: int) -> bytes:
            length_text = self.headers.get('content-length') or '0'
            try:
                length = int(length_text)
            except ValueError as exc:
                raise ValueError('invalid content-length') from exc
            if length < 0 or length > max_bytes:
                raise ValueError('request body too large')
            return self.rfile.read(length) if length else b''

    class _GatewayServer(ThreadingHTTPServer):
        def server_close(self) -> None:
            service.close()
            super().server_close()

    return _GatewayServer((listen.host, listen.port), _Handler)


def _redact_project_view_payload(payload: dict[str, object]) -> dict[str, object]:
    redacted = json.loads(json.dumps(payload))
    view = redacted.get('view') if isinstance(redacted, dict) else None
    if isinstance(view, dict):
        namespace = view.get('namespace')
        if isinstance(namespace, dict):
            for key in _REDACTED_NAMESPACE_KEYS:
                namespace.pop(key, None)
    return redacted


def _project_activity_summary_from_view(payload: dict[str, object]) -> dict[str, object]:
    view = _map(payload.get('view'))
    agents = [_map(item) for item in _iterable(view.get('agents'))]
    working_agents = [agent for agent in agents if _agent_has_working_activity(agent)]
    timestamps: list[object] = []
    for agent in agents:
        timestamps.extend(
            [
                agent.get('last_progress_at'),
                agent.get('updated_at'),
                agent.get('created_at'),
            ]
        )
    content = _map(view.get('content'))
    for item in _iterable(content.get('items')):
        content_item = _map(item)
        timestamps.extend(
            [
                content_item.get('completed_at'),
                content_item.get('finished_at'),
                content_item.get('execution_completed_at'),
                content_item.get('sent_at'),
                content_item.get('updated_at'),
                content_item.get('created_at'),
            ]
        )
    for item in _iterable(view.get('comms')):
        comm = _map(item)
        timestamps.extend(
            [
                comm.get('completed_at'),
                comm.get('finished_at'),
                comm.get('sent_at'),
                comm.get('updated_at'),
                comm.get('created_at'),
            ]
        )
    summary: dict[str, object] = {}
    if working_agents:
        summary['has_working_agents'] = True
        summary['working_agent_count'] = len(working_agents)
    last_activity_at = _latest_mobile_timestamp(timestamps)
    if last_activity_at:
        summary['last_activity_at'] = last_activity_at
    return summary


def _sort_project_payloads_by_recent_activity(
    projects: list[dict[str, object]],
) -> list[dict[str, object]]:
    indexed = list(enumerate(projects))
    indexed.sort(key=lambda item: _project_payload_sort_key(item[0], item[1]))
    return [project for _, project in indexed]


def _project_payload_sort_key(index: int, project: dict[str, object]) -> tuple[float, int, int]:
    recent = _project_payload_recent_activity_at(project)
    if recent is not None:
        return (-recent.timestamp(), 0, index)
    has_working = bool(project.get('has_working_agents')) or (
        (_optional_int(project.get('working_agent_count')) or 0) > 0
    )
    return (float('inf'), 0 if has_working else 1, index)


def _project_payload_recent_activity_at(project: dict[str, object]) -> datetime | None:
    candidates = [
        _parse_mobile_conversation_timestamp(project.get('last_opened_at')),
        _parse_mobile_conversation_timestamp(project.get('last_activity_at')),
    ]
    parsed = [value for value in candidates if value is not None]
    if not parsed:
        return None
    return max(parsed)


def _project_activity_summary_from_record(record: dict[str, object]) -> dict[str, object]:
    summary: dict[str, object] = {}
    last_opened_at = _mobile_conversation_timestamp(record.get('last_opened_at'))
    if last_opened_at:
        summary['last_opened_at'] = last_opened_at
    last_activity_at = _mobile_conversation_timestamp(record.get('last_activity_at'))
    if last_activity_at:
        summary['last_activity_at'] = last_activity_at
    working_agent_count = _optional_int(record.get('working_agent_count')) or 0
    has_working_agents = bool(record.get('has_working_agents')) or working_agent_count > 0
    if has_working_agents:
        summary['has_working_agents'] = True
        summary['working_agent_count'] = working_agent_count
    return summary


def _project_activity_record_stale(
    record: dict[str, object],
    *,
    now_text: str,
    max_age_seconds: int,
) -> bool:
    checked_at = _parse_mobile_conversation_timestamp(record.get('summary_checked_at'))
    now = _parse_mobile_conversation_timestamp(now_text)
    if checked_at is None or now is None:
        return True
    return (now - checked_at).total_seconds() >= max_age_seconds


def _latest_mobile_timestamp(values: list[object]) -> str | None:
    latest: tuple[datetime, str] | None = None
    for value in values:
        text = _mobile_conversation_timestamp(value)
        parsed = _parse_mobile_conversation_timestamp(text)
        if text is None or parsed is None:
            continue
        if latest is None or parsed > latest[0]:
            latest = (parsed, text)
    return latest[1] if latest is not None else None


def _agent_has_working_activity(agent: dict[str, object]) -> bool:
    state = _normalized_text(agent.get('activity_state') or agent.get('state'))
    source = _normalized_text(agent.get('activity_source'))
    reason = _normalized_text(agent.get('activity_reason'))
    if state in {'active', 'busy', 'pending', 'running', 'start', 'starting', 'working'}:
        return True
    if state in {
        'idle',
        'free',
        'completed',
        'complete',
        'done',
        'failed',
        'failure',
        'error',
        'faulted',
        'offline',
        'crashed',
    }:
        return False
    text = f'{source or ""} {reason or ""}'
    return _int(agent.get('queue_depth'), 0) > 0 or any(
        marker in text
        for marker in (
            'queued',
            'reconnect',
            'running',
            'start',
            'submitted',
            'tool',
            'waiting',
            'working',
            'prompt',
        )
    )


def _normalized_text(value: object) -> str | None:
    text = str(value or '').strip().lower()
    return text or None


def _notification_snapshots_for_project(
    project: MobileGatewayProject,
    payload: dict[str, object],
    *,
    observed_at: str,
) -> list[MobileNotificationSnapshot]:
    view = _map(payload.get('view'))
    cache = _map(payload.get('cache'))
    project_record = _map(view.get('project'))
    namespace = _map(view.get('namespace'))
    namespace_epoch = _optional_int(namespace.get('epoch'))
    generated_at = _optional_text(cache.get('generated_at')) or observed_at
    project_short_name = (
        _optional_text(project_record.get('display_name'))
        or _optional_text(project_record.get('name'))
        or project.public_display_name
    )
    snapshots: list[MobileNotificationSnapshot] = []
    for item in _iterable(view.get('agents')):
        agent = _map(item)
        agent_name = _optional_text(agent.get('name'))
        activity_state = _optional_text(agent.get('activity_state'))
        if not agent_name or not activity_state:
            continue
        snapshots.append(
            MobileNotificationSnapshot(
                project_id=project.project_id,
                project_short_name=project_short_name,
                namespace_epoch=namespace_epoch,
                agent=agent_name,
                activity_state=activity_state.lower(),
                observed_at=generated_at,
            )
        )
    return snapshots


def _invalidation_snapshots_for_project(
    project: MobileGatewayProject,
    payload: dict[str, object],
    *,
    observed_at: str,
) -> list[MobileInvalidationSnapshot]:
    """Build a low-sensitive, per-agent change fingerprint for mobile SSE.

    File metadata is hashed before it leaves the gateway.  The app receives no
    path, terminal output, native transcript, or device credential in an
    invalidation event and follows it with its normal authenticated REST read.
    """
    view = _map(payload.get('view'))
    cache = _map(payload.get('cache'))
    project_record = _map(view.get('project'))
    namespace = _map(view.get('namespace'))
    namespace_epoch = _optional_int(namespace.get('epoch'))
    generated_at = _optional_text(cache.get('generated_at')) or observed_at
    project_short_name = (
        _optional_text(project_record.get('display_name'))
        or _optional_text(project_record.get('name'))
        or project.public_display_name
    )
    snapshots: list[MobileInvalidationSnapshot] = []
    for item in _iterable(view.get('agents')):
        agent_record = _map(item)
        agent_name = _optional_text(agent_record.get('name'))
        if not agent_name:
            continue
        provider = _optional_text(agent_record.get('provider'))
        fingerprint = _agent_native_conversation_cache_fingerprint(
            project.project_root,
            agent=agent_name,
            provider=provider,
        )
        fingerprint_text = hashlib.sha256(
            repr(fingerprint).encode('utf-8')
        ).hexdigest()[:24]
        snapshots.append(
            MobileInvalidationSnapshot(
                project_id=project.project_id,
                project_short_name=project_short_name,
                namespace_epoch=namespace_epoch,
                agent=agent_name,
                activity_state=(_optional_text(agent_record.get('activity_state')) or 'unknown').lower(),
                conversation_fingerprint=fingerprint_text,
                observed_at=generated_at,
            )
        )
    return snapshots


def _ccbd_health_summary(payload: dict[str, object]) -> dict[str, object]:
    return {
        'reachable': True,
        'project_id': payload.get('project_id'),
        'mount_state': payload.get('mount_state'),
        'health': payload.get('health'),
        'namespace_epoch': payload.get('namespace_epoch'),
        'namespace_ui_attachable': payload.get('namespace_ui_attachable'),
    }


def _project_available_for_mobile_list(payload: dict[str, object]) -> bool:
    return (
        str(payload.get('health') or '').strip().lower() == 'healthy'
        and str(payload.get('mount_state') or '').strip().lower() == 'mounted'
    )


def _project_health_refresh_failed(payload: dict[str, object]) -> bool:
    health = str(payload.get('health') or '').strip().lower()
    mount_state = str(payload.get('mount_state') or '').strip().lower()
    return health in {'', 'unknown', 'unreachable'} or mount_state in {'', 'unknown', 'unavailable'}


def _is_loopback_host(host: str) -> bool:
    normalized = host.strip().lower()
    return normalized in {'localhost', '127.0.0.1', '::1'}


def _is_private_lan_host(host: str) -> bool:
    try:
        address = ipaddress.ip_address(host.strip())
    except ValueError:
        return False
    return bool(
        (address.is_private or address.is_link_local)
        and not address.is_unspecified
        and not address.is_multicast
        and not address.is_reserved
    )


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace('+00:00', 'Z')


def _error_text(exc: Exception) -> str:
    return str(exc or '').strip() or type(exc).__name__


def _optional_text(value: object) -> str | None:
    text = str(value or '').strip()
    return text or None


def _header_text(headers: Mapping[str, object] | None, name: str) -> str:
    if headers is None:
        return ''
    getter = getattr(headers, 'get', None)
    value = getter(name) if callable(getter) else headers.get(name)
    if value is None and name.lower() != name:
        value = getter(name.lower()) if callable(getter) else headers.get(name.lower())
    return str(value or '').strip()


def _header_file_name(headers: Mapping[str, object] | None) -> str:
    encoded = _header_text(headers, 'X-Ccb-File-Name')
    if encoded:
        decoded = unquote(encoded).strip()
        if decoded:
            return decoded
    return 'attachment'


def _map(value: object) -> dict[str, object]:
    if isinstance(value, Mapping):
        return {str(key): item for key, item in value.items()}
    return {}


def _attachment_records(value: object) -> list[dict[str, object]]:
    records: list[dict[str, object]] = []
    if not isinstance(value, (list, tuple)):
        return records
    for item in value:
        record = _map(item)
        file_id = _optional_text(record.get('file_id')) or _optional_text(record.get('attachment_id'))
        if not file_id:
            continue
        file_name = _optional_text(record.get('file_name')) or _optional_text(record.get('filename')) or 'attachment'
        mime_type = _optional_text(record.get('mime_type')) or 'application/octet-stream'
        records.append(
            {
                'file_id': file_id,
                'file_name': file_name,
                'mime_type': mime_type,
                'size_bytes': _int(record.get('size_bytes'), 0),
                'kind': _optional_text(record.get('kind')) or ('image' if mime_type.startswith('image/') else 'document'),
                **(
                    {'project_relative_path': project_relative_path}
                    if (project_relative_path := _optional_text(record.get('project_relative_path')))
                    else {}
                ),
                **(
                    {'project_path': project_path}
                    if (project_path := _optional_text(record.get('project_path')))
                    else {}
                ),
            }
        )
    return records


def _message_submit_body(body: str, attachments: list[dict[str, object]]) -> str:
    attachment_body = _attachment_submit_body(attachments)
    if not body:
        return attachment_body
    if not attachments:
        return body
    return f'{body}\n\n{attachment_body}'


def _attachment_submit_body(attachments: list[dict[str, object]]) -> str:
    lines: list[str] = []
    for item in attachments:
        name = str(item.get('file_name') or 'attachment').strip() or 'attachment'
        mime_type = str(item.get('mime_type') or 'application/octet-stream')
        size_bytes = _int(item.get('size_bytes'), 0)
        file_id = str(item.get('file_id') or '').strip()
        project_relative_path = str(item.get('project_relative_path') or '').strip()
        if project_relative_path:
            lines.append(
                f'- [{name}]({project_relative_path}) '
                f'({mime_type}, {size_bytes} bytes, file id: {file_id})'
            )
        else:
            lines.append(f'- {name} ({mime_type}, {size_bytes} bytes, file id: {file_id})')
    if not lines:
        return 'Uploaded attachment'
    return 'Attached files:\n' + '\n'.join(lines)


def _mobile_project_upload_relative_path(*, agent: str, file_id: str, file_name: str) -> Path:
    return Path(*_MOBILE_PROJECT_UPLOAD_DIR) / _safe_path_segment(agent) / (
        f'{_safe_path_segment(file_id)}-{_safe_path_segment(file_name)}'
    )


def _workspace_artifact_relative_path_allowed(
    relative_path: Path,
    *,
    agent: str,
) -> bool:
    parts = relative_path.parts
    if not parts or parts[0] == '.git':
        return False
    if parts[0] != '.ccb':
        return True
    if parts[: len(_MOBILE_PROJECT_UPLOAD_DIR)] == _MOBILE_PROJECT_UPLOAD_DIR:
        return True
    try:
        workspace_prefix = ('.ccb', 'workspaces', _safe_path_segment(agent))
    except MobileGatewayError:
        return False
    workspace_parts = parts[len(workspace_prefix) :]
    return (
        parts[: len(workspace_prefix)] == workspace_prefix
        and bool(workspace_parts)
        and all(not part.startswith('.') for part in workspace_parts)
    )


def _safe_path_segment(value: object) -> str:
    text = str(value or '').strip()
    safe = ''.join(ch if ch.isalnum() or ch in {'-', '_', '.'} else '_' for ch in text)
    safe = safe.strip('._')
    if not safe:
        raise MobileGatewayError('invalid file identifier', status_code=400)
    return safe


def _read_file_metadata(directory: Path) -> dict[str, object]:
    try:
        payload = json.loads((directory / 'metadata.json').read_text(encoding='utf-8'))
    except Exception:
        return {}
    return _map(payload)


def _optional_int(value: object) -> int | None:
    if value is None:
        return None
    text = str(value).strip()
    return int(text) if text else None


def _int(value: object, fallback: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return fallback


def _parse_project_action_route(route: str) -> tuple[str, str] | None:
    prefix = '/v1/projects/'
    if not route.startswith(prefix):
        return None
    parts = route[len(prefix):].strip('/').split('/')
    if len(parts) != 2:
        return None
    project_id, action = parts
    if action not in {'focus-agent', 'focus-window', 'lifecycle', 'terminals'}:
        return None
    return unquote(project_id), action


def _parse_project_agent_route(route: str, *, suffix: str) -> tuple[str, str] | None:
    prefix = '/v1/projects/'
    if not route.startswith(prefix):
        return None
    parts = route[len(prefix):].strip('/').split('/')
    if len(parts) != 4 or parts[1] != 'agents' or parts[3] != suffix:
        return None
    project_id = unquote(parts[0]).strip()
    agent = unquote(parts[2]).strip()
    if not project_id or not agent:
        return None
    return project_id, agent


def _parse_project_agent_files_route(route: str) -> tuple[str, str] | None:
    prefix = '/v1/projects/'
    if not route.startswith(prefix):
        return None
    parts = route[len(prefix):].strip('/').split('/')
    if len(parts) != 4 or parts[1] != 'agents' or parts[3] != 'files':
        return None
    project_id = unquote(parts[0]).strip()
    agent = unquote(parts[2]).strip()
    if not project_id or not agent:
        return None
    return project_id, agent


def _parse_project_agent_file_route(route: str) -> tuple[str, str, str] | None:
    prefix = '/v1/projects/'
    if not route.startswith(prefix):
        return None
    parts = route[len(prefix):].strip('/').split('/')
    if len(parts) != 5 or parts[1] != 'agents' or parts[3] != 'files':
        return None
    project_id = unquote(parts[0]).strip()
    agent = unquote(parts[2]).strip()
    file_id = unquote(parts[4]).strip()
    if not project_id or not agent or not file_id:
        return None
    return project_id, agent, file_id


def _validate_agent_conversation_target(
    *,
    project_id: str,
    view_payload: dict[str, object],
    agent: str,
    namespace_epoch: int | None,
) -> dict[str, object]:
    view = _map(view_payload.get('view'))
    namespace = _map(view.get('namespace'))
    actual_epoch = _optional_int(namespace.get('epoch'))
    if actual_epoch is None:
        raise MobileGatewayError('ProjectView namespace epoch is required', status_code=409)
    if namespace_epoch != actual_epoch:
        raise MobileGatewayError('stale namespace epoch', status_code=409)
    agent_name = str(agent or '').strip()
    if not agent_name:
        raise MobileGatewayError('agent is required', status_code=400)
    agents = [_map(item) for item in _iterable(view.get('agents'))]
    matched = next((item for item in agents if str(item.get('name') or '') == agent_name), None)
    if matched is None:
        raise MobileGatewayError('unknown agent', status_code=404)
    return {
        'project_id': project_id,
        'agent': agent_name,
        'namespace_epoch': actual_epoch,
        'agent_record': matched,
    }


def _agent_conversation_items(
    view_payload: dict[str, object],
    *,
    project_id: str,
    agent: str,
    namespace_epoch: int,
    project_root: Path,
    mobile_files_dir: Path | None = None,
    limit: int | None = None,
    cursor: str | None = None,
) -> _ConversationItemsResult:
    view = _map(view_payload.get('view'))
    agents = [_map(item) for item in _iterable(view.get('agents'))]
    agent_record = next((item for item in agents if str(item.get('name') or '') == agent), {})
    provider_key = (_optional_text(agent_record.get('provider')) or '').strip().lower()
    native_items = _agent_native_conversation_items(
        project_root,
        project_id=project_id,
        agent=agent,
        provider=provider_key,
        mobile_files_dir=mobile_files_dir,
        limit=limit,
        cursor=cursor,
    )
    # Provider-native files are the authority whenever those providers are in
    # use, including an intentionally empty native history. Other providers do
    # not all expose a native transcript, so retain the safe structured CCB
    # records. Terminal scrollback is deliberately excluded from this path.
    if provider_key in {'', 'codex', 'claude', 'pi', 'omp'} or native_items.items:
        return native_items
    return _agent_structured_fallback_conversation_items(
        view_payload,
        project_root=project_root,
        project_id=project_id,
        agent=agent,
        mobile_files_dir=mobile_files_dir,
    )


def _agent_structured_fallback_conversation_items(
    view_payload: dict[str, object],
    *,
    project_root: Path,
    project_id: str,
    agent: str,
    mobile_files_dir: Path | None,
) -> _ConversationItemsResult:
    """Safe fallback for providers without a provider-native transcript.

    Only comms, completion snapshots, and durable job records participate.
    In particular this never reads ProjectView content as a terminal proxy and
    never invokes the explicit tmux-history surface.
    """
    view = _map(view_payload.get('view'))
    items: list[dict[str, object]] = []
    seen: set[str] = set()
    for item in _agent_history_conversation_items(
        project_root,
        project_id=project_id,
        agent=agent,
        mobile_files_dir=mobile_files_dir,
    ):
        item_id = str(item.get('id') or '')
        if item_id and item_id not in seen:
            items.append(item)
            seen.add(item_id)
    comms = [_map(item) for item in _iterable(view.get('comms'))]
    comms.sort(key=lambda item: (
        _optional_text(item.get('sent_at'))
        or _optional_text(item.get('created_at'))
        or _optional_text(item.get('updated_at'))
        or '',
        _optional_text(item.get('id')) or '',
    ))
    for comm in comms:
        if not _conversation_item_belongs_to_agent(comm, agent):
            continue
        comm_id = _optional_text(comm.get('id')) or f'comms-{len(items)}'
        body = (
            _optional_text(comm.get('body')) or _optional_text(comm.get('text'))
            or _optional_text(comm.get('message')) or _optional_text(comm.get('body_preview')) or ''
        )
        created_at = _first_mobile_conversation_timestamp(
            comm.get('sent_at'), comm.get('created_at'), comm.get('updated_at')
        )
        if body and f'user-{comm_id}' not in seen:
            user = {
                'id': f'user-{comm_id}', 'agent': agent, 'kind': 'user_message',
                'title': 'You', 'body': body,
                'format': _optional_text(comm.get('format')) or 'markdown',
                'source': 'comms', 'state': 'sent',
                'attachments': _attachment_records(comm.get('attachments')),
            }
            _apply_mobile_conversation_timing(user, sent_at=created_at)
            items.append(user)
            seen.add(str(user['id']))
        completion = _completion_reply_for_job(
            project_root,
            comm_id,
            project_id=project_id,
            agent=agent,
            mobile_files_dir=mobile_files_dir,
        )
        reply = _optional_text(completion.get('body')) or ''
        reply_id = f'reply-{comm_id}'
        if reply and reply_id not in seen:
            completed_at = _first_mobile_conversation_timestamp(
                completion.get('completed_at'), completion.get('sent_at'),
                comm.get('completed_at'), comm.get('finished_at'), comm.get('updated_at'), created_at,
            )
            response = {
                'id': reply_id, 'agent': agent, 'kind': 'agent_reply',
                'title': _optional_text(comm.get('title')) or 'Agent reply',
                'body': reply, 'format': 'markdown', 'source': 'completion_snapshot',
                'attachments': _attachment_records(completion.get('attachments')),
            }
            _apply_mobile_conversation_timing(
                response, sent_at=completed_at, started_at=completion.get('started_at') or created_at,
                completed_at=completed_at, duration_ms=completion.get('duration_ms'),
                duration_seconds=completion.get('duration_seconds'),
            )
            items.append(response)
            seen.add(reply_id)
    items.sort(key=lambda item: (
        _optional_text(item.get('sent_at')) or _optional_text(item.get('completed_at')) or '',
        0 if item.get('kind') == 'user_message' else 1,
        str(item.get('id') or ''),
    ))
    return _ConversationItemsResult(items)


def _agent_native_conversation_items(
    project_root: Path,
    *,
    project_id: str,
    agent: str,
    provider: str | None = None,
    mobile_files_dir: Path | None = None,
    limit: int | None = None,
    cursor: str | None = None,
) -> _ConversationItemsResult:
    provider_key = str(provider or '').strip().lower()
    if provider_key in {'', 'codex'}:
        codex_items = _codex_native_conversation_items(
            project_root,
            project_id=project_id,
            agent=agent,
            mobile_files_dir=mobile_files_dir,
            limit=limit,
            cursor=cursor,
        )
        if codex_items.items:
            return codex_items
    if provider_key in {'', 'claude'}:
        claude_items = _claude_native_conversation_items(
            project_root,
            project_id=project_id,
            agent=agent,
            mobile_files_dir=mobile_files_dir,
        )
        if provider_key == 'claude' or claude_items:
            return _ConversationItemsResult(claude_items)
    if provider_key in {'', 'pi'}:
        pi_items = _pi_family_native_conversation_items(
            project_root,
            project_id=project_id,
            agent=agent,
            provider='pi',
            mobile_files_dir=mobile_files_dir,
        )
        if provider_key == 'pi' or pi_items:
            return _ConversationItemsResult(pi_items)
    if provider_key in {'', 'omp'}:
        return _ConversationItemsResult(
            _pi_family_native_conversation_items(
                project_root,
                project_id=project_id,
                agent=agent,
                provider='omp',
                mobile_files_dir=mobile_files_dir,
            )
        )
    return _ConversationItemsResult([])


def _agent_native_conversation_cache_fingerprint(
    project_root: Path,
    *,
    agent: str,
    provider: str | None = None,
) -> tuple[tuple[str, int, int], ...]:
    provider_key = str(provider or '').strip().lower()
    if provider_key in {'', 'codex'}:
        codex_fingerprint = _codex_native_conversation_cache_fingerprint(
            project_root,
            agent=agent,
        )
        if codex_fingerprint:
            return codex_fingerprint
    if provider_key in {'', 'claude'}:
        claude_fingerprint = _claude_native_conversation_cache_fingerprint(
            project_root,
            agent=agent,
        )
        if claude_fingerprint:
            return claude_fingerprint
    if provider_key in {'', 'pi'}:
        pi_fingerprint = _pi_family_native_conversation_cache_fingerprint(
            project_root,
            agent=agent,
            provider='pi',
        )
        if pi_fingerprint:
            return pi_fingerprint
    if provider_key in {'', 'omp'}:
        omp_fingerprint = _pi_family_native_conversation_cache_fingerprint(
            project_root,
            agent=agent,
            provider='omp',
        )
        if omp_fingerprint:
            return omp_fingerprint
    return ()


def _conversation_file_fingerprint_entry(path: Path) -> tuple[str, int, int] | None:
    try:
        stat = path.stat()
    except Exception:
        return None
    return (str(path), int(stat.st_mtime_ns), int(stat.st_size))


def _copy_conversation_page(page: dict[str, object]) -> dict[str, object]:
    copied: dict[str, object] = {
        'items': [dict(item) for item in _iterable(page.get('items'))],
        'next_cursor': page.get('next_cursor'),
    }
    return copied


def _conversation_page_byte_size(page: dict[str, object]) -> int:
    try:
        return len(
            json.dumps(
                page,
                ensure_ascii=False,
                separators=(',', ':'),
            ).encode('utf-8')
        )
    except Exception:
        return _CONVERSATION_PAGE_CACHE_MAX_BYTES + 1


def _conversation_page_has_provider_native_items(page: dict[str, object]) -> bool:
    for item in _iterable(page.get('items')):
        source = _optional_text(_map(item).get('source')) or ''
        if source.startswith('provider_native/'):
            return True
    return False


def _pi_family_native_conversation_items(
    project_root: Path,
    *,
    project_id: str,
    agent: str,
    provider: str,
    mobile_files_dir: Path | None = None,
) -> list[dict[str, object]]:
    provider_key = str(provider or '').strip().lower()
    if provider_key not in {'pi', 'omp'}:
        return []
    session_paths = _pi_family_native_session_paths(
        project_root,
        agent=agent,
        provider=provider_key,
    )
    if not session_paths:
        return []
    file_roots = [
        path
        for path in (
            mobile_files_dir,
            project_root / '.ccb' / 'ccbd' / 'mobile' / 'files',
        )
        if path is not None
    ]
    items: list[dict[str, object]] = []
    for session_order, session_path in enumerate(session_paths):
        try:
            lines = session_path.open(encoding='utf-8-sig')
            fallback_timestamp = f'{int(session_path.stat().st_mtime):020d}'
        except Exception:
            continue
        parent_by_id: dict[str, str | None] = {}
        visible_by_id: dict[str, dict[str, object]] = {}
        visible_order: list[str] = []
        latest_record_id: str | None = None
        session_id = _native_id_part(session_path.stem, fallback='session')
        with lines:
            for line_number, line in enumerate(lines, start=1):
                line = line.strip()
                if not line:
                    continue
                try:
                    record = _map(json.loads(line))
                except Exception:
                    continue
                record_id = _optional_text(record.get('id'))
                if not record_id:
                    continue
                latest_record_id = record_id
                parent_by_id[record_id] = _optional_text(record.get('parentId'))
                if record.get('type') == 'session':
                    session_id = _native_id_part(record_id, fallback=session_id)
                    continue
                if record.get('type') != 'message':
                    continue
                message = _map(record.get('message'))
                role = str(message.get('role') or '').strip().lower()
                if role not in {'user', 'assistant'}:
                    continue
                body = _pi_message_content_text(message.get('content'))
                body = _inject_workspace_artifacts(
                    body,
                    project_root=project_root,
                    project_id=project_id,
                    agent=agent,
                    mobile_files_dir=mobile_files_dir,
                )
                body = _clean_native_message_text(body)
                if not body:
                    continue
                item_id = (
                    f'{provider_key}-{session_id}-{line_number}-'
                    f'{_native_id_part(record_id, fallback=role)}-{role}'
                )
                if role == 'user':
                    item = {
                        'id': item_id,
                        'agent': agent,
                        'kind': 'user_message',
                        'title': 'You',
                        'body': body,
                        'format': 'markdown',
                        'source': f'provider_native/{provider_key}',
                        'session_id': session_id,
                        'state': 'sent',
                        'attachments': [],
                    }
                else:
                    item = {
                        'id': item_id,
                        'agent': agent,
                        'kind': 'agent_reply',
                        'title': 'Agent reply',
                        'body': body,
                        'format': 'markdown',
                        'source': f'provider_native/{provider_key}',
                        'session_id': session_id,
                        'attachments': _artifact_link_attachments(
                            body,
                            file_roots=file_roots,
                            project_id=project_id,
                            agent=agent,
                        ),
                    }
                _set_native_sort_fields(
                    item,
                    record,
                    fallback_timestamp=fallback_timestamp,
                    thread_order=session_order,
                    line_number=line_number,
                )
                visible_by_id[record_id] = item
                visible_order.append(record_id)
        active_record_ids = _pi_active_record_ids(
            latest_record_id,
            parent_by_id=parent_by_id,
        )
        items.extend(
            visible_by_id[record_id]
            for record_id in visible_order
            if record_id in active_record_ids
        )
    sorted_items = [
        item
        for _, item in sorted(
            enumerate(items),
            key=lambda indexed: (
                _optional_text(indexed[1].get('_native_sort_timestamp')) or '',
                int(indexed[1].get('_native_thread_order') or 0),
                int(indexed[1].get('_native_line_number') or 0),
                indexed[0],
            ),
        )
    ]
    return [
        _without_native_sort_fields(item)
        for item in _coalesce_provider_native_agent_replies(
            sorted_items,
            source=f'provider_native/{provider_key}',
        )
    ]


def _pi_family_native_session_paths(
    project_root: Path,
    *,
    agent: str,
    provider: str,
) -> list[Path]:
    provider_key = str(provider or '').strip().lower()
    if provider_key not in {'pi', 'omp'}:
        return []
    session_dir = (
        project_root
        / '.ccb'
        / 'agents'
        / agent
        / 'provider-state'
        / provider_key
        / 'sessions'
    )
    if session_dir.is_symlink() or not session_dir.is_dir():
        return []
    authority = _pi_family_native_work_dir(
        project_root,
        agent=agent,
        provider=provider_key,
        session_dir=session_dir,
    )
    if authority is None:
        return []
    authoritative_work_dir = normalize_work_dir(authority)
    candidates: list[tuple[str, str, Path]] = []
    try:
        for path in session_dir.glob('*.jsonl'):
            if path.is_symlink() or not path.is_file():
                continue
            header = _pi_family_native_session_header(path)
            if not header:
                continue
            recorded_work_dir = _optional_text(header.get('cwd'))
            if (
                not recorded_work_dir
                or normalize_work_dir(recorded_work_dir) != authoritative_work_dir
            ):
                continue
            candidates.append((
                _optional_text(header.get('timestamp')) or '',
                path.name,
                path,
            ))
    except Exception:
        return []
    return [path for _, _, path in sorted(candidates)]


def _pi_family_native_session_header(path: Path) -> dict[str, object]:
    try:
        with path.open(encoding='utf-8-sig') as lines:
            for line_number, line in enumerate(lines, start=1):
                if line_number > 8:
                    break
                try:
                    record = _map(json.loads(line))
                except Exception:
                    return {}
                if record.get('type') == 'session':
                    return record
                if record.get('type') != 'title':
                    return {}
    except Exception:
        return {}
    return {}


def _pi_family_native_work_dir(
    project_root: Path,
    *,
    agent: str,
    provider: str,
    session_dir: Path,
) -> Path | None:
    binding_path = (
        project_root / '.ccb' / session_filename_for_agent(provider, agent)
    )
    if binding_path.is_symlink() or not binding_path.is_file():
        return None
    try:
        binding = _map(json.loads(binding_path.read_text(encoding='utf-8-sig')))
    except Exception:
        return None
    recorded_agent = _optional_text(binding.get('agent_name'))
    if recorded_agent and recorded_agent != agent:
        return None
    work_dir_values = [
        _optional_text(binding.get(key))
        for key in (
            ('pi_session_work_dir_norm', 'work_dir', 'workspace_path')
            if provider == 'pi'
            else ('work_dir', 'workspace_path')
        )
    ]
    normalized_values = {
        normalize_work_dir(value)
        for value in work_dir_values
        if value and normalize_work_dir(value)
    }
    if len(normalized_values) != 1:
        return None
    authoritative_work_dir = Path(next(iter(normalized_values)))
    if provider == 'pi':
        bound_path_text = _optional_text(binding.get('pi_session_path'))
        bound_session_id = _optional_text(binding.get('pi_session_id'))
        if bound_path_text:
            if not bound_session_id:
                return None
            bound_path = Path(bound_path_text).expanduser()
            if not bound_path.is_absolute():
                bound_path = session_dir / bound_path
            valid, _ = validate_native_session_binding(
                session_id=bound_session_id,
                session_path=bound_path,
                work_dir=authoritative_work_dir,
                session_dir=session_dir,
            )
            if not valid:
                return None
    return authoritative_work_dir


def _pi_family_native_conversation_cache_fingerprint(
    project_root: Path,
    *,
    agent: str,
    provider: str,
) -> tuple[tuple[str, int, int], ...]:
    return tuple(
        entry
        for path in _pi_family_native_session_paths(
            project_root,
            agent=agent,
            provider=provider,
        )
        if (entry := _conversation_file_fingerprint_entry(path)) is not None
    )


def _pi_active_record_ids(
    latest_record_id: str | None,
    *,
    parent_by_id: Mapping[str, str | None],
) -> set[str]:
    active: set[str] = set()
    current = latest_record_id
    while current and current not in active:
        active.add(current)
        current = parent_by_id.get(current)
    return active


def _pi_message_content_text(value: object) -> str:
    if isinstance(value, str):
        return value
    parts: list[str] = []
    for item in _iterable(value):
        content = _map(item)
        if content.get('type') != 'text':
            continue
        text = _optional_text(content.get('text')) or ''
        if text:
            parts.append(text)
    return '\n\n'.join(parts)


def _claude_native_conversation_items(
    project_root: Path,
    *,
    project_id: str,
    agent: str,
    mobile_files_dir: Path | None = None,
) -> list[dict[str, object]]:
    session_path = _claude_native_session_path(project_root, agent=agent)
    if session_path is None:
        return []
    try:
        from provider_backends.claude.comm_runtime.parsing_runtime.entries import (
            extract_message,
        )
    except Exception:
        return []
    file_roots = [
        path
        for path in (
            mobile_files_dir,
            project_root / '.ccb' / 'ccbd' / 'mobile' / 'files',
        )
        if path is not None
    ]
    try:
        lines = session_path.open(encoding='utf-8')
        fallback_timestamp = f'{int(session_path.stat().st_mtime):020d}'
    except Exception:
        return []
    items: list[dict[str, object]] = []
    session_id = _native_id_part(session_path.stem, fallback='session')
    with lines:
        for line_number, line in enumerate(lines, start=1):
            line = line.strip()
            if not line:
                continue
            try:
                record = _map(json.loads(line))
            except Exception:
                continue
            for role in ('user', 'assistant'):
                body = extract_message(record, role)
                if not body:
                    continue
                body = _inject_workspace_artifacts(
                    body,
                    project_root=project_root,
                    project_id=project_id,
                    agent=agent,
                    mobile_files_dir=mobile_files_dir,
                )
                body = _clean_provider_native_message_text(body)
                if not body:
                    continue
                item_id = (
                    f'claude-{session_id}-{line_number}-'
                    f'{_native_id_part(_claude_record_id(record), fallback=role)}-{role}'
                )
                if role == 'user':
                    item = {
                        'id': item_id,
                        'agent': agent,
                        'session_id': session_id,
                        'kind': 'user_message',
                        'title': 'You',
                        'body': body,
                        'format': 'markdown',
                        'source': 'provider_native/claude',
                        'state': 'sent',
                        'attachments': _artifact_link_attachments(
                            body,
                            file_roots=file_roots,
                            project_id=project_id,
                            agent=agent,
                        ),
                    }
                else:
                    item = {
                        'id': item_id,
                        'agent': agent,
                        'session_id': session_id,
                        'kind': 'agent_reply',
                        'title': 'Agent reply',
                        'body': body,
                        'format': 'markdown',
                        'source': 'provider_native/claude',
                        'attachments': _artifact_link_attachments(
                            body,
                            file_roots=file_roots,
                            project_id=project_id,
                            agent=agent,
                        ),
                    }
                _set_native_sort_fields(
                    item,
                    record,
                    fallback_timestamp=fallback_timestamp,
                    thread_order=0,
                    line_number=line_number,
                )
                items.append(item)
    sorted_items = [
        item
        for _, item in sorted(
            enumerate(items),
            key=lambda indexed: (
                _optional_text(indexed[1].get('_native_sort_timestamp')) or '',
                int(indexed[1].get('_native_line_number') or 0),
                indexed[0],
            ),
        )
    ]
    return [
        _without_native_sort_fields(item)
        for item in _coalesce_claude_native_agent_replies(sorted_items)
    ]


def _claude_native_session_path(project_root: Path, *, agent: str) -> Path | None:
    return resolve_provider_session_path(
        'claude',
        None,
        project_root=project_root,
        agent=agent,
    )


def _claude_native_conversation_cache_fingerprint(
    project_root: Path,
    *,
    agent: str,
) -> tuple[tuple[str, int, int], ...]:
    session_path = _claude_native_session_path(project_root, agent=agent)
    if session_path is None:
        return ()
    entry = _conversation_file_fingerprint_entry(session_path)
    return (entry,) if entry is not None else ()


def _claude_record_id(record: dict[str, object]) -> str:
    message = _map(record.get('message'))
    return (
        _optional_text(record.get('uuid'))
        or _optional_text(record.get('id'))
        or _optional_text(message.get('id'))
        or ''
    )


def _codex_native_conversation_items(
    project_root: Path,
    *,
    project_id: str,
    agent: str,
    mobile_files_dir: Path | None = None,
    limit: int | None = None,
    cursor: str | None = None,
) -> _ConversationItemsResult:
    home = project_root / '.ccb' / 'agents' / agent / 'provider-state' / 'codex' / 'home'
    state_path = home / 'state_5.sqlite'
    if not state_path.is_file():
        return _ConversationItemsResult([])
    try:
        connection = sqlite3.connect(f'file:{state_path}?mode=ro', uri=True)
        connection.row_factory = sqlite3.Row
        rows = list(
            connection.execute(
                'select id, rollout_path, created_at, updated_at from threads '
                'where rollout_path is not null and rollout_path != "" '
                'order by created_at asc, updated_at asc, id asc'
            )
        )
    except Exception:
        return _ConversationItemsResult([])
    finally:
        try:
            connection.close()  # type: ignore[possibly-undefined]
        except Exception:
            pass

    if cursor and cursor.startswith(_CODEX_NATIVE_CURSOR_PREFIX):
        if _codex_native_should_use_tail(rows, home):
            cursor_key = _decode_codex_native_cursor(cursor)
            if cursor_key is None:
                raise MobileGatewayError('cursor is invalid', status_code=400)
            return _codex_native_conversation_before_page_from_tail(
                rows,
                home=home,
                project_root=project_root,
                project_id=project_id,
                agent=agent,
                mobile_files_dir=mobile_files_dir,
                limit=limit or 50,
                cursor_key=cursor_key,
            )
        items = _codex_native_conversation_all_items(
            rows,
            home=home,
            project_root=project_root,
            project_id=project_id,
            agent=agent,
            mobile_files_dir=mobile_files_dir,
        )
        return _codex_native_conversation_before_page(
            items,
            limit=limit or 50,
            cursor=cursor,
        )

    if cursor is None and limit is not None and _codex_native_should_use_tail(rows, home):
        return _codex_native_conversation_latest_page(
            rows,
            home=home,
            project_root=project_root,
            project_id=project_id,
            agent=agent,
            mobile_files_dir=mobile_files_dir,
            limit=limit,
        )

    items = _codex_native_conversation_all_items(
        rows,
        home=home,
        project_root=project_root,
        project_id=project_id,
        agent=agent,
        mobile_files_dir=mobile_files_dir,
    )
    return _ConversationItemsResult([
        _without_native_sort_fields(item)
        for item in _coalesce_codex_native_agent_replies(items)
    ])


def _codex_native_conversation_cache_fingerprint(
    project_root: Path,
    *,
    agent: str,
) -> tuple[tuple[str, int, int], ...]:
    home = project_root / '.ccb' / 'agents' / agent / 'provider-state' / 'codex' / 'home'
    state_path = home / 'state_5.sqlite'
    state_entry = _conversation_file_fingerprint_entry(state_path)
    if state_entry is None:
        return ()
    try:
        connection = sqlite3.connect(f'file:{state_path}?mode=ro', uri=True)
        connection.row_factory = sqlite3.Row
        rows = list(
            connection.execute(
                'select id, rollout_path, created_at, updated_at from threads '
                'where rollout_path is not null and rollout_path != "" '
                'order by created_at asc, updated_at asc, id asc'
            )
        )
    except Exception:
        return ()
    finally:
        try:
            connection.close()  # type: ignore[possibly-undefined]
        except Exception:
            pass
    rollout_entries: list[tuple[str, int, int]] = []
    for row in rows:
        rollout_path = _codex_rollout_path(row, home=home)
        if rollout_path is None:
            continue
        entry = _conversation_file_fingerprint_entry(rollout_path)
        if entry is not None:
            rollout_entries.append(entry)
    if not rollout_entries:
        return ()
    return (state_entry, *rollout_entries)


def _codex_native_conversation_all_items(
    rows: list[sqlite3.Row],
    *,
    home: Path,
    project_root: Path,
    project_id: str,
    agent: str,
    mobile_files_dir: Path | None,
) -> list[dict[str, object]]:
    items: list[dict[str, object]] = []
    for row_index, row in enumerate(rows):
        thread_id = str(row['id'] or '').strip() or f'thread-{len(items)}'
        rollout_path = _codex_rollout_path(row, home=home)
        if rollout_path is None:
            continue
        fallback_timestamp = _codex_thread_fallback_timestamp(row)
        items.extend(
            _codex_rollout_conversation_items(
                rollout_path,
                project_root=project_root,
                project_id=project_id,
                agent=agent,
                thread_id=thread_id,
                thread_order=row_index,
                fallback_timestamp=fallback_timestamp,
                mobile_files_dir=mobile_files_dir,
                file_roots=[
                    path
                    for path in (
                        mobile_files_dir,
                        project_root / '.ccb' / 'ccbd' / 'mobile' / 'files',
                    )
                    if path is not None
                ],
            )
        )
    return _codex_sort_native_items(items)


def _codex_sort_native_items(
    items: list[dict[str, object]],
) -> list[dict[str, object]]:
    return [
        item
        for _, item in sorted(
            enumerate(items),
            key=lambda indexed: (
                _optional_text(indexed[1].get('_native_sort_timestamp')) or '',
                int(indexed[1].get('_native_thread_order') or 0),
                int(indexed[1].get('_native_line_number') or 0),
                indexed[0],
            ),
        )
    ]


def _codex_native_should_use_tail(rows: list[sqlite3.Row], home: Path) -> bool:
    if len(rows) > _CODEX_NATIVE_TAIL_THREAD_LIMIT:
        return True
    for row in rows:
        rollout_path = _codex_rollout_path(row, home=home)
        if rollout_path is None:
            continue
        try:
            if rollout_path.stat().st_size >= _CODEX_NATIVE_TAIL_FILE_BYTES:
                return True
        except Exception:
            continue
    return False


def _codex_native_conversation_latest_page(
    rows: list[sqlite3.Row],
    *,
    home: Path,
    project_root: Path,
    project_id: str,
    agent: str,
    mobile_files_dir: Path | None,
    limit: int,
) -> _ConversationItemsResult:
    items: list[dict[str, object]] = []
    total_chunk_count = 0
    has_older = False
    indexed_rows = list(enumerate(rows))
    latest_rows = sorted(
        indexed_rows,
        key=lambda indexed: (
            _codex_thread_timestamp_value(indexed[1]),
            str(indexed[1]['id'] or ''),
        ),
        reverse=True,
    )
    for row_index, row in latest_rows:
        if total_chunk_count >= _CODEX_NATIVE_TAIL_CHUNK_LIMIT:
            has_older = True
            break
        rollout_path = _codex_rollout_path(row, home=home)
        if rollout_path is None:
            continue
        before_offset: int | None = None
        chunk_count = 0
        while True:
            chunk_count += 1
            total_chunk_count += 1
            tail_lines, complete = _codex_rollout_tail_lines(
                rollout_path,
                line_limit=_CODEX_NATIVE_TAIL_LINE_LIMIT,
                before_offset=before_offset,
            )
            if not tail_lines:
                break
            items.extend(
                _codex_rollout_conversation_items(
                    rollout_path,
                    project_root=project_root,
                    project_id=project_id,
                    agent=agent,
                    thread_id=str(row['id'] or '').strip() or f'thread-{row_index}',
                    thread_order=row_index,
                    fallback_timestamp=_codex_thread_fallback_timestamp(row),
                    mobile_files_dir=mobile_files_dir,
                    file_roots=[
                        path
                        for path in (
                            mobile_files_dir,
                            project_root / '.ccb' / 'ccbd' / 'mobile' / 'files',
                        )
                        if path is not None
                    ],
                    line_records=tail_lines,
                )
            )
            coalesced_count = len(
                _coalesce_codex_native_agent_replies(_codex_sort_native_items(items))
            )
            if coalesced_count >= limit * 3:
                has_older = True
                break
            if complete:
                break
            has_older = True
            if (
                chunk_count >= _CODEX_NATIVE_TAIL_CHUNK_LIMIT
                or total_chunk_count >= _CODEX_NATIVE_TAIL_CHUNK_LIMIT
            ):
                break
            before_offset = tail_lines[0][0]
        if len(_coalesce_codex_native_agent_replies(_codex_sort_native_items(items))) >= limit * 3:
            break
    sorted_items = _codex_sort_native_items(items)
    coalesced = _coalesce_codex_native_agent_replies(sorted_items)
    start = max(0, len(coalesced) - limit)
    page_items = coalesced[start:]
    if start > 0:
        has_older = True
    next_cursor = _codex_native_before_cursor(page_items[0]) if has_older and page_items else None
    return _ConversationItemsResult(
        [_without_native_sort_fields(item) for item in page_items],
        already_paged=True,
        next_cursor=next_cursor,
    )


def _codex_native_conversation_before_page(
    sorted_items: list[dict[str, object]],
    *,
    limit: int,
    cursor: str,
) -> _ConversationItemsResult:
    cursor_key = _decode_codex_native_cursor(cursor)
    if cursor_key is None:
        raise MobileGatewayError('cursor is invalid', status_code=400)
    coalesced = _coalesce_codex_native_agent_replies(sorted_items)
    end = len(coalesced)
    for index, item in enumerate(coalesced):
        if _codex_native_sort_key(item) >= cursor_key:
            end = index
            break
    start = max(0, end - limit)
    page_items = coalesced[start:end]
    return _ConversationItemsResult(
        [_without_native_sort_fields(item) for item in page_items],
        already_paged=True,
        next_cursor=_codex_native_before_cursor(page_items[0]) if start > 0 and page_items else None,
    )


def _codex_native_conversation_before_page_from_tail(
    rows: list[sqlite3.Row],
    *,
    home: Path,
    project_root: Path,
    project_id: str,
    agent: str,
    mobile_files_dir: Path | None,
    limit: int,
    cursor_key: tuple[str, int, int, str],
) -> _ConversationItemsResult:
    items: list[dict[str, object]] = []
    indexed_rows = list(enumerate(rows))
    latest_rows = sorted(
        indexed_rows,
        key=lambda indexed: (
            _codex_thread_timestamp_value(indexed[1]),
            str(indexed[1]['id'] or ''),
        ),
        reverse=True,
    )
    started = False
    for row_index, row in latest_rows:
        if row_index == cursor_key[1]:
            before_offset: int | None = cursor_key[2]
            started = True
        elif not started:
            continue
        else:
            before_offset = None
        rollout_path = _codex_rollout_path(row, home=home)
        if rollout_path is None:
            continue
        while True:
            tail_lines, complete = _codex_rollout_tail_lines(
                rollout_path,
                line_limit=_CODEX_NATIVE_TAIL_LINE_LIMIT,
                before_offset=before_offset,
            )
            if not tail_lines:
                break
            items.extend(
                _codex_rollout_conversation_items(
                    rollout_path,
                    project_root=project_root,
                    project_id=project_id,
                    agent=agent,
                    thread_id=str(row['id'] or '').strip() or f'thread-{row_index}',
                    thread_order=row_index,
                    fallback_timestamp=_codex_thread_fallback_timestamp(row),
                    mobile_files_dir=mobile_files_dir,
                    file_roots=[
                        path
                        for path in (
                            mobile_files_dir,
                            project_root / '.ccb' / 'ccbd' / 'mobile' / 'files',
                        )
                        if path is not None
                    ],
                    line_records=tail_lines,
                )
            )
            coalesced_before = [
                item
                for item in _coalesce_codex_native_agent_replies(
                    _codex_sort_native_items(items)
                )
                if _codex_native_sort_key(item) < cursor_key
            ]
            if len(coalesced_before) >= limit * 3:
                break
            if complete:
                break
            before_offset = tail_lines[0][0]
        if len([
            item
            for item in _coalesce_codex_native_agent_replies(
                _codex_sort_native_items(items)
            )
            if _codex_native_sort_key(item) < cursor_key
        ]) >= limit * 3:
            break
    coalesced = [
        item
        for item in _coalesce_codex_native_agent_replies(
            _codex_sort_native_items(items)
        )
        if _codex_native_sort_key(item) < cursor_key
    ]
    start = max(0, len(coalesced) - limit)
    page_items = coalesced[start:]
    return _ConversationItemsResult(
        [_without_native_sort_fields(item) for item in page_items],
        already_paged=True,
        next_cursor=_codex_native_before_cursor(page_items[0]) if start > 0 and page_items else None,
    )


def _codex_rollout_path(row: sqlite3.Row, *, home: Path) -> Path | None:
    rollout_text = str(row['rollout_path'] or '').strip()
    if not rollout_text:
        return None
    rollout_path = Path(rollout_text)
    if not rollout_path.is_absolute():
        rollout_path = home / rollout_path
    return rollout_path


def _codex_thread_timestamp_value(row: sqlite3.Row) -> int:
    for key in ('updated_at', 'created_at'):
        try:
            return int(row[key] or 0)
        except Exception:
            continue
    return 0


def _codex_rollout_tail_lines(
    rollout_path: Path,
    *,
    line_limit: int,
    before_offset: int | None = None,
) -> tuple[list[tuple[int, str]], bool]:
    if line_limit <= 0:
        return [], False
    try:
        with rollout_path.open('rb') as handle:
            handle.seek(0, 2)
            file_size = handle.tell()
            end = min(before_offset if before_offset is not None else file_size, file_size)
            position = end
            chunks: list[bytes] = []
            newline_count = 0
            while position > 0 and newline_count <= line_limit:
                read_size = min(_CODEX_NATIVE_TAIL_READ_BLOCK_BYTES, position)
                position -= read_size
                handle.seek(position)
                chunk = handle.read(read_size)
                chunks.append(chunk)
                newline_count += chunk.count(b'\n')
    except Exception:
        return [], True
    if not chunks:
        return [], before_offset is None
    buffer = b''.join(reversed(chunks))
    start_offset = position
    complete = position == 0
    if position > 0:
        first_newline = buffer.find(b'\n')
        if first_newline < 0:
            return [], False
        start_offset += first_newline + 1
        buffer = buffer[first_newline + 1:]
    records: list[tuple[int, str]] = []
    offset = start_offset
    for raw_line in buffer.splitlines(keepends=True):
        line_offset = offset
        offset += len(raw_line)
        line = raw_line.rstrip(b'\r\n')
        if not line:
            continue
        try:
            records.append((line_offset, line.decode('utf-8')))
        except UnicodeDecodeError:
            continue
    if len(records) > line_limit:
        records = records[-line_limit:]
        complete = False
    return records, complete


def _codex_native_sort_key(item: dict[str, object]) -> tuple[str, int, int, str]:
    return (
        _optional_text(item.get('_native_sort_timestamp')) or '',
        int(item.get('_native_thread_order') or 0),
        int(item.get('_native_line_number') or 0),
        str(item.get('id') or ''),
    )


def _codex_native_before_cursor(item: dict[str, object]) -> str:
    payload = {
        'timestamp': _optional_text(item.get('_native_sort_timestamp')) or '',
        'thread_order': int(item.get('_native_thread_order') or 0),
        'line_number': int(item.get('_native_line_number') or 0),
        'id': str(item.get('id') or ''),
    }
    encoded = base64.urlsafe_b64encode(
        json.dumps(payload, separators=(',', ':')).encode('utf-8')
    ).decode('ascii').rstrip('=')
    return f'{_CODEX_NATIVE_CURSOR_PREFIX}{encoded}'


def _decode_codex_native_cursor(cursor: str) -> tuple[str, int, int, str] | None:
    if not cursor.startswith(_CODEX_NATIVE_CURSOR_PREFIX):
        return None
    encoded = cursor[len(_CODEX_NATIVE_CURSOR_PREFIX):]
    try:
        padded = encoded + ('=' * (-len(encoded) % 4))
        payload = _map(json.loads(base64.urlsafe_b64decode(padded).decode('utf-8')))
        return (
            _optional_text(payload.get('timestamp')) or '',
            int(payload.get('thread_order') or 0),
            int(payload.get('line_number') or 0),
            str(payload.get('id') or ''),
        )
    except Exception:
        return None


def _codex_thread_fallback_timestamp(row: sqlite3.Row) -> str:
    timestamp = row['updated_at'] or row['created_at'] or 0
    try:
        return f'{int(timestamp):020d}'
    except Exception:
        return str(timestamp)


def _codex_rollout_conversation_items(
    rollout_path: Path,
    *,
    project_root: Path,
    project_id: str,
    agent: str,
    thread_id: str,
    thread_order: int,
    fallback_timestamp: str,
    mobile_files_dir: Path | None,
    file_roots: list[Path],
    line_records: list[tuple[int, str]] | None = None,
) -> list[dict[str, object]]:
    event_items: list[dict[str, object]] = []
    event_user_lines: list[int] = []
    event_agent_lines: list[int] = []
    response_items: list[dict[str, object]] = []
    if line_records is None:
        try:
            lines = rollout_path.open(encoding='utf-8')
        except Exception:
            return []
        with lines:
            records = list(enumerate(lines, start=1))
    else:
        records = line_records
    for line_number, line in records:
        line = line.strip()
        if not line:
            continue
        try:
            record = _map(json.loads(line))
        except Exception:
            continue
        payload = _map(record.get('payload'))
        if record.get('type') == 'event_msg':
            if payload.get('type') == 'task_complete':
                completed_at = _native_record_timestamp(record) or fallback_timestamp
                _complete_latest_codex_native_agent_reply(event_items, completed_at)
                _complete_latest_codex_native_agent_reply(response_items, completed_at)
                continue
            event_item = _codex_event_message_conversation_item(
                payload,
                project_root=project_root,
                project_id=project_id,
                agent=agent,
                item_id=f'codex-{thread_id}-{line_number}',
                mobile_files_dir=mobile_files_dir,
                file_roots=file_roots,
            )
            if event_item is not None:
                event_item['session_id'] = thread_id
                _set_native_sort_fields(
                    event_item,
                    record,
                    fallback_timestamp=fallback_timestamp,
                    thread_order=thread_order,
                    line_number=line_number,
                    complete_agent_reply=False,
                )
                event_items.append(event_item)
                if event_item.get('kind') == 'user_message':
                    event_user_lines.append(line_number)
                elif event_item.get('kind') == 'agent_reply':
                    event_agent_lines.append(line_number)
            continue
        if record.get('type') != 'response_item':
            continue
        if payload.get('type') != 'message':
            continue
        role = str(payload.get('role') or '').strip()
        if role not in {'user', 'assistant'}:
            continue
        body = _codex_message_content_text(payload.get('content'))
        body = _inject_workspace_artifacts(
            body,
            project_root=project_root,
            project_id=project_id,
            agent=agent,
            mobile_files_dir=mobile_files_dir,
        )
        body = _clean_codex_native_message_text(body)
        if not body:
            continue
        item_id = f'codex-{thread_id}-{line_number}-{role}'
        if role == 'user':
            item = {
                'id': item_id,
                'agent': agent,
                'session_id': thread_id,
                'kind': 'user_message',
                'title': 'You',
                'body': body,
                'format': 'markdown',
                'source': 'provider_native/codex',
                'state': 'sent',
                'attachments': _artifact_link_attachments(
                    body,
                    file_roots=file_roots,
                    project_id=project_id,
                    agent=agent,
                ),
            }
        else:
            item = {
                'id': item_id,
                'agent': agent,
                'session_id': thread_id,
                'kind': 'agent_reply',
                'title': 'Agent reply',
                'body': body,
                'format': 'markdown',
                'source': 'provider_native/codex',
                'attachments': _artifact_link_attachments(
                    body,
                    file_roots=file_roots,
                    project_id=project_id,
                    agent=agent,
                ),
            }
        _set_native_sort_fields(
            item,
            record,
            fallback_timestamp=fallback_timestamp,
            thread_order=thread_order,
            line_number=line_number,
            complete_agent_reply=False,
        )
        response_items.append(item)
    if not event_items:
        return response_items
    visible_response_assistant_items = [
        item
        for item in response_items
        if item.get('kind') == 'agent_reply'
        and _codex_response_assistant_item_is_visible_with_event_items(
            item,
            event_user_lines=event_user_lines,
            event_agent_lines=event_agent_lines,
        )
    ]
    return _codex_sort_native_items([
        *event_items,
        *visible_response_assistant_items,
    ])


def _codex_response_assistant_item_is_visible_with_event_items(
    item: dict[str, object],
    *,
    event_user_lines: list[int],
    event_agent_lines: list[int],
) -> bool:
    try:
        line_number = int(item.get('_native_line_number') or 0)
    except Exception:
        line_number = 0
    lower_bound = max(
        (line for line in event_user_lines if line < line_number),
        default=0,
    )
    upper_bound = min(
        (line for line in event_user_lines if line > line_number),
        default=10**18,
    )
    return not any(
        lower_bound < line < upper_bound
        for line in event_agent_lines
    )


def _complete_latest_codex_native_agent_reply(
    items: list[dict[str, object]],
    completed_at: object,
) -> None:
    completed_timestamp = _mobile_conversation_timestamp(completed_at)
    if not completed_timestamp:
        return
    for item in reversed(items):
        if item.get('kind') != 'agent_reply':
            if item.get('kind') == 'user_message':
                return
            continue
        item['completed_at'] = completed_timestamp
        item['sent_at'] = completed_timestamp
        started_at = (
            _optional_text(item.get('started_at'))
            or _optional_text(item.get('created_at'))
        )
        if started_at:
            item.setdefault('started_at', started_at)
        duration_ms = _mobile_conversation_duration_ms(
            started_at,
            completed_timestamp,
        )
        if duration_ms is not None:
            item['duration_ms'] = duration_ms
        return


def _set_native_sort_fields(
    item: dict[str, object],
    record: dict[str, object],
    *,
    fallback_timestamp: str,
    thread_order: int,
    line_number: int,
    complete_agent_reply: bool = True,
) -> None:
    timestamp = _native_record_timestamp(record) or fallback_timestamp
    created_at = _mobile_conversation_timestamp(timestamp)
    item['_native_sort_timestamp'] = created_at or timestamp
    item['_native_thread_order'] = thread_order
    item['_native_line_number'] = line_number
    if created_at:
        item.setdefault('created_at', created_at)
        if item.get('kind') == 'user_message':
            item.setdefault('sent_at', created_at)
        elif item.get('kind') == 'agent_reply':
            item.setdefault('sent_at', created_at)
            if complete_agent_reply:
                item.setdefault('completed_at', created_at)


def _native_record_timestamp(record: dict[str, object]) -> str | None:
    payload = _map(record.get('payload'))
    message = _map(record.get('message'))
    return (
        _optional_text(record.get('timestamp'))
        or _optional_text(record.get('created_at'))
        or _optional_text(record.get('updated_at'))
        or _optional_text(payload.get('timestamp'))
        or _optional_text(payload.get('created_at'))
        or _optional_text(message.get('timestamp'))
        or _optional_text(message.get('created_at'))
    )


def _mobile_conversation_timestamp(value: object) -> str | None:
    text = _optional_text(value)
    if not text:
        return None
    if re.fullmatch(r'\d+(\.\d+)?', text):
        try:
            return (
                datetime.fromtimestamp(float(text), timezone.utc)
                .isoformat()
                .replace('+00:00', 'Z')
            )
        except Exception:
            return None
    if 'T' not in text:
        return None
    return text


def _mobile_conversation_duration_ms(
    started_at: object,
    completed_at: object,
) -> int | None:
    started = _parse_mobile_conversation_timestamp(started_at)
    completed = _parse_mobile_conversation_timestamp(completed_at)
    if started is None or completed is None:
        return None
    duration_ms = int((completed - started).total_seconds() * 1000)
    return duration_ms if duration_ms >= 0 else None


def _first_mobile_conversation_timestamp(*values: object) -> str | None:
    for value in values:
        timestamp = _mobile_conversation_timestamp(value)
        if timestamp:
            return timestamp
    return None


def _explicit_mobile_conversation_duration_ms(
    *,
    duration_ms: object = None,
    duration_seconds: object = None,
) -> int | None:
    if duration_ms is not None:
        try:
            value = int(duration_ms)
        except (TypeError, ValueError):
            value = None
        if value is not None and value >= 0:
            return value
    if duration_seconds is not None:
        try:
            value = int(float(duration_seconds) * 1000)
        except (TypeError, ValueError):
            value = None
        if value is not None and value >= 0:
            return value
    return None


def _apply_mobile_conversation_timing(
    item: dict[str, object],
    *,
    sent_at: object = None,
    started_at: object = None,
    completed_at: object = None,
    duration_ms: object = None,
    duration_seconds: object = None,
) -> None:
    sent_timestamp = _mobile_conversation_timestamp(sent_at)
    started_timestamp = _mobile_conversation_timestamp(started_at)
    completed_timestamp = _mobile_conversation_timestamp(completed_at)
    if sent_timestamp:
        item.setdefault('sent_at', sent_timestamp)
    if started_timestamp:
        item.setdefault('started_at', started_timestamp)
    if completed_timestamp:
        item.setdefault('completed_at', completed_timestamp)
        if item.get('kind') == 'agent_reply':
            item.setdefault('sent_at', completed_timestamp)
    explicit_duration = _explicit_mobile_conversation_duration_ms(
        duration_ms=duration_ms,
        duration_seconds=duration_seconds,
    )
    computed_duration = _mobile_conversation_duration_ms(
        started_timestamp,
        completed_timestamp,
    )
    final_duration = explicit_duration if explicit_duration is not None else computed_duration
    if final_duration is not None:
        item.setdefault('duration_ms', final_duration)


def _parse_mobile_conversation_timestamp(value: object) -> datetime | None:
    text = _mobile_conversation_timestamp(value)
    if not text:
        return None
    try:
        return datetime.fromisoformat(text.replace('Z', '+00:00'))
    except Exception:
        return None


def _without_native_sort_fields(item: dict[str, object]) -> dict[str, object]:
    clean = dict(item)
    clean.pop('_native_sort_timestamp', None)
    clean.pop('_native_thread_order', None)
    clean.pop('_native_line_number', None)
    return clean


def _coalesce_codex_native_agent_replies(
    items: list[dict[str, object]],
) -> list[dict[str, object]]:
    return _coalesce_provider_native_agent_replies(
        items,
        source='provider_native/codex',
    )


def _coalesce_claude_native_agent_replies(
    items: list[dict[str, object]],
) -> list[dict[str, object]]:
    return _coalesce_provider_native_agent_replies(
        items,
        source='provider_native/claude',
    )


def _coalesce_provider_native_agent_replies(
    items: list[dict[str, object]],
    *,
    source: str,
) -> list[dict[str, object]]:
    grouped: list[dict[str, object]] = []
    pending: dict[str, object] | None = None

    def flush_pending() -> None:
        nonlocal pending
        if pending is not None:
            grouped.append(pending)
            pending = None

    for item in items:
        if not _is_provider_native_agent_reply(item, source=source):
            flush_pending()
            grouped.append(item)
            continue
        if pending is None:
            pending = dict(item)
            continue
        if pending.get('_native_thread_order') != item.get('_native_thread_order'):
            flush_pending()
            pending = dict(item)
            continue
        pending['body'] = _join_native_agent_reply_bodies(
            _optional_text(pending.get('body')) or '',
            _optional_text(item.get('body')) or '',
        )
        pending['attachments'] = _merge_attachment_records(
            pending.get('attachments'),
            item.get('attachments'),
        )
        started_at = (
            _optional_text(pending.get('started_at'))
            or _optional_text(pending.get('created_at'))
            or _optional_text(pending.get('sent_at'))
        )
        completed_at = _optional_text(item.get('completed_at'))
        if started_at:
            pending['started_at'] = started_at
        if completed_at:
            pending['sent_at'] = completed_at
            pending['completed_at'] = completed_at
            duration_ms = _mobile_conversation_duration_ms(started_at, completed_at)
            if duration_ms is not None:
                pending['duration_ms'] = duration_ms
        pending['_native_line_number'] = item.get('_native_line_number')
        pending['_native_sort_timestamp'] = item.get('_native_sort_timestamp')
    flush_pending()
    return grouped


def _is_provider_native_agent_reply(
    item: dict[str, object],
    *,
    source: str,
) -> bool:
    return (
        item.get('kind') == 'agent_reply'
        and item.get('source') == source
    )


def _join_native_agent_reply_bodies(left: str, right: str) -> str:
    parts = [part.strip() for part in (left, right) if part.strip()]
    return '\n\n'.join(parts)


def _native_id_part(value: object, *, fallback: str) -> str:
    text = str(value or '').strip() or fallback
    safe = ''.join(ch if ch.isalnum() or ch in {'-', '_', '.'} else '_' for ch in text)
    safe = safe.strip('._')
    return safe or fallback


def _merge_attachment_records(*values: object) -> list[dict[str, object]]:
    merged: list[dict[str, object]] = []
    seen: set[str] = set()
    for value in values:
        for record in _attachment_records(value):
            key = (
                _optional_text(record.get('file_id'))
                or _optional_text(record.get('download_url'))
                or _optional_text(record.get('file_name'))
                or json.dumps(record, sort_keys=True)
            )
            if key in seen:
                continue
            seen.add(key)
            merged.append(record)
    return merged


def _codex_event_message_conversation_item(
    payload: dict[str, object],
    *,
    project_root: Path,
    project_id: str,
    agent: str,
    item_id: str,
    mobile_files_dir: Path | None,
    file_roots: list[Path],
) -> dict[str, object] | None:
    payload_type = str(payload.get('type') or '').strip()
    body = _optional_text(payload.get('message')) or ''
    body = _inject_workspace_artifacts(
        body,
        project_root=project_root,
        project_id=project_id,
        agent=agent,
        mobile_files_dir=mobile_files_dir,
    )
    body = _clean_codex_native_message_text(body)
    if not body:
        return None
    if payload_type == 'user_message':
        return {
            'id': f'{item_id}-user',
            'agent': agent,
            'kind': 'user_message',
            'title': 'You',
            'body': body,
            'format': 'markdown',
            'source': 'provider_native/codex',
            'state': 'sent',
            'attachments': _artifact_link_attachments(
                body,
                file_roots=file_roots,
                project_id=project_id,
                agent=agent,
            ),
        }
    if payload_type == 'agent_message':
        return {
            'id': f'{item_id}-assistant',
            'agent': agent,
            'kind': 'agent_reply',
            'title': 'Agent reply',
            'body': body,
            'format': 'markdown',
            'source': 'provider_native/codex',
            'attachments': _artifact_link_attachments(
                body,
                file_roots=file_roots,
                project_id=project_id,
                agent=agent,
            ),
        }
    return None


def _codex_message_content_text(value: object) -> str:
    parts: list[str] = []
    for item in _iterable(value):
        content = _map(item)
        text = (
            _optional_text(content.get('text'))
            or _optional_text(content.get('input_text'))
            or _optional_text(content.get('output_text'))
            or ''
        )
        if text:
            parts.append(text)
    return '\n\n'.join(parts)


_CCB_REQ_LINE_RE = re.compile(r'^\s*CCB_(?:REQ_ID|DONE):\s+\S+\s*$', re.IGNORECASE)


def _inject_workspace_artifacts(
    body: str,
    *,
    project_root: Path,
    project_id: str,
    agent: str,
    mobile_files_dir: Path | None,
) -> str:
    if not body:
        return body
    project_root_resolved = project_root.resolve(strict=False)
    mobile_file_root = (
        mobile_files_dir
        if mobile_files_dir is not None
        else project_root / '.ccb' / 'ccbd' / 'mobile' / 'files'
    )

    def repl(match: re.Match) -> str:
        text = match.group(1)
        url = match.group(2)
        parsed = urlparse(url)
        if url.startswith('#'):
            return match.group(0)
        if parsed.scheme not in {'', 'file'} and not (os.name == 'nt' and len(parsed.scheme) == 1):
            return match.group(0)
        if parsed.netloc not in {'', 'localhost'}:
            return match.group(0)
        try:
            if parsed.scheme == 'file':
                link_path = url2pathname(parsed.path)
            elif os.name == 'nt' and len(parsed.scheme) == 1:
                link_path = f'{parsed.scheme}:{unquote(parsed.path)}'
            else:
                link_path = unquote(parsed.path)
            if not link_path.strip():
                return match.group(0)
            target_path = Path(link_path).expanduser()
            if not target_path.is_absolute():
                target_path = project_root / target_path
            target_path = target_path.resolve(strict=False)
            try:
                relative_path = target_path.relative_to(project_root_resolved)
            except ValueError:
                relative_path = None
            if (
                relative_path is not None
                and not _workspace_artifact_relative_path_allowed(
                    relative_path,
                    agent=agent,
                )
            ):
                return match.group(0)
            target_stat = target_path.stat()
            if not stat.S_ISREG(target_stat.st_mode):
                return match.group(0)
            if target_stat.st_size > _MAX_MOBILE_DOWNLOAD_FILE_BYTES:
                return match.group(0)
            digest = _file_sha256(target_path)
            path_identity = (
                relative_path.as_posix()
                if relative_path is not None
                else target_path.as_posix()
            )
            identity = '\0'.join(
                (
                    project_id,
                    agent,
                    path_identity,
                    str(target_stat.st_size),
                    digest,
                )
            ).encode('utf-8')
            file_id = f'mobile-file-{hashlib.sha256(identity).hexdigest()[:24]}'
            mobile_file_dir = (
                mobile_file_root
                / _safe_path_segment(project_id)
                / _safe_path_segment(agent)
                / file_id
            )
            if not mobile_file_dir.is_dir():
                mobile_file_dir.mkdir(parents=True, exist_ok=True)
                content_path = mobile_file_dir / 'content.bin'
                shutil.copy2(target_path, content_path, follow_symlinks=True)
                mime_type = (
                    mimetypes.guess_type(target_path.name)[0]
                    or 'application/octet-stream'
                )
                record = {
                    'schema_version': _SCHEMA_VERSION,
                    'file_id': file_id,
                    'project_id': project_id,
                    'agent': agent,
                    'device_id': 'auto-injected',
                    'file_name': target_path.name,
                    'mime_type': mime_type,
                    'size_bytes': target_stat.st_size,
                    'sha256': digest,
                    'created_at': _utc_now(),
                }
                (mobile_file_dir / 'metadata.json').write_text(
                    json.dumps(record, ensure_ascii=False, sort_keys=True),
                    encoding='utf-8',
                )
            return f'[{text}](ccb-artifact://{file_id})'
        except Exception:
            return match.group(0)

    return re.sub(r'\[([^\]]+)\]\(([^)]+)\)', repl, body)


def _file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open('rb') as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b''):
            digest.update(chunk)
    return digest.hexdigest()


def _clean_native_message_text(text: str) -> str:
    lines: list[str] = []
    skipping_reply_guidance = False
    for line in str(text or '').splitlines():
        stripped = line.strip()
        if _CCB_REQ_LINE_RE.match(line):
            continue
        if stripped.startswith('CCB_REPLY_MODE:'):
            continue
        if stripped == 'CCB reply guidance:':
            skipping_reply_guidance = True
            continue
        if skipping_reply_guidance:
            if not stripped or stripped.startswith('- '):
                continue
            skipping_reply_guidance = False
        lines.append(line)
    return '\n'.join(lines).strip()


def _clean_provider_native_message_text(text: str) -> str:
    cleaned = _clean_native_message_text(text)
    return clean_provider_local_control_message(cleaned)


def _clean_codex_native_message_text(text: str) -> str:
    return _clean_provider_native_message_text(text)


def _agent_history_conversation_items(
    project_root: Path,
    *,
    project_id: str,
    agent: str,
    mobile_files_dir: Path | None = None,
) -> list[dict[str, object]]:
    latest_by_job: dict[str, dict[str, object]] = {}
    path = project_root / '.ccb' / 'agents' / agent / 'jobs.jsonl'
    try:
        lines = path.read_text(encoding='utf-8').splitlines()
    except Exception:
        return []
    for line in lines:
        line = line.strip()
        if not line:
            continue
        try:
            record = _map(json.loads(line))
        except Exception:
            continue
        job_id = _optional_text(record.get('job_id'))
        if not job_id:
            continue
        if not _job_record_belongs_to_agent(record, agent):
            continue
        latest_by_job[job_id] = record

    records = sorted(
        latest_by_job.values(),
        key=lambda item: (
            _optional_text(item.get('created_at'))
            or _optional_text(item.get('updated_at'))
            or '',
            _optional_text(item.get('job_id')) or '',
        ),
    )
    items: list[dict[str, object]] = []
    for record in records:
        status = str(record.get('status') or '').strip().lower()
        if status not in {'completed', 'cancelled', 'failed', 'incomplete'}:
            continue
        request = _map(record.get('request'))
        body = _optional_text(request.get('body')) or ''
        job_id = _optional_text(record.get('job_id')) or f'history-{len(items)}'
        started_at = _first_mobile_conversation_timestamp(
            record.get('started_at'),
            record.get('created_at'),
        )
        completed_at = _first_mobile_conversation_timestamp(
            record.get('completed_at'),
            record.get('finished_at'),
            record.get('updated_at'),
            started_at,
        )
        if body:
            user_item = {
                'id': f'user-{job_id}',
                'agent': agent,
                'kind': 'user_message',
                'title': 'You',
                'body': body,
                'format': 'markdown',
                'source': _history_source(record),
                'state': 'sent' if status == 'completed' else status,
                'attachments': [],
            }
            _apply_mobile_conversation_timing(user_item, sent_at=started_at)
            items.append(
                user_item
            )
        reply_dict = _completion_reply_from_history_job(
            project_root,
            record,
            project_id=project_id,
            agent=agent,
            mobile_files_dir=mobile_files_dir,
        )
        reply = str(reply_dict.get('body') or '')
        if reply:
            reply_started_at = _first_mobile_conversation_timestamp(
                reply_dict.get('started_at'),
                started_at,
            )
            reply_completed_at = _first_mobile_conversation_timestamp(
                reply_dict.get('completed_at'),
                reply_dict.get('sent_at'),
                completed_at,
            )
            reply_item = {
                'id': f'reply-{job_id}',
                'agent': agent,
                'kind': 'agent_reply',
                'title': 'Agent reply',
                'body': reply,
                'format': 'markdown',
                'source': 'completion_snapshot',
                'attachments': _attachment_records(reply_dict.get('attachments')),
            }
            _apply_mobile_conversation_timing(
                reply_item,
                sent_at=reply_completed_at,
                started_at=reply_started_at,
                completed_at=reply_completed_at,
                duration_ms=reply_dict.get('duration_ms'),
                duration_seconds=reply_dict.get('duration_seconds'),
            )
            items.append(
                reply_item
            )
    return items


def _job_record_belongs_to_agent(record: dict[str, object], agent: str) -> bool:
    request = _map(record.get('request'))
    candidates = (
        _optional_text(record.get('agent_name')),
        _optional_text(record.get('target_name')),
        _optional_text(request.get('to_agent')),
    )
    return agent in {item for item in candidates if item}


def _history_source(record: dict[str, object]) -> str:
    request = _map(record.get('request'))
    route_options = _map(request.get('route_options'))
    return _optional_text(route_options.get('source')) or 'desktop'


def _completion_reply_from_history_job(
    project_root: Path,
    record: dict[str, object],
    *,
    project_id: str,
    agent: str,
    mobile_files_dir: Path | None = None,
) -> dict[str, object]:
    job_id = _optional_text(record.get('job_id'))
    terminal_decision = _map(record.get('terminal_decision'))
    body = _optional_text(terminal_decision.get('reply')) or ''
    reply_dict = _completion_reply_for_job(
        project_root,
        job_id,
        project_id=project_id,
        agent=agent,
        mobile_files_dir=mobile_files_dir,
    )
    if not body:
        return reply_dict
    attachments = _attachment_records(_map(terminal_decision.get('payload')).get('attachments'))
    if not attachments:
        attachments = _attachment_records(reply_dict.get('attachments'))
    if not attachments:
        attachments = _artifact_link_attachments(
            body,
            file_roots=_mobile_file_roots_for_job(project_root, agent, job_id or ''),
            project_id=project_id,
            agent=agent,
        )
    return {'body': body, 'attachments': attachments}


def _agent_conversation_page(
    items: list[dict[str, object]],
    *,
    limit: int,
    cursor: str | None,
) -> dict[str, object]:
    if cursor is None:
        end = len(items)
    else:
        try:
            end = int(cursor)
        except ValueError as exc:
            raise MobileGatewayError('cursor must be an integer', status_code=400) from exc
        if end < 0 or end > len(items):
            raise MobileGatewayError('cursor is out of range', status_code=400)
    start = max(0, end - limit)
    return {
        'items': items[start:end],
        'next_cursor': str(start) if start > 0 else None,
    }


def _conversation_item_belongs_to_agent(item: dict[str, object], agent: str) -> bool:
    item_agent = (
        _optional_text(item.get('agent'))
        or _optional_text(item.get('agent_name'))
        or _optional_text(item.get('target'))
        or _optional_text(item.get('target_agent'))
    )
    if item_agent is not None:
        return item_agent == agent
    targets = item.get('target_agents')
    if isinstance(targets, (list, tuple, set)):
        return agent in {str(target) for target in targets}
    return True


def _completion_reply_for_job(
    project_root: Path,
    job_id: str | None,
    *,
    project_id: str,
    agent: str,
    mobile_files_dir: Path | None = None,
) -> dict[str, object]:
    if not job_id:
        return {'body': '', 'attachments': []}
    path = project_root / '.ccb' / 'ccbd' / 'snapshots' / f'{job_id}.json'
    try:
        payload = json.loads(path.read_text(encoding='utf-8'))
    except Exception:
        return {'body': '', 'attachments': []}
    latest_decision = _map(payload.get('latest_decision'))
    body = (
        _optional_text(latest_decision.get('reply'))
        or _optional_text(payload.get('latest_reply_preview'))
        or ''
    )
    body = _inject_workspace_artifacts(
        body,
        project_root=project_root,
        project_id=project_id,
        agent=agent,
        mobile_files_dir=mobile_files_dir,
    )
    started_at = _first_mobile_conversation_timestamp(
        latest_decision.get('started_at'),
        latest_decision.get('execution_started_at'),
        payload.get('started_at'),
        payload.get('execution_started_at'),
        payload.get('created_at'),
    )
    completed_at = _first_mobile_conversation_timestamp(
        latest_decision.get('completed_at'),
        latest_decision.get('finished_at'),
        latest_decision.get('execution_completed_at'),
        latest_decision.get('created_at'),
        payload.get('completed_at'),
        payload.get('finished_at'),
        payload.get('execution_completed_at'),
        payload.get('updated_at'),
        payload.get('created_at'),
    )
    duration_ms = (
        _explicit_mobile_conversation_duration_ms(
            duration_ms=latest_decision.get('duration_ms') or payload.get('duration_ms'),
            duration_seconds=(
                latest_decision.get('duration_seconds')
                or payload.get('duration_seconds')
            ),
        )
        or _mobile_conversation_duration_ms(started_at, completed_at)
    )

    attachments = []
    payload_obj = _map(latest_decision.get('payload'))
    if 'attachments' in payload_obj:
        attachments = _attachment_records(payload_obj.get('attachments'))
    if not attachments:
        attachments = _artifact_link_attachments(
            body,
            file_roots=[
                path
                for path in (
                    mobile_files_dir,
                    *_mobile_file_roots_for_job(project_root, agent, job_id),
                )
                if path is not None
            ],
            project_id=project_id,
            agent=agent,
        )
    result: dict[str, object] = {'body': body, 'attachments': attachments}
    if started_at:
        result['started_at'] = started_at
    if completed_at:
        result['sent_at'] = completed_at
        result['completed_at'] = completed_at
    if duration_ms is not None:
        result['duration_ms'] = duration_ms
    return result


def _artifact_link_attachments(
    body: str,
    *,
    file_roots: list[Path],
    project_id: str,
    agent: str,
) -> list[dict[str, object]]:
    attachments: list[dict[str, object]] = []
    seen: set[str] = set()
    for file_id in _artifact_file_ids(body):
        if file_id in seen:
            continue
        seen.add(file_id)
        for file_root in file_roots:
            try:
                directory = (
                    file_root
                    / _safe_path_segment(project_id)
                    / _safe_path_segment(agent)
                    / _safe_path_segment(file_id)
                )
            except MobileGatewayError:
                continue
            metadata = _read_file_metadata(directory)
            if not metadata:
                continue
            attachments.extend(_attachment_records([metadata]))
            break
    return attachments


def _mobile_file_roots_for_job(project_root: Path, agent: str, job_id: str) -> list[Path]:
    roots: list[Path] = [project_root / '.ccb' / 'ccbd' / 'mobile' / 'files']
    jobs_path = project_root / '.ccb' / 'agents' / agent / 'jobs.jsonl'
    try:
        lines = jobs_path.read_text(encoding='utf-8').splitlines()
    except Exception:
        return roots
    for line in lines:
        try:
            record = json.loads(line)
        except Exception:
            continue
        if str(_map(record).get('job_id') or '') != job_id:
            continue
        request = _map(_map(record).get('request'))
        route_options = _map(request.get('route_options'))
        mobile_files_dir = _optional_text(route_options.get('mobile_files_dir'))
        if mobile_files_dir:
            path = Path(mobile_files_dir).expanduser()
            if path not in roots:
                roots.insert(0, path)
    return roots


def _artifact_file_ids(body: str) -> list[str]:
    if not body:
        return []
    return [
        match.group(1)
        for match in re.finditer(r'ccb-artifact://([A-Za-z0-9._-]+)', body)
    ]


def _agent_status_summary(agent: dict[str, object]) -> str:
    state = _optional_text(agent.get('state')) or 'idle'
    health = _optional_text(agent.get('runtime_health')) or 'unknown'
    queue_depth = _int(agent.get('queue_depth'), 0)
    if queue_depth > 0:
        suffix = '' if queue_depth == 1 else 's'
        return f'{state}, {health}, {queue_depth} queued item{suffix}'
    return f'{state}, {health}, no queued items'


def _validate_terminal_summary(
    record: dict[str, object],
    view: dict[str, object],
    *,
    herdr_summary: dict[str, object] | None = None,
) -> None:
    summary = _map(record.get('target_summary'))
    agent = _optional_text(summary.get('agent'))
    window = _optional_text(summary.get('window'))
    agents = [_map(item) for item in _iterable(view.get('agents'))]
    windows = [_map(item) for item in _iterable(view.get('windows'))]
    if agent and not any(str(item.get('name') or '') == agent for item in agents):
        raise MobileGatewayError('unknown terminal target agent', status_code=404)
    if window and not any(str(item.get('name') or '') == window for item in windows):
        raise MobileGatewayError('unknown terminal target window', status_code=404)
    pane_id = _optional_text(summary.get('pane_id'))
    if pane_id and agent:
        matched = next((item for item in agents if str(item.get('name') or '') == agent), None)
        if matched is None or _optional_text(matched.get('pane_id')) != pane_id:
            herdr_pane_id = _optional_text(_map(herdr_summary).get('pane_id'))
            if herdr_pane_id == pane_id:
                return
            raise MobileGatewayError('unknown terminal target pane', status_code=404)


def _terminal_summary_pane_id(
    summary: dict[str, object],
    view: dict[str, object],
) -> str | None:
    pane_id = _optional_text(summary.get('pane_id'))
    if pane_id:
        return pane_id
    agent = _optional_text(summary.get('agent'))
    agents = [_map(item) for item in _iterable(view.get('agents'))]
    if agent:
        matched_agent = next(
            (item for item in agents if str(item.get('name') or '') == agent),
            None,
        )
        pane_id = _optional_text(_map(matched_agent).get('pane_id'))
        if pane_id:
            return pane_id
    window = _optional_text(summary.get('window'))
    windows = [_map(item) for item in _iterable(view.get('windows'))]
    if window:
        matched_window = next(
            (item for item in windows if str(item.get('name') or '') == window),
            None,
        )
        pane_id = _optional_text(_map(matched_window).get('active_pane_id'))
        if pane_id:
            return pane_id
    namespace = _map(view.get('namespace'))
    return _optional_text(namespace.get('active_pane_id'))


def _validate_terminal_target(
    project_id: str,
    view_payload: dict[str, object],
    *,
    target: dict[str, object],
    namespace_epoch: int | None,
) -> dict[str, object]:
    view = _map(view_payload.get('view'))
    namespace = _map(view.get('namespace'))
    actual_epoch = _optional_int(namespace.get('epoch'))
    if actual_epoch is None:
        raise MobileGatewayError('ProjectView namespace epoch is required', status_code=409)
    if namespace_epoch != actual_epoch:
        raise MobileGatewayError('stale namespace epoch', status_code=409)
    socket_path = _optional_text(namespace.get('socket_path'))
    session_name = _optional_text(namespace.get('session_name'))

    kind = str(target.get('kind') or '').strip()
    agent = _optional_text(target.get('agent'))
    window = _optional_text(target.get('window'))
    pane_id = _optional_text(target.get('pane_id'))
    agents = [_map(item) for item in _iterable(view.get('agents'))]
    windows = [_map(item) for item in _iterable(view.get('windows'))]

    if kind == 'agent':
        if not agent:
            raise MobileGatewayError('terminal target agent is required', status_code=400)
        matched = next((item for item in agents if str(item.get('name') or '') == agent), None)
        if matched is None:
            raise MobileGatewayError('unknown terminal target agent', status_code=404)
        matched_window = _optional_text(matched.get('window')) or window
        if window and matched_window and window != matched_window:
            raise MobileGatewayError('terminal target window does not match agent', status_code=409)
        matched_pane_id = _optional_text(matched.get('pane_id')) or pane_id
        herdr_summary = _herdr_terminal_target_summary(view, agent=agent, operation='attach')
        if not socket_path or not session_name:
            if herdr_summary is None:
                blocked = _terminal_operation_blocked_payload(
                    view_payload,
                    agent=agent,
                    namespace_epoch=namespace_epoch,
                    operation='attach',
                )
                if blocked is not None:
                    raise _TerminalBlockedError(blocked)
                raise MobileGatewayError('ProjectView tmux evidence is not attachable', status_code=409)
        return {
            'target_epoch': actual_epoch,
            'target_summary': {
                'project_id': project_id,
                'agent': agent,
                'window': matched_window,
                **({'pane_id': matched_pane_id} if matched_pane_id else {}),
                **(herdr_summary or {}),
            },
        }
    if kind == 'window_active_pane':
        if not window:
            raise MobileGatewayError('terminal target window is required', status_code=400)
        matched_window = next((item for item in windows if str(item.get('name') or '') == window), None)
        if matched_window is None:
            raise MobileGatewayError('unknown terminal target window', status_code=404)
        herdr_summary = _herdr_terminal_target_summary(view, agent=None, operation='attach')
        if herdr_summary is not None:
            herdr_pane_id = _optional_text(herdr_summary.get('pane_id'))
            active_pane_id = (
                _optional_text(matched_window.get('active_pane_id'))
                or (
                    _optional_text(namespace.get('active_pane_id'))
                    if _optional_text(namespace.get('active_window')) == window
                    else None
                )
            )
            if not active_pane_id or active_pane_id != herdr_pane_id:
                raise _TerminalBlockedError(
                    _herdr_terminal_target_blocked_payload('window-active-pane-does-not-match-herdr-pane')
                )
        if not socket_path or not session_name:
            if herdr_summary is None:
                blocked = _terminal_operation_blocked_payload(
                    view_payload,
                    agent=None,
                    namespace_epoch=namespace_epoch,
                    operation='attach',
                )
                if blocked is not None:
                    raise _TerminalBlockedError(blocked)
                raise MobileGatewayError('ProjectView tmux evidence is not attachable', status_code=409)
        return {
            'target_epoch': actual_epoch,
            'target_summary': {
                'project_id': project_id,
                'window': window,
                **(herdr_summary or {}),
            },
        }
    if kind == 'pane_evidence':
        if not agent and not window:
            raise MobileGatewayError('pane evidence must include agent or window', status_code=400)
        if agent and not any(str(item.get('name') or '') == agent for item in agents):
            raise MobileGatewayError('unknown terminal target agent', status_code=404)
        if window and not any(str(item.get('name') or '') == window for item in windows):
            raise MobileGatewayError('unknown terminal target window', status_code=404)
        herdr_summary = _herdr_terminal_target_summary(view, agent=agent, operation='attach')
        if herdr_summary is not None:
            herdr_pane_id = _optional_text(herdr_summary.get('pane_id'))
            if not pane_id or pane_id != herdr_pane_id:
                raise _TerminalBlockedError(
                    _herdr_terminal_target_blocked_payload('pane-evidence-does-not-match-herdr-pane')
                )
        if not socket_path or not session_name:
            if herdr_summary is None:
                blocked = _terminal_operation_blocked_payload(
                    view_payload,
                    agent=agent,
                    namespace_epoch=namespace_epoch,
                    operation='attach',
                )
                if blocked is not None:
                    raise _TerminalBlockedError(blocked)
                raise MobileGatewayError('ProjectView tmux evidence is not attachable', status_code=409)
        summary = {'project_id': project_id}
        if agent:
            summary['agent'] = agent
        if window:
            summary['window'] = window
        if pane_id:
            summary['pane_id'] = pane_id
        summary.update(herdr_summary or {})
        return {
            'target_epoch': actual_epoch,
            'target_summary': summary,
        }
    raise MobileGatewayError('unknown terminal target kind', status_code=400)


def _terminal_operation_blocked_payload(
    view_payload: dict[str, object],
    *,
    agent: str | None,
    namespace_epoch: int | None,
    operation: str,
) -> dict[str, object] | None:
    view = _map(view_payload.get('view'))
    namespace = _map(view.get('namespace'))
    actual_epoch = _optional_int(namespace.get('epoch'))
    if actual_epoch is None or namespace_epoch != actual_epoch:
        return None
    projection = _herdr_terminal_projection(view, agent=agent)
    if projection is None:
        return None
    if _herdr_terminal_operation_supported(projection):
        return None
    code = {
        'attach': 'attach_unsupported',
        'history': 'history_unsupported',
        'input': 'input_unsupported',
    }.get(operation, 'target_not_attachable')
    capability_status = _optional_text(projection.get('capability_status')) or 'blocked'
    if capability_status == 'supported':
        capability_status = 'blocked'
    beta_gaps = _text_list(projection.get('beta_gaps'))
    blocking_gaps = _text_list(projection.get('blocking_gaps')) or [code]
    next_action = _optional_text(projection.get('degraded_next_action'))
    message = (
        f'Herdr mobile terminal {operation} is not available; '
        f'next_action={next_action or "collect-validation-transcript"}'
    )
    return {
        'code': code,
        'backend_impl': 'herdr',
        'capability_status': capability_status,
        'degraded_next_action': next_action,
        'message': message,
        'beta_gaps': beta_gaps,
        'blocking_gaps': blocking_gaps,
    }


def _herdr_terminal_target_blocked_payload(reason: str) -> dict[str, object]:
    return {
        'code': 'target_not_attachable',
        'backend_impl': 'herdr',
        'capability_status': 'blocked',
        'degraded_next_action': 'collect-validation-transcript',
        'message': f'Herdr mobile terminal target is not attachable; reason={reason}',
        'beta_gaps': [],
        'blocking_gaps': [reason],
    }


def _herdr_terminal_projection(view: dict[str, object], *, agent: str | None) -> dict[str, object] | None:
    agent_name = _optional_text(agent)
    if agent_name:
        for item in _iterable(view.get('agents')):
            record = _map(item)
            if _optional_text(record.get('name')) != agent_name:
                continue
            projection = _map(record.get('herdr_surface_projection'))
            if projection.get('backend_impl') == 'herdr':
                return projection
    namespace = _map(view.get('namespace'))
    projection = _map(namespace.get('herdr_surface_projection'))
    if projection.get('backend_impl') == 'herdr':
        return projection
    if namespace.get('namespace_backend_impl') == 'herdr':
        return {
            'backend_impl': 'herdr',
            'capability_status': 'blocked',
            'beta_gaps': ['mobile-terminal-validation-pending'],
            'blocking_gaps': ['mobile-terminal-adapter-unavailable'],
            'degraded_next_action': 'collect-validation-transcript',
        }
    return None


def _herdr_terminal_operation_supported(projection: dict[str, object]) -> bool:
    return herdr_surface_projection_passes_gate(projection)


def _herdr_terminal_target_summary(
    view: dict[str, object],
    *,
    agent: str | None,
    operation: str,
) -> dict[str, object] | None:
    projection = _herdr_terminal_projection(view, agent=agent)
    if projection is None or not _herdr_terminal_operation_supported(projection):
        return None
    refs = _map(projection.get('evidence_refs'))
    namespace_ref = _map(refs.get('namespace_ref'))
    pane_ref = _map(refs.get('pane_ref'))
    if not namespace_ref or not pane_ref:
        return None
    pane_id = _optional_text(pane_ref.get('pane_id'))
    return {
        'backend_impl': 'herdr',
        'namespace_ref': dict(namespace_ref),
        'pane_ref': dict(pane_ref),
        'pane_id': pane_id,
        'session_name': _optional_text(namespace_ref.get('session_name')),
        'attach_supported': True,
        'history_supported': True,
        'input_supported': True,
        'blocked_reason': None,
    }


def _terminal_target_kwargs(summary: dict[str, object] | None) -> dict[str, object]:
    if not summary:
        return {}
    return {
        'backend_impl': _optional_text(summary.get('backend_impl')) or 'tmux',
        'namespace_ref': _map(summary.get('namespace_ref')) or None,
        'pane_ref': _map(summary.get('pane_ref')) or None,
        'attach_supported': bool(summary.get('attach_supported')),
        'history_supported': bool(summary.get('history_supported')),
        'input_supported': bool(summary.get('input_supported')),
        'blocked_reason': _optional_text(summary.get('blocked_reason')),
    }


def _text_list(value: object) -> list[str]:
    if isinstance(value, (str, bytes)):
        values = [value]
    elif isinstance(value, (list, tuple, set)):
        values = list(value)
    else:
        values = []
    result: list[str] = []
    for item in values:
        text = _optional_text(item)
        if text and text not in result:
            result.append(text)
    return result


def _terminal_history_target(
    *,
    project_id: str,
    view_payload: dict[str, object],
    agent: str | None,
    namespace_epoch: int | None,
    max_lines: int,
) -> TerminalHistoryTarget:
    view = _map(view_payload.get('view'))
    namespace = _map(view.get('namespace'))
    actual_epoch = _optional_int(namespace.get('epoch'))
    if actual_epoch is None:
        raise MobileGatewayError('ProjectView namespace epoch is required', status_code=409)
    if namespace_epoch != actual_epoch:
        raise MobileGatewayError('stale namespace epoch', status_code=409)
    socket_path = _optional_text(namespace.get('socket_path'))
    session_name = _optional_text(namespace.get('session_name'))
    agent_name = str(agent or '').strip()
    if not agent_name:
        raise MobileGatewayError('agent is required', status_code=400)
    agents = [_map(item) for item in _iterable(view.get('agents'))]
    matched = next((item for item in agents if str(item.get('name') or '') == agent_name), None)
    if matched is None:
        raise MobileGatewayError('unknown terminal target agent', status_code=404)
    pane_id = _optional_text(matched.get('pane_id'))
    herdr_summary = _herdr_terminal_target_summary(view, agent=agent_name, operation='history')
    if not socket_path or not session_name:
        if herdr_summary is None:
            raise MobileGatewayError('ProjectView tmux evidence is not attachable', status_code=409)
    if not pane_id and herdr_summary is not None:
        pane_id = _optional_text(herdr_summary.get('pane_id'))
    if not pane_id:
        raise MobileGatewayError('terminal history target has no pane evidence', status_code=409)
    return TerminalHistoryTarget(
        project_id=project_id,
        namespace_epoch=actual_epoch,
        agent=agent_name,
        window=_optional_text(matched.get('window')) or '',
        pane_id=pane_id,
        socket_path=socket_path or '',
        session_name=session_name or _optional_text(_map(herdr_summary).get('session_name')) or '',
        max_lines=min(2000, max(20, int(max_lines))),
        **_terminal_target_kwargs(herdr_summary),
    )


def _pane_message_target(
    *,
    project_id: str,
    view_payload: dict[str, object],
    target: dict[str, object],
) -> PaneMessageTarget:
    view = _map(view_payload.get('view'))
    namespace = _map(view.get('namespace'))
    socket_path = _optional_text(namespace.get('socket_path'))
    session_name = _optional_text(namespace.get('session_name'))
    agent_record = _map(target.get('agent_record'))
    pane_id = _optional_text(agent_record.get('pane_id'))
    herdr_summary = _herdr_terminal_target_summary(view, agent=str(target.get('agent') or ''), operation='input')
    if not socket_path or not session_name:
        if herdr_summary is None:
            raise MobileGatewayError('ProjectView tmux evidence is not attachable', status_code=409)
    if not pane_id and herdr_summary is not None:
        pane_id = _optional_text(herdr_summary.get('pane_id'))
    if not pane_id:
        raise MobileGatewayError('message target has no pane evidence', status_code=409)
    return PaneMessageTarget(
        project_id=project_id,
        namespace_epoch=int(target.get('namespace_epoch') or 0),
        agent=str(target.get('agent') or ''),
        window=_optional_text(agent_record.get('window')) or '',
        pane_id=pane_id,
        socket_path=socket_path or '',
        session_name=session_name or _optional_text(_map(herdr_summary).get('session_name')) or '',
        **_terminal_target_kwargs(herdr_summary),
    )


def _iterable(value: object):
    return value if isinstance(value, list) else []


def _query_text(query: Mapping[str, object], name: str) -> str | None:
    value = query.get(name)
    if isinstance(value, list):
        return _optional_text(value[0] if value else None)
    return _optional_text(value)


def _query_int(query: Mapping[str, object], name: str) -> int | None:
    text = _query_text(query, name)
    if text is None:
        return None
    try:
        return int(text)
    except ValueError as exc:
        raise MobileGatewayError(f'{name} must be an integer', status_code=400) from exc


def _required_positive_int(value: object, name: str) -> int:
    parsed = _optional_int(value)
    if parsed is None or parsed < 1:
        raise MobileGatewayError(f'{name} must be a positive integer', status_code=400)
    return parsed


def _terminal_websocket_url(headers: Mapping[str, object] | None, *, terminal_id: str) -> str:
    proto = _header_value(headers, 'x-forwarded-proto').lower()
    scheme = 'wss' if proto == 'https' else 'ws'
    host = _header_value(headers, 'x-forwarded-host') or _header_value(headers, 'host') or '127.0.0.1:8787'
    return f'{scheme}://{host}/v1/terminals/{terminal_id}'


def _header_value(headers: Mapping[str, object] | None, name: str) -> str:
    if headers is None:
        return ''
    get = getattr(headers, 'get', None)
    if callable(get):
        value = get(name) or get(name.title())
        if value:
            return str(value).strip()
    for key, item in headers.items():
        if str(key).lower() == name.lower():
            return str(item or '').strip()
    return ''


def _ccbd_focus_status(exc: Exception) -> int:
    text = _error_text(exc)
    if text.startswith('stale_view:'):
        return 409
    if text.startswith('unknown_agent:') or text.startswith('unknown_window:'):
        return 404
    if text.startswith('invalid_request:') or text.startswith('target_missing:'):
        return 400
    return 503


def _terminal_error_code(exc: MobileGatewayError) -> str:
    blocked = _terminal_blocked_payload(exc)
    if blocked is not None:
        return _optional_text(blocked.get('code')) or 'terminal_blocked'
    text = _error_text(exc)
    if text.startswith('stale namespace epoch'):
        return 'stale_namespace_epoch'
    if text.startswith('resume_cursor is required'):
        return 'missing_resume_cursor'
    if text.startswith('terminal resume cursor is stale'):
        return 'stale_resume_cursor'
    if text.startswith('unknown terminal target agent'):
        return 'unknown_agent'
    if text.startswith('unknown terminal target window'):
        return 'unknown_window'
    if text.startswith('ProjectView tmux evidence is not attachable'):
        return 'target_not_attachable'
    return 'terminal_error'


def _mobile_error_payload(exc: MobileGatewayError) -> dict[str, object]:
    blocked = _terminal_blocked_payload(exc)
    if blocked is not None:
        return {
            'schema_version': _SCHEMA_VERSION,
            'status': 'blocked',
            'terminal_blocked': blocked,
            'error': _error_text(exc),
        }
    return {
        'schema_version': _SCHEMA_VERSION,
        'status': 'error',
        'error': _error_text(exc),
    }


def _terminal_error_frame(exc: MobileGatewayError) -> dict[str, object]:
    blocked = _terminal_blocked_payload(exc)
    if blocked is not None:
        return {
            'type': 'error',
            'code': _optional_text(blocked.get('code')) or 'terminal_blocked',
            'terminal_blocked': blocked,
            'message': _error_text(exc),
        }
    return {'type': 'error', 'code': _terminal_error_code(exc)}


def _terminal_blocked_payload(exc: MobileGatewayError) -> dict[str, object] | None:
    payload = getattr(exc, 'terminal_blocked', None)
    return dict(payload) if isinstance(payload, Mapping) else None


def _pump_terminal_output(
    connection: WebSocketConnection,
    session,
    stop: threading.Event,
    close_state: dict[str, str],
    store: MobileGatewayPairingStore,
    terminal_id: str,
    terminal_token: str,
    sequence: int,
    geometry_revision: int,
) -> None:
    try:
        while not stop.is_set():
            data = session.read(0.1)
            if data is None:
                close_state['reason'] = 'pty_closed'
                _safe_send_json(connection, {'type': 'closed', 'reason': 'pty_closed'})
                stop.set()
                return
            viewport = _terminal_viewport_state(session)
            if viewport is not None:
                next_revision = _int(viewport.get('revision'), geometry_revision)
                if next_revision != geometry_revision:
                    geometry_revision = next_revision
                    geometry = _map(viewport.get('geometry'))
                    connection.send_json(
                        {
                            'type': 'geometry',
                            **geometry,
                            'resize_policy': viewport['resize_policy'],
                            'revision': geometry_revision,
                        }
                    )
            if data:
                sequence += 1
                try:
                    payload: dict[str, object] = {
                        'type': 'output',
                        'seq': sequence,
                        'bytes_b64': base64.b64encode(data).decode('ascii'),
                    }
                    take_projection = getattr(session, 'take_output_projection', None)
                    projection = take_projection() if callable(take_projection) else None
                    if isinstance(projection, Mapping):
                        screen = projection.get('screen')
                        history = projection.get('history')
                        history_append = projection.get('history_append')
                        if isinstance(screen, (bytes, bytearray)):
                            payload.update(
                                {
                                    'render_mode': 'replace_snapshot',
                                    'screen_b64': base64.b64encode(screen).decode('ascii'),
                                    'history_reset': bool(
                                        projection.get('history_reset', False)
                                    ),
                                }
                            )
                            if isinstance(history, (bytes, bytearray)):
                                payload['history_b64'] = base64.b64encode(history).decode(
                                    'ascii'
                                )
                            if isinstance(history_append, (bytes, bytearray)):
                                payload['history_append_b64'] = base64.b64encode(
                                    history_append
                                ).decode('ascii')
                    connection.send_json(payload)
                except OSError:
                    close_state['reason'] = 'transport_disconnected'
                    stop.set()
                    return
                store.record_terminal_output_sequence(
                    terminal_id=terminal_id,
                    terminal_token=terminal_token,
                    sequence=sequence,
                )
    except Exception as exc:
        close_state['reason'] = 'terminal_output_error'
        _safe_send_json(connection, {'type': 'error', 'code': 'terminal_output_error', 'message': _error_text(exc)})
        stop.set()


def _terminal_viewport_state(
    session,
    *,
    fallback: TerminalAttachTarget | None = None,
) -> dict[str, object] | None:
    viewport_state = getattr(session, 'viewport_state', None)
    if callable(viewport_state):
        payload = viewport_state()
        if isinstance(payload, Mapping):
            geometry = TerminalGeometry.from_mapping(payload.get('geometry'))
            policy = str(payload.get('resize_policy') or '').strip()
            if policy not in {'adaptive_pane', 'fixed_source', 'client'}:
                raise RuntimeError('terminal viewport resize policy is invalid')
            return {
                'revision': max(0, _int(payload.get('revision'), 0)),
                'geometry': geometry.to_mapping(),
                'resize_policy': policy,
            }
    if fallback is None:
        return None
    return {
        'revision': 0,
        'geometry': fallback.geometry.to_mapping(),
        'resize_policy': (
            'client'
            if fallback.target_summary.get('kind') == 'host_shell'
            else 'fixed_source'
        ),
    }


def _safe_send_json(connection: WebSocketConnection, payload: Mapping[str, object]) -> None:
    try:
        connection.send_json(payload)
    except OSError:
        pass


def _bearer_token(headers: Mapping[str, object] | None) -> str:
    if headers is None:
        return ''
    value = ''
    get = getattr(headers, 'get', None)
    if callable(get):
        value = str(get('authorization') or get('Authorization') or '')
    if not value and isinstance(headers, Mapping):
        for key, item in headers.items():
            if str(key).lower() == 'authorization':
                value = str(item or '')
                break
    prefix = 'bearer '
    if value.lower().startswith(prefix):
        return value[len(prefix):].strip()
    return ''


__all__ = [
    'ListenAddress',
    'MobileGatewayError',
    'MobileGatewayService',
    'build_mobile_gateway_server',
    'parse_listen_address',
]
