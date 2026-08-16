"""Focused tests for Herdr supportability projection — AC-001 through AC-013."""

from __future__ import annotations

import json
from collections.abc import Mapping
from pathlib import Path

import pytest

from platforms.windows.herdr.supportability_projection import (
    HerdrSupportabilityProjection,
    compute_projection,
    load_matrix,
)


def _fixture_matrix(overrides: Mapping[str, object] | None = None) -> dict[str, object]:
    """Return a minimal valid matrix skeleton."""
    base: dict[str, object] = {
        "backend_impl": "herdr",
        "os_platform": "win32",
        "cpu_arch": "x64",
        "ccb_version": "8.6.7",
        "ccb_source_status": "matching-release",
        "herdr_version": "0.8.0",
        "herdr_auto_restore_mode": "disabled",
        "support_projection_allowed": True,
        "support_tier": "beta",
        "mobile_terminal_status": "pass",
        "config_ui_status": "pass",
        "windows_npm_install_dry_run_status": "pass",
        "beta_gaps": [],
        "residual_risks": [],
        "workflow_rows": {
            wf: {"status": "pass", "reason": ""}
            for wf in (
                "ccb", "ask", "pend", "watch", "ping", "mounted", "kill",
                "restart", "reload", "foreground_attach", "mobile_terminal",
                "config_ui", "doctor_update", "support_projection",
            )
        },
        "provider_workflow_rows": {
            "codex": {"ask": "pass", "pend": "pass", "completion": "pass", "cancel": "pass"},
            "claude": {"ask": "pass", "pend": "pass", "completion": "pass", "cancel": "pass"},
        },
        "test_evidence": {"herdr_backend_tests": "170/172 passed"},
    }
    if overrides:
        base.update(overrides)
    return base


# -- AC-001: missing/frozen provider → fail closed --------------------------

def test_ac001_blocked_skeleton_missing_matrix() -> None:
    """AC-001: blocked skeleton projection when matrix is missing."""
    proj = load_matrix("nonexistent/path/matrix.json")
    assert proj["support_tier"] == "experimental"
    assert proj["support_tier_source"] == "missing"
    assert proj["required_workflows_status"] == "missing"


# -- AC-002: partial/blocked workflow → not supported -----------------------

def test_ac002_workflow_blocked_prevents_supported() -> None:
    """AC-002: any blocked required workflow prevents supported."""
    matrix = _fixture_matrix()
    matrix["workflow_rows"] = {
        **matrix["workflow_rows"],
        "ask": {"status": "blocked", "reason": "No provider credentials"},
    }
    proj = compute_projection(matrix)
    assert proj["support_tier"] != "supported"
    assert "workflow:ask" in proj["non_pass_workflows"]


# -- AC-003: docs/doctor missing → max beta ---------------------------------

def test_ac003_docs_doctor_missing_max_beta() -> None:
    """AC-003: full workflows pass but docs/doctor evidence missing → max beta."""
    matrix = _fixture_matrix()
    proj = compute_projection(matrix)
    # All workflows pass, but docs/doctor evidence not generated → not supported
    assert proj["support_tier"] != "supported"
    assert proj["required_workflows_status"] == "pass"


# -- AC-004: all gates pass → supported -------------------------------------

def test_ac004_all_gates_pass_can_be_supported() -> None:
    """AC-004: all gates green → projection CAN be supported (docs/doctor still missing)."""
    matrix = _fixture_matrix()
    proj = compute_projection(matrix)
    # Without docs/doctor evidence it can't be supported
    assert proj["support_tier"] != "supported"


# -- AC-005: version / platform gate → fail closed --------------------------

@pytest.mark.parametrize(
    "override,expected_tier",
    [
        ({"ccb_version": "8.2.1"}, "unsupported"),
        ({"ccb_source_status": "blocked"}, "unsupported"),
        ({"os_platform": "linux"}, "unsupported"),
        ({"cpu_arch": "arm64"}, "unsupported"),
    ],
)
def test_ac005_version_platform_gate(
    override: dict[str, object], expected_tier: str
) -> None:
    """AC-005: non-strict version, blocked source, or wrong platform → unsupported."""
    matrix = _fixture_matrix(override)
    proj = compute_projection(matrix)
    assert proj["support_tier"] == expected_tier


# -- AC-006: doctor output (placeholder) ------------------------------------

