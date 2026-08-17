from __future__ import annotations

from io import StringIO
import os
from pathlib import Path
import subprocess
import time

from cli.management_runtime import provider_updates


class _TtyInput(StringIO):
    def isatty(self) -> bool:
        return True


class _TtyOutput(StringIO):
    def isatty(self) -> bool:
        return True


class _PipeInput(StringIO):
    def isatty(self) -> bool:
        return False


def _candidate(
    provider: str = "codex",
    *,
    current: str = "1.0.0",
    latest: str = "1.1.0",
    command: tuple[str, ...] | None = ("/tools/codex", "update"),
) -> provider_updates.ProviderUpdateCandidate:
    return provider_updates.ProviderUpdateCandidate(
        provider=provider,
        executable=Path(f"/tools/{provider}"),
        current_version=current,
        latest_version=latest,
        owner="native",
        update_command=command,
    )


def test_provider_update_state_path_uses_xdg_state_home(tmp_path: Path) -> None:
    path = provider_updates.provider_update_state_path(
        env={"XDG_STATE_HOME": str(tmp_path / "state")},
        home=tmp_path / "home",
    )

    assert path == tmp_path / "state" / "ccb" / "provider-updates.json"


def test_provider_update_state_path_rejects_relative_xdg_state_home(tmp_path: Path) -> None:
    path = provider_updates.provider_update_state_path(
        env={"XDG_STATE_HOME": "relative-state"},
        home=tmp_path / "home",
    )

    assert path == tmp_path / "home" / ".local" / "state" / "ccb" / "provider-updates.json"


def test_malformed_provider_update_state_schema_fails_closed(tmp_path: Path) -> None:
    state_path = tmp_path / "provider-updates.json"
    state_path.write_text(
        '{"schema_version": "not-a-number", "providers": {"codex": {"muted_version": "1.1.0"}}}\n',
        encoding="utf-8",
    )

    assert provider_updates.load_provider_update_state(state_path) == {
        "schema_version": 1,
        "updated_at": None,
        "providers": {},
    }


def test_npm_package_detection_supports_scoped_and_unscoped_packages() -> None:
    assert provider_updates._npm_package_from_path(
        Path("/nvm/lib/node_modules/@openai/codex/bin/codex.js")
    ) == "@openai/codex"
    assert provider_updates._npm_package_from_path(
        Path("/nvm/lib/node_modules/opencode-ai/bin/opencode")
    ) == "opencode-ai"


def test_dsh_update_catalog_uses_official_npm_package() -> None:
    assert provider_updates._NATIVE_REGISTRY_PACKAGES['dsh'] == '@deepseek-ai/dsh'
    assert provider_updates._PROVIDER_LABELS['dsh'] == 'DeepSeek Harness'


def test_npm_install_prefix_detection_tracks_the_resolved_global_package_root() -> None:
    assert provider_updates._npm_install_prefix_from_path(
        Path("/home/demo/.local/lib/node_modules/@vegamo/deepcode-cli/dist/cli.js")
    ) == Path("/home/demo/.local")
    assert provider_updates._npm_install_prefix_from_path(
        Path("/nvm/versions/node/v22/lib/node_modules/opencode-ai/bin/opencode")
    ) == Path("/nvm/versions/node/v22")


def test_bun_install_home_detection_requires_the_bun_global_layout() -> None:
    assert provider_updates._bun_install_home_from_path(
        Path(
            "/home/demo/.bun/install/global/node_modules/"
            "@oh-my-pi/pi-coding-agent/dist/cli.js"
        )
    ) == Path("/home/demo/.bun")
    assert provider_updates._bun_install_home_from_path(
        Path("/home/demo/.local/lib/node_modules/@openai/codex/bin/codex.js")
    ) is None


def test_provider_version_comparison_handles_semver_prereleases() -> None:
    assert provider_updates._provider_version_is_newer("1.2.0", "1.2.0-beta.2") is True
    assert provider_updates._provider_version_is_newer("1.2.0-beta.10", "1.2.0-beta.2") is True
    assert provider_updates._provider_version_is_newer("1.2.0-beta.2", "1.2.0-beta.10") is False
    assert provider_updates._provider_version_is_newer("1.2.0+build.2", "1.2.0+build.1") is False


