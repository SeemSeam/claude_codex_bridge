from __future__ import annotations

from pathlib import Path
from time import monotonic_ns

from terminal_runtime.tmux_identity import apply_ccb_pane_identity
from project_command_trust import require_runtime_provider_command_approval

from .tmux_backend import _is_herdr_launch, create_tmux_backend, prepared_state, run_cwd
from .pane_runtime import launch_runtime_pane, pane_runtime_id
from .tmux_panes import (
    best_effort_kill_tmux_pane,
    create_detached_tmux_pane,
    launch_pane,
    pane_meets_minimum_size,
    prepare_detached_tmux_server,
)


def _assert_tmux_only_fn(*args, **kwargs):
    """Sentinel: herdr 路径下不应调用 tmux 专用回调（如 create_detached_tmux_pane）。

    若触发说明 herdr launch 缺少 assigned_pane_ref → 误入 tmux pane 分配路径。
    """
    raise RuntimeError(
        'tmux-specific callback invoked on herdr backend path '
        '(herdr launch should use assigned_pane_ref, not dynamically allocate tmux panes)'
    )


def _noop(*args, **kwargs):
    """No-op: herdr 路径下的无害占位回调。"""
    pass


def _create_runtime_backend(
    backend_factory,
    tmux_socket_path: str | None,
    *,
    namespace_backend_impl: str | None = None,
    assigned_pane_ref: object = None,
):
    """按后端类型创建 runtime backend 实例。

    herdr 路径：直接调用 ``backend_factory()``（无需 socket path）。
    tmux 路径：经 ``create_tmux_backend()`` 创建（支持 socket path 注入）。
    """
    if _is_herdr_launch(
        namespace_backend_impl=namespace_backend_impl,
        assigned_pane_ref=assigned_pane_ref,
    ):
        return backend_factory()
    return create_tmux_backend(backend_factory, tmux_socket_path)


