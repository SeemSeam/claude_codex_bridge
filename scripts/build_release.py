#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import io
import json
from pathlib import Path
import platform
import re
import shutil
import subprocess
import sys
import tarfile
from datetime import datetime, timezone


REPO_ROOT = Path(__file__).resolve().parents[1]
LIB_ROOT = REPO_ROOT / "lib"
if str(LIB_ROOT) not in sys.path:
    sys.path.insert(0, str(LIB_ROOT))

from release_artifacts import normalize_arch, release_artifact_basename, release_build_arch


DEFAULT_OUTPUT_DIR = REPO_ROOT / "dist"
EXCLUDES = {
    ".git",
    ".ccb",
    ".ccb-requests",
    ".architec",
    ".claude",
    ".codex",
    ".gemini",
    ".hippocampus",
    ".loop",
    ".tmp_pytest",
    "__pycache__",
    ".pytest_cache",
    ".mypy_cache",
    ".venv",
    "target",
    # Maintainer-only utilities are versioned in git but must not ship in release tarballs.
    "dev_tools",
    "dist",
    # Production roles live in the external agent-roles-spec catalog.
    "roles",
}
_HOST_SYSTEMS = {
    "linux": "Linux",
    "macos": "Darwin",
}


def main_for_target(target_platform: str) -> int:
    args = parse_args(target_platform=target_platform)
    host_system = _expected_host_system(target_platform)
    if platform.system() != host_system:
        raise SystemExit(f"build_{target_platform}_release.py must run on {host_system}")

    output_dir = args.output_dir.resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    channel = args.channel or ("preview" if args.allow_dirty else "stable")

    use_git_ref_source = is_git_checkout(REPO_ROOT) and not args.allow_dirty
    version = resolve_version(REPO_ROOT, git_ref=args.git_ref if use_git_ref_source else None)
    commit, commit_date = resolve_git_metadata(REPO_ROOT, git_ref=args.git_ref if is_git_checkout(REPO_ROOT) else None)
    artifact_basename = release_artifact_basename(target_platform, machine=platform.machine())
    if not artifact_basename:
        raise RuntimeError(
            f"unsupported release target for platform={target_platform!r} machine={platform.machine()!r}"
        )
    stage_root = output_dir / f".stage-{artifact_basename}"
    artifact_root = stage_root / artifact_basename
    artifact_path = output_dir / f"{artifact_basename}.tar.gz"
    sha_path = output_dir / "SHA256SUMS"

    if stage_root.exists():
        shutil.rmtree(stage_root)
    if artifact_path.exists():
        artifact_path.unlink()

    export_release_tree(
        REPO_ROOT,
        artifact_root,
        git_ref=args.git_ref,
        allow_dirty=args.allow_dirty,
        generated_paths=(output_dir, stage_root, artifact_path, sha_path),
    )
    build_sidebar_helper_for_release(artifact_root, target_platform=target_platform)
    patch_ccb_metadata(artifact_root / "ccb.py", version=version, commit=commit, date=commit_date)

    build_info = {
        "version": version,
        "commit": commit,
        "date": commit_date,
        "build_time": utc_now(),
        "platform": target_platform,
        "arch": release_build_arch(target_platform, machine=platform.machine()),
        "channel": channel,
        "source_kind": "preview" if args.allow_dirty else "release",
        "install_mode": "release",
    }
    write_release_metadata(artifact_root, build_info)
    create_tarball(stage_root=stage_root, artifact_root=artifact_root, artifact_path=artifact_path)
    write_sha256(artifact_path=artifact_path, output_path=sha_path)

    print(f"artifact: {artifact_path}")
    print(f"sha256: {sha_path}")
    print(f"version: {version}")
    print(f"commit: {commit}")
    print(f"channel: {channel}")
    print(f"platform: {target_platform}")
    if args.allow_dirty:
        print("warning: built from current dirty worktree for local preview only")
    return 0


def parse_args(*, target_platform: str) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=f"Build a {target_platform} release artifact for ccb")
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    parser.add_argument("--channel")
    parser.add_argument(
        "--git-ref",
        default="HEAD",
        help="git ref to archive when building from a git checkout (default: HEAD)",
    )
    parser.add_argument(
        "--allow-dirty",
        action="store_true",
        help="allow building from the current dirty worktree for local preview only",
    )
    return parser.parse_args()


def _expected_host_system(target_platform: str) -> str:
    expected = _HOST_SYSTEMS.get(str(target_platform or "").strip())
    if not expected:
        raise RuntimeError(f"unsupported release target platform: {target_platform}")
    return expected


