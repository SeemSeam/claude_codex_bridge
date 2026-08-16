from __future__ import annotations

import copy
import json
import re
from pathlib import Path
from typing import Any, Literal, TypedDict

from provider_core.registry import build_default_provider_manifests


FEATURE_SLUG = "native-windows-public-workflow-validation-matrix"
FEATURE_DIR = Path(".codestable/features/2026-07-31-native-windows-public-workflow-validation-matrix")
MATRIX_RELATIVE_PATH = FEATURE_DIR / "evidence/windows-herdr-public-workflow-matrix.json"
PUBLIC_PROVIDER_FREEZE_RELATIVE_PATH = FEATURE_DIR / "evidence/public-providers-freeze.json"
NATIVE_WINDOWS_TRANSCRIPT_RELATIVE_PATH = FEATURE_DIR / "evidence/native-windows-transcript.md"
PROVIDER_WORKFLOWS_TRANSCRIPT_RELATIVE_PATH = FEATURE_DIR / "evidence/provider-workflows-transcript.md"
BLOCKED_EVIDENCE_RELATIVE_PATH = FEATURE_DIR / "evidence/blocked-evidence.md"
ROADMAP_ITEMS_RELATIVE_PATH = Path(
    ".codestable/roadmap/windows-native-herdr-ccb/windows-native-herdr-ccb-items.yaml"
)
SCHEMA_VERSION = 1
WINDOWS_VALIDATED_CCB_VERSION = "8.6.7"
WINDOWS_VALIDATED_SOURCE_STATUS = "matching-release"

REQUIRED_WORKFLOWS = (
    "ccb",
    "ask",
    "pend",
    "watch",
    "ping",
    "mounted",
    "kill",
    "restart",
    "reload",
    "foreground_attach",
    "mobile_terminal",
    "config_ui",
    "doctor_update",
    "support_projection",
)
PROVIDER_WORKFLOWS = ("ask", "pend", "completion", "cancel")
WORKFLOW_STATUSES = ("pass", "partial", "blocked", "failed", "not-run")

RequiredWorkflow = Literal[
    "ccb",
    "ask",
    "pend",
    "watch",
    "ping",
    "mounted",
    "kill",
    "restart",
    "reload",
    "foreground_attach",
    "mobile_terminal",
    "config_ui",
    "doctor_update",
    "support_projection",
]
ProviderWorkflow = Literal["ask", "pend", "completion", "cancel"]
WorkflowStatus = Literal["pass", "partial", "blocked", "failed", "not-run"]
EvidenceClass = Literal["native-windows", "windows-runner", "blocked-evidence", "unit", "regression"]
ProviderEvidenceClass = Literal["native-windows", "windows-runner", "blocked-evidence"]


class WindowsHerdrWorkflowRow(TypedDict):
    workflow: RequiredWorkflow
    status: WorkflowStatus
    evidence_class: EvidenceClass
    command: str | None
    artifact_ref: str | None
    host_evidence_ref: str | None
    backend_impl: Literal["herdr"]
    os_platform: Literal["win32"]
    cpu_arch: Literal["x64"]
    reason: str | None
    beta_gap: str | None
    residual_risk: str | None


class WindowsHerdrProviderWorkflowRow(TypedDict):
    provider: str
    workflow: ProviderWorkflow
    status: WorkflowStatus
    evidence_class: ProviderEvidenceClass
    command: str | None
    artifact_ref: str | None
    host_evidence_ref: str | None
    backend_impl: Literal["herdr"]
    pane_ref: str | None
    reason: str | None
    beta_gap: str | None
    residual_risk: str | None


