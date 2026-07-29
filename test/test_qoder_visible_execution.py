from __future__ import annotations

from pathlib import Path

import pytest
from ccbd.api_models import DeliveryScope, JobRecord, JobStatus, MessageEnvelope
from completion.models import CompletionSourceKind, CompletionStatus
from provider_backends.qoder.execution import (
    QoderPaneExecutionAdapter,
    build_qoder_pane_execution_adapter,
)
from provider_core.protocol import request_anchor_for_job
from provider_core.registry import build_default_backend_registry
from provider_execution.base import ProviderRuntimeContext


class _Backend:
    def __init__(self) -> None:
        self.text = "Qoder ready\n"
        self.sent: list[tuple[str, str]] = []
        self.keys: list[tuple[str, str]] = []

    def send_text_to_pane(self, pane_id: str, text: str) -> None:
        self.sent.append((pane_id, text))

    def get_pane_content(self, pane_id: str, *, lines: int) -> str:
        del pane_id, lines
        return self.text

    def send_key(self, pane_id: str, key: str) -> None:
        self.keys.append((pane_id, key))


class _Session:
    def __init__(self, *, pane_result: tuple[bool, str] = (True, "%12")) -> None:
        self.data = {"terminal": "tmux", "pane_id": "%12"}
        self._pane_result = pane_result

    def ensure_pane(self) -> tuple[bool, str]:
        return self._pane_result


def _job(provider: str, agent_name: str, work_dir: Path) -> JobRecord:
    return JobRecord(
        job_id=f"job_{provider}_visible123",
        submission_id=f"sub_{provider}_visible123",
        agent_name=agent_name,
        provider=provider,
        request=MessageEnvelope(
            project_id="proj",
            to_agent=agent_name,
            from_actor="main",
            body="Reply from the visible pane.",
            task_id=None,
            reply_to=None,
            message_type="ask",
            delivery_scope=DeliveryScope.SINGLE,
        ),
        status=JobStatus.RUNNING,
        terminal_decision=None,
        cancel_requested_at=None,
        created_at="2026-07-29T00:00:00Z",
        updated_at="2026-07-29T00:00:00Z",
        workspace_path=str(work_dir),
    )


def _context(agent_name: str, work_dir: Path) -> ProviderRuntimeContext:
    return ProviderRuntimeContext(
        agent_name=agent_name,
        workspace_path=str(work_dir),
        backend_type="pane-backed",
        runtime_ref="%12",
        session_ref=str(work_dir / ".ccb" / f"session-{agent_name}"),
    )


@pytest.mark.parametrize(
    ("provider", "agent_name"),
    (("qoder", "qoder"), ("qoderclicn", "qodercn")),
)
def test_qoder_asks_execute_in_exact_managed_visible_pane(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    provider: str,
    agent_name: str,
) -> None:
    backend = _Backend()
    session = _Session()
    loaded_instances: list[str | None] = []

    def load_session(work_dir: Path, instance: str | None = None):
        assert work_dir == tmp_path
        loaded_instances.append(instance)
        return session

    def hidden_process_forbidden(*args, **kwargs):
        del args, kwargs
        raise AssertionError("visible Qoder ask must not create a hidden subprocess")

    monkeypatch.setattr(
        "provider_backends.native_cli_support.execution.subprocess.Popen",
        hidden_process_forbidden,
    )
    adapter = build_qoder_pane_execution_adapter(
        provider=provider,
        load_project_session_fn=load_session,
        backend_for_session_fn=lambda data: backend,
    )
    job = _job(provider, agent_name, tmp_path)

    submission = adapter.start(
        job,
        context=_context(agent_name, tmp_path),
        now="2026-07-29T00:00:00Z",
    )

    assert loaded_instances == [agent_name]
    assert submission.source_kind is CompletionSourceKind.TERMINAL_TEXT
    assert submission.diagnostics["mode"] == "visible_pane"
    assert submission.runtime_state["pane_id"] == "%12"
    assert len(backend.sent) == 1
    sent_pane, sent_prompt = backend.sent[0]
    assert sent_pane == "%12"
    assert "Reply from the visible pane." in sent_prompt
    req_id = request_anchor_for_job(job.job_id)
    assert f"CCB_REQ_ID: {req_id}" in sent_prompt
    assert f"CCB_DONE: {req_id}" in sent_prompt

    backend.text = (
        f"CCB_REQ_ID: {req_id}\n"
        "IMPORTANT: when you finish answering\n"
        f"CCB_DONE: {req_id}\n"
        "visible Qoder reply\n"
        f"CCB_DONE: {req_id}\n"
    )
    result = adapter.poll(submission, now="2026-07-29T00:00:03Z")

    assert result is not None
    assert result.decision is not None
    assert result.decision.status is CompletionStatus.COMPLETED
    assert result.decision.reply == "visible Qoder reply"


def test_qoder_visible_adapter_fails_closed_when_owned_pane_is_unavailable(tmp_path: Path) -> None:
    backend = _Backend()
    adapter = build_qoder_pane_execution_adapter(
        provider="qoder",
        load_project_session_fn=lambda work_dir, instance=None: _Session(
            pane_result=(False, "Pane not alive: %12")
        ),
        backend_for_session_fn=lambda data: backend,
    )

    submission = adapter.start(
        _job("qoder", "qoder", tmp_path),
        context=_context("qoder", tmp_path),
        now="2026-07-29T00:00:00Z",
    )

    assert submission.runtime_state["reason"] == "pane_unavailable"
    assert submission.runtime_state["mode"] == "error"
    assert backend.sent == []


def test_default_qoder_backends_register_visible_pane_adapters() -> None:
    registry = build_default_backend_registry(
        include_optional=True,
        include_test_doubles=False,
    )

    for provider in ("qoder", "qoderclicn"):
        backend = registry.get(provider)
        assert backend is not None
        assert isinstance(backend.execution_adapter, QoderPaneExecutionAdapter)
