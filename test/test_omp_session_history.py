from __future__ import annotations

import json
import os
import shlex
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest

from provider_backends.omp import launcher
from provider_backends.omp.session import OmpProjectSession, resume_binding_for_launch


def fixture(tmp_path):
    root = tmp_path / "managed sessions"
    root.mkdir()
    native = root / "native-old.jsonl"
    native.write_text(json.dumps({"type": "session", "id": "native-old", "cwd": str(tmp_path)}) + "\n")
    events = tmp_path / "events.jsonl"
    event = dict(schema_version=1, type="extension_ready", actor="demo",
                 launch_session_id="ccb-old", runtime_instance_id="instance-old",
                 omp_session_id="native-old", omp_session_path=str(native))
    events.write_text(json.dumps(event) + "\n")
    data = dict(agent_name="demo", ccb_project_id="project", work_dir=str(tmp_path),
                ccb_session_id="ccb-old", omp_session_id="ccb-old",
                omp_session_dir=str(root), omp_completion_event_log=str(events))
    record = tmp_path / ".omp-demo-session"
    record.write_text(json.dumps(data))
    return root, native, events, event, data, record


def resolve(tmp_path, root, record):
    return resume_binding_for_launch(record, agent_name="demo", project_id="project",
                                    work_dir=tmp_path, session_dir=root)


def test_legacy_launch_resumes_observed_session_not_newest(tmp_path):
    root, native, events, event, data, record = fixture(tmp_path)
    newer = root / "native-new.jsonl"
    newer.write_text(json.dumps({"type": "session", "id": "native-new", "cwd": str(tmp_path)}) + "\n")
    binding = resolve(tmp_path, root, record)
    assert binding["omp_resume_session_path"] == str(native)
    assert binding["omp_resume_session_id"] == "native-old"
    assert binding["omp_resume_status"] == "exact_session_ready"


@pytest.mark.parametrize("field,value", [("actor", "other"), ("launch_session_id", "ccb-stale"),
                                         ("runtime_instance_id", ""), ("schema_version", 2)])
def test_foreign_or_invalid_observations_never_restore(tmp_path, field, value):
    root, native, events, event, data, record = fixture(tmp_path)
    event[field] = value
    events.write_text(json.dumps(event) + "\n")
    assert resolve(tmp_path, root, record)["omp_resume_status"] == "fresh_no_current_session_observation"


@pytest.mark.parametrize("damage", ["missing", "cwd", "id", "corrupt", "symlink", "outside"])
def test_invalid_transcript_never_resumes(tmp_path, damage):
    root, native, events, event, data, record = fixture(tmp_path)
    if damage == "missing":
        native.unlink()
    elif damage == "cwd":
        native.write_text(json.dumps({"type": "session", "id": "native-old", "cwd": "/wrong"}))
    elif damage == "id":
        native.write_text(json.dumps({"type": "session", "id": "wrong", "cwd": str(tmp_path)}))
    elif damage == "corrupt":
        native.write_text("broken")
    else:
        outside = tmp_path / "native-old.jsonl"
        native.rename(outside)
        if damage == "symlink":
            native.symlink_to(outside)
        else:
            event["omp_session_path"] = str(outside)
            events.write_text(json.dumps(event) + "\n")
    assert resolve(tmp_path, root, record)["omp_resume_status"].startswith("fresh_")


def test_session_switch_overrides_previous_binding_even_when_empty(tmp_path):
    root, native, events, event, data, record = fixture(tmp_path)
    switched = dict(event, type="native_session", omp_session_id="new-empty", omp_session_path="")
    events.write_text(json.dumps(event) + "\n" + json.dumps(switched) + "\n")
    assert resolve(tmp_path, root, record)["omp_resume_status"] == "fresh_no_observed_native_session"


@pytest.mark.parametrize("field,value", [("agent_name", "other"), ("ccb_project_id", "other"),
                                         ("work_dir", "/wrong"), ("active", False)])
def test_binding_owner_checked(tmp_path, field, value):
    root, native, events, event, data, record = fixture(tmp_path)
    data[field] = value
    record.write_text(json.dumps(data))
    assert resolve(tmp_path, root, record)["omp_resume_status"].startswith("fresh_")