def resolve_version(repo_root: Path, *, git_ref: str | None = None) -> str:
    if git_ref and is_git_checkout(repo_root):
        version_text = read_git_file(repo_root, git_ref=git_ref, relative_path="VERSION")
        if version_text.strip():
            return version_text.strip()
        ccb_text = read_git_file(repo_root, git_ref=git_ref, relative_path="ccb.py")
        match = re.search(r'^VERSION\s*=\s*"([^"]+)"', ccb_text, re.MULTILINE)
        if match:
            return match.group(1)
    version_file = repo_root / "VERSION"
    if version_file.exists():
        value = version_file.read_text(encoding="utf-8").strip()
        if value:
            return value
    ccb_path = repo_root / "ccb.py"
    text = ccb_path.read_text(encoding="utf-8", errors="replace")
    match = re.search(r'^VERSION\s*=\s*"([^"]+)"', text, re.MULTILINE)
    if match:
        return match.group(1)
    raise RuntimeError("unable to resolve version from VERSION or ccb.py")


def resolve_git_metadata(repo_root: Path, *, git_ref: str | None = None) -> tuple[str | None, str | None]:
    if not (repo_root / ".git").exists():
        return None, None
    resolved_ref = git_ref or "HEAD"
    commit = run_git(repo_root, ["log", "-1", "--format=%h", resolved_ref])
    commit_date = run_git(repo_root, ["log", "-1", "--format=%cs", resolved_ref])
    return commit or None, commit_date or None