def launch_runtime(
    context,
    command,
    spec,
    plan,
    launcher,
    *,
    backend_factory,
    pane_title_marker_fn,
    launch_session_id_fn,
    create_detached_tmux_pane_fn,
    pane_meets_minimum_size_fn,
    best_effort_kill_tmux_pane_fn,
    write_session_file_fn,
    assigned_pane_id: str | None = None,
    assigned_pane_ref: dict[str, object] | None = None,
    namespace_ref: dict[str, object] | None = None,
    style_index: int = 0,
    tmux_socket_path: str | None = None,
    namespace_backend_impl: str | None = None,
    allow_detached_fallback: bool = True,
) -> dict[str, float]:
    launch_started_ns = monotonic_ns()
    timings_ms: dict[str, float] = {}
    try:
        if getattr(spec, 'provider_command_template', None):
            require_runtime_provider_command_approval(
                context.project.project_root,
                agent_name=spec.name,
                template=str(spec.provider_command_template),
            )
        stage_started_ns = monotonic_ns()
        try:
            runtime_dir = context.paths.agent_dir(spec.name) / 'provider-runtime' / spec.provider
            runtime_dir.mkdir(parents=True, exist_ok=True)
            launch_session_id = launch_session_id_fn(spec.name)
            prepared = prepared_state(launcher, runtime_dir)
            runtime_cwd = run_cwd(
                launcher,
                command=command,
                spec=spec,
                plan=plan,
                runtime_dir=runtime_dir,
                launch_session_id=launch_session_id,
            )
            prepared['run_cwd'] = str(runtime_cwd)
            if launcher.prepare_launch_context is not None:
                prepared = dict(
                    launcher.prepare_launch_context(context, spec, plan, runtime_dir, prepared)
                    or prepared
                )
        finally:
            _record_elapsed_ms(timings_ms, 'prepare_launch_context', stage_started_ns)
        backend = _create_runtime_backend(
            backend_factory,
            tmux_socket_path,
            namespace_backend_impl=namespace_backend_impl,
            assigned_pane_ref=assigned_pane_ref,
        )
        # 将 backend 身份注入 prepared_state，供 build_start_cmd / build_session_payload
        # 按实际后端设置终端类型（CODEX_TERMINAL 等），无需修改 ProviderRuntimeLauncher 签名。
        prepared['ccb_backend_impl'] = str(getattr(backend, 'backend_impl', '') or '').strip() or 'tmux'
        prepared['ccb_backend_family'] = str(getattr(backend, 'backend_family', '') or '').strip() or 'tmux-family'
        pane_title_marker = pane_title_marker_fn(context, spec)

        stage_started_ns = monotonic_ns()
        try:
            start_cmd = launcher.build_start_cmd(
                command,
                spec,
                runtime_dir,
                launch_session_id,
                prepared_state=prepared,
            )
        finally:
            _record_elapsed_ms(timings_ms, 'build_start_cmd', stage_started_ns)

        stage_started_ns = monotonic_ns()
        try:
            backend_is_herdr = _is_herdr_launch(
                namespace_backend_impl=namespace_backend_impl,
                assigned_pane_ref=assigned_pane_ref,
            )
            pane = launch_runtime_pane(
                backend,
                spec_name=spec.name,
                assigned_pane_id=assigned_pane_id,
                assigned_pane_ref=assigned_pane_ref,
                start_cmd=start_cmd,
                run_cwd=runtime_cwd,
                create_detached_tmux_pane_fn=(
                    _assert_tmux_only_fn if backend_is_herdr else create_detached_tmux_pane_fn
                ),
                pane_meets_minimum_size_fn=(
                    _noop if backend_is_herdr else pane_meets_minimum_size_fn
                ),
                best_effort_kill_tmux_pane_fn=(
                    _noop if backend_is_herdr else best_effort_kill_tmux_pane_fn
                ),
                allow_detached_fallback=(
                    False if backend_is_herdr else allow_detached_fallback
                ),
            )
            pane_id = pane_runtime_id(pane)
        finally:
            _record_elapsed_ms(timings_ms, 'tmux_respawn', stage_started_ns)

        stage_started_ns = monotonic_ns()
        try:
            _apply_runtime_pane_identity(
                backend,
                pane,
                title=spec.name,
                agent_label=spec.name,
                project_id=context.project.project_id,
                order_index=style_index,
                slot_key=spec.name,
                session_id=launch_session_id,
                provider_kind=spec.provider,
                role='agent',
            )
        finally:
            _record_elapsed_ms(timings_ms, 'pane_identity', stage_started_ns)

        stage_started_ns = monotonic_ns()
        try:
            _report_runtime_pane_agent(
                backend,
                pane,
                provider_kind=spec.provider,
                session_id=launch_session_id,
            )
        finally:
            _record_elapsed_ms(timings_ms, 'pane_agent_report', stage_started_ns)

        stage_started_ns = monotonic_ns()
        try:
            provider_payload = launcher.build_session_payload(
                context=context,
                spec=spec,
                plan=plan,
                runtime_dir=runtime_dir,
                run_cwd=runtime_cwd,
                pane_id=pane_id,
                pane_title_marker=pane_title_marker,
                start_cmd=start_cmd,
                launch_session_id=launch_session_id,
                prepared_state=prepared,
            )
            write_session_file_fn(
                context=context,
                spec=spec,
                plan=plan,
                runtime_dir=runtime_dir,
                run_cwd=runtime_cwd,
                pane_id=pane_id,
                tmux_socket_name=str(getattr(backend, '_socket_name', '') or '').strip() or None,
                tmux_socket_path=str(getattr(backend, '_socket_path', '') or '').strip() or None,
                pane_title_marker=pane_title_marker,
                start_cmd=start_cmd,
                launch_session_id=launch_session_id,
                provider_payload=provider_payload,
                backend_family=_runtime_backend_family(pane, namespace_ref),
                backend_impl=_runtime_backend_impl(pane, namespace_backend_impl),
                namespace_ref=namespace_ref,
                pane_ref=dict(pane) if isinstance(pane, dict) else None,
            )
        finally:
            _record_elapsed_ms(timings_ms, 'session_write', stage_started_ns)
        if launcher.post_launch is not None:
            stage_started_ns = monotonic_ns()
            try:
                launcher.post_launch(
                    backend,
                    pane_id,
                    runtime_dir,
                    launch_session_id,
                    prepared,
                )
            finally:
                _record_elapsed_ms(timings_ms, 'provider_post_launch', stage_started_ns)
    except Exception as exc:
        _finish_launch_timings(timings_ms, launch_started_ns)
        _attach_startup_timings(exc, timings_ms)
        raise
    _finish_launch_timings(timings_ms, launch_started_ns)
    return timings_ms


