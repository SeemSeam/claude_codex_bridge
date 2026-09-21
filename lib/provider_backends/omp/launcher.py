from __future__ import annotations

import hashlib
import os
import tempfile
from pathlib import Path

from provider_backends.native_cli_support import NativeCliLaunchConfig
from provider_backends.native_cli_support.launcher import (
    build_session_payload as build_native_session_payload,
)
from provider_backends.native_cli_support.launcher import (
    build_start_cmd as build_native_start_cmd,
)
from provider_backends.native_cli_support.launcher import (
    prepare_launch_context as prepare_native_launch_context,
)
from provider_backends.pi.launcher import _PI_COMPLETION_EXTENSION_SOURCE
from provider_backends.pi.launcher import _has_session_control, _template_parts
from provider_backends.omp.session import (
    OMP_RESTART_SESSION_MARKER, render_restart_command, resume_binding_for_launch,
)
from provider_core.contracts import ProviderRuntimeLauncher
from provider_core.pathing import session_filename_for_agent
from provider_core.runtime_shared import provider_start_parts

_OMP_COMPLETION_SCHEMA_VERSION = 1
_OMP_EXTENSION_FILENAME = "ccb-omp-completion.ts"


def build_runtime_launcher() -> ProviderRuntimeLauncher:
    config = _launch_config()
    return ProviderRuntimeLauncher(
        provider="omp",
        launch_mode="simple_tmux",
        prepare_launch_context=lambda context, spec, plan, runtime_dir, prepared_state: (
            prepare_launch_context(
                context,
                spec,
                plan,
                Path(runtime_dir),
                prepared_state,
            )
        ),
        build_start_cmd=lambda command, spec, runtime_dir, launch_session_id, prepared_state=None: (
            _build_start_cmd(
                config,
                command,
                spec,
                Path(runtime_dir),
                launch_session_id,
                prepared_state=prepared_state,
            )
        ),
        build_session_payload=lambda context, spec, plan, runtime_dir, run_cwd, pane_id, pane_title_marker, start_cmd, launch_session_id, prepared_state: (
            _build_session_payload(
                config,
                context,
                spec,
                plan,
                Path(runtime_dir),
                Path(run_cwd),
                pane_id,
                pane_title_marker,
                start_cmd,
                launch_session_id,
                prepared_state,
            )
        ),
    )


def prepare_launch_context(
    context,
    spec,
    plan,
    runtime_dir: Path,
    prepared_state: dict[str, object],
) -> dict[str, object]:
    payload = prepare_native_launch_context(
        _launch_config(),
        context,
        spec,
        plan,
        runtime_dir,
        prepared_state,
    )
    payload["omp_session_dir"] = str(_omp_session_dir(payload))
    payload.update(resume_binding_for_launch(
        context.paths.ccb_dir / session_filename_for_agent("omp", spec.name),
        agent_name=spec.name, project_id=context.project.project_id,
        work_dir=Path(str(payload.get("workspace_path") or plan.workspace_path)),
        session_dir=_omp_session_dir(payload),
    ))
    return payload


def _launch_config() -> NativeCliLaunchConfig:
    return NativeCliLaunchConfig(
        provider="omp",
        visible_args_builder=_omp_visible_args,
        visible_env_builder=_omp_visible_env,
        visible_path_env_names=(
            "PI_CODING_AGENT_DIR",
            "PI_CODING_AGENT_SESSION_DIR",
        ),
        visible_raw_env_names=(
            "CCB_OMP_COMPLETION_EVENTS",
            "CCB_OMP_DISPATCH_EVENTS",
            "CCB_OMP_COMPOSER_SOCKET",
        ),
    )


def _build_start_cmd(
    config: NativeCliLaunchConfig,
    command,
    spec,
    runtime_dir: Path,
    launch_session_id: str,
    *,
    prepared_state: dict[str, object] | None,
) -> str:
    if prepared_state is None:
        raise RuntimeError("omp launch requires prepared_state")
    explicit = _has_session_control((
        *provider_start_parts("omp"), *spec.startup_args,
        *_template_parts(getattr(spec, "provider_command_template", None)),
    ))
    prepared_state["omp_explicit_session_control"] = explicit
    prepared_state["omp_restore_enabled"] = bool(command.restore)
    if explicit:
        prepared_state["omp_resume_status"] = "explicit_session_control"
    elif not command.restore:
        prepared_state["omp_resume_status"] = "fresh_restore_disabled"
    elif prepared_state.get("omp_resume_status") == "exact_session_ready":
        prepared_state["omp_resume_status"] = "exact_session_selected"
    _materialize_completion_extension(
        prepared_state,
        runtime_dir=runtime_dir,
        launch_session_id=launch_session_id,
    )
    template = build_native_start_cmd(
        config,
        command,
        spec,
        runtime_dir,
        launch_session_id,
        prepared_state=prepared_state,
    )
    if explicit:
        return template
    if template.count(OMP_RESTART_SESSION_MARKER) != 1:
        raise RuntimeError("omp restart command template must preserve session marker")
    prepared_state["omp_restart_start_cmd_template"] = template
    path = prepared_state.get("omp_resume_session_path") if prepared_state.get("omp_resume_status") == "exact_session_selected" else None
    return render_restart_command(template, path)


