from __future__ import annotations

from urllib.parse import urlsplit, urlunsplit


_PROVIDER_API_SHORTCUT_ENV = {
    'codex': {
        'key': 'OPENAI_API_KEY',
        'url': 'OPENAI_BASE_URL',
    },
    'claude': {
        'key': 'ANTHROPIC_API_KEY',
        'url': 'ANTHROPIC_BASE_URL',
    },
    'gemini': {
        'key': 'GEMINI_API_KEY',
        'url': 'GOOGLE_GEMINI_BASE_URL',
    },
    'deepseek': {
        'key': 'DEEPCODE_API_KEY',
        'url': 'DEEPCODE_BASE_URL',
    },
    'dsh': {
        'key': 'DEEPSEEK_API_KEY',
        'url': 'DEEPSEEK_BASE_URL',
    },
}


def provider_api_shortcut_env(provider: str, *, key: str | None = None, url: str | None = None) -> dict[str, str]:
    normalized_provider = str(provider or '').strip().lower()
    mapping = _PROVIDER_API_SHORTCUT_ENV.get(normalized_provider)
    if mapping is None:
        supported = ', '.join(sorted(_PROVIDER_API_SHORTCUT_ENV))
        raise ValueError(f'api shortcut is supported only for providers: {supported}')
    env: dict[str, str] = {}
    if str(key or '').strip():
        env[mapping['key']] = str(key).strip()
    if str(url or '').strip():
        env[mapping['url']] = _normalized_shortcut_url(normalized_provider, str(url).strip())
    return env


def _normalized_shortcut_url(provider: str, url: str) -> str:
    if provider != 'codex':
        return url
    try:
        parsed = urlsplit(url)
    except Exception:
        return url
    if not parsed.scheme or not parsed.netloc:
        return url
    path = parsed.path or ''
    if path in {'', '/'}:
        path = '/v1'
    elif path == '/v1/':
        path = '/v1'
    return urlunsplit((parsed.scheme, parsed.netloc, path, parsed.query, parsed.fragment))


def supported_provider_api_shortcuts() -> tuple[str, ...]:
    return tuple(sorted(_PROVIDER_API_SHORTCUT_ENV))


__all__ = ['provider_api_shortcut_env', 'supported_provider_api_shortcuts']
