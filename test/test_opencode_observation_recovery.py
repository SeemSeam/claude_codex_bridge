"""Regressions for #402: unreadable snapshots must not consume future replies."""
import json
import sqlite3
from pathlib import Path
from types import SimpleNamespace

import pytest

import opencode_runtime.storage as storage_module
from opencode_runtime.storage import OpenCodeStorageAccessor, OpenCodeObservationUnavailable
from provider_backends.opencode.comm import OpenCodeLogReader


@pytest.fixture
def database(tmp_path):
    db = tmp_path / 'opencode.db'
    with sqlite3.connect(db) as writer:
        writer.executescript('''
            CREATE TABLE session (id TEXT, directory TEXT, time_updated INTEGER);
            CREATE TABLE message (id TEXT, session_id TEXT, time_created INTEGER, time_updated INTEGER, data TEXT);
            CREATE TABLE part (id TEXT, message_id TEXT, session_id TEXT, time_created INTEGER, time_updated INTEGER, data TEXT);
        ''')
        writer.execute('INSERT INTO session VALUES (?, ?, 10)', ('ses_test', str(tmp_path)))
    reader = OpenCodeLogReader(root=tmp_path / 'storage', work_dir=tmp_path,
                               project_id='test', session_id_filter='ses_test')
    reader._force_read_interval = .2
    return db, reader


def add_reply(db, *, text='OK', completed=20):
    with sqlite3.connect(db) as writer:
        writer.execute('INSERT INTO message VALUES (?, ?, 11, 11, ?)',
                       ('msg_user', 'ses_test', json.dumps({'role': 'user', 'summary': {'diffs': ['unused']}})))
        writer.execute('INSERT INTO message VALUES (?, ?, 12, 12, ?)',
                       ('msg_assistant', 'ses_test', json.dumps({'role': 'assistant', 'parentID': 'msg_user',
                                                               'time': {'completed': completed}})))
        for pid, mid, body in [('prt_user', 'msg_user', 'CCB_REQ_ID: job_recover'),
                               ('prt_reply', 'msg_assistant', text)]:
            writer.execute('INSERT INTO part VALUES (?, ?, ?, 13, 13, ?)',
                           (pid, mid, 'ses_test', json.dumps({'type': 'text', 'text': body})))


def test_same_session_timestamp_recovers_after_copy_failure(database, monkeypatch):
    db, reader = database
    state = reader.capture_state()
    add_reply(db)
    original = storage_module._copy_bounded
    calls = []
    def fail(*args, **kwargs):
        calls.append(args)
        raise OpenCodeObservationUnavailable('snapshot_changed')
    monkeypatch.setattr(storage_module, '_copy_bounded', fail)
    reply, failed = reader.try_get_message(state)
    assert reply is None and failed['observation_error'] == 'snapshot_changed'
    assert failed['session_updated'] == state['session_updated'] == 10
    assert failed['last_assistant_id'] == state['last_assistant_id'] is None
    reader.try_get_message(failed)
    assert len(calls) == 1  # Failure backoff does not copy again immediately.
    reader._observation_retry_at = 0
    monkeypatch.setattr(storage_module, '_copy_bounded', original)
    reply, recovered = reader.try_get_message(failed)
    assert reply == 'OK' and recovered['last_assistant_completed'] == 20
    assert recovered['last_assistant_req_id'] == 'job_recover'
    assert 'observation_error' not in recovered
    assert reader.try_get_message(recovered)[0] is None


def test_part_only_completion_update_is_forced_in_nonblocking_poll(database, monkeypatch):
    db, reader = database
    clock = [100.0]
    monkeypatch.setattr(storage_module.time, 'monotonic', lambda: clock[0])
    state = reader.capture_state()
    add_reply(db, text='', completed=None)
    reply, state = reader.try_get_message(state)
    assert reply is None
    with sqlite3.connect(db) as writer:
        writer.execute('UPDATE part SET data=? WHERE id=?', (json.dumps({'type': 'text', 'text': 'FINISHED'}), 'prt_reply'))
        writer.execute('UPDATE message SET data=? WHERE id=?',
                       (json.dumps({'role': 'assistant', 'parentID': 'msg_user', 'time': {'completed': 21}}), 'msg_assistant'))
    assert reader.try_get_message(state)[0] is None
    clock[0] += .3
    reply, state = reader.try_get_message(state)
    assert reply == 'FINISHED' and state['last_assistant_completed'] == 21
    assert state['session_updated'] == 10
    clock[0] += .3
    assert reader.try_get_message(state)[0] is None


