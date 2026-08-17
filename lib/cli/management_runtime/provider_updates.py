from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, replace
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import time
from typing import Callable, Iterable, Mapping, TextIO
from urllib.parse import quote

from provider_command_defaults import SUPPORTED_PROVIDER_NAMES, provider_executable
from ui_text.i18n import detect_language

from .commands_runtime.matching import is_newer_version
from .versioning_runtime.transport import fetch_json_via_curl, fetch_json_via_urllib


STATE_SCHEMA_VERSION = 1
STATE_FILE_NAME = "provider-updates.json"
LOCK_FILE_NAME = "provider-updates.lock"
LOCK_STALE_SECONDS = 15 * 60
PROBE_TIMEOUT_SECONDS = 8.0
LATEST_PROBE_TIMEOUT_SECONDS = 20.0
UPDATE_TIMEOUT_SECONDS = 10 * 60

_VERSION_RE = re.compile(r"(?<!\d)v?(\d+\.\d+(?:\.\d+)*(?:[-+][0-9A-Za-z.-]+)?)")
_NATIVE_REGISTRY_PACKAGES = {
    "codex": "@openai/codex",
    "claude": "@anthropic-ai/claude-code",
    "gemini": "@google/gemini-cli",
    "opencode": "opencode-ai",
    "mimo": "@mimo-ai/cli",
    "grok": "@xai-official/grok",
    "dsh": "@deepseek-ai/dsh",
}
_NATIVE_UPDATE_ARGS = {
    "codex": ("update",),
    "claude": ("update",),
    "gemini": ("update",),
    "opencode": ("upgrade", "{version}"),
    "mimo": ("upgrade", "{version}"),
    "grok": ("update", "--version", "{version}"),
    "droid": ("update", "--version", "{version}"),
}
_NATIVE_LATEST_ARGS = {
    "droid": ("update", "--check"),
}
_BREW_PACKAGES = {
    "codex": ("codex", True),
    "claude": ("claude-code", True),
    "gemini": ("gemini-cli", False),
    "opencode": ("opencode", False),
}
_PROVIDER_LABELS = {
    "codex": "Codex",
    "claude": "Claude",
    "gemini": "Gemini",
    "opencode": "OpenCode",
    "mimo": "MiMo",
    "grok": "Grok",
    "dsh": "DeepSeek Harness",
}
_EXPECTED_EXECUTABLE_NAMES = {
    "deepseek": "deepcode",
    "cursor": "agent",
    "kiro": "kiro-cli",
}
_NO_AUTO_UPDATE_ENV = {
    "AGY_CLI_DISABLE_AUTO_UPDATE": "1",
    "DISABLE_AUTOUPDATER": "1",
    "FACTORYD_DISABLE_AUTO_UPDATE": "1",
    "GROK_DISABLE_AUTOUPDATER": "1",
    "MIMOCODE_DISABLE_AUTOUPDATE": "true",
    "NO_UPDATE_NOTIFIER": "1",
    "OPENCODE_DISABLE_AUTOUPDATE": "true",
}
_EXPLICIT_UPDATE_ENV = {
    "NO_UPDATE_NOTIFIER": "1",
}
_EXPLICIT_UPDATE_ENV_UNSET = frozenset(_NO_AUTO_UPDATE_ENV) - frozenset(
    _EXPLICIT_UPDATE_ENV
)


def _explicit_update_env() -> dict[str, str]:
    env = dict(os.environ)
    for name in _EXPLICIT_UPDATE_ENV_UNSET:
        env.pop(name, None)
    env.update(_EXPLICIT_UPDATE_ENV)
    return env


@dataclass(frozen=True)
class ProviderUpdateCandidate:
    provider: str
    executable: Path
    current_version: str | None
    latest_version: str | None
    owner: str
    update_command: tuple[str, ...] | None
    package: str | None = None
    issue: str | None = None
    muted: bool = False

    @property
    def label(self) -> str:
        return _PROVIDER_LABELS.get(self.provider, self.provider)

    @property
    def update_available(self) -> bool:
        if not self.current_version or not self.latest_version:
            return False
        return _provider_version_is_newer(self.latest_version, self.current_version)

    @property
    def actionable(self) -> bool:
        return self.update_available and bool(self.update_command)


@dataclass(frozen=True)
class ProviderUpdateExecution:
    provider: str
    success: bool
    before_version: str | None
    after_version: str | None
    detail: str


@dataclass(frozen=True)
class _RegistryRelease:
    version: str | None
    issue: str | None = None


def provider_update_state_path(
    *,
    env: Mapping[str, str] | None = None,
    home: Path | None = None,
) -> Path:
    source_env = os.environ if env is None else env
    raw_state_home = str(source_env.get("XDG_STATE_HOME") or "").strip()
    state_home = Path(raw_state_home).expanduser() if raw_state_home else None
    if state_home is None or not state_home.is_absolute():
        state_home = Path(home or Path.home()).expanduser() / ".local" / "state"
    return state_home / "ccb" / STATE_FILE_NAME


def load_provider_update_state(path: Path | None = None) -> dict[str, object]:
    target = Path(path or provider_update_state_path()).expanduser()
    try:
        payload = json.loads(target.read_text(encoding="utf-8", errors="replace"))
    except Exception:
        return _empty_state()
    if not isinstance(payload, dict) or payload.get("schema_version") != STATE_SCHEMA_VERSION:
        return _empty_state()
    providers = payload.get("providers")
    return {
        "schema_version": STATE_SCHEMA_VERSION,
        "updated_at": str(payload.get("updated_at") or "").strip() or None,
        "providers": dict(providers) if isinstance(providers, dict) else {},
    }


def write_provider_update_state(payload: dict[str, object], path: Path | None = None) -> None:
    target = Path(path or provider_update_state_path()).expanduser()
    target.parent.mkdir(parents=True, exist_ok=True)
    temp_path = target.with_name(f"{target.name}.tmp.{os.getpid()}")
    try:
        temp_path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        try:
            temp_path.chmod(0o600)
        except OSError:
            pass
        os.replace(temp_path, target)
    finally:
        try:
            temp_path.unlink()
        except OSError:
            pass


