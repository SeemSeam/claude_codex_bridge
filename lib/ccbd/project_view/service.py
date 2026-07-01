from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from time import monotonic
from typing import Any
import threading

from agents.config_loader import load_project_config
from agents.models import AgentState
from ccbd.api_models import JobStatus, TargetKind
from ccbd.models import MountState
from ccbd.reload_drain_status import reload_drain_revision, reload_drain_status_payload
from ccbd.project_focus.tmux import backend_for_namespace, refresh_sidebar_panes
from ccbd.services.dispatcher_runtime import comms_recoverability_for_job
from ccbd.system import parse_utc_timestamp, utc_now
from message_bureau import CallbackEdgeState
from provider_pane_status.codex_pane import parse_codex_pane_status
from provider_pane_status.codex_session import CodexRuntimeStatus, compose_codex_runtime_status, read_codex_session_status
from storage.paths import PathLayout

from .activity import (
    AgentActivityFacts,
    PROVIDER_ACTIVITY_PANE_ERROR_PROBE_AFTER_S,
    PROVIDER_INPUT_STUCK_AFTER_S,
    provider_prompt_idle,
    provider_prompt_idle_after_request,
    provider_prompt_input_stuck,
    resolve_agent_activity,
)
from .provider_activity import provider_activity_evidence, record_provider_activity_failure
from .sequence import ProjectViewSequenceCache

PROJECT_VIEW_SCHEMA_VERSION = 1
PROJECT_VIEW_TTL_MS = 1000
PROJECT_VIEW_IDLE_TTL_MS = 5000
PROJECT_VIEW_COMMS_LIMIT = 8
_RECENT_JOB_RESULT_LIMIT = PROJECT_VIEW_COMMS_LIMIT * 8
_RECENT_JOB_SCAN_LIMIT_PER_AGENT = 128
_RECENT_JOB_INITIAL_SCAN_MIN = 8
_RECENT_JOB_INITIAL_SCAN_MAX = 32
_COMMS_RECENT_STATUSES = frozenset(
    {
        JobStatus.COMPLETED,
        JobStatus.CANCELLED,
        JobStatus.FAILED,
        JobStatus.INCOMPLETE,
    }
)
_COMMS_PENDING_STATUSES = frozenset({JobStatus.ACCEPTED, JobStatus.QUEUED})
_COMMS_BODY_PREVIEW_LIMIT = 48
_REPLY_DELIVERY_MESSAGE_TYPE = 'reply_delivery'
_REPLY_DELIVERY_PROVIDER_OPTION = 'reply_delivery'
_REPLY_DELIVERY_REPLY_ID_OPTION = 'reply_delivery_reply_id'


@dataclass(frozen=True)
class ProjectViewDependencies:
    project_root: Path
    project_id: str
    config: object
    registry: object
    mount_manager: object
    namespace_state_store: object
    dispatcher: object
    namespace_controller: object | None = None
    state_store: object | None = None
    paths: object | None = None
    clock: object = utc_now
    sequence_cache: ProjectViewSequenceCache | None = None
    cache_ttl_ms: int | None = None
    metrics: object | None = None


@dataclass
class _ProjectViewMetricsContext:
    tmux_command_count: int = 0
    capture_pane_count: int = 0
    store_scan_count: int = 0


@dataclass
class _ProjectViewBuildContext:
    deps: ProjectViewDependencies
    namespace: object | None
    metrics_context: _ProjectViewMetricsContext | None = None
    backend_loaded: bool = False
    backend: object | None = None
    tmux_facts_loaded: bool = False
    tmux_focus: dict[str, object] = field(default_factory=dict)
    tmux_snapshot: dict[str, dict[str, object]] = field(default_factory=dict)
    pane_text_by_id: dict[str, str | None] = field(default_factory=dict)
    provider_activity_by_agent: dict[str, object | None] = field(default_factory=dict)

    def namespace_backend(self):
        if self.namespace is None or self.deps.namespace_controller is None:
            return None
        if self.backend_loaded:
            return self.backend
        self.backend_loaded = True
        try:
            self.backend = backend_for_namespace(self.deps.namespace_controller._backend_factory, self.namespace)
        except Exception:
            self.backend = None
        return self.backend

    def tmux_project_view_facts(self) -> tuple[dict[str, object], dict[str, dict[str, object]]]:
        if not self.tmux_facts_loaded:
            self.tmux_facts_loaded = True
            self.tmux_focus, self.tmux_snapshot = _collect_tmux_project_view_facts(self)
        return self.tmux_focus, self.tmux_snapshot

    def pane_text_hint(self, pane_id: object) -> str | None:
        pane = str(pane_id or '').strip()
        if not pane.startswith('%'):
            return None
        if pane in self.pane_text_by_id:
            return self.pane_text_by_id[pane]
        backend = self.namespace_backend()
        if backend is None:
            self.pane_text_by_id[pane] = None
            return None
        if self.metrics_context is not None:
            self.metrics_context.capture_pane_count += 1
        cp = _tmux_run_best_effort(
            backend,
            [
                'capture-pane',
                '-p',
                '-t',
                pane,
                '-S',
                '-30',
            ],
            metrics_context=self.metrics_context,
        )
        if cp is None:
            self.pane_text_by_id[pane] = None
            return None
        text = str(getattr(cp, 'stdout', '') or '')
        self.pane_text_by_id[pane] = text or None
        return self.pane_text_by_id[pane]

    def provider_activity_hint(
        self,
        *,
        agent_name: str,
        provider: str,
        runtime: object | None,
        generated_at: str,
    ):
        key = str(agent_name or '').strip()
        if not key:
            return None
        if key not in self.provider_activity_by_agent:
            self.provider_activity_by_agent[key] = provider_activity_evidence(
                project_root=self.deps.project_root,
                project_id=self.deps.project_id,
                paths=self.deps.paths,
                agent_name=key,
                provider=provider,
                runtime=runtime,
                now=generated_at,
            )
        return self.provider_activity_by_agent[key]


@dataclass
class _CommsLookup:
    attempt_store: object | None
    reply_store: object | None
    message_store: object | None
    attempts_by_job_id: dict[str, object | None] = field(default_factory=dict)
    attempts_by_attempt_id: dict[str, object | None] = field(default_factory=dict)
    attempts_by_message_id: dict[tuple[str, str], object | None] = field(default_factory=dict)
    latest_attempt_by_message_agent: dict[tuple[str, str], object | None] = field(default_factory=dict)
    replies_by_reply_id: dict[str, object | None] = field(default_factory=dict)
    messages_by_message_id: dict[str, object | None] = field(default_factory=dict)

    def attempt_by_job_id(self, job_id: object) -> object | None:
        key = str(job_id or '').strip()
        if not key:
            return None
        if key not in self.attempts_by_job_id:
            self.attempts_by_job_id[key] = _call_store(self.attempt_store, 'get_latest_by_job_id', key)
        return self.attempts_by_job_id[key]

    def attempt_by_attempt_id(self, attempt_id: object) -> object | None:
        key = str(attempt_id or '').strip()
        if not key:
            return None
        if key not in self.attempts_by_attempt_id:
            self.attempts_by_attempt_id[key] = _call_store(self.attempt_store, 'get_latest', key)
        return self.attempts_by_attempt_id[key]

    def latest_attempt_by_message_id(self, message_id: object, *, exclude_job_id: object = None) -> object | None:
        message_key = str(message_id or '').strip()
        if not message_key:
            return None
        excluded = str(exclude_job_id or '').strip()
        cache_key = (message_key, excluded)
        if cache_key not in self.attempts_by_message_id:
            self.attempts_by_message_id[cache_key] = _call_store(
                self.attempt_store,
                'get_latest_by_message_id',
                message_key,
                exclude_job_id=excluded or None,
            )
        return self.attempts_by_message_id[cache_key]

    def latest_attempt_for_message_agent(self, message_id: object, agent_name: object) -> object | None:
        message_key = str(message_id or '').strip()
        agent_key = str(agent_name or '').strip()
        if not message_key or not agent_key:
            return None
        cache_key = (message_key, agent_key)
        if cache_key not in self.latest_attempt_by_message_agent:
            self.latest_attempt_by_message_agent[cache_key] = _call_store(
                self.attempt_store,
                'get_latest_by_message_agent',
                message_key,
                agent_key,
            )
        return self.latest_attempt_by_message_agent[cache_key]

    def reply_by_reply_id(self, reply_id: object) -> object | None:
        key = str(reply_id or '').strip()
        if not key:
            return None
        if key not in self.replies_by_reply_id:
            self.replies_by_reply_id[key] = _call_store(self.reply_store, 'get_latest', key)
        return self.replies_by_reply_id[key]

    def message_by_message_id(self, message_id: object) -> object | None:
        key = str(message_id or '').strip()
        if not key:
            return None
        if key not in self.messages_by_message_id:
            self.messages_by_message_id[key] = _call_store(self.message_store, 'get_latest', key)
        return self.messages_by_message_id[key]


