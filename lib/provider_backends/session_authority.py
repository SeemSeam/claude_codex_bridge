from __future__ import annotations

import hashlib
import hmac
import json
import os
import secrets
import stat
import time
from collections.abc import Mapping
from pathlib import Path

from provider_core.source_home import current_provider_source_home
from provider_profiles import provider_api_env_keys
from provider_sessions.files import safe_write_session
from storage.atomic import atomic_write_text


_AUTHORITY_KEY_NAME = '.ccb-authority-hmac-key'
_AUTH_FILES: dict[str, tuple[str, ...]] = {
    'claude': (
        '.config/claude-code/auth.json',
        '.claude/.credentials.json',
    ),
    'gemini': (
        '.gemini/oauth_creds.json',
        '.gemini/google_accounts.json',
        '.gemini/gemini-credentials.json',
        '.gemini/mcp-oauth-tokens.json',
        '.gemini/a2a-oauth-tokens.json',
    ),
    'dsh': ('.credentials.yaml',),
}
_AUTH_METADATA_FILES: dict[str, tuple[str, ...]] = {
    'claude': ('.claude.json', '.claude/.claude.json'),
    'gemini': ('.gemini/settings.json',),
}
_API_FILES: dict[str, tuple[str, ...]] = {
    'claude': ('.claude/settings.json',),
    'gemini': ('.gemini/.env',),
    'dsh': ('.env',),
}
_AUTH_PROJECTION_MANIFEST = '.ccb-auth-projection.json'
_CONTINUITY_SCHEMA_VERSION = 1


def current_provider_authority_fingerprint(provider: str, profile, runtime_dir: Path) -> str:
    """Return an agent-private, non-portable authority fingerprint.

    The persisted value is an HMAC, never a raw credential or a portable hash.
    It changes when the selected profile, API route/env, or inherited auth files
    change, which makes provider conversation restore fail closed across an
    account or API switch.
    """
    provider_name = str(provider or '').strip().lower()
    runtime = Path(runtime_dir).expanduser()
    state_dir = runtime.parent.parent / 'provider-state' / provider_name
    state_dir.mkdir(parents=True, exist_ok=True)
    key_path = state_dir / _AUTHORITY_KEY_NAME
    key = _load_or_create_key(key_path)

    inherit_api = bool(getattr(profile, 'inherit_api', True))
    inherit_auth = bool(getattr(profile, 'inherit_auth', True))
    source_home = current_provider_source_home()
    if provider_name == 'dsh':
        explicit_dsh_home = str(os.environ.get('DSH_HOME') or '').strip()
        source_home = (
            Path(explicit_dsh_home).expanduser()
            if explicit_dsh_home
            else source_home / '.dsh'
        )
    managed_home = _managed_home(runtime, profile, provider_name)
    api_keys = set(provider_api_env_keys(provider_name))
    profile_env = {
        str(name): str(value)
        for name, value in dict(getattr(profile, 'env', {}) or {}).items()
        if str(value).strip()
    }
    explicit_api_names = _explicit_api_owned_names(provider_name, profile_env)
    credential_names = _credential_env_keys(provider_name)
    explicit_credential = bool(credential_names & explicit_api_names)
    owned_api_names = set(explicit_api_names)
    if explicit_credential:
        owned_api_names.update(credential_names)
    inherit_external_auth = inherit_auth and not explicit_credential
    use_managed_auth = not inherit_auth and not explicit_credential
    payload = {
        'provider': provider_name,
        'profile': _profile_record(profile),
        'api_env': {
            name: str(os.environ.get(name) or '')
            for name in sorted(api_keys)
            if name not in owned_api_names
        } if inherit_api else {},
        'source_auth': _auth_file_payload(source_home, provider_name) if inherit_external_auth else {},
        'managed_auth': _auth_file_payload(managed_home, provider_name) if use_managed_auth else {},
        'source_auth_metadata': _metadata_file_payload(source_home, provider_name) if inherit_external_auth else {},
        'managed_auth_metadata': _metadata_file_payload(managed_home, provider_name) if use_managed_auth else {},
        # A keyring-only login has no source-home file to fingerprint.  Once
        # CCB has projected it, include only the manifest-owned private copy so
        # the next stopped restart notices a keyring rotation/logout without
        # treating unrelated Agent-private files as external authority.
        'managed_projected_auth': (
            _projected_auth_file_payload(managed_home, provider_name)
            if inherit_external_auth
            else {}
        ),
        'source_api_files': (
            _api_file_payload(source_home, provider_name, excluded=owned_api_names)
            if inherit_api
            else {}
        ),
        'managed_api_files': (
            _api_file_payload(managed_home, provider_name, excluded=owned_api_names)
            if not inherit_api
            else {}
        ),
    }
    encoded = json.dumps(payload, ensure_ascii=True, sort_keys=True, separators=(',', ':')).encode('utf-8')
    return hmac.new(key, encoded, hashlib.sha256).hexdigest()[:32]