def discover_provider_updates(
    *,
    providers: Iterable[str] = SUPPORTED_PROVIDER_NAMES,
    which_fn: Callable[[str], str | None] = shutil.which,
    run_fn: Callable[..., subprocess.CompletedProcess] = subprocess.run,
    fetch_latest_fn: Callable[[str], str | _RegistryRelease | None] | None = None,
) -> tuple[ProviderUpdateCandidate, ...]:
    fetch_latest = fetch_latest_fn or _fetch_registry_latest
    discovered: list[dict[str, object]] = []
    seen_executables: set[str] = set()

    for provider in providers:
        normalized = str(provider or "").strip().lower()
        if not normalized:
            continue
        command = str(provider_executable(normalized) or "").strip()
        executable = _resolve_executable(command, which_fn=which_fn)
        if executable is None:
            continue
        try:
            resolved = executable.resolve()
        except Exception:
            resolved = executable
        identity = str(resolved)
        if identity in seen_executables:
            continue
        seen_executables.add(identity)
        current_version = _read_installed_version(executable, run_fn=run_fn)
        owner, package, owner_issue = _detect_install_owner(
            normalized,
            executable=executable,
            resolved=resolved,
            which_fn=which_fn,
        )
        npm_prefix = (
            _npm_install_prefix_from_path(resolved) if owner == "npm" else None
        )
        bun_home = _bun_install_home_from_path(resolved) if owner == "bun" else None
        registry_package = package or _NATIVE_REGISTRY_PACKAGES.get(normalized)
        discovered.append(
            {
                "provider": normalized,
                "executable": executable,
                "resolved": resolved,
                "current_version": current_version,
                "owner": owner,
                "package": package,
                "npm_prefix": npm_prefix,
                "bun_home": bun_home,
                "registry_package": registry_package,
                "has_latest_source": bool(
                    registry_package or normalized in _NATIVE_LATEST_ARGS
                ),
                "owner_issue": owner_issue,
            }
        )

    packages = sorted(
        {
            str(row.get("registry_package") or "").strip()
            for row in discovered
            if str(row.get("registry_package") or "").strip()
        }
    )
    latest_by_package = _fetch_latest_versions(packages, fetch_latest_fn=fetch_latest)
    candidates: list[ProviderUpdateCandidate] = []
    for row in discovered:
        provider = str(row["provider"])
        executable = Path(row["executable"])
        current_version = _optional_text(row.get("current_version"))
        owner = str(row.get("owner") or "unknown")
        package = _optional_text(row.get("package"))
        registry_package = _optional_text(row.get("registry_package"))
        registry_release = (
            latest_by_package.get(registry_package or "", _RegistryRelease(None))
            if registry_package
            else _RegistryRelease(None)
        )
        latest_version = registry_release.version
        if latest_version is None and owner == "native":
            latest_version = _fetch_native_latest_version(
                provider,
                executable=executable,
                run_fn=run_fn,
            )
        registry_issue = (
            registry_release.issue if owner in {"npm", "bun"} else None
        )
        if registry_issue:
            command, command_issue = None, registry_issue
        else:
            command, command_issue = _build_update_command(
                provider,
                executable=executable,
                owner=owner,
                package=package,
                latest_version=latest_version,
                which_fn=which_fn,
                run_fn=run_fn,
                npm_prefix=(
                    Path(row["npm_prefix"])
                    if row.get("npm_prefix") is not None
                    else None
                ),
                bun_home=(
                    Path(row["bun_home"])
                    if row.get("bun_home") is not None
                    else None
                ),
                check_npm_prefix_writable=bool(
                    current_version
                    and latest_version
                    and _provider_version_is_newer(latest_version, current_version)
                ),
                check_bun_home_writable=bool(
                    current_version
                    and latest_version
                    and _provider_version_is_newer(latest_version, current_version)
                ),
            )
        issues = [
            _optional_text(row.get("owner_issue")),
            None if current_version else "installed version could not be read",
            (
                None
                if latest_version
                else "latest version could not be resolved"
                if bool(row.get("has_latest_source"))
                else "no supported latest-version source"
            ),
            command_issue,
        ]
        candidates.append(
            ProviderUpdateCandidate(
                provider=provider,
                executable=executable,
                current_version=current_version,
                latest_version=latest_version,
                owner=owner,
                update_command=command,
                package=package,
                issue="; ".join(item for item in issues if item) or None,
            )
        )
    return tuple(candidates)


def execute_provider_update(
    candidate: ProviderUpdateCandidate,
    *,
    run_fn: Callable[..., subprocess.CompletedProcess] = subprocess.run,
    timeout: float = UPDATE_TIMEOUT_SECONDS,
) -> ProviderUpdateExecution:
    if not candidate.update_command:
        return ProviderUpdateExecution(
            provider=candidate.provider,
            success=False,
            before_version=candidate.current_version,
            after_version=candidate.current_version,
            detail="no supported update command",
        )
    env = _explicit_update_env()
    if candidate.owner == "bun":
        try:
            bun_home = _bun_install_home_from_path(candidate.executable.resolve())
        except Exception:
            bun_home = None
        if bun_home is None:
            return ProviderUpdateExecution(
                provider=candidate.provider,
                success=False,
                before_version=candidate.current_version,
                after_version=candidate.current_version,
                detail="Bun install home could not be revalidated before update",
            )
        env["BUN_INSTALL"] = str(bun_home)
    try:
        completed = run_fn(
            list(candidate.update_command),
            env=env,
            timeout=timeout,
        )
    except subprocess.TimeoutExpired:
        return ProviderUpdateExecution(
            provider=candidate.provider,
            success=False,
            before_version=candidate.current_version,
            after_version=None,
            detail=f"update timed out after {timeout:g}s",
        )
    except Exception as exc:
        return ProviderUpdateExecution(
            provider=candidate.provider,
            success=False,
            before_version=candidate.current_version,
            after_version=None,
            detail=f"{type(exc).__name__}: {exc}",
        )
    if int(getattr(completed, "returncode", 1) or 0) != 0:
        return ProviderUpdateExecution(
            provider=candidate.provider,
            success=False,
            before_version=candidate.current_version,
            after_version=None,
            detail=f"updater exited with code {completed.returncode}",
        )
    after_version = _read_installed_version(candidate.executable, run_fn=run_fn)
    verified = bool(
        after_version
        and candidate.latest_version
        and not _provider_version_is_newer(candidate.latest_version, after_version)
    )
    return ProviderUpdateExecution(
        provider=candidate.provider,
        success=verified,
        before_version=candidate.current_version,
        after_version=after_version,
        detail="verified" if verified else "updater returned success but the target version was not verified",
    )


