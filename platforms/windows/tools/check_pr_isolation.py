#!/usr/bin/env python3
from __future__ import annotations

import argparse
import ast
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
import re
import subprocess


REPO_ROOT = Path(__file__).resolve().parents[3]

WINDOWS_OWNED_PREFIXES = (
    "lib/platforms/windows/",
    "platforms/windows/",
    "docs/plantree/plans/windows-native-release/",
    "docs/plantree/plans/windows-wezterm-native/",
)
WINDOWS_OWNED_FILES = frozenset(
    {
        ".github/workflows/release-windows.yml",
        ".github/workflows/windows-pr-isolation.yml",
        "docs/ccbd-windows-psmux-plan.md",
        "install.cmd",
        "install.ps1",
        "scripts/bootstrap-windows-test-env.ps1",
    }
)
WINDOWS_TEST_NAME_MARKERS = ("windows", "herdr", "rmux", "wezterm")

WINDOWS_DIFF_MARKERS = {
    "windows": re.compile(r"\bwindows\b", re.IGNORECASE),
    "herdr": re.compile(r"\bherdr\b", re.IGNORECASE),
    "native-windows": re.compile(r"\bnative[ _-]+windows\b", re.IGNORECASE),
    "platforms/windows": re.compile(r"platforms[/\\]windows", re.IGNORECASE),
    "powershell": re.compile(r"\b(?:powershell|pwsh)\b", re.IGNORECASE),
    "ps1": re.compile(r"\.ps1\b", re.IGNORECASE),
    "wezterm": re.compile(r"\bwezterm\b", re.IGNORECASE),
    "win32": re.compile(r"\b(?:win32|winerror|winreg|msvcrt|pywin32)\b", re.IGNORECASE),
    "windows-api": re.compile(
        r"\b(?:CreateProcess(?:W|A)?|STARTUPINFO(?:EX)?|STARTF_[A-Z_]+|"
        r"DETACHED_PROCESS|CREATE_NEW_PROCESS_GROUP|windll|WinDLL|WINFUNCTYPE)\b",
        re.IGNORECASE,
    ),
    "windows-platform-check": re.compile(
        r"(?:\bos\.name\s*==\s*['\"]nt['\"]|\bsys\.platform\s*==\s*['\"]win32['\"]|"
        r"\bplatform\.system\s*\(\)|\bsys\.getwindowsversion\s*\(\))",
        re.IGNORECASE,
    ),
}
WINDOWS_SUBJECT_MARKER = re.compile(
    r"\b(?:windows|win32|herdr|wezterm|powershell|pwsh)\b",
    re.IGNORECASE,
)

# This is existing architectural debt, not permission to add more. The scan
# permits removal of these imports but rejects every new importer or module.
ALLOWED_SHARED_WINDOWS_IMPORTS = frozenset(
    {
        ("lib/agents/config_loader_runtime/parsing_runtime/topology.py", "platforms.windows.os_platform"),
        ("lib/ccbd/control_plane_transport/factory.py", "platforms.windows.control_plane.tcp"),
        ("lib/ccbd/handlers/ping_runtime/payloads.py", "platforms.windows.herdr.ccbd_surface_projection"),
        ("lib/ccbd/project_view/service.py", "platforms.windows.herdr.ccbd_surface_projection"),
        ("lib/ccbd/socket_server_runtime/server.py", "platforms.windows.control_plane.tcp"),
        ("lib/cli/management_runtime/commands_runtime/update.py", "platforms.windows.release.surface"),
        ("lib/cli/phase2_runtime/handlers_start.py", "platforms.windows.herdr.bootstrap"),
        ("lib/cli/phase2_runtime/handlers_start.py", "platforms.windows.herdr.config_import"),
        ("lib/cli/phase2_runtime/handlers_start.py", "platforms.windows.herdr.runtime.capabilities"),
        ("lib/cli/services/config_ui.py", "platforms.windows.herdr.ccbd_surface_projection"),
        ("lib/cli/services/config_ui.py", "platforms.windows.herdr.surface"),
        ("lib/cli/services/doctor.py", "platforms.windows.herdr.supportability_projection"),
        ("lib/cli/services/doctor.py", "platforms.windows.release.surface"),
        ("lib/cli/services/doctor.py", "platforms.windows.release.workflow_matrix"),
        ("lib/cli/services/doctor_runtime/ccbd.py", "platforms.windows.herdr.surface"),
        ("lib/cli/services/layout_status.py", "platforms.windows.herdr.surface"),
        ("lib/cli/services/ps.py", "platforms.windows.herdr.surface"),
        ("lib/mobile_gateway/service.py", "platforms.windows.herdr.ccbd_surface_projection"),
        ("lib/terminal_runtime/api.py", "platforms.windows.herdr.backend"),
        ("lib/terminal_runtime/api.py", "platforms.windows.herdr.runtime.capabilities"),
        ("lib/terminal_runtime/api.py", "platforms.windows.herdr.runtime.cli"),
        ("lib/terminal_runtime/api.py", "platforms.windows.herdr.runtime.client"),
        ("lib/terminal_runtime/backend_env.py", "platforms.windows.os_platform"),
        ("lib/terminal_runtime/backend_resolver.py", "platforms.windows.herdr.runtime.capabilities"),
    }
)


