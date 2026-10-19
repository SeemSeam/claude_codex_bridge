"""Native Windows acceptance runner. Explicit project required; never uses cwd.

Exercises real CCB/Herdr processes. Run against a disposable project only.
The report distinguishes CLI/backend checks from terminal UI checks.
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import subprocess
import sys
import time
import re
import tempfile
from urllib.request import Request, urlopen
from urllib.parse import urlsplit
from concurrent.futures import ThreadPoolExecutor

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / 'lib'))
from ccbd.socket_client import CcbdClient

ROOT = Path(__file__).resolve().parents[3]


def prepare_project(project: Path) -> None:
    marker = project / '.ccb-native-e2e'
    if project.exists() and not marker.is_file() and any(project.iterdir()):
        raise ValueError('existing nonempty project is not marked as a disposable native E2E project')
    project.mkdir(parents=True, exist_ok=True)
    anchor = project / '.ccb'
    anchor.mkdir(exist_ok=True)
    config = anchor / 'ccb.config'
    if not config.exists():
        config.write_text('version = 2\n[windows]\nmain = "codex:codex; claude:claude"\n[runtime.mux]\nbackend = "herdr"\n', encoding='utf-8')
    marker.write_text('Disposable native Windows CCB E2E workspace\n', encoding='utf-8')


def environment(herdr: Path, sh: Path) -> dict[str, str]:
    env = dict(os.environ)
    for key in tuple(env):
        if key.startswith('CCB_CALLER_') or key in {
            'CCB_SESSION_FILE', 'CCB_SESSION_ID', 'CCB_HERDR_SESSION',
            'CCB_HERDR_CAPABILITY_REPORT', 'CCB_NO_ATTACH', 'CODEX_HOME',
            'CLAUDE_CONFIG_DIR', 'WEZTERM_PANE', 'WEZTERM_UNIX_SOCKET',
        }:
            env.pop(key, None)
    env.update(CCB_HERDR_EXE=str(herdr), CCB_SH_EXECUTABLE=str(sh),
               CCB_RUNTIME_MUX_BACKEND='herdr', CCB_SOURCE_RUNTIME_OK='1', PYTHONUTF8='1')
    env.setdefault('CCB_STARTUP_TRANSACTION_TIMEOUT_S', '180')
    env.setdefault('PROCESSOR_ARCHITECTURE', 'AMD64')
    env['PATH'] = str(ROOT / 'bin') + os.pathsep + env.get('PATH', '')
    return env


class Runner:
    def __init__(self, project, output, env):
        self.project, self.output, self.env = project.resolve(), output, env
        if not (self.project / '.ccb-native-e2e').is_file():
            raise ValueError('Requires a marked disposable native E2E project')
        report = output / 'report.json'
        self.results = json.loads(report.read_text(encoding='utf-8')) if report.exists() else []
        output.mkdir(parents=True, exist_ok=True)

    def cli(self, *args, timeout=300):
        return subprocess.run([sys.executable, '-u', str(ROOT / 'platforms/windows/ccb.py'), *args],
                              cwd=self.project, env=self.env, capture_output=True,
                              encoding='utf-8', errors='replace', timeout=timeout,
                              creationflags=subprocess.CREATE_NO_WINDOW)

    def record(self, name, passed, **detail):
        row = dict(name=name, passed=passed, **detail)
        self.results.append(row)
        (self.output / 'report.json').write_text(json.dumps(self.results, ensure_ascii=False, indent=2), encoding='utf-8')
        print(json.dumps(row, ensure_ascii=False), flush=True)

    def state(self):
        return json.loads((self.project / '.ccb/ccbd/state.json').read_text(encoding='utf-8'))

    def client(self):
        return CcbdClient(self.project / '.ccb/ccbd/ccbd.sock', timeout_s=10)

    def herdr(self, *args):
        return subprocess.run([self.env['CCB_HERDR_EXE'], '--session', self.state()['namespace_session_name'], *args],
                              env=self.env, capture_output=True, encoding='utf-8', errors='replace',
                              timeout=15, creationflags=subprocess.CREATE_NO_WINDOW)

    def panes(self):
        result = self.herdr('pane', 'list')
        result.check_returncode()
        return json.loads(result.stdout)['result']['panes']

    def identity(self):
        return {p['pane_id']: (p['terminal_id'], (p.get('tokens') or {}).get('ccb_session_id'))
                for p in self.panes() if (p.get('tokens') or {}).get('ccb_role') == 'agent'}

    def service_checks(self):
        before = self.identity()
        started = time.monotonic()
        reopen = self.cli('herdr', 'open', '--no-attach', '--wait-ready')
        self.record('reopen_preserves_agents', reopen.returncode == 0 and before == self.identity()
                    and 'mode: reconnect' in reopen.stdout, seconds=round(time.monotonic()-started, 2))
        with ThreadPoolExecutor(max_workers=3) as pool:
            results = list(pool.map(lambda _: self.cli('herdr', 'open', '--no-attach', '--wait-ready'), range(3)))
        (self.output / 'concurrent-reopens.log').write_text('\n'.join(r.stdout + r.stderr for r in results), encoding='utf-8')
        after = self.identity()
        self.record('three_concurrent_reopens', all(r.returncode == 0 and 'mode: reconnect' in r.stdout for r in results)
                    and before == after, exit_codes=[r.returncode for r in results], before=before, after=after)
        for provider in ['codex', 'claude']:
            payload = self.client().ping(provider)
            self.record('agent_ready_' + provider, payload.get('mount_state') == 'mounted'
                        and payload.get('runtime_state') == 'idle', runtime_state=payload.get('runtime_state'))
        for command in [('ping', 'all'), ('ps',), ('queue', 'all'), ('config', 'validate'), ('doctor',)]:
            result = self.cli(*command, timeout=60)
            (self.output / ('-'.join(command) + '.log')).write_text(result.stdout + result.stderr, encoding='utf-8')
            self.record('cli_' + '_'.join(command), result.returncode == 0, exit_code=result.returncode)

    def ui_checks(self, wezterm: Path):
        config = self.output / 'wezterm-e2e.lua'
        config.write_text('local c = {}\nc.default_cwd = ' + json.dumps(str(self.project), ensure_ascii=False) +
            '\nc.default_prog = {' + ','.join(json.dumps(s, ensure_ascii=False) for s in [str(ROOT / 'bin/ccb.exe'), 'herdr', 'open', '--wait-ready']) +
            '}\nc.exit_behavior = "Hold"\nc.initial_cols=150\nc.initial_rows=42\nreturn c\n', encoding='utf-8')
        gui_env = dict(self.env, CCB_PYTHON=sys.executable)
        gui = subprocess.Popen([str(wezterm.with_name('wezterm-gui.exe')), '--config-file', str(config),
                                'start', '--always-new-process'], cwd=self.project, env=gui_env,
                               creationflags=subprocess.CREATE_NO_WINDOW)
        try:
            self.gui_env = dict(gui_env, WEZTERM_UNIX_SOCKET=str(Path(os.environ['USERPROFILE']) / '.local/share/wezterm' / f'gui-sock-{gui.pid}'))
            self.wezterm = wezterm
            (self.output / 'gui.json').write_text(json.dumps({'pid':gui.pid, 'socket':self.gui_env['WEZTERM_UNIX_SOCKET'], 'config':str(config)}), encoding='utf-8')
            deadline = time.monotonic() + 45
            visible = ''
            while time.monotonic() < deadline:
                result = self.wez('get-text', '--pane-id', '0')
                visible = result.stdout
                if 'Codex' in visible or 'Claude' in visible:
                    break
                time.sleep(1)
            time.sleep(8)  # Regression: original attach died after five seconds.
            visible = self.wez('get-text', '--pane-id', '0').stdout
            (self.output / 'gui-start.txt').write_text(visible, encoding='utf-8')
            self.record('native_exe_foreground_survives_8s', gui.poll() is None and ('Codex' in visible or 'Claude' in visible)
                        and 'Process exited' not in visible)
            panes = [p for p in self.panes() if (p.get('tokens') or {}).get('ccb_role') == 'agent']
            focused = next((p for p in panes if p.get('focused')), None)
            if focused is None:
                raise RuntimeError('no focused test agent pane')
            pane_id = focused['pane_id']
            for name, value, no_paste in [('unicode_paste', 'CCB测试_中文 mixed 123 !@#', False),
                                          ('ascii_keys', 'CCB_KEYS_9876543210', True),
                                          ('punctuation_paste', 'CCB >>>===<<;;:98765410/..-,++*)', False)]:
                args = ['send-text', '--pane-id', '0'] + (['--no-paste'] if no_paste else [])
                sent = self.wez(*args, input_text=value)
                time.sleep(1)
                content = self.herdr('pane', 'read', pane_id, '--lines', '12', '--format', 'text').stdout
                (self.output / (name + '.txt')).write_text(content, encoding='utf-8')
                self.record(name, sent.returncode == 0 and value in content)
                self.herdr('pane', 'send-keys', pane_id, 'ctrl+u')
                time.sleep(.5)
            before = self.identity()
        finally:
            gui.terminate()
            gui.wait(timeout=15)
        time.sleep(8)
        result = self.cli('herdr', 'open', '--no-attach', '--wait-ready')
        self.record('ui_close_preserves_agents', result.returncode == 0 and before == self.identity())

    def wez(self, *args, input_text=None):
        return subprocess.run([str(self.wezterm), 'cli', '--no-auto-start', *args],
                              env=self.gui_env, input=input_text, capture_output=True,
                              encoding='utf-8', errors='replace', timeout=10,
                              creationflags=subprocess.CREATE_NO_WINDOW)

    def config_checks(self):
        from playwright.sync_api import sync_playwright
        log_path = self.output / 'config-ui-private.log'
        with log_path.open('w', encoding='utf-8') as log:
            proc = subprocess.Popen([sys.executable, '-u', str(ROOT / 'platforms/windows/ccb.py'), 'config', 'ui', '--no-open'],
                                    cwd=self.project, env=self.env, stdout=log, stderr=log,
                                    creationflags=subprocess.CREATE_NO_WINDOW)
            try:
                deadline = time.monotonic() + 40
                url = None
                while time.monotonic() < deadline:
                    text = log_path.read_text(encoding='utf-8')
                    match = re.search(r'url: (http://127\.0\.0\.1:\d+/\?token=\S+)', text)
                    if match:
                        url = match.group(1)
                        break
                    if proc.poll() is not None:
                        break
                    time.sleep(.3)
                if not url:
                    raise RuntimeError('config UI did not publish a loopback URL')
                parts = urlsplit(url)
                def api(path, payload=None):
                    req = Request(f'{parts.scheme}://{parts.netloc}{path}?{parts.query}',
                                  data=None if payload is None else json.dumps(payload).encode('utf-8'),
                                  headers={'Content-Type':'application/json'})
                    with urlopen(req, timeout=30) as response:
                        return json.load(response)
                with sync_playwright() as browser_api:
                    browser = browser_api.chromium.launch(channel='msedge', headless=True)
                    page = browser.new_page(viewport={'width':1440, 'height':1000})
                    errors = []
                    page.on('pageerror', lambda error: errors.append(str(error)))
                    response = page.goto(url, wait_until='networkidle', timeout=45000)
                    page.screenshot(path=str(self.output / 'config-ui.png'), full_page=True)
                    buttons = page.get_by_role('button').all_text_contents()
                    (self.output / 'config-ui-buttons.json').write_text(json.dumps(buttons, ensure_ascii=False, indent=2), encoding='utf-8')
                    self.record('config_ui_browser', response.status == 200 and not errors
                                and 'CCB' in page.title(), javascript_error_count=len(errors))
                    browser.close()
                for endpoint in ['/api/session', '/api/capabilities', '/api/config']:
                    payload = api(endpoint)
                    self.record('config_ui_' + endpoint.rsplit('/',1)[-1], isinstance(payload, dict))
                config_path = self.project / '.ccb/ccb.config'
                original = config_path.read_text(encoding='utf-8')
                before = self.identity()
                current = api('/api/config')
                result = api('/api/apply', {'text': original + '\n# native Windows E2E roundtrip\n',
                                          'expected_digest': current['digest'], 'mode':'save'})
                changed = 'native Windows E2E roundtrip' in config_path.read_text(encoding='utf-8')
                current = api('/api/config')
                api('/api/apply', {'text': original, 'expected_digest':current['digest'], 'mode':'save'})
                self.record('config_ui_save_restore', changed and config_path.read_text(encoding='utf-8') == original
                            and before == self.identity())
            finally:
                proc.terminate()
                proc.wait(timeout=15)

    def job_checks(self):
        for provider in ['codex', 'claude']:
            marker = 'CCB_E2E_' + provider.upper() + '_OK'
            receipt = self.output / f'ask-{provider}.log'
            if receipt.exists():
                submitted = receipt.read_text(encoding='utf-8')
            else:
                result = self.cli('ask', provider, '--', f'This is an end-to-end connectivity test. Reply with exactly {marker}. Do not use tools or edit files.', timeout=30)
                submitted = result.stdout + result.stderr
                receipt.write_text(submitted, encoding='utf-8')
            match = re.search(r'job=(job_[a-z0-9]+)', submitted)
            if not match:
                self.record('ask_' + provider, False, reason='no job id in submission')
                continue
            job_id = match.group(1)
            deadline = time.monotonic() + 150
            payload = {}
            while time.monotonic() < deadline:
                payload = self.client().get(job_id)
                if payload.get('status') in {'completed', 'failed', 'cancelled', 'succeeded'}:
                    break
                time.sleep(2)
            reply = str(payload.get('reply') or '')
            detail = {k:payload.get(k) for k in ['status', 'reply', 'completion_reason']}
            (self.output / f'pend-{provider}.log').write_text(json.dumps(detail, ensure_ascii=False, indent=2), encoding='utf-8')
            self.record('ask_' + provider, marker in reply and payload.get('status') in {'completed', 'succeeded'},
                        job_id=job_id, status=payload.get('status'))

    def lifecycle_checks(self):
        before = self.identity()
        result = self.cli('restart', 'codex', timeout=180)
        (self.output / 'restart-codex.log').write_text(result.stdout + result.stderr, encoding='utf-8')
        if 'deferred_to_provider_runtime_on_herdr' in result.stdout + result.stderr:
            self.record('restart_codex_reports_unsupported', result.returncode != 0 and before == self.identity(),
                        exit_code=result.returncode, supported=False)
        else:
            self.record('restart_codex', result.returncode == 0, exit_code=result.returncode)
        before_stop = self.identity()
        stopped = self.cli('kill', timeout=90)
        self.record('explicit_stop', stopped.returncode == 0, exit_code=stopped.returncode)
        results = []
        with ThreadPoolExecutor(max_workers=3) as pool:
            results = list(pool.map(lambda _: self.cli('herdr', 'open', '--no-attach', '--wait-ready'), range(3)))
        for i, result in enumerate(results):
            (self.output / f'concurrent-cold-{i}.log').write_text(result.stdout + result.stderr, encoding='utf-8')
        self.record('concurrent_start_after_stop', all(r.returncode == 0 for r in results)
                    and sum('mode: reconnect' in r.stdout for r in results) == 2
                    and len(self.identity()) == 2, exit_codes=[r.returncode for r in results])

    def failure_checks(self):
        from storage.paths import PathLayout

        temporary = tempfile.TemporaryDirectory(prefix='invalid-config-', dir=self.project)
        with temporary as directory:
            invalid = Path(directory).resolve()
            invalid.relative_to(self.project)
            session = PathLayout(invalid).ccbd_tmux_session_name
            try:
                (invalid / '.ccb').mkdir(exist_ok=True)
                (invalid / '.ccb/ccb.config').write_text('version = [broken TOML', encoding='utf-8')
                bad = subprocess.run([sys.executable, str(ROOT/'platforms/windows/ccb.py'), 'herdr', 'open', '--no-attach', '--wait-ready'],
                                     cwd=invalid, env=self.env, capture_output=True, encoding='utf-8',
                                     timeout=30, creationflags=subprocess.CREATE_NO_WINDOW)
                self.record('invalid_config_fails', bad.returncode != 0, exit_code=bad.returncode)
                missing = subprocess.run(['powershell.exe','-NoProfile','-ExecutionPolicy','Bypass','-File',
                                          str(ROOT/'platforms/windows/start.ps1'), '-ProjectRoot',str(self.project),
                                          '-InstallRoot',str(invalid/'missing-install'),'-NoPause','-NoAttach'],
                                         env=self.env, capture_output=True, encoding='utf-8', errors='replace',
                                         timeout=15, creationflags=subprocess.CREATE_NO_WINDOW)
                self.record('missing_launcher_fails_visibly', missing.returncode != 0 and 'launcher is missing' in missing.stdout,
                            exit_code=missing.returncode)
            finally:
                subprocess.run([self.env['CCB_HERDR_EXE'], '--session', session, 'server', 'stop'],
                               env=self.env, capture_output=True, timeout=15,
                               creationflags=subprocess.CREATE_NO_WINDOW)
                # Herdr acknowledges shutdown before its process has necessarily
                # released the cwd handle. Keep cleanup bounded on Windows.
                deadline = time.monotonic() + 5
                while True:
                    try:
                        temporary.cleanup()
                        break
                    except PermissionError:
                        if time.monotonic() >= deadline:
                            raise
                        time.sleep(.1)
        from platforms.windows.herdr.runtime.cli import HerdrCliRequestAdapter
        from terminal_runtime.mux_backend_contract import MuxCommandErrorV2
        adapter = HerdrCliRequestAdapter(session_name=self.state()['namespace_session_name'],
                                         herdr_executable=self.env['CCB_HERDR_EXE'])
        before = self.identity()
        rejected = False
        try:
            adapter('attach_namespace', {'namespace_id':'does-not-exist-e2e'})
        except MuxCommandErrorV2:
            rejected = True
        self.record('missing_workspace_attach_fails', rejected and before == self.identity())

    def cold_start(self):
        started = time.monotonic()
        result = self.cli('herdr', 'open', '--no-attach', '--wait-ready')
        (self.output / 'cold-start.log').write_text(result.stdout + result.stderr, encoding='utf-8')
        self.record('cold_start', result.returncode == 0, exit_code=result.returncode,
                    seconds=round(time.monotonic()-started, 2))
        if result.returncode:
            raise RuntimeError('cold start failed; inspect cold-start.log')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--project', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--herdr', type=Path, required=True)
    parser.add_argument('--sh', type=Path, required=True)
    parser.add_argument('--stage', choices=['cold', 'services', 'ui', 'config', 'jobs', 'lifecycle', 'failures'], default='cold')
    parser.add_argument('--wezterm', type=Path)
    args = parser.parse_args()
    if sys.platform != 'win32':
        parser.error('native Windows required')
    try:
        prepare_project(args.project.resolve())
    except ValueError as exc:
        parser.error(str(exc))
    runner = Runner(args.project.resolve(), args.output.resolve(), environment(args.herdr, args.sh))
    first_result = len(runner.results)
    if args.stage == 'cold':
        runner.cold_start()
    elif args.stage == 'services':
        runner.service_checks()
    elif args.stage == 'config':
        runner.config_checks()
    elif args.stage == 'jobs':
        runner.job_checks()
    elif args.stage == 'lifecycle':
        runner.lifecycle_checks()
    elif args.stage == 'failures':
        runner.failure_checks()
    elif args.stage == 'ui':
        if not args.wezterm:
            parser.error('--wezterm is required for UI checks')
        runner.ui_checks(args.wezterm)
    if any(not row['passed'] for row in runner.results[first_result:]):
        raise SystemExit(1)


if __name__ == '__main__':
    main()
