from __future__ import annotations

import os
from collections.abc import Mapping

from runtime_env.git_identity import GIT_IDENTITY_ENV_KEYS


NETWORK_PROXY_ENV_KEYS = frozenset(
    {
        'HTTP_PROXY',
        'HTTPS_PROXY',
        'ALL_PROXY',
        'NO_PROXY',
        'http_proxy',
        'https_proxy',
        'all_proxy',
        'no_proxy',
        'WS_PROXY',
        'WSS_PROXY',
        'ws_proxy',
        'wss_proxy',
        'NPM_CONFIG_PROXY',
        'NPM_CONFIG_HTTPS_PROXY',
        'NPM_CONFIG_NO_PROXY',
        'npm_config_proxy',
        'npm_config_https_proxy',
        'npm_config_no_proxy',
        'YARN_PROXY',
        'YARN_HTTPS_PROXY',
        'YARN_NO_PROXY',
        'yarn_proxy',
        'yarn_https_proxy',
        'yarn_no_proxy',
        'BUNDLE_HTTPS_PROXY',
        'BUNDLE_NO_PROXY',
        'bundle_https_proxy',
        'bundle_no_proxy',
    }
)

TRUST_STORE_ENV_KEYS = frozenset(
    {
        'CODEX_CA_CERTIFICATE',
        'SSL_CERT_FILE',
        'SSL_CERT_DIR',
        'REQUESTS_CA_BUNDLE',
        'CURL_CA_BUNDLE',
        'NODE_EXTRA_CA_CERTS',
        'GIT_SSL_CAINFO',
        'NPM_CONFIG_CAFILE',
        'npm_config_cafile',
    }
)

DESKTOP_SESSION_ENV_KEYS = frozenset(
    {
        'BROWSER',
        'DBUS_SESSION_BUS_ADDRESS',
        'DESKTOP_SESSION',
        'DISPLAY',
        'SSH_AUTH_SOCK',
        'SSH_CONNECTION',
        'WAYLAND_DISPLAY',
        'XAUTHORITY',
        'XDG_CURRENT_DESKTOP',
        'XDG_RUNTIME_DIR',
        'XDG_SESSION_DESKTOP',
        'XDG_SESSION_TYPE',
    }
)

WSL_SESSION_ENV_KEYS = frozenset(
    {
        'WSL_DISTRO_NAME',
        'WSL_INTEROP',
        'WSLENV',
        'WT_PROFILE_ID',
        'WT_SESSION',
    }
)

ROLE_STORE_ENV_KEYS = frozenset(
    {
        'AGENT_ROLES_STORE',
    }
)

USER_SESSION_TRANSPORT_ENV_KEYS = frozenset(
    NETWORK_PROXY_ENV_KEYS
    | TRUST_STORE_ENV_KEYS
    | DESKTOP_SESSION_ENV_KEYS
    | WSL_SESSION_ENV_KEYS
    | ROLE_STORE_ENV_KEYS
    | GIT_IDENTITY_ENV_KEYS
)


def user_session_transport_env(environ: Mapping[str, object] | None = None) -> dict[str, str]:
    source = os.environ if environ is None else environ
    return {
        key: str(value)
        for key, value in source.items()
        if key in USER_SESSION_TRANSPORT_ENV_KEYS and value is not None and str(value).strip()
    }


__all__ = [
    'DESKTOP_SESSION_ENV_KEYS',
    'NETWORK_PROXY_ENV_KEYS',
    'ROLE_STORE_ENV_KEYS',
    'TRUST_STORE_ENV_KEYS',
    'USER_SESSION_TRANSPORT_ENV_KEYS',
    'WSL_SESSION_ENV_KEYS',
    'user_session_transport_env',
]