class WindowsHerdrPublicWorkflowEvidence(TypedDict):
    schema_version: Literal[1]
    backend_impl: Literal["herdr"]
    os_platform: Literal["win32"]
    cpu_arch: Literal["x64"]
    ccb_version: str
    ccb_source_status: Literal["matching-release", "blocked", "unknown"]
    herdr_version: str
    herdr_auto_restore_mode: Literal["disabled", "observe-only", "unsupported", "unknown"]
    baseline_ref: str | None
    release_surface_ref: str | None
    user_surfaces_ref: str | None
    public_providers: list[str]
    required_workflows: list[RequiredWorkflow]
    workflows: dict[str, WorkflowStatus]
    workflow_rows: dict[str, WindowsHerdrWorkflowRow]
    provider_workflows: list[ProviderWorkflow]
    provider_workflow_rows: dict[str, dict[ProviderWorkflow, WorkflowStatus]]
    provider_workflow_detail_rows: dict[str, WindowsHerdrProviderWorkflowRow]
    mobile_terminal_status: WorkflowStatus
    config_ui_status: WorkflowStatus
    windows_npm_install_dry_run_status: WorkflowStatus
    beta_gaps: list[str]
    residual_risks: list[str]
    artifacts: dict[str, str]
    support_tier: Literal[
        "unsupported",
        "experimental",
        "beta",
        "supported",
    ]
    support_tier_is_candidate: bool
    support_projection_allowed: bool


_REQUIRED_FIELDS = {
    "schema_version",
    "backend_impl",
    "os_platform",
    "cpu_arch",
    "ccb_version",
    "ccb_source_status",
    "herdr_version",
    "herdr_auto_restore_mode",
    "baseline_ref",
    "release_surface_ref",
    "user_surfaces_ref",
    "public_providers",
    "required_workflows",
    "workflows",
    "workflow_rows",
    "provider_workflows",
    "provider_workflow_rows",
    "provider_workflow_detail_rows",
    "mobile_terminal_status",
    "config_ui_status",
    "windows_npm_install_dry_run_status",
    "beta_gaps",
    "residual_risks",
    "artifacts",
    "support_tier",
    "support_tier_is_candidate",
    "support_projection_allowed",
}
_WORKFLOW_ROW_FIELDS = {
    "workflow",
    "status",
    "evidence_class",
    "command",
    "artifact_ref",
    "host_evidence_ref",
    "backend_impl",
    "os_platform",
    "cpu_arch",
    "reason",
    "beta_gap",
    "residual_risk",
}
_PROVIDER_ROW_FIELDS = {
    "provider",
    "workflow",
    "status",
    "evidence_class",
    "command",
    "artifact_ref",
    "host_evidence_ref",
    "backend_impl",
    "pane_ref",
    "reason",
    "beta_gap",
    "residual_risk",
}
_COMMANDS = {
    "ccb": "ccb",
    "ask": "ccb ask <target>",
    "pend": "ccb pend <target>",
    "watch": "ccb pend --watch <target>",
    "ping": "ccb ping",
    "mounted": "ccb ping all",
    "kill": "ccb kill",
    "restart": "ccb restart",
    "reload": "ccb reload",
    "foreground_attach": "ccb",
    "mobile_terminal": "mobile terminal",
    "config_ui": "config ui",
    "doctor_update": "ccb doctor --output; ccb update",
    "support_projection": "consume windows-herdr-public-workflow-matrix.json",
}


def canonical_matrix_json(matrix: dict[str, object]) -> str:
    return json.dumps(matrix, ensure_ascii=True, indent=2, sort_keys=True) + "\n"


def public_provider_names() -> list[str]:
    manifests = build_default_provider_manifests(
        include_optional=True,
        include_test_doubles=False,
    )
    return sorted(manifest.provider for manifest in manifests)


def public_provider_freeze() -> dict[str, object]:
    return {
        "schema_version": 1,
        "source": "build_default_provider_manifests(include_optional=True, include_test_doubles=False)",
        "public_providers": public_provider_names(),
        "provider_workflows": list(PROVIDER_WORKFLOWS),
        "excluded_test_doubles": True,
    }


