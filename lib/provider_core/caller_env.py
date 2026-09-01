from __future__ import annotations

import os
import shlex
from pathlib import Path

from project.ids import compute_project_id
from runtime_env.git_identity import managed_git_identity_env
from runtime_env.user_session import user_session_transport_env
from storage.path_helpers import runtime_project_root_from_path


_MANAGED_PROVIDER_PROCESS_ENV = {
    'AGY_CLI_DISABLE_AUTO_UPDATE': '1',
    'FACTORYD_DISABLE_AUTO_UPDATE': '1',
    'GROK_DISABLE_AUTOUPDATER': '1',
    'NO_UPDATE_NOTIFIER': '1',
}


def caller_context_env(*, actor: str, runtime_dir: Path, launch_session_id: str) -> dict[str, str]:
    env = {
        'CCB_CALLER_ACTOR': str(actor or '').strip(),
        'CCB_CALLER_RUNTIME_DIR': str(runtime_dir),
        'CCB_SESSION_ID': str(launch_session_id or '').strip(),
    }
    project_root = _project_root_from_runtime_dir(runtime_dir)
    if project_root is not None:
        env['CCB_CALLER_PROJECT_ROOT'] = str(project_root)
        env['CCB_CALLER_PROJECT_ID'] = compute_project_id(project_root)
        source_test_bin = project_root / '.ccb' / 'bin'
        source_test_ccb = source_test_bin / 'ccb'
        if os.environ.get('CCB_TEST_ENTRYPOINT') == '1' and source_test_ccb.is_file():
            current_path = os.environ.get('PATH') or ''
            env['PATH'] = str(source_test_bin) + (os.pathsep + current_path if current_path else '')
    return env


def provider_user_session_env() -> dict[str, str]:
    return {
        **user_session_transport_env(),
        **managed_git_identity_env(),
        **_MANAGED_PROVIDER_PROCESS_ENV,
    }


def export_env_clause(env_map: dict[str, str]) -> str:
    rendered = ' '.join(
        f'{key}={shlex.quote(str(value))}'
        for key, value in sorted(env_map.items())
        if str(value).strip()
    )
    if not rendered:
        return ''
    return f'export {rendered}'


def join_env_prefix(*clauses: str) -> str:
    return '; '.join(str(clause).strip() for clause in clauses if str(clause).strip())


def _project_root_from_runtime_dir(runtime_dir: Path) -> Path | None:
    runtime_path = _resolve_path(runtime_dir)
    marker_project_root = runtime_project_root_from_path(runtime_path)
    if marker_project_root is not None:
        return _resolve_path(marker_project_root)
    for candidate in (runtime_path, *runtime_path.parents):
        if candidate.name == 'agents' and candidate.parent.name == '.ccb':
            return _resolve_path(candidate.parent.parent)
    return None


def _resolve_path(path: Path) -> Path:
    current = Path(path).expanduser()
    try:
        return current.resolve()
    except Exception:
        return current.absolute()


__all__ = ['caller_context_env', 'export_env_clause', 'join_env_prefix', 'provider_user_session_env']
