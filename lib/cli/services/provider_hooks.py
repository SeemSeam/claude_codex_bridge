from __future__ import annotations

from datetime import datetime, timezone
import json
from pathlib import Path
import sys

from agents.models import RuntimeMode
from provider_core.source_home import current_provider_source_home
from provider_backends.claude.launcher_runtime.binary_cache import route_claude_binary_cache
from provider_backends.claude.launcher_runtime import materialize_claude_home_config, resolve_claude_home_layout
from provider_backends.codex.launcher_runtime import resolve_codex_home_layout
from provider_backends.droid.home import materialize_droid_home_config
from provider_backends.gemini.launcher_runtime.home import materialize_gemini_home_config
from provider_backends.kimi.skills import materialize_kimi_skills
from provider_backends.mimo.launcher import materialize_mimo_memory_config
from provider_backends.opencode.launcher import materialize_opencode_memory_config
from provider_hooks.settings import (
    build_activity_hook_command,
    build_hook_command,
    install_workspace_activity_hooks,
    install_workspace_completion_hooks,
)
from provider_profiles.codex_home_config import materialize_codex_home_config
from provider_profiles import (
    ResolvedProviderProfile,
    load_resolved_provider_profile,
    materialize_provider_profile,
)


def prepare_workspace_provider_hooks(
    *,
    provider: str,
    workspace_path: Path,
    completion_dir: Path,
    agent_name: str,
    home_root: Path | None,
    project_id: str | None = None,
    runtime_dir: Path | None = None,
    resolved_profile: ResolvedProviderProfile | None = None,
) -> Path | None:
    normalized = str(provider or '').strip().lower()
    if normalized not in {'claude', 'gemini'}:
        return None
    command = build_hook_command(
        provider=normalized,
        script_path=Path(__file__).resolve().parents[3] / 'bin' / 'ccb-provider-finish-hook.py',
        python_executable=sys.executable,
        completion_dir=completion_dir,
        agent_name=agent_name,
        workspace_path=workspace_path,
    )
    settings_path = install_workspace_completion_hooks(
        provider=normalized,
        workspace_path=workspace_path,
        home_root=home_root,
        command=command,
        resolved_profile=resolved_profile,
    )
    if normalized == 'claude' and project_id and runtime_dir is not None:
        activity_command = build_activity_hook_command(
            provider=normalized,
            script_path=Path(__file__).resolve().parents[3] / 'bin' / 'ccb-provider-activity-hook.py',
            python_executable=sys.executable,
            project_id=project_id,
            agent_name=agent_name,
            runtime_dir=runtime_dir,
            workspace_path=workspace_path,
        )
        return install_workspace_activity_hooks(
            provider=normalized,
            workspace_path=workspace_path,
            home_root=home_root,
            command=activity_command,
        ) or settings_path
    return settings_path


def prepare_provider_workspace(
    *,
    layout,
    spec,
    workspace_path: Path,
    completion_dir: Path,
    agent_name: str,
    refresh_profile: bool = False,
    auto_permission: bool = False,
) -> ResolvedProviderProfile:
    runtime_dir = layout.agent_provider_runtime_dir(spec.name, spec.provider)
    resolved_profile = (
        materialize_provider_profile(
            layout=layout,
            spec=spec,
            workspace_path=workspace_path,
        )
        if refresh_profile
        else load_resolved_provider_profile(runtime_dir)
    )
    if resolved_profile is None:
        resolved_profile = materialize_provider_profile(
            layout=layout,
            spec=spec,
            workspace_path=workspace_path,
        )
    _materialize_provider_home(
        layout=layout,
        spec=spec,
        runtime_dir=runtime_dir,
        resolved_profile=resolved_profile,
        workspace_path=workspace_path,
        auto_permission=auto_permission,
    )
    prepare_workspace_provider_hooks(
        provider=spec.provider,
        workspace_path=workspace_path,
        completion_dir=completion_dir,
        agent_name=agent_name,
        home_root=provider_hook_home_root(
            layout=layout,
            spec=spec,
            runtime_dir=runtime_dir,
            resolved_profile=resolved_profile,
        ),
        project_id=getattr(layout, 'project_id', None),
        runtime_dir=runtime_dir,
        resolved_profile=resolved_profile,
    )
    return resolved_profile


