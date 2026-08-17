from __future__ import annotations

import json
from pathlib import Path
import sys

from ccbd.api_models import JobRecord
from provider_backends.native_cli_support import (
    NativeCliExecutionConfig,
    NativeCliExecutionRequest,
    NativeCliObservation,
    NativeCliSubprocessAdapter,
    wrap_native_prompt,
)
from provider_execution.base import (
    ProviderPollResult,
    ProviderRuntimeContext,
    ProviderSubmission,
)
from storage.atomic import atomic_write_json


_MODE = 'dsh_web_rpc'
_REQUEST_SUFFIX = '.dsh-request.json'


class DshProviderAdapter:
    """CCB execution adapter for the official DeepSeek Harness Web API.

    The visible provider pane owns only the long-lived host process.  Job
    submission and completion are bound to DSH's durable RPC/session event
    protocol and never to pane input, pane text, quiet time, or process exit.
    """

    provider = 'dsh'
    restart_resume_supported = True

    def __init__(self) -> None:
        self._delegate = NativeCliSubprocessAdapter(_execution_config())

    def restore_diagnostics(self) -> dict[str, object]:
        return {
            'resume_supported': True,
            'restore_mode': 'exact_dsh_session_history',
            'restore_reason': 'dsh_native_history_resume',
            'restore_detail': (
                'CCB reconnects an observer to the exact persisted DSH session '
                'and RPC id; it never reposts the interrupted prompt'
            ),
        }

    def start(
        self,
        job: JobRecord,
        *,
        context: ProviderRuntimeContext | None,
        now: str,
    ) -> ProviderSubmission:
        return self._delegate.start(job, context=context, now=now)

    def poll(self, submission: ProviderSubmission, *, now: str) -> ProviderPollResult | None:
        return self._delegate.poll(submission, now=now)

    def cancel(self, submission: ProviderSubmission) -> None:
        self._delegate.cancel(submission)

    def export_runtime_state(self, submission: ProviderSubmission) -> dict[str, object]:
        return self._delegate.export_runtime_state(submission)

    def resume(
        self,
        job: JobRecord,
        submission: ProviderSubmission,
        *,
        context: ProviderRuntimeContext | None,
        persisted_state,
        now: str,
    ) -> ProviderSubmission | None:
        if not _resume_binding_is_current(job, submission):
            return None
        return self._delegate.resume(
            job,
            submission,
            context=context,
            persisted_state=persisted_state,
            now=now,
        )


def build_execution_adapter() -> DshProviderAdapter:
    return DshProviderAdapter()


def _execution_config() -> NativeCliExecutionConfig:
    return NativeCliExecutionConfig(
        provider='dsh',
        session_filename='.dsh-session',
        command_builder=_build_command,
        env_builder=_build_env,
        private_path_env_names=('DSH_HOME', 'DSH_AGENTS_HOME'),
        observer=observe_dsh_output,
        output_kind='jsonl',
        mode=_MODE,
        start_failed_reason='dsh_bridge_start_failed',
        failed_reason='dsh_bridge_failed',
        empty_reason='dsh_empty_completed_reply',
        run_error_reason='dsh_native_turn_failed',
        complete_reason='dsh_native_turn_completed',
        process_exit_complete_reason='dsh_process_exit_is_not_completion',
        missing_terminal_reason='dsh_native_turn_end_missing',
        timeout_reason='dsh_native_turn_timeout',
        invalid_protocol_reason='dsh_native_protocol_invalid',
        missing_outcome_reason='dsh_native_outcome_missing',
        terminal_on_process_exit=False,
        terminal_requires_process_exit=False,
        require_outcome_reason=True,
        require_native_anchor=True,
        exact_native_terminal=True,
        resume_command_builder=_build_resume_command,
    )


def _build_command(request: NativeCliExecutionRequest) -> list[str]:
    completion_dir = _completion_dir(request)
    request_path = completion_dir / f'{request.job.job_id}{_REQUEST_SUFFIX}'
    session_file_path = _required_session_text(request.session_data, 'dsh_session_file_path')
    payload = {
        'schema_version': 1,
        'record_type': 'dsh_bridge_request',
        'provider': 'dsh',
        'rpc_id': request.job.job_id,
        'session_id': _required_session_text(request.session_data, 'dsh_session_id'),
        'session_file_path': session_file_path,
        'endpoint_state_path': _required_session_text(
            request.session_data,
            'dsh_endpoint_state_path',
        ),
        'host_instance_id': _required_session_text(
            request.session_data,
            'dsh_host_instance_id',
        ),
        'work_dir': str(request.work_dir),
        # Even CCB no-wrap requests retain this small native correlation line.
        # DSH also persists the same job id independently as source.rpcId.
        'prompt': wrap_native_prompt(request.job.request.body or '', request.request_anchor),
        'model_provider': str(
            request.session_data.get('dsh_model_provider') or 'deepseek-official'
        ).strip(),
        'model': str(request.session_data.get('dsh_model') or '').strip(),
        'reasoning_effort': str(
            request.session_data.get('dsh_reasoning_effort') or ''
        ).strip(),
        'auto_permission': bool(
            request.session_data.get('dsh_auto_permission_enabled')
        ),
    }
    atomic_write_json(request_path, payload)
    return [
        sys.executable,
        '-m',
        'provider_backends.dsh.bridge',
        '--request',
        str(request_path),
    ]


