from __future__ import annotations

from cli.services.doctor_runtime import installation_summary, runtime_identity_summary


def identity_summary(context) -> dict[str, object]:
    installation = installation_summary()
    return runtime_identity_summary(
        context.project.project_root,
        ccb_dir=context.paths.ccb_dir,
        installation=installation,
    )


__all__ = ['identity_summary']