def run_provider_update_flow(
    *,
    mode: str | None = None,
    stdin=None,
    stdout: TextIO | None = None,
    state_path: Path | None = None,
    discover_fn: Callable[[], tuple[ProviderUpdateCandidate, ...]] | None = None,
    execute_fn: Callable[[ProviderUpdateCandidate], ProviderUpdateExecution] | None = None,
) -> int:
    resolved_mode = _normalized_mode(mode)
    input_stream = sys.stdin if stdin is None else stdin
    output_stream = sys.stdout if stdout is None else stdout
    lang = detect_language()
    if resolved_mode == "none":
        print(_text(lang, "disabled"), file=output_stream)
        return 0
    if resolved_mode == "prompt" and not (_stream_is_tty(input_stream) and _stream_is_tty(output_stream)):
        print(_text(lang, "non_interactive"), file=output_stream)
        return 0

    target_state_path = Path(state_path or provider_update_state_path()).expanduser()
    lock_path = target_state_path.with_name(LOCK_FILE_NAME)
    if not _acquire_lock(lock_path):
        print(_text(lang, "locked"), file=output_stream)
        return 0
    try:
        discover = discover_fn or discover_provider_updates
        try:
            candidates = tuple(discover())
        except Exception as exc:
            print(_text(lang, "scan_failed", detail=f"{type(exc).__name__}: {exc}"), file=output_stream)
            return 0
        if not candidates:
            print(_text(lang, "none_installed"), file=output_stream)
            return 0

        state = _merge_scan_state(load_provider_update_state(target_state_path), candidates)
        candidates = _apply_muted_state(candidates, state)
        write_provider_update_state(state, target_state_path)
        available = tuple(candidate for candidate in candidates if candidate.update_available)
        if not available:
            key = "not_found" if _has_unchecked_candidates(candidates) else "current"
            print(_text(lang, key), file=output_stream)
            _print_unchecked_candidates(candidates, lang=lang, stdout=output_stream)
            return 0

        reported = (
            available
            if resolved_mode in {"check", "all"}
            else tuple(candidate for candidate in available if not candidate.muted)
        )
        if not reported:
            print(_text(lang, "all_muted"), file=output_stream)
            _print_unchecked_candidates(candidates, lang=lang, stdout=output_stream)
            return 0

        _print_available_candidates(reported, lang=lang, stdout=output_stream)
        _print_unchecked_candidates(candidates, lang=lang, stdout=output_stream)
        actionable = tuple(candidate for candidate in reported if candidate.update_command)
        if resolved_mode == "check":
            muted = tuple(candidate for candidate in available if candidate.muted)
            if muted:
                versions = ", ".join(f"{item.provider} {item.latest_version}" for item in muted)
                print(_text(lang, "muted", versions=versions), file=output_stream)
            print(_text(lang, "check_only"), file=output_stream)
            return 0
        if not actionable:
            print(_text(lang, "manual_only"), file=output_stream)
            if resolved_mode == "prompt":
                choice = _prompt_report_only_choice(
                    stdin=input_stream,
                    stdout=output_stream,
                    lang=lang,
                )
                if choice == "skip":
                    state = _record_muted_versions(state, reported)
                    write_provider_update_state(state, target_state_path)
                    print(_text(lang, "skipped_versions"), file=output_stream)
                else:
                    state = _record_decision(state, reported, "declined")
                    write_provider_update_state(state, target_state_path)
                    print(_text(lang, "declined"), file=output_stream)
            return 0

        if resolved_mode == "all":
            selected = actionable
        else:
            pending = actionable
            choice = _prompt_update_choice(stdin=input_stream, stdout=output_stream, lang=lang)
            if choice == "skip":
                state = _record_muted_versions(state, reported)
                write_provider_update_state(state, target_state_path)
                print(_text(lang, "skipped_versions"), file=output_stream)
                return 0
            if choice == "select":
                selected = _prompt_selection(pending, stdin=input_stream, stdout=output_stream, lang=lang)
                if not selected:
                    state = _record_decision(state, reported, "declined")
                    write_provider_update_state(state, target_state_path)
                    print(_text(lang, "declined"), file=output_stream)
                    return 0
            elif choice == "all":
                selected = pending
            else:
                state = _record_decision(state, reported, "declined")
                write_provider_update_state(state, target_state_path)
                print(_text(lang, "declined"), file=output_stream)
                return 0

        execute = execute_fn or execute_provider_update
        executions: list[ProviderUpdateExecution] = []
        for candidate in selected:
            print(
                _text(
                    lang,
                    "updating",
                    provider=candidate.label,
                    current=candidate.current_version or "?",
                    latest=candidate.latest_version or "?",
                ),
                file=output_stream,
            )
            result = execute(candidate)
            executions.append(result)
            if result.success:
                print(
                    _text(lang, "updated", provider=candidate.label, version=result.after_version or "?"),
                    file=output_stream,
                )
            else:
                print(
                    _text(lang, "update_failed", provider=candidate.label, detail=result.detail),
                    file=output_stream,
                )
        state = _record_executions(state, selected, executions)
        write_provider_update_state(state, target_state_path)
        if any(result.success for result in executions):
            print(_text(lang, "restart_note"), file=output_stream)
        return 0
    finally:
        _release_lock(lock_path)


