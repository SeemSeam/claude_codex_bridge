from __future__ import annotations

import json
import os
import platform
import shlex
import shutil
import subprocess
import sys
import time
from collections.abc import Callable, Mapping

from platforms.windows.herdr.common import resolve_herdr_executable
from terminal_runtime.mux_backend_contract import MuxCommandErrorV2

_METADATA_SOURCE = "ccb"
_LOGICAL_WINDOW_TOKEN = "ccb_window"
_LOGICAL_WINDOW_ALIAS_TOKEN = "ccb_logical_window"
_NAMESPACE_TOKEN = "ccb_namespace_id"
_ROOT_PANE_TOKEN = "ccb_root_pane"


class HerdrCliRequestAdapter:
    def __init__(
        self,
        *,
        session_name: str,
        herdr_executable: str | None = None,
        run_fn: Callable[..., subprocess.CompletedProcess] = subprocess.run,
        popen_fn: Callable[..., subprocess.Popen] = subprocess.Popen,
        which_fn: Callable[[str], str | None] = shutil.which,
        socket_ref: str | None = None,
        sleep_fn: Callable[[float], None] = time.sleep,
    ) -> None:
        self._session_name = session_name
        self._herdr_executable = herdr_executable
        self._run_fn = run_fn
        self._popen_fn = popen_fn
        self._which_fn = which_fn
        self._socket_ref = str(socket_ref or "").strip() or None
        self._sleep_fn = sleep_fn
        self._server_sessions: set[str] = set()
        self._server_processes: dict[str, subprocess.Popen] = {}

    @property
    def socket_ref(self) -> str:
        return self._ipc_ref_for_session(self._session_name)

    @property
    def session_name(self) -> str:
        return self._session_name

    @property
    def allow_session_scoped_ipc_refs(self) -> bool:
        return self._socket_ref is None

    def __call__(self, operation: str, payload: dict[str, object]) -> Mapping[str, object]:
        if operation == "server_info":
            return self._server_info()
        if operation == "create_session":
            return self._create_session(payload)
        if operation == "restore_session":
            return self._restore_session(payload)
        if operation == "list_windows":
            return self._list_windows(payload)
        if operation == "ensure_window":
            return self._ensure_window(payload)
        if operation == "window_root_pane":
            return self._window_root_pane(payload)
        if operation == "list_panes":
            return self._list_panes(payload)
        if operation == "create_pane":
            return self._create_pane(payload)
        if operation == "set_pane_identity":
            return self._set_pane_identity(payload)
        if operation == "report_pane_agent":
            return self._report_pane_agent(payload)
        if operation == "respawn_pane":
            return self._respawn_pane(payload)
        if operation == "pane_process_info":
            return self._pane_process_info(payload)
        if operation == "move_pane":
            return self._move_pane(payload)
        if operation == "reflow_window":
            return self._reflow_window(payload)
        if operation == "select_window":
            return self._select_window(payload)
        if operation == "kill_window":
            return self._kill_window(payload)
        if operation == "rename_window":
            return self._rename_window(payload)
        if operation == "destroy_namespace":
            return self._destroy_namespace(payload)
        if operation == "kill_server":
            return self._destroy_namespace(payload)
        if operation == "send_text":
            return self._send_text(payload)
        if operation == "capture_pane":
            return self._capture_pane(payload)
        if operation == "kill_pane":
            return self._kill_pane(payload)
        if operation == "attach_namespace":
            return self._attach_namespace(payload)
        if operation == "is_alive":
            return self._is_alive(payload)
        raise MuxCommandErrorV2(
            category="unsupported",
            backend_impl="herdr",
            operation=operation,
            detail=f"unsupported Herdr operation {operation!r}",
            ipc_ref=self.socket_ref,
        )

    def _server_info(self) -> Mapping[str, object]:
        status = self._json_command("server_info", ["status", "--json"], ensure_server=False)
        version_result = self._command("server_info", ["--version"], expect_json=False, ensure_server=False)
        schema = self._json_command("server_info", ["api", "schema", "--json"], ensure_server=False)
        client = status.get("client") if isinstance(status.get("client"), Mapping) else {}
        return {
            "version": str(client.get("version") or version_result.stdout or "").strip(),
            "api_schema": str(schema.get("title") or ""),
            "platform": _runtime_platform(),
            "arch": _runtime_arch(),
        }

    def _create_session(self, payload: Mapping[str, object]) -> Mapping[str, object]:
        title = str(payload.get("title") or payload.get("project_id") or "ccb-herdr")
        session_name = _create_session_scope(title, fallback_session_name=self._session_name)
        if str(title or "").strip().startswith("ccb-"):
            self._ensure_server_ready(session_name)
        cwd = str(payload.get("cwd") or "")
        args = ["workspace", "create"]
        if cwd:
            args.extend(["--cwd", cwd])
        args.extend(["--label", title, "--focus"])
        result = self._json_command("create_session", args, session_name=session_name)
        workspace = _mapping(result.get("workspace"))
        root_pane = _mapping(result.get("root_pane"))
        namespace_id = str(workspace.get("workspace_id") or root_pane.get("workspace_id") or "").strip()
        if not namespace_id:
            raise self._failed(
                "create_session",
                "Herdr workspace create response is missing workspace_id",
                session_name=session_name,
            )
        if not any(
            str(item.get("workspace_id") or "").strip() == namespace_id
            for item in self._workspaces(session_name=session_name)
        ):
            raise self._failed(
                "create_session",
                f"Herdr workspace {namespace_id!r} was not found after creation",
                session_name=session_name,
            )
        root_pane_id = str(root_pane.get("pane_id") or "").strip()
        if root_pane_id:
            self._report_workspace_metadata(
                namespace_id,
                project_id=str(payload.get("project_id") or "").strip(),
                namespace_id=namespace_id,
                window_name=title,
                session_name=session_name,
            )
            self._report_pane_metadata(
                root_pane_id,
                session_name=session_name,
                title=title,
                tokens={
                    "ccb_project_id": str(payload.get("project_id") or "").strip(),
                    _NAMESPACE_TOKEN: namespace_id,
                    _LOGICAL_WINDOW_TOKEN: title,
                    _LOGICAL_WINDOW_ALIAS_TOKEN: title,
                    _ROOT_PANE_TOKEN: "1",
                },
            )
        # New workspaces get a default tab named after the tab number;
        # rename it to match the window name so the Herdr tab bar shows a
        # meaningful label instead of "1", "2", …
        self._rename_workspace_tab(namespace_id, title, session_name=session_name)
        return {
            "namespace_id": namespace_id,
            "session_name": session_name,
            "restore_token": _restore_token(session_name, namespace_id),
            "ipc_ref": self._ipc_ref_for_session(session_name),
        }

    def _restore_session(self, payload: Mapping[str, object]) -> Mapping[str, object]:
        restore_token = str(payload.get("restore_token") or "")
        try:
            session_name, namespace_id = _split_restore_token(restore_token)
        except ValueError as exc:
            raise self._failed("restore_session", str(exc)) from exc
        workspaces = self._workspaces(session_name=session_name)
        for workspace in workspaces:
            if workspace.get("workspace_id") == namespace_id:
                return {
                    "namespace_id": namespace_id,
                    "session_name": session_name,
                    "restore_token": _restore_token(session_name, namespace_id),
                    "ipc_ref": self._ipc_ref_for_session(session_name),
                }
        raise self._failed(
            "restore_session",
            f"unknown Herdr restore token {namespace_id!r}",
            session_name=session_name,
        )

    def _list_windows(self, payload: Mapping[str, object]) -> Mapping[str, object]:
        namespace_id = str(payload.get("namespace_id") or "").strip()
        session_name = _session_name_from_payload(payload, fallback_session_name=self._session_name)
        workspaces = self._logical_workspaces(
            namespace_id=namespace_id,
            session_name=session_name,
        )
        windows = []
        for workspace in workspaces:
            window_name = str(workspace.get("window_name") or "").strip()
            if not window_name:
                continue
            workspace_id = str(workspace.get("workspace_id") or "").strip()
            windows.append(
                {
                    "window_id": workspace_id,
                    "window_name": window_name,
                    "active": bool(workspace.get("focused", False)),
                    "root_pane_id": str(workspace.get("root_pane_id") or "").strip(),
                }
            )
        return {"status": "ok", "windows": windows}

    def _ensure_window(self, payload: Mapping[str, object]) -> Mapping[str, object]:
        namespace_id = str(payload.get("namespace_id") or "").strip()
        session_name = _session_name_from_payload(payload, fallback_session_name=self._session_name)
        window_name = str(payload.get("window_name") or "").strip()
        if not namespace_id or not window_name:
            raise self._failed(
                "ensure_window",
                "Herdr ensure_window requires namespace_id and window_name",
                session_name=session_name,
            )
        logical_workspaces = self._logical_workspaces(
            namespace_id=namespace_id,
            session_name=session_name,
        )
        existing = next(
            (
                workspace
                for workspace in logical_workspaces
                if str(workspace.get("window_name") or "").strip() == window_name
            ),
            None,
        )
        if existing is None:
            anchor = next(
                (
                    workspace
                    for workspace in logical_workspaces
                    if str(workspace.get("workspace_id") or "").strip() == namespace_id
                ),
                None,
            )
            if anchor is not None and _anchor_workspace_can_be_claimed(
                anchor,
                session_name=session_name,
                requested_window_name=window_name,
            ):
                existing = anchor
        if existing is not None:
            workspace_id = str(existing.get("workspace_id") or namespace_id).strip()
            root_pane_id = str(existing.get("root_pane_id") or "").strip()
            self._report_workspace_metadata(
                workspace_id,
                project_id=str(payload.get("project_id") or "").strip(),
                namespace_id=namespace_id,
                window_name=window_name,
                session_name=session_name,
            )
            if root_pane_id:
                self._report_pane_metadata(
                    root_pane_id,
                    session_name=session_name,
                    title=window_name,
                    tokens={
                        "ccb_project_id": str(payload.get("project_id") or "").strip(),
                        _NAMESPACE_TOKEN: namespace_id,
                        _LOGICAL_WINDOW_TOKEN: window_name,
                        _LOGICAL_WINDOW_ALIAS_TOKEN: window_name,
                        _ROOT_PANE_TOKEN: "1",
                    },
                )
            # When reusing an existing workspace (anchor or metadata-match),
            # also rename the tab to the current window name — the workspace
            # may have been created with a different label (e.g. the session
            # name for the anchor) and the tab bar should reflect the
            # configured [windows] name.
            self._rename_workspace_tab(workspace_id, window_name, session_name=session_name)
            existing = {
                **dict(existing),
                "workspace_id": workspace_id,
                "window_name": window_name,
                "root_pane_id": root_pane_id,
            }
        if existing is None:
            cwd = str(payload.get("cwd") or "")
            args = ["workspace", "create"]
            if cwd:
                args.extend(["--cwd", cwd])
            args.extend(["--label", window_name])
            if bool(payload.get("select", False)):
                args.append("--focus")
            result = self._json_command("ensure_window", args, session_name=session_name)
            workspace = _mapping(result.get("workspace"))
            root_pane = _mapping(result.get("root_pane"))
            workspace_id = str(workspace.get("workspace_id") or root_pane.get("workspace_id") or "").strip()
            root_pane_id = str(root_pane.get("pane_id") or "").strip()
            if not workspace_id:
                raise self._failed(
                    "ensure_window",
                    "Herdr workspace create response is missing workspace_id",
                    session_name=session_name,
                )
            self._report_workspace_metadata(
                workspace_id,
                project_id=str(payload.get("project_id") or "").strip(),
                namespace_id=namespace_id,
                window_name=window_name,
                session_name=session_name,
            )
            if root_pane_id:
                self._report_pane_metadata(
                    root_pane_id,
                    session_name=session_name,
                    title=window_name,
                    tokens={
                        "ccb_project_id": str(payload.get("project_id") or "").strip(),
                        _NAMESPACE_TOKEN: namespace_id,
                        _LOGICAL_WINDOW_TOKEN: window_name,
                        _LOGICAL_WINDOW_ALIAS_TOKEN: window_name,
                        _ROOT_PANE_TOKEN: "1",
                    },
                )
            # New workspaces get a default tab named after the tab number;
            # rename it to match the window name so the Herdr tab bar shows a
            # meaningful label instead of "1", "2", …
            self._rename_workspace_tab(workspace_id, window_name, session_name=session_name)
            existing = {
                "workspace_id": workspace_id,
                "window_name": window_name,
                "focused": bool(payload.get("select", False)),
                "root_pane_id": root_pane_id,
            }
        workspace_id = str(existing.get("workspace_id") or namespace_id).strip()
        root_pane_id = str(existing.get("root_pane_id") or "").strip()
        if not workspace_id:
            raise self._failed(
                "ensure_window",
                f"no Herdr pane found for workspace {namespace_id!r}",
                session_name=session_name,
            )
        if bool(payload.get("select", False)):
            self._select_window(
                {
                    "namespace_id": namespace_id,
                    "session_name": session_name,
                    "window_id": workspace_id,
                }
            )
        return {
            "status": "ok",
            "window_id": workspace_id,
            "window_name": window_name,
            "active": bool(payload.get("select", False)),
            "root_pane_id": root_pane_id,
        }

    def _window_root_pane(self, payload: Mapping[str, object]) -> Mapping[str, object]:
        namespace_id = str(payload.get("namespace_id") or "").strip()
        session_name = _session_name_from_payload(payload, fallback_session_name=self._session_name)
        requested_window = str(payload.get("window_name") or "").strip()
        workspace = self._resolve_logical_workspace(
            namespace_id=namespace_id,
            requested_window=requested_window,
            session_name=session_name,
        )
        if workspace is None:
            raise self._not_found(
                "window_root_pane",
                f"unknown Herdr logical window {requested_window!r}",
                session_name=session_name,
            )
        workspace_id = str(workspace.get("workspace_id") or "").strip()
        pane_id = str(workspace.get("root_pane_id") or "").strip()
        if not pane_id:
            raise self._not_found(
                "window_root_pane",
                f"unknown Herdr logical window {requested_window!r}",
                session_name=session_name,
            )
        window_name = str(workspace.get("window_name") or "").strip()
        return {
            "status": "ok",
            "pane": {
                "pane_id": pane_id,
                "session_name": session_name,
                "workspace_id": workspace_id,
            },
            "pane_id": pane_id,
            "session_name": session_name,
            "window_name": window_name,
        }

    def _list_panes(self, payload: Mapping[str, object]) -> Mapping[str, object]:
        namespace_id = str(payload.get("namespace_id") or "").strip()
        session_name = _session_name_from_payload(payload, fallback_session_name=self._session_name)
        return {"status": "ok", "panes": self._panes(namespace_id, session_name=session_name)}

    def _create_pane(self, payload: Mapping[str, object]) -> Mapping[str, object]:
        namespace_id = str(payload.get("namespace_id") or "")
        session_name = _session_name_from_payload(payload, fallback_session_name=self._session_name)
        try:
            command = _command_text(payload.get("command"))
            split_direction = _split_direction(payload.get("direction"))
        except ValueError as exc:
            raise self._failed("create_pane", str(exc), session_name=session_name) from exc
        env = payload.get("env")
        if isinstance(env, Mapping) and env:
            raise self._failed(
                "create_pane",
                "Herdr CLI pane environment overrides are not supported by this adapter",
                session_name=session_name,
            )
        parent = str(payload.get("parent_pane") or "").strip()
        if parent:
            parent_record = self._pane_by_id(parent, session_name=session_name)
            if parent_record is None:
                raise self._failed(
                    "create_pane",
                    f"unknown Herdr parent pane {parent!r}",
                    session_name=session_name,
                )
            parent_workspace_id = str(parent_record.get("workspace_id") or "").strip()
            if parent_workspace_id != namespace_id:
                logical_workspace_ids = {
                    str(workspace.get("workspace_id") or "").strip()
                    for workspace in self._logical_workspaces(
                        namespace_id=namespace_id,
                        session_name=session_name,
                    )
                }
                if parent_workspace_id not in logical_workspace_ids:
                    raise self._not_found(
                        "create_pane",
                        f"unknown Herdr parent pane {parent!r}",
                        session_name=session_name,
                    )
        else:
            parent = self._first_pane(namespace_id, session_name=session_name)
            parent_record = self._pane_by_id(parent, session_name=session_name)
        parent_workspace_id = str((parent_record or {}).get("workspace_id") or "").strip()
        cwd = str(payload.get("cwd") or "")
        args = [
            "pane",
            "split",
            parent,
            "--direction",
            split_direction,
            "--ratio",
            _split_ratio(payload.get("percent")),
        ]
        if cwd:
            args.extend(["--cwd", cwd])
        args.append("--focus")
        result = self._json_command("create_pane", args, session_name=session_name)
        pane = _mapping(result.get("pane"))
        pane_id = str(pane.get("pane_id") or "").strip()
        if not pane_id:
            raise self._failed(
                "create_pane",
                "Herdr pane split response is missing pane_id",
                session_name=session_name,
            )
        if command:
            self._command(
                "create_pane",
                ["pane", "run", pane_id, command],
                expect_json=False,
                session_name=session_name,
            )
        return {
            "pane_id": pane_id,
            "session_name": session_name,
            "workspace_id": str(pane.get("workspace_id") or parent_workspace_id),
        }

    def _set_pane_identity(self, payload: Mapping[str, object]) -> Mapping[str, object]:
        pane_id = str(payload.get("pane_id") or "").strip()
        session_name = _session_name_from_payload(payload, fallback_session_name=self._session_name)
        tokens = {
            str(key).lstrip("@"): str(value)
            for key, value in _mapping(payload.get("tokens")).items()
            if str(key).strip()
        }
        existing = self._pane_by_id(pane_id, session_name=session_name)
        existing_tokens = _pane_tokens(existing) if existing is not None else {}
        tokens = {
            **{
                str(key): ""
                for key in existing_tokens
                if str(key) not in {_NAMESPACE_TOKEN, _ROOT_PANE_TOKEN, _LOGICAL_WINDOW_TOKEN, _LOGICAL_WINDOW_ALIAS_TOKEN}
            },
            **tokens,
        }
        for token_name in (_NAMESPACE_TOKEN, _ROOT_PANE_TOKEN, _LOGICAL_WINDOW_TOKEN, _LOGICAL_WINDOW_ALIAS_TOKEN):
            existing_value = str(existing_tokens.get(token_name) or "").strip()
            if existing_value:
                tokens[token_name] = existing_value
            else:
                tokens.pop(token_name, None)
        title = str(payload.get("title") or "").strip()
        agent_label = str(payload.get("agent_label") or "").strip()
        if agent_label:
            tokens.setdefault("ccb_agent_label", agent_label)
        self._wait_for_pane(pane_id, session_name=session_name)
        self._report_pane_metadata(
            pane_id,
            session_name=session_name,
            title=title or None,
            display_agent=agent_label or None,
            tokens=tokens,
        )
        return {"status": "ok", "pane_id": pane_id}

    def _report_pane_agent(self, payload: Mapping[str, object]) -> Mapping[str, object]:
        pane_id = str(payload.get("pane_id") or "").strip()
        session_name = _session_name_from_payload(payload, fallback_session_name=self._session_name)
        provider_kind = str(payload.get("provider_kind") or "").strip()
        state = str(payload.get("state") or "unknown").strip() or "unknown"
        if state not in {"idle", "working", "blocked", "unknown"}:
            raise self._failed(
                "report_pane_agent",
                f"invalid Herdr agent state {state!r}",
                session_name=session_name,
            )
        if not pane_id:
            raise self._failed("report_pane_agent", "requires pane_id", session_name=session_name)
        if not provider_kind:
            raise self._failed("report_pane_agent", "requires provider_kind", session_name=session_name)
        args = [
            "pane",
            "report-agent",
            pane_id,
            "--source",
            _METADATA_SOURCE,
            "--agent",
            provider_kind,
            "--state",
            state,
        ]
        session_id = str(payload.get("session_id") or "").strip()
        if session_id:
            args.extend(["--agent-session-id", session_id])
        session_path = str(payload.get("session_path") or "").strip()
        if session_path:
            args.extend(["--agent-session-path", session_path])
        self._command(
            "report_pane_agent",
            args,
            expect_json=False,
            session_name=session_name,
        )
        return {
            "status": "ok",
            "pane_id": pane_id,
            "provider_kind": provider_kind,
            "state": state,
        }

    def _respawn_pane(self, payload: Mapping[str, object]) -> Mapping[str, object]:
        pane_id = str(payload.get("pane_id") or "").strip()
        session_name = _session_name_from_payload(payload, fallback_session_name=self._session_name)
        try:
            command = _command_text(payload.get("command"))
        except ValueError as exc:
            raise self._failed("respawn_pane", str(exc), session_name=session_name) from exc
        if not pane_id:
            raise self._failed("respawn_pane", "Herdr respawn_pane requires pane_id", session_name=session_name)
        if command:
            self._command(
                "respawn_pane",
                ["pane", "run", pane_id, command],
                expect_json=False,
                session_name=session_name,
            )
        return {"status": "ok", "pane_id": pane_id}

    def _pane_process_info(self, payload: Mapping[str, object]) -> Mapping[str, object]:
        pane_id = str(payload.get("pane_id") or "").strip()
        session_name = _session_name_from_payload(payload, fallback_session_name=self._session_name)
        if not pane_id:
            raise self._failed("pane_process_info", "requires pane_id", session_name=session_name)
        result = self._command(
            "pane_process_info",
            ["pane", "process-info", "--pane", pane_id],
            expect_json=True,
            session_name=session_name,
        )
        foreground_pid = None
        process_info: dict[str, object] = {}
        stdout = (result.stdout or "").strip()
        if stdout:
            try:
                payload_out = json.loads(stdout)
                process_info = (
                    payload_out.get("process_info")
                    or (payload_out.get("result") or {}).get("process_info")
                    or {}
                )
                if isinstance(process_info, dict):
                    processes = process_info.get("foreground_processes") or []
                    if processes and isinstance(processes[0], dict):
                        foreground_pid = processes[0].get("pid")
            except json.JSONDecodeError:
                pass
        return {
            "status": "ok",
            "pane_id": pane_id,
            "foreground_pid": foreground_pid,
            "process_info": process_info,
        }

    def _move_pane(self, payload: Mapping[str, object]) -> Mapping[str, object]:
        source_pane_id = str(payload.get("source_pane_id") or "").strip()
        session_name = _session_name_from_payload(payload, fallback_session_name=self._session_name)
        if not source_pane_id:
            raise self._failed("move_pane", "Herdr move_pane requires source_pane_id", session_name=session_name)
        if self._pane_by_id(source_pane_id, session_name=session_name) is None:
            raise self._not_found(
                "move_pane",
                f"unknown Herdr source pane {source_pane_id!r}",
                session_name=session_name,
            )
        return {"status": "ok", "pane_id": source_pane_id}

    def _reflow_window(self, payload: Mapping[str, object]) -> Mapping[str, object]:
        namespace_id = str(payload.get("namespace_id") or "").strip()
        session_name = _session_name_from_payload(payload, fallback_session_name=self._session_name)
        requested_window = str(payload.get("window_id") or payload.get("window_name") or "").strip()
        workspace = self._resolve_logical_workspace(
            namespace_id=namespace_id,
            requested_window=requested_window,
            session_name=session_name,
        )
        workspace_id = str((workspace or {}).get("workspace_id") or "").strip()
        if not workspace_id:
            raise self._not_found(
                "reflow_window",
                f"unknown Herdr logical window {requested_window!r}",
                session_name=session_name,
            )
        return {"status": "ok", "window_id": workspace_id}

    def _select_window(self, payload: Mapping[str, object]) -> Mapping[str, object]:
        namespace_id = str(payload.get("namespace_id") or "").strip()
        session_name = _session_name_from_payload(payload, fallback_session_name=self._session_name)
        requested_window = str(payload.get("window_id") or "").strip()
        workspace = self._resolve_logical_workspace(
            namespace_id=namespace_id,
            requested_window=requested_window or namespace_id,
            session_name=session_name,
        )
        workspace_id = str((workspace or {}).get("workspace_id") or "").strip()
        if not workspace_id:
            raise self._not_found(
                "select_window",
                f"unknown Herdr logical window {requested_window!r}",
                session_name=session_name,
            )
        self._command(
            "select_window",
            ["workspace", "focus", workspace_id],
            expect_json=False,
            session_name=session_name,
        )
        return {"status": "ok", "namespace_id": namespace_id, "window_id": workspace_id}

    def _kill_window(self, payload: Mapping[str, object]) -> Mapping[str, object]:
        namespace_id = str(payload.get("namespace_id") or "").strip()
        requested_window = str(payload.get("window_id") or "").strip()
        session_name = _session_name_from_payload(payload, fallback_session_name=self._session_name)
        workspace = self._resolve_logical_workspace(
            namespace_id=namespace_id,
            requested_window=requested_window,
            session_name=session_name,
        )
        workspace_id = str((workspace or {}).get("workspace_id") or "").strip()
        if not workspace_id:
            raise self._not_found(
                "kill_window",
                f"unknown Herdr logical window {requested_window!r}",
                session_name=session_name,
            )
        self._command(
            "kill_window",
            ["workspace", "close", workspace_id],
            expect_json=False,
            session_name=session_name,
        )
        return {"status": "ok", "window_id": workspace_id}

    def _rename_window(self, payload: Mapping[str, object]) -> Mapping[str, object]:
        namespace_id = str(payload.get("namespace_id") or "").strip()
        session_name = _session_name_from_payload(payload, fallback_session_name=self._session_name)
        requested_window = str(payload.get("window_id") or "").strip()
        new_name = str(payload.get("new_name") or "").strip()
        if not new_name:
            raise self._failed(
                "rename_window",
                "Herdr rename_window requires new_name",
                session_name=session_name,
            )
        workspace = self._resolve_logical_workspace(
            namespace_id=namespace_id,
            requested_window=requested_window,
            session_name=session_name,
        )
        workspace_id = str((workspace or {}).get("workspace_id") or "").strip()
        root_pane_id = str((workspace or {}).get("root_pane_id") or "").strip()
        if not workspace_id:
            raise self._not_found(
                "rename_window",
                f"unknown Herdr logical window {requested_window!r}",
                session_name=session_name,
            )
        self._report_workspace_metadata(
            workspace_id,
            project_id=str(_mapping(workspace).get("ccb_project_id") or "").strip(),
            namespace_id=namespace_id,
            window_name=new_name,
            session_name=session_name,
        )
        if root_pane_id:
            self._report_pane_metadata(
                root_pane_id,
                session_name=session_name,
                title=new_name,
                tokens={
                    _NAMESPACE_TOKEN: namespace_id,
                    _LOGICAL_WINDOW_TOKEN: new_name,
                    _LOGICAL_WINDOW_ALIAS_TOKEN: new_name,
                    _ROOT_PANE_TOKEN: "1",
                },
            )
        return {"status": "ok", "window_id": workspace_id, "window_name": new_name}

    def _destroy_namespace(self, payload: Mapping[str, object]) -> Mapping[str, object]:
        namespace_id = str(payload.get("namespace_id") or "").strip()
        session_name = _session_name_from_payload(payload, fallback_session_name=self._session_name)
        if not namespace_id:
            raise self._failed(
                "destroy_namespace",
                "Herdr destroy_namespace requires namespace_id",
                session_name=session_name,
            )
        try:
            workspaces = self._logical_workspaces(
                namespace_id=namespace_id,
                session_name=session_name,
            )
        except MuxCommandErrorV2 as exc:
            # herdr server 未运行 / 会话不可达：namespace 已不可见，destroy 视为
            # 清理完成（幂等），避免 ccbd 启动/停止流程因 server_not_running 失败
            # 而中止（2026-08-06 采集暴露 lease_unmounted）。
            if _looks_like_missing_server(exc.detail):
                return {
                    "status": "ok",
                    "namespace_id": namespace_id,
                    "closed_workspace_ids": [],
                }
            raise
        closed: list[str] = []
        for workspace in workspaces:
            workspace_id = str(workspace.get("workspace_id") or "").strip()
            if not workspace_id:
                continue
            try:
                self._command(
                    "destroy_namespace",
                    ["workspace", "close", workspace_id],
                    expect_json=False,
                    session_name=session_name,
                )
            except MuxCommandErrorV2 as exc:
                # 幂等清理：workspace 已消失（not-found）或 Herdr server 未运行
                # （server_not_running）在 destroy/kill 场景都应视为清理完成，
                # 不冒泡为失败（2026-08-06-...-issue G4）。
                if exc.category == "not-found" or _looks_like_missing_server(exc.detail):
                    continue
                raise
            closed.append(workspace_id)
        return {
            "status": "ok",
            "namespace_id": namespace_id,
            "closed_workspace_ids": closed,
        }

    def _send_text(self, payload: Mapping[str, object]) -> Mapping[str, object]:
        pane_id = str(payload.get("pane_id") or "")
        text = str(payload.get("text") or "")
        session_name = _session_name_from_payload(payload, fallback_session_name=self._session_name)
        self._command(
            "send_text",
            ["pane", "run", pane_id, text],
            expect_json=False,
            session_name=session_name,
        )
        return {"status": "ok", "pane_id": pane_id}

    def _capture_pane(self, payload: Mapping[str, object]) -> Mapping[str, object]:
        pane_id = str(payload.get("pane_id") or "")
        lines = _line_count(payload.get("lines"))
        session_name = _session_name_from_payload(payload, fallback_session_name=self._session_name)
        result = self._command(
            "capture_pane",
            ["pane", "read", pane_id, "--lines", lines, "--format", "text"],
            expect_json=False,
            session_name=session_name,
        )
        return {"status": "ok", "pane_id": pane_id, "text": result.stdout}

    def _kill_pane(self, payload: Mapping[str, object]) -> Mapping[str, object]:
        pane_id = str(payload.get("pane_id") or "")
        session_name = _session_name_from_payload(payload, fallback_session_name=self._session_name)
        self._command(
            "kill_pane",
            ["pane", "close", pane_id],
            expect_json=False,
            session_name=session_name,
        )
        return {"status": "ok", "pane_id": pane_id}

    def _attach_namespace(self, payload: Mapping[str, object]) -> Mapping[str, object]:
        namespace_id = str(payload.get("namespace_id") or "").strip()
        if not namespace_id:
            raise self._failed("attach_namespace", "Herdr attach_namespace is missing namespace_id")
        session_name = _session_name_from_payload(payload, fallback_session_name=self._session_name)
        requested_window = str(payload.get("window_name") or "").strip() or namespace_id
        workspace = self._resolve_logical_workspace(
            namespace_id=namespace_id,
            requested_window=requested_window,
            session_name=session_name,
        )
        workspace_id = str((workspace or {}).get("workspace_id") or "").strip()
        if not workspace_id:
            raise self._not_found(
                "attach_namespace",
                f"unknown Herdr logical window {requested_window!r}",
                session_name=session_name,
            )
        self._command(
            "attach_namespace",
            ["workspace", "focus", workspace_id],
            expect_json=False,
            session_name=session_name,
        )
        # `herdr session attach` is a foreground terminal operation that only
        # succeeds when the calling terminal can take over the session.  In
        # contexts such as Herdr UI (where the terminal already belongs to a
        # different session) or daemon startups the command may exit non-zero
        # or block indefinitely.  Treat a failed attach as non-fatal: the
        # preceding `workspace focus` already brought the target workspace
        # into view.
        executable = self._resolve_executable()
        command = [executable, "session", "attach", session_name]
        attached = False
        try:
            self._run_fn(command, check=True, timeout=5, env=_env_without_xdg_redirects())
            attached = True
        except (OSError, subprocess.SubprocessError):
            pass
        return {
            "status": "ok",
            "namespace_id": namespace_id,
            "session_name": session_name,
            "window_id": workspace_id,
            "attached": attached,
        }

    def _is_alive(self, payload: Mapping[str, object]) -> Mapping[str, object]:
        try:
            captured = self._capture_pane({**dict(payload), "lines": 1})
        except MuxCommandErrorV2 as exc:
            if exc.category == "not-found":
                return {
                    "status": "ok",
                    "pane_id": str(payload.get("pane_id") or ""),
                    "alive": False,
                }
            raise
        return {"status": "ok", "pane_id": captured["pane_id"], "alive": True}

    def _workspaces(self, *, session_name: str | None = None) -> list[Mapping[str, object]]:
        try:
            result = self._json_command("restore_session", ["workspace", "list"], session_name=session_name)
        except MuxCommandErrorV2 as exc:
            if not _looks_like_herdr_list_parse_gap(exc.detail):
                raise
            return self._snapshot_items("restore_session", "workspaces", session_name=session_name)
        workspaces = result.get("workspaces")
        return [item for item in workspaces if isinstance(item, Mapping)] if isinstance(workspaces, list) else []

    def _logical_workspaces(
        self,
        *,
        namespace_id: str,
        session_name: str,
    ) -> list[Mapping[str, object]]:
        namespace_anchor = str(namespace_id or "").strip()
        if not namespace_anchor:
            return []
        workspaces = self._workspaces(session_name=session_name)
        panes = self._panes("", session_name=session_name)
        roots_by_workspace: dict[str, Mapping[str, object]] = {}
        for pane in panes:
            workspace_id = str(pane.get("workspace_id") or "").strip()
            tokens = _pane_tokens(pane)
            if (
                workspace_id
                and str(tokens.get(_ROOT_PANE_TOKEN) or "").strip() == "1"
                and str(tokens.get(_NAMESPACE_TOKEN) or "").strip() == namespace_anchor
            ):
                roots_by_workspace.setdefault(workspace_id, pane)
        logical: list[Mapping[str, object]] = []
        for workspace in workspaces:
            workspace_id = str(workspace.get("workspace_id") or "").strip()
            if not workspace_id:
                continue
            root = roots_by_workspace.get(workspace_id)
            if root is None:
                continue
            root_tokens = _pane_tokens(root)
            window_name = str(
                root_tokens.get(_LOGICAL_WINDOW_TOKEN)
                or root_tokens.get(_LOGICAL_WINDOW_ALIAS_TOKEN)
                or ""
            ).strip()
            if not window_name:
                continue
            logical.append(
                {
                    **dict(workspace),
                    "window_name": window_name,
                    "root_pane_id": str(root.get("pane_id") or "").strip(),
                }
            )
        return logical

    def _resolve_logical_workspace(
        self,
        *,
        namespace_id: str,
        requested_window: str,
        session_name: str,
    ) -> Mapping[str, object] | None:
        target = str(requested_window or "").strip()
        if not target:
            return None
        for workspace in self._logical_workspaces(
            namespace_id=namespace_id,
            session_name=session_name,
        ):
            if target in {
                str(workspace.get("workspace_id") or "").strip(),
                str(workspace.get("window_name") or "").strip(),
            }:
                return workspace
        return None

    def _pane_by_id(self, pane_id: str, *, session_name: str) -> Mapping[str, object] | None:
        target = str(pane_id or "").strip()
        if not target:
            return None
        for pane in self._panes("", session_name=session_name):
            if str(pane.get("pane_id") or "").strip() == target:
                return pane
        return None

    def _wait_for_pane(self, pane_id: str, *, session_name: str) -> Mapping[str, object] | None:
        for _ in range(10):
            pane = self._pane_by_id(pane_id, session_name=session_name)
            if pane is not None:
                return pane
            self._sleep_fn(0.1)
        return None

    def _panes(self, namespace_id: str, *, session_name: str) -> list[Mapping[str, object]]:
        args = ["pane", "list"]
        if namespace_id:
            args.extend(["--workspace", namespace_id])
        try:
            result = self._json_command("list_panes", args, session_name=session_name)
        except MuxCommandErrorV2 as exc:
            if not _looks_like_herdr_list_parse_gap(exc.detail):
                raise
            panes = self._snapshot_items("list_panes", "panes", session_name=session_name)
            if namespace_id:
                return [
                    pane
                    for pane in panes
                    if str(pane.get("workspace_id") or "").strip() == str(namespace_id or "").strip()
                ]
            return panes
        panes = result.get("panes")
        return [item for item in panes if isinstance(item, Mapping)] if isinstance(panes, list) else []

    def _first_pane(self, namespace_id: str, *, session_name: str) -> str:
        for pane in self._panes("", session_name=session_name):
            if pane.get("workspace_id") == namespace_id:
                pane_id = str(pane.get("pane_id") or "")
                if pane_id:
                    return pane_id
        raise self._failed(
            "create_pane",
            f"no Herdr pane found for workspace {namespace_id!r}",
            session_name=session_name,
        )

    def _snapshot_items(
        self,
        operation: str,
        key: str,
        *,
        session_name: str | None,
    ) -> list[Mapping[str, object]]:
        result = self._json_command(operation, ["api", "snapshot"], session_name=session_name)
        snapshot = _mapping(result.get("snapshot"))
        items = snapshot.get(key)
        return [item for item in items if isinstance(item, Mapping)] if isinstance(items, list) else []

    def _root_pane_for_workspace(self, workspace_id: str, *, session_name: str) -> str | None:
        workspace_text = str(workspace_id or "").strip()
        if not workspace_text:
            return None
        for pane in self._panes(workspace_text, session_name=session_name):
            if str(_pane_tokens(pane).get(_ROOT_PANE_TOKEN) or "").strip() == "1":
                pane_id = str(pane.get("pane_id") or "").strip()
                if pane_id:
                    return pane_id
        return None

    def _rename_workspace_tab(
        self,
        workspace_id: str,
        title: str,
        *,
        session_name: str,
    ) -> None:
        """Rename the default tab of a newly created workspace.

        Herdr names the first tab after its number (``"1"``, ``"2"``, …);
        this renames it to *title* so the tab bar shows a meaningful label.
        The rename is best-effort — a failure leaves the default number and
        does not block workspace creation.
        """
        if not isinstance(title, str) or not title.strip():
            return
        tab_id = f"{workspace_id}:t1"
        try:
            self._command(
                "rename_tab",
                ["tab", "rename", tab_id, title],
                expect_json=False,
                session_name=session_name,
            )
        except Exception:
            pass

    def _report_workspace_metadata(
        self,
        workspace_id: str,
        *,
        project_id: str,
        namespace_id: str,
        window_name: str,
        session_name: str,
    ) -> None:
        tokens = {
            "ccb_project_id": project_id,
            _NAMESPACE_TOKEN: namespace_id,
            _LOGICAL_WINDOW_TOKEN: window_name,
            _LOGICAL_WINDOW_ALIAS_TOKEN: window_name,
        }
        args = ["workspace", "report-metadata", workspace_id, "--source", _METADATA_SOURCE]
        for name, value in tokens.items():
            if value:
                args.extend(["--token", f"{name}={value}"])
        self._command(
            "report_workspace_metadata",
            args,
            expect_json=False,
            session_name=session_name,
        )

    def _report_pane_metadata(
        self,
        pane_id: str,
        *,
        session_name: str,
        title: str | None = None,
        display_agent: str | None = None,
        tokens: Mapping[str, str] | None = None,
    ) -> None:
        args = ["pane", "report-metadata", pane_id, "--source", _METADATA_SOURCE]
        if title:
            args.extend(["--title", title])
        if display_agent:
            args.extend(["--display-agent", display_agent])
        for name, value in (tokens or {}).items():
            token_name = str(name).lstrip("@").strip()
            if token_name:
                args.extend(["--token", f"{token_name}={str(value)}"])
        self._command(
            "report_pane_metadata",
            args,
            expect_json=False,
            session_name=session_name,
        )

    def _json_command(
        self,
        operation: str,
        args: list[str],
        *,
        session_name: str | None = None,
        ensure_server: bool = True,
    ) -> Mapping[str, object]:
        result = self._command(
            operation,
            args,
            expect_json=True,
            session_name=session_name,
            ensure_server=ensure_server,
        )
        try:
            payload = json.loads(result.stdout or "{}")
        except json.JSONDecodeError as exc:
            raise self._failed(
                operation,
                "Herdr command did not return JSON",
                session_name=session_name,
            ) from exc
        if not isinstance(payload, Mapping):
            raise self._failed(
                operation,
                "Herdr command JSON response is not an object",
                session_name=session_name,
            )
        outer_detail = _response_detail(payload)
        self._require_ok_json_status(
            operation,
            payload,
            detail=outer_detail,
            session_name=session_name,
        )
        nested = payload.get("result")
        if isinstance(nested, Mapping):
            self._require_ok_json_status(
                operation,
                nested,
                detail=_response_detail(nested) or outer_detail,
                session_name=session_name,
            )
            return nested
        return payload

    def _command(
        self,
        operation: str,
        args: list[str],
        *,
        expect_json: bool,
        session_name: str | None = None,
        ensure_server: bool = True,
    ) -> subprocess.CompletedProcess:
        executable = self._resolve_executable()
        effective_session = session_name or self._session_name
        command = [executable, "--session", effective_session, *args]
        try:
            return self._run_command_once(
                operation,
                command,
                expect_json=expect_json,
                session_name=effective_session,
            )
        except MuxCommandErrorV2 as exc:
            if not ensure_server or not _looks_like_missing_server(exc.detail):
                raise
            self._start_server(effective_session, executable=executable)
        last_error: MuxCommandErrorV2 | None = None
        for _ in range(10):
            try:
                return self._run_command_once(
                    operation,
                    command,
                    expect_json=expect_json,
                    session_name=effective_session,
                )
            except MuxCommandErrorV2 as exc:
                if not _looks_like_missing_server(exc.detail):
                    raise
                last_error = exc
                self._sleep_fn(0.1)
        assert last_error is not None
        raise last_error

    def _run_command_once(
        self,
        operation: str,
        command: list[str],
        *,
        expect_json: bool,
        session_name: str,
    ) -> subprocess.CompletedProcess:
        try:
            return self._run_fn(
                command,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                check=True,
                env=_env_without_xdg_redirects(),
            )
        except (OSError, subprocess.SubprocessError) as exc:
            detail = f"Herdr command failed for {operation}"
            if isinstance(exc, subprocess.CalledProcessError):
                detail = (exc.stderr or exc.stdout or detail).strip() or detail
            raise MuxCommandErrorV2(
                category=_command_error_category(detail, expect_json=expect_json),
                backend_impl="herdr",
                operation=operation,
                detail=detail,
                ipc_ref=self._ipc_ref_for_session(session_name),
                evidence=_command_evidence(operation, command),
            ) from exc

    def _start_server(self, session_name: str, *, executable: str) -> None:
        existing = self._server_processes.get(session_name)
        if existing is not None and existing.poll() is None:
            return
        self._server_sessions.discard(session_name)
        self._server_processes.pop(session_name, None)
        clean_env = _env_without_xdg_redirects()
        command = [executable, "--session", session_name, "server"]
        kwargs: dict[str, object] = {
            "stdin": subprocess.DEVNULL,
            "stdout": subprocess.DEVNULL,
            "stderr": subprocess.DEVNULL,
            "env": clean_env,
        }
        if sys.platform.startswith("win"):
            kwargs["creationflags"] = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        try:
            process = self._popen_fn(command, **kwargs)
        except OSError as exc:
            raise MuxCommandErrorV2(
                category="transient-unavailable",
                backend_impl="herdr",
                operation="start_server",
                detail=f"Herdr server failed to start: {exc}",
                ipc_ref=self._ipc_ref_for_session(session_name),
                evidence=_command_evidence("start_server", command),
            ) from exc
        poll = getattr(process, "poll", None)
        for _ in range(20):
            self._sleep_fn(0.1)
            if callable(poll):
                exit_code = poll()
                if exit_code is not None:
                    raise MuxCommandErrorV2(
                        category="transient-unavailable",
                        backend_impl="herdr",
                        operation="start_server",
                        detail=f"Herdr server exited immediately with code {exit_code}",
                        ipc_ref=self._ipc_ref_for_session(session_name),
                        evidence={
                            **_command_evidence("start_server", command),
                            "exit_code": exit_code,
                        },
                    )
            if self._server_status_running(executable, session_name=session_name, env=clean_env):
                self._server_sessions.add(session_name)
                self._server_processes[session_name] = process
                return
        raise MuxCommandErrorV2(
            category="transient-unavailable",
            backend_impl="herdr",
            operation="start_server",
            detail="Herdr server did not become ready",
            ipc_ref=self._ipc_ref_for_session(session_name),
            evidence=_command_evidence("start_server", command),
        )

    def ensure_server_started(self, session_name: str | None = None) -> None:
        """Ensure a session-scoped Herdr server is running, starting it if needed.

        Public wrapper around ``_ensure_server_ready`` so callers outside the
        adapter (e.g. the ``ccb herdr open`` bootstrap) can reuse the canonical
        server-start + readiness-poll logic instead of reimplementing it.
        """
        self._ensure_server_ready(session_name or self._session_name)

    def _ensure_server_ready(self, session_name: str) -> None:
        executable = self._resolve_executable()
        if self._server_status_running(executable, session_name=session_name,
                                       env=_env_without_xdg_redirects()):
            return
        self._start_server(session_name, executable=executable)

    def _server_status_running(
        self, executable: str, *, session_name: str | None, env: dict[str, str] | None = None
    ) -> bool:
        if session_name is not None:
            command = [executable, "--session", session_name, "status", "server", "--json"]
        else:
            command = [executable, "status", "server", "--json"]
        kwargs: dict[str, object] = dict(
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            check=True,
        )
        if env is not None:
            kwargs["env"] = env
        try:
            result = self._run_fn(command, **kwargs)
            payload = json.loads(result.stdout or "{}")
        except Exception:
            return False
        if not isinstance(payload, Mapping):
            return False
        server = payload.get("server")
        status = server if isinstance(server, Mapping) else payload
        return bool(status.get("running") is True or status.get("status") == "running")

    def _resolve_executable(self) -> str:
        executable = (self._herdr_executable or "").strip()
        if not executable:
            executable = resolve_herdr_executable()
        if not executable:
            executable = self._which_fn("herdr")
        if executable:
            return executable
        raise MuxCommandErrorV2(
            category="transient-unavailable",
            backend_impl="herdr",
            operation="resolve_executable",
            detail="Herdr executable is not available",
            ipc_ref=self.socket_ref,
        )

    def _failed(
        self,
        operation: str,
        detail: str,
        *,
        session_name: str | None = None,
    ) -> MuxCommandErrorV2:
        effective_session = session_name or self._session_name
        return MuxCommandErrorV2(
            category="command-failed",
            backend_impl="herdr",
            operation=operation,
            detail=detail,
            ipc_ref=self._ipc_ref_for_session(effective_session),
        )

    def _not_found(
        self,
        operation: str,
        detail: str,
        *,
        session_name: str | None = None,
    ) -> MuxCommandErrorV2:
        effective_session = session_name or self._session_name
        return MuxCommandErrorV2(
            category="not-found",
            backend_impl="herdr",
            operation=operation,
            detail=detail,
            ipc_ref=self._ipc_ref_for_session(effective_session),
        )

    def _require_ok_json_status(
        self,
        operation: str,
        response: Mapping[str, object],
        *,
        detail: str,
        session_name: str | None,
    ) -> None:
        status = str(response.get("status") or "").strip()
        if status and status != "ok":
            raise self._failed(
                operation,
                detail or f"Herdr command returned status {status}",
                session_name=session_name,
            )

    def _ipc_ref_for_session(self, session_name: str) -> str:
        return self._socket_ref or f"herdr://{session_name}"


