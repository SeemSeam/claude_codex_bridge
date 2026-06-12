"""Structured logging for the inter-agent communication path.

Failures on the comm path (FIFO reads/writes, pane sends) were historically
swallowed; this logger makes them observable without changing behavior.
Logging itself must never break communication, so every operation here is
best-effort.
"""

from __future__ import annotations

import logging
import os
from logging.handlers import RotatingFileHandler
from pathlib import Path

_LOG_ENV_DIR = "CCB_COMM_LOG_DIR"
_DEFAULT_DIR = Path.home() / ".ccb" / "logs"
_FORMAT = "%(asctime)s %(name)s %(levelname)s %(message)s"

_configured: dict[str, logging.Logger] = {}


def _log_dir() -> Path:
    override = os.environ.get(_LOG_ENV_DIR)
    return Path(override) if override else _DEFAULT_DIR


def get_comm_logger(name: str) -> logging.Logger:
    """Return a logger writing to <log_dir>/comm.log; never raises."""
    full_name = f"ccb.comm.{name}"
    cached = _configured.get(full_name)
    if cached is not None:
        return cached
    logger = logging.getLogger(full_name)
    logger.propagate = False
    logger.setLevel(logging.INFO)
    if not logger.handlers:
        try:
            log_dir = _log_dir()
            log_dir.mkdir(parents=True, exist_ok=True)
            handler = RotatingFileHandler(
                log_dir / "comm.log", maxBytes=2_000_000, backupCount=2, encoding="utf-8"
            )
            handler.setFormatter(logging.Formatter(_FORMAT))
            logger.addHandler(handler)
        except Exception:
            logger.addHandler(logging.NullHandler())
    _configured[full_name] = logger
    return logger


def log_comm_event(
    logger: logging.Logger,
    *,
    provider: str,
    direction: str,
    endpoint: str,
    event: str,
    error: BaseException | None = None,
) -> None:
    """Record one comm-path event; swallows its own failures."""
    try:
        detail = f"provider={provider} direction={direction} endpoint={endpoint} event={event}"
        if error is not None:
            logger.warning("%s error_type=%s error=%s", detail, type(error).__name__, error)
        else:
            logger.info(detail)
    except Exception:
        pass


__all__ = ["get_comm_logger", "log_comm_event"]