def test_discovery_uses_matching_npm_install_and_pins_latest_version(tmp_path: Path) -> None:
    nvm_root = tmp_path / "nvm"
    bin_dir = nvm_root / "bin"
    package_entry = nvm_root / "lib" / "node_modules" / "@openai" / "codex" / "bin" / "codex.js"
    package_entry.parent.mkdir(parents=True)
    package_entry.write_text("#!/usr/bin/env node\n", encoding="utf-8")
    bin_dir.mkdir(parents=True)
    codex_entry = bin_dir / "codex"
    codex_entry.symlink_to(package_entry)
    npm_entry = bin_dir / "npm"
    npm_entry.write_text("#!/bin/sh\n", encoding="utf-8")

    def _which(name: str) -> str | None:
        return str(codex_entry) if name == "codex" else None

    def _run(command, **_kwargs):
        assert command == [str(codex_entry), "--version"]
        return subprocess.CompletedProcess(command, 0, stdout="codex-cli 1.0.0\n", stderr="")

    candidates = provider_updates.discover_provider_updates(
        providers=("codex",),
        which_fn=_which,
        run_fn=_run,
        fetch_latest_fn=lambda package: "1.2.0" if package == "@openai/codex" else None,
    )

    assert len(candidates) == 1
    candidate = candidates[0]
    assert candidate.owner == "npm"
    assert candidate.package == "@openai/codex"
    assert candidate.current_version == "1.0.0"
    assert candidate.latest_version == "1.2.0"
    assert candidate.update_command == (
        str(npm_entry),
        "install",
        "--global",
        "--prefix",
        str(nvm_root),
        "@openai/codex@1.2.0",
        "--no-audit",
        "--no-fund",
    )


def test_discovery_pins_user_prefix_when_only_system_npm_is_available(
    tmp_path: Path,
) -> None:
    user_prefix = tmp_path / "home" / ".local"
    package_entry = (
        user_prefix
        / "lib"
        / "node_modules"
        / "@vegamo"
        / "deepcode-cli"
        / "dist"
        / "cli.js"
    )
    package_entry.parent.mkdir(parents=True)
    package_entry.write_text("#!/usr/bin/env node\n", encoding="utf-8")
    bin_dir = user_prefix / "bin"
    bin_dir.mkdir(parents=True)
    executable = bin_dir / "deepcode"
    executable.symlink_to(package_entry)
    system_npm = tmp_path / "usr" / "bin" / "npm"
    system_npm.parent.mkdir(parents=True)
    system_npm.write_text("#!/bin/sh\n", encoding="utf-8")

    def _which(name: str) -> str | None:
        if name == "deepcode":
            return str(executable)
        if name == "npm":
            return str(system_npm)
        return None

    def _run(command, **_kwargs):
        assert command == [str(executable), "--version"]
        return subprocess.CompletedProcess(command, 0, stdout="0.1.29\n", stderr="")

    candidates = provider_updates.discover_provider_updates(
        providers=("deepseek",),
        which_fn=_which,
        run_fn=_run,
        fetch_latest_fn=lambda package: (
            "0.1.34" if package == "@vegamo/deepcode-cli" else None
        ),
    )

    assert len(candidates) == 1
    assert candidates[0].update_command == (
        str(system_npm),
        "install",
        "--global",
        "--prefix",
        str(user_prefix),
        "@vegamo/deepcode-cli@0.1.34",
        "--no-audit",
        "--no-fund",
    )


def test_registry_release_with_local_dependency_is_report_only(
    tmp_path: Path,
) -> None:
    user_prefix = tmp_path / ".local"
    package_entry = (
        user_prefix
        / "lib"
        / "node_modules"
        / "@vegamo"
        / "deepcode-cli"
        / "dist"
        / "cli.js"
    )
    package_entry.parent.mkdir(parents=True)
    package_entry.write_text("#!/usr/bin/env node\n", encoding="utf-8")
    executable = user_prefix / "bin" / "deepcode"
    executable.parent.mkdir(parents=True)
    executable.symlink_to(package_entry)
    npm = user_prefix / "bin" / "npm"
    npm.write_text("#!/bin/sh\n", encoding="utf-8")

    candidates = provider_updates.discover_provider_updates(
        providers=("deepseek",),
        which_fn=lambda name: str(executable) if name == "deepcode" else None,
        run_fn=lambda command, **_kwargs: subprocess.CompletedProcess(
            command,
            0,
            stdout="0.1.29\n",
            stderr="",
        ),
        fetch_latest_fn=lambda _package: provider_updates._RegistryRelease(
            "0.1.34",
            "registry release 0.1.34 declares non-published local dependency "
            "`@vegamo/deepcode-core: file:../core`",
        ),
    )

    assert len(candidates) == 1
    assert candidates[0].update_available is True
    assert candidates[0].update_command is None
    assert "file:../core" in str(candidates[0].issue)