def _mapping(raw: object) -> Mapping[str, object]:
    return raw if isinstance(raw, Mapping) else {}


def _pane_tokens(pane: Mapping[str, object]) -> Mapping[str, object]:
    return _mapping(pane.get("tokens"))


def _anchor_workspace_can_be_claimed(
    workspace: Mapping[str, object],
    *,
    session_name: str,
    requested_window_name: str,
) -> bool:
    current_window_name = str(workspace.get("window_name") or "").strip()
    return not current_window_name or current_window_name in {
        session_name,
        requested_window_name,
    }


def _restore_token(session_name: str, namespace_id: str) -> str:
    return f"{session_name}::{namespace_id}"


def _split_restore_token(token: str) -> tuple[str, str]:
    if token.count("::") != 1:
        raise ValueError("Herdr restore_token must use session::workspace format")
    session_name, namespace_id = token.split("::", 1)
    session_name = session_name.strip()
    namespace_id = namespace_id.strip()
    if not session_name or not namespace_id:
        raise ValueError("Herdr restore_token must include non-empty session and workspace")
    return session_name, namespace_id


def _response_detail(response: Mapping[str, object]) -> str:
    return str(response.get("detail") or response.get("message") or "").strip()


def _command_text(raw: object) -> str:
    if raw is None:
        return ""
    if not isinstance(raw, list):
        raise ValueError("Herdr command must be a list of argv parts")
    argv = [str(part) for part in raw if str(part).strip()]
    if sys.platform.startswith("win"):
        return subprocess.list2cmdline(argv).strip()
    return shlex.join(argv).strip()


