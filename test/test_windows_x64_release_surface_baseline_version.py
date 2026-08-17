from __future__ import annotations

import json
from pathlib import Path

from platforms.windows.release.surface import windows_x64_release_surface_baseline_version_admission


def test_current_workspace_versions_match() -> None:
    admission = windows_x64_release_surface_baseline_version_admission(Path.cwd())
    expected_version = Path("VERSION").read_text(encoding="utf-8").strip()

    assert admission["status"] == "ready"
    assert admission["baseline_version_status"] == "matching"
    assert admission["version"] == expected_version


def test_mismatched_version_blocks_release_route(tmp_path: Path) -> None:
    (tmp_path / "VERSION").write_text("8.6.6-beta.1\n", encoding="utf-8")
    (tmp_path / "package.json").write_text(
        json.dumps({"version": "8.6.6"}), encoding="utf-8"
    )

    admission = windows_x64_release_surface_baseline_version_admission(tmp_path)

    assert admission["status"] == "blocked"
    assert admission["baseline_version_status"] == "mismatch"
    assert admission["implementation_admission"] == "blocked_baseline_mismatch"
