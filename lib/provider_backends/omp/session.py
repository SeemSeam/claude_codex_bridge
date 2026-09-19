from __future__ import annotations

import json
import shlex
from pathlib import Path
from typing import ClassVar, Optional

from provider_backends.native_cli_support.session import (
    NativeCliProjectSession,
    compute_session_key,
    find_project_session_file as _find_project_session_file,
)
from provider_backends.pane_log_support.session import (
    build_session_binding_for_provider, load_project_session_for_provider, read_session_json,
)
from provider_backends.pi.session import validate_native_session_binding
from provider_core.contracts import ProviderSessionBinding
from project.identity import normalize_work_dir

OMP_RESTART_SESSION_MARKER = "__CCB_OMP_EXACT_SESSION_79AC210B__"


class OmpProjectSession(NativeCliProjectSession):
    provider_name: ClassVar[str] = "omp"

    @property
    def omp_session_id(self) -> str:
        return str(self.data.get("omp_session_id") or "")

    @property
    def omp_session_path(self) -> str:
        return str(self.data.get("omp_session_path") or "")

    @property
    def start_cmd(self) -> str:
        original = str(self.data.get("start_cmd") or "")
        template = str(self.data.get("omp_restart_start_cmd_template") or "")
        if self.data.get("omp_explicit_session_control") or not template:
            return original
        binding = resume_binding_for_launch(
            self.session_file, agent_name=str(self.data.get("agent_name") or ""),
            project_id=str(self.data.get("ccb_project_id") or ""),
            work_dir=Path(self.work_dir),
            session_dir=Path(str(self.data.get("omp_session_dir") or "")),
        )
        if self.data.get("omp_restore_enabled") is False:
            binding = {"omp_resume_status": "fresh_restore_disabled"}
        return render_restart_command(template, binding.get("omp_resume_session_path")) or original


def find_project_session_file(work_dir: Path, instance: Optional[str] = None) -> Optional[Path]:
    return _find_project_session_file(work_dir, provider="omp", session_filename=".omp-session", instance=instance)


def load_project_session(work_dir: Path, instance: Optional[str] = None):
    return load_project_session_for_provider(
        work_dir, session_filename=".omp-session", session_cls=OmpProjectSession, instance=instance,
    )


def build_session_binding() -> ProviderSessionBinding:
    return build_session_binding_for_provider(provider="omp", load_session=load_project_session)


def render_restart_command(template: str, path: object = None) -> str:
    if template.count(OMP_RESTART_SESSION_MARKER) != 1:
        return ""
    args = f"--resume {shlex.quote(str(path))}" if path else ""
    return template.replace(OMP_RESTART_SESSION_MARKER, args).strip()


def resume_binding_for_launch(
    session_file: Path, *, agent_name: str, project_id: str,
    work_dir: Path, session_dir: Path,
) -> dict[str, object]:
    def fresh(reason: str) -> dict[str, object]:
        return {"omp_resume_status": f"fresh_{reason}"}

    data = read_session_json(session_file)
    if not data:
        return fresh("no_binding")
    if data.get("active") is False or data.get("agent_name") != agent_name:
        return fresh("agent_mismatch")
    if data.get("ccb_project_id") != project_id:
        return fresh("project_mismatch")
    recorded_cwd = str(data.get("work_dir_norm") or data.get("work_dir") or "")
    if recorded_cwd != normalize_work_dir(work_dir):
        return fresh("work_dir_mismatch")
    native_id = str(data.get("omp_session_id") or "")
    native_path = str(data.get("omp_session_path") or "")
    source = "persisted_native_binding"
    # Only the current launch's sidecar is evidence; never choose transcripts by mtime.
    event_path = str(data.get("omp_completion_event_log") or "")
    if event_path:
        observed = False
        try:
            with Path(event_path).open("rb") as stream:
                stream.seek(0, 2)
                size = stream.tell()
                stream.seek(max(0, size - 8 * 1024 * 1024))
                if stream.tell():
                    stream.readline()
                lines = stream.read().splitlines()
        except OSError:
            return fresh("observation_unavailable")
        for line in reversed(lines):
            try:
                event = json.loads(line)
            except (ValueError, UnicodeError):
                continue
            if not isinstance(event, dict) or event.get("schema_version") != 1:
                continue
            if event.get("actor") != agent_name or event.get("launch_session_id") != data.get("ccb_session_id"):
                continue
            if not event.get("runtime_instance_id") or event.get("type") not in {"extension_ready", "native_session"}:
                continue
            native_id = str(event.get("omp_session_id") or "")
            native_path = str(event.get("omp_session_path") or "")
            source = "omp_extension_observation"
            observed = True
            break
        if not observed:
            return fresh("no_current_session_observation")
    if not native_id or not native_path or native_id.startswith("ccb-"):
        return fresh("no_observed_native_session")
    valid, reason = validate_native_session_binding(
        session_id=native_id, session_path=Path(native_path),
        work_dir=work_dir, session_dir=session_dir,
    )
    if not valid:
        return fresh(reason)
    return {
        "omp_resume_status": "exact_session_ready",
        "omp_resume_session_id": native_id,
        "omp_resume_session_path": native_path,
        "omp_resume_binding_source": source,
    }


__all__ = ["build_session_binding", "compute_session_key", "find_project_session_file", "load_project_session"]
