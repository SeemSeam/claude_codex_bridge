from __future__ import annotations

import importlib
import threading


def _lazy_handler(module_name: str, builder_name: str, *builder_args, **builder_kwargs):
    """Return a handler callable that imports and builds the real handler on first use."""
    builder = None
    handler = None

    def handle(payload: dict) -> dict:
        nonlocal builder, handler
        if handler is None:
            if builder is None:
                mod = importlib.import_module(f'ccbd.handlers.{module_name}')
                builder = getattr(mod, builder_name)
            handler = builder(*builder_args, **builder_kwargs)
        return handler(payload)

    return handle


def register_handlers(app) -> None:
    graph_source = _GraphSource(app)
    dispatcher = _GraphServiceProxy(graph_source, 'dispatcher')
    health_monitor = _GraphServiceProxy(graph_source, 'health_monitor')
    project_view_service = _GraphServiceProxy(graph_source, 'project_view_service')
    project_focus_service = _GraphServiceProxy(graph_source, 'project_focus_service')
    runtime_service = _GraphServiceProxy(graph_source, 'runtime_service')
    ping_graph = _GraphPingDependencies(graph_source)

    app.socket_server.register_handler(
        'submit', _graph_request(graph_source, _lazy_handler('submit', 'build_submit_handler', dispatcher))
    )
    app.socket_server.register_handler(
        'get',
        _graph_request(
            graph_source,
            _lazy_handler('get', 'build_get_handler', dispatcher, health_monitor=health_monitor),
        ),
    )
    app.socket_server.register_handler(
        'watch',
        _graph_request(
            graph_source,
            _lazy_handler('watch', 'build_watch_handler', dispatcher, health_monitor=health_monitor),
        ),
    )
    app.socket_server.register_handler(
        'queue', _graph_request(graph_source, _lazy_handler('queue', 'build_queue_handler', dispatcher))
    )
    app.socket_server.register_handler(
        'trace', _graph_request(graph_source, _lazy_handler('trace', 'build_trace_handler', dispatcher))
    )
    app.socket_server.register_handler(
        'resubmit', _graph_request(graph_source, _lazy_handler('resubmit', 'build_resubmit_handler', dispatcher))
    )
    app.socket_server.register_handler(
        'retry', _graph_request(graph_source, _lazy_handler('retry', 'build_retry_handler', dispatcher))
    )
    app.socket_server.register_handler(
        'comms_recover',
        _graph_request(graph_source, _lazy_handler('comms_recover', 'build_comms_recover_handler', dispatcher)),
    )
    app.socket_server.register_handler(
        'inbox', _graph_request(graph_source, _lazy_handler('inbox', 'build_inbox_handler', dispatcher))
    )
    app.socket_server.register_handler(
        'mailbox_head',
        _graph_request(graph_source, _lazy_handler('mailbox_head', 'build_mailbox_head_handler', dispatcher)),
    )
    app.socket_server.register_handler(
        'ack', _graph_request(graph_source, _lazy_handler('ack', 'build_ack_handler', dispatcher))
    )
    app.socket_server.register_handler(
        'cancel', _graph_request(graph_source, _lazy_handler('cancel', 'build_cancel_handler', dispatcher))
    )
    app.socket_server.register_handler(
        'project_view',
        _graph_request(graph_source, _lazy_handler('project_view', 'build_project_view_handler', project_view_service)),
    )
    app.socket_server.register_handler(
        'project_view_dismiss_comms',
        _lazy_handler(
            'project_view', 'build_project_view_dismiss_comms_handler', app.project_view_state_store
        ),
    )
    app.socket_server.register_handler(
        'project_focus_window',
        _graph_request(
            graph_source,
            _lazy_handler('project_focus', 'build_project_focus_window_handler', project_focus_service),
        ),
    )
    app.socket_server.register_handler(
        'project_focus_agent',
        _graph_request(
            graph_source,
            _lazy_handler('project_focus', 'build_project_focus_agent_handler', project_focus_service),
        ),
    )
    app.socket_server.register_handler(
        'project_sidebar_click',
        _graph_request(
            graph_source,
            _lazy_handler(
                'project_focus',
                'build_project_sidebar_click_handler',
                project_view_service,
                project_focus_service,
            ),
        ),
    )
    app.socket_server.register_handler(
        'project_restart_panes',
        _graph_request(
            graph_source,
            _lazy_handler(
                'project_restart', 'build_project_restart_panes_handler', _GraphAppProxy(app, graph_source)
            ),
        ),
    )
    app.socket_server.register_handler(
        'project_restart_agent',
        _graph_request(
            graph_source,
            _lazy_handler(
                'project_restart', 'build_project_restart_agent_handler', _GraphAppProxy(app, graph_source)
            ),
        ),
    )
    app.socket_server.register_handler(
        'project_clear_context',
        _graph_request(
            graph_source,
            _lazy_handler(
                'project_clear', 'build_project_clear_context_handler', _GraphAppProxy(app, graph_source)
            ),
        ),
    )
    app.socket_server.register_handler(
        'project_reload_config',
        _lazy_handler('project_reload', 'build_project_reload_config_handler', app, graph_source.current),
    )
    app.socket_server.register_handler(
        'ping',
        _graph_request(
            graph_source,
            _lazy_handler(
                'ping',
                'build_ping_handler',
                project_id=app.project_id,
                config=ping_graph,
                paths=app.paths,
                registry=ping_graph,
                health_monitor=ping_graph,
                execution_state_store=app.execution_service._state_store,
                execution_registry=app.execution_registry,
                restore_report_store=app.restore_report_store,
                namespace_state_store=app.namespace_state_store,
                namespace_event_store=app.namespace_event_store,
                start_policy_store=app.start_policy_store,
                metrics=app.control_plane_metrics,
            ),
        ),
    )
    app.socket_server.register_handler(
        'attach', _graph_request(graph_source, _lazy_handler('attach', 'build_attach_handler', runtime_service))
    )
    app.socket_server.register_handler('start', _lazy_handler('start', 'build_start_handler', app))
    app.socket_server.register_handler(
        'restore', _graph_request(graph_source, _lazy_handler('restore', 'build_restore_handler', runtime_service))
    )
    app.socket_server.register_handler('stop-all', _lazy_handler('stop_all', 'build_stop_all_handler', app))
    app.socket_server.register_handler('shutdown', _lazy_handler('shutdown', 'build_shutdown_handler', app))


