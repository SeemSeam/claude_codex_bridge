from __future__ import annotations

from pathlib import Path

import pytest

from provider_core.registry import TEST_DOUBLE_PROVIDER_NAMES, build_default_provider_manifests
from platforms.windows.release.workflow_matrix import (
    MATRIX_RELATIVE_PATH,
    PROVIDER_WORKFLOWS,
    REQUIRED_WORKFLOWS,
    canonical_matrix_json,
    default_blocked_public_workflow_matrix,
    public_provider_freeze,
    public_provider_names,
    validate_windows_herdr_public_workflow_artifacts,
    validate_windows_herdr_public_workflow_evidence,
    windows_herdr_public_workflow_parent_admission,
)


def _pass_row(workflow: str) -> dict[str, object]:
    return {
        "workflow": workflow,
        "status": "pass",
        "evidence_class": "native-windows",
        "command": f"ccb {workflow}",
        "artifact_ref": "evidence/native-windows-transcript.md",
        "host_evidence_ref": "evidence/native-windows-transcript.md#host",
        "backend_impl": "herdr",
        "os_platform": "win32",
        "cpu_arch": "x64",
        "reason": None,
        "beta_gap": None,
        "residual_risk": None,
    }


def _pass_provider_row(provider: str, workflow: str) -> dict[str, object]:
    return {
        "provider": provider,
        "workflow": workflow,
        "status": "pass",
        "evidence_class": "native-windows",
        "command": f"ccb ask {provider}",
        "artifact_ref": "evidence/provider-workflows-transcript.md",
        "host_evidence_ref": "evidence/native-windows-transcript.md#host",
        "backend_impl": "herdr",
        "pane_ref": f"herdr://pane/{provider}",
        "reason": None,
        "beta_gap": None,
        "residual_risk": None,
    }


def _supported_matrix() -> dict[str, object]:
    providers = public_provider_names()
    return {
        "schema_version": 1,
        "backend_impl": "herdr",
        "os_platform": "win32",
        "cpu_arch": "x64",
        "ccb_version": "8.6.7",
        "ccb_source_status": "matching-release",
        "herdr_version": "0.1.0",
        "herdr_auto_restore_mode": "disabled",
        "baseline_ref": ".codestable/features/baseline/acceptance.md",
        "release_surface_ref": ".codestable/features/release/acceptance.md",
        "user_surfaces_ref": ".codestable/features/surfaces/acceptance.md",
        "public_providers": providers,
        "required_workflows": list(REQUIRED_WORKFLOWS),
        "workflows": {workflow: "pass" for workflow in REQUIRED_WORKFLOWS},
        "workflow_rows": {workflow: _pass_row(workflow) for workflow in REQUIRED_WORKFLOWS},
        "provider_workflows": list(PROVIDER_WORKFLOWS),
        "provider_workflow_rows": {
            provider: {workflow: "pass" for workflow in PROVIDER_WORKFLOWS}
            for provider in providers
        },
        "provider_workflow_detail_rows": {
            f"{provider}:{workflow}": _pass_provider_row(provider, workflow)
            for provider in providers
            for workflow in PROVIDER_WORKFLOWS
        },
        "mobile_terminal_status": "pass",
        "config_ui_status": "pass",
        "windows_npm_install_dry_run_status": "pass",
        "beta_gaps": [],
        "residual_risks": [],
        "artifacts": {
            "matrix": MATRIX_RELATIVE_PATH.as_posix(),
            "native_windows_transcript": "evidence/native-windows-transcript.md",
            "provider_workflows_transcript": "evidence/provider-workflows-transcript.md",
        },
        "support_tier": "supported",
        "support_tier_is_candidate": True,
        "support_projection_allowed": True,
    }


def _write_repo_ref(root: Path, reference: object) -> None:
    if not isinstance(reference, str) or not reference.strip():
        return
    path = root / reference.split("#", 1)[0]
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("artifact\n", encoding="utf-8")