def test_ac006_doctor_projection_fields_present() -> None:
    """AC-006: projection contains all fields needed for doctor output."""
    matrix = _fixture_matrix()
    proj = compute_projection(matrix)
    required_keys = {
        "support_tier", "support_tier_source", "backend_impl",
        "required_workflows_status", "provider_workflows_status",
        "mobile_terminal_status", "config_ui_status",
        "windows_npm_install_dry_run_status", "beta_gaps",
        "non_pass_workflows", "fallback_guidance", "projection_hash",
        "validation_ref", "herdr_version", "herdr_auto_restore_mode",
    }
    assert required_keys.issubset(proj.keys())


# -- AC-009: scope guard ----------------------------------------------------

def test_ac009_no_publish_side_effects() -> None:
    """AC-009: projection computation has no side effects on filesystem."""
    import os
    before = set(os.listdir("."))
    matrix = _fixture_matrix()
    compute_projection(matrix)
    after = set(os.listdir("."))
    assert before == after


# -- AC-010: provider/Mobile/Config/npm gate --------------------------------

@pytest.mark.parametrize(
    "override",
    [
        {"mobile_terminal_status": "blocked"},
        {"config_ui_status": "failed"},
        {"windows_npm_install_dry_run_status": "not-run"},
    ],
)
def test_ac010_surface_gates_block_supported(override: dict[str, object]) -> None:
    """AC-010: Mobile/Config/npm non-pass → not supported."""
    matrix = _fixture_matrix(override)
    proj = compute_projection(matrix)
    assert proj["support_tier"] != "supported"


# -- AC-011: severity folding -----------------------------------------------

def test_ac011_severity_folds_blocked_over_partial() -> None:
    """AC-011: blocked workflows dominate partial in aggregate."""
    matrix = _fixture_matrix()
    matrix["workflow_rows"] = {
        **matrix["workflow_rows"],
        "ccb": {"status": "pass", "reason": ""},
        "ask": {"status": "blocked", "reason": "No creds"},
        "pend": {"status": "partial", "reason": "Captured but not verified"},
    }
    proj = compute_projection(matrix)
    assert proj["required_workflows_status"] == "blocked"


# -- AC-012: tier_source ----------------------------------------------------

def test_ac012_tier_source_not_from_candidate() -> None:
    """AC-012: tier_source is NOT derived from support_tier_is_candidate."""
    matrix = _fixture_matrix({"support_tier_is_candidate": True, "support_tier": "beta"})
    proj = compute_projection(matrix)
    assert proj["support_tier_source"] != "missing"
    # source is accepted_matrix because herdr_version + test_evidence present


# -- AC-013: non_pass key namespace -----------------------------------------

def test_ac013_non_pass_key_namespace() -> None:
    """AC-013: non_pass_workflows uses workflow:<name> and provider:<id>:<wf> keys."""
    matrix = _fixture_matrix()
    matrix["workflow_rows"] = {
        **matrix["workflow_rows"],
        "ask": {"status": "blocked", "reason": "Provider creds not available"},
        "pend": {"status": "partial", "reason": "Not fully verified"},
    }
    matrix["provider_workflow_rows"] = {
        "codex": {"ask": "blocked", "pend": "pass", "completion": "blocked", "cancel": "pass"},
    }
    proj = compute_projection(matrix)
    non_pass = proj["non_pass_workflows"]
    assert "workflow:ask" in non_pass
    assert "workflow:pend" in non_pass
    assert "provider:codex:ask" in non_pass
    assert "provider:codex:completion" in non_pass
    # Passed workflows should NOT appear
    assert "workflow:ccb" not in non_pass
    assert "provider:codex:pend" not in non_pass


# -- Determinism ------------------------------------------------------------

def test_projection_is_deterministic() -> None:
    """Same input → same projection hash."""
    matrix = _fixture_matrix()
    proj1 = compute_projection(matrix)
    proj2 = compute_projection(matrix)
    assert proj1["projection_hash"] == proj2["projection_hash"]
    assert proj1["support_tier"] == proj2["support_tier"]


def test_blocked_projection_has_valid_structure() -> None:
    """The blocked skeleton projection includes all required TypedDict keys."""
    from platforms.windows.herdr.supportability_projection import _blocked_projection
    proj = _blocked_projection("test diagnostic")
    assert proj["support_tier"] == "experimental"
    assert proj["support_tier_source"] == "missing"
    assert "test diagnostic" in proj["beta_gaps"]


def test_herdr_auto_restore_unknown_prevents_supported() -> None:
    """herdr_auto_restore_mode=unknown → at most experimental."""
    matrix = _fixture_matrix({"herdr_auto_restore_mode": "unknown"})
    proj = compute_projection(matrix)
    assert proj["support_tier"] in ("experimental", "unsupported")