def provider_workspace_path_for_prepare(
    *,
    command,
    spec,
    plan,
    runtime_dir: Path,
    launcher,
) -> Path:
    if getattr(spec, 'runtime_mode', None) is not RuntimeMode.PANE_BACKED:
        return Path(plan.workspace_path)
    resolve_run_cwd = getattr(launcher, 'resolve_run_cwd', None)
    if resolve_run_cwd is None:
        return Path(plan.workspace_path)
    resolved = resolve_run_cwd(
        command,
        spec,
        plan,
        runtime_dir,
        None,
    )
    if resolved is None:
        return Path(plan.workspace_path)
    return Path(resolved)


def _materialize_provider_home(
    *,
    layout,
    spec,
    runtime_dir: Path,
    resolved_profile: ResolvedProviderProfile | None,
    workspace_path: Path,
    auto_permission: bool,
) -> None:
    provider = str(spec.provider or '').strip().lower()
    if provider == 'claude':
        home_root = resolve_claude_home_layout(runtime_dir, resolved_profile).home_root
        materialize_claude_home_config(
            home_root,
            profile=resolved_profile,
            source_home=current_provider_source_home(),
            project_root=layout.project_root,
            agent_name=spec.name,
            workspace_path=workspace_path,
            auto_permission=auto_permission,
            memory_projection_event_path=layout.agent_events_path(spec.name),
            memory_projection_marker_path=Path(runtime_dir) / 'claude-memory-projection.json',
        )
        _route_claude_binary_cache_if_possible(
            layout=layout,
            home_root=home_root,
        )
        _record_claude_binary_cache_drift_if_present(
            layout=layout,
            spec=spec,
            runtime_dir=runtime_dir,
            home_root=home_root,
        )
        return
    if provider == 'codex':
        materialize_codex_home_config(
            resolve_codex_home_layout(runtime_dir, resolved_profile).codex_home,
            profile=resolved_profile,
            project_root=layout.project_root,
            agent_name=spec.name,
            runtime_dir=runtime_dir,
            workspace_path=workspace_path,
            memory_projection_event_path=layout.agent_events_path(spec.name),
            memory_projection_marker_path=Path(runtime_dir) / 'codex-memory-projection.json',
        )
        return
    if provider == 'droid':
        materialize_droid_home_config(
            layout.agent_provider_state_dir(spec.name, 'droid') / 'home',
            profile=resolved_profile,
        )
        return
    if provider == 'opencode':
        materialize_opencode_memory_config(
            project_root=layout.project_root,
            agent_name=spec.name,
            workspace_path=workspace_path,
            config_path=layout.agent_provider_state_dir(spec.name, 'opencode') / 'opencode.json',
            profile=resolved_profile,
            event_path=layout.agent_events_path(spec.name),
            marker_path=Path(runtime_dir) / 'opencode-memory-projection.json',
        )
        return
    if provider == 'kimi':
        materialize_kimi_skills(
            project_root=layout.project_root,
            agent_name=spec.name,
            state_dir=layout.agent_provider_state_dir(spec.name, 'kimi'),
            profile=resolved_profile,
        )
        return
    if provider == 'mimo':
        materialize_mimo_memory_config(
            project_root=layout.project_root,
            agent_name=spec.name,
            workspace_path=workspace_path,
            config_path=layout.agent_provider_state_dir(spec.name, 'mimo') / 'mimocode.json',
            profile=resolved_profile,
            event_path=layout.agent_events_path(spec.name),
            marker_path=Path(runtime_dir) / 'mimo-memory-projection.json',
        )
        return
    if provider == 'gemini':
        materialize_gemini_home_config(
            resolve_gemini_home_root(
                layout=layout,
                agent_name=spec.name,
                resolved_profile=resolved_profile,
            ),
            profile=resolved_profile,
            source_home=current_provider_source_home(),
            project_root=layout.project_root,
            agent_name=spec.name,
            workspace_path=workspace_path,
            memory_projection_event_path=layout.agent_events_path(spec.name),
            memory_projection_marker_path=Path(runtime_dir) / 'gemini-memory-projection.json',
        )


