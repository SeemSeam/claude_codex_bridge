from __future__ import annotations

import shlex
from pathlib import Path

from agents.models import AgentSpec
from cli.context import CliContext
from cli.models import ParsedStartCommand
from provider_core.caller_env import (
    caller_context_env,
    export_env_clause,
    join_env_prefix,
    provider_user_session_env,
)
from provider_core.contracts import ProviderRuntimeLauncher
from provider_core.identity_prompt import materialize_kimi_identity_agents_md
from provider_core.runtime_shared import apply_provider_command_template, provider_start_parts
from provider_backends.kimi.skills import kimi_skill_dirs_for_launch
from workspace.models import WorkspacePlan


_AUTO_FLAG = "--auto-approve"
_AUTO_FLAGS = {"--auto-approve", "--auto", "--yes", "-y", "--yolo"}


def build_runtime_launcher() -> ProviderRuntimeLauncher:
    return ProviderRuntimeLauncher(
        provider="kimi",
        launch_mode="simple_tmux",
        prepare_launch_context=prepare_launch_context,
        build_start_cmd=build_start_cmd,
        build_session_payload=build_session_payload,
    )


def prepare_launch_context(
    context: CliContext,
    spec: AgentSpec,
    plan: WorkspacePlan,
    runtime_dir: Path,
    prepared_state: dict[str, object],
) -> dict[str, object]:
    del runtime_dir
    payload = dict(prepared_state or {})
    payload["agent_name"] = spec.name
    payload["project_root"] = str(context.project.project_root)
    payload["workspace_path"] = str(prepared_state.get("run_cwd") or plan.workspace_path)
    payload["agent_events_path"] = str(context.paths.agent_events_path(spec.name))
    payload["kimi_skill_dirs"] = [
        str(path)
        for path in kimi_skill_dirs_for_launch(
            project_root=context.project.project_root,
            workspace_path=Path(str(payload["workspace_path"])),
            state_dir=context.paths.agent_provider_state_dir(spec.name, "kimi"),
            env=spec.env,
        )
    ]
    return payload


def build_start_cmd(
    command: ParsedStartCommand,
    spec: AgentSpec,
    runtime_dir,
    launch_session_id: str,
    *,
    prepared_state: dict[str, object] | None = None,
) -> str:
    launch_context = prepared_state or {}
    runtime_dir = Path(runtime_dir)
    materialize_kimi_identity_agents_md(
        launch_context.get("workspace_path"),
        name=spec.name,
        role=getattr(spec, "role", None),
        window=spec.name,
    )
    cmd_parts = provider_start_parts("kimi")
    if command.auto_permission and not _has_any(cmd_parts, _AUTO_FLAGS) and not _has_any(spec.startup_args, _AUTO_FLAGS):
        cmd_parts.append(_AUTO_FLAG)
    cmd_parts.extend(_skill_dir_args(launch_context.get("kimi_skill_dirs"), existing_parts=(*cmd_parts, *spec.startup_args)))
    cmd_parts.extend(spec.startup_args)
    cmd = " ".join(shlex.quote(str(part)) for part in cmd_parts)
    cmd = apply_provider_command_template(cmd, spec.provider_command_template)
    env_prefix = join_env_prefix(
        export_env_clause(provider_user_session_env()),
        export_env_clause(spec.env),
        export_env_clause(
            caller_context_env(actor=spec.name, runtime_dir=runtime_dir, launch_session_id=launch_session_id)
        ),
    )
    if env_prefix:
        return f"{env_prefix}; {cmd}"
    return cmd


def build_session_payload(
    context: CliContext,
    spec: AgentSpec,
    plan: WorkspacePlan,
    runtime_dir,
    run_cwd,
    pane_id: str,
    pane_title_marker: str,
    start_cmd: str,
    launch_session_id: str,
    prepared_state: dict[str, object],
) -> dict[str, object]:
    del prepared_state
    return {
        "ccb_session_id": launch_session_id,
        "agent_name": spec.name,
        "ccb_project_id": context.project.project_id,
        "runtime_dir": str(runtime_dir),
        "completion_artifact_dir": str(runtime_dir / "completion"),
        "terminal": "tmux",
        "tmux_session": pane_id,
        "pane_id": pane_id,
        "pane_title_marker": pane_title_marker,
        "workspace_path": str(plan.workspace_path),
        "work_dir": str(run_cwd),
        "start_dir": str(context.project.project_root),
        "start_cmd": start_cmd,
    }


def _has_any(parts: tuple[str, ...] | list[str], flags: set[str]) -> bool:
    normalized = {str(part).strip() for part in parts}
    return bool(flags & normalized)


def _skill_dir_args(raw_dirs: object, *, existing_parts: tuple[str, ...] | list[str]) -> list[str]:
    args: list[str] = []
    if not isinstance(raw_dirs, (list, tuple)):
        return args
    for raw in raw_dirs:
        text = str(raw or "").strip()
        if not text:
            continue
        path = Path(text).expanduser()
        if not path.is_dir():
            continue
        value = str(path)
        if _has_option_value(existing_parts, "--skills-dir", value) or _has_option_value(args, "--skills-dir", value):
            continue
        args.extend(("--skills-dir", value))
    return args


def _has_option_value(parts: tuple[str, ...] | list[str], option: str, value: str) -> bool:
    normalized = [str(part).strip() for part in parts]
    for index, part in enumerate(normalized):
        if part == option and index + 1 < len(normalized) and normalized[index + 1] == value:
            return True
        if part == f"{option}={value}":
            return True
    return False


__all__ = ["build_runtime_launcher", "build_start_cmd", "prepare_launch_context"]
