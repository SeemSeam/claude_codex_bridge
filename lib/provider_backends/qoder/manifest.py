from __future__ import annotations

from agents.models import RuntimeMode
from completion.models import CompletionFamily, CompletionSourceKind, SelectorFamily
from completion.profiles import CompletionManifest
from provider_core.manifests import ProviderManifest


def build_manifest() -> ProviderManifest:
    return build_qoder_pane_manifest(provider="qoder")


def build_qoder_pane_manifest(*, provider: str) -> ProviderManifest:
    provider = str(provider or "").strip().lower()
    return ProviderManifest(
        provider=provider,
        supports_resume=False,
        supports_permission_auto=True,
        supports_stream_watch=False,
        supports_subagents=False,
        supports_workspace_attach=True,
        runtime_profiles={
            RuntimeMode.PANE_BACKED: CompletionManifest(
                provider=provider,
                runtime_mode=RuntimeMode.PANE_BACKED.value,
                completion_family=CompletionFamily.TERMINAL_TEXT_QUIET,
                completion_source_kind=CompletionSourceKind.TERMINAL_TEXT,
                supports_exact_completion=False,
                supports_observed_completion=True,
                supports_anchor_binding=True,
                supports_reply_stability=False,
                supports_terminal_reason=True,
                selector_family=SelectorFamily.FINAL_MESSAGE,
            ),
        },
    )


__all__ = ["build_manifest", "build_qoder_pane_manifest"]