@dataclass(frozen=True)
class IsolationReport:
    windows_scoped: bool
    scope_evidence: tuple[str, ...]
    protected_paths: tuple[str, ...]
    new_shared_windows_imports: tuple[tuple[str, str], ...]

    @property
    def passed(self) -> bool:
        return not self.protected_paths and not self.new_shared_windows_imports


def normalized_repo_path(value: str) -> str:
    raw = str(value or "").replace("\\", "/")
    while raw.startswith("./"):
        raw = raw[2:]
    path = PurePosixPath(raw)
    if not raw or path.is_absolute() or ".." in path.parts:
        raise ValueError(f"invalid repository-relative path: {value!r}")
    return path.as_posix()


def is_windows_owned_path(value: str) -> bool:
    path = normalized_repo_path(value)
    if path in WINDOWS_OWNED_FILES:
        return True
    if path.startswith(WINDOWS_OWNED_PREFIXES):
        return True
    if path.startswith("test/"):
        name = PurePosixPath(path).name.lower()
        return any(marker in name for marker in WINDOWS_TEST_NAME_MARKERS)
    return False


def windows_scope_evidence(
    changed_paths: list[str] | tuple[str, ...],
    *,
    patch_text: str = "",
    commit_subjects: tuple[str, ...] = (),
) -> tuple[str, ...]:
    evidence = {
        f"path:{path}"
        for path in sorted({normalized_repo_path(path) for path in changed_paths})
        if is_windows_owned_path(path)
    }
    content_lines = []
    for line in patch_text.splitlines():
        if not line.startswith(("+", "-")) or line.startswith(("+++", "---")):
            continue
        content_lines.append(line[1:])
    content = "\n".join(content_lines)
    for name, pattern in WINDOWS_DIFF_MARKERS.items():
        if pattern.search(content):
            evidence.add(f"diff-marker:{name}")
    for subject in commit_subjects:
        if WINDOWS_SUBJECT_MARKER.search(subject):
            evidence.add(f"commit-subject:{subject.strip()}")
    return tuple(sorted(evidence))


def scan_shared_windows_imports(repo_root: Path) -> frozenset[tuple[str, str]]:
    imports: set[tuple[str, str]] = set()
    lib_root = repo_root / "lib"
    windows_root = lib_root / "platforms" / "windows"
    if not lib_root.is_dir():
        raise RuntimeError(f"cannot inspect Windows dependency boundary: missing {lib_root}")
    for path in sorted(lib_root.rglob("*.py")):
        if path.is_relative_to(windows_root):
            continue
        relative = path.relative_to(repo_root).as_posix()
        if path.is_symlink():
            raise RuntimeError(f"cannot inspect Windows dependency boundary through symlink: {relative}")
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=relative)
        except (OSError, SyntaxError, UnicodeError) as exc:
            raise RuntimeError(f"cannot inspect Windows dependency boundary in {relative}: {exc}") from exc
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom):
                module = str(node.module or "")
                if module.startswith("platforms.windows"):
                    imports.add((relative, module))
                elif module == "platforms":
                    for alias in node.names:
                        if alias.name == "windows" or alias.name.startswith("windows."):
                            imports.add((relative, f"platforms.{alias.name}"))
            elif isinstance(node, ast.Import):
                for alias in node.names:
                    if alias.name.startswith("platforms.windows"):
                        imports.add((relative, alias.name))
    return frozenset(imports)