def test_registry_latest_rejects_non_published_local_dependencies(
    monkeypatch,
) -> None:
    monkeypatch.setattr(
        provider_updates,
        "fetch_json_via_urllib",
        lambda *_args, **_kwargs: {
            "version": "0.1.34",
            "dependencies": {
                "@vegamo/deepcode-core": "file:../core",
                "chalk": "^5.6.2",
            },
        },
    )

    release = provider_updates._fetch_registry_latest("@vegamo/deepcode-cli")

    assert release.version == "0.1.34"
    assert "@vegamo/deepcode-core: file:../core" in str(release.issue)


def test_discovery_uses_bun_for_a_bun_global_provider(tmp_path: Path) -> None:
    bun_home = tmp_path / "home" / ".bun"
    package_entry = (
        bun_home
        / "install"
        / "global"
        / "node_modules"
        / "@oh-my-pi"
        / "pi-coding-agent"
        / "dist"
        / "cli.js"
    )
    package_entry.parent.mkdir(parents=True)
    package_entry.write_text("#!/usr/bin/env bun\n", encoding="utf-8")
    bin_dir = bun_home / "bin"
    bin_dir.mkdir(parents=True)
    executable = bin_dir / "omp"
    executable.symlink_to(package_entry)
    bun = bin_dir / "bun"
    bun.write_text("#!/bin/sh\n", encoding="utf-8")

    def _which(name: str) -> str | None:
        return str(executable) if name == "omp" else None

    def _run(command, **_kwargs):
        assert command == [str(executable), "--version"]
        return subprocess.CompletedProcess(command, 0, stdout="17.1.3\n", stderr="")

    candidates = provider_updates.discover_provider_updates(
        providers=("omp",),
        which_fn=_which,
        run_fn=_run,
        fetch_latest_fn=lambda package: (
            "17.1.4" if package == "@oh-my-pi/pi-coding-agent" else None
        ),
    )

    assert len(candidates) == 1
    assert candidates[0].owner == "bun"
    assert candidates[0].package == "@oh-my-pi/pi-coding-agent"
    assert candidates[0].update_command == (
        str(bun),
        "add",
        "--global",
        "@oh-my-pi/pi-coding-agent@17.1.4",
    )


def test_npm_update_is_report_only_when_detected_prefix_is_not_writable(
    monkeypatch,
    tmp_path: Path,
) -> None:
    system_prefix = tmp_path / "system"
    (system_prefix / "bin").mkdir(parents=True)
    (system_prefix / "lib" / "node_modules").mkdir(parents=True)
    npm = tmp_path / "usr" / "bin" / "npm"
    npm.parent.mkdir(parents=True)
    npm.write_text("#!/bin/sh\n", encoding="utf-8")
    monkeypatch.setattr(provider_updates.os, "access", lambda _path, _mode: False)

    command, issue = provider_updates._build_update_command(
        "pi",
        executable=system_prefix / "bin" / "pi",
        owner="npm",
        package="@earendil-works/pi-coding-agent",
        latest_version="0.82.1",
        which_fn=lambda name: str(npm) if name == "npm" else None,
        run_fn=subprocess.run,
        npm_prefix=system_prefix,
    )

    assert command is None
    assert f"npm install prefix `{system_prefix}` is not writable" in str(issue)


def test_bun_update_is_report_only_when_detected_home_is_not_writable(
    monkeypatch,
    tmp_path: Path,
) -> None:
    bun_home = tmp_path / ".bun"
    bun = bun_home / "bin" / "bun"
    bun.parent.mkdir(parents=True)
    bun.write_text("#!/bin/sh\n", encoding="utf-8")
    monkeypatch.setattr(provider_updates.os, "access", lambda _path, _mode: False)

    command, issue = provider_updates._build_update_command(
        "omp",
        executable=bun_home / "bin" / "omp",
        owner="bun",
        package="@oh-my-pi/pi-coding-agent",
        latest_version="17.1.4",
        which_fn=lambda _name: None,
        run_fn=subprocess.run,
        bun_home=bun_home,
    )

    assert command is None
    assert f"Bun install home `{bun_home}` is not writable" in str(issue)


