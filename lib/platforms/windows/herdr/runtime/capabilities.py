from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping

from terminal_runtime.mux_backend_contract import (
    MuxCapabilitiesV2,
    MuxCommandErrorV2,
    make_capabilities,
)

_CORE_REQUIRED_CAPABILITIES = {
    "session_attach",
    "pane_spawn",
    "send_input",
    "read_output",
    "kill_pane",
}
_OPERATION_REQUIRED_CAPABILITIES = {
    "capabilities": ("session_attach",),
    "prepare_server": ("session_attach",),
    "create_session": ("session_attach", "workspace_create", "workspace_metadata", "pane_metadata"),
    "restore_session": ("session_attach", "workspace_list"),
    "namespace_alive": ("session_attach", "pane_list"),
    "list_windows": ("workspace_list", "pane_list"),
    "ensure_window": (
        "workspace_list",
        "workspace_create",
        "workspace_focus",
        "pane_list",
        "workspace_metadata",
        "pane_metadata",
    ),
    "window_root_pane": ("workspace_list", "pane_list"),
    "set_pane_identity": ("pane_list", "pane_metadata"),
    "report_pane_agent": ("pane_list", "pane_metadata"),
    "report_pane_agent_session": ("pane_list", "pane_metadata"),
    "release_pane_agent": ("pane_list", "pane_metadata"),
    "describe_pane": ("pane_list",),
    "list_panes_by_user_options": ("pane_list",),
    "create_pane": ("pane_list", "pane_split", "pane_run"),
    "respawn_pane": ("pane_list", "pane_run"),
    "move_pane": ("pane_list", "pane_split"),
    "reflow_window": ("workspace_list", "pane_list"),
    "send_text": ("send_input",),
    "capture_pane": ("read_output",),
    "kill_pane": ("kill_pane",),
    "select_window": ("workspace_list", "pane_list", "workspace_focus"),
    "kill_window": ("workspace_list", "pane_list", "workspace_close"),
    "destroy_namespace": ("workspace_list", "pane_list", "workspace_close"),
    "kill_server": ("workspace_list", "pane_list", "workspace_close"),
    "rename_window": ("workspace_list", "pane_list", "workspace_metadata", "pane_metadata"),
    "attach_namespace": ("workspace_list", "pane_list", "workspace_focus"),
}
_KNOWN_CAPABILITIES = _CORE_REQUIRED_CAPABILITIES | {
    name for names in _OPERATION_REQUIRED_CAPABILITIES.values() for name in names
}
_SUPPORTED_STATUS = {"supported", "partial", "unsupported", "workaround"}
_CONTINUE_RECOMMENDATIONS = {"continue", "continue-with-gaps"}
_PASS_VERDICTS = {"pass", "partial"}
_STOP_RECOMMENDATIONS = {"stop", "needs-upstream-issue"}
_BLOCKING_VERDICTS = {"blocked", "failed"}


@dataclass(frozen=True)
class HerdrCapabilityGate:
    capabilities: MuxCapabilitiesV2 | None
    failure_reason: str | None = None
    diagnostic: str | None = None
    capability_report_ref: str | None = None

    @classmethod
    def from_spike_evidence(
        cls,
        spike_evidence: Mapping[str, object] | None,
        *,
        capability_report_ref: str | None,
    ) -> "HerdrCapabilityGate":
        if not spike_evidence:
            return cls(
                capabilities=None,
                failure_reason="herdr-capability-missing",
                diagnostic="Herdr capability evidence is unavailable",
                capability_report_ref=capability_report_ref,
            )

        adapter_recommendation = str(spike_evidence.get("adapter_recommendation") or "").strip()
        verdict = str(spike_evidence.get("verdict") or "").strip()
        failure_class = str(spike_evidence.get("failure_class") or "").strip()
        if adapter_recommendation in _STOP_RECOMMENDATIONS:
            return cls._blocked(
                capability_report_ref,
                f"Herdr spike adapter recommendation is {adapter_recommendation}",
            )
        if adapter_recommendation not in _CONTINUE_RECOMMENDATIONS:
            return cls._blocked(
                capability_report_ref,
                "Herdr spike adapter recommendation is missing or unknown",
            )
        if verdict in _BLOCKING_VERDICTS:
            return cls._blocked(capability_report_ref, f"Herdr spike verdict is {verdict}")
        if verdict not in _PASS_VERDICTS:
            return cls._blocked(capability_report_ref, "Herdr spike verdict is missing or unknown")
        if failure_class and failure_class not in {"none", "windows-beta-gap"}:
            return cls._blocked(
                capability_report_ref,
                f"Herdr spike failure_class is {failure_class}",
            )

        projection = spike_evidence.get("capability_projection")
        if not isinstance(projection, Mapping):
            return cls._blocked(capability_report_ref, "Herdr capability projection is missing")

        command_status = _status_mapping(projection.get("command_status"))
        semantic_status = _status_mapping(projection.get("semantic_status"))
        windows_beta_gaps = _string_list(projection.get("windows_beta_gaps"))
        blocking_gaps = _string_list(projection.get("blocking_gaps"))
        if command_status is None or semantic_status is None or windows_beta_gaps is None or blocking_gaps is None:
            return cls._blocked(
                capability_report_ref,
                "Herdr capability projection contains unknown status values or malformed gap fields",
            )
        capabilities = make_capabilities(
            backend_impl="herdr",
            command_status=command_status,
            semantic_status=semantic_status,
            windows_beta_gaps=windows_beta_gaps,
            blocking_gaps=blocking_gaps,
            source_ref=capability_report_ref,
        )
        capabilities["adapter_recommendation"] = adapter_recommendation  # type: ignore[typeddict-unknown-key]
        capabilities["verdict"] = verdict  # type: ignore[typeddict-unknown-key]
        capabilities["failure_class"] = failure_class or "none"  # type: ignore[typeddict-unknown-key]
        if not required_capability_statuses_supported(capabilities):
            return cls._blocked(
                capability_report_ref,
                "Herdr capability projection lacks required supported namespace capabilities",
            )
        return cls(
            capabilities=capabilities,
            failure_reason=None,
            diagnostic=None,
            capability_report_ref=capability_report_ref,
        )

    @classmethod
    def _blocked(
        cls,
        capability_report_ref: str | None,
        diagnostic: str,
    ) -> "HerdrCapabilityGate":
        return cls(
            capabilities=None,
            failure_reason="unsupported-capability",
            diagnostic=diagnostic,
            capability_report_ref=capability_report_ref,
        )

    def require_supported(self, operation: str) -> MuxCapabilitiesV2:
        if self.capabilities is not None:
            unsupported = unsupported_capability_names(self.capabilities, operation)
            if not unsupported:
                return self.capabilities
            raise MuxCommandErrorV2(
                category="unsupported",
                backend_impl="herdr",
                operation=operation,
                detail=f"Herdr capability gate is missing supported capabilities for {operation}",
                evidence={
                    "failure_reason": "unsupported-capability",
                    "capability_report_ref": self.capability_report_ref,
                    "unsupported_capabilities": unsupported,
                },
            )
        raise MuxCommandErrorV2(
            category="unsupported",
            backend_impl="herdr",
            operation=operation,
            detail=self.diagnostic or "Herdr capability gate is blocked",
            evidence={
                "failure_reason": self.failure_reason or "unsupported-capability",
                "capability_report_ref": self.capability_report_ref,
            },
        )


