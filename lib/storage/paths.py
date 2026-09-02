from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import json
import os
from pathlib import Path

from project.ids import compute_project_id, project_slug
from runtime_env.source_home import current_provider_source_home

from .atomic import atomic_write_json
from .path_helpers import (
    RuntimeStatePlacement,
    SocketPlacement,
    choose_runtime_state_placement,
    choose_socket_placement,
    normalized_segment,
    read_runtime_root_marker_payload,
    read_runtime_root_ref_payload,
    runtime_root_marker_path,
    runtime_root_ref_path,
    runtime_state_placement_payload,
)
from .paths_agents import (
    AgentMailboxPathMixin,
    AgentRuntimePathMixin,
    WorkspacePathMixin,
)
from .paths_ccbd import (
    CcbdArtifactsPathMixin,
    CcbdMailboxPathMixin,
    CcbdMountPathMixin,
    CcbdOpsPathMixin,
    ProjectAnchorPathMixin,
)
from .paths_targets import TargetPathMixin

_SHARED_CACHE_PROVIDERS = frozenset({'codex'})
_EXTERNAL_CACHE_PROVIDERS = frozenset({'claude', 'gemini'})
_USER_PROVIDER_CACHE_PROVIDERS = frozenset({'gemini', 'pi'})


@dataclass(frozen=True)
class PathLayout(
    ProjectAnchorPathMixin,
    CcbdMailboxPathMixin,
    CcbdMountPathMixin,
    CcbdOpsPathMixin,
    CcbdArtifactsPathMixin,
    AgentRuntimePathMixin,
    AgentMailboxPathMixin,
    WorkspacePathMixin,
    TargetPathMixin,
):
    project_root: Path

    def __post_init__(self) -> None:
        root = Path(self.project_root).expanduser()
        try:
            root = root.resolve()
        except Exception:
            root = root.absolute()
        object.__setattr__(self, 'project_root', root)
        project_id = compute_project_id(root)
        object.__setattr__(self, '_project_id', project_id)
        placement = choose_runtime_state_placement(
            project_root=root,
            project_id=project_id,
            anchor_path=root / '.ccb',
        )
        object.__setattr__(self, '_runtime_state_placement', placement)
        object.__setattr__(self, '_state_root', placement.effective_path)

    @property
    def project_slug(self) -> str:
        return project_slug(self.project_root)

    @property
    def project_id(self) -> str:
        return self._project_id

    @property
    def project_socket_key(self) -> str:
        return self.project_id[:12]

    @property
    def runtime_state_placement(self) -> RuntimeStatePlacement:
        return self._runtime_state_placement

    @property
    def runtime_state_root(self) -> Path:
        return self._state_root

    @property
    def shared_cache_dir(self) -> Path:
        return self.runtime_state_root / 'shared-cache'

    @property
    def project_memory_path(self) -> Path:
        return self.ccb_dir / 'ccb_memory.md'

    @property
    def memory_seed_path(self) -> Path:
        return self.runtime_state_root / 'state' / 'memory.seed.json'

    @property
    def runtime_memory_dir(self) -> Path:
        return self.runtime_state_root / 'runtime' / 'memory'

    def runtime_memory_bundle_path(self, agent_name: str) -> Path:
        from agents.models import normalize_agent_name

        return self.runtime_memory_dir / f'{normalize_agent_name(agent_name)}.md'

    def provider_shared_cache_dir(self, provider: str) -> Path:
        normalized = normalized_segment(provider, label='provider')
        if normalized != str(provider or '').strip().lower() or normalized not in _SHARED_CACHE_PROVIDERS:
            supported = ', '.join(sorted(_SHARED_CACHE_PROVIDERS))
            raise ValueError(f'provider must be one of: {supported}')
        return self.shared_cache_dir / normalized

    def ensure_provider_shared_cache_dir(self, provider: str, *, created_at: str | None = None) -> Path:
        placement = self.runtime_state_placement
        if placement.filesystem_hint == 'wsl_drvfs' and placement.root_kind != 'relocated':
            raise RuntimeError('shared cache requires relocated runtime state for WSL drvfs project anchors')
        cache_dir = self.provider_shared_cache_dir(provider)
        timestamp = created_at or _utc_now()
        self.ensure_runtime_state_root(created_at=timestamp)
        cache_dir.mkdir(parents=True, exist_ok=True)
        manifest_path = cache_dir / 'MANIFEST.json'
        if not manifest_path.exists():
            atomic_write_json(
                manifest_path,
                {
                    'schema_version': 1,
                    'record_type': 'ccb_shared_cache_manifest',
                    'provider': cache_dir.name,
                    'project_id': self.project_id,
                    'runtime_state_root': str(self.runtime_state_root),
                    'created_at': timestamp,
                    'entries': [],
                },
            )
        return cache_dir

    @property
    def external_provider_cache_root(self) -> Path:
        return legacy_provider_projects_root() / self.project_id[:16] / 'provider-cache'

    def provider_external_cache_dir(self, provider: str) -> Path:
        normalized = normalized_segment(provider, label='provider')
        if normalized != str(provider or '').strip().lower() or normalized not in _EXTERNAL_CACHE_PROVIDERS:
            supported = ', '.join(sorted(_EXTERNAL_CACHE_PROVIDERS))
            raise ValueError(f'provider must be one of: {supported}')
        return self.external_provider_cache_root / normalized

    @property
    def user_provider_cache_root(self) -> Path:
        return user_provider_cache_root()

    def provider_user_cache_dir(self, provider: str) -> Path:
        return provider_user_cache_dir(provider)

    def ensure_provider_user_cache_dir(self, provider: str, *, created_at: str | None = None) -> Path:
        return ensure_provider_user_cache_dir(provider, created_at=created_at)

    @property
    def runtime_root_marker_path(self) -> Path:
        return runtime_root_marker_path(self.runtime_state_root)

    @property
    def runtime_root_ref_path(self) -> Path:
        return runtime_root_ref_path(self.ccb_dir)

    @property
    def runtime_marker_status(self) -> str:
        if self.runtime_state_placement.root_kind == 'project':
            return 'not_required'
        try:
            self._validate_runtime_root_marker()
            self._validate_runtime_root_ref()
            return 'ok'
        except FileNotFoundError:
            return 'missing'
        except Exception:
            return 'mismatch'

    def ensure_runtime_state_root(self, *, created_at: str | None = None) -> None:
        if self.runtime_state_placement.root_kind == 'project':
            return
        self.ccb_dir.mkdir(parents=True, exist_ok=True)
        self.runtime_state_root.mkdir(parents=True, exist_ok=True)
        timestamp = created_at or _utc_now()
        self._validate_runtime_root_marker(allow_missing=True)
        self._validate_runtime_root_ref(allow_missing=True)
        atomic_write_json(self.runtime_root_marker_path, self._runtime_root_marker_payload(created_at=timestamp))
        atomic_write_json(self.runtime_root_ref_path, self._runtime_root_ref_payload(created_at=timestamp))

    def _project_socket_placement(self, stem: str) -> SocketPlacement:
        return choose_socket_placement(
            preferred_path=self.ccbd_dir / f'{stem}.sock',
            project_socket_key=self.project_socket_key,
            preferred_root_kind='runtime' if self.runtime_state_placement.root_kind == 'relocated' else 'project',
        )

    def _project_socket_path(self, stem: str) -> Path:
        return self._project_socket_placement(stem).effective_path

    def runtime_state_payload(self) -> dict[str, object]:
        payload = runtime_state_placement_payload(self.runtime_state_placement)
        payload['runtime_marker_status'] = self.runtime_marker_status
        payload['runtime_root_marker_path'] = str(self.runtime_root_marker_path)
        payload['runtime_root_ref_path'] = str(self.runtime_root_ref_path)
        return payload

    def _runtime_root_marker_payload(self, *, created_at: str) -> dict[str, object]:
        return {
            'schema_version': 1,
            'record_type': 'ccb_runtime_root',
            'project_id': self.project_id,
            'project_root': str(self.project_root),
            'anchor_path': str(self.ccb_dir),
            'runtime_root_path': str(self.runtime_state_root),
            'created_at': created_at,
        }

    def _runtime_root_ref_payload(self, *, created_at: str) -> dict[str, object]:
        return {
            'schema_version': 1,
            'record_type': 'ccb_runtime_root_ref',
            'project_id': self.project_id,
            'runtime_state_root': str(self.runtime_state_root),
            'created_at': created_at,
        }

    def _validate_runtime_root_marker(self, *, allow_missing: bool = False) -> None:
        payload = read_runtime_root_marker_payload(self.runtime_root_marker_path)
        if not payload:
            if allow_missing and not self.runtime_root_marker_path.exists():
                return
            if not self.runtime_root_marker_path.exists():
                raise FileNotFoundError(str(self.runtime_root_marker_path))
            raise RuntimeError(f'{self.runtime_root_marker_path} is invalid')
        expected = {
            'project_id': self.project_id,
            'project_root': str(self.project_root),
            'anchor_path': str(self.ccb_dir),
            'runtime_root_path': str(self.runtime_state_root),
        }
        _validate_expected_fields(payload, expected, label=str(self.runtime_root_marker_path))

    def _validate_runtime_root_ref(self, *, allow_missing: bool = False) -> None:
        payload = read_runtime_root_ref_payload(self.ccb_dir, project_id=self.project_id)
        if not payload:
            if allow_missing and not self.runtime_root_ref_path.exists():
                return
            if not self.runtime_root_ref_path.exists():
                raise FileNotFoundError(str(self.runtime_root_ref_path))
            raise RuntimeError(f'{self.runtime_root_ref_path} is invalid')
        expected = {
            'project_id': self.project_id,
            'runtime_state_root': str(self.runtime_state_root),
        }
        _validate_expected_fields(payload, expected, label=str(self.runtime_root_ref_path))


