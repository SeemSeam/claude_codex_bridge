from copy import deepcopy
from io import StringIO
import json
from types import SimpleNamespace

import pytest

from cli.parser import CliParser, CliUsageError
from cli.services import screen
from cli.phase2_runtime.handlers_screen import handle_screen
from terminal_runtime.tmux_backend import TmuxBackend


@pytest.mark.parametrize('args', [[], ['a', 'b'], ['a', '--lines'], ['a', '--lines', '-1'],
    ['a', '--lines', '1001'], ['a', '--lines', 'x'], ['a', '--bad'], ['a', '--json', '--json']])
def test_parser_rejects_invalid_screen(args):
    with pytest.raises(CliUsageError):
        CliParser().parse(['screen', *args])


def test_screen_parser_options():
    command = CliParser().parse(['--project', '/tmp/project', 'screen', 'demo', '--lines', '120', '--json'])
    assert (command.agent_name, command.lines, command.json_output) == ('demo', 120, True)
    assert CliParser().parse(['screen', 'demo']).lines == 0


@pytest.fixture
def capture_env(monkeypatch):
    view = {'project': {'id': 'p'}, 'ccbd': {'state': 'mounted'},
            'namespace': {'socket_path': '/tmp/project-tmux', 'epoch': 2, 'namespace_backend_impl': 'tmux'},
            'agents': [{'name': 'demo', 'pane_id': '%3'}]}
    info = {'pane_id': '%3', 'pane_dead': '0', '@ccb_project_id': 'p', '@ccb_agent': 'demo',
            '@ccb_namespace_epoch': '2', '@ccb_managed_by': 'ccbd', '@ccb_role': 'agent', '@ccb_session_id': 's'}
    calls = []
    backend = SimpleNamespace(describe_pane=lambda *a, **k: dict(info),
                              capture_screen=lambda *a, **k: calls.append((a, k)) or 'hello\n  世界\n')
    def make_backend(**kwargs):
        assert kwargs == {'socket_path': '/tmp/project-tmux'}
        return backend
    monkeypatch.setattr(screen, 'TmuxBackend', make_backend)
    client = SimpleNamespace(project_view=lambda: {'view': deepcopy(view)})
    monkeypatch.setattr(screen, 'connect_current_mounted_daemon', lambda _: SimpleNamespace(client=client))
    return SimpleNamespace(view=view, info=info, calls=calls, backend=backend, client=client,
                           context=SimpleNamespace(project=SimpleNamespace(project_id='p')))


def test_screen_json_and_plain(capture_env):
    command = CliParser().parse(['screen', 'demo', '--lines', '120', '--json'])
    services = SimpleNamespace(agent_screen=screen.agent_screen)
    out = StringIO()
    assert handle_screen(capture_env.context, command, out, services) == 0
    payload = json.loads(out.getvalue())
    assert payload['text'] == 'hello\n  世界\n'
    assert payload['pane'] == '%3' and payload['captured_at']
    assert capture_env.calls == [(('%3',), {'history_lines': 120})]
    out = StringIO()
    assert handle_screen(capture_env.context, CliParser().parse(['screen', 'demo']), out, services) == 0
    assert 'agent: demo' in out.getvalue() and '--- screen ---\nhello\n  世界\n' in out.getvalue()


@pytest.mark.parametrize('key,value', [('pane_dead', '1'), ('@ccb_project_id', 'foreign'),
    ('@ccb_agent', 'other'), ('@ccb_namespace_epoch', '1'), ('@ccb_role', 'cmd')])
def test_screen_rejects_wrong_pane(capture_env, key, value):
    capture_env.info[key] = value
    payload = screen.agent_screen(capture_env.context, CliParser().parse(['screen', 'demo']))
    assert payload['status'] == 'failed'
    assert not capture_env.calls


def test_screen_rejects_unknown_and_changed_binding(capture_env):
    assert screen.agent_screen(capture_env.context, CliParser().parse(['screen', 'unknown']))['status'] == 'failed'
    def capture(*a, **k):
        capture_env.info['@ccb_session_id'] = 'new'
        return 'must not expose'
    capture_env.backend.capture_screen = capture
    result = screen.agent_screen(capture_env.context, CliParser().parse(['screen', 'demo']))
    assert result['status'] == 'failed' and 'text' not in result


def test_screen_capture_failure_and_daemon_unavailable(capture_env, monkeypatch):
    capture_env.backend.capture_screen = lambda *a, **k: None
    command = CliParser().parse(['screen', 'demo', '--json'])
    assert screen.agent_screen(capture_env.context, command)['status'] == 'failed'
    def unavailable(_):
        raise RuntimeError('project ccbd is unmounted')
    monkeypatch.setattr(screen, 'connect_current_mounted_daemon', unavailable)
    out = StringIO()
    assert handle_screen(capture_env.context, command, out, SimpleNamespace(agent_screen=screen.agent_screen)) == 1
    assert 'unmounted' in json.loads(out.getvalue())['error']


@pytest.mark.parametrize('section,key,value', [
    ('project', 'id', 'foreign'), ('ccbd', 'state', 'unmounted'),
    ('namespace', 'namespace_backend_impl', 'unsupported'),
    ('namespace', 'socket_path', None), ('namespace', 'epoch', None),
])
def test_screen_requires_current_project_namespace(capture_env, section, key, value):
    capture_env.view[section][key] = value
    assert screen.agent_screen(capture_env.context, CliParser().parse(['screen', 'demo']))['status'] == 'failed'
    assert not capture_env.calls


def test_screen_rejects_unbound_or_rebound_agent(capture_env):
    capture_env.view['agents'][0]['pane_id'] = None
    command = CliParser().parse(['screen', 'demo'])
    assert screen.agent_screen(capture_env.context, command)['status'] == 'failed'
    capture_env.view['agents'][0]['pane_id'] = '%3'
    def capture(*a, **k):
        capture_env.view['agents'][0]['pane_id'] = '%4'
        return 'must not expose'
    capture_env.backend.capture_screen = capture
    assert screen.agent_screen(capture_env.context, command)['status'] == 'failed'


@pytest.mark.parametrize('lines', [0, 120, 1000])
def test_tmux_capture_visible_and_history(lines):
    backend = TmuxBackend(socket_path='/tmp/screen-test')
    calls = []
    backend._pane_service().tmux_run_fn = lambda args, **kw: calls.append((args, kw)) or SimpleNamespace(
        returncode=0, stdout='\x1b[31mred\x1b[0m\n  next\n')
    assert backend.capture_screen('%3', history_lines=lines) == 'red\n  next\n'
    args, kwargs = calls[0]
    assert args == ['capture-pane', '-p', '-t', '%3'] + (['-S', f'-{lines}'] if lines else [])
    assert kwargs['timeout'] == 2.0


def test_tmux_capture_failure_is_not_empty_success():
    backend = TmuxBackend(socket_path='/tmp/screen-test')
    backend._pane_service().tmux_run_fn = lambda *a, **k: SimpleNamespace(returncode=1, stdout='')
    assert backend.capture_screen('%3') is None
    def timeout(*a, **k):
        raise TimeoutError('tmux timeout')
    backend._pane_service().tmux_run_fn = timeout
    assert backend.capture_screen('%3') is None
