from __future__ import annotations

from collections.abc import Callable, Mapping
from typing import Literal, TypedDict

from terminal_runtime.mux_backend_contract import (
    MuxCommandErrorV2,
    MuxNamespaceRefV2,
    MuxOperationEvidenceV2,
    MuxPaneRefV2,
    make_namespace_ref,
    make_operation_evidence,
    make_pane_ref,
)

EXPECTED_HERDR_API_SCHEMA = "Herdr API"


class HerdrServerInfo(TypedDict):
    version: str
    api_schema: str
    platform: Literal["windows"]
    arch: Literal["x64"]
    socket_ref: str


HerdrRequestFn = Callable[[str, dict[str, object]], Mapping[str, object]]


class HerdrSocketClient:
    def __init__(
        self,
        *,
        request_fn: HerdrRequestFn,
        socket_ref: str,
        expected_api_schema: str = EXPECTED_HERDR_API_SCHEMA,
        allow_session_scoped_ipc_refs: bool = False,
    ) -> None:
        self._request_fn = request_fn
        self._socket_ref = socket_ref
        self._expected_api_schema = expected_api_schema
        self._allow_session_scoped_ipc_refs = allow_session_scoped_ipc_refs

    @property
    def socket_ref(self) -> str:
        return self._socket_ref

    @property
    def session_name(self) -> str:
        session_name = getattr(self._request_fn, "session_name", None)
        return str(session_name or "").strip()

    @property
    def allow_session_scoped_ipc_refs(self) -> bool:
        return self._allow_session_scoped_ipc_refs

    def server_info(self) -> HerdrServerInfo:
        response = self._request("server_info", {}, require_result=True)
        version = str(response.get("version") or "").strip()
        api_schema = str(response.get("api_schema") or "").strip()
        platform = str(response.get("platform") or "").strip()
        arch = str(response.get("arch") or "").strip()
        if (
            api_schema != self._expected_api_schema
            or not version
            or platform != "windows"
            or arch != "x64"
        ):
            raise MuxCommandErrorV2(
                category="schema-mismatch",
                backend_impl="herdr",
                operation="server_info",
                detail="Herdr server_info does not match the expected Herdr contract",
                ipc_ref=self._socket_ref,
                evidence={
                    "expected_api_schema": self._expected_api_schema,
                    "actual_api_schema": api_schema,
                    "actual_version": version,
                    "expected_platform": "windows",
                    "actual_platform": platform,
                    "expected_arch": "x64",
                    "actual_arch": arch,
                    "socket_ref": self._socket_ref,
                },
            )
        return {
            "version": version,
            "api_schema": api_schema,
            "platform": "windows",
            "arch": "x64",
            "socket_ref": self._socket_ref,
        }

    def create_session(
        self,
        *,
        project_id: str,
        cwd: str,
        title: str,
    ) -> MuxNamespaceRefV2:
        response = self._request(
            "create_session",
            {"project_id": project_id, "cwd": cwd, "title": title},
            require_result=True,
        )
        return self._namespace_ref(
            "create_session",
            response,
            fallback_session_name=title or project_id,
        )

    def restore_session(self, *, restore_token: str) -> MuxNamespaceRefV2:
        session_name, namespace_id = _split_restore_token(restore_token)
        response = self._request("restore_session", {"restore_token": restore_token}, require_result=True)
        actual_namespace_id = _namespace_id_from_response(response)
        actual_session_name = str(response.get("session_name") or "").strip()
        actual_restore_token = str(response.get("restore_token") or "").strip()
        if not actual_namespace_id:
            raise MuxCommandErrorV2(
                category="command-failed",
                backend_impl="herdr",
                operation="restore_session",
                detail="Herdr restore_session response is missing namespace_id",
                ipc_ref=self._socket_ref,
                evidence={"socket_ref": self._socket_ref},
            )
        if actual_namespace_id != namespace_id:
            raise MuxCommandErrorV2(
                category="command-failed",
                backend_impl="herdr",
                operation="restore_session",
                detail="Herdr restore_session returned a namespace different from the requested restore_token",
                ipc_ref=self._socket_ref,
                evidence={
                    "socket_ref": self._socket_ref,
                    "expected_namespace_id": namespace_id,
                    "actual_namespace_id": actual_namespace_id,
                },
            )
        if not actual_session_name:
            raise MuxCommandErrorV2(
                category="command-failed",
                backend_impl="herdr",
                operation="restore_session",
                detail="Herdr restore_session response is missing session_name",
                ipc_ref=self._socket_ref,
                evidence={"socket_ref": self._socket_ref},
            )
        if actual_session_name != session_name:
            raise MuxCommandErrorV2(
                category="command-failed",
                backend_impl="herdr",
                operation="restore_session",
                detail="Herdr restore_session returned a session different from the requested restore_token",
                ipc_ref=self._socket_ref,
                evidence={
                    "socket_ref": self._socket_ref,
                    "expected_session_name": session_name,
                    "actual_session_name": actual_session_name,
                },
            )
        if not actual_restore_token:
            raise MuxCommandErrorV2(
                category="command-failed",
                backend_impl="herdr",
                operation="restore_session",
                detail="Herdr restore_session response is missing restore_token",
                ipc_ref=self._socket_ref,
                evidence={"socket_ref": self._socket_ref},
            )
        if actual_restore_token != restore_token:
            raise MuxCommandErrorV2(
                category="command-failed",
                backend_impl="herdr",
                operation="restore_session",
                detail="Herdr restore_session returned a restore_token different from the requested token",
                ipc_ref=self._socket_ref,
                evidence={
                    "socket_ref": self._socket_ref,
                    "expected_restore_token": restore_token,
                    "actual_restore_token": actual_restore_token,
                },
            )
        return self._namespace_ref(
            "restore_session",
            response,
            fallback_session_name=session_name,
        )

    def list_windows(self, namespace: MuxNamespaceRefV2) -> list[Mapping[str, object]]:
        response = self._request(
            "list_windows",
            {
                "namespace_id": namespace["namespace_id"],
                "session_name": namespace["session_name"],
                "ipc_ref": self._socket_ref,
            },
            require_status=True,
        )
        windows = response.get("windows")
        return [item for item in windows if isinstance(item, Mapping)] if isinstance(windows, list) else []

    def ensure_window(
        self,
        namespace: MuxNamespaceRefV2,
        *,
        window_name: str,
        cwd: str,
        select: bool,
    ) -> Mapping[str, object]:
        return self._request(
            "ensure_window",
            {
                "namespace_id": namespace["namespace_id"],
                "session_name": namespace["session_name"],
                "ipc_ref": self._socket_ref,
                "window_name": window_name,
                "cwd": cwd,
                "select": select,
            },
            require_status=True,
        )

    def window_root_pane(
        self,
        namespace: MuxNamespaceRefV2,
        *,
        window_name: str,
    ) -> MuxPaneRefV2:
        response = self._request(
            "window_root_pane",
            {
                "namespace_id": namespace["namespace_id"],
                "session_name": namespace["session_name"],
                "ipc_ref": self._socket_ref,
                "window_name": window_name,
            },
            require_status=True,
        )
        pane = response.get("pane")
        if not isinstance(pane, Mapping):
            pane = response
        pane_id = str(pane.get("pane_id") or response.get("pane_id") or "").strip()
        response_session_name = str(
            pane.get("session_name") or response.get("session_name") or ""
        ).strip()
        if response_session_name and response_session_name != namespace["session_name"]:
            raise MuxCommandErrorV2(
                category="command-failed",
                backend_impl="herdr",
                operation="window_root_pane",
                detail="Herdr window_root_pane returned a session different from the requested namespace",
                ipc_ref=self._socket_ref,
                evidence={
                    "socket_ref": self._socket_ref,
                    "expected_session_name": namespace["session_name"],
                    "actual_session_name": response_session_name,
                },
            )
        session_name = response_session_name or namespace["session_name"]
        try:
            return make_pane_ref(
                backend_impl="herdr",
                pane_id=pane_id,
                session_name=session_name,
                window_name=window_name,
            )
        except ValueError as exc:
            raise MuxCommandErrorV2(
                category="command-failed",
                backend_impl="herdr",
                operation="window_root_pane",
                detail=str(exc),
                ipc_ref=self._socket_ref,
                evidence={"socket_ref": self._socket_ref},
            ) from exc

    def list_panes(self, namespace: MuxNamespaceRefV2 | None = None) -> list[Mapping[str, object]]:
        payload: dict[str, object] = {"ipc_ref": self._socket_ref}
        if namespace is not None:
            payload.update(
                {
                    "namespace_id": namespace["namespace_id"],
                    "session_name": namespace["session_name"],
                }
            )
        response = self._request("list_panes", payload, require_status=True)
        panes = response.get("panes")
        return [item for item in panes if isinstance(item, Mapping)] if isinstance(panes, list) else []

    def create_pane(
        self,
        namespace: MuxNamespaceRefV2,
        *,
        command: list[str],
        cwd: str,
        env: dict[str, str],
        title: str,
        direction: str = "right",
        percent: int = 50,
        parent_pane: str | None = None,
    ) -> MuxPaneRefV2:
        response = self._request(
            "create_pane",
            {
                "namespace_id": namespace["namespace_id"],
                "session_name": namespace["session_name"],
                "ipc_ref": self._socket_ref,
                "command": list(command),
                "cwd": cwd,
                "env": dict(env),
                "title": title,
                "direction": direction,
                "percent": percent,
                "parent_pane": parent_pane,
            },
            require_result=True,
        )
        pane_id = str(response.get("pane_id") or "")
        response_session_name = str(response.get("session_name") or "").strip()
        if response_session_name and response_session_name != namespace["session_name"]:
            raise MuxCommandErrorV2(
                category="command-failed",
                backend_impl="herdr",
                operation="create_pane",
                detail="Herdr create_pane returned a session different from the requested namespace",
                ipc_ref=self._socket_ref,
                evidence={
                    "socket_ref": self._socket_ref,
                    "expected_session_name": namespace["session_name"],
                    "actual_session_name": response_session_name,
                },
            )
        session_name = response_session_name or namespace["session_name"]
        try:
            return make_pane_ref(
                backend_impl="herdr",
                pane_id=pane_id,
                session_name=session_name,
                window_name=title or None,
            )
        except ValueError as exc:
            raise MuxCommandErrorV2(
                category="command-failed",
                backend_impl="herdr",
                operation="create_pane",
                detail=str(exc),
                ipc_ref=self._socket_ref,
                evidence={"socket_ref": self._socket_ref},
            ) from exc

    def respawn_pane(
        self,
        pane: MuxPaneRefV2,
        *,
        command: list[str],
        cwd: str,
        env: dict[str, str],
    ) -> MuxOperationEvidenceV2:
        response = self._request(
            "respawn_pane",
            {
                "pane_id": pane["pane_id"],
                "session_name": pane["session_name"],
                "command": list(command),
                "cwd": cwd,
                "env": dict(env),
            },
            require_status=True,
        )
        return _operation_evidence("respawn_pane", pane, response, detail="pane command respawned")

    def pane_process_info(
        self,
        pane: MuxPaneRefV2,
    ) -> Mapping[str, object]:
        """Return the pane's foreground process pid via ``pane process-info``.

        Used to backfill ``runtime_pid`` for pane-backed agents (herdr respawns
        the provider CLI into the pane; CCB does not track its pid via a
        provider session).
        """
        return self._request(
            "pane_process_info",
            {"pane_id": pane["pane_id"], "session_name": pane["session_name"]},
            require_status=False,
        )

    def reflow_window(
        self,
        namespace: MuxNamespaceRefV2,
        *,
        window_name: str,
        window_id: str | None,
        target: str,
        prefer_topology_layout: bool,
    ) -> MuxOperationEvidenceV2:
        response = self._request(
            "reflow_window",
            {
                "namespace_id": namespace["namespace_id"],
                "session_name": namespace["session_name"],
                "ipc_ref": self._socket_ref,
                "window_name": window_name,
                "window_id": window_id,
                "target": target,
                "prefer_topology_layout": prefer_topology_layout,
            },
            require_status=True,
        )
        return make_operation_evidence(
            operation="reflow_window",
            backend_impl="herdr",
            pane_id=str(response.get("window_id") or window_id or "").strip() or None,
            status="ok" if str(response.get("status") or "ok") == "ok" else "failed",
            detail="logical workspace reflow observed",
        )

    def move_pane(
        self,
        source_pane: MuxPaneRefV2,
        anchor_pane: MuxPaneRefV2,
        *,
        direction: str,
    ) -> MuxOperationEvidenceV2:
        response = self._request(
            "move_pane",
            {
                "source_pane_id": source_pane["pane_id"],
                "anchor_pane_id": anchor_pane["pane_id"],
                "session_name": source_pane["session_name"],
                "direction": direction,
            },
            require_status=True,
        )
        return _operation_evidence("move_pane", source_pane, response, detail="pane moved")

    def send_text(self, pane: MuxPaneRefV2, text: str) -> MuxOperationEvidenceV2:
        response = self._request(
            "send_text",
            {"pane_id": pane["pane_id"], "session_name": pane["session_name"], "text": text},
            require_status=True,
        )
        return _operation_evidence("send_text", pane, response, detail="text sent")

    def capture_pane(
        self,
        pane: MuxPaneRefV2,
        *,
        lines: int,
    ) -> tuple[str, MuxOperationEvidenceV2]:
        response = self._request(
            "capture_pane",
            {"pane_id": pane["pane_id"], "session_name": pane["session_name"], "lines": lines},
            require_status=True,
        )
        return (
            str(response.get("text") or ""),
            _operation_evidence("capture_pane", pane, response, detail="pane output captured"),
        )

    def kill_pane(self, pane: MuxPaneRefV2) -> MuxOperationEvidenceV2:
        response = self._request(
            "kill_pane",
            {"pane_id": pane["pane_id"], "session_name": pane["session_name"]},
            require_status=True,
        )
        return _operation_evidence("kill_pane", pane, response, detail="pane killed")

    def set_pane_identity(
        self,
        pane: MuxPaneRefV2,
        *,
        title: str,
        agent_label: str,
        tokens: Mapping[str, str],
        role: str | None = None,
        provider_kind: str | None = None,
    ) -> MuxOperationEvidenceV2:
        payload: dict[str, object] = {
            "pane_id": pane["pane_id"],
            "session_name": pane["session_name"],
            "title": title,
            "agent_label": agent_label,
            "tokens": dict(tokens),
        }
        response = self._request(
            "set_pane_identity",
            payload,
            require_status=True,
        )
        return _operation_evidence("set_pane_identity", pane, response, detail="pane metadata updated")

    def report_pane_agent(
        self,
        pane: MuxPaneRefV2,
        *,
        provider_kind: str,
        state: str = "unknown",
        session_id: str | None = None,
        session_path: str | None = None,
    ) -> MuxOperationEvidenceV2:
        payload: dict[str, object] = {
            "pane_id": pane["pane_id"],
            "session_name": pane["session_name"],
            "provider_kind": provider_kind,
            "state": state,
        }
        if session_id:
            payload["session_id"] = session_id
        if session_path:
            payload["session_path"] = session_path
        response = self._request(
            "report_pane_agent",
            payload,
            require_status=True,
        )
        return _operation_evidence("report_pane_agent", pane, response, detail="pane agent reported")

    def select_window(
        self,
        namespace: MuxNamespaceRefV2,
        *,
        window_id: str | None,
        target: str,
    ) -> MuxOperationEvidenceV2:
        response = self._request(
            "select_window",
            {
                "namespace_id": namespace["namespace_id"],
                "session_name": namespace["session_name"],
                "ipc_ref": self._socket_ref,
                "window_id": window_id,
                "target": target,
            },
            require_status=True,
        )
        return make_operation_evidence(
            operation="select_window",
            backend_impl="herdr",
            pane_id=window_id,
            status="ok" if str(response.get("status") or "ok") == "ok" else "failed",
            detail="workspace focused",
        )

    def kill_window(
        self,
        namespace: MuxNamespaceRefV2,
        *,
        window_id: str | None,
        target: str,
    ) -> MuxOperationEvidenceV2:
        response = self._request(
            "kill_window",
            {
                "namespace_id": namespace["namespace_id"],
                "session_name": namespace["session_name"],
                "ipc_ref": self._socket_ref,
                "window_id": window_id,
                "target": target,
            },
            require_status=True,
        )
        return make_operation_evidence(
            operation="kill_window",
            backend_impl="herdr",
            pane_id=window_id,
            status="ok" if str(response.get("status") or "ok") == "ok" else "failed",
            detail="logical workspace closed",
        )

    def rename_window(
        self,
        namespace: MuxNamespaceRefV2,
        *,
        window_id: str | None,
        target: str,
        new_name: str,
    ) -> MuxOperationEvidenceV2:
        response = self._request(
            "rename_window",
            {
                "namespace_id": namespace["namespace_id"],
                "session_name": namespace["session_name"],
                "ipc_ref": self._socket_ref,
                "window_id": window_id,
                "target": target,
                "new_name": new_name,
            },
            require_status=True,
        )
        return make_operation_evidence(
            operation="rename_window",
            backend_impl="herdr",
            pane_id=str(response.get("window_id") or window_id or "").strip() or None,
            status="ok" if str(response.get("status") or "ok") == "ok" else "failed",
            detail="logical workspace renamed",
        )

    def destroy_namespace(self, namespace: MuxNamespaceRefV2) -> MuxOperationEvidenceV2:
        response = self._request(
            "destroy_namespace",
            {
                "namespace_id": namespace["namespace_id"],
                "session_name": namespace["session_name"],
                "ipc_ref": self._socket_ref,
            },
            require_status=True,
        )
        return make_operation_evidence(
            operation="destroy_namespace",
            backend_impl="herdr",
            pane_id=None,
            status="ok" if str(response.get("status") or "ok") == "ok" else "failed",
            detail="logical namespace destroyed",
        )

    def close_workspace(self, session_name: str) -> MuxOperationEvidenceV2:
        """Best-effort close of the Herdr workspace associated with *session_name*.

        Workspace accumulation was observed during repeated kill/restart cycles
        (run-20260807-004015: 6 ``ccb-avaprintdesigner`` workspaces).  This
        method sends a ``close_workspace`` request to Herdr so that old
        workspaces do not pile up.  Failures are logged but never block the
        destroy flow — the workspace will be orphaned in Herdr and can be
        manually cleaned up later.
        """
        try:
            response = self._request(
                "close_workspace",
                {
                    "session_name": session_name,
                    "ipc_ref": self._socket_ref,
                },
                require_status=True,
            )
            status = "ok" if str(response.get("status") or "ok") == "ok" else "failed"
        except Exception:
            status = "failed"
        return make_operation_evidence(
            operation="close_workspace",
            backend_impl="herdr",
            pane_id=None,
            status=status,
            detail=f"workspace close attempted for session {session_name!r}",
        )

    def kill_server(self, namespace: MuxNamespaceRefV2) -> MuxOperationEvidenceV2:
        response = self._request(
            "kill_server",
            {
                "namespace_id": namespace["namespace_id"],
                "session_name": namespace["session_name"],
                "ipc_ref": self._socket_ref,
            },
            require_status=True,
        )
        return make_operation_evidence(
            operation="kill_server",
            backend_impl="herdr",
            pane_id=None,
            status="ok" if str(response.get("status") or "ok") == "ok" else "failed",
            detail="logical namespace destroyed",
        )

    def attach_namespace(
        self,
        namespace: MuxNamespaceRefV2,
        *,
        window_name: str | None = None,
    ) -> MuxOperationEvidenceV2:
        response = self._request(
            "attach_namespace",
            {
                "namespace_id": namespace["namespace_id"],
                "session_name": namespace["session_name"],
                "ipc_ref": self._socket_ref,
                "window_name": window_name,
            },
            require_status=True,
        )
        return make_operation_evidence(
            operation="attach_namespace",
            backend_impl="herdr",
            pane_id=None,
            status="ok" if str(response.get("status") or "ok") == "ok" else "failed",
            detail="namespace attached",
        )

    def _request(
        self,
        operation: str,
        payload: dict[str, object],
        *,
        require_status: bool = False,
        require_result: bool = False,
    ) -> Mapping[str, object]:
        try:
            response = self._request_fn(operation, payload)
        except MuxCommandErrorV2:
            raise
        except Exception as exc:
            raise MuxCommandErrorV2(
                category="transient-unavailable",
                backend_impl="herdr",
                operation=operation,
                detail=f"Herdr socket request failed: {exc}",
                ipc_ref=self._socket_ref,
                evidence={"socket_ref": self._socket_ref},
            ) from exc
        if not isinstance(response, Mapping):
            raise MuxCommandErrorV2(
                category="command-failed",
                backend_impl="herdr",
                operation=operation,
                detail="Herdr socket response is not an object",
                ipc_ref=self._socket_ref,
                evidence={"socket_ref": self._socket_ref},
            )
        outer_status = str(response.get("status") or "").strip()
        if outer_status and outer_status != "ok":
            raise MuxCommandErrorV2(
                category=_error_category(outer_status),
                backend_impl="herdr",
                operation=operation,
                detail=str(response.get("detail") or response.get("message") or f"Herdr {operation} failed"),
                ipc_ref=self._socket_ref,
                evidence={"socket_ref": self._socket_ref, "status": outer_status},
            )
        result = response.get("result")
        outer_detail = str(response.get("detail") or response.get("message") or "").strip()
        if "result" in response:
            if not isinstance(result, Mapping):
                raise MuxCommandErrorV2(
                    category="command-failed",
                    backend_impl="herdr",
                    operation=operation,
                    detail="Herdr socket response result is not an object",
                    ipc_ref=self._socket_ref,
                    evidence={"socket_ref": self._socket_ref},
                )
            response = result
        elif outer_status == "ok" and require_result:
            raise MuxCommandErrorV2(
                category="command-failed",
                backend_impl="herdr",
                operation=operation,
                detail="Herdr socket success response is missing result object",
                ipc_ref=self._socket_ref,
                evidence={"socket_ref": self._socket_ref, "status": outer_status},
            )
        status = str(response.get("status") or outer_status).strip()
        if (require_status and not status) or (status and status != "ok"):
            detail = str(
                response.get("detail")
                or response.get("message")
                or outer_detail
                or f"Herdr {operation} failed"
            )
            raise MuxCommandErrorV2(
                category=_error_category(status),
                backend_impl="herdr",
                operation=operation,
                detail=detail,
                ipc_ref=self._socket_ref,
                evidence={"socket_ref": self._socket_ref, "status": status},
            )
        return response

    def _namespace_ref(
        self,
        operation: str,
        response: Mapping[str, object],
        *,
        fallback_session_name: str,
    ) -> MuxNamespaceRefV2:
        namespace_id = _namespace_id_from_response(response)
        session_name = str(response.get("session_name") or fallback_session_name)
        restore_token = response.get("restore_token")
        try:
            raw_ipc_ref = str(response.get("ipc_ref") or "").strip()
            ipc_ref = self._socket_ref
            if raw_ipc_ref == self._socket_ref or (
                self._allow_session_scoped_ipc_refs and raw_ipc_ref == f"herdr://{session_name}"
            ):
                ipc_ref = raw_ipc_ref
            return make_namespace_ref(
                backend_impl="herdr",
                namespace_id=namespace_id,
                session_name=session_name,
                ipc_kind="herdr_socket",
                ipc_ref=ipc_ref,
                restore_token=str(restore_token) if restore_token is not None else None,
            )
        except ValueError as exc:
            raise MuxCommandErrorV2(
                category="command-failed",
                backend_impl="herdr",
                operation=operation,
                detail=str(exc),
                ipc_ref=self._socket_ref,
                evidence={"socket_ref": self._socket_ref},
            ) from exc


