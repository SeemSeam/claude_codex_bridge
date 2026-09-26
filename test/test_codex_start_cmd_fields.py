from __future__ import annotations

from provider_backends.codex.start_cmd_runtime.fields import (
    effective_start_cmd,
    persist_resume_start_cmd_fields,
    resume_template_command,
)


def test_effective_start_cmd_rebuilds_resume_from_base_start_command() -> None:
    data = {
        "start_cmd": "export CODEX_RUNTIME_DIR=/tmp/demo; codex -c disable_paste_burst=true",
        "codex_start_cmd": "codex resume stale-session",
        "codex_session_id": "fresh-session",
    }

    assert effective_start_cmd(data) == (
        "export CODEX_RUNTIME_DIR=/tmp/demo; "
        "codex -c disable_paste_burst=true resume fresh-session"
    )


def test_persist_resume_start_cmd_fields_updates_both_stored_fields() -> None:
    data = {
        "start_cmd": "export CODEX_RUNTIME_DIR=/tmp/demo; codex -c disable_paste_burst=true",
    }

    updated = persist_resume_start_cmd_fields(data, "resume-session")

    assert updated == (
        "export CODEX_RUNTIME_DIR=/tmp/demo; "
        "codex -c disable_paste_burst=true resume resume-session"
    )
    assert data["codex_start_cmd"] == updated
    assert data["start_cmd"] == updated


def test_resume_template_command_prefers_non_resume_base_command() -> None:
    data = {
        "start_cmd": "export CODEX_RUNTIME_DIR=/tmp/demo; codex -c disable_paste_burst=true",
        "codex_start_cmd": "codex resume stale-session",
    }

    assert resume_template_command(data) == "export CODEX_RUNTIME_DIR=/tmp/demo; codex -c disable_paste_burst=true"


def test_persist_repairs_existing_polluted_binding_without_changing_session() -> None:
    base = ('export CODEX_HOME=/tmp/managed; codex --ask-for-approval never '
            '--sandbox danger-full-access --dangerously-bypass-hook-trust')
    polluted = base + ' resume original-session' * 5
    data = {'start_cmd': polluted, 'codex_start_cmd': polluted,
            'codex_session_id': 'original-session', 'codex_session_path': '/tmp/history.jsonl'}
    persist_resume_start_cmd_fields(data, data['codex_session_id'])
    assert effective_start_cmd(data) == base + ' resume original-session'
    assert data['start_cmd'] == data['codex_start_cmd']
    assert data['codex_session_id'] == 'original-session'
    assert data['codex_session_path'] == '/tmp/history.jsonl'