@dataclass(frozen=True)
class _CachedProjectViewResponse:
    response: dict[str, object]
    expires_at: float
    dispatcher_revision: int
    drain_revision: tuple[int, int] | None


class ProjectViewService:
    def __init__(self, deps: ProjectViewDependencies) -> None:
        self._deps = deps
        self._sequence_cache = deps.sequence_cache or ProjectViewSequenceCache()
        self._cached_response: _CachedProjectViewResponse | None = None
        self._sidebar_refresh_lock = threading.Lock()
        self._sidebar_refresh_pending = False

    def invalidate_cache(self) -> None:
        self._cached_response = None

    def request_sidebar_refresh(self) -> None:
        with self._sidebar_refresh_lock:
            self._sidebar_refresh_pending = True

    def build_response(self, *, schema_version: int = PROJECT_VIEW_SCHEMA_VERSION) -> dict[str, object]:
        if int(schema_version) != PROJECT_VIEW_SCHEMA_VERSION:
            raise ValueError(f'project_view schema_version must be {PROJECT_VIEW_SCHEMA_VERSION}')
        response_started = monotonic()
        sidebar_refresh_started = None
        ttl_ms = _project_view_ttl_ms(self._deps)
        ttl_s = max(0.0, ttl_ms / 1000.0)
        now = monotonic()
        dispatcher_revision = _dispatcher_project_view_revision(self._deps.dispatcher)
        drain_revision = reload_drain_revision(self._deps)
        did_refresh_sidebar = False
        if ttl_s > 0:
            cached = self._cached_response
            if (
                cached is not None
                and now < cached.expires_at
                and cached.dispatcher_revision == dispatcher_revision
                and cached.drain_revision == drain_revision
            ):
                did_refresh_sidebar = self._consume_sidebar_refresh_request()
                if did_refresh_sidebar:
                    sidebar_refresh_started = monotonic()
                    did_refresh_sidebar = self._refresh_sidebar_panes(refresh_started=sidebar_refresh_started)
                _record_project_view_cache_hit(self._deps.metrics, response_started=response_started)
                return cached.response
        metrics_context = _ProjectViewMetricsContext()
        sidebar_refresh_started = monotonic()
        did_refresh_sidebar = self._consume_sidebar_refresh_request()
        if did_refresh_sidebar:
            did_refresh_sidebar = self._refresh_sidebar_panes(refresh_started=sidebar_refresh_started)
        generated_at = self._deps.clock()
        build_started = monotonic()
        view = build_project_view(self._deps, generated_at=generated_at, metrics_context=metrics_context)
        response = {
            'view': view,
            'cache': {
                'generated_at': generated_at,
                'ttl_ms': ttl_ms,
                'sequence': self._sequence_cache.sequence_for(view),
            },
        }
        if ttl_s > 0:
            self._cached_response = _CachedProjectViewResponse(
                response=response,
                expires_at=monotonic() + ttl_s,
                dispatcher_revision=dispatcher_revision,
                drain_revision=drain_revision,
            )
        _record_project_view_cache_miss(
            self._deps.metrics,
            response_started=response_started,
            build_started=build_started,
            context=metrics_context,
        )
        return response

    def _consume_sidebar_refresh_request(self) -> bool:
        with self._sidebar_refresh_lock:
            if not self._sidebar_refresh_pending:
                return False
            self._sidebar_refresh_pending = False
            return True

    def _refresh_sidebar_panes(self, *, refresh_started: float | None = None) -> bool:
        if self._deps.namespace_controller is None:
            return False
        namespace = self._deps.namespace_state_store.load()
        if namespace is None:
            return False
        started = refresh_started if refresh_started is not None else monotonic()
        try:
            backend = backend_for_namespace(self._deps.namespace_controller._backend_factory, namespace)
            refresh_sidebar_panes(
                backend,
                project_id=self._deps.project_id,
                session_name=namespace.tmux_session_name,
            )
            _record_project_view_sidebar_refresh(
                self._deps.metrics,
                refresh_started=started,
                success=True,
            )
            return True
        except Exception:
            _record_project_view_sidebar_refresh(
                self._deps.metrics,
                refresh_started=started,
                success=False,
            )
            return False

def build_project_view(
    deps: ProjectViewDependencies,
    *,
    generated_at: str,
    metrics_context: _ProjectViewMetricsContext | None = None,
) -> dict[str, object]:
    lease = deps.mount_manager.load_state()
    namespace = deps.namespace_state_store.load()
    context = _ProjectViewBuildContext(deps=deps, namespace=namespace, metrics_context=metrics_context)
    focus = _focus_snapshot(context)
    tmux_snapshot = _tmux_snapshot(context)
    namespace_mounted = lease is not None and lease.mount_state is MountState.MOUNTED
    window_by_agent = _window_by_agent(deps.config)
    active_jobs = _active_jobs_by_agent(deps.dispatcher)
    queued_jobs = _queued_jobs_by_agent(deps.dispatcher)
    callback_waits = _callback_waits_by_parent_agent(deps.dispatcher)
    provider_runtime_by_agent = _provider_runtime_by_agent(deps.dispatcher)
    reload_drains = reload_drain_status_payload(deps)
    active_drain_by_agent = _active_reload_drains_by_agent(reload_drains)

    agents = [
        _agent_view(
            deps=deps,
            agent_name=agent_name,
            window_name=window_by_agent[agent_name],
            order=order,
            namespace_mounted=namespace_mounted,
            generated_at=generated_at,
            active=focus.get('active_agent') == agent_name,
            context=context,
            active_job=active_jobs.get(agent_name),
            queued_jobs=queued_jobs.get(agent_name, ()),
            callback_wait=callback_waits.get(agent_name),
            provider_runtimes=provider_runtime_by_agent.get(agent_name, ()),
            reload_drain=active_drain_by_agent.get(agent_name),
        )
        for order, agent_name in enumerate(_agent_order(deps.config))
    ]

    return {
        'schema_version': PROJECT_VIEW_SCHEMA_VERSION,
        'generated_at': generated_at,
        'project': {
            'id': deps.project_id,
            'root': str(deps.project_root),
            'display_name': deps.project_root.name,
        },
        'ccbd': _ccbd_view(lease),
        'namespace': _namespace_view(
            config=deps.config,
            sidebar_view_result=_current_sidebar_view(deps),
            namespace=namespace,
            focus=focus,
        ),
        'windows': _window_views(config=deps.config, focus=focus, tmux_snapshot=tmux_snapshot),
        'agents': agents,
        'reload_drains': reload_drains,
        'comms': _comms_view(
            deps,
            context=context,
            active_jobs=active_jobs,
            queued_jobs=queued_jobs,
            generated_at=generated_at,
        ),
    }


def _dispatcher_project_view_revision(dispatcher: object | None) -> int:
    value = getattr(dispatcher, 'project_view_revision', 0)
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def _project_view_ttl_ms(deps: ProjectViewDependencies) -> int:
    if deps.cache_ttl_ms is not None:
        ttl_ms = int(deps.cache_ttl_ms)
    elif _project_view_work_pending(deps):
        ttl_ms = _env_int('CCB_PROJECT_VIEW_TTL_MS', PROJECT_VIEW_TTL_MS)
    else:
        ttl_ms = _env_int('CCB_PROJECT_VIEW_IDLE_TTL_MS', PROJECT_VIEW_IDLE_TTL_MS)
    return max(0, ttl_ms)


def _env_int(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name, default))
    except (TypeError, ValueError):
        return default


def _project_view_work_pending(deps: ProjectViewDependencies) -> bool:
    if _reload_drain_active_count(deps) > 0:
        return True
    dispatcher_state = getattr(getattr(deps, 'dispatcher', None), '_state', None)
    if dispatcher_state is not None:
        try:
            if dispatcher_state.active_items():
                return True
        except Exception:
            return True
        queues = getattr(dispatcher_state, '_queues', {})
        try:
            if any(len(queue) > 0 for queue in queues.values()):
                return True
        except Exception:
            return True
    registry = getattr(deps, 'registry', None)
    try:
        runtimes = registry.list_all() if registry is not None else ()
    except Exception:
        return True
    return any(getattr(runtime, 'state', None) in {AgentState.STARTING, AgentState.BUSY, AgentState.DEGRADED} for runtime in runtimes)


