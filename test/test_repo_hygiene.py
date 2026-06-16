from __future__ import annotations

from pathlib import Path
import re
import subprocess

import pytest


def _is_ephemeral_repo_artifact(path_text: str) -> bool:
    path = Path(path_text.strip())
    if not path.parts:
        return False
    first = path.parts[0]
    return first == ".tmp_pytest" or first.startswith(".tmp_test_env_")


def test_git_index_does_not_track_ephemeral_test_artifacts() -> None:
    repo_root = Path(__file__).resolve().parents[1]
    if not (repo_root / ".git").exists():
        pytest.skip("git checkout required")

    completed = subprocess.run(
        ["git", "-C", str(repo_root), "ls-files"],
        capture_output=True,
        text=True,
        check=True,
    )
    tracked = [
        line
        for line in completed.stdout.splitlines()
        if line.strip() and _is_ephemeral_repo_artifact(line)
    ]

    assert tracked == []


def test_useful_tools_skills_are_provider_paired() -> None:
    repo_root = Path(__file__).resolve().parents[1]
    useful_tools = repo_root / "useful_tools"
    if not useful_tools.exists():
        pytest.skip("useful_tools not present")

    codex_root = useful_tools / "codex_skills"
    claude_root = useful_tools / "claude_skills"
    codex_skills = {
        path.name
        for path in codex_root.iterdir()
        if path.is_dir() and (path / "SKILL.md").is_file()
    }
    claude_skills = {
        path.name
        for path in claude_root.iterdir()
        if path.is_dir() and (path / "SKILL.md").is_file()
    }

    assert codex_skills == claude_skills


def test_inherited_skills_live_under_inherit_skills_only() -> None:
    repo_root = Path(__file__).resolve().parents[1]

    for legacy_root in ("claude_skills", "codex_skills", "droid_skills", "kimi_skills", "mimo_skills", "opencode_skills"):
        assert not (repo_root / legacy_root).exists()

    inherited = repo_root / "inherit_skills"
    assert (inherited / "claude_skills" / "ask" / "SKILL.md").is_file()
    assert (inherited / "codex_skills" / "ask" / "SKILL.md").is_file()
    assert (inherited / "droid_skills" / "ask" / "SKILL.md").is_file()
    assert (inherited / "kimi_skills" / "ask" / "SKILL.md").is_file()
    assert (inherited / "mimo_skills" / "ask.md").is_file()
    assert (inherited / "opencode_skills" / "ask.md").is_file()
    assert (inherited / "claude_skills" / "ccb-clear" / "SKILL.md").is_file()
    assert (inherited / "codex_skills" / "ccb-clear" / "SKILL.md").is_file()

    assert not (inherited / "claude_skills" / "ccb-config").exists()
    assert not (inherited / "codex_skills" / "ccb-config").exists()
    assert not (repo_root / "useful_tools" / "claude_skills" / "ccb-config").exists()
    assert not (repo_root / "useful_tools" / "codex_skills" / "ccb-config").exists()
    assert not (repo_root / "useful_tools" / "claude_skills" / "ccb-clear").exists()
    assert not (repo_root / "useful_tools" / "codex_skills" / "ccb-clear").exists()


def test_inherited_skill_set_is_minimal() -> None:
    repo_root = Path(__file__).resolve().parents[1]

    expected = {
        "claude_skills": {"ask", "ccb-clear"},
        "codex_skills": {"ask", "ccb-clear"},
        "droid_skills": {"ask"},
        "kimi_skills": {"ask"},
    }
    for provider_root, expected_names in expected.items():
        skill_root = repo_root / "inherit_skills" / provider_root
        actual = {
            path.name
            for path in skill_root.iterdir()
            if path.is_dir() and (path / "SKILL.md").is_file()
        }

        assert actual == expected_names

    for provider_root in ("mimo_skills", "opencode_skills"):
        skill_root = repo_root / "inherit_skills" / provider_root
        assert {path.name for path in skill_root.iterdir() if path.is_file()} == {"ask.md"}


def test_install_scripts_current_skill_lists_are_minimal() -> None:
    repo_root = Path(__file__).resolve().parents[1]
    install_sh = (repo_root / "install.sh").read_text(encoding="utf-8")
    install_ps1 = (repo_root / "install.ps1").read_text(encoding="utf-8")

    assert 'local ccb_skills="ask ccb-config ccb-clear"' in install_sh
    assert 'local legacy_skills="ccb-config ' in install_sh
    assert 'local ccb_skills="ask ping' not in install_sh
    assert '$ccbSkills = @("ask", "ccb-config", "ccb-clear")' in install_ps1
    assert '$legacySkills = @("ccb-config",' in install_ps1
    assert '$ccbSkills = @("ask", "ccb-config", "ping"' not in install_ps1
    assert '$droidSkills = @("ask")' in install_ps1


def test_inherited_codex_skill_names_are_valid_and_match_directories() -> None:
    repo_root = Path(__file__).resolve().parents[1]
    skill_root = repo_root / "inherit_skills" / "codex_skills"
    name_re = re.compile(r"^[a-z0-9][a-z0-9-]{0,63}$")

    for skill_dir in skill_root.iterdir():
        if not skill_dir.is_dir():
            continue
        skill_md = skill_dir / "SKILL.md"
        if not skill_md.is_file():
            continue
        text = skill_md.read_text(encoding="utf-8")
        first_name = next(
            line.split(":", 1)[1].strip()
            for line in text.splitlines()
            if line.startswith("name:")
        )

        assert first_name == skill_dir.name
        assert name_re.fullmatch(first_name)


def test_full_ccb_config_skill_is_not_inherited() -> None:
    repo_root = Path(__file__).resolve().parents[1]
    for provider_root in ("claude_skills", "codex_skills"):
        assert not (repo_root / "inherit_skills" / provider_root / "ccb-config").exists()


def test_public_readme_routes_config_work_to_ccb_self() -> None:
    repo_root = Path(__file__).resolve().parents[1]
    readme = (repo_root / "README.md").read_text(encoding="utf-8")
    readme_zh = (repo_root / "README_zh.md").read_text(encoding="utf-8")

    assert "ccb_self" in readme
    assert "agentroles.ccb_self" in readme
    assert "ccb_self" in readme_zh
    assert "agentroles.ccb_self" in readme_zh


def test_source_checkout_runtime_discipline_is_enforced_by_entrypoints() -> None:
    repo_root = Path(__file__).resolve().parents[1]
    source_entrypoint = (repo_root / "ccb.py").read_text(encoding="utf-8")
    test_entrypoint = (repo_root / "ccb_test").read_text(encoding="utf-8")

    for text in (source_entrypoint, test_entrypoint):
        assert "source checkout" in text or "source-change validation" in text
        assert "ccb_test" in text
        assert "test_ccb2" in text
        assert 'parent / "test_ccb",' not in text
        assert 'parent / "ccb_test2",' not in text
    assert "/home/bfly/yunwei/test_ccb2" in test_entrypoint
    assert "diagnose" in test_entrypoint


def test_inherited_runtime_skills_distinguish_source_validation_from_work_environment() -> None:
    repo_root = Path(__file__).resolve().parents[1]
    for provider_root in ("claude_skills", "codex_skills"):
        for skill_name in ("ccb-clear",):
            skill_text = (
                repo_root
                / "inherit_skills"
                / provider_root
                / skill_name
                / "SKILL.md"
            ).read_text(encoding="utf-8")

            assert "source checkout" in skill_text
            assert "source validation" in skill_text
            assert "ccb_test" in skill_text
            assert "/home/bfly/yunwei/test_ccb2" in skill_text
