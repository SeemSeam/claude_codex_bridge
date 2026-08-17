from __future__ import annotations

import importlib.util
from pathlib import Path
import subprocess
import sys


REPO_ROOT = Path(__file__).resolve().parents[1]
CHECKER_PATH = REPO_ROOT / "platforms" / "windows" / "tools" / "check_pr_isolation.py"


def _load_checker():
    spec = importlib.util.spec_from_file_location("check_windows_pr_isolation", CHECKER_PATH)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_windows_only_diff_stays_inside_frozen_ownership() -> None:
    checker = _load_checker()

    report = checker.evaluate_isolation(
        [
            "lib/platforms/windows/herdr/runtime/client.py",
            "platforms/windows/installer/install.ps1",
            "test/test_herdr_backend_client.py",
            "docs/plantree/plans/windows-native-release/roadmap.md",
        ]
    )

    assert report.windows_scoped is True
    assert report.protected_paths == ()
    assert report.passed is True


def test_windows_diff_rejects_shared_unix_macos_npm_and_mobile_surfaces() -> None:
    checker = _load_checker()
    protected = {
        "lib/workspace/reconcile.py",
        "test/test_v2_workspace_manager.py",
        "scripts/build_linux_release.py",
        "scripts/build_macos_release.py",
        ".github/workflows/release-artifacts.yml",
        ".github/workflows/npm-publish.yml",
        "install.sh",
        "package.json",
        "mobile/app/lib/app/ccb_mobile_app.dart",
    }

    report = checker.evaluate_isolation(
        ["lib/platforms/windows/herdr/backend.py", *sorted(protected)]
    )

    assert report.windows_scoped is True
    assert set(report.protected_paths) == protected
    assert report.passed is False


def test_windows_intent_in_commit_subject_catches_posix_only_diff() -> None:
    checker = _load_checker()

    report = checker.evaluate_isolation(
        [
            "lib/cli/kill_runtime/zombies.py",
            "lib/runtime_accelerator/ownership.py",
        ],
        commit_subjects=("fix: handle non-UTF-8 subprocess output on Windows",),
    )

    assert report.windows_scoped is True
    assert report.protected_paths == (
        "lib/cli/kill_runtime/zombies.py",
        "lib/runtime_accelerator/ownership.py",
    )
    assert report.passed is False


def test_winerror_diff_marker_catches_platform_fix_hidden_in_shared_cleanup() -> None:
    checker = _load_checker()

    report = checker.evaluate_isolation(
        ["lib/workspace/reconcile.py"],
        patch_text="+    if int(getattr(exc, 'winerror', 0) or 0) == 32:\n+        return True\n",
    )

    assert report.windows_scoped is True
    assert report.scope_evidence == ("diff-marker:win32",)
    assert report.protected_paths == ("lib/workspace/reconcile.py",)


def test_windows_diff_rejects_global_release_metadata() -> None:
    checker = _load_checker()
    protected = {
        "docs/releases/v8.6.9.md",
        "CHANGELOG.md",
        "README.md",
        "README/zh.md",
        "VERSION",
    }

    report = checker.evaluate_isolation(
        [
            "lib/platforms/windows/release/projection.json",
            *sorted(protected),
        ]
    )

    assert report.windows_scoped is True
    assert set(report.protected_paths) == protected
    assert report.passed is False


def test_platform_system_windows_branch_is_rejected_in_shared_code() -> None:
    checker = _load_checker()

    report = checker.evaluate_isolation(
        ["lib/workspace/reconcile.py"],
        patch_text='+    if platform.system() == "Windows":\n+        retain_state()\n',
    )

    assert report.windows_scoped is True
    assert "diff-marker:windows" in report.scope_evidence
    assert "diff-marker:windows-platform-check" in report.scope_evidence
    assert report.protected_paths == ("lib/workspace/reconcile.py",)


