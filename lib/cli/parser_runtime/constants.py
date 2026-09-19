from __future__ import annotations

SUBCOMMANDS = {
    'ask',
    'cancel',
    'followup',
    'clear',
    'compact',
    'cleanup',
    'kill',
    'ps',
    'ping',
    'watch',
    'pend',
    'queue',
    'relay',
    'trace',
    'resubmit',
    'retry',
    'wait-any',
    'wait-all',
    'wait-quorum',
    'inbox',
    'ack',
    'agent',
    'logs',
    'screen',
    'layout',
    'loop',
    'plan',
    'question',
    'maintenance',
    'mobile',
    'doctor',
    'repair',
    'config',
    'fault',
    'frontdesk',
    'reload',
    'restart',
    'herdr',
}

ASK_OPTIONS_WITH_VALUES = {'--task-id', '--reply-to', '--mode'}
ASK_FLAG_OPTIONS = {
    '--artifact-io',
    '--artifact-reply',
    '--artifact-request',
    '--chain',
    '--compact',
    '--inline-request',
    '--silence',
}
WAIT_COMMAND_TO_MODE = {
    'wait-any': 'any',
    'wait-all': 'all',
    'wait-quorum': 'quorum',
}
ASK_JOB_ACTIONS = {'get', 'cancel'}


__all__ = [
    'ASK_FLAG_OPTIONS',
    'ASK_JOB_ACTIONS',
    'ASK_OPTIONS_WITH_VALUES',
    'SUBCOMMANDS',
    'WAIT_COMMAND_TO_MODE',
]
