from __future__ import annotations

from pathlib import Path
import os
import threading
import uuid

from agents.config_loader import load_project_config
from agents.store import AgentRestoreStore
from ccbd.lifecycle_report_store import CcbdShutdownReportStore, CcbdStartupReportStore
from ccbd.metrics import ControlPlaneMetrics
from ccbd.project_view import ProjectViewStateStore
from ccbd.reload_drain import DrainQueueStore
from ccbd.restore_report_store import CcbdRestoreReportStore
from ccbd.services import (
    CcbdLifecycleStore,
    JobHeartbeatService,
    MountManager,
    OwnershipGuard,
    SnapshotWriter,
)
from ccbd.services.project_namespace import ProjectNamespaceController
from ccbd.services.project_namespace_state import ProjectNamespaceEventStore, ProjectNamespaceStateStore
from ccbd.services.start_policy import CcbdStartPolicyStore
from ccbd.socket_server import CcbdSocketServer
from fault_injection import FaultInjectionService
from heartbeat import HeartbeatPolicy, HeartbeatStateStore
from mobile_gateway.project_registry import publish_mobile_gateway_project
from project.ids import compute_project_id
from provider_core.catalog import build_default_provider_catalog
from provider_execution.registry import build_default_execution_registry
from provider_execution.service import ExecutionService
from provider_execution.state_store import ExecutionStateStore
from storage.paths import PathLayout
from storage.text_artifacts import sweep_expired_text_artifacts

from .handlers import register_handlers
from .request_guard import lifecycle_is_stopping, rejection_for_request
from .service_graph import CcbdServiceGraphDependencies, build_ccbd_service_graph, publish_ccbd_service_graph

APP_REQUEST_TIMEOUT_S = 0.0
JOB_HEARTBEAT_SILENCE_START_AFTER_S = 600.0
JOB_HEARTBEAT_REPEAT_INTERVAL_S = 600.0