def _read_json_object(path: Path) -> dict[str, object]:
    try:
        data = json.loads(Path(path).read_text(encoding='utf-8'))
    except FileNotFoundError:
        return {}
    if not isinstance(data, dict):
        raise RuntimeError(f'{path} must contain a JSON object')
    return data


def _validate_expected_fields(payload: dict[str, object], expected: dict[str, str], *, label: str) -> None:
    for key, value in expected.items():
        recorded = str(payload.get(key) or '').strip()
        if recorded != value:
            raise RuntimeError(f'{label} field {key} mismatch: expected {value}, found {recorded or "<missing>"}')


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace('+00:00', 'Z')


def _user_cache_home() -> Path:
    raw = str(os.environ.get('XDG_CACHE_HOME') or '').strip()
    if raw:
        candidate = Path(raw).expanduser()
        managed_base = _managed_provider_xdg_cache_base(candidate)
        return managed_base if managed_base is not None else candidate
    return current_provider_source_home() / '.cache'


def _managed_provider_xdg_cache_base(path: Path) -> Path | None:
    candidate = Path(path).expanduser()
    for ancestor in (candidate, *candidate.parents):
        if ancestor.name != 'ccb':
            continue
        try:
            relative = candidate.relative_to(ancestor)
        except ValueError:
            continue
        parts = relative.parts
        if parts[:3] == ('provider-cache', 'gemini', 'xdg'):
            return ancestor.parent
        if (
            len(parts) >= 5
            and parts[0] == 'projects'
            and parts[2:5] == ('provider-cache', 'gemini', 'xdg')
        ):
            return ancestor.parent
    return None


