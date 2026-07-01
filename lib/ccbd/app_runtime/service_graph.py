from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from agents.config_identity import project_config_identity_payload
from ccbd.project_focus import ProjectFocusDependencies, ProjectFocusService
from ccbd.project_view import ProjectViewDependencies, ProjectViewService
from ccbd.services import AgentRegistry, HealthMonitor, JobDispatcher, RuntimeService
from ccbd.supervision import RuntimeSupervisionLoop
from ccbd.supervisor import RuntimeSupervisor
from completion.tracker import CompletionTrackerService
from provider_core.registry import build_default_session_binding_map

SERVICE_GRAPH_RETAINED_COUNT_SCOPE = 'published_graph_count_not_inflight_retention'


@dataclass(frozen=True)
class CcbdPingPayloadServices:
    config: object
    registry: object
    health_monitor: object


@dataclass(frozen=True)
class CcbdServiceGraph:
    version: int
    created_at: str
    config: object
    config_identity: dict
    registry: object
    runtime_service: object
    runtime_supervisor: object
    runtime_supervision: object
    completion_tracker: object
    dispatcher: object
    project_view_service: object
    project_focus_service: object
    health_monitor: object
    ping_payload_services: CcbdPingPayloadServices


@dataclass(frozen=True)
class CcbdServiceGraphDependencies:
    project_root: Path
    project_id: str
    paths: object
    config: object
    provider_catalog: object
    mount_manager: object
    lifecycle_store: object
    restore_store: object
    namespace_state_store: object
    project_view_state_store: object
    project_namespace: object
    ownership_guard: object
    startup_report_store: object
    shutdown_report_store: object
    start_policy_store: object
    execution_service: object
    snapshot_writer: object
    control_plane_metrics: object
    clock: Callable[[], str]
    request_timeout_s: float
    daemon_generation_getter: Callable[[], int | None]
    mount_agent_fn: Callable[[str], None] | None = None
    remount_project_fn: Callable[[str], None] | None = None
    mount_missing_runtime_fn: Callable[[str], bool] | None = None
    supervision_suspended_fn: Callable[[], bool] | None = None
    version: int = 1
    created_at: str | None = None
    providers: frozenset[str] | None = None


