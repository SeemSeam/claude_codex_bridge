from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ParsedStartCommand:
    project: str | None
    agent_names: tuple[str, ...]
    restore: bool
    auto_permission: bool
    reset_context: bool = False
    kind: str = 'start'


@dataclass(frozen=True)
class ParsedKillCommand:
    project: str | None
    force: bool = False
    kind: str = 'kill'


@dataclass(frozen=True)
class ParsedClearCommand:
    project: str | None
    agent_names: tuple[str, ...] = ()
    kind: str = 'clear'


@dataclass(frozen=True)
class ParsedCompactCommand:
    project: str | None
    agent_names: tuple[str, ...] = ()
    kind: str = 'compact'


@dataclass(frozen=True)
class ParsedRestartCommand:
    project: str | None
    agent_name: str
    kind: str = 'restart'


@dataclass(frozen=True)
class ParsedMaintenanceCommand:
    project: str | None
    action: str = 'status'
    args: tuple[str, ...] = ()
    kind: str = 'maintenance'


@dataclass(frozen=True)
class ParsedMobileCommand:
    project: str | None
    action: str
    listen: str = '127.0.0.1:8787'
    public_url: str | None = None
    route_provider: str = 'lan'
    device_id: str | None = None
    kind: str = 'mobile'


@dataclass(frozen=True)
class ParsedRelayCommand:
    project: str | None
    target: str
    action: str
    invite_id: str | None = None
    host_id: str | None = None
    db_path: str | None = None
    secrets_path: str | None = None
    ttl_seconds: int = 900
    label: str | None = None
    reason: str | None = None
    max_sessions: int = 4
    max_bytes_per_day: int = 1073741824
    relay_mode: str | None = None
    relay_origin: str | None = None
    invitation: str | None = None
    invitation_file: str | None = None
    credential_path: str | None = None
    json_output: bool = False
    kind: str = 'relay'


@dataclass(frozen=True)
class ParsedAgentCommand:
    project: str | None
    action: str
    agent_name: str | None = None
    agent_names: tuple[str, ...] = ()
    provider: str | None = None
    profile: str | None = None
    role: str | None = None
    model: str | None = None
    thinking: str | None = None
    workspace_mode: str | None = None
    window_name: str | None = None
    window_class: str | None = None
    loop_id: str | None = None
    node_id: str | None = None
    lifetime: str | None = None
    visibility: str | None = None
    role_class: str | None = None
    policy: str | None = None
    idle_only: bool = False
    summary_policy: str | None = None
    force: bool = False
    reason: str | None = None
    json_output: bool = False
    kind: str = 'agent'


@dataclass(frozen=True)
class ParsedLayoutCommand:
    project: str | None
    action: str
    agent_name: str | None = None
    panes: int = 0
    window_prefix: str = 'layout'
    window_name: str | None = None
    window_class: str | None = None
    loop_id: str | None = None
    node_id: str | None = None
    timeout_s: float = 5.0
    session_name: str | None = None
    cleanup: bool = True
    json_output: bool = False
    kind: str = 'layout'


@dataclass(frozen=True)
class ParsedLoopCapacityCommand:
    project: str | None
    action: str
    loop_id: str
    profile_counts: tuple[tuple[str, int], ...] = ()
    policy: str = 'auto'
    idle_only: bool = False
    json_output: bool = False
    kind: str = 'loop-capacity'


@dataclass(frozen=True)
class ParsedLoopTopologyCommand:
    project: str | None
    action: str
    loop_id: str
    from_path: str | None = None
    proposal_id: str | None = None
    apply: bool = False
    policy: str = 'auto'
    idle_only: bool = False
    json_output: bool = False
    kind: str = 'loop-topology'


@dataclass(frozen=True)
class ParsedLoopRunOnceCommand:
    project: str | None
    loop_id: str | None = None
    task: str | None = None
    task_id: str | None = None
    worker_profile: str = 'worker'
    reviewer_profile: str = 'code_reviewer'
    orchestrator: str = 'orchestrator'
    round_checker: str = 'round_checker'
    timeout_s: float | None = None
    json_output: bool = False
    kind: str = 'loop-run-once'


