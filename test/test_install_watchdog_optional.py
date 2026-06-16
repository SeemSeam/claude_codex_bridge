from __future__ import annotations

import os
import shlex
import subprocess
import textwrap
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
INSTALL_SH = REPO_ROOT / "install.sh"


def _run_install_snippet(tmp_path: Path, body: str) -> subprocess.CompletedProcess[str]:
    env = os.environ.copy()
    env.update(
        {
            "HOME": str(tmp_path / "home"),
            "CODEX_INSTALL_PREFIX": str(tmp_path / "install"),
            "CODEX_BIN_DIR": str(tmp_path / "bin"),
            "CCB_LANG": "en",
            "CCB_INSTALL_ASSUME_YES": "1",
        }
    )
    command = textwrap.dedent(
        f"""
        set -euo pipefail
        source {shlex.quote(str(INSTALL_SH))}
        {body}
        """
    )
    return subprocess.run(
        ["bash", "-lc", command],
        capture_output=True,
        text=True,
        env=env,
        cwd=REPO_ROOT,
    )


def test_install_watchdog_skip_is_successful_and_explicit(tmp_path: Path) -> None:
    completed = _run_install_snippet(
        tmp_path,
        """
        CCB_INSTALL_WATCHDOG=0
        require_python_version >/dev/null
        install_watchdog
        echo done
        """,
    )

    assert completed.returncode == 0, completed.stderr or completed.stdout
    assert "watchdog auto-install skipped" in completed.stdout
    assert "done" in completed.stdout


def test_install_tomli_skip_is_successful_and_explicit(tmp_path: Path) -> None:
    completed = _run_install_snippet(
        tmp_path,
        """
        CCB_INSTALL_TOMLI=0
        require_python_version >/dev/null
        install_tomli
        echo done
        """,
    )

    assert completed.returncode == 0, completed.stderr or completed.stdout
    assert "tomli auto-install skipped" in completed.stdout
    assert "done" in completed.stdout


def test_install_requirements_continue_when_optional_watchdog_is_skipped(tmp_path: Path) -> None:
    completed = _run_install_snippet(
        tmp_path,
        """
        install_tomli() { echo "tomli stub"; }
        CCB_INSTALL_WATCHDOG=0
        require_terminal_backend() { echo "tmux stub"; }
        install_requirements
        echo requirements-ok
        """,
    )

    assert completed.returncode == 0, completed.stderr or completed.stdout
    assert "tomli stub" in completed.stdout
    assert "watchdog auto-install skipped" in completed.stdout
    assert "tmux stub" in completed.stdout
    assert "requirements-ok" in completed.stdout


def test_install_role_pack_provisioning_runs_by_default_without_prompt(tmp_path: Path) -> None:
    completed = _run_install_snippet(
        tmp_path,
        """
        CCB_SOURCE_KIND=release
        mkdir -p "$CODEX_INSTALL_PREFIX"
        cat > "$CODEX_INSTALL_PREFIX/ccb" <<'SH'
        #!/usr/bin/env bash
        printf '%s\\n' "$*" >> "$CODEX_INSTALL_PREFIX/ccb-argv.txt"
        exit 0
        SH
        chmod +x "$CODEX_INSTALL_PREFIX/ccb"
        check_role_pack_dependencies() { echo "deps:$1"; return 0; }
        provision_role_packs
        cat "$CODEX_INSTALL_PREFIX/ccb-argv.txt"
        """,
    )

    assert completed.returncode == 0, completed.stderr or completed.stdout
    assert "Role Pack provisioning enabled by default" in completed.stdout
    assert "Install catalog Role Packs and dependencies now?" not in completed.stdout
    assert "Role Pack provisioning skipped in non-interactive install" not in completed.stdout
    assert "roles update agentroles.archi" in completed.stdout
    assert "roles update agentroles.ccb_self" in completed.stdout


def test_install_neovim_provisioning_function_is_removed() -> None:
    text = INSTALL_SH.read_text(encoding="utf-8")

    assert "provision_neovim_tool" not in text
    assert "CCB_INSTALL_NEOVIM" not in text
    assert "tools install neovim" not in text


