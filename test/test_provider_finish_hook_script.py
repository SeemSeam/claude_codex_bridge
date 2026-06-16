from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path


def test_provider_finish_hook_writes_claude_completion_event(tmp_path: Path) -> None:
    project_root = Path(__file__).resolve().parents[1]
    completion_dir = tmp_path / "completion"
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    transcript = tmp_path / "transcript.jsonl"
    transcript.write_text(
        '{"type":"user","message":{"content":"CCB_REQ_ID: 20260331-130805-796-1333224-9"}}\n',
        encoding="utf-8",
    )
    payload = {
        "hook_event_name": "Stop",
        "transcript_path": str(transcript),
        "last_assistant_message": "A3_FIX_13_OK",
        "session_id": "claude-session-1",
    }

    proc = subprocess.run(
        [
            sys.executable,
            str(project_root / "bin" / "ccb-provider-finish-hook.py"),
            "--provider",
            "claude",
            "--completion-dir",
            str(completion_dir),
            "--agent-name",
            "agent3",
            "--workspace",
            str(workspace),
        ],
        input=json.dumps(payload, ensure_ascii=False),
        text=True,
        capture_output=True,
        check=False,
    )

    assert proc.returncode == 0, proc.stderr
    event_path = completion_dir / "events" / "20260331-130805-796-1333224-9.json"
    assert event_path.exists()
    event = json.loads(event_path.read_text(encoding="utf-8"))
    assert event["provider"] == "claude"
    assert event["agent_name"] == "agent3"
    assert event["reply"] == "A3_FIX_13_OK"
    assert event["status"] == "completed"


def test_provider_finish_hook_marks_empty_claude_reply_incomplete(tmp_path: Path) -> None:
    project_root = Path(__file__).resolve().parents[1]
    completion_dir = tmp_path / "completion"
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    transcript = tmp_path / "transcript.jsonl"
    req_id = "job_emptyclaude123"
    transcript.write_text(
        "\n".join(
            json.dumps(record)
            for record in (
                {
                    "uuid": "old-user",
                    "type": "user",
                    "message": {
                        "role": "user",
                        "content": "CCB_REQ_ID: job_previous111\n\nPrevious task.",
                    },
                },
                {
                    "uuid": "old-assistant",
                    "parentUuid": "old-user",
                    "type": "assistant",
                    "message": {
                        "role": "assistant",
                        "content": [{"type": "text", "text": "previous done"}],
                    },
                },
                {
                    "uuid": "current-user",
                    "type": "user",
                    "message": {
                        "role": "user",
                        "content": f"CCB_REQ_ID: {req_id}\n\nRun the task.",
                    },
                },
            )
        )
        + "\n",
        encoding="utf-8",
    )
    payload = {
        "hook_event_name": "Stop",
        "transcript_path": str(transcript),
        "last_assistant_message": "",
        "session_id": "claude-session-1",
    }

    proc = subprocess.run(
        [
            sys.executable,
            str(project_root / "bin" / "ccb-provider-finish-hook.py"),
            "--provider",
            "claude",
            "--completion-dir",
            str(completion_dir),
            "--agent-name",
            "agent3",
            "--workspace",
            str(workspace),
        ],
        input=json.dumps(payload, ensure_ascii=False),
        text=True,
        capture_output=True,
        check=False,
    )

    assert proc.returncode == 0, proc.stderr
    event_path = completion_dir / "events" / f"{req_id}.json"
    assert event_path.exists()
    event = json.loads(event_path.read_text(encoding="utf-8"))
    assert event["provider"] == "claude"
    assert event["reply"] == ""
    assert event["status"] == "incomplete"
    assert event["diagnostics"]["reason"] == "hook_stop_empty_reply"
    assert event["diagnostics"]["empty_reply"] is True
    assert event["diagnostics"]["error_type"] == "empty_provider_reply"
    assert "without assistant reply text" in event["diagnostics"]["diagnosis"]


def test_provider_finish_hook_uses_outer_claude_req_id_when_body_mentions_old_req_id(tmp_path: Path) -> None:
    project_root = Path(__file__).resolve().parents[1]
    completion_dir = tmp_path / "completion"
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    transcript = tmp_path / "transcript.jsonl"
    current_req_id = "job_current123abc"
    embedded_old_req_id = "job_old456def"
    transcript.write_text(
        json.dumps(
            {
                "type": "user",
                "message": {
                    "role": "user",
                    "content": (
                        f"CCB_REQ_ID: {current_req_id}\n\n"
                        f"CCB_REQ_ID: {embedded_old_req_id}\n\n"
                        "Forwarded review context that contains an older request id."
                    ),
                },
            }
        )
        + "\n",
        encoding="utf-8",
    )
    payload = {
        "hook_event_name": "Stop",
        "transcript_path": str(transcript),
        "last_assistant_message": "review completed",
        "session_id": "claude-session-1",
    }

    proc = subprocess.run(
        [
            sys.executable,
            str(project_root / "bin" / "ccb-provider-finish-hook.py"),
            "--provider",
            "claude",
            "--completion-dir",
            str(completion_dir),
            "--agent-name",
            "agent3",
            "--workspace",
            str(workspace),
        ],
        input=json.dumps(payload, ensure_ascii=False),
        text=True,
        capture_output=True,
        check=False,
    )

    assert proc.returncode == 0, proc.stderr
    event_path = completion_dir / "events" / f"{current_req_id}.json"
    old_event_path = completion_dir / "events" / f"{embedded_old_req_id}.json"
    assert event_path.exists()
    assert not old_event_path.exists()
    event = json.loads(event_path.read_text(encoding="utf-8"))
    assert event["req_id"] == current_req_id
    assert event["reply"] == "review completed"
    assert event["status"] == "completed"


