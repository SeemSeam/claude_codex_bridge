#!/usr/bin/env python3

from __future__ import annotations

import sys


def main() -> int:
    print(
        "error: standalone ccb-cleanup was removed; use `ccb kill --zombies` for global cleanup or `ccb kill` inside a project",
        file=sys.stderr,
    )
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