def test_each_poll_uses_one_snapshot_and_never_refreshes_undelivered_baseline(database, monkeypatch):
    db, reader = database
    state = reader.capture_state()
    add_reply(db)
    copied = []
    original = storage_module._copy_bounded
    def copy(*args, **kwargs):
        copied.append(args[0])
        original(*args, **kwargs)
    monkeypatch.setattr(storage_module, '_copy_bounded', copy)
    reply, updated = reader.try_get_message(state)
    assert reply == 'OK'
    assert copied == [db]  # session + message + assistant parts + parent parts
    assert updated['last_assistant_id'] == 'msg_assistant'
    assert 'summary' not in reader._read_messages('ses_test')[0]


def test_snapshot_scope_is_consistent_and_releases_connection(database):
    db, reader = database
    store = reader._storage
    with store.observation():
        assert store.fetch_opencode_db_rows('SELECT time_updated FROM session', ())[0][0] == 10
        with sqlite3.connect(db) as writer:
            writer.execute('UPDATE session SET time_updated=99')
        assert store.fetch_opencode_db_rows('SELECT time_updated FROM session', ())[0][0] == 10
    assert store.fetch_opencode_db_rows('SELECT time_updated FROM session', ())[0][0] == 99


def test_byte_budget_is_explicit_and_can_recover(database, monkeypatch):
    db, reader = database
    state = reader.capture_state()
    monkeypatch.setattr(storage_module, 'SNAPSHOT_MAX_BYTES', 1)
    reply, failed = reader.try_get_message(state)
    assert reply is None and failed['observation_error'] == 'snapshot_byte_budget'
    assert failed['session_updated'] == 10
    monkeypatch.setattr(storage_module, 'SNAPSHOT_MAX_BYTES', 256 * 1024 * 1024)
    reader._observation_retry_at = 0
    add_reply(db)
    assert reader.try_get_message(failed)[0] == 'OK'


def test_copy_time_budget_and_corrupt_storage_are_not_empty(database, monkeypatch):
    db, reader = database
    monkeypatch.setattr(storage_module, 'SNAPSHOT_MAX_SECONDS', -1)
    with pytest.raises(OpenCodeObservationUnavailable, match='snapshot_time_budget'):
        reader.capture_state()
    monkeypatch.setattr(storage_module, 'SNAPSHOT_MAX_SECONDS', 2)
    db.write_bytes(b'corrupt fixture')
    with pytest.raises(OpenCodeObservationUnavailable, match='query_failed'):
        reader.capture_state()


def test_large_sparse_file_rejected_before_copy(tmp_path, monkeypatch):
    db = tmp_path / 'opencode.db'
    with db.open('wb') as output:
        output.truncate(257 * 1024 * 1024)
    monkeypatch.setattr(storage_module, '_copy_bounded', lambda *a, **k: pytest.fail('oversized DB was copied'))
    with pytest.raises(OpenCodeObservationUnavailable, match='snapshot_byte_budget'):
        OpenCodeStorageAccessor(tmp_path / 'storage').fetch_opencode_db_rows('SELECT 1', ())