def _write_matrix_artifact_refs(root: Path, matrix: dict[str, object], *, parent_refs: bool = False) -> None:
    for reference in matrix["artifacts"].values():
        _write_repo_ref(root, reference)
    for row in matrix["workflow_rows"].values():
        _write_repo_ref(root, row["artifact_ref"])
        _write_repo_ref(root, row["host_evidence_ref"])
    for row in matrix["provider_workflow_detail_rows"].values():
        _write_repo_ref(root, row["artifact_ref"])
        _write_repo_ref(root, row["host_evidence_ref"])
    if parent_refs:
        for reference in (
            matrix["baseline_ref"],
            matrix["release_surface_ref"],
            matrix["user_surfaces_ref"],
        ):
            _write_repo_ref(root, reference)


def _write_parent_roadmap(root: Path) -> None:
    roadmap = root / ".codestable" / "roadmap" / "windows-native-herdr-ccb"
    roadmap.mkdir(parents=True)
    (roadmap / "windows-native-herdr-ccb-items.yaml").write_text(
        "\n".join(
            [
                "items:",
                "  - slug: windows-x64-release-surface",
                "    status: done",
                "    feature: 2026-07-31-windows-x64-release-surface",
                "  - slug: herdr-user-surfaces-parity",
                "    status: done",
                "    feature: 2026-07-31-herdr-user-surfaces-parity",
                "  - slug: native-windows-public-workflow-validation-matrix",
                "    depends_on: [windows-x64-release-surface, herdr-user-surfaces-parity]",
                "    status: in-progress",
                "    feature: 2026-07-31-native-windows-public-workflow-validation-matrix",
                "",
            ]
        ),
        encoding="utf-8",
    )


def _write_parent_acceptances(root: Path, body: str) -> None:
    for slug in ("windows-x64-release-surface", "herdr-user-surfaces-parity"):
        feature = root / ".codestable" / "features" / f"2026-07-31-{slug}"
        feature.mkdir(parents=True)
        (feature / f"{slug}-acceptance.md").write_text(
            "\n".join(
                [
                    "---",
                    "doc_type: feature-acceptance",
                    "status: passed",
                    "---",
                    "",
                    body,
                ]
            ),
            encoding="utf-8",
        )


def test_required_workflow_matrix_rejects_missing_or_drifting_keys() -> None:
    matrix = _supported_matrix()
    missing = REQUIRED_WORKFLOWS[-1]
    matrix["required_workflows"] = list(REQUIRED_WORKFLOWS[:-1])
    matrix["workflows"] = {key: value for key, value in matrix["workflows"].items() if key != missing}
    matrix["workflow_rows"] = {key: value for key, value in matrix["workflow_rows"].items() if key != missing}

    with pytest.raises(ValueError, match="required workflow"):
        validate_windows_herdr_public_workflow_evidence(matrix)


def test_non_pass_workflow_rows_require_reason() -> None:
    matrix = _supported_matrix()
    matrix["workflows"]["ping"] = "blocked"
    matrix["workflow_rows"]["ping"]["status"] = "blocked"

    with pytest.raises(ValueError, match="reason"):
        validate_windows_herdr_public_workflow_evidence(matrix)


def test_pass_workflow_rows_require_host_evidence_ref() -> None:
    matrix = _supported_matrix()
    matrix["workflow_rows"]["ping"]["host_evidence_ref"] = None

    with pytest.raises(ValueError, match="host_evidence_ref"):
        validate_windows_herdr_public_workflow_evidence(matrix)


def test_provider_summary_and_detail_rows_must_match() -> None:
    matrix = _supported_matrix()
    matrix["provider_workflow_detail_rows"]["codex:ask"]["status"] = "blocked"
    matrix["provider_workflow_detail_rows"]["codex:ask"]["reason"] = "credential missing"

    with pytest.raises(ValueError, match="provider workflow"):
        validate_windows_herdr_public_workflow_evidence(matrix)


def test_pass_provider_rows_require_host_evidence_ref() -> None:
    matrix = _supported_matrix()
    matrix["provider_workflow_detail_rows"]["codex:ask"]["host_evidence_ref"] = None

    with pytest.raises(ValueError, match="host_evidence_ref"):
        validate_windows_herdr_public_workflow_evidence(matrix)