def _reload_drain_active_count(deps: ProjectViewDependencies) -> int:
    payload = reload_drain_status_payload(deps)
    try:
        return int(payload.get('active_count') or 0)
    except (TypeError, ValueError):
        return 0


def _record_project_view_cache_hit(metrics, *, response_started: float) -> None:
    if metrics is None:
        return
    metrics.project_view_cache_hits = int(getattr(metrics, 'project_view_cache_hits', 0) or 0) + 1
    metrics.last_project_view_response_duration_s = max(0.0, monotonic() - response_started)


def _record_project_view_cache_miss(
    metrics,
    *,
    response_started: float,
    build_started: float,
    context: _ProjectViewMetricsContext,
) -> None:
    if metrics is None:
        return
    metrics.project_view_cache_misses = int(getattr(metrics, 'project_view_cache_misses', 0) or 0) + 1
    finished = monotonic()
    metrics.last_project_view_response_duration_s = max(0.0, finished - response_started)
    metrics.last_project_view_build_duration_s = max(0.0, finished - build_started)
    metrics.last_project_view_tmux_command_count = context.tmux_command_count
    metrics.last_project_view_capture_pane_count = context.capture_pane_count
    metrics.last_project_view_store_scan_count = context.store_scan_count


def _record_project_view_sidebar_refresh(metrics, *, refresh_started: float, success: bool) -> None:
    if metrics is None:
        return
    metrics.project_view_sidebar_refreshes = int(getattr(metrics, 'project_view_sidebar_refreshes', 0) or 0) + 1
    if not success:
        metrics.project_view_sidebar_refresh_failures = (
            int(getattr(metrics, 'project_view_sidebar_refresh_failures', 0) or 0) + 1
        )
    metrics.last_project_view_sidebar_refresh_duration_s = max(0.0, monotonic() - refresh_started)


def _agent_view(
    *,
    deps: ProjectViewDependencies,
    agent_name: str,
    window_name: str,
    order: int,
    namespace_mounted: bool,
    generated_at: str,
    context: _ProjectViewBuildContext,
    active_job,
    queued_jobs: tuple,
    callback_wait,
    active: bool = False,
    provider_runtimes: tuple[dict[str, object], ...] = (),
    reload_drain: dict[str, object] | None = None,
) -> dict[str, object]:
    spec = deps.config.agents[agent_name]
    runtime = deps.registry.get(agent_name)
    provider_is_codex = _is_codex_provider(spec.provider)
    provider_activity = None
    if not provider_is_codex:
        provider_activity = context.provider_activity_hint(
            agent_name=agent_name,
            provider=spec.provider,
            runtime=runtime,
            generated_at=generated_at,
        )
    job = _top_activity_job(active_job=active_job, queued_jobs=queued_jobs)
    provider_runtime = _select_provider_runtime(
        provider_runtimes,
        agent_name=agent_name,
        current_job_id=getattr(job, 'job_id', None) if job is not None else None,
    )
    queue_depth = len(queued_jobs) + (1 if _is_top_activity_job(active_job) else 0)
    callback_child_agent = _callback_child_agent(callback_wait)
    pane_text = None
    codex_runtime_status = None
    if provider_is_codex:
        pane_text = context.pane_text_hint(getattr(runtime, 'pane_id', None) if runtime is not None else None)
        if pane_text is not None:
            codex_runtime_status = _codex_runtime_status(
                deps=deps,
                agent_name=agent_name,
                provider=spec.provider,
                provider_profile=getattr(spec, 'provider_profile', None),
                runtime=runtime,
                pane_text=pane_text,
                current_job=job,
            )
    elif provider_activity is None or _provider_activity_needs_pane_error_probe(provider_activity, generated_at):
        pane_text = context.pane_text_hint(getattr(runtime, 'pane_id', None) if runtime is not None else None)
    activity = resolve_agent_activity(
        AgentActivityFacts(
            namespace_mounted=namespace_mounted,
            provider=spec.provider,
            runtime_state=_runtime_state(runtime),
            runtime_health=getattr(runtime, 'health', None) if runtime is not None else None,
            reconcile_state=getattr(runtime, 'reconcile_state', None) if runtime is not None else None,
            desired_state=getattr(runtime, 'desired_state', None) if runtime is not None else None,
            pane_id=getattr(runtime, 'pane_id', None) if runtime is not None else None,
            pane_state=getattr(runtime, 'pane_state', None) if runtime is not None else None,
            pane_text=pane_text,
            current_job_status=job.status.value if job is not None else None,
            current_job_id=job.job_id if job is not None else None,
            current_job_updated_at=job.updated_at if job is not None else None,
            queue_depth=queue_depth,
            callback_waiting_state=callback_wait.state.value if callback_wait is not None else None,
            callback_child_job_id=callback_wait.child_job_id if callback_wait is not None else None,
            callback_child_agent=callback_child_agent,
            callback_updated_at=callback_wait.updated_at if callback_wait is not None else None,
            provider_activity_state=getattr(provider_activity, 'state', None),
            provider_activity_source=getattr(provider_activity, 'source', None),
            provider_activity_reason=getattr(provider_activity, 'reason', None),
            provider_activity_updated_at=getattr(provider_activity, 'updated_at', None),
            provider_runtime_state=getattr(codex_runtime_status, 'state', None),
            provider_runtime_source='codex_runtime' if codex_runtime_status is not None else None,
            provider_runtime_reason=getattr(codex_runtime_status, 'reason', None),
        ),
        now=generated_at,
    )
    _record_inferred_provider_failure(
        deps=deps,
        agent_name=agent_name,
        provider=spec.provider,
        runtime=runtime,
        provider_activity=provider_activity,
        activity=activity,
        generated_at=generated_at,
    )
    record = {
        'name': agent_name,
        'provider': spec.provider,
        'window': window_name,
        'order': order,
        'pane_id': getattr(runtime, 'pane_id', None) if runtime is not None else None,
        'active': bool(active),
        'queue_depth': queue_depth,
        **activity.to_record(),
        'callback_waiting_child_job_id': callback_wait.child_job_id if callback_wait is not None else None,
        'callback_waiting_child_agent': callback_child_agent,
        'callback_waiting_state': callback_wait.state.value if callback_wait is not None else None,
        'runtime_state': _runtime_state(runtime),
        'runtime_health': getattr(runtime, 'health', None) if runtime is not None else None,
        'reconcile_state': getattr(runtime, 'reconcile_state', None) if runtime is not None else None,
        'workspace_path': getattr(runtime, 'workspace_path', None) if runtime is not None else None,
        'reload_drain': dict(reload_drain) if reload_drain is not None else None,
        'dispatch_blocked_by_reload_drain': reload_drain is not None,
    }
    if provider_runtime is not None:
        record['provider_runtime'] = provider_runtime
    if codex_runtime_status is not None:
        record['provider_runtime_status'] = codex_runtime_status.to_record()
    return record


def _is_codex_provider(provider: object) -> bool:
    return str(provider or '').strip().lower() == 'codex'


def _codex_runtime_status(
    *,
    deps: ProjectViewDependencies,
    agent_name: str,
    provider: str,
    provider_profile: object | None,
    runtime: object | None,
    pane_text: str | None,
    current_job,
) -> CodexRuntimeStatus | None:
    if not _is_codex_provider(provider):
        return None
    pane_state = _clean_pane_state(getattr(runtime, 'pane_state', None) if runtime is not None else None)
    pane_status = parse_codex_pane_status(
        pane_text,
        pane_dead=pane_state in {'missing', 'dead'},
    )
    session_status = read_codex_session_status(
        _codex_session_root(
            deps=deps,
            agent_name=agent_name,
            provider=provider,
            provider_profile=provider_profile,
        ),
        work_dir=getattr(runtime, 'workspace_path', None) if runtime is not None else None,
        min_mtime_s=_job_updated_epoch_for_session_floor(current_job),
    )
    raw_status = compose_codex_runtime_status(pane_status, session_status)
    if (
        _job_is_running(current_job)
        and raw_status.state == 'unknown'
    ):
        return CodexRuntimeStatus(
            'start',
            'prompt_submitted_waiting_for_first_signal',
            'stabilizer',
            raw_status.pane_state,
            raw_status.pane_reason,
            raw_status.session_state,
            raw_status.session_reason,
            notes=(
                *raw_status.notes,
                f'raw_state={raw_status.state}',
                f'raw_reason={raw_status.reason}',
            ),
        )
    return raw_status