def _status_mapping(raw: object) -> dict[str, str] | None:
    if not isinstance(raw, Mapping):
        return None
    result: dict[str, str] = {}
    for key, value in raw.items():
        key_str = str(key)
        status = str(value or "").strip()
        if status not in _SUPPORTED_STATUS:
            return None
        if key_str in _KNOWN_CAPABILITIES:
            result[key_str] = status
    if not _CORE_REQUIRED_CAPABILITIES.issubset(result):
        return None
    return result


def _string_list(raw: object) -> list[str] | None:
    if not isinstance(raw, list):
        return None
    result: list[str] = []
    for item in raw:
        if not isinstance(item, str):
            return None
        item = item.strip()
        if item:
            result.append(item)
    return result


def herdr_capability_report_supported(capabilities: Mapping[str, object]) -> bool:
    adapter_recommendation = str(capabilities.get("adapter_recommendation") or "").strip()
    verdict = str(capabilities.get("verdict") or "").strip()
    failure_class = str(capabilities.get("failure_class") or "").strip()
    windows_beta_gaps = capabilities.get("windows_beta_gaps")
    return (
        adapter_recommendation in _CONTINUE_RECOMMENDATIONS
        and verdict in _PASS_VERDICTS
        and failure_class in {"", "none", "windows-beta-gap"}
        and isinstance(windows_beta_gaps, list)
        and not windows_beta_gaps
        and required_capability_statuses_supported(capabilities)
    )


def required_capability_statuses_supported(capabilities: Mapping[str, object]) -> bool:
    return not unsupported_capability_names(capabilities, "capabilities")


def unsupported_capability_names(capabilities: Mapping[str, object], operation: str) -> list[str]:
    command_status = capabilities.get("command_status")
    semantic_status = capabilities.get("semantic_status")
    blocking_gaps = capabilities.get("blocking_gaps")
    windows_beta_gaps = capabilities.get("windows_beta_gaps")
    if (
        capabilities.get("backend_impl") != "herdr"
        or not isinstance(command_status, Mapping)
        or not isinstance(semantic_status, Mapping)
        or not isinstance(blocking_gaps, list)
        or not isinstance(windows_beta_gaps, list)
    ):
        return _capability_keys_for_operation(operation)
    blocked = {str(item) for item in blocking_gaps if str(item).strip()}
    beta_gaps = {str(item) for item in windows_beta_gaps if str(item).strip()}
    required = set(_capability_keys_for_operation(operation))
    unsupported: list[str] = []
    for name in sorted(required):
        if (
            name in blocked
            or name in beta_gaps
            or command_status.get(name) != "supported"
            or semantic_status.get(name) != "supported"
        ):
            unsupported.append(name)
    return unsupported


def _capability_keys_for_operation(operation: str) -> list[str]:
    return list(_OPERATION_REQUIRED_CAPABILITIES.get(operation, (operation,)))


__all__ = [
    "HerdrCapabilityGate",
    "herdr_capability_report_supported",
    "required_capability_statuses_supported",
    "unsupported_capability_names",
]
