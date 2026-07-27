from __future__ import annotations

import json
from pathlib import Path

from provider_backends.qoder.launcher import build_qoder_runtime_launcher
from provider_core.contracts import ProviderRuntimeLauncher


def build_runtime_launcher() -> ProviderRuntimeLauncher:
    return build_qoder_runtime_launcher(
        provider="qodercn",
        managed_config_preparer=_disable_managed_updates,
    )


def _disable_managed_updates(config_dir: Path) -> None:
    config_dir.mkdir(parents=True, exist_ok=True)
    settings_path = config_dir / "settings.json"
    settings: dict[str, object] = {}
    if settings_path.is_file():
        try:
            loaded = json.loads(settings_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise RuntimeError(f"cannot merge Qoder CN settings: {settings_path}") from exc
        if not isinstance(loaded, dict):
            raise RuntimeError(f"Qoder CN settings must contain an object: {settings_path}")
        settings = loaded
    general = settings.get("general")
    if not isinstance(general, dict):
        general = {}
        settings["general"] = general
    general["enableAutoUpdate"] = False
    general["enableAutoUpdateNotification"] = False
    settings_path.write_text(
        json.dumps(settings, ensure_ascii=True, indent=2) + "\n",
        encoding="utf-8",
    )


__all__ = ["build_runtime_launcher"]