def stored_provider_authority_fingerprint(data: Mapping[str, object], provider: str) -> str:
    return str(data.get(f'{str(provider).strip().lower()}_provider_authority_fingerprint') or '').strip()


def provider_authority_matches(
    data: Mapping[str, object],
    provider: str,
    current: str,
    *,
    allow_legacy_missing: bool = False,
) -> bool:
    stored = stored_provider_authority_fingerprint(data, provider)
    if not stored:
        return bool(allow_legacy_missing and current)
    return bool(stored and current and hmac.compare_digest(stored, current))


def linked_continuation_pending(data: Mapping[str, object], provider: str) -> bool:
    """Return whether a new authority generation still lacks a native binding."""
    return (
        str(data.get('ccb_resume_compatibility') or '').strip() == 'linked_continuation'
        and not _provider_binding_present(data, str(provider or '').strip().lower())
    )


def remember_bound_provider_session_authority(
    data: dict[str, object],
    provider: str,
) -> bool:
    """Bind a newly observed native session to the current CCB generation."""
    provider_name = str(provider or '').strip().lower()
    current = stored_provider_authority_fingerprint(data, provider_name)
    if not current or not _provider_binding_present(data, provider_name):
        return False
    previous_compatibility = str(
        data.get('ccb_resume_compatibility') or ''
    ).strip()
    continuation_launch_mode = str(
        data.get('ccb_continuation_launch_mode') or ''
    ).strip()
    expected_mode = {
        'codex': 'fork',
        'claude': 'fork',
        'gemini': 'import',
    }.get(provider_name)
    bound_fingerprint = str(
        data.get(f'{provider_name}_session_authority_fingerprint') or ''
    ).strip()
    continuity_complete = bool(
        data.get('ccb_continuity_schema_version')
        and str(data.get('ccb_conversation_id') or '').strip()
        and data.get('ccb_authority_generation')
    )
    if (
        bound_fingerprint
        and hmac.compare_digest(bound_fingerprint, current)
        and continuity_complete
        and previous_compatibility
        in {'managed_local_history', 'native_fork_continuation', 'linked_continuation'}
    ):
        if (
            continuation_launch_mode == expected_mode
            and previous_compatibility != 'native_fork_continuation'
        ):
            data['ccb_resume_compatibility'] = 'native_fork_continuation'
            data['ccb_continuity_status'] = 'continued_on_new_authority'
            data['ccb_continuity_updated_at'] = time.strftime('%Y-%m-%d %H:%M:%S')
            return True
        return False
    changed = rebind_provider_session_data(
        data,
        provider_name,
        current,
        native_resume_compatible=True,
    )
    if previous_compatibility in {
        'linked_continuation',
        'native_fork_continuation',
    }:
        compatibility = (
            'native_fork_continuation'
            if (
                continuation_launch_mode == expected_mode
                or previous_compatibility == 'native_fork_continuation'
            )
            else 'linked_continuation'
        )
        if data.get('ccb_resume_compatibility') != compatibility:
            data['ccb_resume_compatibility'] = compatibility
            changed = True
    return changed


