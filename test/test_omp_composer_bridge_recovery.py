"""Execute the real generated bridge with Bun, without a provider or model."""
import os
import shutil
import socket
import subprocess

import pytest

from provider_backends.omp.composer_bridge import BRIDGE, IMPORTS


@pytest.mark.skipif(shutil.which('bun') is None, reason='requires OMP Bun runtime')
@pytest.mark.parametrize('stale', [False, True])
def test_editor_bridge_recovers_socket_after_process_crash(tmp_path, stale):
    path = tmp_path / 'editor.sock'
    if stale:
        with socket.socket(socket.AF_UNIX) as sock:
            sock.bind(str(path))
    script = IMPORTS + '''
const actor = "worker";
const launchSessionId = "launch";
const runtimeInstanceId = "runtime";
const handlers: Record<string, any> = {};
const pi = {on(name: string, fn: any) {handlers[name] = fn;}};
''' + BRIDGE + '''
const ctx = {hasUI: true, isIdle: () => true,
  sessionManager: {getSessionId: () => "session"},
  ui: {getEditorText: () => "", setEditorText: () => {throw Error("unexpected clear");}}};
await handlers.session_start({}, ctx);
await new Promise(resolve => setTimeout(resolve, 30));
const answer = await new Promise((resolve, reject) => {
  const client = createConnection(process.env.CCB_OMP_COMPOSER_SOCKET!);
  client.on("error", reject);
  client.setTimeout(1000, () => {client.destroy(); reject(Error("timeout"));});
  client.on("connect", () => client.write(JSON.stringify({actor, launch_session_id: launchSessionId, operation: "inspect"}) + "\\n"));
  client.on("data", data => {resolve(JSON.parse(data.toString())); client.destroy();});
});
console.log(JSON.stringify(answer));
await handlers.session_shutdown();
'''
    result = subprocess.run(['bun', 'run', '-'], input=script, text=True,
                            capture_output=True, timeout=10,
                            env={**os.environ, 'CCB_OMP_COMPOSER_SOCKET': str(path)})
    assert result.returncode == 0, result.stderr
    assert '"state":"empty"' in result.stdout


@pytest.mark.skipif(shutil.which('bun') is None, reason='requires OMP Bun runtime')
@pytest.mark.parametrize('kind', ['live', 'file', 'symlink'])
def test_bridge_does_not_replace_other_socket_or_file(tmp_path, kind):
    path = tmp_path / 'editor.sock'
    with socket.socket(socket.AF_UNIX) as live:
        if kind == 'live':
            live.bind(str(path))
            live.listen(2)
        elif kind == 'file':
            path.write_text('not a socket')
        else:
            destination = tmp_path / 'destination'
            destination.write_text('not a socket')
            path.symlink_to(destination)
        before = path.lstat()
        script = IMPORTS + '''
const actor = "worker", launchSessionId = "launch", runtimeInstanceId = "runtime";
const handlers: Record<string, any> = {};
const pi = {on(name: string, fn: any) {handlers[name] = fn;}};
''' + BRIDGE + '''
await handlers.session_start({}, {});
await handlers.session_shutdown();
'''
        result = subprocess.run(['bun', 'run', '-'], input=script, text=True,
                                capture_output=True, timeout=10,
                                env={**os.environ, 'CCB_OMP_COMPOSER_SOCKET': str(path)})
        assert result.returncode == 0, result.stderr
        assert path.lstat().st_ino == before.st_ino
        assert path.lstat().st_mode == before.st_mode