def validate_windows_herdr_public_workflow_evidence(
    raw: dict[str, Any],
) -> WindowsHerdrPublicWorkflowEvidence:
    missing = _REQUIRED_FIELDS - set(raw)
    if missing:
        raise ValueError(f"matrix missing required fields: {', '.join(sorted(missing))}")
    extra = set(raw) - _REQUIRED_FIELDS
    if extra:
        raise ValueError(f"matrix has unknown fields: {', '.join(sorted(extra))}")
    if raw.get("schema_version") != SCHEMA_VERSION:
        raise ValueError("unsupported matrix schema version")
    _require_equal(raw, "backend_impl", "herdr")
    _require_equal(raw, "os_platform", "win32")
    _require_equal(raw, "cpu_arch", "x64")
    _require_equal(raw, "ccb_version", WINDOWS_VALIDATED_CCB_VERSION)
    _require_in(raw, "ccb_source_status", {WINDOWS_VALIDATED_SOURCE_STATUS, "blocked", "unknown"})
    _require_in(raw, "herdr_auto_restore_mode", {"disabled", "observe-only", "unsupported", "unknown"})
    _require_in(raw, "support_tier", {"unsupported", "experimental", "beta", "supported"})
    if not _non_empty_text(raw.get("herdr_version")):
        raise ValueError("herdr_version must be a non-empty string")
    for field in ("baseline_ref", "release_surface_ref", "user_surfaces_ref"):
        _validate_string_or_none(raw.get(field), field)
    if raw.get("support_tier_is_candidate") is not True:
        raise ValueError("support_tier_is_candidate must be true")
    if not isinstance(raw.get("support_projection_allowed"), bool):
        raise ValueError("support_projection_allowed must be bool")
    _validate_string_list(raw.get("beta_gaps"), "beta_gaps")
    _validate_string_list(raw.get("residual_risks"), "residual_risks")
    _validate_artifacts(raw.get("artifacts"))
    _validate_required_workflows(raw)
    _validate_provider_workflows(raw)
    _validate_support_candidate_rule(raw)
    return copy.deepcopy(raw)


def validate_windows_herdr_public_workflow_artifacts(
    root: str | Path,
    raw: dict[str, Any],
) -> WindowsHerdrPublicWorkflowEvidence:
    matrix = validate_windows_herdr_public_workflow_evidence(raw)
    root_path = Path(root)
    for field in ("baseline_ref", "release_surface_ref", "user_surfaces_ref"):
        if matrix["support_projection_allowed"] or _non_empty_text(matrix[field]):
            _require_existing_repo_ref(root_path, matrix[field], f"parent ref {field}")
    for key, ref in matrix["artifacts"].items():
        _require_existing_repo_ref(root_path, ref, f"matrix artifact {key}")
    for workflow, row in matrix["workflow_rows"].items():
        _require_existing_repo_ref(root_path, row["artifact_ref"], f"workflow artifact {workflow}")
        _require_existing_repo_ref(root_path, row["host_evidence_ref"], f"workflow host evidence {workflow}")
    for key, row in matrix["provider_workflow_detail_rows"].items():
        _require_existing_repo_ref(root_path, row["artifact_ref"], f"provider artifact {key}")
        _require_existing_repo_ref(root_path, row["host_evidence_ref"], f"provider host evidence {key}")
    return matrix