def _session_name_from_payload(
    payload: Mapping[str, object],
    *,
    fallback_session_name: str,
) -> str:
    session_name = str(payload.get("session_name") or "").strip()
    return session_name or fallback_session_name


def _create_session_scope(title: str, *, fallback_session_name: str) -> str:
    title_text = str(title or "").strip()
    if title_text.startswith("ccb-"):
        return title_text
    return str(fallback_session_name or "").strip() or title_text or "ccb-herdr"


def _split_direction(raw: object) -> str:
    direction = str(raw or "right").strip().lower()
    if direction in {"right", "horizontal"}:
        return "right"
    if direction in {"down", "bottom", "vertical"}:
        return "down"
    raise ValueError(f"unsupported Herdr split direction {direction!r}; expected right or bottom/down")


def _split_ratio(raw: object) -> str:
    try:
        percent = int(raw)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        percent = 50
    percent = min(max(percent, 1), 100)
    return str(percent / 100).rstrip("0").rstrip(".")


def _line_count(raw: object) -> str:
    try:
        line_count = int(raw)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        line_count = 100
    return str(max(line_count, 1))


def _runtime_platform() -> str:
    if sys.platform.startswith("win"):
        return "windows"
    if sys.platform.startswith("linux"):
        return "linux"
    if sys.platform == "darwin":
        return "darwin"
    return sys.platform