def rebind_provider_session_authority(
    session,
    provider: str,
    current: str,
    *,
    native_resume_compatible: bool = True,
) -> bool:
    """Bind Agent-private local history to the newly prepared authority.

    Provider authority controls credentials and routing.  A managed local
    transcript remains conversation state, so a compatible private history
    can move to the next authority generation without being deleted.
    """
    data = getattr(session, 'data', None)
    if not isinstance(data, dict):
        return False
    changed = rebind_provider_session_data(
        data,
        provider,
        current,
        native_resume_compatible=native_resume_compatible,
    )
    if not changed:
        return False
    writer = getattr(session, '_write_back', None)
    if callable(writer):
        writer()
        return True
    session_file = getattr(session, 'session_file', None)
    if session_file is None:
        return True
    ok, error = safe_write_session(
        Path(session_file),
        json.dumps(data, ensure_ascii=False, indent=2) + '\n',
    )
    if not ok:
        raise RuntimeError(error or f'failed to persist {provider} session continuity: {session_file}')
    return True


def rebind_provider_session_data(
    data: dict[str, object],
    provider: str,
    current: str,
    *,
    native_resume_compatible: bool = True,
) -> bool:
    provider_name = str(provider or '').strip().lower()
    current_fingerprint = str(current or '').strip()
    if not provider_name or not current_fingerprint:
        return False

    before = dict(data)
    stored = stored_provider_authority_fingerprint(data, provider_name)
    has_binding = _provider_binding_present(data, provider_name)
    generation = _positive_int(data.get('ccb_authority_generation'), default=1)
    conversation_id = _conversation_id(data, provider_name)
    old_binding = _provider_binding_record(
        data,
        provider_name,
        generation=generation,
        conversation_id=conversation_id,
    )
    if stored and not hmac.compare_digest(stored, current_fingerprint):
        generation += 1
        status = 'continued_on_new_authority'
    elif not stored:
        status = 'adopted_legacy' if has_binding else 'continued_local_history'
    else:
        status = str(data.get('ccb_continuity_status') or '').strip() or 'resumed_same_authority'

    history = _session_history(data)
    if stored and not hmac.compare_digest(stored, current_fingerprint) and old_binding is not None:
        _append_unique_history(history, old_binding)

    if not native_resume_compatible and old_binding is not None:
        # Keep the old native transcript discoverable, but do not let a fresh
        # Provider generation resume a binding whose compatibility is unknown.
        _append_unique_history(history, old_binding)
        for key in (
            f'{provider_name}_session_id',
            f'{provider_name}_session_path',
            f'{provider_name}_session_authority_fingerprint',
        ):
            data.pop(key, None)
        old_id = str(old_binding.get('provider_session_id') or '').strip()
        old_path = str(old_binding.get('provider_session_path') or '').strip()
        if old_id:
            data[f'old_{provider_name}_session_id'] = old_id
        if old_path:
            data[f'old_{provider_name}_session_path'] = old_path
        status = 'continued_on_new_authority'

    data['ccb_continuity_schema_version'] = _CONTINUITY_SCHEMA_VERSION
    data['ccb_conversation_id'] = conversation_id
    data['ccb_authority_generation'] = generation
    data['ccb_continuity_status'] = status
    data['ccb_resume_compatibility'] = (
        'managed_local_history' if native_resume_compatible else 'linked_continuation'
    )
    data[f'{provider_name}_provider_authority_fingerprint'] = current_fingerprint
    if _provider_binding_present(data, provider_name) and native_resume_compatible:
        data[f'{provider_name}_session_authority_fingerprint'] = current_fingerprint
    if history:
        data['ccb_session_history'] = history

    continuity_fields_changed = any(
        data.get(key) != before.get(key)
        for key in (
            'ccb_continuity_schema_version',
            'ccb_conversation_id',
            'ccb_authority_generation',
            'ccb_continuity_status',
            'ccb_resume_compatibility',
            'ccb_session_history',
            f'{provider_name}_provider_authority_fingerprint',
            f'{provider_name}_session_authority_fingerprint',
            f'{provider_name}_session_id',
            f'{provider_name}_session_path',
        )
    )
    if continuity_fields_changed:
        data['ccb_continuity_updated_at'] = time.strftime('%Y-%m-%d %H:%M:%S')
    return data != before