def evaluate_isolation(
    changed_paths: list[str] | tuple[str, ...],
    *,
    patch_text: str = "",
    commit_subjects: tuple[str, ...] = (),
    shared_windows_imports: frozenset[tuple[str, str]] | None = None,
) -> IsolationReport:
    paths = tuple(sorted({normalized_repo_path(path) for path in changed_paths}))
    evidence = windows_scope_evidence(
        paths,
        patch_text=patch_text,
        commit_subjects=commit_subjects,
    )
    protected = ()
    if evidence:
        protected = tuple(
            path
            for path in paths
            if not is_windows_owned_path(path)
        )
    observed_imports = shared_windows_imports or frozenset()
    new_imports = tuple(sorted(observed_imports - ALLOWED_SHARED_WINDOWS_IMPORTS))
    return IsolationReport(
        windows_scoped=bool(evidence),
        scope_evidence=evidence,
        protected_paths=protected,
        new_shared_windows_imports=new_imports,
    )


def git_diff_evidence(repo_root: Path, *, base: str, head: str) -> tuple[list[str], str, tuple[str, ...]]:
    diff_range = f"{base}...{head}"
    paths_output = _git(repo_root, "diff", "--name-only", "-z", "--no-renames", diff_range)
    patch_text = _git(repo_root, "diff", "--unified=0", "--no-color", "--no-renames", diff_range)
    subjects_output = _git(repo_root, "log", "--format=%s", f"{base}..{head}")
    paths = [item for item in paths_output.split("\0") if item]
    subjects = tuple(line.strip() for line in subjects_output.splitlines() if line.strip())
    return paths, patch_text, subjects


def _git(repo_root: Path, *args: str) -> str:
    completed = subprocess.run(
        ["git", "-C", str(repo_root), *args],
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="strict",
    )
    if completed.returncode != 0:
        detail = completed.stderr.strip() or completed.stdout.strip()
        raise RuntimeError(f"git {' '.join(args)} failed: {detail}")
    return completed.stdout


def _print_report(report: IsolationReport) -> None:
    if report.passed:
        scope = "windows" if report.windows_scoped else "non-windows"
        print(f"windows_pr_isolation: pass scope={scope}")
        return
    print("windows_pr_isolation: fail")
    if report.scope_evidence:
        print("Windows scope evidence:")
        for item in report.scope_evidence:
            print(f"  - {item}")
    if report.protected_paths:
        print("Protected non-Windows paths changed by the Windows-scoped diff:")
        for path in report.protected_paths:
            print(f"  - {path}")
    if report.new_shared_windows_imports:
        print("New shared-layer imports of Windows implementation modules:")
        for path, module in report.new_shared_windows_imports:
            print(f"  - {path}: {module}")
    print(
        "Move Windows runtime/release logic under lib/platforms/windows or "
        "platforms/windows. Land shared Linux/macOS behavior, global release "
        "metadata, and gate-policy changes in separate reviewed PRs."
    )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Reject Windows-scoped PR diffs that cross Linux/macOS/shared ownership boundaries."
    )
    parser.add_argument("--base", required=True, help="PR base commit SHA or ref")
    parser.add_argument("--head", required=True, help="PR head commit SHA or ref")
    parser.add_argument("--repo-root", type=Path, default=REPO_ROOT)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    repo_root = args.repo_root.resolve()
    paths, patch_text, subjects = git_diff_evidence(
        repo_root,
        base=args.base,
        head=args.head,
    )
    report = evaluate_isolation(
        paths,
        patch_text=patch_text,
        commit_subjects=subjects,
        shared_windows_imports=scan_shared_windows_imports(repo_root),
    )
    _print_report(report)
    return 0 if report.passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