def _codex_session_root(
    *,
    deps: ProjectViewDependencies,
    agent_name: str,
    provider: str,
    provider_profile: object | None,
) -> Path | None:
    layout = deps.paths or PathLayout(deps.project_root)
    runtime_dir_resolver = getattr(layout, 'agent_provider_runtime_dir', None)
    if not callable(runtime_dir_resolver):
        return None
    try:
        runtime_dir = Path(runtime_dir_resolver(agent_name, provider))
        from provider_backends.codex.launcher_runtime.command_runtime.home import (
            resolve_codex_home_layout,
        )

        return resolve_codex_home_layout(runtime_dir, provider_profile).session_root
    except Exception:
        return None


def _job_updated_epoch_for_session_floor(job) -> float | None:
    if not _job_is_running(job):
        return None
    try:
        return parse_utc_timestamp(str(getattr(job, 'updated_at', '') or '')).timestamp()
    except Exception:
        return None


def _job_is_running(job) -> bool:
    return job is not None and getattr(job, 'status', None) is JobStatus.RUNNING


def _clean_pane_state(value: object) -> str:
    return str(value or '').strip().lower()


def _active_reload_drains_by_agent(reload_drains: dict[str, object]) -> dict[str, dict[str, object]]:
    records: dict[str, dict[str, object]] = {}
    for item in tuple(reload_drains.get('active_records') or ()):
        if not isinstance(item, dict):
            continue
        agent_name = str(item.get('agent') or '').strip()
        if agent_name:
            records[agent_name] = dict(item)
    return records


def _provider_activity_needs_pane_error_probe(provider_activity: object | None, generated_at: str) -> bool:
    state = str(getattr(provider_activity, 'state', '') or '').strip().lower()
    if state not in {'active', 'pending'}:
        return False
    age_s = _job_age_seconds(generated_at, getattr(provider_activity, 'updated_at', None))
    return age_s is not None and age_s >= PROVIDER_ACTIVITY_PANE_ERROR_PROBE_AFTER_S


def _record_inferred_provider_failure(
    *,
    deps: ProjectViewDependencies,
    agent_name: str,
    provider: str,
    runtime: object | None,
    provider_activity: object | None,
    activity,
    generated_at: str,
) -> None:
    provider_state = str(getattr(provider_activity, 'state', '') or '').strip().lower()
    if provider_state not in {'active', 'pending'}:
        return
    if getattr(activity, 'state', None) != 'failed':
        return
    if getattr(activity, 'source', None) not in {'provider_pane', 'codex_runtime'}:
        return
    reason = str(getattr(activity, 'reason', '') or '').strip()
    if reason not in {'provider_terminal_error', 'provider_api_error', 'provider_auth_failed', 'provider_config_error', 'provider_error_text'}:
        return
    record_provider_activity_failure(
        project_root=deps.project_root,
        project_id=deps.project_id,
        paths=deps.paths,
        agent_name=agent_name,
        provider=provider,
        runtime=runtime,
        reason=reason,
        updated_at=generated_at,
    )


def _top_activity_job(*, active_job, queued_jobs: tuple):
    if _is_top_activity_job(active_job):
        return active_job
    for job in queued_jobs:
        if _is_top_activity_job(job):
            return job
    return None


def _is_top_activity_job(job) -> bool:
    return job is not None and job.status in {JobStatus.ACCEPTED, JobStatus.QUEUED, JobStatus.RUNNING}


def _callback_waits_by_parent_agent(dispatcher) -> dict[str, object]:
    bureau = getattr(dispatcher, '_message_bureau', None)
    if bureau is None:
        return {}
    result: dict[str, object] = {}
    for edge in bureau.pending_callback_edges():
        if edge.state not in {CallbackEdgeState.PENDING, CallbackEdgeState.CHILD_COMPLETED}:
            continue
        parent_agent = str(edge.parent_agent or '').strip()
        if not parent_agent:
            continue
        current = result.get(parent_agent)
        if current is None or str(edge.updated_at or '') >= str(getattr(current, 'updated_at', '') or ''):
            result[parent_agent] = edge
    return result


def _callback_child_agent(edge) -> str | None:
    if edge is None:
        return None
    value = str((edge.diagnostics or {}).get('child_agent') or '').strip()
    return value or None


def _ccbd_view(lease) -> dict[str, object]:
    return {
        'state': lease.mount_state.value if lease is not None else 'unmounted',
        'health': 'healthy' if lease is not None and lease.mount_state is MountState.MOUNTED else 'unmounted',
        'generation': lease.generation if lease is not None else None,
        'last_heartbeat_at': lease.last_heartbeat_at if lease is not None else None,
    }


def _namespace_view(*, config, sidebar_view_result, namespace, focus: dict[str, object]) -> dict[str, object]:
    sidebar = config.sidebar.to_record()
    sidebar_view, sidebar_view_error = sidebar_view_result
    sidebar['view'] = sidebar_view.to_record()
    if sidebar_view_error is not None:
        sidebar['view_error'] = sidebar_view_error
    return {
        'epoch': namespace.namespace_epoch if namespace is not None else None,
        'socket_path': namespace.tmux_socket_path if namespace is not None else None,
        'session_name': namespace.tmux_session_name if namespace is not None else None,
        'active_window': focus.get('active_window') or config.entry_window,
        'active_pane_id': focus.get('active_pane_id'),
        'entry_window': config.entry_window,
        'sidebar': sidebar,
    }


def _current_sidebar_view(deps: ProjectViewDependencies):
    try:
        return load_project_config(deps.project_root).config.sidebar_view, None
    except Exception as exc:
        return deps.config.sidebar_view, str(exc)


def _window_views(*, config, focus: dict[str, object], tmux_snapshot: dict[str, dict[str, object]]) -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    for window in config.windows:
        rows.append(
            {
                'name': window.name,
                'label': window.name,
                'kind': 'agents',
                'order': window.order,
                'tmux_window_id': tmux_snapshot.get(window.name, {}).get('tmux_window_id'),
                'tmux_window_index': tmux_snapshot.get(window.name, {}).get('tmux_window_index'),
                'active': window.name == (focus.get('active_window') or config.entry_window),
                'sidebar_pane_id': tmux_snapshot.get(window.name, {}).get('sidebar_pane_id'),
                'agents': list(window.agent_names),
                'tools': list(getattr(window, 'tool_names', ()) or ()),
            }
        )
    offset = len(rows)
    for tool in tuple(getattr(config, 'tool_windows', ()) or ()):
        rows.append(
            {
                'name': tool.name,
                'label': tool.label,
                'kind': 'tool',
                'show_in_sidebar': bool(getattr(tool, 'show_in_sidebar', True)),
                'order': offset + tool.order,
                'tmux_window_id': tmux_snapshot.get(tool.name, {}).get('tmux_window_id'),
                'tmux_window_index': tmux_snapshot.get(tool.name, {}).get('tmux_window_index'),
                'active': tool.name == (focus.get('active_window') or config.entry_window),
                'sidebar_pane_id': tmux_snapshot.get(tool.name, {}).get('sidebar_pane_id'),
                'agents': [],
            }
        )
    return rows


def _tmux_snapshot(context: _ProjectViewBuildContext) -> dict[str, dict[str, object]]:
    return context.tmux_project_view_facts()[1]


def _collect_tmux_project_view_facts(context: _ProjectViewBuildContext) -> tuple[dict[str, object], dict[str, dict[str, object]]]:
    namespace = context.namespace
    backend = context.namespace_backend()
    if namespace is None or backend is None:
        return {}, {}
    focus_cp = _tmux_run_best_effort(
        backend,
        [
            'display-message',
            '-p',
            '-t',
            namespace.tmux_session_name,
            '#{window_name}\t#{pane_id}\t#{@ccb_role}\t#{@ccb_slot}',
        ],
        metrics_context=context.metrics_context,
    )
    windows_cp = _tmux_run_best_effort(
        backend,
        [
            'list-windows',
            '-t',
            namespace.tmux_session_name,
            '-F',
            '#{window_name}\t#{window_id}\t#{window_index}',
        ],
        metrics_context=context.metrics_context,
    )
    sidebars_cp = _tmux_run_best_effort(
        backend,
        [
            'list-panes',
            '-a',
            '-F',
            '#{session_name}\t#{window_name}\t#{pane_id}\t#{@ccb_project_id}\t#{@ccb_role}\t#{@ccb_sidebar_instance}\t#{@ccb_window}',
        ],
        metrics_context=context.metrics_context,
    )
    parsed = _parse_tmux_project_view_outputs(
        focus_stdout=str(getattr(focus_cp, 'stdout', '') or '') if focus_cp is not None else '',
        windows_stdout=str(getattr(windows_cp, 'stdout', '') or '') if windows_cp is not None else '',
        sidebars_stdout=str(getattr(sidebars_cp, 'stdout', '') or '') if sidebars_cp is not None else '',
        session_name=str(namespace.tmux_session_name or ''),
        project_id=str(context.deps.project_id or ''),
    )
    focus = parsed.get('focus') if isinstance(parsed, dict) else {}
    if not isinstance(focus, dict):
        focus = {}
    windows = parsed.get('windows') if isinstance(parsed, dict) else {}
    if not isinstance(windows, dict):
        windows = {}
    sidebars = parsed.get('sidebars') if isinstance(parsed, dict) else {}
    if not isinstance(sidebars, dict):
        sidebars = {}
    result: dict[str, dict[str, object]] = {}
    for window_name, window_facts in windows.items():
        if not isinstance(window_name, str) or not isinstance(window_facts, dict):
            continue
        result[window_name] = dict(window_facts)
    for window_name, pane_id in sidebars.items():
        if not isinstance(window_name, str):
            continue
        result.setdefault(window_name, {})['sidebar_pane_id'] = pane_id
    return dict(focus), result