def merge_session_continuity(
    payload: dict[str, object],
    existing: Mapping[str, object],
    provider: str,
) -> None:
    """Carry stable CCB conversation metadata across Provider launches."""
    provider_name = str(provider or '').strip().lower()
    existing_data = dict(existing or {})
    launch_session_id = str(payload.get('ccb_session_id') or '').strip()
    previous_launch_id = str(existing_data.get('ccb_session_id') or '').strip()
    conversation_id = str(existing_data.get('ccb_conversation_id') or '').strip()
    if not conversation_id:
        conversation_id = (
            str(payload.get('ccb_conversation_id') or '').strip()
            or previous_launch_id
            or _provider_session_id(existing_data, provider_name)
            or launch_session_id
        )
    if not conversation_id:
        conversation_id = f'ccb-conversation-{secrets.token_hex(8)}'

    previous_fingerprint = stored_provider_authority_fingerprint(existing_data, provider_name)
    current_fingerprint = stored_provider_authority_fingerprint(payload, provider_name)
    generation = max(
        _positive_int(existing_data.get('ccb_authority_generation'), default=1),
        _positive_int(payload.get('ccb_authority_generation'), default=1),
    )
    history = _session_history(existing_data)
    authority_changed = bool(
        previous_fingerprint
        and current_fingerprint
        and not hmac.compare_digest(previous_fingerprint, current_fingerprint)
    )
    old_binding = _provider_binding_record(
        existing_data,
        provider_name,
        generation=generation,
        conversation_id=conversation_id,
    )
    current_binding = _provider_binding_present(payload, provider_name)

    if authority_changed:
        generation += 1
        if old_binding is not None:
            _append_unique_history(history, old_binding)
        status = 'continued_on_new_authority'
        compatibility = str(payload.get('ccb_resume_compatibility') or '').strip()
        if not compatibility:
            compatibility = 'managed_local_history' if current_binding else 'linked_continuation'
    elif existing_data:
        status = str(existing_data.get('ccb_continuity_status') or '').strip() or 'resumed_same_authority'
        compatibility = str(existing_data.get('ccb_resume_compatibility') or '').strip()
        if not compatibility:
            compatibility = 'managed_local_history' if current_binding else 'linked_continuation'
    else:
        status = 'new_conversation'
        compatibility = 'managed_local_history' if current_binding else 'pending_native_binding'

    payload['ccb_continuity_schema_version'] = _CONTINUITY_SCHEMA_VERSION
    payload['ccb_conversation_id'] = conversation_id
    payload['ccb_authority_generation'] = generation
    payload['ccb_continuity_status'] = status
    payload['ccb_resume_compatibility'] = compatibility
    if previous_launch_id and previous_launch_id != launch_session_id:
        payload['ccb_parent_session_id'] = previous_launch_id
        payload['ccb_parent_conversation_id'] = conversation_id
    elif existing_data.get('ccb_parent_session_id'):
        payload['ccb_parent_session_id'] = existing_data['ccb_parent_session_id']
        if existing_data.get('ccb_parent_conversation_id'):
            payload['ccb_parent_conversation_id'] = existing_data['ccb_parent_conversation_id']
    if history:
        payload['ccb_session_history'] = history
    payload['ccb_continuity_updated_at'] = time.strftime('%Y-%m-%d %H:%M:%S')


