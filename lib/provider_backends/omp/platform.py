from __future__ import annotations

import sys


def collab_enabled() -> bool:
    """Select the Linux runtime explicitly, never by bridge availability."""
    return sys.platform == "linux"
