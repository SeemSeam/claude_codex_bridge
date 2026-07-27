from __future__ import annotations

from pathlib import Path

from provider_sessions.files import find_project_session_file as _find_project_session_file


PROVIDER_SESSION_FILENAMES = {
    'codex': '.codex-session',
    'claude': '.claude-session',
    'gemini': '.gemini-session',
    'opencode': '.opencode-session',
    'droid': '.droid-session',
    'agy': '.agy-session',
    'kimi': '.kimi-session',
    'deepseek': '.deepseek-session',
    'mimo': '.mimo-session',
    'qwen': '.qwen-session',
    'qoder': '.qoder-session',
    'qodercn': '.qodercn-session',
    'cursor': '.cursor-session',
    'copilot': '.copilot-session',
    'crush': '.crush-session',
    'grok': '.grok-session',
    'kiro': '.kiro-session',
    'pi': '.pi-session',
    'omp': '.omp-session',
    'zai': '.zai-session',
    'grok': '.grok-session',
}


def session_filename_for_instance(base_filename: str, instance: str | None) -> str:
    if not instance:
        return base_filename
    instance = instance.strip()
    if not instance:
        return base_filename
    if base_filename.endswith('-session'):
        prefix = base_filename[:-len('-session')]
        return f'{prefix}-{instance}-session'
    return f'{base_filename}-{instance}'


def find_session_file_for_work_dir(work_dir: Path, session_filename: str) -> Path | None:
    resolved = _find_project_session_file(work_dir, session_filename)
    if resolved is not None:
        return resolved
    try:
        candidate = Path(work_dir).expanduser() / session_filename
    except Exception:
        return None
    return candidate if candidate.exists() else None


def session_filename_for_agent(provider: str, agent_name: str) -> str:
    normalized_provider = str(provider or '').strip().lower()
    try:
        base = PROVIDER_SESSION_FILENAMES[normalized_provider]
    except KeyError as exc:
        raise RuntimeError(f'unsupported session filename provider: {provider}') from exc
    normalized_agent = str(agent_name or '').strip()
    if not normalized_agent:
        return base
    return session_filename_for_instance(base, normalized_agent)


__all__ = [
    'PROVIDER_SESSION_FILENAMES',
    'find_session_file_for_work_dir',
    'session_filename_for_agent',
    'session_filename_for_instance',
]
