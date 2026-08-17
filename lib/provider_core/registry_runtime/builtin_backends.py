from __future__ import annotations

from provider_core.contracts import ProviderBackend

CORE_PROVIDER_NAMES = ("codex", "claude", "gemini")
OPTIONAL_PROVIDER_NAMES = (
    "opencode",
    "droid",
    "agy",
    "kimi",
    "deepseek",
    "dsh",
    "mimo",
    "qwen",
    "qoder",
    "qoderclicn",
    "cursor",
    "copilot",
    "crush",
    "grok",
    "kiro",
    "pi",
    "omp",
    "zai",
)


def build_builtin_backends(*, include_optional: bool = True) -> list[ProviderBackend]:
    from provider_backends.agy import build_backend as build_agy_backend
    from provider_backends.claude import build_backend as build_claude_backend
    from provider_backends.codex import build_backend as build_codex_backend
    from provider_backends.copilot import build_backend as build_copilot_backend
    from provider_backends.crush import build_backend as build_crush_backend
    from provider_backends.cursor import build_backend as build_cursor_backend
    from provider_backends.deepseek import build_backend as build_deepseek_backend
    from provider_backends.dsh import build_backend as build_dsh_backend
    from provider_backends.droid import build_backend as build_droid_backend
    from provider_backends.gemini import build_backend as build_gemini_backend
    from provider_backends.grok import build_backend as build_grok_backend
    from provider_backends.kiro import build_backend as build_kiro_backend
    from provider_backends.kimi import build_backend as build_kimi_backend
    from provider_backends.mimo import build_backend as build_mimo_backend
    from provider_backends.opencode import build_backend as build_opencode_backend
    from provider_backends.pi import build_backend as build_pi_backend
    from provider_backends.omp import build_backend as build_omp_backend
    from provider_backends.qoder import build_backend as build_qoder_backend
    from provider_backends.qoderclicn import build_backend as build_qoderclicn_backend
    from provider_backends.qwen import build_backend as build_qwen_backend
    from provider_backends.zai import build_backend as build_zai_backend

    backends = [
        build_codex_backend(),
        build_claude_backend(),
        build_gemini_backend(),
    ]
    if include_optional:
        backends.extend([
            build_opencode_backend(),
            build_droid_backend(),
            build_agy_backend(),
            build_kimi_backend(),
            build_deepseek_backend(),
            build_dsh_backend(),
            build_mimo_backend(),
            build_qwen_backend(),
            build_qoder_backend(),
            build_qoderclicn_backend(),
            build_cursor_backend(),
            build_copilot_backend(),
            build_crush_backend(),
            build_grok_backend(),
            build_kiro_backend(),
            build_pi_backend(),
            build_omp_backend(),
            build_zai_backend(),
        ])
    return backends


__all__ = ["CORE_PROVIDER_NAMES", "OPTIONAL_PROVIDER_NAMES", "build_builtin_backends"]
