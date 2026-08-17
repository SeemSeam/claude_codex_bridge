from __future__ import annotations

import json
import os
from pathlib import Path
import shlex
import sqlite3
from types import SimpleNamespace

import pytest

from agents.models import (
    AgentSpec,
    PermissionMode,
    ProviderProfileSpec,
    QueuePolicy,
    RestoreMode,
    RuntimeMode,
    SkillOverlaySpec,
    WorkspaceMode,
)
from cli.models import ParsedStartCommand
from provider_backends.agy import launcher as agy_launcher
from provider_backends.deepseek.launcher import build_start_cmd as build_deepseek_start_cmd
from provider_backends.deepseek.home import materialize_deepseek_home
from provider_backends.kimi.launcher import (
    build_session_payload as build_kimi_session_payload,
    build_start_cmd as build_kimi_start_cmd,
)
from provider_backends.kimi.home import materialize_kimi_home
from provider_backends.kimi.session import KIMI_RESTART_SESSION_MARKER
from provider_backends.kimi.skills import kimi_skill_dirs_for_launch, materialize_kimi_skills
from provider_backends.mimo.launcher import build_start_cmd as build_mimo_start_cmd
from provider_backends.mimo.home import materialize_mimo_home
from provider_backends.native_cli_support import home as native_home
from provider_backends.native_cli_support.home import materialize_native_login_state


def _spec(
    name: str,
    provider: str,
    *,
    startup_args: tuple[str, ...] = (),
    provider_command_template: str | None = None,
    model: str | None = None,
    thinking: str | None = None,
    env: dict[str, str] | None = None,
) -> AgentSpec:
    return AgentSpec(
        name=name,
        provider=provider,
        target=".",
        workspace_mode=WorkspaceMode.GIT_WORKTREE,
        workspace_root=None,
        runtime_mode=RuntimeMode.PANE_BACKED,
        restore_default=RestoreMode.AUTO,
        permission_default=PermissionMode.MANUAL,
        queue_policy=QueuePolicy.SERIAL_PER_AGENT,
        startup_args=startup_args,
        provider_command_template=provider_command_template,
        model=model,
        thinking=thinking,
        env=env or {},
    )


def test_agy_detaches_legacy_source_link_and_inherits_credentials_one_way(
    monkeypatch,
    tmp_path: Path,
) -> None:
    source_home = tmp_path / "source-home"
    managed_home = tmp_path / "managed-home"
    source_gemini = source_home / ".gemini"
    source_gemini.mkdir(parents=True)
    (source_gemini / "oauth_creds.json").write_text(
        '{"refresh_token":"source-token"}\n',
        encoding="utf-8",
    )
    (source_gemini / "settings.json").write_text('{"theme":"source"}\n', encoding="utf-8")
    managed_home.mkdir(parents=True)
    (managed_home / ".gemini").symlink_to(source_gemini, target_is_directory=True)
    monkeypatch.setattr(agy_launcher, "_resolve_managed_home", lambda runtime_dir: managed_home)
    monkeypatch.setattr(agy_launcher, "_resolve_credential_source_home", lambda: source_home)
    monkeypatch.setenv("AGY_START_CMD", "agy")

    command = ParsedStartCommand(
        project=None,
        agent_names=("agy_agent",),
        restore=False,
        auto_permission=False,
    )
    cmd = agy_launcher.build_start_cmd(
        command,
        _spec("agy_agent", "agy"),
        tmp_path / "runtime",
        "launch-agy",
    )

    managed_gemini = managed_home / ".gemini"
    assert managed_gemini.is_dir()
    assert not managed_gemini.is_symlink()
    assert (managed_gemini / "oauth_creds.json").read_text(encoding="utf-8") == (
        '{"refresh_token":"source-token"}\n'
    )
    assert f"HOME={shlex.quote(str(managed_home))}" in cmd

    (managed_gemini / "oauth_creds.json").write_text(
        '{"refresh_token":"managed-token"}\n',
        encoding="utf-8",
    )
    assert (source_gemini / "oauth_creds.json").read_text(encoding="utf-8") == (
        '{"refresh_token":"source-token"}\n'
    )


