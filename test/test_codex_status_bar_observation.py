"""Native 0.156.1 status bars must not strand an empty Codex composer."""
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from provider_execution.draft_guard import allow_submission_send
from provider_execution.draft_observation import inspect_screen

CAPTURES = json.loads((Path(__file__).parent / 'fixtures/composer/codex-status-01561.json').read_text())


@pytest.mark.parametrize('capture', CAPTURES, ids=lambda c: c['case'])
def test_native_status_bar_releases_empty_guard(capture):
    assert inspect_screen('codex', capture, binding='pane').state == 'empty'
    backend = SimpleNamespace(capture_composer=lambda _: dict(capture, binding='pane'))
    state = {'draft_guard_enabled': True, 'backend': backend, 'pane_id': '%1'}
    assert allow_submission_send('codex', state)
    assert state['draft_guard_pending'] is False


def screen(content='\x1b[2mAsk Codex to do anything\x1b[0m', footer='unrelated model · /tmp/project', x=2, y=0):
    return {'text': f'› {content}\n\n  {footer}', 'cursor_x': x, 'cursor_y': y}


@pytest.mark.parametrize('footer', ['unrelated model · /tmp/project', '未来模型', 'Context 100% left'])
def test_native_ghost_not_model_or_separator_authorizes_empty(footer):
    assert inspect_screen('codex', screen(footer=footer), binding='pane').state == 'empty'


@pytest.mark.parametrize('content,x,y', [
    ('\x1b[2mAsk Codex to do anything\x1b[0m\n  user draft', 2, 0),
    ('\x1b[2mAsk Codex to do anything\x1b[0m', 3, 0),
    ('\x1b[2mAsk Codex to do anything\x1b[0m', 2, 1),
    ('user draft · /tmp/project', 2, 0),
])
def test_opaque_footer_does_not_authorize_draft_or_wrong_cursor(content, x, y):
    assert inspect_screen('codex', screen(content=content, x=x, y=y), binding='pane').state == 'unknown'


@pytest.mark.parametrize('footer', ['unrelated model · /tmp/project', 'gpt-6-astra', 'GPT-6-Astra', 'o3', 'draft · text', '未来模型'])
def test_nondim_native_placeholder_releases_empty_regardless_of_footer_label(footer):
    # Newer Codex builds stop dimming the placeholder text and may reword it.
    # A known placeholder at the initial cursor with an empty composer region
    # below is empty evidence on its own; the footer label stays opaque.
    assert inspect_screen('codex', screen(footer=footer), binding='pane').state == 'empty'


@pytest.mark.parametrize('footer', ['Context 0% left', 'Context 42% left', 'Context 100% left'])
def test_context_only_status_keeps_human_draft_nonempty(footer):
    assert inspect_screen('codex', screen(content='human draft', footer=footer), binding='pane').state == 'nonempty'


@pytest.mark.parametrize('footer', ['Context 101% left', 'Context 42% left user prose', 'Context -1% left'])
def test_context_status_requires_exact_native_format(footer):
    assert inspect_screen('codex', screen(content='human draft', footer=footer), binding='pane').state == 'unknown'


def test_busy_native_ghost_still_blocks():
    capture = screen()
    capture['text'] = '• Working (1s • esc to interrupt)\n' + capture['text']
    capture['cursor_y'] = 1
    assert inspect_screen('codex', capture, binding='pane').reason == 'provider_busy'


@pytest.mark.parametrize('mode', ['INSERT', 'NORMAL', 'VISUAL'])
def test_native_ghost_does_not_bypass_editor_mode(mode):
    assert inspect_screen('codex', screen(footer=mode), binding='pane').state == 'unknown'


def test_missing_blank_separator_still_blocks():
    capture = screen()
    capture['text'] = capture['text'].replace('\n\n', '\n')
    assert inspect_screen('codex', capture, binding='pane').state == 'unknown'


@pytest.mark.parametrize('capture', CAPTURES, ids=lambda c: c['case'])
def test_deferred_sender_releases_native_empty_once_without_clearing(capture):
    from completion.models import CompletionSourceKind
    from provider_execution.base import ProviderSubmission
    from provider_backends.codex.execution_runtime.start import dispatch_guarded_prompt

    current = [screen(content='human draft')]
    sent, keys = [], []
    backend = SimpleNamespace(
        capture_composer=lambda _: dict(current[0], binding='pane'),
        send_text_to_pane=lambda pane, text: sent.append(text),
        send_key=lambda pane, key: keys.append(key),
    )
    now = '2026-10-05T16:00:00Z'
    submission = ProviderSubmission(
        'job-status', 'codex', 'codex', now, now, CompletionSourceKind.SESSION_EVENT_LOG, '',
        runtime_state={'mode': 'active', 'draft_guard_enabled': True,
                       'backend': backend, 'pane_id': '%1', 'prompt_sent': False,
                       'pending_prompt': 'CCB_TASK',
                       'reader': SimpleNamespace(capture_state=lambda: {'cursor': 'fresh'})},
    )
    waiting = dispatch_guarded_prompt(submission, now=now)
    assert not waiting.runtime_state['prompt_sent']
    assert not sent and not keys
    current[0] = capture
    released = dispatch_guarded_prompt(waiting, now=now)
    assert released.runtime_state['prompt_sent']
    assert released.runtime_state['state'] == {'cursor': 'fresh'}
    assert 'pending_prompt' not in released.runtime_state
    dispatch_guarded_prompt(released, now=now)
    assert sent == ['CCB_TASK']
    assert not keys
