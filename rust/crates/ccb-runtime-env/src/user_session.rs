//! User-session transport environment keys.
//!
//! Mirrors `runtime_env/user_session.py` from Python v7.5.2.

use std::collections::{HashMap, HashSet};

/// Network proxy-related environment keys forwarded to provider runtimes.
pub const NETWORK_PROXY_ENV_KEYS: &[&str] = &[
    "HTTP_PROXY",
    "HTTPS_PROXY",
    "ALL_PROXY",
    "NO_PROXY",
    "http_proxy",
    "https_proxy",
    "all_proxy",
    "no_proxy",
    "WS_PROXY",
    "WSS_PROXY",
    "ws_proxy",
    "wss_proxy",
    "NPM_CONFIG_PROXY",
    "NPM_CONFIG_HTTPS_PROXY",
    "NPM_CONFIG_NO_PROXY",
    "npm_config_proxy",
    "npm_config_https_proxy",
    "npm_config_no_proxy",
    "YARN_PROXY",
    "YARN_HTTPS_PROXY",
    "YARN_NO_PROXY",
    "yarn_proxy",
    "yarn_https_proxy",
    "yarn_no_proxy",
    "BUNDLE_HTTPS_PROXY",
    "BUNDLE_NO_PROXY",
    "bundle_https_proxy",
    "bundle_no_proxy",
];

/// Trust-store / CA certificate environment keys forwarded to provider
/// runtimes.
pub const TRUST_STORE_ENV_KEYS: &[&str] = &[
    "CODEX_CA_CERTIFICATE",
    "SSL_CERT_FILE",
    "SSL_CERT_DIR",
    "REQUESTS_CA_BUNDLE",
    "CURL_CA_BUNDLE",
    "NODE_EXTRA_CA_CERTS",
    "GIT_SSL_CAINFO",
    "NPM_CONFIG_CAFILE",
    "npm_config_cafile",
];

/// Desktop session environment keys forwarded to provider runtimes.
pub const DESKTOP_SESSION_ENV_KEYS: &[&str] = &[
    "BROWSER",
    "DBUS_SESSION_BUS_ADDRESS",
    "DESKTOP_SESSION",
    "DISPLAY",
    "SSH_AUTH_SOCK",
    "SSH_CONNECTION",
    "WAYLAND_DISPLAY",
    "XAUTHORITY",
    "XDG_CURRENT_DESKTOP",
    "XDG_RUNTIME_DIR",
    "XDG_SESSION_DESKTOP",
    "XDG_SESSION_TYPE",
];

/// WSL session environment keys forwarded to provider runtimes.
pub const WSL_SESSION_ENV_KEYS: &[&str] = &[
    "WSL_DISTRO_NAME",
    "WSL_INTEROP",
    "WSLENV",
    "WT_PROFILE_ID",
    "WT_SESSION",
];

/// The full set of environment keys transported from the user's session into
/// provider runtimes.
pub const USER_SESSION_TRANSPORT_ENV_KEYS: &[&str] = &[
    // NETWORK_PROXY_ENV_KEYS
    "HTTP_PROXY",
    "HTTPS_PROXY",
    "ALL_PROXY",
    "NO_PROXY",
    "http_proxy",
    "https_proxy",
    "all_proxy",
    "no_proxy",
    "WS_PROXY",
    "WSS_PROXY",
    "ws_proxy",
    "wss_proxy",
    "NPM_CONFIG_PROXY",
    "NPM_CONFIG_HTTPS_PROXY",
    "NPM_CONFIG_NO_PROXY",
    "npm_config_proxy",
    "npm_config_https_proxy",
    "npm_config_no_proxy",
    "YARN_PROXY",
    "YARN_HTTPS_PROXY",
    "YARN_NO_PROXY",
    "yarn_proxy",
    "yarn_https_proxy",
    "yarn_no_proxy",
    "BUNDLE_HTTPS_PROXY",
    "BUNDLE_NO_PROXY",
    "bundle_https_proxy",
    "bundle_no_proxy",
    // TRUST_STORE_ENV_KEYS
    "CODEX_CA_CERTIFICATE",
    "SSL_CERT_FILE",
    "SSL_CERT_DIR",
    "REQUESTS_CA_BUNDLE",
    "CURL_CA_BUNDLE",
    "NODE_EXTRA_CA_CERTS",
    "GIT_SSL_CAINFO",
    "NPM_CONFIG_CAFILE",
    "npm_config_cafile",
    // DESKTOP_SESSION_ENV_KEYS
    "BROWSER",
    "DBUS_SESSION_BUS_ADDRESS",
    "DESKTOP_SESSION",
    "DISPLAY",
    "SSH_AUTH_SOCK",
    "SSH_CONNECTION",
    "WAYLAND_DISPLAY",
    "XAUTHORITY",
    "XDG_CURRENT_DESKTOP",
    "XDG_RUNTIME_DIR",
    "XDG_SESSION_DESKTOP",
    "XDG_SESSION_TYPE",
    // WSL_SESSION_ENV_KEYS
    "WSL_DISTRO_NAME",
    "WSL_INTEROP",
    "WSLENV",
    "WT_PROFILE_ID",
    "WT_SESSION",
];