def test_pass_provider_rows_require_pane_ref() -> None:
    matrix = _supported_matrix()
    matrix["provider_workflow_detail_rows"]["codex:ask"]["pane_ref"] = None

    with pytest.raises(ValueError, match="pane_ref"):
        validate_windows_herdr_public_workflow_evidence(matrix)


def test_public_providers_must_match_current_catalog() -> None:
    matrix = _supported_matrix()
    matrix["public_providers"] = matrix["public_providers"][:-1]
    matrix["provider_workflow_rows"] = {
        provider: rows
        for provider, rows in matrix["provider_workflow_rows"].items()
        if provider in matrix["public_providers"]
    }
    matrix["provider_workflow_detail_rows"] = {
        key: row
        for key, row in matrix["provider_workflow_detail_rows"].items()
        if row["provider"] in matrix["public_providers"]
    }

    with pytest.raises(ValueError, match="public provider"):
        validate_windows_herdr_public_workflow_evidence(matrix)


def test_public_providers_reject_empty_catalog() -> None:
    matrix = _supported_matrix()
    matrix["public_providers"] = []
    matrix["provider_workflow_rows"] = {}
    matrix["provider_workflow_detail_rows"] = {}

    with pytest.raises(ValueError, match="public provider"):
        validate_windows_herdr_public_workflow_evidence(matrix)


def test_support_tier_must_remain_candidate_evidence() -> None:
    matrix = _supported_matrix()
    matrix["support_tier_is_candidate"] = False

    with pytest.raises(ValueError, match="candidate"):
        validate_windows_herdr_public_workflow_evidence(matrix)


def test_missing_required_field_with_extra_field_still_fails() -> None:
    matrix = _supported_matrix()
    del matrix["baseline_ref"]
    matrix["extra"] = "ignored"

    with pytest.raises(ValueError, match="missing required"):
        validate_windows_herdr_public_workflow_evidence(matrix)


def test_unknown_top_level_field_fails_closed() -> None:
    matrix = _supported_matrix()
    matrix["extra"] = "ignored"

    with pytest.raises(ValueError, match="unknown fields"):
        validate_windows_herdr_public_workflow_evidence(matrix)


def test_unknown_workflow_row_field_fails_closed() -> None:
    matrix = _supported_matrix()
    matrix["workflow_rows"]["ping"]["extra"] = "ignored"

    with pytest.raises(ValueError, match="workflow row has unknown fields"):
        validate_windows_herdr_public_workflow_evidence(matrix)


def test_missing_provider_detail_row_field_fails_closed() -> None:
    matrix = _supported_matrix()
    del matrix["provider_workflow_detail_rows"]["codex:ask"]["artifact_ref"]

    with pytest.raises(ValueError, match="provider workflow detail row missing required fields"):
        validate_windows_herdr_public_workflow_evidence(matrix)


def test_non_string_scalar_fields_fail_closed() -> None:
    matrix = _supported_matrix()
    matrix["workflow_rows"]["ping"]["command"] = ["ccb ping"]

    with pytest.raises(ValueError, match="must be string or null"):
        validate_windows_herdr_public_workflow_evidence(matrix)


def test_herdr_version_must_be_non_empty_string() -> None:
    matrix = _supported_matrix()
    matrix["herdr_version"] = []

    with pytest.raises(ValueError, match="herdr_version"):
        validate_windows_herdr_public_workflow_evidence(matrix)


def test_parent_refs_must_be_string_or_null() -> None:
    matrix = _supported_matrix()
    matrix["baseline_ref"] = []
    matrix["support_projection_allowed"] = False
    matrix["support_tier"] = "beta"

    with pytest.raises(ValueError, match="baseline_ref"):
        validate_windows_herdr_public_workflow_evidence(matrix)


