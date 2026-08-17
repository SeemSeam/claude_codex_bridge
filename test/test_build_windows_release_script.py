from __future__ import annotations

import importlib.util
import json
from pathlib import Path
import zipfile

import pytest


def _load_module():
    script_path = (
        Path(__file__).resolve().parents[1]
        / "platforms"
        / "windows"
        / "packaging"
        / "build_release.py"
    )
    spec = importlib.util.spec_from_file_location("build_windows_release", script_path)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_windows_release_identity_is_isolated_x64_zip() -> None:
    module = _load_module()

    assert module.ARTIFACT_NAME == "ccb-windows-x86_64.zip"
    assert module.normalize_arch("AMD64") == "x86_64"
    assert module.normalize_arch("x64") == "x86_64"
    assert module.WINDOWS_PLATFORM_DIR.as_posix() == "platforms/windows"
    assert module.WINDOWS_RUNTIME_DIR.as_posix() == "lib/platforms/windows"


def test_default_payload_allowlist_only_names_tracked_source_directories() -> None:
    module = _load_module()
    repo_root = Path(__file__).resolve().parents[1]

    assert "commands" not in module.PAYLOAD_DIRS
    missing = [name for name in module.PAYLOAD_DIRS if not (repo_root / name).is_dir()]
    assert missing == []


def test_builder_rejects_non_windows_or_non_x64_host(monkeypatch) -> None:
    module = _load_module()
    monkeypatch.setattr(module.platform, "system", lambda: "Linux")
    monkeypatch.setattr(module.platform, "machine", lambda: "x86_64")

    with pytest.raises(RuntimeError, match="native Windows x64"):
        module.require_windows_x64_host()


def test_copy_payload_uses_allowlist_and_keeps_unix_builders_out(monkeypatch, tmp_path: Path) -> None:
    module = _load_module()
    export_root = tmp_path / "export"
    artifact_root = tmp_path / "artifact"
    (export_root / "shared").mkdir(parents=True)
    (export_root / "shared" / "runtime.py").write_text("shared\n", encoding="utf-8")
    (export_root / "VERSION").write_text("8.6.6\n", encoding="utf-8")
    windows_root = export_root / "platforms" / "windows"
    for name in ("docs", "installer"):
        (windows_root / name).mkdir(parents=True)
        (windows_root / name / "marker.txt").write_text(name, encoding="utf-8")
    (export_root / "install.sh").write_text("unix\n", encoding="utf-8")
    (export_root / "scripts").mkdir()
    (export_root / "scripts" / "build_linux_release.py").write_text("unix\n", encoding="utf-8")

    monkeypatch.setattr(module, "PAYLOAD_FILES", ("VERSION",))
    monkeypatch.setattr(module, "PAYLOAD_DIRS", ("shared",))
    module.copy_payload(export_root, artifact_root)

    assert (artifact_root / "VERSION").is_file()
    assert (artifact_root / "shared" / "runtime.py").is_file()
    assert (artifact_root / "platforms/windows/installer/marker.txt").is_file()
    assert not (artifact_root / "install.sh").exists()
    assert not (artifact_root / "scripts").exists()


def test_native_launcher_is_projected_to_all_public_windows_commands(monkeypatch, tmp_path: Path) -> None:
    module = _load_module()
    artifact_root = tmp_path / "artifact"
    build_root = tmp_path / "build"

    def fake_cargo_build(_manifest: Path, target_dir: Path, binary_name: str) -> Path:
        binary = target_dir / "release" / f"{binary_name}.exe"
        binary.parent.mkdir(parents=True, exist_ok=True)
        binary.write_bytes(b"MZfake")
        return binary

    monkeypatch.setattr(module, "_cargo_build", fake_cargo_build)
    entries = module.build_native_binaries(artifact_root, build_root)

    for name in ("ccb", "ask", "autonew", "ctx-transfer"):
        assert entries[name] == f"bin/{name}.exe"
        assert (artifact_root / entries[name]).read_bytes().startswith(b"MZ")
    assert entries["ccb-agent-sidebar"] == "bin/ccb-agent-sidebar.exe"
    assert entries["ccb-rs-helper"] == "bin/ccb-rs-helper.exe"


def test_metadata_archive_and_checksum_are_self_consistent(tmp_path: Path) -> None:
    module = _load_module()
    stage_root = tmp_path / "stage"
    artifact_root = stage_root / module.ARTIFACT_BASENAME
    bin_dir = artifact_root / "bin"
    bin_dir.mkdir(parents=True)
    (artifact_root / "VERSION").write_text("8.6.6\n", encoding="utf-8")
    (artifact_root / "install.ps1").write_text("Write-Host install\n", encoding="utf-8")
    entries = {
        "ccb": "bin/ccb.exe",
        "ask": "bin/ask.exe",
        "autonew": "bin/autonew.exe",
        "ctx-transfer": "bin/ctx-transfer.exe",
    }
    for relative in entries.values():
        (artifact_root / relative).write_bytes(b"MZfake")

    module.write_metadata(
        artifact_root,
        version="8.6.6",
        commit="a" * 40,
        commit_date="2026-08-11",
        channel="beta",
        bin_entries=entries,
    )
    archive_path = tmp_path / module.ARTIFACT_NAME
    checksum_path = tmp_path / module.CHECKSUM_NAME
    module.create_zip(stage_root, artifact_root, archive_path)
    digest = module.write_checksum(archive_path, checksum_path)
    module.verify_archive(archive_path, version="8.6.6")

    assert checksum_path.read_text(encoding="utf-8") == f"{digest}  {archive_path.name}\n"
    with zipfile.ZipFile(archive_path) as archive:
        manifest = json.loads(archive.read(f"{module.ARTIFACT_BASENAME}/WINDOWS_MANIFEST.json"))
    assert manifest["support_tier"] == "beta"
    assert manifest["executable_entry"] == "bin/ccb.exe"
    assert manifest["prerequisites"]["python"] == ">=3.10"


def test_windows_release_workflow_handles_stable_and_beta_tags() -> None:
    text = Path(".github/workflows/release-windows.yml").read_text(encoding="utf-8")
    version = Path("VERSION").read_text(encoding="utf-8").strip()

    assert "name: Native Windows Release" in text
    assert f'default: "v{version}"' in text
    assert '- "v*.*.*"' in text
    assert '- "v*-beta.*"' in text
    assert "release_flags=(--latest)" in text
    assert 'if [[ "$TAG_NAME" == *-* ]]; then' in text
    assert "release_flags=(--prerelease)" in text
    assert text.count("--prerelease") == 1
    assert '--title "$title"' in text
