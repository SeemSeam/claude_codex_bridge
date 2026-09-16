from __future__ import annotations

import shlex
from pathlib import Path

from provider_profiles import provider_api_env_keys


_CLAUDE_CREDENTIAL_ENV_KEYS = {'ANTHROPIC_API_KEY', 'ANTHROPIC_AUTH_TOKEN'}
CLAUDE_AMBIENT_AUTH_ENV_KEYS = frozenset(_CLAUDE_CREDENTIAL_ENV_KEYS | {
    'CLAUDE_CODE_API_KEY_FILE_DESCRIPTOR',
    'CLAUDE_CODE_OAUTH_TOKEN',
    'CLAUDE_CODE_OAUTH_TOKEN_FILE_DESCRIPTOR',
})
CLAUDE_AUTH_COMMAND_CONTROL_ENV_KEYS = frozenset({
    'DISABLE_LOGIN_COMMAND',
    'DISABLE_LOGOUT_COMMAND',
})
CLAUDE_INDEPENDENT_AUTH_ENV_KEYS = (
    CLAUDE_AMBIENT_AUTH_ENV_KEYS | CLAUDE_AUTH_COMMAND_CONTROL_ENV_KEYS
)
_CLAUDE_API_ENV_GROUPS = (
    _CLAUDE_CREDENTIAL_ENV_KEYS,
    {'ANTHROPIC_BASE_URL'},
)


def build_claude_env_prefix(
    *,
    profile=None,
    extra_env: dict[str, str] | None = None,
    env: dict[str, str] | None = None,
    should_drop_base_url_fn,
    claude_user_api_env_fn=None,
    claude_user_base_url_fn,
) -> str:
    api_keys = provider_api_env_keys("claude")
    inherited_env = env or {}
    explicitly_configured = collect_explicit_api_env(
        profile=profile,
        extra_env=extra_env,
        api_keys=api_keys,
    )
    explicit_env = dict(explicitly_configured)
    explicit_env = inherit_api_env(
        explicit_env,
        profile=profile,
        inherited_env=inherited_env,
        api_keys=api_keys,
    )
    explicit_env = inherit_user_settings_api_env(
        explicit_env,
        profile=profile,
        api_keys=api_keys,
        claude_user_api_env_fn=claude_user_api_env_fn,
    )
    parts = unset_api_env_parts(
        profile=profile,
        api_keys=api_keys,
        explicitly_configured=explicitly_configured,
    )

    explicit_env = reconcile_base_url(
        explicit_env,
        profile=profile,
        env=inherited_env,
        parts=parts,
        should_drop_base_url_fn=should_drop_base_url_fn,
        claude_user_base_url_fn=claude_user_base_url_fn,
    )

    passthrough_statement = render_export_statement(passthrough_env(extra_env, api_keys=api_keys))
    if passthrough_statement:
        parts.append(passthrough_statement)

    export_statement = render_export_statement(explicit_env)
    if export_statement:
        parts.append(export_statement)
    return "; ".join(parts)


def passthrough_env(extra_env: dict[str, str] | None, *, api_keys: set[str]) -> dict[str, str]:
    """Keys from `agents.<name>.env` that the API reconciler above does not govern.

    That reconciler exists to decide credential and endpoint precedence, so it
    keeps only the provider's API keys. `agents.<name>.env` is a general
    environment map though, and every other launcher passes it through whole, so
    the remaining keys still have to reach the launched process.

    Emitted before the API exports and before CCB's own managed overrides, so
    neither can be shadowed by a value declared in config.
    """
    return {key: value for key, value in (extra_env or {}).items() if key not in api_keys}


def runtime_home_env_parts(*, profile=None) -> list[str]:
    if profile is None or not profile.runtime_home:
        return []
    runtime_home = Path(profile.runtime_home).expanduser()
    projects_root = runtime_home / ".claude" / "projects"
    session_env_root = runtime_home / ".claude" / "session-env"
    return [
        "unset CODEX_HOME",
        "unset CODEX_SESSION_ROOT",
        f"export HOME={shlex.quote(str(runtime_home))}",
        f"export CLAUDE_CONFIG_DIR={shlex.quote(str(runtime_home / '.claude'))}",
        f"export CLAUDE_PROJECTS_ROOT={shlex.quote(str(projects_root))}",
        f"export CLAUDE_SESSION_ENV_ROOT={shlex.quote(str(session_env_root))}",
    ]


def collect_explicit_api_env(*, profile=None, extra_env: dict[str, str] | None, api_keys: set[str]) -> dict[str, str]:
    explicit_env: dict[str, str] = {}
    if profile is not None:
        explicit_env.update(filtered_api_env(profile.env, api_keys=api_keys))
    if extra_env:
        explicit_env.update(filtered_api_env(extra_env, api_keys=api_keys))
    return explicit_env


def filtered_api_env(env_map: dict[str, str], *, api_keys: set[str]) -> dict[str, str]:
    return {key: value for key, value in env_map.items() if key in api_keys}