def test_new_shared_import_of_windows_implementation_is_rejected() -> None:
    checker = _load_checker()
    new_import = (
        "lib/provider_backends/claude/launcher_runtime/service.py",
        "platforms.windows.herdr.runtime.client",
    )

    report = checker.evaluate_isolation(
        [],
        shared_windows_imports=frozenset({new_import}),
    )

    assert report.windows_scoped is False
    assert report.new_shared_windows_imports == (new_import,)
    assert report.passed is False


def test_current_shared_windows_reverse_dependencies_do_not_expand() -> None:
    checker = _load_checker()

    observed = checker.scan_shared_windows_imports(REPO_ROOT)

    assert observed
    assert observed <= checker.ALLOWED_SHARED_WINDOWS_IMPORTS


def test_from_platforms_import_windows_is_a_reverse_dependency(tmp_path: Path) -> None:
    checker = _load_checker()
    shared_module = tmp_path / "lib" / "shared.py"
    shared_module.parent.mkdir(parents=True)
    shared_module.write_text("from platforms import windows\n", encoding="utf-8")

    observed = checker.scan_shared_windows_imports(tmp_path)

    assert observed == frozenset({("lib/shared.py", "platforms.windows")})
    report = checker.evaluate_isolation([], shared_windows_imports=observed)
    assert report.new_shared_windows_imports == (("lib/shared.py", "platforms.windows"),)
    assert report.passed is False


def test_git_diff_paths_use_nul_delimiters(tmp_path: Path) -> None:
    checker = _load_checker()
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-q", str(repo)], check=True)
    subprocess.run(["git", "-C", str(repo), "config", "user.name", "CCB Test"], check=True)
    subprocess.run(
        ["git", "-C", str(repo), "config", "user.email", "ccb-test@example.invalid"],
        check=True,
    )
    windows_file = repo / "lib" / "platforms" / "windows" / "line\nbreak.py"
    windows_file.parent.mkdir(parents=True)
    windows_file.write_text("baseline\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(repo), "add", "."], check=True)
    subprocess.run(["git", "-C", str(repo), "commit", "-qm", "baseline"], check=True)
    base = subprocess.run(
        ["git", "-C", str(repo), "rev-parse", "HEAD"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    windows_file.write_text("changed\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(repo), "add", "."], check=True)
    subprocess.run(["git", "-C", str(repo), "commit", "-qm", "windows change"], check=True)
    head = subprocess.run(
        ["git", "-C", str(repo), "rev-parse", "HEAD"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()

    paths, _, _ = checker.git_diff_evidence(repo, base=base, head=head)

    assert paths == ["lib/platforms/windows/line\nbreak.py"]


def test_unix_release_and_npm_owners_do_not_reference_windows_implementation() -> None:
    for relative in (
        "scripts/build_release.py",
        "scripts/build_linux_release.py",
        "scripts/build_macos_release.py",
        ".github/workflows/release-artifacts.yml",
        ".github/workflows/npm-publish.yml",
        "install.sh",
        "package.json",
    ):
        text = (REPO_ROOT / relative).read_text(encoding="utf-8").lower()
        assert "lib/platforms/windows" not in text, relative
        assert "platforms/windows" not in text, relative
        assert "herdr" not in text, relative


def test_windows_pr_isolation_workflow_uses_trusted_base_policy() -> None:
    text = (REPO_ROOT / ".github/workflows/windows-pr-isolation.yml").read_text(
        encoding="utf-8"
    )

    assert "pull_request_target:" in text
    assert "fetch-depth: 0" in text
    assert "repository: ${{ github.repository }}" in text
    assert "repository: ${{ github.event.pull_request.head.repo.full_name }}" in text
    assert "path: policy" in text
    assert "path: pr" in text
    assert "github.event.pull_request.base.sha" in text
    assert "github.event.pull_request.head.sha" in text
    assert 'checker="${GITHUB_WORKSPACE}/policy/platforms/windows/tools/check_pr_isolation.py"' in text
    assert '--repo-root "${GITHUB_WORKSPACE}/pr"' in text
    assert "python platforms/windows/tools/check_pr_isolation.py" not in text
