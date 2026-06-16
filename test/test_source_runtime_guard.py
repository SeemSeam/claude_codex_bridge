from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
CCB = REPO_ROOT / "ccb.py"
CCB_TEST = REPO_ROOT / "ccb_test"


def _run_source_ccb(args: list[str], *, cwd: Path, extra_env: dict[str, str] | None = None) -> subprocess.CompletedProcess[str]:
    env = dict(os.environ)
    env.pop("PYTEST_CURRENT_TEST", None)
    env.pop("CCB_SOURCE_RUNTIME_OK", None)
    env.pop("CCB_SOURCE_ALLOWED_ROOTS", None)
    if extra_env:
        env.update(extra_env)
    return subprocess.run(
        [sys.executable, str(CCB), *args],
        cwd=str(cwd),
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )


def _run_ccb_test(args: list[str], *, cwd: Path, extra_env: dict[str, str] | None = None) -> subprocess.CompletedProcess[str]:
    env = dict(os.environ)
    env.pop("PYTEST_CURRENT_TEST", None)
    env.pop("CCB_SOURCE_RUNTIME_OK", None)
    env.pop("CCB_SOURCE_ALLOWED_ROOTS", None)
    env.pop("CCB_TEST_ROOTS", None)
    if extra_env:
        env.update(extra_env)
    return subprocess.run(
        [sys.executable, str(CCB_TEST), *args],
        cwd=str(cwd),
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )


def test_source_ccb_allows_introspection_outside_test_roots() -> None:
    proc = _run_source_ccb(["--print-version"], cwd=REPO_ROOT)

    assert proc.returncode == 0
    assert proc.stdout.strip()


def test_source_ccb_rejects_stateful_commands_outside_test_roots() -> None:
    proc = _run_source_ccb(["doctor"], cwd=REPO_ROOT)

    assert proc.returncode == 1
    assert "Refusing to run the CCB source checkout outside an allowed test project" in proc.stderr
    assert (
        "Use `/home/bfly/yunwei/ccb_source/ccb_test` from "
        "`/home/bfly/yunwei/test_ccb2` for source-change validation"
    ) in proc.stderr


def test_source_ccb_default_allowed_roots_are_dedicated_test_project_only() -> None:
    proc = _run_source_ccb(["doctor"], cwd=REPO_ROOT)

    allowed_line = next(line for line in proc.stderr.splitlines() if line.startswith("Allowed source roots:"))
    roots = [item.strip() for item in allowed_line.split(":", 1)[1].split(",")]
    assert roots == [str(REPO_ROOT.parent / "test_ccb2")]


def test_source_ccb_rejects_legacy_sibling_project_arg_without_override() -> None:
    legacy_project = REPO_ROOT.parent / "test_ccb"

    proc = _run_source_ccb(["--project", str(legacy_project), "doctor"], cwd=REPO_ROOT)

    assert proc.returncode == 1
    assert "Refusing to run the CCB source checkout outside an allowed test project" in proc.stderr
    assert f"Allowed source roots: {REPO_ROOT.parent / 'test_ccb2'}" in proc.stderr


def test_source_ccb_rejects_legacy_named_external_cwd_without_override(tmp_path: Path) -> None:
    legacy_named_project = tmp_path / "test_ccb"
    legacy_named_project.mkdir()

    proc = _run_source_ccb(["doctor"], cwd=legacy_named_project)

    assert proc.returncode == 1
    assert "Refusing to run the CCB source checkout outside an allowed test project" in proc.stderr
    assert f"Allowed source roots: {REPO_ROOT.parent / 'test_ccb2'}" in proc.stderr


def test_source_ccb_allows_stateful_commands_under_configured_test_root(tmp_path: Path) -> None:
    allowed = tmp_path / "test-project"
    project = allowed / "repo"
    project.mkdir(parents=True)
    (project / ".ccb").mkdir()
    (project / ".ccb" / "ccb.config").write_text("cmd; agent1:codex\n", encoding="utf-8")

    proc = _run_source_ccb(
        ["config", "validate"],
        cwd=project,
        extra_env={"CCB_SOURCE_ALLOWED_ROOTS": str(allowed)},
    )

    assert proc.returncode == 0
    assert "config_status: valid" in proc.stdout


def test_source_ccb_allows_project_arg_under_configured_test_root_from_source_cwd(tmp_path: Path) -> None:
    allowed = tmp_path / "test-project"
    project = allowed / "repo"
    project.mkdir(parents=True)
    (project / ".ccb").mkdir()
    (project / ".ccb" / "ccb.config").write_text("cmd; agent1:codex\n", encoding="utf-8")

    proc = _run_source_ccb(
        ["--project", str(project), "config", "validate"],
        cwd=REPO_ROOT,
        extra_env={"CCB_SOURCE_ALLOWED_ROOTS": str(allowed)},
    )

    assert proc.returncode == 0
    assert "config_status: valid" in proc.stdout


def test_source_ccb_explicit_override_allows_one_off_run(tmp_path: Path) -> None:
    project = tmp_path / "repo"
    project.mkdir()
    (project / ".ccb").mkdir()
    (project / ".ccb" / "ccb.config").write_text("cmd; agent1:codex\n", encoding="utf-8")

    proc = _run_source_ccb(["config", "validate"], cwd=project, extra_env={"CCB_SOURCE_RUNTIME_OK": "1"})

    assert proc.returncode == 0
    assert "config_status: valid" in proc.stdout