def test_current_npm_install_does_not_report_prefix_permission_as_update_failure(
    monkeypatch,
    tmp_path: Path,
) -> None:
    system_prefix = tmp_path / "system"
    npm = tmp_path / "usr" / "bin" / "npm"
    npm.parent.mkdir(parents=True)
    npm.write_text("#!/bin/sh\n", encoding="utf-8")
    monkeypatch.setattr(provider_updates.os, "access", lambda _path, _mode: False)

    command, issue = provider_updates._build_update_command(
        "codex",
        executable=system_prefix / "bin" / "codex",
        owner="npm",
        package="@openai/codex",
        latest_version="0.145.0",
        which_fn=lambda name: str(npm) if name == "npm" else None,
        run_fn=subprocess.run,
        npm_prefix=system_prefix,
        check_npm_prefix_writable=False,
    )

    assert command == (
        str(npm),
        "install",
        "--global",
        "--prefix",
        str(system_prefix),
        "@openai/codex@0.145.0",
        "--no-audit",
        "--no-fund",
    )
    assert issue is None


def test_custom_start_wrapper_is_never_treated_as_native_provider_updater(
    monkeypatch,
    tmp_path: Path,
) -> None:
    wrapper = tmp_path / "bin" / "env"
    wrapper.parent.mkdir(parents=True)
    wrapper.write_text("#!/bin/sh\n", encoding="utf-8")
    monkeypatch.setenv("CODEX_START_CMD", f"{wrapper} CODEX_MODE=managed codex")

    def _run(command, **_kwargs):
        return subprocess.CompletedProcess(command, 0, stdout="env 1.0.0\n", stderr="")

    candidates = provider_updates.discover_provider_updates(
        providers=("codex",),
        which_fn=lambda _name: None,
        run_fn=_run,
        fetch_latest_fn=lambda _package: "1.1.0",
    )

    assert len(candidates) == 1
    assert candidates[0].owner == "custom"
    assert candidates[0].update_command is None
    assert "not updated automatically" in str(candidates[0].issue)


def test_windows_interop_provider_is_report_only_from_wsl() -> None:
    executable = Path("/mnt/c/Users/demo/AppData/Roaming/npm/codex.cmd")

    owner, package, issue = provider_updates._detect_install_owner(
        "codex",
        executable=executable,
        resolved=executable,
        which_fn=lambda _name: None,
    )

    assert owner == "windows-interop"
    assert package is None
    assert "not updated from WSL" in str(issue)


def test_linux_npm_package_with_exe_named_payload_is_not_windows_interop(
    tmp_path: Path,
) -> None:
    bin_dir = tmp_path / "bin"
    package_binary = tmp_path / "lib" / "node_modules" / "opencode-ai" / "bin" / "opencode.exe"
    bin_dir.mkdir(parents=True)
    package_binary.parent.mkdir(parents=True)
    executable = bin_dir / "opencode"
    executable.symlink_to(package_binary)
    npm = bin_dir / "npm"
    npm.write_text("#!/bin/sh\n", encoding="utf-8")

    owner, package, issue = provider_updates._detect_install_owner(
        "opencode",
        executable=executable,
        resolved=package_binary,
        which_fn=lambda _name: None,
    )

    assert owner == "npm"
    assert package == "opencode-ai"
    assert issue is None


def test_snap_provider_is_report_only() -> None:
    owner, package, issue = provider_updates._detect_install_owner(
        "codex",
        executable=Path("/snap/bin/codex"),
        resolved=Path("/snap/codex/123/bin/codex"),
        which_fn=lambda _name: None,
    )

    assert owner == "snap"
    assert package is None
    assert "snapd" in str(issue)


def test_homebrew_cask_provider_uses_known_brew_upgrade_mapping() -> None:
    executable = Path("/opt/homebrew/bin/codex")
    brew = "/opt/homebrew/bin/brew"
    owner, package, issue = provider_updates._detect_install_owner(
        "codex",
        executable=executable,
        resolved=Path("/opt/homebrew/Caskroom/codex/1.0.0/codex"),
        which_fn=lambda name: brew if name == "brew" else None,
    )

    command, command_issue = provider_updates._build_update_command(
        "codex",
        executable=executable,
        owner=owner,
        package=package,
        latest_version="1.1.0",
        which_fn=lambda name: brew if name == "brew" else None,
        run_fn=subprocess.run,
    )

    assert owner == "brew"
    assert issue is None
    assert command == (brew, "upgrade", "--cask", "codex")
    assert command_issue is None