def test_agy_wsl_pins_windows_appdata_to_managed_home(
    monkeypatch,
    tmp_path: Path,
) -> None:
    source_home = tmp_path / "source-home"
    managed_home = tmp_path / "managed-home"
    source_home.mkdir()
    monkeypatch.setattr(agy_launcher, "_resolve_managed_home", lambda runtime_dir: managed_home)
    monkeypatch.setattr(agy_launcher, "_resolve_credential_source_home", lambda: source_home)
    monkeypatch.setenv("AGY_START_CMD", "agy")
    monkeypatch.setenv("WSL_DISTRO_NAME", "Ubuntu")
    monkeypatch.setenv("WSLENV", "EXISTING/u")

    cmd = agy_launcher.build_start_cmd(
        ParsedStartCommand(
            project=None,
            agent_names=("agy_agent",),
            restore=False,
            auto_permission=False,
        ),
        _spec("agy_agent", "agy"),
        tmp_path / "runtime",
        "launch-agy",
    )

    assert f"APPDATA={shlex.quote(str(managed_home / 'AppData' / 'Roaming'))}" in cmd
    assert f"LOCALAPPDATA={shlex.quote(str(managed_home / 'AppData' / 'Local'))}" in cmd
    assert "APPDATA/p" in cmd
    assert "LOCALAPPDATA/p" in cmd
    assert "EXISTING/u" in cmd


def test_agy_forces_file_token_storage_inside_private_home(
    monkeypatch,
    tmp_path: Path,
) -> None:
    source_home = tmp_path / "source-home"
    managed_home = tmp_path / "managed-home"
    external_home = tmp_path / "user-selected-home"
    source_marker = (
        source_home
        / ".gemini"
        / "antigravity-cli"
        / "cache"
        / "antigravity-keyring-unavailable"
    )
    source_marker.parent.mkdir(parents=True)
    source_marker.write_text("source-owned\n", encoding="utf-8")
    managed_marker = (
        managed_home
        / ".gemini"
        / "antigravity-cli"
        / "cache"
        / "antigravity-keyring-unavailable"
    )
    managed_marker.parent.mkdir(parents=True)
    managed_marker.symlink_to(source_marker)

    monkeypatch.setattr(agy_launcher, "_resolve_managed_home", lambda runtime_dir: managed_home)
    monkeypatch.setattr(agy_launcher, "_resolve_credential_source_home", lambda: source_home)
    monkeypatch.setenv("AGY_START_CMD", "agy")

    cmd = agy_launcher.build_start_cmd(
        ParsedStartCommand(
            project=None,
            agent_names=("agy_agent",),
            restore=False,
            auto_permission=False,
        ),
        _spec(
            "agy_agent",
            "agy",
            env={"HOME": str(external_home), "USERPROFILE": str(external_home)},
        ),
        tmp_path / "runtime",
        "launch-agy",
    )

    assert managed_marker.is_file()
    assert not managed_marker.is_symlink()
    assert managed_marker.read_bytes() == b""
    if os.name != "nt":
        assert managed_marker.stat().st_mode & 0o777 == 0o600
    assert source_marker.read_text(encoding="utf-8") == "source-owned\n"
    assert cmd.rfind(f"HOME={shlex.quote(str(managed_home))}") > cmd.rfind(
        f"HOME={shlex.quote(str(external_home))}"
    )
    assert cmd.rfind(f"USERPROFILE={shlex.quote(str(managed_home))}") > cmd.rfind(
        f"USERPROFILE={shlex.quote(str(external_home))}"
    )
    assert "antigravity-keyring-unavailable" not in cmd


def test_agy_fails_closed_when_legacy_credential_link_cannot_be_detached(
    monkeypatch,
    tmp_path: Path,
) -> None:
    source_home = tmp_path / "source-home"
    managed_home = tmp_path / "managed-home"
    source_home.mkdir()
    managed_home.mkdir()
    monkeypatch.setattr(agy_launcher, "_resolve_managed_home", lambda runtime_dir: managed_home)
    monkeypatch.setattr(agy_launcher, "_resolve_credential_source_home", lambda: source_home)

    def detach(target: Path, source: Path) -> bool:
        del source
        return target == managed_home

    monkeypatch.setattr(agy_launcher, "_detach_legacy_credential_link", detach)
    monkeypatch.setenv("AGY_START_CMD", "agy")

    with pytest.raises(RuntimeError, match="managed credential directory aliases external source"):
        agy_launcher.build_start_cmd(
            ParsedStartCommand(
                project=None,
                agent_names=("agy_agent",),
                restore=False,
                auto_permission=False,
            ),
            _spec("agy_agent", "agy"),
            tmp_path / "runtime",
            "launch-agy",
        )

    assert not (source_home / agy_launcher._AGY_KEYRING_BYPASS_MARKER_REL).exists()
    assert not (managed_home / agy_launcher._AGY_KEYRING_BYPASS_MARKER_REL).exists()