def _resolve_executable(command: str, *, which_fn: Callable[[str], str | None]) -> Path | None:
    raw = str(command or "").strip()
    if not raw:
        return None
    if os.sep in raw or (os.altsep and os.altsep in raw):
        candidate = Path(raw).expanduser()
        return candidate if candidate.is_file() else None
    resolved = which_fn(raw)
    return Path(resolved).expanduser() if resolved else None


def _read_installed_version(
    executable: Path,
    *,
    run_fn: Callable[..., subprocess.CompletedProcess],
) -> str | None:
    env = dict(os.environ)
    env.update(_NO_AUTO_UPDATE_ENV)
    try:
        completed = run_fn(
            [str(executable), "--version"],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="replace",
            env=env,
            timeout=PROBE_TIMEOUT_SECONDS,
        )
    except Exception:
        return None
    output = "\n".join(
        part for part in (str(getattr(completed, "stdout", "") or ""), str(getattr(completed, "stderr", "") or "")) if part
    )
    match = _VERSION_RE.search(output)
    return match.group(1) if match else None


def _detect_install_owner(
    provider: str,
    *,
    executable: Path,
    resolved: Path,
    which_fn: Callable[[str], str | None],
) -> tuple[str, str | None, str | None]:
    if _looks_like_windows_interop_path(executable, resolved):
        return (
            "windows-interop",
            None,
            "Windows interop installations are not updated from WSL; update this CLI from Windows",
        )
    bun_home = _bun_install_home_from_path(resolved)
    package = _npm_package_from_path(resolved)
    if bun_home is not None and package:
        bun = _bun_executable_for(
            executable,
            bun_home=bun_home,
            which_fn=which_fn,
        )
        if bun is None:
            return "bun", package, "matching Bun executable was not found"
        return "bun", package, None
    if package:
        npm = _npm_executable_for(executable, which_fn=which_fn)
        if npm is None:
            return "npm", package, "matching npm executable was not found"
        return "npm", package, None
    if _looks_like_windows_executable(executable, resolved):
        return (
            "windows-interop",
            None,
            "Windows interop installations are not updated from WSL; update this CLI from Windows",
        )
    expected_name = _EXPECTED_EXECUTABLE_NAMES.get(provider, provider)
    executable_name = executable.name
    for suffix in (".exe", ".cmd", ".bat"):
        if executable_name.lower().endswith(suffix):
            executable_name = executable_name[: -len(suffix)]
            break
    if executable_name.lower() != expected_name.lower():
        return "custom", None, f"custom start wrapper `{executable.name}` is not updated automatically"
    path_text = str(resolved)
    if str(executable).startswith("/snap/bin/") or "/snap/" in path_text:
        return "snap", None, "Snap installations are updated by snapd"
    if "/Cellar/" in path_text or "/Caskroom/" in path_text:
        if provider not in _BREW_PACKAGES:
            return "brew", None, "Homebrew package mapping is not available"
        if not which_fn("brew"):
            return "brew", None, "brew executable was not found"
        return "brew", None, None
    return "native", None, None


def _looks_like_windows_interop_path(executable: Path, resolved: Path) -> bool:
    for candidate in (executable, resolved):
        text = str(candidate)
        if re.match(r"(?i)^/mnt/[a-z](?:/|$)", text):
            return True
        if re.match(r"(?i)^[a-z]:[\\/]", text):
            return True
    return False


def _looks_like_windows_executable(executable: Path, resolved: Path) -> bool:
    return any(
        str(candidate).lower().endswith((".exe", ".cmd", ".bat", ".ps1"))
        for candidate in (executable, resolved)
    )


def _npm_package_from_path(path: Path) -> str | None:
    parts = path.parts
    indexes = [index for index, part in enumerate(parts) if part == "node_modules"]
    for index in reversed(indexes):
        if index + 1 >= len(parts):
            continue
        first = parts[index + 1]
        if first.startswith("@"):
            if index + 2 < len(parts):
                return f"{first}/{parts[index + 2]}"
            continue
        return first
    return None


def _npm_install_prefix_from_path(path: Path) -> Path | None:
    parts = path.parts
    indexes = [index for index, part in enumerate(parts) if part == "node_modules"]
    for index in reversed(indexes):
        if index <= 0:
            continue
        modules_parent = Path(*parts[:index])
        if modules_parent.name == "lib":
            return modules_parent.parent
        return modules_parent
    return None


def _bun_install_home_from_path(path: Path) -> Path | None:
    parts = path.parts
    for index in range(len(parts) - 2):
        if parts[index : index + 3] != ("install", "global", "node_modules"):
            continue
        if index <= 0:
            return None
        return Path(*parts[:index])
    return None


def _npm_executable_for(executable: Path, *, which_fn: Callable[[str], str | None]) -> Path | None:
    sibling = executable.parent / ("npm.cmd" if os.name == "nt" else "npm")
    if sibling.is_file():
        return sibling
    resolved = which_fn("npm")
    return Path(resolved).expanduser() if resolved else None


def _bun_executable_for(
    executable: Path,
    *,
    bun_home: Path,
    which_fn: Callable[[str], str | None],
) -> Path | None:
    owned = Path(bun_home).expanduser() / "bin" / ("bun.exe" if os.name == "nt" else "bun")
    if owned.is_file():
        return owned
    sibling = executable.parent / ("bun.exe" if os.name == "nt" else "bun")
    if sibling.is_file():
        return sibling
    resolved = which_fn("bun")
    return Path(resolved).expanduser() if resolved else None


