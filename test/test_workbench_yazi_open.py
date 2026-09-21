"""Opt-in native UI verification; the opener is replaced, never the editor."""
from __future__ import annotations

import json
import os
from pathlib import Path
import shlex
import shutil
import subprocess
import sys
import time
import uuid

import pytest

from cli.tools_runtime import workbench


@pytest.mark.parametrize('system,release,wsl_env,tools,expected', [
    ('Linux', '6.8.0', {}, ['gio', 'xdg-open'], 'gio'),
    ('Linux', '6.8.0', {}, ['xdg-open'], 'xdg-open'),
    ('Darwin', '24.0.0', {}, ['open', 'gio'], 'open'),
    ('Linux', '6.6.87.2-microsoft-standard-WSL2', {}, ['wslview', 'gio'], 'wslview'),
    ('Linux', '4.4.0-Microsoft', {}, ['wslview', 'gio'], 'wslview'),
    ('Linux', '6.8.0', {'WSL_DISTRO_NAME': 'Ubuntu'}, ['wslview', 'gio'], 'wslview'),
    ('Linux', '6.8.0', {'WSL_INTEROP': '/run/WSL/1_interop'}, ['wslview', 'gio'], 'wslview'),
])
def test_system_opener_preserves_multiple_paths(tmp_path, monkeypatch, system, release, wsl_env, tools, expected):
    import tomllib
    monkeypatch.setenv('XDG_DATA_HOME', str(tmp_path / 'data'))
    paths = workbench._paths()
    profile = paths['yazi_safe_profile']
    profile.mkdir(parents=True)
    workbench._write_yazi_config(paths, rich=False)
    config = tomllib.loads((profile / 'yazi.toml').read_text())
    command = next(item['run'] for item in config['opener']['system'] if item['for'] == 'unix')
    log = tmp_path / 'calls'
    bin_dir = tmp_path / 'bin'
    bin_dir.mkdir()
    uname = bin_dir / 'uname'
    uname.write_text(f'#!/bin/sh\ncase "$1" in -s) echo {shlex.quote(system)};; -r) echo {shlex.quote(release)};; esac\n')
    uname.chmod(0o755)
    for tool in tools:
        stub = bin_dir / tool
        stub.write_text(f'#!{sys.executable}\nimport json, sys\n'
                        f'with open({str(log)!r}, "a") as out: out.write(json.dumps(sys.argv) + "\\n")\n')
        stub.chmod(0o755)
    files = [str(tmp_path / "space ' $; file.txt"), '/mnt/c/Users/A B/中文.pdf']
    subprocess.run(['/bin/sh', '-c', command.replace('%s', shlex.join(files))],
                   env={'PATH': str(bin_dir), **wsl_env}, check=True)
    calls = [json.loads(line) for line in log.read_text().splitlines()]
    if expected == 'open':
        assert calls == [[str(bin_dir / 'open'), *files]]
    else:
        assert [Path(call[0]).name for call in calls] == [expected] * 2
        assert [call[1:] for call in calls] == [(['open', name] if expected == 'gio' else [name]) for name in files]


def test_wsl_missing_bridge_reports_failure_without_linux_fallback(tmp_path):
    helper = tmp_path / 'ccb-file-open'
    workbench._write_file_opener(helper)
    uname = tmp_path / 'uname'
    uname.write_text('#!/bin/sh\necho Linux\n')
    uname.chmod(0o755)
    gio = tmp_path / 'gio'
    gio.write_text('#!/bin/sh\nexit 0\n')
    gio.chmod(0o755)
    result = subprocess.run([str(helper), '/mnt/c/a.txt'],
                            env={'PATH': str(tmp_path), 'WSL_DISTRO_NAME': 'Ubuntu'},
                            capture_output=True, text=True)
    assert result.returncode == 127
    assert 'wslview (wslu)' in result.stderr


