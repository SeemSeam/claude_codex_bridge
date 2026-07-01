//! Control-plane environment filtering.
//!
//! Mirrors `runtime_env/control_plane.py` from Python v7.5.2.

use std::collections::{HashMap, HashSet};

use crate::user_session::USER_SESSION_TRANSPORT_ENV_KEYS;

/// Provider start-command override environment variables. In Python this list
/// is exported by `provider_core.runtime_shared`; it is inlined here to avoid
/// a circular dependency between the runtime-env and provider-core crates.
const PROVIDER_START_ENV_VARS: &[&str] = &[
    "CODEX_START_CMD",
    "CLAUDE_START_CMD",
    "GEMINI_START_CMD",
    "OPENCODE_START_CMD",
    "DROID_START_CMD",
    "AGY_START_CMD",
    "KIMI_START_CMD",
    "DEEPSEEK_START_CMD",
    "MIMO_START_CMD",
    "QWEN_START_CMD",
    "CURSOR_START_CMD",
    "COPILOT_START_CMD",
    "CRUSH_START_CMD",
    "KIRO_START_CMD",
    "PI_START_CMD",
];

/// Environment variable names explicitly forwarded to the control plane.
pub const CONTROL_PLANE_ALLOWLIST: &[&str] = &[
    "ANTHROPIC_API_KEY",
    "ANTHROPIC_AUTH_TOKEN",
    "ANTHROPIC_BASE_URL",
    "CCB_BACKEND_ENV",
    "CCB_CCBD_FAULTHANDLER",
    "CCB_CCBD_MIN_POLL_INTERVAL_S",
    "CCB_DEBUG",
    "CCB_KEEPER_PID",
    "CCB_KEYCHAIN_SERVICE_OVERRIDE",
    "CCB_LANG",
    "CCB_NO_ATTACH",
    "CCB_REPLY_LANG",
    "CCB_STDIN_ENCODING",
    "CCB_VERSION",
    "DBUS_SESSION_BUS_ADDRESS",
    "DESKTOP_SESSION",
    "DISPLAY",
    "GEMINI_API_KEY",
    "GEMINI_MODEL",
    "GOOGLE_API_BASE",
    "GOOGLE_API_KEY",
    "GOOGLE_GEMINI_BASE_URL",
    "GOOGLE_GENAI_USE_VERTEXAI",
    "HOME",
    "LANG",
    "LC_ALL",
    "LC_MESSAGES",
    "LOCALAPPDATA",
    "OPENAI_API_BASE",
    "OPENAI_API_KEY",
    "OPENAI_BASE_URL",
    "OPENAI_ORG_ID",
    "OPENAI_ORGANIZATION",
    "PATH",
    "PYTHONUNBUFFERED",
    "SHELL",
    "SSH_AUTH_SOCK",
    "SYSTEMROOT",
    "TERM",
    "TMP",
    "TEMP",
    "TMPDIR",
    "USER",
    "USERPROFILE",
    "XDG_CACHE_HOME",
    "XDG_CONFIG_HOME",
    "XDG_CURRENT_DESKTOP",
    "XDG_DATA_HOME",
    "XDG_RUNTIME_DIR",
    "XDG_SESSION_DESKTOP",
    "XDG_SESSION_TYPE",
    "XAUTHORITY",
    "WAYLAND_DISPLAY",
];

/// Environment variable prefixes blocked from the control plane.
pub const CONTROL_PLANE_BLOCKED_PREFIXES: &[&str] = &[
    "CODEX_",
    "CLAUDE_",
    "GEMINI_",
    "OPENCODE_",
    "DROID_",
    "CCB_CALLER_",
];

/// Environment variable names exactly blocked from the control plane.
pub const CONTROL_PLANE_BLOCKED_EXACT: &[&str] = &[
    "CCB_SESSION_FILE",
    "CCB_SESSION_ID",
    "CCB_TMUX_SOCKET",
    "CCB_TMUX_SOCKET_PATH",
    "PYTHONPATH",
    "TMUX",
    "TMUX_PANE",
];