def _fetch_latest_versions(
    packages: list[str],
    *,
    fetch_latest_fn: Callable[[str], str | _RegistryRelease | None],
) -> dict[str, _RegistryRelease]:
    if not packages:
        return {}
    results: dict[str, _RegistryRelease] = {}
    workers = min(4, len(packages))
    with ThreadPoolExecutor(max_workers=workers, thread_name_prefix="ccb-provider-update") as pool:
        futures = {pool.submit(fetch_latest_fn, package): package for package in packages}
        for future in as_completed(futures):
            package = futures[future]
            try:
                result = future.result()
                results[package] = (
                    result
                    if isinstance(result, _RegistryRelease)
                    else _RegistryRelease(_optional_text(result))
                )
            except Exception:
                results[package] = _RegistryRelease(None)
    return results


def _fetch_registry_latest(package: str) -> _RegistryRelease:
    encoded = quote(str(package or "").strip(), safe="")
    if not encoded:
        return _RegistryRelease(None)
    url = f"https://registry.npmjs.org/{encoded}/latest"
    payload = None
    try:
        payload = fetch_json_via_urllib(url, timeout=5, user_agent="ccb-provider-update")
    except Exception:
        payload = None
    if payload is None:
        payload = fetch_json_via_curl(url, timeout=8)
    if not isinstance(payload, dict):
        return _RegistryRelease(None)
    version = _optional_text(payload.get("version"))
    return _RegistryRelease(
        version,
        _registry_release_install_issue(payload, version=version),
    )


def _registry_release_install_issue(
    payload: Mapping[str, object],
    *,
    version: str | None,
) -> str | None:
    for field in ("dependencies", "optionalDependencies", "peerDependencies"):
        dependencies = payload.get(field)
        if not isinstance(dependencies, Mapping):
            continue
        for name, raw_spec in dependencies.items():
            spec = str(raw_spec or "").strip()
            if not spec.lower().startswith(("file:", "link:", "workspace:")):
                continue
            release = f" {version}" if version else ""
            return (
                f"registry release{release} declares non-published local dependency "
                f"`{name}: {spec}`; wait for the provider publisher to replace this release"
            )
    return None


def _fetch_native_latest_version(
    provider: str,
    *,
    executable: Path,
    run_fn: Callable[..., subprocess.CompletedProcess],
) -> str | None:
    args = _NATIVE_LATEST_ARGS.get(provider)
    if args is None:
        return None
    completed = None
    for _attempt in range(2):
        try:
            result = run_fn(
                [str(executable), *args],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                encoding="utf-8",
                errors="replace",
                env=_explicit_update_env(),
                timeout=LATEST_PROBE_TIMEOUT_SECONDS,
            )
        except Exception:
            continue
        if int(getattr(result, "returncode", 1) or 0) == 0:
            completed = result
            break
    if completed is None:
        return None
    output = "\n".join(
        part
        for part in (
            str(getattr(completed, "stdout", "") or ""),
            str(getattr(completed, "stderr", "") or ""),
        )
        if part
    )
    lines = output.splitlines()
    for marker in ("update available", "current version"):
        for line in lines:
            if marker not in line.lower():
                continue
            match = _VERSION_RE.search(line)
            if match:
                return match.group(1)
    return None


def _build_update_command(
    provider: str,
    *,
    executable: Path,
    owner: str,
    package: str | None,
    latest_version: str | None,
    which_fn: Callable[[str], str | None],
    run_fn: Callable[..., subprocess.CompletedProcess],
    npm_prefix: Path | None = None,
    bun_home: Path | None = None,
    check_npm_prefix_writable: bool = True,
    check_bun_home_writable: bool = True,
) -> tuple[tuple[str, ...] | None, str | None]:
    if not latest_version:
        return None, None
    if owner == "npm" and package:
        npm = _npm_executable_for(executable, which_fn=which_fn)
        if npm is None:
            return None, "matching npm executable was not found"
        if npm_prefix is None:
            return None, "npm install prefix could not be derived from the provider executable"
        prefix_issue = (
            _npm_prefix_write_issue(npm_prefix)
            if check_npm_prefix_writable
            else None
        )
        if prefix_issue is not None:
            return None, prefix_issue
        return (
            str(npm),
            "install",
            "--global",
            "--prefix",
            str(npm_prefix),
            f"{package}@{latest_version}",
            "--no-audit",
            "--no-fund",
        ), None
    if owner == "bun" and package:
        if bun_home is None:
            return None, "Bun install home could not be derived from the provider executable"
        bun = _bun_executable_for(
            executable,
            bun_home=bun_home,
            which_fn=which_fn,
        )
        if bun is None:
            return None, "matching Bun executable was not found"
        home_issue = (
            _bun_home_write_issue(bun_home)
            if check_bun_home_writable
            else None
        )
        if home_issue is not None:
            return None, home_issue
        return (
            str(bun),
            "add",
            "--global",
            f"{package}@{latest_version}",
        ), None
    if owner == "brew":
        brew = which_fn("brew")
        package_info = _BREW_PACKAGES.get(provider)
        if not brew or package_info is None:
            return None, "Homebrew updater is not available"
        package_name, is_cask = package_info
        args = [str(brew), "upgrade"]
        if is_cask:
            args.append("--cask")
        args.append(package_name)
        return tuple(args), None
    if owner == "native":
        args = _NATIVE_UPDATE_ARGS.get(provider)
        if args is None:
            return None, "this native provider has no CCB-managed updater"
        subcommand = args[0]
        if not _native_subcommand_supported(executable, subcommand, run_fn=run_fn):
            return None, f"installed {provider} CLI does not expose `{executable.name} {subcommand}`"
        return tuple(
            [str(executable), *(latest_version if part == "{version}" else part for part in args)]
        ), None
    return None, None


def _npm_prefix_write_issue(prefix: Path) -> str | None:
    target_prefix = Path(prefix).expanduser()
    for target in (
        target_prefix / "bin",
        target_prefix / "lib" / "node_modules",
    ):
        probe = target
        while not probe.exists() and probe.parent != probe:
            probe = probe.parent
        if not os.access(probe, os.W_OK | os.X_OK):
            return (
                f"npm install prefix `{target_prefix}` is not writable by the current user; "
                "update this provider with its installation owner or move it to a user-owned npm prefix"
            )
    return None