def test_failed_opener_stops_without_duplicate_retry(tmp_path):
    helper = tmp_path / 'ccb-file-open'
    workbench._write_file_opener(helper)
    for name, body in [('uname', 'echo Linux'), ('gio', 'exit 17'),
                       ('xdg-open', 'echo unexpected-retry; exit 0')]:
        tool = tmp_path / name
        tool.write_text('#!/bin/sh\n' + body + '\n')
        tool.chmod(0o755)
    result = subprocess.run([str(helper), '/tmp/a.txt'], env={'PATH': str(tmp_path)},
                            capture_output=True, text=True)
    assert result.returncode == 17
    assert 'unexpected-retry' not in result.stdout


@pytest.mark.skipif(os.environ.get('CCB_TEST_REAL_YAZI') != '1', reason='requires native Yazi and tmux')
@pytest.mark.parametrize('rich', [False, True])
def test_native_file_open_and_directory_navigation(tmp_path, monkeypatch, rich):
    yazi = shutil.which('yazi')
    tmux = shutil.which('tmux')
    assert yazi and tmux
    monkeypatch.setenv('XDG_DATA_HOME', str(tmp_path / 'data'))
    paths = workbench._paths()
    profile = paths['yazi_rich_profile' if rich else 'yazi_safe_profile']
    profile.mkdir(parents=True)
    workbench._write_yazi_config(paths, rich=rich)
    # Parsing also catches invalid generated TOML before native startup.
    import tomllib
    for name in ('yazi.toml', 'keymap.toml'):
        tomllib.loads((profile / name).read_text())
    files = tmp_path / 'files'
    files.mkdir()
    folder = files / 'folder'
    folder.mkdir()
    filename = "z space ' $; file.txt"
    target = files / filename
    target.write_text('test document\n')
    log = tmp_path / 'opened.jsonl'
    bin_dir = tmp_path / 'bin'
    bin_dir.mkdir()
    opener = bin_dir / 'xdg-open'
    opener.write_text(f'#!{sys.executable}\nimport json, sys\n'
                      'args = sys.argv[2:] if sys.argv[0].endswith("/gio") else sys.argv[1:]\n'
                      f'with open({str(log)!r}, "a") as out: out.write(json.dumps(args) + "\\n")\n')
    opener.chmod(0o755)
    (bin_dir / 'gio').symlink_to(opener)
    env = dict(os.environ, YAZI_CONFIG_HOME=str(profile),
               PATH=str(bin_dir) + os.pathsep + os.environ['PATH'])
    env.pop('TMUX', None)
    server = 'ccb-yazi-open-' + uuid.uuid4().hex[:12]

    def mux(*args):
        return subprocess.check_output([tmux, '-L', server, *args], env=env, text=True)

    def screen():
        return mux('capture-pane', '-p', '-t', 'test:0.0')

    def until(predicate):
        deadline = time.monotonic() + 8
        while time.monotonic() < deadline:
            if predicate():
                return
            time.sleep(0.1)
        raise AssertionError(screen())

    def opened():
        return [json.loads(line) for line in log.read_text().splitlines()] if log.exists() else []

    def click_name(name):
        rows = screen().splitlines()
        row, text = next((i, line) for i, line in enumerate(rows) if name in line)
        col = text.index(name) + 2
        mux('send-keys', '-t', 'test:0.0', '-l', f'\x1b[<0;{col};{row + 1}M\x1b[<0;{col};{row + 1}m')

    try:
        mux('new-session', '-d', '-s', 'test', '-x', '130', '-y', '32',
            shlex.join([yazi, str(files)]))
        until(lambda: filename in screen())
        click_name(filename)
        until(lambda: len(opened()) == 1)
        assert opened() == [[str(target)]]
        mux('send-keys', '-t', 'test:0.0', 'Enter')
        until(lambda: len(opened()) == 2)
        assert opened() == [[str(target)], [str(target)]]
        # Enter on a folder stays inside Yazi, with no external opener call.
        mux('send-keys', '-t', 'test:0.0', 'g', 'g', 'Enter')
        until(lambda: str(folder) in screen().splitlines()[0])
        assert len(opened()) == 2
        mux('send-keys', '-t', 'test:0.0', 'h')
        until(lambda: filename in screen())
        click_name('folder')
        until(lambda: str(folder) in screen().splitlines()[0])
        assert len(opened()) == 2
    finally:
        subprocess.run([tmux, '-L', server, 'kill-server'], env=env, capture_output=True)
