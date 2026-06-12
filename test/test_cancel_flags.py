"""Tests for filesystem cancel flags written on job cancellation (phase 2.1)."""

from __future__ import annotations

from pathlib import Path

from ccbd.api_models import DeliveryScope, MessageEnvelope
from ccbd.services.dispatcher import JobDispatcher
from ccbd.services.dispatcher_runtime.cancel_flags import (
    cancel_flag_path,
    cleanup_cancel_flags,
    write_cancel_flag,
)
from ccbd.services.registry import AgentRegistry
from storage.paths import PathLayout

from test_v2_ccbd_dispatcher import _bootstrap_test_project, _provider_config, _runtime


def _make_dispatcher(tmp_path: Path):
    project_root = tmp_path / "repo"
    ctx = _bootstrap_test_project(project_root)
    layout = PathLayout(project_root)
    config = _provider_config("codex")
    registry = AgentRegistry(layout, config)
    registry.upsert(_runtime("codex", project_id=ctx.project_id, layout=layout, pid=101))
    dispatcher = JobDispatcher(layout, config, registry, clock=lambda: "2026-06-13T00:00:00Z")
    return ctx, layout, dispatcher


def _submit(dispatcher, ctx, body: str):
    return dispatcher.submit(
        MessageEnvelope(
            project_id=ctx.project_id,
            to_agent="codex",
            from_actor="user",
            body=body,
            task_id=None,
            reply_to=None,
            message_type="ask",
            delivery_scope=DeliveryScope.SINGLE,
        )
    )


def test_cancel_writes_flag_file_for_agent(tmp_path: Path) -> None:
    ctx, layout, dispatcher = _make_dispatcher(tmp_path)
    receipt = _submit(dispatcher, ctx, "task body")
    job_id = receipt.jobs[0].job_id

    dispatcher.cancel(job_id)

    flag = cancel_flag_path(layout, "codex", job_id)
    assert flag.exists(), "cancel must drop a flag file visible to the agent"
    assert dispatcher.get(job_id).status.value == "cancelled"


def test_write_and_cleanup_cancel_flags(tmp_path: Path) -> None:
    ctx, layout, _ = _make_dispatcher(tmp_path)
    import os
    import time

    fresh = write_cancel_flag(layout, "codex", "job-fresh")
    stale = write_cancel_flag(layout, "codex", "job-stale")
    assert fresh is not None and stale is not None
    os.utime(stale, (time.time() - 100_000, time.time() - 100_000))

    cleanup_cancel_flags(layout, "codex")

    assert fresh.exists()
    assert not stale.exists()
