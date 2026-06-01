from __future__ import annotations

import pytest

from agents.models import RuntimeMode
from completion.models import CompletionFamily
from provider_core.catalog import (
    CORE_PROVIDER_NAMES,
    OPTIONAL_PROVIDER_NAMES,
    ProviderCatalog,
    build_default_provider_catalog,
)
from provider_core.manifests import ProviderManifest
from completion.profiles import CompletionManifest
from completion.models import CompletionSourceKind, SelectorFamily


def test_default_provider_catalog_contains_expected_profiles() -> None:
    catalog = build_default_provider_catalog()
    assert set(catalog.providers()) >= {
        'fake',
        'fake-codex',
        'fake-claude',
        'fake-gemini',
        'fake-legacy',
        'claude',
        'codex',
        'gemini',
        'opencode',
        'droid',
        'agy',
    }
    codex = catalog.resolve_completion_manifest('codex', RuntimeMode.PANE_BACKED)
    assert codex.completion_family is CompletionFamily.PROTOCOL_TURN
    gemini = catalog.resolve_completion_manifest('gemini', RuntimeMode.PANE_BACKED)
    assert gemini.completion_family is CompletionFamily.ANCHORED_SESSION_STABILITY
    fake = catalog.resolve_completion_manifest('fake', RuntimeMode.PANE_BACKED)
    assert fake.completion_family is CompletionFamily.STRUCTURED_RESULT
    fake_codex = catalog.resolve_completion_manifest('fake-codex', RuntimeMode.PANE_BACKED)
    assert fake_codex.completion_family is CompletionFamily.PROTOCOL_TURN
    fake_gemini = catalog.resolve_completion_manifest('fake-gemini', RuntimeMode.PANE_BACKED)
    assert fake_gemini.completion_family is CompletionFamily.ANCHORED_SESSION_STABILITY
    assert catalog.get('agy').supports_resume is True
    agy = catalog.resolve_completion_manifest('agy', RuntimeMode.PANE_BACKED)
    assert agy.completion_family is CompletionFamily.TERMINAL_TEXT_QUIET
    fake_legacy = catalog.resolve_completion_manifest('fake-legacy', RuntimeMode.PANE_BACKED)
    assert fake_legacy.completion_family is CompletionFamily.TERMINAL_TEXT_QUIET


def test_provider_catalog_rejects_duplicate_provider() -> None:
    manifest = ProviderManifest(
        provider='codex',
        supports_resume=True,
        supports_permission_auto=True,
        supports_stream_watch=True,
        supports_subagents=False,
        supports_workspace_attach=True,
        runtime_profiles={
            RuntimeMode.PANE_BACKED: CompletionManifest(
                provider='codex',
                runtime_mode='pane-backed',
                completion_family=CompletionFamily.PROTOCOL_TURN,
                completion_source_kind=CompletionSourceKind.PROTOCOL_EVENT_STREAM,
                supports_exact_completion=True,
                supports_observed_completion=False,
                supports_anchor_binding=True,
                supports_reply_stability=False,
                supports_terminal_reason=True,
                selector_family=SelectorFamily.FINAL_MESSAGE,
            )
        },
    )
    catalog = ProviderCatalog([manifest])
    with pytest.raises(ValueError):
        catalog.register(manifest)


def test_provider_catalog_rejects_unsupported_runtime_mode() -> None:
    catalog = build_default_provider_catalog()
    with pytest.raises(ValueError):
        catalog.resolve_completion_manifest('codex', RuntimeMode.HEADLESS)


def test_provider_catalog_can_build_core_only_catalog() -> None:
    catalog = build_default_provider_catalog(include_optional=False, include_test_doubles=False)
    assert set(catalog.providers()) == set(CORE_PROVIDER_NAMES)
    assert set(CORE_PROVIDER_NAMES) == {'codex', 'claude', 'gemini'}
    assert set(OPTIONAL_PROVIDER_NAMES) == {'opencode', 'droid', 'agy'}