def test_sourced_install_script_has_no_neovim_provisioning_function(tmp_path: Path) -> None:
    completed = _run_install_snippet(
        tmp_path,
        """
        if declare -F provision_neovim_tool >/dev/null; then
          echo unexpected-neovim-function
          exit 9
        fi
        echo no-neovim-function
        """,
    )

    assert completed.returncode == 0, completed.stderr or completed.stdout
    assert "no-neovim-function" in completed.stdout


def test_install_requirements_defers_tomli_to_managed_venv(tmp_path: Path) -> None:
    completed = _run_install_snippet(
        tmp_path,
        """
        CCB_SOURCE_KIND=release
        CCB_USE_MANAGED_VENV=1
        install_tomli() { echo unexpected-system-tomli; exit 9; }
        install_watchdog() { echo unexpected-system-watchdog; exit 9; }
        require_terminal_backend() { echo "tmux stub"; }
        install_requirements
        echo requirements-ok
        """,
    )

    assert completed.returncode == 0, completed.stderr or completed.stdout
    assert "managed Python venv" in completed.stdout
    assert "unexpected-system-tomli" not in completed.stdout
    assert "unexpected-system-watchdog" not in completed.stdout
    assert "requirements-ok" in completed.stdout


def test_install_requirements_defers_watchdog_to_managed_venv(tmp_path: Path) -> None:
    completed = _run_install_snippet(
        tmp_path,
        """
        CCB_SOURCE_KIND=release
        CCB_USE_MANAGED_VENV=1
        install_watchdog() { echo unexpected-system-watchdog; exit 9; }
        require_terminal_backend() { echo "tmux stub"; }
        install_requirements
        echo requirements-ok
        """,
    )

    assert completed.returncode == 0, completed.stderr or completed.stdout
    assert "managed Python venv" in completed.stdout
    assert "unexpected-system-watchdog" not in completed.stdout
    assert "requirements-ok" in completed.stdout


def test_install_tomli_for_python_uses_real_virtualenv_scope(tmp_path: Path) -> None:
    completed = _run_install_snippet(
        tmp_path,
        """
        CCB_SOURCE_KIND=release
        CCB_USE_MANAGED_VENV=1
        venv_dir="$HOME/managed-venv"
        fake_modules="$HOME/fake-modules"
        pip_argv_marker="$HOME/tomli-pip-argv.txt"
        python3 -m venv "$venv_dir"
        mkdir -p "$fake_modules"
        cat > "$fake_modules/pip.py" <<'PY'
        import os
        import pathlib
        import sys

        pathlib.Path(os.environ["PIP_ARGV_MARKER"]).write_text("\\n".join(sys.argv), encoding="utf-8")
        if any(arg.startswith("--user") for arg in sys.argv):
            print("unexpected-user-scope")
            raise SystemExit(9)
        pathlib.Path(os.environ["FAKE_MODULES_DIR"], "tomli.py").write_text("__version__ = 'test'\\n", encoding="utf-8")
        raise SystemExit(0)
        PY
        python_has_toml_reader() {
          "$PYTHON_BIN" - <<'PY'
        import importlib.util
        import sys

        raise SystemExit(0 if importlib.util.find_spec("tomli") else 1)
        PY
        }
        PIP_ARGV_MARKER="$pip_argv_marker" \
        FAKE_MODULES_DIR="$fake_modules" \
        PYTHONPATH="$fake_modules" \
          install_tomli_for_python "$venv_dir/bin/python"
        """,
    )

    assert completed.returncode == 0, completed.stderr or completed.stdout
    assert "unexpected-user-scope" not in completed.stdout
    assert "TOML parser available" in completed.stdout
    pip_argv = (tmp_path / "home" / "tomli-pip-argv.txt").read_text(encoding="utf-8")
    assert "install" in pip_argv
    assert "tomli>=2.0.0" in pip_argv
    assert "--user" not in pip_argv