def initialize_app(app, project_root: str | Path, *, clock, pid: int | None) -> None:
    app.project_root = Path(project_root).expanduser().resolve()
    app.project_id = compute_project_id(app.project_root)
    app.paths = PathLayout(app.project_root)
    app.paths.ensure_runtime_state_root()
    _publish_mobile_gateway_project(app.project_id, app.project_root, app.paths.ccbd_socket_path, clock=clock)
    sweep_expired_text_artifacts(app.paths)
    app.clock = clock
    app.pid = pid or os.getpid()
    config = load_project_config(app.project_root).config
    keeper_pid = str(os.environ.get('CCB_KEEPER_PID') or '').strip()
    app.keeper_pid = int(keeper_pid) if keeper_pid.isdigit() and int(keeper_pid) > 0 else None
    app.daemon_instance_id = uuid.uuid4().hex
    app.start_maintenance_lock = threading.Lock()
    app._service_graph_publish_lock = threading.Lock()
    _configured_providers = frozenset(
        str(spec.provider or '').strip().lower() for spec in config.agents.values()
    )
    app.provider_catalog = build_default_provider_catalog(providers=_configured_providers)
    app.mount_manager = MountManager(app.paths, clock=app.clock)
    app.lifecycle_store = CcbdLifecycleStore(app.paths)
    app.restore_report_store = CcbdRestoreReportStore(app.paths)
    app.startup_report_store = CcbdStartupReportStore(app.paths)
    app.shutdown_report_store = CcbdShutdownReportStore(app.paths)
    app.namespace_state_store = ProjectNamespaceStateStore(app.paths)
    app.namespace_event_store = ProjectNamespaceEventStore(app.paths)
    app.project_view_state_store = ProjectViewStateStore(app.paths, project_id=app.project_id)
    app.start_policy_store = CcbdStartPolicyStore(app.paths)
    app.reload_drain_store = DrainQueueStore(app.paths)
    app.ownership_guard = OwnershipGuard(app.paths, app.mount_manager, clock=app.clock)
    app.restore_store = AgentRestoreStore(app.paths)
    app.project_namespace = ProjectNamespaceController(app.paths, app.project_id, clock=app.clock)
    app.snapshot_writer = SnapshotWriter(app.paths, clock=app.clock)
    app.execution_registry = build_default_execution_registry(providers=_configured_providers)
    app.fault_injection = FaultInjectionService(app.paths, clock=app.clock)
    app.execution_service = ExecutionService(
        app.execution_registry,
        clock=app.clock,
        state_store=ExecutionStateStore(app.paths),
        fault_injection=app.fault_injection,
    )
    app.control_plane_metrics = ControlPlaneMetrics()
    app.lease = None
    app.runtime_accelerator = None
    service_graph = build_ccbd_service_graph(
        CcbdServiceGraphDependencies(
            project_root=app.project_root,
            project_id=app.project_id,
            paths=app.paths,
            config=config,
            provider_catalog=app.provider_catalog,
            mount_manager=app.mount_manager,
            lifecycle_store=app.lifecycle_store,
            restore_store=app.restore_store,
            namespace_state_store=app.namespace_state_store,
            project_view_state_store=app.project_view_state_store,
            project_namespace=app.project_namespace,
            ownership_guard=app.ownership_guard,
            startup_report_store=app.startup_report_store,
            shutdown_report_store=app.shutdown_report_store,
            start_policy_store=app.start_policy_store,
            execution_service=app.execution_service,
            snapshot_writer=app.snapshot_writer,
            control_plane_metrics=app.control_plane_metrics,
            clock=app.clock,
            request_timeout_s=APP_REQUEST_TIMEOUT_S,
            daemon_generation_getter=lambda: app.lease.generation if app.lease is not None else None,
            mount_agent_fn=app._mount_agent_from_policy,
            remount_project_fn=app._remount_project_from_policy,
            mount_missing_runtime_fn=lambda agent_name: app._mount_missing_runtime_requested(agent_name),
            supervision_suspended_fn=lambda: lifecycle_is_stopping(_safe_load_lifecycle(app)),
            version=1,
            providers=_configured_providers,
        )
    )
    publish_ccbd_service_graph(app, service_graph)
    app.heartbeat_state_store = HeartbeatStateStore(app.paths)
    app.job_heartbeat = JobHeartbeatService(
        app.paths,
        policy=HeartbeatPolicy(
            silence_start_after_s=JOB_HEARTBEAT_SILENCE_START_AFTER_S,
            repeat_interval_s=JOB_HEARTBEAT_REPEAT_INTERVAL_S,
        ),
        store=app.heartbeat_state_store,
        clock=app.clock,
    )
    app.socket_server = CcbdSocketServer(app.paths.ccbd_socket_path)
    app.socket_server._record_request_queue_wait = lambda value: setattr(
        app.control_plane_metrics,
        'last_request_queue_wait_s',
        value,
    )
    app.socket_server._record_pending_maintenance_ticks = lambda value: setattr(
        app.control_plane_metrics,
        'pending_maintenance_ticks',
        value,
    )
    app.socket_server._record_handler_latency = lambda op, value: app.control_plane_metrics.last_handler_latency_s_by_op.__setitem__(
        str(op or ''),
        value,
    )
    app.socket_server.set_request_guard(lambda op: rejection_for_request(app, op))
    app.project_stop_requested = False
    register_handlers(app)


def _safe_load_lifecycle(app):
    try:
        return app.lifecycle_store.load()
    except Exception:
        return None


def _publish_mobile_gateway_project(project_id: str, project_root: Path, ccbd_socket_path: Path, *, clock) -> None:
    try:
        publish_mobile_gateway_project(
            project_id=project_id,
            project_root=project_root,
            ccbd_socket_path=ccbd_socket_path,
            display_name=project_root.name,
            updated_at=clock(),
        )
    except Exception:
        pass


__all__ = ['initialize_app']
