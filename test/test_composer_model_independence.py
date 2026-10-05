"""Model labels must not authorize, block, or delimit composer delivery."""
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from provider_execution.draft_guard import DraftTarget
from provider_execution.draft_observation import inspect_screen


CAPTURES = json.loads((Path(__file__).parent / 'fixtures/composer/native-20260920.json').read_text())
LABELS = ['GPT-6-Astra', 'o3', 'local/custom-model', '未来模型', '']


@pytest.mark.parametrize('label', LABELS)
@pytest.mark.parametrize('capture', [c for c in CAPTURES if c['provider'] == 'codex'],
                         ids=lambda c: c['case'])
def test_codex_native_captures_ignore_model_label(capture, label):
    changed = {**capture, 'text': capture['text'].replace('gpt-6-astra', label)}
    assert inspect_screen('codex', changed, binding='pane') == inspect_screen('codex', capture, binding='pane')


@pytest.mark.parametrize('label', LABELS)
@pytest.mark.parametrize('content, expected', [('', 'empty'), ('draft\nsecond line', 'nonempty')])
def test_claude_composer_ignores_status_model(label, content, expected):
    screen = {'text': f'────────────\n❯ {content}\n────────────\n  {label}',
              'cursor_x': 2, 'cursor_y': 1}
    assert inspect_screen('claude', screen, binding='pane').state == expected


@pytest.mark.parametrize('label', LABELS)
@pytest.mark.parametrize('state', ['empty', 'nonempty', 'unknown'])
def test_omp_uses_native_editor_state_not_model(monkeypatch, label, state):
    screen = {'text': f'{label}\n╰─ \n  draft', 'cursor_y': 2, 'binding': 'pane'}
    backend = SimpleNamespace(capture_composer=lambda pane: screen)
    calls = []

    def inspect(self, operation):
        calls.append(operation)
        return {'state': state, 'runtime_instance_id': 'runtime', 'session_id': 'session'}

    monkeypatch.setattr(DraftTarget, '_editor_request', inspect)
    target = DraftTarget('omp', backend, '%1', 'generation')
    assert target.observe().state == state
    assert calls == ['inspect']
    screen['cursor_y'] = 0  # Native editor contents cannot authorize a menu.
    assert target.observe().state == 'unknown'
    assert calls == ['inspect']


@pytest.mark.parametrize('footer', ['gpt-6-astra', 'GPT-6-Astra', 'o3', 'draft · text'])
def test_model_text_alone_does_not_establish_codex_footer(footer):
    screen = {'text': f'› Ask Codex to do anything\n\n  {footer}', 'cursor_x': 2, 'cursor_y': 0}
    assert inspect_screen('codex', screen, binding='pane').state == 'unknown'


def test_codex_draft_below_footer_like_text_is_not_ignored():
    screen = {'text': '› Ask Codex to do anything\n\n  ? for shortcuts\n  remaining draft',
              'cursor_x': 2, 'cursor_y': 0}
    assert inspect_screen('codex', screen, binding='pane').state == 'unknown'


def test_codex_multiline_placeholder_is_not_empty():
    screen = {'text': '› Ask Codex to do anything\n  remaining draft\n\n  ? for shortcuts',
              'cursor_x': 2, 'cursor_y': 0}
    assert inspect_screen('codex', screen, binding='pane').state == 'nonempty'


@pytest.mark.parametrize('mode', ['INSERT', 'NORMAL', 'VISUAL'])
def test_codex_special_editor_mode_remains_unknown(mode):
    screen = {'text': f'› Ask Codex to do anything\n\n  ? for shortcuts  {mode}',
              'cursor_x': 2, 'cursor_y': 0}
    assert inspect_screen('codex', screen, binding='pane').state == 'unknown'


@pytest.mark.parametrize('label', LABELS)
def test_codex_0158_two_row_footer(label):
    footer = f'  \x1b[38;5;223m{label} default\x1b[39m · \x1b[38;5;151m~/project\x1b[39m\n  ? for shortcuts'
    screen = {'text': '› Ask Codex to do anything\n\n'+footer, 'cursor_x': 2, 'cursor_y': 0}
    assert inspect_screen('codex', screen, binding='pane').state == 'empty'
    screen['text'] = '› Ask Codex to do anything\n  actual draft\n\n'+footer
    assert inspect_screen('codex', screen, binding='pane').state == 'nonempty'
    screen['text'] += '\n  unexpected menu'
    assert inspect_screen('codex', screen, binding='pane').state == 'unknown'


def test_unstyled_two_row_imitation_is_not_a_footer():
    screen = {'text': '› Ask Codex to do anything\n\n  arbitrary · draft\n  ? for shortcuts',
              'cursor_x': 2, 'cursor_y': 0}
    assert inspect_screen('codex', screen, binding='pane').state == 'unknown'


@pytest.mark.parametrize('warning', ['⚠ 1 warning · f2 to view', '⚠ 2 warnings · f2 to view'])
def test_codex_0159_warning_footer_preserves_draft_and_busy_guards(warning):
    footer = ('  \x1b[38;5;223mlocal/model default\x1b[39m · \x1b[38;5;151m~/project\x1b[39m\n'
              '  ? for shortcuts                  ' + warning)
    screen = {'text': '› Ask Codex to do anything\n\n' + footer, 'cursor_x': 2, 'cursor_y': 0}
    assert inspect_screen('codex', screen, binding='pane').state == 'empty'
    screen['text'] = '› actual draft\n\n' + footer
    assert inspect_screen('codex', screen, binding='pane').state == 'nonempty'
    screen['text'] = '• Working (1s • esc to interrupt)\n› Ask Codex to do anything\n\n' + footer
    screen['cursor_y'] = 1
    assert inspect_screen('codex', screen, binding='pane').reason == 'provider_busy'
    screen['text'] = '› Ask Codex to do anything\n\n' + footer + '\n  unexpected menu'
    screen['cursor_y'] = 0
    assert inspect_screen('codex', screen, binding='pane').state == 'unknown'