def _conversation_id(data: Mapping[str, object], provider: str) -> str:
    for value in (
        data.get('ccb_conversation_id'),
        data.get('ccb_session_id'),
        data.get(f'{provider}_session_id'),
    ):
        text = str(value or '').strip()
        if text:
            return text
    return f'ccb-conversation-{secrets.token_hex(8)}'


def _provider_session_id(data: Mapping[str, object], provider: str) -> str:
    return str(data.get(f'{provider}_session_id') or '').strip()


def _provider_binding_present(data: Mapping[str, object], provider: str) -> bool:
    return bool(
        _provider_session_id(data, provider)
        or str(data.get(f'{provider}_session_path') or '').strip()
    )


def _provider_binding_record(
    data: Mapping[str, object],
    provider: str,
    *,
    generation: int,
    conversation_id: str | None = None,
) -> dict[str, object] | None:
    session_id = _provider_session_id(data, provider)
    session_path = str(data.get(f'{provider}_session_path') or '').strip()
    if not session_id and not session_path:
        return None
    payload: dict[str, object] = {
        'provider': provider,
        'authority_generation': generation,
        'continuity_status': str(data.get('ccb_continuity_status') or '').strip() or 'historical',
        'conversation_id': (
            str(conversation_id or '').strip()
            or str(data.get('ccb_conversation_id') or '').strip()
        ),
    }
    if session_id:
        payload['provider_session_id'] = session_id
    if session_path:
        payload['provider_session_path'] = session_path
    return payload


def _session_history(data: Mapping[str, object]) -> list[dict[str, object]]:
    raw = data.get('ccb_session_history')
    if not isinstance(raw, list):
        return []
    return [dict(item) for item in raw if isinstance(item, dict)]


def _append_unique_history(history: list[dict[str, object]], item: dict[str, object]) -> None:
    identity = (
        str(item.get('provider') or ''),
        str(item.get('provider_session_id') or ''),
        str(item.get('provider_session_path') or ''),
        str(item.get('authority_generation') or ''),
    )
    for existing in history:
        candidate = (
            str(existing.get('provider') or ''),
            str(existing.get('provider_session_id') or ''),
            str(existing.get('provider_session_path') or ''),
            str(existing.get('authority_generation') or ''),
        )
        if candidate == identity:
            return
    history.append(item)


def _positive_int(value: object, *, default: int) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return default
    return parsed if parsed > 0 else default


def _profile_record(profile) -> dict[str, object]:
    if profile is None:
        return {}
    return {
        'provider': str(getattr(profile, 'provider', '') or ''),
        'agent_name': str(getattr(profile, 'agent_name', '') or ''),
        'mode': str(getattr(profile, 'mode', '') or ''),
        'runtime_home': str(getattr(profile, 'runtime_home', '') or ''),
        'env': dict(getattr(profile, 'env', {}) or {}),
        'inherit_api': bool(getattr(profile, 'inherit_api', True)),
        'inherit_auth': bool(getattr(profile, 'inherit_auth', True)),
        'inherit_config': bool(getattr(profile, 'inherit_config', True)),
    }


def _credential_env_keys(provider: str) -> set[str]:
    return {
        'claude': {'ANTHROPIC_API_KEY', 'ANTHROPIC_AUTH_TOKEN'},
        'gemini': {'GEMINI_API_KEY', 'GOOGLE_API_KEY', 'GOOGLE_APPLICATION_CREDENTIALS'},
        'codex': {'OPENAI_API_KEY'},
        'dsh': {'DEEPSEEK_API_KEY'},
    }.get(provider, set())