def test_legacy_poll_reports_continuous_observation_failure_then_recovers(monkeypatch):
    from completion.models import CompletionStatus, CompletionSourceKind
    from provider_execution.base import ProviderSubmission
    from provider_backends.opencode.execution_runtime import polling
    state = {'session_id': 'ses_test', 'session_updated': 10, 'observation_error': 'snapshot_changed'}
    reader = SimpleNamespace(try_get_message=lambda old: (None, state))
    monkeypatch.setattr(polling, 'prepare_active_poll', lambda *a, **k: SimpleNamespace(reader=reader))
    submission = ProviderSubmission(job_id='job_test', agent_name='worker', provider='opencode',
                                    source_kind=CompletionSourceKind.SESSION_SNAPSHOT, reply='',
                                    accepted_at='2026-11-07T00:00:00Z', ready_at='2026-11-07T00:00:00Z',
                                    runtime_state={'state': {}})
    first = polling.poll_submission(submission, now='2026-11-07T00:00:00Z', state_session_path_fn=lambda _: '')
    assert first.decision is None
    failed = polling.poll_submission(first.submission, now='2026-11-07T00:00:31Z', state_session_path_fn=lambda _: '')
    assert failed.decision.status is CompletionStatus.INCOMPLETE
    assert failed.decision.diagnostics['provider_may_still_be_running']
    state.pop('observation_error')
    recovered = polling.poll_submission(first.submission, now='2026-11-07T00:00:10Z', state_session_path_fn=lambda _: '')
    assert 'observation_failure_since' not in recovered.submission.runtime_state
    assert 'observation_error' not in recovered.submission.diagnostics


def test_unchanged_database_skips_repeat_copies_but_revalidates_on_schedule(database, monkeypatch):
    db, reader = database
    clock = [100.0]
    monkeypatch.setattr(storage_module.time, 'monotonic', lambda: clock[0])
    state = reader.capture_state()
    copies = []
    original = storage_module._copy_bounded
    def copy(*args, **kwargs):
        copies.append(args[0])
        original(*args, **kwargs)
    monkeypatch.setattr(storage_module, '_copy_bounded', copy)
    _, state = reader.try_get_message(state)
    for _ in range(5):
        _, state = reader.try_get_message(state)
    assert copies == [db]
    clock[0] += .3
    _, state = reader.try_get_message(state)
    assert copies == [db, db]


def test_empty_read_never_performs_second_baseline_refresh(monkeypatch):
    from provider_backends.opencode.runtime.reply_polling_runtime.loop import read_since
    reader = SimpleNamespace(_force_read_interval=.2, _poll_interval=0)
    refreshed = []
    reply, state = read_since(reader, {}, 0, False,
        get_latest_session_fn=lambda _: {'payload': {'id': 'ses_test', 'time': {'updated': 10}}},
        find_reply_fn=lambda *args: (None, None),
        merge_reply_state_fn=lambda state, **kwargs: {**state, 'session_updated': 10},
        refresh_observed_state_fn=lambda *args, **kwargs: refreshed.append('undelivered reply absorbed'),
        reset_state_for_session_fn=lambda state, sid: state,
        session_updated_fn=lambda payload: payload['time']['updated'])
    assert reply is None and state['session_updated'] == 10 and not refreshed


def test_recovered_idle_read_persists_failure_timer_reset_without_new_items(monkeypatch):
    from completion.models import CompletionSourceKind
    from provider_execution.base import ProviderSubmission
    from provider_backends.opencode.execution_runtime import polling
    good = {'session_id': 'ses_test', 'session_updated': 10}
    reader = SimpleNamespace(try_get_message=lambda old: (None, dict(good)))
    monkeypatch.setattr(polling, 'prepare_active_poll', lambda *a, **k: SimpleNamespace(reader=reader))
    submission = ProviderSubmission(job_id='job_test', agent_name='worker', provider='opencode',
        source_kind=CompletionSourceKind.SESSION_SNAPSHOT, reply='',
        accepted_at='2026-11-07T00:00:00Z', ready_at='2026-11-07T00:00:00Z',
        diagnostics={'observation_error': 'snapshot_changed', 'observation_failure_since': '2026-11-07T00:00:00Z'},
        runtime_state={'state': dict(good),
                       'anchor_emitted': True, 'observation_failure_since': '2026-11-07T00:00:00Z',
                       'next_seq': 1, 'reply_buffer': '', 'session_path': ''})
    result = polling.poll_submission(submission, now='2026-11-07T00:00:10Z', state_session_path_fn=lambda _: '')
    assert result is not None and not result.items and result.decision is None
    assert 'observation_failure_since' not in result.submission.runtime_state
    assert 'observation_error' not in result.submission.diagnostics
    assert polling.poll_submission(result.submission, now='2026-11-07T00:00:11Z', state_session_path_fn=lambda _: '') is None