def default_blocked_public_workflow_matrix(
    *,
    public_providers: list[str] | None = None,
    reason: str,
    baseline_ref: str | None = None,
    release_surface_ref: str | None = None,
    user_surfaces_ref: str | None = None,
) -> WindowsHerdrPublicWorkflowEvidence:
    providers = sorted(public_providers if public_providers is not None else public_provider_names())
    if providers != public_provider_names():
        raise ValueError("blocked matrix provider catalog must match current public provider set")
    workflows = {workflow: "blocked" for workflow in REQUIRED_WORKFLOWS}
    workflow_rows = {
        workflow: _blocked_workflow_row(workflow=workflow, reason=reason) for workflow in REQUIRED_WORKFLOWS
    }
    provider_summary = {
        provider: {workflow: "blocked" for workflow in PROVIDER_WORKFLOWS} for provider in providers
    }
    provider_detail = {
        f"{provider}:{workflow}": _blocked_provider_workflow_row(
            provider=provider,
            workflow=workflow,
            reason=reason,
        )
        for provider in providers
        for workflow in PROVIDER_WORKFLOWS
    }
    return {
        "schema_version": SCHEMA_VERSION,
        "backend_impl": "herdr",
        "os_platform": "win32",
        "cpu_arch": "x64",
        "ccb_version": WINDOWS_VALIDATED_CCB_VERSION,
        "ccb_source_status": "unknown",
        "herdr_version": "unknown",
        "herdr_auto_restore_mode": "unknown",
        "baseline_ref": baseline_ref,
        "release_surface_ref": release_surface_ref,
        "user_surfaces_ref": user_surfaces_ref,
        "public_providers": providers,
        "required_workflows": list(REQUIRED_WORKFLOWS),
        "workflows": workflows,
        "workflow_rows": workflow_rows,
        "provider_workflows": list(PROVIDER_WORKFLOWS),
        "provider_workflow_rows": provider_summary,
        "provider_workflow_detail_rows": provider_detail,
        "mobile_terminal_status": "blocked",
        "config_ui_status": "blocked",
        "windows_npm_install_dry_run_status": "not-run",
        "beta_gaps": [reason],
        "residual_risks": [reason],
        "artifacts": {
            "matrix": MATRIX_RELATIVE_PATH.as_posix(),
            "public_providers_freeze": PUBLIC_PROVIDER_FREEZE_RELATIVE_PATH.as_posix(),
            "native_windows_transcript": NATIVE_WINDOWS_TRANSCRIPT_RELATIVE_PATH.as_posix(),
            "provider_workflows_transcript": PROVIDER_WORKFLOWS_TRANSCRIPT_RELATIVE_PATH.as_posix(),
            "blocked_evidence": BLOCKED_EVIDENCE_RELATIVE_PATH.as_posix(),
        },
        "support_tier": "beta",
        "support_tier_is_candidate": True,
        "support_projection_allowed": False,
    }


def windows_herdr_public_workflow_parent_admission(root: str | Path) -> dict[str, object]:
    root_path = Path(root)
    items = _parse_roadmap_items(root_path / ROADMAP_ITEMS_RELATIVE_PATH)
    current = items.get(FEATURE_SLUG)
    if current is None:
        return _blocked_parent_admission(f"roadmap item missing: {FEATURE_SLUG}")
    depends_on = current.get("depends_on")
    if not isinstance(depends_on, list):
        return _blocked_parent_admission("current item depends_on missing")

    parent_refs: dict[str, str] = {}
    for parent_slug in depends_on:
        parent = items.get(parent_slug)
        if parent is None:
            return _blocked_parent_admission(f"parent item missing: {parent_slug}")
        if parent.get("status") != "done":
            return _blocked_parent_admission(f"parent item not done: {parent_slug}")
        feature_dir = _non_empty_text(parent.get("feature"))
        if not feature_dir:
            return _blocked_parent_admission(f"parent feature pointer missing: {parent_slug}")
        acceptance = root_path / ".codestable" / "features" / feature_dir / f"{parent_slug}-acceptance.md"
        if not acceptance.exists():
            return _blocked_parent_admission(f"parent acceptance missing: {_rel(root_path, acceptance)}")
        frontmatter = _frontmatter(acceptance)
        if frontmatter.get("doc_type") != "feature-acceptance" or frontmatter.get("status") != "passed":
            return _blocked_parent_admission(f"parent acceptance not passed: {_rel(root_path, acceptance)}")
        text = _read_text(acceptance)
        if not _has_acceptance_artifact_refs(root_path, acceptance.parent, text):
            return _blocked_parent_admission(f"parent acceptance lacks artifact refs: {_rel(root_path, acceptance)}")
        parent_refs[parent_slug] = _rel(root_path, acceptance)

    return {
        "status": "ready",
        "implementation_admission": "admitted",
        "parent_refs": parent_refs,
    }


