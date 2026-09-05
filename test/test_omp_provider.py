from __future__ import annotations

from pathlib import Path
import sqlite3
import stat
from types import SimpleNamespace

import pytest

from ccbd.api_models import DeliveryScope, JobRecord, JobStatus, MessageEnvelope
from provider_backends.native_cli_support import NativeCliExecutionRequest
from provider_backends.native_cli_support.home import materialize_native_login_state
from provider_backends.omp.launcher import (
    _materialize_completion_extension,
    _omp_completion_extension_source,
    _omp_visible_args,
    _omp_visible_env,
)
from provider_backends.omp.execution import _build_command, _build_env


def _request(tmp_path: Path) -> NativeCliExecutionRequest:
    job = JobRecord(
        job_id="job_omp_contract",
        submission_id="sub_omp",
        agent_name="omp1",
        provider="omp",
        request=MessageEnvelope(
            project_id="project",
            to_agent="omp1",
            from_actor="main",
            body="Reply with exactly: READY",
            task_id=None,
            reply_to=None,
            message_type="ask",
            delivery_scope=DeliveryScope.SINGLE,
        ),
        status=JobStatus.RUNNING,
        terminal_decision=None,
        cancel_requested_at=None,
        created_at="2026-07-14T00:00:00Z",
        updated_at="2026-07-14T00:00:00Z",
        workspace_path=str(tmp_path),
    )
    return NativeCliExecutionRequest(
        provider="omp",
        job=job,
        work_dir=tmp_path,
        prompt="Reply with exactly: READY",
        session_data={
            "omp_state_dir": str(tmp_path / ".ccb" / "omp"),
            "omp_home": str(tmp_path / ".ccb" / "omp" / "home"),
        },
        request_anchor="req_omp_contract",
    )


def test_omp_command_uses_supported_structured_cli_contract(tmp_path: Path) -> None:
    command = _build_command(_request(tmp_path))

    assert command[1:] == [
        "--mode",
        "json",
        "--session-dir",
        str(tmp_path / ".ccb" / "omp" / "sessions"),
        "--approval-mode",
        "yolo",
        "--print",
        "Reply with exactly: READY",
    ]
    assert "--name" not in command
    assert "--no-approve" not in command


def test_omp_visible_launch_uses_provider_state_session_dir(tmp_path: Path) -> None:
    prepared = {
        "omp_state_dir": str(tmp_path / "provider-state"),
        "omp_home": str(tmp_path / "provider-state" / "home"),
    }
    runtime_dir = tmp_path / "provider-runtime"
    _materialize_completion_extension(
        prepared,
        runtime_dir=runtime_dir,
        launch_session_id="ccb-omp-launch",
    )

    assert _omp_visible_args(prepared) == (
        "--session-dir",
        str(tmp_path / "provider-state" / "sessions"),
        "--extension",
        str(runtime_dir / "completion" / "ccb-omp-completion.ts"),
        "--approval-mode",
        "yolo",
    )
    assert _omp_visible_env(prepared) == {
        "PI_CODING_AGENT_DIR": str(
            tmp_path / "provider-state" / "home" / ".omp" / "agent"
        ),
        "PI_CODING_AGENT_SESSION_DIR": str(
            tmp_path / "provider-state" / "sessions"
        ),
        "CCB_OMP_COMPLETION_EVENTS": str(
            prepared["omp_completion_event_log"]
        ),
        "CCB_OMP_DISPATCH_EVENTS": str(prepared["omp_dispatch_event_log"]),
    }
    for key in (
        "omp_completion_extension",
        "omp_completion_event_log",
        "omp_dispatch_event_log",
    ):
        assert Path(str(prepared[key])).stat().st_mode & 0o777 == 0o600


def test_omp_extension_normalizes_only_final_agent_end_to_settled() -> None:
    source = _omp_completion_extension_source()

    assert 'pi.on("agent_settled"' not in source
    assert "event?.willContinue === true" in source
    assert "if (!willContinue)" in source
    assert 'appendEvent("agent_settled"' in source
    assert "CCB_OMP_COMPLETION_EVENTS" in source
    assert "CCB_OMP_DISPATCH_EVENTS" in source


def test_omp_headless_launch_uses_same_private_agent_and_session_roots(
    tmp_path: Path,
) -> None:
    request = _request(tmp_path)

    assert _build_env(request) == {
        "PI_CODING_AGENT_DIR": str(
            tmp_path / ".ccb" / "omp" / "home" / ".omp" / "agent"
        ),
        "PI_CODING_AGENT_SESSION_DIR": str(
            tmp_path / ".ccb" / "omp" / "sessions"
        ),
    }