def test_restart_command_resolves_event_and_quotes_path(tmp_path):
    root, native, events, event, data, record = fixture(tmp_path)
    data.update(start_cmd="omp", omp_restart_start_cmd_template=f"omp {launcher.OMP_RESTART_SESSION_MARKER}")
    session = OmpProjectSession(record, data)
    assert shlex.split(session.start_cmd) == ["omp", "--resume", str(native)]
    data["omp_restore_enabled"] = False
    assert shlex.split(session.start_cmd) == ["omp"]
    data["omp_explicit_session_control"] = True
    data["start_cmd"] = "omp --resume user-choice"
    assert session.start_cmd == data["start_cmd"]


@pytest.mark.parametrize("restore,explicit", [(True, False), (False, False), (True, True)])
def test_launcher_restore_policy_and_native_payload(monkeypatch, tmp_path, restore, explicit):
    root, native, events, event, data, record = fixture(tmp_path)
    prepared = resolve(tmp_path, root, record)
    monkeypatch.setattr(launcher, "_materialize_completion_extension", lambda *a, **kw: None)
    monkeypatch.setattr(launcher, "provider_start_parts", lambda _: ("omp",))
    monkeypatch.setattr(launcher, "build_native_start_cmd", lambda *a, **kw:
                        "omp --resume manual" if explicit else f"omp {launcher.OMP_RESTART_SESSION_MARKER}")
    spec = SimpleNamespace(startup_args=("--resume", "manual") if explicit else ())
    command = launcher._build_start_cmd(launcher._launch_config(), SimpleNamespace(restore=restore),
                                       spec, tmp_path, "ccb-new", prepared_state=prepared)
    assert shlex.split(command) == (["omp", "--resume", "manual"] if explicit else
                                  ["omp", "--resume", str(native)] if restore else ["omp"])
    monkeypatch.setattr(launcher, "build_native_session_payload", lambda *a, **kw: {"ccb_session_id": "ccb-new", "omp_session_id": "ccb-new"})
    prepared["omp_session_dir"] = str(root)
    payload = launcher._build_session_payload(launcher._launch_config(), None, spec, None,
                                             tmp_path, tmp_path, "%1", "marker", command, "ccb-new", prepared)
    assert payload["ccb_session_id"] == "ccb-new"
    if restore and not explicit:
        assert payload["omp_session_id"] == "native-old"
        assert payload["omp_session_path"] == str(native)
    else:
        assert "omp_session_id" not in payload


def test_extension_observes_unmanaged_input_switch_and_turn_end():
    source = launcher._omp_completion_extension_source()
    assert 'pi.on("session_switch"' in source
    assert 'pi.on("input", async (event: any, ctx: any)' in source
    assert 'pi.on("turn_end", async (event: any, ctx: any)' in source
    assert source.count("observeNativeSession(ctx);") == 3
    assert 'appendEvent("native_session"' in source
    assert "pi_session_id" not in source


def test_prepare_launch_reads_previous_binding_before_new_sidecar(monkeypatch, tmp_path):
    root, native, events, event, data, record = fixture(tmp_path)
    monkeypatch.setattr(launcher, "prepare_native_launch_context", lambda *a, **kw: {
        "workspace_path": str(tmp_path), "omp_session_dir": str(root),
    })
    context = SimpleNamespace(paths=SimpleNamespace(ccb_dir=tmp_path), project=SimpleNamespace(project_id="project"))
    prepared = launcher.prepare_launch_context(context, SimpleNamespace(name="demo"),
                                               SimpleNamespace(workspace_path=tmp_path), tmp_path / "runtime", {})
    assert prepared["omp_resume_session_path"] == str(native)
    launcher._materialize_completion_extension(prepared, runtime_dir=tmp_path / "runtime", launch_session_id="ccb-new")
    assert prepared["omp_completion_event_log"] != str(events)
    assert prepared["omp_resume_session_id"] == "native-old"


def test_missing_sidecar_does_not_silently_restore_stale_binding(tmp_path):
    root, native, events, event, data, record = fixture(tmp_path)
    data.update(omp_session_id="native-old", omp_session_path=str(native))
    record.write_text(json.dumps(data))
    events.unlink()
    assert resolve(tmp_path, root, record)["omp_resume_status"] == "fresh_observation_unavailable"


def test_switch_to_another_valid_session_selects_switched_identity(tmp_path):
    root, native, events, event, data, record = fixture(tmp_path)
    switched_path = root / "switched.jsonl"
    switched_path.write_text(json.dumps({"type": "session", "id": "switched", "cwd": str(tmp_path)}))
    event2 = dict(event, type="native_session", omp_session_id="switched", omp_session_path=str(switched_path))
    events.write_text(json.dumps(event) + "\n" + json.dumps(event2) + "\n")
    assert resolve(tmp_path, root, record)["omp_resume_session_id"] == "switched"