def _build_session_payload(
    config: NativeCliLaunchConfig,
    context,
    spec,
    plan,
    runtime_dir: Path,
    run_cwd: Path,
    pane_id: str,
    pane_title_marker: str,
    start_cmd: str,
    launch_session_id: str,
    prepared_state: dict[str, object],
) -> dict[str, object]:
    payload = build_native_session_payload(
        config,
        context,
        spec,
        plan,
        runtime_dir,
        run_cwd,
        pane_id,
        pane_title_marker,
        start_cmd,
        launch_session_id,
        prepared_state,
    )
    payload.update(
        {
            "omp_completion_schema_version": _OMP_COMPLETION_SCHEMA_VERSION,
            "omp_draft_guard_version": 1,
            "omp_draft_guard_socket": str(prepared_state.get("omp_draft_guard_socket") or ""),
            "omp_completion_extension": str(
                prepared_state.get("omp_completion_extension") or ""
            ),
            "omp_completion_event_log": str(
                prepared_state.get("omp_completion_event_log") or ""
            ),
            "omp_dispatch_event_log": str(
                prepared_state.get("omp_dispatch_event_log") or ""
            ),
            "omp_session_dir": str(
                prepared_state.get("omp_session_dir")
                or _omp_session_dir(prepared_state)
            ),
        }
    )
    # Native identity and CCB launch identity are different namespaces.
    payload.pop("omp_session_id", None)
    for key in ("omp_resume_status", "omp_explicit_session_control", "omp_restore_enabled", "omp_restart_start_cmd_template"):
        payload[key] = prepared_state.get(key)
    if prepared_state.get("omp_resume_status") == "exact_session_selected":
        payload.update({
            "omp_session_id": prepared_state["omp_resume_session_id"],
            "omp_session_path": prepared_state["omp_resume_session_path"],
            "omp_session_binding_source": prepared_state.get("omp_resume_binding_source"),
        })
    return payload


def _omp_visible_args(prepared_state: dict[str, object]) -> tuple[str, ...]:
    session_dir = _omp_session_dir(prepared_state)
    extension_path = _path_from_prepared(
        prepared_state,
        "omp_completion_extension",
    )
    session_dir.mkdir(parents=True, exist_ok=True)
    args = (
        "--session-dir",
        str(session_dir),
        "--extension",
        str(extension_path),
        "--approval-mode",
        "yolo",
    )
    if prepared_state.get("omp_explicit_session_control") is False:
        return (*args, OMP_RESTART_SESSION_MARKER)
    return args


def _omp_visible_env(prepared_state: dict[str, object]) -> dict[str, str]:
    agent_dir = _path_from_prepared(prepared_state, "omp_home") / ".omp" / "agent"
    session_dir = _omp_session_dir(prepared_state)
    completion_events = _path_from_prepared(
        prepared_state,
        "omp_completion_event_log",
    )
    dispatch_events = _path_from_prepared(
        prepared_state,
        "omp_dispatch_event_log",
    )
    agent_dir.mkdir(parents=True, exist_ok=True)
    session_dir.mkdir(parents=True, exist_ok=True)
    return {
        "PI_CODING_AGENT_DIR": str(agent_dir),
        "PI_CODING_AGENT_SESSION_DIR": str(session_dir),
        "CCB_OMP_COMPLETION_EVENTS": str(completion_events),
        "CCB_OMP_DISPATCH_EVENTS": str(dispatch_events),
        "CCB_OMP_COMPOSER_SOCKET": str(prepared_state.get("omp_draft_guard_socket") or ""),
    }


def _omp_session_dir(prepared_state: dict[str, object]) -> Path:
    raw = str(prepared_state.get("omp_session_dir") or "").strip()
    if raw:
        return Path(raw).expanduser()
    return _path_from_prepared(prepared_state, "omp_state_dir") / "sessions"


def _path_from_prepared(prepared_state: dict[str, object], key: str) -> Path:
    raw = str(prepared_state.get(key) or "").strip()
    if not raw:
        raise RuntimeError(f"omp launch requires {key} in prepared_state")
    return Path(raw).expanduser()