def test_all_pass_matrix_allows_candidate_supported_projection() -> None:
    validated = validate_windows_herdr_public_workflow_evidence(_supported_matrix())

    assert validated["support_projection_allowed"] is True
    assert validated["support_tier"] == "supported"
    assert validated["support_tier_is_candidate"] is True


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("mobile_terminal_status", "partial"),
        ("config_ui_status", "blocked"),
        ("windows_npm_install_dry_run_status", "not-run"),
        ("ccb_source_status", "blocked"),
        ("herdr_auto_restore_mode", "observe-only"),
    ],
)
def test_hard_gate_fields_block_candidate_supported_projection(field: str, value: str) -> None:
    matrix = _supported_matrix()
    matrix[field] = value
    if field in {"mobile_terminal_status", "config_ui_status"}:
        workflow = field.removesuffix("_status")
        matrix["workflows"][workflow] = value
        matrix["workflow_rows"][workflow]["status"] = value
        matrix["workflow_rows"][workflow]["reason"] = "hard gate not satisfied"
    matrix["support_projection_allowed"] = False
    matrix["support_tier"] = "beta"
    matrix["beta_gaps"] = ["hard gate not satisfied"]

    validated = validate_windows_herdr_public_workflow_evidence(matrix)

    assert validated["support_projection_allowed"] is False
    assert validated["support_tier"] != "supported"


def test_mobile_and_config_top_level_statuses_match_workflow_rows() -> None:
    matrix = default_blocked_public_workflow_matrix(reason="blocked")
    matrix["mobile_terminal_status"] = "not-run"

    with pytest.raises(ValueError, match="mobile_terminal_status"):
        validate_windows_herdr_public_workflow_evidence(matrix)


def test_candidate_supported_is_rejected_when_any_required_gate_is_not_pass() -> None:
    matrix = _supported_matrix()
    matrix["provider_workflow_rows"]["codex"]["cancel"] = "blocked"
    matrix["provider_workflow_detail_rows"]["codex:cancel"]["status"] = "blocked"
    matrix["provider_workflow_detail_rows"]["codex:cancel"]["reason"] = "provider CLI unavailable"
    matrix["support_projection_allowed"] = True
    matrix["support_tier"] = "supported"

    with pytest.raises(ValueError, match="supported"):
        validate_windows_herdr_public_workflow_evidence(matrix)


def test_all_pass_matrix_requires_projection_allowed_true() -> None:
    matrix = _supported_matrix()
    matrix["support_projection_allowed"] = False
    matrix["support_tier"] = "beta"

    with pytest.raises(ValueError, match="support_projection_allowed"):
        validate_windows_herdr_public_workflow_evidence(matrix)


def test_supported_projection_requires_parent_refs() -> None:
    matrix = _supported_matrix()
    matrix["release_surface_ref"] = None

    with pytest.raises(ValueError, match="parent refs"):
        validate_windows_herdr_public_workflow_evidence(matrix)


def test_root_aware_artifact_validation_rejects_missing_pass_refs(tmp_path: Path) -> None:
    matrix = _supported_matrix()
    _write_matrix_artifact_refs(tmp_path, matrix, parent_refs=True)
    matrix["workflow_rows"]["ping"]["artifact_ref"] = "evidence/does-not-exist.md"

    with pytest.raises(ValueError, match="artifact"):
        validate_windows_herdr_public_workflow_artifacts(tmp_path, matrix)


def test_root_aware_artifact_validation_checks_pass_rows_in_blocked_matrix(tmp_path: Path) -> None:
    matrix = default_blocked_public_workflow_matrix(reason="partial transcript")
    _write_matrix_artifact_refs(tmp_path, matrix)
    matrix["workflows"]["ping"] = "pass"
    matrix["workflow_rows"]["ping"]["status"] = "pass"
    matrix["workflow_rows"]["ping"]["evidence_class"] = "native-windows"
    matrix["workflow_rows"]["ping"]["artifact_ref"] = "evidence/missing-ping.md"

    with pytest.raises(ValueError, match="workflow artifact ping"):
        validate_windows_herdr_public_workflow_artifacts(tmp_path, matrix)


