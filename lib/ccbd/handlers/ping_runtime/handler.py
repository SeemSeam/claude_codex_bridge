from __future__ import annotations

from time import monotonic

from .payloads import build_agent_payload, build_ccbd_payload
from .summaries import (
    load_namespace_event_summary,
    load_namespace_summary,
    load_restore_summary,
    load_start_policy_summary,
)


def build_ping_handler(
    *,
    project_id: str,
    config,
    paths,
    registry,
    health_monitor,
    execution_state_store=None,
    execution_registry=None,
    restore_report_store=None,
    namespace_state_store=None,
    namespace_event_store=None,
    start_policy_store=None,
    metrics=None,
):
    def handle(payload: dict) -> dict:
        started = monotonic()
        target = str(payload.get('target') or '').strip().lower()
        try:
            daemon_health = getattr(health_monitor, 'local_daemon_health', health_monitor.daemon_health)
            inspection = daemon_health()
            if target in {'', 'ccbd'}:
                execution_summary = execution_state_store.summary() if execution_state_store is not None else {}
                restore_summary = load_restore_summary(restore_report_store)
                namespace_summary = load_namespace_summary(namespace_state_store)
                namespace_event_summary = load_namespace_event_summary(namespace_event_store)
                start_policy_summary = load_start_policy_summary(start_policy_store)
                return build_ccbd_payload(
                    project_id=project_id,
                    config=config,
                    paths=paths,
                    inspection=inspection,
                    execution_summary=execution_summary,
                    restore_summary=restore_summary,
                    namespace_summary=namespace_summary,
                    namespace_event_summary=namespace_event_summary,
                    start_policy_summary=start_policy_summary,
                    control_plane_metrics=metrics,
                )
            if target == 'all':
                return {
                    'project_id': project_id,
                    'ccbd_state': str(getattr(inspection, 'phase', '') or '').strip()
                    or str(getattr(getattr(getattr(inspection, 'lease', None), 'mount_state', None), 'value', '') or 'unmounted'),
                    'agents': [
                        build_agent_payload(
                            project_id=project_id,
                            agent_name=name,
                            registry=registry,
                            inspection=inspection,
                            execution_registry=execution_registry,
                            provider_pane_assessment=_provider_pane_assessment_for(health_monitor, registry, name),
                        )
                        for name in registry.list_known_agents()
                    ],
                }
            return build_agent_payload(
                project_id=project_id,
                agent_name=target,
                registry=registry,
                inspection=inspection,
                execution_registry=execution_registry,
                provider_pane_assessment=_provider_pane_assessment_for(health_monitor, registry, target),
            )
        finally:
            if metrics is not None:
                metrics.last_ping_duration_s = max(0.0, monotonic() - started)

    return handle


def _provider_pane_assessment_for(health_monitor, registry, agent_name):
    """Best-effort fresh ProviderPaneAssessment for an agent, or None.

    Uses the health monitor's wired assess_provider_pane with the monitor's
    own registry/session_bindings/namespace_state_store. Returns None on any
    miss (runtime not started, no session, monitor stub without the seam) so
    ping stays healthy when pane content visibility is unavailable.
    """
    if health_monitor is None:
        return None
    assess = getattr(health_monitor, '_assess_provider_pane', None)
    if not callable(assess):
        return None
    try:
        runtime = registry.get(agent_name)
    except Exception:
        return None
    if runtime is None:
        return None
    inner_registry = getattr(health_monitor, '_registry', None) or registry
    session_bindings = getattr(health_monitor, '_session_bindings', None)
    namespace_state_store = getattr(health_monitor, '_namespace_state_store', None)
    try:
        return assess(
            runtime=runtime,
            registry=inner_registry,
            session_bindings=session_bindings,
            namespace_state_store=namespace_state_store,
        )
    except Exception:
        return None


__all__ = ['build_ping_handler']
