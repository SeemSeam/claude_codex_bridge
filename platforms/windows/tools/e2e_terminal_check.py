"""Verify actual WezTerm ancestry and refusal of a known-risk terminal host."""
import argparse
import json
from pathlib import Path
import subprocess
import sys
import time

from e2e_local import ROOT, Runner, environment


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ('project', 'output', 'old-wezterm', 'new-wezterm', 'herdr', 'sh'):
        parser.add_argument('--' + name, type=Path, required=True)
    args = parser.parse_args()
    runner = Runner(args.project, args.output, environment(args.herdr, args.sh))
    before = runner.identity()
    checks = [
        ('ancestor_signed_pair', args.new_wezterm, ['windows-terminal-check', '--json'], 'verified-pair'),
        ('ancestor_old_pair', args.old_wezterm, ['windows-terminal-check', '--json'], 'known-risk'),
        ('automatic_old_host_refusal', args.old_wezterm, ['herdr', 'open', '--wait-ready'], 'blocked attachment'),
    ]
    for name, wezterm, arguments, expected in checks:
        config = runner.output / (name + '.lua')
        command = [str(ROOT / 'bin/ccb.exe'), *arguments]
        config.write_text('return {default_cwd=' + json.dumps(str(runner.project), ensure_ascii=False) +
                          ',default_prog={' + ','.join(json.dumps(s, ensure_ascii=False) for s in command) +
                          '},initial_cols=180,initial_rows=35,exit_behavior="Hold"}', encoding='utf-8')
        env = dict(runner.env, CCB_PYTHON=sys.executable)
        gui = subprocess.Popen([str(wezterm.with_name('wezterm-gui.exe')), '--config-file', str(config),
                                'start', '--always-new-process'], env=env, creationflags=subprocess.CREATE_NO_WINDOW)
        runner.wezterm = wezterm
        runner.gui_env = dict(env, WEZTERM_UNIX_SOCKET=str(Path.home() / '.local/share/wezterm' / f'gui-sock-{gui.pid}'))
        try:
            deadline = time.monotonic() + 45
            visible = ''
            while time.monotonic() < deadline:
                visible = runner.wez('get-text', '--pane-id', '0').stdout
                if expected in visible:
                    break
                time.sleep(1)
            (runner.output / (name + '.txt')).write_text(visible, encoding='utf-8')
            passed = expected in visible
            if name.startswith('ancestor'):
                passed = passed and 'ancestor' in visible
            runner.record(name, passed)
            if not passed:
                raise RuntimeError('Terminal acceptance failed: ' + name)
        finally:
            if gui.poll() is None:
                gui.terminate()
                gui.wait(timeout=15)
    runner.record('terminal_checks_preserve_agents', runner.identity() == before)


if __name__ == '__main__':
    main()