def _materialize_completion_extension(
    prepared_state: dict[str, object],
    *,
    runtime_dir: Path,
    launch_session_id: str,
) -> None:
    launch_id = str(launch_session_id or "").strip()
    if not launch_id:
        raise RuntimeError("omp launch requires a launch session id")
    completion_dir = runtime_dir / "completion"
    completion_dir.mkdir(parents=True, exist_ok=True)
    token = hashlib.sha256(launch_id.encode("utf-8", "replace")).hexdigest()[:16]
    extension_path = completion_dir / _OMP_EXTENSION_FILENAME
    completion_events = completion_dir / f"omp-pane-{token}.events.jsonl"
    dispatch_events = completion_dir / f"omp-pane-{token}.dispatch.jsonl"

    _write_owner_only(extension_path, _omp_completion_extension_source())
    _touch_owner_only(completion_events)
    _touch_owner_only(dispatch_events)
    prepared_state["omp_completion_schema_version"] = _OMP_COMPLETION_SCHEMA_VERSION
    prepared_state["omp_completion_extension"] = str(extension_path)
    prepared_state["omp_completion_event_log"] = str(completion_events)
    prepared_state["omp_dispatch_event_log"] = str(dispatch_events)
    socket_token = hashlib.sha256(str(completion_events.resolve()).encode()).hexdigest()[:24]
    prepared_state["omp_draft_guard_socket"] = str(
        Path(tempfile.gettempdir()) / f"ccb-editor-{socket_token}.sock"
    )


def _write_owner_only(path: Path, content: str) -> None:
    current = ""
    try:
        current = path.read_text(encoding="utf-8")
    except OSError:
        pass
    if current != content:
        path.write_text(content, encoding="utf-8")
    try:
        os.chmod(path, 0o600)
    except OSError:
        pass


def _touch_owner_only(path: Path) -> None:
    path.touch(exist_ok=True)
    try:
        os.chmod(path, 0o600)
    except OSError:
        pass


def _omp_completion_extension_source() -> str:
    source = (
        _PI_COMPLETION_EXTENSION_SOURCE.replace("CCB_PI_", "CCB_OMP_")
        .replace("ccbPiCompletion", "ccbOmpCompletion")
        .replace("pi_session_", "omp_session_")
    )
    old_agent_end = '''  pi.on("agent_end", async (event: any) => {
    if (Array.isArray(event?.messages)) {
      for (let index = event.messages.length - 1; index >= 0; index -= 1) {
        const candidate = normalizeAssistant(event.messages[index]);
        if (candidate) {
          latestAssistant = candidate;
          break;
        }
      }
    }
    appendEvent("agent_end", { assistant: latestAssistant });
  });

  pi.on("agent_settled", async () => {
    appendEvent("agent_settled", { assistant: latestAssistant });
    activeReqId = "";
    latestAssistant = null;
  });'''
    new_agent_end = '''  pi.on("agent_end", async (event: any) => {
    if (Array.isArray(event?.messages)) {
      for (let index = event.messages.length - 1; index >= 0; index -= 1) {
        const candidate = normalizeAssistant(event.messages[index]);
        if (candidate) {
          latestAssistant = candidate;
          break;
        }
      }
    }
    const willContinue =
      event?.willContinue === true || latestAssistant?.stop_reason === "tool_use";
    appendEvent("agent_end", {
      assistant: latestAssistant,
      will_continue: willContinue,
    });
    if (!willContinue) {
      appendEvent("agent_settled", { assistant: latestAssistant });
      activeReqId = "";
      latestAssistant = null;
    }
  });'''
    if old_agent_end not in source:
        raise RuntimeError("Pi completion extension template changed unexpectedly")
    source = source.replace(old_agent_end, new_agent_end)
    # Observe interactive turns and switches too, not just CCB-managed asks.
    source = source.replace(
        '  pi.on("input", async (event: any) => {',
        '''  const observeNativeSession = (ctx: any) => {
    const manager = ctx?.sessionManager;
    appendEvent("native_session", {
      omp_session_id: String(manager?.getSessionId?.() || ""),
      omp_session_path: String(manager?.getSessionFile?.() || ""),
    });
  };
  pi.on("session_switch", async (_event: any, ctx: any) => {
    observeNativeSession(ctx);
  });
  pi.on("input", async (event: any, ctx: any) => {
    observeNativeSession(ctx);''',
    )
    source = source.replace(
        '  pi.on("turn_end", async (event: any) => {',
        '  pi.on("turn_end", async (event: any, ctx: any) => {\n    observeNativeSession(ctx);',
    )
    from .composer_bridge import BRIDGE, IMPORTS
    return IMPORTS + source.replace(
        'export default function ccbOmpCompletion(pi: any): void {',
        'export default function ccbOmpCompletion(pi: any): void {' + BRIDGE,
    )


__all__ = ["build_runtime_launcher", "prepare_launch_context"]