def _operation_evidence(
    operation: str,
    pane: MuxPaneRefV2,
    response: Mapping[str, object],
    *,
    detail: str,
) -> MuxOperationEvidenceV2:
    status = str(response.get("status") or "ok")
    return make_operation_evidence(
        operation=operation,
        backend_impl="herdr",
        pane_id=pane["pane_id"],
        status="ok" if status == "ok" else "failed",
        detail=detail,
    )


def _namespace_id_from_response(response: Mapping[str, object]) -> str:
    return str(
        response.get("namespace_id")
        or response.get("workspace_id")
        or response.get("session_id")
        or ""
    ).strip()


def _error_category(status: str) -> str:
    if status in {
        "schema-mismatch",
        "unsupported",
        "not-found",
        "transient-unavailable",
        "command-failed",
    }:
        return status
    return "command-failed"


def _split_restore_token(restore_token: str) -> tuple[str, str]:
    if restore_token.count("::") != 1:
        raise MuxCommandErrorV2(
            category="command-failed",
            backend_impl="herdr",
            operation="restore_session",
            detail="Herdr restore_token must use session::workspace format",
        )
    session_name, namespace_id = restore_token.split("::", 1)
    session_name = session_name.strip()
    namespace_id = namespace_id.strip()
    if not session_name or not namespace_id:
        raise MuxCommandErrorV2(
            category="command-failed",
            backend_impl="herdr",
            operation="restore_session",
            detail="Herdr restore_token must include non-empty session and workspace",
        )
    return session_name, namespace_id


__all__ = [
    "EXPECTED_HERDR_API_SCHEMA",
    "HerdrRequestFn",
    "HerdrServerInfo",
    "HerdrSocketClient",
]
