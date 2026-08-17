from __future__ import annotations

from provider_core.runtime_specs import (
    AGY_CLIENT_SPEC,
    AGY_RUNTIME_SPEC,
    CLIENT_SPECS_BY_PROVIDER,
    CODEX_CLIENT_SPEC,
    CODEX_RUNTIME_SPEC,
    COPILOT_CLIENT_SPEC,
    COPILOT_RUNTIME_SPEC,
    CRUSH_CLIENT_SPEC,
    CRUSH_RUNTIME_SPEC,
    CURSOR_CLIENT_SPEC,
    CURSOR_RUNTIME_SPEC,
    DEEPSEEK_CLIENT_SPEC,
    DEEPSEEK_RUNTIME_SPEC,
    DSH_CLIENT_SPEC,
    DSH_RUNTIME_SPEC,
    GROK_CLIENT_SPEC,
    GROK_RUNTIME_SPEC,
    KIMI_CLIENT_SPEC,
    KIMI_RUNTIME_SPEC,
    KIRO_CLIENT_SPEC,
    KIRO_RUNTIME_SPEC,
    MIMO_CLIENT_SPEC,
    MIMO_RUNTIME_SPEC,
    PI_CLIENT_SPEC,
    PI_RUNTIME_SPEC,
    OMP_CLIENT_SPEC,
    OMP_RUNTIME_SPEC,
    QWEN_CLIENT_SPEC,
    QWEN_RUNTIME_SPEC,
    QODER_CLIENT_SPEC,
    QODER_RUNTIME_SPEC,
    QODERCLICN_CLIENT_SPEC,
    QODERCLICN_RUNTIME_SPEC,
    RUNTIME_SPECS_BY_PROVIDER,
    provider_env_name,
    provider_marker_prefix,
)


