from __future__ import annotations

import pytest
from agents.models import RuntimeMode
from completion.models import CompletionFamily, CompletionSourceKind, SelectorFamily
from completion.profiles import CompletionManifest
from provider_core.catalog import (
    CORE_PROVIDER_NAMES,
    OPTIONAL_PROVIDER_NAMES,
    ProviderCatalog,
    build_default_provider_catalog,
)
from provider_core.manifests import ProviderManifest


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
        'kimi',
        'deepseek',
        'dsh',
        'mimo',
        'qwen',
        'qoder',
        'qoderclicn',
        'cursor',
        'copilot',
        'crush',
        'kiro',
        'pi',
        'omp',
        'zai',
        'grok',
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
    assert agy.completion_family is CompletionFamily.SESSION_BOUNDARY
    assert agy.completion_source_kind is CompletionSourceKind.SESSION_EVENT_LOG
    assert agy.supports_observed_completion is True
    assert agy.supports_anchor_binding is True
    kimi = catalog.resolve_completion_manifest('kimi', RuntimeMode.PANE_BACKED)
    assert catalog.get('kimi').supports_resume is False
    assert kimi.completion_family is CompletionFamily.SESSION_BOUNDARY
    assert kimi.completion_source_kind is CompletionSourceKind.SESSION_EVENT_LOG
    assert kimi.supports_observed_completion is True
    assert kimi.supports_anchor_binding is True
    deepseek = catalog.resolve_completion_manifest('deepseek', RuntimeMode.PANE_BACKED)
    assert deepseek.completion_family is CompletionFamily.SESSION_BOUNDARY
    assert deepseek.completion_source_kind is CompletionSourceKind.SESSION_SNAPSHOT
    assert deepseek.supports_observed_completion is True
    assert deepseek.supports_anchor_binding is True
    dsh = catalog.resolve_completion_manifest('dsh', RuntimeMode.PANE_BACKED)
    assert catalog.get('dsh').supports_resume is True
    assert catalog.get('dsh').supports_stream_watch is True
    assert dsh.completion_family is CompletionFamily.STRUCTURED_RESULT
    assert dsh.completion_source_kind is CompletionSourceKind.STRUCTURED_RESULT_STREAM
    assert dsh.supports_exact_completion is True
    assert dsh.supports_observed_completion is False
    assert dsh.supports_anchor_binding is True
    assert dsh.supports_terminal_reason is True
    assert dsh.supports_reply_stability is False
    mimo = catalog.resolve_completion_manifest('mimo', RuntimeMode.PANE_BACKED)
    assert mimo.completion_family is CompletionFamily.STRUCTURED_RESULT
    assert mimo.completion_source_kind is CompletionSourceKind.STRUCTURED_RESULT_STREAM
    assert mimo.supports_observed_completion is True
    assert mimo.supports_anchor_binding is True
    pi = catalog.resolve_completion_manifest('pi', RuntimeMode.PANE_BACKED)
    assert catalog.get('pi').supports_resume is True
    assert pi.completion_family is CompletionFamily.SESSION_BOUNDARY
    assert pi.completion_source_kind is CompletionSourceKind.SESSION_EVENT_LOG
    assert pi.supports_exact_completion is True
    assert pi.supports_anchor_binding is True
    for provider in ('qwen', 'qoder', 'qoderclicn', 'cursor', 'copilot', 'crush', 'kiro', 'omp', 'zai', 'grok'):
        native = catalog.resolve_completion_manifest(provider, RuntimeMode.PANE_BACKED)
        assert native.completion_family is CompletionFamily.STRUCTURED_RESULT
        assert native.completion_source_kind is CompletionSourceKind.STRUCTURED_RESULT_STREAM
        assert native.supports_observed_completion is True
        assert native.supports_anchor_binding is True
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
    assert set(OPTIONAL_PROVIDER_NAMES) == {
        'opencode',
        'droid',
        'agy',
        'kimi',
        'deepseek',
        'dsh',
        'mimo',
        'qwen',
        'qoder',
        'qoderclicn',
        'cursor',
        'copilot',
        'crush',
        'kiro',
        'pi',
        'omp',
        'zai',
        'grok',
    }
