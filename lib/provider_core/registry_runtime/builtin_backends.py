from __future__ import annotations

from provider_core.contracts import ProviderBackend

CORE_PROVIDER_NAMES = ("codex", "claude", "gemini")
OPTIONAL_PROVIDER_NAMES = (
    "opencode",
    "droid",
    "agy",
    "kimi",
    "deepseek",
    "mimo",
    "qwen",
    "cursor",
    "copilot",
    "crush",
    "kiro",
    "pi",
    "zai",
)


def build_builtin_backends(
    *, include_optional: bool = True, providers: set[str] | None = None
) -> list[ProviderBackend]:
    """Build the builtin provider backends.

    Args:
        include_optional: Whether optional providers should be included when no
            explicit ``providers`` filter is given.  Defaults to ``True`` for
            backward compatibility.
        providers: If given, only backends whose provider name is in this set
            are imported and built.  This lets ccbd avoid loading provider
            backends that are not configured for the current project.
    """
    requested = set(providers) if providers else None

    def _include(name: str) -> bool:
        return requested is None or name in requested

    backends: list[ProviderBackend] = []
    if _include("codex"):
        from provider_backends.codex import build_backend as build_codex_backend

        backends.append(build_codex_backend())
    if _include("claude"):
        from provider_backends.claude import build_backend as build_claude_backend

        backends.append(build_claude_backend())
    if _include("gemini"):
        from provider_backends.gemini import build_backend as build_gemini_backend

        backends.append(build_gemini_backend())

    if not include_optional and requested is None:
        return backends

    if _include("opencode"):
        from provider_backends.opencode import build_backend as build_opencode_backend

        backends.append(build_opencode_backend())
    if _include("droid"):
        from provider_backends.droid import build_backend as build_droid_backend

        backends.append(build_droid_backend())
    if _include("agy"):
        from provider_backends.agy import build_backend as build_agy_backend

        backends.append(build_agy_backend())
    if _include("kimi"):
        from provider_backends.kimi import build_backend as build_kimi_backend

        backends.append(build_kimi_backend())
    if _include("deepseek"):
        from provider_backends.deepseek import build_backend as build_deepseek_backend

        backends.append(build_deepseek_backend())
    if _include("mimo"):
        from provider_backends.mimo import build_backend as build_mimo_backend

        backends.append(build_mimo_backend())
    if _include("qwen"):
        from provider_backends.qwen import build_backend as build_qwen_backend

        backends.append(build_qwen_backend())
    if _include("cursor"):
        from provider_backends.cursor import build_backend as build_cursor_backend

        backends.append(build_cursor_backend())
    if _include("copilot"):
        from provider_backends.copilot import build_backend as build_copilot_backend

        backends.append(build_copilot_backend())
    if _include("crush"):
        from provider_backends.crush import build_backend as build_crush_backend

        backends.append(build_crush_backend())
    if _include("kiro"):
        from provider_backends.kiro import build_backend as build_kiro_backend

        backends.append(build_kiro_backend())
    if _include("pi"):
        from provider_backends.pi import build_backend as build_pi_backend

        backends.append(build_pi_backend())
    if _include("zai"):
        from provider_backends.zai import build_backend as build_zai_backend

        backends.append(build_zai_backend())
    return backends


__all__ = ["CORE_PROVIDER_NAMES", "OPTIONAL_PROVIDER_NAMES", "build_builtin_backends"]