class _GraphSource:
    def __init__(self, app) -> None:
        self._app = app
        self._request = threading.local()

    def current(self):
        graph = getattr(self._request, 'graph', None)
        if graph is not None:
            return graph
        current = getattr(self._app, 'current_service_graph', None)
        if callable(current):
            return current()
        graph = getattr(self._app, 'service_graph', None)
        if graph is None:
            raise RuntimeError('ccbd service graph is not published')
        return graph

    def run(self, handler, payload: dict) -> dict:
        graph = self.current()
        previous = getattr(self._request, 'graph', None)
        self._request.graph = graph
        try:
            result = handler(payload)
            if isinstance(result, tuple) and len(result) == 2 and callable(result[1]):
                payload_result, after_response_action = result
                return payload_result, self._wrap_action(graph, after_response_action)
            return result
        finally:
            self._set_request_graph(previous)

    def _wrap_action(self, graph, action):
        def run_action() -> None:
            previous = getattr(self._request, 'graph', None)
            self._request.graph = graph
            try:
                action()
            finally:
                self._set_request_graph(previous)

        return run_action

    def _set_request_graph(self, graph) -> None:
        if graph is None:
            try:
                del self._request.graph
            except AttributeError:
                pass
            return
        self._request.graph = graph


class _GraphServiceProxy:
    def __init__(self, graph_source: _GraphSource, service_name: str) -> None:
        self._graph_source = graph_source
        self._service_name = service_name

    def _service(self):
        return getattr(self._graph_source.current(), self._service_name)

    def __getattr__(self, name: str):
        return getattr(self._service(), name)


class _GraphPingDependencies:
    def __init__(self, graph_source: _GraphSource) -> None:
        self._graph_source = graph_source

    def _graph(self):
        return self._graph_source.current()

    def _ping_services(self):
        return self._graph().ping_payload_services

    @property
    def agents(self):
        return self._ping_services().config.agents

    def to_record(self):
        return self._ping_services().config.to_record()

    def list_known_agents(self):
        return self._ping_services().registry.list_known_agents()

    def spec_for(self, agent_name: str):
        return self._ping_services().registry.spec_for(agent_name)

    def get(self, agent_name: str):
        return self._ping_services().registry.get(agent_name)

    def daemon_health(self):
        return self._ping_services().health_monitor.daemon_health()

    def local_daemon_health(self):
        return self._ping_services().health_monitor.local_daemon_health()


class _GraphAppProxy:
    def __init__(self, app, graph_source: _GraphSource) -> None:
        self._app = app
        self._graph_source = graph_source

    def __getattr__(self, name: str):
        if name in {
            'config',
            'config_identity',
            'registry',
            'runtime_service',
            'runtime_supervisor',
            'runtime_supervision',
            'completion_tracker',
            'dispatcher',
            'project_view_service',
            'project_focus_service',
            'health_monitor',
        }:
            return getattr(self._graph_source.current(), name)
        return getattr(self._app, name)


def _graph_request(graph_source: _GraphSource, handler):
    def handle(payload: dict) -> dict:
        return graph_source.run(handler, payload)

    return handle


__all__ = ['register_handlers']