def _parse_tmux_project_view_outputs(
    *,
    focus_stdout: str,
    windows_stdout: str,
    sidebars_stdout: str,
    session_name: str,
    project_id: str,
) -> dict[str, object]:
    mode = str(os.environ.get('CCB_RUST_PROJECT_VIEW', '')).strip().lower()
    required = mode == 'required'
    try:
        from rust_helpers_project_view import parse_tmux_project_view_outputs
    except Exception as exc:
        if required:
            raise RuntimeError(
                'project_view.tmux.parse requires ccb-rs-helper; no Python fallback is available for this path'
            ) from exc
        return _parse_tmux_project_view_outputs_python(
            focus_stdout=focus_stdout,
            windows_stdout=windows_stdout,
            sidebars_stdout=sidebars_stdout,
            session_name=session_name,
            project_id=project_id,
        )
    try:
        return parse_tmux_project_view_outputs(
            focus_stdout=focus_stdout,
            windows_stdout=windows_stdout,
            sidebars_stdout=sidebars_stdout,
            session_name=session_name,
            project_id=project_id,
        ).value
    except Exception:
        if required:
            raise
        return _parse_tmux_project_view_outputs_python(
            focus_stdout=focus_stdout,
            windows_stdout=windows_stdout,
            sidebars_stdout=sidebars_stdout,
            session_name=session_name,
            project_id=project_id,
        )


def _parse_tmux_project_view_outputs_python(
    *,
    focus_stdout: str,
    windows_stdout: str,
    sidebars_stdout: str,
    session_name: str,
    project_id: str,
) -> dict[str, object]:
    return {
        'focus': _parse_focus_stdout(focus_stdout),
        'windows': _parse_windows_stdout(windows_stdout),
        'sidebars': _parse_sidebars_stdout(sidebars_stdout, session_name=session_name, project_id=project_id),
    }


def _parse_focus_stdout(stdout: str) -> dict[str, object]:
    parts = ((str(stdout or '').splitlines() or [''])[0]).split('\t')
    if len(parts) != 4:
        return {}
    active_agent = parts[3].strip() if parts[2].strip() == 'agent' else None
    return {
        'active_window': parts[0].strip() or None,
        'active_pane_id': parts[1].strip() or None,
        'active_agent': active_agent or None,
    }


def _parse_windows_stdout(stdout: str) -> dict[str, dict[str, object]]:
    result: dict[str, dict[str, object]] = {}
    for line in (stdout or '').splitlines():
        parts = line.split('\t')
        if len(parts) != 3:
            continue
        window_name, window_id, window_index = (_clean_text(item) for item in parts)
        if window_name is None:
            continue
        result[window_name] = {
            'tmux_window_id': window_id,
            'tmux_window_index': _coerce_int(window_index),
        }
    return result


def _parse_sidebars_stdout(stdout: str, *, session_name: str, project_id: str) -> dict[str, str]:
    result: dict[str, str] = {}
    for line in (stdout or '').splitlines():
        parts = line.split('\t')
        if len(parts) != 7:
            continue
        session, window_name, pane_id, pane_project_id, role, sidebar_instance, ccb_window = (
            _clean_text(item) for item in parts
        )
        if session != session_name or pane_project_id != project_id or role != 'sidebar':
            continue
        if pane_id is None or not pane_id.startswith('%'):
            continue
        resolved_window = sidebar_instance or ccb_window or window_name
        if resolved_window is None or resolved_window in result:
            continue
        result[resolved_window] = pane_id
    return result


def _tmux_windows(
    backend,
    *,
    session_name: str,
    metrics_context: _ProjectViewMetricsContext | None = None,
) -> dict[str, dict[str, object]]:
    cp = _tmux_run_best_effort(
        backend,
        [
            'list-windows',
            '-t',
            session_name,
            '-F',
            '#{window_name}\t#{window_id}\t#{window_index}',
        ],
        metrics_context=metrics_context,
    )
    if cp is None:
        return {}
    result: dict[str, dict[str, object]] = {}
    for line in (getattr(cp, 'stdout', '') or '').splitlines():
        parts = line.split('\t')
        if len(parts) != 3:
            continue
        window_name, window_id, window_index = (_clean_text(item) for item in parts)
        if window_name is None:
            continue
        result[window_name] = {
            'tmux_window_id': window_id,
            'tmux_window_index': _coerce_int(window_index),
        }
    return result


def _tmux_sidebar_panes(
    backend,
    *,
    session_name: str,
    project_id: str,
    metrics_context: _ProjectViewMetricsContext | None = None,
) -> dict[str, str]:
    cp = _tmux_run_best_effort(
        backend,
        [
            'list-panes',
            '-a',
            '-F',
            '#{session_name}\t#{window_name}\t#{pane_id}\t#{@ccb_project_id}\t#{@ccb_role}\t#{@ccb_sidebar_instance}\t#{@ccb_window}',
        ],
        metrics_context=metrics_context,
    )
    if cp is None:
        return {}
    result: dict[str, str] = {}
    for line in (getattr(cp, 'stdout', '') or '').splitlines():
        parts = line.split('\t')
        if len(parts) != 7:
            continue
        session, window_name, pane_id, pane_project_id, role, sidebar_instance, ccb_window = (
            _clean_text(item) for item in parts
        )
        if session != session_name or pane_project_id != project_id or role != 'sidebar':
            continue
        if pane_id is None or not pane_id.startswith('%'):
            continue
        resolved_window = sidebar_instance or ccb_window or window_name
        if resolved_window is None or resolved_window in result:
            continue
        result[resolved_window] = pane_id
    return result


def _tmux_run_best_effort(
    backend,
    args: list[str],
    *,
    metrics_context: _ProjectViewMetricsContext | None = None,
):
    runner = getattr(backend, '_tmux_run', None)
    if not callable(runner):
        return None
    if metrics_context is not None:
        metrics_context.tmux_command_count += 1
    try:
        cp = runner(args, capture=True, check=False, timeout=0.5)
    except Exception:
        return None
    if getattr(cp, 'returncode', 1) != 0:
        return None
    return cp


def _clean_text(value: Any) -> str | None:
    text = str(value or '').strip()
    return text or None


def _coerce_int(value: str | None) -> int | None:
    text = str(value or '').strip()
    if not text.isdigit():
        return None
    return int(text)


def _comms_view(
    deps: ProjectViewDependencies,
    *,
    context: _ProjectViewBuildContext,
    active_jobs: dict[str, object],
    queued_jobs: dict[str, tuple],
    generated_at: str,
) -> list[dict[str, object]]:
    dispatcher = deps.dispatcher
    dismissed_comms = _dismissed_comms(deps)
    jobs = _project_jobs(
        dispatcher,
        active_jobs=active_jobs,
        queued_jobs=queued_jobs,
        metrics_context=context.metrics_context,
    )
    comms_lookup = _comms_lookup(dispatcher)
    reply_deliveries = _reply_deliveries_by_source_job_id(dispatcher, jobs, comms_lookup=comms_lookup)
    jobs = _with_reply_delivery_sources(dispatcher, jobs, reply_deliveries=reply_deliveries)
    attempts_by_job_id = _attempt_lineage_by_job_id_from_lookup(comms_lookup, jobs)
    jobs = _latest_business_jobs_by_lineage(
        dispatcher,
        jobs,
        reply_deliveries=reply_deliveries,
        attempts_by_job_id=attempts_by_job_id,
    )
    configured_agents = _configured_agent_names(dispatcher)
    lineage_for_recoverability = _recoverability_lineage_lookup(dispatcher, comms_lookup)
    rows = []
    for job in jobs:
        reply_delivery = reply_deliveries.get(job.job_id)
        running_recover_hint = _running_recover_hint(context, job=job, generated_at=generated_at)
        rows.append(
            (
                _comm_sort_key(job, reply_delivery),
                _comm_record(
                    dispatcher,
                    job,
                    reply_delivery=reply_delivery,
                    configured_agents=configured_agents,
                    running_recover_hint=running_recover_hint,
                    lineage_for_recoverability=lineage_for_recoverability,
                ),
            )
        )
    rows.sort(key=lambda item: item[0], reverse=True)
    visible = [
        record
        for _sort_key, record in rows
        if str(record.get('id') or '').strip() not in dismissed_comms
    ]
    return visible[:PROJECT_VIEW_COMMS_LIMIT]


