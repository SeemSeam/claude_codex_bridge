from __future__ import annotations

import json
import os
import platform
import subprocess
import sys
import time
from pathlib import Path

from .commands_runtime import latest_version
from .install import find_install_dir
from .startup_update_state import (
    load_update_check_state,
    refresh_cache_payload,
    startup_release_update_supported,
    update_check_lock_path,
    utc_now_text,
    write_update_check_state,
)
from .versioning import get_available_versions, get_version_info


BACKGROUND_REFRESH_COMMAND = "__refresh-update-cache"
REFRESH_LOCK_TTL_S = 5 * 60
REFRESH_URLLIB_TIMEOUT_S = 1.5
REFRESH_CURL_TIMEOUT_S = 1.5
REFRESH_GIT_TIMEOUT_S = 1.0


def maybe_handle_background_update_refresh_command(tokens: list[str], *, script_root: Path) -> int | None:
    if list(tokens[:1]) != [BACKGROUND_REFRESH_COMMAND]:
        return None
    lock_path = _background_refresh_lock_path(script_root)
    try:
        refresh_update_check_cache(find_install_dir(script_root))
    except Exception:
        return 0
    finally:
        release_refresh_lock(lock_path)
    return 0


def refresh_update_check_cache(install_dir: Path, *, platform_name: str | None = None) -> bool:
    local_info = get_version_info(install_dir)
    if not startup_release_update_supported(local_info, platform_name=platform_name or platform.system()):
        return False
    latest = _latest_available_version()
    if not latest:
        return False
    now = time.time()
    payload = refresh_cache_payload(
        local_info=local_info,
        latest=latest,
        existing=load_update_check_state(install_dir) or {},
        now=now,
    )
    write_update_check_state(install_dir, payload)
    return True


def schedule_background_update_refresh(*, script_root: Path, install_dir: Path) -> bool:
    lock_path = acquire_refresh_lock(install_dir)
    if lock_path is None:
        return False
    try:
        _spawn_background_refresh(script_root=script_root, install_dir=install_dir, lock_path=lock_path)
    except Exception:
        release_refresh_lock(lock_path)
        return False
    return True


def acquire_refresh_lock(install_dir: Path) -> Path | None:
    lock_path = update_check_lock_path(install_dir)
    if not _clear_expired_lock(lock_path):
        return None
    try:
        fd = os.open(lock_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    except FileExistsError:
        return None
    with os.fdopen(fd, "w", encoding="utf-8") as handle:
        handle.write(json.dumps({"pid": os.getpid(), "created_at": utc_now_text(time.time())}))
        handle.write("\n")
    return lock_path


def release_refresh_lock(lock_path: Path) -> None:
    try:
        lock_path.unlink(missing_ok=True)
    except Exception:
        pass


def _background_refresh_lock_path(script_root: Path) -> Path:
    install_dir = find_install_dir(script_root)
    return Path(os.environ.get("CCB_UPDATE_REFRESH_LOCK") or update_check_lock_path(install_dir))


def _latest_available_version() -> str | None:
    versions = get_available_versions(
        urllib_timeout=REFRESH_URLLIB_TIMEOUT_S,
        curl_timeout=REFRESH_CURL_TIMEOUT_S,
        git_timeout=REFRESH_GIT_TIMEOUT_S,
    )
    return latest_version(versions)


def _clear_expired_lock(lock_path: Path) -> bool:
    if not lock_path.exists():
        return True
    lock_age = max(0.0, time.time() - lock_path.stat().st_mtime)
    if lock_age <= REFRESH_LOCK_TTL_S:
        return False
    try:
        lock_path.unlink()
    except OSError:
        pass
    return True


def _spawn_background_refresh(*, script_root: Path, install_dir: Path, lock_path: Path) -> None:
    env = dict(os.environ)
    env["CCB_UPDATE_REFRESH_LOCK"] = str(lock_path)
    env["CCB_SKIP_STARTUP_UPDATE_CHECK"] = "1"
    subprocess.Popen(
        [sys.executable, str(Path(script_root) / "ccb.py"), BACKGROUND_REFRESH_COMMAND],
        cwd=str(install_dir),
        env=env,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
    )


__all__ = [
    "BACKGROUND_REFRESH_COMMAND",
    "acquire_refresh_lock",
    "maybe_handle_background_update_refresh_command",
    "refresh_update_check_cache",
    "release_refresh_lock",
    "schedule_background_update_refresh",
]
