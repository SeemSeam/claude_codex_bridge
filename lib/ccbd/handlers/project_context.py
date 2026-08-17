from __future__ import annotations

import time


OPENCODE_CONTEXT_SUBMIT_DELAY_S = 0.3

# Keep this table explicit. A provider without a verified native compaction
# command must fail closed instead of receiving a normal prompt by accident.
# ``None`` is intentional: it means the provider is known to CCB but has no
# verified native compaction spelling in the installed/provider contract.
COMPACT_COMMANDS: dict[str, str | None] = {
    'codex': '/compact',
    'claude': '/compact',
    'gemini': '/compress',
    'opencode': '/compact',
    'droid': '/compress',
    'agy': '/compress',
    'kimi': '/compact',
    'deepseek': None,
    # Official DeepSeek Harness executes this through its structured Web API;
    # project_compact must never type it into the lifecycle/log pane.
    'dsh': '/compact',
    'mimo': '/compact',
    'qwen': '/compress',
    'qoder': None,
    'qoderclicn': None,
    'cursor': None,
    'copilot': '/compact',
    'crush': '/summarize',
    'grok': None,
    'kiro': None,
    'pi': '/compact',
    'omp': '/compact',
    'zai': None,
}


def send_context_command(backend, *, pane_id: str, command: str, provider: str = '') -> None:
    try:
        backend._ensure_not_in_copy_mode(pane_id)
    except Exception:
        pass
    backend._tmux_run(['send-keys', '-t', pane_id, 'C-u'], check=True, capture=True)
    backend._tmux_run(['send-keys', '-t', pane_id, '-l', command], check=True, capture=True)
    if provider == 'opencode':
        time.sleep(OPENCODE_CONTEXT_SUBMIT_DELAY_S)
    backend._tmux_run(['send-keys', '-t', pane_id, 'Enter'], check=True, capture=True)


__all__ = ['COMPACT_COMMANDS', 'OPENCODE_CONTEXT_SUBMIT_DELAY_S', 'send_context_command']
