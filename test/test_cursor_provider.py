from __future__ import annotations

from provider_backends.cursor import execution
from provider_backends.native_cli_support import NativeCliSubprocessAdapter


def test_cursor_execution_adapter_defaults_to_visible_pane(monkeypatch) -> None:
    monkeypatch.delenv("CCB_CURSOR_EXECUTION_MODE", raising=False)

    adapter = execution.build_execution_adapter()

    assert type(adapter).__name__ == "CursorPaneExecutionAdapter"
    assert getattr(adapter, "provider", "") == "cursor"


def test_cursor_execution_adapter_supports_explicit_headless_rollback(monkeypatch) -> None:
    monkeypatch.setenv("CCB_CURSOR_EXECUTION_MODE", "headless")

    adapter = execution.build_execution_adapter()

    assert isinstance(adapter, NativeCliSubprocessAdapter)


def test_cursor_execution_adapter_rejects_unknown_mode(monkeypatch) -> None:
    monkeypatch.setenv("CCB_CURSOR_EXECUTION_MODE", "mirror")

    try:
        execution.build_execution_adapter()
    except ValueError as exc:
        assert "CCB_CURSOR_EXECUTION_MODE" in str(exc)
        assert "mirror" in str(exc)
    else:
        raise AssertionError("unknown Cursor execution mode must fail closed")


def test_cursor_headless_command_and_env_builders_remain_available() -> None:
    assert callable(execution.build_headless_execution_adapter)
    assert callable(execution._build_command)
    assert callable(execution._build_env)