/// Extract user-session transport environment variables.
///
/// When `environ` is `None`, the current process environment is used.
/// Empty or whitespace-only values are dropped.
pub fn user_session_transport_env(
    environ: Option<&HashMap<String, String>>,
) -> HashMap<String, String> {
    let keys: HashSet<&str> = USER_SESSION_TRANSPORT_ENV_KEYS.iter().copied().collect();
    let source: HashMap<String, String> = match environ {
        Some(e) => e.clone(),
        None => std::env::vars().collect(),
    };

    source
        .into_iter()
        .filter(|(key, value)| keys.contains(key.as_str()) && !value.trim().is_empty())
        .collect()
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_user_session_transport_env_selects_only_transport_keys() {
        let input = HashMap::from([
            (
                "HTTPS_PROXY".to_string(),
                "http://127.0.0.1:7890".to_string(),
            ),
            (
                "http_proxy".to_string(),
                "http://127.0.0.1:7891".to_string(),
            ),
            ("NO_PROXY".to_string(), "localhost,127.0.0.1".to_string()),
            (
                "CODEX_CA_CERTIFICATE".to_string(),
                "/tmp/codex-ca.pem".to_string(),
            ),
            (
                "NODE_EXTRA_CA_CERTS".to_string(),
                "/tmp/node-ca.pem".to_string(),
            ),
            (
                "WSL_INTEROP".to_string(),
                "/run/WSL/1234_interop".to_string(),
            ),
            ("BROWSER".to_string(), "wslview".to_string()),
            (
                "CODEX_HOME".to_string(),
                "/tmp/global-codex-home".to_string(),
            ),
            (
                "GEMINI_ROOT".to_string(),
                "/tmp/global-gemini-root".to_string(),
            ),
            (
                "CLAUDE_PROJECTS_ROOT".to_string(),
                "/tmp/global-claude-projects".to_string(),
            ),
            ("EMPTY_PROXY".to_string(), "".to_string()),
            ("SSL_CERT_FILE".to_string(), "".to_string()),
        ]);

        let env = user_session_transport_env(Some(&input));

        assert_eq!(env.get("HTTPS_PROXY").unwrap(), "http://127.0.0.1:7890");
        assert_eq!(env.get("http_proxy").unwrap(), "http://127.0.0.1:7891");
        assert_eq!(env.get("NO_PROXY").unwrap(), "localhost,127.0.0.1");
        assert_eq!(
            env.get("CODEX_CA_CERTIFICATE").unwrap(),
            "/tmp/codex-ca.pem"
        );
        assert_eq!(env.get("NODE_EXTRA_CA_CERTS").unwrap(), "/tmp/node-ca.pem");
        assert_eq!(env.get("WSL_INTEROP").unwrap(), "/run/WSL/1234_interop");
        assert_eq!(env.get("BROWSER").unwrap(), "wslview");
        assert!(!env.contains_key("CODEX_HOME"));
        assert!(!env.contains_key("GEMINI_ROOT"));
        assert!(!env.contains_key("CLAUDE_PROJECTS_ROOT"));
        assert!(!env.contains_key("EMPTY_PROXY"));
        assert!(!env.contains_key("SSL_CERT_FILE"));
    }
}
