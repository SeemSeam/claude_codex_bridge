"""Opt-in remote workspace transactions. No provider credentials are transported."""

import sys


class LocalWorkspaceSynchronizer:
    """Keep ordinary dispatch independent of the optional Linux transport."""

    def before_dispatch(self, job, context):
        return job

    def bind_context(self, job, context):
        return context

    def for_resume(self, job, context):
        return job

    def before_complete(self, job, decision):
        return decision


def create_workspace_synchronizer(layout, config):
    remote_specs = [
        spec for spec in config.agents.values() if getattr(spec, 'remote_workspace', None)
    ]
    if not remote_specs:
        return LocalWorkspaceSynchronizer()
    if sys.platform != 'linux':
        raise ValueError('remote_workspace currently requires a Linux controller (including WSL2)')
    for spec in remote_specs:
        mode = getattr(spec.runtime_mode, 'value', spec.runtime_mode)
        if spec.provider != 'claude' or mode != 'pane-backed':
            raise ValueError('remote_workspace currently supports pane-backed Claude workers only')
        if not spec.provider_command_template:
            raise ValueError(
                'remote_workspace requires an approved remote provider_command_template'
            )
    # Do not import fcntl, /proc-dependent provider code, or O_NOFOLLOW-based
    # file handling for a local-only project on Windows or macOS.
    from .service import WorkspaceSynchronizer

    return WorkspaceSynchronizer(layout, config)