def test_omp_projects_current_and_legacy_config_files_one_way(tmp_path: Path) -> None:
    source_home = tmp_path / "source-home"
    target_home = tmp_path / "managed-home"
    source_agent = source_home / ".omp" / "agent"
    source_agent.mkdir(parents=True)
    fixtures = {
        "config.yml": "modelRoles:\n  default: custom/model\n",
        "config.yaml": "theme:\n  dark: true\n",
        "models.yml": "providers:\n  custom:\n    baseUrl: https://example.test\n",
        "models.yaml": "modelOverrides:\n  custom/model: {}\n",
        "models.json": '{"providers":{"legacy":{}}}\n',
        "settings.json": '{"theme":"legacy"}\n',
    }
    for filename, content in fixtures.items():
        (source_agent / filename).write_text(content, encoding="utf-8")

    materialize_native_login_state("omp", target_home, source_home=source_home)

    target_agent = target_home / ".omp" / "agent"
    for filename, content in fixtures.items():
        target = target_agent / filename
        assert target.read_text(encoding="utf-8") == content
        assert not target.is_symlink()
    (target_agent / "models.yml").write_text("managed: true\n", encoding="utf-8")
    assert (source_agent / "models.yml").read_text(encoding="utf-8") == fixtures[
        "models.yml"
    ]


def test_omp_projects_required_ccb_skills_when_optional_inheritance_is_disabled(
    tmp_path: Path,
) -> None:
    source_home = tmp_path / "source-home"
    target_home = tmp_path / "managed-home"

    materialize_native_login_state(
        "omp",
        target_home,
        source_home=source_home,
        profile=SimpleNamespace(
            inherit_auth=False,
            inherit_config=False,
            inherit_skills=False,
        ),
    )

    skills_root = target_home / ".omp" / "agent" / "skills"
    for skill_name in ("ask", "ccb-clear", "ccb-compact", "ccb-diagnose"):
        skill_file = skills_root / skill_name / "SKILL.md"
        marker = skills_root / f"{skill_name}.ccb-projection.json"
        assert skill_file.is_file()
        assert marker.is_file()
        assert f"name: {skill_name}" in skill_file.read_text(encoding="utf-8")


def test_omp_auth_capable_config_requires_both_inheritance_gates(tmp_path: Path) -> None:
    source_home = tmp_path / "source-home"
    source_agent = source_home / ".omp" / "agent"
    source_agent.mkdir(parents=True)
    (source_agent / "config.yml").write_text("theme:\n  dark: true\n", encoding="utf-8")
    with sqlite3.connect(source_agent / "agent.db") as connection:
        connection.executescript(
            """
            CREATE TABLE auth_credentials (
                id INTEGER PRIMARY KEY,
                provider TEXT NOT NULL,
                credential_type TEXT NOT NULL,
                data TEXT NOT NULL
            );
            INSERT INTO auth_credentials VALUES (1, 'custom', 'api_key', '{"key":"source"}');
            """
        )

    config_without_auth = tmp_path / "config-without-auth"
    materialize_native_login_state(
        "omp",
        config_without_auth,
        source_home=source_home,
        profile=SimpleNamespace(inherit_auth=False, inherit_config=True),
    )
    assert not (config_without_auth / ".omp" / "agent" / "config.yml").exists()
    assert not (config_without_auth / ".omp" / "agent" / "agent.db").exists()

    auth_without_config = tmp_path / "auth-without-config"
    materialize_native_login_state(
        "omp",
        auth_without_config,
        source_home=source_home,
        profile=SimpleNamespace(inherit_auth=True, inherit_config=False),
    )
    assert (auth_without_config / ".omp" / "agent" / "agent.db").is_file()
    assert not (auth_without_config / ".omp" / "agent" / "config.yml").exists()


