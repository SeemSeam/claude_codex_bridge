from __future__ import annotations

import os

from runtime_env import env_bool, env_float, env_int
from terminal_runtime.env import tmux_history_limit


def test_env_bool_truthy_and_falsy(monkeypatch) -> None:
    monkeypatch.delenv("X", raising=False)
    assert env_bool("X", default=True) is True
    assert env_bool("X", default=False) is False

    for v in ("1", "true", "yes", "on", " TRUE ", "Yes"):
        monkeypatch.setenv("X", v)
        assert env_bool("X", default=False) is True

    for v in ("0", "false", "no", "off", " 0 ", "False"):
        monkeypatch.setenv("X", v)
        assert env_bool("X", default=True) is False

    monkeypatch.setenv("X", "maybe")
    assert env_bool("X", default=True) is True
    assert env_bool("X", default=False) is False


def test_env_int_parsing(monkeypatch) -> None:
    monkeypatch.delenv("X", raising=False)
    assert env_int("X", 7) == 7

    monkeypatch.setenv("X", " 42 ")
    assert env_int("X", 7) == 42

    monkeypatch.setenv("X", "bad")
    assert env_int("X", 7) == 7


def test_env_float_parsing(monkeypatch) -> None:
    monkeypatch.delenv("X", raising=False)
    assert env_float("X", 1.5) == 1.5

    monkeypatch.setenv("X", " 2.75 ")
    assert env_float("X", 1.5) == 2.75

    monkeypatch.setenv("X", "bad")
    assert env_float("X", 1.5) == 1.5


def test_tmux_history_limit_defaults_and_allows_non_negative_override(monkeypatch) -> None:
    monkeypatch.delenv("CCB_TMUX_HISTORY_LIMIT", raising=False)
    assert tmux_history_limit() == 10000

    monkeypatch.setenv("CCB_TMUX_HISTORY_LIMIT", "1234")
    assert tmux_history_limit() == 1234

    monkeypatch.setenv("CCB_TMUX_HISTORY_LIMIT", "-5")
    assert tmux_history_limit() == 0

    monkeypatch.setenv("CCB_TMUX_HISTORY_LIMIT", "invalid")
    assert tmux_history_limit() == 10000