def test_provider_finish_hook_ignores_later_claude_tool_result_req_id(tmp_path: Path) -> None:
    project_root = Path(__file__).resolve().parents[1]
    completion_dir = tmp_path / "completion"
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    transcript = tmp_path / "transcript.jsonl"
    current_req_id = "job_currentabc123"
    tool_result_req_id = "job_toolresult999"
    transcript.write_text(
        json.dumps(
            {
                "type": "user",
                "message": {
                    "role": "user",
                    "content": f"CCB_REQ_ID: {current_req_id}\n\nReview this package.",
                },
            }
        )
        + "\n"
        + json.dumps(
            {
                "type": "user",
                "message": {
                    "role": "user",
                    "content": [
                        {
                            "type": "tool_result",
                            "tool_use_id": "tooluse_1",
                            "content": f"Command output mentioned CCB_REQ_ID: {tool_result_req_id}",
                            "is_error": False,
                        }
                    ],
                },
            }
        )
        + "\n",
        encoding="utf-8",
    )
    payload = {
        "hook_event_name": "Stop",
        "transcript_path": str(transcript),
        "last_assistant_message": "done after tools",
        "session_id": "claude-session-1",
    }

    proc = subprocess.run(
        [
            sys.executable,
            str(project_root / "bin" / "ccb-provider-finish-hook.py"),
            "--provider",
            "claude",
            "--completion-dir",
            str(completion_dir),
            "--agent-name",
            "agent3",
            "--workspace",
            str(workspace),
        ],
        input=json.dumps(payload, ensure_ascii=False),
        text=True,
        capture_output=True,
        check=False,
    )

    assert proc.returncode == 0, proc.stderr
    event_path = completion_dir / "events" / f"{current_req_id}.json"
    tool_result_event_path = completion_dir / "events" / f"{tool_result_req_id}.json"
    assert event_path.exists()
    assert not tool_result_event_path.exists()
    event = json.loads(event_path.read_text(encoding="utf-8"))
    assert event["req_id"] == current_req_id
    assert event["reply"] == "done after tools"
    assert event["status"] == "completed"


def test_provider_finish_hook_ignores_claude_scheduled_task_after_stale_ccb_prompt(tmp_path: Path) -> None:
    project_root = Path(__file__).resolve().parents[1]
    completion_dir = tmp_path / "completion"
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    transcript = tmp_path / "transcript.jsonl"
    stale_req_id = "job_stale123abc"
    scheduled_reply = "当前进度：已完成第9次，正在执行第10次。"
    transcript.write_text(
        "\n".join(
            json.dumps(record, ensure_ascii=False)
            for record in (
                {
                    "uuid": "u1",
                    "type": "user",
                    "message": {
                        "role": "user",
                        "content": f"CCB_REQ_ID: {stale_req_id}\n\nRun a long task.",
                    },
                },
                {
                    "uuid": "u2",
                    "parentUuid": "u1",
                    "type": "user",
                    "message": {
                        "role": "user",
                        "content": [{"type": "text", "text": "[Request interrupted by user]"}],
                    },
                },
                {
                    "uuid": "s1",
                    "parentUuid": "u2",
                    "type": "system",
                    "subtype": "scheduled_task_fire",
                    "content": "Running scheduled task",
                },
                {
                    "uuid": "u3",
                    "parentUuid": "s1",
                    "type": "user",
                    "message": {
                        "role": "user",
                        "content": "循环计数，共50次",
                    },
                    "isMeta": True,
                },
                {
                    "uuid": "a1",
                    "parentUuid": "u3",
                    "type": "assistant",
                    "message": {
                        "role": "assistant",
                        "content": [{"type": "text", "text": scheduled_reply}],
                    },
                },
                {
                    "type": "last-prompt",
                    "lastPrompt": f"CCB_REQ_ID: {stale_req_id}\n\nRun a long task.",
                },
            )
        )
        + "\n",
        encoding="utf-8",
    )
    payload = {
        "hook_event_name": "Stop",
        "transcript_path": str(transcript),
        "last_assistant_message": scheduled_reply,
        "session_id": "claude-session-1",
    }

    proc = subprocess.run(
        [
            sys.executable,
            str(project_root / "bin" / "ccb-provider-finish-hook.py"),
            "--provider",
            "claude",
            "--completion-dir",
            str(completion_dir),
            "--agent-name",
            "agent3",
            "--workspace",
            str(workspace),
        ],
        input=json.dumps(payload, ensure_ascii=False),
        text=True,
        capture_output=True,
        check=False,
    )

    assert proc.returncode == 0, proc.stderr
    assert not (completion_dir / "events" / f"{stale_req_id}.json").exists()


