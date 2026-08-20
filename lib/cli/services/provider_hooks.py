from __future__ import annotations

from datetime import datetime, timezone
import json
import os
from pathlib import Path
import shlex
import sys

from agents.models import RuntimeMode
from provider_core.one_way_inheritance import ensure_private_directory
from provider_core.source_home import current_provider_source_home
from provider_backends.claude.launcher_runtime.legacy_binary_cache import detach_legacy_claude_binary_cache
from provider_backends.claude.launcher_runtime import materialize_claude_home_config, resolve_claude_home_layout
from provider_backends.codex.launcher_runtime import resolve_codex_home_layout
from provider_backends.copilot.home import materialize_copilot_home_config
from provider_backends.droid.home import materialize_droid_home_config
from provider_backends.gemini.launcher_runtime.home import materialize_gemini_home_config
from provider_backends.qwen.home import materialize_qwen_home_config
from provider_backends.kimi.skills import materialize_kimi_skills
from provider_backends.kiro.home import materialize_kiro_home_config
from provider_backends.mimo.launcher import materialize_mimo_memory_config
from provider_backends.opencode.launcher import materialize_opencode_memory_config
from provider_hooks.settings import (
    build_activity_hook_command,
    build_hook_command,
    install_workspace_activity_hooks,
    install_workspace_completion_hooks,
    migrate_legacy_project_ccb_hooks,
)
from provider_profiles.codex_home_config import materialize_codex_home_config
from provider_profiles import (
    ResolvedProviderProfile,
    load_resolved_provider_profile,
    materialize_provider_profile,
)
from .role_command_policy import ensure_role_command_policy_supported