def _bun_home_write_issue(bun_home: Path) -> str | None:
    target_home = Path(bun_home).expanduser()
    for target in (
        target_home / "bin",
        target_home / "install" / "global" / "node_modules",
    ):
        probe = target
        while not probe.exists() and probe.parent != probe:
            probe = probe.parent
        if not os.access(probe, os.W_OK | os.X_OK):
            return (
                f"Bun install home `{target_home}` is not writable by the current user; "
                "update this provider with the Bun installation owner"
            )
    return None


def _native_subcommand_supported(
    executable: Path,
    subcommand: str,
    *,
    run_fn: Callable[..., subprocess.CompletedProcess],
) -> bool:
    try:
        completed = run_fn(
            [str(executable), subcommand, "--help"],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="replace",
            env=_explicit_update_env(),
            timeout=PROBE_TIMEOUT_SECONDS,
        )
    except Exception:
        return False
    output = "\n".join(
        part for part in (str(getattr(completed, "stdout", "") or ""), str(getattr(completed, "stderr", "") or "")) if part
    )
    name = re.escape(executable.name)
    return bool(
        re.search(
            rf"(?im)^\s*(?:usage:\s*)?{name}\s+{re.escape(subcommand)}\b",
            output,
        )
    )


def _empty_state() -> dict[str, object]:
    return {
        "schema_version": STATE_SCHEMA_VERSION,
        "updated_at": None,
        "providers": {},
    }


def _merge_scan_state(
    existing: dict[str, object],
    candidates: tuple[ProviderUpdateCandidate, ...],
) -> dict[str, object]:
    previous_rows = existing.get("providers")
    previous = dict(previous_rows) if isinstance(previous_rows, dict) else {}
    supported = set(SUPPORTED_PROVIDER_NAMES)
    rows: dict[str, object] = {
        str(provider): dict(raw_row)
        for provider, raw_row in previous.items()
        if str(provider) in supported and isinstance(raw_row, dict)
    }
    for candidate in candidates:
        old_row = previous.get(candidate.provider)
        old = dict(old_row) if isinstance(old_row, dict) else {}
        muted_version = _optional_text(old.get("muted_version"))
        if candidate.latest_version and muted_version != candidate.latest_version:
            muted_version = None
        rows[candidate.provider] = {
            "current_version": candidate.current_version,
            "latest_version": candidate.latest_version,
            "owner": candidate.owner,
            "package": candidate.package,
            "executable": str(candidate.executable),
            "muted_version": muted_version,
            "last_decision": _optional_text(old.get("last_decision")),
            "last_decision_at": _optional_text(old.get("last_decision_at")),
            "last_update_status": _optional_text(old.get("last_update_status")),
            "last_update_at": _optional_text(old.get("last_update_at")),
            "last_update_detail": _optional_text(old.get("last_update_detail")),
        }
    return {
        "schema_version": STATE_SCHEMA_VERSION,
        "updated_at": _utc_now(),
        "providers": rows,
    }


def _apply_muted_state(
    candidates: tuple[ProviderUpdateCandidate, ...],
    state: dict[str, object],
) -> tuple[ProviderUpdateCandidate, ...]:
    rows = state.get("providers")
    providers = dict(rows) if isinstance(rows, dict) else {}
    result: list[ProviderUpdateCandidate] = []
    for candidate in candidates:
        raw_row = providers.get(candidate.provider)
        row = dict(raw_row) if isinstance(raw_row, dict) else {}
        muted = bool(
            candidate.latest_version
            and _optional_text(row.get("muted_version")) == candidate.latest_version
        )
        result.append(replace(candidate, muted=muted))
    return tuple(result)


def _record_muted_versions(
    state: dict[str, object],
    candidates: tuple[ProviderUpdateCandidate, ...],
) -> dict[str, object]:
    payload = _clone_state(state)
    rows = payload["providers"]
    now = _utc_now()
    for candidate in candidates:
        row = rows.setdefault(candidate.provider, {})
        row["muted_version"] = candidate.latest_version
        row["last_decision"] = "muted"
        row["last_decision_at"] = now
    payload["updated_at"] = now
    return payload


def _record_decision(
    state: dict[str, object],
    candidates: tuple[ProviderUpdateCandidate, ...],
    decision: str,
) -> dict[str, object]:
    payload = _clone_state(state)
    rows = payload["providers"]
    now = _utc_now()
    for candidate in candidates:
        row = rows.setdefault(candidate.provider, {})
        row["last_decision"] = decision
        row["last_decision_at"] = now
    payload["updated_at"] = now
    return payload


def _record_executions(
    state: dict[str, object],
    candidates: tuple[ProviderUpdateCandidate, ...],
    executions: list[ProviderUpdateExecution],
) -> dict[str, object]:
    payload = _clone_state(state)
    rows = payload["providers"]
    candidate_by_provider = {candidate.provider: candidate for candidate in candidates}
    now = _utc_now()
    for execution in executions:
        candidate = candidate_by_provider.get(execution.provider)
        row = rows.setdefault(execution.provider, {})
        row["current_version"] = execution.after_version or execution.before_version
        row["last_update_status"] = "updated" if execution.success else "failed"
        row["last_update_at"] = now
        row["last_update_detail"] = execution.detail
        row["last_decision"] = "accepted"
        row["last_decision_at"] = now
        if execution.success and candidate and row.get("muted_version") == candidate.latest_version:
            row["muted_version"] = None
    payload["updated_at"] = now
    return payload


def _clone_state(state: dict[str, object]) -> dict[str, object]:
    rows = state.get("providers")
    providers: dict[str, dict[str, object]] = {}
    if isinstance(rows, dict):
        for provider, raw_row in rows.items():
            providers[str(provider)] = dict(raw_row) if isinstance(raw_row, dict) else {}
    return {
        "schema_version": STATE_SCHEMA_VERSION,
        "updated_at": _optional_text(state.get("updated_at")),
        "providers": providers,
    }