def user_provider_cache_root() -> Path:
    return _user_cache_home() / 'ccb' / 'provider-cache'


def legacy_provider_projects_root() -> Path:
    return _user_cache_home() / 'ccb' / 'projects'


def provider_user_cache_dir(provider: str) -> Path:
    normalized = normalized_segment(provider, label='provider')
    if (
        normalized != str(provider or '').strip().lower()
        or normalized not in _USER_PROVIDER_CACHE_PROVIDERS
    ):
        supported = ', '.join(sorted(_USER_PROVIDER_CACHE_PROVIDERS))
        raise ValueError(f'provider must be one of: {supported}')
    return user_provider_cache_root() / normalized


def ensure_provider_user_cache_dir(provider: str, *, created_at: str | None = None) -> Path:
    cache_dir = provider_user_cache_dir(provider)
    timestamp = created_at or _utc_now()
    cache_dir.mkdir(parents=True, exist_ok=True)
    manifest_path = cache_dir / 'MANIFEST.json'
    if not manifest_path.exists():
        atomic_write_json(
            manifest_path,
            {
                'schema_version': 1,
                'record_type': 'ccb_user_provider_cache_manifest',
                'provider': cache_dir.name,
                'scope': 'user',
                'created_at': timestamp,
                'entries': [],
            },
        )
    return cache_dir


__all__ = [
    'PathLayout',
    'ensure_provider_user_cache_dir',
    'legacy_provider_projects_root',
    'provider_user_cache_dir',
    'user_provider_cache_root',
]