def _record_elapsed_ms(timings_ms: dict[str, float], field_name: str, started_ns: int) -> None:
    elapsed_ms = max(0.0, (monotonic_ns() - started_ns) / 1_000_000)
    timings_ms[field_name] = timings_ms.get(field_name, 0.0) + elapsed_ms


def _apply_runtime_pane_identity(
    backend,
    pane,
    *,
    title: str,
    agent_label: str,
    project_id: str,
    order_index: int | None,
    slot_key: str,
    session_id: str,
    provider_kind: str | None = None,
    role: str | None = None,
) -> None:
    if isinstance(pane, dict) and str(pane.get('backend_impl') or '').strip() == 'herdr':
        backend.set_pane_identity(
            dict(pane),
            title=title,
            agent_label=agent_label,
            project_id=project_id,
            order_index=order_index,
            slot_key=slot_key,
            session_id=session_id,
            managed_by='ccbd',
            role=role or 'agent',
            provider_kind=provider_kind,
        )
        return
    apply_ccb_pane_identity(
        backend,
        pane_runtime_id(pane),
        title=title,
        agent_label=agent_label,
        project_id=project_id,
        order_index=order_index,
        slot_key=slot_key,
        session_id=session_id,
    )


def _report_runtime_pane_agent(
    backend,
    pane,
    *,
    provider_kind: str,
    session_id: str,
) -> None:
    if not (isinstance(pane, dict) and str(pane.get('backend_impl') or '').strip() == 'herdr'):
        return
    # Herdr 当前版本不会仅凭 report-agent-session 创建 agent 身份；CCB 必须用
    # report-agent 注册 pane。先 best-effort release 旧权威，再用 idle+seq 创建
    # 身份，后续由 HerdrAgentLifecycleBridge 在 CCB 状态切换时持续递增上报。
    releaser = getattr(backend, 'release_pane_agent', None)
    if callable(releaser):
        try:
            releaser(
                dict(pane),
                provider_kind=provider_kind,
            )
        except Exception:
            pass
    reporter = getattr(backend, 'report_pane_agent', None)
    if not callable(reporter):
        return
    reporter(
        dict(pane),
        provider_kind=provider_kind,
        state='idle',
        seq=1,
        session_id=session_id,
    )


def _runtime_backend_family(pane, namespace_ref: dict[str, object] | None) -> str:
    if isinstance(pane, dict):
        value = str(pane.get('backend_family') or '').strip()
        if value:
            return value
    if isinstance(namespace_ref, dict):
        value = str(namespace_ref.get('backend_family') or '').strip()
        if value:
            return value
    return 'tmux-family'


def _runtime_backend_impl(pane, namespace_backend_impl: str | None) -> str:
    if isinstance(pane, dict):
        value = str(pane.get('backend_impl') or '').strip()
        if value:
            return value
    value = str(namespace_backend_impl or '').strip()
    return value or 'tmux'


def _finish_launch_timings(timings_ms: dict[str, float], launch_started_ns: int) -> None:
    total_ms = max(0.0, (monotonic_ns() - launch_started_ns) / 1_000_000)
    measured_ms = sum(timings_ms.values())
    timings_ms['unattributed'] = timings_ms.get('unattributed', 0.0) + max(
        0.0,
        total_ms - measured_ms,
    )


def _attach_startup_timings(exc: Exception, timings_ms: dict[str, float]) -> None:
    try:
        setattr(exc, 'ccb_startup_timings_ms', dict(timings_ms))
    except Exception:
        return


__all__ = [
    'best_effort_kill_tmux_pane',
    'create_detached_tmux_pane',
    'launch_runtime',
    'pane_meets_minimum_size',
    'prepare_detached_tmux_server',
]