def _prompt_update_choice(*, stdin, stdout: TextIO, lang: str) -> str:
    print(_text(lang, "choices"), file=stdout)
    stdout.write(_text(lang, "choice_prompt"))
    stdout.flush()
    try:
        answer = str(stdin.readline() or "").strip().lower()
    except Exception:
        answer = ""
    if answer in {"a", "y", "yes"}:
        return "all"
    if answer in {"s", "select"}:
        return "select"
    if answer in {"k", "skip", "v"}:
        return "skip"
    return "decline"


def _prompt_report_only_choice(*, stdin, stdout: TextIO, lang: str) -> str:
    stdout.write(_text(lang, "manual_choice_prompt"))
    stdout.flush()
    try:
        answer = str(stdin.readline() or "").strip().lower()
    except Exception:
        answer = ""
    return "skip" if answer in {"k", "skip", "y", "yes"} else "decline"


def _prompt_selection(
    candidates: tuple[ProviderUpdateCandidate, ...],
    *,
    stdin,
    stdout: TextIO,
    lang: str,
) -> tuple[ProviderUpdateCandidate, ...]:
    for index, candidate in enumerate(candidates, start=1):
        print(f"   {index}. {candidate.label} {candidate.current_version} → {candidate.latest_version}", file=stdout)
    stdout.write(_text(lang, "select_prompt"))
    stdout.flush()
    try:
        raw = str(stdin.readline() or "")
    except Exception:
        raw = ""
    selected_indexes: set[int] = set()
    for token in re.split(r"[\s,]+", raw.strip()):
        if token.isdigit():
            selected_indexes.add(int(token))
    return tuple(
        candidate
        for index, candidate in enumerate(candidates, start=1)
        if index in selected_indexes
    )


def _print_available_candidates(
    candidates: tuple[ProviderUpdateCandidate, ...],
    *,
    lang: str,
    stdout: TextIO,
) -> None:
    print(_text(lang, "available"), file=stdout)
    print(_text(lang, "table_header"), file=stdout)
    for candidate in candidates:
        owner = candidate.owner
        if candidate.package:
            owner = f"{owner}:{candidate.package}"
        action = _text(lang, "managed") if candidate.update_command else _text(lang, "manual")
        print(
            f"   {candidate.label:<12} {candidate.current_version or '?':<14} "
            f"{candidate.latest_version or '?':<14} {owner:<28} {action}",
            file=stdout,
        )


def _print_unchecked_candidates(
    candidates: tuple[ProviderUpdateCandidate, ...],
    *,
    lang: str,
    stdout: TextIO,
) -> None:
    unchecked = tuple(
        candidate
        for candidate in candidates
        if candidate.issue and not candidate.update_available
    )
    if not unchecked:
        return
    print(_text(lang, "unchecked"), file=stdout)
    for candidate in unchecked:
        print(f"   - {candidate.label}: {candidate.issue}", file=stdout)


def _has_unchecked_candidates(candidates: tuple[ProviderUpdateCandidate, ...]) -> bool:
    return any(candidate.issue and not candidate.update_available for candidate in candidates)


def _normalized_mode(mode: str | None) -> str:
    value = str(mode or os.environ.get("CCB_UPDATE_PROVIDERS") or "prompt").strip().lower()
    return value if value in {"prompt", "check", "all", "none"} else "prompt"


def _stream_is_tty(stream: object) -> bool:
    isatty = getattr(stream, "isatty", None)
    if not callable(isatty):
        return False
    try:
        return bool(isatty())
    except Exception:
        return False


def _acquire_lock(path: Path) -> bool:
    path.parent.mkdir(parents=True, exist_ok=True)
    for attempt in range(2):
        try:
            descriptor = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        except FileExistsError:
            if attempt == 0 and _lock_is_stale(path):
                try:
                    path.unlink()
                except OSError:
                    return False
                continue
            return False
        try:
            os.write(descriptor, f"{os.getpid()} {_utc_now()}\n".encode("utf-8"))
        finally:
            os.close(descriptor)
        return True
    return False


def _lock_is_stale(path: Path) -> bool:
    try:
        stat = path.stat()
    except OSError:
        return False
    try:
        raw_pid = path.read_text(encoding="utf-8", errors="replace").split(maxsplit=1)[0]
        pid = int(raw_pid)
    except (OSError, ValueError, IndexError):
        pid = 0
    if pid > 0:
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            return True
        except PermissionError:
            return False
        except OverflowError:
            pass
        except OSError:
            pass
        else:
            return False
    return stat.st_mtime + LOCK_STALE_SECONDS < time.time()


def _release_lock(path: Path) -> None:
    try:
        path.unlink()
    except OSError:
        pass


def _utc_now() -> str:
    return datetime.now(tz=timezone.utc).isoformat().replace("+00:00", "Z")


def _optional_text(value: object) -> str | None:
    text = str(value or "").strip()
    return text or None


def _provider_version_is_newer(candidate: str, current: str) -> bool:
    candidate_parts = _semantic_version_parts(candidate)
    current_parts = _semantic_version_parts(current)
    if candidate_parts is None or current_parts is None:
        return is_newer_version(candidate, current)
    candidate_release, candidate_prerelease = candidate_parts
    current_release, current_prerelease = current_parts
    width = max(len(candidate_release), len(current_release))
    candidate_release = candidate_release + (0,) * (width - len(candidate_release))
    current_release = current_release + (0,) * (width - len(current_release))
    if candidate_release != current_release:
        return candidate_release > current_release
    return _prerelease_is_newer(candidate_prerelease, current_prerelease)


