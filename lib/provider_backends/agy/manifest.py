from __future__ import annotations

from agents.models import RuntimeMode
from completion.models import CompletionFamily, CompletionSourceKind, SelectorFamily
from completion.profiles import CompletionManifest
from provider_core.manifests import ProviderManifest


def build_manifest() -> ProviderManifest:
    return ProviderManifest(
        provider='agy',
        supports_resume=True,
        supports_permission_auto=True,
        supports_stream_watch=False,
        supports_subagents=True,
        supports_workspace_attach=True,
        runtime_profiles={
            RuntimeMode.PANE_BACKED: CompletionManifest(
                provider='agy',
                runtime_mode=RuntimeMode.PANE_BACKED.value,
                completion_family=CompletionFamily.TERMINAL_TEXT_QUIET,
                completion_source_kind=CompletionSourceKind.TERMINAL_TEXT,
                supports_exact_completion=False,
                supports_observed_completion=False,
                supports_anchor_binding=False,
                supports_reply_stability=False,
                supports_terminal_reason=False,
                selector_family=SelectorFamily.FINAL_MESSAGE,
            ),
        },
    )


__all__ = ['build_manifest']