def test_install_watchdog_for_python_uses_real_virtualenv_scope(tmp_path: Path) -> None:
    completed = _run_install_snippet(
        tmp_path,
        """
        CCB_SOURCE_KIND=release
        CCB_USE_MANAGED_VENV=1
        venv_dir="$HOME/managed-venv"
        fake_modules="$HOME/fake-modules"
        pip_argv_marker="$HOME/pip-argv.txt"
        python3 -m venv "$venv_dir"
        mkdir -p "$fake_modules"
        cat > "$fake_modules/pip.py" <<'PY'
        import os
        import pathlib
        import sys

        pathlib.Path(os.environ["PIP_ARGV_MARKER"]).write_text("\\n".join(sys.argv), encoding="utf-8")
        if any(arg.startswith("--user") for arg in sys.argv):
            print("unexpected-user-scope")
            raise SystemExit(9)
        pathlib.Path(os.environ["FAKE_MODULES_DIR"], "watchdog.py").write_text("__version__ = 'test'\\n", encoding="utf-8")
        raise SystemExit(0)
        PY
        PIP_ARGV_MARKER="$pip_argv_marker" \
        FAKE_MODULES_DIR="$fake_modules" \
        PYTHONPATH="$fake_modules" \
          install_watchdog_for_python "$venv_dir/bin/python"
        """,
    )

    assert completed.returncode == 0, completed.stderr or completed.stdout
    assert "unexpected-user-scope" not in completed.stdout
    assert "watchdog installed" in completed.stdout
    pip_argv = (tmp_path / "home" / "pip-argv.txt").read_text(encoding="utf-8")
    assert "install" in pip_argv
    assert "watchdog>=2.1.0" in pip_argv
    assert "--user" not in pip_argv


def test_release_managed_venv_wraps_installed_python_entrypoints(tmp_path: Path) -> None:
    completed = _run_install_snippet(
        tmp_path,
        """
        mkdir -p "$CODEX_INSTALL_PREFIX/bin"
        cat > "$CODEX_INSTALL_PREFIX/bin/_ccb-python" <<'SH'
        #!/usr/bin/env bash
        exec /usr/bin/env python3 "$@"
        SH
        cat > "$CODEX_INSTALL_PREFIX/ccb" <<'PY'
        #!/usr/bin/env python3
        print("ccb")
        PY
        cat > "$CODEX_INSTALL_PREFIX/bin/ask" <<'PY'
        #!/usr/bin/env python3
        print("ask")
        PY
        cp "$CODEX_INSTALL_PREFIX/bin/ask" "$CODEX_INSTALL_PREFIX/bin/autonew"
        cp "$CODEX_INSTALL_PREFIX/bin/ask" "$CODEX_INSTALL_PREFIX/bin/ctx-transfer"
        cp "$CODEX_INSTALL_PREFIX/bin/ask" "$CODEX_INSTALL_PREFIX/bin/ccb-cleanup"
        cp "$CODEX_INSTALL_PREFIX/bin/ask" "$CODEX_INSTALL_PREFIX/bin/ccb-provider-activity-hook"
        cp "$CODEX_INSTALL_PREFIX/bin/ask" "$CODEX_INSTALL_PREFIX/bin/ccb-provider-finish-hook"
        chmod +x "$CODEX_INSTALL_PREFIX/bin/_ccb-python" "$CODEX_INSTALL_PREFIX/ccb" "$CODEX_INSTALL_PREFIX/bin/ask" "$CODEX_INSTALL_PREFIX/bin/autonew" "$CODEX_INSTALL_PREFIX/bin/ctx-transfer" "$CODEX_INSTALL_PREFIX/bin/ccb-cleanup" "$CODEX_INSTALL_PREFIX/bin/ccb-provider-activity-hook" "$CODEX_INSTALL_PREFIX/bin/ccb-provider-finish-hook"
        CCB_SOURCE_KIND=release
        CCB_USE_MANAGED_VENV=1
        CCB_INSTALL_WATCHDOG=0
        require_python_version >/dev/null
        install_managed_venv
        install_bin_links
        "$CODEX_BIN_DIR/ccb"
        "$CODEX_BIN_DIR/ask"
        """,
    )

    assert completed.returncode == 0, completed.stderr or completed.stdout
    assert "OK: Managed Python venv ready" in completed.stdout
    assert "ccb" in completed.stdout
    assert "ask" in completed.stdout

    wrapper = tmp_path / "bin" / "ccb"
    ask_wrapper = tmp_path / "bin" / "ask"
    wrapper_text = wrapper.read_text(encoding="utf-8")
    assert wrapper_text.startswith("#!/usr/bin/env bash")
    assert '[[ "${TERM:-}" == "xterm-ghostty" ]]' in wrapper_text
    assert "export TERM=xterm-256color" in wrapper_text
    assert str(tmp_path / "install" / ".venv" / "bin" / "python") in wrapper_text
    assert str(tmp_path / "install" / ".venv" / "bin" / "python") in ask_wrapper.read_text(encoding="utf-8")