def inherit_api_env(
    explicit_env: dict[str, str],
    *,
    profile=None,
    inherited_env: dict[str, str],
    api_keys: set[str],
) -> dict[str, str]:
    if profile is not None and not profile.inherit_api:
        return explicit_env
    inherited = filtered_api_env(dict(inherited_env or {}), api_keys=api_keys)
    # Base URL precedence is resolved separately so user settings can override
    # ambient shell state without overriding an explicit profile value.
    inherited.pop("ANTHROPIC_BASE_URL", None)
    if profile is not None and not getattr(profile, "inherit_auth", True):
        inherited.pop("ANTHROPIC_AUTH_TOKEN", None)
        inherited.pop("ANTHROPIC_API_KEY", None)
    if _has_explicit_credential(explicit_env):
        for key in _CLAUDE_CREDENTIAL_ENV_KEYS:
            inherited.pop(key, None)
    return merge_missing_api_env(explicit_env, inherited)


def inherit_user_settings_api_env(
    explicit_env: dict[str, str],
    *,
    profile=None,
    api_keys: set[str],
    claude_user_api_env_fn,
) -> dict[str, str]:
    if claude_user_api_env_fn is None:
        return explicit_env
    if profile is not None and not profile.inherit_api:
        return explicit_env
    inherited = filtered_api_env(dict(claude_user_api_env_fn() or {}), api_keys=api_keys)
    if profile is not None and not getattr(profile, "inherit_auth", True):
        inherited.pop("ANTHROPIC_AUTH_TOKEN", None)
        inherited.pop("ANTHROPIC_API_KEY", None)
    if _has_explicit_credential(explicit_env):
        for key in _CLAUDE_CREDENTIAL_ENV_KEYS:
            inherited.pop(key, None)
    return merge_missing_api_env(explicit_env, inherited)


def merge_missing_api_env(explicit_env: dict[str, str], inherited_env: dict[str, str]) -> dict[str, str]:
    merged = dict(explicit_env)
    for key, value in inherited_env.items():
        if str(merged.get(key) or "").strip():
            continue
        if str(value or "").strip():
            merged[key] = value
    return merged


def _has_explicit_credential(env: dict[str, str]) -> bool:
    return any(str(env.get(key) or '').strip() for key in _CLAUDE_CREDENTIAL_ENV_KEYS)


def unset_api_env_parts(
    *,
    profile=None,
    api_keys: set[str],
    explicitly_configured: dict[str, str] | None = None,
) -> list[str]:
    cleared: set[str] = set()
    if profile is not None and not getattr(profile, "inherit_auth", True):
        cleared.update(CLAUDE_AMBIENT_AUTH_ENV_KEYS)
    if profile is not None and not profile.inherit_api:
        cleared.update(api_keys)
    else:
        configured = {
            key
            for key, value in dict(explicitly_configured or {}).items()
            if str(value).strip()
        }
        for group in _CLAUDE_API_ENV_GROUPS:
            if group & configured:
                cleared.update(group)
    return [f"unset {key}" for key in sorted(cleared)]


def reconcile_base_url(
    explicit_env: dict[str, str],
    *,
    profile=None,
    env: dict[str, str],
    parts: list[str],
    should_drop_base_url_fn,
    claude_user_base_url_fn,
) -> dict[str, str]:
    base_url = explicit_env.get("ANTHROPIC_BASE_URL")
    if base_url:
        if should_drop_base_url_fn(base_url):
            explicit_env.pop("ANTHROPIC_BASE_URL", None)
            ensure_unset(parts, "ANTHROPIC_BASE_URL")
        return explicit_env

    if profile is not None and not profile.inherit_api:
        return explicit_env

    inherited_base_url = inherited_base_url_value(env=env, claude_user_base_url_fn=claude_user_base_url_fn)
    if not inherited_base_url:
        return explicit_env
    if should_drop_base_url_fn(inherited_base_url):
        ensure_unset(parts, "ANTHROPIC_BASE_URL")
        return explicit_env
    explicit_env["ANTHROPIC_BASE_URL"] = inherited_base_url
    return explicit_env


def inherited_base_url_value(*, env: dict[str, str], claude_user_base_url_fn) -> str:
    settings_base_url = str(claude_user_base_url_fn() or "").strip()
    if settings_base_url:
        return settings_base_url
    env_base_url = str(env.get("ANTHROPIC_BASE_URL") or "").strip()
    if env_base_url:
        return env_base_url
    return ""


def ensure_unset(parts: list[str], key: str) -> None:
    statement = f"unset {key}"
    if statement not in parts:
        parts.append(statement)


def render_export_statement(explicit_env: dict[str, str]) -> str:
    exports = " ".join(
        f"{key}={shlex.quote(value)}"
        for key, value in sorted(explicit_env.items())
        if str(value).strip()
    )
    if not exports:
        return ""
    return f"export {exports}"


__all__ = ["build_claude_env_prefix", "passthrough_env"]