def _runtime_arch() -> str:
    machine = platform.machine().lower()
    if machine in {"amd64", "x86_64"}:
        return "x64"
    if machine in {"arm64", "aarch64"}:
        return "arm64"
    return machine


def _command_evidence(operation: str, command: list[str]) -> dict[str, object]:
    return {
        "operation": operation,
        "executable": command[0] if command else "",
        "arg_count": max(len(command) - 1, 0),
        "argv": _redacted_argv(operation, command),
    }


def _command_error_category(detail: str, *, expect_json: bool) -> str:
    lowered = detail.lower()
    if (
        "not found" in lowered
        or "not_found" in lowered
        or "notfound" in lowered
        or "unknown pane" in lowered
        or "unknown workspace" in lowered
    ):
        return "not-found"
    return "transient-unavailable" if expect_json else "command-failed"


def _looks_like_herdr_list_parse_gap(detail: str) -> bool:
    lowered = str(detail or "").lower()
    return "did not return json" in lowered or "json response is not an object" in lowered


def _looks_like_missing_server(detail: str) -> bool:
    lowered = detail.lower()
    return (
        "kind: notfound" in lowered
        or "kind: not found" in lowered
        or "os { code: 2" in lowered
        or "server_not_running" in lowered
        or "no herdr server is running" in lowered
    )