def test_provider_finish_hook_writes_gemini_failed_event_for_login_required_response(tmp_path: Path) -> None:
    project_root = Path(__file__).resolve().parents[1]
    completion_dir = tmp_path / "completion"
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    req_id = "20260331-130805-796-1333224-10"
    payload = {
        "hook_event_name": "AfterAgent",
        "prompt": f"CCB_REQ_ID: {req_id} Execute the full request from @/tmp/request.md and reply directly.",
        "prompt_response": (
            "Code Assist login required.\n"
            "Attempting to open authentication page in your browser.\n"
            "Otherwise navigate to:\nhttps://accounts.google.com/o/oauth2/v2/auth?... \n"
        ),
        "session_id": "gemini-session-1",
        "finishReason": "STOP",
    }

    proc = subprocess.run(
        [
            sys.executable,
            str(project_root / "bin" / "ccb-provider-finish-hook.py"),
            "--provider",
            "gemini",
            "--completion-dir",
            str(completion_dir),
            "--agent-name",
            "agent3",
            "--workspace",
            str(workspace),
        ],
        input=json.dumps(payload, ensure_ascii=False),
        text=True,
        capture_output=True,
        check=False,
    )

    assert proc.returncode == 0, proc.stderr
    event_path = completion_dir / "events" / f"{req_id}.json"
    assert event_path.exists()
    event = json.loads(event_path.read_text(encoding="utf-8"))
    assert event["provider"] == "gemini"
    assert event["agent_name"] == "agent3"
    assert event["status"] == "failed"
    assert event["reply"].startswith("Code Assist login required.")
    assert event["diagnostics"]["hook_event_name"] == "AfterAgent"
    assert event["diagnostics"]["finish_reason"] == "STOP"
    assert event["diagnostics"]["error_type"] == "provider_api_error"
    assert event["diagnostics"]["error_code"] == "LoginRequired"
    assert "login required" in event["diagnostics"]["error_message"].lower()


def test_provider_finish_hook_accepts_job_id_anchor_from_prompt(tmp_path: Path) -> None:
    project_root = Path(__file__).resolve().parents[1]
    completion_dir = tmp_path / "completion"
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    req_id = "job_06188b28c1db"
    payload = {
        "hook_event_name": "AfterAgent",
        "prompt": f"CCB_REQ_ID: {req_id} Execute the full request from @/tmp/request.md and reply directly.",
        "prompt_response": "job-based reply",
        "session_id": "gemini-session-1",
        "finishReason": "STOP",
    }

    proc = subprocess.run(
        [
            sys.executable,
            str(project_root / "bin" / "ccb-provider-finish-hook.py"),
            "--provider",
            "gemini",
            "--completion-dir",
            str(completion_dir),
            "--agent-name",
            "agent2",
            "--workspace",
            str(workspace),
        ],
        input=json.dumps(payload, ensure_ascii=False),
        text=True,
        capture_output=True,
        check=False,
    )

    assert proc.returncode == 0, proc.stderr
    event_path = completion_dir / "events" / f"{req_id}.json"
    assert event_path.exists()
    event = json.loads(event_path.read_text(encoding="utf-8"))
    assert event["req_id"] == req_id
    assert event["reply"] == "job-based reply"


def test_provider_finish_hook_marks_empty_gemini_reply_incomplete(tmp_path: Path) -> None:
    project_root = Path(__file__).resolve().parents[1]
    completion_dir = tmp_path / "completion"
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    req_id = "job_7c1f6ab28cde"
    payload = {
        "hook_event_name": "AfterAgent",
        "prompt": f"CCB_REQ_ID: {req_id} Execute the full request from @/tmp/request.md and reply directly.",
        "prompt_response": "",
        "session_id": "gemini-session-1",
        "finishReason": "STOP",
    }

    proc = subprocess.run(
        [
            sys.executable,
            str(project_root / "bin" / "ccb-provider-finish-hook.py"),
            "--provider",
            "gemini",
            "--completion-dir",
            str(completion_dir),
            "--agent-name",
            "agent2",
            "--workspace",
            str(workspace),
        ],
        input=json.dumps(payload, ensure_ascii=False),
        text=True,
        capture_output=True,
        check=False,
    )

    assert proc.returncode == 0, proc.stderr
    event_path = completion_dir / "events" / f"{req_id}.json"
    assert event_path.exists()
    event = json.loads(event_path.read_text(encoding="utf-8"))
    assert event["req_id"] == req_id
    assert event["reply"] == ""
    assert event["status"] == "incomplete"
    assert event["diagnostics"]["reason"] == "hook_after_agent_incomplete"
    assert event["diagnostics"]["empty_reply"] is True