@dataclass(frozen=True)
class ParsedLoopRunnerCommand:
    project: str | None
    once: bool = True
    auto: bool = False
    task_id: str | None = None
    plan_slug: str | None = None
    role_job_id: str | None = None
    wait_job_id: str | None = None
    timeout_s: float | None = None
    max_steps: int = 24
    poll_interval_s: float = 2.0
    consume_role_output: bool = False
    json_output: bool = False
    kind: str = 'loop-runner'


@dataclass(frozen=True)
class ParsedPlanTaskCommand:
    project: str | None
    action: str
    plan_slug: str | None = None
    title: str | None = None
    task_id: str | None = None
    artifact_kind: str | None = None
    file_path: str | None = None
    route: str | None = None
    status: str | None = None
    next_owner: str | None = None
    activation_reason: str | None = None
    loop_id: str | None = None
    result: str | None = None
    json_output: bool = False
    kind: str = 'plan-task'


@dataclass(frozen=True)
class ParsedFrontdeskCommand:
    project: str | None
    action: str
    plan_slug: str | None = None
    request_id: str | None = None
    source_job_id: str | None = None
    file_path: str | None = None
    intake_base64: str | None = None
    intake_text: str = ''
    json_output: bool = False
    kind: str = 'frontdesk'


@dataclass(frozen=True)
class ParsedQuestionCommand:
    project: str | None
    action: str
    task_id: str | None = None
    file_path: str | None = None
    json_output: bool = False
    kind: str = 'question'


@dataclass(frozen=True)
class ParsedCleanupCommand:
    project: str | None
    legacy_provider_caches: bool = False
    kind: str = 'cleanup'


@dataclass(frozen=True)
class ParsedPsCommand:
    project: str | None
    alive_only: bool = False
    kind: str = 'ps'


@dataclass(frozen=True)
class ParsedConfigValidateCommand:
    project: str | None
    action: str = 'validate'
    json_output: bool = False
    to_version: int | None = None
    dry_run: bool = False
    kind: str = 'config-validate'


@dataclass(frozen=True)
class ParsedConfigUiCommand:
    project: str | None
    no_open: bool = False
    port: int | None = None
    kind: str = 'config-ui'


@dataclass(frozen=True)
class ParsedConfigImportHerdrCommand:
    project: str | None
    output_path: str | None = None
    dry_run: bool = True
    force: bool = False
    kind: str = 'config-import-herdr'


@dataclass(frozen=True)
class ParsedHerdrOpenCommand:
    project: str | None
    herdr_exe: str | None = None
    herdr_session: str | None = None
    no_attach: bool = False
    wait_ready: bool = False
    kind: str = 'herdr-open'


@dataclass(frozen=True)
class ParsedReloadCommand:
    project: str | None
    dry_run: bool = False
    kind: str = 'reload'


@dataclass(frozen=True)
class ParsedDoctorCommand:
    project: str | None
    bundle: bool = False
    output_path: str | None = None
    storage: bool = False
    json_output: bool = False
    kind: str = 'doctor'


@dataclass(frozen=True)
class ParsedScreenCommand:
    project: str | None
    agent_name: str
    lines: int = 0
    json_output: bool = False
    kind: str = 'screen'


@dataclass(frozen=True)
class ParsedLogsCommand:
    project: str | None
    agent_name: str
    kind: str = 'logs'


@dataclass(frozen=True)
class ParsedPingCommand:
    project: str | None
    target: str
    kind: str = 'ping'


__all__ = [
    'ParsedAgentCommand',
    'ParsedClearCommand',
    'ParsedCompactCommand',
    'ParsedCleanupCommand',
    'ParsedConfigUiCommand',
    'ParsedConfigValidateCommand',
    'ParsedDoctorCommand',
    'ParsedFrontdeskCommand',
    'ParsedHerdrOpenCommand',
    'ParsedKillCommand',
    'ParsedLayoutCommand',
    'ParsedLogsCommand',
    'ParsedScreenCommand',
    'ParsedLoopCapacityCommand',
    'ParsedLoopTopologyCommand',
    'ParsedLoopRunOnceCommand',
    'ParsedLoopRunnerCommand',
    'ParsedMaintenanceCommand',
    'ParsedMobileCommand',
    'ParsedPlanTaskCommand',
    'ParsedPingCommand',
    'ParsedPsCommand',
    'ParsedQuestionCommand',
    'ParsedRelayCommand',
    'ParsedReloadCommand',
    'ParsedRestartCommand',
    'ParsedStartCommand',
]