def _validate_required_workflows(raw: dict[str, Any]) -> None:
    required = raw.get("required_workflows")
    workflows = raw.get("workflows")
    rows = raw.get("workflow_rows")
    if not _is_exact_string_sequence(required, REQUIRED_WORKFLOWS):
        raise ValueError("required workflow key set drifted")
    if not isinstance(workflows, dict) or set(workflows) != set(REQUIRED_WORKFLOWS):
        raise ValueError("required workflow summary key set drifted")
    if not isinstance(rows, dict) or set(rows) != set(REQUIRED_WORKFLOWS):
        raise ValueError("required workflow row key set drifted")
    for workflow in REQUIRED_WORKFLOWS:
        status = workflows[workflow]
        if status not in WORKFLOW_STATUSES:
            raise ValueError(f"invalid workflow status: {workflow}")
        _validate_workflow_row(workflow, rows[workflow], status)
    for field in ("mobile_terminal_status", "config_ui_status", "windows_npm_install_dry_run_status"):
        _require_in(raw, field, set(WORKFLOW_STATUSES))
    if raw["mobile_terminal_status"] != workflows["mobile_terminal"]:
        raise ValueError("mobile_terminal_status must match mobile_terminal workflow status")
    if raw["config_ui_status"] != workflows["config_ui"]:
        raise ValueError("config_ui_status must match config_ui workflow status")


def _validate_workflow_row(workflow: str, row: Any, expected_status: str) -> None:
    if not isinstance(row, dict):
        raise ValueError(f"workflow row must be an object: {workflow}")
    missing = _WORKFLOW_ROW_FIELDS - set(row)
    if missing:
        raise ValueError(f"workflow row missing required fields: {workflow}")
    extra = set(row) - _WORKFLOW_ROW_FIELDS
    if extra:
        raise ValueError(f"workflow row has unknown fields: {workflow}")
    if row.get("workflow") != workflow:
        raise ValueError(f"workflow row key mismatch: {workflow}")
    if row.get("status") != expected_status:
        raise ValueError(f"workflow row status mismatch: {workflow}")
    if row.get("evidence_class") not in {"native-windows", "windows-runner", "blocked-evidence", "unit", "regression"}:
        raise ValueError(f"invalid workflow evidence_class: {workflow}")
    for field in ("command", "reason", "beta_gap", "residual_risk"):
        _validate_string_or_none(row.get(field), f"workflow row {field}: {workflow}")
    _require_equal(row, "backend_impl", "herdr")
    _require_equal(row, "os_platform", "win32")
    _require_equal(row, "cpu_arch", "x64")
    if not _non_empty_text(row.get("artifact_ref")):
        raise ValueError(f"workflow row artifact_ref missing: {workflow}")
    if not _non_empty_text(row.get("host_evidence_ref")):
        raise ValueError(f"workflow row host_evidence_ref missing: {workflow}")
    if expected_status == "pass" and row.get("evidence_class") not in {"native-windows", "windows-runner"}:
        raise ValueError(f"workflow pass requires native Windows evidence: {workflow}")
    if expected_status != "pass" and not _non_empty_text(row.get("reason")):
        raise ValueError(f"workflow non-pass row reason missing: {workflow}")


def _validate_provider_workflows(raw: dict[str, Any]) -> None:
    providers = raw.get("public_providers")
    provider_workflows = raw.get("provider_workflows")
    summary = raw.get("provider_workflow_rows")
    details = raw.get("provider_workflow_detail_rows")
    _validate_string_list(providers, "public_providers")
    if sorted(providers) != public_provider_names():
        raise ValueError("public provider catalog does not match current public provider set")
    if not _is_exact_string_sequence(provider_workflows, PROVIDER_WORKFLOWS):
        raise ValueError("provider workflow key set drifted")
    if not isinstance(summary, dict) or set(summary) != set(providers):
        raise ValueError("provider workflow summary provider key set drifted")
    expected_detail_keys = {
        f"{provider}:{workflow}" for provider in providers for workflow in PROVIDER_WORKFLOWS
    }
    if not isinstance(details, dict) or set(details) != expected_detail_keys:
        raise ValueError("provider workflow detail key set drifted")
    for provider in providers:
        provider_summary = summary[provider]
        if not isinstance(provider_summary, dict) or set(provider_summary) != set(PROVIDER_WORKFLOWS):
            raise ValueError(f"provider workflow summary shape drifted: {provider}")
        for workflow in PROVIDER_WORKFLOWS:
            status = provider_summary[workflow]
            if status not in WORKFLOW_STATUSES:
                raise ValueError(f"invalid provider workflow status: {provider}:{workflow}")
            _validate_provider_detail_row(
                provider,
                workflow,
                details[f"{provider}:{workflow}"],
                status,
            )


