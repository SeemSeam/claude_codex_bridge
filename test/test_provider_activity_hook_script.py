from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path


def test_provider_activity_hook_writes_codex_active_snapshot(tmp_path: Path) -> None:
    project_root = Path(__file__).resolve().parents[1]
    runtime_dir = tmp_path / 'runtime'
    workspace = tmp_path / 'workspace'
    env = {
        **os.environ,
        'CCB_CALLER_ACTOR': 'agent2',
        'CCB_CALLER_RUNTIME_DIR': str(runtime_dir),
        'CCB_SESSION_ID': 'ccb-agent2-1',
        'TMUX_PANE': '%42',
    }
    payload = {
        'hook_event_name': 'UserPromptSubmit',
        'session_id': 'codex-session-1',
        'turn_id': 'turn-1',
        'prompt': 'do not store me',
    }

    proc = subprocess.run(
        [
            sys.executable,
            str(project_root / 'bin' / 'ccb-provider-activity-hook.py'),
            '--provider',
            'codex',
            '--project-id',
            'project-1',
            '--workspace',
            str(workspace),
        ],
        input=json.dumps(payload),
        text=True,
        capture_output=True,
        check=False,
        env=env,
    )

    assert proc.returncode == 0, proc.stderr
    activity = json.loads((runtime_dir / 'activity.json').read_text(encoding='utf-8'))
    assert activity['state'] == 'active'
    assert activity['event_name'] == 'UserPromptSubmit'
    assert activity['agent_name'] == 'agent2'
    assert activity['ccb_session_id'] == 'ccb-agent2-1'
    assert activity['pane_id'] == '%42'
    assert activity['provider_session_id'] == 'codex-session-1'
    assert activity['provider_turn_id'] == 'turn-1'
    assert 'prompt' not in activity


def test_provider_activity_hook_maps_claude_waiting_notification(tmp_path: Path) -> None:
    project_root = Path(__file__).resolve().parents[1]
    runtime_dir = tmp_path / 'runtime'
    payload = {
        'hook_event_name': 'Notification',
        'message': 'Waiting for permission approval',
    }

    proc = subprocess.run(
        [
            sys.executable,
            str(project_root / 'bin' / 'ccb-provider-activity-hook.py'),
            '--provider',
            'claude',
            '--project-id',
            'project-1',
            '--agent-name',
            'agent3',
            '--runtime-dir',
            str(runtime_dir),
        ],
        input=json.dumps(payload),
        text=True,
        capture_output=True,
        check=False,
    )

    assert proc.returncode == 0, proc.stderr
    activity = json.loads((runtime_dir / 'activity.json').read_text(encoding='utf-8'))
    assert activity['state'] == 'pending'
    assert activity['event_name'] == 'Notification'


def test_provider_activity_hook_exits_zero_without_writing_on_malformed_payload(tmp_path: Path) -> None:
    project_root = Path(__file__).resolve().parents[1]
    runtime_dir = tmp_path / 'runtime'

    proc = subprocess.run(
        [
            sys.executable,
            str(project_root / 'bin' / 'ccb-provider-activity-hook.py'),
            '--provider',
            'codex',
            '--project-id',
            'project-1',
            '--agent-name',
            'agent2',
            '--runtime-dir',
            str(runtime_dir),
        ],
        input='{not-json',
        text=True,
        capture_output=True,
        check=False,
    )

    assert proc.returncode == 0, proc.stderr
    assert not (runtime_dir / 'activity.json').exists()


def test_provider_activity_hook_maps_error_payload_to_failed_without_secret(tmp_path: Path) -> None:
    project_root = Path(__file__).resolve().parents[1]
    runtime_dir = tmp_path / 'runtime'
    payload = {
        'hook_event_name': 'Stop',
        'error': {
            'type': 'provider_api_error',
            'code': 'model_not_found',
            'message': 'model unavailable',
            'api_key': 'must-not-leak',
        },
    }

    proc = subprocess.run(
        [
            sys.executable,
            str(project_root / 'bin' / 'ccb-provider-activity-hook.py'),
            '--provider',
            'codex',
            '--project-id',
            'project-1',
            '--agent-name',
            'agent2',
            '--runtime-dir',
            str(runtime_dir),
        ],
        input=json.dumps(payload),
        text=True,
        capture_output=True,
        check=False,
    )

    assert proc.returncode == 0, proc.stderr
    activity = json.loads((runtime_dir / 'activity.json').read_text(encoding='utf-8'))
    assert activity['state'] == 'failed'
    assert activity['diagnostics']['error_type'] == 'provider_api_error'
    assert activity['diagnostics']['error_code'] == 'model_not_found'
    assert 'api_key' not in activity['diagnostics']
