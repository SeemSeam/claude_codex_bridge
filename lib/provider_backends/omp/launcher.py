from __future__ import annotations

import hashlib
import os
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
from provider_core.contracts import ProviderRuntimeLauncher

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
    _materialize_completion_extension(
        prepared_state,
        runtime_dir=runtime_dir,
        launch_session_id=launch_session_id,
    )
    return build_native_start_cmd(
        config,
        command,
        spec,
        runtime_dir,
        launch_session_id,
        prepared_state=prepared_state,
    )


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
    return payload


def _omp_visible_args(prepared_state: dict[str, object]) -> tuple[str, ...]:
    session_dir = _omp_session_dir(prepared_state)
    extension_path = _path_from_prepared(
        prepared_state,
        "omp_completion_extension",
    )
    session_dir.mkdir(parents=True, exist_ok=True)
    return (
        "--session-dir",
        str(session_dir),
        "--extension",
        str(extension_path),
        "--approval-mode",
        "yolo",
    )


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
    const willContinue = event?.willContinue === true;
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
    return source.replace(old_agent_end, new_agent_end)


__all__ = ["build_runtime_launcher", "prepare_launch_context"]