def _validate_provider_detail_row(provider: str, workflow: str, row: Any, expected_status: str) -> None:
    if not isinstance(row, dict):
        raise ValueError(f"provider workflow detail row must be an object: {provider}:{workflow}")
    missing = _PROVIDER_ROW_FIELDS - set(row)
    if missing:
        raise ValueError(f"provider workflow detail row missing required fields: {provider}:{workflow}")
    extra = set(row) - _PROVIDER_ROW_FIELDS
    if extra:
        raise ValueError(f"provider workflow detail row has unknown fields: {provider}:{workflow}")
    if row.get("provider") != provider or row.get("workflow") != workflow:
        raise ValueError(f"provider workflow detail row key mismatch: {provider}:{workflow}")
    if row.get("status") != expected_status:
        raise ValueError(f"provider workflow detail status mismatch: {provider}:{workflow}")
    if row.get("evidence_class") not in {"native-windows", "windows-runner", "blocked-evidence"}:
        raise ValueError(f"invalid provider workflow evidence_class: {provider}:{workflow}")
    for field in ("command", "pane_ref", "reason", "beta_gap", "residual_risk"):
        _validate_string_or_none(row.get(field), f"provider workflow {field}: {provider}:{workflow}")
    _require_equal(row, "backend_impl", "herdr")
    if not _non_empty_text(row.get("artifact_ref")):
        raise ValueError(f"provider workflow artifact_ref missing: {provider}:{workflow}")
    if not _non_empty_text(row.get("host_evidence_ref")):
        raise ValueError(f"provider workflow host_evidence_ref missing: {provider}:{workflow}")
    if expected_status == "pass" and row.get("evidence_class") not in {"native-windows", "windows-runner"}:
        raise ValueError(f"provider workflow pass requires native Windows evidence: {provider}:{workflow}")
    if expected_status == "pass" and not _non_empty_text(row.get("pane_ref")):
        raise ValueError(f"provider workflow pane_ref missing: {provider}:{workflow}")
    if expected_status != "pass" and not _non_empty_text(row.get("reason")):
        raise ValueError(f"provider workflow non-pass row reason missing: {provider}:{workflow}")


def _validate_support_candidate_rule(raw: dict[str, Any]) -> None:
    all_workflows_pass = all(raw["workflows"][workflow] == "pass" for workflow in REQUIRED_WORKFLOWS)
    all_provider_workflows_pass = all(
        raw["provider_workflow_rows"][provider][workflow] == "pass"
        for provider in raw["public_providers"]
        for workflow in PROVIDER_WORKFLOWS
    )
    supported_gate = (
        all_workflows_pass
        and all_provider_workflows_pass
        and raw["mobile_terminal_status"] == "pass"
        and raw["config_ui_status"] == "pass"
        and raw["windows_npm_install_dry_run_status"] == "pass"
        and raw["ccb_source_status"] == WINDOWS_VALIDATED_SOURCE_STATUS
        and raw["herdr_auto_restore_mode"] == "disabled"
        and all(_non_empty_text(raw.get(field)) for field in ("baseline_ref", "release_surface_ref", "user_surfaces_ref"))
        and not raw["beta_gaps"]
    )
    if all_workflows_pass and all_provider_workflows_pass and not all(
        _non_empty_text(raw.get(field)) for field in ("baseline_ref", "release_surface_ref", "user_surfaces_ref")
    ):
        raise ValueError("supported matrix requires parent refs")
    if raw["support_projection_allowed"] is not supported_gate:
        raise ValueError("support_projection_allowed must match the supported gate result")
    if supported_gate and raw["support_tier"] != "supported":
        raise ValueError("supported gate requires the supported candidate tier")
    if raw["support_tier"] == "supported" and not supported_gate:
        raise ValueError("supported support_tier requires support_projection_allowed and every hard gate")


