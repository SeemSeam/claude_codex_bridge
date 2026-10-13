"""TD-007: CLI parser stdin detection tests."""
from __future__ import annotations
import stat
import sys
from unittest.mock import MagicMock, patch
import pytest

from cli.parser import CliParser


class TestReadOptionalStdin:
    """Test _read_optional_stdin socket detection (TD-007)."""

    def test_stdin_socket_returns_empty(self, monkeypatch):
        """AC1: Unix socket stdin should return empty without blocking."""
        parser = CliParser()
        
        # Mock isatty to return False (not TTY)
        monkeypatch.setattr(sys.stdin, 'isatty', lambda: False)
        
        # Mock os.fstat to return socket mode
        mock_stat = MagicMock()
        mock_stat.st_mode = stat.S_IFSOCK | 0o600
        monkeypatch.setattr('os.fstat', lambda fd: mock_stat)
        
        # Mock read_stdin_text to verify it's NOT called
        mock_read = MagicMock(return_value="should not be read")
        monkeypatch.setattr('cli.parser.read_stdin_text', mock_read)
        
        result = parser._read_optional_stdin()
        
        assert result == ''
        mock_read.assert_not_called()  # Should not block on socket

    def test_stdin_fifo_reads_normally(self, monkeypatch):
        """AC2: FIFO (pipe) stdin should read normally."""
        parser = CliParser()
        
        monkeypatch.setattr(sys.stdin, 'isatty', lambda: False)
        
        mock_stat = MagicMock()
        mock_stat.st_mode = stat.S_IFIFO | 0o600
        monkeypatch.setattr('os.fstat', lambda fd: mock_stat)
        
        monkeypatch.setattr('cli.parser.read_stdin_text', lambda: "piped content")
        
        result = parser._read_optional_stdin()
        
        assert result == "piped content"

    def test_stdin_regular_file_reads_normally(self, monkeypatch):
        """AC3: Regular file stdin should read normally."""
        parser = CliParser()
        
        monkeypatch.setattr(sys.stdin, 'isatty', lambda: False)
        
        mock_stat = MagicMock()
        mock_stat.st_mode = stat.S_IFREG | 0o644
        monkeypatch.setattr('os.fstat', lambda fd: mock_stat)
        
        monkeypatch.setattr('cli.parser.read_stdin_text', lambda: "file content")
        
        result = parser._read_optional_stdin()
        
        assert result == "file content"

    def test_stdin_tty_returns_empty_without_checking_mode(self, monkeypatch):
        """AC4: TTY stdin should return empty without calling fstat."""
        parser = CliParser()
        
        # Mock isatty to return True (TTY)
        monkeypatch.setattr(sys.stdin, 'isatty', lambda: True)
        
        # Track if fstat is called
        fstat_called = []
        def mock_fstat(fd):
            fstat_called.append(True)
            return MagicMock()
        monkeypatch.setattr('os.fstat', mock_fstat)
        
        result = parser._read_optional_stdin()
        
        assert result == ''
        assert len(fstat_called) == 0  # fstat should NOT be called for TTY

    def test_fstat_oserror_falls_back_to_read(self, monkeypatch):
        """AC5: OSError from fstat should fall back to read_stdin_text."""
        parser = CliParser()
        
        monkeypatch.setattr(sys.stdin, 'isatty', lambda: False)
        
        # Mock fstat to raise OSError
        def mock_fstat(fd):
            raise OSError("fstat failed")
        monkeypatch.setattr('os.fstat', mock_fstat)
        
        # read_stdin_text should still be called as fallback
        monkeypatch.setattr('cli.parser.read_stdin_text', lambda: "fallback content")
        
        result = parser._read_optional_stdin()
        
        assert result == "fallback content"
