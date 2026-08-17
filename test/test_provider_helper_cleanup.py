from __future__ import annotations

import os
from types import SimpleNamespace

from provider_runtime.helper_cleanup import cleanup_stale_runtime_helper, terminate_helper_manifest_path
from provider_runtime.helper_manifest import ProviderHelperManifest, save_helper_manifest
from storage.paths import PathLayout


def _write_helper(path, *, runtime_generation: int = 1, leader_pid: int = 777, pgid: int = 888) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        (
            '{"schema_version":1,"record_type":"provider_helper_manifest","agent_name":"agent1",'
            f'"runtime_generation":{runtime_generation},"helper_kind":"codex_bridge","leader_pid":{leader_pid},"pgid":{pgid},'
            '"started_at":"2026-04-22T00:00:00Z","owner_daemon_generation":5,"state":"running"}\n'
        ),
        encoding='utf-8',
    )


def _patch_posix_os(monkeypatch, killed: list[tuple[int, int]]) -> None:
    os_proxy = SimpleNamespace(
        name='posix',
        getpgrp=lambda: 999,
        killpg=lambda pgid, sig: killed.append((pgid, int(sig))) or None,
        kill=lambda pid, sig: (_ for _ in ()).throw(ProcessLookupError()) if sig == 0 else None,
    )
    monkeypatch.setattr('provider_runtime.helper_cleanup.os', os_proxy)
    monkeypatch.setattr(
        'provider_runtime.helper_cleanup._shared_is_pid_alive',
        lambda _pid: False,
    )


def test_save_helper_manifest_skips_identical_atomic_rewrite(tmp_path) -> None:
    path = tmp_path / 'helper.json'
    manifest = ProviderHelperManifest(
        agent_name='agent1',
        runtime_generation=3,
        helper_kind='codex_bridge',
        leader_pid=777,
        pgid=888,
        started_at='2026-04-22T00:00:00Z',
        owner_daemon_generation=5,
    )
    save_helper_manifest(path, manifest)
    fixed_ns = 1_000_000_000
    os.utime(path, ns=(fixed_ns, fixed_ns))

    save_helper_manifest(path, manifest)

    assert path.stat().st_mtime_ns == fixed_ns


def test_cleanup_stale_runtime_helper_reaps_superseded_manifest(tmp_path, monkeypatch) -> None:
    layout = PathLayout(tmp_path / 'repo')
    helper_path = layout.agent_helper_path('agent1')
    _write_helper(helper_path, runtime_generation=1, leader_pid=777, pgid=888)
    killed: list[tuple[int, int]] = []

    _patch_posix_os(monkeypatch, killed)

    reaped = cleanup_stale_runtime_helper(
        layout,
        SimpleNamespace(
            agent_name='agent1',
            provider='codex',
            runtime_generation=2,
            state='idle',
            runtime_root='/tmp/runtime-new',
        ),
    )

    assert reaped is True
    assert helper_path.exists() is False
    assert killed[0][0] == 888


def test_cleanup_stale_runtime_helper_keeps_current_owner_manifest(tmp_path) -> None:
    layout = PathLayout(tmp_path / 'repo')
    helper_path = layout.agent_helper_path('agent1')
    _write_helper(helper_path, runtime_generation=3)

    reaped = cleanup_stale_runtime_helper(
        layout,
        SimpleNamespace(
            agent_name='agent1',
            provider='codex',
            runtime_generation=3,
            state='idle',
            runtime_root='/tmp/runtime-current',
        ),
    )

    assert reaped is False
    assert helper_path.exists() is True


def test_cleanup_stale_runtime_helper_requires_canonical_runtime_generation(tmp_path, monkeypatch) -> None:
    layout = PathLayout(tmp_path / 'repo')
    helper_path = layout.agent_helper_path('agent1')
    _write_helper(helper_path, runtime_generation=3, leader_pid=777, pgid=888)
    killed: list[tuple[int, int]] = []

    _patch_posix_os(monkeypatch, killed)

    reaped = cleanup_stale_runtime_helper(
        layout,
        SimpleNamespace(
            agent_name='agent1',
            provider='codex',
            binding_generation=3,
            runtime_generation=None,
            state='idle',
            runtime_root='/tmp/runtime-current',
        ),
    )

    assert reaped is True
    assert helper_path.exists() is False
    assert killed[0][0] == 888


def test_terminate_helper_manifest_path_clears_file_when_leader_is_gone(tmp_path, monkeypatch) -> None:
    layout = PathLayout(tmp_path / 'repo')
    helper_path = layout.agent_helper_path('agent1')
    _write_helper(helper_path, leader_pid=501, pgid=601)
    killed: list[tuple[int, int]] = []

    _patch_posix_os(monkeypatch, killed)

    assert terminate_helper_manifest_path(helper_path) is True
    assert helper_path.exists() is False
    assert killed[0][0] == 601