def test_ccb_test_rejects_source_checkout_cwd() -> None:
    proc = _run_ccb_test(["doctor"], cwd=REPO_ROOT)

    assert proc.returncode == 1
    assert "Refusing to run `ccb_test` from the CCB source checkout" in proc.stderr
    assert "cd /home/bfly/yunwei/test_ccb2 && /home/bfly/yunwei/ccb_source/ccb_test config validate" in proc.stderr


def test_ccb_test_rejects_external_project_without_allowed_root(tmp_path: Path) -> None:
    project = tmp_path / "repo"
    project.mkdir()
    (project / ".ccb").mkdir()
    (project / ".ccb" / "ccb.config").write_text("cmd; agent1:codex\n", encoding="utf-8")

    proc = _run_ccb_test(["config", "validate"], cwd=project)

    assert proc.returncode == 1
    assert "Refusing to run `ccb_test` outside an allowed source-test project" in proc.stderr
    assert f"Allowed source-test roots: {REPO_ROOT.parent / 'test_ccb2'}" in proc.stderr


def test_ccb_test_allows_external_project_with_explicit_test_root(tmp_path: Path) -> None:
    project = tmp_path / "repo"
    project.mkdir()
    (project / ".ccb").mkdir()
    (project / ".ccb" / "ccb.config").write_text("cmd; agent1:codex\n", encoding="utf-8")

    proc = _run_ccb_test(["config", "validate"], cwd=project, extra_env={"CCB_TEST_ROOTS": str(tmp_path)})

    assert proc.returncode == 0
    assert "config_status: valid" in proc.stdout


def test_ccb_test_allows_external_project_with_explicit_source_allowed_root(tmp_path: Path) -> None:
    project = tmp_path / "repo"
    project.mkdir()
    (project / ".ccb").mkdir()
    (project / ".ccb" / "ccb.config").write_text("cmd; agent1:codex\n", encoding="utf-8")

    proc = _run_ccb_test(["config", "validate"], cwd=project, extra_env={"CCB_SOURCE_ALLOWED_ROOTS": str(tmp_path)})

    assert proc.returncode == 0
    assert "config_status: valid" in proc.stdout


def test_ccb_test_rejects_legacy_sibling_project_arg_without_override(tmp_path: Path) -> None:
    external = tmp_path / "external"
    external.mkdir()
    legacy_project = REPO_ROOT.parent / "test_ccb"

    proc = _run_ccb_test(["--project", str(legacy_project), "doctor"], cwd=external)

    assert proc.returncode == 1
    assert "Refusing to run `ccb_test` outside an allowed source-test project" in proc.stderr
    assert f"Checked project path: {legacy_project}" in proc.stderr


def test_ccb_test_allows_project_arg_under_explicit_test_root(tmp_path: Path) -> None:
    allowed = tmp_path / "allowed"
    project = allowed / "repo"
    external = tmp_path / "external"
    project.mkdir(parents=True)
    external.mkdir()
    (project / ".ccb").mkdir()
    (project / ".ccb" / "ccb.config").write_text("cmd; agent1:codex\n", encoding="utf-8")

    proc = _run_ccb_test(
        ["--project", str(project), "config", "validate"],
        cwd=external,
        extra_env={"CCB_TEST_ROOTS": str(allowed)},
    )

    assert proc.returncode == 0
    assert "config_status: valid" in proc.stdout


def test_ccb_test_rejects_project_arg_inside_source_checkout(tmp_path: Path) -> None:
    external = tmp_path / "external"
    external.mkdir()

    proc = _run_ccb_test(["--project", str(REPO_ROOT), "doctor"], cwd=external)

    assert proc.returncode == 1
    assert "Refusing to run `ccb_test` against a project inside the CCB source checkout" in proc.stderr


def test_ccb_test_diagnose_reports_wrapper_roots_and_allowance(tmp_path: Path) -> None:
    project = tmp_path / "repo"
    project.mkdir()

    proc = _run_ccb_test(["--diagnose"], cwd=project)

    assert proc.returncode == 0
    assert f"wrapper: {CCB_TEST}" in proc.stdout
    assert f"source_ccb: {CCB}" in proc.stdout
    assert f"cwd: {project}" in proc.stdout
    assert f"default_roots: {REPO_ROOT.parent / 'test_ccb2'}" in proc.stdout
    assert f"effective_roots: {REPO_ROOT.parent / 'test_ccb2'}" in proc.stdout
    assert "allowed_source_test_project: no" in proc.stdout


def test_ccb_test_diagnose_reports_explicit_allowed_root(tmp_path: Path) -> None:
    project = tmp_path / "repo"
    project.mkdir()

    proc = _run_ccb_test(["diagnose"], cwd=project, extra_env={"CCB_TEST_ROOTS": str(tmp_path)})

    assert proc.returncode == 0
    assert f"env_CCB_TEST_ROOTS: {tmp_path}" in proc.stdout
    assert f"checked_paths: {project}" in proc.stdout
    assert "allowed_source_test_project: yes" in proc.stdout