def provider_hook_home_root(
    *,
    layout,
    spec,
    runtime_dir: Path,
    resolved_profile: ResolvedProviderProfile | None,
) -> Path | None:
    provider = str(spec.provider or '').strip().lower()
    if provider == 'claude':
        return resolve_claude_home_layout(runtime_dir, resolved_profile).home_root
    if provider == 'gemini':
        return resolve_gemini_home_root(
            layout=layout,
            agent_name=spec.name,
            resolved_profile=resolved_profile,
        )
    return None


def resolve_gemini_home_root(*, layout, agent_name: str, resolved_profile: ResolvedProviderProfile | None) -> Path:
    del resolved_profile
    return layout.agent_provider_state_dir(agent_name, 'gemini') / 'home'


def _record_claude_binary_cache_drift_if_present(*, layout, spec, runtime_dir: Path, home_root: Path) -> None:
    versions_dir = Path(home_root) / '.local' / 'share' / 'claude' / 'versions'
    if _claude_versions_dir_points_to_shared_cache(layout, versions_dir):
        return
    signature = _claude_versions_cache_signature(versions_dir)
    if signature is None:
        return
    marker_path = Path(runtime_dir) / 'claude-binary-cache-drift.json'
    if _same_cached_signature(marker_path, signature):
        return
    payload = {
        'record_type': 'agent_event',
        'event_type': 'claude_binary_cache_drift',
        'provider': 'claude',
        'agent_name': spec.name,
        'status': 'notice',
        'reason': signature['reason'],
        'versions_dir': str(versions_dir),
        'version_count': len(signature['version_names']),
        'version_names': signature['version_names'],
        'created_at': datetime.now(timezone.utc).isoformat().replace('+00:00', 'Z'),
    }
    try:
        events_path = layout.agent_events_path(spec.name)
        events_path.parent.mkdir(parents=True, exist_ok=True)
        with events_path.open('a', encoding='utf-8') as handle:
            handle.write(json.dumps(payload, ensure_ascii=False) + '\n')
        marker_path.parent.mkdir(parents=True, exist_ok=True)
        marker_path.write_text(json.dumps(signature, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    except OSError:
        return


def _route_claude_binary_cache_if_possible(*, layout, home_root: Path) -> None:
    try:
        cache_root = layout.ensure_provider_external_cache_dir('claude')
    except Exception:
        return
    route_claude_binary_cache(home_root, cache_root, source_home=current_provider_source_home())


def _claude_versions_cache_signature(versions_dir: Path) -> dict[str, object] | None:
    try:
        if versions_dir.is_symlink():
            return {
                'reason': 'versions_dir_symlink',
                'versions_dir': str(versions_dir),
                'version_names': [],
            }
        if not versions_dir.is_dir():
            return None
        version_names = sorted(child.name for child in versions_dir.iterdir() if not child.name.startswith('.'))
    except OSError:
        return None
    if not version_names:
        return None
    return {
        'reason': 'per_agent_versions_cache_present',
        'versions_dir': str(versions_dir),
        'version_names': version_names,
    }


def _claude_versions_dir_points_to_shared_cache(layout, versions_dir: Path) -> bool:
    try:
        resolved = Path(versions_dir).resolve()
        external_versions = layout.provider_external_cache_dir('claude') / 'versions'
        legacy_shared_versions = layout.provider_shared_cache_dir('claude') / 'versions'
        return Path(versions_dir).is_symlink() and resolved in {
            external_versions.resolve(strict=False),
            legacy_shared_versions.resolve(strict=False),
        }
    except Exception:
        return False


def _same_cached_signature(marker_path: Path, signature: dict[str, object]) -> bool:
    try:
        payload = json.loads(marker_path.read_text(encoding='utf-8'))
    except Exception:
        return False
    return isinstance(payload, dict) and payload == signature


__all__ = [
    'prepare_provider_workspace',
    'prepare_workspace_provider_hooks',
    'provider_workspace_path_for_prepare',
    'provider_hook_home_root',
    'resolve_gemini_home_root',
]
