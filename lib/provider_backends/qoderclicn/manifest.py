from __future__ import annotations

from provider_core.manifests import ProviderManifest

from provider_backends.qoder.manifest import build_qoder_pane_manifest


def build_manifest() -> ProviderManifest:
    return build_qoder_pane_manifest(provider="qoderclicn")


__all__ = ["build_manifest"]
