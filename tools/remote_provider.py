#!/usr/bin/env python3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'lib'))
from remote_workspace.provider_client import main

if __name__ == '__main__':
    main()
