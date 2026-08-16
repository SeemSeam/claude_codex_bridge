from __future__ import annotations

import json
from pathlib import Path

from platforms.windows.release.surface import (
    PROJECTION_RELATIVE_PATH,
    assert_windows_x64_release_surface_projection_fresh,
    canonical_projection_json,
    load_windows_x64_release_surface_projection,
    windows_beta_projection,
)


WINDOWS_X64 = {"os_platform": "win32", "cpu_arch": "x64", "wow64": False}


def _write_projection(root: Path, projection: dict[str, object]) -> Path:
    path = root / PROJECTION_RELATIVE_PATH
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(canonical_projection_json(projection), encoding="utf-8")
    return path


def test_missing_projection_fails_closed(tmp_path: Path) -> None:
    projection = load_windows_x64_release_surface_projection(tmp_path, WINDOWS_X64)

    assert projection["projection_source"] == "default_blocked"
    assert projection["surface_state"] == "blocked"
    assert projection["failure_reason"] == "release-artifact-missing"
    assert projection["baseline_version_status"] == "unknown"
    assert projection["windows_npm_enabled"] is False


def test_beta_projection_admits_native_windows_x64_as_degraded(tmp_path: Path) -> None:
    _write_projection(tmp_path, windows_beta_projection("8.6.7"))

    projection = load_windows_x64_release_surface_projection(tmp_path, WINDOWS_X64)

    assert projection["surface_state"] == "degraded"
    assert projection["artifact_status"] == "ready"
    assert projection["managed_python_status"] == "degraded"
    assert projection["release_install_entry"] == "install_ps1"
    assert projection["update_entry"] == "diagnostic_only"
    assert projection["windows_executable_entry"] == "bin/ccb.exe"
    assert projection["windows_npm_enabled"] is False


def test_host_gate_blocks_non_x64_projection(tmp_path: Path) -> None:
    _write_projection(tmp_path, windows_beta_projection("8.6.7"))

    projection = load_windows_x64_release_surface_projection(
        tmp_path,
        {"os_platform": "win32", "cpu_arch": "arm64", "wow64": False},
    )

    assert projection["surface_state"] == "blocked"
    assert projection["failure_reason"] == "not-x64"
    assert projection["release_install_entry"] == "diagnostic_only"


def test_malformed_projection_fails_closed(tmp_path: Path) -> None:
    path = tmp_path / PROJECTION_RELATIVE_PATH
    path.parent.mkdir(parents=True)
    path.write_text("{not json", encoding="utf-8")

    projection = load_windows_x64_release_surface_projection(tmp_path, WINDOWS_X64)

    assert projection["surface_state"] == "blocked"
    assert projection["failure_reason"] == "projection-schema-invalid"


def test_projection_with_missing_required_field_fails_closed(tmp_path: Path) -> None:
    projection = windows_beta_projection("8.6.7")
    del projection["archive_name"]
    _write_projection(tmp_path, projection)

    loaded = load_windows_x64_release_surface_projection(tmp_path, WINDOWS_X64)

    assert loaded["surface_state"] == "blocked"
    assert loaded["failure_reason"] == "projection-schema-invalid"


def test_packaged_projection_matches_release_version() -> None:
    assert_windows_x64_release_surface_projection_fresh(Path.cwd())


def test_projection_freshness_rejects_stale_version(tmp_path: Path) -> None:
    (tmp_path / "VERSION").write_text("8.6.7-beta.1\n", encoding="utf-8")
    _write_projection(tmp_path, windows_beta_projection("8.6.7"))

    try:
        assert_windows_x64_release_surface_projection_fresh(tmp_path)
    except ValueError as exc:
        assert "not fresh" in str(exc)
    else:
        raise AssertionError("stale packaged projection was accepted")


def test_npm_package_remains_unix_only_for_windows_beta() -> None:
    manifest = json.loads(Path("package.json").read_text(encoding="utf-8"))

    assert manifest["os"] == ["linux", "darwin"]
    assert "win32" not in manifest["os"]
    assert PROJECTION_RELATIVE_PATH.as_posix() not in manifest["files"]
