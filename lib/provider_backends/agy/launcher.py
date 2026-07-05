from __future__ import annotations

import functools
import hashlib
import os
import shlex
import sqlite3
import subprocess
import sys
import urllib.parse
from pathlib import Path

from provider_core.source_home import current_provider_source_home

from agents.models import AgentSpec
from cli.context import CliContext
from cli.models import ParsedStartCommand
from agents.policy import should_restore_provider_history
from provider_core.caller_env import (
    caller_context_env,
    export_env_clause,
    join_env_prefix,
    provider_user_session_env,
)
from provider_core.contracts import ProviderRuntimeLauncher
from provider_core.identity_prompt import inject_identity_args
from provider_core.runtime_shared import apply_provider_command_template, provider_start_parts
from workspace.models import WorkspacePlan


_YOLO_FLAG = '--dangerously-skip-permissions'
_WSL_POWERSHELL = '/mnt/c/Windows/System32/WindowsPowerShell/v1.0/powershell.exe'
_WSL_CMD = '/mnt/c/Windows/System32/cmd.exe'
_AGY_CREDENTIAL_DIRS = ('.gemini', '.antigravity')
_AGY_NTFS_HOMES_DIRNAME = '.ccb_agy_homes'
_AGY_CONVERSATIONS_REL = Path('.gemini') / 'antigravity-cli' / 'conversations'


def _log_warn(msg: str) -> None:
    sys.stderr.write(f'agy launcher: {msg}\n')