def test_release_managed_venv_wrapper_uses_absolute_target_path(tmp_path: Path) -> None:
    completed = _run_install_snippet(
        tmp_path,
        """
        mkdir -p "$CODEX_INSTALL_PREFIX/bin"
        cat > "$CODEX_INSTALL_PREFIX/bin/_ccb-python" <<'SH'
        #!/usr/bin/env bash
        exec /usr/bin/env python3 "$@"
        SH
        cat > "$CODEX_INSTALL_PREFIX/ccb" <<'PY'
        #!/usr/bin/env python3
        print("ccb")
        PY
        cat > "$CODEX_INSTALL_PREFIX/bin/ask" <<'PY'
        #!/usr/bin/env python3
        print("ask")
        PY
        cp "$CODEX_INSTALL_PREFIX/bin/ask" "$CODEX_INSTALL_PREFIX/bin/autonew"
        cp "$CODEX_INSTALL_PREFIX/bin/ask" "$CODEX_INSTALL_PREFIX/bin/ctx-transfer"
        cp "$CODEX_INSTALL_PREFIX/bin/ask" "$CODEX_INSTALL_PREFIX/bin/ccb-cleanup"
        cp "$CODEX_INSTALL_PREFIX/bin/ask" "$CODEX_INSTALL_PREFIX/bin/ccb-provider-activity-hook"
        cp "$CODEX_INSTALL_PREFIX/bin/ask" "$CODEX_INSTALL_PREFIX/bin/ccb-provider-finish-hook"
        chmod +x "$CODEX_INSTALL_PREFIX/bin/_ccb-python" "$CODEX_INSTALL_PREFIX/ccb" "$CODEX_INSTALL_PREFIX/bin/ask" "$CODEX_INSTALL_PREFIX/bin/autonew" "$CODEX_INSTALL_PREFIX/bin/ctx-transfer" "$CODEX_INSTALL_PREFIX/bin/ccb-cleanup" "$CODEX_INSTALL_PREFIX/bin/ccb-provider-activity-hook" "$CODEX_INSTALL_PREFIX/bin/ccb-provider-finish-hook"
        CCB_SOURCE_KIND=release
        CCB_USE_MANAGED_VENV=1
        CCB_INSTALL_WATCHDOG=0
        install_managed_venv
        install_bin_links
        """,
    )

    assert completed.returncode == 0, completed.stderr or completed.stdout
    assert str(tmp_path / "install" / "ccb") in (tmp_path / "bin" / "ccb").read_text(encoding="utf-8")


def test_install_managed_venv_selects_python_when_called_directly(tmp_path: Path) -> None:
    completed = _run_install_snippet(
        tmp_path,
        """
        CCB_SOURCE_KIND=release
        CCB_USE_MANAGED_VENV=1
        CCB_INSTALL_WATCHDOG=0
        install_managed_venv
        echo venv-ok
        """,
    )

    assert completed.returncode == 0, completed.stderr or completed.stdout
    assert "Managed Python venv ready" in completed.stdout
    assert "venv-ok" in completed.stdout


def test_use_managed_venv_auto_requires_release_on_macos(tmp_path: Path) -> None:
    completed = _run_install_snippet(
        tmp_path,
        """
        CCB_SOURCE_KIND=source
        CCB_BUILD_PLATFORM=macos
        if use_managed_venv; then
          echo unexpected-managed-venv
          exit 1
        fi
        echo source-stays-unmanaged
        """,
    )

    assert completed.returncode == 0, completed.stderr or completed.stdout
    assert "source-stays-unmanaged" in completed.stdout


def test_source_dev_mode_does_not_use_managed_venv_by_default(tmp_path: Path) -> None:
    completed = _run_install_snippet(
        tmp_path,
        """
        CCB_SOURCE_KIND=source
        if use_managed_venv; then
          echo unexpected-managed-venv
          exit 1
        fi
        echo source-no-venv
        """,
    )

    assert completed.returncode == 0, completed.stderr or completed.stdout
    assert "source-no-venv" in completed.stdout
