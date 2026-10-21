from pathlib import Path

from provider_backends.omp import socket_paths


def test_macos_socket_root_ignores_long_per_user_tmpdir(monkeypatch):
    monkeypatch.setattr(socket_paths.sys, 'platform', 'darwin')
    monkeypatch.setattr(socket_paths.tempfile, 'gettempdir', lambda: '/var/folders/' + 'x' * 100 + '/T')
    root = socket_paths.socket_temp_root()
    assert root == Path('/tmp')
    endpoint = root / ('ccb-omp-501-' + 'a' * 20) / 'bridge.sock'
    assert len(str(endpoint).encode()) < 104


def test_linux_keeps_existing_temporary_directory(monkeypatch):
    monkeypatch.setattr(socket_paths.sys, 'platform', 'linux')
    monkeypatch.setattr(socket_paths.tempfile, 'gettempdir', lambda: '/tmp/custom')
    assert socket_paths.socket_temp_root() == Path('/tmp/custom')
