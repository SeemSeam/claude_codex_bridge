from __future__ import annotations

import os
from collections.abc import Mapping
from pathlib import Path

from agents.config_loader import load_project_config
from agents.models import AgentSpec, AgentValidationError, normalize_agent_name
from cli.context import CliContext
from mailbox_runtime.targets import USER_ACTOR
from workspace.actors import resolve_workspace_actor


def resolve_ask_sender(context: CliContext, explicit_sender: str | None) -> str:
    sender = str(explicit_sender or '').strip()
    if sender:
        return sender

    config = load_project_config(context.project.project_root).config
    allowed_session_actors = frozenset(
        {
            *[normalize_agent_name(name) for name in getattr(config, 'agents', {}) or {}],
        }
    )
    session_actor = _resolve_session_actor(context, allowed_session_actors=allowed_session_actors)
    if session_actor:
        return session_actor

    # OMP's Python tool filters CCB_* and CODEX_* out of its environment,
    # but preserves PI_*. Recover only an exact, configured managed OMP home.
    # A supplied runtime path remains authoritative even when it is stale.
    runtime_env_seen = any(
        str(os.environ.get(key) or '').strip()
        for key in ('CCB_CALLER_RUNTIME_DIR', 'CODEX_RUNTIME_DIR')
    )
    if not runtime_env_seen:
        omp_actor = _actor_from_omp_agent_dir(context, config.agents)
        if omp_actor:
            return omp_actor

    workspace_actor = resolve_workspace_actor(context.cwd, project_id=context.project.project_id)
    if workspace_actor:
        return workspace_actor

    return USER_ACTOR


def _actor_from_omp_agent_dir(context: CliContext, configured_agents: Mapping[str, AgentSpec]) -> str | None:
    raw = str(os.environ.get('PI_CODING_AGENT_DIR') or '').strip()
    if not raw:
        return None
    agent_dir = _resolve_path(Path(raw))
    try:
        relative = agent_dir.relative_to(_resolve_path(context.paths.agents_dir))
    except ValueError:
        return None
    if len(relative.parts) != 6 or relative.parts[1:] != ('provider-state', 'omp', 'home', '.omp', 'agent'):
        return None
    actor = relative.parts[0]
    spec = configured_agents.get(actor)
    if getattr(spec, 'provider', None) != 'omp':
        return None
    return actor


def _resolve_session_actor(context: CliContext, *, allowed_session_actors: frozenset[str]) -> str | None:
    runtime_env_seen = False
    for env_name in ('CCB_CALLER_RUNTIME_DIR', 'CODEX_RUNTIME_DIR'):
        if str(os.environ.get(env_name) or '').strip():
            runtime_env_seen = True
        actor = _actor_from_runtime_dir(
            os.environ.get(env_name),
            agents_dir=context.paths.agents_dir,
            allowed_session_actors=allowed_session_actors,
        )
        if actor is not None:
            return actor
    if runtime_env_seen:
        return None

    for env_name in ('CCB_CALLER_ACTOR',):
        actor = _normalized_actor_candidate(os.environ.get(env_name))
        if actor in allowed_session_actors:
            return actor

    return _actor_from_session_id(os.environ.get('CCB_SESSION_ID'), allowed_session_actors=allowed_session_actors)


def _actor_from_runtime_dir(
    value: str | None,
    *,
    agents_dir: Path,
    allowed_session_actors: frozenset[str],
) -> str | None:
    runtime_dir = str(value or '').strip()
    if not runtime_dir:
        return None
    resolved_runtime_dir = _resolve_path(Path(runtime_dir))
    resolved_agents_dir = _resolve_path(agents_dir)
    try:
        relative = resolved_runtime_dir.relative_to(resolved_agents_dir)
    except ValueError:
        return None
    if not relative.parts:
        return None
    candidate = _normalized_actor_candidate(relative.parts[0])
    if candidate in allowed_session_actors:
        return candidate
    return None


def _actor_from_session_id(value: str | None, *, allowed_session_actors: frozenset[str]) -> str | None:
    session_id = str(value or '').strip().lower()
    if not session_id.startswith('ccb-'):
        return None
    suffix = session_id[4:]
    matches = [actor for actor in allowed_session_actors if suffix == actor or suffix.startswith(f'{actor}-')]
    if not matches:
        return None
    return max(matches, key=len)


def _normalized_actor_candidate(value: str | None) -> str | None:
    text = str(value or '').strip()
    if not text:
        return None
    try:
        return normalize_agent_name(text)
    except AgentValidationError:
        return None


def _resolve_path(path: Path) -> Path:
    current = Path(path).expanduser()
    try:
        return current.resolve()
    except Exception:
        return current.absolute()


__all__ = ['resolve_ask_sender']
