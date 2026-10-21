"""Short POSIX IPC paths; macOS TMPDIR often exceeds sockaddr_un limits."""
from pathlib import Path
import sys
import tempfile


def socket_temp_root() -> Path:
    # macOS resolves /tmp to /private/tmp. Both are short enough for its
    # 104-byte sockaddr_un; never put sockets in /var/folders/.../T.
    return Path('/tmp') if sys.platform == 'darwin' else Path(tempfile.gettempdir())