def test_native_grok_update_is_capability_checked_and_version_pinned(tmp_path: Path) -> None:
    executable = tmp_path / "bin" / "grok"
    executable.parent.mkdir(parents=True)
    executable.write_text("#!/bin/sh\n", encoding="utf-8")

    def _run(command, **_kwargs):
        if command == [str(executable), "--version"]:
            return subprocess.CompletedProcess(command, 0, stdout="grok 0.2.0\n", stderr="")
        assert command == [str(executable), "update", "--help"]
        return subprocess.CompletedProcess(
            command,
            0,
            stdout="Usage: grok update [OPTIONS]\n",
            stderr="",
        )

    candidates = provider_updates.discover_provider_updates(
        providers=("grok",),
        which_fn=lambda name: str(executable) if name == "grok" else None,
        run_fn=_run,
        fetch_latest_fn=lambda package: "0.3.0" if package == "@xai-official/grok" else None,
    )

    assert len(candidates) == 1
    assert candidates[0].owner == "native"
    assert candidates[0].update_command == (
        str(executable),
        "update",
        "--version",
        "0.3.0",
    )


def test_native_droid_uses_read_only_check_and_pinned_self_update(
    monkeypatch,
    tmp_path: Path,
) -> None:
    executable = tmp_path / "bin" / "droid"
    executable.parent.mkdir(parents=True)
    executable.write_text("#!/bin/sh\n", encoding="utf-8")
    for name in provider_updates._EXPLICIT_UPDATE_ENV_UNSET:
        monkeypatch.setenv(name, "inherited-disabled")

    def _run(command, **kwargs):
        env = kwargs["env"]
        if command == [str(executable), "--version"]:
            assert {
                name: env.get(name) for name in provider_updates._NO_AUTO_UPDATE_ENV
            } == provider_updates._NO_AUTO_UPDATE_ENV
            return subprocess.CompletedProcess(command, 0, stdout="0.175.1\n", stderr="")
        if command == [str(executable), "update", "--check"]:
            assert env["NO_UPDATE_NOTIFIER"] == "1"
            assert all(name not in env for name in provider_updates._EXPLICIT_UPDATE_ENV_UNSET)
            return subprocess.CompletedProcess(
                command,
                0,
                stdout=(
                    "Droid Update\n"
                    "Current version: 0.175.1\n"
                    "! Update available: 0.178.0\n"
                ),
                stderr="",
            )
        assert command == [str(executable), "update", "--help"]
        assert env["NO_UPDATE_NOTIFIER"] == "1"
        assert all(name not in env for name in provider_updates._EXPLICIT_UPDATE_ENV_UNSET)
        return subprocess.CompletedProcess(
            command,
            0,
            stdout="Usage: droid update [options]\n",
            stderr="",
        )

    candidates = provider_updates.discover_provider_updates(
        providers=("droid",),
        which_fn=lambda name: str(executable) if name == "droid" else None,
        run_fn=_run,
        fetch_latest_fn=lambda _package: (_ for _ in ()).throw(
            AssertionError("Droid uses its native read-only update check")
        ),
    )

    assert len(candidates) == 1
    assert candidates[0].latest_version == "0.178.0"
    assert candidates[0].update_command == (
        str(executable),
        "update",
        "--version",
        "0.178.0",
    )


def test_native_latest_check_retries_one_transient_failure(tmp_path: Path) -> None:
    executable = tmp_path / "droid"
    calls = 0

    def _run(command, **_kwargs):
        nonlocal calls
        calls += 1
        if calls == 1:
            return subprocess.CompletedProcess(command, 1, stdout="", stderr="network error")
        return subprocess.CompletedProcess(
            command,
            0,
            stdout="Current version: 0.175.1\nUpdate available: 0.178.0\n",
            stderr="",
        )

    latest = provider_updates._fetch_native_latest_version(
        "droid",
        executable=executable,
        run_fn=_run,
    )

    assert latest == "0.178.0"
    assert calls == 2


def test_native_updater_is_not_used_when_cli_does_not_expose_subcommand(tmp_path: Path) -> None:
    executable = tmp_path / "bin" / "codex"
    executable.parent.mkdir(parents=True)
    executable.write_text("#!/bin/sh\n", encoding="utf-8")

    def _run(command, **_kwargs):
        if command == [str(executable), "--version"]:
            return subprocess.CompletedProcess(command, 0, stdout="codex 1.0.0\n", stderr="")
        return subprocess.CompletedProcess(command, 2, stdout="Usage: codex [OPTIONS]\n", stderr="")

    candidates = provider_updates.discover_provider_updates(
        providers=("codex",),
        which_fn=lambda name: str(executable) if name == "codex" else None,
        run_fn=_run,
        fetch_latest_fn=lambda _package: "1.1.0",
    )

    assert len(candidates) == 1
    assert candidates[0].update_command is None
    assert "does not expose `codex update`" in str(candidates[0].issue)