def _dismissed_comms(deps: ProjectViewDependencies) -> frozenset[str]:
    store = deps.state_store
    if store is None or not hasattr(store, 'load'):
        return frozenset()
    try:
        state = store.load()
    except Exception:
        return frozenset()
    return frozenset(str(item) for item in getattr(state, 'dismissed_comms', frozenset()))


def _latest_business_jobs_by_lineage(
    dispatcher,
    jobs: tuple[object, ...],
    *,
    reply_deliveries: dict[str, object],
    attempts_by_job_id: dict[str, tuple[str, int]] | None = None,
) -> tuple[object, ...]:
    if attempts_by_job_id is None:
        attempts_by_job_id = _attempt_lineage_by_job_id(dispatcher, jobs)
    latest_by_lineage: dict[tuple[str, ...], object] = {}
    for job in jobs:
        if not _is_business_comms_job(job):
            continue
        key = _comm_lineage_key(job, attempts_by_job_id)
        current = latest_by_lineage.get(key)
        if current is None or _comm_lineage_rank(job, attempts_by_job_id, reply_deliveries) >= _comm_lineage_rank(
            current,
            attempts_by_job_id,
            reply_deliveries,
        ):
            latest_by_lineage[key] = job
    return tuple(latest_by_lineage.values())


def _attempt_lineage_by_job_id(dispatcher, jobs: tuple[object, ...]) -> dict[str, tuple[str, int]]:
    return _attempt_lineage_by_job_id_from_lookup(_comms_lookup(dispatcher), jobs)


def _attempt_lineage_by_job_id_from_lookup(
    comms_lookup: _CommsLookup,
    jobs: tuple[object, ...],
) -> dict[str, tuple[str, int]]:
    result: dict[str, tuple[str, int]] = {}
    for job in jobs:
        job_id = str(getattr(job, 'job_id', '') or '').strip()
        if not job_id:
            continue
        attempt = comms_lookup.attempt_by_job_id(job_id)
        if attempt is None:
            continue
        message_id = str(getattr(attempt, 'message_id', '') or '').strip()
        if not message_id:
            continue
        try:
            retry_index = int(getattr(attempt, 'retry_index', 0) or 0)
        except Exception:
            retry_index = 0
        result[job_id] = (message_id, retry_index)
    return result


def _comms_lookup(dispatcher) -> _CommsLookup:
    control = getattr(dispatcher, '_message_bureau_control', None)
    return _CommsLookup(
        attempt_store=getattr(control, '_attempt_store', None) if control is not None else None,
        reply_store=getattr(control, '_reply_store', None) if control is not None else None,
        message_store=getattr(control, '_message_store', None) if control is not None else None,
    )


def _call_store(store, method_name: str, *args, **kwargs):
    method = getattr(store, method_name, None)
    if not callable(method):
        return None
    try:
        return method(*args, **kwargs)
    except AssertionError:
        raise
    except Exception:
        return None


def _comm_lineage_key(job, attempts_by_job_id: dict[str, tuple[str, int]]) -> tuple[str, ...]:
    job_id = str(getattr(job, 'job_id', '') or '').strip()
    target = str(getattr(job, 'target_name', '') or getattr(job, 'agent_name', '') or '').strip()
    attempt = attempts_by_job_id.get(job_id)
    if attempt is None:
        return ('job', job_id)
    return ('message', attempt[0], target)


def _comm_lineage_rank(job, attempts_by_job_id: dict[str, tuple[str, int]], reply_deliveries: dict[str, object]) -> tuple[int, int, str]:
    job_id = str(getattr(job, 'job_id', '') or '').strip()
    attempt = attempts_by_job_id.get(job_id)
    lineage_rank = 1 if attempt is not None else 0
    retry_index = attempt[1] if attempt is not None else 0
    return (lineage_rank, retry_index, _comm_sort_key(job, reply_deliveries.get(job_id)))


def _project_jobs(
    dispatcher,
    *,
    active_jobs: dict[str, object],
    queued_jobs: dict[str, tuple],
    metrics_context: _ProjectViewMetricsContext | None = None,
) -> tuple[object, ...]:
    jobs = []
    for job in active_jobs.values():
        jobs.append(job)
    for items in queued_jobs.values():
        jobs.extend(items)
    jobs.extend(_recent_jobs(dispatcher, metrics_context=metrics_context))
    by_id = {}
    for job in jobs:
        by_id[job.job_id] = job
    return tuple(by_id.values())


def _with_reply_delivery_sources(
    dispatcher,
    jobs: tuple[object, ...],
    *,
    reply_deliveries: dict[str, object],
) -> tuple[object, ...]:
    if not reply_deliveries:
        return jobs
    by_id = {str(getattr(job, 'job_id', '') or ''): job for job in jobs}
    for source_job_id in reply_deliveries:
        if not source_job_id or source_job_id in by_id:
            continue
        try:
            source = dispatcher.get(source_job_id)
        except Exception:
            source = None
        if source is not None:
            by_id[source_job_id] = source
    return tuple(by_id.values())


def _running_recover_hint(context: _ProjectViewBuildContext, *, job, generated_at: str) -> str | None:
    if getattr(job, 'status', None) is not JobStatus.RUNNING:
        return None
    agent_name = str(getattr(job, 'agent_name', '') or '').strip()
    deps = context.deps
    runtime = deps.registry.get(agent_name) if agent_name else None
    if runtime is None:
        return None
    provider = str(getattr(deps.config.agents.get(agent_name), 'provider', '') or '').strip().lower()
    if provider != 'claude':
        return None
    pane_text = context.pane_text_hint(getattr(runtime, 'pane_id', None))
    age = _job_age_seconds(generated_at, getattr(job, 'updated_at', None))
    if (
        age is not None
        and age > PROVIDER_INPUT_STUCK_AFTER_S
        and provider_prompt_idle_after_request(pane_text, getattr(job, 'job_id', None))
    ):
        return 'provider_prompt_idle'
    if (
        age is not None
        and age > PROVIDER_INPUT_STUCK_AFTER_S
        and provider_prompt_input_stuck(pane_text, getattr(job, 'job_id', None))
    ):
        return 'provider_prompt_input_stuck'
    if age is not None and age > 120 and provider_prompt_idle(pane_text):
        return 'provider_prompt_idle_stale'
    return None


def _job_age_seconds(now: str, timestamp: str | None) -> float | None:
    if not timestamp:
        return None
    try:
        return (parse_utc_timestamp(now) - parse_utc_timestamp(timestamp)).total_seconds()
    except Exception:
        return None


def _recent_jobs(
    dispatcher,
    *,
    metrics_context: _ProjectViewMetricsContext | None = None,
) -> tuple[object, ...]:
    store = getattr(dispatcher, '_job_store', None)
    config = getattr(dispatcher, '_config', None)
    agents = getattr(config, 'agents', {}) if config is not None else {}
    if store is None:
        return ()
    jobs: list[object] = []
    agent_names = tuple(agents)
    initial_scan_limit = _recent_job_initial_scan_limit(
        agent_count=len(agent_names),
        result_limit=_RECENT_JOB_RESULT_LIMIT,
        max_per_agent=_RECENT_JOB_SCAN_LIMIT_PER_AGENT,
    )
    if hasattr(store, 'list_project_view_recent_jobs'):
        try:
            if metrics_context is not None:
                metrics_context.store_scan_count += len(agent_names)
            jobs = list(
                store.list_project_view_recent_jobs(
                    agent_names,
                    per_agent_limit=_RECENT_JOB_SCAN_LIMIT_PER_AGENT,
                    per_agent_initial_limit=initial_scan_limit,
                    result_limit=_RECENT_JOB_RESULT_LIMIT,
                    statuses=tuple(status.value for status in _COMMS_RECENT_STATUSES),
                )
            )
        except Exception:
            jobs = []
        return tuple(sorted(jobs, key=lambda item: item.updated_at, reverse=True)[:_RECENT_JOB_RESULT_LIMIT])
    if hasattr(store, 'list_agent_tails_batch'):
        try:
            if metrics_context is not None:
                metrics_context.store_scan_count += len(agent_names)
            records_by_agent = store.list_agent_tails_batch(agent_names, limit=initial_scan_limit)
        except Exception:
            records_by_agent = {}
        for agent_name in agents:
            records = records_by_agent.get(agent_name, ()) if isinstance(records_by_agent, dict) else ()
            latest_by_job: dict[str, object] = {}
            for record in records:
                latest_by_job[record.job_id] = record
            jobs.extend(
                record
                for record in latest_by_job.values()
                if getattr(record, 'status', None) in _COMMS_RECENT_STATUSES
            )
        return tuple(sorted(jobs, key=lambda item: item.updated_at, reverse=True)[:_RECENT_JOB_RESULT_LIMIT])
    for agent_name in agents:
        try:
            if metrics_context is not None:
                metrics_context.store_scan_count += 1
            if hasattr(store, 'list_agent_tail'):
                records = store.list_agent_tail(agent_name, limit=initial_scan_limit)
            else:
                records = store.list_agent(agent_name)
        except Exception:
            continue
        latest_by_job: dict[str, object] = {}
        for record in records:
            latest_by_job[record.job_id] = record
        jobs.extend(
            record
            for record in latest_by_job.values()
            if getattr(record, 'status', None) in _COMMS_RECENT_STATUSES
        )
    return tuple(sorted(jobs, key=lambda item: item.updated_at, reverse=True)[:_RECENT_JOB_RESULT_LIMIT])