def _explicit_api_owned_names(provider: str, profile_env: Mapping[str, str]) -> set[str]:
    """Expand an explicit value to every alias in the same authority dimension."""
    configured = set(profile_env)
    groups: dict[str, tuple[set[str], ...]] = {
        'claude': (
            {'ANTHROPIC_API_KEY', 'ANTHROPIC_AUTH_TOKEN'},
            {'ANTHROPIC_BASE_URL'},
        ),
        'gemini': (
            {'GEMINI_API_KEY', 'GOOGLE_API_KEY', 'GOOGLE_APPLICATION_CREDENTIALS'},
            {'GOOGLE_API_BASE', 'GOOGLE_GEMINI_BASE_URL', 'GOOGLE_VERTEX_BASE_URL'},
            {'GOOGLE_GENAI_USE_VERTEXAI', 'GOOGLE_GENAI_USE_GCA'},
            {'GOOGLE_CLOUD_PROJECT'},
            {'GOOGLE_CLOUD_LOCATION'},
            {'GEMINI_MODEL'},
        ),
        'codex': (
            {'OPENAI_API_KEY'},
            {'OPENAI_BASE_URL', 'OPENAI_API_BASE'},
            {'OPENAI_ORG_ID', 'OPENAI_ORGANIZATION'},
        ),
        'dsh': (
            {'DEEPSEEK_API_KEY'},
            {'DEEPSEEK_BASE_URL'},
        ),
    }.get(provider, ())
    owned: set[str] = set()
    for group in groups:
        if group & configured:
            owned.update(group)
    return owned


def _managed_home(runtime_dir: Path, profile, provider: str) -> Path:
    explicit = str(getattr(profile, 'runtime_home', '') or '').strip()
    if explicit:
        return Path(explicit).expanduser()
    return runtime_dir.parent.parent / 'provider-state' / provider / 'home'


def _auth_file_payload(root: Path, provider: str) -> dict[str, str]:
    payload: dict[str, str] = {}
    for relative in _AUTH_FILES.get(provider, ()):
        path = Path(root).expanduser() / relative
        content = _read_optional_regular_file(path, label=f'{provider} auth')
        if content is not None:
            payload[relative] = content.hex()
    return payload


def _metadata_file_payload(root: Path, provider: str) -> dict[str, object]:
    payload: dict[str, object] = {}
    for relative in _AUTH_METADATA_FILES.get(provider, ()):
        path = Path(root).expanduser() / relative
        content = _read_optional_regular_file(path, label=f'{provider} auth metadata')
        if content is None:
            continue
        try:
            data = json.loads(content.decode('utf-8'))
        except (UnicodeError, ValueError, TypeError) as exc:
            raise RuntimeError(f'cannot parse {provider} auth metadata source: {path}') from exc
        if not isinstance(data, dict):
            raise RuntimeError(f'{provider} auth metadata source must contain an object: {path}')
        if provider == 'claude' and relative in {'.claude.json', '.claude/.claude.json'}:
            selected = {key: data.get(key) for key in ('oauthAccount', 'primaryApiKey') if key in data}
        elif provider == 'gemini' and relative == '.gemini/settings.json':
            security = data.get('security') if isinstance(data, dict) else None
            auth = security.get('auth') if isinstance(security, dict) else None
            selected = (
                {'selectedType': auth.get('selectedType')}
                if isinstance(auth, dict) and 'selectedType' in auth
                else {}
            )
        else:
            selected = {}
        if selected:
            payload[relative] = selected
    return payload


def _api_file_payload(
    root: Path,
    provider: str,
    *,
    excluded: set[str] | None = None,
) -> dict[str, object]:
    payload: dict[str, object] = {}
    allowed = provider_api_env_keys(provider) - set(excluded or ())
    for relative in _API_FILES.get(provider, ()):
        path = Path(root).expanduser() / relative
        if relative.endswith('settings.json'):
            content = _read_optional_regular_file(path, label=f'{provider} API config')
            if content is None:
                continue
            try:
                data = json.loads(content.decode('utf-8'))
            except (UnicodeError, ValueError, TypeError) as exc:
                raise RuntimeError(f'cannot parse {provider} API config source: {path}') from exc
            if not isinstance(data, dict):
                raise RuntimeError(f'{provider} API config source must contain an object: {path}')
            env = data.get('env') if isinstance(data, dict) else None
            selected = {
                str(key): str(value)
                for key, value in dict(env or {}).items()
                if str(key) in allowed
            }
        elif relative.endswith('.env'):
            selected = _selected_dotenv_values(path, allowed=allowed)
        else:
            selected = {}
        if selected:
            payload[relative] = selected
    return payload