@pytest.mark.skipif(not os.environ.get("CCB_OMP_NATIVE_SMOKE"), reason="opt-in isolated real OMP")
def test_real_omp_resumes_history_and_reports_native_identity(tmp_path):
    root = tmp_path / "sessions"
    root.mkdir()
    native_id = "019fdd2b-8362-7958-9e85-d0a5eed17084"
    native = root / f"{native_id}.jsonl"
    stamp = "2026-09-19T00:00:00.000Z"
    native.write_text("\n".join(json.dumps(row) for row in [
        dict(type="session", version=3, id=native_id, timestamp=stamp, cwd=str(tmp_path)),
        dict(type="message", id="user0001", parentId=None, timestamp=stamp,
             message=dict(role="user", content=[dict(type="text", text="CCB_HISTORY_CANARY_74")], timestamp=1789776000000)),
    ]) + "\n")
    home = tmp_path / "isolated-home"
    home.mkdir()
    agent_dir = home / ".omp/agent"
    agent_dir.mkdir(parents=True)
    # A local-only dummy model lets RPC inspect history without credentials.
    (agent_dir / "models.yml").write_text(json.dumps({"providers": {"ccb-smoke": {
        "baseUrl": "http://127.0.0.1:1/v1", "api": "openai-completions",
        "apiKey": "test-placeholder", "models": [{"id": "history-only", "name": "History only",
        "reasoning": False, "input": ["text"], "contextWindow": 32000, "maxTokens": 1000,
        "cost": {"input": 0, "output": 0, "cacheRead": 0, "cacheWrite": 0}}]}}}))
    prepared = {"omp_state_dir": str(tmp_path / "state"), "omp_home": str(home)}
    launcher._materialize_completion_extension(prepared, runtime_dir=tmp_path / "runtime", launch_session_id="ccb-smoke")
    env = {"PATH": os.environ["PATH"], "HOME": str(home), "TERM": "dumb",
           "XDG_CONFIG_HOME": str(home / ".config"), "XDG_DATA_HOME": str(home / ".local/share"),
           "XDG_CACHE_HOME": str(home / ".cache"), "XDG_STATE_HOME": str(home / ".local/state"),
           "PI_CODING_AGENT_DIR": str(home / ".omp/agent"), "PI_CODING_AGENT_SESSION_DIR": str(root),
           "CCB_CALLER_ACTOR": "demo", "CCB_SESSION_ID": "ccb-smoke",
           "CCB_OMP_COMPLETION_EVENTS": prepared["omp_completion_event_log"],
           "CCB_OMP_DISPATCH_EVENTS": prepared["omp_dispatch_event_log"]}
    command = shlex.split(launcher.render_restart_command(
        f"{shlex.quote(os.environ['CCB_OMP_NATIVE_SMOKE'])} {launcher.OMP_RESTART_SESSION_MARKER}", native))
    command += ["--provider", "ccb-smoke", "--model", "history-only", "--mode", "rpc", "--session-dir", str(root), "--no-lsp", "--no-skills", "--no-rules",
                "--extension", prepared["omp_completion_extension"]]
    result = subprocess.run(command, cwd=tmp_path, env=env, input=(
        '{"id":"state","type":"get_state"}\n{"id":"messages","type":"get_messages"}\n'),
        text=True, capture_output=True, timeout=35)
    assert result.returncode == 0, result.stderr[-1500:]
    responses = {}
    for line in result.stdout.splitlines():
        try:
            row = json.loads(line)
        except ValueError:
            continue
        if isinstance(row, dict) and row.get("id") in {"state", "messages"}:
            responses[row["id"]] = row
    assert responses["state"]["data"]["sessionId"] == native_id
    assert "CCB_HISTORY_CANARY_74" in json.dumps(responses["messages"]["data"])
    events = [json.loads(line) for line in Path(prepared["omp_completion_event_log"]).read_text().splitlines()]
    ready = next(row for row in events if row["type"] == "extension_ready")
    assert ready["omp_session_id"] == native_id
    assert ready["omp_session_path"] == str(native)
    record = tmp_path / ".omp-demo-session"
    record.write_text(json.dumps(dict(agent_name="demo", ccb_project_id="project", work_dir=str(tmp_path),
                                     ccb_session_id="ccb-smoke", omp_completion_event_log=prepared["omp_completion_event_log"])))
    assert resolve(tmp_path, root, record)["omp_resume_session_id"] == native_id
