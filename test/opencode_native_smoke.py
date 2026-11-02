"""Opt-in no-model OpenCode v1 native-state smoke against a selected CCB install.

Run: python test/opencode_native_smoke.py --app-root /installed/ccb
Starts only an isolated loopback server; never inherits Provider auth/config.
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import shutil
import socket
import subprocess
import sys
import tempfile
import time
from urllib.parse import urlencode
from urllib.request import Request, urlopen


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--app-root', type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument('--opencode', default='opencode')
    args = parser.parse_args()
    app_root = args.app_root.resolve()
    sys.path.insert(0, str(app_root / 'lib'))
    from opencode_runtime import native as native_module
    assert Path(native_module.__file__).resolve().is_relative_to(app_root), 'Wrong app-root imported'
    executable = shutil.which(args.opencode)
    if executable is None:
        raise RuntimeError('Install upstream OpenCode first')
    with tempfile.TemporaryDirectory(prefix='ccb-native-opencode-') as temporary:
        root = Path(temporary)
        with socket.socket() as probe:
            probe.bind(('127.0.0.1', 0))
            port = probe.getsockname()[1]
        environment = {'PATH': os.environ.get('PATH', ''), 'OPENCODE_DISABLE_AUTOUPDATE': 'true'}
        for variable, name in [('HOME', 'home'), ('XDG_DATA_HOME', 'data'),
                               ('XDG_CONFIG_HOME', 'config'), ('XDG_STATE_HOME', 'state'),
                               ('XDG_CACHE_HOME', 'cache')]:
            (root / name).mkdir(mode=0o700)
            environment[variable] = str(root / name)
        with (root / 'server.log').open('w') as log:
            process = subprocess.Popen([executable, 'serve', '--hostname', '127.0.0.1', '--port', str(port)],
                                       cwd=root, env=environment, stdout=log, stderr=log)
            try:
                origin = f'http://127.0.0.1:{port}'
                deadline = time.monotonic() + 15
                while True:
                    try:
                        with urlopen(origin + '/global/health', timeout=0.5) as response:
                            json.load(response)
                        break
                    except Exception:
                        if process.poll() is not None or time.monotonic() > deadline:
                            raise RuntimeError('Isolated OpenCode v1 server unavailable')
                        time.sleep(0.1)
                request = Request(origin + '/session?' + urlencode({'directory': str(root)}),
                                  data=b'{}', headers={'Content-Type': 'application/json'})
                with urlopen(request, timeout=2) as response:
                    info = json.load(response)
                native = native_module.OpenCodeNativeState(origin, str(root))
                assert native.session(info['id'])['payload']['id'] == info['id']
                assert native.messages(info['id']) == []
                print('PASS OpenCode v1 isolated native service: exact session/workspace and structured messages; no model request')
            finally:
                process.terminate()
                try:
                    process.wait(timeout=3)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait()


if __name__ == '__main__':
    main()