def test_root_aware_artifact_validation_checks_blocked_row_refs(tmp_path: Path) -> None:
    matrix = default_blocked_public_workflow_matrix(reason="blocked")
    _write_matrix_artifact_refs(tmp_path, matrix)
    matrix["workflow_rows"]["ping"]["artifact_ref"] = "evidence/missing-blocked-ping.md"

    with pytest.raises(ValueError, match="workflow artifact ping"):
        validate_windows_herdr_public_workflow_artifacts(tmp_path, matrix)


def test_root_aware_artifact_validation_checks_blocked_provider_refs(tmp_path: Path) -> None:
    matrix = default_blocked_public_workflow_matrix(reason="blocked")
    _write_matrix_artifact_refs(tmp_path, matrix)
    matrix["provider_workflow_detail_rows"]["codex:ask"]["artifact_ref"] = "evidence/missing-provider.md"

    with pytest.raises(ValueError, match="provider artifact codex:ask"):
        validate_windows_herdr_public_workflow_artifacts(tmp_path, matrix)


def test_root_aware_artifact_validation_checks_top_level_artifacts(tmp_path: Path) -> None:
    matrix = default_blocked_public_workflow_matrix(reason="blocked")
    _write_matrix_artifact_refs(tmp_path, matrix)
    matrix["artifacts"]["blocked_evidence"] = "evidence/missing-top-level.md"

    with pytest.raises(ValueError, match="matrix artifact blocked_evidence"):
        validate_windows_herdr_public_workflow_artifacts(tmp_path, matrix)


def test_root_aware_artifact_validation_rejects_path_escape(tmp_path: Path) -> None:
    matrix = _supported_matrix()
    _write_matrix_artifact_refs(tmp_path, matrix, parent_refs=True)
    outside = tmp_path.parent / "windows-herdr-outside-parent.md"
    outside.write_text("outside root\n", encoding="utf-8")
    matrix["release_surface_ref"] = f"../{outside.name}"

    with pytest.raises(ValueError, match="outside root"):
        validate_windows_herdr_public_workflow_artifacts(tmp_path, matrix)


def test_root_aware_artifact_validation_rejects_absolute_repo_ref(tmp_path: Path) -> None:
    matrix = default_blocked_public_workflow_matrix(reason="blocked")
    _write_matrix_artifact_refs(tmp_path, matrix)
    absolute = tmp_path / "absolute-evidence.md"
    absolute.write_text("artifact\n", encoding="utf-8")
    matrix["artifacts"]["blocked_evidence"] = str(absolute)

    with pytest.raises(ValueError, match="repo-relative"):
        validate_windows_herdr_public_workflow_artifacts(tmp_path, matrix)


@pytest.mark.parametrize(
    "reference",
    [
        "/tmp/evidence.md",
        "C:/tmp/evidence.md",
        "C:tmp/evidence.md",
        r"C:\tmp\evidence.md",
        r"\Users\me\evidence.md",
        "//server/share/evidence.md",
        r"\\server\share\evidence.md",
    ],
)
def test_root_aware_artifact_validation_rejects_windows_absolute_repo_refs(
    tmp_path: Path,
    reference: str,
) -> None:
    matrix = default_blocked_public_workflow_matrix(reason="blocked")
    _write_matrix_artifact_refs(tmp_path, matrix)
    matrix["artifacts"]["blocked_evidence"] = reference

    with pytest.raises(ValueError, match="repo-relative"):
        validate_windows_herdr_public_workflow_artifacts(tmp_path, matrix)


def test_root_aware_artifact_validation_rejects_missing_parent_refs(tmp_path: Path) -> None:
    matrix = _supported_matrix()
    _write_matrix_artifact_refs(tmp_path, matrix)

    with pytest.raises(ValueError, match="parent ref"):
        validate_windows_herdr_public_workflow_artifacts(tmp_path, matrix)


def test_root_aware_artifact_validation_checks_non_empty_parent_refs_when_blocked(tmp_path: Path) -> None:
    matrix = default_blocked_public_workflow_matrix(
        reason="blocked",
        release_surface_ref="evidence/missing-parent.md",
    )
    _write_matrix_artifact_refs(tmp_path, matrix)

    with pytest.raises(ValueError, match="parent ref release_surface_ref"):
        validate_windows_herdr_public_workflow_artifacts(tmp_path, matrix)