def test_agy_refreshes_file_token_storage_marker_on_every_launch(
    monkeypatch,
    tmp_path: Path,
) -> None:
    source_home = tmp_path / "source-home"
    managed_home = tmp_path / "managed-home"
    source_home.mkdir()
    monkeypatch.setattr(agy_launcher, "_resolve_managed_home", lambda runtime_dir: managed_home)
    monkeypatch.setattr(agy_launcher, "_resolve_credential_source_home", lambda: source_home)
    monkeypatch.setenv("AGY_START_CMD", "agy")
    command = ParsedStartCommand(
        project=None,
        agent_names=("agy_agent",),
        restore=False,
        auto_permission=False,
    )
    spec = _spec("agy_agent", "agy")
    runtime_dir = tmp_path / "runtime"

    agy_launcher.build_start_cmd(command, spec, runtime_dir, "launch-agy-1")
    marker = managed_home / agy_launcher._AGY_KEYRING_BYPASS_MARKER_REL
    os.utime(marker, (1, 1))

    agy_launcher.build_start_cmd(command, spec, runtime_dir, "launch-agy-2")

    assert marker.stat().st_mtime > 1


def test_kimi_start_cmd_uses_env_override_and_auto_without_implicit_restore(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("KIMI_START_CMD", "/tmp/stub-kimi --profile test")
    command = ParsedStartCommand(project=None, agent_names=("kimi_agent",), restore=True, auto_permission=True)
    spec = _spec("kimi_agent", "kimi", startup_args=("--model", "kimi-k2"))

    cmd = build_kimi_start_cmd(command, spec, tmp_path / "runtime", "launch-1")

    assert cmd.endswith("/tmp/stub-kimi --profile test --auto-approve --model kimi-k2")
    assert "--continue" not in cmd


def test_materialize_kimi_home_inherits_login_one_way(tmp_path: Path) -> None:
    source_home = tmp_path / "source-home"
    share = tmp_path / "managed" / ".kimi"
    code = tmp_path / "managed" / ".kimi-code"
    (source_home / ".kimi").mkdir(parents=True)
    (source_home / ".kimi-code" / "credentials").mkdir(parents=True)
    (source_home / ".kimi" / "kimi.json").write_text(
        '{"access_token":"source"}\n',
        encoding="utf-8",
    )
    (source_home / ".kimi-code" / "credentials" / "login.json").write_text(
        '{"refresh_token":"source"}\n',
        encoding="utf-8",
    )

    materialize_kimi_home(share, code, source_home=source_home)

    (share / "kimi.json").write_text('{"access_token":"managed"}\n', encoding="utf-8")
    (code / "credentials" / "login.json").unlink()
    assert (source_home / ".kimi" / "kimi.json").read_text(encoding="utf-8") == (
        '{"access_token":"source"}\n'
    )
    assert (source_home / ".kimi-code" / "credentials" / "login.json").is_file()


def test_kimi_start_cmd_preserves_explicit_user_restore_and_does_not_duplicate_auto_flags(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.delenv("KIMI_START_CMD", raising=False)
    command = ParsedStartCommand(project=None, agent_names=("kimi_agent",), restore=True, auto_permission=True)
    spec = _spec("kimi_agent", "kimi", startup_args=("--yolo", "--session", "abc"))

    cmd = build_kimi_start_cmd(command, spec, tmp_path / "runtime", "launch-1")

    assert cmd.endswith("kimi --yolo --session abc")
    assert "--auto-approve" not in cmd
    assert "--continue" not in cmd


def test_kimi_start_cmd_treats_legacy_auto_flag_as_explicit(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.delenv("KIMI_START_CMD", raising=False)
    command = ParsedStartCommand(project=None, agent_names=("kimi_agent",), restore=True, auto_permission=True)
    spec = _spec("kimi_agent", "kimi", startup_args=("--auto", "--session", "abc"))

    cmd = build_kimi_start_cmd(command, spec, tmp_path / "runtime", "launch-1")

    assert cmd.endswith("kimi --auto --session abc")
    assert "--auto-approve" not in cmd
    assert "--continue" not in cmd


def test_kimi_start_cmd_resumes_only_the_prevalidated_exact_session(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.delenv("KIMI_START_CMD", raising=False)
    command = ParsedStartCommand(project=None, agent_names=("kimi_agent",), restore=True, auto_permission=False)
    spec = _spec("kimi_agent", "kimi")
    prepared = {
        "kimi_resume_flag": "--session",
        "kimi_resume_session_id": "session-owned-by-kimi-agent",
        "kimi_resume_session_path": str(tmp_path / "wire.jsonl"),
        "kimi_resume_status": "exact_session_ready",
    }

    cmd = build_kimi_start_cmd(
        command,
        spec,
        tmp_path / "runtime",
        "launch-2",
        prepared_state=prepared,
    )

    command_parts = shlex.split(cmd.rsplit("; ", 1)[-1])
    assert command_parts[-2:] == [
        "--session",
        "session-owned-by-kimi-agent",
    ]
    assert command_parts.count("--session") == 1
    assert "--continue" not in cmd
    assert prepared["kimi_resume_status"] == "exact_session_selected"
    assert KIMI_RESTART_SESSION_MARKER not in cmd
    assert prepared["kimi_restart_start_cmd_template"].count(KIMI_RESTART_SESSION_MARKER) == 1
    assert prepared["kimi_capability_command_parts"] == ["kimi"]


def test_kimi_explicit_session_control_wins_over_owned_resume(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.delenv("KIMI_START_CMD", raising=False)
    command = ParsedStartCommand(project=None, agent_names=("kimi_agent",), restore=True, auto_permission=False)
    spec = _spec("kimi_agent", "kimi", startup_args=("--continue",))
    prepared = {
        "kimi_resume_flag": "--session",
        "kimi_resume_session_id": "session-owned-by-kimi-agent",
        "kimi_resume_status": "exact_session_ready",
    }

    cmd = build_kimi_start_cmd(
        command,
        spec,
        tmp_path / "runtime",
        "launch-3",
        prepared_state=prepared,
    )

    parts = shlex.split(cmd.rsplit("; ", 1)[-1])
    assert parts.count("--continue") == 1
    assert "--session" not in parts
    assert prepared["kimi_resume_status"] == "explicit_session_control"


def test_kimi_exact_selector_survives_provider_command_template_once(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.delenv("KIMI_START_CMD", raising=False)
    command = ParsedStartCommand(project=None, agent_names=("kimi_agent",), restore=True, auto_permission=False)
    spec = _spec(
        "kimi_agent",
        "kimi",
        provider_command_template="env KIMI_WRAPPED=1 {command}",
    )
    prepared = {
        "kimi_resume_flag": "--session",
        "kimi_resume_session_id": "native-owned-session",
        "kimi_resume_status": "exact_session_ready",
    }

    cmd = build_kimi_start_cmd(
        command,
        spec,
        tmp_path / "runtime",
        "launch-template",
        prepared_state=prepared,
    )

    parts = shlex.split(cmd.rsplit("; ", 1)[-1])
    assert parts == ["env", "KIMI_WRAPPED=1", "kimi", "--session", "native-owned-session"]
    assert prepared["kimi_restart_start_cmd_template"].count(KIMI_RESTART_SESSION_MARKER) == 1


def test_kimi_clear_reset_discards_carried_automatic_resume(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.delenv("KIMI_START_CMD", raising=False)
    command = ParsedStartCommand(project=None, agent_names=("kimi_agent",), restore=False, auto_permission=False)
    spec = _spec("kimi_agent", "kimi")
    prepared = {
        "kimi_resume_flag": "--session",
        "kimi_resume_session_id": "session-owned-by-kimi-agent",
        "kimi_resume_status": "exact_session_ready",
    }

    cmd = build_kimi_start_cmd(
        command,
        spec,
        tmp_path / "runtime",
        "launch-reset",
        prepared_state=prepared,
    )

    assert "--session" not in shlex.split(cmd.rsplit("; ", 1)[-1])
    assert "--continue" not in cmd
    assert prepared["kimi_resume_status"] == "fresh_restore_disabled"


def test_kimi_session_payload_retains_only_selected_exact_binding(tmp_path: Path) -> None:
    project = tmp_path / "repo"
    project.mkdir()
    context = SimpleNamespace(project=SimpleNamespace(project_id="project-1", project_root=project))
    spec = _spec("kimi_agent", "kimi")
    plan = SimpleNamespace(workspace_path=project)
    prepared = {
        "kimi_share_dir": str(tmp_path / "share"),
        "kimi_resume_session_id": "session-owned-by-kimi-agent",
        "kimi_resume_session_path": str(tmp_path / "share" / "sessions" / "hash" / "session-owned-by-kimi-agent" / "wire.jsonl"),
        "kimi_resume_status": "exact_session_selected",
    }

    payload = build_kimi_session_payload(
        context=context,
        spec=spec,
        plan=plan,
        runtime_dir=tmp_path / "runtime",
        run_cwd=project,
        pane_id="%1",
        pane_title_marker="marker",
        start_cmd="kimi --session session-owned-by-kimi-agent",
        launch_session_id="launch-4",
        prepared_state=prepared,
    )

    assert payload["kimi_share_dir"] == str(tmp_path / "share")
    assert payload["kimi_session_id"] == "session-owned-by-kimi-agent"
    assert payload["kimi_session_path"] == prepared["kimi_resume_session_path"]
    assert payload["kimi_resume_status"] == "exact_session_selected"
    assert payload["kimi_restart_start_cmd_template"] == ""
    assert payload["kimi_capability_command_parts"] == []


def test_kimi_start_cmd_adds_materialized_skill_dirs(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.delenv("KIMI_START_CMD", raising=False)
    home = tmp_path / "home"
    project = tmp_path / "repo"
    workspace = project / "pkg"
    state_dir = tmp_path / "provider-state" / "kimi"
    project_skill_dir = project / ".kimi" / "skills"
    user_skill_dir = home / ".kimi" / "skills"
    ccb_skill_dir = state_dir / "inherited-skills"
    for path in (project / ".git", workspace, project_skill_dir, user_skill_dir, ccb_skill_dir):
        path.mkdir(parents=True)
    command = ParsedStartCommand(project=None, agent_names=("kimi_agent",), restore=False, auto_permission=False)
    spec = _spec("kimi_agent", "kimi", startup_args=("--model", "kimi-k2"))

    cmd = build_kimi_start_cmd(
        command,
        spec,
        tmp_path / "runtime",
        "launch-1",
        prepared_state={
            "kimi_skill_dirs": [
                str(path)
                for path in kimi_skill_dirs_for_launch(
                    project_root=project,
                    workspace_path=workspace,
                    state_dir=state_dir,
                    env={"HOME": str(home)},
                )
            ]
        },
    )

    parts = shlex.split(cmd)
    kimi_index = parts.index("kimi")
    assert parts[kimi_index : kimi_index + 7] == [
        "kimi",
        "--skills-dir",
        str(project_skill_dir),
        "--skills-dir",
        str(user_skill_dir),
        "--skills-dir",
        str(ccb_skill_dir),
    ]
    assert parts[-2:] == ["--model", "kimi-k2"]


def test_materialize_kimi_skills_projects_skill_overlays(tmp_path: Path) -> None:
    project = tmp_path / "repo"
    state_dir = tmp_path / "provider-state" / "kimi"
    overlay_source = tmp_path / "codex-skills"
    project.mkdir(parents=True)
    for skill_name in ("trellis-check", "trellis-start", "unrelated"):
        skill_dir = overlay_source / skill_name
        skill_dir.mkdir(parents=True, exist_ok=True)
        (skill_dir / "SKILL.md").write_text(f"{skill_name}\n", encoding="utf-8")

    active_dirs = materialize_kimi_skills(
        project_root=project,
        agent_name="agent1",
        state_dir=state_dir,
        profile=ProviderProfileSpec(
            inherit_skills=False,
            skill_overlays={
                "n14_trellis": SkillOverlaySpec(
                    source=str(overlay_source),
                    include=("trellis-*",),
                ),
            },
        ),
    )

    overlay_dir = state_dir / "overlay-skills"
    assert overlay_dir in active_dirs
    assert (overlay_dir / "trellis-check" / "SKILL.md").read_text(encoding="utf-8") == "trellis-check\n"
    assert (overlay_dir / "trellis-start" / "SKILL.md").read_text(encoding="utf-8") == "trellis-start\n"
    assert (overlay_dir / "trellis-check.ccb-projection.json").is_file()
    assert (overlay_dir / "trellis-start.ccb-projection.json").is_file()
    assert not (overlay_dir / "unrelated").exists()


def test_materialize_kimi_skills_preserves_unmarked_packaged_target(
    tmp_path: Path,
    monkeypatch,
) -> None:
    packaged = tmp_path / 'packaged-kimi-skills'
    for skill_name in ('ask', 'ccb-clear', 'ccb-compact', 'ccb-diagnose'):
        (packaged / skill_name).mkdir(parents=True)
        (packaged / skill_name / 'SKILL.md').write_text(f'{skill_name}\n', encoding='utf-8')
    state_dir = tmp_path / 'provider-state' / 'kimi'
    inherited_dir = state_dir / 'inherited-skills'
    (inherited_dir / 'user-skill').mkdir(parents=True)
    (inherited_dir / 'user-skill' / 'SKILL.md').write_text('user\n', encoding='utf-8')
    monkeypatch.setattr(
        'provider_core.inherited_skills.packaged_inherited_skills_dir',
        lambda provider: packaged,
    )

    active_dirs = materialize_kimi_skills(
        project_root=None,
        agent_name='agent1',
        state_dir=state_dir,
        profile=ProviderProfileSpec(inherit_skills=True),
    )

    assert inherited_dir in active_dirs
    assert (inherited_dir / 'user-skill' / 'SKILL.md').read_text(encoding='utf-8') == 'user\n'
    assert (inherited_dir / 'ask' / 'SKILL.md').is_file()
    assert (inherited_dir / 'ccb-clear' / 'SKILL.md').is_file()
    assert (inherited_dir / 'ccb-compact' / 'SKILL.md').is_file()
    assert (inherited_dir / 'ccb-diagnose' / 'SKILL.md').is_file()
    assert not Path(f'{inherited_dir}.ccb-projection.json').exists()


def test_deepseek_start_cmd_defaults_to_deepcode_and_keeps_startup_args(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.delenv("DEEPSEEK_START_CMD", raising=False)
    command = ParsedStartCommand(project=None, agent_names=("deep_agent",), restore=True, auto_permission=True)
    spec = _spec("deep_agent", "deepseek", startup_args=("--raw",))

    cmd = build_deepseek_start_cmd(command, spec, tmp_path / "runtime", "launch-1")

    assert cmd.endswith("deepcode --raw")
    assert f"HOME={shlex.quote(str(tmp_path / 'runtime' / 'deepseek-home'))}" in cmd


def test_materialize_deepseek_home_inherits_settings_one_way(tmp_path: Path) -> None:
    source_home = tmp_path / "source-home"
    target_home = tmp_path / "managed-home"
    source_settings = source_home / ".deepcode" / "settings.json"
    source_settings.parent.mkdir(parents=True)
    source_settings.write_text('{"apiKey":"source-key"}\n', encoding="utf-8")

    materialize_deepseek_home(target_home, source_home=source_home)

    target_settings = target_home / ".deepcode" / "settings.json"
    target_settings.write_text('{"apiKey":"managed-key"}\n', encoding="utf-8")
    assert source_settings.read_text(encoding="utf-8") == '{"apiKey":"source-key"}\n'


def test_native_pi_auth_projection_is_one_way(tmp_path: Path) -> None:
    source_home = tmp_path / "source-home"
    target_home = tmp_path / "managed-home"
    source_auth = source_home / ".pi" / "agent" / "auth.json"
    source_auth.parent.mkdir(parents=True)
    source_auth.write_text('{"provider":"source-token"}\n', encoding="utf-8")

    materialize_native_login_state("pi", target_home, source_home=source_home)

    target_auth = target_home / "auth.json"
    assert target_auth.read_text(encoding="utf-8") == '{"provider":"source-token"}\n'
    target_auth.unlink()
    assert source_auth.is_file()


def test_native_cursor_projection_detaches_managed_home_symlink(tmp_path: Path) -> None:
    source_home = tmp_path / "source-home"
    outside = tmp_path / "outside"
    target_home = tmp_path / "managed-home"
    source_config = source_home / ".cursor" / "cli-config.json"
    source_config.parent.mkdir(parents=True)
    source_config.write_text('{"accessToken":"source"}\n', encoding="utf-8")
    outside.mkdir()
    target_home.symlink_to(outside, target_is_directory=True)

    materialize_native_login_state("cursor", target_home, source_home=source_home)

    assert target_home.is_dir() and not target_home.is_symlink()
    assert (target_home / ".cursor" / "cli-config.json").is_file()
    assert not (outside / ".cursor").exists()


def test_native_cursor_projects_file_store_auth_one_way(tmp_path: Path) -> None:
    source_home = tmp_path / "source-home"
    target_home = tmp_path / "managed-home"
    source_auth = source_home / ".config" / "cursor" / "auth.json"
    source_auth.parent.mkdir(parents=True)
    source_auth.write_text(
        '{"accessToken":"source","refreshToken":"source-refresh"}\n',
        encoding="utf-8",
    )

    materialize_native_login_state(
        "cursor",
        target_home,
        source_home=source_home,
        config_dir=target_home / ".config",
    )

    target_auth = target_home / ".config" / "cursor" / "auth.json"
    assert target_auth.is_file() and not target_auth.is_symlink()
    target_auth.unlink()
    assert source_auth.is_file()


def test_native_cursor_imports_macos_keychain_auth_into_private_file(
    monkeypatch,
    tmp_path: Path,
) -> None:
    source_home = tmp_path / "source-home"
    target_home = tmp_path / "managed-home"
    source_home.mkdir()
    monkeypatch.delenv("CCB_SOURCE_HOME", raising=False)
    monkeypatch.setattr(native_home, "current_provider_source_home", lambda: source_home)
    monkeypatch.setattr(native_home.platform, "system", lambda: "Darwin")
    values = {
        "cursor-access-token": "source-access",
        "cursor-refresh-token": "source-refresh",
        "cursor-api-key": None,
        "cursor-bedrock-access-key": None,
        "cursor-bedrock-secret-key": None,
        "cursor-bedrock-session-token": None,
    }
    reads: list[tuple[str, str]] = []

    def fake_read(service: str, account: str, **_kwargs) -> str | None:
        reads.append((service, account))
        return values[service]

    monkeypatch.setattr(native_home, "read_keyring_password", fake_read)

    materialize_native_login_state("cursor", target_home)

    target_auth = target_home / ".cursor" / "auth.json"
    assert json.loads(target_auth.read_text(encoding="utf-8")) == {
        "accessToken": "source-access",
        "refreshToken": "source-refresh",
    }
    assert reads == [(service, "cursor-user") for service in values]
    target_auth.write_text(
        '{"accessToken":"managed-access","refreshToken":"managed-refresh"}\n',
        encoding="utf-8",
    )
    materialize_native_login_state("cursor", target_home)
    assert json.loads(target_auth.read_text(encoding="utf-8")) == {
        "accessToken": "managed-access",
        "refreshToken": "managed-refresh",
    }


def test_native_cursor_explicit_source_never_reads_external_keychain(
    monkeypatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setattr(native_home.platform, "system", lambda: "Darwin")

    def unexpected_read(*_args, **_kwargs):
        raise AssertionError("external keychain must not be read for an explicit source home")

    monkeypatch.setattr(native_home, "read_keyring_password", unexpected_read)

    materialize_native_login_state(
        "cursor",
        tmp_path / "managed-home",
        source_home=tmp_path / "source-home",
    )


def test_native_zai_projects_current_user_settings_one_way(tmp_path: Path) -> None:
    source_home = tmp_path / "source-home"
    target_home = tmp_path / "managed-home"
    source_settings = source_home / ".zai" / "user-settings.json"
    source_settings.parent.mkdir(parents=True)
    source_settings.write_text(
        '{"apiKey":"source-key","baseURL":"https://example.test"}\n',
        encoding="utf-8",
    )

    materialize_native_login_state("zai", target_home, source_home=source_home)

    target_settings = target_home / ".zai" / "user-settings.json"
    assert target_settings.is_file() and not target_settings.is_symlink()
    target_settings.write_text('{"apiKey":"managed-key"}\n', encoding="utf-8")
    assert source_settings.read_text(encoding="utf-8") == (
        '{"apiKey":"source-key","baseURL":"https://example.test"}\n'
    )


def test_native_crush_projects_provider_api_state_into_private_data_dir(
    tmp_path: Path,
) -> None:
    source_home = tmp_path / "source-home"
    target_home = tmp_path / "managed-home"
    target_data = tmp_path / "provider-state" / "data"
    source_providers = source_home / ".local" / "share" / "crush" / "providers.json"
    source_config = source_home / ".config" / "crush" / "crush.json"
    source_projects = source_providers.with_name("projects.json")
    source_providers.parent.mkdir(parents=True)
    source_config.parent.mkdir(parents=True)
    source_providers.write_text('[{"api_key":"source-key"}]\n', encoding="utf-8")
    source_projects.write_text('{"projects":["source-workspace"]}\n', encoding="utf-8")
    source_config.write_text('{"options":{"theme":"source"}}\n', encoding="utf-8")

    materialize_native_login_state(
        "crush",
        target_home,
        source_home=source_home,
        data_dir=target_data,
        config_dir=target_home / ".config",
    )

    target_providers = target_data / "providers.json"
    assert target_providers.is_file() and not target_providers.is_symlink()
    assert not (target_data / "projects.json").exists()
    assert (target_home / ".config" / "crush" / "crush.json").is_file()
    target_providers.write_text('[{"api_key":"managed-key"}]\n', encoding="utf-8")
    assert source_providers.read_text(encoding="utf-8") == '[{"api_key":"source-key"}]\n'


def test_native_kiro_snapshots_auth_database_one_way_without_history(
    monkeypatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setattr(
        "provider_backends.native_cli_support.home.platform.system",
        lambda: "Linux",
    )
    source_home = tmp_path / "source-home"
    target_home = tmp_path / "managed-home"
    target_data = tmp_path / "provider-state" / "data"
    source_database = source_home / ".local" / "share" / "kiro-cli" / "data.sqlite3"
    source_database.parent.mkdir(parents=True)
    with sqlite3.connect(source_database) as connection:
        connection.executescript(
            """
            CREATE TABLE migrations (version INTEGER PRIMARY KEY);
            CREATE TABLE auth_kv (key TEXT PRIMARY KEY, value TEXT);
            CREATE TABLE state (key TEXT PRIMARY KEY, value TEXT);
            CREATE TABLE history (id INTEGER PRIMARY KEY, command TEXT);
            INSERT INTO migrations VALUES (5);
            INSERT INTO auth_kv VALUES ('kirocli:social:token', 'source-token');
            INSERT INTO state VALUES ('api.codewhisperer.profile', 'source-profile');
            INSERT INTO history(command) VALUES ('source private command');
            """
        )

    materialize_native_login_state(
        "kiro",
        target_home,
        source_home=source_home,
        data_dir=target_data,
    )

    target_database = target_data / "kiro-cli" / "data.sqlite3"
    assert target_database.is_file() and not target_database.is_symlink()
    with sqlite3.connect(target_database) as connection:
        assert connection.execute("SELECT value FROM auth_kv").fetchone() == ("source-token",)
        assert connection.execute("SELECT value FROM state").fetchone() == ("source-profile",)
        assert connection.execute("SELECT COUNT(*) FROM history").fetchone() == (0,)
        connection.execute("DELETE FROM auth_kv")
        connection.commit()
    with sqlite3.connect(f"{source_database.absolute().as_uri()}?mode=ro", uri=True) as connection:
        assert connection.execute("SELECT value FROM auth_kv").fetchone() == ("source-token",)


def test_native_kiro_fails_closed_on_macos_keychain_backend(
    monkeypatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setattr(
        "provider_backends.native_cli_support.home.platform.system",
        lambda: "Darwin",
    )

    with pytest.raises(RuntimeError, match="global macOS Keychain"):
        materialize_native_login_state(
            "kiro",
            tmp_path / "managed-home",
            source_home=tmp_path / "source-home",
        )


def test_deepseek_start_cmd_supports_env_override_and_template(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("DEEPSEEK_START_CMD", "/tmp/deepcode --config demo")
    command = ParsedStartCommand(project=None, agent_names=("deep_agent",), restore=False, auto_permission=False)
    spec = _spec("deep_agent", "deepseek", provider_command_template="sandbox=1 {command}")

    cmd = build_deepseek_start_cmd(command, spec, tmp_path / "runtime", "launch-1")

    assert cmd.endswith("sandbox=1 /tmp/deepcode --config demo")


def test_deepseek_start_cmd_compiles_model_and_thinking_to_deepcode_env(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.delenv("DEEPSEEK_START_CMD", raising=False)
    command = ParsedStartCommand(project=None, agent_names=("deep_agent",), restore=False, auto_permission=False)
    spec = _spec(
        "deep_agent",
        "deepseek",
        model="deepseek-v4-pro",
        thinking="max",
    )

    cmd = build_deepseek_start_cmd(command, spec, tmp_path / "runtime", "launch-1")

    assert "DEEPCODE_MODEL=deepseek-v4-pro" in cmd
    assert "DEEPCODE_THINKING_ENABLED=true" in cmd
    assert "DEEPCODE_REASONING_EFFORT=max" in cmd
    assert cmd.endswith("deepcode")


def test_deepseek_start_cmd_compiles_thinking_off_without_effort(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.delenv("DEEPSEEK_START_CMD", raising=False)
    command = ParsedStartCommand(project=None, agent_names=("deep_agent",), restore=False, auto_permission=False)
    spec = _spec(
        "deep_agent",
        "deepseek",
        model="deepseek-v4-flash",
        thinking="off",
    )

    cmd = build_deepseek_start_cmd(command, spec, tmp_path / "runtime", "launch-1")

    assert "DEEPCODE_MODEL=deepseek-v4-flash" in cmd
    assert "DEEPCODE_THINKING_ENABLED=false" in cmd
    assert "DEEPCODE_REASONING_EFFORT" not in cmd


def test_mimo_start_cmd_uses_managed_home_config_and_env_override(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("MIMO_START_CMD", "/tmp/stub-mimo --provider mimo")
    runtime_dir = tmp_path / "runtime"
    state_dir = tmp_path / "provider-state" / "mimo"
    config_path = state_dir / "mimocode.json"
    config_path.parent.mkdir(parents=True)
    config_path.write_text("{}\n", encoding="utf-8")
    command = ParsedStartCommand(project=None, agent_names=("mimo_agent",), restore=True, auto_permission=False)
    spec = _spec("mimo_agent", "mimo", startup_args=("--model", "mimo-auto"))

    cmd = build_mimo_start_cmd(
        command,
        spec,
        runtime_dir,
        "launch-1",
        prepared_state={
            "mimo_home": str(state_dir / "home"),
            "mimo_config_path": str(config_path),
        },
    )

    assert "MIMOCODE_HOME=" + str(state_dir / "home") in cmd
    assert "HOME=" + str(state_dir / "home") in cmd
    assert "MIMOCODE_CONFIG=" + str(config_path) in cmd
    assert "MIMOCODE_DISABLE_AUTOUPDATE=true" in cmd
    assert "MIMOCODE_ENABLE_ANALYSIS=false" in cmd
    parts = shlex.split(cmd.rsplit("; ", 1)[-1])
    mimo_index = parts.index("/tmp/stub-mimo")
    assert parts[mimo_index : mimo_index + 5] == [
        "/tmp/stub-mimo",
        "--provider",
        "mimo",
        "--continue",
        "--model",
    ]


def test_materialize_mimo_home_inherits_auth_one_way(tmp_path: Path) -> None:
    source_home = tmp_path / "source-home"
    target_home = tmp_path / "managed-home"
    source_auth = source_home / ".mimocode" / "auth.json"
    source_auth.parent.mkdir(parents=True)
    source_auth.write_text('{"token":"source"}\n', encoding="utf-8")

    materialize_mimo_home(target_home, source_home=source_home)

    target_auth = target_home / "auth.json"
    assert target_auth.read_text(encoding="utf-8") == '{"token":"source"}\n'
    target_auth.write_text('{"token":"managed"}\n', encoding="utf-8")
    assert source_auth.read_text(encoding="utf-8") == '{"token":"source"}\n'
