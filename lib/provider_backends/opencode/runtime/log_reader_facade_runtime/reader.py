from __future__ import annotations

from .cancel import OpenCodeCancelMixin
from .state import initialize_reader
from .storage import OpenCodeStorageMixin
from .timeline import OpenCodeTimelineMixin


class OpenCodeLogReader(OpenCodeStorageMixin, OpenCodeTimelineMixin, OpenCodeCancelMixin):
    def __init__(
        self,
        root=None,
        work_dir=None,
        project_id: str = "global",
        *,
        session_id_filter: str | None = None,
        api_url: str | None = None,
    ):
        self._native = None
        if api_url:
            from opencode_runtime.native import OpenCodeNativeState, NativeStateError
            if not session_id_filter or not work_dir:
                raise NativeStateError('OpenCode native observation requires exact session and workspace binding')
            self._native = OpenCodeNativeState(api_url, str(work_dir))
        initialize_reader(
            self,
            root=root,
            work_dir=work_dir,
            project_id=project_id,
            session_id_filter=session_id_filter,
        )


__all__ = ['OpenCodeLogReader']