def test_default_blocked_skeleton_covers_all_workflows_and_providers() -> None:
    providers = public_provider_names()
    matrix = default_blocked_public_workflow_matrix(reason="Native Windows transcript missing")
    validated = validate_windows_herdr_public_workflow_evidence(matrix)

    assert set(validated["required_workflows"]) == set(REQUIRED_WORKFLOWS)
    assert set(validated["workflow_rows"]) == set(REQUIRED_WORKFLOWS)
    assert validated["workflow_rows"]["mounted"]["command"] == "ccb ping all"
    assert validated["workflow_rows"]["watch"]["command"] == "ccb pend --watch <target>"
    assert set(validated["provider_workflow_rows"]) == set(providers)
    assert set(validated["provider_workflow_rows"]["codex"]) == set(PROVIDER_WORKFLOWS)
    assert set(validated["provider_workflow_detail_rows"]) == {
        f"{provider}:{workflow}"
        for provider in providers
        for workflow in PROVIDER_WORKFLOWS
    }
    assert validated["support_projection_allowed"] is False
    assert validated["support_tier"] == "beta"


def test_blocked_matrix_generator_rejects_provider_subset() -> None:
    with pytest.raises(ValueError, match="provider catalog"):
        default_blocked_public_workflow_matrix(
            public_providers=["codex"],
            reason="blocked",
        )


def test_public_provider_catalog_freeze_excludes_test_doubles() -> None:
    expected = sorted(
        manifest.provider
        for manifest in build_default_provider_manifests(
            include_optional=True,
            include_test_doubles=False,
        )
    )

    assert public_provider_names() == expected
    assert not (set(public_provider_names()) & set(TEST_DOUBLE_PROVIDER_NAMES))


def test_public_provider_catalog_freeze_artifact_matches_current_catalog() -> None:
    freeze = public_provider_freeze()

    assert freeze["public_providers"] == public_provider_names()
    assert freeze["provider_workflows"] == list(PROVIDER_WORKFLOWS)
    assert freeze["excluded_test_doubles"] is True


def test_parent_admission_blocks_missing_parent_acceptance_refs(tmp_path: Path) -> None:
    _write_parent_roadmap(tmp_path)

    admission = windows_herdr_public_workflow_parent_admission(tmp_path)

    assert admission["status"] == "blocked"
    assert "parent acceptance missing" in admission["reason"]


def test_parent_admission_rejects_negated_artifact_refs(tmp_path: Path) -> None:
    _write_parent_roadmap(tmp_path)
    _write_parent_acceptances(tmp_path, "No artifact refs are available for this parent.")

    admission = windows_herdr_public_workflow_parent_admission(tmp_path)

    assert admission["status"] == "blocked"
    assert "lacks artifact refs" in admission["reason"]


@pytest.mark.parametrize(
    "body",
    [
        "Artifact refs are unavailable: evidence/foo.md",
        "Artifact refs not available: evidence/foo.md",
        "No CMD-001 evidence is available.",
        "Artifact refs were not recorded: CMD-001",
        "Artifact refs are unrecorded: CMD-001",
        "Artifact refs were not passed: evidence/foo.md",
        "Artifact refs failed validation: evidence/foo.md",
        "Artifact refs are blocked: evidence/foo.md",
        "Artifact refs: none.",
        "Artifact refs are absent: evidence/foo.md",
        "Artifact refs omitted: evidence/foo.md",
        "Artifact refs TBD: evidence/foo.md",
        "Artifact refs pending: evidence/foo.md",
    ],
)
def test_parent_admission_rejects_artifact_ref_negative_phrasing(tmp_path: Path, body: str) -> None:
    _write_parent_roadmap(tmp_path)
    _write_parent_acceptances(tmp_path, body)

    admission = windows_herdr_public_workflow_parent_admission(tmp_path)

    assert admission["status"] == "blocked"
    assert "lacks artifact refs" in admission["reason"]