def test_omp_auth_projection_snapshots_wal_and_drops_non_auth_state(
    tmp_path: Path,
) -> None:
    source_home = tmp_path / "source-home"
    target_home = tmp_path / "managed-home"
    source_agent = source_home / ".omp" / "agent"
    target_agent = target_home / ".omp" / "agent"
    source_agent.mkdir(parents=True)
    target_agent.mkdir(parents=True)
    source_database = source_agent / "agent.db"
    source_connection = sqlite3.connect(source_database)
    try:
        assert source_connection.execute("PRAGMA journal_mode=WAL").fetchone() == ("wal",)
        source_connection.execute("PRAGMA wal_autocheckpoint=0")
        source_connection.executescript(
            """
            CREATE TABLE auth_schema_version (id INTEGER PRIMARY KEY, version INTEGER NOT NULL);
            CREATE TABLE schema_version (version INTEGER PRIMARY KEY);
            CREATE TABLE auth_credentials (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                provider TEXT NOT NULL,
                credential_type TEXT NOT NULL,
                data TEXT NOT NULL,
                disabled_cause TEXT,
                identity_key TEXT,
                created_at INTEGER NOT NULL,
                updated_at INTEGER NOT NULL
            );
            CREATE TABLE auth_credential_blocks (
                credential_id INTEGER,
                provider_key TEXT,
                block_scope TEXT,
                blocked_until_ms INTEGER,
                updated_at INTEGER
            );
            CREATE TABLE auth_credential_refresh_leases (
                credential_id INTEGER PRIMARY KEY,
                owner TEXT,
                expires_at_ms INTEGER,
                updated_at INTEGER
            );
            CREATE TABLE settings (key TEXT PRIMARY KEY, value TEXT NOT NULL);
            CREATE TABLE cache (key TEXT PRIMARY KEY, value TEXT NOT NULL, expires_at INTEGER);
            CREATE TABLE model_usage (model_key TEXT PRIMARY KEY, last_used_at INTEGER);
            INSERT INTO auth_schema_version VALUES (1, 4);
            INSERT INTO schema_version VALUES (6);
            INSERT INTO auth_credentials
                (provider, credential_type, data, created_at, updated_at)
                VALUES ('custom', 'api_key', '{"key":"source-auth"}', 1, 1);
            INSERT INTO auth_credential_blocks VALUES (1, 'custom', '', 999999, 1);
            INSERT INTO auth_credential_refresh_leases VALUES (1, 'global-owner', 999999, 1);
            INSERT INTO settings VALUES ('private-history', 'source-private-history');
            INSERT INTO cache VALUES ('mcp_tools:private', 'source-private-cache', 999999);
            INSERT INTO model_usage VALUES ('custom/model', 1);
            """
        )
        source_connection.commit()
        source_wal = source_database.with_name("agent.db-wal")
        assert source_wal.is_file()
        source_fingerprints = {
            path: (
                path.read_bytes(),
                stat.S_IMODE(path.stat().st_mode),
                path.stat().st_mtime_ns,
            )
            for path in (source_database, source_wal)
        }
        (target_agent / "agent.db-wal").write_bytes(b"stale-wal")
        (target_agent / "agent.db-shm").write_bytes(b"stale-shm")

        materialize_native_login_state("omp", target_home, source_home=source_home)

        target_database = target_agent / "agent.db"
        assert target_database.is_file() and not target_database.is_symlink()
        assert stat.S_IMODE(target_database.stat().st_mode) == 0o600
        assert not (target_agent / "agent.db-wal").exists()
        assert not (target_agent / "agent.db-shm").exists()
        assert b"source-private-history" not in target_database.read_bytes()
        assert b"source-private-cache" not in target_database.read_bytes()
        with sqlite3.connect(target_database) as target_connection:
            assert target_connection.execute(
                "SELECT provider, credential_type, data FROM auth_credentials"
            ).fetchone() == ("custom", "api_key", '{"key":"source-auth"}')
            target_tables = {
                str(row[0])
                for row in target_connection.execute(
                    "SELECT name FROM sqlite_master WHERE type = 'table'"
                )
            }
            for table in (
                "auth_credential_blocks",
                "auth_credential_refresh_leases",
                "settings",
                "cache",
                "model_usage",
            ):
                assert table not in target_tables
            target_connection.execute("DELETE FROM auth_credentials")
            target_connection.commit()
        assert source_connection.execute(
            "SELECT data FROM auth_credentials"
        ).fetchone() == ('{"key":"source-auth"}',)
        assert source_connection.execute("SELECT value FROM settings").fetchone() == (
            "source-private-history",
        )
        for path, fingerprint in source_fingerprints.items():
            assert (
                path.read_bytes(),
                stat.S_IMODE(path.stat().st_mode),
                path.stat().st_mtime_ns,
            ) == fingerprint
    finally:
        source_connection.close()