def _blocked_workflow_row(*, workflow: str, reason: str) -> WindowsHerdrWorkflowRow:
    return {
        "workflow": workflow,
        "status": "blocked",
        "evidence_class": "blocked-evidence",
        "command": _COMMANDS[workflow],
        "artifact_ref": BLOCKED_EVIDENCE_RELATIVE_PATH.as_posix(),
        "host_evidence_ref": NATIVE_WINDOWS_TRANSCRIPT_RELATIVE_PATH.as_posix(),
        "backend_impl": "herdr",
        "os_platform": "win32",
        "cpu_arch": "x64",
        "reason": reason,
        "beta_gap": reason,
        "residual_risk": reason,
    }


def _blocked_provider_workflow_row(
    *,
    provider: str,
    workflow: str,
    reason: str,
) -> WindowsHerdrProviderWorkflowRow:
    return {
        "provider": provider,
        "workflow": workflow,
        "status": "blocked",
        "evidence_class": "blocked-evidence",
        "command": f"ccb {workflow} --provider {provider}",
        "artifact_ref": PROVIDER_WORKFLOWS_TRANSCRIPT_RELATIVE_PATH.as_posix(),
        "host_evidence_ref": NATIVE_WINDOWS_TRANSCRIPT_RELATIVE_PATH.as_posix(),
        "backend_impl": "herdr",
        "pane_ref": None,
        "reason": reason,
        "beta_gap": reason,
        "residual_risk": reason,
    }


def _parse_roadmap_items(path: Path) -> dict[str, dict[str, object]]:
    text = _read_text(path)
    items: dict[str, dict[str, object]] = {}
    current: dict[str, object] | None = None
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith("- slug:"):
            slug = _unquote(stripped.split(":", 1)[1].strip())
            current = {"slug": slug}
            items[slug] = current
            continue
        if current is None or ":" not in stripped:
            continue
        key, value = stripped.split(":", 1)
        current[key.strip()] = _parse_scalar_or_list(value.strip())
    return items


def _parse_scalar_or_list(value: str) -> object:
    value = value.split(" #", 1)[0].strip()
    if value.startswith("[") and value.endswith("]"):
        inner = value[1:-1].strip()
        if not inner:
            return []
        return [_unquote(item.strip()) for item in inner.split(",")]
    return _unquote(value)


def _frontmatter(path: Path) -> dict[str, str]:
    text = _read_text(path)
    if not text.startswith("---"):
        return {}
    end = text.find("\n---", 3)
    if end < 0:
        return {}
    fields: dict[str, str] = {}
    for line in text[3:end].splitlines():
        if ":" not in line:
            continue
        key, value = line.split(":", 1)
        fields[key.strip()] = _unquote(value.strip())
    return fields


def _has_acceptance_artifact_refs(root: Path, acceptance_dir: Path, text: str) -> bool:
    for line in text.splitlines():
        lowered = line.lower()
        if not _positive_evidence_ref_line(lowered):
            continue
        cmd_refs = re.findall(r"\bcmd-\d{3}\b", lowered)
        if cmd_refs and not all(_cmd_evidence_file_exists(acceptance_dir, cmd_ref) for cmd_ref in cmd_refs):
            continue
        repo_refs = _repo_evidence_refs(line)
        if repo_refs:
            refs_valid = True
            for ref in repo_refs:
                try:
                    _require_existing_repo_ref(root, ref, "parent acceptance artifact ref")
                except ValueError:
                    refs_valid = False
                    break
            if refs_valid:
                return True
            continue
        if cmd_refs:
            return True
    return False


def _cmd_evidence_file_exists(acceptance_dir: Path, cmd_ref: str) -> bool:
    evidence_dir = acceptance_dir / "evidence"
    if not evidence_dir.is_dir():
        return False
    prefix = cmd_ref.lower()
    try:
        return any(child.is_file() and child.name.lower().startswith(prefix) for child in evidence_dir.iterdir())
    except OSError:
        return False


def _repo_evidence_refs(line: str) -> list[str]:
    refs: list[str] = []
    for token in re.split(r"\s+", line):
        ref = token.strip("`'\",;:()[]")
        if "evidence/" in ref:
            refs.append(ref)
    return refs