@functools.lru_cache(maxsize=1)
def _detect_windows_user_home() -> Path | None:
    """Resolve the real Windows %USERPROFILE% from inside WSL.

    Queries the Win32 API via PowerShell's [Environment]::GetFolderPath,
    which avoids the env-var route. CCB rewrites HOME/USERPROFILE for
    sandboxed sub-providers, so a cmd.exe-based env probe inherits the
    rewritten values and points at the wrong home.
    """
    if not Path(_WSL_POWERSHELL).exists():
        return None
    try:
        ps = subprocess.run(
            [
                _WSL_POWERSHELL,
                '-NoProfile',
                '-Command',
                '[Environment]::GetFolderPath("UserProfile")',
            ],
            capture_output=True,
            text=True,
            check=True,
            timeout=10,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        _log_warn(f'Windows home detection via PowerShell failed: {exc}')
        return None
    win_path = ps.stdout.strip()
    if not win_path:
        return None
    try:
        wp = subprocess.run(
            ['wslpath', '-u', win_path],
            capture_output=True,
            text=True,
            check=True,
            timeout=5,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        _log_warn(f'wslpath -u {win_path!r} failed: {exc}')
        return None
    wsl_path = wp.stdout.strip()
    if not wsl_path:
        return None
    resolved = Path(wsl_path)
    return resolved if resolved.exists() else None


def _resolve_credential_source_home() -> Path | None:
    """Find the home directory hosting agy credentials.

    Search order:
    1. CCB_AGY_SOURCE_HOME env override (escape hatch).
    2. Real Windows %USERPROFILE% via Win32 API.
    Returns None to let the caller fall back to current_provider_source_home().
    """
    override = os.environ.get('CCB_AGY_SOURCE_HOME')
    if override:
        candidate = Path(override).expanduser()
        if candidate.exists():
            return candidate
        _log_warn(f'CCB_AGY_SOURCE_HOME points to nonexistent path: {override}')
    return _detect_windows_user_home()


@functools.lru_cache(maxsize=1)
def _agy_ntfs_homes_root() -> Path | None:
    """NTFS directory hosting per-runtime managed HOME dirs (WSL-path view).

    Returns None when the Windows USERPROFILE cannot be located on NTFS
    (only /mnt/<drive>/... paths qualify; non-NTFS HOMEs cannot host
    directory junctions).
    """
    win_home = _detect_windows_user_home()
    if win_home is None:
        return None
    if not str(win_home).startswith('/mnt/'):
        return None
    return win_home / _AGY_NTFS_HOMES_DIRNAME


def _resolve_managed_home(runtime_dir: Path) -> Path:
    """Pick where agy's managed HOME lives for this runtime_dir.

    Preferred: NTFS subdir under %USERPROFILE%/.ccb_agy_homes/<runtime-id>.
    NTFS placement is required so we can use directory junctions for
    credential dirs (.gemini, .antigravity). Junctions are the only mount
    kind that present Attributes=Directory,ReparsePoint to Windows go
    binaries — WSL 9p symlinks present only ReparsePoint, which makes
    agy.exe's MkdirAll crash with "file already exists" during
    setUpInstallationID.

    Fallback: runtime_dir / 'home' on WSL ext4. agy.exe will likely crash
    on launch in this mode; the fallback exists only for environments
    where the Windows home cannot be detected.
    """
    root = _agy_ntfs_homes_root()
    if root is None:
        return runtime_dir / 'home'
    runtime_id = hashlib.sha1(str(runtime_dir).encode()).hexdigest()[:16]
    return root / runtime_id


def _ensure_directory_junction(link: Path, target: Path) -> bool:
    """Create an NTFS directory junction at `link` pointing at `target`.

    Both paths must reside on NTFS (mounted via /mnt/<drive>). Junctions
    do not require admin rights (unlike symlinks via `mklink /D`).
    Returns True on success or if `link` already exists.
    """
    if link.is_symlink() or link.exists():
        return True
    try:
        link_win = subprocess.run(
            ['wslpath', '-w', str(link)],
            capture_output=True,
            text=True,
            check=True,
            timeout=5,
        ).stdout.strip()
        target_win = subprocess.run(
            ['wslpath', '-w', str(target)],
            capture_output=True,
            text=True,
            check=True,
            timeout=5,
        ).stdout.strip()
    except (OSError, subprocess.SubprocessError) as exc:
        _log_warn(f'wslpath conversion failed for junction {link} -> {target}: {exc}')
        return False
    try:
        subprocess.run(
            [_WSL_CMD, '/c', 'mklink', '/J', link_win, target_win],
            capture_output=True,
            text=True,
            errors='replace',
            check=True,
            timeout=10,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        _log_warn(f'mklink /J {link_win} -> {target_win} failed: {exc}')
        return False
    return True


def _wslpath_to_windows(wsl_path: Path) -> str | None:
    """Translate a WSL path to a Windows path via `wslpath -w`."""
    try:
        out = subprocess.run(
            ['wslpath', '-w', str(wsl_path)],
            capture_output=True,
            text=True,
            check=True,
            timeout=5,
        ).stdout.strip()
    except (OSError, subprocess.SubprocessError) as exc:
        _log_warn(f'wslpath -w {wsl_path} failed: {exc}')
        return None
    return out or None


def _encode_cwd_for_agy(win_cwd: str) -> bytes:
    """Encode a Windows cwd the way agy stores it in trajectory_metadata_blob.

    Example: ``F:\\项目资料\\AI\\ccb-changes`` →
             ``F:/%E9%A1%B9%E7%9B%AE%E8%B5%84%E6%96%99/AI/ccb-changes``
    The drive letter and colon are kept literal; the rest is percent-encoded
    using URL-quoting (forward slashes preserved). Conversation DBs always
    embed this exact substring inside ``trajectory_metadata_blob.data``.
    """
    forward = win_cwd.replace('\\', '/')
    if len(forward) >= 2 and forward[1] == ':':
        head = forward[:2]
        tail = forward[2:]
        return (head + urllib.parse.quote(tail, safe='/')).encode('ascii')
    return urllib.parse.quote(forward, safe='/').encode('ascii')


def _find_latest_conversation_uuid(credential_home: Path, win_cwd: str) -> str | None:
    """Scan agy's conversations/*.db for the latest match on ``win_cwd``.

    agy's ``--continue`` relies on ``cache/last_conversations.json`` which
    only gets refreshed on graceful exit. After ``ccb kill``, that file is
    stale (or missing the cwd entry entirely), so ``--continue`` falls back
    to "start a new conversation". Each conversation DB embeds the project
    cwd as a URL-encoded ``file:///<path>`` URL in
    ``trajectory_metadata_blob.data``, which is enough to identify the
    most-recent conversation for any given cwd. The returned UUID is then
    passed to agy via ``--conversation <UUID>``.

    Returns None when the conversations directory is missing or no match
    exists (caller falls back to plain ``--continue``).
    """
    conv_dir = credential_home / _AGY_CONVERSATIONS_REL
    if not conv_dir.is_dir():
        _log_warn(f'agy conversations dir missing: {conv_dir}')
        return None
    needle = _encode_cwd_for_agy(win_cwd)
    best_mtime = -1.0
    best_uuid: str | None = None
    for db in conv_dir.glob('*.db'):
        try:
            mtime = db.stat().st_mtime
        except OSError as exc:
            _log_warn(f'stat failed for {db}: {exc}')
            continue
        if mtime <= best_mtime:
            continue
        try:
            with sqlite3.connect(
                f'file:{db}?mode=ro', uri=True, timeout=2.0
            ) as conn:
                row = conn.execute(
                    'SELECT data FROM trajectory_metadata_blob LIMIT 1'
                ).fetchone()
        except sqlite3.Error as exc:
            _log_warn(f'sqlite read failed for {db}: {exc}')
            continue
        if not row or not row[0]:
            continue
        data = _conversation_data_bytes(row[0], db=db)
        if data is None:
            continue
        if needle in data:
            best_mtime = mtime
            best_uuid = db.stem
    return best_uuid


def _conversation_data_bytes(value: object, *, db: Path) -> bytes | None:
    if isinstance(value, bytes):
        return value
    if isinstance(value, memoryview):
        return value.tobytes()
    if isinstance(value, str):
        return value.encode('utf-8')
    _log_warn(f'unsupported agy conversation metadata type in {db}: {type(value).__name__}')
    return None


def _resolve_resume_uuid(
    credential_home: Path, prepared_state: dict[str, object] | None
) -> str | None:
    """Pick the agy conversation UUID to resume for this launch.

    Uses ``prepared_state['workspace_path']`` (set by
    ``prepare_launch_context``) as the WSL-side cwd, converts it to the
    Windows path agy embeds in its conversation DBs, and returns the latest
    matching conversation. Returns None if any step fails so the caller
    falls back to ``--continue``.
    """
    if not prepared_state:
        return None
    workspace_path = prepared_state.get('workspace_path')
    if not workspace_path:
        return None
    win_cwd = _wslpath_to_windows(Path(str(workspace_path)))
    if not win_cwd:
        return None
    try:
        return _find_latest_conversation_uuid(credential_home, win_cwd)
    except Exception as exc:
        _log_warn(f'agy conversation resume lookup failed: {exc}')
        return None


def build_runtime_launcher() -> ProviderRuntimeLauncher:
    return ProviderRuntimeLauncher(
        provider='agy',
        launch_mode='simple_tmux',
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
    payload['agent_name'] = spec.name
    payload['project_root'] = str(context.project.project_root)
    payload['workspace_path'] = str(prepared_state.get('run_cwd') or plan.workspace_path)
    payload['agent_events_path'] = str(context.paths.agent_events_path(spec.name))
    return payload


def build_start_cmd(
    command: ParsedStartCommand,
    spec: AgentSpec,
    runtime_dir,
    launch_session_id: str,
    *,
    prepared_state: dict[str, object] | None = None,
) -> str:
    runtime_dir = Path(runtime_dir)
    managed_home = _resolve_managed_home(runtime_dir)
    managed_home.mkdir(parents=True, exist_ok=True)
    credential_home = _resolve_credential_source_home() or current_provider_source_home()
    managed_on_ntfs = str(managed_home).startswith('/mnt/')

    cmd_parts = provider_start_parts('agy')
    if command.auto_permission and _YOLO_FLAG not in cmd_parts and _YOLO_FLAG not in spec.startup_args:
        cmd_parts.append(_YOLO_FLAG)
    if (
        should_restore_provider_history(spec.restore_default, cli_restore=command.restore)
        and not _has_restore_arg(cmd_parts)
        and not _has_restore_arg(spec.startup_args)
    ):
        resume_uuid = _resolve_resume_uuid(credential_home, prepared_state)
        if resume_uuid:
            cmd_parts.extend(['--conversation', resume_uuid])
        else:
            cmd_parts.append('--continue')
    cmd_parts = inject_identity_args(
        cmd_parts,
        provider=spec.provider,
        name=spec.name,
        role=spec.role,
        window=spec.name,
    )
    cmd_parts.extend(spec.startup_args)
    cmd = ' '.join(shlex.quote(str(part)) for part in cmd_parts)
    cmd = apply_provider_command_template(cmd, spec.provider_command_template)
    env_prefix = join_env_prefix(
        export_env_clause(provider_user_session_env()),
        export_env_clause(spec.env),
        export_env_clause(
            caller_context_env(actor=spec.name, runtime_dir=runtime_dir, launch_session_id=launch_session_id)
        ),
    )

    for cred_dir in _AGY_CREDENTIAL_DIRS:
        src = credential_home / cred_dir
        tgt = managed_home / cred_dir
        if tgt.is_symlink() or tgt.exists():
            continue
        if not src.exists():
            _log_warn(f'credential source missing, skipping link: {src}')
            continue
        if managed_on_ntfs and str(src).startswith('/mnt/'):
            _ensure_directory_junction(tgt, src)
        else:
            try:
                tgt.symlink_to(src, target_is_directory=True)
            except OSError as exc:
                _log_warn(f'failed to symlink {tgt} -> {src}: {exc}')

    overrides = {'HOME': str(managed_home), 'USERPROFILE': str(managed_home)}
    if "WSL_DISTRO_NAME" in os.environ:
        wslenv_additions = "HOME/p:USERPROFILE/p"
        existing_wslenv = os.environ.get("WSLENV", "")
        if existing_wslenv:
            overrides['WSLENV'] = f"{wslenv_additions}:{existing_wslenv}"
        else:
            overrides['WSLENV'] = wslenv_additions
    override_exports = ' '.join(f"{k}={shlex.quote(v)}" for k, v in overrides.items())
    env_prefix = f"export {override_exports}; {env_prefix}" if env_prefix else f"export {override_exports}"

    if env_prefix:
        return f'{env_prefix}; {cmd}'
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
    del context, spec, prepared_state
    return {
        'ccb_session_id': launch_session_id,
        'runtime_dir': str(runtime_dir),
        'completion_artifact_dir': str(runtime_dir / 'completion'),
        'terminal': 'tmux',
        'tmux_session': pane_id,
        'pane_id': pane_id,
        'pane_title_marker': pane_title_marker,
        'workspace_path': str(plan.workspace_path),
        'work_dir': str(run_cwd),
        'start_cmd': start_cmd,
    }


def _has_restore_arg(parts: tuple[str, ...] | list[str]) -> bool:
    normalized = {str(part).strip() for part in parts}
    return bool({'--continue', '-c', '--conversation'} & normalized)


__all__ = ['build_runtime_launcher', 'build_start_cmd', 'prepare_launch_context']