def _build_resume_command(state: dict[str, object]) -> list[str]:
    request_path = _request_path_from_state(state)
    if not request_path.is_file():
        raise RuntimeError(f'dsh bridge request is missing: {request_path}')
    return [
        sys.executable,
        '-m',
        'provider_backends.dsh.bridge',
        '--request',
        str(request_path),
        '--observe-only',
    ]


def _build_env(request: NativeCliExecutionRequest) -> dict[str, str]:
    home = _required_session_text(request.session_data, 'dsh_home')
    agents_home = str(request.session_data.get('dsh_agents_home') or '').strip() or home
    return {
        'DSH_HOME': home,
        'DSH_AGENTS_HOME': agents_home,
        'PYTHONUNBUFFERED': '1',
    }


def observe_dsh_output(path: Path) -> NativeCliObservation:
    if not Path(path).is_file():
        return NativeCliObservation()
    last: dict[str, object] | None = None
    protocol_error = ''
    try:
        lines = Path(path).read_text(encoding='utf-8', errors='replace').splitlines()
    except OSError as exc:
        return NativeCliObservation(error=f'dsh_output_read_failed:{exc}')
    for line in lines:
        if not line.strip():
            continue
        try:
            value = json.loads(line)
        except json.JSONDecodeError:
            protocol_error = protocol_error or 'dsh_bridge_output_invalid_json'
            continue
        if not isinstance(value, dict):
            protocol_error = protocol_error or 'dsh_bridge_output_not_object'
            continue
        if value.get('type') == 'dsh/observation':
            last = value
    if last is None:
        return NativeCliObservation(protocol_error=protocol_error)

    rpc_id = str(last.get('rpc_id') or '').strip()
    session_id = str(last.get('session_id') or '').strip()
    turn = last.get('turn')
    if not rpc_id or not session_id:
        protocol_error = protocol_error or 'dsh_observation_identity_missing'
    if not isinstance(last.get('anchor_seen'), bool):
        protocol_error = protocol_error or 'dsh_observation_anchor_invalid'
    if not isinstance(last.get('finished'), bool):
        protocol_error = protocol_error or 'dsh_observation_terminal_invalid'
    reply_value = last.get('reply')
    if not isinstance(reply_value, str):
        protocol_error = protocol_error or 'dsh_observation_reply_invalid'
        reply_value = ''
    turn_ref = f'{session_id}:{turn}' if session_id and isinstance(turn, int) else None
    return NativeCliObservation(
        text=reply_value,
        anchor_seen=last.get('anchor_seen') is True,
        finished=last.get('finished') is True,
        finish_reason=str(last.get('finish_reason') or ''),
        turn_ref=turn_ref,
        completed_at=last.get('completed_at'),
        error=str(last.get('error') or ''),
        outcome_reason=str(last.get('outcome_reason') or ''),
        protocol_error=protocol_error or str(last.get('protocol_error') or ''),
    )


def _resume_binding_is_current(job: JobRecord, submission: ProviderSubmission) -> bool:
    state = dict(submission.runtime_state or {})
    try:
        request_path = _request_path_from_state(state)
        request = json.loads(request_path.read_text(encoding='utf-8'))
        if not isinstance(request, dict):
            return False
        if request.get('record_type') != 'dsh_bridge_request':
            return False
        if str(request.get('rpc_id') or '') != job.job_id:
            return False
        session_file = Path(str(request.get('session_file_path') or '')).expanduser()
        binding = json.loads(session_file.read_text(encoding='utf-8-sig'))
        if not isinstance(binding, dict) or str(binding.get('provider') or '') != 'dsh':
            return False
        return (
            str(binding.get('dsh_session_id') or '')
            == str(request.get('session_id') or '')
            and str(binding.get('dsh_endpoint_state_path') or '')
            == str(request.get('endpoint_state_path') or '')
            and str(binding.get('dsh_host_instance_id') or '')
            == str(request.get('host_instance_id') or '')
        )
    except Exception:
        return False


def _completion_dir(request: NativeCliExecutionRequest) -> Path:
    raw = str(request.session_data.get('completion_artifact_dir') or '').strip()
    if raw:
        directory = Path(raw).expanduser()
    else:
        runtime = str(request.session_data.get('runtime_dir') or '').strip()
        directory = (
            Path(runtime).expanduser() / 'completion'
            if runtime
            else request.work_dir / '.ccb' / 'runtime' / 'dsh' / 'completion'
        )
    directory.mkdir(parents=True, exist_ok=True)
    return directory


def _request_path_from_state(state: dict[str, object]) -> Path:
    stdout = Path(str(state.get('stdout_path') or '')).expanduser()
    job_id = str(state.get('job_id') or '').strip()
    if not stdout.name or not job_id:
        raise RuntimeError('dsh persisted execution state is incomplete')
    return stdout.parent / f'{job_id}{_REQUEST_SUFFIX}'


def _required_session_text(session: dict[str, object], key: str) -> str:
    value = str(session.get(key) or '').strip()
    if not value:
        raise RuntimeError(f'dsh session binding omitted {key}')
    return value


__all__ = [
    'DshProviderAdapter',
    'build_execution_adapter',
    'observe_dsh_output',
]