def _positive_evidence_ref_line(lowered_line: str) -> bool:
    negative_tokens = (
        "no ",
        "not available",
        "unavailable",
        "unrecorded",
        "not recorded",
        "not passed",
        "not found",
        "not valid",
        "none",
        "absent",
        "omitted",
        "tbd",
        "pending",
        "without ",
        "lacks ",
        "missing ",
        "failed",
        "blocked",
    )
    if any(token in lowered_line for token in negative_tokens):
        return False
    has_repo_artifact_ref = re.search(r"(?:^|[`\s:([])(?:\.codestable/)?[^`\s)\]]*evidence/[^`\s)\]]+", lowered_line)
    has_command_artifact_ref = re.search(r"\bcmd-\d{3}\b", lowered_line)
    return bool(has_repo_artifact_ref or has_command_artifact_ref)


def _require_existing_repo_ref(root: Path, value: object, label: str) -> None:
    ref = _non_empty_text(value)
    if not ref:
        raise ValueError(f"{label} missing")
    path_part = ref.split("#", 1)[0]
    if (
        Path(path_part).is_absolute()
        or re.match(r"^[A-Za-z]:", path_part)
        or path_part.startswith(("/", "\\"))
    ):
        raise ValueError(f"{label} must be repo-relative: {ref}")
    root_path = root.resolve()
    path = (root_path / path_part).resolve()
    try:
        path.relative_to(root_path)
    except ValueError as exc:
        raise ValueError(f"{label} outside root: {ref}") from exc
    if not path.is_file():
        raise ValueError(f"{label} artifact missing: {ref}")


def _validate_artifacts(value: Any) -> None:
    if not isinstance(value, dict) or not value:
        raise ValueError("artifacts must be a non-empty string mapping")
    for key, item in value.items():
        if not _non_empty_text(key) or not _non_empty_text(item):
            raise ValueError("artifacts must be a non-empty string mapping")


def _validate_string_list(value: Any, field: str) -> None:
    if not isinstance(value, list) or not all(_non_empty_text(item) for item in value):
        raise ValueError(f"{field} must be a string list")


def _validate_string_or_none(value: object, field: str) -> None:
    if value is not None and not isinstance(value, str):
        raise ValueError(f"{field} must be string or null")


def _is_exact_string_sequence(value: Any, expected: tuple[str, ...]) -> bool:
    return isinstance(value, list) and value == list(expected)


def _require_equal(raw: dict[str, Any], field: str, expected: object) -> None:
    if raw.get(field) != expected:
        raise ValueError(f"{field} must be {expected!r}")


def _require_in(raw: dict[str, Any], field: str, expected: set[str]) -> None:
    if raw.get(field) not in expected:
        raise ValueError(f"invalid {field}")


def _non_empty_text(value: object) -> str:
    return value if isinstance(value, str) and value.strip() else ""


def _unquote(value: str) -> str:
    return value.strip().strip('"').strip("'")


def _read_text(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8-sig")
    except OSError:
        return ""


def _rel(root: Path, path: Path) -> str:
    try:
        return path.relative_to(root).as_posix()
    except ValueError:
        return path.as_posix()


def _blocked_parent_admission(reason: str) -> dict[str, object]:
    return {
        "status": "blocked",
        "implementation_admission": "blocked_upstream_pending",
        "reason": reason,
    }


__all__ = [
    "BLOCKED_EVIDENCE_RELATIVE_PATH",
    "MATRIX_RELATIVE_PATH",
    "NATIVE_WINDOWS_TRANSCRIPT_RELATIVE_PATH",
    "PROVIDER_WORKFLOWS",
    "PROVIDER_WORKFLOWS_TRANSCRIPT_RELATIVE_PATH",
    "PUBLIC_PROVIDER_FREEZE_RELATIVE_PATH",
    "REQUIRED_WORKFLOWS",
    "WindowsHerdrProviderWorkflowRow",
    "WindowsHerdrPublicWorkflowEvidence",
    "WindowsHerdrWorkflowRow",
    "canonical_matrix_json",
    "default_blocked_public_workflow_matrix",
    "public_provider_freeze",
    "public_provider_names",
    "validate_windows_herdr_public_workflow_artifacts",
    "validate_windows_herdr_public_workflow_evidence",
    "windows_herdr_public_workflow_parent_admission",
]