def build_ccbd_service_graph(deps: CcbdServiceGraphDependencies) -> CcbdServiceGraph:
    config_identity = project_config_identity_payload(deps.config)
    registry = AgentRegistry(deps.paths, deps.config)
    session_bindings = build_default_session_binding_map(providers=deps.providers)
    runtime_service = RuntimeService(
        deps.paths,
        registry,
        deps.project_id,
        deps.restore_store,
        session_bindings=session_bindings,
        daemon_generation_getter=deps.daemon_generation_getter,
        clock=deps.clock,
    )
    runtime_supervisor = RuntimeSupervisor(
        project_root=deps.project_root,
        project_id=deps.project_id,
        paths=deps.paths,
        config=deps.config,
        registry=registry,
        runtime_service=runtime_service,
        project_namespace=deps.project_namespace,
        clock=deps.clock,
        mount_manager=deps.mount_manager,
        ownership_guard=deps.ownership_guard,
        startup_report_store=deps.startup_report_store,
        shutdown_report_store=deps.shutdown_report_store,
        start_policy_store=deps.start_policy_store,
    )
    runtime_supervision = RuntimeSupervisionLoop(
        project_id=deps.project_id,
        layout=deps.paths,
        config=deps.config,
        registry=registry,
        runtime_service=runtime_service,
        mount_agent_fn=deps.mount_agent_fn,
        remount_project_fn=deps.remount_project_fn,
        clock=deps.clock,
        generation_getter=deps.daemon_generation_getter,
        mount_missing_runtime_fn=deps.mount_missing_runtime_fn,
        supervision_suspended_fn=deps.supervision_suspended_fn,
    )
    completion_tracker = CompletionTrackerService(
        deps.config,
        deps.provider_catalog,
        request_timeout_s=deps.request_timeout_s,
    )
    dispatcher = JobDispatcher(
        deps.paths,
        deps.config,
        registry,
        runtime_service=runtime_service,
        execution_service=deps.execution_service,
        auto_reply_delivery_on_complete=True,
        require_actionable_runtime_binding_for_execution=True,
        completion_tracker=completion_tracker,
        provider_catalog=deps.provider_catalog,
        snapshot_writer=deps.snapshot_writer,
        timing_sink=deps.control_plane_metrics,
        clock=deps.clock,
    )
    project_view_service = ProjectViewService(
        ProjectViewDependencies(
            project_root=deps.project_root,
            project_id=deps.project_id,
            config=deps.config,
            registry=registry,
            mount_manager=deps.mount_manager,
            namespace_state_store=deps.namespace_state_store,
            dispatcher=dispatcher,
            namespace_controller=deps.project_namespace,
            state_store=deps.project_view_state_store,
            paths=deps.paths,
            clock=deps.clock,
            metrics=deps.control_plane_metrics,
        )
    )
    project_focus_service = ProjectFocusService(
        ProjectFocusDependencies(
            project_id=deps.project_id,
            config=deps.config,
            namespace_controller=deps.project_namespace,
            project_view_service=project_view_service,
        )
    )
    health_monitor = HealthMonitor(
        registry,
        deps.ownership_guard,
        project_id=deps.project_id,
        lifecycle_store=deps.lifecycle_store,
        runtime_service=runtime_service,
        clock=deps.clock,
        session_bindings=session_bindings,
        namespace_state_store=deps.namespace_state_store,
    )
    return CcbdServiceGraph(
        version=int(deps.version),
        created_at=deps.created_at or deps.clock(),
        config=deps.config,
        config_identity=config_identity,
        registry=registry,
        runtime_service=runtime_service,
        runtime_supervisor=runtime_supervisor,
        runtime_supervision=runtime_supervision,
        completion_tracker=completion_tracker,
        dispatcher=dispatcher,
        project_view_service=project_view_service,
        project_focus_service=project_focus_service,
        health_monitor=health_monitor,
        ping_payload_services=CcbdPingPayloadServices(
            config=deps.config,
            registry=registry,
            health_monitor=health_monitor,
        ),
    )


def current_ccbd_service_graph(app) -> CcbdServiceGraph:
    graph = getattr(app, 'service_graph', None)
    if graph is None:
        raise RuntimeError('ccbd service graph is not published')
    return graph


def publish_ccbd_service_graph(app, graph: CcbdServiceGraph) -> CcbdServiceGraph:
    lock = getattr(app, '_service_graph_publish_lock', None)
    if lock is None:
        _publish_service_graph_fields(app, graph)
        return graph
    with lock:
        _publish_service_graph_fields(app, graph)
    return graph


def _publish_service_graph_fields(app, graph: CcbdServiceGraph) -> None:
    app.config = graph.config
    app.config_identity = graph.config_identity
    app.registry = graph.registry
    app.runtime_service = graph.runtime_service
    app.runtime_supervisor = graph.runtime_supervisor
    app.runtime_supervision = graph.runtime_supervision
    app.completion_tracker = graph.completion_tracker
    app.dispatcher = graph.dispatcher
    app.project_view_service = graph.project_view_service
    app.project_focus_service = graph.project_focus_service
    app.health_monitor = graph.health_monitor
    app.service_graph = graph
    app.control_plane_metrics.service_graph_version = graph.version
    app.control_plane_metrics.service_graph_created_at = graph.created_at
    app.control_plane_metrics.service_graph_retained_count = 1
    app.control_plane_metrics.service_graph_retained_count_scope = SERVICE_GRAPH_RETAINED_COUNT_SCOPE


__all__ = [
    'CcbdPingPayloadServices',
    'CcbdServiceGraph',
    'CcbdServiceGraphDependencies',
    'build_ccbd_service_graph',
    'current_ccbd_service_graph',
    'publish_ccbd_service_graph',
]
