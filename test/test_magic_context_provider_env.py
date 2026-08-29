from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from provider_backends.native_cli_support.launcher import (
    NativeCliLaunchConfig,
    build_start_cmd,
)
from provider_backends.native_cli_support.execution import _native_cli_env
from provider_core.caller_env import magic_context_storage_env
from provider_backends.omp.execution import _build_env as build_omp_headless_env
from provider_backends.pi.execution import _build_env as build_pi_headless_env
from runtime_env.control_plane import control_plane_env


def _prepared(provider: str, root: Path) -> dict[str, object]:
    state = root / f"{provider}-state"
    return {
        f"{provider}_state_dir": str(state),
        f"{provider}_home": str(state / "home"),
        f"{provider}_data_dir": str(state / "data"),
    }


def _spec(provider: str) -> SimpleNamespace:
    return SimpleNamespace(
        name=f"{provider}-agent",
        provider=provider,
        startup_args=(),
        env={},
        provider_command_template=None,
    )


def test_magic_context_storage_env_targets_only_supported_harnesses(monkeypatch) -> None:
    monkeypatch.setenv("CCB_SOURCE_HOME", "/tmp/ccb-source-home")

    expected = "/tmp/ccb-source-home/.local/share/cortexkit/magic-context"
    for provider in ("pi", "omp", "opencode"):
        assert magic_context_storage_env(provider) == {
            "MAGIC_CONTEXT_STORAGE_DIR": expected
        }
    assert magic_context_storage_env("codex") == {}
    assert magic_context_storage_env("claude") == {}


def test_magic_context_storage_env_forwards_an_explicit_absolute_override(monkeypatch) -> None:
    monkeypatch.setenv("CCB_SOURCE_HOME", "/tmp/ccb-source-home")
    monkeypatch.setenv("MAGIC_CONTEXT_STORAGE_DIR", "/srv/shared/magic-context")

    assert magic_context_storage_env("pi") == {
        "MAGIC_CONTEXT_STORAGE_DIR": "/srv/shared/magic-context"
    }


def test_magic_context_storage_env_rejects_relative_override(monkeypatch) -> None:
    monkeypatch.setenv("MAGIC_CONTEXT_STORAGE_DIR", "./magic-context")

    try:
        magic_context_storage_env("pi")
    except ValueError as exc:
        assert str(exc) == "MAGIC_CONTEXT_STORAGE_DIR must be an absolute path"
    else:
        raise AssertionError("relative Magic Context storage override was accepted")


def test_native_pane_launcher_injects_magic_context_storage_for_pi_and_omp(
    monkeypatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setenv("CCB_SOURCE_HOME", str(tmp_path / "source-home"))
    expected = str(
        tmp_path / "source-home" / ".local" / "share" / "cortexkit" / "magic-context"
    )
    command = SimpleNamespace()
    for provider in ("pi", "omp", "opencode"):
        provider_config = NativeCliLaunchConfig(provider=provider)
        prepared = _prepared(provider, tmp_path)
        cmd = build_start_cmd(
            provider_config,
            command,
            _spec(provider),
            tmp_path / "runtime",
            "launch-session",
            prepared_state=prepared,
        )
        assert f"MAGIC_CONTEXT_STORAGE_DIR={expected}" in cmd


def test_headless_pi_and_omp_env_injects_magic_context_storage(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("CCB_SOURCE_HOME", str(tmp_path / "source-home"))
    expected = str(
        tmp_path / "source-home" / ".local" / "share" / "cortexkit" / "magic-context"
    )
    for provider, builder in (("pi", build_pi_headless_env), ("omp", build_omp_headless_env)):
        state = tmp_path / provider / "state"
        request = SimpleNamespace(
            provider=provider,
            session_data={
                f"{provider}_home": str(state / "home"),
                f"{provider}_session_dir": str(state / "sessions"),
                f"{provider}_state_dir": str(state),
            }
        )
        config = SimpleNamespace(
            provider=provider,
            env_builder=builder,
            private_path_env_names=(),
            private_raw_env_names=(),
        )
        env = _native_cli_env(config, request)
        assert env["MAGIC_CONTEXT_STORAGE_DIR"] == expected


def test_control_plane_preserves_explicit_magic_context_storage_override(monkeypatch) -> None:
    monkeypatch.setenv("MAGIC_CONTEXT_STORAGE_DIR", "/srv/shared/magic-context")

    assert control_plane_env()["MAGIC_CONTEXT_STORAGE_DIR"] == "/srv/shared/magic-context"