/// Build the environment for the CCB control plane.
///
/// `extra` may be used to add, override, or remove (`None`) variables after the
/// base filtering is applied.
pub fn control_plane_env(
    extra: Option<&HashMap<String, Option<String>>>,
) -> HashMap<String, String> {
    let allowlist: HashSet<&str> = CONTROL_PLANE_ALLOWLIST.iter().copied().collect();
    let blocked_exact: HashSet<&str> = CONTROL_PLANE_BLOCKED_EXACT.iter().copied().collect();
    let transport_keys: HashSet<&str> = USER_SESSION_TRANSPORT_ENV_KEYS.iter().copied().collect();
    let provider_start: HashSet<&str> = PROVIDER_START_ENV_VARS.iter().copied().collect();

    let mut env = HashMap::new();
    for (key, value) in std::env::vars() {
        if blocked_exact.contains(key.as_str()) {
            continue;
        }
        if allowlist.contains(key.as_str())
            || transport_keys.contains(key.as_str())
            || provider_start.contains(key.as_str())
        {
            env.insert(key, value);
            continue;
        }
        if CONTROL_PLANE_BLOCKED_PREFIXES
            .iter()
            .any(|prefix| key.starts_with(prefix))
        {
            continue;
        }
        if key == "PYTHONPATH" {
            continue;
        }
        if key.starts_with("PYTHON") || key.starts_with("VIRTUAL_ENV") || key.starts_with("CONDA") {
            env.insert(key, value);
        }
    }

    if let Some(extra) = extra {
        for (key, value) in extra {
            match value {
                Some(v) => {
                    env.insert(key.clone(), v.clone());
                }
                None => {
                    env.remove(key);
                }
            }
        }
    }

    env
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::collections::HashMap;

    fn clear_test_env() {
        for key in [
            "OPENAI_API_KEY",
            "OPENAI_BASE_URL",
            "ANTHROPIC_API_KEY",
            "GEMINI_API_KEY",
            "GEMINI_MODEL",
            "GOOGLE_GEMINI_BASE_URL",
            "CCB_KEYCHAIN_SERVICE_OVERRIDE",
            "DISPLAY",
            "WAYLAND_DISPLAY",
            "DBUS_SESSION_BUS_ADDRESS",
            "XAUTHORITY",
            "SSH_AUTH_SOCK",
            "HTTPS_PROXY",
            "NO_PROXY",
            "CODEX_CA_CERTIFICATE",
            "SSL_CERT_FILE",
            "WSL_INTEROP",
            "WSL_DISTRO_NAME",
            "CODEX_HOME",
            "CODEX_SESSION_ROOT",
            "GEMINI_ROOT",
            "CLAUDE_PROJECTS_ROOT",
            "CCB_SESSION_ID",
            "CCB_CALLER_ACTOR",
            "TMUX",
            "TMUX_PANE",
            "CCB_TMUX_SOCKET",
            "CCB_TMUX_SOCKET_PATH",
            "PYTHONPATH",
            "PYTHONUNBUFFERED",
        ] {
            std::env::remove_var(key);
        }
        for env_name in PROVIDER_START_ENV_VARS {
            std::env::remove_var(env_name);
        }
    }

    #[test]
    fn test_control_plane_env_keeps_provider_api_env() {
        clear_test_env();
        std::env::set_var("OPENAI_API_KEY", "openai-key");
        std::env::set_var("OPENAI_BASE_URL", "https://api.example.test/v1");
        std::env::set_var("ANTHROPIC_API_KEY", "anthropic-key");
        std::env::set_var("GEMINI_API_KEY", "gemini-key");
        std::env::set_var("GEMINI_MODEL", "gemini-3.1-pro-preview");
        std::env::set_var("GOOGLE_GEMINI_BASE_URL", "https://chatapi.onechats.ai");

        let env = control_plane_env(None);

        assert_eq!(env.get("OPENAI_API_KEY").unwrap(), "openai-key");
        assert_eq!(
            env.get("OPENAI_BASE_URL").unwrap(),
            "https://api.example.test/v1"
        );
        assert_eq!(env.get("ANTHROPIC_API_KEY").unwrap(), "anthropic-key");
        assert_eq!(env.get("GEMINI_API_KEY").unwrap(), "gemini-key");
        assert_eq!(env.get("GEMINI_MODEL").unwrap(), "gemini-3.1-pro-preview");
        assert_eq!(
            env.get("GOOGLE_GEMINI_BASE_URL").unwrap(),
            "https://chatapi.onechats.ai"
        );
    }

    #[test]
    fn test_control_plane_env_keeps_provider_start_overrides() {
        clear_test_env();
        for env_name in PROVIDER_START_ENV_VARS {
            std::env::set_var(env_name, format!("/tmp/{} --stub", env_name.to_lowercase()));
        }
        std::env::set_var("CODEX_HOME", "/tmp/global-codex-home");
        std::env::set_var("QWEN_HOME", "/tmp/global-qwen-home");
        std::env::set_var("CCB_SESSION_ID", "stale-session");

        let env = control_plane_env(None);

        for env_name in PROVIDER_START_ENV_VARS {
            assert_eq!(
                env.get(*env_name).unwrap(),
                &format!("/tmp/{} --stub", env_name.to_lowercase())
            );
        }
        assert!(!env.contains_key("CODEX_HOME"));
        assert!(!env.contains_key("QWEN_HOME"));
        assert!(!env.contains_key("CCB_SESSION_ID"));
    }

    #[test]
    fn test_control_plane_env_keeps_claude_keychain_override() {
        clear_test_env();
        std::env::set_var(
            "CCB_KEYCHAIN_SERVICE_OVERRIDE",
            "Claude Code-credentials-account-a",
        );

        let env = control_plane_env(None);

        assert_eq!(
            env.get("CCB_KEYCHAIN_SERVICE_OVERRIDE").unwrap(),
            "Claude Code-credentials-account-a"
        );
    }

    #[test]
    fn test_control_plane_env_keeps_user_session_transport_for_cmd_shell() {
        clear_test_env();
        std::env::set_var("DISPLAY", ":0");
        std::env::set_var("WAYLAND_DISPLAY", "wayland-0");
        std::env::set_var("DBUS_SESSION_BUS_ADDRESS", "unix:path=/run/user/1000/bus");
        std::env::set_var("XAUTHORITY", "/tmp/.Xauthority");
        std::env::set_var("SSH_AUTH_SOCK", "/tmp/ssh-agent.sock");

        let env = control_plane_env(None);

        assert_eq!(env.get("DISPLAY").unwrap(), ":0");
        assert_eq!(env.get("WAYLAND_DISPLAY").unwrap(), "wayland-0");
        assert_eq!(
            env.get("DBUS_SESSION_BUS_ADDRESS").unwrap(),
            "unix:path=/run/user/1000/bus"
        );
        assert_eq!(env.get("XAUTHORITY").unwrap(), "/tmp/.Xauthority");
        assert_eq!(env.get("SSH_AUTH_SOCK").unwrap(), "/tmp/ssh-agent.sock");
    }

    #[test]
    fn test_control_plane_env_keeps_network_transport_without_provider_authority() {
        clear_test_env();
        std::env::set_var("HTTPS_PROXY", "http://127.0.0.1:7890");
        std::env::set_var("NO_PROXY", "localhost,127.0.0.1");
        std::env::set_var("CODEX_CA_CERTIFICATE", "/tmp/codex-ca.pem");
        std::env::set_var("SSL_CERT_FILE", "/tmp/ca.pem");
        std::env::set_var("WSL_INTEROP", "/run/WSL/1234_interop");
        std::env::set_var("WSL_DISTRO_NAME", "Ubuntu-22.04");
        std::env::set_var("CODEX_HOME", "/tmp/global-codex-home");
        std::env::set_var("CODEX_SESSION_ROOT", "/tmp/global-codex-sessions");
        std::env::set_var("GEMINI_ROOT", "/tmp/global-gemini-root");
        std::env::set_var("CLAUDE_PROJECTS_ROOT", "/tmp/global-claude-projects");
        std::env::set_var("CCB_SESSION_ID", "stale-session");
        std::env::set_var("CCB_CALLER_ACTOR", "stale-agent");

        let env = control_plane_env(None);

        assert_eq!(env.get("HTTPS_PROXY").unwrap(), "http://127.0.0.1:7890");
        assert_eq!(env.get("NO_PROXY").unwrap(), "localhost,127.0.0.1");
        assert_eq!(
            env.get("CODEX_CA_CERTIFICATE").unwrap(),
            "/tmp/codex-ca.pem"
        );
        assert_eq!(env.get("SSL_CERT_FILE").unwrap(), "/tmp/ca.pem");
        assert_eq!(env.get("WSL_INTEROP").unwrap(), "/run/WSL/1234_interop");
        assert_eq!(env.get("WSL_DISTRO_NAME").unwrap(), "Ubuntu-22.04");
        assert!(!env.contains_key("CODEX_HOME"));
        assert!(!env.contains_key("CODEX_SESSION_ROOT"));
        assert!(!env.contains_key("GEMINI_ROOT"));
        assert!(!env.contains_key("CLAUDE_PROJECTS_ROOT"));
        assert!(!env.contains_key("CCB_SESSION_ID"));
        assert!(!env.contains_key("CCB_CALLER_ACTOR"));
    }

    #[test]
    fn test_control_plane_env_drops_outer_tmux_authority() {
        clear_test_env();
        std::env::set_var("TMUX", "/tmp/tmux-1000/default,123,0");
        std::env::set_var("TMUX_PANE", "%77");
        std::env::set_var("CCB_TMUX_SOCKET", "outer");
        std::env::set_var("CCB_TMUX_SOCKET_PATH", "/tmp/outer.sock");

        let env = control_plane_env(None);

        assert!(!env.contains_key("TMUX"));
        assert!(!env.contains_key("TMUX_PANE"));
        assert!(!env.contains_key("CCB_TMUX_SOCKET"));
        assert!(!env.contains_key("CCB_TMUX_SOCKET_PATH"));
    }

    #[test]
    fn test_control_plane_env_drops_outer_pythonpath() {
        clear_test_env();
        std::env::set_var("PYTHONPATH", "/stable/ccb/lib:/other");
        std::env::set_var("PYTHONUNBUFFERED", "1");

        let env = control_plane_env(None);

        assert!(!env.contains_key("PYTHONPATH"));
        assert_eq!(env.get("PYTHONUNBUFFERED").unwrap(), "1");
    }

    #[test]
    fn test_control_plane_env_extra_overrides_and_removes() {
        clear_test_env();
        std::env::remove_var("PYTHONUNBUFFERED");
        let mut extra = HashMap::new();
        extra.insert("CUSTOM_KEY".to_string(), Some("custom-value".to_string()));
        extra.insert("PYTHONUNBUFFERED".to_string(), None);

        let env = control_plane_env(Some(&extra));

        assert_eq!(env.get("CUSTOM_KEY").unwrap(), "custom-value");
        assert!(!env.contains_key("PYTHONUNBUFFERED"));
    }
}