def prepare_workspace_provider_hooks(
    *,
    provider: str,
    workspace_path: Path,
    completion_dir: Path,
    agent_name: str,
    home_root: Path | None,
    project_id: str | None = None,
    project_root: Path | None = None,
    runtime_dir: Path | None = None,
    resolved_profile: ResolvedProviderProfile | None = None,
) -> Path | None:
    normalized = str(provider or '').strip().lower()
    if normalized not in {'claude', 'gemini'}:
        return None
    if normalized == 'claude':
        roots = (Path(workspace_path), Path(project_root) if project_root is not None else None)
        migrated_roots: set[Path] = set()
        for root in roots:
            if root is None:
                continue
            normalized_root = root.expanduser().absolute()
            if normalized_root in migrated_roots:
                continue
            migrated_roots.add(normalized_root)
            migrate_legacy_project_ccb_hooks(workspace_root=normalized_root)
    command = build_hook_command(
        provider=normalized,
        script_path=Path(__file__).resolve().parents[3] / 'bin' / 'ccb-provider-finish-hook',
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
            script_path=Path(__file__).resolve().parents[3] / 'bin' / 'ccb-provider-activity-hook',
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
    _materialize_source_test_command_shims(layout.project_root)
    command_policy = ensure_role_command_policy_supported(spec=spec)
    resolved_profile = (
        materialize_provider_profile(
            layout=layout,
            spec=spec,
            workspace_path=workspace_path,
            materialize_home=False,
        )
        if refresh_profile
        else load_resolved_provider_profile(runtime_dir)
    )
    if resolved_profile is None:
        resolved_profile = materialize_provider_profile(
            layout=layout,
            spec=spec,
            workspace_path=workspace_path,
            materialize_home=False,
        )
    _materialize_provider_home(
        layout=layout,
        spec=spec,
        runtime_dir=runtime_dir,
        resolved_profile=resolved_profile,
        workspace_path=workspace_path,
        auto_permission=auto_permission,
        command_policy=command_policy,
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
        project_root=layout.project_root,
        runtime_dir=runtime_dir,
        resolved_profile=resolved_profile,
    )
    return resolved_profile


def _materialize_source_test_command_shims(project_root: Path) -> None:
    if os.environ.get('CCB_TEST_ENTRYPOINT') != '1':
        return
    source_root = Path(__file__).resolve().parents[3]
    wrapper = source_root / 'ccb_test'
    if not wrapper.is_file():
        return
    bin_dir = Path(project_root) / '.ccb' / 'bin'
    bin_dir.mkdir(parents=True, exist_ok=True)
    shims = {
        'ccb': f'exec {shlex.quote(str(wrapper))} "$@"\n',
        'ask': f'exec {shlex.quote(str(wrapper))} ask "$@"\n',
        'codex-reconnect': (
            f'exec {shlex.quote(str(source_root / "bin" / "codex-reconnect"))} "$@"\n'
        ),
    }
    for name, command in shims.items():
        shim = bin_dir / name
        shim.write_text(
            '#!/usr/bin/env bash\n' + command,
            encoding='utf-8',
        )
        shim.chmod(0o755)


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
    command_policy=None,
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
            command_policy=command_policy,
            extra_env=dict(getattr(spec, 'env', {}) or {}),
            memory_projection_event_path=layout.agent_events_path(spec.name),
            memory_projection_marker_path=Path(runtime_dir) / 'claude-memory-projection.json',
        )
        _detach_legacy_claude_binary_cache_if_present(
            layout=layout,
            spec=spec,
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
            command_policy=command_policy,
            memory_projection_event_path=layout.agent_events_path(spec.name),
            memory_projection_marker_path=Path(runtime_dir) / 'codex-memory-projection.json',
            # The cached resolved profile can predate a ccb.config edit, so the
            # agent's current model/catalog are plumbed from the live spec rather
            # than read out of the possibly-stale profile env.
            model=spec.model,
            model_catalog_json=spec.provider_profile.env.get('model_catalog_json'),
        )
        return
    if provider == 'droid':
        droid_home = ensure_private_directory(
            layout.agent_provider_state_dir(spec.name, 'droid') / 'home'
        )
        materialize_droid_home_config(
            droid_home / '.factory',
            profile=resolved_profile,
            command_policy=command_policy,
        )
        return
    if provider == 'copilot':
        materialize_copilot_home_config(
            layout.agent_provider_state_dir(spec.name, 'copilot') / 'home',
            profile=resolved_profile,
            command_policy=command_policy,
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
    if provider == 'kiro':
        materialize_kiro_home_config(
            layout.agent_provider_state_dir(spec.name, 'kiro') / 'home',
            profile=resolved_profile,
            source_home=current_provider_source_home(),
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
            command_policy=command_policy,
        )
        return
    if provider == 'qwen':
        materialize_qwen_home_config(
            layout.agent_provider_state_dir(spec.name, 'qwen') / 'home',
            profile=resolved_profile,
            command_policy=command_policy,
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


def _detach_legacy_claude_binary_cache_if_present(*, layout, spec, home_root: Path) -> None:
    try:
        result = detach_legacy_claude_binary_cache(
            home_root,
            cache_roots=(
                layout.provider_external_cache_dir('claude'),
                layout.shared_cache_dir / 'claude',
            ),
        )
    except Exception:
        return
    if result.get('status') != 'ok':
        return
    payload = {
        'record_type': 'agent_event',
        'event_type': 'claude_binary_cache_detached',
        'provider': 'claude',
        'agent_name': spec.name,
        'status': 'ok',
        'reason': result.get('reason'),
        'versions_dir': result.get('versions_dir'),
        'versions_target': result.get('versions_target'),
        'created_at': datetime.now(timezone.utc).isoformat().replace('+00:00', 'Z'),
    }
    try:
        events_path = layout.agent_events_path(spec.name)
        events_path.parent.mkdir(parents=True, exist_ok=True)
        with events_path.open('a', encoding='utf-8') as handle:
            handle.write(json.dumps(payload, ensure_ascii=False) + '\n')
    except OSError:
        return


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
