"""Herdr Native Windows supportability projection — single owner of the support tier.

Consumes the accepted ``WindowsHerdrPublicWorkflowEvidence`` matrix artifact and
produces a deterministic :class:`HerdrSupportabilityProjection` dict.  Every
user-visible surface (doctor, docs, README) must consume this projection — no
hand-written support claims.

Fail-closed by design: missing / partial / blocked evidence → downgraded tier.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from pathlib import Path
from typing import Any, Literal, TypedDict

HerdrSupportTier = Literal["unsupported", "experimental", "beta", "supported"]
HerdrInstallEntry = Literal["npm", "install_ps1", "source", "diagnostic_only"]
TierSource = Literal["accepted_matrix", "blocked_skeleton", "missing"]


class HerdrSupportabilityProjection(TypedDict, total=False):
    support_tier: HerdrSupportTier
    support_tier_source: TierSource
    projection_hash: str | None
    backend_impl: Literal["herdr"]
    os_platform: Literal["win32"]
    cpu_arch: Literal["x64"]
    ccb_version: str | None
    ccb_source_status: Literal["matching-release", "blocked", "unknown"]
    herdr_version: str | None
    herdr_auto_restore_mode: Literal["disabled", "observe-only", "unsupported", "unknown"]
    validation_ref: str | None
    provider_catalog_ref: str | None
    provider_catalog_status: Literal["fresh", "stale", "missing"]
    release_surface_ref: str | None
    release_surface_status: Literal["pass", "blocked", "missing"]
    docs_consistency_ref: str | None
    doctor_render_ref: str | None
    install_entry: HerdrInstallEntry
    windows_npm_enabled: bool
    windows_npm_install_dry_run_status: Literal["pass", "partial", "blocked", "failed", "not-run"]
    required_workflows_status: Literal["pass", "partial", "blocked", "missing"]
    provider_workflows_status: Literal["pass", "partial", "blocked", "missing"]
    mobile_terminal_status: Literal["pass", "partial", "blocked", "failed", "not-run"]
    config_ui_status: Literal["pass", "partial", "blocked", "failed", "not-run"]
    beta_gaps: list[str]
    residual_risks: list[str]
    non_pass_workflows: dict[str, str]
    fallback_guidance: str


_REQUIRED_WORKFLOWS = (
    "ccb", "ask", "pend", "watch", "ping", "mounted", "kill",
    "restart", "reload", "foreground_attach", "mobile_terminal",
    "config_ui", "doctor_update", "support_projection",
)
_PROVIDER_WORKFLOWS = ("ask", "pend", "completion", "cancel")
_WORKFLOW_SEVERITY: dict[str, int] = {
    "missing": 0,
    "blocked": 1,
    "failed": 1,
    "not-run": 1,
    "partial": 2,
    "pass": 3,
}
_PROJECTION_HASH_FIELDS = frozenset(
    {
        "support_tier", "support_tier_source", "backend_impl", "os_platform",
        "cpu_arch", "ccb_version", "ccb_source_status", "herdr_version",
        "herdr_auto_restore_mode", "validation_ref", "provider_catalog_ref",
        "provider_catalog_status", "release_surface_ref", "release_surface_status",
        "install_entry", "windows_npm_enabled", "windows_npm_install_dry_run_status",
        "required_workflows_status", "provider_workflows_status",
        "mobile_terminal_status", "config_ui_status",
        "beta_gaps", "residual_risks", "non_pass_workflows", "fallback_guidance",
    }
)
_EXCLUDED_HASH_FIELDS = frozenset(
    {"projection_hash", "docs_consistency_ref", "doctor_render_ref"}
)


def compute_projection(
    matrix: Mapping[str, object],
    *,
    repo_root: Path | str | None = None,
) -> HerdrSupportabilityProjection:
    """Compute the Herdr supportability projection from a validated matrix.

    Args:
        matrix: A ``WindowsHerdrPublicWorkflowEvidence`` dict loaded from the
            accepted parent feature artifact.
        repo_root: Optional repo root for computing relative artifact refs.

    Returns:
        A deterministic ``HerdrSupportabilityProjection``.  The tier is always
        fail-closed — ``supported`` only when all gates are satisfied.
    """
    _require_kind(matrix, "herdr", "backend_impl")

    # -- metadata -----------------------------------------------------------
    ccb_version = _str_or(matrix.get("ccb_version"), None)
    release_version_matches = ccb_version == "8.6.7"
    source_status = _source_status(str(matrix.get("ccb_source_status") or ""))
    release_source_matches = source_status == "matching-release"

    herdr_version = _str_or(matrix.get("herdr_version"), None)
    herdr_auto_restore = _herdr_auto_restore_mode(
        str(matrix.get("herdr_auto_restore_mode") or "")
    )

    platform_ok = str(matrix.get("os_platform") or "") == "win32"
    arch_ok = str(matrix.get("cpu_arch") or "") == "x64"

    # -- workflow aggregation -----------------------------------------------
    wf_rows = matrix.get("workflow_rows")
    wf_rows = wf_rows if isinstance(wf_rows, Mapping) else {}
    required_status, non_pass_wf = _aggregate_workflows(wf_rows, _REQUIRED_WORKFLOWS)

    # -- provider aggregation ------------------------------------------------
    provider_rows = matrix.get("provider_workflow_rows")
    provider_rows = provider_rows if isinstance(provider_rows, Mapping) else {}
    provider_status, non_pass_pvd = _aggregate_provider_workflows(
        provider_rows, _PROVIDER_WORKFLOWS
    )
    non_pass_wf.update(non_pass_pvd)

    # -- special statuses ----------------------------------------------------
    mobile = _status_or(str(matrix.get("mobile_terminal_status") or ""), "not-run")
    config_ui = _status_or(str(matrix.get("config_ui_status") or ""), "not-run")
    npm_dry = _status_or(
        str(matrix.get("windows_npm_install_dry_run_status") or ""), "not-run"
    )

    beta_gaps = _str_list(matrix.get("beta_gaps"))
    residual_risks = _str_list(matrix.get("residual_risks"))

    # -- release surface ----------------------------------------------------
    release_ref = _str_or(matrix.get("release_surface_ref"), None)
    release_status = _release_surface_status(matrix, repo_root=repo_root)

    # -- provider catalog ---------------------------------------------------
    provider_catalog_ref: str | None = None
    provider_catalog_status: Literal["fresh", "stale", "missing"] = "missing"

    # -- tier source --------------------------------------------------------
    tier_source: TierSource = _matrix_tier_source(matrix)
    matrix_candidate_tier = str(matrix.get("support_tier") or "beta")
    allowed = matrix.get("support_projection_allowed") is True

    # -- docs/doctor evidence (placeholder — filled by consistency guards) ---
    docs_consistency_ref: str | None = None
    doctor_render_ref: str | None = None

    # -- install entry ------------------------------------------------------
    install_entry: HerdrInstallEntry = "diagnostic_only"
    windows_npm_enabled = False

    # -- tier computation ---------------------------------------------------
    final_tier = _compute_tier(
        platform_ok=platform_ok,
        arch_ok=arch_ok,
        release_version_matches=release_version_matches,
        release_source_matches=release_source_matches,
        herdr_auto_restore=herdr_auto_restore,
        required_status=required_status,
        provider_status=provider_status,
        mobile_status=mobile,
        config_ui_status=config_ui,
        npm_dry_status=npm_dry,
        beta_gaps=beta_gaps,
        release_surface_status=release_status,
        provider_catalog_status=provider_catalog_status,
        allowed=allowed,
        candidate_tier=matrix_candidate_tier,
        # docs/doctor evidence not yet generated → can't be supported
        docs_ok=False,
        doctor_ok=False,
    )

    validation_ref = _relative_artifact_ref(matrix, repo_root=repo_root)

    fallback = _build_fallback_guidance(
        tier=final_tier,
        required_status=required_status,
        provider_status=provider_status,
        beta_gaps=beta_gaps,
    )

    projection: HerdrSupportabilityProjection = {
        "support_tier": final_tier,
        "support_tier_source": tier_source,
        "projection_hash": None,
        "backend_impl": "herdr",
        "os_platform": "win32",
        "cpu_arch": "x64",
        "ccb_version": ccb_version_gate(ccb_version),
        "ccb_source_status": source_status,
        "herdr_version": herdr_version,
        "herdr_auto_restore_mode": herdr_auto_restore,
        "validation_ref": validation_ref,
        "provider_catalog_ref": provider_catalog_ref,
        "provider_catalog_status": provider_catalog_status,
        "release_surface_ref": release_ref,
        "release_surface_status": release_status,
        "docs_consistency_ref": docs_consistency_ref,
        "doctor_render_ref": doctor_render_ref,
        "install_entry": install_entry,
        "windows_npm_enabled": windows_npm_enabled,
        "windows_npm_install_dry_run_status": npm_dry,
        "required_workflows_status": required_status,
        "provider_workflows_status": provider_status,
        "mobile_terminal_status": mobile,
        "config_ui_status": config_ui,
        "beta_gaps": beta_gaps,
        "residual_risks": residual_risks,
        "non_pass_workflows": non_pass_wf,
        "fallback_guidance": fallback,
    }
    projection["projection_hash"] = _compute_projection_hash(projection)
    return projection


def load_matrix(
    matrix_path: str | Path,
    *,
    repo_root: Path | str | None = None,
) -> HerdrSupportabilityProjection:
    """Load a matrix artifact from disk and compute its projection."""
    path = Path(matrix_path)
    if not path.is_absolute() and repo_root is not None:
        path = Path(repo_root) / path
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return _blocked_projection("Herdr validation matrix artifact is missing or unreadable")
    if not isinstance(payload, dict):
        return _blocked_projection("Herdr validation matrix artifact is malformed")
    return compute_projection(payload, repo_root=repo_root)


# -- helpers ----------------------------------------------------------------

def _str_or(value: object, default: object = "") -> str | None:
    if value is None:
        return default  # type: ignore[return-value]
    return str(value).strip() or None


def _str_list(value: object) -> list[str]:
    if not isinstance(value, list):
        return []
    return [str(item).strip() for item in value if str(item).strip()]


def _require_kind(payload: Mapping[str, object], expected: str, field: str) -> None:
    actual = str(payload.get(field) or "")
    if actual != expected:
        raise ValueError(
            f"Herdr matrix {field} must be {expected!r}, got {actual!r}"
        )


def _source_status(raw: str) -> Literal["matching-release", "blocked", "unknown"]:
    if raw == "matching-release":
        return "matching-release"
    if raw in {"blocked", "unknown"}:
        return raw  # type: ignore[return-value]
    return "unknown"


def _herdr_auto_restore_mode(
    raw: str,
) -> Literal["disabled", "observe-only", "unsupported", "unknown"]:
    for mode in ("disabled", "observe-only", "unsupported", "unknown"):
        if raw == mode:
            return mode  # type: ignore[return-value]
    return "unknown"


def _status_or(
    raw: str,
    default: str,
) -> Literal["pass", "partial", "blocked", "failed", "not-run"]:
    if raw in {"pass", "partial", "blocked", "failed", "not-run"}:
        return raw  # type: ignore[return-value]
    if default in {"pass", "partial", "blocked", "failed", "not-run"}:
        return default  # type: ignore[return-value]
    return "not-run"


def _aggregate_workflows(
    rows: Mapping[str, object],
    required: tuple[str, ...],
) -> tuple[
    Literal["pass", "partial", "blocked", "missing"],
    dict[str, str],
]:
    non_pass: dict[str, str] = {}
    worst_severity = 99
    for wf in required:
        row = rows.get(wf)
        if not isinstance(row, Mapping):
            non_pass[f"workflow:{wf}"] = f"Missing matrix row for required workflow {wf!r}"
            worst_severity = min(worst_severity, _WORKFLOW_SEVERITY["missing"])
            continue
        status = str(row.get("status") or "not-run")
        severity = _WORKFLOW_SEVERITY.get(status)
        if severity is None:
            non_pass[f"workflow:{wf}"] = f"Unknown workflow status {status!r}"
            worst_severity = min(worst_severity, _WORKFLOW_SEVERITY["blocked"])
            continue
        if status != "pass":
            reason = str(row.get("reason") or row.get("beta_gap") or f"Workflow {wf} status is {status}")
            non_pass[f"workflow:{wf}"] = reason
        worst_severity = min(worst_severity, severity)

    if not rows:
        return "missing", non_pass
    return _severity_to_aggregate(worst_severity), non_pass


def _aggregate_provider_workflows(
    rows: Mapping[str, object],
    workflows: tuple[str, ...],
) -> tuple[
    Literal["pass", "partial", "blocked", "missing"],
    dict[str, str],
]:
    non_pass: dict[str, str] = {}
    worst_severity = 99
    if not rows:
        return "missing", non_pass

    for provider_id, provider_row in rows.items():
        provider_id_str = str(provider_id)
        if not isinstance(provider_row, Mapping):
            for wf in workflows:
                non_pass[f"provider:{provider_id_str}:{wf}"] = (
                    f"Missing provider workflow row for {provider_id_str}"
                )
            worst_severity = min(worst_severity, _WORKFLOW_SEVERITY["missing"])
            continue
        for wf in workflows:
            status = str(provider_row.get(wf) or "not-run")
            severity = _WORKFLOW_SEVERITY.get(status)
            if severity is None:
                non_pass[f"provider:{provider_id_str}:{wf}"] = (
                    f"Unknown provider workflow status {status!r}"
                )
                worst_severity = min(worst_severity, _WORKFLOW_SEVERITY["blocked"])
                continue
            if status != "pass":
                detail_rows = matrix_provider_detail_rows_for(rows, provider_id_str)
                reason = _provider_reason(detail_rows, provider_id_str, wf)
                non_pass[f"provider:{provider_id_str}:{wf}"] = reason
            worst_severity = min(worst_severity, severity)

    return _severity_to_aggregate(worst_severity), non_pass


def matrix_provider_detail_rows_for(
    rows: Mapping[str, object],
    provider_id: str,
) -> Mapping[str, object] | None:
    """Resolve the detailed provider-workflow rows if present."""
    detail_rows = rows.get("provider_workflow_detail_rows")
    if isinstance(detail_rows, Mapping):
        candidate = detail_rows.get(provider_id)
        if isinstance(candidate, Mapping):
            return candidate
    return None


def _provider_reason(
    detail_rows: Mapping[str, object] | None,
    provider_id: str,
    workflow: str,
) -> str:
    if detail_rows is not None:
        row = detail_rows.get(f"{provider_id}:{workflow}")
        if isinstance(row, Mapping):
            reason = str(row.get("reason") or row.get("beta_gap") or "")
            if reason:
                return reason
    return f"Provider {provider_id} {workflow} status is not pass"


def _severity_to_aggregate(
    worst: int,
) -> Literal["pass", "partial", "blocked", "missing"]:
    if worst <= _WORKFLOW_SEVERITY["blocked"]:
        return "blocked"
    if worst == _WORKFLOW_SEVERITY["partial"]:
        return "partial"
    if worst == _WORKFLOW_SEVERITY["pass"]:
        return "pass"
    return "missing"


def _matrix_tier_source(matrix: Mapping[str, object]) -> TierSource:
    """Determine tier source from parent acceptance state and artifact kind.

    Does NOT use ``support_tier_is_candidate`` to infer the source.
    """
    # The caller must provide the parent acceptance state.  When loaded from
    # an accepted feature artifact the source is ``accepted_matrix``; when the
    # matrix is a blocked skeleton it is ``blocked_skeleton``; otherwise
    # ``missing``.
    source_ref = str(matrix.get("validation_ref") or matrix.get("source_ref") or "")
    if "blocked-evidence" in source_ref.lower() or "blocked_skeleton" in source_ref.lower():
        return "blocked_skeleton"
    # Heuristic: if the matrix has concrete version numbers and test evidence,
    # treat it as accepted_matrix; otherwise as blocked_skeleton.
    if matrix.get("herdr_version") and matrix.get("test_evidence"):
        return "accepted_matrix"
    if matrix.get("support_projection_allowed") is True:
        return "accepted_matrix"
    return "blocked_skeleton"


def _release_surface_status(
    matrix: Mapping[str, object],
    *,
    repo_root: Path | str | None = None,
) -> Literal["pass", "blocked", "missing"]:
    """Check the release surface artifact referenced by the matrix."""
    ref = _str_or(matrix.get("release_surface_ref"))
    if not ref:
        return "missing"
    # In a full implementation this would load and validate the
    # WindowsX64ReleaseSurfaceProjection artifact.  For now, return blocked
    # since the release surface is gated on upstream acceptance.
    return "blocked"


def _relative_artifact_ref(
    matrix: Mapping[str, object],
    *,
    repo_root: Path | str | None = None,
) -> str | None:
    """Compute a repo-relative path to the matrix artifact."""
    ref = _str_or(matrix.get("validation_ref") or matrix.get("_matrix_path"))
    if ref:
        return ref
    ref = _str_or(matrix.get("artifacts", {}).get("matrix") if isinstance(matrix.get("artifacts"), Mapping) else None)  # type: ignore[union-attr]
    return ref


def ccb_version_gate(raw: str | None) -> str | None:
    """Gate the support matrix to the Windows beta release under test."""
    if raw == "8.6.7":
        return raw
    return None


# -- tier computation -------------------------------------------------------

def _compute_tier(
    *,
    platform_ok: bool,
    arch_ok: bool,
    release_version_matches: bool,
    release_source_matches: bool,
    herdr_auto_restore: str,
    required_status: str,
    provider_status: str,
    mobile_status: str,
    config_ui_status: str,
    npm_dry_status: str,
    beta_gaps: list[str],
    release_surface_status: str,
    provider_catalog_status: str,
    allowed: bool,
    candidate_tier: str,
    docs_ok: bool,
    doctor_ok: bool,
) -> HerdrSupportTier:
    """Pure-function tier rule — fail-closed."""

    # Hard blockers → unsupported
    if not platform_ok or not arch_ok:
        return "unsupported"
    if not release_version_matches or not release_source_matches:
        return "unsupported"

    # Experimental floor
    if herdr_auto_restore == "unknown":
        return "experimental"
    if required_status == "missing":
        return "experimental"

    # Beta floor — any non-pass / non-fresh condition prevents supported
    beta_conditions = [
        herdr_auto_restore != "disabled",
        required_status != "pass",
        provider_status != "pass",
        mobile_status != "pass",
        config_ui_status != "pass",
        npm_dry_status != "pass",
        len(beta_gaps) > 0,
        release_surface_status != "pass",
        provider_catalog_status != "fresh",
        not allowed,
        not docs_ok,
        not doctor_ok,
    ]
    if any(beta_conditions):
        # If blocked-level conditions exist, floor is experimental
        blocked_conditions = [
            required_status in ("blocked", "missing"),
            provider_status in ("blocked", "missing"),
            mobile_status in ("blocked", "failed"),
            config_ui_status in ("blocked", "failed"),
            not allowed,
        ]
        if any(blocked_conditions):
            return "experimental"
        return "beta"

    # All gates passed
    return "supported"


def _build_fallback_guidance(
    *,
    tier: HerdrSupportTier,
    required_status: str,
    provider_status: str,
    beta_gaps: list[str],
) -> str:
    if tier == "supported":
        return (
            "Native Windows x64 (Herdr) is supported. "
            "See doctor output for current validation evidence."
        )
    if tier == "beta":
        return (
            "Native Windows x64 (Herdr) is in beta. "
            "Core CCB workflows may work; provider and surface coverage is incomplete. "
            "See doctor output for known gaps."
        )
    if tier == "experimental":
        parts = ["Native Windows x64 (Herdr) is experimental. "]
        if required_status != "pass":
            parts.append(f"Required workflows: {required_status}. ")
        if provider_status != "pass":
            parts.append(f"Provider workflows: {provider_status}. ")
        parts.append("Do not rely on this for production workflows.")
        return "".join(parts)
    return (
        "Native Windows x64 (Herdr) is not supported. "
        "Use a Linux/macOS tmux environment for full CCB functionality."
    )


# -- projection identity ----------------------------------------------------

def _compute_projection_hash(projection: HerdrSupportabilityProjection) -> str:
    """SHA-256 of the stable projection fields, deterministic."""
    stable: dict[str, object] = {}
    for key in sorted(_PROJECTION_HASH_FIELDS):
        if key in projection:
            stable[key] = projection[key]  # type: ignore[literal-required]
    # Sort internal dicts for determinism
    non_pass = stable.get("non_pass_workflows")
    if isinstance(non_pass, dict):
        stable["non_pass_workflows"] = dict(sorted(non_pass.items()))
    canonical = json.dumps(stable, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _blocked_projection(diagnostic: str) -> HerdrSupportabilityProjection:
    """Return a fail-closed skeleton projection."""
    return {
        "support_tier": "experimental",
        "support_tier_source": "missing",
        "projection_hash": None,
        "backend_impl": "herdr",
        "os_platform": "win32",
        "cpu_arch": "x64",
        "ccb_version": None,
        "ccb_source_status": "unknown",
        "herdr_version": None,
        "herdr_auto_restore_mode": "unknown",
        "validation_ref": None,
        "provider_catalog_ref": None,
        "provider_catalog_status": "missing",
        "release_surface_ref": None,
        "release_surface_status": "missing",
        "docs_consistency_ref": None,
        "doctor_render_ref": None,
        "install_entry": "diagnostic_only",
        "windows_npm_enabled": False,
        "windows_npm_install_dry_run_status": "not-run",
        "required_workflows_status": "missing",
        "provider_workflows_status": "missing",
        "mobile_terminal_status": "not-run",
        "config_ui_status": "not-run",
        "beta_gaps": [diagnostic],
        "residual_risks": [],
        "non_pass_workflows": {},
        "fallback_guidance": _build_fallback_guidance(
            tier="experimental",
            required_status="missing",
            provider_status="missing",
            beta_gaps=[diagnostic],
        ),
    }


__all__ = [
    "HerdrSupportabilityProjection",
    "HerdrSupportTier",
    "HerdrInstallEntry",
    "TierSource",
    "compute_projection",
    "load_matrix",
    "_blocked_projection",
]