def _semantic_version_parts(value: str) -> tuple[tuple[int, ...], tuple[str, ...] | None] | None:
    normalized = str(value or "").strip().lstrip("vV")
    match = re.fullmatch(
        r"(?P<release>\d+(?:\.\d+)*)(?:-(?P<prerelease>[0-9A-Za-z.-]+))?(?:\+[0-9A-Za-z.-]+)?",
        normalized,
    )
    if match is None:
        return None
    release = tuple(int(part) for part in match.group("release").split("."))
    raw_prerelease = match.group("prerelease")
    prerelease = tuple(raw_prerelease.split(".")) if raw_prerelease else None
    return release, prerelease


def _prerelease_is_newer(
    candidate: tuple[str, ...] | None,
    current: tuple[str, ...] | None,
) -> bool:
    if candidate is None:
        return current is not None
    if current is None:
        return False
    for candidate_part, current_part in zip(candidate, current):
        if candidate_part == current_part:
            continue
        candidate_numeric = candidate_part.isdigit()
        current_numeric = current_part.isdigit()
        if candidate_numeric and current_numeric:
            return int(candidate_part) > int(current_part)
        if candidate_numeric != current_numeric:
            return not candidate_numeric
        return candidate_part > current_part
    return len(candidate) > len(current)


_TEXT = {
    "en": {
        "disabled": "ℹ️  Provider update management disabled for this run.",
        "non_interactive": "ℹ️  Provider update prompt skipped in non-interactive mode; use `--providers check|all` explicitly.",
        "locked": "ℹ️  Another CCB provider update check is already running; skipping this check.",
        "scan_failed": "⚠️  Provider update check failed: {detail}",
        "none_installed": "ℹ️  No installed provider CLIs were detected.",
        "current": "✅ Installed provider CLIs are already current.",
        "not_found": "ℹ️  No supported provider CLI updates were found.",
        "all_muted": "✅ Detected provider versions remain skipped; a newer version will be offered automatically.",
        "available": "🔌 Provider updates available:",
        "table_header": "   Provider     Current        Available      Installed by                 Action",
        "managed": "managed",
        "manual": "manual",
        "manual_only": "ℹ️  Available updates use unsupported installation owners; update them manually.",
        "manual_choice_prompt": "Skip these reported versions until a newer version appears? [y/N]: ",
        "unchecked": "ℹ️  Installed providers not fully checked:",
        "check_only": "ℹ️  Check-only mode: no provider was changed.",
        "muted": "ℹ️  Skipped versions remain muted: {versions}",
        "choices": "   [a] update all  [s] select  [Enter/n] not now  [k] skip these versions",
        "choice_prompt": "Update providers? [a/s/N/k]: ",
        "select_prompt": "Select provider numbers (comma-separated, Enter cancels): ",
        "skipped_versions": "✅ These provider versions will stay hidden until a newer version appears.",
        "declined": "ℹ️  Provider updates declined; they will be offered on the next `ccb update`.",
        "updating": "🔄 Updating {provider}: {current} → {latest}...",
        "updated": "✅ {provider} updated to {version}.",
        "update_failed": "⚠️  {provider} update failed: {detail}",
        "restart_note": "ℹ️  Running provider panes were not restarted; the new versions apply on the next start or explicit restart.",
    },
    "zh": {
        "disabled": "ℹ️  本次已关闭 provider 更新管理。",
        "non_interactive": "ℹ️  非交互模式不会弹出 provider 更新提示；如需检查或更新，请显式使用 `--providers check|all`。",
        "locked": "ℹ️  另一个 CCB provider 更新检查正在运行，本次跳过。",
        "scan_failed": "⚠️  Provider 更新检查失败：{detail}",
        "none_installed": "ℹ️  未检测到已安装的 provider CLI。",
        "current": "✅ 已安装的 provider CLI 均为最新版本。",
        "not_found": "ℹ️  未发现 CCB 可确认的 provider CLI 更新。",
        "all_muted": "✅ 检测到的 provider 版本仍处于跳过状态；出现更高版本后会自动重新提示。",
        "available": "🔌 检测到 provider 可更新：",
        "table_header": "   Provider     当前版本       可用版本       安装来源                     操作",
        "managed": "可管理",
        "manual": "需手动",
        "manual_only": "ℹ️  可用更新的安装来源暂不受 CCB 管理，请手动更新。",
        "manual_choice_prompt": "是否跳过这些已报告版本，直到出现更高版本？[y/N]：",
        "unchecked": "ℹ️  以下已安装 provider 未能完整检查：",
        "check_only": "ℹ️  当前为仅检查模式，没有修改 provider。",
        "muted": "ℹ️  以下已跳过版本继续保持静默：{versions}",
        "choices": "   [a] 全部更新  [s] 选择更新  [Enter/n] 暂不更新  [k] 跳过这些版本",
        "choice_prompt": "是否更新 provider？[a/s/N/k]：",
        "select_prompt": "输入 provider 序号（逗号分隔，直接回车取消）：",
        "skipped_versions": "✅ 已跳过这些 provider 版本；出现更高版本后会重新提示。",
        "declined": "ℹ️  本次暂不更新；下次执行 `ccb update` 时会再次提示。",
        "updating": "🔄 正在更新 {provider}：{current} → {latest}...",
        "updated": "✅ {provider} 已更新到 {version}。",
        "update_failed": "⚠️  {provider} 更新失败：{detail}",
        "restart_note": "ℹ️  当前运行中的 provider pane 未自动重启；新版本会在下次启动或显式重启后生效。",
    },
}


def _text(lang: str, key: str, **values: object) -> str:
    messages = _TEXT.get(lang, _TEXT["en"])
    template = messages.get(key, _TEXT["en"].get(key, key))
    try:
        return template.format(**values)
    except Exception:
        return template


__all__ = [
    "ProviderUpdateCandidate",
    "ProviderUpdateExecution",
    "discover_provider_updates",
    "execute_provider_update",
    "load_provider_update_state",
    "provider_update_state_path",
    "run_provider_update_flow",
    "write_provider_update_state",
]
