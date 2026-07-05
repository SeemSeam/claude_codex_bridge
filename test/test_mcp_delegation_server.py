from __future__ import annotations

import importlib.util
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
SERVER_PATH = REPO_ROOT / "mcp" / "ccb-delegation" / "server.py"


def _load_module():
    spec = importlib.util.spec_from_file_location("ccb_delegation_server", SERVER_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_mcp_delegation_server_exposes_agent_first_tools() -> None:
    module = _load_module()
    tool_names = {tool["name"] for tool in module.TOOL_DEFS}

    assert {"ccb_ask_agent", "ccb_pend_agent", "ccb_ping_agent", "ccb_roster", "ccb_peer_status"} <= tool_names
    assert "ccb_ask_codex" not in tool_names
    assert "cask" not in tool_names