def run_git(repo_root: Path, args: list[str]) -> str:
    result = subprocess.run(
        ["git", "-C", str(repo_root), *args],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    if result.returncode != 0:
        return ""
    return result.stdout.strip()


def read_git_file(repo_root: Path, *, git_ref: str, relative_path: str) -> str:
    result = subprocess.run(
        ["git", "-C", str(repo_root), "show", f"{git_ref}:{relative_path}"],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    if result.returncode != 0:
        return ""
    return result.stdout


def generated_relpaths_under_repo(repo_root: Path, paths: tuple[Path, ...] | list[Path] | None = None) -> tuple[Path, ...]:
    repo_root = repo_root.resolve()
    relpaths: set[Path] = set()
    for raw_path in paths or ():
        path = Path(raw_path).resolve()
        try:
            relpath = path.relative_to(repo_root)
        except ValueError:
            continue
        if not relpath.parts:
            continue
        relpaths.add(relpath)
    return tuple(sorted(relpaths, key=lambda item: (len(item.parts), item.as_posix())))


def is_generated_relpath(path: Path, generated_relpaths: tuple[Path, ...]) -> bool:
    return any(path == relpath or relpath in path.parents for relpath in generated_relpaths)


def should_ignore_copy_relpath(path: Path, *, generated_relpaths: tuple[Path, ...]) -> bool:
    if is_excluded_relpath(path):
        return True
    return is_generated_relpath(path, generated_relpaths)


def copy_repo_tree(
    repo_root: Path,
    destination: Path,
    *,
    generated_paths: tuple[Path, ...] | list[Path] | None = None,
) -> None:
    repo_root = repo_root.resolve()
    generated_relpaths = generated_relpaths_under_repo(repo_root, generated_paths)

    def _ignore(current_dir: str, names: list[str]) -> set[str]:
        current_path = Path(current_dir).resolve()
        current_relpath = current_path.relative_to(repo_root)
        ignored: set[str] = set()
        for name in names:
            candidate = current_relpath / name if current_relpath.parts else Path(name)
            if should_ignore_copy_relpath(candidate, generated_relpaths=generated_relpaths):
                ignored.add(name)
        return ignored

    shutil.copytree(repo_root, destination, ignore=_ignore)
    prune_excluded_paths(destination)


def export_release_tree(
    repo_root: Path,
    destination: Path,
    *,
    git_ref: str,
    allow_dirty: bool,
    generated_paths: tuple[Path, ...] | list[Path] | None = None,
) -> None:
    if is_git_checkout(repo_root):
        if allow_dirty:
            copy_repo_tree(repo_root, destination, generated_paths=generated_paths)
            return
        ensure_clean_worktree(repo_root)
        export_git_archive(repo_root, destination, git_ref=git_ref)
        return
    copy_repo_tree(repo_root, destination, generated_paths=generated_paths)


def build_sidebar_helper_for_release(artifact_root: Path, *, target_platform: str = "linux") -> None:
    crate_dir = artifact_root / "tools" / "ccb-agent-sidebar"
    output_bin = artifact_root / "bin" / "ccb-agent-sidebar"
    if not (crate_dir / "Cargo.toml").is_file():
        return

    if target_platform == "macos":
        build_macos_universal_sidebar_helper(artifact_root=artifact_root, crate_dir=crate_dir, output_bin=output_bin)
    else:
        build_native_sidebar_helper(artifact_root=artifact_root, crate_dir=crate_dir, output_bin=output_bin)

    shutil.rmtree(crate_dir / "target", ignore_errors=True)


def build_native_sidebar_helper(*, artifact_root: Path, crate_dir: Path, output_bin: Path) -> None:
    source_bin = crate_dir / "target" / "release" / "ccb-agent-sidebar"
    run_sidebar_cargo_build(artifact_root=artifact_root, crate_dir=crate_dir, target=None)
    if not source_bin.is_file():
        raise RuntimeError(f"sidebar build did not produce expected binary: {source_bin}")
    output_bin.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source_bin, output_bin)
    output_bin.chmod(0o755)


def build_macos_universal_sidebar_helper(*, artifact_root: Path, crate_dir: Path, output_bin: Path) -> None:
    target_bins: list[Path] = []
    for target in ("x86_64-apple-darwin", "aarch64-apple-darwin"):
        run_sidebar_cargo_build(artifact_root=artifact_root, crate_dir=crate_dir, target=target)
        target_bin = crate_dir / "target" / target / "release" / "ccb-agent-sidebar"
        if not target_bin.is_file():
            raise RuntimeError(f"sidebar build did not produce expected {target} binary: {target_bin}")
        target_bins.append(target_bin)

    output_bin.parent.mkdir(parents=True, exist_ok=True)
    result = subprocess.run(
        ["lipo", "-create", "-output", str(output_bin), *(str(path) for path in target_bins)],
        cwd=artifact_root,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    if result.returncode != 0:
        details = (result.stderr or result.stdout or "").strip()
        raise RuntimeError(f"failed to create macOS universal ccb-agent-sidebar: {details or result.returncode}")
    output_bin.chmod(0o755)
    verify_macos_universal_sidebar_binary(output_bin)


def run_sidebar_cargo_build(*, artifact_root: Path, crate_dir: Path, target: str | None) -> None:
    command = ["cargo", "build", "--release", "--manifest-path", str(crate_dir / "Cargo.toml")]
    if target:
        command.extend(["--target", target])
    result = subprocess.run(
        command,
        cwd=artifact_root,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    if result.returncode != 0:
        details = (result.stderr or result.stdout or "").strip()
        suffix = f" for target {target}" if target else ""
        raise RuntimeError(f"failed to build ccb-agent-sidebar{suffix} for release: {details or result.returncode}")


def verify_macos_universal_sidebar_binary(output_bin: Path) -> None:
    result = subprocess.run(
        ["file", str(output_bin)],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    if result.returncode != 0:
        details = (result.stderr or result.stdout or "").strip()
        raise RuntimeError(f"failed to inspect macOS ccb-agent-sidebar binary: {details or result.returncode}")
    description = result.stdout.strip()
    if "universal binary" not in description:
        raise RuntimeError(f"macOS ccb-agent-sidebar is not a universal binary: {description}")


def is_git_checkout(repo_root: Path) -> bool:
    return (repo_root / ".git").exists()


def ensure_clean_worktree(repo_root: Path) -> None:
    entries = dirty_worktree_entries(repo_root)
    if not entries:
        return
    preview = "\n".join(f"  {entry}" for entry in entries[:20])
    remaining = len(entries) - min(len(entries), 20)
    if remaining > 0:
        preview += f"\n  ... and {remaining} more"
    raise RuntimeError(
        "refusing to build release from a dirty worktree.\n"
        "Commit or stash changes first, or pass --allow-dirty for a local preview build.\n"
        f"Dirty entries:\n{preview}"
    )


def dirty_worktree_entries(repo_root: Path) -> tuple[str, ...]:
    result = subprocess.run(
        ["git", "-C", str(repo_root), "status", "--porcelain", "--untracked-files=all"],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    if result.returncode != 0:
        raise RuntimeError(f"git status failed: {result.stderr.strip() or result.stdout.strip() or result.returncode}")
    return tuple(
        line.rstrip()
        for line in result.stdout.splitlines()
        if line.strip() and not is_excluded_status_entry(line)
    )


def is_excluded_status_entry(line: str) -> bool:
    text = str(line or "").rstrip()
    if len(text) < 4:
        return False
    payload = text[3:].strip()
    if not payload:
        return False
    candidates = [part.strip() for part in payload.split("->")]
    return all(is_excluded_relpath(candidate) for candidate in candidates if candidate)


def is_excluded_relpath(value: str) -> bool:
    path = Path(str(value or "").strip())
    return any(is_excluded_part(part) for part in path.parts)


def is_excluded_part(part: str) -> bool:
    text = str(part or "").strip()
    if not text:
        return False
    if text in EXCLUDES:
        return True
    return text.startswith(".tmp_test_env_")


def export_git_archive(repo_root: Path, destination: Path, *, git_ref: str) -> None:
    result = subprocess.run(
        ["git", "-C", str(repo_root), "archive", "--format=tar", git_ref],
        capture_output=True,
    )
    if result.returncode != 0:
        stderr = result.stderr.decode("utf-8", errors="replace").strip()
        raise RuntimeError(f"git archive failed for {git_ref}: {stderr or result.returncode}")
    destination.mkdir(parents=True, exist_ok=True)
    with tarfile.open(fileobj=io.BytesIO(result.stdout), mode="r:") as tar:
        tar.extractall(destination)
    prune_excluded_paths(destination)


def prune_excluded_paths(root: Path) -> None:
    for path in sorted(root.rglob("*"), key=lambda item: len(item.parts), reverse=True):
        if not is_excluded_part(path.name):
            continue
        if path.is_dir():
            shutil.rmtree(path, ignore_errors=True)
        else:
            path.unlink(missing_ok=True)


def patch_ccb_metadata(ccb_path: Path, *, version: str, commit: str | None, date: str | None) -> None:
    text = ccb_path.read_text(encoding="utf-8", errors="replace")
    text = re.sub(r'^VERSION\s*=\s*"[^"]*"', f'VERSION = "{version}"', text, flags=re.MULTILINE)
    if commit:
        text = re.sub(r'^GIT_COMMIT\s*=\s*"[^"]*"', f'GIT_COMMIT = "{commit}"', text, flags=re.MULTILINE)
    if date:
        text = re.sub(r'^GIT_DATE\s*=\s*"[^"]*"', f'GIT_DATE = "{date}"', text, flags=re.MULTILINE)
    ccb_path.write_text(text, encoding="utf-8")


def write_release_metadata(artifact_root: Path, build_info: dict[str, str | None]) -> None:
    version_text = str(build_info.get("version") or "").strip()
    if version_text:
        (artifact_root / "VERSION").write_text(version_text + "\n", encoding="utf-8")
    (artifact_root / "BUILD_INFO.json").write_text(
        json.dumps(build_info, ensure_ascii=True, indent=2) + "\n",
        encoding="utf-8",
    )


def create_tarball(*, stage_root: Path, artifact_root: Path, artifact_path: Path) -> None:
    legacy_alias = stage_root / artifact_path.name
    legacy_alias.unlink(missing_ok=True)
    legacy_alias.symlink_to(artifact_root.name)
    with tarfile.open(artifact_path, "w:gz") as tar:
        tar.add(artifact_root, arcname=artifact_root.name)
        tar.add(legacy_alias, arcname=legacy_alias.name)
    shutil.rmtree(stage_root, ignore_errors=True)


def write_sha256(*, artifact_path: Path, output_path: Path) -> None:
    digest = hashlib.sha256(artifact_path.read_bytes()).hexdigest()
    output_path.write_text(f"{digest}  {artifact_path.name}\n", encoding="utf-8")


def utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


__all__ = [
    "DEFAULT_OUTPUT_DIR",
    "EXCLUDES",
    "build_sidebar_helper_for_release",
    "copy_repo_tree",
    "create_tarball",
    "dirty_worktree_entries",
    "ensure_clean_worktree",
    "export_git_archive",
    "export_release_tree",
    "generated_relpaths_under_repo",
    "is_git_checkout",
    "is_generated_relpath",
    "main_for_target",
    "normalize_arch",
    "parse_args",
    "patch_ccb_metadata",
    "prune_excluded_paths",
    "read_git_file",
    "release_artifact_basename",
    "release_build_arch",
    "resolve_git_metadata",
    "resolve_version",
    "run_git",
    "should_ignore_copy_relpath",
    "utc_now",
    "write_release_metadata",
    "write_sha256",
]
