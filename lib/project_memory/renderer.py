from __future__ import annotations

from pathlib import Path

from .types import ProjectMemorySource

CCB_RUNTIME_COORDINATION_RULES = """## CCB Runtime Coordination Rules

- CCB `ask` is submit-only. Submit once. If the submission is accepted, end the current Agent turn immediately so CCB can deliver the child result in a later turn. Do not continue task work, wait, poll, or run `pend`/`watch`/`ping` in that turn. For an explicitly requested runtime diagnosis, use only the bounded diagnostic commands required by that task.
- Prefer `/ask <agent> <message>` when available. Shell fallback:

```bash
command ask "$TARGET" <<'EOF'
$MESSAGE
EOF
```

- For a user-requested conversation reset, run `command ccb clear` for all configured agents or `command ccb clear "$AGENT"` for named agents. This sends provider-native clear input without deleting `.ccb` state, workspaces, auth, sessions, logs, or project memory.
- During an active CCB ask task, use `ask --chain` only when the current task cannot finish without that exact child result; never add it merely to bypass a rejected plain ask. An accepted chain submission also ends the current Agent turn; CCB resumes the parent through a result-chain continuation. Use `ask --silence` only for independent no-result-needed work.
- Finish an inbound CCB task in its current turn. If the original caller is a registered CCB agent, CCB routes that turn's terminal result through the existing lineage; do not open a new `ask` to report completion to the original caller.
- Outside an Agent turn, a direct CLI submitter may read terminal results from control output such as `watch` or `trace`.
- During a CCB result-chain continuation, answer directly with the final result; do not use `ask`, `--chain`, or `--silence` to send that final result to the original caller.
- `--silence` is not an active-job correction channel. Use `ccb followup <active_job_id> --message "<correction>"` only when the target provider advertises exact active-turn support; only `injected` is success. For `rejected`, `too_late`, or `terminal`, cancel and resubmit the complete corrected task instead of queueing a correction as ordinary work.
- A `completed` CCB job means provider execution ended normally; it does not by itself prove business acceptance.
- For every inbound CCB task, answer directly and concisely. Include only relevant conclusions, blockers, risks, evidence, and next actions; omit raw logs, repeated context, and background unless the current request explicitly asks for them. Explicit output requirements in the current request override this default.
- `CCB_REPLY_MODE: compact` means distill aggressively and keep only details needed for the task. `CCB_REPLY_MODE: silent` means return the shortest useful terminal status and include details only for failures, blockers, or required next actions.
- CCB runtime interruption is the primary cancellation mechanism. If a task is interrupted or cancelled, stop immediately and reply `CANCELLED`. Do not poll cancellation files during ordinary work. For an ambiguous interruption or before an irreversible external side effect, the cooperative flag for `CCB_REQ_ID: <job>` is `<project_root>/.ccb/agents/<agent>/cancel_flags/<job>.cancel`.
"""


def render_memory_bundle(
    *,
    project_root: Path,
    agent_name: str,
    provider: str,
    sources: tuple[ProjectMemorySource, ...],
    workspace_path: Path | None = None,
) -> str:
    lines = [
        '# CCB Managed Agent Memory',
        '',
        '<!-- ccb-memory-bundle schema_version=1',
        'generated_by: ccb',
        'do_not_edit: true',
        f'agent: {agent_name}',
        f'provider: {provider}',
        f'project_root: {Path(project_root).expanduser().resolve()}',
    ]
    if workspace_path is not None:
        lines.append(f'workspace_path: {Path(workspace_path).expanduser().resolve()}')
    lines.extend(['-->', '', CCB_RUNTIME_COORDINATION_RULES.rstrip(), ''])

    for source in sources:
        if not source.exists and not source.warning:
            continue
        if not source.content.strip() and not source.warning:
            continue
        lines.extend(_render_source_section(source))

    return '\n'.join(lines).rstrip() + '\n'


def _render_source_section(source: ProjectMemorySource) -> list[str]:
    content = source.content.rstrip()
    lines = [
        f'## {source.title}',
        f'source: {source.path}',
    ]
    if source.warning:
        lines.append(f'warning: {source.warning}')
    lines.extend(['', content, ''])
    return lines


__all__ = ['render_memory_bundle']