def _redacted_argv(operation: str, command: list[str]) -> list[str]:
    if operation != "send_text":
        return list(command)
    redacted = list(command)
    if "--session" in redacted:
        session_index = redacted.index("--session")
        redacted = list(redacted[:session_index - 1]) + ["<redacted>", "--session", redacted[session_index + 1]]
    elif redacted:
        redacted[-1] = "<redacted>"
    return redacted


def _env_without_xdg_redirects() -> dict[str, str]:
    """Return a copy of os.environ with the wrapper's XDG sandbox redirects
    removed, so that Herdr CLI commands can reach the real default Herdr
    server instead of the source-dev sandbox."""
    env = dict(os.environ)
    for key in ("XDG_CONFIG_HOME", "XDG_CACHE_HOME", "XDG_STATE_HOME"):
        env.pop(key, None)
    # Herdr v0.8.0 respects HERDR_CONFIG_PATH.  When XDG is cleared,
    # point to the real config so the CLI discovers the running daemon.
    if "HERDR_CONFIG_PATH" not in env:
        env["HERDR_CONFIG_PATH"] = os.path.join(
            os.environ.get("USERPROFILE", os.path.expanduser("~")),
            "AppData", "Roaming", "herdr", "config.toml",
        )
    return env


__all__ = ["HerdrCliRequestAdapter"]