def _recent_job_initial_scan_limit(*, agent_count: int, result_limit: int, max_per_agent: int) -> int:
    if agent_count <= 0 or result_limit <= 0 or max_per_agent <= 0:
        return 0
    per_agent = ((result_limit + agent_count - 1) // agent_count) * 2
    per_agent = max(_RECENT_JOB_INITIAL_SCAN_MIN, per_agent)
    per_agent = min(_RECENT_JOB_INITIAL_SCAN_MAX, per_agent)
    return min(max_per_agent, per_agent)


def _configured_agent_names(dispatcher) -> frozenset[str]:
    config = getattr(dispatcher, '_config', None)
    agents = getattr(config, 'agents', {}) if config is not None else {}
    return frozenset(str(name) for name in agents)


def _reply_deliveries_by_source_job_id(
    dispatcher,
    jobs: tuple[object, ...],
    *,
    comms_lookup: _CommsLookup | None = None,
) -> dict[str, object]:
    if comms_lookup is None:
        comms_lookup = _comms_lookup(dispatcher)
    result: dict[str, object] = {}
    for job in jobs:
        if not _is_reply_delivery_job(job):
            continue
        source_job_id = _reply_delivery_source_job_id(dispatcher, job, comms_lookup=comms_lookup)
        if not source_job_id:
            continue
        current = result.get(source_job_id)
        if current is None or str(job.updated_at) >= str(current.updated_at):
            result[source_job_id] = job
    return result


def _is_business_comms_job(job) -> bool:
    if _is_reply_delivery_job(job):
        return False
    message_type = str(getattr(job.request, 'message_type', '') or '').strip().lower()
    return message_type in {'ask', ''}


def _is_reply_delivery_job(job) -> bool:
    message_type = str(getattr(job.request, 'message_type', '') or '').strip().lower()
    if message_type == _REPLY_DELIVERY_MESSAGE_TYPE:
        return True
    return bool((getattr(job, 'provider_options', {}) or {}).get(_REPLY_DELIVERY_PROVIDER_OPTION))


def _reply_delivery_source_job_id(
    dispatcher,
    job,
    *,
    comms_lookup: _CommsLookup | None = None,
) -> str | None:
    if comms_lookup is None:
        comms_lookup = _comms_lookup(dispatcher)
    return (
        _reply_delivery_source_job_id_from_reply_record(dispatcher, job, comms_lookup=comms_lookup)
        or _reply_delivery_source_job_id_from_message_origin(dispatcher, job, comms_lookup=comms_lookup)
        or _reply_delivery_source_job_id_from_body(job)
    )


def _reply_delivery_source_job_id_from_reply_record(
    dispatcher,
    job,
    *,
    comms_lookup: _CommsLookup,
) -> str | None:
    reply_id = str((getattr(job, 'provider_options', {}) or {}).get(_REPLY_DELIVERY_REPLY_ID_OPTION) or '').strip()
    if not reply_id:
        return None
    try:
        reply = comms_lookup.reply_by_reply_id(reply_id)
        if reply is None:
            return None
        return _source_job_id_from_attempt_lookup(
            comms_lookup,
            attempt_id=getattr(reply, 'attempt_id', None),
            message_id=getattr(reply, 'message_id', None),
            exclude_job_id=getattr(job, 'job_id', None),
        )
    except Exception:
        return None


def _reply_delivery_source_job_id_from_message_origin(
    dispatcher,
    job,
    *,
    comms_lookup: _CommsLookup,
) -> str | None:
    try:
        delivery_attempt = comms_lookup.attempt_by_job_id(getattr(job, 'job_id', None))
        if delivery_attempt is None:
            return None
        delivery_message = comms_lookup.message_by_message_id(getattr(delivery_attempt, 'message_id', None))
        origin_message_id = str(getattr(delivery_message, 'origin_message_id', '') or '').strip()
        if not origin_message_id:
            return None
        return _source_job_id_from_attempt_lookup(
            comms_lookup,
            message_id=origin_message_id,
            exclude_job_id=getattr(job, 'job_id', None),
        )
    except Exception:
        return None


def _source_job_id_from_attempt_lookup(
    comms_lookup: _CommsLookup,
    *,
    attempt_id: str | None = None,
    message_id: str | None = None,
    exclude_job_id: str | None = None,
) -> str | None:
    excluded = str(exclude_job_id or '').strip()
    attempt_key = str(attempt_id or '').strip()
    if attempt_key:
        attempt = comms_lookup.attempt_by_attempt_id(attempt_key)
        job_id = _attempt_job_id(attempt, exclude_job_id=excluded)
        if job_id:
            return job_id
    message_key = str(message_id or '').strip()
    if message_key:
        attempt = comms_lookup.latest_attempt_by_message_id(message_key, exclude_job_id=excluded)
        job_id = _attempt_job_id(attempt, exclude_job_id=excluded)
        if job_id:
            return job_id
    return None


def _recoverability_lineage_lookup(dispatcher, comms_lookup: _CommsLookup):
    def lookup(job):
        job_id = str(getattr(job, 'job_id', '') or '').strip()
        if not job_id:
            return None
        attempt = comms_lookup.attempt_by_job_id(job_id)
        if attempt is None:
            return None
        message_id = str(getattr(attempt, 'message_id', '') or '').strip()
        agent_name = str(getattr(attempt, 'agent_name', '') or '').strip()
        latest_attempt = comms_lookup.latest_attempt_for_message_agent(message_id, agent_name)
        return _ProjectViewLineage(attempt=attempt, latest_attempt=latest_attempt, inbound=None)

    return lookup


@dataclass(frozen=True)
class _ProjectViewLineage:
    attempt: object
    latest_attempt: object | None
    inbound: object | None


def _attempt_job_id(attempt, *, exclude_job_id: str | None = None) -> str | None:
    job_id = str(getattr(attempt, 'job_id', '') or '').strip()
    if not job_id or job_id == str(exclude_job_id or '').strip():
        return None
    return job_id


def _reply_delivery_source_job_id_from_body(job) -> str | None:
    body = str(getattr(job.request, 'body', '') or '')
    first_line = body.splitlines()[0] if body else ''
    for token in first_line.split():
        key, sep, value = token.partition('=')
        if sep and key == 'job':
            text = value.strip()
            return text or None
    return None


def _comm_sort_key(job, reply_delivery) -> str:
    updated_at = str(getattr(job, 'updated_at', '') or '')
    if reply_delivery is not None:
        updated_at = max(updated_at, str(getattr(reply_delivery, 'updated_at', '') or ''))
    return updated_at


def _comm_record(
    dispatcher,
    job,
    *,
    reply_delivery,
    configured_agents: frozenset[str],
    running_recover_hint: str | None = None,
    lineage_for_recoverability=None,
) -> dict[str, object]:
    business_status, status_label = _comm_business_status(
        job,
        reply_delivery=reply_delivery,
        configured_agents=configured_agents,
    )
    if running_recover_hint and getattr(job, 'status', None) is JobStatus.RUNNING:
        business_status, status_label = 'blocked', 'stuck'
    reply_status = reply_delivery.status.value if reply_delivery is not None else None
    updated_at = _comm_sort_key(job, reply_delivery)
    recoverability = comms_recoverability_for_job(
        dispatcher,
        job,
        reply_delivery=reply_delivery,
        running_hint=running_recover_hint,
        lineage_for_job=lineage_for_recoverability,
    )
    attachments = _comm_attachments(job)
    return {
        'id': job.job_id,
        'short_id': _short_id(job.job_id),
        'created_at': job.created_at,
        'updated_at': updated_at,
        'sender': job.request.from_actor,
        'target': job.target_name,
        'status': job.status.value,
        'business_status': business_status,
        'status_label': status_label,
        'body_preview': _body_preview(job.request.body),
        'reply_status': reply_status,
        'reply_delivery_job_id': reply_delivery.job_id if reply_delivery is not None else None,
        'callback': bool(getattr(job.request, 'reply_to', None)),
        'short_reason': _short_reason(job),
        **({'attachments': attachments} if attachments else {}),
        **recoverability.to_record(),
    }


def _comm_attachments(job) -> list[dict[str, object]]:
    options = getattr(getattr(job, 'request', None), 'route_options', None)
    if not isinstance(options, dict):
        return []
    raw = options.get('attachments')
    if not isinstance(raw, (list, tuple)):
        return []
    attachments: list[dict[str, object]] = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        file_id = str(item.get('file_id') or item.get('attachment_id') or '').strip()
        if not file_id:
            continue
        file_name = str(item.get('file_name') or item.get('filename') or 'attachment').strip()
        mime_type = str(item.get('mime_type') or 'application/octet-stream').strip()
        try:
            size_bytes = int(item.get('size_bytes') or 0)
        except Exception:
            size_bytes = 0
        kind = str(item.get('kind') or '').strip()
        if not kind:
            kind = 'image' if mime_type.startswith('image/') else 'document'
        attachments.append(
            {
                'file_id': file_id,
                'file_name': file_name or 'attachment',
                'mime_type': mime_type or 'application/octet-stream',
                'size_bytes': max(0, size_bytes),
                'kind': kind,
            }
        )
    return attachments


def _comm_business_status(job, *, reply_delivery, configured_agents: frozenset[str]) -> tuple[str, str]:
    if job.status in _COMMS_PENDING_STATUSES:
        return 'sending', 'send'
    if job.status is JobStatus.RUNNING:
        return 'replying', 'work'
    if job.status is JobStatus.COMPLETED:
        if bool(getattr(job.request, 'silence_on_success', False)):
            return 'completed', 'done'
        if _expects_reply_delivery(job, configured_agents):
            if reply_delivery is None or reply_delivery.status in _COMMS_PENDING_STATUSES or reply_delivery.status is JobStatus.RUNNING:
                return 'delivering', 'back'
            if reply_delivery.status is JobStatus.COMPLETED:
                return 'replied', 'done'
            return 'delivery_failed', 'fail'
        return 'replied', 'done'
    if job.status is JobStatus.CANCELLED:
        return 'cancelled', 'fail'
    if job.status is JobStatus.INCOMPLETE:
        return 'incomplete', 'fail'
    if job.status is JobStatus.FAILED:
        return 'failed', 'fail'
    return job.status.value, job.status.value


def _expects_reply_delivery(job, configured_agents: frozenset[str]) -> bool:
    sender = str(getattr(job.request, 'from_actor', '') or '').strip()
    target = str(getattr(job, 'target_name', '') or '').strip()
    return sender in configured_agents and sender != target


def _body_preview(value: object) -> str:
    text = _clean_body_preview_text(str(value or ''))
    if len(text) <= _COMMS_BODY_PREVIEW_LIMIT:
        return text
    return f'{text[: _COMMS_BODY_PREVIEW_LIMIT - 3]}...'


def _clean_body_preview_text(value: str) -> str:
    text = ' '.join(str(value or '').split())
    if not text:
        return ''
    lowered = text.lower()
    for prefix in (
        'reply exactly:',
        'reply exactly',
        'only reply:',
        'only reply',
        'respond exactly:',
        'respond exactly',
    ):
        if lowered.startswith(prefix):
            return _summarize_token_preview(text[len(prefix):].strip(' :：'))
    for prefix in ('只回复：', '只回复:', '只回复'):
        if text.startswith(prefix):
            return _summarize_token_preview(text[len(prefix):].strip(' :：'))
    return _summarize_token_preview(text)


def _summarize_token_preview(value: str) -> str:
    text = value.strip()
    token = text.strip(' .,:;，。；：')
    if not token or ' ' in token:
        return text
    compact = token.replace('-', '_')
    if not compact.replace('_', '').isalnum():
        return text
    if compact.upper() != compact:
        return text
    parts = [part for part in compact.split('_') if part]
    if len(parts) < 2:
        return text
    suffix = parts[-1]
    if suffix not in {'OK', 'FAIL', 'SHOULD', 'DELIVER'}:
        return text
    body_parts = parts[:-1]
    if len(body_parts) == 1 or all(_looks_like_probe_part(part) for part in body_parts):
        return f'probe: {body_parts[0]}'
    return f'smoke: {" ".join(part.lower() for part in body_parts)}'


def _looks_like_probe_part(value: str) -> bool:
    text = value.strip()
    return len(text) <= 6 and any(char.isdigit() for char in text)


def _short_reason(job) -> str | None:
    decision = job.terminal_decision or {}
    reason = str(decision.get('reason') or '').strip()
    return reason or None


def _short_id(value: str) -> str:
    text = str(value or '').strip()
    return text[-4:] if len(text) > 4 else text


def _agent_order(config) -> tuple[str, ...]:
    names: list[str] = []
    for window in config.windows:
        names.extend(window.agent_names)
    return tuple(names)


def _window_by_agent(config) -> dict[str, str]:
    result: dict[str, str] = {}
    for window in config.windows:
        for agent_name in window.agent_names:
            result[agent_name] = window.name
    return result


def _active_jobs_by_agent(dispatcher) -> dict[str, object]:
    result = {}
    for target_kind, target_name, job_id in dispatcher._state.active_items():
        if target_kind is not TargetKind.AGENT:
            continue
        job = dispatcher.get(job_id)
        if job is not None:
            result[str(target_name)] = job
    return result


def _queued_jobs_by_agent(dispatcher) -> dict[str, tuple]:
    result = {}
    for target_kind, target_name in dispatcher._state.slots():
        if target_kind is not TargetKind.AGENT:
            continue
        jobs = []
        for job_id in dispatcher._state.queued_items_for(target_kind, target_name):
            job = dispatcher.get(job_id)
            if job is not None and job.status in {JobStatus.ACCEPTED, JobStatus.QUEUED}:
                jobs.append(job)
        if jobs:
            result[str(target_name)] = tuple(jobs)
    return result


def _provider_runtime_by_agent(dispatcher) -> dict[str, tuple[dict[str, object], ...]]:
    execution = getattr(dispatcher, '_execution_service', None)
    snapshotter = getattr(execution, 'active_runtime_snapshots', None)
    if not callable(snapshotter):
        return {}
    try:
        snapshots = snapshotter()
    except Exception:
        return {}
    grouped: dict[str, list[dict[str, object]]] = {}
    for snapshot in snapshots:
        if not isinstance(snapshot, dict):
            continue
        agent_name = str(snapshot.get('agent_name') or '').strip()
        if not agent_name:
            continue
        grouped.setdefault(agent_name, []).append(snapshot)
    return {agent_name: tuple(items) for agent_name, items in grouped.items()}


def _select_provider_runtime(
    snapshots: tuple[dict[str, object], ...],
    *,
    agent_name: str,
    current_job_id: object,
) -> dict[str, object] | None:
    if not snapshots:
        return None
    current = str(current_job_id or '').strip()
    if current:
        for snapshot in snapshots:
            if str(snapshot.get('job_id') or '').strip() == current:
                return snapshot
        return None
    if len(snapshots) == 1:
        return snapshots[0]
    return {
        'agent_name': agent_name,
        'conflict': 'multiple_provider_runtimes_without_control_job',
        'runtime_count': len(snapshots),
        'job_ids': [
            str(snapshot.get('job_id') or '').strip()
            for snapshot in snapshots
            if str(snapshot.get('job_id') or '').strip()
        ],
    }


def _runtime_state(runtime) -> str | None:
    if runtime is None:
        return AgentState.STOPPED.value
    return runtime.state.value


def _focus_snapshot(context: _ProjectViewBuildContext) -> dict[str, object]:
    return context.tmux_project_view_facts()[0]


__all__ = [
    'PROJECT_VIEW_SCHEMA_VERSION',
    'PROJECT_VIEW_TTL_MS',
    'ProjectViewDependencies',
    'ProjectViewService',
    'build_project_view',
]