def test_omp_auth_projection_rejects_source_database_symlink(tmp_path: Path) -> None:
    source_home = tmp_path / "source-home"
    source_agent = source_home / ".omp" / "agent"
    source_agent.mkdir(parents=True)
    external_database = tmp_path / "external.db"
    with sqlite3.connect(external_database) as connection:
        connection.execute("CREATE TABLE auth_credentials (id INTEGER PRIMARY KEY)")
    try:
        (source_agent / "agent.db").symlink_to(external_database)
    except OSError as exc:
        pytest.skip(f"filesystem does not support symlinks: {exc}")
    target_database = tmp_path / "managed-home" / ".omp" / "agent" / "agent.db"
    target_database.parent.mkdir(parents=True)
    target_database.write_bytes(b"managed-private-state")

    materialize_native_login_state(
        "omp",
        tmp_path / "managed-home",
        source_home=source_home,
    )

    assert target_database.read_bytes() == b"managed-private-state"


def test_omp_auth_projection_blocks_malformed_source_database(tmp_path: Path) -> None:
    source_home = tmp_path / "source-home"
    source_database = source_home / ".omp" / "agent" / "agent.db"
    source_database.parent.mkdir(parents=True)
    with sqlite3.connect(source_database) as connection:
        connection.execute("CREATE TABLE settings (key TEXT PRIMARY KEY, value TEXT)")
    target_database = tmp_path / "managed-home" / ".omp" / "agent" / "agent.db"
    target_database.parent.mkdir(parents=True)
    target_database.write_bytes(b"managed-private-state")

    with pytest.raises(RuntimeError, match="no auth_credentials table"):
        materialize_native_login_state(
            "omp",
            tmp_path / "managed-home",
            source_home=source_home,
        )

    assert target_database.read_bytes() == b"managed-private-state"


@pytest.mark.parametrize("alias_kind", ("symlink", "hardlink"))
def test_omp_auth_projection_detaches_managed_database_alias(
    tmp_path: Path,
    alias_kind: str,
) -> None:
    source_home = tmp_path / "source-home"
    source_agent = source_home / ".omp" / "agent"
    source_agent.mkdir(parents=True)
    with sqlite3.connect(source_agent / "agent.db") as connection:
        connection.executescript(
            """
            CREATE TABLE auth_credentials (
                id INTEGER PRIMARY KEY,
                provider TEXT NOT NULL,
                credential_type TEXT NOT NULL,
                data TEXT NOT NULL
            );
            INSERT INTO auth_credentials VALUES (1, 'custom', 'api_key', '{"key":"source"}');
            """
        )
    target_database = tmp_path / "managed-home" / ".omp" / "agent" / "agent.db"
    target_database.parent.mkdir(parents=True)
    outside = tmp_path / "outside.db"
    outside.write_bytes(b"outside-private-state")
    try:
        if alias_kind == "symlink":
            target_database.symlink_to(outside)
        else:
            target_database.hardlink_to(outside)
    except OSError as exc:
        pytest.skip(f"filesystem does not support {alias_kind}: {exc}")

    materialize_native_login_state(
        "omp",
        tmp_path / "managed-home",
        source_home=source_home,
    )

    assert target_database.is_file() and not target_database.is_symlink()
    assert outside.read_bytes() == b"outside-private-state"
    assert target_database.stat().st_ino != outside.stat().st_ino


@pytest.mark.parametrize("alias_kind", ("symlink", "hardlink"))
def test_omp_auth_projection_detaches_managed_alias_when_source_is_missing(
    tmp_path: Path,
    alias_kind: str,
) -> None:
    source_home = tmp_path / "source-home"
    source_home.mkdir()
    target_database = tmp_path / "managed-home" / ".omp" / "agent" / "agent.db"
    target_database.parent.mkdir(parents=True)
    outside = tmp_path / "outside.db"
    outside.write_bytes(b"outside-private-state")
    try:
        if alias_kind == "symlink":
            target_database.symlink_to(outside)
        else:
            target_database.hardlink_to(outside)
    except OSError as exc:
        pytest.skip(f"filesystem does not support {alias_kind}: {exc}")

    materialize_native_login_state(
        "omp",
        tmp_path / "managed-home",
        source_home=source_home,
    )

    assert not target_database.exists()
    assert not target_database.is_symlink()
    assert outside.read_bytes() == b"outside-private-state"