def _projected_auth_file_payload(root: Path, provider: str) -> dict[str, str]:
    """Read only CCB-owned auth projections for authority fingerprinting."""
    manifest_path = Path(root).expanduser() / _AUTH_PROJECTION_MANIFEST
    try:
        content = _read_optional_regular_file(manifest_path, label=f'{provider} auth projection')
        if content is None:
            return {}
        payload = json.loads(content.decode('utf-8'))
    except (UnicodeError, ValueError, TypeError, RuntimeError):
        # A malformed marker must never claim ownership of Agent-private auth.
        return {}
    expected_record = f'ccb_{provider}_auth_projection'
    if not isinstance(payload, dict) or payload.get('record_type') != expected_record:
        return {}
    raw_files = payload.get('projected_files')
    if not isinstance(raw_files, list):
        return {}
    allowed = set(_AUTH_FILES.get(provider, ()))
    selected: dict[str, str] = {}
    for raw_name in raw_files:
        name = str(raw_name or '').strip()
        if provider == 'gemini' and name and not name.startswith('.gemini/'):
            name = f'.gemini/{name}'
        if name not in allowed:
            continue
        content = _read_optional_regular_file(Path(root).expanduser() / name, label=f'{provider} projected auth')
        if content is not None:
            selected[name] = content.hex()
    return selected


def _selected_dotenv_values(path: Path, *, allowed: set[str]) -> dict[str, str]:
    content = _read_optional_regular_file(path, label='Provider API env')
    if content is None:
        return {}
    try:
        lines = content.decode('utf-8').splitlines()
    except UnicodeError as exc:
        raise RuntimeError(f'cannot parse Provider API env source: {path}') from exc
    selected: dict[str, str] = {}
    for raw_line in lines:
        line = raw_line.strip()
        if not line or line.startswith('#'):
            continue
        if line.startswith('export '):
            line = line[7:].lstrip()
        key, separator, value = line.partition('=')
        normalized_key = key.strip()
        if separator and normalized_key in allowed:
            selected[normalized_key] = value.strip()
    return selected


def _read_optional_regular_file(path: Path, *, label: str) -> bytes | None:
    source = Path(path).expanduser()
    try:
        metadata = source.lstat()
    except FileNotFoundError:
        return None
    except OSError as exc:
        raise RuntimeError(f'cannot inspect {label} source: {source}: {exc}') from exc
    if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISREG(metadata.st_mode):
        raise RuntimeError(f'{label} source must be a regular file: {source}')
    try:
        return source.read_bytes()
    except OSError as exc:
        raise RuntimeError(f'cannot read {label} source: {source}: {exc}') from exc


def _load_or_create_key(path: Path) -> bytes:
    try:
        key = bytes.fromhex(path.read_text(encoding='ascii').strip())
        if len(key) >= 32:
            return key
    except (OSError, ValueError):
        pass
    key = secrets.token_bytes(32)
    atomic_write_text(path, key.hex() + '\n')
    try:
        os.chmod(path, 0o600)
    except OSError:
        pass
    return key


__all__ = [
    'current_provider_authority_fingerprint',
    'linked_continuation_pending',
    'remember_bound_provider_session_authority',
    'merge_session_continuity',
    'provider_authority_matches',
    'rebind_provider_session_authority',
    'rebind_provider_session_data',
    'stored_provider_authority_fingerprint',
]