def test_runtime_specs_use_provider_native_names() -> None:
    assert CODEX_RUNTIME_SPEC.provider_key == "codex"
    assert CODEX_RUNTIME_SPEC.service_name == "codex"
    assert CODEX_RUNTIME_SPEC.state_file_name == "codex-runtime.json"
    assert CODEX_RUNTIME_SPEC.log_file_name == "codex-runtime.log"
    assert CODEX_RUNTIME_SPEC.idle_timeout_env == "CCB_CODEX_RUNTIME_IDLE_TIMEOUT_S"
    assert CODEX_RUNTIME_SPEC.lock_name == "codex-runtime"

    assert CODEX_CLIENT_SPEC.provider_key == "codex"
    assert CODEX_CLIENT_SPEC.enabled_env == "CCB_CODEX"
    assert CODEX_CLIENT_SPEC.autostart_env == "CCB_CODEX_AUTOSTART"
    assert CODEX_CLIENT_SPEC.state_file_env == "CCB_CODEX_STATE_FILE"
    assert CODEX_CLIENT_SPEC.session_filename == ".codex-session"
    assert provider_env_name("claude", "PANE_CHECK_INTERVAL") == "CCB_CLAUDE_PANE_CHECK_INTERVAL"
    assert provider_env_name("codebuddy", "REBIND_TAIL_BYTES") == "CCB_CODEBUDDY_REBIND_TAIL_BYTES"
    assert provider_marker_prefix("opencode") == "opencode"
    assert AGY_RUNTIME_SPEC.provider_key == "agy"
    assert AGY_RUNTIME_SPEC.service_name == "agy"
    assert AGY_RUNTIME_SPEC.state_file_name == "agy-runtime.json"
    assert AGY_RUNTIME_SPEC.idle_timeout_env == "CCB_AGY_RUNTIME_IDLE_TIMEOUT_S"
    assert AGY_CLIENT_SPEC.provider_key == "agy"
    assert AGY_CLIENT_SPEC.state_file_env == "CCB_AGY_STATE_FILE"
    assert AGY_CLIENT_SPEC.session_filename == ".agy-session"
    assert RUNTIME_SPECS_BY_PROVIDER["agy"] is AGY_RUNTIME_SPEC
    assert CLIENT_SPECS_BY_PROVIDER["agy"] is AGY_CLIENT_SPEC
    assert KIMI_RUNTIME_SPEC.provider_key == "kimi"
    assert KIMI_RUNTIME_SPEC.idle_timeout_env == "CCB_KIMI_RUNTIME_IDLE_TIMEOUT_S"
    assert KIMI_CLIENT_SPEC.session_filename == ".kimi-session"
    assert DEEPSEEK_RUNTIME_SPEC.provider_key == "deepseek"
    assert DEEPSEEK_RUNTIME_SPEC.idle_timeout_env == "CCB_DEEPSEEK_RUNTIME_IDLE_TIMEOUT_S"
    assert DEEPSEEK_CLIENT_SPEC.session_filename == ".deepseek-session"
    assert DSH_RUNTIME_SPEC.provider_key == "dsh"
    assert DSH_RUNTIME_SPEC.idle_timeout_env == "CCB_DSH_RUNTIME_IDLE_TIMEOUT_S"
    assert DSH_CLIENT_SPEC.session_filename == ".dsh-session"
    assert MIMO_RUNTIME_SPEC.provider_key == "mimo"
    assert MIMO_RUNTIME_SPEC.idle_timeout_env == "CCB_MIMO_RUNTIME_IDLE_TIMEOUT_S"
    assert MIMO_CLIENT_SPEC.session_filename == ".mimo-session"
    assert QWEN_RUNTIME_SPEC.provider_key == "qwen"
    assert QWEN_RUNTIME_SPEC.idle_timeout_env == "CCB_QWEN_RUNTIME_IDLE_TIMEOUT_S"
    assert QWEN_CLIENT_SPEC.session_filename == ".qwen-session"
    assert QODER_RUNTIME_SPEC.provider_key == "qoder"
    assert QODER_RUNTIME_SPEC.idle_timeout_env == "CCB_QODER_RUNTIME_IDLE_TIMEOUT_S"
    assert QODER_CLIENT_SPEC.session_filename == ".qoder-session"
    assert QODERCLICN_RUNTIME_SPEC.provider_key == "qoderclicn"
    assert QODERCLICN_RUNTIME_SPEC.idle_timeout_env == "CCB_QODERCLICN_RUNTIME_IDLE_TIMEOUT_S"
    assert QODERCLICN_CLIENT_SPEC.session_filename == ".qoderclicn-session"
    assert CURSOR_RUNTIME_SPEC.provider_key == "cursor"
    assert CURSOR_RUNTIME_SPEC.idle_timeout_env == "CCB_CURSOR_RUNTIME_IDLE_TIMEOUT_S"
    assert CURSOR_CLIENT_SPEC.session_filename == ".cursor-session"
    assert COPILOT_RUNTIME_SPEC.provider_key == "copilot"
    assert COPILOT_RUNTIME_SPEC.idle_timeout_env == "CCB_COPILOT_RUNTIME_IDLE_TIMEOUT_S"
    assert COPILOT_CLIENT_SPEC.session_filename == ".copilot-session"
    assert CRUSH_RUNTIME_SPEC.provider_key == "crush"
    assert CRUSH_RUNTIME_SPEC.idle_timeout_env == "CCB_CRUSH_RUNTIME_IDLE_TIMEOUT_S"
    assert CRUSH_CLIENT_SPEC.session_filename == ".crush-session"
    assert GROK_RUNTIME_SPEC.provider_key == "grok"
    assert GROK_RUNTIME_SPEC.idle_timeout_env == "CCB_GROK_RUNTIME_IDLE_TIMEOUT_S"
    assert GROK_CLIENT_SPEC.session_filename == ".grok-session"
    assert KIRO_RUNTIME_SPEC.provider_key == "kiro"
    assert KIRO_RUNTIME_SPEC.idle_timeout_env == "CCB_KIRO_RUNTIME_IDLE_TIMEOUT_S"
    assert KIRO_CLIENT_SPEC.session_filename == ".kiro-session"
    assert PI_RUNTIME_SPEC.provider_key == "pi"
    assert PI_RUNTIME_SPEC.idle_timeout_env == "CCB_PI_RUNTIME_IDLE_TIMEOUT_S"
    assert PI_CLIENT_SPEC.session_filename == ".pi-session"
    assert OMP_RUNTIME_SPEC.provider_key == "omp"
    assert OMP_RUNTIME_SPEC.idle_timeout_env == "CCB_OMP_RUNTIME_IDLE_TIMEOUT_S"
    assert OMP_CLIENT_SPEC.session_filename == ".omp-session"
    assert RUNTIME_SPECS_BY_PROVIDER["kimi"] is KIMI_RUNTIME_SPEC
    assert CLIENT_SPECS_BY_PROVIDER["deepseek"] is DEEPSEEK_CLIENT_SPEC
    assert RUNTIME_SPECS_BY_PROVIDER["dsh"] is DSH_RUNTIME_SPEC
    assert CLIENT_SPECS_BY_PROVIDER["dsh"] is DSH_CLIENT_SPEC
    assert RUNTIME_SPECS_BY_PROVIDER["mimo"] is MIMO_RUNTIME_SPEC
    assert CLIENT_SPECS_BY_PROVIDER["mimo"] is MIMO_CLIENT_SPEC
    assert RUNTIME_SPECS_BY_PROVIDER["qwen"] is QWEN_RUNTIME_SPEC
    assert RUNTIME_SPECS_BY_PROVIDER["qoder"] is QODER_RUNTIME_SPEC
    assert CLIENT_SPECS_BY_PROVIDER["qoder"] is QODER_CLIENT_SPEC
    assert RUNTIME_SPECS_BY_PROVIDER["qoderclicn"] is QODERCLICN_RUNTIME_SPEC
    assert CLIENT_SPECS_BY_PROVIDER["qoderclicn"] is QODERCLICN_CLIENT_SPEC
    assert CLIENT_SPECS_BY_PROVIDER["cursor"] is CURSOR_CLIENT_SPEC
    assert RUNTIME_SPECS_BY_PROVIDER["copilot"] is COPILOT_RUNTIME_SPEC
    assert CLIENT_SPECS_BY_PROVIDER["crush"] is CRUSH_CLIENT_SPEC
    assert CLIENT_SPECS_BY_PROVIDER["grok"] is GROK_CLIENT_SPEC
    assert RUNTIME_SPECS_BY_PROVIDER["kiro"] is KIRO_RUNTIME_SPEC
    assert CLIENT_SPECS_BY_PROVIDER["pi"] is PI_CLIENT_SPEC
    assert CLIENT_SPECS_BY_PROVIDER["omp"] is OMP_CLIENT_SPEC