def test_prompt_skip_mutes_only_the_detected_versions_in_chinese(
    monkeypatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setenv("CCB_LANG", "zh")
    state_path = tmp_path / "state" / "provider-updates.json"
    stdout = _TtyOutput()

    code = provider_updates.run_provider_update_flow(
        mode="prompt",
        stdin=_TtyInput("k\n"),
        stdout=stdout,
        state_path=state_path,
        discover_fn=lambda: (_candidate(),),
    )

    assert code == 0
    assert "出现更高版本后会重新提示" in stdout.getvalue()
    state = provider_updates.load_provider_update_state(state_path)
    assert state["providers"]["codex"]["muted_version"] == "1.1.0"
    assert state["providers"]["codex"]["last_decision"] == "muted"


def test_prompt_can_mute_report_only_snap_version(
    monkeypatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setenv("CCB_LANG", "en")
    state_path = tmp_path / "state" / "provider-updates.json"
    stdout = _TtyOutput()
    candidate = provider_updates.ProviderUpdateCandidate(
        provider="codex",
        executable=Path("/snap/bin/codex"),
        current_version="1.0.0",
        latest_version="1.1.0",
        owner="snap",
        update_command=None,
        issue="Snap installations are updated by snapd",
    )

    code = provider_updates.run_provider_update_flow(
        mode="prompt",
        stdin=_TtyInput("y\n"),
        stdout=stdout,
        state_path=state_path,
        discover_fn=lambda: (candidate,),
        execute_fn=lambda _candidate: (_ for _ in ()).throw(
            AssertionError("report-only providers must not execute an updater")
        ),
    )

    assert code == 0
    assert "update them manually" in stdout.getvalue()
    assert "until a newer version appears" in stdout.getvalue()
    state = provider_updates.load_provider_update_state(state_path)
    assert state["providers"]["codex"]["muted_version"] == "1.1.0"
    assert state["providers"]["codex"]["last_decision"] == "muted"


def test_muted_version_is_not_offered_again_in_default_prompt_mode(
    monkeypatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setenv("CCB_LANG", "en")
    state_path = tmp_path / "state" / "provider-updates.json"
    provider_updates.write_provider_update_state(
        {
            "schema_version": 1,
            "updated_at": "2026-01-01T00:00:00Z",
            "providers": {
                "codex": {
                    "muted_version": "1.1.0",
                    "latest_version": "1.1.0",
                }
            },
        },
        state_path,
    )
    stdout = _TtyOutput()

    code = provider_updates.run_provider_update_flow(
        mode="prompt",
        stdin=_TtyInput("a\n"),
        stdout=stdout,
        state_path=state_path,
        discover_fn=lambda: (_candidate(),),
        execute_fn=lambda _candidate: (_ for _ in ()).throw(
            AssertionError("muted provider must not be offered")
        ),
    )

    assert code == 0
    assert "Provider updates available" not in stdout.getvalue()
    assert "remain skipped" in stdout.getvalue()


def test_newer_provider_version_automatically_clears_previous_mute(
    monkeypatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setenv("CCB_LANG", "en")
    state_path = tmp_path / "state" / "provider-updates.json"
    provider_updates.write_provider_update_state(
        {
            "schema_version": 1,
            "updated_at": "2026-01-01T00:00:00Z",
            "providers": {
                "codex": {
                    "muted_version": "1.1.0",
                    "latest_version": "1.1.0",
                }
            },
        },
        state_path,
    )

    code = provider_updates.run_provider_update_flow(
        mode="check",
        stdout=StringIO(),
        state_path=state_path,
        discover_fn=lambda: (_candidate(latest="1.2.0"),),
    )

    assert code == 0
    state = provider_updates.load_provider_update_state(state_path)
    assert state["providers"]["codex"]["muted_version"] is None


def test_transient_latest_version_failure_preserves_previous_mute(
    monkeypatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setenv("CCB_LANG", "en")
    state_path = tmp_path / "state" / "provider-updates.json"
    provider_updates.write_provider_update_state(
        {
            "schema_version": 1,
            "updated_at": "2026-01-01T00:00:00Z",
            "providers": {
                "codex": {
                    "muted_version": "1.1.0",
                    "latest_version": "1.1.0",
                }
            },
        },
        state_path,
    )
    unavailable = _candidate(latest="")
    unavailable = provider_updates.ProviderUpdateCandidate(
        provider=unavailable.provider,
        executable=unavailable.executable,
        current_version=unavailable.current_version,
        latest_version=None,
        owner=unavailable.owner,
        update_command=None,
        issue="latest version could not be resolved",
    )

    code = provider_updates.run_provider_update_flow(
        mode="check",
        stdout=StringIO(),
        state_path=state_path,
        discover_fn=lambda: (unavailable,),
    )

    assert code == 0
    state = provider_updates.load_provider_update_state(state_path)
    assert state["providers"]["codex"]["muted_version"] == "1.1.0"


def test_decline_prompts_again_later_without_muting_version(
    monkeypatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setenv("CCB_LANG", "en")
    state_path = tmp_path / "state" / "provider-updates.json"
    stdout = _TtyOutput()

    code = provider_updates.run_provider_update_flow(
        mode="prompt",
        stdin=_TtyInput("\n"),
        stdout=stdout,
        state_path=state_path,
        discover_fn=lambda: (_candidate(),),
    )

    assert code == 0
    assert "next `ccb update`" in stdout.getvalue()
    state = provider_updates.load_provider_update_state(state_path)
    assert state["providers"]["codex"]["muted_version"] is None
    assert state["providers"]["codex"]["last_decision"] == "declined"


def test_explicit_all_updates_and_verifies_without_prompt(
    monkeypatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setenv("CCB_LANG", "en")
    state_path = tmp_path / "state" / "provider-updates.json"
    calls: list[str] = []

    def _execute(candidate):
        calls.append(candidate.provider)
        return provider_updates.ProviderUpdateExecution(
            provider=candidate.provider,
            success=True,
            before_version=candidate.current_version,
            after_version=candidate.latest_version,
            detail="verified",
        )

    stdout = StringIO()
    code = provider_updates.run_provider_update_flow(
        mode="all",
        stdout=stdout,
        state_path=state_path,
        discover_fn=lambda: (
            _candidate("codex"),
            _candidate("gemini", current="2.0.0", latest="2.1.0", command=("/tools/npm", "install")),
        ),
        execute_fn=_execute,
    )

    assert code == 0
    assert calls == ["codex", "gemini"]
    assert "Running provider panes were not restarted" in stdout.getvalue()
    state = provider_updates.load_provider_update_state(state_path)
    assert state["providers"]["codex"]["current_version"] == "1.1.0"
    assert state["providers"]["gemini"]["current_version"] == "2.1.0"


def test_interactive_selection_updates_only_chosen_provider(
    monkeypatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setenv("CCB_LANG", "en")
    calls: list[str] = []

    def _execute(candidate):
        calls.append(candidate.provider)
        return provider_updates.ProviderUpdateExecution(
            provider=candidate.provider,
            success=True,
            before_version=candidate.current_version,
            after_version=candidate.latest_version,
            detail="verified",
        )

    code = provider_updates.run_provider_update_flow(
        mode="prompt",
        stdin=_TtyInput("s\n2\n"),
        stdout=_TtyOutput(),
        state_path=tmp_path / "provider-updates.json",
        discover_fn=lambda: (
            _candidate("codex"),
            _candidate("gemini", current="2.0.0", latest="2.1.0"),
        ),
        execute_fn=_execute,
    )

    assert code == 0
    assert calls == ["gemini"]


def test_prompt_mode_is_silent_from_provider_network_work_in_non_tty(
    monkeypatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setenv("CCB_LANG", "en")
    calls: list[str] = []
    stdout = StringIO()

    code = provider_updates.run_provider_update_flow(
        mode="prompt",
        stdin=_PipeInput(),
        stdout=stdout,
        state_path=tmp_path / "provider-updates.json",
        discover_fn=lambda: calls.append("discover") or (),
    )

    assert code == 0
    assert calls == []
    assert "non-interactive mode" in stdout.getvalue()


def test_execute_provider_update_requires_post_update_version_verification(
    monkeypatch,
) -> None:
    candidate = _candidate()
    calls = 0
    for name in provider_updates._EXPLICIT_UPDATE_ENV_UNSET:
        monkeypatch.setenv(name, "inherited-disabled")

    def _run(command, **kwargs):
        nonlocal calls
        calls += 1
        env = kwargs["env"]
        if calls == 1:
            assert command == list(candidate.update_command)
            assert env["NO_UPDATE_NOTIFIER"] == "1"
            assert all(name not in env for name in provider_updates._EXPLICIT_UPDATE_ENV_UNSET)
            return subprocess.CompletedProcess(command, 0)
        assert {
            name: env.get(name) for name in provider_updates._NO_AUTO_UPDATE_ENV
        } == provider_updates._NO_AUTO_UPDATE_ENV
        return subprocess.CompletedProcess(command, 0, stdout="codex-cli 1.1.0\n", stderr="")

    result = provider_updates.execute_provider_update(candidate, run_fn=_run)

    assert result.success is True
    assert result.after_version == "1.1.0"


def test_execute_bun_update_pins_the_detected_bun_home(tmp_path: Path) -> None:
    bun_home = tmp_path / ".bun"
    package_entry = (
        bun_home
        / "install"
        / "global"
        / "node_modules"
        / "@oh-my-pi"
        / "pi-coding-agent"
        / "dist"
        / "cli.js"
    )
    package_entry.parent.mkdir(parents=True)
    package_entry.write_text("#!/usr/bin/env bun\n", encoding="utf-8")
    executable = bun_home / "bin" / "omp"
    executable.parent.mkdir(parents=True)
    executable.symlink_to(package_entry)
    candidate = provider_updates.ProviderUpdateCandidate(
        provider="omp",
        executable=executable,
        current_version="17.1.3",
        latest_version="17.1.4",
        owner="bun",
        package="@oh-my-pi/pi-coding-agent",
        update_command=(
            str(bun_home / "bin" / "bun"),
            "add",
            "--global",
            "@oh-my-pi/pi-coding-agent@17.1.4",
        ),
    )
    calls = 0

    def _run(command, **kwargs):
        nonlocal calls
        calls += 1
        if calls == 1:
            assert command == list(candidate.update_command)
            assert kwargs["env"]["BUN_INSTALL"] == str(bun_home)
            return subprocess.CompletedProcess(command, 0)
        return subprocess.CompletedProcess(
            command,
            0,
            stdout="17.1.4\n",
            stderr="",
        )

    result = provider_updates.execute_provider_update(candidate, run_fn=_run)

    assert result.success is True
    assert result.after_version == "17.1.4"


def test_execute_bun_update_fails_closed_when_home_cannot_be_revalidated() -> None:
    candidate = provider_updates.ProviderUpdateCandidate(
        provider="omp",
        executable=Path("/missing/omp"),
        current_version="17.1.3",
        latest_version="17.1.4",
        owner="bun",
        package="@oh-my-pi/pi-coding-agent",
        update_command=(
            "/missing/bun",
            "add",
            "--global",
            "@oh-my-pi/pi-coding-agent@17.1.4",
        ),
    )

    result = provider_updates.execute_provider_update(
        candidate,
        run_fn=lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("an unbound Bun update must not execute")
        ),
    )

    assert result.success is False
    assert "could not be revalidated" in result.detail


def test_check_reports_unchecked_native_provider_without_claiming_all_current(
    monkeypatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setenv("CCB_LANG", "en")
    candidate = provider_updates.ProviderUpdateCandidate(
        provider="agy",
        executable=Path("/tools/agy"),
        current_version="1.0.0",
        latest_version=None,
        owner="native",
        update_command=None,
        issue="no supported latest-version source",
    )
    stdout = StringIO()

    code = provider_updates.run_provider_update_flow(
        mode="check",
        stdout=stdout,
        state_path=tmp_path / "provider-updates.json",
        discover_fn=lambda: (candidate,),
    )

    assert code == 0
    assert "No supported provider CLI updates were found" in stdout.getvalue()
    assert "already current" not in stdout.getvalue()
    assert "agy: no supported latest-version source" in stdout.getvalue()


def test_live_provider_update_lock_does_not_expire_by_age(tmp_path: Path) -> None:
    lock_path = tmp_path / "provider-updates.lock"
    lock_path.write_text(f"{os.getpid()} 2026-01-01T00:00:00Z\n", encoding="utf-8")
    old = time.time() - provider_updates.LOCK_STALE_SECONDS - 60
    os.utime(lock_path, (old, old))

    assert provider_updates._lock_is_stale(lock_path) is False


def test_dead_provider_update_lock_is_stale_even_when_recent(
    monkeypatch,
    tmp_path: Path,
) -> None:
    lock_path = tmp_path / "provider-updates.lock"
    lock_path.write_text("424242 2026-01-01T00:00:00Z\n", encoding="utf-8")

    def _dead_process(_pid: int, _signal: int) -> None:
        raise ProcessLookupError

    monkeypatch.setattr(provider_updates.os, "kill", _dead_process)

    assert provider_updates._lock_is_stale(lock_path) is True