def test_parent_admission_rejects_missing_repo_artifact_ref(tmp_path: Path) -> None:
    _write_parent_roadmap(tmp_path)
    _write_parent_acceptances(
        tmp_path,
        "Artifact refs: .codestable/features/parent/evidence/missing.json",
    )

    admission = windows_herdr_public_workflow_parent_admission(tmp_path)

    assert admission["status"] == "blocked"
    assert "lacks artifact refs" in admission["reason"]


def test_parent_admission_rejects_cmd_line_with_missing_repo_artifact_ref(tmp_path: Path) -> None:
    _write_parent_roadmap(tmp_path)
    _write_parent_acceptances(
        tmp_path,
        "Artifact refs: CMD-001 .codestable/features/parent/evidence/missing.json",
    )

    admission = windows_herdr_public_workflow_parent_admission(tmp_path)

    assert admission["status"] == "blocked"
    assert "lacks artifact refs" in admission["reason"]


def test_parent_admission_rejects_repo_artifact_line_with_unresolved_cmd_ref(tmp_path: Path) -> None:
    _write_parent_roadmap(tmp_path)
    path = tmp_path / ".codestable/features/parent/evidence/result.json"
    path.parent.mkdir(parents=True)
    path.write_text("parent artifact\n", encoding="utf-8")
    _write_parent_acceptances(
        tmp_path,
        "Artifact refs: CMD-001 .codestable/features/parent/evidence/result.json",
    )

    admission = windows_herdr_public_workflow_parent_admission(tmp_path)

    assert admission["status"] == "blocked"
    assert "lacks artifact refs" in admission["reason"]


def test_parent_admission_rejects_bare_cmd_ref(tmp_path: Path) -> None:
    _write_parent_roadmap(tmp_path)
    _write_parent_acceptances(tmp_path, "Artifact refs: CMD-001")

    admission = windows_herdr_public_workflow_parent_admission(tmp_path)

    assert admission["status"] == "blocked"
    assert "lacks artifact refs" in admission["reason"]


def test_parent_admission_accepts_cmd_ref_with_matching_evidence_file(tmp_path: Path) -> None:
    _write_parent_roadmap(tmp_path)
    _write_parent_acceptances(tmp_path, "Artifact refs: CMD-001")
    for slug in ("windows-x64-release-surface", "herdr-user-surfaces-parity"):
        evidence = tmp_path / ".codestable" / "features" / f"2026-07-31-{slug}" / "evidence"
        evidence.mkdir(parents=True, exist_ok=True)
        (evidence / "cmd-001-parent-evidence.md").write_text("parent evidence\n", encoding="utf-8")

    admission = windows_herdr_public_workflow_parent_admission(tmp_path)

    assert admission["status"] == "ready"
    assert admission["implementation_admission"] == "admitted"


def test_parent_admission_accepts_fixture_roadmap_refs(tmp_path: Path) -> None:
    _write_parent_roadmap(tmp_path)
    body = "Artifact refs: .codestable/features/parent/evidence/result.json"
    path = tmp_path / ".codestable/features/parent/evidence/result.json"
    path.parent.mkdir(parents=True)
    path.write_text("parent artifact\n", encoding="utf-8")
    _write_parent_acceptances(tmp_path, body)

    admission = windows_herdr_public_workflow_parent_admission(tmp_path)

    assert admission["status"] == "ready"
    assert admission["implementation_admission"] == "admitted"
    assert admission["parent_refs"]["windows-x64-release-surface"].endswith(
        "windows-x64-release-surface/windows-x64-release-surface-acceptance.md"
    )
    assert admission["parent_refs"]["herdr-user-surfaces-parity"].endswith(
        "herdr-user-surfaces-parity/herdr-user-surfaces-parity-acceptance.md"
    )


def test_canonical_matrix_json_is_deterministic() -> None:
    matrix = default_blocked_public_workflow_matrix(reason="blocked")

    assert canonical_matrix_json(matrix) == canonical_matrix_json(dict(reversed(list(matrix.items()))))
