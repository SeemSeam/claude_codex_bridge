from __future__ import annotations

from collections.abc import Mapping
from typing import Literal

from terminal_runtime.backend_types import TerminalBackend
from platforms.windows.herdr.runtime.capabilities import HerdrCapabilityGate
from platforms.windows.herdr.runtime.client import HerdrSocketClient
from terminal_runtime.mux_backend_contract import (
    MuxCapabilitiesV2,
    MuxCommandErrorV2,
    MuxNamespaceRefV2,
    MuxOperationEvidenceV2,
    MuxPaneRefV2,
    make_namespace_ref,
    make_pane_ref,
)


class HerdrBackend(TerminalBackend):
    backend_impl = "herdr"

    def __init__(
        self,
        *,
        client: HerdrSocketClient,
        capability_gate: HerdrCapabilityGate,
    ) -> None:
        self._client = client
        self._capability_gate = capability_gate
        self._panes: dict[str, MuxPaneRefV2] = {}
        self._pane_namespaces: dict[str, MuxNamespaceRefV2] = {}
        self._legacy_namespaces: dict[str, MuxNamespaceRefV2] = {}
        self._known_namespaces: dict[tuple[str, str], MuxNamespaceRefV2] = {}
        self._logical_windows: dict[tuple[str, str, str], dict[str, object]] = {}

    def capabilities(self) -> MuxCapabilitiesV2:
        return self._capability_gate.require_supported("capabilities")

    def prepare_server(self) -> None:
        self._capability_gate.require_supported("prepare_server")
        self._client.server_info()

    def ensure_server_policy(self) -> None:
        self.prepare_server()

    def create_session(
        self,
        *,
        project_id: str,
        cwd: str,
        title: str,
    ) -> MuxNamespaceRefV2:
        self._capability_gate.require_supported("create_session")
        self._client.server_info()
        return self._register_namespace(
            self._client.create_session(project_id=project_id, cwd=cwd, title=title)
        )

    def restore_session(self, *, restore_token: str) -> MuxNamespaceRefV2:
        self._capability_gate.require_supported("restore_session")
        self._client.server_info()
        return self._register_namespace(self._client.restore_session(restore_token=restore_token))

    def namespace_alive(self, namespace: MuxNamespaceRefV2) -> bool:
        namespace_ref = self._namespace_ref_from_mapping(namespace, operation="namespace_alive")
        self._capability_gate.require_supported("namespace_alive")
        self._client.server_info()
        try:
            self._client.list_panes(namespace_ref)
        except MuxCommandErrorV2 as exc:
            if exc.category == "not-found":
                return False
            raise
        return True

    def list_windows(self, namespace: MuxNamespaceRefV2) -> list[Mapping[str, object]]:
        namespace_ref = self._namespace_ref_from_mapping(namespace, operation="list_windows")
        self._capability_gate.require_supported("list_windows")
        self._client.server_info()
        return self._client.list_windows(namespace_ref)

    def ensure_window(
        self,
        namespace: MuxNamespaceRefV2,
        *,
        window_name: str,
        cwd: str,
        select: bool,
    ) -> Mapping[str, object]:
        namespace_ref = self._namespace_ref_from_mapping(namespace, operation="ensure_window")
        self._capability_gate.require_supported("ensure_window")
        self._client.server_info()
        record = dict(
            self._client.ensure_window(
                namespace_ref,
                window_name=window_name,
                cwd=cwd,
                select=select,
            )
        )
        record.setdefault("window_name", window_name)
        record.setdefault("window_id", record.get("root_pane_id"))
        self._logical_windows[
            (namespace_ref["session_name"], namespace_ref["namespace_id"], window_name)
        ] = record
        return record

    create_window = ensure_window

    def window_root_pane(
        self,
        namespace: MuxNamespaceRefV2,
        *,
        window_name: str,
    ) -> MuxPaneRefV2:
        namespace_ref = self._namespace_ref_from_mapping(namespace, operation="window_root_pane")
        self._capability_gate.require_supported("window_root_pane")
        self._client.server_info()
        try:
            pane = self._client.window_root_pane(namespace_ref, window_name=window_name)
        except MuxCommandErrorV2 as exc:
            if exc.category != "not-found":
                raise
            panes = self._client.list_panes()
            pane = _root_pane_from_metadata(
                panes,
                session_name=namespace_ref["session_name"],
                namespace_id=namespace_ref["namespace_id"],
                window_name=window_name,
            )
            if pane is None:
                raise
        self._panes[pane["pane_id"]] = pane
        self._pane_namespaces[pane["pane_id"]] = namespace_ref
        return pane

    def set_pane_identity(
        self,
        pane: MuxPaneRefV2,
        *,
        title: str,
        agent_label: str,
        project_id: str,
        order_index: int | None = None,
        is_cmd: bool = False,
        role: str | None = None,
        slot_key: str | None = None,
        window_name: str | None = None,
        sidebar_instance: str | None = None,
        session_id: str | None = None,
        namespace_epoch: int | None = None,
        managed_by: str | None = None,
        provider_kind: str | None = None,
    ) -> MuxOperationEvidenceV2:
        pane_ref = self._pane_ref(pane, operation="set_pane_identity")
        self._capability_gate.require_supported("set_pane_identity")
        namespace = self._pane_namespaces.get(pane_ref["pane_id"])
        if namespace is None:
            namespace = self._known_namespace_for_session(
                pane_ref["session_name"],
                operation="set_pane_identity",
                pane_id=pane_ref["pane_id"],
            )
        if namespace is None:
            raise MuxCommandErrorV2(
                category="not-found",
                backend_impl="herdr",
                operation="set_pane_identity",
                detail=f"unknown Herdr namespace for pane {pane_ref['pane_id']!r}",
                evidence={"pane_id": pane_ref["pane_id"]},
            )
        self._panes[pane_ref["pane_id"]] = pane_ref
        self._pane_namespaces[pane_ref["pane_id"]] = namespace
        tokens = {
            "ccb_project_id": project_id,
            "ccb_order": str(order_index) if order_index is not None else "",
            "ccb_is_cmd": "1" if is_cmd else "0",
            "ccb_role": role or "",
            "ccb_slot": slot_key or "",
            "ccb_window": window_name or pane_ref.get("window_name") or "",
            "ccb_sidebar_instance": sidebar_instance or "",
            "ccb_session_id": session_id or "",
            "ccb_namespace_epoch": str(namespace_epoch) if namespace_epoch is not None else "",
            "ccb_managed_by": managed_by or "",
        }
        return self._client.set_pane_identity(
            pane_ref,
            title=title,
            agent_label=agent_label,
            tokens=tokens,
            role=role,
            provider_kind=provider_kind,
        )

    def report_pane_agent(
        self,
        pane: MuxPaneRefV2,
        *,
        provider_kind: str,
        state: str = "unknown",
        session_id: str | None = None,
        session_path: str | None = None,
    ) -> MuxOperationEvidenceV2:
        pane_ref = self._pane_ref(pane, operation="report_pane_agent")
        self._capability_gate.require_supported("report_pane_agent")
        self._client.server_info()
        return self._client.report_pane_agent(
            pane_ref,
            provider_kind=provider_kind,
            state=state,
            session_id=session_id,
            session_path=session_path,
        )

    def describe_pane(
        self,
        pane_id: str,
        *,
        user_options: tuple[str, ...] = (),
    ) -> dict[str, object] | None:
        pane_text = str(pane_id or "").strip()
        if not pane_text:
            return None
        self._capability_gate.require_supported("describe_pane")
        self._client.server_info()
        namespace = getattr(self, "_ccb_project_namespace_ref", None)
        namespace_ref = namespace if isinstance(namespace, dict) else None
        panes = self._client.list_panes(namespace_ref)
        for pane in panes:
            if str(pane.get("pane_id") or "").strip() != pane_text:
                continue
            tokens = pane.get("tokens") if isinstance(pane.get("tokens"), Mapping) else {}
            session_name = str(
                (namespace_ref or {}).get("session_name")
                or pane.get("session_name")
                or self._client.session_name
                or ""
            ).strip()
            details: dict[str, object] = {
                "pane_id": pane_text,
                "session_name": session_name,
                "window_id": pane.get("workspace_id"),
                "window_name": _token_value(tokens, "ccb_window")
                or _token_value(tokens, "ccb_logical_window"),
                "pane_title": pane.get("title") or pane.get("terminal_title"),
                "pane_dead": "0",
                "@ccb_role": _token_value(tokens, "ccb_role"),
                "@ccb_slot": _token_value(tokens, "ccb_slot"),
                "@ccb_window": _token_value(tokens, "ccb_window")
                or _token_value(tokens, "ccb_logical_window"),
                "@ccb_sidebar_instance": _token_value(tokens, "ccb_sidebar_instance"),
                "@ccb_agent": _token_value(tokens, "ccb_agent_label"),
                "@ccb_session_id": _token_value(tokens, "ccb_session_id"),
                "@ccb_project_id": _token_value(tokens, "ccb_project_id"),
                "@ccb_managed_by": _token_value(tokens, "ccb_managed_by"),
                "@ccb_namespace_epoch": _token_value(tokens, "ccb_namespace_epoch"),
            }
            if user_options:
                details.update(
                    {
                        option: _token_value(tokens, str(option).lstrip("@"))
                        for option in user_options
                    }
                )
            return details
        return None

    def list_panes_by_user_options(self, expected: dict[str, str]) -> list[str]:
        self._capability_gate.require_supported("list_panes_by_user_options")
        self._client.server_info()
        namespace = getattr(self, "_ccb_project_namespace_ref", None)
        panes = self._client.list_panes(namespace if isinstance(namespace, dict) else None)
        normalized = {str(key).lstrip("@"): str(value) for key, value in expected.items()}
        return [
            str(pane.get("pane_id") or "").strip()
            for pane in panes
            if str(pane.get("pane_id") or "").strip()
            and all(
                _token_value(
                    pane.get("tokens") if isinstance(pane.get("tokens"), Mapping) else {},
                    key,
                )
                == value
                for key, value in normalized.items()
            )
        ]

    def select_window(
        self,
        namespace: MuxNamespaceRefV2,
        *,
        window_id: str | None,
        target: str,
    ) -> MuxOperationEvidenceV2:
        namespace_ref = self._namespace_ref_from_mapping(namespace, operation="select_window")
        self._capability_gate.require_supported("select_window")
        self._client.server_info()
        return self._client.select_window(namespace_ref, window_id=window_id, target=target)

    def kill_window(
        self,
        namespace: MuxNamespaceRefV2,
        *,
        window_id: str | None,
        target: str,
    ) -> MuxOperationEvidenceV2:
        namespace_ref = self._namespace_ref_from_mapping(namespace, operation="kill_window")
        self._capability_gate.require_supported("kill_window")
        self._client.server_info()
        evidence = self._client.kill_window(namespace_ref, window_id=window_id, target=target)
        if window_id:
            self._panes.pop(window_id, None)
            self._pane_namespaces.pop(window_id, None)
        for key, record in tuple(self._logical_windows.items()):
            session_name, namespace_id, _window_name = key
            if session_name != namespace_ref["session_name"] or namespace_id != namespace_ref["namespace_id"]:
                continue
            if (
                record.get("window_id") == window_id
                or record.get("window_name") == target.rsplit(":", 1)[-1]
            ):
                root_pane_id = str(record.get("root_pane_id") or "").strip()
                if root_pane_id:
                    self._panes.pop(root_pane_id, None)
                    self._pane_namespaces.pop(root_pane_id, None)
                self._logical_windows.pop(key, None)
        return evidence

    def rename_window(
        self,
        namespace: MuxNamespaceRefV2,
        *,
        window_id: str | None,
        target: str,
        new_name: str,
    ) -> MuxOperationEvidenceV2:
        namespace_ref = self._namespace_ref_from_mapping(namespace, operation="rename_window")
        self._capability_gate.require_supported("rename_window")
        self._client.server_info()
        evidence = self._client.rename_window(
            namespace_ref,
            window_id=window_id,
            target=target,
            new_name=new_name,
        )
        old_name = target.rsplit(":", 1)[-1] if target else window_id
        for key, record in tuple(self._logical_windows.items()):
            session_name, namespace_id, window_name = key
            if session_name != namespace_ref["session_name"] or namespace_id != namespace_ref["namespace_id"]:
                continue
            if record.get("window_id") == window_id or window_name == old_name:
                updated = {**record, "window_name": new_name}
                self._logical_windows.pop(key, None)
                self._logical_windows[(session_name, namespace_id, new_name)] = updated
        return evidence

    def destroy_namespace(self, namespace: MuxNamespaceRefV2) -> MuxOperationEvidenceV2:
        namespace_ref = self._namespace_ref_from_mapping(namespace, operation="destroy_namespace")
        self._capability_gate.require_supported("destroy_namespace")
        self._client.server_info()
        evidence = self._client.destroy_namespace(namespace_ref)
        # Best-effort workspace cleanup: close the Herdr workspace so that
        # repeated kill/restart cycles do not accumulate orphan workspaces
        # (run-20260807-004015 observed 6 ccb-avaprintdesigner workspaces).
        session_name = namespace_ref.get("session_name", "")
        if session_name:
            try:
                self._client.close_workspace(str(session_name))
            except Exception:
                pass
        self._drop_namespace_refs(namespace_ref)
        return evidence

    def kill_server(self, namespace: MuxNamespaceRefV2) -> MuxOperationEvidenceV2:
        namespace_ref = self._namespace_ref_from_mapping(namespace, operation="kill_server")
        self._capability_gate.require_supported("kill_server")
        self._client.server_info()
        evidence = self._client.kill_server(namespace_ref)
        self._drop_namespace_refs(namespace_ref)
        return evidence

    def attach_persisted_session(
        self,
        namespace: Mapping[str, object],
        *,
        pane_id: str | None = None,
        pane_ref: Mapping[str, object] | None = None,
    ) -> None:
        namespace_ref = self._register_namespace(
            self._namespace_ref_from_mapping(dict(namespace), operation="attach_persisted_session")
        )
        setattr(self, "_ccb_project_namespace_ref", namespace_ref)
        pane_text = str((pane_ref or {}).get("pane_id") or pane_id or "").strip()
        if not pane_text:
            return
        session_name = str((pane_ref or {}).get("session_name") or namespace_ref["session_name"]).strip()
        pane = make_pane_ref(
            backend_impl="herdr",
            pane_id=pane_text,
            session_name=session_name,
            window_name=_optional_text((pane_ref or {}).get("window_name")),
            agent_slug=_optional_text((pane_ref or {}).get("agent_slug")),
        )
        self._panes[pane_text] = pane
        self._pane_namespaces[pane_text] = namespace_ref

    def namespace_ref(self, session_name: str, namespace_id: str) -> MuxNamespaceRefV2:
        return self._register_namespace(
            make_namespace_ref(
                backend_impl="herdr",
                namespace_id=namespace_id,
                session_name=session_name,
                ipc_kind="herdr_socket",
                ipc_ref=self._client.socket_ref,
            )
        )

    def _drop_namespace_refs(self, namespace: MuxNamespaceRefV2) -> None:
        session_name = namespace["session_name"]
        namespace_id = namespace["namespace_id"]
        for key in [
            key
            for key in self._known_namespaces
            if key == (session_name, namespace_id)
        ]:
            self._known_namespaces.pop(key, None)
        for key in [
            key
            for key in self._logical_windows
            if key[0] == session_name and key[1] == namespace_id
        ]:
            self._logical_windows.pop(key, None)
        for pane_id, pane_namespace in tuple(self._pane_namespaces.items()):
            if (
                pane_namespace["session_name"] == session_name
                and pane_namespace["namespace_id"] == namespace_id
            ):
                self._pane_namespaces.pop(pane_id, None)
                self._panes.pop(pane_id, None)

    def create_pane(
        self,
        namespace_or_cmd,
        cwd: str | None = None,
        direction: str = "right",
        percent: int = 50,
        parent_pane: str | None = None,
        *,
        command: list[str] | None = None,
        env: dict[str, str] | None = None,
        title: str = "",
    ):
        if isinstance(namespace_or_cmd, dict):
            namespace = self._namespace_ref_from_mapping(namespace_or_cmd, operation="create_pane")
            return self._create_v2_pane(
                namespace,
                command=list(command or []),
                cwd=cwd or "",
                env=env or {},
                title=title,
                direction=direction,
                percent=percent,
                parent_pane=str(parent_pane) if parent_pane is not None else None,
            )
        if parent_pane is not None:
            parent_key = str(parent_pane)
            try:
                namespace = self._pane_namespaces[parent_key]
            except KeyError as exc:
                raise MuxCommandErrorV2(
                    category="not-found",
                    backend_impl="herdr",
                    operation="create_pane",
                    detail=f"unknown Herdr pane {parent_key!r}",
                    evidence={"pane_id": parent_key},
                ) from exc
        else:
            namespace = self._ensure_legacy_namespace(cwd or "")
        legacy_command = (
            list(command)
            if command is not None
            else ([str(namespace_or_cmd)] if str(namespace_or_cmd).strip() else [])
        )
        pane = self._create_v2_pane(
            namespace,
            command=legacy_command,
            cwd=cwd or "",
            env=env or {},
            title=title or "legacy",
            direction=direction,
            percent=percent,
            parent_pane=str(parent_pane) if parent_pane is not None else None,
        )
        return pane["pane_id"]

    def split_pane(
        self,
        pane: MuxPaneRefV2,
        *,
        direction: Literal["left", "right", "up", "down"] = "right",
        percent: int = 50,
        command: list[str] | None = None,
        cwd: str = "",
        env: dict[str, str] | None = None,
        title: str = "",
    ) -> MuxPaneRefV2:
        pane_ref = self._pane_ref(pane, operation="split_pane")
        pane_id = pane_ref["pane_id"]
        namespace = self._pane_namespaces.get(pane_id) or self._known_namespace_for_session(
            pane_ref["session_name"],
            operation="split_pane",
            pane_id=pane_id,
        )
        if namespace is None:
            raise MuxCommandErrorV2(
                category="not-found",
                backend_impl="herdr",
                operation="split_pane",
                detail=f"unknown Herdr pane {pane_id!r}",
                evidence={"pane_id": pane_id},
            )
        return self._create_v2_pane(
            namespace,
            command=command or [],
            cwd=cwd,
            env=env or {},
            title=title,
            direction=direction,
            percent=percent,
            parent_pane=pane_id,
        )

    def respawn_pane(
        self,
        pane: MuxPaneRefV2,
        *,
        command: list[str],
        cwd: str,
        env: dict[str, str] | None = None,
    ) -> MuxOperationEvidenceV2:
        pane_ref = self._pane_ref(pane, operation="respawn_pane")
        self._capability_gate.require_supported("respawn_pane")
        self._client.server_info()
        return self._client.respawn_pane(
            pane_ref,
            command=command,
            cwd=cwd,
            env=env or {},
        )

    def pane_process_info(
        self,
        pane: MuxPaneRefV2,
    ) -> Mapping[str, object]:
        """Return the pane's foreground process info (``pane process-info``)."""
        pane_ref = self._pane_ref(pane, operation="pane_process_info")
        self._client.server_info()
        return self._client.pane_process_info(pane_ref)

    def move_pane(
        self,
        source_pane: MuxPaneRefV2,
        anchor_pane: MuxPaneRefV2,
        *,
        direction: str,
    ) -> MuxOperationEvidenceV2:
        source_ref = self._pane_ref(source_pane, operation="move_pane")
        anchor_ref = self._pane_ref(anchor_pane, operation="move_pane")
        self._capability_gate.require_supported("move_pane")
        self._client.server_info()
        return self._client.move_pane(source_ref, anchor_ref, direction=direction)

    def reflow_window(
        self,
        namespace: MuxNamespaceRefV2,
        *,
        window_name: str,
        window_id: str | None,
        target: str,
        prefer_topology_layout: bool = False,
    ) -> MuxOperationEvidenceV2:
        namespace_ref = self._namespace_ref_from_mapping(namespace, operation="reflow_window")
        self._capability_gate.require_supported("reflow_window")
        self._client.server_info()
        return self._client.reflow_window(
            namespace_ref,
            window_name=window_name,
            window_id=window_id,
            target=target,
            prefer_topology_layout=prefer_topology_layout,
        )

    def _create_v2_pane(
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
        namespace = self._register_namespace(namespace)
        self._capability_gate.require_supported("create_pane")
        self._client.server_info()
        pane = self._client.create_pane(
            namespace,
            command=command,
            cwd=cwd,
            env=env,
            title=title,
            direction=direction,
            percent=percent,
            parent_pane=parent_pane,
        )
        self._panes[pane["pane_id"]] = pane
        self._pane_namespaces[pane["pane_id"]] = namespace
        return pane

    def send_text(self, pane_id, text: str) -> MuxOperationEvidenceV2 | None:
        pane = self._pane_ref(pane_id, operation="send_text")
        self._capability_gate.require_supported("send_text")
        self._client.server_info()
        evidence = self._client.send_text(pane, text)
        return evidence if isinstance(pane_id, dict) else None

    def capture_pane(
        self,
        pane: MuxPaneRefV2,
        *,
        lines: int,
        ) -> tuple[str, MuxOperationEvidenceV2]:
        pane = self._pane_ref(pane, operation="capture_pane")
        self._capability_gate.require_supported("capture_pane")
        self._client.server_info()
        return self._client.capture_pane(pane, lines=lines)

    def kill_pane(self, pane_id) -> MuxOperationEvidenceV2 | None:
        pane = self._pane_ref(pane_id, operation="kill_pane")
        self._capability_gate.require_supported("kill_pane")
        self._client.server_info()
        evidence = self._client.kill_pane(pane)
        self._panes.pop(pane["pane_id"], None)
        self._pane_namespaces.pop(pane["pane_id"], None)
        return evidence if isinstance(pane_id, dict) else None

    def attach_namespace(
        self,
        namespace: MuxNamespaceRefV2,
        *,
        window_name: str | None = None,
    ) -> MuxOperationEvidenceV2:
        namespace_ref = self._namespace_ref_from_mapping(namespace, operation="attach_namespace")
        self._capability_gate.require_supported("attach_namespace")
        self._client.server_info()
        return self._client.attach_namespace(namespace_ref, window_name=window_name)

    def is_alive(self, pane_id) -> bool:
        try:
            pane = self._pane_ref(pane_id, operation="is_alive")
        except MuxCommandErrorV2 as exc:
            if exc.category == "not-found":
                return False
            raise
        cached = pane["pane_id"] in self._panes
        try:
            self._capability_gate.require_supported("capture_pane")
            self._client.server_info()
            self._client.capture_pane(pane, lines=1)
        except MuxCommandErrorV2 as exc:
            if exc.category == "not-found":
                self._panes.pop(pane["pane_id"], None)
                self._pane_namespaces.pop(pane["pane_id"], None)
                return False
            if exc.operation == "server_info" or exc.category in {"schema-mismatch", "unsupported"}:
                raise
            return cached
        return True

    def activate(self, pane_id: str) -> None:
        if pane_id not in self._panes:
            raise MuxCommandErrorV2(
                category="not-found",
                backend_impl="herdr",
                operation="activate",
                detail=f"unknown Herdr pane {pane_id!r}",
                evidence={"pane_id": pane_id},
            )
        raise MuxCommandErrorV2(
            category="unsupported",
            backend_impl="herdr",
            operation="activate",
            detail="Herdr pane activation is not supported by the current backend adapter",
            evidence={"pane_id": pane_id},
        )

    def _pane_ref(self, pane_or_id, *, operation: str) -> MuxPaneRefV2:
        if isinstance(pane_or_id, dict):
            pane_id = str(pane_or_id.get("pane_id") or "")
            session_name = str(pane_or_id.get("session_name") or "").strip()
            if (
                str(pane_or_id.get("backend_impl") or "") != "herdr"
                or not pane_id
                or not session_name
            ):
                raise MuxCommandErrorV2(
                    category="not-found",
                    backend_impl="herdr",
                    operation=operation,
                    detail=f"unknown Herdr pane {pane_id!r}",
                    evidence={"pane_id": pane_id},
                )
            cached = self._panes.get(pane_id)
            if cached is not None and cached.get("session_name") != session_name:
                raise MuxCommandErrorV2(
                    category="not-found",
                    backend_impl="herdr",
                    operation=operation,
                    detail=f"unknown Herdr pane {pane_id!r}",
                    evidence={"pane_id": pane_id},
                )
            if (
                cached is None
                and self._known_namespace_for_session(
                    session_name,
                    operation=operation,
                    pane_id=pane_id,
                )
                is None
            ):
                raise MuxCommandErrorV2(
                    category="not-found",
                    backend_impl="herdr",
                    operation=operation,
                    detail=f"unknown Herdr pane {pane_id!r}",
                    evidence={"pane_id": pane_id},
                )
            return make_pane_ref(
                backend_impl="herdr",
                pane_id=pane_id,
                session_name=session_name,
                window_name=pane_or_id.get("window_name"),  # type: ignore[arg-type]
                agent_slug=pane_or_id.get("agent_slug"),  # type: ignore[arg-type]
            )
        pane_id = str(pane_or_id)
        try:
            return self._panes[pane_id]
        except KeyError as exc:
            raise MuxCommandErrorV2(
                category="not-found",
                backend_impl="herdr",
                operation=operation,
                detail=f"unknown Herdr pane {pane_id!r}",
                evidence={"pane_id": pane_id},
            ) from exc

    def _namespace_ref_from_mapping(self, namespace: dict, *, operation: str) -> MuxNamespaceRefV2:
        session_name = str(namespace.get("session_name") or "").strip()
        ipc_ref = str(namespace.get("ipc_ref") or "").strip()
        if (
            namespace.get("backend_impl") != "herdr"
            or namespace.get("ipc_kind") != "herdr_socket"
            or not str(namespace.get("namespace_id") or "").strip()
            or not session_name
            or not self._namespace_ipc_ref_matches(ipc_ref, session_name)
        ):
            raise MuxCommandErrorV2(
                category="command-failed",
                backend_impl="herdr",
                operation=operation,
                detail="invalid Herdr namespace ref",
                evidence={"namespace_id": str(namespace.get("namespace_id") or "")},
            )
        return namespace  # type: ignore[return-value]

    def _register_namespace(self, namespace: MuxNamespaceRefV2) -> MuxNamespaceRefV2:
        self._known_namespaces[(namespace["session_name"], namespace["namespace_id"])] = namespace
        return namespace

    def _known_namespace_for_session(
        self,
        session_name: str,
        *,
        operation: str,
        pane_id: str,
    ) -> MuxNamespaceRefV2 | None:
        current = getattr(self, "_ccb_project_namespace_ref", None)
        matches = [
            namespace
            for (known_session, _), namespace in self._known_namespaces.items()
            if known_session == session_name
        ]
        if (
            isinstance(current, dict)
            and str(current.get("session_name") or "").strip() == session_name
        ):
            matches.append(current)  # type: ignore[arg-type]
        unique: dict[tuple[str, str], MuxNamespaceRefV2] = {}
        for namespace in matches:
            unique[(namespace["session_name"], namespace["namespace_id"])] = namespace
        matches = list(unique.values())
        if len(matches) <= 1:
            return matches[0] if matches else None
        raise MuxCommandErrorV2(
            category="not-found",
            backend_impl="herdr",
            operation=operation,
            detail=f"ambiguous Herdr namespace for pane {pane_id!r}",
            evidence={"pane_id": pane_id, "session_name": session_name},
        )

    def _namespace_ipc_ref_matches(self, ipc_ref: str, session_name: str) -> bool:
        if ipc_ref == self._client.socket_ref:
            return True
        return self._client.allow_session_scoped_ipc_refs and ipc_ref == f"herdr://{session_name}"

    def _ensure_legacy_namespace(self, cwd: str) -> MuxNamespaceRefV2:
        key = (cwd or "").strip()
        namespace = self._legacy_namespaces.get(key)
        if namespace is None:
            namespace = self.create_session(
                project_id="ccb-herdr",
                cwd=cwd,
                title="ccb-herdr",
            )
            self._legacy_namespaces[key] = namespace
        return namespace


def _token_value(tokens: Mapping[str, object], key: str) -> str:
    return str(tokens.get(str(key).lstrip("@")) or "").strip()


def _optional_text(value: object) -> str | None:
    text = str(value or "").strip()
    return text or None


def _root_pane_from_metadata(
    panes: list[Mapping[str, object]],
    *,
    session_name: str,
    namespace_id: str,
    window_name: str,
) -> MuxPaneRefV2 | None:
    root_candidates: list[Mapping[str, object]] = []
    candidates: list[Mapping[str, object]] = []
    for pane in panes:
        tokens = pane.get("tokens") if isinstance(pane.get("tokens"), Mapping) else {}
        logical_window = _token_value(tokens, "ccb_window") or _token_value(
            tokens,
            "ccb_logical_window",
        )
        if _token_value(tokens, "ccb_namespace_id") != namespace_id:
            continue
        is_root = _token_value(tokens, "ccb_root_pane") == "1"
        if logical_window == window_name and is_root:
            root_candidates.append(pane)
        if logical_window == window_name or (not window_name and is_root):
            candidates.append(pane)
    if root_candidates:
        candidates = root_candidates
    if not candidates:
        return None
    pane = candidates[0]
    pane_id = str(pane.get("pane_id") or "").strip()
    if not pane_id:
        return None
    return make_pane_ref(
        backend_impl="herdr",
        pane_id=pane_id,
        session_name=session_name,
        window_name=window_name or None,
    )


__all__ = ["HerdrBackend"]
